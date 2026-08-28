"""Die zwei Werkzeuge des Agenten: `web_search` und `fetch_page`.

Mehr braucht es nicht. Instagram, Amazon, Branchenbuch oder Ladenwebsite --
alles sind einfach Suchtreffer, die gelesen werden koennen. Es gibt bewusst
keine plattformspezifischen Scraper.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from scoutr.cache import Cache, cache_key
from scoutr.config import Settings
from scoutr.extract import extract_product, has_spec_heading
from scoutr.fetch import Fetcher, load_rules
from scoutr.models import PageResult, Product, SearchResult, domain_of
from scoutr.search import OPEN_BACKEND_NAMES, SearchError, search_news, search_web

#: Callback fuer die Live-Anzeige: (event, payload)
EventHook = Callable[[str, dict[str, Any]], None]
#: LLM-Fallback fuer Specs: (seitentext, url) -> {"CPU": "...", ...}
SpecExtractor = Callable[[str, str], dict[str, str]]

#: Drittes Werkzeug, das nur der Hauptagent bekommt -- Subagenten duerfen
#: keine weiteren Subagenten starten.
SUBAGENT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "research_subtasks",
        "description": (
            "Gibt mehrere unabhaengige Teilfragen an Rechercheassistenten ab, die "
            "parallel suchen und lesen. Nutze das, wenn die Anfrage in Teile zerfaellt, "
            "die sich getrennt beantworten lassen -- etwa mehrere Kandidaten, mehrere "
            "Orte oder mehrere Kriterien. Jede Teilfrage muss fuer sich verstaendlich "
            "sein und den noetigen Zusammenhang selbst mitbringen (Ort, Produkt, "
            "Kriterium). Du bekommst je Teilfrage eine Zusammenfassung mit Quellen "
            "zurueck und fasst daraus die Antwort zusammen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Zwei bis vier eigenstaendige Teilfragen, jeweils ein ganzer Satz."
                    ),
                }
            },
            "required": ["tasks"],
        },
    },
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Schickt eine Suchanfrage ans Web und liefert Titel, URL und Snippet je Treffer. "
                "Formuliere mehrere unterschiedliche Anfragen statt nur einer. "
                "Baue Ort, Stadt oder Region direkt in die Anfrage ein, wenn der Nutzer einen "
                "Ort genannt hat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Die Suchanfrage."},
                    "count": {
                        "type": "integer",
                        "description": "Gewuenschte Trefferzahl (1-20, Default 8).",
                    },
                    "country": {
                        "type": "string",
                        "description": "ISO-Laendercode fuer den Ortsfilter, z.B. 'de'.",
                    },
                    "lang": {
                        "type": "string",
                        "description": "ISO-Sprachcode der Ergebnisse, z.B. 'de'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Laedt eine Seite und gibt den lesbaren Textinhalt zurueck (Navigation, Werbung "
                "und Cookie-Banner sind entfernt). Bei Produktseiten kommen zusaetzlich "
                "strukturierte Daten (Name, Bild-URL, Preis, Specs) zurueck. "
                "Seiten hinter Paywall, Login oder Captcha werden uebersprungen und mit einem "
                "Grund gemeldet -- dann nimm einfach eine andere Quelle."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Vollstaendige http(s)-URL."},
                },
                "required": ["url"],
            },
        },
    },
]



NEWS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_news",
        "description": (
            "Sucht in aktuellen Nachrichten -- fuer Ereignisse, Ankuendigungen und alles, "
            "wo das Datum zaehlt. Jeder Treffer traegt Datum und Quelle im Snippet. "
            "Fuer zeitlose Fakten nimm weiter web_search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Die Suchanfrage."},
                "count": {"type": "integer", "description": "Trefferzahl (1-20, Default 8)."},
            },
            "required": ["query"],
        },
    },
}

CALC_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": (
            "Rechnet einen arithmetischen Ausdruck exakt aus -- Preise pro Einheit, "
            "Durchschnitte, Rabatte, Umrechnungen. Erlaubt: Zahlen (auch 1.099,99), "
            "+ - * / // % ** und Klammern. Rechne NIE selbst im Kopf, benutze dieses "
            "Werkzeug."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Der Ausdruck, z.B. '(1099 + 1149) / 2'.",
                }
            },
            "required": ["expression"],
        },
    },
}

#: Nur fuer den Hauptagenten -- Subagenten sollen keine Notizen anlegen.
MEMORY_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": (
            "Schreibt eine kurze Notiz auf den dauerhaften Merkzettel des Nutzers -- er "
            "gilt ueber Sitzungen hinweg. Benutze es NUR, wenn der Nutzer ausdruecklich "
            "darum bittet (\"merk dir\", \"notier dir\"), nie von dir aus."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Die Notiz, ein bis zwei Saetze."}
            },
            "required": ["text"],
        },
    },
}


# News-Suche und Rechner gehoeren zum Kern -- auch Subagenten sollen aktuelle
# Meldungen finden und richtig rechnen koennen.
TOOL_SCHEMAS.extend([NEWS_SCHEMA, CALC_SCHEMA])


@dataclass
class ToolStats:
    """Was in einem Durchlauf passiert ist -- fuer Ausgabe und Verlauf."""

    searches: list[str] = field(default_factory=list)
    news_searches: list[str] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    products: list[Product] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    calculations: int = 0
    notes_saved: int = 0

    @property
    def tool_calls(self) -> int:
        return (
            len(self.searches)
            + len(self.news_searches)
            + len(self.fetched)
            + len(self.skipped)
            + self.calculations
            + self.notes_saved
        )

    def reset(self) -> None:
        self.searches.clear()
        self.news_searches.clear()
        self.fetched.clear()
        self.skipped.clear()
        self.products.clear()
        self.sources.clear()
        self.calculations = 0
        self.notes_saved = 0


class Toolbox:
    """Fuehrt die Tool-Calls des Agenten aus."""

    def __init__(
        self,
        settings: Settings,
        cache: Cache | None = None,
        on_event: EventHook | None = None,
        spec_extractor: SpecExtractor | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.on_event = on_event
        self.spec_extractor = spec_extractor
        self.rules = load_rules()
        self.stats = ToolStats()
        #: Setzt der Agent, wenn Subagenten erlaubt sind.
        self.subagent_runner: Callable[[list[str]], list[dict[str, Any]]] | None = None
        self._fetcher = fetcher or Fetcher(
            user_agent=settings.user_agent,
            timeout=settings.fetch_timeout,
            delay_seconds=settings.request_delay_seconds,
            rules=self.rules,
            enable_browser=settings.enable_playwright,
        )
        #: Suchtreffer nach URL, damit blockierte Seiten wenigstens als Link taugen.
        self.seen_results: dict[str, SearchResult] = {}

    def close(self) -> None:
        self._fetcher.close()

    def _emit(self, event: str, **payload: Any) -> None:
        if self.on_event:
            self.on_event(event, payload)

    # -- Werkzeug 1 -------------------------------------------------------
    def web_search(
        self,
        query: str,
        count: int = 0,
        country: str = "",
        lang: str = "",
    ) -> dict[str, Any]:
        """Sucht im Web und liefert Treffer als einfache Dicts."""
        query = (query or "").strip()
        count = int(count or self.settings.max_results_default)
        country = (country or self.settings.country or "de").lower()
        lang = (lang or self.settings.lang or "de").lower()

        self.stats.searches.append(query)
        self._emit("search", query=query, count=count)

        if not query:
            return {"query": query, "results": [], "error": "Leere Suchanfrage."}

        key = cache_key(
            "search",
            self.settings.search_backend,
            self.settings.search_engines,
            query,
            count,
            country,
            lang,
        )
        cached = self.cache.get(key) if self.cache else None
        if cached is not None:
            results = [SearchResult(**item) for item in cached]
        else:
            try:
                results = search_web(
                    query,
                    count=count,
                    country=country,
                    lang=lang,
                    backend=self.settings.search_backend,
                    engines=self.settings.search_engines,
                    instance_url=self.settings.searxng_url,
                )
            except SearchError as exc:
                # Fallback: faellt das konfigurierte Backend aus (SearXNG down,
                # API-Limit), uebernimmt die offene Metasuche -- die braucht
                # nichts und ist praktisch immer da.
                if self.settings.search_backend in OPEN_BACKEND_NAMES:
                    self._emit("error", message=str(exc))
                    return {"query": query, "results": [], "error": str(exc)}
                self._emit("fallback", source=self.settings.search_backend, target="metasuche")
                try:
                    results = search_web(query, count=count, country=country, lang=lang)
                except SearchError as second:
                    self._emit("error", message=str(second))
                    return {"query": query, "results": [], "error": str(second)}
            if self.cache:
                self.cache.set(
                    key,
                    [result.model_dump() for result in results],
                    kind="search",
                    label=query,
                )

        for result in results:
            self.seen_results.setdefault(result.url, result)

        self._emit("search_done", query=query, hits=len(results))
        return {
            "query": query,
            "country": country,
            "lang": lang,
            "results": [result.as_tool_dict() for result in results],
        }

    # -- Werkzeug 2 -------------------------------------------------------
    def fetch_page(self, url: str) -> dict[str, Any]:
        """Laedt eine Seite und gibt lesbaren Text plus Produktdaten zurueck."""
        url = (url or "").strip()
        self._emit("fetch", url=url)

        key = cache_key("page", url)
        cached = self.cache.get(key) if self.cache else None
        if cached is not None:
            page = PageResult(**cached)
            page.via = "cache"
        else:
            page = self._fetcher.fetch(url, want_products=True)
            self._maybe_llm_specs(page)
            # Voruebergehende Fehler nicht cachen: ein Timeout von jetzt sagt
            # nichts darueber, ob die Seite in einer Stunde erreichbar ist.
            # Stabile Ergebnisse (Inhalt, blocked, paywall) duerfen 24 h liegen.
            transient = page.skipped_reason in ("timeout", "network_error")
            if self.cache and not transient:
                self.cache.set(key, page.model_dump(), kind="page", label=page.title or url)

        if page.ok:
            self.stats.fetched.append(page.final_url or url)
            self.stats.sources.append(
                {
                    "url": page.final_url or url,
                    "title": page.title,
                    "domain": page.source_domain or domain_of(url),
                }
            )
            for product in page.products:
                self.stats.products.append(product)
            self._emit("fetch_done", url=url, title=page.title, words=page.word_count)
        else:
            self.stats.skipped[url] = page.skipped_reason
            self._emit("skip", url=url, reason=page.skipped_reason)

        payload = page.as_tool_dict()
        if not page.ok:
            hint = self.seen_results.get(url)
            if hint:
                # Blockierte Seiten bleiben als Kauf-/Referenzlink brauchbar.
                payload["search_title"] = hint.title
                payload["search_snippet"] = hint.snippet
        return payload

    def _maybe_llm_specs(self, page: PageResult) -> None:
        """LLM-Fallback (Quelle 5), wenn strukturierte Extraktion nichts hergab."""
        if not (page.ok and self.spec_extractor and page.text):
            return
        if not page.product_hint:
            # Kein Produkt in Sicht -- kein Grund, das LLM zu bemuehen.
            return
        if page.products and page.products[0].specs:
            return
        specs = self.spec_extractor(page.text, page.final_url or page.url)
        if not specs:
            return
        if page.products:
            for key, value in specs.items():
                page.products[0].specs.setdefault(key, value)
        else:
            page.products = [
                Product(
                    name=page.title or page.url,
                    url=page.final_url or page.url,
                    specs=specs,
                    source_domain=page.source_domain,
                )
            ]

    # -- Dispatch ---------------------------------------------------------
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Fuehrt den Tool-Call *name* mit *arguments* aus."""
        if name == "web_search":
            return self.web_search(
                query=str(arguments.get("query", "")),
                count=int(arguments.get("count") or 0),
                country=str(arguments.get("country") or ""),
                lang=str(arguments.get("lang") or ""),
            )
        if name == "fetch_page":
            return self.fetch_page(url=str(arguments.get("url", "")))
        if name == "search_news":
            return self.search_news(
                query=str(arguments.get("query", "")), count=int(arguments.get("count") or 0)
            )
        if name == "calculate":
            return self.calculate(expression=str(arguments.get("expression", "")))
        if name == "remember":
            return self.remember(text=str(arguments.get("text", "")))
        if name == "research_subtasks":
            return self.research_subtasks(arguments.get("tasks") or [])
        return {"error": f"Unbekanntes Werkzeug '{name}'."}

    # -- Werkzeug 3: News -------------------------------------------------
    def search_news(self, query: str, count: int = 0) -> dict[str, Any]:
        """News-Suche; faellt bei Ausfall auf die normale Websuche zurueck."""
        query = (query or "").strip()
        count = int(count or self.settings.max_results_default)
        self.stats.news_searches.append(query)
        self._emit("search", query=f"News: {query}", count=count)
        if not query:
            return {"query": query, "results": [], "error": "Leere Suchanfrage."}

        key = cache_key("news", query, count, self.settings.country, self.settings.lang)
        cached = self.cache.get(key) if self.cache else None
        if cached is not None:
            results = [SearchResult(**item) for item in cached]
        else:
            try:
                results = search_news(
                    query, count=count, country=self.settings.country, lang=self.settings.lang
                )
            except SearchError:
                # Fallback: lieber normale Treffer als gar keine.
                self._emit("fallback", source="news", target="websuche")
                try:
                    results = search_web(
                        query,
                        count=count,
                        country=self.settings.country,
                        lang=self.settings.lang,
                    )
                except SearchError as exc:
                    self._emit("error", message=str(exc))
                    return {"query": query, "results": [], "error": str(exc)}
            if self.cache:
                # News veralten schnell -- eine Stunde statt 24.
                self.cache.set(
                    key,
                    [result.model_dump() for result in results],
                    kind="news",
                    label=query,
                    ttl=3600,
                )

        for result in results:
            self.seen_results.setdefault(result.url, result)
        self._emit("search_done", query=query, hits=len(results))
        return {"query": query, "results": [result.as_tool_dict() for result in results]}

    # -- Werkzeug 4: Rechner ----------------------------------------------
    def calculate(self, expression: str) -> dict[str, Any]:
        """Exakte Arithmetik -- damit das Modell nie selbst rechnen muss."""
        from scoutr.calc import CalcError, calculate_pretty

        self.stats.calculations += 1
        self._emit("calculate", expression=expression)
        try:
            return {"expression": expression, "result": calculate_pretty(expression)}
        except CalcError as exc:
            return {"expression": expression, "error": str(exc)}

    # -- Werkzeug 5: Merkzettel (nur Hauptagent) --------------------------
    def remember(self, text: str) -> dict[str, Any]:
        """Notiz auf den dauerhaften Merkzettel des Nutzers schreiben."""
        text = (text or "").strip()
        if not text:
            return {"error": "Leere Notiz."}
        if self.cache is None:
            return {"error": "Kein Speicher verfuegbar -- Notiz nicht abgelegt."}
        note_id = self.cache.add_note(text)
        self.stats.notes_saved += 1
        self._emit("remember", text=text)
        return {"saved": True, "note_id": note_id, "text": text}

    # -- Werkzeug 6 (nur fuer den Hauptagenten) ---------------------------
    def research_subtasks(self, tasks: Any) -> dict[str, Any]:
        """Gibt Teilfragen an parallele Subagenten ab."""
        if self.subagent_runner is None:
            return {"error": "Subagenten sind in dieser Sitzung nicht aktiv."}
        if not isinstance(tasks, list):
            return {"error": "`tasks` muss eine Liste von Teilfragen sein."}
        clean = [str(task).strip() for task in tasks if str(task).strip()]
        if not clean:
            return {"error": "Keine Teilfragen angegeben."}
        results = self.subagent_runner(clean)
        return {"results": results}


def looks_like_product_page(html: str, url: str) -> bool:
    """Kleine Hilfe fuer Tests und den LLM-Fallback."""
    from selectolax.parser import HTMLParser

    if extract_product(html, url) is not None:
        return True
    return has_spec_heading(HTMLParser(html))
