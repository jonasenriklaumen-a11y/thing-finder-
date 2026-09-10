"""Kleine Laeden, die keine Suchmaschine kennt -- aus der Karte.

Das haerteste Suchproblem ist nicht die grosse Frage, sondern die kleine:
der Fahrradladen in der Nebenstrasse, die Werkstatt ohne Website, das Cafe,
das nur auf einem Zettel im Schaufenster wirbt. Suchmaschinen kennen sie
nicht oder erst auf Seite vier, weil niemand fuer sie optimiert.

In OpenStreetMap stehen sie trotzdem -- eingetragen von Leuten vor Ort, mit
Adresse, Oeffnungszeiten, Telefonnummer und, wenn es eine gibt, der Website.
Diese Datei fragt genau das ab: erst den Ort (Nominatim), dann die Umgebung
(Overpass).

**Wie hier mit fremden Diensten umgegangen wird.** Beide Dienste gehoeren
der OpenStreetMap Foundation und sind gespendete Rechenzeit, keine
Selbstbedienung. Ihre Nutzungsregeln stehen nicht nur im Kommentar, sie sind
hier eingebaut:

* **Ein Aufruf pro Sekunde**, dienstweit, ueber ein Schloss -- auch wenn
  vierundvierzig Agenten gleichzeitig fragen.
* **Ehrlicher User-Agent** aus den Einstellungen. Keine Tarnung als Browser.
* **Keine systematischen Abfragen.** Es wird immer genau eine Umgebung zu
  einer Frage eines Menschen abgefragt, nie ein Raster, nie eine Liste aller
  Postleitzahlen. Ohne Ort gibt es kein Ergebnis, keinen Rundumschlag.
* **Enge Grenzen**: ein Umkreis, eine Kategorie, hoechstens `MAX_PLACES`
  Treffer, harte Zeitlimits.

Faellt einer der Dienste aus, ist das kein Fehler, sondern ein leeres
Ergebnis mit Begruendung: die Antwort entsteht dann eben aus dem Web.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

#: Die beiden Dienste. Beide von der OSM Foundation, beide gespendet.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

#: Overpass ist gespendete Rechenzeit und entsprechend oft ausgelastet -- ein
#: Zeitlimit dort heisst nicht, dass es den Laden nicht gibt. Deshalb steht
#: hinter dem Hauptserver ein zweiter, oeffentlich zum Ausweichen gedachter.
#: Der Takt gilt weiter fuer beide zusammen: ausweichen ist kein Freibrief,
#: doppelt so oft zu fragen.
OVERPASS_MIRRORS = (
    OVERPASS_URL,
    "https://overpass.kumi.systems/api/interpreter",
)

#: Hoechstens ein Aufruf je Sekunde -- so steht es in der Nutzungsregel von
#: Nominatim, und Overpass bittet um dasselbe Mass. Das Schloss ist
#: modulweit: es hilft nichts, wenn jeder Agent fuer sich hoeflich ist.
MIN_INTERVAL = 1.05
_takt = threading.Lock()
_zuletzt = 0.0

#: Obergrenzen. Ein Umkreis, eine Kategorie, ueberschaubar viele Treffer.
MAX_PLACES = 30
MAX_RADIUS_M = 15000
DEFAULT_RADIUS_M = 4000

#: Was gesucht wird, in OSM-Sprache. Links das Wort, wie ein Mensch es sagt,
#: rechts der Filter. Ein Wort, das hier nicht steht, wird ueber den Namen
#: gesucht -- auch das findet den "Radladen Meier".
CATEGORIES: dict[str, str] = {
    "cafe": 'nwr[amenity=cafe]',
    "café": 'nwr[amenity=cafe]',
    "kaffee": 'nwr[amenity=cafe]',
    "restaurant": 'nwr[amenity=restaurant]',
    "essen": 'nwr[amenity~"^(restaurant|fast_food|cafe)$"]',
    "imbiss": 'nwr[amenity=fast_food]',
    "bar": 'nwr[amenity~"^(bar|pub)$"]',
    "kneipe": 'nwr[amenity~"^(bar|pub)$"]',
    "baecker": 'nwr[shop=bakery]',
    "bäcker": 'nwr[shop=bakery]',
    "metzger": 'nwr[shop=butcher]',
    "supermarkt": 'nwr[shop~"^(supermarket|convenience)$"]',
    "apotheke": 'nwr[amenity=pharmacy]',
    "arzt": 'nwr[amenity~"^(doctors|clinic)$"]',
    "zahnarzt": 'nwr[amenity=dentist]',
    "friseur": 'nwr[shop=hairdresser]',
    "fahrrad": 'nwr[shop=bicycle]',
    "rad": 'nwr[shop=bicycle]',
    "werkstatt": 'nwr[shop=car_repair]',
    "auto": 'nwr[shop~"^(car|car_repair)$"]',
    "buchladen": 'nwr[shop=books]',
    "buch": 'nwr[shop=books]',
    "blumen": 'nwr[shop=florist]',
    "hotel": 'nwr[tourism~"^(hotel|guest_house|hostel)$"]',
    "pension": 'nwr[tourism~"^(guest_house|hostel)$"]',
    "museum": 'nwr[tourism=museum]',
    "spielplatz": 'nwr[leisure=playground]',
    "park": 'nwr[leisure=park]',
    "schwimmbad": 'nwr[leisure=sports_centre][sport=swimming]',
    "sport": 'nwr[leisure~"^(sports_centre|fitness_centre)$"]',
    "schule": 'nwr[amenity=school]',
    "kindergarten": 'nwr[amenity=kindergarten]',
    "bibliothek": 'nwr[amenity=library]',
    "handwerk": 'nwr[craft]',
    "schreiner": 'nwr[craft~"^(carpenter|joiner)$"]',
    "elektriker": 'nwr[craft=electrician]',
    "maler": 'nwr[craft=painter]',
    "sanitaer": 'nwr[craft=plumber]',
    "baumarkt": 'nwr[shop=doityourself]',
    "kiosk": 'nwr[shop=kiosk]',
    "post": 'nwr[amenity=post_office]',
    "bank": 'nwr[amenity=bank]',
    "tankstelle": 'nwr[amenity=fuel]',
    "werkstatt fahrrad": 'nwr[shop=bicycle]',
}


@dataclass
class Place:
    """Ein Ort, wie ihn die Karte kennt."""

    name: str
    kind: str = ""
    address: str = ""
    website: str = ""
    phone: str = ""
    opening_hours: str = ""
    lat: float = 0.0
    lon: float = 0.0
    tags: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "kind": self.kind,
            "address": self.address,
            "website": self.website,
            "phone": self.phone,
            "opening_hours": self.opening_hours,
        }
        return {name: wert for name, wert in payload.items() if wert}


class PlacesError(RuntimeError):
    """Die Karte hat nicht geantwortet -- kein Grund, die Antwort abzubrechen."""


def _warte() -> None:
    """Haelt den Takt von einem Aufruf je Sekunde ein -- fuer alle Threads."""
    global _zuletzt
    with _takt:
        rest = MIN_INTERVAL - (time.monotonic() - _zuletzt)
        if rest > 0:
            time.sleep(rest)
        _zuletzt = time.monotonic()


def _client(user_agent: str, timeout: float) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        timeout=timeout,
        follow_redirects=True,
    )


def geocode(place: str, user_agent: str, timeout: float = 15.0) -> tuple[float, float, str]:
    """Sucht die Koordinaten eines Ortes.

    Raises:
        PlacesError: Wenn der Ort leer ist oder nichts gefunden wurde.
    """
    place = " ".join((place or "").split())
    if not place:
        raise PlacesError("Ohne Ort gibt es nichts nachzuschlagen.")
    _warte()
    try:
        with _client(user_agent, timeout) as client:
            antwort = client.get(
                NOMINATIM_URL,
                params={"q": place, "format": "jsonv2", "limit": 1, "addressdetails": 0},
            )
            antwort.raise_for_status()
            treffer = antwort.json()
    except Exception as exc:  # httpx-Fehler, JSON-Fehler, alles dasselbe hier
        raise PlacesError(f"Ortssuche fehlgeschlagen: {type(exc).__name__}") from exc
    if not isinstance(treffer, list) or not treffer:
        raise PlacesError(f"Den Ort '{place}' kennt die Karte nicht.")
    erster = treffer[0]
    try:
        return float(erster["lat"]), float(erster["lon"]), str(erster.get("display_name", place))
    except (KeyError, TypeError, ValueError) as exc:
        raise PlacesError("Die Ortssuche gab etwas Unerwartetes zurueck.") from exc


def _filter_for(what: str) -> str:
    """Der Overpass-Filter zu einem Suchwort."""
    text = " ".join((what or "").split()).lower()
    if not text:
        return "nwr[shop]"
    for wort, filter_text in CATEGORIES.items():
        if wort in text:
            return filter_text
    # Nichts Bekanntes: ueber den Namen suchen. Findet den "Radladen Meier"
    # auch dann, wenn er als etwas eingetragen ist, das hier nicht steht.
    sicher = "".join(zeichen for zeichen in text if zeichen.isalnum() or zeichen in " -äöüß")
    sicher = sicher.strip()[:40]
    if not sicher:
        return "nwr[shop]"
    return f'nwr[name~"{sicher}",i]'


def find_places(
    what: str,
    where: str,
    user_agent: str,
    *,
    radius_m: int = DEFAULT_RADIUS_M,
    limit: int = MAX_PLACES,
    timeout: float = 25.0,
) -> tuple[list[Place], str]:
    """Sucht Orte in der Umgebung von *where*.

    Returns:
        Die Treffer und den ausgeschriebenen Ortsnamen.

    Raises:
        PlacesError: Wenn Ort oder Karte nicht antworten.
    """
    radius = max(200, min(int(radius_m or DEFAULT_RADIUS_M), MAX_RADIUS_M))
    limit = max(1, min(int(limit or MAX_PLACES), MAX_PLACES))
    lat, lon, gefunden = geocode(where, user_agent, timeout=min(timeout, 15.0))

    abfrage = (
        f"[out:json][timeout:{int(min(timeout, 25))}];"
        f"{_filter_for(what)}(around:{radius},{lat},{lon});"
        f"out center tags {limit};"
    )
    daten: Any = None
    letzter: Exception | None = None
    for server in OVERPASS_MIRRORS:
        _warte()
        try:
            with _client(user_agent, timeout) as client:
                antwort = client.post(server, data={"data": abfrage})
                antwort.raise_for_status()
                daten = antwort.json()
            break
        except Exception as exc:
            letzter = exc
    if daten is None:
        raise PlacesError(
            f"Die Karte antwortet gerade nicht: {type(letzter).__name__}"
        ) from letzter

    if not isinstance(daten, dict):
        raise PlacesError("Die Karte gab etwas Unerwartetes zurück.")
    elements = daten.get("elements", [])
    if not isinstance(elements, list):
        raise PlacesError("Die Karte gab etwas Unerwartetes zurück.")

    orte: list[Place] = []
    for eintrag in elements:
        if not isinstance(eintrag, dict):
            continue
        raw_tags = eintrag.get("tags") or {}
        if not isinstance(raw_tags, dict):
            continue
        tags = {str(k): str(v) for k, v in raw_tags.items()}
        name = tags.get("name", "").strip()
        if not name:
            continue  # ohne Namen ist ein Punkt auf der Karte keine Auskunft
        mitte = eintrag.get("center") or {}
        if not isinstance(mitte, dict):
            mitte = {}
        try:
            lat = float(eintrag.get("lat") or mitte.get("lat") or 0.0)
            lon = float(eintrag.get("lon") or mitte.get("lon") or 0.0)
        except (TypeError, ValueError):
            lat, lon = 0.0, 0.0
        orte.append(
            Place(
                name=name,
                kind=(
                    tags.get("shop")
                    or tags.get("amenity")
                    or tags.get("craft")
                    or tags.get("tourism")
                    or tags.get("leisure")
                    or ""
                ),
                address=_address(tags),
                website=(tags.get("website") or tags.get("contact:website") or "").strip(),
                phone=(tags.get("phone") or tags.get("contact:phone") or "").strip(),
                opening_hours=tags.get("opening_hours", "").strip(),
                lat=lat,
                lon=lon,
                tags=tags,
            )
        )
        if len(orte) >= limit:
            break
    return orte, gefunden


def _address(tags: dict[str, str]) -> str:
    """Baut aus den Adressfeldern eine Zeile."""
    strasse = " ".join(
        teil for teil in (tags.get("addr:street", ""), tags.get("addr:housenumber", "")) if teil
    ).strip()
    ort = " ".join(
        teil for teil in (tags.get("addr:postcode", ""), tags.get("addr:city", "")) if teil
    ).strip()
    return ", ".join(teil for teil in (strasse, ort) if teil)
