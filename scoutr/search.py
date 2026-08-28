"""Websuche hinter einer einzigen Funktion.

Voreinstellung ist eine **schluessellose Metasuche** ueber offene Engines
(DuckDuckGo, Mojeek, Startpage, Brave-HTML, Yahoo, Wikipedia) -- nichts davon
braucht einen API-Key oder ein Konto.

Wer lieber eine eigene Instanz betreibt, nimmt `searxng`; wer kommerzielle
APIs mag, `brave` oder `tavily`. Ein Wechsel aendert nur diese Datei --
`search_web()` bleibt fuer den Rest identisch.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from scoutr.models import SearchResult, domain_of

#: Offene Engines, die `ddgs` ohne Key abfragt.
OPEN_ENGINES = ("duckduckgo", "mojeek", "startpage", "brave", "yahoo", "wikipedia")

#: Backends, die ohne Key auskommen.
KEYLESS_BACKENDS = ("duckduckgo", "ddg", "open", "searxng")

#: Die Namen der offenen Metasuche selbst -- von hier gibt es kein weiteres
#: Fallback, sie IST das Fallback.
OPEN_BACKEND_NAMES = frozenset({"duckduckgo", "ddg", "open"})


def _region(country: str, lang: str) -> str:
    """`de` + `de` -> `de-de`, wie es die Engines als Region erwarten."""
    country = (country or "de").lower()
    lang = (lang or country).lower()
    return f"{country}-{lang}"


class SearchError(RuntimeError):
    """Die Suche konnte nicht ausgefuehrt werden."""


@dataclass(slots=True)
class SearchOptions:
    """Alles, was ein Backend ausser der Anfrage noch braucht."""

    count: int = 8
    country: str = "de"
    lang: str = "de"
    api_key: str = ""
    #: Komma-Liste fuer die offene Metasuche, z.B. "duckduckgo,mojeek".
    engines: str = ""
    #: Basis-URL der eigenen SearXNG-Instanz.
    instance_url: str = ""
    #: Wohin Fehler einzelner Engines gemeldet werden (fuer die Anzeige).
    notes: list[str] = field(default_factory=list)


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
# Backends ohne Schluessel
# ---------------------------------------------------------------------------
def _search_open(query: str, options: SearchOptions) -> list[SearchResult]:
    """Metasuche ueber offene Engines -- kein API-Key, kein Konto.

    `ddgs` fragt mehrere Suchmaschinen per HTML ab und mischt die Treffer.
    Faellt eine Engine aus (Rate-Limit, Umbau), uebernehmen die anderen.
    """
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    engines = [name.strip() for name in (options.engines or "").split(",") if name.strip()]
    unknown = [name for name in engines if name not in OPEN_ENGINES]
    if unknown:
        raise SearchError(
            f"Unbekannte Engine(s): {', '.join(unknown)}. "
            f"Verfuegbar ohne Key: {', '.join(OPEN_ENGINES)}"
        )
    backend = ",".join(engines) if engines else "auto"

    try:
        raw = DDGS(timeout=20).text(
            query,
            region=_region(options.country, options.lang),
            safesearch="moderate",
            max_results=max(options.count, 5),
            backend=backend,
        )
    except DDGSException as exc:
        raise SearchError(
            f"Suche fehlgeschlagen: {exc}\n"
            f"Alle offenen Engines waren nicht erreichbar. Einzelne lassen sich mit "
            f"SCOUTR_SEARCH_ENGINES gezielt waehlen (verfuegbar: {', '.join(OPEN_ENGINES)}), "
            f"oder du nimmst mit SCOUTR_SEARCH_BACKEND=searxng eine eigene Instanz."
        ) from exc

    return [
        SearchResult(
            title=(item.get("title") or "").strip(),
            url=(item.get("href") or item.get("url") or "").strip(),
            snippet=(item.get("body") or item.get("description") or "").strip(),
        )
        for item in raw
    ]


def _search_searxng(query: str, options: SearchOptions) -> list[SearchResult]:
    """SearXNG -- freie Metasuchmaschine, selbst hostbar, ohne Key.

    Die Instanz muss die JSON-Ausgabe erlauben (in `settings.yml` unter
    `search.formats` den Eintrag `json` ergaenzen). Bei eigenen Instanzen ist
    das eine Zeile; viele oeffentliche Instanzen haben JSON abgeschaltet.
    """
    base = (options.instance_url or os.environ.get("SCOUTR_SEARXNG_URL", "")).strip()
    if not base:
        raise SearchError(
            "SCOUTR_SEARXNG_URL fehlt. Trage die Adresse deiner SearXNG-Instanz ein, "
            "z.B. http://localhost:8080 -- eine eigene laeuft mit "
            "`docker run -p 8080:8080 searxng/searxng`."
        )

    try:
        response = httpx.get(
            f"{base.rstrip('/')}/search",
            params={
                "q": query,
                "format": "json",
                "language": (options.lang or "de").lower(),
                "categories": "general",
                "safesearch": 1,
            },
            headers={"Accept": "application/json"},
            timeout=25,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise SearchError(f"SearXNG unter {base} nicht erreichbar: {exc}") from exc

    if response.status_code == 403:
        raise SearchError(
            f"SearXNG unter {base} lehnt die Anfrage ab (403). Meist ist die JSON-Ausgabe "
            "abgeschaltet -- ergaenze in der settings.yml unter `search.formats` den "
            "Eintrag `json`."
        )
    if response.status_code != 200:
        raise SearchError(f"SearXNG antwortete mit {response.status_code}")

    try:
        payload = response.json().get("results", [])
    except ValueError as exc:
        raise SearchError(
            f"SearXNG unter {base} lieferte kein JSON. Aktiviere `json` in "
            "`search.formats` der settings.yml."
        ) from exc

    return [
        SearchResult(
            title=(item.get("title") or "").strip(),
            url=(item.get("url") or "").strip(),
            snippet=(item.get("content") or "").strip(),
        )
        for item in payload
    ]


# ---------------------------------------------------------------------------
# Backends mit Schluessel
# ---------------------------------------------------------------------------
def _search_brave(query: str, options: SearchOptions) -> list[SearchResult]:
    key = options.api_key or os.environ.get("BRAVE_API_KEY", "")
    if not key:
        raise SearchError("BRAVE_API_KEY fehlt -- setze ihn per `scoutr setup`.")
    response = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={
            "q": query,
            "count": min(max(options.count, 1), 20),
            "country": (options.country or "de").upper(),
            "search_lang": options.lang or "de",
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


def _search_tavily(query: str, options: SearchOptions) -> list[SearchResult]:
    key = options.api_key or os.environ.get("TAVILY_API_KEY", "")
    if not key:
        raise SearchError("TAVILY_API_KEY fehlt -- setze ihn per `scoutr setup`.")
    response = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": min(max(options.count, 1), 20)},
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


Backend = Callable[[str, SearchOptions], list[SearchResult]]

BACKENDS: dict[str, Backend] = {
    "duckduckgo": _search_open,  # historischer Name der offenen Metasuche
    "ddg": _search_open,
    "open": _search_open,
    "searxng": _search_searxng,
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
    engines: str = "",
    instance_url: str = "",
) -> list[SearchResult]:
    """Schickt *query* an die konfigurierte Suchmaschine.

    Args:
        query: Die Suchanfrage.
        count: Gewuenschte Trefferzahl (1--20).
        country: ISO-Laendercode fuer den Ortsfilter der API.
        lang: ISO-Sprachcode.
        backend: Name aus :data:`BACKENDS`. Ohne Key laufen `open`
            (Metasuche, Default) und `searxng` (eigene Instanz).
        api_key: Optionaler Key; sonst aus der Umgebung.
        engines: Komma-Liste offener Engines, z.B. `"duckduckgo,mojeek"`.
        instance_url: Basis-URL der SearXNG-Instanz.

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
            f"Unbekannte Suchmaschine '{backend}'. Verfuegbar: {', '.join(sorted(BACKENDS))} "
            f"(ohne Key: {', '.join(KEYLESS_BACKENDS)})"
        )
    options = SearchOptions(
        count=count,
        country=country,
        lang=lang,
        api_key=api_key,
        engines=engines,
        instance_url=instance_url,
    )
    return _dedupe(runner(query, options), count)


