"""Tests fuer HTML-, Markdown- und CSV-Export."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scoutr.export import Turn, export, to_html, to_markdown
from scoutr.models import Product


def _turn() -> Turn:
    return Turn(
        question="Laptop bis 1200 € für Bildbearbeitung",
        answer="Drei Kandidaten:\n\n1. Lenovo Yoga Pro 7",
        sources=[
            {
                "url": "https://notebookcheck.com/test",
                "title": "Test",
                "domain": "notebookcheck.com",
            }
        ],
        searches=["laptop bildbearbeitung test 2026"],
        skipped={"https://www.amazon.de/dp/X": "blocked"},
        products=[
            Product(
                name="Lenovo Yoga Pro 7",
                url="https://shop.de/yoga",
                image_url="https://cdn.shop.de/yoga.jpg",
                price="1099,00",
                currency="EUR",
                rating=4.6,
                specs={"CPU": "Ryzen 7 8845HS", "RAM": "32 GB"},
                source_domain="shop.de",
            ),
            Product(
                name="ThinkPad X1",
                url="https://shop.de/x1",
                price="1149,00",
                currency="EUR",
                specs={"CPU": "Core Ultra 7"},
                source_domain="shop.de",
            ),
        ],
    )


def test_markdown_contains_image_links_and_sources() -> None:
    text = to_markdown([_turn()])
    assert "![Lenovo Yoga Pro 7](https://cdn.shop.de/yoga.jpg)" in text
    assert "**CPU:** Ryzen 7 8845HS" in text
    assert "[Test](https://notebookcheck.com/test)" in text
    assert "`laptop bildbearbeitung test 2026`" in text


def test_html_embeds_images_and_comparison() -> None:
    html = to_html([_turn()])
    assert '<img src="https://cdn.shop.de/yoga.jpg"' in html
    assert "<h3>Vergleich</h3>" in html
    # ThinkPad hat kein RAM -> Platzhalter statt geratenem Wert.
    assert "<td>–</td>" in html
    assert "notebookcheck.com/test" in html


def test_html_escapes_user_content() -> None:
    turn = Turn(question="<script>alert(1)</script>", answer="a & b")
    html = to_html([turn])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "a &amp; b" in html


def test_csv_has_one_row_per_product(tmp_path: Path) -> None:
    target = export([_turn()], "csv", path=tmp_path / "out.csv")
    rows = list(csv.reader(target.open(encoding="utf-8")))
    assert rows[0][:3] == ["name", "preis", "waehrung"]
    assert "CPU" in rows[0] and "RAM" in rows[0]
    assert len(rows) == 3
    assert rows[1][0] == "Lenovo Yoga Pro 7"
    # Fehlende Specs bleiben leer statt geraten.
    assert rows[2][rows[0].index("RAM")] == ""


def test_csv_without_products_exports_answers(tmp_path: Path) -> None:
    turn = Turn(question="Cafés?", answer="Zwei gefunden", sources=[{"url": "https://a.de"}])
    target = export([turn], "csv", path=tmp_path / "out.csv")
    rows = list(csv.reader(target.open(encoding="utf-8")))
    assert rows[0] == ["frage", "antwort", "quellen"]
    assert rows[1][0] == "Cafés?"


def test_export_writes_files(tmp_path: Path) -> None:
    for fmt in ("html", "md"):
        path = export([_turn()], fmt, directory=tmp_path)
        assert path.exists() and path.suffix == f".{fmt}"
        assert path.parent == tmp_path


def test_export_rejects_unknown_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unbekanntes Format"):
        export([_turn()], "pdf", directory=tmp_path)


def test_export_rejects_empty_session(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nichts zu exportieren"):
        export([], "html", directory=tmp_path)


def test_default_filename_contains_topic(tmp_path: Path) -> None:
    path = export([_turn()], "md", directory=tmp_path)
    assert "laptop-bis-1200" in path.name
