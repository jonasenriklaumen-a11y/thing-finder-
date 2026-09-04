"""Die Lagerverwaltung im eigenen Netz -- Raeume, Moebel, Artikel.

Angebunden wird das Projekt `storage-system`: eine kleine Web-Anwendung, die
Besitz in drei Ebenen fuehrt -- **Raum -> Moebel/Behaelter -> Artikel** -- und
jedem Artikel eine eindeutige Nummer gibt (`A1`, `B42`). Damit kann Cortex
Fragen beantworten, die im Web nicht stehen koennen: "wo liegt das Ladekabel",
"wie viele Schrauben habe ich noch", "was ist alles im Keller".

**Rechte.** Was Cortex hier darf, steht in den Einstellungen und nur dort:

* ``off``   -- gar nichts. Die Werkzeuge existieren fuer das Modell nicht.
* ``read``  -- suchen, stoebern, zaehlen. Kein Schreibzugriff.
* ``write`` -- zusaetzlich Artikel anlegen und aendern.

Geloescht wird in keiner Stufe. Ein versehentlich angelegter Artikel ist in
zehn Sekunden wieder weg; ein versehentlich geloeschter Raum nimmt alle Moebel
und Artikel darin mit, und das laesst sich nicht rueckgaengig machen. Diese
Entscheidung gehoert einem Menschen vor der Oberflaeche, nicht einem Modell.

**Und eine Ehrlichkeit dazu:** Die Lagerverwaltung selbst kennt keine
Anmeldung -- sie ist fuers Heimnetz gebaut, wer drin ist, darf schreiben. Die
Rechte hier sind also eine Fessel fuer Cortex, kein Schloss am Server. Wer den
Server ueber das Heimnetz hinaus erreichbar macht, braucht davor einen
Reverse Proxy mit Anmeldung.
"""

from __future__ import annotations

import contextlib
from typing import Any
from urllib.parse import urlparse

import httpx

#: Der Port, auf dem die Lagerverwaltung standardmaessig lauscht.
STORAGE_PORT = 3000

#: Womit sich der Server unter /api/config zu erkennen gibt.
APP_ID = "storage-system"

#: Die erlaubten Rechtestufen, von nichts bis alles.
ACCESS_LEVELS = ("off", "read", "write")

REQUEST_TIMEOUT = 10.0

#: Hoechstzahl der Eintraege je Antwort. Ein Lager mit tausend Artikeln wuerde
#: das Kontextfenster sonst allein fuellen.
MAX_ROWS = 50


class StorageError(RuntimeError):
    """Die Lagerverwaltung liess sich nicht erreichen oder lehnte ab."""


class NotAllowed(StorageError):
    """Der Rechtestufe nach ist das hier nicht erlaubt."""


