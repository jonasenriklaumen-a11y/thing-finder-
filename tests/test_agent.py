"""Tests fuer die Agenten-Schleife -- das LLM ist durchgaengig gemockt."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from scoutr.agent import Agent, _parse_spec_json
from scoutr.config import Settings
from scoutr.fetch import Fetcher, RobotsPolicy
from scoutr.models import SearchResult
from scoutr.tools import Toolbox


# ---------------------------------------------------------------------------
# Attrappen
# ---------------------------------------------------------------------------
def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        index=0,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _message(content: str = "", tool_calls: list[Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def _stream(content: str = "", tool_calls: list[Any] | None = None):
    """Baut einen Stream aus Text-Deltas und (am Ende) Tool-Call-Deltas."""
    chunks = []
    for piece in content.split(" ") if content else []:
        chunks.append(
            SimpleNamespace(
                choices=[
                    SimpleNamespace(delta=SimpleNamespace(content=piece + " ", tool_calls=None))
                ]
            )
        )
    for index, call in enumerate(tool_calls or []):
        call.index = index
        delta = SimpleNamespace(content=None, tool_calls=[call])
        chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=delta)]))
    return iter(chunks)


class ScriptedLLM:
    """Gibt der Reihe nach vorbereitete Antworten zurueck."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("mehr LLM-Aufrufe als vorbereitete Antworten")
        return self.responses.pop(0)


@pytest.fixture
def toolbox(settings: Settings, fixture_html) -> Toolbox:
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


