"""Tests fuer die beiden Agenten-Werkzeuge inklusive Cache-Verhalten."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from cortex.cache import Cache
from cortex.config import Settings
from cortex.fetch import Fetcher, RobotsPolicy
from cortex.models import SearchResult
from cortex.tools import TOOL_SCHEMAS, Toolbox


def _mock_fetcher(handler) -> Fetcher:
    fetcher = Fetcher(
        user_agent="cortex-test/0.1", timeout=5, delay_seconds=0, enable_browser=False
    )
    fetcher._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    fetcher.robots = RobotsPolicy(fetcher._client, "cortex-test/0.1")
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

    monkeypatch.setattr("cortex.tools.search_web", fake_search)
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

    monkeypatch.setattr("cortex.tools.search_web", fake_search)
    cache = Cache(tmp_path / "c.sqlite3")
    box = Toolbox(settings, cache=cache, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    box.web_search("gleiche frage")
    box.web_search("gleiche frage")
    assert len(calls) == 1


def test_search_error_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from cortex.search import SearchError

    def failing(*args, **kwargs):
        raise SearchError("keine Verbindung")

    monkeypatch.setattr("cortex.tools.search_web", failing)
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
        "cortex.tools.search_web",
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
    from cortex.cache import Cache

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
    from cortex.cache import Cache

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
        "cortex.tools.search_news",
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
    from cortex.search import SearchError

    def failing_news(query, **kwargs):
        raise SearchError("News tot")

    monkeypatch.setattr("cortex.tools.search_news", failing_news)
    monkeypatch.setattr(
        "cortex.tools.search_web",
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
    from cortex.search import SearchError

    settings.search_backend = "searxng"
    calls: list[str] = []

    def routing(query, count, country, lang, backend="duckduckgo", **kwargs):
        calls.append(backend)
        if backend == "searxng":
            raise SearchError("Instanz nicht erreichbar")
        return [SearchResult(title="Metasuche-Treffer", url="https://b.de/")]

    monkeypatch.setattr("cortex.tools.search_web", routing)
    box = Toolbox(settings, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    payload = box.web_search("frage")
    assert payload["results"][0]["title"] == "Metasuche-Treffer"
    assert calls == ["searxng", "duckduckgo"]


def test_open_metasearch_has_no_further_fallback(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from cortex.search import SearchError

    settings.search_backend = "duckduckgo"
    calls: list[str] = []

    def failing(query, count, country, lang, backend="duckduckgo", **kwargs):
        calls.append(backend)
        raise SearchError("alles tot")

    monkeypatch.setattr("cortex.tools.search_web", failing)
    box = Toolbox(settings, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    assert "error" in box.web_search("frage")
    assert calls == ["duckduckgo"]


def test_calculate_tool(settings: Settings) -> None:
    box = Toolbox(settings, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    assert box.call("calculate", {"expression": "(1099 + 1149) / 2"})["result"] == "1124"
    assert "error" in box.call("calculate", {"expression": "__import__('os')"})
    assert box.stats.calculations == 2


def test_remember_tool_persists_notes(settings: Settings, tmp_path: Path) -> None:
    from cortex.cache import Cache

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


# -- Heimnetz -------------------------------------------------------------
def test_lan_scan_can_be_switched_off(settings: Settings) -> None:
    settings.lan_enabled = False
    assert "abgeschaltet" in Toolbox(settings).lan_scan()["error"]
    assert "abgeschaltet" in Toolbox(settings).lan_check("192.168.1.5")["error"]


def test_lan_scan_refuses_public_networks(settings: Settings) -> None:
    """Fremde Netze durchsucht cortex nicht."""
    result = Toolbox(settings).lan_scan("8.8.8.0/24")
    assert "kein privates Netz" in result["error"]


def test_lan_scan_without_a_known_network_says_what_to_do(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cortex.lan.own_subnet", lambda: "")
    result = Toolbox(settings).lan_scan()
    assert "CORTEX_LAN_SUBNET" in result["error"]


def test_lan_scan_reports_devices(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex.lan import Device

    monkeypatch.setattr(
        "cortex.lan.scan",
        lambda subnet, quick=True: [
            Device(address="192.168.1.5", name="ha.local", ports=[8123],
                   services=["Home Assistant"], title="Home Assistant")
        ],
    )
    box = Toolbox(settings)
    result = box.lan_scan("192.168.1.0/24")
    assert result["count"] == 1
    assert result["devices"][0]["title"] == "Home Assistant"
    assert "kein Beweis" in result["note"]  # ehrlich ueber die Grenzen
    assert box.stats.lan_scans == 1


def test_lan_check_refuses_addresses_outside_the_home(settings: Settings) -> None:
    result = Toolbox(settings).lan_check("8.8.8.8")
    assert result["reachable"] is False
    assert "ausserhalb" in result["error"]


def test_lan_check_reports_an_unresolvable_name(settings: Settings) -> None:
    result = Toolbox(settings).lan_check("gibt-es-nicht.invalid")
    assert result["reachable"] is False
    assert "aufloesbar" in result["error"]


def test_lan_check_says_silent_not_absent(settings: Settings,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein stilles Geraet kann schlafen -- das ist kein Beweis fuer 'weg'."""
    monkeypatch.setattr("socket.gethostbyname", lambda host: "192.168.1.99")
    monkeypatch.setattr("cortex.lan.check_host", lambda address, ports, **kw: None)
    result = Toolbox(settings).lan_check("192.168.1.99")
    assert result["reachable"] is False
    assert "Ruhezustand" in result["note"]


