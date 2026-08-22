"""Tests fuer die Suchschicht -- alle Netzwerkaufrufe sind gemockt."""

from __future__ import annotations

import httpx
import pytest

from scoutr import search
from scoutr.models import SearchResult
from scoutr.search import SearchError, search_web


def test_region_mapping() -> None:
    assert search._region("de", "de") == "de-de"
    assert search._region("US", "EN") == "us-en"
    assert search._region("", "") == "de-de"


def test_duckduckgo_backend_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDDGS:
        def __init__(self, *args, **kwargs) -> None:
            self.kwargs = kwargs

        def text(self, query: str, **kwargs):
            assert kwargs["region"] == "de-de"
            return [
                {
                    "title": "Café Nordwand",
                    "href": "https://cafe-nordwand.de/",
                    "body": "Kaffee, WLAN, Steckdosen",
                }
            ]

    monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
    results = search_web("cafés mönchengladbach", count=5, backend="duckduckgo")
    assert len(results) == 1
    assert results[0].title == "Café Nordwand"
    assert results[0].source_domain == "cafe-nordwand.de"
    assert results[0].rank == 1


def test_duckduckgo_errors_become_search_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from ddgs.exceptions import DDGSException

    class FailingDDGS:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def text(self, query: str, **kwargs):
            raise DDGSException("boom")

    monkeypatch.setattr("ddgs.DDGS", FailingDDGS)
    with pytest.raises(SearchError, match="DuckDuckGo"):
        search_web("test", backend="duckduckgo")


def test_dedupe_and_count_limit() -> None:
    raw = [
        SearchResult(title="a", url="https://x.de/1"),
        SearchResult(title="a2", url="https://x.de/1"),
        SearchResult(title="b", url="https://y.de/2"),
        SearchResult(title="c", url="https://z.de/3"),
    ]
    out = search._dedupe(raw, count=2)
    assert [result.url for result in out] == ["https://x.de/1", "https://y.de/2"]
    assert [result.rank for result in out] == [1, 2]


def test_brave_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url, **kwargs):
        assert kwargs["headers"]["X-Subscription-Token"] == "key-123"
        assert kwargs["params"]["country"] == "DE"
        return httpx.Response(
            200,
            json={"web": {"results": [{"title": "T", "url": "https://a.de", "description": "D"}]}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    results = search_web("q", backend="brave", api_key="key-123")
    assert results[0].url == "https://a.de"


def test_brave_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    with pytest.raises(SearchError, match="BRAVE_API_KEY"):
        search_web("q", backend="brave")


def test_tavily_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url, **kwargs):
        assert kwargs["json"]["api_key"] == "tv-1"
        return httpx.Response(
            200,
            json={"results": [{"title": "T", "url": "https://b.de", "content": "C"}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    results = search_web("q", backend="tavily", api_key="tv-1")
    assert results[0].snippet == "C"


def test_unknown_backend() -> None:
    with pytest.raises(SearchError, match="Unbekannte Suchmaschine"):
        search_web("q", backend="altavista")


def test_empty_query_short_circuits() -> None:
    assert search_web("   ") == []
