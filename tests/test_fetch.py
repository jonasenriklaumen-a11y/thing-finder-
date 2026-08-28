"""Tests fuer Extraktion, Consent-Erkennung, robots.txt und Rate-Limit."""

from __future__ import annotations

import time

import httpx
import pytest

from scoutr.fetch import (
    MIN_CONTENT_CHARS,
    DomainThrottle,
    Fetcher,
    RobotsPolicy,
    classify_failure,
    drop_consent_paragraphs,
    extract_text,
    load_rules,
    needs_browser,
    page_title,
    strip_noise,
)


# ---------------------------------------------------------------------------
# Stufe 1: Saeuberung und Extraktion
# ---------------------------------------------------------------------------
def test_strip_removes_onetrust_nodes(fixture_html) -> None:
    html = fixture_html("onetrust_article.html")
    cleaned = strip_noise(html)
    assert "onetrust-consent-sdk" not in cleaned
    assert "Alle akzeptieren" not in cleaned
    assert "Café Nordwand" in cleaned


@pytest.mark.parametrize(
    ("fixture", "must_contain", "must_not_contain"),
    [
        ("onetrust_article.html", "Hindenburgstraße 12", "berechtigten Interesses"),
        ("cookiebot_article.html", "Ryzen 7 8845HS", "Ihre Privatsphäre"),
        ("usercentrics_shop.html", "3K-OLED-Display", "Alle akzeptieren"),
    ],
)
def test_consent_banners_do_not_reach_the_text(
    fixture_html, fixture: str, must_contain: str, must_not_contain: str
) -> None:
    text = extract_text(fixture_html(fixture), "https://example.de/")
    assert must_contain in text
    assert must_not_contain not in text
    assert len(text) > MIN_CONTENT_CHARS


def test_drop_consent_paragraphs() -> None:
    text = (
        "Wir verwenden Cookies und ähnliche Technologien. Alle akzeptieren.\n"
        "Das Café liegt an der Hindenburgstraße und hat sonntags von 10 bis 18 Uhr geöffnet."
    )
    cleaned = drop_consent_paragraphs(text)
    assert "Hindenburgstraße" in cleaned
    assert "Cookies" not in cleaned


def test_long_article_keeps_incidental_cookie_mention() -> None:
    paragraph = (
        "Der Betreiber erklärte in einem langen Interview, dass die Website Cookies einsetze, "
        "um Reichweiten zu messen. " + "Weitere Sätze zum eigentlichen Thema folgen. " * 20
    )
    assert "Interview" in drop_consent_paragraphs(paragraph)


def test_page_title(fixture_html) -> None:
    assert "Café Nordwand" in page_title(fixture_html("onetrust_article.html"))


# ---------------------------------------------------------------------------
# Stufe 2: hat die Extraktion geklappt?
# ---------------------------------------------------------------------------
def test_consent_wall_is_detected(fixture_html) -> None:
    html = fixture_html("consent_wall.html")
    text = extract_text(html, "https://example.de/")
    assert classify_failure(html, text) == "consent_required"


def test_paywall_is_detected(fixture_html) -> None:
    html = fixture_html("paywall_article.html")
    text = extract_text(html, "https://example.de/")
    assert classify_failure(html, text) == "paywall"


def test_captcha_is_detected(fixture_html) -> None:
    html = fixture_html("captcha_block.html")
    text = extract_text(html, "https://example.de/")
    assert classify_failure(html, text) == "blocked"


def test_status_403_is_blocked() -> None:
    assert classify_failure("<html></html>", "irgendwas", status_code=403) == "blocked"


def test_good_pages_are_not_flagged(fixture_html) -> None:
    for name in ("onetrust_article.html", "cookiebot_article.html", "plain_article.html"):
        html = fixture_html(name)
        assert classify_failure(html, extract_text(html, "https://x.de/")) == "", name


def test_empty_page_is_empty() -> None:
    assert classify_failure("<html><body></body></html>", "") == "empty"


