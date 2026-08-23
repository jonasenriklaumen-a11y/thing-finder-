"""Kommandozeile von scoutr: Chat-Loop, Slash-Befehle, Setup."""

from __future__ import annotations

import contextlib
import difflib
import os
import re
import sys
from datetime import datetime
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
    model_problem,
    reset_settings_cache,
    write_env_file,
)
from scoutr.search import OPEN_ENGINES

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="scoutr -- KI-Rechercheagent fuer die Kommandozeile.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()

MODEL_PRESETS: list[tuple[str, str]] = [
    ("anthropic/claude-sonnet-4-6", "Anthropic Claude Sonnet 4.6 (Default)"),
    ("openai/gpt-4o", "OpenAI GPT-4o"),
    ("gemini/gemini-2.0-flash", "Google Gemini 2.0 Flash"),
    ("nvidia_nim/meta/llama-3.3-70b-instruct", "NVIDIA NIM (build.nvidia.com)"),
    ("ollama_chat/qwen2.5:7b", "Lokal via Ollama -- kein API-Key noetig"),
]

#: Position des lokalen Modells in MODEL_PRESETS (1-basiert).
LOCAL_CHOICE = 5

#: Modelle ohne Tool-Calling koennen den Agenten nicht fahren -- darauf
#: weisen wir bei der Einrichtung hin.
TOOL_CALL_WARNING = (
    "Das Modell muss Tool-Calling (Function Calling) beherrschen -- sonst kann "
    "der Agent weder suchen noch Seiten lesen."
)


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


def _probe_search(
    backend: str, api_key: str = "", engines: str = "", instance_url: str = ""
) -> tuple[bool, str]:
    """Schickt eine Testsuche an das gewaehlte Such-Backend."""
    try:
        from scoutr.search import search_web

        results = search_web(
            "wetter berlin",
            count=3,
            backend=backend,
            api_key=api_key,
            engines=engines,
            instance_url=instance_url,
        )
        if not results:
            return False, "Keine Treffer -- Backend erreichbar, aber ohne Ergebnis."
        return True, f"{len(results)} Treffer, erster: {results[0].title[:60]}"
    except Exception as exc:
        return False, f"{exc}"


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
    local_api_base = ""
    table = Table(show_header=False, box=None, pad_edge=False)
    for index, (model_id, label) in enumerate(MODEL_PRESETS, start=1):
        table.add_row(f"  [cyan]{index}[/cyan]", f"[bold]{model_id}[/bold]", label)
    table.add_row("  [cyan]0[/cyan]", "[bold]eigenes Modell[/bold]", "beliebige LiteLLM-Modell-ID")
    console.print(table)

    choice = typer.prompt("Auswahl", default="1")
    if choice.strip() == "0":
        console.print(f"  [dim]{TOOL_CALL_WARNING}[/dim]")
        model = typer.prompt(
            "Modell-ID im LiteLLM-Format, z.B. nvidia_nim/meta/llama-3.3-70b-instruct"
        )
    elif choice.strip() == str(LOCAL_CHOICE):
        # Lokales Modell: installieren, laden, pruefen -- danach ist der
        # LLM-Teil erledigt und es geht direkt zur Suchmaschine weiter.
        local_id = _run_local_setup()
        if not local_id:
            console.print("[yellow]Lokale Einrichtung abgebrochen.[/yellow]")
            raise typer.Exit(code=1)
        from scoutr.local_model import DEFAULT_OLLAMA_URL

        model = local_id
        local_api_base = DEFAULT_OLLAMA_URL
    else:
        try:
            model = MODEL_PRESETS[int(choice) - 1][0]
        except (ValueError, IndexError):
            model = MODEL_PRESETS[0][0]
    console.print(f"  Modell: [bold cyan]{model}[/bold cyan]")
    model_issue = model_problem(model)
    if model_issue:
        console.print(f"  [yellow]{model_issue}[/yellow]")

    key_name = api_key_name_for(model)
    api_key = ""
    api_base = local_api_base
    if local_api_base:
        console.print("  [dim]Laeuft lokal -- kein API-Key noetig.[/dim]")
    elif key_name:
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
    console.print(
        "  [cyan]1[/cyan] offene Metasuche  [green]kein Key[/green] "
        f"[dim]({', '.join(OPEN_ENGINES[:4])} ...)[/dim]"
    )
    console.print(
        "  [cyan]2[/cyan] SearXNG           [green]kein Key[/green] "
        "[dim]eigene oder fremde Instanz[/dim]"
    )
    console.print(
        "  [cyan]3[/cyan] Brave Search      [dim]BRAVE_API_KEY, https://brave.com/search/api/[/dim]"
    )
    console.print(
        "  [cyan]4[/cyan] Tavily            [dim]TAVILY_API_KEY, https://tavily.com/[/dim]"
    )
    backend_choice = typer.prompt("Auswahl", default="1").strip()
    backend = {"1": "duckduckgo", "2": "searxng", "3": "brave", "4": "tavily"}.get(
        backend_choice, "duckduckgo"
    )

    backend_key_name = SEARCH_BACKEND_KEYS.get(backend, "")
    backend_key = ""
    engines = ""
    searxng_url = ""

    if backend == "duckduckgo":
        console.print(
            "  [dim]Fragt mehrere offene Suchmaschinen an und mischt die Treffer. "
            "Faellt eine aus, uebernehmen die anderen.[/dim]"
        )
        engines = typer.prompt(
            f"  Engines einschraenken? ({', '.join(OPEN_ENGINES)}) -- leer = alle",
            default="",
        ).strip()
    elif backend == "searxng":
        console.print(
            "  [dim]SearXNG ist eine freie Metasuchmaschine zum Selberhosten:[/dim]\n"
            "  [dim]  docker run -d -p 8080:8080 searxng/searxng[/dim]\n"
            "  [dim]In der settings.yml muss unter `search.formats` der Eintrag "
            "`json` stehen.[/dim]"
        )
        searxng_url = typer.prompt(
            "  Adresse der Instanz", default=os.environ.get("SCOUTR_SEARXNG_URL", "")
            or "http://localhost:8080"
        ).strip()
    elif backend_key_name:
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
            search_ok, search_msg = _probe_search(backend, backend_key, engines, searxng_url)
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
    if engines:
        values["SCOUTR_SEARCH_ENGINES"] = engines
    if searxng_url:
        values["SCOUTR_SEARXNG_URL"] = searxng_url
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
        "NVIDIA_NIM_API_KEY": "https://build.nvidia.com/ -- Modell waehlen, dann 'Get API Key'",
        "XAI_API_KEY": "https://console.x.ai/",
        "TOGETHER_API_KEY": "https://api.together.ai/settings/api-keys",
        "CEREBRAS_API_KEY": "https://cloud.cerebras.ai/",
        "PERPLEXITYAI_API_KEY": "https://www.perplexity.ai/settings/api",
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
    table.add_row(
        "Suchmaschine",
        settings.search_backend
        + (f" ({settings.search_engines})" if settings.search_engines else "")
        + (f" @ {settings.searxng_url}" if settings.searxng_url else ""),
    )
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


