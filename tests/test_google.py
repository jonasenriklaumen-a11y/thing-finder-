"""Tests fuer Gmail und Kalender -- alle Google-Aufrufe sind gemockt."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from scoutr import google
from scoutr.google import Google, GoogleError, NotConnected, Tokens, TokenStore


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


@pytest.fixture
def store(tmp_path: Path) -> TokenStore:
    return TokenStore(tmp_path)


@pytest.fixture
def linked(store: TokenStore) -> TokenStore:
    store.save(
        Tokens(
            access_token="at-1",
            refresh_token="rt-1",
            expires_at=time.time() + 3600,
            email="jemand@example.com",
        )
    )
    return store


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Ablage
# ---------------------------------------------------------------------------
def test_tokens_are_not_readable_on_disk(store: TokenStore) -> None:
    """Wer die Datei oeffnet, soll darin kein Refresh-Token finden."""
    store.save(Tokens(access_token="geheim-at", refresh_token="geheim-rt"))
    raw = store.path.read_text(encoding="utf-8")
    assert "geheim-rt" not in raw
    assert "geheim-at" not in raw
    assert store.load().refresh_token == "geheim-rt"


def test_the_token_file_is_private(store: TokenStore) -> None:
    store.save(Tokens(refresh_token="rt"))
    assert store.path.stat().st_mode & 0o077 == 0


def test_without_a_file_nothing_is_connected(store: TokenStore) -> None:
    assert store.load().refresh_token == ""


def test_an_unreadable_file_means_not_connected(store: TokenStore) -> None:
    """Kaputt heisst 'melde dich neu an', nicht 'Absturz'."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("kein gueltiges Token", encoding="utf-8")
    assert store.load().refresh_token == ""


def test_disconnecting_removes_the_file(store: TokenStore) -> None:
    store.save(Tokens(refresh_token="rt"))
    store.clear()
    assert not store.path.exists()
    store.clear()  # zweimal trennen ist kein Fehler


# ---------------------------------------------------------------------------
# Anmeldung
# ---------------------------------------------------------------------------
def test_only_read_scopes_are_requested() -> None:
    """Verschicken oder loeschen soll technisch unmoeglich sein."""
    url = google.consent_url("id-1", "http://localhost:8765/google")
    assert "gmail.readonly" in url
    assert "calendar.readonly" in url
    for forbidden in ("gmail.send", "gmail.modify", "gmail.compose", "calendar.events"):
        assert forbidden not in url


def test_the_consent_url_asks_for_a_refresh_token() -> None:
    """Ohne access_type=offline waere nach einer Stunde Schluss."""
    url = google.consent_url("id-1", "http://localhost:8765/google")
    assert "access_type=offline" in url
    assert "prompt=consent" in url


def test_a_missing_client_id_is_explained() -> None:
    with pytest.raises(GoogleError, match="Client-ID"):
        google.consent_url("", "http://localhost:8765/google")


@pytest.mark.parametrize(
    ("pasted", "expected"),
    [
        ("http://localhost:8765/google?code=4%2F0AX&scope=email", "4/0AX"),
        ("http://localhost:8765/google?state=s&code=abc", "abc"),
        ("  abc  ", "abc"),
        ("", ""),
        ("http://localhost:8765/google?error=access_denied", ""),
    ],
)
def test_the_code_is_found_in_whatever_gets_pasted(pasted: str, expected: str) -> None:
    """Vom Handy aus kopiert man die Adresse, am Rechner oft nur den Code."""
    assert google.code_from(pasted) == expected


def test_a_missing_refresh_token_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    """Google schickt beim zweiten Mal keines -- das muss man wissen."""
    monkeypatch.setattr(google, "_token_request", lambda payload: {"access_token": "at"})
    with pytest.raises(GoogleError, match=re.escape("myaccount.google.com/permissions")):
        google.exchange_code("id", "secret", "code-1", "http://localhost/google")


def test_an_empty_code_is_refused() -> None:
    with pytest.raises(GoogleError, match="Kein Code"):
        google.exchange_code("id", "secret", "   ", "http://localhost/google")


# ---------------------------------------------------------------------------
# Kalender
# ---------------------------------------------------------------------------
def test_events_come_back_readable(linked: TokenStore) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "summary": "Zahnarzt",
                        "start": {"dateTime": "2026-09-08T09:30:00+02:00"},
                        "end": {"dateTime": "2026-09-08T10:00:00+02:00"},
                        "location": "Bremen",
                        "attendees": [{"email": "praxis@example.com"}],
                    }
                ]
            },
        )

    api = Google("id", "secret", linked, _client(handler))
    events = api.events(days=3)
    assert events[0]["summary"] == "Zahnarzt"
    assert events[0]["location"] == "Bremen"
    assert events[0]["whole_day"] is False
    assert "singleEvents=true" in seen["url"], "Serientermine muessen aufgeloest werden"
    assert "orderBy=startTime" in seen["url"]


def test_a_whole_day_event_is_marked(linked: TokenStore) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"items": [{"summary": "Urlaub", "start": {"date": "2026-09-08"}}]}
        )

    api = Google("id", "secret", linked, _client(handler))
    assert api.events()[0]["whole_day"] is True


