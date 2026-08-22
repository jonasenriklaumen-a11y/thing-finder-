"""Tests fuer die Suchschicht -- alle Netzwerkaufrufe sind gemockt."""

from __future__ import annotations

import httpx
import pytest

from scoutr import search
from scoutr.models import SearchResult
from scoutr.search import KEYLESS_BACKENDS, OPEN_ENGINES, SearchError, search_web


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
            assert kwargs["backend"] == "auto"
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
    with pytest.raises(SearchError, match="Suche fehlgeschlagen"):
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


# ---------------------------------------------------------------------------
# Offene Backends ohne Schluessel
# ---------------------------------------------------------------------------
def test_keyless_backends_need_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die schluessellosen Backends duerfen ohne jede Umgebungsvariable laufen."""
    for name in ("BRAVE_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert "duckduckgo" in KEYLESS_BACKENDS
    assert "searxng" in KEYLESS_BACKENDS
    assert "brave" not in KEYLESS_BACKENDS
    assert "tavily" not in KEYLESS_BACKENDS


def test_specific_open_engines_are_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class FakeDDGS:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def text(self, query: str, **kwargs):
            captured["backend"] = kwargs["backend"]
            return [{"title": "T", "href": "https://a.de/", "body": "S"}]

    monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
    search_web("q", backend="open", engines="duckduckgo, mojeek")
    assert captured["backend"] == "duckduckgo,mojeek"


def test_unknown_engine_is_rejected_with_the_available_list() -> None:
    with pytest.raises(SearchError, match="Unbekannte Engine") as excinfo:
        search_web("q", backend="open", engines="google")
    assert "mojeek" in str(excinfo.value)


def test_all_open_engines_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDDGS:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def text(self, query: str, **kwargs):
            return [{"title": "T", "href": "https://a.de/", "body": "S"}]

    monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
    for engine in OPEN_ENGINES:
        assert search_web("q", backend="open", engines=engine)


def test_engine_failure_message_points_to_the_alternatives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ddgs.exceptions import DDGSException

    class FailingDDGS:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def text(self, query: str, **kwargs):
            raise DDGSException("alle Engines tot")

    monkeypatch.setattr("ddgs.DDGS", FailingDDGS)
    with pytest.raises(SearchError) as excinfo:
        search_web("q", backend="open")
    message = str(excinfo.value)
    assert "SCOUTR_SEARCH_ENGINES" in message
    assert "searxng" in message


# ---------------------------------------------------------------------------
# SearXNG
# ---------------------------------------------------------------------------
def test_searxng_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url, **kwargs):
        assert url == "http://localhost:8080/search"
        assert kwargs["params"]["format"] == "json"
        assert kwargs["params"]["language"] == "de"
        return httpx.Response(
            200,
            json={"results": [{"title": "T", "url": "https://a.de", "content": "C"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    results = search_web("q", backend="searxng", instance_url="http://localhost:8080")
    assert results[0].url == "https://a.de"
    assert results[0].snippet == "C"


def test_searxng_trailing_slash_is_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        return httpx.Response(200, json={"results": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    search_web("q", backend="searxng", instance_url="https://such.example/")
    assert seen == ["https://such.example/search"]


def test_searxng_without_url_explains_how_to_get_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOUTR_SEARXNG_URL", raising=False)
    with pytest.raises(SearchError) as excinfo:
        search_web("q", backend="searxng")
    assert "docker run" in str(excinfo.value)


def test_searxng_403_explains_the_json_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kwargs: httpx.Response(403, request=httpx.Request("GET", url)),
    )
    with pytest.raises(SearchError, match=r"search\.formats"):
        search_web("q", backend="searxng", instance_url="https://such.example")


def test_searxng_html_response_explains_the_json_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kwargs: httpx.Response(
            200, text="<html>keine JSON-Ausgabe</html>", request=httpx.Request("GET", url)
        ),
    )
    with pytest.raises(SearchError, match="kein JSON"):
        search_web("q", backend="searxng", instance_url="https://such.example")


def test_searxng_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(url, **kwargs):
        raise httpx.ConnectError("kein Netz", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", failing)
    with pytest.raises(SearchError, match="nicht erreichbar"):
        search_web("q", backend="searxng", instance_url="http://localhost:8080")


def test_unknown_backend_lists_the_keyless_ones() -> None:
    with pytest.raises(SearchError) as excinfo:
        search_web("q", backend="altavista")
    assert "ohne Key" in str(excinfo.value)
    assert "searxng" in str(excinfo.value)
