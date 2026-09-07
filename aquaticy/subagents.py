"""Subagenten: mehrere Rechercheauftraege parallel bearbeiten.

Das Hauptmodell darf Teilaufgaben abgeben, statt alles selbst nacheinander
abzuarbeiten. Jeder Subagent bekommt dieselben zwei Werkzeuge, ein eigenes,
kleines Budget und liefert eine knappe Zusammenfassung mit Quellen zurueck.

Warum das hilft: "Finde Cafes mit WLAN, die sonntags offen haben und
Steckdosen haben" zerfaellt in Teilfragen, die unabhaengig voneinander
recherchiert werden koennen. Nacheinander kostet das viele Runden im
Hauptkontext -- parallel bleibt der Hauptverlauf kurz und uebersichtlich.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from aquaticy.cache import Cache
from aquaticy.config import Settings
from aquaticy.pace import paced
from aquaticy.tools import TOOL_SCHEMAS, EventHook, Toolbox

SUBAGENT_PROMPT = """\
Du bist ein Rechercheassistent und bearbeitest GENAU EINE Teilfrage. Du hast \
zwei Werkzeuge: `web_search` und `fetch_page`.

Vorgehen: EIN `web_search`-Aufruf, in dem du ueber `queries` zwei weitere \
Formulierungen mitgibst -- andere Woerter, anderer Blickwinkel, nicht dieselbe \
Anfrage zweimal. Die drei laufen gleichzeitig, die Treffer werden gemischt, und \
es kostet dich nur einen einzigen Aufruf von deinem knappen Budget. Dann die \
aussichtsreichsten Treffer lesen und antworten.

Regeln:
- Sei ausfuehrlich: gib ALLE gefundenen Fakten weiter, auch Details am Rand \
(Adresse, Zeiten, Preise, Ausstattung, Einschraenkungen). Deine Antwort ist \
Rohmaterial fuer den Hauptagenten -- was du weglaesst, ist fuer ihn verloren. \
Bis zu 400 Woerter.
- Nenne zu JEDER Angabe die Quelle (Domain), am besten direkt dahinter.
- Rate nie. Was du nicht gefunden hast, schreibst du als "nicht gefunden".
- Liefert `fetch_page` einen `skipped_reason`, nimm eine andere Quelle.
- Kein Vorwort, keine Wiederholung der Frage -- nur das Ergebnis.

Findest du auf Anhieb nichts oder nur Portale ohne Inhalt, gib nicht auf -- \
wechsel die Technik:
- `local_places` fragt die KARTE statt der Suchmaschine. Kleine Laeden, \
Werkstaetten, Praxen und Vereine stehen dort mit Adresse, Telefon und Website, \
auch wenn keine Suchmaschine sie kennt. Fuer alles Oertliche der beste erste \
Griff, nicht der letzte.
- `find_profiles` sucht zu einem Namen alles ausserhalb der eigenen Website: \
Instagram, LinkedIn, Facebook, X, YouTube, Wikipedia, Bewertungsportale. Geht \
es um eine Marke, eine Firma, eine Einrichtung oder eine Person, ist das der \
zweite Griff nach der Karte -- oft steht dort Aktuelleres als auf der Seite, \
und manche haben ueberhaupt nur ein Profil.
- Suchoperatoren: den genauen Namen in Anfuehrungszeichen ("Radladen Meier"), \
`filetype:pdf` fuer Aushaenge, Programme, Satzungen und Amtsblaetter, `site:` \
fuer eine bestimmte Seite oder Endung (site:bremen.de).
- Verzeichnisse nennen oft die Website, die sonst nirgends auftaucht: Das \
Oertliche, Gelbe Seiten, 11880, meinestadt.de, Branchenbuecher, das \
Vereinsregister, die Seite der Gemeinde, der Kreis, die Innung.
- Andere Worte: Ortsteil statt Stadt, Umgangssprache statt Fachwort, die alte \
Bezeichnung, Englisch statt Deutsch.
- Eine gefundene Seite fuehrt oft zur gesuchten: Impressum, Partner, \
Mitglieder, Links, ein Zeitungsartikel ueber sie.
%(role)s
Deine Teilfrage lautet:
%(task)s"""

#: Die Rollen. Vierundzwanzig gleiche Agenten suchen vierundzwanzigmal
#: dasselbe: was oben in den Treffern steht. Wer nach Preisen sucht, braucht
#: aber andere Genauigkeit als wer nach Erfahrungen sucht -- und wer nach
#: Kritik sucht, findet sie nur, wenn er ausdruecklich danach fragt.
#:
#: Die Rolle steht als kurzer Absatz im Prompt und aendert sonst nichts:
#: dieselben Werkzeuge, dasselbe Budget, dieselbe Form der Antwort. Wer
#: keine Rolle bekommt, arbeitet wie bisher -- das ist der Normalfall.
ROLE_EXTRA = {
    "standard": "",
    "zahlen": """
Deine Rolle: Zahlen. Dich interessieren Preise, Gebuehren, Masse, Termine und \
Fristen -- der Rest nur, soweit er eine Zahl einordnet. Jede Zahl bekommt \
Einheit oder Waehrung, den Stand (seit wann gilt sie?) und ihre Quelle. \
Findest du fuer dieselbe Angabe zwei verschiedene Zahlen, nenne BEIDE mit \
ihrer Quelle, statt dich fuer eine zu entscheiden. Rechne nichts um, wenn du \
den Kurs nicht kennst.
""",
    "gegenstimmen": """
