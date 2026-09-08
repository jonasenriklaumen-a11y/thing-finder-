"""Gmail und Google Kalender -- lesend.

Aquaticy AI soll auf die Frage "wann ist mein naechster Termin" nicht das Web
durchsuchen muessen. Dieses Modul holt Termine und Mails direkt beim
Konto des Nutzers ab.

**Nur lesen.** Angefragt werden ausschliesslich die Leserechte
`gmail.readonly` und `calendar.readonly`. Damit ist technisch ausgeschlossen,
dass hier je eine Mail verschickt, geloescht oder ein Termin veraendert wird
-- Google laesst es schlicht nicht zu. Das ist Absicht: der Agent liest mit,
er handelt nicht im Namen des Nutzers.

**Aus, bis jemand es einschaltet.** Ohne `AQUATICY_GOOGLE=true` und ohne
hinterlegtes Konto existieren die Werkzeuge fuer das Modell gar nicht.

**Die Anmeldedaten bleiben hier.** Access- und Refresh-Token liegen
verschluesselt im Datenordner (dieselbe Fernet-Schluesseldatei wie beim
Speicher) und gehen nie an den Browser -- der erfaehrt nur, ob ein Konto
verbunden ist und welche Adresse es hat.

Angebunden wird ueber die OAuth-Variante fuer Desktop-Programme: der Nutzer
bestaetigt bei Google, Google schickt ihn auf `http://localhost:<port>`
zurueck, und von dort holt sich aquaticy den Code ab. Wer die Oberflaeche vom
Handy aus bedient, kopiert stattdessen die Adresse aus der Adresszeile --
darin steht derselbe Code.
"""

from __future__ import annotations

import base64
import contextlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"

#: Nur Leserechte. Das ist der Normalfall -- mehr soll Aquaticy nicht koennen,
#: solange es niemand ausdruecklich erlaubt.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
)

#: Mit "Aendern erlaubt" kommen zwei Rechte dazu -- und nur diese zwei:
#:
#: * `calendar.events` legt Termine an und aendert sie. Der Kalender selbst
#:   (Freigaben, Loeschen des Kalenders) bleibt aussen vor.
#: * `gmail.compose` schreibt Entwuerfe. Es ist bewusst nicht `gmail.send`:
#:   ein Entwurf kann man noch lesen, bevor er hinausgeht, eine verschickte
#:   Mail nicht mehr zurueckholen. Aquaticy verschickt nichts.
WRITE_SCOPES = (
    *SCOPES,
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.compose",
)

#: Datei mit den verschluesselten Token.
TOKEN_FILE = "google.json"

#: Ein paar Sekunden Sicherheitsabstand: laeuft das Token waehrend der
#: Anfrage ab, waere die Antwort ein 401 statt eines Ergebnisses.
EXPIRY_MARGIN = 60

#: Hoechstzahl der Treffer, die eine Abfrage zurueckgibt.
MAX_RESULTS = 20

#: So viel Text einer Mail wird uebernommen. Eine lange Mail wuerde sonst das
#: halbe Kontextfenster fuellen.
MAX_BODY_CHARS = 6000

REQUEST_TIMEOUT = 20


class GoogleError(RuntimeError):
    """Etwas an der Google-Anbindung ging schief."""


class NotConnected(GoogleError):
    """Es ist gar kein Konto verbunden."""


@dataclass(slots=True)
class Tokens:
    """Was nach der Anmeldung uebrig bleibt."""

    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    email: str = ""
    scopes: list[str] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return not self.access_token or time.time() >= self.expires_at - EXPIRY_MARGIN


