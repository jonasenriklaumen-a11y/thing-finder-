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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from scoutr.cache import Cache
from scoutr.config import Settings
from scoutr.tools import TOOL_SCHEMAS, EventHook, Toolbox

SUBAGENT_PROMPT = """\
Du bist ein Rechercheassistent und bearbeitest GENAU EINE Teilfrage. Du hast \
zwei Werkzeuge: `web_search` und `fetch_page`.

Vorgehen: ein bis zwei Suchanfragen, die aussichtsreichsten Treffer lesen, \
dann antworten.

Regeln:
- Antworte knapp: hoechstens 200 Woerter.
- Nenne zu jeder Angabe die Quelle (Domain).
- Rate nie. Was du nicht gefunden hast, schreibst du als "nicht gefunden".
- Liefert `fetch_page` einen `skipped_reason`, nimm eine andere Quelle.
- Kein Vorwort, keine Wiederholung der Frage -- nur das Ergebnis.

Deine Teilfrage lautet:
%(task)s"""


PLANNER_PROMPT = """\
Zerlege die folgende Nutzeranfrage in eigenstaendige Teilfragen, die sich \
unabhaengig voneinander im Web recherchieren lassen.

Regeln:
- Hoechstens %(limit)d Teilfragen, lieber weniger.
- Jede Teilfrage muss FUER SICH verstaendlich sein: Ort, Produkt, Zeitraum und \
Kriterium gehoeren hinein, auch wenn sie in der Anfrage nur einmal vorkommen.
- Zerlege nach Sachen, die getrennt gesucht werden koennen: einzelne Kandidaten, \
einzelne Orte, einzelne Kriterien.
- Laesst sich die Anfrage nicht sinnvoll teilen, gib GENAU EINE Teilfrage \
zurueck, die die Anfrage wiedergibt.
- Antworte NUR mit einem JSON-Array aus Strings, ohne Erklaerung.

%(context)s
Anfrage: %(question)s"""


def plan_subtasks(
    question: str,
    settings: Settings,
    context: str = "",
    limit: int = 4,
) -> list[str]:
    """Laesst das Hauptmodell die Anfrage in Teilfragen zerlegen.

    Gibt bei Zweifeln eine einzige Teilfrage zurueck -- die Anfrage selbst.
    So bleibt der Ablauf gleich, auch wenn sich nichts sinnvoll teilen laesst.
    """
    import litellm

    litellm.suppress_debug_info = True
    prompt = PLANNER_PROMPT % {
        "limit": max(1, limit),
        "question": question.strip(),
        "context": f"Bisheriges Gespraech:\n{context}\n" if context.strip() else "",
    }
    try:
        response = litellm.completion(
            model=settings.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            **settings.llm_kwargs(),
        )
        raw = (response.choices[0].message.content or "").strip()
    except Exception:
        return [question.strip()]

    tasks = _parse_task_list(raw)
    return tasks[: max(1, limit)] if tasks else [question.strip()]


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


@dataclass
class SubagentResult:
    """Was ein Subagent herausgefunden hat."""

    task: str
    summary: str = ""
    sources: list[dict[str, str]] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)
    tool_calls: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"task": self.task}
        if self.error:
            payload["error"] = self.error
            return payload
        payload["summary"] = self.summary
        payload["sources"] = [source.get("url", "") for source in self.sources]
        payload["searches"] = self.searches
        return payload


