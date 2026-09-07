"""Der Master: er beauftragt, bewertet und schickt nach.

Bis hierher war die Zerlegung ein Vorgang ohne Aufsicht: ein Planer schnitt
die Frage in Teile, die Agenten liefen los, und was zurueckkam, wurde
genommen, wie es war. Kam von einem Agenten nichts -- die Seite war weg, die
Suche ging ins Leere, das Thema lag daneben --, dann fehlte das eben in der
Antwort. Gemerkt hat es niemand ausser dem Leser.

Der Master schliesst diese Luecke. Er ist ein eigener Modellaufruf auf dem
staerksten erreichbaren Modell und macht drei Dinge:

1. **Beauftragen.** Er entscheidet, wie viele Agenten die Frage braucht, und
   gibt jedem einen eigenen Auftrag UND eine eigene Rolle -- in seinen Worten,
   nicht aus einer Liste. Zwei Auftraege darf er als "schwer" markieren; die
   gehen an die starken Agenten.
2. **Bewerten.** Wenn alle zurueck sind, liest er die Rueckmeldungen und sagt,
   was traegt und was nicht: leer, am Thema vorbei, nur Portalseiten ohne
   Inhalt, offensichtlich veraltet.
3. **Nachschicken.** Was fehlt, vergibt er neu -- mit anderer Technik als beim
   ersten Mal. Der Nutzer sieht das; eine Nachrunde ist kein Makel, sondern
   der Grund, warum am Ende etwas dasteht.

Alles hier ist JSON-erzwungen und faellt bei Unklarheit auf etwas Brauchbares
zurueck: der Master darf die Recherche verbessern, aber niemals verhindern.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from aquaticy.config import Settings
from aquaticy.pace import paced
from aquaticy.subagents import Task, as_task

#: Hoechstens so viele Nachrunden. Zwei sind das Mass: nach der zweiten liegt
#: es nicht mehr an der Formulierung, sondern daran, dass es die Information
#: im Netz nicht gibt -- und dann gehoert genau das in die Antwort.
MAX_ROUNDS = 2

#: So viele Agenten bekommt eine Nachrunde hoechstens. Sie soll Luecken
#: schliessen, nicht die ganze Recherche wiederholen.
MAX_RETRY = 8

PLAN_PROMPT = """\
Du leitest eine Rechercheeinheit. Vor dir liegt eine Frage; deine Agenten
suchen im Web, jeder auf eigenen Seiten, alle gleichzeitig.

Dir stehen bis zu %(limit)d Agenten zur Verfuegung. %(force)s

Jeder Agent bekommt von dir zweierlei:
- einen AUFTRAG: ein Satz, fuer sich verstaendlich, mit Ort, Produkt, Zeitraum
  und Kriterium. Der Agent sieht das Gespraech nicht -- was nicht im Auftrag
  steht, weiss er nicht.
- eine ROLLE: drei bis acht Woerter, was genau dieser Agent beitraegt
  ("achtet auf Preise und deren Stand", "sucht Betreiberseiten statt Portale",
  "geht die Kritik durch"). Keine zwei Agenten mit derselben Rolle.

Regeln:
- Ein Agent, ein Feld. Ueberschneiden sich zwei Auftraege, schaerf einen nach.
- Zerlege nach Sachgebieten, nicht nach Formulierungen. "Cafe A", "Cafe B",
  "Cafe C" sind drei Felder; "gute Cafes", "schoene Cafes" ist zweimal
  dasselbe.
- Denk an die Seiten, die man leicht vergisst: Preise und Kosten, Erfahrungen
  und Kritik, aktuelle Aenderungen, offizielle Angaben, Alternativen, Tests,
  Bedingungen und Einschraenkungen, Anfahrt und Oeffnungszeiten.
- Hoechstens %(strong)d Auftraege darfst du mit "schwer": true markieren. Die
  gehen an die starken Agenten -- nimm dafuer das, was am schwersten zu finden
  ist (kleine oertliche Anbieter ohne Website, Zahlen aus Dokumenten,
  Widerspruechliches), nicht das Wichtigste.
