"""Die vier Dienste ohne Schluessel aus 9.5.17: Tagesschau, Wikipedia,
Waehrungsrechner, Feiertage. Kein Test spricht mit dem Netz -- die Antworten
kommen aus einem httpx.MockTransport, der mitschreibt, was gefragt wurde.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from aquaticy import addons, tools
from aquaticy.config import Settings


@pytest.fixture(autouse=True)
def ohne_takt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(addons, "_takt", lambda host, abstand=1.05: None)
    addons._tagesschau_abrufe.clear()


def _mock(antworten: dict[str, Any], gefragt: list[httpx.Request]) -> httpx.Client:
    def handler(anfrage: httpx.Request) -> httpx.Response:
        gefragt.append(anfrage)
        for anfang, antwort in antworten.items():
            if str(anfrage.url).startswith(anfang):
                if isinstance(antwort, httpx.Response):
                    return antwort
                return httpx.Response(200, json=antwort)
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler))


# -- Katalog -----------------------------------------------------------------
def test_the_four_new_services_are_in_the_catalogue() -> None:
    for neu in ("nachrichten", "wikipedia", "waehrung", "feiertage"):
        addon = addons.CATALOG[neu]
        assert addon.login == "keine" and not addon.werkstatt and not addon.programm
        assert neu in addons.RIGHTS
        assert neu in tools.ADDON_TOOLS.values()


def test_services_install_instantly_and_expose_their_tools(tmp_path: Path) -> None:
    konto = Settings(data_dir=tmp_path, env_path=tmp_path / ".env")
    assert tools.addon_schemas_for(konto) == []
    for neu in ("nachrichten", "wikipedia", "waehrung", "feiertage"):
        eintrag = addons.install(konto, neu, pro=False)
        assert eintrag["installed"] and eintrag["status"] == "bereit"
    namen = {s["function"]["name"] for s in tools.addon_schemas_for(konto, pro=False)}
    assert {"news_tagesschau", "wikipedia", "currency", "holidays"} <= namen
    # Fremder Text: die Ergebnisse gelten als nicht vertrauenswuerdig.
    assert {"news_tagesschau", "wikipedia", "currency", "holidays"} <= tools.UNTRUSTED_SOURCES


# -- Tagesschau ----------------------------------------------------------------
def test_news_reads_a_topic() -> None:
    gefragt: list[httpx.Request] = []
    client = _mock({f"{addons.TAGESSCHAU}/news/": {"news": [
        {"title": "Bundestag beschließt X", "topline": "Haushalt", "firstSentence": "Heute …",
         "date": "2026-09-26T10:00:00", "ressort": "inland",
         "shareURL": "https://www.tagesschau.de/inland/x.html"},
    ]}}, gefragt)
    ergebnis = addons.news("inland", client=client)
    assert ergebnis["meldungen"][0]["titel"] == "Bundestag beschließt X"
    assert ergebnis["meldungen"][0]["link"].startswith("https://www.tagesschau.de/")
    assert gefragt[0].url.params["ressort"] == "inland"
    assert gefragt[0].method == "GET"


def test_news_searches() -> None:
    gefragt: list[httpx.Request] = []
    client = _mock({f"{addons.TAGESSCHAU}/search/": {"searchResults": [
        {"title": "Bahnstreik", "shareURL": "https://www.tagesschau.de/a.html"}]}}, gefragt)
    ergebnis = addons.news(query="Bahn Streik", limit=3, client=client)
    assert ergebnis["meldungen"][0]["titel"] == "Bahnstreik"
    assert gefragt[0].url.params["searchText"] == "Bahn Streik"
    assert gefragt[0].url.params["pageSize"] == "3"


def test_news_rejects_unknown_topics_and_respects_the_hourly_limit() -> None:
    assert "Unbekanntes Thema" in addons.news("klatsch")["error"]
    jetzt = 1_000_000.0
    for _ in range(addons.TAGESSCHAU_PER_HOUR):
        assert addons._tagesschau_erlaubt(jetzt)
    assert not addons._tagesschau_erlaubt(jetzt + 10)
    assert addons._tagesschau_erlaubt(jetzt + 3601)  # eine Stunde spaeter wieder frei


# -- Wikipedia -----------------------------------------------------------------
def test_wikipedia_finds_and_summarises() -> None:
    gefragt: list[httpx.Request] = []
    basis = addons.WIKIPEDIA.format(sprache="de")
    client = _mock({
        f"{basis}/w/rest.php/v1/search/page": {"pages": [
            {"key": "Bremen", "title": "Bremen"}, {"key": "Land_Bremen", "title": "Land Bremen"}]},
        f"{basis}/api/rest_v1/page/summary/Bremen": {
            "title": "Bremen", "description": "Stadt in Deutschland",
            "extract": "Bremen ist eine Großstadt …",
            "content_urls": {"desktop": {"page": "https://de.wikipedia.org/wiki/Bremen"}}},
    }, gefragt)
    artikel = addons.wikipedia("Bremen", client=client)
    assert artikel["titel"] == "Bremen"
    assert artikel["zusammenfassung"].startswith("Bremen ist")
    assert artikel["weitere"] == ["Land Bremen"]
    assert all(a.method == "GET" for a in gefragt)


def test_wikipedia_needs_a_term_and_only_knows_two_languages() -> None:
    assert "Wonach" in addons.wikipedia("")["error"]
    gefragt: list[httpx.Request] = []
    addons.wikipedia("x", lang="evil.example", client=_mock({}, gefragt))
    assert gefragt[0].url.host == "de.wikipedia.org"


# -- Waehrung ------------------------------------------------------------------
def test_currency_converts() -> None:
    gefragt: list[httpx.Request] = []
    client = _mock({addons.FRANKFURTER: {"amount": 1.0, "base": "EUR", "date": "2026-09-25",
                                         "rates": {"USD": 1.1, "GBP": 0.85}}}, gefragt)
    ergebnis = addons.currency("100", "eur", "usd, gbp", client=client)
    assert ergebnis["ergebnis"] == {"USD": 110.0, "GBP": 85.0}
    assert gefragt[0].url.params["symbols"] == "USD,GBP"


def test_currency_rejects_nonsense() -> None:
    assert "dreistellig" in addons.currency(1, "EURO")["error"]
    assert "keine Zahl" in addons.currency("viel", "EUR")["error"]
    assert "groesser" in addons.currency(-5, "EUR")["error"]


# -- Feiertage -----------------------------------------------------------------
def test_holidays_filter_by_state() -> None:
    gefragt: list[httpx.Request] = []
    client = _mock({addons.NAGER.format(jahr=2026, land="DE"): [
        {"date": "2026-01-01", "localName": "Neujahr", "global": True, "counties": None},
        {"date": "2026-01-06", "localName": "Heilige Drei Könige", "global": False,
         "counties": ["DE-BW", "DE-BY", "DE-ST"]},
        {"date": "2026-10-31", "localName": "Reformationstag", "global": False,
         "counties": ["DE-HB", "DE-NI"]},
    ]}, gefragt)
    bremen = addons.holidays("de", 2026, "HB", client=client)
    assert [t["name"] for t in bremen["feiertage"]] == ["Neujahr", "Reformationstag"]
    assert bremen["bundesland"] == "DE-HB"


def test_holidays_reject_nonsense() -> None:
    assert "zweistellig" in addons.holidays("Deutschland")["error"]
    assert "1990" in addons.holidays("DE", 3000)["error"]


# -- Rechte werden im Werkzeug durchgesetzt ---------------------------------------
def test_the_holiday_right_only_germany_is_enforced(tmp_path: Path) -> None:
    konto = Settings(data_dir=tmp_path, env_path=tmp_path / ".env")
    addons.install(konto, "feiertage", pro=False)
    addons.set_rights(konto, "feiertage", {"land": "de"})
    box = tools.Toolbox(konto)
    antwort = box._addon_call("holidays", {"country": "AT"})
    assert "Nur Deutschland" in antwort["error"]


def test_a_service_that_is_not_installed_does_not_exist(tmp_path: Path) -> None:
    konto = Settings(data_dir=tmp_path, env_path=tmp_path / ".env")
    antwort = tools.Toolbox(konto)._addon_call("wikipedia", {"query": "Bremen"})
    assert "nicht installiert" in antwort["error"]


def test_the_euro_only_currency_right_is_enforced(tmp_path: Path) -> None:
    konto = Settings(data_dir=tmp_path, env_path=tmp_path / ".env")
    addons.install(konto, "waehrung", pro=False)
    addons.set_rights(konto, "waehrung", {"waehrungen": "euro"})
    antwort = tools.Toolbox(konto)._addon_call("currency", {"base": "USD", "to": "GBP"})
    assert "Nur von oder nach Euro" in antwort["error"]