# ---------------------------------------------------------------------------
# Ablage
# ---------------------------------------------------------------------------
class TokenStore:
    """Legt die Token verschluesselt im Datenordner ab."""

    def __init__(self, data_dir: Path, passphrase: str = "") -> None:
        self.path = Path(data_dir) / TOKEN_FILE
        self._key_path = Path(data_dir) / "memory.key"
        self._passphrase = passphrase

    def _cipher(self) -> Any:
        from aquaticy.memory import Cipher

        return Cipher(self._key_path, self._passphrase)

    def load(self) -> Tokens:
        if not self.path.is_file():
            return Tokens()
        try:
            raw = self._cipher().decrypt(self.path.read_text(encoding="utf-8").strip())
            data = json.loads(raw) if raw else {}
        except (OSError, ValueError, json.JSONDecodeError):
            # Unlesbar heisst "nicht verbunden", nicht "Absturz". Der Nutzer
            # meldet sich dann eben neu an.
            return Tokens()
        if not isinstance(data, dict):
            return Tokens()
        return Tokens(
            access_token=str(data.get("access_token", "")),
            refresh_token=str(data.get("refresh_token", "")),
            expires_at=float(data.get("expires_at", 0) or 0),
            email=str(data.get("email", "")),
            scopes=list(data.get("scopes") or []),
        )

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            self._cipher().encrypt(json.dumps(asdict(tokens), ensure_ascii=False)),
            encoding="utf-8",
        )
        from aquaticy.memory import secure_file

        secure_file(self.path)

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink()


# ---------------------------------------------------------------------------
# Anmeldung
# ---------------------------------------------------------------------------
def consent_url(
    client_id: str, redirect_uri: str, state: str = "", *, write: bool = False
) -> str:
    """Die Adresse, auf der der Nutzer bei Google zustimmt.

    `access_type=offline` sorgt fuer ein Refresh-Token, `prompt=consent`
    dafuer, dass Google es auch beim zweiten Mal wieder mitschickt -- sonst
    bekommt man beim erneuten Verbinden keines und die Sitzung endet nach
    einer Stunde.
    """
    if not client_id:
        raise GoogleError("Es fehlt die Client-ID. Siehe Einrichtung in der README.")
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(WRITE_SCOPES if write else SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if state:
        query["state"] = state
    return f"{AUTH_URL}?{urlencode(query)}"


def code_from(text: str) -> str:
    """Zieht den Code aus dem, was der Nutzer einfuegt.

    Erlaubt ist beides: die ganze Adresse aus der Adresszeile oder nur der
    Code. Wer vom Handy aus verbindet, kopiert die Adresse -- der Browser
    zeigt dann zwar eine Fehlerseite, aber der Code steht in der Zeile.
    """
    text = (text or "").strip()
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        found = parse_qs(urlparse(text).query).get("code", [""])[0]
        return found.strip()
    return text


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> Tokens:
    """Tauscht den Code gegen Access- und Refresh-Token."""
    code = code_from(code)
    if not code:
        raise GoogleError("Kein Code angekommen.")
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    data = _token_request(payload)
    tokens = Tokens(
        access_token=str(data.get("access_token", "")),
        refresh_token=str(data.get("refresh_token", "")),
        expires_at=time.time() + float(data.get("expires_in", 3600) or 3600),
        scopes=str(data.get("scope", "")).split(),
    )
    if not tokens.refresh_token:
        raise GoogleError(
            "Google hat kein Refresh-Token geschickt. Das passiert, wenn der Zugriff "
            "schon einmal erteilt wurde: unter myaccount.google.com/permissions den "
            "Eintrag entfernen und noch einmal verbinden."
        )
    return tokens


def refresh_tokens(client_id: str, client_secret: str, tokens: Tokens) -> Tokens:
    """Holt ein frisches Access-Token. Das Refresh-Token bleibt bestehen."""
    if not tokens.refresh_token:
        raise NotConnected("Kein Konto verbunden.")
    data = _token_request(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": tokens.refresh_token,
            "grant_type": "refresh_token",
        }
    )
    tokens.access_token = str(data.get("access_token", ""))
    tokens.expires_at = time.time() + float(data.get("expires_in", 3600) or 3600)
    return tokens


