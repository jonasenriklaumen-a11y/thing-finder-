"""Kommandozeile von scoutr: Chat-Loop, Slash-Befehle, Setup."""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scoutr import __version__
from scoutr.cache import Cache
from scoutr.config import (
    DEFAULT_ENV_PATH,
    SEARCH_BACKEND_KEYS,
    Settings,
    api_key_name_for,
    find_env_file,
    get_settings,
    load_env,
    reset_settings_cache,
    write_env_file,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="scoutr -- KI-Rechercheagent fuer die Kommandozeile.",
)
console = Console()

MODEL_PRESETS: list[tuple[str, str]] = [
    ("anthropic/claude-sonnet-4-6", "Anthropic Claude Sonnet 4.6 (Default)"),
    ("openai/gpt-4o", "OpenAI GPT-4o"),
    ("gemini/gemini-2.0-flash", "Google Gemini 2.0 Flash"),
    ("ollama/llama3.1", "Lokal via Ollama (kein API-Key)"),
]


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def _probe_llm(model: str, api_key: str, api_base: str) -> tuple[bool, str]:
    """Schickt eine winzige Testanfrage an das Modell."""
    try:
        import litellm

        litellm.suppress_debug_info = True
        kwargs: dict[str, object] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": "Antworte mit genau dem Wort: ok"}],
            max_tokens=8,
            timeout=30,
            **kwargs,
        )
        text = (response.choices[0].message.content or "").strip()
        return True, text or "(leere Antwort, aber Verbindung steht)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _probe_search(backend: str, api_key: str) -> tuple[bool, str]:
    """Schickt eine Testsuche an das gewaehlte Such-Backend."""
    try:
        from scoutr.search import search_web

        results = search_web("scoutr test query", count=3, backend=backend, api_key=api_key)
        if not results:
            return False, "Keine Treffer -- Backend erreichbar, aber ohne Ergebnis."
        return True, f"{len(results)} Treffer, erster: {results[0].title[:60]}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


@app.command("setup")
def setup_command(
    env_file: Path | None = typer.Option(
        None, "--env-file", help="Zieldatei fuer die .env (Default: ~/.config/scoutr/.env)."
    ),
    no_probe: bool = typer.Option(False, "--no-probe", help="Keine Testanfragen schicken."),
) -> None:
    """Interaktive Ersteinrichtung: fragt Modell und Keys ab, testet sie, schreibt die `.env`."""
    load_env()
    target = env_file or find_env_file() or DEFAULT_ENV_PATH
    console.print(
        Panel.fit(
            "[bold]scoutr setup[/bold]\n"
            f"Konfiguration wird geschrieben nach: [cyan]{target}[/cyan]",
            border_style="cyan",
        )
    )

    # -- LLM ---------------------------------------------------------------
    console.print("\n[bold]1. LLM-Anbieter[/bold]")
    table = Table(show_header=False, box=None, pad_edge=False)
    for index, (model_id, label) in enumerate(MODEL_PRESETS, start=1):
        table.add_row(f"  [cyan]{index}[/cyan]", f"[bold]{model_id}[/bold]", label)
    table.add_row("  [cyan]0[/cyan]", "[bold]eigenes Modell[/bold]", "beliebige LiteLLM-Modell-ID")
    console.print(table)

    choice = typer.prompt("Auswahl", default="1")
    if choice.strip() == "0":
        model = typer.prompt("Modell-ID (LiteLLM-Format, z.B. openai/gpt-4o)")
    else:
        try:
            model = MODEL_PRESETS[int(choice) - 1][0]
        except (ValueError, IndexError):
            model = MODEL_PRESETS[0][0]
    console.print(f"  Modell: [bold cyan]{model}[/bold cyan]")

    key_name = api_key_name_for(model)
    api_key = ""
    api_base = ""
    if key_name:
        current = os.environ.get(key_name, "")
        hint = f" [dim](aktuell gesetzt: ...{current[-4:]})[/dim]" if current else ""
        console.print(f"  Benoetigter Key: [bold]{key_name}[/bold]{hint}")
        console.print(f"  [dim]Wo bekomme ich den? {_key_hint(key_name)}[/dim]")
        api_key = typer.prompt(f"{key_name}", default=current, hide_input=not current).strip()
    else:
        console.print("  [dim]Dieses Modell braucht keinen API-Key.[/dim]")
        default_base = "http://localhost:11434" if model.startswith("ollama") else ""
        api_base = typer.prompt(
            "API-Basis-URL (leer lassen fuer Default)", default=default_base
        ).strip()

    # -- Suche -------------------------------------------------------------
    console.print("\n[bold]2. Suchmaschine[/bold]")
    console.print("  [cyan]1[/cyan] duckduckgo  [dim]kein Key noetig (Default)[/dim]")
    console.print("  [cyan]2[/cyan] brave       [dim]BRAVE_API_KEY, https://brave.com/search/api/[/dim]")
    console.print("  [cyan]3[/cyan] tavily      [dim]TAVILY_API_KEY, https://tavily.com/[/dim]")
    backend_choice = typer.prompt("Auswahl", default="1").strip()
    backend = {"1": "duckduckgo", "2": "brave", "3": "tavily"}.get(backend_choice, "duckduckgo")
    backend_key_name = SEARCH_BACKEND_KEYS.get(backend, "")
    backend_key = ""
    if backend_key_name:
        current = os.environ.get(backend_key_name, "")
        console.print(f"  [dim]Wo bekomme ich den? {_key_hint(backend_key_name)}[/dim]")
        backend_key = typer.prompt(
            backend_key_name, default=current, hide_input=not current
        ).strip()

    # -- Ort ---------------------------------------------------------------
    console.print("\n[bold]3. Voreinstellungen[/bold]")
    location = typer.prompt("Standard-Ort (optional, z.B. Moenchengladbach)", default="").strip()
    lang = typer.prompt("Sprache (ISO-Code)", default="de").strip() or "de"
    country = typer.prompt("Land (ISO-Code)", default="de").strip() or "de"

    # -- Proben ------------------------------------------------------------
    if not no_probe:
        console.print("\n[bold]4. Verbindungstest[/bold]")
        if api_key:
            os.environ[key_name] = api_key
        if backend_key:
            os.environ[backend_key_name] = backend_key

        with console.status("  Teste LLM ..."):
            llm_ok, llm_msg = _probe_llm(model, api_key, api_base)
        console.print(f"  {'[green]OK[/green]' if llm_ok else '[red]FEHLER[/red]'}  LLM: {llm_msg}")

        with console.status("  Teste Suche ..."):
            search_ok, search_msg = _probe_search(backend, backend_key)
        marker = "[green]OK[/green]" if search_ok else "[red]FEHLER[/red]"
        console.print(f"  {marker}  Suche: {search_msg}")

        if not (llm_ok and search_ok):
            console.print(
                "\n[yellow]Mindestens ein Test ist fehlgeschlagen.[/yellow] "
                "Die Konfiguration wird trotzdem gespeichert -- du kannst sie jederzeit "
                "mit [bold]scoutr setup[/bold] korrigieren."
            )

    # -- Schreiben ---------------------------------------------------------
    values: dict[str, str] = {
        "SCOUTR_MODEL": model,
        "SCOUTR_SEARCH_BACKEND": backend,
        "SCOUTR_LOCATION": location,
        "SCOUTR_LANG": lang,
        "SCOUTR_COUNTRY": country,
    }
    if key_name and api_key:
        values[key_name] = api_key
    if backend_key_name and backend_key:
        values[backend_key_name] = backend_key
    if api_base:
        values["SCOUTR_API_BASE"] = api_base

    written = write_env_file(values, target)
    reset_settings_cache()
    console.print(
        Panel.fit(
            f"Gespeichert in [cyan]{written}[/cyan]\n\n"
            "Jetzt loslegen:\n"
            '  [bold]scoutr[/bold]                     Chat starten\n'
            '  [bold]scoutr "deine Frage"[/bold]       einmalige Recherche',
            title="fertig",
            border_style="green",
        )
    )


