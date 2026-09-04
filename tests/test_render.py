"""Tests fuer die Terminalausgabe."""

from __future__ import annotations

from rich.console import Console

from cortex.models import Product
from cortex.render import ChatRenderer, comparison_table, print_products, product_card


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


def test_shorten_cuts_at_word_boundaries() -> None:
    from cortex.render import shorten

    long = "Welche Cafés in Mönchengladbach haben kostenloses WLAN und Steckdosen?"
    short = shorten(long, 40)
    assert len(short) <= 40
    assert short.endswith("…")
    # Nicht mitten im Wort abgeschnitten.
    assert not short[:-1].endswith(" ")
    assert "Mönchengladbach" in short


def test_shorten_leaves_short_text_alone() -> None:
    from cortex.render import shorten

    assert shorten("kurz", 40) == "kurz"


def test_shorten_normalises_whitespace() -> None:
    from cortex.render import shorten

    assert shorten("a   b\n c", 40) == "a b c"


def test_subagent_steps_are_shown() -> None:
    console = _console()
    renderer = ChatRenderer(console, show_images=False)
    renderer.handle("planning", {"question": "Frage"})
    renderer.handle("subagents", {"tasks": ["Teil eins", "Teil zwei"]})
    renderer.handle("subagent_done", {"task": "Teil eins", "tool_calls": 4})
    renderer.handle("subagent_done", {"task": "Teil zwei", "error": "kaputt"})
    output = console.export_text()
    assert "[Plane]" in output
    assert "[Teile] 2 Teilfragen" in output
    assert "[Fertig] Teil eins" in output
    assert "fehlgeschlagen" in output


def test_retry_is_shown() -> None:
    console = _console()
    ChatRenderer(console, show_images=False).handle(
        "retry", {"attempt": 2, "reason": "Speichermangel", "detail": "entladen: qwen3:8b"}
    )
    output = console.export_text()
    assert "Neuer Versuch 2" in output
    assert "entladen: qwen3:8b" in output


def test_singular_and_plural_of_calls() -> None:
    console = _console()
    renderer = ChatRenderer(console, show_images=False)
    renderer.handle("subagent_done", {"task": "eins", "tool_calls": 1})
    renderer.handle("subagent_done", {"task": "zwei", "tool_calls": 4})
    output = console.export_text()
    assert "(1 Aufruf)" in output
    assert "(4 Aufrufe)" in output


def test_planning_is_announced_before_the_call() -> None:
    console = _console()
    ChatRenderer(console, show_images=False).handle("planning", {"question": "Frage"})
    assert "[Plane]" in console.export_text()


def test_chat_verdict_is_shown_only_when_the_model_decided() -> None:
    """Bei "hallo" waere eine Meldung nur Laerm -- da entschied die Heuristik."""
    console = _console()
    renderer = ChatRenderer(console, show_images=False)
    renderer.handle("triage", {"decision": "chat", "source": "heuristik"})
    assert console.export_text().strip() == ""

    renderer.handle("triage", {"decision": "chat", "source": "modell", "seconds": 1.2})
    output = console.export_text()
    assert "[Ohne Suche]" in output and "1.2s" in output


def test_research_verdict_stays_silent() -> None:
    """Danach kommt ohnehin [Teile] -- zwei Meldungen waeren doppelt."""
    console = _console()
    ChatRenderer(console, show_images=False).handle(
        "triage", {"decision": "recherche", "source": "modell"}
    )
    assert console.export_text().strip() == ""