def _token_request(payload: dict[str, str]) -> dict[str, Any]:
    try:
        response = httpx.post(TOKEN_URL, data=payload, timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        raise GoogleError(f"Google nicht erreichbar: {exc}") from exc
    if response.status_code >= 400:
        raise GoogleError(_explain(response))
    try:
        return response.json()
    except ValueError as exc:
        raise GoogleError("Google hat keine verwertbare Antwort geschickt.") from exc


def _explain(response: httpx.Response) -> str:
    """Aus einer Google-Fehlerantwort einen Satz machen, der weiterhilft."""
    detail = ""
    with contextlib.suppress(ValueError):
        body = response.json()
        detail = str(
            body.get("error_description")
            or (body.get("error") or {}).get("message")
            or body.get("error")
            or ""
        )
    if response.status_code == 401:
        return f"Google lehnt die Anmeldung ab ({detail or 'nicht autorisiert'})."
    if response.status_code == 403:
        return (
            f"Google verweigert den Zugriff ({detail or 'kein Recht'}). Sind Gmail- und "
            "Kalender-API im Projekt aktiviert und das Konto als Testnutzer eingetragen?"
        )
    return f"Google antwortet mit {response.status_code}: {detail or 'kein Grund genannt'}"


# ---------------------------------------------------------------------------
# Zugriff
# ---------------------------------------------------------------------------
class Google:
    """Liest Mails und Termine eines verbundenen Kontos."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        store: TokenStore,
        client: httpx.Client | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.store = store
        self._http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
        self._tokens: Tokens | None = None

    # -- Verbindung ------------------------------------------------------
    def tokens(self) -> Tokens:
        if self._tokens is None:
            self._tokens = self.store.load()
        return self._tokens

    def connected(self) -> bool:
        return bool(self.tokens().refresh_token)

    def account(self) -> str:
        """Die Adresse des verbundenen Kontos, oder "" wenn keines da ist."""
        return self.tokens().email

    def disconnect(self) -> None:
        self.store.clear()
        self._tokens = None

    def remember(self, tokens: Tokens) -> None:
        """Neue Token uebernehmen und die Kontoadresse dazu holen."""
        self._tokens = tokens
        with contextlib.suppress(GoogleError):
            tokens.email = self._email()
        self.store.save(tokens)

    def _email(self) -> str:
        data = self._get("https://www.googleapis.com/oauth2/v2/userinfo", {})
        return str(data.get("email", ""))

    def _access(self) -> str:
        tokens = self.tokens()
        if not tokens.refresh_token:
            raise NotConnected(
                "Kein Google-Konto verbunden. In den Einstellungen unter "
                "'Gmail & Kalender' verbinden."
            )
        if tokens.stale:
            refresh_tokens(self.client_id, self.client_secret, tokens)
            self.store.save(tokens)
        return tokens.access_token

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._access()}"}
        try:
            response = self._http.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise GoogleError(f"Google nicht erreichbar: {exc}") from exc
        if response.status_code >= 400:
            raise GoogleError(_explain(response))
        try:
            return response.json()
        except ValueError as exc:
            raise GoogleError("Google hat keine verwertbare Antwort geschickt.") from exc

    # -- Kalender --------------------------------------------------------
    def events(self, days: int = 7, query: str = "", count: int = 10) -> list[dict[str, Any]]:
        """Termine der naechsten *days* Tage, zeitlich sortiert.

        `singleEvents` loest Serientermine in einzelne auf -- ohne das kommt
        eine woechentliche Besprechung als ein einziger Eintrag von 2019
        zurueck, und die Frage "was ist morgen" waere nicht zu beantworten.
        """
        days = max(1, min(int(days or 7), 365))
        now = datetime.now(UTC)
        params: dict[str, Any] = {
            "timeMin": now.isoformat().replace("+00:00", "Z"),
            "timeMax": (now + timedelta(days=days)).isoformat().replace("+00:00", "Z"),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max(1, min(int(count or 10), MAX_RESULTS)),
        }
        if query:
            params["q"] = query
        data = self._get(f"{CALENDAR_API}/calendars/primary/events", params)
        return [_event(item) for item in data.get("items", [])]

    # -- Gmail -----------------------------------------------------------
    def search_mail(self, query: str = "", count: int = 8) -> list[dict[str, Any]]:
        """Sucht Mails. *query* ist die Gmail-Suchsyntax (`from:`, `newer_than:` ...)."""
        count = max(1, min(int(count or 8), MAX_RESULTS))
        params: dict[str, Any] = {"maxResults": count}
        if query:
            params["q"] = query
        listing = self._get(f"{GMAIL_API}/messages", params)
        out: list[dict[str, Any]] = []
        for item in listing.get("messages", [])[:count]:
            with contextlib.suppress(GoogleError):
                out.append(self._headers(str(item.get("id", ""))))
        return [entry for entry in out if entry]

    def _headers(self, message_id: str) -> dict[str, Any]:
        """Absender, Betreff, Datum -- ohne den Text zu laden."""
        data = self._get(
            f"{GMAIL_API}/messages/{message_id}",
            {
                "format": "metadata",
                "metadataHeaders": ["From", "To", "Subject", "Date"],
            },
        )
        headers = {
            str(item.get("name", "")).lower(): str(item.get("value", ""))
            for item in (data.get("payload") or {}).get("headers", [])
        }
        return {
            "id": message_id,
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", "(kein Betreff)"),
            "date": headers.get("date", ""),
            "snippet": _unescape(str(data.get("snippet", ""))),
            "unread": "UNREAD" in (data.get("labelIds") or []),
        }

    def read_mail(self, message_id: str) -> dict[str, Any]:
        """Holt den Text einer Mail."""
        message_id = (message_id or "").strip()
        if not message_id:
            raise GoogleError("Ohne Kennung laesst sich keine Mail lesen.")
        data = self._get(f"{GMAIL_API}/messages/{message_id}", {"format": "full"})
        headers = {
            str(item.get("name", "")).lower(): str(item.get("value", ""))
            for item in (data.get("payload") or {}).get("headers", [])
        }
        body = _body_text(data.get("payload") or {})
        return {
            "id": message_id,
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", "(kein Betreff)"),
            "date": headers.get("date", ""),
            "text": body[:MAX_BODY_CHARS],
            "shortened": len(body) > MAX_BODY_CHARS,
        }

    # -- Schreiben -------------------------------------------------------
    # Alles hier setzt voraus, dass der Nutzer beim Verbinden ausdruecklich
    # "Aendern erlaubt" gewaehlt hat: ohne die erweiterten Rechte antwortet
    # Google mit einem 403, und das ist die richtige Antwort.
    def _write(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._access()}"}
        try:
            response = self._http.request(
                method, url, json=payload, params=params or {}, headers=headers
            )
        except httpx.HTTPError as exc:
            raise GoogleError(f"Google nicht erreichbar: {exc}") from exc
        if response.status_code == 403:
            raise GoogleError(
                "Google verweigert das Schreiben. Wahrscheinlich wurde beim "
                "Verbinden nur Lesen erlaubt -- in den Einstellungen "
                "'Ändern erlaubt' setzen und neu verbinden."
            )
        if response.status_code >= 400:
            raise GoogleError(_explain(response))
        try:
            return dict(response.json())
        except ValueError as exc:
            raise GoogleError("Google hat keine verwertbare Antwort geschickt.") from exc

    def create_event(
        self,
        summary: str,
        start: str,
        end: str = "",
        *,
        description: str = "",
        location: str = "",
        whole_day: bool = False,
    ) -> dict[str, Any]:
        """Legt einen Termin im Hauptkalender an.

        Zeiten kommen als ISO-8601 (`2026-09-08T14:00:00`). Ohne Ende dauert
        der Termin eine Stunde -- das ist die uebliche Annahme und besser als
        eine Fehlermeldung ueber ein fehlendes Feld.
        """
        summary = " ".join(str(summary).split())[:300]
        if not summary:
            raise GoogleError("Ein Termin braucht einen Titel.")
        if not start:
            raise GoogleError("Ein Termin braucht einen Anfang.")
        payload: dict[str, Any] = {"summary": summary}
        if description:
            payload["description"] = str(description)[:4000]
        if location:
            payload["location"] = str(location)[:300]
        payload["start"], payload["end"] = _times(start, end, whole_day)
        data = self._write("POST", f"{CALENDAR_API}/calendars/primary/events", payload)
        return {
            "created": True,
            "id": str(data.get("id", "")),
            "link": str(data.get("htmlLink", "")),
            "event": _event(data),
        }

    def update_event(
        self,
        event_id: str,
        *,
        summary: str = "",
        start: str = "",
        end: str = "",
        description: str = "",
        location: str = "",
        whole_day: bool = False,
    ) -> dict[str, Any]:
        """Aendert einen bestehenden Termin.

        Geaendert wird nur, was mitgegeben wird (PATCH): wer den Titel
        korrigiert, soll nicht versehentlich die Uhrzeit loeschen.
        """
        event_id = str(event_id).strip()
        if not event_id:
            raise GoogleError("Ohne Kennung des Termins geht es nicht.")
        payload: dict[str, Any] = {}
        if summary:
            payload["summary"] = " ".join(str(summary).split())[:300]
        if description:
            payload["description"] = str(description)[:4000]
        if location:
            payload["location"] = str(location)[:300]
        if start:
            payload["start"], payload["end"] = _times(start, end, whole_day)
        elif end:
            payload["end"] = _end_time(end, whole_day)
        if not payload:
            raise GoogleError("Es wurde nichts genannt, was sich aendern soll.")
        data = self._write(
            "PATCH", f"{CALENDAR_API}/calendars/primary/events/{event_id}", payload
        )
        return {
            "updated": True,
            "id": str(data.get("id", "")),
            "link": str(data.get("htmlLink", "")),
            "event": _event(data),
        }

    def create_draft(
        self, to: str, subject: str, body: str, *, cc: str = ""
    ) -> dict[str, Any]:
        """Legt einen Mail-Entwurf an -- verschickt wird nichts.

        Der Entwurf steht danach in Gmail unter "Entwürfe". Ob er hinausgeht,
        entscheidet ein Mensch; Aquaticy hat dafuer gar nicht die Rechte.
        """
        subject = " ".join(str(subject).split())[:300]
        if not subject and not body:
            raise GoogleError("Ein Entwurf ohne Betreff und ohne Text waere leer.")
        kopf = [f"To: {str(to).strip()}"] if to else []
        if cc:
            kopf.append(f"Cc: {str(cc).strip()}")
        kopf += [
            f"Subject: {subject}",
            "Content-Type: text/plain; charset=utf-8",
            "MIME-Version: 1.0",
        ]
        roh = "\r\n".join(kopf) + "\r\n\r\n" + str(body or "")
        codiert = base64.urlsafe_b64encode(roh.encode("utf-8")).decode("ascii")
        data = self._write(
            "POST", f"{GMAIL_API}/drafts", {"message": {"raw": codiert}}
        )
        return {
            "drafted": True,
            "id": str(data.get("id", "")),
            "to": to,
            "subject": subject,
            "note": "Liegt in Gmail unter 'Entwürfe'. Verschickt wurde nichts.",
        }

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._http.close()


# ---------------------------------------------------------------------------
# Aufbereiten
# ---------------------------------------------------------------------------
def _times(start: str, end: str, whole_day: bool) -> tuple[dict[str, str], dict[str, str]]:
    """Macht aus zwei Zeitangaben das, was der Kalender erwartet.

    Ohne Ende wird eine Stunde angenommen -- bei einem ganzen Tag ein Tag.
    Uhrzeiten bekommen immer einen Offset. Google verlangt ihn, wenn kein
    separates ``timeZone``-Feld gesetzt ist; bei einer naiven Eingabe gilt die
    Ortszeit des Rechners.
    """
    roh_start = str(start).strip()
    roh_ende = str(end).strip()
    if whole_day or (len(roh_start) == 10 and "T" not in roh_start):
        tag = roh_start[:10]
        if not roh_ende:
            with contextlib.suppress(ValueError):
                naechster = datetime.fromisoformat(tag) + timedelta(days=1)
                roh_ende = naechster.date().isoformat()
        return ({"date": tag}, {"date": roh_ende[:10] or tag})
    try:
        beginn = _calendar_datetime(roh_start)
        ende = _calendar_datetime(roh_ende) if roh_ende else beginn + timedelta(hours=1)
    except ValueError as exc:
        raise GoogleError(f"Mit '{start}' kann der Kalender nichts anfangen.") from exc
    return ({"dateTime": beginn.isoformat()}, {"dateTime": ende.isoformat()})


def _calendar_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def _end_time(end: str, whole_day: bool) -> dict[str, str]:
    """Formatiert eine einzelne PATCH-Endzeit ohne den Start anzufassen."""
    value = str(end).strip()
    if whole_day:
        try:
            return {"date": datetime.fromisoformat(value[:10]).date().isoformat()}
        except ValueError as exc:
            raise GoogleError(f"Mit '{end}' kann der Kalender nichts anfangen.") from exc
    try:
        return {"dateTime": _calendar_datetime(value).isoformat()}
    except ValueError as exc:
        raise GoogleError(f"Mit '{end}' kann der Kalender nichts anfangen.") from exc


def _event(item: dict[str, Any]) -> dict[str, Any]:
    start = item.get("start") or {}
    end = item.get("end") or {}
    whole_day = "date" in start
    return {
        # Die Kennung braucht, wer einen Termin spaeter aendern will.
        "id": str(item.get("id", "")),
        "summary": str(item.get("summary", "(ohne Titel)")),
        "start": str(start.get("dateTime") or start.get("date") or ""),
        "end": str(end.get("dateTime") or end.get("date") or ""),
        "whole_day": whole_day,
        "location": str(item.get("location", "")),
        "description": str(item.get("description", ""))[:500],
        "organizer": str((item.get("organizer") or {}).get("email", "")),
        "attendees": [
            str(person.get("email", "")) for person in (item.get("attendees") or [])[:10]
        ],
    }


def _body_text(payload: dict[str, Any]) -> str:
    """Sucht den Textteil einer Mail -- lieber Klartext als HTML."""
    plain = _find_part(payload, "text/plain")
    if plain:
        return plain
    html = _find_part(payload, "text/html")
    return _strip_tags(html) if html else ""


def _find_part(part: dict[str, Any], wanted: str) -> str:
    if str(part.get("mimeType", "")).startswith(wanted):
        return _decode(str((part.get("body") or {}).get("data", "")))
    for child in part.get("parts") or []:
        found = _find_part(child, wanted)
        if found:
            return found
    return ""


def _decode(data: str) -> str:
    """Gmail liefert base64url mit fehlendem Padding."""
    if not data:
        return ""
    import binascii

    with contextlib.suppress(ValueError, binascii.Error):
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", "replace")
    return ""


_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t]*\n\s*\n\s*")


def _strip_tags(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = _TAG.sub(" ", text)
    text = _unescape(text)
    return _SPACE.sub("\n\n", text).strip()


def _unescape(text: str) -> str:
    from html import unescape

    return unescape(text).replace("‌", "").replace("\xa0", " ").strip()
