"""Der Agent: LLM-Schleife mit Tool-Calling.

Der Agent hat genau zwei Werkzeuge -- `web_search` und `fetch_page` -- und
kombiniert sie selbststaendig, so oft er will (bis zum Limit aus den
Settings). Danach gibt er den Zwischenstand aus.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from scoutr.cache import Cache
from scoutr.config import Settings
from scoutr.models import Product
from scoutr.tools import TOOL_SCHEMAS, EventHook, Toolbox

SYSTEM_PROMPT = """\
Du bist scoutr, ein Rechercheagent in der Kommandozeile. Du beantwortest Fragen \
ausschliesslich auf Basis dessen, was du im Web tatsaechlich gefunden und gelesen hast.

Deine Werkzeuge:
- `web_search(query, count, country, lang)` -- schickt eine Suchanfrage ans Web.
- `fetch_page(url)` -- laedt eine Seite und gibt den lesbaren Text zurueck.

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
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.toolbox = toolbox or Toolbox(
            settings,
            cache=cache,
            on_event=on_event,
            spec_extractor=self.extract_specs,
        )
        self.last_result: AgentResult | None = None

    # -- Zustand ----------------------------------------------------------
    def close(self) -> None:
        self.toolbox.close()

    def clear(self) -> None:
        """Verwirft den Gespraechsverlauf, behaelt aber die Konfiguration."""
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.last_result = None

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
    def _completion(self, messages: list[dict[str, Any]], *, stream: bool) -> dict[str, Any]:
        """Ein LLM-Aufruf; gibt eine Assistant-Nachricht als Dict zurueck."""
        import litellm

        litellm.suppress_debug_info = True
        kwargs = self.settings.llm_kwargs()
        response = litellm.completion(
            model=self.settings.model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            stream=stream,
            **kwargs,
        )
        if not stream:
            message = response.choices[0].message
            content = message.content or ""
            if content:
                self._emit("answer_chunk", text=content)
            return {
                "role": "assistant",
                "content": content,
                "tool_calls": _tool_calls_to_dicts(getattr(message, "tool_calls", None)),
            }
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
                entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if getattr(call, "id", None):
                    entry["id"] = call.id
                function = getattr(call, "function", None)
                if function is not None:
                    if getattr(function, "name", None):
                        entry["name"] += function.name
                    if getattr(function, "arguments", None):
                        entry["arguments"] += function.arguments

        tool_calls = [
            {
                "id": entry["id"] or f"call_{index}",
                "type": "function",
                "function": {"name": entry["name"], "arguments": entry["arguments"] or "{}"},
            }
            for index, entry in sorted(calls.items())
            if entry["name"]
        ]
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

        self.messages.append({"role": "user", "content": self._with_context(question)})

        budget = max(1, self.settings.max_tool_calls)
        used = 0

        while True:
            remaining = budget - used
            if remaining <= 0:
                result.hit_limit = True
                self.messages.append({"role": "user", "content": BUDGET_PROMPT})
                final = self._final_answer(stream=stream)
                result.answer = final
                break

            try:
                message = self._completion(self.messages, stream=stream)
            except Exception as exc:  # LLM-Fehler duerfen den Chat nicht toeten
                result.error = f"{type(exc).__name__}: {exc}"
                self._emit("error", message=result.error)
                self.messages.pop()  # die unbeantwortete Nutzerfrage zuruecknehmen
                return self._finish(result)

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

        return self._finish(result)

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

        payload = self.toolbox.call(name, arguments)
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "name": name,
                "content": json.dumps(payload, ensure_ascii=False)[:60_000],
            }
        )

    def _final_answer(self, *, stream: bool) -> str:
        """Letzter Aufruf ohne Werkzeuge -- der Zwischenstand muss raus."""
        import litellm

        litellm.suppress_debug_info = True
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

    def _finish(self, result: AgentResult) -> AgentResult:
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
                question=_last_user_question(self.messages),
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


def _last_user_question(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content") or ""
            return str(content).split("\n\n[Ortsfilter:")[0]
    return ""


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
