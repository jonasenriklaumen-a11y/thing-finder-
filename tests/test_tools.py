"""Tests fuer die beiden Agenten-Werkzeuge inklusive Cache-Verhalten."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from scoutr.cache import Cache
from scoutr.config import Settings
from scoutr.fetch import Fetcher, RobotsPolicy
from scoutr.models import SearchResult
from scoutr.tools import TOOL_SCHEMAS, Toolbox


def _mock_fetcher(handler) -> Fetcher:
    fetcher = Fetcher(
        user_agent="scoutr-test/0.1", timeout=5, delay_seconds=0, enable_browser=False
    )
    fetcher._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    fetcher.robots = RobotsPolicy(fetcher._client, "scoutr-test/0.1")
    return fetcher


def _html_handler(html: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    return handler


def test_core_tool_schemas() -> None:
    names = [schema["function"]["name"] for schema in TOOL_SCHEMAS]
    assert names == ["web_search", "fetch_page", "search_news", "calculate"]


def test_web_search_uses_settings_defaults(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    captured: dict[str, Any] = {}

    def fake_search(query, **kwargs):
        captured.update(query=query, **kwargs)
        return [SearchResult(title="T", url="https://a.de/", snippet="S", rank=1)]

    monkeypatch.setattr("scoutr.tools.search_web", fake_search)
    settings.country = "at"
    settings.lang = "de"
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    result = box.web_search("cafés")
    assert captured["country"] == "at"
    assert captured["count"] == settings.max_results_default
    assert captured["backend"] == "duckduckgo"
    assert result["results"][0]["url"] == "https://a.de/"
    assert box.stats.searches == ["cafés"]


def test_web_search_result_is_cached(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path
) -> None:
    calls: list[str] = []

    def fake_search(query, **kwargs):
        calls.append(query)
        return [SearchResult(title="T", url="https://a.de/", snippet="S", rank=1)]

    monkeypatch.setattr("scoutr.tools.search_web", fake_search)
    cache = Cache(tmp_path / "c.sqlite3")
    box = Toolbox(settings, cache=cache, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    box.web_search("gleiche frage")
    box.web_search("gleiche frage")
    assert len(calls) == 1


def test_search_error_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from scoutr.search import SearchError

    def failing(*args, **kwargs):
        raise SearchError("keine Verbindung")

    monkeypatch.setattr("scoutr.tools.search_web", failing)
    box = Toolbox(settings, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    result = box.web_search("egal")
    assert result["results"] == []
    assert "keine Verbindung" in result["error"]


def test_fetch_page_returns_text_and_records_source(fixture_html, settings: Settings) -> None:
    handler = _html_handler(fixture_html("plain_article.html"))
    box = Toolbox(settings, fetcher=_mock_fetcher(handler))
    payload = box.fetch_page("https://cafe-sonntag.de/")
    assert payload["ok"] is True
    assert "Franzbrötchen" in payload["text"]
    assert box.stats.sources[0]["domain"] == "cafe-sonntag.de"


def test_fetch_page_keeps_search_hint_for_blocked_pages(settings: Settings) -> None:
    box = Toolbox(settings, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    box.seen_results["https://www.amazon.de/dp/B0TEST"] = SearchResult(
        title="Lenovo Yoga Pro 7", url="https://www.amazon.de/dp/B0TEST", snippet="ab 1099 EUR"
    )
    payload = box.fetch_page("https://www.amazon.de/dp/B0TEST")
    assert payload["ok"] is False
    assert payload["skipped_reason"] == "blocked"
    assert payload["search_title"] == "Lenovo Yoga Pro 7"
    assert "1099" in payload["search_snippet"]
    assert box.stats.skipped["https://www.amazon.de/dp/B0TEST"] == "blocked"


def test_fetch_page_is_cached(fixture_html, settings: Settings, tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(str(request.url))
        return httpx.Response(
            200, text=fixture_html("plain_article.html"), headers={"content-type": "text/html"}
        )

    cache = Cache(tmp_path / "c.sqlite3")
    box = Toolbox(settings, cache=cache, fetcher=_mock_fetcher(handler))
    box.fetch_page("https://cafe-sonntag.de/")
    second = box.fetch_page("https://cafe-sonntag.de/")
    assert len(calls) == 1
    assert second["ok"] is True


def test_consent_wall_is_skipped_with_reason(fixture_html, settings: Settings) -> None:
    box = Toolbox(settings, fetcher=_mock_fetcher(_html_handler(fixture_html("consent_wall.html"))))
    payload = box.fetch_page("https://zeitung.example/artikel")
    assert payload["skipped_reason"] == "consent_required"
    assert "nicht umgangen" in payload["note"]


PROSE_PRODUCT_PAGE = """
<html><head><title>Nordlicht X1 im Ueberblick</title></head><body><main>
<h1>Nordlicht X1</h1>
<p>Das Nordlicht X1 ist ein kompaktes Notebook fuer unterwegs. Wir haben es ueber mehrere
   Wochen benutzt und dabei vor allem auf Display und Akku geachtet. Der Eindruck ist gut,
   auch wenn die Tastatur gewoehnungsbeduerftig bleibt und der Luefter unter Last hoerbar wird.</p>
