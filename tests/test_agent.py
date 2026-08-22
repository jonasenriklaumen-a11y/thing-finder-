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
    assert names == ["web_search", "fetch_page"]


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
