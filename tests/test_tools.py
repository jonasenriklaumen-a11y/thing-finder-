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


# -- Heimnetz -------------------------------------------------------------
def test_lan_scan_can_be_switched_off(settings: Settings) -> None:
    settings.lan_enabled = False
    assert "abgeschaltet" in Toolbox(settings).lan_scan()["error"]
    assert "abgeschaltet" in Toolbox(settings).lan_check("192.168.1.5")["error"]


def test_lan_scan_refuses_public_networks(settings: Settings) -> None:
    """Fremde Netze durchsucht scoutr nicht."""
    result = Toolbox(settings).lan_scan("8.8.8.0/24")
    assert "kein privates Netz" in result["error"]


def test_lan_scan_without_a_known_network_says_what_to_do(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("scoutr.lan.own_subnet", lambda: "")
    result = Toolbox(settings).lan_scan()
    assert "SCOUTR_LAN_SUBNET" in result["error"]


def test_lan_scan_reports_devices(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from scoutr.lan import Device

    monkeypatch.setattr(
        "scoutr.lan.scan",
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
    monkeypatch.setattr("scoutr.lan.check_host", lambda address, ports, **kw: None)
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
        "scoutr.homeassistant.HomeAssistant.domains", lambda self: {"light": 12, "sensor": 40}
    )
    result = Toolbox(_ha_settings(settings)).ha_states()
    assert result["overview"] == {"light": 12, "sensor": 40}
    assert "domain" in result["note"]


def test_ha_states_finds_entities(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from scoutr.homeassistant import Entity

    monkeypatch.setattr(
        "scoutr.homeassistant.HomeAssistant.find",
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
    from scoutr.homeassistant import HomeAssistantError

    def boom(self, search="", domain="", limit=60):
        raise HomeAssistantError("Token abgelehnt")

    monkeypatch.setattr("scoutr.homeassistant.HomeAssistant.find", boom)
    assert Toolbox(_ha_settings(settings)).ha_states(search="x")["error"] == "Token abgelehnt"


def test_switching_is_off_unless_allowed(settings: Settings) -> None:
    """Standardmaessig sieht scoutr nur nach."""
    result = Toolbox(_ha_settings(settings)).ha_call("light", "turn_on", "light.kueche")
    assert "nur nachsehen" in result["error"]
    assert "SCOUTR_HA_CONTROL" in result["error"]


def test_unknown_domains_are_refused(settings: Settings) -> None:
    result = Toolbox(_ha_settings(settings, control=True)).ha_call("shell_command", "rm", "")
    assert "schaltet im Bereich" in result["error"]


def test_a_lock_needs_a_confirmation(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein missverstandener Satz darf nicht die Haustuer aufschliessen."""
    called: list[tuple] = []
    monkeypatch.setattr(
        "scoutr.homeassistant.HomeAssistant.call",
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
        "scoutr.homeassistant.HomeAssistant.call",
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
        "scoutr.homeassistant.HomeAssistant.call",
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
        "scoutr.homeassistant.HomeAssistant.call", lambda self, d, s, e="", data=None: [{"x": 1}]
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
    monkeypatch.setattr("scoutr.lan.scan", lambda subnet, quick=True: [])
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

    monkeypatch.setattr("scoutr.tools.search_web", fake_search)
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

    monkeypatch.setattr("scoutr.tools.search_web", fake_search)
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

    monkeypatch.setattr("scoutr.tools.search_web", fake_search)
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

    monkeypatch.setattr("scoutr.tools.search_web", fake_search)
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

    monkeypatch.setattr("scoutr.tools.search_web", fake_search)
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

    monkeypatch.setattr("scoutr.tools.search_web", fake_search)
    box = Toolbox(settings, cache=None, fetcher=_mock_fetcher(_html_handler("<html></html>")))
    result = box.web_search("Wie viel kostet ein gebrauchtes Lastenrad in Bremen?")
    assert result["results"][0]["url"] == "https://b.de/"


def test_the_fan_out_survives_a_dead_phrasing(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Eine gescheiterte Formulierung darf die Suche nicht mitreissen."""
    from scoutr.search import SearchError

    def fake_search(query, **kwargs):
        if not query.startswith("Wie"):
            raise SearchError("Engine weg")
        return [SearchResult(title="A", url="https://a.de/", snippet="S")]

    monkeypatch.setattr("scoutr.tools.search_web", fake_search)
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
    from scoutr.google import GoogleError

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
