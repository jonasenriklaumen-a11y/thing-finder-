"""Der Agent: LLM-Schleife mit Tool-Calling.

Der Agent hat genau zwei Werkzeuge -- `web_search` und `fetch_page` -- und
kombiniert sie selbststaendig, so oft er will (bis zum Limit aus den
Settings). Danach gibt er den Zwischenstand aus.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scoutr.cache import Cache
from scoutr.config import Settings
from scoutr.models import Product
from scoutr.tools import MEMORY_SCHEMA, SUBAGENT_SCHEMA, TOOL_SCHEMAS, EventHook, Toolbox

SYSTEM_PROMPT = """\
Du bist scoutr, ein Rechercheagent in der Kommandozeile. Du beantwortest Fragen \
ausschliesslich auf Basis dessen, was du im Web tatsaechlich gefunden und gelesen hast.

Deine Werkzeuge:
- `web_search(query, count, country, lang)` -- schickt eine Suchanfrage ans Web.
- `fetch_page(url)` -- laedt eine Seite (auch PDFs) und gibt den lesbaren Text zurueck.
- `search_news(query, count)` -- Nachrichten mit Datum, fuer alles Aktuelle.
- `calculate(expression)` -- exakte Arithmetik. Rechne nie selbst im Kopf.
- `remember(text)` -- Notiz auf den dauerhaften Merkzettel, NUR auf ausdrueckliche \
Bitte des Nutzers.

So gehst du vor:
1. Ueberlege, welche Suchanfragen sinnvoll sind, und formuliere MEHRERE Varianten -- \
nie nur eine. Unterschiedliche Formulierungen, Synonyme, Fachbegriffe, konkrete \
Eigenschaften aus der Frage des Nutzers.
2. Sichte die Treffer und entscheide, welche Seiten sich zu lesen lohnen. Rufe pro Runde \
mehrere Seiten ab, statt eine nach der anderen.
3. Lies die relevanten Seiten und zieh die gewuenschten Informationen heraus.
4. Fasse zusammen, bewerte gegen die Kriterien des Nutzers und nenne zu jeder Angabe die \
Quelle (Domain, bei Bedarf mit Link).

Harte Regeln:
- Rate nie. Was du nicht gefunden hast, kennzeichnest du als "nicht gefunden".
- Erfinde keine Adressen, Preise, Oeffnungszeiten, Bewertungen oder technischen Daten. \
Jede konkrete Angabe muss aus einer gelesenen Seite oder einem Such-Snippet stammen.
- Liefert `fetch_page` einen `skipped_reason` (blocked, consent_required, paywall, \
robots_disallowed), dann versuche NICHT, das zu umgehen. Nimm eine andere Quelle -- es \
gibt fast immer eine zweite Quelle fuer dieselbe Information.
- Nennst du eine Zahl oder ein Detail aus einem Such-Snippet statt aus einer gelesenen \
Seite, schreib das dazu.

Ortsfilter:
- Nennt der Nutzer eine Stadt, Region oder ein Land, baust du das in die Suchanfragen ein \
UND setzt `country` und `lang` passend.
- Treffer, die offensichtlich ausserhalb des gewuenschten Gebiets liegen, sortierst du aus \
und erwaehnst sie nicht.

Produktfragen ("welchen Laptop soll ich kaufen", "Preis fuer X"):
- Sammle 3 bis 6 Kandidaten.
- Nutze fuer Specs bevorzugt Herstellerseiten, Testberichte (z.B. Notebookcheck, Heise, \
Chip) und Preisvergleiche -- Marktplaetze wie Amazon blockieren Abrufe und liefern \
ohnehin schlechtere Daten.
- Gib die Kandidaten als Vergleich mit denselben Spec-Zeilen aus, damit man sie \
nebeneinander lesen kann. Fehlende Werte als "–", niemals geraten.
- Kommt `fetch_page` mit strukturierten `products`-Daten zurueck, verwende deren Werte \
(inklusive `image_url`) woertlich.