- Steht unten ein Ortsfilter, gehoert der Ort in JEDEN Auftrag.

Antworte NUR mit JSON:
{"plan": "ein Satz, was du vorhast",
 "agenten": [{"auftrag": "...", "rolle": "...", "schwer": false}]}

%(context)sFrage: %(question)s"""

REVIEW_PROMPT = """\
Du leitest eine Rechercheeinheit. Deine Agenten sind zurueck. Lies ihre
Rueckmeldungen und sag, ob die Recherche traegt.

Nicht brauchbar ist eine Rueckmeldung, die leer ist, am Thema vorbeigeht, nur
Portalseiten ohne Inhalt nennt, offensichtlich veraltet ist oder statt einer
Antwort erklaert, warum nichts zu finden war.

Fehlt etwas, vergib NEUE Auftraege -- hoechstens %(retry)d, jeder mit einer
anderen Technik als beim ersten Mal:
- die Karte statt der Suchmaschine (kleine Laeden, Werkstaetten, Vereine),
- der genaue Name in Anfuehrungszeichen, `filetype:pdf`, `site:`,
- Verzeichnisse (Das Oertliche, Gelbe Seiten, 11880, meinestadt.de), die
  Seite der Gemeinde, das Amtsblatt, das Vereinsregister,
- andere Worte: Ortsteil statt Stadt, Umgangssprache, die alte Bezeichnung,
- der Umweg ueber eine Nachbarseite: Impressum, Partner, Mitglieder, Presse.

Sei streng, aber erfinde keine Luecke: ist alles beisammen, sag "gut" und gib
keine neuen Auftraege. Was es nachweislich nicht gibt, ist keine Luecke,
sondern ein Ergebnis.

Antworte NUR mit JSON:
{"urteil": "gut" oder "luecken",
 "fehlt": ["was noch fehlt, je ein kurzer Satz"],
 "nachrunde": [{"auftrag": "...", "rolle": "...", "schwer": false}]}

Frage: %(question)s

