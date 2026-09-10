"""Datenmodelle, die zwischen Tools, Agent und Ausgabe wandern."""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field


def domain_of(url: str) -> str:
    """`https://www.cafe-nordwand.de/x?y=1` -> `cafe-nordwand.de`."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


class SearchResult(BaseModel):
    """Ein einzelner Suchtreffer."""

    title: str = ""
    url: str = ""
    snippet: str = ""
    source_domain: str = ""
    rank: int = 0

    def as_tool_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "domain": self.source_domain,
        }


class Product(BaseModel):
    """Strukturierte Produktdaten einer Seite."""

    name: str
    url: str
    image_url: str | None = None
    price: str | None = None
    currency: str | None = None
    rating: float | None = None
    specs: dict[str, str] = Field(default_factory=dict)
    availability: str | None = None
    source_domain: str = ""

    def price_display(self) -> str:
        if not self.price:
            return "–"
        symbol = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF"}.get(
            (self.currency or "").upper(), self.currency or ""
        )
        return f"{self.price} {symbol}".strip()


class PageResult(BaseModel):
    """Ergebnis eines Seitenabrufs -- egal ob erfolgreich oder uebersprungen."""

    url: str
    final_url: str = ""
    ok: bool = False
    status_code: int | None = None
    title: str = ""
    text: str = ""
    #: Warum wurde die Seite uebersprungen? z.B. `blocked`, `consent_required`,
    #: `paywall`, `robots_disallowed`, `timeout`, `empty`
    skipped_reason: str = ""
    #: Wie kam der Inhalt zustande: `http`, `browser`, `cache`
    via: str = "http"
    source_domain: str = ""
    products: list[Product] = Field(default_factory=list)
    truncated: bool = False
    #: Sieht die Seite ueberhaupt nach einem Produkt aus? Steuert den LLM-Fallback.
    product_hint: bool = False

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def as_tool_dict(self, max_chars: int = 12000) -> dict[str, object]:
        """Kompakte Darstellung fuer das LLM."""
        if not self.ok:
            return {
                "url": self.url,
                "ok": False,
                "skipped_reason": self.skipped_reason,
                "status_code": self.status_code,
                "note": skip_note(self.skipped_reason),
            }
        text = self.text[:max_chars]
        payload: dict[str, object] = {
            "url": self.final_url or self.url,
            "ok": True,
            "title": self.title,
            "domain": self.source_domain,
            "via": self.via,
            "word_count": self.word_count,
            "text": text,
            "truncated": self.truncated or len(self.text) > max_chars,
        }
        if self.products:
            payload["products"] = [
                product.model_dump(exclude_none=True) for product in self.products
            ]
        return payload


#: Was der Server mit einem Abweisungsstatus eigentlich sagt. Die Nummer ist
#: der Unterschied zwischen "melde dich an" und "du fragst zu oft" -- und
#: davon haengt ab, ob es sich lohnt, es spaeter noch einmal zu versuchen.
HTTP_REFUSALS: dict[int, str] = {
    401: "verlangt eine Anmeldung (401)",
    402: "verlangt eine Bezahlung (402)",
    403: "weist automatisierte Abrufe ab (403)",
    407: "verlangt eine Anmeldung am Netzuebergang (407)",
    429: "hat zu viele Abrufe gesehen und bremst (429)",
}

SKIP_NOTES: dict[str, str] = {
    "blocked_by_list": (
        "Diese Domain steht auf der Liste bekannter Blocker -- sie wird gar nicht "
        "erst abgerufen. Nutze den Suchtreffer nur als Link."
    ),
    "blocked_bot_wall": (
        "Auf der Seite steht eine Bot-Pruefung (Captcha oder Browser-Check). "
        "Die wird nicht geloest -- nimm eine andere Quelle."
    ),
    "consent_required": (
        "Die Seite liefert ohne Cookie-Zustimmung keinen Inhalt (Consent-Wall). "
        "Das wird nicht umgangen -- nimm eine andere Quelle."
    ),
    "paywall": (
        "Inhalt liegt hinter einer Bezahl- oder Loginschranke -- nicht oeffentlich zugaenglich."
    ),
    "robots_disallowed": "robots.txt der Domain verbietet das Abrufen dieser URL.",
    "timeout": "Zeitueberschreitung beim Abruf.",
    "http_error": "Server hat mit einem Fehlerstatus geantwortet.",
    "empty": "Kein lesbarer Textinhalt gefunden.",
    "unsupported_content_type": "Kein lesbares Dokument (z.B. Bild, Video, Archiv).",
    "pdf_error": (
        "Das PDF liess sich nicht lesen (beschaedigt, gescannt ohne Textebene, oder zu gross)."
    ),
    "network_error": "Netzwerkfehler beim Abruf.",
    "invalid_url": "Die URL ist ungueltig oder verwendet kein http(s).",
}


def skip_note(reason: str) -> str:
    """Der Klartext zu einem Abbruchgrund -- auch fuer die HTTP-Nummern."""
    if reason in SKIP_NOTES:
        return SKIP_NOTES[reason]
    if reason.startswith("blocked_http_"):
        code = reason.rsplit("_", 1)[-1]
        wie = HTTP_REFUSALS.get(int(code) if code.isdigit() else 0, f"lehnt ab ({code})")
        return (
            f"Die Seite {wie}. Das liegt an ihr, nicht an der Adresse: nutze den "
            "Treffer als Link und hol die Fakten aus einer anderen Quelle."
        )
    return "Seite konnte nicht gelesen werden."