def test_needs_browser_only_for_consent_and_empty(fixture_html) -> None:
    consent = fixture_html("consent_wall.html")
    assert needs_browser(consent, extract_text(consent, ""))
    paywall = fixture_html("paywall_article.html")
    assert not needs_browser(paywall, extract_text(paywall, ""))


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------
def _client_returning(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_robots_disallow_is_respected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /privat/\n")

    policy = RobotsPolicy(_client_returning(handler), "scoutr/0.1")
    assert policy.allows("https://example.de/oeffentlich")
    assert not policy.allows("https://example.de/privat/geheim")


def test_missing_robots_allows_everything() -> None:
    policy = RobotsPolicy(_client_returning(lambda r: httpx.Response(404)), "scoutr/0.1")
    assert policy.allows("https://example.de/beliebig")


def test_unreachable_robots_allows_everything() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("kein Netz", request=request)

    policy = RobotsPolicy(_client_returning(handler), "scoutr/0.1")
    assert policy.allows("https://example.de/x")


def test_robots_is_fetched_once_per_origin() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow:\n")

    policy = RobotsPolicy(_client_returning(handler), "scoutr/0.1")
    policy.allows("https://example.de/a")
    policy.allows("https://example.de/b")
    assert len(calls) == 1


def test_non_http_urls_are_rejected() -> None:
    policy = RobotsPolicy(_client_returning(lambda r: httpx.Response(404)), "scoutr/0.1")
    assert not policy.allows("ftp://example.de/x")


# ---------------------------------------------------------------------------
# Rate-Limit
# ---------------------------------------------------------------------------
def test_throttle_spaces_requests_per_domain() -> None:
    throttle = DomainThrottle(delay_seconds=0.2)
    start = time.monotonic()
    throttle.wait("a.de")
    throttle.wait("a.de")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.19


def test_throttle_is_per_domain() -> None:
    throttle = DomainThrottle(delay_seconds=0.3)
    start = time.monotonic()
    throttle.wait("a.de")
    throttle.wait("b.de")
    assert time.monotonic() - start < 0.2


# ---------------------------------------------------------------------------
# Fetcher (mit MockTransport)
# ---------------------------------------------------------------------------
def _fetcher(handler, **kwargs) -> Fetcher:
    fetcher = Fetcher(
        user_agent="scoutr-test/0.1",
        timeout=5,
        delay_seconds=0,
        enable_browser=False,
        **kwargs,
    )
    fetcher._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    fetcher.robots = RobotsPolicy(fetcher._client, "scoutr-test/0.1")
    return fetcher


def test_fetch_happy_path(fixture_html) -> None:
    html = fixture_html("onetrust_article.html")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"})

    with _fetcher(handler) as fetcher:
        page = fetcher.fetch("https://cafe-nordwand.de/")
    assert page.ok
    assert page.source_domain == "cafe-nordwand.de"
    assert "Hindenburgstraße 12" in page.text
    assert "Alle akzeptieren" not in page.text
    assert page.word_count > 50


def test_fetch_reports_403_as_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403, text="nope")

    with _fetcher(handler) as fetcher:
        page = fetcher.fetch("https://shop.example/artikel")
    assert not page.ok
    assert page.skipped_reason == "blocked"


def test_fetch_respects_robots() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /geheim")
        raise AssertionError("Seite haette nicht abgerufen werden duerfen")

    with _fetcher(handler) as fetcher:
        page = fetcher.fetch("https://example.de/geheim/seite")
    assert page.skipped_reason == "robots_disallowed"


def test_known_blocking_domain_is_not_even_requested() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("amazon.de haette nicht abgerufen werden duerfen")

    with _fetcher(handler) as fetcher:
        page = fetcher.fetch("https://www.amazon.de/dp/B0TEST")
    assert page.skipped_reason == "blocked"


def test_fetch_skips_non_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"\x89PNG...", headers={"content-type": "image/png"})

    with _fetcher(handler) as fetcher:
        page = fetcher.fetch("https://example.de/bild.png")
    assert page.skipped_reason == "unsupported_content_type"


def test_fetch_handles_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ReadTimeout("zu langsam", request=request)

    with _fetcher(handler) as fetcher:
        page = fetcher.fetch("https://example.de/lahm")
    assert page.skipped_reason == "timeout"