def _key_hint(key_name: str) -> str:
    return {
        "ANTHROPIC_API_KEY": "https://console.anthropic.com/settings/keys",
        "OPENAI_API_KEY": "https://platform.openai.com/api-keys",
        "GEMINI_API_KEY": "https://aistudio.google.com/app/apikey",
        "GROQ_API_KEY": "https://console.groq.com/keys",
        "MISTRAL_API_KEY": "https://console.mistral.ai/api-keys/",
        "OPENROUTER_API_KEY": "https://openrouter.ai/keys",
        "BRAVE_API_KEY": "https://brave.com/search/api/",
        "TAVILY_API_KEY": "https://app.tavily.com/home",
    }.get(key_name, "siehe Doku des Anbieters")


# ---------------------------------------------------------------------------
# config / version
# ---------------------------------------------------------------------------
@app.command("config")
def config_command() -> None:
    """Zeigt die aktive Konfiguration und meldet fehlende Angaben."""
    settings = get_settings()
    table = Table(title="scoutr-Konfiguration", show_header=False, title_justify="left")
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    table.add_row(".env", str(settings.env_path or "(keine gefunden)"))
    table.add_row("Modell", settings.model)
    table.add_row("Vision-Modell", settings.effective_vision_model)
    table.add_row("Suchmaschine", settings.search_backend)
    table.add_row("Ort", settings.location or "(keiner)")
    table.add_row("Sprache / Land", f"{settings.lang} / {settings.country}")
    table.add_row("Max. Tool-Calls", str(settings.max_tool_calls))
    table.add_row("Cache-TTL", f"{settings.cache_ttl_hours} h")
    table.add_row("Datenverzeichnis", str(settings.data_dir))
    key_name = settings.api_key_name
    if key_name:
        key = settings.api_key
        table.add_row(key_name, f"gesetzt (...{key[-4:]})" if key else "[red]fehlt[/red]")
    console.print(table)

    cache = Cache(settings.db_path, settings.cache_ttl_hours)
    stats = cache.stats()
    if stats:
        console.print(
            "Cache: " + ", ".join(f"{kind}={count}" for kind, count in sorted(stats.items()))
        )

    problems = settings.missing_requirements()
    if problems:
        console.print("\n[yellow]Fehlende Angaben:[/yellow]")
        for problem in problems:
            console.print(f"  - {problem}")
        console.print("Behebe sie mit [bold]scoutr setup[/bold].")
    else:
        console.print("\n[green]Konfiguration vollstaendig.[/green]")


@app.command("version")
def version_command() -> None:
    """Gibt die Version aus."""
    console.print(f"scoutr {__version__}")


def _echo_placeholder(settings: Settings) -> None:
    console.print(
        Panel.fit(
            "Der Chat-Modus wird in einem spaeteren Schritt aktiviert.\n"
            f"Aktives Modell: [cyan]{settings.model}[/cyan]",
            border_style="yellow",
        )
    )


@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context) -> None:
    """Ohne Unterbefehl: Chat starten (kommt in Schritt 5)."""
    if ctx.invoked_subcommand is not None:
        return
    _echo_placeholder(get_settings())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
