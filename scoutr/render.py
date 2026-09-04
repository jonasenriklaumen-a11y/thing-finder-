"""Terminal-Ausgabe: Live-Zwischenschritte, gestreamte Antwort, Produktkarten."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from scoutr.models import Product

SKIP_LABELS: dict[str, str] = {
    "blocked": "blockiert",
    "consent_required": "Zustimmung erforderlich",
    "paywall": "Bezahlschranke",
    "robots_disallowed": "per robots.txt gesperrt",
    "timeout": "Zeitueberschreitung",
    "http_error": "Serverfehler",
    "empty": "kein Inhalt",
    "unsupported_content_type": "kein HTML",
    "network_error": "Netzwerkfehler",
    "invalid_url": "ungueltige URL",
}


def shorten(text: str, limit: int = 70) -> str:
    """Kuerzt an der Wortgrenze statt mitten im Wort."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return f"{cut.rstrip(',;.')}…"


def _domain(url: str) -> str:
    from scoutr.models import domain_of

    return domain_of(url) or url


class ChatRenderer:
    """Uebersetzt Agenten-Ereignisse in Terminalausgabe.

    Zwischenschritte erscheinen live (`[Suche] ...`, `[Lese] 4 Seiten...`),
    die Antwort wird als Markdown gestreamt.
    """

    def __init__(self, console: Console, show_images: bool = True) -> None:
        self.console = console
        self.show_images = show_images
        self._live: Live | None = None
        self._answer: list[str] = []
        self._fetch_count = 0
        self._skips: list[str] = []
        self._streaming_answer = False

    # -- Ereignisse -------------------------------------------------------
    def handle(self, event: str, payload: dict[str, Any]) -> None:
        handler = getattr(self, f"_on_{event}", None)
        if handler is not None:
            handler(payload)

    def _on_search(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        self.console.print(
            Text.assemble(("  [Suche] ", "bold cyan"), (payload.get("query", ""), "white"))
        )

    def _on_fetch(self, payload: dict[str, Any]) -> None:
        self._fetch_count += 1
        self._update_reading()

    def _on_skip(self, payload: dict[str, Any]) -> None:
        reason = SKIP_LABELS.get(payload.get("reason", ""), payload.get("reason", ""))
        self._skips.append(f"{_domain(payload.get('url', ''))} uebersprungen: {reason}")
        self._update_reading()

    def _on_calendar(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        wanted = payload.get("query") or f"{payload.get('days', 7)} Tage"
        self.console.print(
            Text.assemble(("  [Termine] ", "bold cyan"), (str(wanted), "white"))
        )

    def _on_mail(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        self.console.print(
            Text.assemble(("  [Mail] ", "bold cyan"), (str(payload.get("query", "")), "white"))
        )

    def _on_planning(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        self.console.print(
            Text.assemble(("  [Plane] ", "bold magenta"), ("sichte die Anfrage ...", "dim"))
        )

    def _on_triage(self, payload: dict[str, Any]) -> None:
        """Nur melden, wenn KEINE Recherche folgt -- sonst spricht [Teile]."""
        if payload.get("decision") != "chat":
            return
        self._flush_reading()
        if payload.get("source") == "heuristik":
            return  # Gruss: gar keine Meldung, das waere nur Laerm
        seconds = payload.get("seconds")
        suffix = f" ({seconds}s)" if seconds else ""
        self.console.print(
            Text.assemble(
                ("  [Ohne Suche]", "dim"),
                (f" direkt beantwortet{suffix}", "dim"),
            )
        )

    def _on_subagents(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        tasks = payload.get("tasks", [])
        self.console.print(
            Text.assemble(("  [Teile] ", "bold magenta"), (f"{len(tasks)} Teilfragen", "white"))
        )
        for task in tasks:
            self.console.print(f"          [dim]{shorten(task, 80)}[/dim]")

    def _on_subagent_done(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        task = shorten(payload.get("task", ""), 62)
        if payload.get("error"):
            self.console.print(f"  [yellow][Teil][/yellow] {task} [red](fehlgeschlagen)[/red]")
        else:
            calls = int(payload.get("tool_calls", 0) or 0)
            wort = "Aufruf" if calls == 1 else "Aufrufe"
            self.console.print(
                Text.assemble(
                    ("  [Fertig] ", "bold green"),
                    (task, "white"),
                    (f"  ({calls} {wort})", "dim"),
                )
            )

    def _on_fallback(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        self.console.print(
            f"  [yellow][Ausweich][/yellow] {payload.get('source', '')} -> "
            f"{payload.get('target', '')}"
        )

    def _on_calculate(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        expression = shorten(payload.get("expression", ""), 70)
        self.console.print(Text.assemble(("  [Rechne] ", "bold cyan"), (expression, "white")))

    def _on_remember(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        text = shorten(payload.get("text", ""), 70)
        self.console.print(Text.assemble(("  [Merke]  ", "bold green"), (text, "white")))

    def _on_retry(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        detail = payload.get("detail", "")
        suffix = f" -- {detail}" if detail else ""
        self.console.print(
            f"  [yellow][Neuer Versuch {payload.get('attempt')}][/yellow] "
            f"{payload.get('reason', '')}{suffix}"
        )

    def _on_memory_save(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        self.console.print(
            Text.assemble(
                ("  [Merke] ", "bold cyan"), (str(payload.get("text", ""))[:70], "white")
            )
        )

    def _on_memory_read(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        self.console.print(
            Text.assemble(
                ("  [Speicher] ", "bold cyan"), (str(payload.get("query", "")), "white")
            )
        )

    def _on_lan_scan(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        self.console.print(
            Text.assemble(("  [Netz]  ", "bold cyan"), (f"{payload.get('subnet', '')} ...", "dim"))
        )

    def _on_lan_done(self, payload: dict[str, Any]) -> None:
        count = int(payload.get("found", 0) or 0)
        word = "Geraet" if count == 1 else "Geraete"
        self.console.print(
            Text.assemble(("  [Netz]  ", "bold cyan"), (f"{count} {word} erreichbar", "white"))
        )

    def _on_lan_check(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        self.console.print(
            Text.assemble(("  [Netz]  ", "bold cyan"), (str(payload.get("host", "")), "white"))
        )

    def _on_ha_read(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        self.console.print(
            Text.assemble(("  [Haus]  ", "bold cyan"), (str(payload.get("search", "")), "white"))
        )

    def _on_ha_call(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        target = payload.get("entity_id") or payload.get("domain", "")
        self.console.print(
            Text.assemble(
                ("  [Haus]  ", "bold yellow"),
                (f"{payload.get('domain')}.{payload.get('service')} -> {target}", "white"),
            )
        )

    def _on_ask(self, payload: dict[str, Any]) -> None:
        """Vor einer Rueckfrage muss die Live-Anzeige weg.

        Sonst schreibt rich weiter in denselben Bereich, in dem gerade die
        Eingabeaufforderung steht -- die Frage waere dann nicht mehr lesbar.
        """
        self._stop_live()
        self._streaming_answer = False
        self._fetch_count = 0
        self._skips.clear()

    def _on_error(self, payload: dict[str, Any]) -> None:
        self._flush_reading()
        self.console.print(f"  [red][Fehler][/red] {payload.get('message', '')}")

    def _on_answer_chunk(self, payload: dict[str, Any]) -> None:
        if not self._streaming_answer:
            self._flush_reading()
            self.console.print()
            self._streaming_answer = True
            self._live = Live(
                Markdown(""),
                console=self.console,
                refresh_per_second=8,
                vertical_overflow="visible",
            )
            self._live.start()
        self._answer.append(payload.get("text", ""))
        if self._live is not None:
            self._live.update(Markdown("".join(self._answer)))

    def _on_done(self, payload: dict[str, Any]) -> None:
        self._stop_live()
        self._streaming_answer = False
        if payload.get("hit_limit"):
            self.console.print(
                f"\n  [yellow]Limit von {payload.get('tool_calls')} Werkzeug-Aufrufen erreicht "
                "-- oben steht der Zwischenstand.[/yellow]"
            )

    # -- Live-Zeile "[Lese] N Seiten..." ----------------------------------
    def _reading_text(self) -> Text:
        text = Text.assemble(
            ("  [Lese]  ", "bold cyan"), (f"{self._fetch_count} Seiten...", "white")
        )
        if self._skips:
            text.append(f"  ({'; '.join(self._skips[-2:])})", style="dim yellow")
        return text

    def _update_reading(self) -> None:
        if self._streaming_answer:
            return
        if self._live is None:
            self._live = Live(self._reading_text(), console=self.console, refresh_per_second=8)
            self._live.start()
        else:
            self._live.update(self._reading_text())

    def _flush_reading(self) -> None:
        """Beendet die Live-Zeile und laesst sie stehen."""
        if self._live is not None and not self._streaming_answer:
            self._live.update(self._reading_text())
            self._stop_live()
            self._fetch_count = 0
            self._skips.clear()

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    # -- Abschluss --------------------------------------------------------
    def reset(self) -> None:
        self._stop_live()
        self._answer.clear()
        self._fetch_count = 0
        self._skips.clear()
        self._streaming_answer = False

    def print_answer(self, text: str) -> None:
        """Antwort ausgeben -- aber nur, wenn sie nicht schon durchgelaufen ist."""
        if self._answer:
            return
        if text.strip():
            self.console.print()
            self.console.print(Markdown(text))


# ---------------------------------------------------------------------------
# Bilder
# ---------------------------------------------------------------------------
def image_backend() -> str:
    """Welche Bildausgabe kann dieses Terminal? `term-image`, `chafa` oder `none`."""
    term = os.environ.get("TERM", "")
    term_program = os.environ.get("TERM_PROGRAM", "")
    capable = (
        "kitty" in term
        or bool(os.environ.get("KITTY_WINDOW_ID"))
        or term_program in {"iTerm.app", "WezTerm"}
        or bool(os.environ.get("WEZTERM_PANE"))
        or os.environ.get("TERM_PROGRAM") == "ghostty"
    )
    if not capable:
        return "none"
    try:
        import term_image  # noqa: F401

        return "term-image"
    except ImportError:
        pass
    if shutil.which("chafa"):
        return "chafa"
    return "none"


def render_image(console: Console, url: str, width: int = 34) -> bool:
    """Zeigt ein Bild im Terminal an. Gibt `False` zurueck, wenn das nicht geht."""
    backend = image_backend()
    if backend == "none" or not url:
        return False
    try:
        if backend == "term-image":
            from term_image.image import from_url

            image = from_url(url, width=width)
            console.print(str(image))
            return True
        if backend == "chafa":
            import httpx

            response = httpx.get(url, timeout=10, follow_redirects=True)
            if response.status_code != 200:
                return False
            with tempfile.NamedTemporaryFile(suffix=".img", delete=True) as handle:
                handle.write(response.content)
                handle.flush()
                output = subprocess.run(
                    ["chafa", f"--size={width}x{width // 2}", handle.name],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            if output.returncode == 0:
                console.print(output.stdout)
                return True
    except Exception:
        return False
    return False


def product_card(product: Product) -> Panel:
    """Eine Produktkarte mit Preis, Bild-URL und Specs."""
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="cyan", no_wrap=True, width=12)
    table.add_column(overflow="fold")
    if product.image_url:
        table.add_row("[Bild]", f"[blue]{product.image_url}[/blue]")
    if product.rating is not None:
        table.add_row("Bewertung", f"{product.rating}")
    if product.availability:
        table.add_row("Verfuegbar", product.availability)
    for key, value in list(product.specs.items())[:12]:
        table.add_row(key, value)
    table.add_row("Quelle", f"[dim]{product.source_domain}[/dim]")
    title = f"{product.name}  [bold green]{product.price_display()}[/bold green]"
    return Panel(Group(table), title=title, title_align="left", border_style="cyan")


def comparison_table(products: list[Product]) -> Table:
    """Vergleichstabelle mit denselben Spec-Zeilen fuer alle Kandidaten."""
    table = Table(title="Vergleich", title_justify="left", header_style="bold cyan")
    table.add_column("", style="cyan", no_wrap=True)
    for product in products:
        table.add_column(product.name[:28], overflow="fold")

    table.add_row("Preis", *[product.price_display() for product in products])
    table.add_row(
        "Bewertung",
        *[str(product.rating) if product.rating is not None else "–" for product in products],
    )

    keys: list[str] = []
    for product in products:
        for key in product.specs:
            if key not in keys:
                keys.append(key)
    for key in keys[:14]:
        table.add_row(key, *[product.specs.get(key, "–") for product in products])

    table.add_row("Quelle", *[product.source_domain or "–" for product in products])
    return table


def print_products(console: Console, products: list[Product], show_images: bool = True) -> None:
    """Gibt Produktkarten und -- ab zwei Produkten -- eine Vergleichstabelle aus."""
    if not products:
        return
    console.print()
    for product in products[:6]:
        if show_images and product.image_url:
            render_image(console, product.image_url)
        console.print(product_card(product))
    if len(products) > 1:
        console.print()
        console.print(comparison_table(products[:6]))