Rueckmeldungen:
%(findings)s"""

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plan": {"type": "string"},
        "agenten": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "auftrag": {"type": "string"},
                    "rolle": {"type": "string"},
                    "schwer": {"type": "boolean"},
                },
                "required": ["auftrag", "rolle"],
            },
        },
    },
    "required": ["plan", "agenten"],
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "urteil": {"type": "string"},
        "fehlt": {"type": "array", "items": {"type": "string"}},
        "nachrunde": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "auftrag": {"type": "string"},
                    "rolle": {"type": "string"},
                    "schwer": {"type": "boolean"},
                },
                "required": ["auftrag", "rolle"],
            },
        },
    },
    "required": ["urteil"],
}


@dataclass
class Mission:
    """Was der Master vorhat und wen er dafuer losschickt."""

    tasks: list[Task] = field(default_factory=list)
    plan: str = ""
    #: Kam der Master gar nicht zu Wort (Ausfall, Zeitlimit)?
    fallback: bool = False


@dataclass
class Review:
    """Was der Master von den Rueckmeldungen haelt."""

    ok: bool = True
    missing: list[str] = field(default_factory=list)
    retries: list[Task] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return "gut" if self.ok else "luecken"


def _json_call(
    prompt: str,
    settings: Settings,
    model: str,
    schema: dict[str, Any],
    name: str,
    max_tokens: int,
) -> dict[str, Any]:
    """Ein Modellaufruf mit erzwungenem JSON. Wirft nichts -- gibt {} zurueck."""
    import litellm

    litellm.suppress_debug_info = True
    try:
        with paced(model):
            response = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                timeout=max(10.0, settings.planner_timeout * 3),
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": name, "schema": schema},
                },
                **settings.fast_kwargs_for(model),
            )
        raw = (response.choices[0].message.content or "").strip()
    except Exception:
        return {}
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _tasks_from(raw: Any, limit: int, strong_left: int) -> list[Task]:
    """Macht aus dem, was das Modell schickt, saubere Auftraege."""
    tasks: list[Task] = []
    for eintrag in raw if isinstance(raw, list) else []:
        task = as_task(eintrag)
        if not task.text:
            continue
        if task.strong and strong_left <= 0:
            task.strong = False
        if task.strong:
            strong_left -= 1
        tasks.append(task)
        if len(tasks) >= limit:
            break
    return tasks


def plan_mission(
    question: str,
    settings: Settings,
    *,
    model: str = "",
    context: str = "",
    limit: int = 12,
    strong: int = 0,
    forced: bool = False,
) -> Mission:
    """Laesst den Master die Agenten beauftragen.

    Args:
        limit: Wie viele Agenten hoechstens.
        strong: Wie viele davon auf dem starken Modell laufen duerfen.
        forced: `/max` -- dann MUSS er alle besetzen.

    Returns:
        Die Mission. Faellt der Master aus, kommt eine leere zurueck
        (`fallback=True`) und der Aufrufer plant wie bisher.
    """
    limit = max(1, int(limit))
    force = (
        f"Besetze ALLE {limit} -- der Nutzer hat ausdruecklich die volle Mannschaft "
        "angefordert. Lieber ein Auftrag, der wenig bringt, als ein Agent, der "
        "danebensteht."
        if forced
        else (
            "Wie viele du davon einsetzt, entscheidest du an der Frage: eine "
            "einzelne Angabe braucht zwei, ein Vergleich eine Handvoll, ein "
            "Ueberblick ueber ein ganzes Feld alle. Setz nicht mehr an, als du "
            "sinnvoll voneinander abgrenzen kannst -- zwei Agenten auf demselben "
            "Feld kosten doppelt und bringen dasselbe."
        )
    )
    prompt = PLAN_PROMPT % {
        "limit": limit,
        "strong": max(0, int(strong)),
        "force": force,
        "context": f"Bisheriges Gespraech:\n{context}\n\n" if context.strip() else "",
        "question": question.strip()[:1200],
    }
    payload = _json_call(
        prompt,
        settings,
        model or settings.model,
        PLAN_SCHEMA,
        "auftraege",
        max_tokens=min(4000, 220 + limit * 60),
    )
    tasks = _tasks_from(payload.get("agenten"), limit, max(0, int(strong)))
    if not tasks:
        return Mission(tasks=[], plan="", fallback=True)
    return Mission(tasks=tasks, plan=str(payload.get("plan") or "").strip())


def review_results(
    question: str,
    findings: str,
    settings: Settings,
    *,
    model: str = "",
    retry: int = MAX_RETRY,
    strong: int = 0,
) -> Review:
    """Laesst den Master die Rueckmeldungen bewerten.

    Faellt er aus, gilt die Recherche als in Ordnung: eine Nachrunde, die auf
    einem Fehler beruht, waere teurer als eine, die ausbleibt.
    """
    retry = max(1, int(retry))
    prompt = REVIEW_PROMPT % {
        "retry": retry,
        "question": question.strip()[:1200],
        "findings": findings[:20000],
    }
    payload = _json_call(
        prompt,
        settings,
        model or settings.model,
        REVIEW_SCHEMA,
        "bewertung",
        max_tokens=min(2000, 200 + retry * 80),
    )
    if not payload:
        return Review(ok=True)
    urteil = str(payload.get("urteil") or "").strip().lower()
    fehlt = [str(eintrag).strip() for eintrag in payload.get("fehlt") or [] if str(eintrag).strip()]
    nachrunde = _tasks_from(payload.get("nachrunde"), retry, max(0, int(strong)))
    # Das Urteil zaehlt, aber Taten zaehlen mehr: wer Nachauftraege vergibt,
    # hat Luecken gesehen, auch wenn er "gut" geschrieben hat.
    ok = urteil.startswith("gut") and not nachrunde
    return Review(ok=ok, missing=fehlt[:10], retries=nachrunde)
