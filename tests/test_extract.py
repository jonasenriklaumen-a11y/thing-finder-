"""Tests fuer die Produktextraktion (JSON-LD, OG, Microdata, Spec-Tabellen)."""

from __future__ import annotations

from selectolax.parser import HTMLParser

from scoutr.extract import (
    extract_product,
    extract_spec_tables,
    has_spec_heading,
    parse_price,
    product_from_jsonld,
)

JSONLD_PAGE = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Testgerät",
 "image":"/bilder/gerät.jpg",
 "offers":{"@type":"Offer","price":"249.99","priceCurrency":"EUR",
           "availability":"https://schema.org/InStock"},
 "aggregateRating":{"ratingValue":"4.3","reviewCount":"51"},
 "brand":{"name":"Testmarke"}}
</script></head><body><h1>Testgerät</h1></body></html>
"""


def test_parse_price() -> None:
    assert parse_price("1.099,00 €") == ("1.099,00", "EUR")
    assert parse_price("249.99") == ("249.99", None)
    assert parse_price("USD 19.90") == ("19.90", "USD")
    assert parse_price(None) == (None, None)
    assert parse_price("") == (None, None)


def test_jsonld_product() -> None:
    product = product_from_jsonld(HTMLParser(JSONLD_PAGE), "https://shop.de/artikel/1")
    assert product is not None
    assert product.name == "Testgerät"
    assert product.price == "249.99"
    assert product.currency == "EUR"
    assert product.rating == 4.3
    assert product.availability == "InStock"
    assert product.specs["Marke"] == "Testmarke"
    assert product.source_domain == "shop.de"


def test_relative_image_urls_become_absolute() -> None:
    product = product_from_jsonld(HTMLParser(JSONLD_PAGE), "https://shop.de/artikel/1")
    assert product is not None
    assert product.image_url == "https://shop.de/bilder/gerät.jpg"


def test_jsonld_inside_graph() -> None:
    html = """<script type="application/ld+json">
    {"@graph":[{"@type":"WebPage"},{"@type":"Product","name":"Im Graph",
     "offers":{"price":"10","priceCurrency":"EUR"}}]}</script>"""
    product = product_from_jsonld(HTMLParser(html), "https://x.de/")
    assert product is not None and product.name == "Im Graph"


def test_broken_jsonld_is_ignored() -> None:
    html = '<script type="application/ld+json">{kaputt,,,}</script>'
    assert product_from_jsonld(HTMLParser(html), "https://x.de/") is None


def test_full_extraction_from_shop_fixture(fixture_html) -> None:
    product = extract_product(
        fixture_html("usercentrics_shop.html"), "https://beispielshop.de/yoga-pro-7"
    )
    assert product is not None
    assert product.name == "Lenovo Yoga Pro 7 14APH8"
    assert product.image_url == "https://cdn.beispielshop.de/yoga-pro-7-1.jpg"
    assert product.rating == 4.6
    assert product.specs["Marke"] == "Lenovo"
    assert product.specs["GTIN"] == "0197532123456"
    # Spec-Tabellen (hier: Definitionsliste) fuellen die Luecken auf.
    assert product.specs["Prozessor"] == "AMD Ryzen 7 8845HS"
    assert product.specs["Arbeitsspeicher"] == "32 GB LPDDR5x-6400"


def test_extraction_without_jsonld_uses_og_and_tables(fixture_html) -> None:
    product = extract_product(
        fixture_html("product_no_jsonld.html"), "https://hersteller.de/x1-carbon"
    )
    assert product is not None
    assert product.name == "ThinkPad X1 Carbon Gen 12"
    assert product.image_url == "https://cdn.hersteller.de/img/x1c-g12.png"
    assert product.specs["CPU"] == "Intel Core Ultra 7 155H"
    # Navigations-Zeilen fliegen raus.
    assert "Cookie-Einstellungen" not in product.specs


def test_article_without_product_signals_returns_none(fixture_html) -> None:
    assert extract_product(fixture_html("plain_article.html"), "https://cafe.de/") is None


def test_spec_tables_and_heading(fixture_html) -> None:
    tree = HTMLParser(fixture_html("cookiebot_article.html"))
    assert has_spec_heading(tree)
    specs = extract_spec_tables(tree)
    assert specs["RAM"] == "32 GB LPDDR5x"
    assert specs["Gewicht"] == "1,49 kg"


def test_spec_table_ignores_rows_with_more_than_two_cells() -> None:
    html = "<table><tr><td>a</td><td>b</td><td>c</td></tr></table>"
    assert extract_spec_tables(HTMLParser(html)) == {}


def test_empty_html_yields_no_product() -> None:
    assert extract_product("", "https://x.de/") is None