Antwortformat:
- Deutsch, knapp, ohne Vorrede und ohne Wiederholung der Frage.
- Nummerierte Liste bei mehreren Ergebnissen, je Eintrag: Name, die harten Fakten, dann \
eine Zeile "Quelle: ...".
- Am Ende hoechstens ein Satz zu dem, was du nicht klaeren konntest.
"""

BUDGET_PROMPT = """\
Das Werkzeug-Budget ist aufgebraucht. Beantworte die Frage jetzt mit dem, was du bereits \
gelesen hast. Kennzeichne offene Punkte ausdruecklich als "nicht gefunden" und weise in \
einem Satz darauf hin, dass die Recherche am Limit abgebrochen wurde."""

IMAGE_PROMPT = """\
Beschreibe, was auf diesem Bild zu sehen ist -- mit Blick darauf, wonach man im Web \
suchen wuerde. Nenne, wenn erkennbar: Produkt- oder Objektart, Marke, Modellbezeichnung, \
Aufschriften, Logos, Text im Bild, Farbe und auffaellige Merkmale. Rate nicht: Was du \
nicht sicher erkennst, laesst du weg. Antworte in hoechstens 120 Woertern auf Deutsch und \
haenge eine Zeile "Suchbegriffe: ..." mit 3 bis 5 konkreten Suchbegriffen an."""

SPEC_PROMPT = """\
Aus dem folgenden Seitentext sollen technische Daten eines Produkts als JSON-Objekt \
extrahiert werden -- flach, nur Strings, hoechstens 15 Eintraege, deutsche Schluessel \
(z.B. "Display", "CPU", "RAM", "Gewicht", "Akku"). Erfinde nichts: was nicht im Text \
steht, laesst du weg. Steht dort gar kein Produkt, antworte mit {}.
Antworte NUR mit dem JSON-Objekt.

URL: %(url)s

