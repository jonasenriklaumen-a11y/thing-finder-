"""Tests fuer die Kartensuche -- kein Netz, alles gemockt.

Der Punkt dieser Datei: die Karte ist gespendete Rechenzeit fremder Leute.
Was hier geprueft wird, ist deshalb nicht nur "findet er etwas", sondern
auch "haelt er sich an die Regeln, unter denen er fragen darf".
"""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import unquote_plus

import httpx
import pytest

from aquaticy.config import Settings
from aquaticy.places import (
    MAX_PLACES,
    Place,
    PlacesError,
    _address,
    _filter_for,
    find_places,
    geocode,
)
from aquaticy.tools import Toolbox


@pytest.fixture(autouse=True)
def _kein_warten(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Takt wird eigens geprueft -- die anderen Tests sollen nicht warten."""
    monkeypatch.setattr("aquaticy.places.MIN_INTERVAL", 0.0)


def _fake_client(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    gesehen: list[httpx.Request] = []

    def merkend(request: httpx.Request) -> httpx.Response:
        gesehen.append(request)
        return handler(request)

    def bauen(user_agent: str, timeout: float) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(merkend),
            headers={"User-Agent": user_agent},
            timeout=timeout,
        )

    monkeypatch.setattr("aquaticy.places._client", bauen)
    return gesehen


def _antwort(request: httpx.Request) -> httpx.Response:
    if "nominatim" in request.url.host:
        return httpx.Response(
            200,
            json=[{"lat": "53.0793", "lon": "8.8017", "display_name": "Bremen, Deutschland"}],
        )
    return httpx.Response(
        200,
        json={
            "elements": [
                {
                    "type": "node",
                    "lat": 53.08,
                    "lon": 8.80,
                    "tags": {
                        "name": "Radladen Meier",
                        "shop": "bicycle",
                        "addr:street": "Nebenstraße",
                        "addr:housenumber": "7",
                        "addr:postcode": "28195",
                        "addr:city": "Bremen",
                        "website": "https://radladen-meier.de",
                        "phone": "0421 12345",
                        "opening_hours": "Mo-Fr 09:00-18:00",
                    },
                },
                # Ohne Namen ist ein Punkt auf der Karte keine Auskunft.
                {"type": "node", "lat": 53.081, "lon": 8.801, "tags": {"shop": "bicycle"}},
            ]
        },
    )


def test_a_small_shop_comes_back_with_everything_that_matters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Genau das ist der Fall, für den es die Karte gibt: der Laden in der
    Nebenstraße, den keine Suchmaschine kennt."""
    _fake_client(monkeypatch, _antwort)
    orte, ortsname = find_places("Fahrradladen", "Bremen", "aquaticy-test/1.0")

    assert ortsname == "Bremen, Deutschland"
    assert len(orte) == 1, "Punkte ohne Namen fallen weg"
    laden = orte[0]
    assert laden.name == "Radladen Meier"
    assert laden.website == "https://radladen-meier.de"
    assert laden.address == "Nebenstraße 7, 28195 Bremen"
    assert laden.opening_hours == "Mo-Fr 09:00-18:00"
    assert laden.kind == "bicycle"


def test_the_honest_user_agent_goes_along(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Nutzungsregel verlangt einen echten Namen -- keine Browser-Tarnung."""
    gesehen = _fake_client(monkeypatch, _antwort)
    find_places("Cafe", "Bremen", "aquaticy/9.2 (+https://example.org)")
    assert gesehen, "es wurde gar nicht gefragt"
    for anfrage in gesehen:
        assert anfrage.headers["User-Agent"].startswith("aquaticy/9.2")
        assert "Mozilla" not in anfrage.headers["User-Agent"]


def test_only_one_place_at_a_time_never_a_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Systematische Abfragen sind ausdruecklich verboten. Es geht immer genau
    eine Umgebung zu einer Frage eines Menschen hinaus."""
    gesehen = _fake_client(monkeypatch, _antwort)
    find_places("Cafe", "Bremen", "aquaticy-test/1.0", radius_m=3000)
    assert len(gesehen) == 2, "einmal Ort, einmal Umgebung -- mehr nicht"
    abfrage = unquote_plus(gesehen[1].content.decode())
    assert "around:3000" in abfrage
    assert abfrage.count("around:") == 1


def test_the_radius_stays_within_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    gesehen = _fake_client(monkeypatch, _antwort)
    find_places("Cafe", "Bremen", "aquaticy-test/1.0", radius_m=999_999)
    assert "around:15000" in unquote_plus(gesehen[1].content.decode())

    gesehen.clear()
    find_places("Cafe", "Bremen", "aquaticy-test/1.0", radius_m=1)
    assert "around:200" in unquote_plus(gesehen[1].content.decode())


def test_the_number_of_hits_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    def viele(request: httpx.Request) -> httpx.Response:
        if "nominatim" in request.url.host:
            return _antwort(request)
        return httpx.Response(
            200,
            json={
                "elements": [
                    {"type": "node", "lat": 1.0, "lon": 1.0, "tags": {"name": f"Laden {n}"}}
                    for n in range(200)
                ]
            },
        )

    _fake_client(monkeypatch, viele)
    orte, _ = find_places("Cafe", "Bremen", "aquaticy-test/1.0")
    assert len(orte) == MAX_PLACES


def test_one_request_per_second(monkeypatch: pytest.MonkeyPatch) -> None:
    """Das Schloss ist modulweit -- es hilft nichts, wenn jeder Agent fuer
    sich hoeflich ist und vierundvierzig gleichzeitig fragen."""
    monkeypatch.setattr("aquaticy.places.MIN_INTERVAL", 0.05)
    monkeypatch.setattr("aquaticy.places._zuletzt", 0.0)
    _fake_client(monkeypatch, _antwort)

    zeiten: list[float] = []
    schloss = threading.Lock()

    def einer() -> None:
        find_places("Cafe", "Bremen", "aquaticy-test/1.0")
        with schloss:
            zeiten.append(time.monotonic())

    faeden = [threading.Thread(target=einer) for _ in range(4)]
    start = time.monotonic()
    for faden in faeden:
        faden.start()
    for faden in faeden:
        faden.join()
    # Vier Laeufe zu je zwei Aufrufen: sieben Wartezeiten liegen dazwischen.
    assert max(zeiten) - start >= 0.05 * 6


def test_an_unknown_word_is_searched_by_name() -> None:
    """"Radladen Meier" steht in keiner Kategorienliste -- gefunden werden
    soll er trotzdem."""
    assert _filter_for("Cafe") == "nwr[amenity=cafe]"
    assert _filter_for("bestes Café") == "nwr[amenity=cafe]"
    assert _filter_for("Fahrradladen") == "nwr[shop=bicycle]"
    assert 'name~"segelmacher"' in _filter_for("Segelmacher")
    # Sonderzeichen fliegen raus, bevor sie in die Abfrage kommen: aus einem
    # Versuch, die Abfrage zu schliessen, wird ein harmloses Suchwort.
    gebaut = _filter_for('a"];out;//')
    assert gebaut == 'nwr[name~"aout",i]'
    assert "];" not in gebaut and ";" not in gebaut


def test_the_map_failing_is_not_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    def kaputt(request: httpx.Request) -> httpx.Response:
        if "nominatim" in request.url.host:
            return _antwort(request)
        return httpx.Response(504)

    _fake_client(monkeypatch, kaputt)
    with pytest.raises(PlacesError):
        find_places("Cafe", "Bremen", "aquaticy-test/1.0")


@pytest.mark.parametrize("payload", [[], "kaputt", {"elements": "kaputt"}])
def test_an_unexpected_map_answer_becomes_a_places_error(
    monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        if "nominatim" in request.url.host:
            return _antwort(request)
        return httpx.Response(200, json=payload)

    _fake_client(monkeypatch, unexpected)
    with pytest.raises(PlacesError, match="Unerwartetes"):
        find_places("Café", "Bremen", "aquaticy-test/1.0")


def test_broken_map_rows_are_skipped_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def rows(request: httpx.Request) -> httpx.Response:
        if "nominatim" in request.url.host:
            return _antwort(request)
        return httpx.Response(
            200,
            json={
                "elements": [
                    None,
                    {"tags": "kaputt"},
                    {"tags": {"name": "Café Sicher"}, "lat": "keine-zahl"},
                ]
            },
        )

    _fake_client(monkeypatch, rows)
    places, _ = find_places("Café", "Bremen", "aquaticy-test/1.0")
    assert [place.name for place in places] == ["Café Sicher"]
    assert places[0].lat == 0.0


def test_an_unknown_place_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_client(monkeypatch, lambda request: httpx.Response(200, json=[]))
    with pytest.raises(PlacesError, match="kennt die Karte nicht"):
        geocode("Fantasialand 12", "aquaticy-test/1.0")


def test_without_a_place_nothing_goes_out(monkeypatch: pytest.MonkeyPatch) -> None:
    gesehen = _fake_client(monkeypatch, _antwort)
    with pytest.raises(PlacesError):
        geocode("   ", "aquaticy-test/1.0")
    assert gesehen == [], "ohne Ort wird gar nicht erst gefragt"


def test_an_address_without_fields_stays_empty() -> None:
    assert _address({}) == ""
    assert _address({"addr:city": "Bremen"}) == "Bremen"
    assert _address({"addr:street": "Hauptstraße"}) == "Hauptstraße"


def test_the_place_dict_leaves_out_what_is_missing() -> None:
    schlicht = Place(name="Kiosk")
    assert schlicht.as_dict() == {"name": "Kiosk"}


# ---------------------------------------------------------------------------
# Das Werkzeug am Werkzeugkasten
# ---------------------------------------------------------------------------
def test_the_tool_uses_the_location_filter(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    gesehen: dict[str, Any] = {}

    def fake_find(what, where, user_agent, **kwargs):
        gesehen.update(what=what, where=where, agent=user_agent, **kwargs)
        return [Place(name="Café Klein", website="https://klein.example")], "Bremen"

    monkeypatch.setattr("aquaticy.places.find_places", fake_find)
    settings.location = "Bremen"
    box = Toolbox(settings, cache=None)
    payload = box.local_places(what="Café")
    assert gesehen["where"] == "Bremen"
    assert payload["results"][0]["website"] == "https://klein.example"
    assert "fetch_page" in payload["note"], "der nächste Schritt steht dabei"
    box.close()


def test_the_tool_without_any_place_explains_itself(settings: Settings) -> None:
    settings.location = ""
    box = Toolbox(settings, cache=None)
    payload = box.local_places(what="Café")
    assert payload["results"] == []
    assert "Ort" in payload["error"]
    box.close()


def test_the_tool_rejects_a_broken_radius_without_crashing(settings: Settings) -> None:
    settings.location = "Bremen"
    box = Toolbox(settings, cache=None)
    payload = box.local_places(what="Café", radius_km="weit weg")  # type: ignore[arg-type]
    assert payload["results"] == []
    assert "Zahl" in payload["error"]
    box.close()


def test_a_broken_map_does_not_break_the_answer(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def kaputt(*args: Any, **kwargs: Any):
        raise PlacesError("Die Karte antwortet gerade nicht: ReadTimeout")

    monkeypatch.setattr("aquaticy.places.find_places", kaputt)
    settings.location = "Bremen"
    events: list[tuple[str, dict[str, Any]]] = []
    box = Toolbox(settings, cache=None, on_event=lambda name, data: events.append((name, data)))
    payload = box.local_places(what="Café")
    assert payload["results"] == []
    assert "antwortet gerade nicht" in payload["error"]
    assert payload["fallback"] == "web_search"
    assert any(name == "places_done" and data["hits"] == 0 for name, data in events)
    assert any(name == "note" and "Web" in data["text"] for name, data in events)
    assert not any(name == "error" for name, _ in events)
    box.close()