Deine Rolle: Gegenstimmen. Du suchst, was in Werbetexten nicht steht: Kritik, \
bekannte Maengel, Beschwerden, Rueckrufe, Einschraenkungen, schlechte \
Erfahrungen. Frag ausdruecklich danach ("... Probleme", "... Kritik", \
"... Erfahrungen negativ") -- von selbst kommt das nicht nach oben. Bleib \
fair: sag dazu, wie verbreitet eine Klage ist und woher sie kommt; ein \
einzelner wuetender Beitrag ist noch kein Befund. Findest du nichts \
Belastbares, schreibst du genau das -- auch das ist ein Ergebnis.
""",
    "tiefe": """
Deine Rolle: Spurensuche. Du bist fuer das zustaendig, was sich nicht einfach \
finden laesst -- den kleinen Laden ohne Website, den Verein ohne \
Suchmaschinen-Eintrag, die Zahl, die nur in einem PDF steht. Fang mit \
`local_places` an, wenn es etwas Oertliches ist, und mit `find_profiles`, \
wenn ein Name im Spiel ist. Danach die Operatoren \
(Anfuehrungszeichen, `filetype:pdf`, `site:`), dann die Verzeichnisse, dann \
die Umwege ueber Nachbarseiten und Zeitungsartikel. Gib nicht nach zwei \
Suchen auf: dass etwas nicht auf Seite eins steht, heisst nicht, dass es das \
nicht gibt. Findest du wirklich nichts, schreib auf, WO du gesucht hast.
""",
    "frisch": """
Deine Rolle: Aktuelles. Dich interessiert der Stand von heute: Neuerungen, \
Aenderungen, Termine, Ankuendigungen. Nimm dafuer `search_news`. Zu jeder \
Angabe gehoert ihr Datum; was aelter als ein Jahr ist, kennzeichnest du als \
alt. Ist etwas seit Jahren unveraendert, sag auch das.
""",
}

#: Wie die Rolle in der Oberflaeche heisst. Leer heisst: keine Marke, das ist
#: der normale Rechercheauftrag.
ROLE_LABELS = {"standard": "", "zahlen": "Zahlen", "gegenstimmen": "Gegenstimmen",
               "frisch": "Aktuelles", "tiefe": "Spurensuche"}

#: Woran eine Rolle zu erkennen ist. Reine Textarbeit, kein Modellaufruf --
#: die Zuordnung darf keine Wartezeit kosten. Gezaehlt werden Treffer; die
#: Rolle mit den meisten gewinnt, bei Gleichstand die weiter oben. Trifft
#: nichts, bleibt es beim normalen Auftrag: lieber keine Rolle als eine
#: falsche, die den Agenten am Thema vorbeisuchen laesst.
_ROLE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("zahlen", ("preis", "kosten", "kostet", "guenstig", "günstig", "teuer", "euro",
                "€", "gebuehr", "gebühr", "tarif", "miete", "gehalt", "rabatt",
                "wie viel", "wieviel", "budget")),
    ("gegenstimmen", ("erfahrung", "kritik", "problem", "nachteil", "beschwerde",
                      "maengel", "mängel", "mangel", "rueckruf", "rückruf",
                      "schwaech", "schwäch", "taugt", "lohnt sich")),
    ("frisch", ("aktuell", "derzeit", "neueste", "neuesten", "momentan", "heute",
                "diese woche", "news", "nachricht", "geaendert", "geändert",
                "seit wann", "neu seit")),
    ("tiefe", ("klein", "lokal", "in der naehe", "in der nähe", "um die ecke",
               "versteckt", "geheimtipp", "unbekannt", "nische", "wer bietet",
               "gibt es ueberhaupt", "gibt es überhaupt", "inhabergefuehrt",
               "inhabergeführt", "familienbetrieb", "verein", "ehrenamt")),
)


def role_for(task: str) -> str:
    """Welche Rolle zu dieser Teilfrage passt -- oder "standard"."""
    text = (task or "").lower()
    beste, punkte = "standard", 0
    for rolle, hinweise in _ROLE_HINTS:
        treffer = sum(1 for wort in hinweise if wort in text)
        if treffer > punkte:
            beste, punkte = rolle, treffer
    return beste


#: Der Pruefer. Er recherchiert nicht neu, er kontrolliert -- und zwar auf
#: anderen Seiten als der Kollege, dessen Ergebnis er vor sich hat. Das
#: erledigt die gemeinsame Domainliste von selbst: was gelesen wurde, ist aus
#: seinen Treffern heraussortiert.
CHECK_PROMPT = """\
Du bist Pruefer. Ein Kollege hat gerade recherchiert, du kontrollierst sein \
Ergebnis -- auf ANDEREN Seiten als er. Die Seiten, die er gelesen hat, sind \
aus deinen Treffern heraussortiert; du siehst also von selbst nur Neues.

Vorgehen: EIN `web_search`-Aufruf mit zwei bis drei Formulierungen ueber \
`queries`, dann die aussichtsreichsten Treffer lesen.

