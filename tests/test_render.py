"""Tests fuer die Terminalausgabe."""

from __future__ import annotations

from rich.console import Console

from scoutr.models import Product
from scoutr.render import ChatRenderer, comparison_table, print_products, product_card


def _console() -> Console:
    return Console(record=True, width=100, force_terminal=False, legacy_windows=False)


def test_search_and_read_steps_are_shown() -> None:
    console = _console()
    renderer = ChatRenderer(console, show_images=False)
    renderer.handle("search", {"query": "cafés mönchengladbach"})
    renderer.handle("fetch", {"url": "https://a.de/"})
    renderer.handle("fetch", {"url": "https://b.de/"})
    renderer.handle("skip", {"url": "https://www.amazon.de/dp/X", "reason": "blocked"})
    renderer.handle("answer_chunk", {"text": "Ergebnis"})
    renderer.handle("done", {"tool_calls": 3, "hit_limit": False})

    output = console.export_text()
    assert "[Suche] cafés mönchengladbach" in output
    assert "[Lese]  2 Seiten..." in output
    assert "amazon.de uebersprungen: blockiert" in output


def test_limit_notice_is_shown() -> None:
    console = _console()
    renderer = ChatRenderer(console, show_images=False)
    renderer.handle("done", {"tool_calls": 20, "hit_limit": True})
    assert "Limit von 20" in console.export_text()


def test_answer_is_printed_only_once() -> None:
    console = _console()
    renderer = ChatRenderer(console, show_images=False)
    renderer.handle("answer_chunk", {"text": "Die Antwort"})
    renderer.handle("done", {"tool_calls": 1, "hit_limit": False})
    renderer.print_answer("Die Antwort")
    assert console.export_text().count("Die Antwort") == 1


def test_print_answer_without_stream() -> None:
    console = _console()
    renderer = ChatRenderer(console, show_images=False)
    renderer.print_answer("Nur einmal")
    assert "Nur einmal" in console.export_text()


def test_error_event() -> None:
    console = _console()
    ChatRenderer(console, show_images=False).handle("error", {"message": "kaputt"})
    assert "[Fehler] kaputt" in console.export_text()


def _product(name: str, price: str | None = "1099,00", **kwargs) -> Product:
    return Product(
        name=name,
        url=f"https://shop.de/{name}",
        price=price,
        currency="EUR",
        source_domain="shop.de",
        **kwargs,
    )


def test_product_card_contains_price_and_specs() -> None:
    console = _console()
    console.print(product_card(_product("Yoga Pro 7", specs={"CPU": "Ryzen 7"})))
    output = console.export_text()
    assert "Yoga Pro 7" in output
    assert "1099,00 €" in output
    assert "Ryzen 7" in output


def test_missing_price_is_a_dash() -> None:
    console = _console()
    console.print(product_card(_product("Ohne Preis", price=None)))
    assert "–" in console.export_text()


def test_comparison_table_aligns_spec_rows() -> None:
    products = [
        _product("A", specs={"CPU": "Ryzen 7", "RAM": "32 GB"}),
        _product("B", specs={"CPU": "Core Ultra 7"}),
    ]
    console = _console()
    console.print(comparison_table(products))
    lines = [line for line in console.export_text().splitlines() if "RAM" in line]
    assert lines, "RAM-Zeile fehlt"
    # B hat kein RAM -> Platzhalter statt geratenem Wert.
    assert "–" in lines[0]


def test_print_products_is_silent_without_products() -> None:
    console = _console()
    print_products(console, [], show_images=False)
    assert console.export_text().strip() == ""