# -- Home Assistant -------------------------------------------------------
def _ha_settings(settings: Settings, control: bool = False) -> Settings:
    settings.ha_url = "http://192.168.1.5:8123"
    settings.ha_token = "geheim"
    settings.ha_control = control
    return settings


def test_ha_without_setup_points_at_the_command(settings: Settings) -> None:
    result = Toolbox(settings).ha_states()
    assert "connect-ha" in result["error"]


def test_ha_states_gives_an_overview_first(settings: Settings,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """Bei tausend Entitaeten ist die Uebersicht der einzige brauchbare Einstieg."""
    monkeypatch.setattr(
        "cortex.homeassistant.HomeAssistant.domains", lambda self: {"light": 12, "sensor": 40}
    )
    result = Toolbox(_ha_settings(settings)).ha_states()
    assert result["overview"] == {"light": 12, "sensor": 40}
    assert "domain" in result["note"]


def test_ha_states_finds_entities(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex.homeassistant import Entity

    monkeypatch.setattr(
        "cortex.homeassistant.HomeAssistant.find",
        lambda self, search="", domain="", limit=60: [
            Entity(entity_id="light.kueche", state="on", name="Kueche")
        ],
    )
    box = Toolbox(_ha_settings(settings))
    result = box.ha_states(search="kueche")
    assert result["entities"] == [{"entity_id": "light.kueche", "name": "Kueche", "state": "on"}]
    assert box.stats.ha_reads == 1


def test_ha_errors_reach_the_model_as_text(settings: Settings,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex.homeassistant import HomeAssistantError

    def boom(self, search="", domain="", limit=60):
        raise HomeAssistantError("Token abgelehnt")

    monkeypatch.setattr("cortex.homeassistant.HomeAssistant.find", boom)
    assert Toolbox(_ha_settings(settings)).ha_states(search="x")["error"] == "Token abgelehnt"


def test_switching_is_off_unless_allowed(settings: Settings) -> None:
    """Standardmaessig sieht cortex nur nach."""
    result = Toolbox(_ha_settings(settings)).ha_call("light", "turn_on", "light.kueche")
    assert "nur nachsehen" in result["error"]
    assert "CORTEX_HA_CONTROL" in result["error"]


def test_unknown_domains_are_refused(settings: Settings) -> None:
    result = Toolbox(_ha_settings(settings, control=True)).ha_call("shell_command", "rm", "")
    assert "schaltet im Bereich" in result["error"]


def test_a_lock_needs_a_confirmation(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein missverstandener Satz darf nicht die Haustuer aufschliessen."""
    called: list[tuple] = []
    monkeypatch.setattr(
        "cortex.homeassistant.HomeAssistant.call",
        lambda self, d, s, e="", data=None: called.append((d, s, e)) or [],
    )
    box = Toolbox(_ha_settings(settings, control=True))
    box.ask_handler = lambda question, options: "nein"
    result = box.ha_call("lock", "unlock", "lock.haustuer")
    assert result["done"] is False
    assert not called, "trotz Ablehnung geschaltet"


def test_a_confirmed_lock_is_actually_opened(settings: Settings,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple] = []
    monkeypatch.setattr(
        "cortex.homeassistant.HomeAssistant.call",
        lambda self, d, s, e="", data=None: called.append((d, s, e)) or [{"x": 1}],
    )
    box = Toolbox(_ha_settings(settings, control=True))
    box.ask_handler = lambda question, options: "ja"
    result = box.ha_call("lock", "unlock", "lock.haustuer")
    assert result["done"] is True
    assert called == [("lock", "unlock", "lock.haustuer")]


def test_without_anyone_to_confirm_the_lock_stays_shut(settings: Settings,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    called: list = []
    monkeypatch.setattr(
        "cortex.homeassistant.HomeAssistant.call",
        lambda self, d, s, e="", data=None: called.append(1) or [],
    )
    box = Toolbox(_ha_settings(settings, control=True))  # kein ask_handler
    result = box.ha_call("alarm_control_panel", "alarm_disarm", "alarm.haus")
    assert "bestaetigungspflichtig" in result["error"]
    assert not called


def test_a_light_needs_no_confirmation(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Licht anmachen ist umkehrbar -- danach zu fragen waere nur laestig."""
    asked: list = []
    monkeypatch.setattr(
        "cortex.homeassistant.HomeAssistant.call", lambda self, d, s, e="", data=None: [{"x": 1}]
    )
    box = Toolbox(_ha_settings(settings, control=True))
    box.ask_handler = lambda question, options: asked.append(question) or "ja"
    result = box.ha_call("light", "turn_on", "light.kueche")
    assert result["done"] is True
    assert not asked
    assert box.stats.ha_calls == 1


def test_ha_call_needs_domain_and_service(settings: Settings) -> None:
    box = Toolbox(_ha_settings(settings, control=True))
    assert "muessen angegeben sein" in box.ha_call("", "turn_on")["error"]


def test_the_home_tools_are_reachable_through_call(settings: Settings,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cortex.lan.scan", lambda subnet, quick=True: [])
    box = Toolbox(settings)
    assert box.call("lan_scan", {"subnet": "192.168.1.0/24"})["count"] == 0
    assert "connect-ha" in box.call("ha_states", {})["error"]


# ---------------------------------------------------------------------------
# Auffaecherung der Suche
# ---------------------------------------------------------------------------
def test_one_call_searches_several_phrasings(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Ein Werkzeugaufruf, mehrere Formulierungen -- das Budget bleibt gleich."""
    asked: list[str] = []

    def fake_search(query, **kwargs):
        asked.append(query)
        return [SearchResult(title="T", url=f"https://{len(asked)}.de/", snippet="S")]

    monkeypatch.setattr("cortex.tools.search_web", fake_search)
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    result = box.web_search("Wie viel kostet ein gebrauchtes Lastenrad in Bremen?")

    assert len(asked) == 2, asked
    assert asked[0] == "Wie viel kostet ein gebrauchtes Lastenrad in Bremen?"
    assert asked[1] == "kostet gebrauchtes Lastenrad Bremen"
    assert len(result["queries"]) == 2


def test_the_model_may_send_its_own_phrasings(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    asked: list[str] = []

    def fake_search(query, **kwargs):
        asked.append(query)
        return [SearchResult(title="T", url=f"https://{len(asked)}.de/", snippet="S")]

    monkeypatch.setattr("cortex.tools.search_web", fake_search)
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    box.call(
        "web_search",
        {"query": "Lastenrad Preis", "queries": ["Cargobike gebraucht Bremen"]},
    )
    assert sorted(asked) == ["Cargobike gebraucht Bremen", "Lastenrad Preis"]


def test_duplicate_phrasings_are_searched_once(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    asked: list[str] = []

    def fake_search(query, **kwargs):
        asked.append(query)
        return [SearchResult(title="T", url="https://a.de/", snippet="S")]

    monkeypatch.setattr("cortex.tools.search_web", fake_search)
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    box.call("web_search", {"query": "Lastenrad Bremen", "queries": ["lastenrad bremen"]})
    assert asked == ["Lastenrad Bremen"]


def test_a_query_list_sent_as_a_string_still_works(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Kleinere Modelle schicken statt einer Liste gern einen einzelnen String."""
    asked: list[str] = []

    def fake_search(query, **kwargs):
        asked.append(query)
        return [SearchResult(title="T", url=f"https://{len(asked)}.de/", snippet="S")]

    monkeypatch.setattr("cortex.tools.search_web", fake_search)
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    box.call("web_search", {"query": "Lastenrad", "queries": "Cargobike"})
    assert asked == ["Lastenrad", "Cargobike"]


def test_the_fan_out_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    asked: list[str] = []

    def fake_search(query, **kwargs):
        asked.append(query)
        return [SearchResult(title="T", url="https://a.de/", snippet="S")]

    monkeypatch.setattr("cortex.tools.search_web", fake_search)
    settings.search_variants = 1
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    box.web_search("Wie viel kostet ein Lastenrad in Bremen?")
    assert len(asked) == 1


def test_a_hit_found_by_two_phrasings_moves_up(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def fake_search(query, **kwargs):
        if query.startswith("Wie"):
            return [
                SearchResult(title="A", url="https://a.de/", snippet="S"),
                SearchResult(title="B", url="https://b.de/", snippet="S"),
            ]
        return [
            SearchResult(title="C", url="https://c.de/", snippet="S"),
            SearchResult(title="B", url="https://b.de/", snippet="S"),
        ]

    monkeypatch.setattr("cortex.tools.search_web", fake_search)
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    result = box.web_search("Wie viel kostet ein gebrauchtes Lastenrad in Bremen?")
    assert result["results"][0]["url"] == "https://b.de/"


def test_the_fan_out_survives_a_dead_phrasing(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Eine gescheiterte Formulierung darf die Suche nicht mitreissen."""
    from cortex.search import SearchError

    def fake_search(query, **kwargs):
        if not query.startswith("Wie"):
            raise SearchError("Engine weg")
        return [SearchResult(title="A", url="https://a.de/", snippet="S")]

    monkeypatch.setattr("cortex.tools.search_web", fake_search)
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    result = box.web_search("Wie viel kostet ein gebrauchtes Lastenrad in Bremen?")
    assert result["results"][0]["url"] == "https://a.de/"
    assert result["queries"] == ["Wie viel kostet ein gebrauchtes Lastenrad in Bremen?"]


# ---------------------------------------------------------------------------
# Gmail und Kalender
# ---------------------------------------------------------------------------
class FakeGoogle:
    """Steht fuer das verbundene Konto -- ohne Netz."""

    def __init__(self, connected: bool = True) -> None:
        self._connected = connected
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def connected(self) -> bool:
        return self._connected

    def events(self, days=7, query="", count=10):
        self.calls.append(("events", {"days": days, "query": query, "count": count}))
        return [{"summary": "Zahnarzt", "start": "2026-09-08T09:30:00+02:00"}]

    def search_mail(self, query="", count=8):
        self.calls.append(("search_mail", {"query": query, "count": count}))
        return [{"id": "m1", "subject": "Ihre Sendung", "from": "DHL"}]

    def read_mail(self, message_id):
        self.calls.append(("read_mail", {"message_id": message_id}))
        return {"id": message_id, "subject": "Ihre Sendung", "text": "Unterwegs"}

    def close(self) -> None:
        pass


def _google_box(settings: Settings, fake: FakeGoogle | None = None) -> Toolbox:
    settings.google_enabled = True
    settings.google_client_id = "id.apps.googleusercontent.com"
    settings.google_client_secret = "secret"
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    box._google_client = fake if fake is not None else FakeGoogle()
    return box


def test_calendar_and_mail_are_off_until_switched_on(settings: Settings) -> None:
    """Privates ist aus, bis es jemand einschaltet."""
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    for result in (box.calendar_events(), box.mail_search(), box.mail_read("m1")):
        assert "abgeschaltet" in result["error"]


def test_without_a_connected_account_the_hint_says_how(settings: Settings) -> None:
    box = _google_box(settings, FakeGoogle(connected=False))
    assert "kein Google-Konto verbunden" in box.calendar_events()["error"]


def test_missing_credentials_point_at_the_readme(settings: Settings) -> None:
    settings.google_enabled = True
    settings.google_client_id = ""
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    assert "GOOGLE_CLIENT_ID" in box.calendar_events()["error"]


def test_events_come_through_the_tool(settings: Settings) -> None:
    fake = FakeGoogle()
    box = _google_box(settings, fake)
    result = box.calendar_events(days=3, query="Zahnarzt")
    assert result["events"][0]["summary"] == "Zahnarzt"
    assert fake.calls[0] == ("events", {"days": 3, "query": "Zahnarzt", "count": 10})
    assert box.stats.google_reads == 1


def test_mail_search_and_read_come_through_the_tool(settings: Settings) -> None:
    fake = FakeGoogle()
    box = _google_box(settings, fake)
    listing = box.mail_search(query="from:dhl", count=3)
    assert listing["mails"][0]["subject"] == "Ihre Sendung"
    body = box.mail_read("m1")
    assert body["text"] == "Unterwegs"
    assert [name for name, _ in fake.calls] == ["search_mail", "read_mail"]


def test_a_google_failure_is_reported_not_raised(settings: Settings) -> None:
    from cortex.google import GoogleError

    class Broken(FakeGoogle):
        def events(self, days=7, query="", count=10):
            raise GoogleError("Google verweigert den Zugriff")

    box = _google_box(settings, Broken())
    assert "verweigert" in box.calendar_events()["error"]


def test_the_google_tools_are_dispatched(settings: Settings) -> None:
    fake = FakeGoogle()
    box = _google_box(settings, fake)
    box.call("calendar_events", {"days": 2})
    box.call("mail_search", {"query": "is:unread"})
    box.call("mail_read", {"message_id": "m9"})
    assert [name for name, _ in fake.calls] == ["events", "search_mail", "read_mail"]


def test_reading_mail_counts_against_the_budget(settings: Settings) -> None:
    box = _google_box(settings)
    before = box.stats.tool_calls
    box.mail_search()
    assert box.stats.tool_calls == before + 1


# ---------------------------------------------------------------------------
# Lagerverwaltung
# ---------------------------------------------------------------------------
class FakeStorage:
    """Steht fuer die Lagerverwaltung -- ohne Netz."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def search(self, query, limit=20):
        self.calls.append(("search", {"query": query, "limit": limit}))
        return [{"id": 2, "number": "A2", "name": "Schrauben", "roomName": "Keller"}]

    def rooms(self):
        self.calls.append(("rooms", {}))
        return [{"id": 1, "name": "Keller", "itemCount": 2}]

    def furniture(self, room_id):
        self.calls.append(("furniture", {"room_id": room_id}))
        return [{"id": 1, "name": "Regal"}]

    def items(self, furniture_id):
        self.calls.append(("items", {"furniture_id": furniture_id}))
        return [{"id": 2, "number": "A2", "name": "Schrauben"}]

    def add_item(self, furniture_id, name, quantity=1):
        self.calls.append(("add_item", {"furniture_id": furniture_id, "name": name}))
        return {"id": 9, "number": "A3", "name": name, "quantity": quantity}

    def add_room(self, name):
        self.calls.append(("add_room", {"name": name}))
        return {"id": 4, "name": name}

    def add_furniture(self, room_id, name):
        self.calls.append(("add_furniture", {"room_id": room_id, "name": name}))
        return {"id": 7, "name": name}

    def update_item(self, item_id, name="", quantity=None):
        self.calls.append(("update_item", {"item_id": item_id, "name": name}))
        return {"id": item_id, "name": name or "unverändert"}

    def change_quantity(self, item_id, delta):
        self.calls.append(("change_quantity", {"item_id": item_id, "delta": delta}))
        return {"id": item_id, "quantity": 238}

    def close(self) -> None:
        pass


def _storage_box(settings: Settings, access: str = "write") -> tuple[Toolbox, FakeStorage]:
    settings.storage_url = "http://192.168.1.5:3000"
    settings.storage_access = access
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    fake = FakeStorage()
    box._storage_client = fake
    return box, fake


def test_the_storage_is_off_until_an_address_is_entered(settings: Settings) -> None:
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    assert "keine Lagerverwaltung eingetragen" in box.storage_find("schraube")["error"]


def test_switching_the_storage_off_blocks_even_reading(settings: Settings) -> None:
    box, _ = _storage_box(settings, access="off")
    assert "abgeschaltet" in box.storage_find("schraube")["error"]
    assert "abgeschaltet" in box.storage_browse()["error"]


def test_finding_an_item_returns_its_place(settings: Settings) -> None:
    box, fake = _storage_box(settings, access="read")
    result = box.storage_find("schraube", limit=5)
    assert result["items"][0]["number"] == "A2"
    assert fake.calls == [("search", {"query": "schraube", "limit": 5})]
    assert box.stats.storage_reads == 1


def test_an_empty_result_says_not_recorded_not_not_owned(settings: Settings) -> None:
    """"Hast du nicht" waere eine Behauptung, die das Lager gar nicht hergibt."""
    box, _ = _storage_box(settings, access="read")
    empty = FakeStorage()
    empty.search = lambda query, limit=20: []
    box._storage_client = empty
    assert "nicht eingetragen" in box.storage_find("gibtesnicht")["note"]


def test_browsing_walks_the_three_levels(settings: Settings) -> None:
    box, fake = _storage_box(settings, access="read")
    assert box.storage_browse()["level"] == "rooms"
    assert box.storage_browse(room_id=1)["level"] == "furniture"
    assert box.storage_browse(furniture_id=1)["level"] == "items"
    assert [name for name, _ in fake.calls] == ["rooms", "furniture", "items"]


def test_adding_picks_the_level_from_the_given_id(settings: Settings) -> None:
    box, fake = _storage_box(settings)
    assert box.storage_add("Schraube", furniture_id=3)["created"] == "Artikel"
    assert box.storage_add("Regal", room_id=1)["created"] == "Moebel"
    assert box.storage_add("Dachboden")["created"] == "Raum"
    assert [name for name, _ in fake.calls] == ["add_item", "add_furniture", "add_room"]


def test_editing_prefers_delta_over_setting(settings: Settings) -> None:
    """Gesetzt wuerde ueberschreiben, was jemand anderes gerade geaendert hat."""
    box, fake = _storage_box(settings)
    box.storage_edit(item_id=5, delta=-12, quantity=999)
    assert fake.calls == [("change_quantity", {"item_id": 5, "delta": -12})]


def test_writes_count_against_the_budget(settings: Settings) -> None:
    box, _ = _storage_box(settings)
    before = box.stats.tool_calls
    box.storage_add("Dachboden")
    assert box.stats.tool_calls == before + 1


def test_a_storage_failure_is_reported_not_raised(settings: Settings) -> None:
    from cortex.storage import StorageError

    box, _ = _storage_box(settings)
    broken = FakeStorage()
    broken.search = lambda query, limit=20: (_ for _ in ()).throw(StorageError("Server weg"))
    box._storage_client = broken
    assert "Server weg" in box.storage_find("x")["error"]


def test_the_storage_tools_are_dispatched(settings: Settings) -> None:
    box, fake = _storage_box(settings)
    box.call("storage_find", {"query": "schraube"})
    box.call("storage_browse", {"room_id": 1})
    box.call("storage_add", {"name": "Kiste", "room_id": 1})
    box.call("storage_edit", {"item_id": 2, "delta": 1})
    assert [name for name, _ in fake.calls] == [
        "search",
        "furniture",
        "add_furniture",
        "change_quantity",
    ]


# ---------------------------------------------------------------------------
# Einstellungen aus dem Gespraech
# ---------------------------------------------------------------------------
def test_the_appearance_is_changed_in_the_browser_not_in_a_file(
    settings: Settings, tmp_path: Path
) -> None:
    """Aussehen steht in keiner .env -- die Oberflaeche hoert auf das Ereignis."""
    events: list[tuple[str, dict[str, Any]]] = []
    box = Toolbox(
        settings,
        cache=None,
        on_event=lambda name, payload: events.append((name, payload)),
        fetcher=_mock_fetcher(_html_handler("<html></html>")),
    )
    result = box.change_setting("hintergrund", "weiß")
    assert result["value"] == "hell"
    assert ("appearance", {"theme": "light"}) in events
    assert "path" not in result, "es wurde nichts geschrieben"


def test_a_setting_is_written_and_the_agent_rebuilt(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr("cortex.config.find_env_file", lambda: target)
    rebuilt: list[bool] = []

    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    box.on_settings_changed = lambda: rebuilt.append(True)
    result = box.change_setting("ort", "Hamburg")

    assert result["changed"] == "Standard-Ort"
    assert "CORTEX_LOCATION=Hamburg" in target.read_text(encoding="utf-8")
    assert rebuilt == [True], "die naechste Frage laeuft mit den neuen Werten"


def test_changing_rights_is_refused_with_a_reason(settings: Settings) -> None:
    """Ein Satz im Chat darf die Rechteauswahl nicht aushebeln."""
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    for name in ("storage_access", "ha_control", "google", "lan_enabled", "memory"):
        error = box.change_setting(name, "an")["error"]
        assert "Einstellungen" in error, name
        assert "aendere ich nicht" in error, name


def test_a_key_is_never_entered_from_the_chat(settings: Settings) -> None:
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    assert "Zugangsdaten" in box.change_setting("anthropic_api_key", "sk-ant-123")["error"]


def test_an_unknown_setting_lists_what_is_possible(settings: Settings) -> None:
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    error = box.change_setting("lieblingsfarbe", "gruen")["error"]
    assert "kenne ich nicht" in error
    assert "aussehen" in error, "was geht, steht dabei"


def test_a_bad_value_is_reported_not_stored(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr("cortex.config.find_env_file", lambda: target)
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    assert "zwischen 1 und 60" in box.change_setting("werkzeug_budget", "9999")["error"]
    assert target.read_text(encoding="utf-8") == "", "nichts geschrieben"


def test_the_setting_tool_is_dispatched(settings: Settings) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    box = Toolbox(
        settings,
        cache=None,
        on_event=lambda name, payload: events.append((name, payload)),
        fetcher=_mock_fetcher(_html_handler("<html></html>")),
    )
    box.call("change_setting", {"setting": "aussehen", "value": "dunkel"})
    assert ("appearance", {"theme": "dark"}) in events


def test_the_recheck_filters_out_pages_already_read(settings: Settings) -> None:
    """Eine zweite Runde auf denselben Seiten braeuchte niemand."""

    def fake_search(query, **kwargs):
        return [
            SearchResult(title="Alt", url="https://gelesen.de/1", snippet="S"),
            SearchResult(title="Neu", url="https://frisch.de/1", snippet="S"),
        ]

    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    with patch("cortex.tools.search_web", side_effect=fake_search):
        box.avoid_domains = {"gelesen.de"}
        result = box.web_search("frage")

    urls = [hit["url"] for hit in result["results"]]
    assert urls == ["https://frisch.de/1"]
    assert "aussortiert" in result["note"]


def test_nothing_left_after_filtering_is_better_than_nothing(settings: Settings) -> None:
    """Eine leere Trefferliste waere schlechter als eine bekannte."""

    def fake_search(query, **kwargs):
        return [SearchResult(title="Alt", url="https://gelesen.de/1", snippet="S")]

    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    with patch("cortex.tools.search_web", side_effect=fake_search):
        box.avoid_domains = {"gelesen.de"}
        result = box.web_search("frage")
    assert len(result["results"]) == 1


def test_without_a_recheck_nothing_is_filtered(settings: Settings) -> None:
    def fake_search(query, **kwargs):
        return [SearchResult(title="A", url="https://a.de/1", snippet="S")]

    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    with patch("cortex.tools.search_web", side_effect=fake_search):
        result = box.web_search("frage")
    assert len(result["results"]) == 1
    assert "note" not in result


def test_a_read_page_claims_its_domain_when_asked_to(settings: Settings) -> None:
    """So meidet der naechste Agent, was dieser schon gelesen hat."""
    langer_text = "<p>" + ("Ein Satz mit genug Text, damit die Seite zaehlt. " * 30) + "</p>"
    fetcher = _mock_fetcher(_html_handler(f"<html><body>{langer_text}</body></html>"))
    box = Toolbox(settings, cache=None, fetcher=fetcher)
    box.fetch_page("https://example.org/a")
    assert box.avoid_domains == set(), "ohne Anweisung traegt niemand etwas ein"

    box.claim_sources = True
    box.fetch_page("https://beispiel.de/b")
    assert "beispiel.de" in box.avoid_domains
