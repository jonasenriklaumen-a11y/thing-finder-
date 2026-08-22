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
                "note": SKIP_NOTES.get(self.skipped_reason, "Seite konnte nicht gelesen werden."),
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


SKIP_NOTES: dict[str, str] = {
    "blocked": (
        "Die Seite blockiert automatisierte Abrufe (403/Captcha). "
        "Nutze den Suchtreffer nur als Link und hole die Fakten aus einer anderen Quelle."
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
    "unsupported_content_type": "Kein HTML-Dokument (z.B. PDF, Bild, Video).",
    "network_error": "Netzwerkfehler beim Abruf.",
    "invalid_url": "Die URL ist ungueltig oder verwendet kein http(s).",
}
