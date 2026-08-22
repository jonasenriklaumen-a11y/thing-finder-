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


def test_tool_schemas_are_exactly_two() -> None:
    names = [schema["function"]["name"] for schema in TOOL_SCHEMAS]
    assert names == ["web_search", "fetch_page"]


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
