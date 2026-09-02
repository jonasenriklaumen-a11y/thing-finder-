"""Tests fuer die Home-Assistant-Anbindung -- Netz gemockt."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from scoutr.homeassistant import (
    ALLOWED_DOMAINS,
    PROTECTED_DOMAINS,
    HomeAssistant,
    HomeAssistantError,
    normalize_url,
)

STATES: list[dict[str, Any]] = [
    {
        "entity_id": "light.wohnzimmer",
        "state": "on",
        "attributes": {"friendly_name": "Wohnzimmer Decke"},
        "last_changed": "2026-09-02T18:30:00.000Z",
    },
    {
        "entity_id": "sensor.wohnzimmer_temperatur",
        "state": "21.4",
        "attributes": {"friendly_name": "Wohnzimmer Temperatur", "unit_of_measurement": "°C"},
        "last_changed": "2026-09-02T18:29:00.000Z",
    },
    {
        "entity_id": "lock.haustuer",
        "state": "locked",
        "attributes": {"friendly_name": "Haustuer"},
        "last_changed": "2026-09-02T08:00:00.000Z",
    },
    {"entity_id": "switch.kaffee", "state": "off", "attributes": {}},
]


@pytest.fixture
def ha(monkeypatch: pytest.MonkeyPatch) -> HomeAssistant:
    """Client, dessen Anfragen auf eine nachgebaute Instanz treffen."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.headers.get("Authorization") != "Bearer geheim":
            return httpx.Response(401, json={"message": "Unauthorized"})
        path = request.url.path
        if path == "/api/":
            return httpx.Response(200, json={"message": "API running."})
        if path == "/api/config":
            return httpx.Response(200, json={"location_name": "Zuhause", "version": "2026.8.1"})
        if path == "/api/states":
            return httpx.Response(200, json=STATES)
        if path.startswith("/api/services/"):
            return httpx.Response(200, json=[{"entity_id": "light.wohnzimmer"}])
        return httpx.Response(404, json={"message": "not found"})

    transport = httpx.MockTransport(handler)

    def patched(method: str, url: str, **kwargs: Any) -> httpx.Response:
        kwargs.pop("timeout", None)
        with httpx.Client(transport=transport) as session:
            return session.request(method, url, **kwargs)

    monkeypatch.setattr(httpx, "request", patched)
    instance = HomeAssistant("http://192.168.1.5:8123", "geheim")
    instance.calls = calls  # type: ignore[attr-defined]
    return instance


# -- Adressen -------------------------------------------------------------
def test_a_bare_address_gets_scheme_and_port() -> None:
    """Niemand tippt gern http:// und :8123 mit."""
    assert normalize_url("192.168.1.5") == "http://192.168.1.5:8123"
    assert normalize_url("homeassistant.local") == "http://homeassistant.local:8123"


def test_an_explicit_address_is_left_alone() -> None:
    assert normalize_url("http://192.168.1.5:8123/") == "http://192.168.1.5:8123"
    assert normalize_url("https://ha.example.com") == "https://ha.example.com"
    assert normalize_url("http://192.168.1.5:9000") == "http://192.168.1.5:9000"


def test_an_empty_address_stays_empty() -> None:
    assert normalize_url("") == ""
    assert not HomeAssistant("", "token").configured
    assert not HomeAssistant("http://x:8123", "").configured


# -- Lesen ----------------------------------------------------------------
def test_ping_reports_name_and_version(ha: HomeAssistant) -> None:
    assert ha.ping() == "Zuhause 2026.8.1"


def test_states_are_read_with_names_and_units(ha: HomeAssistant) -> None:
    entities = {entity.entity_id: entity for entity in ha.states()}
    assert entities["light.wohnzimmer"].state == "on"
    assert entities["sensor.wohnzimmer_temperatur"].unit == "°C"
    assert entities["sensor.wohnzimmer_temperatur"].name == "Wohnzimmer Temperatur"
    assert entities["light.wohnzimmer"].domain == "light"


def test_searching_by_word(ha: HomeAssistant) -> None:
    found = [entity.entity_id for entity in ha.find(search="wohnzimmer")]
    assert found == ["light.wohnzimmer", "sensor.wohnzimmer_temperatur"]


def test_searching_by_domain(ha: HomeAssistant) -> None:
    assert [entity.entity_id for entity in ha.find(domain="lock")] == ["lock.haustuer"]