def search_news(
    query: str,
    count: int = 8,
    country: str = "de",
    lang: str = "de",
) -> list[SearchResult]:
    """Sucht in Nachrichten -- immer ueber die offene Metasuche, ohne Key.

    Fuer "was ist diese Woche passiert" liefert die normale Websuche zu viel
    Altes; die News-Vertikale sortiert nach Aktualitaet und traegt ein Datum.

    Raises:
        SearchError: Wenn keine Engine erreichbar ist.
    """
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    query = query.strip()
    if not query:
        return []
    count = min(max(int(count or 8), 1), 20)
    try:
        raw = DDGS(timeout=20).news(
            query,
            region=_region(country, lang),
            safesearch="moderate",
            max_results=max(count, 5),
        )
    except DDGSException as exc:
        raise SearchError(f"News-Suche fehlgeschlagen: {exc}") from exc

    results = []
    for item in raw:
        snippet = (item.get("body") or item.get("excerpt") or "").strip()
        date = (item.get("date") or "").strip()
        source = (item.get("source") or "").strip()
        prefix = " · ".join(part for part in (date[:10], source) if part)
        results.append(
            SearchResult(
                title=(item.get("title") or "").strip(),
                url=(item.get("url") or item.get("href") or "").strip(),
                snippet=f"[{prefix}] {snippet}" if prefix else snippet,
            )
        )
    return _dedupe(results, count)