def test_fetch_rejects_invalid_url() -> None:
    with _fetcher(lambda r: httpx.Response(404)) as fetcher:
        assert fetcher.fetch("nicht-mal-eine-url").skipped_reason == "invalid_url"


def test_fetch_extracts_products(fixture_html) -> None:
    html = fixture_html("usercentrics_shop.html")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    with _fetcher(handler) as fetcher:
        page = fetcher.fetch("https://beispielshop.de/yoga-pro-7", want_products=True)
    assert page.ok
    assert page.products
    product = page.products[0]
    assert product.name == "Lenovo Yoga Pro 7 14APH8"
    assert product.price == "1099.00"
    assert product.currency == "EUR"


def test_rules_are_loaded_from_yaml() -> None:
    rules = load_rules()
    assert '#onetrust-consent-sdk' in rules.strip_selectors
    assert "amazon.de" in rules.known_blocking_domains


def test_subdomains_of_known_blockers_are_skipped_too() -> None:
    """m.amazon.de und smile.amazon.de sind genauso zwecklos wie amazon.de."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("haette nicht abgerufen werden duerfen")

    with _fetcher(handler) as fetcher:
        for url in (
            "https://m.amazon.de/dp/B0TEST",
            "https://smile.amazon.de/dp/B0TEST",
            "https://de-de.facebook.com/laden/",
        ):
            assert fetcher.fetch(url).skipped_reason == "blocked", url


def test_lookalike_domains_are_not_blocked(fixture_html) -> None:
    """notamazon.de ist NICHT amazon.de -- nur echte Subdomains zaehlen."""
    html = fixture_html("plain_article.html")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    with _fetcher(handler) as fetcher:
        assert fetcher.fetch("https://notamazon.de/artikel").ok


# ---------------------------------------------------------------------------
# PDF-Abruf
# ---------------------------------------------------------------------------
def _tiny_pdf(text: str) -> bytes:
    """Handgebautes Minimal-PDF mit echter Textebene."""
    import io

    stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode())
        out.write(body)
        out.write(b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF".encode()
    )
    return out.getvalue()


PDF_TEXT = (
    "Technische Daten des Notebooks: Prozessor Ryzen 7 8845HS, 32 GB Arbeitsspeicher, "
    "1 TB SSD, Display 14,5 Zoll OLED. Strassenpreis zum Testzeitpunkt 1099 Euro. "
    "Die Garantie betraegt 24 Monate ab Kaufdatum und umfasst auch den Akku."
)


def test_pdf_content_is_extracted() -> None:
    """Datenblaetter und Preislisten stecken oft in PDFs -- vorher blinder Fleck."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, content=_tiny_pdf(PDF_TEXT), headers={"content-type": "application/pdf"}
        )

    with _fetcher(handler) as fetcher:
        page = fetcher.fetch("https://hersteller.de/datenblatt.pdf")
    assert page.ok, page.skipped_reason
    assert page.via == "pdf"
    assert "Ryzen 7 8845HS" in page.text
    assert "1099 Euro" in page.text


def test_pdf_detected_by_url_suffix_without_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=_tiny_pdf(PDF_TEXT), headers={})

    with _fetcher(handler) as fetcher:
        assert fetcher.fetch("https://x.de/karte.pdf").ok


def test_broken_pdf_is_skipped_cleanly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, content=b"%PDF-1.4 kaputt", headers={"content-type": "application/pdf"}
        )

    with _fetcher(handler) as fetcher:
        page = fetcher.fetch("https://x.de/kaputt.pdf")
    assert not page.ok
    assert page.skipped_reason == "pdf_error"


def test_oversized_pdf_is_skipped() -> None:
    from scoutr import fetch as fetch_mod

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=b"x" * (fetch_mod.MAX_PDF_BYTES + 1),
            headers={"content-type": "application/pdf"},
        )

    with _fetcher(handler) as fetcher:
        assert fetcher.fetch("https://x.de/riesig.pdf").skipped_reason == "pdf_error"