TEXT:
%(text)s
"""


#: Fehler, bei denen ein zweiter Versuch sinnvoll ist.
TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "connection error",
    "temporarily unavailable",
    "service unavailable",
    "internal server error",
    "502",
    "503",
    "504",
    "overloaded",
    "rate limit",
    "too many requests",
)

#: Offensichtlicher Small-Talk -- dafuer wird gar nichts gefragt, auch kein
#: Modell. Bewusst eng gefasst: im Zweifel entscheidet die Vorpruefung.
SMALL_TALK_RE = re.compile(
    r"^(hallo|hi|hey|moin|servus|guten\s+(morgen|tag|abend)|"
    r"danke(\s+(dir|schoen|schön|sehr))?|vielen\s+dank|thx|thanks|"
    r"ok(ay)?|cool|super|top|passt|perfekt|nice|"
    r"tsch(ue|ü)ss|bye|ciao|bis\s+(dann|morgen|spaeter|später)|gute\s+nacht|"
    r"wie\s+geht('?s|\s+es)(\s+dir)?|alles\s+klar|aha|hm+|test)"
    r"[\s!?.,:;)~-]*$",
    re.IGNORECASE,
)

TRIAGE_PROMPT = (
    "Entscheide, ob die folgende Nutzernachricht eine Web-Recherche braucht oder nur "
    "normale Konversation ist (Gruss, Dank, Meinung, Frage an dich selbst, Kommentar "
    "zum Gespraech). Nachfragen zu einer laufenden Recherche zaehlen als Recherche. "
    "Antworte mit GENAU einem Wort: RECHERCHE oder CHAT.\n\nNachricht: %s"
)

#: Beginn der internen Nachricht mit den Vorrecherche-Ergebnissen.
PRE_RESEARCH_PREFIX = "Zu deiner Unterstuetzung wurde die Anfrage"

#: Platzhalter fuer aeltere Werkzeug-Ergebnisse, die aus dem Verlauf fliegen.
TRIMMED_NOTE = "[gekuerzt -- aeltere Werkzeug-Ausgabe, die Fakten stehen in der Antwort]"

#: Platzhalter fuer aeltere Vorrecherche-Bloecke.
TRIMMED_RESEARCH = "[gekuerzt -- Vorrecherche eines frueheren Turns, das Ergebnis steht unten]"

#: Grob: ein Token sind etwa vier Zeichen. Reicht, um den Verlauf unter dem
#: Fenster zu halten -- genau zaehlen muessten wir je Modell anders.
CHARS_PER_TOKEN = 4

#: Wie viel des Fensters fuer die Antwort und die naechste Werkzeugrunde
#: frei bleiben muss.
ANSWER_RESERVE = 0.30

#: So viele Nachrichten am Ende bleiben immer unangetastet -- der laufende
#: Turn darf nie beschnitten werden.
PROTECTED_TAIL = 6

#: Auf so viel wird eine alte Nachricht eingedampft, wenn der Platz knapp wird.
SHRUNK_LENGTH = 300


#: Fehler, die ein zweiter Versuch sicher NICHT behebt -- auch wenn LiteLLM
#: sie als APIConnectionError etikettiert.
PERMANENT_MARKERS = ("jsondecodeerror", "extra data", "expecting value", "invalid api key")


def is_transient(detail: str) -> bool:
    """Lohnt sich bei diesem Fehler ein zweiter Versuch?"""
    lowered = detail.lower()
    if any(marker in lowered for marker in PERMANENT_MARKERS):
        return False
    return any(marker in lowered for marker in TRANSIENT_MARKERS)


@dataclass
class AgentResult:
    """Was ein Durchlauf ergeben hat."""

    answer: str
    tool_calls: int = 0
    searches: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    products: list[Product] = field(default_factory=list)
    hit_limit: bool = False
    error: str = ""

    def meta(self) -> dict[str, Any]:
        return {
            "tool_calls": self.tool_calls,
            "searches": self.searches,
            "sources": self.sources,
            "skipped": self.skipped,
            "hit_limit": self.hit_limit,
        }


class Agent:
    """Haelt den Gespraechsverlauf und fuehrt die Tool-Schleife aus."""

    def __init__(
        self,
        settings: Settings,
        cache: Cache | None = None,
        on_event: EventHook | None = None,
        toolbox: Toolbox | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.on_event = on_event
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(cache)}
        ]
        self.toolbox = toolbox or Toolbox(
            settings,
            cache=cache,
            on_event=on_event,
            spec_extractor=self.extract_specs,
        )
        #: Subagenten sind nur fuer den Hauptagenten da -- sonst koennte sich
        #: die Kette endlos fortsetzen.
        self.use_subagents = settings.max_subagents > 0
        if self.use_subagents:
            self.toolbox.subagent_runner = self._run_subagents
        self.last_result: AgentResult | None = None

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Die Werkzeuge, die dieser Agent anbietet.

        Merkzettel und Subagenten bekommt nur der Hauptagent -- Subagenten
        sollen weder Notizen anlegen noch weitere Subagenten starten.
        """
        extra: list[dict[str, Any]] = []
        if self.cache is not None:
            extra.append(MEMORY_SCHEMA)
        if self.use_subagents:
            extra.append(SUBAGENT_SCHEMA)
        return [*TOOL_SCHEMAS, *extra]

    def _auto_subagents_wanted(self) -> bool:
        """Soll vor der eigentlichen Runde automatisch vorrecherchiert werden?"""
        return self.use_subagents and self.settings.subagents_auto

    def _needs_research(self, question: str) -> bool:
        """Kurze Vorpruefung: braucht diese Nachricht ueberhaupt eine Recherche?

        "hallo" oder "danke" sollen keinen Planungs- und Subagenten-Apparat
        anwerfen. Stufe 1 ist eine Heuristik (kostet nichts), Stufe 2 ein
        winziger Modellaufruf mit hartem Zeitlimit. Faellt der aus oder
        dauert er zu lange, gilt sicherheitshalber: Recherche -- lieber
        einmal zu viel geplant als eine echte Frage unbeantwortet.
        """
        text = question.strip()
        if not text or SMALL_TALK_RE.match(text):
            self._emit("triage", decision="chat", source="heuristik")
            return False

        import litellm

        litellm.suppress_debug_info = True
        model = self.settings.effective_subagent_model
        started = time.monotonic()
        try:
            response = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": TRIAGE_PROMPT % text[:400]}],
                max_tokens=10,
                timeout=max(1.0, self.settings.triage_timeout),
                **self.settings.llm_kwargs_for(model),
            )
            answer = (response.choices[0].message.content or "").strip().lower()
        except Exception:
            self._emit("triage", decision="recherche", source="fallback")
            return True

        elapsed = round(time.monotonic() - started, 2)
        if "chat" in answer and "recherche" not in answer:
            self._emit("triage", decision="chat", source="modell", seconds=elapsed)
            return False
        self._emit("triage", decision="recherche", source="modell", seconds=elapsed)
        return True

    def _recent_context(self, turns: int = 2) -> str:
        """Die letzten Wortmeldungen -- damit Nachfragen verstaendlich bleiben.

        Interne Zwischennachrichten (Vorrecherche-Ergebnisse, Budget-Hinweis)
        gehoeren nicht hinein: der Planer soll das Gespraech sehen, nicht
        unsere Regie-Anweisungen.
        """
        parts: list[str] = []
        for message in self.messages[1:-1]:
            role = message.get("role")
            if role not in ("user", "assistant"):
                continue
            content = str(message.get("content") or "").strip()
            if not content or content.startswith((PRE_RESEARCH_PREFIX, BUDGET_PROMPT[:40])):
                continue
            content = content.split("\n\n[Ortsfilter:")[0]
            parts.append(f"{'Nutzer' if role == 'user' else 'scoutr'}: {content[:400]}")
        return "\n".join(parts[-turns * 2 :])

    def _auto_research(self, question: str, budget: int) -> int:
        """Zerlegt die Frage, laesst die Teile parallel bearbeiten, meldet zurueck.

        Returns:
            Wie viele Werkzeug-Aufrufe das gekostet hat.
        """
        from scoutr.subagents import plan_subtasks

        limit = max(1, min(self.settings.max_subagents, 4))
        self._emit("planning", question=question)
        context = self._recent_context()
        if self.settings.location:
            # Sonst planen die Teilfragen ohne den Ort aus --location/.env
            # und die Subagenten suchen weltweit.
            context = f"[Ortsfilter: {self.settings.location}]\n{context}".strip()
        try:
            tasks = plan_subtasks(question, self.settings, context=context, limit=limit)
        except Exception as exc:
            # Scheitert die Planung, macht der Hauptagent es eben selbst.
            self._emit("error", message=f"Planung fehlgeschlagen: {exc}")
            return 0

        results = self._run_subagents(tasks)
        spent = sum(int(result.get("tool_calls", 0) or 0) for result in results)

        findings = json.dumps({"vorrecherche": results}, ensure_ascii=False)
        self.messages.append(
            {
                "role": "user",
                "content": (
                    PRE_RESEARCH_PREFIX
                    + " bereits in Teilfragen "
                    "zerlegt und vorrecherchiert. Nutze diese Ergebnisse, pruefe sie "
                    "gegen die Kriterien des Nutzers und recherchiere nur nach, wo "
                    "etwas fehlt.\n\n"
                    + findings[: max(2000, self.settings.max_tool_chars * 2)]
                ),
            }
        )
        return min(spent, max(0, budget - 1))

    def _run_subagents(self, tasks: list[str]) -> list[dict[str, Any]]:
        """Fuehrt Teilfragen parallel aus und zaehlt ihr Budget mit."""
        from scoutr.subagents import run_subagents

        results = run_subagents(
            tasks,
            self.settings,
            cache=self.cache,
            on_event=self.on_event,
            parallel=2,
        )
        for result in results:
            self.toolbox.stats.sources.extend(result.sources)
            self.toolbox.stats.searches.extend(result.searches)
        payloads = []
        for result in results:
            payload = result.as_dict()
            payload["tool_calls"] = result.tool_calls
            payloads.append(payload)
        return payloads

    # -- Zustand ----------------------------------------------------------
    def close(self) -> None:
        self.toolbox.close()

    def clear(self) -> None:
        """Verwirft den Gespraechsverlauf, behaelt aber die Konfiguration."""
        self.messages = [{"role": "system", "content": self._system_prompt(self.cache)}]
        self.last_result = None

    @staticmethod
    def _system_prompt(cache: Cache | None) -> str:
        """Systemprompt plus Tagesdatum und Merkzettel.

        Lokale Modelle mit altem Wissensstand suchen sonst nach "Test 2024",
        obwohl laengst ein anderes Jahr ist. Und der Merkzettel macht
        "merk dir X" ueber Sitzungen hinweg nutzbar.
        """
        from datetime import date

        weekdays = (
            "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"
        )
        today = date.today()
        prompt = (
            f"{SYSTEM_PROMPT}\n"
            f"Heute ist {weekdays[today.weekday()]}, der {today.strftime('%d.%m.%Y')}. "
            f"Richte Suchanfragen nach Aktualitaet daran aus, nicht an deinem "
            f"Wissensstand."
        )
        if cache is not None:
            try:
                notes = cache.list_notes(limit=8)
            except Exception:
                notes = []
            if notes:
                lines = "\n".join(f"- {note.text}" for note in notes)
                prompt += (
                    "\n\nMerkzettel des Nutzers aus frueheren Sitzungen "
                    "(beruecksichtigen, wenn relevant):\n" + lines
                )
        return prompt

    def set_location(self, location: str, lang: str = "", country: str = "") -> None:
        """Setzt den Ortsfilter fuer alle folgenden Anfragen."""
        self.settings.location = location.strip()
        if lang:
            self.settings.lang = lang.strip().lower()
        if country:
            self.settings.country = country.strip().lower()

    def set_model(self, model: str) -> None:
        self.settings.model = model.strip()

    def _emit(self, event: str, **payload: Any) -> None:
        if self.on_event:
            self.on_event(event, payload)

    # -- LLM --------------------------------------------------------------
    def _completion_with_retry(
        self, messages: list[dict[str, Any]], *, stream: bool
    ) -> dict[str, Any]:
        """Ruft das LLM und wiederholt bei voruebergehenden Stoerungen.

        Lokale Modelle sterben gern an Speichermangel; in dem Fall raeumen wir
        auf und versuchen es noch einmal, statt den ganzen Durchlauf zu
        verlieren.
        """
        attempts = max(1, self.settings.llm_retries)
        last_error: Exception | None = None

        # Sicherheitsnetz: ein einziges kaputtes Argument im Verlauf laesst
        # LiteLLM jeden weiteren Aufruf ablehnen. Vor dem Senden reparieren,
        # damit auch Altbestand keine Sitzung mehr vergiften kann.
        sanitize_history(messages)

        for attempt in range(attempts):
            try:
                return self._completion(messages, stream=stream)
            except Exception as exc:
                last_error = exc
                detail = f"{type(exc).__name__}: {exc}"
                if attempt + 1 >= attempts:
                    break

                from scoutr.local_model import free_memory, resource_problem

                if resource_problem(detail):
                    freed = free_memory()
                    self._emit(
                        "retry",
                        attempt=attempt + 1,
                        reason="Speichermangel",
                        detail=f"entladen: {', '.join(freed)}" if freed else "",
                    )
                elif is_transient(detail):
                    self._emit("retry", attempt=attempt + 1, reason="Verbindung", detail="")
                else:
                    break  # echter Fehler -- Wiederholen hilft nicht
                time.sleep(min(2**attempt, 8))

        raise last_error if last_error else RuntimeError("LLM-Aufruf fehlgeschlagen")

    def _completion(self, messages: list[dict[str, Any]], *, stream: bool) -> dict[str, Any]:
        """Ein LLM-Aufruf; gibt eine Assistant-Nachricht als Dict zurueck."""
        import litellm

        litellm.suppress_debug_info = True
        kwargs = self.settings.llm_kwargs()
        response = litellm.completion(
            model=self.settings.model,
            messages=messages,
            tools=self.tools,
            tool_choice="auto",
            stream=stream,
            **kwargs,
        )
        if not stream:
            message = response.choices[0].message
            content = message.content or ""
            tool_calls = repair_tool_calls(
                _tool_calls_to_dicts(getattr(message, "tool_calls", None))
            )
            # Kommentar VOR Werkzeugaufrufen ("Ich suche mal ...") ist keine
            # Antwort: als answer_chunk gerendert wuerde er sich mit den
            # [Suche]-Zeilen verhaken und spaeter vor der echten Antwort
            # kleben bleiben.
            if content and not tool_calls:
                self._emit("answer_chunk", text=content)
            return {"role": "assistant", "content": content, "tool_calls": tool_calls}
        return self._consume_stream(response)

    def _consume_stream(self, response: Any) -> dict[str, Any]:
        """Sammelt Text- und Tool-Call-Deltas eines Streams ein."""
        content_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}

        for chunk in response:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                content_parts.append(text)
                self._emit("answer_chunk", text=text)
            for call in getattr(delta, "tool_calls", None) or []:
                index = getattr(call, "index", 0) or 0
                call_id = getattr(call, "id", None)
                entry = calls.get(index)
                if entry is not None and call_id and entry["id"] and entry["id"] != call_id:
                    # Ollama meldet jeden Tool-Call unter Index 0 -- eine neue
                    # ID am selben Index ist ein NEUER Aufruf, kein Delta.
                    index = max(calls) + 1
                    entry = None
                if entry is None:
                    entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if call_id:
                    entry["id"] = call_id
                function = getattr(call, "function", None)
                if function is not None:
                    if getattr(function, "name", None):
                        entry["name"] += function.name
                    if getattr(function, "arguments", None):
                        entry["arguments"] += function.arguments

        tool_calls = repair_tool_calls(
            [
                {
                    "id": entry["id"] or f"call_{index}",
                    "type": "function",
                    "function": {"name": entry["name"], "arguments": entry["arguments"] or "{}"},
                }
                for index, entry in sorted(calls.items())
                if entry["name"]
            ]
        )
        return {
            "role": "assistant",
            "content": "".join(content_parts),
            "tool_calls": tool_calls,
        }

    # -- Hauptschleife ----------------------------------------------------
    def ask(self, question: str, *, stream: bool = True) -> AgentResult:
        """Beantwortet *question* -- sucht, liest und wertet aus."""
        question = question.strip()
        self.toolbox.stats.reset()
        result = AgentResult(answer="")
        if not question:
            result.answer = ""
            return result

        # Alles ab hier gehoert zu diesem Turn. Scheitert das LLM endgueltig,
        # wird bis hierher zurueckgeschnitten -- ein halber Turn (Assistant-
        # Nachricht mit Tool-Calls ohne Antworten) macht den Verlauf fuer
        # jede weitere Frage unbrauchbar, die API lehnt ihn dann ab.
        turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": self._with_context(question)})

        budget = max(1, self.settings.max_tool_calls)
        used = 0

        # Automatische Vorrecherche: die Anfrage wird zerlegt und die Teile
        # laufen parallel, bevor der Hauptagent uebernimmt. Was die
        # Subagenten schon gefunden haben, steht ihm dann zur Verfuegung.
        if self._auto_subagents_wanted() and self._needs_research(question):
            used += self._auto_research(question, budget)

        while True:
            remaining = budget - used
            if remaining <= 0:
                result.hit_limit = True
                self.messages.append({"role": "user", "content": BUDGET_PROMPT})
                final = self._final_answer(stream=stream)
                result.answer = final
                break

            try:
                self._trim_history()
                message = self._completion_with_retry(self.messages, stream=stream)
            except Exception as exc:  # LLM-Fehler duerfen den Chat nicht toeten
                result.error = f"{type(exc).__name__}: {exc}"
                self._emit("error", message=result.error)
                # Den ganzen angefangenen Turn verwerfen, nicht nur die letzte
                # Nachricht -- sonst bleibt ein Tool-Call ohne Antwort stehen.
                del self.messages[turn_start:]
                return self._finish(result, question)

            tool_calls = message.get("tool_calls") or []
            self.messages.append(_assistant_message(message))

            if not tool_calls:
                result.answer = message.get("content", "")
                break

            if len(tool_calls) > remaining:
                # Budget deckelt die Runde -- der Rest wird als Fehlschlag gemeldet.
                tool_calls = tool_calls[:remaining]

            for call in tool_calls:
                used += 1
                self._run_tool_call(call)

            # Auf abgeschnittene Calls muss trotzdem geantwortet werden.
            answered = {call["id"] for call in tool_calls}
            for call in message.get("tool_calls") or []:
                if call["id"] not in answered:
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": call["function"]["name"],
                            "content": json.dumps(
                                {"error": "Werkzeug-Budget aufgebraucht."}, ensure_ascii=False
                            ),
                        }
                    )

        return self._finish(result, question)

    def _history_chars(self) -> int:
        return sum(len(str(message.get("content") or "")) for message in self.messages)

    def _budget_chars(self) -> int:
        """Wie viele Zeichen der Verlauf hoechstens belegen darf."""
        window = self.settings.context_tokens
        if window <= 0:
            window = 8192  # Annahme fuer Anbieter ohne eigene Angabe
        return int(window * CHARS_PER_TOKEN * (1 - ANSWER_RESERVE))

    def _trim_history(self) -> None:
        """Haelt den Verlauf sicher unter dem Kontextfenster.

        Laeuft das Fenster ueber, wirft der Anbieter still den ANFANG weg --
        Systemprompt und fruehere Fragen zuerst. Genau so fuehlt sich
        "er erinnert sich nicht mehr an die letzte Frage" an. Deshalb kuerzen
        wir lieber selbst und kontrolliert, in drei Stufen:

        1. Aeltere Werkzeug-Ausgaben werden zu einem Platzhalter.
        2. Aeltere Vorrecherche-Bloecke ebenso -- sie wiederholen sich sonst
           jeden Turn und fressen das Fenster auf.
        3. Reicht das nicht, werden aelteste Nachrichten eingedampft. Nie
           geloescht: ein Werkzeugaufruf ohne Antwort macht den Verlauf
           ungueltig. Systemprompt und laufender Turn bleiben unangetastet.
        """
        # -- Stufe 1: alte Werkzeug-Ausgaben ------------------------------
        keep = max(1, self.settings.keep_full_results)
        tool_indexes = [
            index
            for index, message in enumerate(self.messages)
            if message.get("role") == "tool"
        ]
        for index in tool_indexes[:-keep]:
            message = self.messages[index]
            if message.get("content") != TRIMMED_NOTE:
                message["content"] = TRIMMED_NOTE

        # -- Stufe 2: alte Vorrecherche-Bloecke ---------------------------
        research_indexes = [
            index
            for index, message in enumerate(self.messages)
            if str(message.get("content") or "").startswith(PRE_RESEARCH_PREFIX)
        ]
        for index in research_indexes[:-1]:
            self.messages[index]["content"] = TRIMMED_RESEARCH

        # -- Stufe 3: notfalls die aeltesten Nachrichten eindampfen -------
        budget = self._budget_chars()
        if self._history_chars() <= budget:
            return
        last_protected = max(1, len(self.messages) - PROTECTED_TAIL)
        if self._shrink_range(1, last_protected, budget):
            return

        # -- Stufe 4: aelteste Nachrichten ganz verwerfen ------------------
        self._drop_oldest(budget)

        # -- Stufe 5: bei sehr kleinem Fenster auch den Schwanz ------------
        # Sechs volle Nachrichten koennen ein 2k-Fenster allein sprengen.
        # Dann muss auch der laufende Turn dran -- nur die letzten beiden
        # (aktuelle Frage und eine laufende Werkzeugantwort) bleiben ganz,
        # sonst wuesste das Modell nicht mehr, worum es gerade geht.
        self._shrink_range(
            max(1, len(self.messages) - PROTECTED_TAIL),
            max(1, len(self.messages) - 2),
            budget,
        )

    def _drop_oldest(self, budget: int) -> None:
        """Verwirft die aeltesten Nachrichten, bis der Verlauf passt.

        Vorsichtig: eine Assistant-Nachricht mit Werkzeugaufrufen darf nie
        ohne ihre Antworten stehenbleiben, und eine Tool-Antwort nie ohne
        ihren Aufruf -- beides macht den Verlauf fuer die API ungueltig.
        Deshalb wird nach jedem Wurf so lange weiter verworfen, bis vorne
        wieder eine eigenstaendige Nachricht steht.
        """
        while self._history_chars() > budget and len(self.messages) > PROTECTED_TAIL + 1:
            del self.messages[1]
            # Verwaiste Tool-Antworten hinterherwerfen.
            while len(self.messages) > PROTECTED_TAIL + 1 and (
                self.messages[1].get("role") == "tool"
            ):
                del self.messages[1]

    def _shrink_range(self, start: int, stop: int, budget: int) -> bool:
        """Dampft Nachrichten von *start* bis *stop* ein. `True`, wenn es reicht."""
        for index in range(start, stop):
            if self._history_chars() <= budget:
                return True
            content = str(self.messages[index].get("content") or "")
            if len(content) <= SHRUNK_LENGTH:
                continue
            self.messages[index]["content"] = content[:SHRUNK_LENGTH].rstrip() + " […]"
        return self._history_chars() <= budget

    def _run_tool_call(self, call: dict[str, Any]) -> None:
        """Fuehrt einen einzelnen Tool-Call aus und haengt das Ergebnis an."""
        name = call["function"]["name"]
        raw_args = call["function"].get("arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}

        try:
            payload = self.toolbox.call(name, arguments)
        except Exception as exc:
            # Ein kaputtes Werkzeug darf den Durchlauf nicht beenden -- der
            # Agent soll es mit einer anderen Quelle weiter versuchen.
            payload = {"error": f"{type(exc).__name__}: {exc}"}
            self._emit("error", message=f"Werkzeug {name}: {exc}")

        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "name": name,
                "content": json.dumps(payload, ensure_ascii=False)[
                    : max(1000, self.settings.max_tool_chars)
                ],
            }
        )

    def _final_answer(self, *, stream: bool) -> str:
        """Letzter Aufruf ohne Werkzeuge -- der Zwischenstand muss raus."""
        import litellm

        litellm.suppress_debug_info = True
        # Der einzige Sendepfad neben _completion_with_retry -- dasselbe
        # Sicherheitsnetz gegen kaputte Tool-Argumente gehoert auch hierher.
        sanitize_history(self.messages)
        try:
            response = litellm.completion(
                model=self.settings.model,
                messages=self.messages,
                stream=stream,
                **self.settings.llm_kwargs(),
            )
        except Exception as exc:
            self._emit("error", message=f"{type(exc).__name__}: {exc}")
            return ""
        if not stream:
            text = response.choices[0].message.content or ""
            if text:
                self._emit("answer_chunk", text=text)
        else:
            parts: list[str] = []
            for chunk in response:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                piece = getattr(choices[0].delta, "content", None)
                if piece:
                    parts.append(piece)
                    self._emit("answer_chunk", text=piece)
            text = "".join(parts)
        self.messages.append({"role": "assistant", "content": text})
        return text

    def _finish(self, result: AgentResult, question: str = "") -> AgentResult:
        stats = self.toolbox.stats
        result.tool_calls = stats.tool_calls
        result.searches = list(stats.searches)
        result.sources = list(stats.sources)
        result.skipped = dict(stats.skipped)
        result.products = list(stats.products)
        self.last_result = result
        self._emit("done", tool_calls=result.tool_calls, hit_limit=result.hit_limit)
        if self.cache and result.answer:
            self.cache.add_history(
                session_id=str(id(self)),
                question=question,
                answer=result.answer,
                meta=result.meta(),
            )
        return result

    def _with_context(self, question: str) -> str:
        """Haengt den aktiven Ortsfilter an die Nutzerfrage."""
        location = (self.settings.location or "").strip()
        if not location:
            return question
        return (
            f"{question}\n\n[Ortsfilter: {location} · Sprache {self.settings.lang} · "
            f"Land {self.settings.country}. Baue den Ort in die Suchanfragen ein, setze "
            f"country/lang entsprechend und sortiere Treffer ausserhalb des Gebiets aus.]"
        )

    # -- Bild als Eingabe -------------------------------------------------
    def describe_image(self, path: str | Path) -> str:
        """Laesst ein Vision-Modell beschreiben, was auf dem Bild zu sehen ist.

        Raises:
            FileNotFoundError: Wenn *path* nicht existiert.
            RuntimeError: Wenn das Modell nicht antwortet.
        """
        import base64
        import mimetypes

        import litellm

        litellm.suppress_debug_info = True
        image_path = Path(path).expanduser()
        if not image_path.is_file():
            raise FileNotFoundError(f"Bild nicht gefunden: {image_path}")

        mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        self._emit("image", path=str(image_path))

        kwargs = self.settings.llm_kwargs_for(self.settings.effective_vision_model)
        try:
            response = litellm.completion(
                model=self.settings.effective_vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": IMAGE_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{encoded}"},
                            },
                        ],
                    }
                ],
                max_tokens=400,
                **kwargs,
            )
        except Exception as exc:
            raise RuntimeError(f"Bildbeschreibung fehlgeschlagen: {exc}") from exc
        description = (response.choices[0].message.content or "").strip()
        self._emit("image_done", description=description)
        return description

    # -- LLM-Fallback fuer Specs (Quelle 5 der Produktextraktion) ---------
    def extract_specs(self, text: str, url: str) -> dict[str, str]:
        """Laesst das LLM technische Daten aus reinem Text ziehen."""
        import litellm

        litellm.suppress_debug_info = True
        try:
            response = litellm.completion(
                model=self.settings.model,
                messages=[
                    {
                        "role": "user",
                        "content": SPEC_PROMPT % {"url": url, "text": text[:8000]},
                    }
                ],
                max_tokens=700,
                **self.settings.llm_kwargs(),
            )
            raw = (response.choices[0].message.content or "").strip()
        except Exception:
            return {}
        return _parse_spec_json(raw)


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def _tool_calls_to_dicts(tool_calls: Any) -> list[dict[str, Any]]:
    """Normalisiert LiteLLM-Objekte auf einfache Dicts."""
    out: list[dict[str, Any]] = []
    for index, call in enumerate(tool_calls or []):
        if isinstance(call, dict):
            function = call.get("function", {})
            out.append(
                {
                    "id": call.get("id") or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": function.get("arguments", "{}"),
                    },
                }
            )
            continue
        function = getattr(call, "function", None)
        out.append(
            {
                "id": getattr(call, "id", None) or f"call_{index}",
                "type": "function",
                "function": {
                    "name": getattr(function, "name", "") or "",
                    "arguments": getattr(function, "arguments", "") or "{}",
                },
            }
        )
    return out


