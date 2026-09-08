"""Tests fuer die Subagenten -- LLM und Netzwerk sind gemockt."""

from __future__ import annotations

import contextlib
import json
import threading
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from aquaticy.config import Settings
from aquaticy.fetch import Fetcher, RobotsPolicy
from aquaticy.models import SearchResult
from aquaticy.subagents import SubagentResult, run_subagents
from aquaticy.tools import Toolbox


@pytest.fixture(autouse=True)
def _stub_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aquaticy.tools.search_web",
        lambda query, **kwargs: [SearchResult(title="T", url="https://a.de/", snippet="S")],
    )


def _toolbox(settings: Settings, fixture_html) -> Toolbox:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, text=fixture_html("plain_article.html"), headers={"content-type": "text/html"}
        )

    fetcher = Fetcher("aquaticy-test/0.1", timeout=5, delay_seconds=0, enable_browser=False)
    fetcher._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    fetcher.robots = RobotsPolicy(fetcher._client, "aquaticy-test/0.1")
    return Toolbox(settings, cache=None, fetcher=fetcher)


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "s1") -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _reply(content: str = "", tool_calls: list[Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def test_each_task_gets_its_own_answer(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def completion(**kwargs: Any):
        task = kwargs["messages"][0]["content"]
        return _reply(content=f"Antwort auf: {task.splitlines()[-1]}")

    monkeypatch.setattr("litellm.completion", completion)
    results = run_subagents(["Frage A", "Frage B"], settings, parallel=1)
    assert [result.task for result in results] == ["Frage A", "Frage B"]
    assert "Frage A" in results[0].summary
    assert "Frage B" in results[1].summary


def test_subagent_uses_its_tools(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, fixture_html
) -> None:
    calls = {"n": 0}

    def completion(**kwargs: Any):
        calls["n"] += 1
        if calls["n"] == 1:
            return _reply(tool_calls=[_tool_call("web_search", {"query": "cafés"})])
        return _reply(content="Ein Café gefunden. Quelle: a.de")

    monkeypatch.setattr("litellm.completion", completion)
    from aquaticy.subagents import _run_one

    result = _run_one("Finde Cafés", settings, None, None, toolbox=_toolbox(settings, fixture_html))
    assert result.tool_calls == 1
    assert result.searches == ["cafés"]
    assert "Café gefunden" in result.summary


def test_budget_forces_a_summary(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Auch wenn der Subagent endlos sucht, kommt am Ende eine Antwort."""
    settings.subagent_budget = 2
    calls = {"n": 0}

    def completion(**kwargs: Any):
        calls["n"] += 1
        if "tools" in kwargs:
            return _reply(tool_calls=[_tool_call("web_search", {"query": f"q{calls['n']}"})])
        return _reply(content="Zwischenstand: nicht gefunden.")

    monkeypatch.setattr("litellm.completion", completion)
    results = run_subagents(["Endlose Frage"], settings, parallel=1)
    assert results[0].tool_calls == 2
    assert "Zwischenstand" in results[0].summary


def test_a_failing_subagent_does_not_kill_the_others(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def completion(**kwargs: Any):
        if "kaputt" in kwargs["messages"][0]["content"]:
            raise RuntimeError("Modell weg")
        return _reply(content="Alles gut")

    monkeypatch.setattr("litellm.completion", completion)
    results = run_subagents(["kaputt", "heil"], settings, parallel=1)
    assert "Modell weg" in results[0].error
    assert results[1].summary == "Alles gut"


def test_tasks_are_capped(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    settings.max_subagents = 2
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content="ok"))
    results = run_subagents(["a", "b", "c", "d", "e"], settings, parallel=1)
    assert len(results) == 2


def test_empty_tasks_are_dropped(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content="ok"))
    assert run_subagents(["  ", ""], settings) == []


def test_parallel_execution_really_overlaps(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Zwei Subagenten sollen nicht nacheinander warten."""
    active = {"now": 0, "max": 0}
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=5)

    def completion(**kwargs: Any):
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait()
        with lock:
            active["now"] -= 1
        return _reply(content="fertig")

    monkeypatch.setattr("litellm.completion", completion)
    results = run_subagents(["a", "b"], settings, parallel=2)
    assert len(results) == 2
    assert active["max"] == 2


def test_results_keep_their_order(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    import time

    def completion(**kwargs: Any):
        task = kwargs["messages"][0]["content"].splitlines()[-1]
        if task == "schnell":
            time.sleep(0.05)
        return _reply(content=task)

    monkeypatch.setattr("litellm.completion", completion)
    results = run_subagents(["langsam", "schnell"], settings, parallel=2)
    assert [result.task for result in results] == ["langsam", "schnell"]


def test_events_are_emitted(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content="ok"))
    events: list[str] = []
    run_subagents(
        ["a", "b"], settings, on_event=lambda name, payload: events.append(name), parallel=1
    )
    assert events[0] == "subagents"
    assert events.count("subagent_done") == 2


def test_result_serialisation() -> None:
    result = SubagentResult(
        task="Frage",
        summary="Antwort",
        sources=[{"url": "https://a.de", "title": "T"}],
        searches=["q"],
    )
    payload = result.as_dict()
    assert payload["summary"] == "Antwort"
    assert payload["sources"] == ["https://a.de"]

    broken = SubagentResult(task="Frage", error="kaputt").as_dict()
    assert broken["error"] == "kaputt"
    assert "summary" not in broken


# ---------------------------------------------------------------------------
# Planung
# ---------------------------------------------------------------------------
def test_planner_returns_the_task_list(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from aquaticy.subagents import plan_subtasks

    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: _reply(content='["Teil A", "Teil B", "Teil C"]'),
    )
    assert plan_subtasks("Frage", settings) == ["Teil A", "Teil B", "Teil C"]


def test_planner_respects_the_limit(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    from aquaticy.subagents import plan_subtasks

    monkeypatch.setattr(
        "litellm.completion", lambda **kwargs: _reply(content='["a","b","c","d","e","f"]')
    )
    assert len(plan_subtasks("Frage", settings, limit=2)) == 2


def test_planner_falls_back_to_the_question(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Unbrauchbare Planung darf den Ablauf nicht aendern."""
    from aquaticy.subagents import plan_subtasks

    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content="keine Ahnung"))
    assert plan_subtasks("Meine Frage", settings) == ["Meine Frage"]


def test_planner_survives_a_dead_model(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from aquaticy.subagents import plan_subtasks

    def failing(**kwargs: Any):
        raise RuntimeError("weg")

    monkeypatch.setattr("litellm.completion", failing)
    assert plan_subtasks("Meine Frage", settings) == ["Meine Frage"]


def test_planner_gets_the_context(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    from aquaticy.subagents import plan_subtasks

    captured: dict[str, Any] = {}

    def completion(**kwargs: Any):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return _reply(content='["x"]')

    monkeypatch.setattr("litellm.completion", completion)
    plan_subtasks("nur die sonntags", settings, context="Nutzer: Cafés in Köln")
    assert "Cafés in Köln" in captured["prompt"]


def test_subagents_use_their_own_model(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Ein leichtes Modell fuer die Teilfragen, das grosse bleibt beim Hauptagenten."""
    settings.subagent_model = "ollama_chat/qwen3:1.7b"
    used: list[str] = []

    def completion(**kwargs: Any):
        used.append(kwargs["model"])
        return _reply(content="fertig")

    monkeypatch.setattr("litellm.completion", completion)
    run_subagents(["Teilfrage"], settings, parallel=1)
    assert used == ["ollama_chat/qwen3:1.7b"]


def test_without_its_own_model_the_small_one_is_used(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Ohne eigene Angabe das schnelle kleine Modell des Anbieters.

    Ein 70B-Modell für "such die Öffnungszeiten" kostet Sekunden, und die
    summieren sich mit jedem der vierundvierzig Agenten.
    """
    settings.subagent_model = ""
    used: list[str] = []
    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: used.append(kwargs["model"]) or _reply(content="fertig"),
    )
    run_subagents(["Teilfrage"], settings, parallel=1)
    assert used == ["mistral/mistral-small-latest"]


def test_an_unknown_provider_keeps_the_main_model(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Kennt Aquaticy zum Anbieter kein kleines Modell, bleibt es beim großen."""
    settings.subagent_model = ""
    settings.model = "fremd/riesenmodell"
    used: list[str] = []
    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: used.append(kwargs["model"]) or _reply(content="fertig"),
    )
    run_subagents(["Teilfrage"], settings, parallel=1)
    assert used == ["fremd/riesenmodell"]


def test_overflowing_subagent_calls_still_get_answers(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Abgeschnittene Tool-Calls brauchen eine Antwort, sonst ist der
    Verlauf ungueltig und die Abschluss-Zusammenfassung schlaegt fehl."""
    settings.subagent_budget = 1
    histories: list[list[dict[str, Any]]] = []

    def completion(**kwargs: Any):
        histories.append(kwargs["messages"])
        if "tools" in kwargs and len(histories) == 1:
            return _reply(
                tool_calls=[
                    _tool_call("web_search", {"query": "a"}, "s1"),
                    _tool_call("web_search", {"query": "b"}, "s2"),
                    _tool_call("web_search", {"query": "c"}, "s3"),
                ]
            )
        return _reply(content="Zusammenfassung")

    monkeypatch.setattr("litellm.completion", completion)
    results = run_subagents(["Frage"], settings, parallel=1)
    assert results[0].summary == "Zusammenfassung"
    # Der letzte Aufruf sah fuer jeden Tool-Call eine Antwort.
    final_history = histories[-1]
    assistant = next(m for m in final_history if m.get("tool_calls"))
    tool_ids = {m["tool_call_id"] for m in final_history if m.get("role") == "tool"}
    assert {c["id"] for c in assistant["tool_calls"]} == tool_ids == {"s1", "s2", "s3"}
    budget_answers = [
        m for m in final_history if m.get("role") == "tool" and "Budget" in m["content"]
    ]
    assert len(budget_answers) == 2


def test_subagents_share_one_fetcher(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Die Drossel (1 Request/s je Domain) muss ueber alle Subagenten gelten."""
    created: list[Any] = []
    from aquaticy.fetch import Fetcher as RealFetcher

    class SpyFetcher(RealFetcher):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr("aquaticy.fetch.Fetcher", SpyFetcher)
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content="ok"))
    run_subagents(["a", "b", "c"], settings, parallel=2)
    assert len(created) == 1
    # Und er wurde am Ende geschlossen.
    assert created[0]._client.is_closed


def test_dead_subagent_model_falls_back_to_the_main_model(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Kleines Modell nicht geladen -> das Hauptmodell uebernimmt die Teilfrage."""
    settings.subagent_model = "ollama_chat/qwen3:1.7b"
    used: list[str] = []

    def completion(**kwargs: Any):
        used.append(kwargs["model"])
        if kwargs["model"] == "ollama_chat/qwen3:1.7b":
            raise RuntimeError("model not found")
        return _reply(content="vom Hauptmodell beantwortet")

    monkeypatch.setattr("litellm.completion", completion)
    events: list[str] = []
    results = run_subagents(
        ["Teilfrage"], settings, on_event=lambda name, payload: events.append(name), parallel=1
    )
    assert results[0].summary == "vom Hauptmodell beantwortet"
    assert used == ["ollama_chat/qwen3:1.7b", settings.model]
    assert "fallback" in events


def test_dead_main_model_stays_dead(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Ist das Hauptmodell selbst tot, gibt es nichts zum Ausweichen.

    Das kleine Modell darf einmal ausfallen -- dann übernimmt das große.
    Fällt auch das aus, ist Schluss: ein drittes Mal fragen wäre dieselbe
    Antwort und dieselbe Wartezeit noch einmal.
    """
    settings.subagent_model = settings.model
    calls = {"n": 0}

    def failing(**kwargs: Any):
        calls["n"] += 1
        raise RuntimeError("weg")

    monkeypatch.setattr("litellm.completion", failing)
    results = run_subagents(["Teilfrage"], settings, parallel=1)
    assert results[0].error
    assert calls["n"] == 1


def test_the_small_model_falls_back_exactly_once(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Auch das automatisch gewählte kleine Modell hat einen Ausweg."""
    settings.subagent_model = ""
    used: list[str] = []

    def failing(**kwargs: Any):
        used.append(kwargs["model"])
        raise RuntimeError("weg")

    monkeypatch.setattr("litellm.completion", failing)
    results = run_subagents(["Teilfrage"], settings, parallel=1)
    assert results[0].error
    assert used == ["mistral/mistral-small-latest", settings.model]


# ---------------------------------------------------------------------------
# Tempo: ein Aufruf, kleines Modell, kein Denk-Modus
# ---------------------------------------------------------------------------
def test_plan_request_returns_decision_and_tasks(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from aquaticy.subagents import plan_request

    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: _reply(content='{"recherche": true, "teilfragen": ["A", "B"]}'),
    )
    assert plan_request("Frage", settings) == (True, ["A", "B"])


def test_plan_request_detects_chat(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    from aquaticy.subagents import plan_request

    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: _reply(content='{"recherche": false, "teilfragen": []}'),
    )
    assert plan_request("danke dir", settings) == (False, [])


def test_plan_request_uses_the_small_model_without_thinking(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Der Planer lief frueher auf dem grossen Modell -- auf einer knappen
    Karte kostete allein der Modellwechsel mehr als der Aufruf."""
    from aquaticy.subagents import plan_request

    settings.model = "ollama_chat/gemma4:12b"
    settings.subagent_model = "ollama_chat/qwen3:1.7b"
    captured: dict[str, Any] = {}

    def completion(**kwargs: Any):
        captured.update(kwargs)
        return _reply(content='{"recherche": true, "teilfragen": ["A"]}')

    monkeypatch.setattr("litellm.completion", completion)
    plan_request("Frage", settings)
    assert captured["model"] == "ollama_chat/qwen3:1.7b"
    assert captured["reasoning_effort"] == "disable"
    assert captured["num_ctx"] == 2048
    assert captured["max_tokens"] <= 200
    assert captured["response_format"]["json_schema"]["schema"]["required"] == [
        "recherche",
        "teilfragen",
    ]


def test_plan_request_survives_garbage(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from aquaticy.subagents import plan_request

    for answer in ("kein JSON", "", '{"kaputt":'):
        monkeypatch.setattr(
            "litellm.completion",
            lambda _answer=answer, **kwargs: _reply(content=_answer),
        )
        needs, tasks = plan_request("Meine Frage", settings)
        assert needs is True
        assert tasks == ["Meine Frage"]


def test_plan_request_accepts_a_bare_array(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Manche Modelle ignorieren das Schema und liefern nur die Liste."""
    from aquaticy.subagents import plan_request

    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content='["A", "B"]'))
    assert plan_request("Frage", settings) == (True, ["A", "B"])


def test_plan_request_respects_the_limit(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from aquaticy.subagents import plan_request

    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: _reply(
            content='{"recherche": true, "teilfragen": ["a","b","c","d","e"]}'
        ),
    )
    assert len(plan_request("Frage", settings, limit=2)[1]) == 2


def test_subagents_run_without_thinking_mode(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Vier parallele Subagenten, die erst seitenlang ueberlegen, sind der
    Unterschied zwischen zehn und dreissig Sekunden."""
    settings.model = "ollama_chat/gemma4:12b"
    settings.subagent_model = "ollama_chat/qwen3:1.7b"
    captured: dict[str, Any] = {}

    def completion(**kwargs: Any):
        captured.update(kwargs)
        return _reply(content="fertig")

    monkeypatch.setattr("litellm.completion", completion)
    run_subagents(["Teilfrage"], settings, parallel=1)
    assert captured["reasoning_effort"] == "disable"
    # Aber volles Fenster -- Subagenten lesen ganze Seiten.
    assert captured["num_ctx"] == settings.context_tokens


def test_subagent_prompt_asks_for_detail() -> None:
    """Was der Subagent weglaesst, ist fuer den Hauptagenten verloren."""
    from aquaticy.subagents import SUBAGENT_PROMPT

    assert "ausfuehrlich" in SUBAGENT_PROMPT.lower()
    assert "400 Woerter" in SUBAGENT_PROMPT
    assert "hoechstens 200 Woerter" not in SUBAGENT_PROMPT
    assert "Rate nie" in SUBAGENT_PROMPT


# ---------------------------------------------------------------------------
# Verschiedene Quellen je Agent
# ---------------------------------------------------------------------------
def test_agents_share_one_list_of_claimed_domains(monkeypatch, tmp_path) -> None:
    """Drei Teilfragen zum selben Thema sollen nicht dieselbe Seite lesen."""
    from aquaticy.config import Settings
    from aquaticy.subagents import SubagentResult, run_subagents

    gesehen: list[object] = []

    def fake_one(task, settings, cache, on_event, toolbox=None, stop=None, **rest):
        gesehen.append(toolbox)
        toolbox.avoid_domains.add(f"{task}.example")
        return SubagentResult(task=task, summary="ok")

    monkeypatch.setattr("aquaticy.subagents._run_one", fake_one)
    settings = Settings(data_dir=tmp_path, max_subagents=3)
    run_subagents(["a", "b", "c"], settings, parallel=1)

    assert len(gesehen) == 3
    geteilt = {id(box.avoid_domains) for box in gesehen}
    assert len(geteilt) == 1, "alle Agenten teilen sich dieselbe Menge"
    assert all(box.claim_sources for box in gesehen), "jeder traegt selbst ein"
    assert gesehen[0].avoid_domains == {"a.example", "b.example", "c.example"}


def test_a_call_may_raise_the_cap(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Der Pro-Modus hebt die Grenze fuer seinen Turn an -- ohne die
    Einstellung anzufassen."""
    settings.max_subagents = 2
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content="ok"))
    results = run_subagents(["a", "b", "c", "d"], settings, parallel=1, limit=4)
    assert len(results) == 4
    assert settings.max_subagents == 2, "die Einstellung bleibt, wie sie war"


def test_the_same_task_does_not_run_twice(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Zwei gleiche Auftraege lesen dieselben Seiten und melden dasselbe --
    bezahlt wird beides. Bei 24 Agenten faellt das ins Gewicht."""
    settings.max_subagents = 24
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content="ok"))
    results = run_subagents(
        ["Cafes in Bremen", "cafes in bremen.", "  Cafes in Bremen  ", "Cafes in Kiel"],
        settings,
        parallel=1,
    )
    assert [result.task for result in results] == ["Cafes in Bremen", "Cafes in Kiel"]


def test_the_subagent_is_told_to_ask_three_ways(settings: Settings) -> None:
    """Drei Formulierungen in EINEM Aufruf: mehr Treffer, gleiche Kosten."""
    from aquaticy.subagents import SUBAGENT_PROMPT

    assert "`queries`" in SUBAGENT_PROMPT
    assert "einen einzigen Aufruf" in SUBAGENT_PROMPT


def test_many_agents_do_not_start_in_the_same_millisecond(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path
) -> None:
    """Vierundzwanzig Anfragen auf einmal beantwortet ein Anbieter mit einer
    Ratenbegrenzung -- und jeder Subagent, der sie abbekommt, faellt aus."""
    import time

    from aquaticy.subagents import LAUNCH_STAGGER, STAGGER_AFTER

    starts: list[float] = []

    def fake_one(task, s, cache, on_event, toolbox=None, stop=None, **rest):
        starts.append(time.monotonic())
        return SubagentResult(task=task, summary="ok")

    monkeypatch.setattr("aquaticy.subagents._run_one", fake_one)
    settings.max_subagents = 24
    aufgaben = [f"Teilfrage {nummer}" for nummer in range(8)]
    run_subagents(aufgaben, settings, parallel=8)

    assert len(starts) == 8
    spanne = max(starts) - min(starts)
    erwartet = (len(aufgaben) - 1) * LAUNCH_STAGGER
    assert spanne >= erwartet * 0.6, f"zu dicht beieinander ({spanne:.2f}s)"
    assert STAGGER_AFTER == 4, "bis vier Agenten bleibt alles wie bisher"


def test_a_handful_of_agents_still_starts_at_once(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Der Versatz ist fuer die Breite da, nicht fuer den Alltag."""
    import time

    starts: list[float] = []

    def fake_one(task, s, cache, on_event, toolbox=None, stop=None, **rest):
        starts.append(time.monotonic())
        return SubagentResult(task=task, summary="ok")

    monkeypatch.setattr("aquaticy.subagents._run_one", fake_one)
    run_subagents(["a", "b", "c"], settings, parallel=3)
    assert max(starts) - min(starts) < 0.5


# ---------------------------------------------------------------------------
# Rollen
# ---------------------------------------------------------------------------
def test_the_role_comes_out_of_the_task() -> None:
    """Reine Textarbeit -- die Zuordnung darf keine Wartezeit kosten."""
    from aquaticy.subagents import role_for

    assert role_for("Was kostet ein Lastenrad in Bremen?") == "zahlen"
    assert role_for("Welche Probleme und Beschwerden gibt es zum Modell X?") == "gegenstimmen"
    assert role_for("Was hat sich seit wann an der Förderung geändert?") == "frisch"
    # Ohne Anhaltspunkt bleibt es beim normalen Auftrag: lieber keine Rolle
    # als eine falsche, die am Thema vorbeisucht.
    assert role_for("Cafés mit WLAN in Bremen") == "standard"
    assert role_for("") == "standard"


def test_each_role_says_something_different() -> None:
    from aquaticy.subagents import ROLE_EXTRA, ROLE_LABELS

    assert ROLE_EXTRA["standard"] == "", "der Normalfall bleibt, wie er war"
    assert "Preise" in ROLE_EXTRA["zahlen"]
    assert "BEIDE" in ROLE_EXTRA["zahlen"], "zwei Zahlen sind zwei Zahlen"
    assert "Kritik" in ROLE_EXTRA["gegenstimmen"]
    assert "search_news" in ROLE_EXTRA["frisch"]
    assert set(ROLE_LABELS) == set(ROLE_EXTRA)
    assert ROLE_LABELS["standard"] == "", "der Normalfall braucht keine Marke"


def test_the_role_reaches_the_agent(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    gesehen: list[str] = []

    def completion(**kwargs: Any):
        gesehen.append(kwargs["messages"][0]["content"])
        return _reply(content="ok")

    monkeypatch.setattr("litellm.completion", completion)
    results = run_subagents(["Was kostet die Jahreskarte in Bremen?"], settings, parallel=1)
    assert results[0].role == "zahlen"
    assert "Deine Rolle: Zahlen" in gesehen[0]
    assert "Deine Teilfrage lautet" in gesehen[0], "der Auftrag bleibt derselbe"


def test_an_unknown_role_falls_back(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    from aquaticy.subagents import _run_one

    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content="ok"))
    ergebnis = _run_one("Frage", settings, None, None, role="quatsch")
    assert ergebnis.role == "standard"


# ---------------------------------------------------------------------------
# Die Pruefer
# ---------------------------------------------------------------------------
def test_the_checkers_run_while_the_others_are_still_searching(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Der Punkt der vier: sie pruefen NEBENHER. Waere es eine zweite Runde,
    koennte man sich den Aufwand sparen und die alte Gegenprobe nehmen.

    Der Test haelt die spaeteren Rechercheure so lange fest, bis eine
    Pruefung angefangen hat. Liefe die Pruefung erst nach der Recherche,
    warteten beide aufeinander -- und der Test liefe in seinen Timeout.
    """
    import threading as th

    pruefung_laeuft = th.Event()

    def completion(**kwargs: Any):
        text = kwargs["messages"][0]["content"]
        if text.startswith("Du bist Pruefer"):
            pruefung_laeuft.set()
            return _reply(content="BESTAETIGT -- passt so (quelle.de)")
        if "Teilfrage 0" in text:
            return _reply(content="Ergebnis mit Zahl 42 (a.de)")
        assert pruefung_laeuft.wait(timeout=5), "die Pruefung lief erst hinterher"
        return _reply(content="Ergebnis (b.de)")

    monkeypatch.setattr("litellm.completion", completion)
    settings.max_subagents = 8
    results = run_subagents(
        [f"Teilfrage {nummer}" for nummer in range(4)], settings, parallel=4, checkers=2
    )
    assert [result.verdict for result in results] == ["BESTAETIGT"] * 4


def test_a_check_lands_on_its_own_result(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def completion(**kwargs: Any):
        text = kwargs["messages"][0]["content"]
        if text.startswith("Du bist Pruefer"):
            assert "Ergebnis: 12 Euro" in text, "der Pruefer sieht, was er pruefen soll"
            return _reply(content="ABWEICHUNG -- anderswo 14 Euro (b.de)")
        return _reply(content="Ergebnis: 12 Euro (a.de)")

    monkeypatch.setattr("litellm.completion", completion)
    results = run_subagents(["Was kostet es?"], settings, parallel=2, checkers=1)
    assert results[0].verdict == "ABWEICHUNG"
    assert "14 Euro" in results[0].check
    assert results[0].as_dict()["verdict"] == "ABWEICHUNG"


def test_a_checker_does_not_close_the_shared_fetcher(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Pruefer und Suchende lesen parallel mit demselben HTTP-Client."""
    closed: list[int] = []

    def run_one(task: str, *args: Any, **kwargs: Any) -> SubagentResult:
        return SubagentResult(task=task, summary="BESTAETIGT")

    monkeypatch.setattr("aquaticy.subagents._run_one", run_one)
    monkeypatch.setattr(Fetcher, "close", lambda fetcher: closed.append(id(fetcher)))
    run_subagents(["Frage"], settings, parallel=1, checkers=1)

    assert len(closed) == 1, "nur der Koordinator schliesst den gemeinsamen Fetcher"


def test_without_checkers_nothing_is_checked(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    aufrufe: list[str] = []

    def completion(**kwargs: Any):
        aufrufe.append(kwargs["messages"][0]["content"])
        return _reply(content="Ergebnis (a.de)")

    monkeypatch.setattr("litellm.completion", completion)
    results = run_subagents(["Frage a", "Frage b"], settings, parallel=2)
    assert not any(text.startswith("Du bist Pruefer") for text in aufrufe)
    assert all(not result.check for result in results)
    assert "check" not in results[0].as_dict()


def test_nothing_to_check_is_not_checked(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Ein gescheiterter Agent hat kein Ergebnis -- da gibt es nichts zu
    pruefen, und ein Pruefer waere reine Wartezeit."""
    def completion(**kwargs: Any):
        if kwargs["messages"][0]["content"].startswith("Du bist Pruefer"):
            raise AssertionError("hier gibt es nichts zu pruefen")
        return _reply(content="   ")

    monkeypatch.setattr("litellm.completion", completion)
    results = run_subagents(["Frage"], settings, parallel=2, checkers=2)
    assert results[0].check == ""


def test_the_verdict_is_read_from_the_first_word() -> None:
    from aquaticy.subagents import verdict_of

    assert verdict_of("BESTAETIGT — alles stimmt") == "BESTAETIGT"
    assert verdict_of("**ABWEICHUNG**: Preis anders") == "ABWEICHUNG"
    assert verdict_of("Kurz gesagt: UNKLAR, nichts gefunden") == "UNKLAR"
    assert verdict_of("Ich habe nichts geprüft.") == ""
    assert verdict_of("") == ""


def test_the_budget_can_be_raised_for_one_call(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Im Pro-Modus bekommt jeder Agent mehr Aufrufe -- ohne dass sich die
    Einstellung aendert."""
    settings.subagent_budget = 2
    runden = {"n": 0}

    def completion(**kwargs: Any):
        runden["n"] += 1
        if runden["n"] > 20:
            return _reply(content="Schluss")
        return _reply(tool_calls=[_tool_call("web_search", {"query": "x"})])

    monkeypatch.setattr("litellm.completion", completion)
    ergebnis = run_subagents(["Frage"], settings, parallel=1, budget=5)[0]
    assert ergebnis.tool_calls == 5


def test_never_more_checkers_than_searchers(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Bei einem lokalen Modell laufen zwei Agenten nebeneinander -- vier
    Pruefer obendrauf waeren sechs Anfragen an dieselbe Grafikkarte."""
    gleichzeitig = {"jetzt": 0, "hoechstens": 0}
    uhr = threading.Lock()

    def completion(**kwargs: Any):
        with uhr:
            gleichzeitig["jetzt"] += 1
            gleichzeitig["hoechstens"] = max(
                gleichzeitig["hoechstens"], gleichzeitig["jetzt"]
            )
        try:
            return _reply(content="BESTAETIGT -- passt (a.de)")
        finally:
            with uhr:
                gleichzeitig["jetzt"] -= 1

    monkeypatch.setattr("litellm.completion", completion)
    settings.max_subagents = 8
    run_subagents([f"Frage {n}" for n in range(6)], settings, parallel=2, checkers=4)
    assert gleichzeitig["hoechstens"] <= 4, "zwei Suchende und hoechstens zwei Pruefer"


# ---------------------------------------------------------------------------
# Immer die volle Zahl an Agenten
# ---------------------------------------------------------------------------
def test_the_tasks_are_filled_up_to_the_number_of_agents() -> None:
    """Der Planer liefert drei Teilfragen, obwohl zwölf Agenten bereitstehen --
    dann suchen zwölf Agenten zu dritt."""
    from aquaticy.subagents import spread_tasks

    aufgefuellt = spread_tasks(
        "Gute Cafés mit WLAN in Bremen",
        ["Cafés mit WLAN Bremen Mitte", "Cafés mit Steckdosen Bremen"],
        12,
    )
    texte = [task.text for task in aufgefuellt]
    assert len(texte) == 12
    assert texte[:2] == ["Cafés mit WLAN Bremen Mitte", "Cafés mit Steckdosen Bremen"]
    assert len(set(texte)) == 12, "keine zwei gleichen Aufträge"
    # Die Blickwinkel sind so gewählt, dass die Rollen von selbst passen.
    rollen = {task.role for task in aufgefuellt}
    assert {"zahlen", "gegenstimmen", "frisch"} <= rollen


def test_more_tasks_than_agents_are_cut() -> None:
    from aquaticy.subagents import spread_tasks

    assert len(spread_tasks("Frage", [f"Teil {n}" for n in range(20)], 12)) == 12


def test_nothing_is_invented_out_of_nothing() -> None:
    from aquaticy.subagents import spread_tasks

    assert spread_tasks("", [], 12) == []
    assert [task.text for task in spread_tasks("", ["Teil A"], 12)] == ["Teil A"]


def test_the_planner_is_asked_for_the_full_number(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from aquaticy.subagents import plan_request

    gesehen: list[str] = []

    def completion(**kwargs: Any):
        gesehen.append(kwargs["messages"][0]["content"])
        return _reply(content='{"recherche": true, "teilfragen": ["A", "B"]}')

    monkeypatch.setattr("litellm.completion", completion)
    plan_request("Frage", settings, limit=24)
    assert "in 24 eigenstaendige Teilfragen" in gesehen[0]
    assert "nicht weniger" in gesehen[0]
    assert "gehoert der Ort in JEDE" in gesehen[0]


def test_the_place_lands_in_every_subtask(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Der Subagent sieht das Gespräch nicht und den Ortsfilter erst recht
    nicht -- für ihn ist die Teilfrage alles, was es gibt."""
    from aquaticy.subagents import plan_request

    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: _reply(
            content='{"recherche": true, "teilfragen": ["Cafés mit WLAN", "Cafés in Bremen"]}'
        ),
    )
    settings.location = "Bremen"
    _, tasks = plan_request("Wo kann ich arbeiten?", settings)
    assert tasks == ["Cafés mit WLAN Bremen", "Cafés in Bremen"]