def test_the_number_of_days_stays_sane(linked: TokenStore) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": []})

    api = Google("id", "secret", linked, _client(handler))
    api.events(days=99999)
    assert "timeMax" in seen["url"]
    api.events(days=0)  # kein Absturz bei 0


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------
def test_mail_search_returns_headers_only(linked: TokenStore) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        return httpx.Response(
            200,
            json={
                "snippet": "Ihre Sendung ist unterwegs",
                "labelIds": ["UNREAD"],
                "payload": {
                    "headers": [
                        {"name": "From", "value": "DHL <noreply@dhl.de>"},
                        {"name": "Subject", "value": "Ihre Sendung"},
                        {"name": "Date", "value": "Mon, 7 Sep 2026 08:00:00 +0200"},
                    ]
                },
            },
        )

    api = Google("id", "secret", linked, _client(handler))
    mails = api.search_mail("from:dhl", count=1)
    assert mails[0]["subject"] == "Ihre Sendung"
    assert mails[0]["unread"] is True
    assert "format=metadata" in calls[1], "der Volltext wird hier gar nicht geholt"


def test_reading_a_mail_prefers_plain_text(linked: TokenStore) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "payload": {
                    "mimeType": "multipart/alternative",
                    "headers": [{"name": "Subject", "value": "Termin"}],
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64("Reiner Text")}},
                        {"mimeType": "text/html", "body": {"data": _b64("<p>HTML</p>")}},
                    ],
                }
            },
        )

    api = Google("id", "secret", linked, _client(handler))
    assert api.read_mail("m1")["text"] == "Reiner Text"


def test_html_only_mail_is_stripped(linked: TokenStore) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "payload": {
                    "mimeType": "text/html",
                    "headers": [],
                    "body": {"data": _b64("<style>x{}</style><p>Hallo <b>Welt</b></p>")},
                }
            },
        )

    api = Google("id", "secret", linked, _client(handler))
    text = api.read_mail("m1")["text"]
    assert "Hallo" in text and "Welt" in text
    assert "<" not in text and "x{}" not in text


def test_a_very_long_mail_is_shortened(linked: TokenStore) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [],
                    "body": {"data": _b64("A" * 20000)},
                }
            },
        )

    api = Google("id", "secret", linked, _client(handler))
    mail = api.read_mail("m1")
    assert len(mail["text"]) == google.MAX_BODY_CHARS
    assert mail["shortened"] is True


def test_reading_without_an_id_is_refused(linked: TokenStore) -> None:
    api = Google("id", "secret", linked, _client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(GoogleError, match="Kennung"):
        api.read_mail("  ")


# ---------------------------------------------------------------------------
# Token-Erneuerung und Fehler
# ---------------------------------------------------------------------------
def test_an_expired_token_is_renewed(store: TokenStore, monkeypatch: pytest.MonkeyPatch) -> None:
    store.save(Tokens(access_token="alt", refresh_token="rt-1", expires_at=0))
    monkeypatch.setattr(
        google,
        "_token_request",
        lambda payload: {"access_token": "neu", "expires_in": 3600},
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"items": []})

    api = Google("id", "secret", store, _client(handler))
    api.events()
    assert seen["auth"] == "Bearer neu"
    assert store.load().access_token == "neu", "das frische Token wird gemerkt"


def test_without_a_connection_the_message_is_useful(store: TokenStore) -> None:
    api = Google("id", "secret", store, _client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(NotConnected, match="Einstellungen"):
        api.events()


def test_a_403_names_the_likely_cause(linked: TokenStore) -> None:
    """Fast immer: API nicht aktiviert oder Konto kein Testnutzer."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "Gmail API has not been used"}})

    api = Google("id", "secret", linked, _client(handler))
    with pytest.raises(GoogleError, match="Testnutzer"):
        api.events()


def test_a_network_failure_is_reported_not_raised_raw(linked: TokenStore) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("kein Netz")

    api = Google("id", "secret", linked, _client(handler))
    with pytest.raises(GoogleError, match="nicht erreichbar"):
        api.events()


def test_a_single_broken_mail_does_not_sink_the_search(linked: TokenStore) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        if "m1" in request.url.path:
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"snippet": "gut", "payload": {"headers": []}})

    api = Google("id", "secret", linked, _client(handler))
    mails = api.search_mail(count=2)
    assert len(mails) == 1


def test_the_account_is_remembered_when_connecting(
    store: TokenStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"email": "jemand@example.com"})

    api = Google("id", "secret", store, _client(handler))
    api.remember(Tokens(access_token="at", refresh_token="rt", expires_at=time.time() + 3600))
    assert store.load().email == "jemand@example.com"
    assert api.connected() is True


def test_the_stored_file_is_json_shaped(store: TokenStore) -> None:
    """Damit ein spaeteres Feld nicht die ganze Datei unlesbar macht."""
    store.save(Tokens(refresh_token="rt", email="a@b.de"))
    from scoutr.memory import Cipher

    raw = Cipher(store._key_path).decrypt(store.path.read_text(encoding="utf-8"))
    assert json.loads(raw)["email"] == "a@b.de"