def test_the_overview_counts_per_domain(ha: HomeAssistant) -> None:
    """Bei tausend Entitaeten ist die Uebersicht der einzige brauchbare Einstieg."""
    assert ha.domains() == {"light": 1, "lock": 1, "sensor": 1, "switch": 1}


def test_an_entity_reads_like_a_sentence(ha: HomeAssistant) -> None:
    entity = ha.find(search="temperatur")[0]
    assert str(entity) == "Wohnzimmer Temperatur: 21.4 °C"


def test_the_search_result_is_capped(ha: HomeAssistant) -> None:
    assert len(ha.find(limit=2)) == 2


# -- Fehlerfaelle ---------------------------------------------------------
def test_a_wrong_token_says_where_to_get_a_new_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "request",
        lambda method, url, **kw: httpx.Client(transport=transport).request(
            method, url, **{k: v for k, v in kw.items() if k != "timeout"}
        ),
    )
    with pytest.raises(HomeAssistantError) as exc:
        HomeAssistant("http://x:8123", "falsch").states()
    assert "Langlebige Zugriffstokens" in str(exc.value)


def test_an_unreachable_instance_names_the_address(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(method: str, url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("keine Route")

    monkeypatch.setattr(httpx, "request", boom)
    with pytest.raises(HomeAssistantError) as exc:
        HomeAssistant("http://192.168.1.5:8123", "t").states()
    assert "192.168.1.5:8123" in str(exc.value)


def test_without_setup_the_message_points_at_the_command() -> None:
    with pytest.raises(HomeAssistantError) as exc:
        HomeAssistant("", "").states()
    assert "connect-ha" in str(exc.value)


# -- Schalten -------------------------------------------------------------
def test_calling_a_service(ha: HomeAssistant) -> None:
    changed = ha.call("light", "turn_on", "light.wohnzimmer", {"brightness_pct": 40})
    assert changed == [{"entity_id": "light.wohnzimmer"}]
    sent = ha.calls[-1]  # type: ignore[attr-defined]
    assert sent.url.path == "/api/services/light/turn_on"
    import json

    assert json.loads(sent.content) == {
        "brightness_pct": 40,
        "entity_id": "light.wohnzimmer",
    }


def test_the_protected_domains_cover_what_matters() -> None:
    """Was die Wohnung oeffnet oder Waerme regelt, wird nie stillschweigend geschaltet."""
    for domain in ("lock", "alarm_control_panel", "cover"):
        assert domain in PROTECTED_DOMAINS
    assert PROTECTED_DOMAINS <= ALLOWED_DOMAINS


# -- Zwischenspeicher -----------------------------------------------------
def test_repeated_reads_hit_the_instance_once(ha: HomeAssistant) -> None:
    """Der Agent fragt in einer Runde gern dreimal -- das reicht einmal."""
    from scoutr.homeassistant import _states_cache

    _states_cache.clear()
    ha.domains()
    ha.find(search="wohnzimmer")
    ha.find(domain="lock")
    paths = [call.url.path for call in ha.calls]  # type: ignore[attr-defined]
    assert paths.count("/api/states") == 1


def test_a_stale_cache_is_refused(ha: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Licht kann in zehn Sekunden ausgehen -- so lange und nicht laenger."""
    import scoutr.homeassistant as module

    module._states_cache.clear()
    ha.states()
    monkeypatch.setattr(module.time, "monotonic", lambda: 10_000.0)
    ha.states()
    paths = [call.url.path for call in ha.calls]  # type: ignore[attr-defined]
    assert paths.count("/api/states") == 2


def test_switching_something_invalidates_the_cache(ha: HomeAssistant) -> None:
    """Sonst meldet scoutr direkt nach dem Schalten noch den alten Zustand."""
    from scoutr.homeassistant import _states_cache

    _states_cache.clear()
    ha.states()
    ha.call("light", "turn_on", "light.wohnzimmer")
    ha.states()
    paths = [call.url.path for call in ha.calls]  # type: ignore[attr-defined]
    assert paths.count("/api/states") == 2


def test_fresh_bypasses_the_cache(ha: HomeAssistant) -> None:
    from scoutr.homeassistant import _states_cache

    _states_cache.clear()
    ha.states()
    ha.states(fresh=True)
    paths = [call.url.path for call in ha.calls]  # type: ignore[attr-defined]
    assert paths.count("/api/states") == 2