def normalize_url(url: str) -> str:
    """Macht aus "192.168.1.5" oder "lager.local" eine benutzbare Adresse."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = f"http://{url}"
    parsed = urlparse(url)
    if parsed.port is None and parsed.scheme == "http":
        url = f"{url}:{STORAGE_PORT}"
    return url


def normalize_access(value: str) -> str:
    """Unbekanntes wird zu "off" -- im Zweifel lieber nichts duerfen."""
    value = (value or "").strip().lower()
    return value if value in ACCESS_LEVELS else "off"


class Storage:
    """Schmaler Client fuer die REST-Schnittstelle der Lagerverwaltung."""

    def __init__(
        self,
        url: str,
        access: str = "read",
        timeout: float = REQUEST_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = normalize_url(url)
        self.access = normalize_access(access)
        self._http = client or httpx.Client(timeout=timeout, follow_redirects=True)

    # -- Zustand ----------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.url) and self.access != "off"

    @property
    def may_write(self) -> bool:
        return self.access == "write"

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._http.close()

    # -- Grundlagen -------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.url:
            raise StorageError(
                "Es ist keine Lagerverwaltung eingetragen. Der Nutzer traegt sie in den "
                "Einstellungen unter 'Zuhause & Netz' ein."
            )
        if self.access == "off":
            raise NotAllowed("Der Zugriff auf die Lagerverwaltung ist abgeschaltet.")
        if method != "GET" and not self.may_write:
            raise NotAllowed(
                "Cortex darf in der Lagerverwaltung nur lesen. Aendern laesst sich das "
                "in den Einstellungen unter 'Zuhause & Netz'."
            )
        try:
            response = self._http.request(method, f"{self.url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise StorageError(f"Lagerverwaltung nicht erreichbar: {exc}") from exc
        if response.status_code >= 400:
            raise StorageError(_explain(response))
        try:
            return response.json()
        except ValueError as exc:
            raise StorageError(
                "Unter dieser Adresse antwortet etwas anderes als die Lagerverwaltung."
            ) from exc

    def info(self) -> dict[str, Any]:
        """Kennung und Einstellungen des Servers -- der Verbindungstest."""
        data = self._request("GET", "/api/config")
        if not isinstance(data, dict) or data.get("app") != APP_ID:
            raise StorageError(
                "Unter dieser Adresse laeuft keine Lagerverwaltung "
                "(die Kennung unter /api/config fehlt)."
            )
        return data

    # -- Lesen ------------------------------------------------------------
    def rooms(self) -> list[dict[str, Any]]:
        """Alle Raeume mit der Zahl ihrer Moebel und Artikel."""
        return _rows(self._request("GET", "/api/rooms"))

    def furniture(self, room_id: int) -> list[dict[str, Any]]:
        """Die Moebel eines Raums."""
        return _rows(self._request("GET", f"/api/rooms/{int(room_id)}/furniture"))

    def items(self, furniture_id: int) -> list[dict[str, Any]]:
        """Die Artikel eines Moebels, nach Nummer sortiert."""
        return _rows(self._request("GET", f"/api/furniture/{int(furniture_id)}/items"))

    def item(self, item_id: int) -> dict[str, Any]:
        """Ein einzelner Artikel mit vollem Pfad."""
        return _clean(self._request("GET", f"/api/items/{int(item_id)}"))

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Sucht in Artikelnummern und -namen; Treffer nennen ihren Pfad."""
        query = (query or "").strip()
        if not query:
            return []
        data = self._request(
            "GET",
            "/api/search",
            params={"q": query, "limit": max(1, min(int(limit or 20), MAX_ROWS))},
        )
        return _rows(data.get("results") if isinstance(data, dict) else data)

    # -- Schreiben --------------------------------------------------------
    def add_item(self, furniture_id: int, name: str, quantity: int = 1) -> dict[str, Any]:
        """Legt einen Artikel an. Die Nummer vergibt der Server."""
        name = (name or "").strip()
        if not name:
            raise StorageError("Ein Artikel braucht einen Namen.")
        return _clean(
            self._request(
                "POST",
                f"/api/furniture/{int(furniture_id)}/items",
                json={"name": name, "quantity": max(0, int(quantity))},
            )
        )

    def update_item(
        self, item_id: int, name: str = "", quantity: int | None = None
    ) -> dict[str, Any]:
        """Aendert Namen und/oder Bestand eines Artikels."""
        payload: dict[str, Any] = {}
        if (name or "").strip():
            payload["name"] = name.strip()
        if quantity is not None:
            payload["quantity"] = max(0, int(quantity))
        if not payload:
            raise StorageError("Es wurde nichts zum Aendern angegeben.")
        return _clean(self._request("PATCH", f"/api/items/{int(item_id)}", json=payload))

    def change_quantity(self, item_id: int, delta: int) -> dict[str, Any]:
        """Zaehlt den Bestand hoch oder runter -- ohne den alten Wert zu kennen.

        Das ist nicht dasselbe wie `update_item`: zwei Leute, die gleichzeitig
        eine Schraube entnehmen, kommen hier auf zwei weniger. Mit gesetztem
        Wert wuerde einer den anderen ueberschreiben.
        """
        delta = int(delta)
        if delta == 0:
            raise StorageError("Eine Aenderung um null ist keine.")
        return _clean(
            self._request("POST", f"/api/items/{int(item_id)}/quantity", json={"delta": delta})
        )

    def add_room(self, name: str) -> dict[str, Any]:
        """Legt einen Raum an."""
        name = (name or "").strip()
        if not name:
            raise StorageError("Ein Raum braucht einen Namen.")
        return _clean(self._request("POST", "/api/rooms", json={"name": name}))

    def add_furniture(self, room_id: int, name: str) -> dict[str, Any]:
        """Legt ein Moebel an. Das Nummernpraefix vergibt der Server."""
        name = (name or "").strip()
        if not name:
            raise StorageError("Ein Moebel braucht einen Namen.")
        return _clean(
            self._request("POST", f"/api/rooms/{int(room_id)}/furniture", json={"name": name})
        )


# ---------------------------------------------------------------------------
# Aufbereiten
# ---------------------------------------------------------------------------
#: Felder, die das Modell nicht braucht. Bilddateinamen und Zeitstempel
#: fuellen nur das Kontextfenster.
DROP_FIELDS = frozenset({"imageFile", "createdAt", "updatedAt", "prefix", "seq"})


def _clean(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return {key: value for key, value in row.items() if key not in DROP_FIELDS}


def _rows(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    return [_clean(row) for row in data[:MAX_ROWS] if isinstance(row, dict)]


def _explain(response: httpx.Response) -> str:
    """Aus einer Fehlerantwort einen Satz machen, der weiterhilft."""
    detail = ""
    with contextlib.suppress(ValueError):
        body = response.json()
        if isinstance(body, dict):
            detail = str(body.get("error") or "")
    if response.status_code == 404:
        return detail or "Nicht gefunden -- Raum, Moebel oder Artikel gibt es nicht."
    if response.status_code == 400:
        return detail or "Die Angaben waren unvollstaendig oder unzulaessig."
    return f"Lagerverwaltung antwortet mit {response.status_code}: {detail or 'kein Grund genannt'}"


# ---------------------------------------------------------------------------
# Im Netz finden
# ---------------------------------------------------------------------------
def discover(subnet: str = "", timeout: float = 1.0) -> list[str]:
    """Sucht die Lagerverwaltung im eigenen Netz.

    Gefunden wird nicht "irgendwas auf Port 3000" -- jeder Kandidat wird
    gefragt, ob er sich als Lagerverwaltung zu erkennen gibt. Auf Port 3000
    laeuft in vielen Haushalten irgendein anderer Entwicklungsserver.
    """
    from cortex.lan import NotPrivate, find_port

    found: list[str] = []
    try:
        candidates = find_port(STORAGE_PORT, subnet)
    except (NotPrivate, ValueError):
        return found

    for address in candidates:
        url = f"http://{address}:{STORAGE_PORT}"
        client = Storage(url, access="read", timeout=timeout)
        try:
            client.info()
        except StorageError:
            continue
        finally:
            client.close()
        found.append(url)
    return found