def _assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    """Assistant-Nachricht so, wie sie zurueck in den Verlauf darf."""
    out: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("tool_calls"):
        out["tool_calls"] = message["tool_calls"]
    return out


def sanitize_history(messages: list[dict[str, Any]]) -> None:
    """Repariert ungueltige Tool-Call-Argumente in einem Verlauf, in place.

    Aufteilen geht hier nicht mehr -- die Tool-Antworten sind schon
    zugeordnet. Also: erstes gueltiges Objekt behalten, sonst `{}`.
    """
    for message in messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            raw = str(function.get("arguments") or "{}")
            try:
                json.loads(raw)
                continue
            except json.JSONDecodeError:
                pass
            pieces = split_json_objects(raw)
            function["arguments"] = pieces[0] if pieces else "{}"


def split_json_objects(raw: str) -> list[str]:
    """Zerlegt aneinandergeklebte JSON-Objekte in einzelne.

    Ollama streamt Tool-Calls mit jeweils kompletten Argumenten, aber alle
    unter Index 0 -- naiv aufsummiert entsteht `{"a":1}{"b":2}`. Und manche
    Modelle haengen selbst Text hinter ihr JSON. Beides laesst LiteLLM beim
    NAECHSTEN Aufruf mit "Extra data" sterben, weil es die Argumente aus dem
    Verlauf zurueckparst.
    """
    decoder = json.JSONDecoder()
    objects: list[str] = []
    index = 0
    raw = raw.strip()
    while index < len(raw):
        while index < len(raw) and raw[index] in " \t\r\n,":
            index += 1
        if index >= len(raw):
            break
        try:
            value, end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            break
        if isinstance(value, dict):
            objects.append(json.dumps(value, ensure_ascii=False))
        index = end
    return objects


