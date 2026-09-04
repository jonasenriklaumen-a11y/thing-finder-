"""Tests fuer die Lagerverwaltung -- alle HTTP-Aufrufe sind gemockt."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from cortex import storage
from cortex.storage import NotAllowed, Storage, StorageError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(payload: Any):
    return lambda request: httpx.Response(200, json=payload)


CONFIG = {"app": "storage-system", "name": "Lagerverwaltung", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# Adressen und Rechte
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("192.168.1.5", "http://192.168.1.5:3000"),
        ("192.168.1.5:8080", "http://192.168.1.5:8080"),
        ("http://lager.local:3000/", "http://lager.local:3000"),
        ("https://lager.example", "https://lager.example"),
        ("", ""),
    ],
)
def test_addresses_are_completed(given: str, expected: str) -> None:
    """Den Port tippt niemand gern mit."""
    assert storage.normalize_url(given) == expected


@pytest.mark.parametrize(
    ("given", "expected"),
    [("read", "read"), ("WRITE", "write"), ("off", "off"), ("", "off"), ("quatsch", "off")],
)
def test_unknown_rights_mean_nothing_allowed(given: str, expected: str) -> None:
    """Im Zweifel lieber nichts duerfen als versehentlich alles."""
    assert storage.normalize_access(given) == expected


def test_reading_is_refused_when_switched_off() -> None:
    client = Storage("192.168.1.5", access="off", client=_client(_ok([])))
    with pytest.raises(NotAllowed, match="abgeschaltet"):
        client.rooms()


def test_writing_is_refused_with_read_only() -> None:
    """Die wichtigste Zusage der Einstellung -- und sie sitzt im Client."""
    client = Storage("192.168.1.5", access="read", client=_client(_ok({"id": 1})))
    for attempt in (
        lambda: client.add_room("Keller"),
        lambda: client.add_furniture(1, "Regal"),
        lambda: client.add_item(1, "Schraube"),
        lambda: client.update_item(1, name="anders"),
        lambda: client.change_quantity(1, -1),
    ):
        with pytest.raises(NotAllowed, match="nur lesen"):
            attempt()


def test_reading_works_with_read_only() -> None:
    client = Storage("192.168.1.5", access="read", client=_client(_ok([{"id": 1, "name": "K"}])))
    assert client.rooms()[0]["name"] == "K"


def test_without_an_address_the_hint_says_where_to_put_one() -> None:
    client = Storage("", access="read", client=_client(_ok([])))
    with pytest.raises(StorageError, match="Einstellungen"):
        client.rooms()


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------
def test_the_server_has_to_identify_itself() -> None:
    """Auf Port 3000 laeuft in vielen Haushalten irgendein anderer Server."""
    client = Storage("192.168.1.5", client=_client(_ok({"maxUploadMb": 25})))
    with pytest.raises(StorageError, match="keine Lagerverwaltung"):
        client.info()


def test_html_instead_of_json_is_explained() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>irgendwas</html>")

    client = Storage("192.168.1.5", client=_client(handler))
    with pytest.raises(StorageError, match="etwas anderes"):
        client.info()


def test_a_search_carries_the_full_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "schraub"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 2,
                        "number": "A2",
                        "name": "Schrauben 4x40",
                        "quantity": 250,
                        "roomName": "Keller",
                        "furnitureName": "Regal links",
                        "imageFile": "abc.png",
                        "createdAt": "2026-01-01",
                    }
                ]
            },
        )

    hits = Storage("192.168.1.5", client=_client(handler)).search("schraub")
    assert hits[0]["number"] == "A2"
    assert hits[0]["roomName"] == "Keller"
    assert "imageFile" not in hits[0], "Dateinamen fuellen nur das Kontextfenster"
    assert "createdAt" not in hits[0]


def test_an_empty_search_asks_nothing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("darf gar nicht erst rausgehen")

    assert Storage("192.168.1.5", client=_client(handler)).search("   ") == []


def test_long_lists_are_cut() -> None:
    """Ein Lager mit tausend Artikeln wuerde das Kontextfenster allein fuellen."""
    rows = [{"id": i, "name": f"Teil {i}"} for i in range(500)]
    client = Storage("192.168.1.5", client=_client(_ok(rows)))
    assert len(client.rooms()) == storage.MAX_ROWS


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------
def test_adding_an_item_sends_name_and_quantity() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(path=request.url.path, body=json.loads(request.content))
        return httpx.Response(201, json={"id": 5, "number": "A1", "name": "Schraube"})

    client = Storage("192.168.1.5", access="write", client=_client(handler))
    created = client.add_item(3, "Schraube", 12)
    assert seen["path"] == "/api/furniture/3/items"
    assert seen["body"] == {"name": "Schraube", "quantity": 12}
    assert created["number"] == "A1", "die Nummer vergibt der Server"


def test_a_nameless_entry_is_refused_before_the_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("darf gar nicht erst rausgehen")

    client = Storage("192.168.1.5", access="write", client=_client(handler))
    for attempt in (
        lambda: client.add_item(1, "  "),
        lambda: client.add_room(""),
        lambda: client.add_furniture(1, ""),
    ):
        with pytest.raises(StorageError, match="Namen"):
            attempt()


def test_changing_by_delta_uses_the_relative_endpoint() -> None:
    """Zwei Leute entnehmen gleichzeitig -- gesetzt wuerde einer den anderen ueberschreiben."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(path=request.url.path, body=json.loads(request.content))
        return httpx.Response(200, json={"id": 5, "quantity": 238})

    client = Storage("192.168.1.5", access="write", client=_client(handler))
    client.change_quantity(5, -12)
    assert seen["path"] == "/api/items/5/quantity"
    assert seen["body"] == {"delta": -12}