def _run_one(
    task: str,
    settings: Settings,
    cache: Cache | None,
    on_event: EventHook | None,
    toolbox: Toolbox | None = None,
) -> SubagentResult:
    """Fuehrt einen Subagenten aus -- eigene Toolbox, eigenes Budget."""
    import litellm

    litellm.suppress_debug_info = True
    result = SubagentResult(task=task)
    box = toolbox or Toolbox(settings, cache=cache, on_event=None)
    owns_box = toolbox is None

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": SUBAGENT_PROMPT % {"task": task}}
    ]
    budget = max(1, settings.subagent_budget)
    used = 0

    try:
        while used < budget:
            try:
                response = litellm.completion(
                    model=settings.effective_subagent_model,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto",
                    **settings.llm_kwargs(),
                )
            except Exception as exc:
                result.error = f"{type(exc).__name__}: {exc}"
                return result

            message = response.choices[0].message
            calls = getattr(message, "tool_calls", None) or []
            content = message.content or ""

            if not calls:
                result.summary = content.strip()
                break

            from scoutr.agent import repair_tool_calls

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

            answered: set[str] = set()
            for call in call_dicts:
                call_id = call["id"]
                if used >= budget:
                    # Auch abgeschnittene Aufrufe brauchen eine Antwort --
                    # ein Tool-Call ohne Antwort macht den Verlauf ungueltig
                    # und der abschliessende Aufruf wuerde abgelehnt.
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": call["function"]["name"],
                            "content": json.dumps(
                                {"error": "Werkzeug-Budget aufgebraucht."},
                                ensure_ascii=False,
                            ),
                        }
                    )
                    continue
                answered.add(call_id)
                used += 1
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
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": json.dumps(payload, ensure_ascii=False)[
                            : max(1000, settings.max_tool_chars)
                        ],
                    }
                )

        if not result.summary:
            # Budget aufgebraucht -- ein letzter Aufruf ohne Werkzeuge.
            messages.append(
                {
                    "role": "user",
                    "content": "Fasse jetzt zusammen, was du gefunden hast. "
                    "Offene Punkte kennzeichnest du als 'nicht gefunden'.",
                }
            )
            try:
                response = litellm.completion(
                    model=settings.effective_subagent_model,
                    messages=messages,
                    **settings.llm_kwargs(),
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
            on_event("subagent_done", {"task": task, "tool_calls": used, "error": result.error})


def run_subagents(
    tasks: list[str],
    settings: Settings,
    cache: Cache | None = None,
    on_event: EventHook | None = None,
    parallel: int = 2,
) -> list[SubagentResult]:
    """Bearbeitet *tasks* nebenlaeufig und gibt die Ergebnisse in Reihenfolge zurueck.

    Args:
        tasks: Die Teilfragen.
        settings: Laufzeit-Einstellungen (Modell, Budget je Subagent).
        cache: Gemeinsamer Cache -- doppelte Suchen kosten so nichts.
        on_event: Callback fuer die Live-Anzeige.
        parallel: Wie viele gleichzeitig. Bei lokalen Modellen bringt mehr als
            zwei wenig, weil sie ohnehin nacheinander rechnen.
    """
    clean = [task.strip() for task in tasks if task and task.strip()]
    clean = clean[: max(1, settings.max_subagents)]
    if not clean:
        return []

    if on_event:
        on_event("subagents", {"tasks": clean})

    # Ein gemeinsamer Fetcher fuer alle: dessen Drossel und robots.txt-Cache
    # gelten damit ueber die Subagenten hinweg. Mit je eigenem Fetcher wuerden
    # zwei parallele Subagenten dieselbe Domain gleichzeitig treffen -- und
    # unser Versprechen von einem Request pro Sekunde und Domain waere hin.
    from scoutr.fetch import Fetcher

    shared_fetcher = Fetcher(
        user_agent=settings.user_agent,
        timeout=settings.fetch_timeout,
        delay_seconds=settings.request_delay_seconds,
        enable_browser=settings.enable_playwright,
    )
    boxes = [Toolbox(settings, cache=cache, fetcher=shared_fetcher) for _ in clean]
    try:
        workers = max(1, min(parallel, len(clean)))
        if workers == 1:
            return [
                _run_one(task, settings, cache, on_event, toolbox=box)
                for task, box in zip(clean, boxes, strict=True)
            ]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_run_one, task, settings, cache, on_event, toolbox=box)
                for task, box in zip(clean, boxes, strict=True)
            ]
            return [future.result() for future in futures]
    finally:
        shared_fetcher.close()