<h2>Technische Daten</h2>
<p>Verbaut ist ein Achtkerner mit 32 GB Arbeitsspeicher; das Display misst 14 Zoll und loest
   mit 2880 x 1800 Bildpunkten auf. Der Akku fasst 70 Wattstunden, das Gewicht liegt bei
   1,2 Kilogramm. Alles steht hier als Fliesstext statt in einer Tabelle.</p>
</main></body></html>
"""


def test_llm_spec_fallback_is_used_when_structured_data_missing(settings: Settings) -> None:
    def extractor(text: str, url: str) -> dict[str, str]:
        assert "Nordlicht X1" in text
        return {"RAM": "32 GB", "Display": '14 Zoll, 2880 x 1800'}

    box = Toolbox(
        settings,
        spec_extractor=extractor,
        fetcher=_mock_fetcher(_html_handler(PROSE_PRODUCT_PAGE)),
    )
    payload = box.fetch_page("https://tests.example/nordlicht-x1")
    assert payload["products"][0]["specs"]["RAM"] == "32 GB"


def test_llm_spec_fallback_is_skipped_on_non_product_pages(
    fixture_html, settings: Settings
) -> None:
    calls: list[str] = []

    def extractor(text: str, url: str) -> dict[str, str]:
        calls.append(url)
        return {"egal": "wert"}

    box = Toolbox(
        settings,
        spec_extractor=extractor,
        fetcher=_mock_fetcher(_html_handler(fixture_html("plain_article.html"))),
    )
    payload = box.fetch_page("https://cafe-sonntag.de/")
    assert payload["ok"] is True
    assert calls == []
    assert "products" not in payload


def test_events_are_emitted(
    fixture_html, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "scoutr.tools.search_web",
        lambda *args, **kwargs: [SearchResult(title="T", url="https://a.de/")],
    )
    box = Toolbox(
        settings,
        on_event=lambda name, payload: events.append((name, payload)),
        fetcher=_mock_fetcher(_html_handler(fixture_html("plain_article.html"))),
    )
    box.web_search("frage")
    box.fetch_page("https://cafe-sonntag.de/")
    assert [name for name, _ in events] == ["search", "search_done", "fetch", "fetch_done"]


def test_dispatch_unknown_tool(settings: Settings) -> None:
    box = Toolbox(settings, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    assert "Unbekanntes Werkzeug" in box.call("rm_rf", {})["error"]


def test_transient_fetch_failures_are_not_cached(
    settings: Settings, tmp_path: Path
) -> None:
    """Ein Timeout von jetzt darf nicht 24 Stunden lang festgeschrieben sein."""
    from scoutr.cache import Cache

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ReadTimeout("zu langsam", request=request)
        body = "Endlich erreichbar. " * 30
        return httpx.Response(
            200,
            text=f"<html><body><main><p>{body}</p></main></body></html>",
            headers={"content-type": "text/html"},
        )

    cache = Cache(tmp_path / "c.sqlite3")
    box = Toolbox(settings, cache=cache, fetcher=_mock_fetcher(handler))
    first = box.fetch_page("https://langsam.example/")
    assert first["skipped_reason"] == "timeout"
    # Zweiter Versuch trifft die Seite wirklich -- kein Cache-Treffer.
    second = box.fetch_page("https://langsam.example/")
    assert second["ok"] is True


def test_stable_failures_stay_cached(settings: Settings, tmp_path: Path) -> None:
    """blocked dagegen ist stabil und darf liegen bleiben."""
    from scoutr.cache import Cache

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts["n"] += 1
        return httpx.Response(403, text="nope")

    cache = Cache(tmp_path / "c.sqlite3")
    box = Toolbox(settings, cache=cache, fetcher=_mock_fetcher(handler))
    box.fetch_page("https://zu.example/")
    box.fetch_page("https://zu.example/")
    assert attempts["n"] == 1


# ---------------------------------------------------------------------------
# News, Rechner, Merkzettel, Fallbacks
# ---------------------------------------------------------------------------
def test_search_news_carries_date_and_source(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(
        "scoutr.tools.search_news",
        lambda query, **kwargs: [
            SearchResult(
                title="Neuer Laptop vorgestellt",
                url="https://news.example/artikel",
                snippet="[2026-08-24 · heise online] Der Hersteller zeigt ...",
            )
        ],
    )
    box = Toolbox(settings, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    payload = box.search_news("laptop neuheiten")
    assert payload["results"][0]["snippet"].startswith("[2026-08-24")
    assert box.stats.news_searches == ["laptop neuheiten"]
    assert box.stats.tool_calls == 1


def test_news_falls_back_to_web_search(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Faellt die News-Vertikale aus, gibt es normale Treffer statt keiner."""
    from scoutr.search import SearchError

    def failing_news(query, **kwargs):
        raise SearchError("News tot")

    monkeypatch.setattr("scoutr.tools.search_news", failing_news)
    monkeypatch.setattr(
        "scoutr.tools.search_web",
        lambda query, **kwargs: [SearchResult(title="Web-Treffer", url="https://a.de/")],
    )
    events: list[str] = []
    box = Toolbox(
        settings,
        on_event=lambda name, payload: events.append(name),
        fetcher=_mock_fetcher(_html_handler("<html></html>")),
    )
    payload = box.search_news("aktuelles")
    assert payload["results"][0]["title"] == "Web-Treffer"
    assert "fallback" in events