def repair_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sorgt dafuer, dass jeder Tool-Call GUELTIGE JSON-Argumente traegt.

    Aneinandergeklebte Objekte werden zu eigenen Aufrufen aufgeteilt
    (Duplikate fallen weg), Unlesbares wird zu `{}` -- besser ein leerer
    Aufruf, den das Werkzeug sauber ablehnt, als ein Verlauf, den die API
    fuer immer zurueckweist.
    """
    repaired: list[dict[str, Any]] = []
    for call in tool_calls:
        function = call.get("function", {})
        raw = str(function.get("arguments") or "{}")
        pieces = split_json_objects(raw)
        if not pieces:
            pieces = ["{}"]
        seen: set[str] = set()
        for piece_index, piece in enumerate(pieces):
            if piece in seen:
                continue  # Ollama wiederholt Chunks gelegentlich komplett
            seen.add(piece)
            entry = {
                "id": call.get("id", "call_0") if piece_index == 0 else
                f"{call.get('id', 'call_0')}_{piece_index}",
                "type": "function",
                "function": {"name": function.get("name", ""), "arguments": piece},
            }
            repaired.append(entry)
    return repaired


def _parse_spec_json(raw: str) -> dict[str, str]:
    """Zieht ein flaches String-Dict aus der LLM-Antwort."""
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key)[:80]: str(value)[:160]
        for key, value in list(data.items())[:15]
        if value not in (None, "", [], {})
    }


SpecExtractorType = Callable[[str, str], dict[str, str]]
