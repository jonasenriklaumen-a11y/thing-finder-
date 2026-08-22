"""Rechercheergebnisse als HTML, Markdown oder CSV sichern."""

from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from scoutr.models import Product

FORMATS = ("html", "md", "csv")


@dataclass
class Turn:
    """Ein Frage/Antwort-Paar samt Belegen."""

    question: str
    answer: str
    sources: list[dict[str, str]] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


def _slug(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return (slug[:limit].rstrip("-")) or "recherche"


def default_path(turns: list[Turn], fmt: str, directory: Path) -> Path:
    """Baut einen Dateinamen aus Zeitstempel und erster Frage."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    topic = _slug(turns[0].question) if turns else "recherche"
    return directory / f"scoutr-{stamp}-{topic}.{fmt}"


def download_images(turns: list[Turn], directory: Path, timeout: float = 15.0) -> dict[str, str]:
    """Laedt Produktbilder in *directory* und gibt {url: dateiname} zurueck."""
    directory.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for turn in turns:
            for index, product in enumerate(turn.products):
                url = product.image_url
                if not url or url in mapping:
                    continue
                try:
                    response = client.get(url)
                    if response.status_code != 200:
                        continue
                    suffix = Path(httpx.URL(url).path).suffix[:5] or ".jpg"
                    name = f"{_slug(product.name, 30)}-{index}{suffix}"
                    (directory / name).write_bytes(response.content)
                    mapping[url] = name
                except httpx.HTTPError:
                    continue
    return mapping


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def to_markdown(turns: list[Turn], image_map: dict[str, str] | None = None) -> str:
    """Markdown mit Bild-Links."""
    image_map = image_map or {}
    lines: list[str] = ["# scoutr-Recherche", ""]
    lines.append(f"_{datetime.now().strftime('%d.%m.%Y %H:%M')}_")
    lines.append("")

    for turn in turns:
        lines.append(f"## {turn.question}")
        lines.append("")
        lines.append(turn.answer.strip())
        lines.append("")

        for product in turn.products:
            lines.append(f"### {product.name}")
            if product.image_url:
                target = image_map.get(product.image_url, product.image_url)
                lines.append(f"![{product.name}]({target})")
                lines.append("")
            lines.append(f"- **Preis:** {product.price_display()}")
            if product.rating is not None:
                lines.append(f"- **Bewertung:** {product.rating}")
            if product.availability:
                lines.append(f"- **Verfuegbarkeit:** {product.availability}")
            for key, value in product.specs.items():
                lines.append(f"- **{key}:** {value}")
            lines.append(f"- **Quelle:** [{product.source_domain}]({product.url})")
            lines.append("")

        if turn.searches:
            lines.append("**Suchanfragen:** " + ", ".join(f"`{q}`" for q in turn.searches))
            lines.append("")
        if turn.sources:
            lines.append("**Gelesene Quellen:**")
            for source in turn.sources:
                title = source.get("title") or source.get("url", "")
                lines.append(f"- [{title}]({source.get('url', '')})")
            lines.append("")
        if turn.skipped:
            lines.append("**Uebersprungen:**")
            for url, reason in turn.skipped.items():
                lines.append(f"- {url} — {reason}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
HTML_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
       max-width: 62rem; margin: 0 auto; padding: 2rem 1.25rem 4rem;
       line-height: 1.6; background: #fbfbfa; color: #1c1c1a; }
@media (prefers-color-scheme: dark) { body { background: #17171a; color: #e9e9e6; }
  .card, .turn { background: #202024; border-color: #33333a; }
  th { background: #26262b; } a { color: #7fb0ff; } }
h1 { font-size: 1.6rem; margin-bottom: .2rem; }
h2 { font-size: 1.25rem; margin-top: 2.5rem; }
.meta { color: #77776f; font-size: .85rem; }
.turn { background: #fff; border: 1px solid #e6e6e0; border-radius: .75rem;
        padding: 1.25rem 1.5rem; margin: 1rem 0 2rem; }
.answer { white-space: pre-wrap; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr));
         gap: 1rem; margin: 1.5rem 0; }
.card { background: #fff; border: 1px solid #e6e6e0; border-radius: .75rem;
        padding: 1rem; }
.card img { width: 100%; height: 11rem; object-fit: contain; background: #f4f4f0;
            border-radius: .5rem; }
.card h3 { font-size: 1rem; margin: .75rem 0 .25rem; }
.price { font-weight: 700; color: #1a7f4b; }
table { border-collapse: collapse; width: 100%; margin: .75rem 0; font-size: .9rem;
        display: block; overflow-x: auto; }
th, td { border: 1px solid #e6e6e0; padding: .4rem .6rem; text-align: left;
         vertical-align: top; }
th { background: #f4f4f0; font-weight: 600; }
ul { padding-left: 1.2rem; }
.skip { color: #a06b00; font-size: .9rem; }
code { background: #efefe9; padding: .1rem .3rem; border-radius: .25rem; }
@media (prefers-color-scheme: dark) { code { background: #2a2a30; } }
"""


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _spec_matrix(products: list[Product]) -> tuple[list[str], list[list[str]]]:
    keys: list[str] = []
    for product in products:
        for key in product.specs:
            if key not in keys:
                keys.append(key)
    rows = [[product.specs.get(key, "–") for product in products] for key in keys]
    return keys, rows


def to_html(turns: list[Turn], image_map: dict[str, str] | None = None) -> str:
    """Eigenstaendige HTML-Datei mit Produktbildern, Specs-Tabelle und Links."""
    image_map = image_map or {}
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="de"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>scoutr-Recherche</title>",
        f"<style>{HTML_STYLE}</style></head><body>",
        "<h1>scoutr-Recherche</h1>",
        f'<p class="meta">{_e(datetime.now().strftime("%d.%m.%Y %H:%M"))}</p>',
    ]

    for turn in turns:
        parts.append('<section class="turn">')
        parts.append(f"<h2>{_e(turn.question)}</h2>")
        parts.append(f'<div class="answer">{_e(turn.answer.strip())}</div>')

        if turn.products:
            parts.append('<div class="cards">')
            for product in turn.products:
                parts.append('<article class="card">')
                if product.image_url:
                    src = image_map.get(product.image_url, product.image_url)
                    parts.append(f'<img src="{_e(src)}" alt="{_e(product.name)}" loading="lazy">')
                parts.append(f"<h3>{_e(product.name)}</h3>")
                parts.append(f'<p class="price">{_e(product.price_display())}</p>')
                if product.rating is not None:
                    parts.append(f"<p>Bewertung: {_e(product.rating)}</p>")
                if product.specs:
                    parts.append("<table>")
                    for key, value in product.specs.items():
                        parts.append(f"<tr><th>{_e(key)}</th><td>{_e(value)}</td></tr>")
                    parts.append("</table>")
                parts.append(
                    f'<p><a href="{_e(product.url)}" rel="noopener">'
                    f"{_e(product.source_domain or product.url)}</a></p>"
                )
                parts.append("</article>")
            parts.append("</div>")

            if len(turn.products) > 1:
                keys, rows = _spec_matrix(turn.products)
                parts.append("<h3>Vergleich</h3><table><tr><th></th>")
                parts.extend(f"<th>{_e(product.name)}</th>" for product in turn.products)
                parts.append("</tr>")
                parts.append(
                    "<tr><th>Preis</th>"
                    + "".join(f"<td>{_e(p.price_display())}</td>" for p in turn.products)
                    + "</tr>"
                )
                for key, row in zip(keys, rows, strict=True):
                    parts.append(
                        f"<tr><th>{_e(key)}</th>"
                        + "".join(f"<td>{_e(cell)}</td>" for cell in row)
                        + "</tr>"
                    )
                parts.append("</table>")

        if turn.searches:
            queries = ", ".join(f"<code>{_e(query)}</code>" for query in turn.searches)
            parts.append(f'<p class="meta">Suchanfragen: {queries}</p>')
        if turn.sources:
            parts.append("<h3>Gelesene Quellen</h3><ul>")
            for source in turn.sources:
                url = source.get("url", "")
                title = source.get("title") or url
                parts.append(f'<li><a href="{_e(url)}" rel="noopener">{_e(title)}</a></li>')
            parts.append("</ul>")
        if turn.skipped:
            items = "".join(
                f"<li>{_e(url)} — {_e(reason)}</li>" for url, reason in turn.skipped.items()
            )
            parts.append(f'<p class="skip">Uebersprungen:</p><ul class="skip">{items}</ul>')
        parts.append("</section>")

    parts.append("</body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def write_csv(turns: list[Turn], path: Path) -> Path:
    """Eine Zeile je Produkt; Specs werden zu Spalten."""
    products = [product for turn in turns for product in turn.products]
    spec_keys: list[str] = []
    for product in products:
        for key in product.specs:
            if key not in spec_keys:
                spec_keys.append(key)

    header = [
        "name",
        "preis",
        "waehrung",
        "bewertung",
        "verfuegbarkeit",
        "bild_url",
        "quelle",
        "url",
        *spec_keys,
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not products:
            # Ohne Produkte exportieren wir die Antworten selbst.
            writer.writerow(["frage", "antwort", "quellen"])
            for turn in turns:
                writer.writerow(
                    [
                        turn.question,
                        turn.answer,
                        " | ".join(source.get("url", "") for source in turn.sources),
                    ]
                )
            return path
        writer.writerow(header)
        for product in products:
            writer.writerow(
                [
                    product.name,
                    product.price or "",
                    product.currency or "",
                    product.rating if product.rating is not None else "",
                    product.availability or "",
                    product.image_url or "",
                    product.source_domain,
                    product.url,
                    *[product.specs.get(key, "") for key in spec_keys],
                ]
            )
    return path


# ---------------------------------------------------------------------------
# Einstieg
# ---------------------------------------------------------------------------
def export(
    turns: list[Turn],
    fmt: str,
    path: Path | None = None,
    directory: Path | None = None,
    with_images: bool = False,
) -> Path:
    """Schreibt die Recherche im gewuenschten Format und gibt den Pfad zurueck.

    Raises:
        ValueError: Bei unbekanntem Format oder leerer Recherche.
    """
    fmt = (fmt or "").lower().lstrip(".")
    if fmt not in FORMATS:
        raise ValueError(f"Unbekanntes Format '{fmt}'. Moeglich: {', '.join(FORMATS)}")
    if not turns:
        raise ValueError("Es gibt noch nichts zu exportieren.")

    target = path or default_path(turns, fmt, directory or Path.cwd())
    target.parent.mkdir(parents=True, exist_ok=True)

    image_map: dict[str, str] = {}
    if with_images and fmt in ("html", "md"):
        asset_dir = target.with_suffix("") / "bilder"
        image_map = {
            url: str(Path(asset_dir.parent.name) / "bilder" / name)
            for url, name in download_images(turns, asset_dir).items()
        }

    if fmt == "csv":
        return write_csv(turns, target)
    content = to_html(turns, image_map) if fmt == "html" else to_markdown(turns, image_map)
    target.write_text(content, encoding="utf-8")
    return target
