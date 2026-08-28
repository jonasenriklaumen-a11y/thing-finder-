"""Seitenabruf: HTTP holen, saeubern, Text extrahieren.

Aufbau der Cookie-/Popup-Behandlung:

* **Stufe 1** -- reines HTTP ohne JavaScript. Consent-Banner sind dann meist
  gar nicht im DOM; was doch drin ist, entfernen wir per Selektor-Blockliste
  aus :mod:`scoutr.selectors` (`selectors.yaml`).
* **Stufe 2** -- pruefen, ob die Extraktion geklappt hat. Bleibt zu wenig Text
  uebrig und stehen Consent-Marker im HTML, gilt die Seite als Consent-Wall.
* **Stufe 3** -- optionaler Playwright-Fallback in :mod:`scoutr.browser`.

Gesperrte Inhalte (Paywall, Login, Captcha) werden **nie** umgangen.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
import yaml
from selectolax.parser import HTMLParser

from scoutr.extract import extract_product, has_spec_heading
from scoutr.models import PageResult, domain_of

SELECTORS_PATH = Path(__file__).with_name("selectors.yaml")

#: Ab wie vielen Zeichen gilt ein Text als echter Inhalt?
MIN_CONTENT_CHARS = 200
#: Wie viel Text bekommt der Agent maximal pro Seite?
MAX_TEXT_CHARS = 20_000
#: Wie viel HTML laden wir maximal herunter?
MAX_HTML_BYTES = 4_000_000
#: Obergrenze fuer PDFs -- Datenblaetter sind klein, Scans riesig.
MAX_PDF_BYTES = 25_000_000
#: Mehr Seiten liest niemand am Stueck; haelt auch pypdf im Zaum.
MAX_PDF_PAGES = 40


@dataclass(slots=True)
class SiteRules:
    """Inhalt der `selectors.yaml`."""

    strip_selectors: list[str] = field(default_factory=list)
    consent_phrases: list[str] = field(default_factory=list)
    consent_wall_markers: list[str] = field(default_factory=list)
    blocked_markers: list[str] = field(default_factory=list)
    paywall_markers: list[str] = field(default_factory=list)
    known_blocking_domains: list[str] = field(default_factory=list)
    # Stufe 3 (Playwright)
    cmp_reject_selectors: list[str] = field(default_factory=list)
    reject_text_pattern: str = ""
    overlay_remove_selectors: list[str] = field(default_factory=list)


@lru_cache(maxsize=4)
def load_rules(path: str | None = None) -> SiteRules:
    """Laedt `selectors.yaml` (gecacht)."""
    target = Path(path) if path else SELECTORS_PATH
    data: dict[str, list[str]] = {}
    if target.is_file():
        loaded = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    return SiteRules(
        strip_selectors=list(data.get("strip_selectors") or []),
        consent_phrases=[phrase.lower() for phrase in data.get("consent_phrases") or []],
        consent_wall_markers=[m.lower() for m in data.get("consent_wall_markers") or []],
        blocked_markers=[m.lower() for m in data.get("blocked_markers") or []],
        paywall_markers=[m.lower() for m in data.get("paywall_markers") or []],
        known_blocking_domains=[d.lower() for d in data.get("known_blocking_domains") or []],
        cmp_reject_selectors=list(data.get("cmp_reject_selectors") or []),
        reject_text_pattern=str(data.get("reject_text_pattern") or ""),
        overlay_remove_selectors=list(data.get("overlay_remove_selectors") or []),
    )


# ---------------------------------------------------------------------------
# Stufe 1: saeubern und extrahieren
# ---------------------------------------------------------------------------
def strip_noise(html: str, rules: SiteRules | None = None) -> str:
    """Entfernt Consent-Banner, Overlays und Navigationsballast aus *html*."""
    rules = rules or load_rules()
    tree = HTMLParser(html)
    for selector in rules.strip_selectors:
        try:
            nodes = tree.css(selector)
        except Exception:  # ungueltiger Selektor in der YAML -- ueberspringen
            continue
        for node in nodes:
            node.decompose()
    return tree.html or ""


def _is_consent_paragraph(paragraph: str, phrases: list[str]) -> bool:
    """Besteht der Absatz im Wesentlichen aus Consent-Formulierungen?"""
    lowered = paragraph.lower()
    hits = sum(1 for phrase in phrases if phrase in lowered)
    if hits == 0:
        return False
    # Lange Fliesstext-Absaetze mit einer beilaeufigen Erwaehnung behalten wir.
    return hits >= 2 or len(paragraph) < 400


def drop_consent_paragraphs(text: str, rules: SiteRules | None = None) -> str:
    """Streicht Absaetze, die nur Cookie-Hinweise enthalten."""
    rules = rules or load_rules()
    if not text:
        return text
    kept = [
        paragraph
        for paragraph in text.split("\n")
        if not _is_consent_paragraph(paragraph, rules.consent_phrases)
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def extract_text(html: str, url: str = "", rules: SiteRules | None = None) -> str:
    """Stufe 1 komplett: saeubern, trafilatura, Consent-Absaetze streichen."""
    rules = rules or load_rules()
    if not html:
        return ""
    cleaned = strip_noise(html, rules)
    text = (
        trafilatura.extract(
            cleaned,
            url=url or None,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
            no_fallback=False,
        )
        or ""
    )
    if not text.strip():
        # trafilatura gibt bei sehr kurzen Seiten gern nichts zurueck.
        text = HTMLParser(cleaned).text(separator="\n", strip=True) or ""
    return drop_consent_paragraphs(text, rules)


def extract_pdf_text(data: bytes) -> tuple[str, str]:
    """Zieht (Text, Titel) aus PDF-Bytes.

    Datenblaetter, Speisekarten und Preislisten stecken oft in PDFs --
    vorher war jedes davon ein blinder Fleck. Gescannte PDFs ohne Textebene
    liefern leeren Text; OCR machen wir bewusst nicht.

    Returns:
        ("", "") wenn nichts lesbar ist.
    """
    import io

    try:
        from pypdf import PdfReader
    except ImportError:
        return "", ""
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            pages.append(page.extract_text() or "")
        text = "\n\n".join(part.strip() for part in pages if part.strip())
        title = ""
        metadata = reader.metadata
        if metadata and metadata.title:
            title = str(metadata.title)
        return text.strip(), title.strip()
    except Exception:
        # pypdf wirft je nach Datei alles Moegliche -- ein kaputtes PDF ist
        # ein uebersprungener Treffer, kein Absturz.
        return "", ""


def page_title(html: str) -> str:
    """Titel der Seite (aus `<title>` oder `og:title`)."""
    tree = HTMLParser(html)
    node = tree.css_first("title")
    title = node.text(strip=True) if node else ""
    if not title:
        meta = tree.css_first('meta[property="og:title"]')
        title = (meta.attributes.get("content") or "").strip() if meta else ""
    return re.sub(r"\s+", " ", title)[:200]


# ---------------------------------------------------------------------------
# Stufe 2: hat es geklappt?
# ---------------------------------------------------------------------------
def _contains_any(haystack: str, needles: list[str]) -> str:
    for needle in needles:
        if needle in haystack:
            return needle
    return ""


def classify_failure(
    html: str, text: str, status_code: int | None = None, rules: SiteRules | None = None
) -> str:
    """Entscheidet, ob und warum ein Abruf als gescheitert gilt.

    Returns:
        Leerer String, wenn alles in Ordnung ist, sonst einer der Gruende
        `blocked`, `paywall`, `consent_required`, `empty`.
    """
    rules = rules or load_rules()
    html_lower = html.lower()
    text_lower = text.lower()

    if status_code in (401, 402, 403, 407, 429):
        return "blocked"
    if _contains_any(html_lower[:200_000], rules.blocked_markers):
        return "blocked"

    enough_text = len(text.strip()) >= MIN_CONTENT_CHARS
    if not enough_text:
        # Reihenfolge mit Bedacht: "Zustimmen oder Abo" ist eine Consent-Wall,
        # keine Bezahlschranke.
        if _contains_any(html_lower[:200_000], rules.consent_wall_markers) or _contains_any(
            text_lower, rules.consent_phrases
        ):
            return "consent_required"
        # Der Paywall-Hinweis steckt oft in einem Knoten, den Stufe 1 bereits
        # entfernt hat -- deshalb hier im Roh-HTML nachsehen.
        if _contains_any(text_lower, rules.paywall_markers) or _contains_any(
            html_lower[:200_000], rules.paywall_markers
        ):
            return "paywall"
        return "empty"

    # Genug Text -- aber besteht er nur aus Consent-Geschwaetz?
    consent_hits = sum(1 for phrase in rules.consent_phrases if phrase in text_lower)
    if consent_hits >= 3 and len(text.strip()) < 1200:
        return "consent_required"

    # Paywall-Teaser: kurzer Text plus eindeutiger Abo-Hinweis.
    if len(text.strip()) < 900 and _contains_any(text_lower, rules.paywall_markers):
        return "paywall"

    return ""


def needs_browser(html: str, text: str, rules: SiteRules | None = None) -> bool:
    """Wuerde ein JavaScript-Rendering (Stufe 3) hier vermutlich helfen?"""
    reason = classify_failure(html, text, None, rules)
    return reason in {"consent_required", "empty"}


# ---------------------------------------------------------------------------
# robots.txt und Rate-Limit
# ---------------------------------------------------------------------------
class RobotsPolicy:
    """Fragt und cached `robots.txt` je Origin."""

    def __init__(self, client: httpx.Client, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def allows(self, url: str) -> bool:
        """Darf *url* laut robots.txt abgerufen werden? Im Zweifel: ja."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            if origin not in self._parsers:
                self._parsers[origin] = self._load(origin)
            parser = self._parsers[origin]
        if parser is None:
            return True
        return parser.can_fetch(self._user_agent, url)

    def _load(self, origin: str) -> urllib.robotparser.RobotFileParser | None:
        try:
            response = self._client.get(urljoin(origin, "/robots.txt"), timeout=10)
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            # Keine robots.txt -> alles erlaubt.
            return None
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser


