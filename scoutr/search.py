"""Websuche hinter einer einzigen Funktion.

Default ist DuckDuckGo (kein API-Key). Ein Wechsel auf Brave oder Tavily
aendert nur diese Datei -- `search_web()` bleibt fuer den Rest identisch.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import httpx

from scoutr.models import SearchResult, domain_of


def _region(country: str, lang: str) -> str:
    """`de` + `de` -> `de-de`, wie es DuckDuckGo als Region erwartet."""
    country = (country or "de").lower()
    lang = (lang or country).lower()
    return f"{country}-{lang}"


class SearchError(RuntimeError):
    """Die Suche konnte nicht ausgefuehrt werden."""


def _dedupe(results: list[SearchResult], count: int) -> list[SearchResult]:
    """Doppelte URLs entfernen und Raenge neu vergeben."""
    seen: set[str] = set()
    out: list[SearchResult] = []
    for result in results:
        url = result.url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        result.rank = len(out) + 1
        result.source_domain = result.source_domain or domain_of(url)
        out.append(result)
        if len(out) >= count:
            break
    return out


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
def _search_duckduckgo(
    query: str, count: int, country: str, lang: str, api_key: str
) -> list[SearchResult]:
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    try:
        raw = DDGS(timeout=20).text(
            query,
            region=_region(country, lang),
            safesearch="moderate",
            max_results=max(count, 5),
        )
    except DDGSException as exc:
        raise SearchError(f"DuckDuckGo-Suche fehlgeschlagen: {exc}") from exc

    return [
        SearchResult(
            title=(item.get("title") or "").strip(),
            url=(item.get("href") or item.get("url") or "").strip(),
            snippet=(item.get("body") or item.get("description") or "").strip(),
        )
        for item in raw
    ]


def _search_brave(
    query: str, count: int, country: str, lang: str, api_key: str
) -> list[SearchResult]:
    key = api_key or os.environ.get("BRAVE_API_KEY", "")
    if not key:
        raise SearchError("BRAVE_API_KEY fehlt -- setze ihn per `scoutr setup`.")
    response = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={
            "q": query,
            "count": min(max(count, 1), 20),
            "country": (country or "de").upper(),
            "search_lang": lang or "de",
        },
        headers={"Accept": "application/json", "X-Subscription-Token": key},
        timeout=20,
    )
    if response.status_code != 200:
        raise SearchError(f"Brave-Suche antwortete mit {response.status_code}")
    payload = response.json().get("web", {}).get("results", [])
    return [
        SearchResult(
            title=(item.get("title") or "").strip(),
            url=(item.get("url") or "").strip(),
            snippet=(item.get("description") or "").strip(),
        )
        for item in payload
    ]


def _search_tavily(
    query: str, count: int, country: str, lang: str, api_key: str
) -> list[SearchResult]:
    key = api_key or os.environ.get("TAVILY_API_KEY", "")
    if not key:
        raise SearchError("TAVILY_API_KEY fehlt -- setze ihn per `scoutr setup`.")
    response = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": min(max(count, 1), 20)},
        timeout=25,
    )
    if response.status_code != 200:
        raise SearchError(f"Tavily-Suche antwortete mit {response.status_code}")
    return [
        SearchResult(
            title=(item.get("title") or "").strip(),
            url=(item.get("url") or "").strip(),
            snippet=(item.get("content") or "").strip(),
        )
        for item in response.json().get("results", [])
    ]


Backend = Callable[[str, int, str, str, str], list[SearchResult]]

BACKENDS: dict[str, Backend] = {
    "duckduckgo": _search_duckduckgo,
    "ddg": _search_duckduckgo,
    "brave": _search_brave,
    "tavily": _search_tavily,
}


def search_web(
    query: str,
    count: int = 8,
    country: str = "de",
    lang: str = "de",
    *,
    backend: str = "duckduckgo",
    api_key: str = "",
) -> list[SearchResult]:
    """Schickt *query* an die konfigurierte Suchmaschine.

    Args:
        query: Die Suchanfrage.
        count: Gewuenschte Trefferzahl (1--20).
        country: ISO-Laendercode fuer den Ortsfilter der API.
        lang: ISO-Sprachcode.
        backend: Name aus :data:`BACKENDS`.
        api_key: Optionaler Key; sonst aus der Umgebung.

    Raises:
        SearchError: Wenn das Backend unbekannt ist oder die Suche scheitert.
    """
    query = query.strip()
    if not query:
        return []
    count = min(max(int(count or 8), 1), 20)
    runner = BACKENDS.get((backend or "duckduckgo").lower())
    if runner is None:
        raise SearchError(
            f"Unbekannte Suchmaschine '{backend}'. Verfuegbar: {', '.join(sorted(BACKENDS))}"
        )
    return _dedupe(runner(query, count, country, lang, api_key), count)