def test_web_search_falls_back_to_open_metasearch(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """SearXNG down -> die offene Metasuche uebernimmt."""
    from scoutr.search import SearchError

    settings.search_backend = "searxng"
    calls: list[str] = []

    def routing(query, count, country, lang, backend="duckduckgo", **kwargs):
        calls.append(backend)
        if backend == "searxng":
            raise SearchError("Instanz nicht erreichbar")
        return [SearchResult(title="Metasuche-Treffer", url="https://b.de/")]

    monkeypatch.setattr("scoutr.tools.search_web", routing)
    box = Toolbox(settings, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    payload = box.web_search("frage")
    assert payload["results"][0]["title"] == "Metasuche-Treffer"
    assert calls == ["searxng", "duckduckgo"]


def test_open_metasearch_has_no_further_fallback(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from scoutr.search import SearchError

    settings.search_backend = "duckduckgo"
    calls: list[str] = []

    def failing(query, count, country, lang, backend="duckduckgo", **kwargs):
        calls.append(backend)
        raise SearchError("alles tot")

    monkeypatch.setattr("scoutr.tools.search_web", failing)
    box = Toolbox(settings, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    assert "error" in box.web_search("frage")
    assert calls == ["duckduckgo"]


def test_calculate_tool(settings: Settings) -> None:
    box = Toolbox(settings, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    assert box.call("calculate", {"expression": "(1099 + 1149) / 2"})["result"] == "1124"
    assert "error" in box.call("calculate", {"expression": "__import__('os')"})
    assert box.stats.calculations == 2


def test_remember_tool_persists_notes(settings: Settings, tmp_path: Path) -> None:
    from scoutr.cache import Cache

    cache = Cache(tmp_path / "c.sqlite3")
    box = Toolbox(settings, cache=cache, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    payload = box.call("remember", {"text": "Budget: 1200 Euro"})
    assert payload["saved"] is True
    assert [note.text for note in cache.list_notes()] == ["Budget: 1200 Euro"]
    assert box.stats.notes_saved == 1


def test_remember_without_cache_reports_it(settings: Settings) -> None:
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    assert "error" in box.remember("etwas")


# -- Rueckfragen an den Nutzer -------------------------------------------
def test_ask_user_without_anyone_to_ask(settings: Settings) -> None:
    """Ohne Gegenueber ist das kein Fehler -- das Modell soll weiterarbeiten."""
    box = Toolbox(settings)
    result = box.ask_user("Welches Budget?")
    assert result["answered"] is False
    assert "Annahme" in result["note"]
    assert box.stats.questions == 0


def test_ask_user_passes_question_and_options(settings: Settings) -> None:
    seen: list[tuple[str, list[str]]] = []

    box = Toolbox(settings)
    box.ask_handler = lambda question, options: (seen.append((question, options)), "bis 1200")[1]
    result = box.ask_user("Welches Budget?", ["bis 800", "bis 1200", " ", "egal"])
    assert result == {"answered": True, "question": "Welches Budget?", "answer": "bis 1200"}
    assert seen == [("Welches Budget?", ["bis 800", "bis 1200", "egal"])]  # Leeres fliegt raus
    assert box.stats.questions == 1


def test_ask_user_emits_events(settings: Settings) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    box = Toolbox(settings, on_event=lambda name, payload: events.append((name, payload)))
    box.ask_handler = lambda question, options: "ja"
    box.ask_user("Passt das?", ["ja", "nein"])
    assert [name for name, _ in events] == ["ask", "ask_done"]
    assert events[0][1] == {"question": "Passt das?", "options": ["ja", "nein"]}
    assert events[1][1]["answer"] == "ja"


def test_ask_user_stops_after_two_questions(settings: Settings) -> None:
    """Wer dreimal fragt, hat die Anfrage nicht verstanden."""
    box = Toolbox(settings)
    box.ask_handler = lambda question, options: "egal"
    assert box.ask_user("Erste?")["answered"] is True
    assert box.ask_user("Zweite?")["answered"] is True
    third = box.ask_user("Dritte?")
    assert third["answered"] is False
    assert "Annahme" in third["note"]
    assert box.stats.questions == 2


def test_no_answer_is_not_an_error(settings: Settings) -> None:
    box = Toolbox(settings)
    box.ask_handler = lambda question, options: ""
    result = box.ask_user("Welches Budget?")
    assert result["answered"] is False
    assert "Annahme" in result["note"]


def test_a_broken_ask_handler_does_not_kill_the_turn(settings: Settings) -> None:
    def boom(question: str, options: list[str]) -> str:
        raise RuntimeError("Browser weg")

    box = Toolbox(settings)
    box.ask_handler = boom
    assert box.ask_user("Und nun?") == {
        "answered": False,
        "note": "Rueckfrage fehlgeschlagen: Browser weg",
    }


def test_empty_question_is_rejected(settings: Settings) -> None:
    box = Toolbox(settings)
    box.ask_handler = lambda question, options: "nie gefragt"
    assert "error" in box.ask_user("   ")


def test_questions_do_not_eat_the_research_budget(settings: Settings) -> None:
    box = Toolbox(settings)
    box.ask_handler = lambda question, options: "ja"
    box.ask_user("Passt das?")
    assert box.stats.tool_calls == 0


def test_ask_user_is_reachable_through_call(settings: Settings) -> None:
    box = Toolbox(settings)
    box.ask_handler = lambda question, options: f"{question}|{','.join(options)}"
    result = box.call("ask_user", {"question": "Wo?", "options": ["hier", "dort"]})
    assert result["answer"] == "Wo?|hier,dort"