@app.command("search")
def search_command(
    query: str = typer.Argument(..., help="Die Suchanfrage."),
    count: int = typer.Option(8, "--count", "-n", help="Anzahl Treffer."),
    country: str = typer.Option("", "--country", help="ISO-Laendercode, z.B. de."),
    lang: str = typer.Option("", "--lang", help="ISO-Sprachcode, z.B. de."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Cache umgehen."),
) -> None:
    """Fuehrt `web_search` einmal direkt aus -- zum Testen ohne LLM."""
    from scoutr.tools import Toolbox

    settings = get_settings()
    cache = None if no_cache else Cache(settings.db_path, settings.cache_ttl_hours)
    box = Toolbox(settings, cache=cache)
    try:
        payload = box.web_search(query, count=count, country=country, lang=lang)
    finally:
        box.close()

    if payload.get("error"):
        console.print(f"[red]Fehler:[/red] {payload['error']}")
        raise typer.Exit(code=1)

    results = payload["results"]
    console.print(
        f"[dim]{len(results)} Treffer fuer[/dim] [bold]{query}[/bold] "
        f"[dim]({payload['country']}/{payload['lang']})[/dim]\n"
    )
    for item in results:
        console.print(f"[cyan]{item['rank']:>2}.[/cyan] [bold]{item['title']}[/bold]")
        console.print(f"    [blue]{item['url']}[/blue]")
        if item["snippet"]:
            console.print(f"    [dim]{item['snippet'][:220]}[/dim]")
        console.print()


@app.command("fetch")
def fetch_command(
    url: str = typer.Argument(..., help="Die abzurufende URL."),
    chars: int = typer.Option(2000, "--chars", "-c", help="Wie viel Text anzeigen?"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Cache umgehen."),
    products: bool = typer.Option(True, "--products/--no-products", help="Produktdaten ziehen."),
) -> None:
    """Fuehrt `fetch_page` einmal direkt aus -- zum Testen ohne LLM."""
    from scoutr.tools import Toolbox

    settings = get_settings()
    cache = None if no_cache else Cache(settings.db_path, settings.cache_ttl_hours)
    box = Toolbox(settings, cache=cache)
    try:
        payload = box.fetch_page(url)
    finally:
        box.close()

    if not payload.get("ok"):
        console.print(
            f"[yellow]uebersprungen[/yellow] ({payload.get('skipped_reason')}): "
            f"{payload.get('note', '')}"
        )
        raise typer.Exit(code=2)

    console.print(f"[bold]{payload['title']}[/bold]")
    console.print(
        f"[dim]{payload['url']} · {payload['domain']} · {payload['word_count']} Woerter "
        f"· via {payload['via']}[/dim]\n"
    )
    if products and payload.get("products"):
        for product in payload["products"]:
            table = Table(title=product["name"], show_header=False, title_justify="left")
            table.add_column(style="cyan", no_wrap=True)
            table.add_column()
            for key in ("price", "currency", "rating", "availability", "image_url"):
                if product.get(key) is not None:
                    table.add_row(key, str(product[key]))
            for key, value in (product.get("specs") or {}).items():
                table.add_row(key, value)
            console.print(table)
            console.print()
    text = payload["text"]
    console.print(text[:chars])
    if len(text) > chars or payload.get("truncated"):
        console.print(f"\n[dim]... gekuerzt (insgesamt {len(text)} Zeichen)[/dim]")


@app.command("cache")
def cache_command(
    clear: bool = typer.Option(False, "--clear", help="Cache komplett leeren."),
    kind: str = typer.Option("", "--kind", help="Nur eine Art leeren: search oder page."),
) -> None:
    """Zeigt oder leert den Response-Cache."""
    settings = get_settings()
    cache = Cache(settings.db_path, settings.cache_ttl_hours)
    if clear:
        removed = cache.clear(kind or None)
        console.print(f"[green]{removed} Eintraege geloescht.[/green]")
        return
    purged = cache.purge_expired()
    stats = cache.stats()
    console.print(f"Datei: [cyan]{settings.db_path}[/cyan]")
    console.print(f"TTL: {settings.cache_ttl_hours} h, abgelaufen entfernt: {purged}")
    console.print(
        "Gueltige Eintraege: "
        + (", ".join(f"{name}={count}" for name, count in sorted(stats.items())) or "keine")
    )


@app.command("version")
def version_command() -> None:
    """Gibt die Version aus."""
    console.print(f"scoutr {__version__}")


# ---------------------------------------------------------------------------
# history / install-browser
# ---------------------------------------------------------------------------
@app.command("history")
def history_command(
    limit: int = typer.Option(20, "--limit", "-n", help="Wie viele Eintraege?"),
) -> None:
    """Zeigt vergangene Recherchen."""
    settings = get_settings()
    entries = Cache(settings.db_path, settings.cache_ttl_hours).recent_history(limit=limit)
    if not entries:
        console.print("[dim]Noch keine Recherchen gespeichert.[/dim]")
        return
    for entry in entries:
        when = datetime.fromtimestamp(entry.created_at).strftime("%d.%m.%Y %H:%M")
        console.print(f"[dim]{when}[/dim]  [bold]{entry.question}[/bold]")
        first_line = entry.answer.strip().splitlines()[0] if entry.answer.strip() else ""
        console.print(f"    [dim]{first_line[:110]}[/dim]")


@app.command("install-browser")
def install_browser_command() -> None:
    """Installiert Playwright samt Chromium fuer den JavaScript-Fallback (Stufe 3)."""
    import subprocess

    try:
        import playwright  # noqa: F401
    except ImportError:
        console.print("[yellow]Playwright fehlt.[/yellow] Installiere es mit:")
        console.print('  [bold]uv tool install --with playwright scoutr[/bold]')
        console.print("  [dim]oder: pip install \'scoutr[browser]\'[/dim]")
        raise typer.Exit(code=1) from None

    console.print("Lade Chromium herunter ...")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"], check=False
    )
    if result.returncode == 0:
        console.print("[green]Fertig.[/green] Der Browser-Fallback ist jetzt aktiv.")
    else:
        console.print("[red]Installation fehlgeschlagen.[/red]")
        raise typer.Exit(code=result.returncode)



# ---------------------------------------------------------------------------
# install-model
# ---------------------------------------------------------------------------
def _ensure_ollama(assume_yes: bool = False) -> bool:
    """Sorgt dafuer, dass Ollama installiert ist und laeuft.

    Der Installationsbefehl wird immer angezeigt und muss bestaetigt werden --
    hier laedt nichts ungefragt etwas aus dem Netz und fuehrt es aus.
    """
    from scoutr import local_model as lm

    # -- 1. Ollama ---------------------------------------------------------
    if lm.ollama_binary() is None:
        command = lm.install_command()
        if command is None:
            console.print(f"[yellow]Ollama fehlt.[/yellow] {lm.install_hint()}")
            return False
        console.print("\n[bold]1. Ollama installieren[/bold]")
        console.print("  [dim]Ollama fehlt. Dieser Befehl wuerde ausgefuehrt:[/dim]")
        shown = " ".join(command[2:]) if len(command) > 2 else " ".join(command)
        console.print(f"  [cyan]{shown}[/cyan]")
        if not (assume_yes or typer.confirm("  Ausfuehren?", default=True)):
            console.print(f"  [dim]Abgebrochen. Von Hand: {lm.install_hint()}[/dim]")
            return False
        if not lm.install_ollama():
            console.print("[red]Installation fehlgeschlagen.[/red]")
            return False
        console.print("  [green]Ollama installiert.[/green]")
    else:
        console.print(
            f"\n[bold]1. Ollama[/bold]  [green]gefunden[/green] [dim]({lm.ollama_binary()})[/dim]"
        )

    # -- 2. Server ---------------------------------------------------------
    console.print("\n[bold]2. Server[/bold]")
    if lm.server_running():
        console.print("  [green]laeuft bereits[/green]")
        return True
    with console.status("  starte ollama serve ..."):
        started = lm.start_server()
    if not started:
        console.print(
            "  [red]Server startet nicht.[/red] Starte ihn von Hand: [bold]ollama serve[/bold]"
        )
        return False
    console.print("  [green]gestartet[/green]")
    return True


def _pick_local_model(models, recommended, already: set[str], label: str) -> str:
    """Zeigt eine Modellauswahl und gibt den gewaehlten Ollama-Namen zurueck."""
    table = Table(show_header=True, box=None, header_style="dim")
    table.add_column(" ", style="cyan", no_wrap=True)
    table.add_column(label)
    table.add_column("Groesse", justify="right")
    table.add_column("")
    for index, candidate in enumerate(models, start=1):
        marker = []
        if candidate.name in already:
            marker.append("[green]geladen[/green]")
        if candidate.name == recommended.name:
            marker.append("[cyan]empfohlen[/cyan]")
        table.add_row(
            str(index),
            candidate.name,
            f"~{candidate.size_gb} GB",
            f"{candidate.note} {' '.join(marker)}".strip(),
        )
    console.print(table)
    default_index = str(list(models).index(recommended) + 1)
    answer = typer.prompt("  Auswahl (oder eigener Ollama-Name)", default=default_index).strip()
    if answer.isdigit() and 1 <= int(answer) <= len(models):
        return models[int(answer) - 1].name
    return answer


def _pull_if_needed(name: str, already: set[str]) -> bool:
    """Laedt *name*, falls noch nicht vorhanden. `False` bei Fehler."""
    from scoutr import local_model as lm

    if name in already:
        console.print(f"  [green]{name} ist bereits geladen.[/green]")
        return True
    console.print(f"  Lade [bold]{name}[/bold] -- das dauert beim ersten Mal.")
    try:
        with console.status(f"  ollama pull {name} ...") as status:
            for line in lm.pull_model(name):
                status.update(f"  {line[:90]}")
    except lm.LocalModelError as exc:
        console.print(f"  [red]{exc}[/red]")
        console.print(
            "  [dim]Gibt es den Namen noch? Katalog: https://ollama.com/library[/dim]"
        )
        return False
    size = lm.model_size_gb(name)
    suffix = f" [dim]({size} GB)[/dim]" if size else ""
    console.print(f"  [green]geladen[/green]{suffix}")
    return True


def _run_vision_setup(model_name: str = "", assume_yes: bool = False) -> str:
    """Richtet ein lokales Vision-Modell ein. Gibt die Modell-ID zurueck, sonst "".

    Vision-Modelle brauchen kein Tool-Calling -- sie beschreiben nur, was auf
    dem Bild zu sehen ist. Die Recherche danach macht das Hauptmodell.
    """
    from scoutr import local_model as lm

    console.print("\n[bold]5. Vision-Modell[/bold] [dim](fuer scoutr --image und /image)[/dim]")
    if not (model_name or assume_yes) and not typer.confirm(
        "  Auch Bilder als Eingabe nutzen?", default=True
    ):
        console.print("  [dim]Uebersprungen. Spaeter: scoutr install-model --vision-only[/dim]")
        return ""

    already = set(lm.installed_models())
    if model_name:
        chosen = model_name
    else:
        recommended = lm.recommend_vision_model(lm.total_memory_gb())
        chosen = _pick_local_model(lm.VISION_MODELS, recommended, already, "Vision-Modell")

    if not _pull_if_needed(chosen, already):
        return ""

    model_id = f"{lm.MODEL_PREFIX}/{chosen}"
    console.print("  [dim]Sehtest: das Modell bekommt ein rotes Bild gezeigt.[/dim]")
    with console.status("  teste ..."):
        works, detail = lm.verify_vision(model_id)
    if works:
        console.print(f"  [green]OK[/green]  {detail}")
        return model_id

    console.print(f"  [red]FEHLER[/red]  {detail}")
    if assume_yes or typer.confirm("  Trotzdem eintragen?", default=False):
        return model_id
    return ""


def _run_local_setup(model_name: str = "", assume_yes: bool = False) -> str:
    """Richtet ein lokales Modell ein. Gibt die Modell-ID zurueck, sonst "".

    Der Ablauf: Ollama finden oder installieren, Server starten, Modell laden,
    Tool-Calling an einem echten Aufruf pruefen.
    """
    from scoutr import local_model as lm

    console.print(
        Panel.fit(
            "[bold]Lokales Modell einrichten[/bold]\n"
            "[dim]Laeuft komplett auf diesem Rechner -- kein API-Key, kein Konto.[/dim]",
            border_style="cyan",
        )
    )

    if not _ensure_ollama(assume_yes=assume_yes):
        return ""

    # -- 3. Modell waehlen -------------------------------------------------
    console.print("\n[bold]3. Modell[/bold]")
    memory = lm.total_memory_gb()
    gpu = lm.gpu_hint()
    if memory:
        console.print(f"  [dim]Arbeitsspeicher: {memory} GB[/dim]")
    if gpu:
        console.print(f"  [dim]GPU: {gpu}[/dim]")

    already = set(lm.installed_models())
    if model_name:
        chosen = model_name
    else:
        recommended = lm.recommend_model(memory)
        chosen = _pick_local_model(lm.LOCAL_MODELS, recommended, already, "Modell")

    # -- 4. Laden ----------------------------------------------------------
    if not _pull_if_needed(chosen, already):
        return ""

    # -- 5. Tool-Calling pruefen ------------------------------------------
    model_id = f"{lm.MODEL_PREFIX}/{chosen}"
    console.print("\n[bold]4. Tool-Calling pruefen[/bold]")
    console.print("  [dim]Ohne Werkzeugaufrufe kann der Agent weder suchen noch lesen.[/dim]")
    with console.status("  teste ..."):
        works, detail = lm.verify_tool_calling(model_id)
    if works:
        console.print(f"  [green]OK[/green]  {detail}")
    else:
        console.print(f"  [red]FEHLER[/red]  {detail}")
        console.print(
            "  [yellow]Dieses Modell kann keine Werkzeuge aufrufen.[/yellow] "
            "Nimm ein anderes aus der Liste -- scoutr wuerde sonst aus dem "
            "Gedaechtnis antworten statt aus dem Web."
        )
        if not (assume_yes or typer.confirm("  Trotzdem eintragen?", default=False)):
            return ""

    return model_id


@app.command("install-model")
def install_model_command(
    model: str = typer.Option("", "--model", "-m", help="Ollama-Modellname, z.B. qwen2.5:7b."),
    vision_model: str = typer.Option(
        "", "--vision-model", help="Vision-Modell, z.B. llava:7b."
    ),
    vision: bool = typer.Option(
        True, "--vision/--no-vision", help="Auch ein Vision-Modell einrichten."
    ),
    vision_only: bool = typer.Option(
        False, "--vision-only", help="Nur das Vision-Modell einrichten."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Rueckfragen ueberspringen."),
    env_file: Path | None = typer.Option(None, "--env-file", help="Zieldatei fuer die .env."),
) -> None:
    """Installiert lokale Modelle und traegt sie ein -- ohne API-Key."""
    from scoutr.local_model import DEFAULT_OLLAMA_URL, env_values, vision_env_values

    values: dict[str, str] = {}

    if vision_only:
        if not _ensure_ollama(assume_yes=yes):
            raise typer.Exit(code=1)
        vision_id = _run_vision_setup(vision_model, assume_yes=True)
        if not vision_id:
            raise typer.Exit(code=1)
        values.update(vision_env_values(vision_id))
    else:
        model_id = _run_local_setup(model, assume_yes=yes)
        if not model_id:
            raise typer.Exit(code=1)
        values.update(env_values(model_id, DEFAULT_OLLAMA_URL))

        # Mit --yes wird nicht gefragt -- dann laden wir ein Vision-Modell nur,
        # wenn es ausdruecklich benannt wurde. Ungefragt mehrere Gigabyte
        # herunterzuladen waere nicht in Ordnung.
        if vision_model or (vision and not yes):
            vision_id = _run_vision_setup(vision_model, assume_yes=yes)
            if vision_id:
                values.update(vision_env_values(vision_id))

    target = env_file or find_env_file() or DEFAULT_ENV_PATH
    written = write_env_file(values, target)
    reset_settings_cache()

    lines = [f"{key.removeprefix('SCOUTR_')}: [bold cyan]{value}[/bold cyan]"
             for key, value in values.items() if key.endswith("MODEL")]
    console.print(
        Panel.fit(
            "\n".join(lines)
            + f"\nEingetragen in [cyan]{written}[/cyan]\n\n"
            "Loslegen:\n"
            "  [bold]scoutr[/bold]                     Chat\n"
            '  [bold]scoutr "deine Frage"[/bold]       einmalige Recherche\n'
            + (
                "  [bold]scoutr --image foto.jpg[/bold]  Bild als Ausgangspunkt"
                if "SCOUTR_VISION_MODEL" in values
                else ""
            ),
            title="fertig",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
HELP_TEXT = """\
[bold]Slash-Befehle[/bold]
  [cyan]/location <ort>[/cyan]      Ortsfilter setzen (leer = aufheben)
  [cyan]/model <name>[/cyan]        Modell wechseln, z.B. openai/gpt-4o
  [cyan]/export html|md|csv[/cyan]  Recherche dieser Sitzung speichern
  [cyan]/image <pfad>[/cyan]        Bild beschreiben lassen und danach recherchieren
  [cyan]/history[/cyan]             Frueherer Recherchen anzeigen
  [cyan]/clear[/cyan]               Gespraechsverlauf verwerfen
  [cyan]/help[/cyan]                Diese Uebersicht
  [cyan]/quit[/cyan]                Beenden (auch Strg+D)
"""


def _banner(settings: Settings) -> Panel:
    location = settings.location or "kein Ortsfilter"
    return Panel.fit(
        f"[bold]scoutr[/bold] [dim]{__version__}[/dim]\n"
        f"[dim]Modell {settings.model} · Suche {settings.search_backend} · {location}[/dim]\n"
        "[dim]Frag einfach los. /help zeigt die Befehle.[/dim]",
        border_style="cyan",
    )


def _apply_overrides(
    settings: Settings, location: str, lang: str, country: str, model: str, max_calls: int
) -> None:
    if location:
        settings.location = location
    if lang:
        settings.lang = lang.lower()
        if not country:
            settings.country = lang.lower()
    if country:
        settings.country = country.lower()
    if model:
        settings.model = model
    if max_calls:
        settings.max_tool_calls = max_calls


def _warn_if_unconfigured(settings: Settings) -> None:
    problems = settings.missing_requirements()
    if problems:
        console.print(
            Panel.fit(
                "\n".join(problems)
                + "\n\nRichte scoutr mit [bold]scoutr setup[/bold] ein."
                + "\n[dim]Keinen API-Key? [bold]scoutr install-model[/bold] richtet ein "
                "lokales Modell ein -- laeuft ohne Key und ohne Konto.[/dim]",
                title="Konfiguration unvollstaendig",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)


def _record_turn(turns: list, question: str, result) -> None:
    from scoutr.export import Turn

    turns.append(
        Turn(
            question=question,
            answer=result.answer,
            sources=result.sources,
            products=result.products,
            searches=result.searches,
            skipped=result.skipped,
        )
    )


def _do_export(turns: list, fmt: str, settings: Settings) -> None:
    from scoutr.export import export

    try:
        path = export(
            turns,
            fmt,
            directory=Path.cwd(),
            with_images=settings.download_images,
        )
    except ValueError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        return
    console.print(f"[green]Gespeichert:[/green] {path}")


def _run_turn(agent, renderer, question: str, stream: bool, show_images: bool) -> object:
    """Eine Frage durchlaufen lassen und die Ausgabe erzeugen."""
    renderer.reset()
    console.print()
    result = agent.ask(question, stream=stream)
    if not stream:
        renderer.print_answer(result.answer)
    if result.error:
        console.print(f"[red]Fehler:[/red] {result.error}")
        return result
    if result.products:
        from scoutr.render import print_products

        print_products(console, result.products, show_images=show_images)
    console.print()
    return result


@app.command("chat")
def chat_command(
    question: str | None = typer.Argument(
        None, help="Einmalige Frage. Ohne Angabe startet der Chat."
    ),
    location: str = typer.Option("", "--location", "-L", help="Ortsfilter, z.B. Moenchengladbach."),
    lang: str = typer.Option("", "--lang", help="Sprache der Suche, z.B. de."),
    country: str = typer.Option("", "--country", help="Land der Suche, z.B. de."),
    model: str = typer.Option("", "--model", "-m", help="Modell im LiteLLM-Format."),
    image: Path | None = typer.Option(None, "--image", help="Bild als Ausgangspunkt."),
    max_calls: int = typer.Option(0, "--max-calls", help="Werkzeug-Budget (Default 20)."),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Antwort streamen."),
    show_images: bool = typer.Option(
        True, "--images/--no-images", help="Produktbilder im Terminal anzeigen."
    ),
    download_images: bool = typer.Option(
        False, "--download-images", help="Bilder beim Export mitspeichern."
    ),
) -> None:
    """Startet den Chat -- oder beantwortet mit Argument eine einzelne Frage."""
    from scoutr.agent import Agent
    from scoutr.render import ChatRenderer

    settings = get_settings()
    _apply_overrides(settings, location, lang, country, model, max_calls)
    settings.download_images = download_images
    _warn_if_unconfigured(settings)

    cache = Cache(settings.db_path, settings.cache_ttl_hours)
    renderer = ChatRenderer(console, show_images=show_images)
    agent = Agent(settings, cache=cache, on_event=renderer.handle)
    turns: list = []

    try:
        prefix = ""
        if image is not None:
            prefix = _describe_image(agent, image)
            if prefix is None:
                raise typer.Exit(code=1)

        # -- Einmaliger Durchlauf ----------------------------------------
        if question:
            full = f"{prefix}\n\n{question}".strip() if prefix else question
            result = _run_turn(agent, renderer, full, stream, show_images)
            _record_turn(turns, question, result)
            return

        # -- Chat --------------------------------------------------------
        console.print(_banner(settings))
        if prefix:
            result = _run_turn(agent, renderer, prefix, stream, show_images)
            _record_turn(turns, "Bildrecherche", result)

        _enable_readline()
        while True:
            try:
                line = console.input("\n[bold cyan]>[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Bis dann.[/dim]")
                break
            if not line:
                continue
            if line.startswith("/"):
                if _handle_slash(line, agent, settings, turns, renderer):
                    break
                continue
            try:
                result = _run_turn(agent, renderer, line, stream, show_images)
            except KeyboardInterrupt:
                renderer.reset()
                console.print("\n[yellow]Abgebrochen.[/yellow]")
                continue
            _record_turn(turns, line, result)
    finally:
        renderer.reset()
        agent.close()


def _describe_image(agent, image: Path) -> str | None:
    """Bild beschreiben lassen; `None` bei Fehler."""
    try:
        with console.status(f"  Sehe mir {image.name} an ..."):
            description = agent.describe_image(image)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print(
            "[dim]Kann dein Modell ueberhaupt Bilder sehen? Ein eigenes Vision-Modell "
            "richtet [bold]scoutr install-model --vision-only[/bold] ein.[/dim]"
        )
        return None
    console.print(Panel(description, title=f"[Bild] {image.name}", border_style="cyan"))
    return f"Auf dem Bild ist Folgendes zu sehen:\n{description}"


def _handle_slash(line: str, agent, settings: Settings, turns: list, renderer) -> bool:
    """Fuehrt einen Slash-Befehl aus. Gibt `True` zurueck, wenn beendet werden soll."""
    command, _, argument = line[1:].partition(" ")
    command = command.lower()
    argument = argument.strip()

    if command in ("quit", "exit", "q"):
        console.print("[dim]Bis dann.[/dim]")
        return True

    if command == "help":
        console.print(HELP_TEXT)
    elif command == "location":
        agent.set_location(argument)
        console.print(
            f"[green]Ortsfilter:[/green] {argument}"
            if argument
            else "[green]Ortsfilter aufgehoben.[/green]"
        )
    elif command == "model":
        if not argument:
            console.print(f"Aktuelles Modell: [bold]{settings.model}[/bold]")
        else:
            problem = model_problem(argument)
            if problem:
                console.print(f"[yellow]{problem}[/yellow]")
                console.print(f"[dim]Weiter mit {settings.model}.[/dim]")
            else:
                agent.set_model(argument)
                console.print(f"[green]Modell:[/green] {argument}")
    elif command == "export":
        _do_export(turns, argument or "html", settings)
    elif command == "image":
        if not argument:
            console.print("[yellow]Nutzung: /image pfad/zum/bild.jpg[/yellow]")
        else:
            description = _describe_image(agent, Path(argument))
            if description:
                result = _run_turn(agent, renderer, description, True, True)
                _record_turn(turns, f"Bild: {argument}", result)
    elif command == "history":
        history_command(limit=15)
    elif command == "clear":
        agent.clear()
        turns.clear()
        console.print("[green]Verlauf verworfen.[/green]")
    else:
        console.print(f"[yellow]Unbekannter Befehl '/{command}'.[/yellow] /help zeigt alle.")
    return False


def _enable_readline() -> None:
    """Pfeiltasten und Eingabe-History, wenn readline verfuegbar ist."""
    with contextlib.suppress(ImportError):
        import readline  # noqa: F401


@app.command("export")
def export_command(
    fmt: str = typer.Argument("html", help="html, md oder csv."),
    limit: int = typer.Option(1, "--limit", "-n", help="Wie viele Eintraege aus dem Verlauf?"),
    out: Path | None = typer.Option(None, "--out", "-o", help="Zieldatei."),
) -> None:
    """Exportiert die letzten Recherchen aus dem Verlauf."""
    from scoutr.export import Turn, export

    settings = get_settings()
    entries = Cache(settings.db_path, settings.cache_ttl_hours).recent_history(limit=limit)
    if not entries:
        console.print("[yellow]Der Verlauf ist leer.[/yellow]")
        raise typer.Exit(code=1)

    turns = [
        Turn(
            question=entry.question,
            answer=entry.answer,
            sources=entry.meta.get("sources", []),
            searches=entry.meta.get("searches", []),
            skipped=entry.meta.get("skipped", {}),
        )
        for entry in entries
    ]
    try:
        path = export(turns, fmt, path=out, directory=Path.cwd())
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Gespeichert:[/green] {path}")


#: Alles, was kein Unterbefehl ist, gilt als Frage an den Chat.
COMMANDS = {
    "setup",
    "config",
    "search",
    "fetch",
    "cache",
    "history",
    "export",
    "install-browser",
    "install-model",
    "chat",
    "version",
}


#: Sieht wie ein Unterbefehl aus: kleingeschrieben, mit Bindestrich, ein Wort.
#: Eine echte Rechercheanfrage sieht praktisch nie so aus.
SUBCOMMAND_LIKE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")


def _unknown_command(name: str) -> None:
    """Meldet einen unbekannten Unterbefehl, statt ihn ans LLM zu schicken."""
    console.print(f"[red]Unbekannter Befehl '{name}'.[/red]")
    close = difflib.get_close_matches(name, sorted(COMMANDS), n=2, cutoff=0.5)
    if close:
        console.print("Meintest du: " + ", ".join(f"[bold]{item}[/bold]" for item in close))
        console.print(
            f"[dim]Kennt deine Installation '{name}' noch nicht? "
            "Dann ist sie veraltet -- siehe README, Abschnitt Quickstart.[/dim]"
        )
    console.print("[dim]Alle Befehle: " + ", ".join(sorted(COMMANDS)) + "[/dim]")
    console.print(
        f'[dim]War das als Frage gemeint? Dann in Anfuehrungszeichen: scoutr "{name}"[/dim]'
    )


def main() -> None:
    """Einstiegspunkt: `scoutr` und `scoutr "Frage"` landen im Chat."""
    argv = sys.argv[1:]
    if argv and argv[0] in ("--version", "-V"):
        console.print(f"scoutr {__version__}")
        return
    # Ein einzelnes Wort mit Bindestrich ist fast sicher ein vertippter oder
    # unbekannter Unterbefehl. Den als Rechercheanfrage ans LLM zu schicken
    # kostet Zeit und Geld und hilft niemandem.
    if (
        len(argv) == 1
        and argv[0] not in COMMANDS
        and not argv[0].startswith("-")
        and SUBCOMMAND_LIKE.match(argv[0])
    ):
        _unknown_command(argv[0])
        raise SystemExit(2)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("--help", "-h")):
        sys.argv.insert(1, "chat")
    app()


if __name__ == "__main__":
    main()
