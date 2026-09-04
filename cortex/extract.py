"""Strukturierte Produktdaten aus HTML ziehen.

Reihenfolge der Quellen (die erste, die etwas liefert, gewinnt -- fehlende
Felder werden aus den nachfolgenden Quellen aufgefuellt):

1. JSON-LD (`<script type="application/ld+json">`, `@type: Product`)
2. Open-Graph-Tags (`og:image`, `og:title`, `product:price:amount`)
3. Microdata (`itemprop="image"`, `itemprop="price"`)
4. Spec-Tabellen (`<table>` / `<dl>` unter "Technische Daten" o.ae.)

Ein LLM-Fallback existiert zusaetzlich in :mod:`cortex.tools`, wenn hier
nichts herauskommt.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from cortex.models import Product, domain_of

#: Ueberschriften, unter denen typischerweise technische Daten stehen.
SPEC_HEADINGS = (
    "technische daten",
    "technische details",
    "spezifikation",
    "specifications",
    "specs",
    "tech specs",
    "datenblatt",
    "produktdetails",
    "product details",
    "eigenschaften",
    "merkmale",
    "ausstattung",
)

#: Zeilen mit diesen Schluesseln sind Navigations- statt Produktdaten.
SPEC_KEY_BLOCKLIST = ("cookie", "newsletter", "datenschutz", "impressum", "versand nach")

MAX_SPECS = 40
MAX_SPEC_VALUE_LEN = 160

_PRICE_RE = re.compile(r"(\d[\d.\s]*(?:[.,]\d{1,2})?)")
_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "chf": "CHF", "fr.": "CHF"}


def _text(node: Node | None) -> str:
    return re.sub(r"\s+", " ", node.text(deep=True, strip=True)).strip() if node else ""


def _absolute(url: str | None, base_url: str) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url or url.startswith(("data:", "javascript:")):
        return None
    return urljoin(base_url, url)


def parse_price(raw: Any) -> tuple[str | None, str | None]:
    """Trennt `"1.099,00 €"` in `("1.099,00", "EUR")`."""
    if raw is None:
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None

    currency: str | None = None
    lowered = text.lower()
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in lowered:
            currency = code
            break
    if currency is None:
        match = re.search(r"\b(EUR|USD|GBP|CHF|PLN|SEK|DKK)\b", text, re.IGNORECASE)
        if match:
            currency = match.group(1).upper()

    price_match = _PRICE_RE.search(text.replace(" ", " "))
    price = price_match.group(1).strip() if price_match else None
    return price, currency


def _as_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 1. JSON-LD
# ---------------------------------------------------------------------------
def _iter_jsonld_objects(tree: HTMLParser) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text(deep=True, strip=False) or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Manche Seiten haengen mehrere JSON-Objekte hintereinander.
            try:
                data = json.loads(f"[{raw.strip().rstrip(',')}]")
            except json.JSONDecodeError:
                continue
        _flatten_jsonld(data, objects)
    return objects


def _flatten_jsonld(data: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(data, list):
        for item in data:
            _flatten_jsonld(item, out)
    elif isinstance(data, dict):
        out.append(data)
        for key in ("@graph", "mainEntity", "itemListElement", "hasVariant"):
            if key in data:
                _flatten_jsonld(data[key], out)


def _is_product(obj: dict[str, Any]) -> bool:
    type_field = obj.get("@type")
    types = type_field if isinstance(type_field, list) else [type_field]
    return any(str(t).lower() in {"product", "productmodel", "individualproduct"} for t in types)


def _first_image(value: Any, base_url: str) -> str | None:
    if isinstance(value, str):
        return _absolute(value, base_url)
    if isinstance(value, list) and value:
        return _first_image(value[0], base_url)
    if isinstance(value, dict):
        return _first_image(value.get("url") or value.get("contentUrl"), base_url)
    return None


def _offer_fields(obj: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    offers = obj.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return None, None, None
    price, currency = parse_price(offers.get("price") or offers.get("lowPrice"))
    currency = offers.get("priceCurrency") or currency
    availability = offers.get("availability")
    if isinstance(availability, str):
        availability = availability.rsplit("/", 1)[-1]
    return price, (currency or None), (availability or None)


def product_from_jsonld(tree: HTMLParser, base_url: str) -> Product | None:
    """Baut ein :class:`Product` aus dem ersten JSON-LD-Produktobjekt."""
    for obj in _iter_jsonld_objects(tree):
        if not _is_product(obj):
            continue
        name = str(obj.get("name") or "").strip()
        if not name:
            continue
        price, currency, availability = _offer_fields(obj)
        rating = None
        aggregate = obj.get("aggregateRating")
        if isinstance(aggregate, dict):
            rating = _as_float(aggregate.get("ratingValue"))

        specs: dict[str, str] = {}
        brand = obj.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        if brand:
            specs["Marke"] = str(brand)
        for key, label in (("gtin13", "GTIN"), ("gtin", "GTIN"), ("sku", "SKU"), ("mpn", "MPN")):
            if obj.get(key):
                specs.setdefault(label, str(obj[key]))
        for prop in obj.get("additionalProperty") or []:
            if isinstance(prop, dict) and prop.get("name"):
                specs[str(prop["name"])[:80]] = str(prop.get("value", ""))[:MAX_SPEC_VALUE_LEN]

        return Product(
            name=name,
            url=base_url,
            image_url=_first_image(obj.get("image"), base_url),
            price=price,
            currency=currency,
            rating=rating,
            specs=specs,
            availability=availability,
            source_domain=domain_of(base_url),
        )
    return None


# ---------------------------------------------------------------------------
# 2. Open Graph
# ---------------------------------------------------------------------------
def _meta(tree: HTMLParser, *names: str) -> str | None:
    for name in names:
        for selector in (f'meta[property="{name}"]', f'meta[name="{name}"]'):
            node = tree.css_first(selector)
            if node:
                content = (node.attributes.get("content") or "").strip()
                if content:
                    return content
    return None


def product_from_opengraph(tree: HTMLParser, base_url: str) -> Product | None:
    """Fallback ueber Open-Graph- und Twitter-Card-Tags."""
    name = _meta(tree, "og:title", "twitter:title")
    if not name:
        return None
    price_raw = _meta(tree, "product:price:amount", "og:price:amount", "twitter:data1")
    price, currency = parse_price(price_raw)
    currency = _meta(tree, "product:price:currency", "og:price:currency") or currency
    return Product(
        name=name.strip(),
        url=base_url,
        image_url=_absolute(_meta(tree, "og:image", "twitter:image"), base_url),
        price=price,
        currency=currency,
        availability=_meta(tree, "product:availability", "og:availability"),
        source_domain=domain_of(base_url),
    )


# ---------------------------------------------------------------------------
# 3. Microdata
# ---------------------------------------------------------------------------
def _itemprop(tree: HTMLParser, prop: str) -> str | None:
    node = tree.css_first(f'[itemprop="{prop}"]')
    if node is None:
        return None
    attrs = node.attributes
    for attr in ("content", "src", "href", "data-src"):
        value = attrs.get(attr)
        if value and value.strip():
            return value.strip()
    return _text(node) or None


def product_from_microdata(tree: HTMLParser, base_url: str) -> Product | None:
    """Fallback ueber `itemprop`-Attribute."""
    name = _itemprop(tree, "name")
    if not name:
        return None
    price, currency = parse_price(_itemprop(tree, "price"))
    return Product(
        name=name.strip(),
        url=base_url,
        image_url=_absolute(_itemprop(tree, "image"), base_url),
        price=price,
        currency=_itemprop(tree, "priceCurrency") or currency,
        rating=_as_float(_itemprop(tree, "ratingValue")),
        availability=_itemprop(tree, "availability"),
        source_domain=domain_of(base_url),
    )


# ---------------------------------------------------------------------------
# 4. Spec-Tabellen
# ---------------------------------------------------------------------------
def _clean_spec(key: str, value: str) -> tuple[str, str] | None:
    key = key.strip().rstrip(":").strip()
    value = value.strip()
    if not key or not value or key.lower() == value.lower():
        return None
    if len(key) > 80 or len(value) > 400:
        return None
    lowered = key.lower()
    if any(blocked in lowered for blocked in SPEC_KEY_BLOCKLIST):
        return None
    return key, value[:MAX_SPEC_VALUE_LEN]


def extract_spec_tables(tree: HTMLParser) -> dict[str, str]:
    """Sammelt Key-Value-Paare aus Tabellen und Definitionslisten."""
    specs: dict[str, str] = {}

    for table in tree.css("table"):
        for row in table.css("tr"):
            cells = row.css("th, td")
            if len(cells) != 2:
                continue
            pair = _clean_spec(_text(cells[0]), _text(cells[1]))
            if pair and pair[0] not in specs:
                specs[pair[0]] = pair[1]
            if len(specs) >= MAX_SPECS:
                return specs

    for definition_list in tree.css("dl"):
        terms = definition_list.css("dt")
        definitions = definition_list.css("dd")
        for term, definition in zip(terms, definitions, strict=False):
            pair = _clean_spec(_text(term), _text(definition))
            if pair and pair[0] not in specs:
                specs[pair[0]] = pair[1]
            if len(specs) >= MAX_SPECS:
                return specs

    return specs


def has_spec_heading(tree: HTMLParser) -> bool:
    """Gibt es auf der Seite eine Ueberschrift wie "Technische Daten"?"""
    for node in tree.css("h1, h2, h3, h4, caption, summary, legend"):
        if any(heading in _text(node).lower() for heading in SPEC_HEADINGS):
            return True
    return False


# ---------------------------------------------------------------------------
# Orchestrierung
# ---------------------------------------------------------------------------
def _merge(base: Product, extra: Product | None) -> Product:
    """Fuellt leere Felder von *base* aus *extra* auf."""
    if extra is None:
        return base
    for field in ("image_url", "price", "currency", "rating", "availability"):
        if getattr(base, field) is None:
            setattr(base, field, getattr(extra, field))
    for key, value in extra.specs.items():
        base.specs.setdefault(key, value)
    return base


def extract_product(html: str, base_url: str) -> Product | None:
    """Zieht Produktdaten aus *html*; `None`, wenn die Seite kein Produkt zeigt.

    Args:
        html: Roh-HTML der Seite.
        base_url: Endgueltige URL -- fuer das Aufloesen relativer Bild-URLs.
    """
    if not html:
        return None
    tree = HTMLParser(html)

    product = product_from_jsonld(tree, base_url)
    from_og = product_from_opengraph(tree, base_url)
    from_micro = product_from_microdata(tree, base_url)

    if product is None:
        # Ohne JSON-LD brauchen wir ein zweites Signal, sonst halten wir jede
        # beliebige Seite mit og:title faelschlich fuer ein Produkt.
        candidate = from_micro or from_og
        if candidate is None:
            return None
        looks_like_product = bool(
            candidate.price
            or has_spec_heading(tree)
            or tree.css_first('[itemtype*="schema.org/Product" i]')
        )
        if not looks_like_product:
            return None
        product = candidate

    product = _merge(product, from_micro)
    product = _merge(product, from_og)

    for key, value in extract_spec_tables(tree).items():
        if len(product.specs) >= MAX_SPECS:
            break
        product.specs.setdefault(key, value)

    return product