Pruefen sollst du die harten Angaben: Zahlen, Preise, Termine, \
Oeffnungszeiten, Versionen, Namen, Adressen. Meinungen und Einschaetzungen \
pruefst du nicht -- die kann man nicht nachschlagen.

Antworte mit HOECHSTENS 150 Woertern und beginne mit genau einem dieser Woerter:
BESTAETIGT -- alles, was du pruefen konntest, stimmt.
ABWEICHUNG -- mindestens eine Angabe steht anderswo anders. Nenne sie: was \
stand beim Kollegen, was steht bei dir, und beide Quellen.
UNKLAR -- du hast dazu nichts Belastbares gefunden.

Danach in Stichpunkten, was du geprueft hast, je mit Quelle. Erfinde keinen \
Widerspruch, damit die Pruefung etwas hergibt: BESTAETIGT ist ein gutes \
Ergebnis, und "nirgends bestaetigt" ist etwas anderes als "falsch".

Teilfrage des Kollegen:
%(task)s

Sein Ergebnis:
%(summary)s"""

#: Die drei Urteile, in der Reihenfolge, in der sie zaehlen.
VERDICTS = ("ABWEICHUNG", "UNKLAR", "BESTAETIGT")


def verdict_of(text: str) -> str:
    """Das Urteil aus der Antwort des Pruefers -- oder "" wenn keins dasteht."""
    anfang = (text or "").strip().lstrip("*# ").upper()[:40]
    for urteil in VERDICTS:
        if anfang.startswith(urteil):
            return urteil
    # Manche Modelle stellen einen Satz voran. Dann suchen wir das Wort im
    # ersten Absatz -- aber nur dort, sonst faengt man es aus der Begruendung.
    kopf = (text or "").strip().upper()[:200]
    for urteil in VERDICTS:
        if urteil in kopf:
            return urteil
    return ""


PLANNER_PROMPT = """\
Du bist die Vorstufe eines Rechercheagenten. Entscheide zweierlei und antworte \
NUR mit JSON.

1. Braucht die Nachricht eine Web-Recherche? Blosse Konversation (Gruss, Dank, \
Meinung, Frage an dich selbst) braucht keine. Nachfragen zu einer laufenden \
Recherche brauchen eine.
2. Wenn ja: zerlege sie in %(limit)d eigenstaendige Teilfragen -- so viele, nicht \
weniger. Fuer jede arbeitet ein eigener Agent auf eigenen Seiten; eine Teilfrage \
weniger ist eine Seite weniger, die jemand liest.

So kommst du auf %(limit)d, ohne dich zu wiederholen: die Sache selbst, dann ihre \
Seiten -- Preise und Kosten, Erfahrungen und Kritik, aktuelle Aenderungen, \
offizielle Angaben, Alternativen, Tests, Bedingungen und Einschraenkungen, \
Oeffnungszeiten und Erreichbarkeit -- und bei mehreren Kandidaten, Orten oder \
Zeitraeumen je einer davon.

Jede Teilfrage muss FUER SICH verstaendlich sein: Ort, Produkt, Zeitraum und \
Kriterium gehoeren hinein. Steht oben ein Ortsfilter, gehoert der Ort in JEDE \
Teilfrage. Und keine zwei Teilfragen duerfen dasselbe fragen -- zwei gleiche \
Auftraege lesen dieselben Seiten.

Format: {"recherche": true, "teilfragen": ["...", "..."]}
Bei blosser Konversation: {"recherche": false, "teilfragen": []}