def test_a_change_by_zero_is_refused() -> None:
    client = Storage("192.168.1.5", access="write", client=_client(_ok({})))
    with pytest.raises(StorageError, match="keine"):
        client.change_quantity(5, 0)


def test_an_edit_without_any_field_is_refused() -> None:
    client = Storage("192.168.1.5", access="write", client=_client(_ok({})))
    with pytest.raises(StorageError, match="nichts zum Aendern"):
        client.update_item(5)


def test_there_is_no_way_to_delete() -> None:
    """Ein geloeschter Raum nimmt alles darin mit -- das bleibt beim Menschen."""
    assert not [name for name in dir(Storage) if "delete" in name or "remove" in name]


# ---------------------------------------------------------------------------
# Fehler
# ---------------------------------------------------------------------------
def test_a_404_is_put_in_plain_words() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Artikel nicht gefunden."})

    client = Storage("192.168.1.5", client=_client(handler))
    with pytest.raises(StorageError, match="nicht gefunden"):
        client.item(999)


def test_an_unreachable_server_is_reported_not_raised_raw() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("kein Netz")

    client = Storage("192.168.1.5", client=_client(handler))
    with pytest.raises(StorageError, match="nicht erreichbar"):
        client.rooms()


# ---------------------------------------------------------------------------
# Im Netz finden
# ---------------------------------------------------------------------------
def test_discovery_only_accepts_a_real_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auf Port 3000 laeuft oft ein anderer Entwicklungsserver."""
    monkeypatch.setattr("cortex.lan.find_port", lambda port, subnet: ["10.0.0.7", "10.0.0.9"])

    def handler(request: httpx.Request) -> httpx.Response:
        if "10.0.0.9" in str(request.url):
            return httpx.Response(200, json=CONFIG)
        return httpx.Response(200, json={"hello": "irgendein anderer Dienst"})

    monkeypatch.setattr(
        storage, "Storage", lambda url, **kw: Storage(url, client=_client(handler), **kw)
    )
    assert storage.discover() == ["http://10.0.0.9:3000"]


def test_discovery_without_a_network_gives_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex.lan import NotPrivate

    def boom(port, subnet):
        raise NotPrivate("kein privates Netz")

    monkeypatch.setattr("cortex.lan.find_port", boom)
    assert storage.discover() == []