class DomainThrottle:
    """Sorgt fuer maximal einen Request pro Sekunde und Domain."""

    def __init__(self, delay_seconds: float = 1.0) -> None:
        self.delay = max(0.0, delay_seconds)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, domain: str) -> float:
        """Blockiert, bis der naechste Request erlaubt ist; gibt die Wartezeit zurueck."""
        if self.delay <= 0 or not domain:
            return 0.0
        with self._lock:
            now = time.monotonic()
            earliest = self._last.get(domain, 0.0) + self.delay
            sleep_for = max(0.0, earliest - now)
            self._last[domain] = now + sleep_for
        if sleep_for:
            time.sleep(sleep_for)
        return sleep_for


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------
HTML_CONTENT_TYPES = ("text/html", "application/xhtml", "text/plain", "application/xml")


class Fetcher:
    """Holt Seiten -- hoeflich, mit Cookie-Jar im Speicher und ohne Umgehungen."""

    def __init__(
        self,
        user_agent: str,
        timeout: float = 15.0,
        delay_seconds: float = 1.0,
        rules: SiteRules | None = None,
        respect_robots: bool = True,
        enable_browser: bool = True,
    ) -> None:
        self.rules = rules or load_rules()
        self.user_agent = user_agent
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.enable_browser = enable_browser
        # Cookies bleiben nur im Speicher -- nichts landet auf der Platte.
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.6",
                "DNT": "1",
                "Sec-GPC": "1",
            },
            timeout=timeout,
            follow_redirects=True,
            max_redirects=5,
        )
        self.robots = RobotsPolicy(self._client, user_agent)
        self.throttle = DomainThrottle(delay_seconds)

    # -- Lebenszyklus -----------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- Abruf ------------------------------------------------------------
    def fetch(self, url: str, *, want_products: bool = False) -> PageResult:
        """Laedt *url* und gibt lesbaren Text (und optional Produktdaten) zurueck."""
        url = (url or "").strip()
        domain = domain_of(url)
        result = PageResult(url=url, source_domain=domain)

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            result.skipped_reason = "invalid_url"
            return result

        if any(
            domain == blocked or domain.endswith(f".{blocked}")
            for blocked in self.rules.known_blocking_domains
        ):
            # Bekannte Blocker gar nicht erst behelligen -- auch ihre
            # Subdomains (m.amazon.de, smile.amazon.de) laufen ins Leere.
            result.skipped_reason = "blocked"
            return result

        if self.respect_robots and not self.robots.allows(url):
            result.skipped_reason = "robots_disallowed"
            return result

        self.throttle.wait(domain)

        try:
            response = self._client.get(url)
        except httpx.TimeoutException:
            result.skipped_reason = "timeout"
            return result
        except httpx.HTTPError:
            result.skipped_reason = "network_error"
            return result

        result.status_code = response.status_code
        result.final_url = str(response.url)
        result.source_domain = domain_of(result.final_url) or domain

        if response.status_code in (401, 402, 403, 407, 429):
            result.skipped_reason = "blocked"
            return result
        if response.status_code >= 400:
            result.skipped_reason = "http_error"
            return result

        content_type = response.headers.get("content-type", "").lower()
        final_path = urlparse(result.final_url or url).path.lower()
        if "application/pdf" in content_type or final_path.endswith(".pdf"):
            return self._finish_pdf(result, response.content)
        if content_type and not any(kind in content_type for kind in HTML_CONTENT_TYPES):
            result.skipped_reason = "unsupported_content_type"
            return result

        html = response.text[:MAX_HTML_BYTES]
        return self._finish(result, html, want_products=want_products)

    def _finish_pdf(self, result: PageResult, data: bytes) -> PageResult:
        """Text aus einem PDF ziehen -- Datenblaetter, Speisekarten, Preislisten."""
        if len(data) > MAX_PDF_BYTES:
            result.skipped_reason = "pdf_error"
            return result
        text, title = extract_pdf_text(data)
        if len(text.strip()) < MIN_CONTENT_CHARS:
            result.skipped_reason = "pdf_error" if not text.strip() else "empty"
            return result
        result.ok = True
        result.via = "pdf"
        result.title = title or Path(urlparse(result.final_url or result.url).path).name
        result.truncated = len(text) > MAX_TEXT_CHARS
        result.text = text[:MAX_TEXT_CHARS]
        return result

    def _finish(self, result: PageResult, html: str, *, want_products: bool) -> PageResult:
        """Stufe 1 + 2 auf bereits geladenes HTML anwenden."""
        text = extract_text(html, result.final_url or result.url, self.rules)
        reason = classify_failure(html, text, result.status_code, self.rules)

        if reason and self.enable_browser and reason in {"consent_required", "empty"}:
            rendered = self._try_browser(result.final_url or result.url)
            if rendered:
                html = rendered
                text = extract_text(html, result.final_url or result.url, self.rules)
                reason = classify_failure(html, text, None, self.rules)
                result.via = "browser"

        if reason:
            result.skipped_reason = reason
            return result

        result.ok = True
        result.title = page_title(html)
        result.truncated = len(text) > MAX_TEXT_CHARS
        result.text = text[:MAX_TEXT_CHARS]

        if want_products:
            product = extract_product(html, result.final_url or result.url)
            if product is not None:
                result.products = [product]
                result.product_hint = True
            elif has_spec_heading(HTMLParser(html)):
                # Kein strukturierter Treffer, aber eine "Technische Daten"-Ueberschrift:
                # hier lohnt sich der LLM-Fallback.
                result.product_hint = True
        return result

    def _try_browser(self, url: str) -> str | None:
        """Stufe 3 -- nur wenn Playwright installiert ist."""
        try:
            from scoutr.browser import render_page
        except ImportError:
            return None
        try:
            return render_page(url, user_agent=self.user_agent, timeout=self.timeout)
        except Exception:
            # Der Browser-Fallback darf den normalen Ablauf nie sprengen.
            return None