%(context)sNachricht: %(question)s"""

#: Blickwinkel zum Auffuellen. Kommt der Planer mit weniger Teilfragen zurueck
#: als Agenten bereitstehen, wird der Rest daraus gebildet: dieselbe Frage,
#: anderer Blickwinkel. Das ist keine Verlegenheitsloesung -- genau diese
#: Seiten fehlen sonst in der Antwort, weil niemand danach gesucht hat. Die
#: Formulierungen sind so gewaehlt, dass `role_for` ihnen von selbst die
#: passende Rolle gibt: Preise werden zu Zahlen, Kritik zu Gegenstimmen.
ANGLES = (
    "Preise, Kosten und Gebuehren",
    "Erfahrungen, Kritik und bekannte Probleme",
    "aktuelle Aenderungen und Neuigkeiten",
    "offizielle Angaben der Anbieter oder Behoerden",
    "Alternativen und womit man vergleichen sollte",
    "Tests, Bewertungen und Vergleiche",
    "Oeffnungszeiten, Anfahrt und Erreichbarkeit",
    "Voraussetzungen, Bedingungen und Einschraenkungen",
    "Ausstattung, Umfang und technische Daten",
    "wer es anbietet und wo es das gibt",
    "haeufige Fragen und Missverstaendnisse",
    "Fristen, Termine und Zeitraeume",
    "Foerderungen, Rabatte und Zuschuesse",
    "Erfahrungsberichte aus Foren und Gruppen",
)

#: Und WO gesucht wird. Reicht die Zahl der Blickwinkel nicht (bei `/max` sind
#: es vierundvierzig Agenten), entsteht die zweite Haelfte aus dieser Liste:
#: dieselbe Frage, anderer Ort zum Suchen. Die Reihenfolge ist die aus der
#: Recherche zu schwer auffindbaren Dingen -- Karte und Verzeichnisse zuerst,
#: weil sie finden, was keine Suchmaschine kennt.
SOURCES = (
    "in der Karte (OpenStreetMap) statt in der Suchmaschine",
    "in Verzeichnissen wie Das Oertliche, Gelbe Seiten, 11880, meinestadt.de",
    "auf den Seiten der Betreiber selbst, nicht auf Portalen",
    "auf offiziellen Seiten: Gemeinde, Kreis, Kammer, Verband",
    "in PDFs und Dokumenten (filetype:pdf): Aushaenge, Programme, Satzungen",
    "in der oertlichen Presse und in Wochenblaettern",
    "in Foren, Gruppen und Kommentaren",
    "ueber Nachbarseiten: Impressum, Partner, Mitglieder, Links",
)


def spread_tasks(question: str, tasks: Sequence[Any], limit: int) -> list[Task]:
    """Fuellt die Teilfragen auf *limit* auf.

    Der Planer liefert oft drei oder vier Teilfragen, auch wenn zwoelf oder
    vierundzwanzig Agenten bereitstehen -- und dann suchen zwoelf Agenten
    nicht, sondern vier. Hier kommen die fehlenden dazu: erst die Frage unter
    einem anderen Blickwinkel, dann die Blickwinkel auf den Teilfragen selbst.
    Doppeltes faellt raus; mehr als sich sinnvoll bilden laesst, wird nicht
    erfunden.
    """
    limit = max(1, int(limit))
    out = _distinct(tasks)[:limit]
    kern = " ".join((question or "").split())[:200]
    if len(out) >= limit or not kern:
        return out
    for angle in ANGLES:
        if len(out) >= limit:
            return out
        out.append(Task(text=f"{kern} -- {angle}"))
    # Immer noch Platz: dieselben Blickwinkel auf die Auftraege des Planers.
    for angle in ANGLES:
        for task in _distinct(tasks):
            if len(out) >= limit:
                break
            if task.text.lower() == kern.lower():
                continue
            out.append(Task(text=f"{task.text} -- {angle}"))
        if len(out) >= limit:
            break
    # Und wenn das immer noch nicht reicht (bei `/max` sind es vierundvierzig
    # Agenten und manchmal nur ein Auftrag), kommt die zweite Achse dazu:
    # nicht WAS, sondern WO gesucht wird.
    for quelle in SOURCES:
        for angle in ANGLES:
            if len(out) >= limit:
                break
            out.append(Task(text=f"{kern} -- {angle}, gesucht {quelle}", angle=quelle))
        if len(out) >= limit:
            break
    return _distinct(out)[:limit]

#: Ein knappes Schema haelt kleine Modelle bei der Sache und beendet die
#: Ausgabe frueher -- das ist der Loewenanteil der Wartezeit.
PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recherche": {"type": "boolean"},
        "teilfragen": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["recherche", "teilfragen"],
}


def plan_request(
    question: str,
    settings: Settings,
    context: str = "",
    limit: int = 4,
) -> tuple[bool, list[str]]:
    """Entscheidet Recherche-oder-Chat UND zerlegt -- in EINEM Aufruf.

    Vorher waren das zwei Aufrufe auf zwei verschiedenen Modellen: die
    Vorpruefung auf dem kleinen, die Planung auf dem grossen. Auf einer
    Karte, die nur eines gleichzeitig haelt, kostete allein der Wechsel
    mehr als beide Aufrufe zusammen. Jetzt laeuft beides auf dem kleinen
    Modell, ohne Denk-Modus und mit erzwungenem JSON.

    Returns:
        (braucht_recherche, teilfragen). Im Zweifel `(True, [question])` --
        lieber einmal zu viel recherchiert als eine Frage verschluckt.
    """
    import litellm

    litellm.suppress_debug_info = True
    model = settings.effective_subagent_model
    prompt = PLANNER_PROMPT % {
        "limit": max(1, limit),
        "question": question.strip()[:600],
        "context": f"Bisheriges Gespraech:\n{context}\n\n" if context.strip() else "",
    }
    try:
        with paced(model):
            response = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                timeout=max(2.0, settings.planner_timeout),
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "plan", "schema": PLANNER_SCHEMA},
                },
                **settings.fast_kwargs_for(model),
            )
        raw = (response.choices[0].message.content or "").strip()
    except Exception:
        return True, _located([question.strip()], settings.location)

    needs, tasks = _parse_plan(raw, question, limit)
    return needs, _located(tasks, settings.location)


def _located(tasks: list[str], location: str) -> list[str]:
    """Setzt den Ort in jede Teilfrage, die ihn nicht schon nennt.

    Der Subagent sieht das Gespraech nicht und den Ortsfilter erst recht
    nicht -- fuer ihn ist die Teilfrage alles, was es gibt. Steht der Ort
    nicht drin, sucht er im ganzen Sprachraum.
    """
    from aquaticy.queries import with_place

    return [with_place(task, location) for task in tasks]


def _parse_plan(raw: str, question: str, limit: int) -> tuple[bool, list[str]]:
    """Liest die Planer-Antwort; faellt bei Unklarheit auf Recherche zurueck."""
    payload: Any = None
    if raw:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            try:
                payload = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                payload = None

    if not isinstance(payload, dict):
        # Vielleicht kam nur ein nacktes Array -- auch das nehmen wir.
        tasks = _parse_task_list(raw)
        return True, (tasks[:limit] if tasks else [question.strip()])

    if payload.get("recherche") is False:
        return False, []

    tasks = [
        str(item).strip() for item in (payload.get("teilfragen") or []) if str(item).strip()
    ]
    return True, (tasks[:limit] if tasks else [question.strip()])


def plan_subtasks(
    question: str,
    settings: Settings,
    context: str = "",
    limit: int = 4,
) -> list[str]:
    """Nur die Zerlegung -- fuer Aufrufer, die die Entscheidung schon kennen."""
    _, tasks = plan_request(question, settings, context, limit)
    return tasks or [question.strip()]


def _parse_task_list(raw: str) -> list[str]:
    """Zieht ein JSON-Array aus der Antwort des Modells."""
    if not raw:
        return []
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _subagent_kwargs(settings: Settings, model: str) -> dict[str, Any]:
    """Aufrufargumente fuer einen Subagenten.

    Volles Kontextfenster (sie lesen ganze Seiten), aber ohne Denk-Modus:
    eine eng umrissene Teilfrage braucht keine seitenlange Ueberlegung, und
    bei vierundvierzig Agenten summiert sich das zu Minuten. Dazu ein
    Zeitlimit -- ein haengender Agent darf die Recherche nicht aufhalten,
    die anderen dreiundvierzig sind ja laengst zurueck.
    """
    kwargs = settings.llm_kwargs_for(model)
    from aquaticy.config import provider_of

    if provider_of(model) in ("ollama", "ollama_chat"):
        kwargs["reasoning_effort"] = "disable"
    kwargs.setdefault("timeout", 90.0)
    kwargs.setdefault("drop_params", True)
    return kwargs


@dataclass
class Task:
    """Ein Auftrag, wie der Master ihn vergibt.

    Frueher war ein Auftrag eine Zeichenkette. Das reichte, solange alle
    Agenten dasselbe taten. Jetzt gibt der Master jedem seine eigene Rolle
    ("achte auf die Oeffnungszeiten", "such die Betreiber, nicht die
    Portale") -- und zwei Auftraege sind schwer genug fuer die starken
    Agenten. Beides gehoert an den Auftrag, nicht in eine Parallelliste.
    """

    text: str
    #: Die Rolle in eigenen Worten. Leer heisst: die aus dem Wortlaut
    #: abgeleitete Rolle (siehe `role_for`).
    angle: str = ""
    #: Auf das starke Modell, mit groesserem Budget.
    strong: bool = False

    @property
    def role(self) -> str:
        """Die bekannte Rolle -- fuer die Technik-Hinweise im Auftrag."""
        return role_for(f"{self.text} {self.angle}")


def as_task(item: Any) -> Task:
    """Macht aus einer Zeichenkette, einem dict oder einem Task einen Task."""
    if isinstance(item, Task):
        return item
    if isinstance(item, dict):
        return Task(
            text=str(item.get("text") or item.get("auftrag") or item.get("task") or "").strip(),
            angle=str(item.get("angle") or item.get("rolle") or "").strip(),
            strong=bool(item.get("strong") or item.get("schwer")),
        )
    return Task(text=str(item or "").strip())


@dataclass
class SubagentResult:
    """Was ein Subagent herausgefunden hat."""

    task: str
    summary: str = ""
    sources: list[dict[str, str]] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)
    tool_calls: int = 0
    error: str = ""
    #: Mit welcher Rolle gearbeitet wurde -- fuer die Anzeige und damit der
    #: Hauptagent weiss, unter welchem Blickwinkel etwas gefunden wurde.
    role: str = "standard"
    #: Die Rolle in den Worten des Masters, falls er eine vergeben hat.
    angle: str = ""
    #: Lief er auf dem starken Modell?
    strong: bool = False
    #: Was der Pruefer dazu gesagt hat, und sein Urteil in einem Wort. Leer,
    #: wenn nicht geprueft wurde -- das ist der Normalfall.
    check: str = ""
    verdict: str = ""
    check_sources: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"task": self.task, "role": self.role}
        if self.angle:
            payload["angle"] = self.angle
        if self.strong:
            payload["strong"] = True
        if self.error:
            payload["error"] = self.error
            return payload
        payload["summary"] = self.summary
        payload["sources"] = [source.get("url", "") for source in self.sources]
        payload["searches"] = self.searches
        if self.check:
            payload["check"] = self.check
            payload["verdict"] = self.verdict
            payload["check_sources"] = [
                source.get("url", "") for source in self.check_sources
            ]
        return payload


def _run_one(
    task: str,
    settings: Settings,
    cache: Cache | None,
    on_event: EventHook | None,
    toolbox: Toolbox | None = None,
    stop: threading.Event | None = None,
    role: str = "standard",
    budget: int = 0,
    prompt: str = "",
    kind: str = "subagent",
    angle: str = "",
    model: str = "",
) -> SubagentResult:
    """Fuehrt einen Subagenten aus -- eigene Toolbox, eigenes Budget.

    Args:
        role: Der Blickwinkel (siehe ROLE_EXTRA). Aendert nur den Prompt.
        budget: Werkzeug-Aufrufe fuer diesen Agenten. 0 = die Einstellung.
        prompt: Ein eigener Auftrag statt des Rechercheauftrags -- so laeuft
            der Pruefer durch dieselbe Schleife.
        kind: Wofuer die Meldung am Ende steht: "subagent" oder "check".
        angle: Die Rolle in den Worten des Masters. Steht zusaetzlich zur
            Technik der bekannten Rolle im Auftrag.
        model: Ein anderes Modell als das eingestellte -- so laufen die
            starken Agenten auf dem starken Modell.
    """
    import litellm

    litellm.suppress_debug_info = True
    role = role if role in ROLE_EXTRA else "standard"
    result = SubagentResult(task=task, role=role, angle=angle, strong=bool(model))
    box = toolbox or Toolbox(settings, cache=cache, on_event=None)
    owns_box = toolbox is None

    # Die Rolle des Masters steht ueber der bekannten: sie ist auf diesen
    # einen Auftrag gemuenzt, die andere bringt die Technik mit.
    rollentext = ROLE_EXTRA[role]
    if angle.strip():
        rollentext = f"\nDein Auftrag im Team: {angle.strip()}\n{rollentext}"
    auftrag = prompt or SUBAGENT_PROMPT % {"task": task, "role": rollentext}
    messages: list[dict[str, Any]] = [{"role": "user", "content": auftrag}]
    budget = max(1, int(budget) or settings.subagent_budget)
    used = 0
    # Faellt das kleine Subagenten-Modell aus (nicht geladen, abgestuerzt),
    # uebernimmt das Hauptmodell -- langsamer, aber die Teilfrage wird
    # beantwortet statt verworfen.
    model_in_use = model or settings.effective_subagent_model

    try:
        while used < budget:
            if stop is not None and stop.is_set():
                # Abgebrochen. Was bis hierher gefunden wurde, geht mit --
                # der Hauptagent kann es noch verwenden.
                result.error = result.error or "Abgebrochen."
                break
            try:
                with paced(model_in_use):
                    response = litellm.completion(
                        model=model_in_use,
                        messages=messages,
                        tools=TOOL_SCHEMAS,
                        tool_choice="auto",
                        **_subagent_kwargs(settings, model_in_use),
                    )
            except Exception as exc:
                if model_in_use != settings.model:
                    if on_event:
                        on_event(
                            "fallback",
                            {"source": model_in_use, "target": settings.model},
                        )
                    model_in_use = settings.model
                    continue
                result.error = f"{type(exc).__name__}: {exc}"
                return result

            message = response.choices[0].message
            calls = getattr(message, "tool_calls", None) or []
            content = message.content or ""

            if not calls:
                result.summary = content.strip()
                break

            from aquaticy.agent import repair_tool_calls, run_calls

            call_dicts = repair_tool_calls(
                [
                    {
                        "id": getattr(call, "id", None) or f"sub_{index}",
                        "type": "function",
                        "function": {
                            "name": getattr(call.function, "name", "") or "",
                            "arguments": getattr(call.function, "arguments", "") or "{}",
                        },
                    }
                    for index, call in enumerate(calls)
                ]
            )
            messages.append(
                {"role": "assistant", "content": content, "tool_calls": call_dicts}
            )

            # Was ins Budget passt, laeuft nebeneinander; der Rest bekommt
            # eine Absage. Auch abgeschnittene Aufrufe BRAUCHEN eine Antwort
            # -- ein Tool-Call ohne Antwort macht den Verlauf ungueltig und
            # der abschliessende Aufruf wuerde abgelehnt.
            room = max(0, budget - used)
            doable, cut = call_dicts[:room], call_dicts[room:]
            used += len(doable)

            def answer(call: dict[str, Any]) -> dict[str, Any]:
                name = call["function"]["name"]
                raw = call["function"]["arguments"] or "{}"
                try:
                    arguments = json.loads(raw)
                except json.JSONDecodeError:
                    arguments = {}
                try:
                    payload = box.call(name, arguments if isinstance(arguments, dict) else {})
                except Exception as exc:
                    payload = {"error": f"{type(exc).__name__}: {exc}"}
                return {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": json.dumps(payload, ensure_ascii=False)[
                        : max(1000, settings.max_tool_chars)
                    ],
                }

            messages.extend(run_calls(doable, answer))
            messages.extend(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["function"]["name"],
                    "content": json.dumps(
                        {"error": "Werkzeug-Budget aufgebraucht."}, ensure_ascii=False
                    ),
                }
                for call in cut
            )

        if not result.summary:
            # Budget aufgebraucht -- ein letzter Aufruf ohne Werkzeuge.
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Gib jetzt dein Urteil ab: ein Wort (BESTAETIGT, "
                        "ABWEICHUNG oder UNKLAR), danach in Stichpunkten, was du "
                        "geprueft hast, je mit Quelle. Was du nicht pruefen "
                        "konntest, faellt unter UNKLAR."
                        if kind == "check"
                        else "Fasse jetzt zusammen, was du gefunden hast -- "
                        "vollstaendig und mit allen Details samt Quelle je Angabe. "
                        "Offene Punkte kennzeichnest du als 'nicht gefunden'."
                    ),
                }
            )
            try:
                with paced(model_in_use):
                    response = litellm.completion(
                        model=model_in_use,
                        messages=messages,
                        **_subagent_kwargs(settings, model_in_use),
                    )
                result.summary = (response.choices[0].message.content or "").strip()
            except Exception as exc:
                result.error = f"{type(exc).__name__}: {exc}"

        result.sources = list(box.stats.sources)
        result.searches = list(box.stats.searches)
        result.tool_calls = used
        return result
    finally:
        if owns_box:
            box.close()
        if on_event:
            on_event(
                f"{kind}_done",
                {
                    "task": task,
                    "tool_calls": used,
                    "error": result.error,
                    "role": result.role,
                    "verdict": verdict_of(result.summary) if kind == "check" else "",
                },
            )


#: Ab wie vielen gleichzeitigen Agenten sie versetzt starten -- und um wie
#: viel. Nicht der Hoeflichkeit wegen: vierundzwanzig Anfragen in derselben
#: Millisekunde sind fuer jeden Anbieter ein Ausschlag, auf den er mit einer
#: Ratenbegrenzung antwortet. Ein Subagent, der die abbekommt, faellt aus --
#: seine Teilfrage bleibt unbeantwortet. Ueber knapp zwei Sekunden verteilt
#: passiert das nicht, und neben einer Recherche von vielen Sekunden faellt
#: der Versatz nicht auf. Bis vier Agenten bleibt alles wie bisher.
STAGGER_AFTER = 4
LAUNCH_STAGGER = 0.08
MAX_LAUNCH_DELAY = 2.0

#: Wie viel mehr Werkzeug-Budget ein starker Agent bekommt. Er sitzt an dem,
#: was die anderen nicht gefunden haben -- da reicht eine Suche selten.
STRONG_EXTRA_BUDGET = 6


def _distinct(tasks: Sequence[Any]) -> list[Task]:
    """Die Auftraege ohne Leerzeilen und ohne Wiederholungen.

    Bei vier Auftraegen faellt ein doppelter kaum auf. Bei vierundvierzig
    schon: zwei gleichlautende belegen zwei Agenten, lesen dieselben Seiten
    und melden dasselbe zurueck -- bezahlt wird beides. Verglichen wird
    nachlaessig (Kleinschreibung, zusammengefasste Leerzeichen, kein
    Satzende), weil der Master denselben Auftrag gern zweimal leicht anders
    schreibt.
    """
    out: list[Task] = []
    gesehen: set[str] = set()
    for eintrag in tasks:
        task = as_task(eintrag)
        task.text = " ".join(task.text.split())
        if not task.text:
            continue
        marke = task.text.lower().rstrip(".!?")
        if marke in gesehen:
            continue
        gesehen.add(marke)
        out.append(task)
    return out


def run_subagents(
    tasks: Sequence[Any],
    settings: Settings,
    cache: Cache | None = None,
    on_event: EventHook | None = None,
    parallel: int = 2,
    stop: threading.Event | None = None,
    limit: int | None = None,
    checkers: int = 0,
    budget: int = 0,
    strong_model: str = "",
) -> list[SubagentResult]:
    """Bearbeitet *tasks* nebenlaeufig und gibt die Ergebnisse in Reihenfolge zurueck.

    Args:
        tasks: Die Teilfragen.
        settings: Laufzeit-Einstellungen (Modell, Budget je Subagent).
        cache: Gemeinsamer Cache -- doppelte Suchen kosten so nichts.
        on_event: Callback fuer die Live-Anzeige.
        parallel: Wie viele gleichzeitig. Bei lokalen Modellen bringt mehr als
            zwei wenig, weil sie ohnehin nacheinander rechnen.
        stop: Wird sie gesetzt, brechen noch nicht begonnene Teilfragen ab und
            laufende enden nach ihrem naechsten Schritt.
        limit: Obergrenze fuer diesen Aufruf. Ohne Angabe die Einstellung --
            der Pro-Modus hebt sie fuer seinen Turn an.
        checkers: Wie viele Pruefer nebenher mitlaufen. Sie nehmen sich jedes
            fertige Ergebnis vor und suchen auf ANDEREN Seiten nach
            Bestaetigung oder Widerspruch. 0 = keine Gegenprobe.
        budget: Werkzeug-Aufrufe je Agent. 0 = die Einstellung.
        strong_model: Modell fuer die als `strong` markierten Auftraege. Leer
            heisst: alle arbeiten mit demselben Modell.
    """
    ceiling = max(1, int(limit if limit is not None else settings.max_subagents))
    clean = _distinct(tasks)[:ceiling]
    if not clean:
        return []

    # Die Rolle steckt im Auftrag: wer nach Preisen fragt, bekommt den
    # Agenten, der auf Zahlen achtet. Das kostet keinen Modellaufruf.
    roles = [task.role for task in clean]
    # Nie mehr Pruefer als Rechercheure gleichzeitig: bei einem lokalen
    # Modell laufen zwei Agenten nebeneinander, und vier Pruefer obendrauf
    # waeren sechs Anfragen an dieselbe Grafikkarte -- die rechnet sie
    # ohnehin nacheinander, es wuerde nur alles langsamer.
    checkers = max(0, min(int(checkers), max(1, parallel)))
    if on_event:
        on_event(
            "subagents",
            {
                "tasks": [task.text for task in clean],
                "roles": roles,
                "angles": [task.angle for task in clean],
                "strong": [task.strong for task in clean],
            },
        )
        if checkers:
            on_event("checkers", {"count": checkers})

    # Ein gemeinsamer Fetcher fuer alle: dessen Drossel und robots.txt-Cache
    # gelten damit ueber die Subagenten hinweg. Mit je eigenem Fetcher wuerden
    # zwei parallele Subagenten dieselbe Domain gleichzeitig treffen -- und
    # unser Versprechen von einem Request pro Sekunde und Domain waere hin.
    from aquaticy.fetch import Fetcher

    shared_fetcher = Fetcher(
        user_agent=settings.user_agent,
        timeout=settings.fetch_timeout,
        delay_seconds=settings.request_delay_seconds,
        enable_browser=settings.enable_playwright,
    )
    boxes = [Toolbox(settings, cache=cache, fetcher=shared_fetcher) for _ in clean]
    # Eine Menge fuer alle: was ein Agent gelesen hat, ist fuer die anderen
    # verbraucht. Ohne das laufen drei Teilfragen zum selben Thema auf
    # dieselben zwei Seiten zu und die Zerlegung bringt keine Breite.
    # Zugegriffen wird darauf aus mehreren Threads; set.add und die Pruefung
    # beim Filtern sind jeweils ein einzelner Bytecode-Schritt, da braucht es
    # kein Schloss.
    shared_domains: set[str] = set()
    for box in boxes:
        box.avoid_domains = shared_domains
        box.claim_sources = True
    def pruefe(result: SubagentResult) -> None:
        """Nimmt sich ein fertiges Ergebnis vor -- auf anderen Seiten.

        Der Pruefer bekommt eine eigene Toolbox, aber dieselbe Domainliste:
        was der Kollege gelesen hat, ist aus seinen Treffern heraussortiert.
        Er prueft also zwangslaeufig woanders -- genau das macht die
        Gegenprobe aus.
        """
        if stop is not None and stop.is_set():
            return
        if result.error or not result.summary.strip():
            return  # nichts da, was sich pruefen liesse
        if on_event:
            on_event("check", {"task": result.task})
        box = Toolbox(settings, cache=cache, fetcher=shared_fetcher)
        box.avoid_domains = shared_domains
        box.claim_sources = True
        try:
            geprueft = _run_one(
                result.task,
                settings,
                cache,
                on_event,
                toolbox=box,
                stop=stop,
                budget=budget,
                kind="check",
                prompt=CHECK_PROMPT
                % {"task": result.task, "summary": result.summary[:4000]},
            )
        finally:
            box.close()
        if geprueft.error:
            return
        result.check = geprueft.summary
        result.verdict = verdict_of(geprueft.summary)
        result.check_sources = list(geprueft.sources)
        # Was der Pruefer gesucht hat, zaehlt mit: sonst steht am Ende eine
        # Zahl unter der Antwort, die kleiner ist als das, was wirklich lief.
        result.searches.extend(geprueft.searches)

    try:
        # Die Pruefer bekommen eigene Faeden: sie sollen arbeiten, WAEHREND
        # die anderen noch suchen. Ist die Recherche durch, stehen ihre Faeden
        # dem Ruecksstau an Pruefungen zur Verfuegung -- aus vier Pruefern
        # werden dann alle.
        def einer(task: Task, box: Toolbox) -> SubagentResult:
            """Ein Agent. Die starken laufen auf dem starken Modell und
            bekommen mehr Budget -- sie sitzen an den schweren Auftraegen."""
            stark = bool(task.strong and strong_model)
            return _run_one(
                task.text,
                settings,
                cache,
                on_event,
                toolbox=box,
                stop=stop,
                role=task.role,
                angle=task.angle,
                budget=(budget + STRONG_EXTRA_BUDGET) if stark else budget,
                model=strong_model if stark else "",
            )

        workers = max(1, min(parallel, len(clean)) + checkers)
        if workers == 1:
            return [einer(task, box) for task, box in zip(clean, boxes, strict=True)]

        offene_pruefungen: list[Any] = []
        schloss = threading.Lock()

        def start(position: int, task: Task, box: Toolbox) -> SubagentResult:
            if position and workers > STAGGER_AFTER:
                time.sleep(min(position * LAUNCH_STAGGER, MAX_LAUNCH_DELAY))
            ergebnis = einer(task, box)
            if checkers:
                # Sofort weiterreichen, nicht erst am Ende: die Pruefung des
                # ersten Ergebnisses laeuft, waehrend die letzte Teilfrage
                # noch sucht.
                auftrag = pool.submit(pruefe, ergebnis)
                with schloss:
                    offene_pruefungen.append(auftrag)
            return ergebnis

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(start, position, task, box)
                for position, (task, box) in enumerate(zip(clean, boxes, strict=True))
            ]
            results = [future.result() for future in futures]
            # Der Ausstieg aus dem `with` wartet auf die Pruefungen, die noch
            # laufen oder in der Warteschlange stehen. Erst danach stehen die
            # Vermerke in den Ergebnissen.
        # Eine gescheiterte Pruefung aendert nichts an der Recherche -- still
        # verschwinden soll sie trotzdem nicht.
        for auftrag in offene_pruefungen:
            fehler = auftrag.exception()
            if fehler is not None and on_event:
                on_event("error", {"message": f"Pruefung fehlgeschlagen: {fehler}"})
        return results
    finally:
        shared_fetcher.close()
