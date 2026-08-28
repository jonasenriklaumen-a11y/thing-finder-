"""Tests fuer die Subagenten -- LLM und Netzwerk sind gemockt."""

from __future__ import annotations

import contextlib
import json
import threading
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from scoutr.config import Settings
from scoutr.fetch import Fetcher, RobotsPolicy
from scoutr.models import SearchResult
from scoutr.subagents import SubagentResult, run_subagents
from scoutr.tools import Toolbox


@pytest.fixture(autouse=True)
def _stub_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scoutr.tools.search_web",
        lambda query, **kwargs: [SearchResult(title="T", url="https://a.de/", snippet="S")],
    )


def _toolbox(settings: Settings, fixture_html) -> Toolbox:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, text=fixture_html("plain_article.html"), headers={"content-type": "text/html"}
        )

    fetcher = Fetcher("scoutr-test/0.1", timeout=5, delay_seconds=0, enable_browser=False)
    fetcher._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    fetcher.robots = RobotsPolicy(fetcher._client, "scoutr-test/0.1")
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
    from scoutr.subagents import _run_one

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
    from scoutr.subagents import plan_subtasks

    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: _reply(content='["Teil A", "Teil B", "Teil C"]'),
    )
    assert plan_subtasks("Frage", settings) == ["Teil A", "Teil B", "Teil C"]


def test_planner_respects_the_limit(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    from scoutr.subagents import plan_subtasks

    monkeypatch.setattr(
        "litellm.completion", lambda **kwargs: _reply(content='["a","b","c","d","e","f"]')
    )
    assert len(plan_subtasks("Frage", settings, limit=2)) == 2


def test_planner_falls_back_to_the_question(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Unbrauchbare Planung darf den Ablauf nicht aendern."""
    from scoutr.subagents import plan_subtasks

    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content="keine Ahnung"))
    assert plan_subtasks("Meine Frage", settings) == ["Meine Frage"]


def test_planner_survives_a_dead_model(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from scoutr.subagents import plan_subtasks

    def failing(**kwargs: Any):
        raise RuntimeError("weg")

    monkeypatch.setattr("litellm.completion", failing)
    assert plan_subtasks("Meine Frage", settings) == ["Meine Frage"]


def test_planner_gets_the_context(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    from scoutr.subagents import plan_subtasks

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


def test_without_its_own_model_the_main_one_is_used(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    settings.subagent_model = ""
    used: list[str] = []
    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: used.append(kwargs["model"]) or _reply(content="fertig"),
    )
    run_subagents(["Teilfrage"], settings, parallel=1)
    assert used == [settings.model]


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
    from scoutr.fetch import Fetcher as RealFetcher

    class SpyFetcher(RealFetcher):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr("scoutr.fetch.Fetcher", SpyFetcher)
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
    """Ohne eigenes Subagenten-Modell gibt es nichts zum Ausweichen."""
    settings.subagent_model = ""
    calls = {"n": 0}

    def failing(**kwargs: Any):
        calls["n"] += 1
        raise RuntimeError("weg")

    monkeypatch.setattr("litellm.completion", failing)
    results = run_subagents(["Teilfrage"], settings, parallel=1)
    assert results[0].error
    assert calls["n"] == 1