@pytest.fixture(autouse=True)
def _stub_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scoutr.tools.search_web",
        lambda query, **kwargs: [
            SearchResult(title="Café Sonntag", url="https://cafe-sonntag.de/", snippet="WLAN")
        ],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_agent_searches_reads_and_answers(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    llm = ScriptedLLM(
        _message(tool_calls=[_tool_call("web_search", {"query": "cafés mönchengladbach"})]),
        _message(tool_calls=[_tool_call("fetch_page", {"url": "https://cafe-sonntag.de/"}, "c2")]),
        _message(content="Ein Café gefunden. Quelle: cafe-sonntag.de"),
    )
    monkeypatch.setattr("litellm.completion", llm)

    agent = Agent(settings, cache=None, toolbox=toolbox)
    result = agent.ask("Finde Cafés mit WLAN", stream=False)

    assert "cafe-sonntag.de" in result.answer
    assert result.tool_calls == 2
    assert result.searches == ["cafés mönchengladbach"]
    assert result.sources[0]["domain"] == "cafe-sonntag.de"
    assert not result.hit_limit
    # Der Verlauf enthaelt beide Tool-Antworten.
    assert sum(1 for m in agent.messages if m["role"] == "tool") == 2


def test_tools_are_offered_to_the_llm(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    llm = ScriptedLLM(_message(content="fertig"))
    monkeypatch.setattr("litellm.completion", llm)
    Agent(settings, cache=None, toolbox=toolbox).ask("Frage", stream=False)
    names = [tool["function"]["name"] for tool in llm.calls[0]["tools"]]
    # Ohne Cache kein Merkzettel-Werkzeug; das Heimnetz ist standardmaessig dabei.
    assert names == [
        "web_search",
        "fetch_page",
        "search_news",
        "calculate",
        "lan_scan",
        "lan_check",
        "research_subtasks",
    ]


def test_subagents_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Ohne Subagenten bleiben die Kernwerkzeuge."""
    settings.max_subagents = 0
    settings.lan_enabled = False
    llm = ScriptedLLM(_message(content="fertig"))
    monkeypatch.setattr("litellm.completion", llm)
    Agent(settings, cache=None, toolbox=toolbox).ask("Frage", stream=False)
    names = [tool["function"]["name"] for tool in llm.calls[0]["tools"]]
    assert names == ["web_search", "fetch_page", "search_news", "calculate"]


def test_home_tools_follow_the_settings(settings: Settings) -> None:
    """Nichts vom Haus taucht auf, was der Nutzer nicht freigegeben hat."""
    settings.lan_enabled = False
    names = lambda: [tool["function"]["name"] for tool in Agent(settings).tools]  # noqa: E731
    assert "lan_scan" not in names() and "ha_states" not in names()

    settings.lan_enabled = True
    assert "lan_scan" in names() and "lan_check" in names()

    # Home Assistant erscheint erst mit Adresse UND Token.
    settings.ha_url = "http://192.168.1.5:8123"
    assert "ha_states" not in names()
    settings.ha_token = "geheim"
    assert "ha_states" in names()

    # Schalten erst, wenn es ausdruecklich erlaubt ist.
    assert "ha_call" not in names()
    settings.ha_control = True
    assert "ha_call" in names()


def test_budget_limit_forces_interim_answer(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    settings.max_tool_calls = 3
    searching = [
        _message(tool_calls=[_tool_call("web_search", {"query": f"q{i}"}, f"c{i}")])
        for i in range(3)
    ]
    llm = ScriptedLLM(*searching, _message(content="Zwischenstand: nicht gefunden."))
    monkeypatch.setattr("litellm.completion", llm)

    agent = Agent(settings, cache=None, toolbox=toolbox)
    result = agent.ask("Frage", stream=False)

    assert result.hit_limit
    assert result.tool_calls == 3
    assert "Zwischenstand" in result.answer
    # Der letzte Aufruf laeuft ohne Werkzeuge.
    assert "tools" not in llm.calls[-1]


def test_parallel_tool_calls_are_all_answered(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    calls = [
        _tool_call("web_search", {"query": "a"}, "c1"),
        _tool_call("web_search", {"query": "b"}, "c2"),
    ]
    llm = ScriptedLLM(_message(tool_calls=calls), _message(content="fertig"))
    monkeypatch.setattr("litellm.completion", llm)

    agent = Agent(settings, cache=None, toolbox=toolbox)
    result = agent.ask("Frage", stream=False)
    assert result.searches == ["a", "b"]
    tool_ids = [m["tool_call_id"] for m in agent.messages if m["role"] == "tool"]
    assert tool_ids == ["c1", "c2"]


def test_overflowing_calls_get_a_budget_error_message(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    settings.max_tool_calls = 1
    calls = [
        _tool_call("web_search", {"query": "a"}, "c1"),
        _tool_call("web_search", {"query": "b"}, "c2"),
    ]
    llm = ScriptedLLM(_message(tool_calls=calls), _message(content="Zwischenstand"))
    monkeypatch.setattr("litellm.completion", llm)

    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.ask("Frage", stream=False)
    budget_messages = [
        m for m in agent.messages if m["role"] == "tool" and "Budget" in m["content"]
    ]
    assert len(budget_messages) == 1


def test_llm_error_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    def failing(**kwargs: Any):
        raise RuntimeError("Modell nicht erreichbar")

    monkeypatch.setattr("litellm.completion", failing)
    agent = Agent(settings, cache=None, toolbox=toolbox)
    result = agent.ask("Frage", stream=False)
    assert "Modell nicht erreichbar" in result.error
    assert result.answer == ""
    # Die unbeantwortete Frage bleibt nicht im Verlauf stehen.
    assert all(m["role"] != "user" for m in agent.messages)


def test_streaming_collects_text_and_tool_calls(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    llm = ScriptedLLM(
        _stream(tool_calls=[_tool_call("web_search", {"query": "cafés"})]),
        _stream(content="Hier ist die Antwort"),
    )
    monkeypatch.setattr("litellm.completion", llm)

    chunks: list[str] = []
    agent = Agent(
        settings,
        cache=None,
        toolbox=toolbox,
        on_event=lambda name, payload: chunks.append(payload["text"])
        if name == "answer_chunk"
        else None,
    )
    result = agent.ask("Frage", stream=True)
    assert result.searches == ["cafés"]
    assert result.answer.strip() == "Hier ist die Antwort"
    assert "".join(chunks).strip() == "Hier ist die Antwort"


def test_location_is_added_to_the_question(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    llm = ScriptedLLM(_message(content="ok"))
    monkeypatch.setattr("litellm.completion", llm)
    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.set_location("Mönchengladbach", lang="de", country="de")
    agent.ask("Cafés?", stream=False)
    user_messages = [m["content"] for m in agent.messages if m["role"] == "user"]
    assert "Ortsfilter: Mönchengladbach" in user_messages[0]
    assert "Cafés?" in user_messages[0]


def test_context_is_kept_across_turns(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    llm = ScriptedLLM(_message(content="erste Antwort"), _message(content="zweite Antwort"))
    monkeypatch.setattr("litellm.completion", llm)
    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.ask("Frage 1", stream=False)
    agent.ask("nur die mit 4+ Sternen", stream=False)
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "user", "assistant"]
    assert "Frage 1" in llm.calls[1]["messages"][1]["content"]


def test_clear_resets_history(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    monkeypatch.setattr("litellm.completion", ScriptedLLM(_message(content="ok")))
    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.ask("Frage", stream=False)
    agent.clear()
    assert [m["role"] for m in agent.messages] == ["system"]


def test_history_is_written_to_the_cache(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox, tmp_path
) -> None:
    from scoutr.cache import Cache

    monkeypatch.setattr("litellm.completion", ScriptedLLM(_message(content="Antwort")))
    cache = Cache(tmp_path / "c.sqlite3")
    agent = Agent(settings, cache=cache, toolbox=toolbox)
    agent.ask("Meine Frage", stream=False)
    entries = cache.recent_history()
    assert entries[0].question == "Meine Frage"
    assert entries[0].answer == "Antwort"


def test_broken_tool_arguments_do_not_crash(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    broken = SimpleNamespace(
        id="c1",
        type="function",
        index=0,
        function=SimpleNamespace(name="web_search", arguments="{kaputt"),
    )
    llm = ScriptedLLM(_message(tool_calls=[broken]), _message(content="trotzdem fertig"))
    monkeypatch.setattr("litellm.completion", llm)
    agent = Agent(settings, cache=None, toolbox=toolbox)
    result = agent.ask("Frage", stream=False)
    assert result.answer == "trotzdem fertig"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"CPU": "Ryzen 7", "RAM": "32 GB"}', {"CPU": "Ryzen 7", "RAM": "32 GB"}),
        ('```json\n{"CPU": "M3"}\n```', {"CPU": "M3"}),
        ("Hier: {\"RAM\": \"16 GB\"} -- fertig", {"RAM": "16 GB"}),
        ("{}", {}),
        ("kein JSON", {}),
        ('{"leer": null, "gut": "ja"}', {"gut": "ja"}),
    ],
)
def test_parse_spec_json(raw: str, expected: dict[str, str]) -> None:
    assert _parse_spec_json(raw) == expected


# ---------------------------------------------------------------------------
# Stabilitaet
# ---------------------------------------------------------------------------
def test_transient_errors_are_retried(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    attempts = {"n": 0}

    def flaky(**kwargs: Any):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("Connection error")
        return _message(content="endlich da")

    monkeypatch.setattr("litellm.completion", flaky)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    result = Agent(settings, cache=None, toolbox=toolbox).ask("Frage", stream=False)
    assert result.answer == "endlich da"
    assert attempts["n"] == 3


def test_a_crashed_runner_frees_memory_and_retries(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Genau der Absturz aus der Praxis -- danach wird aufgeraeumt."""
    attempts = {"n": 0}
    freed: list[str] = []

    def crashing(**kwargs: Any):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("model runner has unexpectedly stopped, resource limitations")
        return _message(content="zweiter Versuch")

    monkeypatch.setattr("litellm.completion", crashing)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr(
        "scoutr.local_model.free_memory", lambda *a, **k: freed.append("frei") or ["qwen3:8b"]
    )
    result = Agent(settings, cache=None, toolbox=toolbox).ask("Frage", stream=False)
    assert result.answer == "zweiter Versuch"
    assert freed == ["frei"]


def test_permanent_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Ein falscher Key wird durch Wiederholen nicht richtig."""
    attempts = {"n": 0}

    def failing(**kwargs: Any):
        attempts["n"] += 1
        raise RuntimeError("AuthenticationError: invalid api key")

    monkeypatch.setattr("litellm.completion", failing)
    result = Agent(settings, cache=None, toolbox=toolbox).ask("Frage", stream=False)
    assert attempts["n"] == 1
    assert "invalid api key" in result.error


def test_old_tool_results_are_trimmed(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Sonst laeuft bei lokalen Modellen der Kontext ueber."""
    from scoutr.agent import TRIMMED_NOTE

    settings.keep_full_results = 2
    settings.max_tool_calls = 6
    searches = [
        _message(tool_calls=[_tool_call("web_search", {"query": f"q{i}"}, f"c{i}")])
        for i in range(5)
    ]
    llm = ScriptedLLM(*searches, _message(content="fertig"))
    monkeypatch.setattr("litellm.completion", llm)

    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.ask("Frage", stream=False)
    tool_messages = [m for m in agent.messages if m["role"] == "tool"]
    assert len(tool_messages) == 5
    assert sum(1 for m in tool_messages if m["content"] == TRIMMED_NOTE) == 3
    # Die juengsten bleiben vollstaendig.
    assert all(m["content"] != TRIMMED_NOTE for m in tool_messages[-2:])


def test_tool_results_are_capped(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    settings.max_tool_chars = 1200
    llm = ScriptedLLM(
        _message(tool_calls=[_tool_call("fetch_page", {"url": "https://cafe-sonntag.de/"})]),
        _message(content="fertig"),
    )
    monkeypatch.setattr("litellm.completion", llm)
    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.ask("Frage", stream=False)
    tool_message = next(m for m in agent.messages if m["role"] == "tool")
    assert len(tool_message["content"]) <= 1200


def test_a_broken_tool_does_not_end_the_run(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    def exploding(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("Werkzeug kaputt")

    monkeypatch.setattr(toolbox, "call", exploding)
    llm = ScriptedLLM(
        _message(tool_calls=[_tool_call("web_search", {"query": "x"})]),
        _message(content="trotzdem fertig"),
    )
    monkeypatch.setattr("litellm.completion", llm)
    agent = Agent(settings, cache=None, toolbox=toolbox)
    result = agent.ask("Frage", stream=False)
    assert result.answer == "trotzdem fertig"
    tool_message = next(m for m in agent.messages if m["role"] == "tool")
    assert "Werkzeug kaputt" in tool_message["content"]


def test_subagent_results_reach_the_parent(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Quellen der Subagenten muessen in der Gesamtbilanz auftauchen."""
    from scoutr.subagents import SubagentResult

    monkeypatch.setattr(
        "scoutr.subagents.run_subagents",
        lambda tasks, *a, **k: [
            SubagentResult(
                task=task,
                summary=f"Ergebnis {task}",
                sources=[{"url": f"https://{task}.de", "title": task}],
                searches=[task],
            )
            for task in tasks
        ],
    )
    llm = ScriptedLLM(
        _message(tool_calls=[_tool_call("research_subtasks", {"tasks": ["eins", "zwei"]})]),
        _message(content="zusammengefasst"),
    )
    monkeypatch.setattr("litellm.completion", llm)

    agent = Agent(settings, cache=None, toolbox=toolbox)
    result = agent.ask("Frage", stream=False)
    assert result.answer == "zusammengefasst"
    assert {source["url"] for source in result.sources} == {
        "https://eins.de",
        "https://zwei.de",
    }
    assert set(result.searches) == {"eins", "zwei"}


def test_subagent_tool_without_runner_is_reported(settings: Settings, toolbox: Toolbox) -> None:
    assert "nicht aktiv" in toolbox.call("research_subtasks", {"tasks": ["x"]})["error"]


# ---------------------------------------------------------------------------
# Automatische Vorrecherche
# ---------------------------------------------------------------------------
def _planner_llm(monkeypatch: pytest.MonkeyPatch, tasks: list[str], *answers: str):
    """Erst die Planungsantwort, dann die normalen Antworten.

    Die Chat-oder-Recherche-Vorpruefung wird hier fest auf "Recherche"
    gesetzt -- diese Tests pruefen den Planer, nicht die Vorpruefung.
    """
    import json as _json

    monkeypatch.setattr("scoutr.agent.Agent._needs_research", lambda self, q: True)
    queue = [_message(content=_json.dumps(tasks))] + [
        _message(content=answer) for answer in answers
    ]
    llm = ScriptedLLM(*queue)
    monkeypatch.setattr("litellm.completion", llm)
    return llm


def test_every_question_is_split_automatically(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    settings.subagents_auto = True
    _planner_llm(monkeypatch, ["Teil A", "Teil B"], "Endantwort")

    seen: list[list[str]] = []
    monkeypatch.setattr(
        "scoutr.agent.Agent._run_subagents",
        lambda self, tasks: seen.append(tasks) or [{"task": t, "summary": "ok"} for t in tasks],
    )
    agent = Agent(settings, cache=None, toolbox=toolbox)
    result = agent.ask("Zusammengesetzte Frage", stream=False)

    assert seen == [["Teil A", "Teil B"]]
    assert result.answer == "Endantwort"
    # Die Vorrecherche steht dem Hauptagenten zur Verfuegung -- als Text,
    # nicht als JSON.
    from scoutr.agent import PRE_RESEARCH_PREFIX

    blob = next(
        str(m.get("content", ""))
        for m in agent.messages
        if str(m.get("content", "")).startswith(PRE_RESEARCH_PREFIX)
    )
    assert "### Teil A" in blob and "### Teil B" in blob


def test_auto_research_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    settings.subagents_auto = False
    llm = ScriptedLLM(_message(content="direkt geantwortet"))
    monkeypatch.setattr("litellm.completion", llm)
    called: list[Any] = []
    monkeypatch.setattr(
        "scoutr.agent.Agent._run_subagents", lambda self, tasks: called.append(tasks) or []
    )
    result = Agent(settings, cache=None, toolbox=toolbox).ask("Frage", stream=False)
    assert called == []
    assert result.answer == "direkt geantwortet"


def test_a_failed_plan_does_not_stop_the_run(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Scheitert die Planung, macht der Hauptagent einfach selbst weiter."""
    settings.subagents_auto = True
    monkeypatch.setattr("scoutr.agent.Agent._needs_research", lambda self, q: True)
    monkeypatch.setattr(
        "scoutr.subagents.plan_subtasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Planer weg")),
    )
    llm = ScriptedLLM(_message(content="trotzdem geantwortet"))
    monkeypatch.setattr("litellm.completion", llm)
    result = Agent(settings, cache=None, toolbox=toolbox).ask("Frage", stream=False)
    assert result.answer == "trotzdem geantwortet"


def test_follow_up_questions_get_the_conversation_as_context(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """'nur die mit 4+ Sternen' ist ohne Vorgeschichte nicht recherchierbar."""
    settings.subagents_auto = True
    monkeypatch.setattr("scoutr.agent.Agent._needs_research", lambda self, q: True)
    contexts: list[str] = []

    def fake_plan(question, settings_arg, context="", limit=4):
        contexts.append(context)
        return [question]

    monkeypatch.setattr("scoutr.subagents.plan_subtasks", fake_plan)
    monkeypatch.setattr("scoutr.agent.Agent._run_subagents", lambda self, tasks: [])
    llm = ScriptedLLM(_message(content="erste"), _message(content="zweite"))
    monkeypatch.setattr("litellm.completion", llm)

    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.ask("Cafés in Köln", stream=False)
    agent.ask("nur die mit 4+ Sternen", stream=False)

    assert contexts[0] == ""
    assert "Cafés in Köln" in contexts[1]


def test_subagent_calls_count_towards_the_budget(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    settings.subagents_auto = True
    settings.max_tool_calls = 10
    _planner_llm(monkeypatch, ["A"], "fertig")
    monkeypatch.setattr(
        "scoutr.agent.Agent._run_subagents",
        lambda self, tasks: [{"task": "A", "summary": "ok", "tool_calls": 5}],
    )
    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.ask("Frage", stream=False)
    # Der Hauptagent darf danach nicht so tun, als haette er das volle Budget.
    assert agent.last_result is not None


# ---------------------------------------------------------------------------
# Regressionen aus der Fehlersuche
# ---------------------------------------------------------------------------
def test_llm_error_mid_turn_leaves_no_orphan_tool_calls(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Scheitert das LLM NACH einer Werkzeugrunde, muss der ganze Turn weg.

    Vorher blieb eine Assistant-Nachricht mit Tool-Calls stehen, deren
    Antworten weggepoppt waren -- jede weitere Frage der Sitzung wurde dann
    von der API abgelehnt.
    """
    calls = {"n": 0}

    def flaky(**kwargs: Any):
        calls["n"] += 1
        if calls["n"] == 1:
            return _message(tool_calls=[_tool_call("web_search", {"query": "x"})])
        raise RuntimeError("AuthenticationError: invalid key")

    monkeypatch.setattr("litellm.completion", flaky)
    agent = Agent(settings, cache=None, toolbox=toolbox)
    result = agent.ask("Meine Frage", stream=False)
    assert result.error
    # Kompletter Turn verworfen: nur der Systemprompt bleibt.
    assert [m["role"] for m in agent.messages] == ["system"]

    # Und die naechste Frage funktioniert wieder.
    monkeypatch.setattr("litellm.completion", ScriptedLLM(_message(content="geht wieder")))
    assert agent.ask("Neue Frage", stream=False).answer == "geht wieder"


def test_history_stores_the_question_not_the_budget_prompt(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox, tmp_path
) -> None:
    """Am Budget-Limit wurde vorher der Budget-Hinweis als Frage gespeichert."""
    from scoutr.cache import Cache

    settings.max_tool_calls = 1
    llm = ScriptedLLM(
        _message(tool_calls=[_tool_call("web_search", {"query": "x"})]),
        _message(content="Zwischenstand"),
    )
    monkeypatch.setattr("litellm.completion", llm)
    cache = Cache(tmp_path / "c.sqlite3")
    Agent(settings, cache=cache, toolbox=toolbox).ask("Cafés in Köln?", stream=False)
    assert cache.recent_history()[0].question == "Cafés in Köln?"


def test_planner_sees_the_location_filter(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Mit --location muessen auch die Teilfragen den Ort kennen."""
    settings.subagents_auto = True
    monkeypatch.setattr("scoutr.agent.Agent._needs_research", lambda self, q: True)
    settings.location = "Mönchengladbach"
    seen: dict[str, str] = {}

    def spy_plan(question, settings_arg, context="", limit=4):
        seen["question"] = question
        seen["context"] = context
        return [question]

    monkeypatch.setattr("scoutr.subagents.plan_subtasks", spy_plan)
    monkeypatch.setattr("scoutr.agent.Agent._run_subagents", lambda self, tasks: [])
    monkeypatch.setattr("litellm.completion", ScriptedLLM(_message(content="ok")))
    Agent(settings, cache=None, toolbox=toolbox).ask("Cafés mit WLAN", stream=False)
    assert "Mönchengladbach" in seen["context"]


def test_planner_context_hides_internal_messages(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Vorrecherche-Blob und Budget-Hinweis sind Regie, kein Gespraech."""
    from scoutr.agent import BUDGET_PROMPT, PRE_RESEARCH_PREFIX

    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.messages += [
        {"role": "user", "content": "Cafés in Köln\n\n[Ortsfilter: Köln · Sprache de]"},
        {"role": "user", "content": PRE_RESEARCH_PREFIX + " zerlegt: {...}"},
        {"role": "assistant", "content": "Zwei gefunden."},
        {"role": "user", "content": BUDGET_PROMPT},
        {"role": "assistant", "content": "Rest."},
        {"role": "user", "content": "aktuelle Frage"},
    ]
    context = agent._recent_context()
    assert "Cafés in Köln" in context
    assert PRE_RESEARCH_PREFIX not in context
    assert "Budget" not in context
    assert "[Ortsfilter:" not in context


# ---------------------------------------------------------------------------
# Kaputtes Tool-Call-JSON (Ollama/Gemma) -- Regression zu "Extra data"
# ---------------------------------------------------------------------------
def test_split_json_objects() -> None:
    from scoutr.agent import split_json_objects

    assert split_json_objects('{"a": 1}') == ['{"a": 1}']
    assert split_json_objects('{"a": 1}{"b": 2}') == ['{"a": 1}', '{"b": 2}']
    assert split_json_objects('{"a": 1} , {"b": 2}') == ['{"a": 1}', '{"b": 2}']
    assert split_json_objects('{"q": "x"} und noch Text dahinter') == ['{"q": "x"}']
    assert split_json_objects("voelliger Unsinn") == []
    assert split_json_objects("") == []


def test_repair_splits_concatenated_calls() -> None:
    """Zwei zusammengeklebte Objekte werden zwei eigene Aufrufe."""
    from scoutr.agent import repair_tool_calls

    repaired = repair_tool_calls(
        [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "web_search", "arguments": '{"query":"a"}{"query":"b"}'},
            }
        ]
    )
    assert len(repaired) == 2
    assert [json.loads(c["function"]["arguments"]) for c in repaired] == [
        {"query": "a"},
        {"query": "b"},
    ]
    # IDs bleiben eindeutig.
    assert len({c["id"] for c in repaired}) == 2


def test_repair_dedupes_repeated_chunks_and_fixes_garbage() -> None:
    from scoutr.agent import repair_tool_calls

    repaired = repair_tool_calls(
        [
            {"id": "c1", "type": "function",
             "function": {"name": "f", "arguments": '{"q": "x"}{"q": "x"}'}},
            {"id": "c2", "type": "function",
             "function": {"name": "g", "arguments": "kein json"}},
        ]
    )
    assert len(repaired) == 2
    assert repaired[0]["function"]["arguments"] == '{"q": "x"}'
    assert repaired[1]["function"]["arguments"] == "{}"


def test_stream_with_ollama_style_index_zero_calls(
    settings: Settings, toolbox: Toolbox
) -> None:
    """Ollama meldet jeden Tool-Call unter Index 0 mit kompletten Argumenten.

    Vorher wurden die Argumente konkateniert -- der Verlauf war danach fuer
    jede weitere Anfrage unbrauchbar ("Extra data" beim Zurueckparsen).
    """
    chunks = []
    for number, args in enumerate(('{"query": "a"}', '{"query": "b"}')):
        call = SimpleNamespace(
            index=0,
            id=f"ollama_{number}",
            function=SimpleNamespace(name="web_search", arguments=args),
        )
        delta = SimpleNamespace(content=None, tool_calls=[call])
        chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=delta)]))

    agent = Agent(settings, cache=None, toolbox=toolbox)
    message = agent._consume_stream(iter(chunks))
    assert len(message["tool_calls"]) == 2
    for call in message["tool_calls"]:
        json.loads(call["function"]["arguments"])


def test_poisoned_history_is_healed_before_sending(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Altbestand mit kaputten Argumenten darf die Sitzung nicht mehr toeten."""
    sent: list[list[dict[str, Any]]] = []

    def completion(**kwargs: Any):
        sent.append(kwargs["messages"])
        return _message(content="geht")

    monkeypatch.setattr("litellm.completion", completion)
    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "alt",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": '{"q":"a"}{"q":"b"}'},
                }
            ],
        }
    )
    agent.messages.append({"role": "tool", "tool_call_id": "alt", "name": "web_search",
                           "content": "{}"})
    result = agent.ask("Neue Frage", stream=False)
    assert result.answer == "geht"
    for message in sent[0]:
        for call in message.get("tool_calls") or []:
            json.loads(call["function"]["arguments"])


def test_json_errors_are_not_retried_as_connection_problems() -> None:
    from scoutr.agent import is_transient

    assert not is_transient("APIConnectionError: Extra data: line 1 column 73 (char 72)")
    assert not is_transient("json.decoder.JSONDecodeError: Expecting value")
    assert is_transient("APIConnectionError: Connection refused")


def test_pre_tool_commentary_is_not_rendered_as_answer(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """"Ich suche mal ..." vor einem Tool-Call darf nicht als Antwort erscheinen."""
    llm = ScriptedLLM(
        _message(content="Ich suche mal danach.",
                 tool_calls=[_tool_call("web_search", {"query": "x"})]),
        _message(content="Die echte Antwort."),
    )
    monkeypatch.setattr("litellm.completion", llm)
    chunks: list[str] = []
    agent = Agent(
        settings,
        cache=None,
        toolbox=toolbox,
        on_event=lambda name, payload: chunks.append(payload["text"])
        if name == "answer_chunk"
        else None,
    )
    result = agent.ask("Frage", stream=False)
    assert result.answer == "Die echte Antwort."
    assert chunks == ["Die echte Antwort."]


def test_final_answer_sends_a_sanitized_history(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Auch der Budget-Endspurt darf keine kaputten Argumente verschicken."""
    sent: list[list[dict[str, Any]]] = []

    def completion(**kwargs: Any):
        sent.append(kwargs["messages"])
        return _message(content="Zwischenstand")

    monkeypatch.setattr("litellm.completion", completion)
    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "alt", "type": "function",
                 "function": {"name": "web_search", "arguments": '{"q":"a"}{"q":"b"}'}}
            ],
        }
    )
    agent.messages.append(
        {"role": "tool", "tool_call_id": "alt", "name": "web_search", "content": "{}"}
    )
    assert agent._final_answer(stream=False) == "Zwischenstand"
    for message in sent[0]:
        for call in message.get("tool_calls") or []:
            json.loads(call["function"]["arguments"])


# ---------------------------------------------------------------------------
# Datum und Merkzettel im Systemprompt
# ---------------------------------------------------------------------------
def test_system_prompt_carries_todays_date(settings: Settings, toolbox: Toolbox) -> None:
    """Sonst sucht ein Modell mit altem Wissensstand nach "Test 2024"."""
    from datetime import date

    agent = Agent(settings, cache=None, toolbox=toolbox)
    assert date.today().strftime("%d.%m.%Y") in agent.messages[0]["content"]


def test_notes_are_injected_into_the_system_prompt(
    settings: Settings, toolbox: Toolbox, tmp_path
) -> None:
    from scoutr.cache import Cache

    cache = Cache(tmp_path / "c.sqlite3")
    cache.add_note("Budget fuer den Laptop: 1200 Euro")
    agent = Agent(settings, cache=cache, toolbox=toolbox)
    assert "Budget fuer den Laptop: 1200 Euro" in agent.messages[0]["content"]
    # /clear behaelt den Merkzettel.
    agent.clear()
    assert "Budget fuer den Laptop" in agent.messages[0]["content"]


def test_memory_tool_is_offered_only_with_a_cache(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox, tmp_path
) -> None:
    from scoutr.cache import Cache

    llm = ScriptedLLM(_message(content="ok"))
    monkeypatch.setattr("litellm.completion", llm)
    Agent(settings, cache=Cache(tmp_path / "c.sqlite3"), toolbox=toolbox).ask(
        "Frage", stream=False
    )
    names = [tool["function"]["name"] for tool in llm.calls[0]["tools"]]
    assert "remember" in names


# ---------------------------------------------------------------------------
# Vorpruefung: Chat oder Recherche?
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "greeting",
    ["hallo", "Hi!", "danke", "Vielen Dank!", "guten Morgen", "ok", "tschüss", "wie geht's?"],
)
def test_small_talk_skips_planning_without_any_llm_call(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox, greeting: str
) -> None:
    """"hallo" darf weder Planer noch Subagenten anwerfen -- und die
    Vorpruefung selbst kostet hier null Modellaufrufe."""
    settings.subagents_auto = True
    calls: list[str] = []

    def completion(**kwargs: Any):
        calls.append(kwargs["messages"][0]["content"][:30])
        return _message(content="Hallo! Was soll ich recherchieren?")

    monkeypatch.setattr("litellm.completion", completion)
    researched: list[Any] = []
    monkeypatch.setattr(
        "scoutr.agent.Agent._auto_research",
        lambda self, q, b: researched.append(q) or 0,
    )
    result = Agent(settings, cache=None, toolbox=toolbox).ask(greeting, stream=False)
    assert researched == []
    # Genau ein Aufruf: die Antwort selbst. Keine Triage, kein Planer.
    assert len(calls) == 1
    assert result.answer


def test_ambiguous_messages_ask_the_small_model_with_a_time_limit(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Ein Aufruf, kleines Modell, kein Denk-Modus, erzwungenes JSON."""
    settings.subagents_auto = True
    settings.subagent_model = "ollama_chat/qwen3:1.7b"
    settings.model = "ollama_chat/gemma4:12b"
    settings.planner_timeout = 20.0
    planner: dict[str, Any] = {}

    def completion(**kwargs: Any):
        if "response_format" in kwargs:
            planner.update(kwargs)
            return _message(content='{"recherche": false, "teilfragen": []}')
        return _message(content="Gern geschehen!")

    monkeypatch.setattr("litellm.completion", completion)
    researched: list[Any] = []
    monkeypatch.setattr(
        "scoutr.agent.Agent._auto_research", lambda self, q, b: researched.append(q) or 0
    )
    Agent(settings, cache=None, toolbox=toolbox).ask(
        "das zweite klingt gut, oder was meinst du?", stream=False
    )
    assert researched == []
    # Laeuft auf dem kleinen Modell, nicht auf dem grossen -- das spart auf
    # einer knappen Karte den Modellwechsel.
    assert planner["model"] == "ollama_chat/qwen3:1.7b"
    assert planner["timeout"] == 20.0
    assert planner["max_tokens"] <= 200
    assert planner["reasoning_effort"] == "disable"
    assert planner["num_ctx"] == 2048


def test_one_call_covers_triage_and_planning(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Frueher zwei Aufrufe -- jetzt liefert einer Entscheidung UND Teilfragen."""
    settings.subagents_auto = True
    planner_calls = {"n": 0}

    def completion(**kwargs: Any):
        if "response_format" in kwargs:
            planner_calls["n"] += 1
            return _message(
                content='{"recherche": true, "teilfragen": ["Teil A", "Teil B"]}'
            )
        return _message(content="Antwort.")

    monkeypatch.setattr("litellm.completion", completion)
    seen: list[list[str]] = []
    monkeypatch.setattr(
        "scoutr.agent.Agent._run_subagents",
        lambda self, tasks: seen.append(tasks) or [{"task": t, "summary": "ok"} for t in tasks],
    )
    Agent(settings, cache=None, toolbox=toolbox).ask("Zusammengesetzte Frage", stream=False)
    assert seen == [["Teil A", "Teil B"]]
    assert planner_calls["n"] == 1, "kein zweiter Planungsaufruf"


def test_research_verdict_starts_the_planner(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    settings.subagents_auto = True

    def completion(**kwargs: Any):
        if "response_format" in kwargs:
            return _message(content='{"recherche": true, "teilfragen": ["Teil"]}')
        return _message(content="Ergebnis.")

    monkeypatch.setattr("litellm.completion", completion)
    researched: list[Any] = []
    monkeypatch.setattr(
        "scoutr.agent.Agent._auto_research", lambda self, q, b: researched.append(q) or 0
    )
    Agent(settings, cache=None, toolbox=toolbox).ask(
        "welche kaffeemuehle bis 150 euro", stream=False
    )
    assert researched == ["welche kaffeemuehle bis 150 euro"]


def test_triage_failure_defaults_to_research(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Timeout oder toter Server: lieber einmal zu viel geplant als eine
    echte Frage unbeantwortet."""
    settings.subagents_auto = True

    def completion(**kwargs: Any):
        if "response_format" in kwargs:
            raise TimeoutError("zu langsam")
        return _message(content="Ergebnis.")

    monkeypatch.setattr("litellm.completion", completion)
    researched: list[Any] = []
    monkeypatch.setattr(
        "scoutr.agent.Agent._auto_research", lambda self, q, b: researched.append(q) or 0
    )
    Agent(settings, cache=None, toolbox=toolbox).ask("irgendeine frage", stream=False)
    assert researched == ["irgendeine frage"]


def test_unclear_triage_answers_default_to_research(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    settings.subagents_auto = True

    def completion(**kwargs: Any):
        if "response_format" in kwargs:
            return _message(content="Vielleicht ein bisschen von beidem?")
        return _message(content="Ergebnis.")

    monkeypatch.setattr("litellm.completion", completion)
    researched: list[Any] = []
    monkeypatch.setattr(
        "scoutr.agent.Agent._auto_research", lambda self, q, b: researched.append(q) or 0
    )
    Agent(settings, cache=None, toolbox=toolbox).ask("hmm schwierig", stream=False)
    assert researched == ["hmm schwierig"]


def test_real_questions_are_never_smalltalk() -> None:
    """Die Heuristik darf keine echten Fragen schlucken."""
    from scoutr.agent import SMALL_TALK_RE

    for question in (
        "hallo, welche cafés in köln haben wlan?",
        "danke -- und was kostet das teurere?",
        "wie geht das mit dem export?",
        "test von notebooks bis 1200 euro",
        "ok und sonntags?",
    ):
        assert not SMALL_TALK_RE.match(question), question


def test_agent_requests_the_large_context_from_ollama(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Der Hauptaufruf muss num_ctx wirklich mitschicken -- sonst schneidet
    Ollama den Verlauf still ab und Nachfragen gehen ins Leere."""
    settings.model = "ollama_chat/gemma4:12b"
    seen: dict[str, Any] = {}

    def completion(**kwargs: Any):
        seen.update(kwargs)
        return _message(content="ok")

    monkeypatch.setattr("litellm.completion", completion)
    Agent(settings, cache=None, toolbox=toolbox).ask("Frage", stream=False)
    assert seen["num_ctx"] == settings.context_tokens


# ---------------------------------------------------------------------------
# Kontext ueber mehrere Turns
# ---------------------------------------------------------------------------
def _bare_agent(context_tokens: int) -> Agent:
    from scoutr.config import Settings as RealSettings

    agent = Agent.__new__(Agent)
    agent.settings = RealSettings(model="ollama_chat/x", context_tokens=context_tokens)
    agent.messages = [{"role": "system", "content": "Systemprompt"}]
    return agent


def test_pre_research_blocks_do_not_pile_up(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Jeder Turn hinterliess einen Vorrecherche-Block von mehreren Kilobyte,
    der nie gekuerzt wurde -- nach zwei Turns lief jedes kleine Fenster ueber
    und der Anbieter warf den Anfang weg."""
    from scoutr.agent import PRE_RESEARCH_PREFIX, TRIMMED_RESEARCH

    settings.subagents_auto = True
    monkeypatch.setattr("scoutr.agent.Agent._needs_research", lambda self, q: True)
    monkeypatch.setattr(
        "scoutr.subagents.plan_subtasks", lambda q, s, context="", limit=4: [q]
    )
    monkeypatch.setattr(
        "scoutr.agent.Agent._run_subagents",
        lambda self, tasks: [{"task": tasks[0], "summary": "Ergebnis. " * 200}],
    )
    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: _message(content="Antwort."),
    )

    agent = Agent(settings, cache=None, toolbox=toolbox)
    sizes = []
    for number in range(4):
        agent.ask(f"Frage {number}", stream=False)
        agent._trim_history()
        sizes.append(sum(len(str(m.get("content") or "")) for m in agent.messages))

    full_blocks = [
        m
        for m in agent.messages
        if str(m.get("content") or "").startswith(PRE_RESEARCH_PREFIX)
    ]
    assert len(full_blocks) == 1, "nur der juengste Block darf voll bleiben"
    assert any(m.get("content") == TRIMMED_RESEARCH for m in agent.messages)
    # Der Verlauf waechst nur noch um die Antworten, nicht um die Bloecke.
    assert sizes[-1] - sizes[-2] < 500


def test_history_stays_within_the_context_window() -> None:
    """Laeuft das Fenster ueber, wirft der Anbieter STILL den Anfang weg --
    lieber selbst kuerzen und den Systemprompt behalten."""
    for window in (2048, 4096, 16384):
        agent = _bare_agent(window)
        for number in range(12):
            agent.messages.append({"role": "user", "content": f"Frage {number}: " + "x" * 900})
            agent.messages.append({"role": "assistant", "content": "y" * 900})
        agent._trim_history()
        assert agent._history_chars() <= agent._budget_chars(), window
        assert agent.messages[0]["role"] == "system", window


def test_the_current_question_is_never_shortened() -> None:
    agent = _bare_agent(2048)
    for number in range(12):
        agent.messages.append({"role": "user", "content": f"alte Frage {number} " + "x" * 900})
        agent.messages.append({"role": "assistant", "content": "y" * 900})
    agent.messages.append({"role": "user", "content": "Die aktuelle Frage " + "z" * 900})
    agent._trim_history()
    assert agent.messages[-1]["content"].endswith("z" * 100)


def test_tool_calls_and_answers_stay_paired_while_trimming() -> None:
    """Ein Aufruf ohne Antwort (oder umgekehrt) macht den Verlauf ungueltig."""
    agent = _bare_agent(1024)
    for number in range(8):
        agent.messages.append({"role": "user", "content": "F" * 400})
        agent.messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{number}",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": "{}"},
                    }
                ],
            }
        )
        agent.messages.append(
            {"role": "tool", "tool_call_id": f"c{number}", "name": "web_search",
             "content": "R" * 400}
        )
        agent.messages.append({"role": "assistant", "content": "A" * 400})

    agent._trim_history()
    call_ids = {
        call["id"] for message in agent.messages for call in (message.get("tool_calls") or [])
    }
    answer_ids = {m["tool_call_id"] for m in agent.messages if m.get("role") == "tool"}
    assert call_ids == answer_ids
    assert agent.messages[1]["role"] != "tool", "keine verwaiste Tool-Antwort vorn"


def test_earlier_questions_survive_a_normal_conversation(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    """Der eigentliche Wunsch: Nachfragen muessen die frueheren Turns kennen."""
    settings.context_tokens = 16384
    monkeypatch.setattr("litellm.completion", ScriptedLLM(*[_message(content="ok")] * 5))
    agent = Agent(settings, cache=None, toolbox=toolbox)
    for number in range(5):
        agent.ask(f"Frage Nummer {number}", stream=False)
    text = " ".join(str(m.get("content") or "") for m in agent.messages)
    for number in range(5):
        assert f"Frage Nummer {number}" in text


# ---------------------------------------------------------------------------
# Ausfuehrlichkeit und Kontext-Sparsamkeit
# ---------------------------------------------------------------------------
def test_system_prompt_asks_for_thorough_answers() -> None:
    """Der Prompt hat frueher zur Knappheit gedraengt ("knapp", "hoechstens
    ein Satz") -- genau das machte die Antworten duenn."""
    from scoutr.agent import SYSTEM_PROMPT

    assert "ausfuehrlich" in SYSTEM_PROMPT.lower()
    assert "GROSSZUEGIG" in SYSTEM_PROMPT
    assert "Fazit" in SYSTEM_PROMPT
    assert "Nicht gefunden:" in SYSTEM_PROMPT
    # Die alten Bremsen sind weg.
    assert "Deutsch, knapp" not in SYSTEM_PROMPT
    assert "hoechstens ein Satz" not in SYSTEM_PROMPT
    # Vollstaendigkeit darf Genauigkeit nie ersetzen.
    assert "Vollstaendigkeit ersetzt niemals Genauigkeit" in SYSTEM_PROMPT


def test_budget_prompt_still_wants_everything() -> None:
    from scoutr.agent import BUDGET_PROMPT

    assert "vollstaendig" in BUDGET_PROMPT
    assert "Nicht gefunden:" in BUDGET_PROMPT


def test_findings_are_plain_text_not_json() -> None:
    """JSON kostet ein Achtel mehr Zeichen und liest sich fuer kleine
    Modelle schlechter."""
    from scoutr.agent import format_findings

    text = format_findings(
        [
            {
                "task": "Cafés mit WLAN",
                "summary": "Zwei gefunden.",
                "sources": ["https://a.de", "https://a.de", "https://b.de"],
            }
        ]
    )
    assert text.startswith("### Cafés mit WLAN")
    assert '{"' not in text
    # Doppelte Quellen fliegen raus, Reihenfolge bleibt.
    assert text.count("https://a.de") == 1
    assert text.index("https://a.de") < text.index("https://b.de")


def test_findings_report_failed_subtasks() -> None:
    from scoutr.agent import format_findings

    text = format_findings([{"task": "Bewertungen", "error": "Modell weg"}])
    assert "nicht beantwortet" in text and "Modell weg" in text


def test_pre_research_reaches_the_agent_as_text(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    settings.subagents_auto = True
    monkeypatch.setattr("scoutr.agent.Agent._needs_research", lambda self, q: True)
    monkeypatch.setattr(
        "scoutr.subagents.plan_subtasks", lambda q, s, context="", limit=4: ["Teil A"]
    )
    monkeypatch.setattr(
        "scoutr.agent.Agent._run_subagents",
        lambda self, tasks: [
            {"task": "Teil A", "summary": "Ausfuehrliches Ergebnis.",
             "sources": ["https://a.de"]}
        ],
    )
    monkeypatch.setattr("litellm.completion", ScriptedLLM(_message(content="Antwort.")))
    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.ask("Frage", stream=False)
    blob = next(
        m["content"] for m in agent.messages if str(m.get("content", "")).startswith(
            "Zu deiner Unterstuetzung"
        )
    )
    assert "### Teil A" in blob
    assert "Ausfuehrliches Ergebnis." in blob
    assert '"summary"' not in blob


# -- Rueckfragen nur mit Gegenueber --------------------------------------
def test_ask_tool_appears_only_with_a_handler(settings: Settings) -> None:
    """Ohne jemanden am anderen Ende waere das Werkzeug eine Falle."""
    agent = Agent(settings)
    assert "ask_user" not in [tool["function"]["name"] for tool in agent.tools]

    agent.set_ask_handler(lambda question, options: "ja")
    assert "ask_user" in [tool["function"]["name"] for tool in agent.tools]

    agent.set_ask_handler(None)
    assert "ask_user" not in [tool["function"]["name"] for tool in agent.tools]


def test_ask_instructions_follow_the_handler(settings: Settings) -> None:
    agent = Agent(settings)
    assert "ask_user" not in agent.messages[0]["content"]

    agent.set_ask_handler(lambda question, options: "ja")
    prompt = agent.messages[0]["content"]
    assert "ask_user(question, options)" in prompt
    assert "hoechstens zweimal" in prompt

    # Zweimal anmelden haengt den Absatz nicht zweimal an.
    agent.set_ask_handler(lambda question, options: "ja")
    assert agent.messages[0]["content"] == prompt

    agent.set_ask_handler(None)
    assert "ask_user" not in agent.messages[0]["content"]


def test_setting_the_handler_keeps_the_notes_in_the_prompt(tmp_path) -> None:
    """Der Merkzettel steht hinter dem Systemprompt -- er darf nicht verschwinden."""
    from scoutr.cache import Cache
    from scoutr.config import Settings as S

    settings = S(model="openai/gpt-4o", data_dir=tmp_path / "d", subagents_auto=False)
    cache = Cache(settings.db_path, settings.cache_ttl_hours)
    cache.add_note("Ich wohne in Bremen.")
    agent = Agent(settings, cache=cache)
    agent.set_ask_handler(lambda question, options: "ja")
    agent.set_ask_handler(None)
    assert "Bremen" in agent.messages[0]["content"]
