"""Konfiguration von cortex.

Alle Einstellungen kommen aus Umgebungsvariablen bzw. der `.env` im
Arbeitsverzeichnis oder unter `~/.config/cortex/.env`.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from cortex import __version__

#: Reihenfolge, in der nach einer `.env` gesucht wird (erste gewinnt).
ENV_CANDIDATES: tuple[Path, ...] = (
    Path.cwd() / ".env",
    Path.home() / ".config" / "cortex" / ".env",
    Path.home() / ".cortex" / ".env",
)

#: Ort, an den `cortex setup` schreibt, wenn noch keine `.env` existiert.
DEFAULT_ENV_PATH = Path.home() / ".config" / "cortex" / ".env"

DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"

#: Welcher API-Key-Name gehoert zu welchem LiteLLM-Provider-Praefix?
PROVIDER_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gpt": "OPENAI_API_KEY",
    "azure": "AZURE_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "vertex_ai": "VERTEXAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia_nim": "NVIDIA_NIM_API_KEY",
    "xai": "XAI_API_KEY",
    "together_ai": "TOGETHER_API_KEY",
    "fireworks_ai": "FIREWORKS_AI_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "perplexity": "PERPLEXITYAI_API_KEY",
    "ollama": "",  # lokal, kein Key noetig
    "ollama_chat": "",
    "lm_studio": "",
}

SEARCH_BACKEND_KEYS: dict[str, str] = {
    "duckduckgo": "",  # offene Metasuche, kein Key noetig
    "ddg": "",
    "open": "",
    "searxng": "",  # eigene Instanz, kein Key noetig
    "brave": "BRAVE_API_KEY",
    "tavily": "TAVILY_API_KEY",
}


def find_env_file() -> Path | None:
    """Gibt die erste existierende `.env` aus :data:`ENV_CANDIDATES` zurueck."""
    for candidate in ENV_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def load_env(path: Path | None = None, *, override: bool = False) -> Path | None:
    """Laedt die `.env` in die Prozessumgebung und gibt den benutzten Pfad zurueck."""
    env_path = path or find_env_file()
    if env_path and env_path.is_file():
        load_dotenv(env_path, override=override)
        return env_path
    return None


def _env_str(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "ja"}


def provider_of(model: str) -> str:
    """`anthropic/claude-sonnet-4-6` -> `anthropic`."""
    return model.split("/", 1)[0].lower() if "/" in model else model.split("-", 1)[0].lower()


#: Fuer Anbieter, die in PROVIDER_KEYS nicht stehen. LiteLLM kennt weit mehr
#: Provider, als hier sinnvoll aufzuzaehlen sind -- damit laesst sich jeder
#: davon nutzen, ohne dass cortex angepasst werden muss.
GENERIC_KEY_NAME = "CORTEX_API_KEY"


def api_key_name_for(model: str) -> str:
    """Name der Umgebungsvariable, in der der Key fuer *model* erwartet wird.

    Steht der Anbieter nicht in :data:`PROVIDER_KEYS`, gilt
    :data:`GENERIC_KEY_NAME` -- aber nur, wenn dort auch etwas steht. Sonst
    bleibt es dabei, dass LiteLLM sich den Key selbst aus der Umgebung holt.
    """
    provider = provider_of(model)
    if provider in PROVIDER_KEYS:
        return PROVIDER_KEYS[provider]
    return GENERIC_KEY_NAME if _env_str(GENERIC_KEY_NAME) else ""


def resolve_model(model: str) -> str:
    """Prueft, ob LiteLLM den Anbieter aus *model* ableiten kann.

    Returns:
        Den erkannten Anbieter, oder einen leeren String, wenn nicht.
    """
    if not model.strip():
        return ""
    try:
        import litellm

        litellm.suppress_debug_info = True
        _, provider, *_ = litellm.get_llm_provider(model=model.strip())
        return str(provider or "")
    except Exception:
        return ""


def suggest_model(model: str) -> str:
    """Sucht zu einer nicht aufloesbaren Modell-ID eine, die funktioniert.

    Typischer Fall: `nvidia/nemotron-...` statt `nvidia_nim/nvidia/nemotron-...`.
    Vorgeschlagen wird nur, wenn das erste Segment der Eingabe zu einem
    bekannten Anbieter passt -- LiteLLM akzeptiert unter einem gueltigen
    Praefix naemlich jede beliebige Modell-ID, sodass wir sonst Unsinn
    vorschlagen wuerden.
    """
    model = model.strip()
    if "/" not in model:
        return ""
    head = model.split("/", 1)[0].lower()
    if not head:
        return ""
    related = [
        provider
        for provider in PROVIDER_KEYS
        if provider.startswith(head) or head.startswith(provider.split("_", 1)[0])
    ]
    for provider in related:
        candidate = f"{provider}/{model}"
        if resolve_model(candidate):
            return candidate
    return ""


#: Der Port, auf dem Ollama lauscht. Eine Adresse mit diesem Port gehoert zu
#: Ollama -- und damit nie zu einem Anbieter in der Cloud.
OLLAMA_PORT = 11434


def is_ollama_base(url: str) -> bool:
    """Zeigt *url* auf eine Ollama-Instanz?"""
    from urllib.parse import urlparse

    if not url:
        return False
    parsed = urlparse(url if "://" in url else f"http://{url}")
    return parsed.port == OLLAMA_PORT


def base_fits(url: str, model: str) -> bool:
    """Passt die eigene Adresse zu diesem Modell?

    Wer von Ollama auf einen Cloud-Anbieter wechselt, laesst leicht
    `CORTEX_API_BASE=http://localhost:11434` stehen. LiteLLM schickt die
    Anfrage dann brav dorthin, und Ollama antwortet mit "404 page not found"
    -- was wie ein Fehler des Anbieters aussieht, aber keiner ist.

    Geprueft wird nur der Ollama-Port, nicht "irgendwas Lokales": ein
    LiteLLM-Proxy auf localhost oder im Heimnetz ist ein voellig
    berechtigter Weg zu einem Cloud-Modell, und den wollen wir nicht
    versehentlich zusperren.
    """
    if not url or provider_of(model) in ("ollama", "ollama_chat"):
        return True
    return not is_ollama_base(url)


def model_problem(model: str) -> str:
    """Menschenlesbare Meldung, wenn *model* so nicht benutzbar ist."""
    if resolve_model(model):
        return ""
    message = (
        f"Modell '{model}' laesst sich keinem Anbieter zuordnen. "
        "Die Modell-ID braucht ein Anbieter-Praefix."
    )
    suggestion = suggest_model(model)
    if suggestion:
        message += f"\nMeintest du: {suggestion}"
    else:
        message += (
            "\nBeispiele: anthropic/claude-sonnet-4-6, openai/gpt-4o, "
            "nvidia_nim/meta/llama-3.3-70b-instruct, ollama/llama3.1"
        )
    return message


@dataclass(slots=True)
class Settings:
    """Alle Laufzeit-Einstellungen an einem Ort."""

    model: str = DEFAULT_MODEL
    vision_model: str = ""
    api_base: str = ""
    search_backend: str = "duckduckgo"
    #: Komma-Liste offener Engines fuer die Metasuche (leer = alle).
    search_engines: str = ""
    #: Basis-URL der eigenen SearXNG-Instanz.
    searxng_url: str = ""
    location: str = ""
    lang: str = "de"
    country: str = "de"
    max_tool_calls: int = 20
    #: Wie oft ein LLM-Aufruf bei transienten Fehlern wiederholt wird.
    llm_retries: int = 3
    #: Wie viele Zeichen ein einzelnes Werkzeug-Ergebnis im Verlauf belegt.
    max_tool_chars: int = 8000
    #: So viele der juengsten Werkzeug-Ergebnisse bleiben ungekuerzt.
    keep_full_results: int = 4
    #: Obergrenze fuer parallele Subagenten.
    max_subagents: int = 12
    #: Zerlegt cortex jede Frage von sich aus in Teilaufgaben?
    subagents_auto: bool = True
    #: Zeitlimit fuer die Chat-oder-Recherche-Vorpruefung in Sekunden.
    triage_timeout: float = 5.0
    #: Zeitlimit fuer den Planungsschritt in Sekunden.
    planner_timeout: float = 20.0
    #: Kontextfenster fuer lokale Ollama-Modelle in Token. Ollama nimmt sonst
    #: seinen winzigen Default (2048-4096) und wirft bei Ueberlauf STILL den
    #: Anfang des Verlaufs weg -- Systemprompt und fruehere Fragen zuerst.
    #: Genau so fuehlt sich "er erinnert sich nicht an die letzte Frage" an.
    #: 0 = Ollama-Default benutzen.
    context_tokens: int = 16384
    #: Eigenes, leichtes Modell fuer die Subagenten. Leer = Hauptmodell.
    subagent_model: str = ""
    #: Werkzeug-Budget je Subagent.
    subagent_budget: int = 6
    #: Wie viele Subagenten gleichzeitig laufen. Bei lokalen Modellen bringt
    #: mehr als zwei wenig -- die GPU rechnet ohnehin nacheinander. Bei
    #: Cloud-Modellen ist mehr fast geschenkt.
    subagent_parallel: int = 0
    #: Adresse der eigenen Home-Assistant-Instanz, z.B. http://192.168.1.5:8123
    ha_url: str = ""
    #: Langlebiges Zugriffstoken aus dem Home-Assistant-Profil.
    ha_token: str = ""
    #: Darf cortex im Haus auch SCHALTEN, oder nur nachsehen? Aus gutem Grund
    #: standardmaessig aus: eine missverstandene Nebenbemerkung soll nicht das
    #: Licht ausmachen.
    ha_control: bool = False
    #: Darf Cortex AI sich ueber Gespraeche hinweg Dinge merken?
    memory_enabled: bool = True
    #: Passphrase fuer den Speicher. Leer = Schluesseldatei im Datenordner.
    #: Gesetzt = der Schluessel wird bei jedem Start neu abgeleitet und liegt
    #: nirgends auf der Platte.
    memory_key: str = ""
    #: Darf Cortex AI Gmail und den Google Kalender lesen? Aus, bis es jemand
    #: einschaltet -- und selbst dann nur lesend (siehe cortex/google.py).
    google_enabled: bool = False
    #: Zugangsdaten der eigenen Google-Cloud-Anwendung (Typ "Desktop").
    google_client_id: str = ""
    google_client_secret: str = ""
    #: Adresse der Lagerverwaltung im eigenen Netz (leer = keine).
    storage_url: str = ""
    #: Was Cortex dort darf: "off", "read" oder "write". Geloescht wird in
    #: keiner Stufe -- siehe cortex/storage.py.
    storage_access: str = "read"
    #: Darf cortex das eigene Netz durchsuchen?
    lan_enabled: bool = True
    #: Netz, das dabei durchsucht wird. Leer = das eigene automatisch erkennen.
    lan_subnet: str = ""
    fetch_timeout: float = 15.0
    cache_ttl_hours: int = 24
    max_results_default: int = 8
    #: Wie viele Formulierungen derselben Frage hoechstens rausgehen. Eine
    #: einzige findet nur, was zufaellig genau so im Netz steht; zwei bis drei
    #: decken deutlich mehr ab. Ab der vierten wird es beliebig. 1 = aus.
    search_variants: int = 3
    #: Womit sich Cortex bei Webseiten meldet. Ehrlich und mit Kontaktweg --
    #: die Version kommt aus dem Paket, damit hier nicht jahrelang eine
    #: falsche Nummer steht.
    user_agent: str = field(
        default_factory=lambda: (
            f"cortex/{__version__} (+https://github.com/jonasenriklaumen-a11y/thing-finder-; "
            "research agent; contact via repository issues)"
        )
    )
    request_delay_seconds: float = 1.0
    enable_playwright: bool = True
    download_images: bool = False
    data_dir: Path = field(default_factory=lambda: Path.home() / ".cortex")
    env_path: Path | None = None

    # -- Ableitungen ------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "cortex.sqlite3"

    @property
    def effective_vision_model(self) -> str:
        return self.vision_model or self.model

    @property
    def effective_parallel(self) -> int:
        """Wie viele Subagenten gleichzeitig laufen duerfen.

        Ohne eigene Angabe: zwei bei lokalen Modellen -- die GPU rechnet
        ohnehin nacheinander, mehr bringt nur Speicherdruck. In der Cloud
        alle auf einmal, denn dort ist Warten reine Netzwerklatenz.
        """
        if self.subagent_parallel > 0:
            return self.subagent_parallel
        local = provider_of(self.effective_subagent_model) in ("ollama", "ollama_chat")
        if local:
            return 2
        # In der Cloud laufen die Aufrufe wirklich nebeneinander. Ein Deckel
        # von vier hiesse bei zwoelf Teilfragen: drei Wellen hintereinander,
        # also dreimal warten statt einmal -- gemessen 6,2s statt 3,7s.
        # Eine Ratenbegrenzung faengt der Wiederholungsversuch ab.
        return max(1, self.max_subagents)

    @property
    def effective_subagent_model(self) -> str:
        """Womit die Subagenten arbeiten -- notfalls mit dem Hauptmodell."""
        return self.subagent_model or self.model

    @property
    def api_key_name(self) -> str:
        return api_key_name_for(self.model)

    @property
    def api_key(self) -> str:
        name = self.api_key_name
        return _env_str(name) if name else ""

    @property
    def search_key_name(self) -> str:
        """Der Schluesselname der gewaehlten Suchmaschine, "" bei den offenen."""
        return SEARCH_BACKEND_KEYS.get(self.search_backend, "")

    @property
    def search_api_key(self) -> str:
        name = self.search_key_name
        return _env_str(name) if name else ""

    def llm_kwargs(self) -> dict[str, object]:
        """Zusatzargumente fuer `litellm.completion` mit dem Hauptmodell."""
        return self.llm_kwargs_for(self.model)

    def llm_kwargs_for(self, model: str) -> dict[str, object]:
        """Zusatzargumente fuer einen Aufruf mit *model*.

        api_base und api_key gehoeren zum Anbieter des HAUPTmodells. Laeuft
        das Vision- oder Subagenten-Modell bei einem anderen Anbieter, waeren
        sie falsch -- eine lokale Ollama-Basis-URL an Anthropic zu schicken
        bricht den Aufruf. Dann lieber nichts mitgeben: LiteLLM findet den
        richtigen Key selbst in der Umgebung.

        Ollama-Modelle bekommen zusaetzlich `num_ctx`: ohne das gilt Ollamas
        winziger Default und der Verlauf wird bei Ueberlauf still vorn
        abgeschnitten -- das Modell "vergisst" dann die letzte Frage.
        """
        kwargs: dict[str, object] = {}
        if provider_of(model) == provider_of(self.model):
            if self.api_base and base_fits(self.api_base, model):
                kwargs["api_base"] = self.api_base
            if self.api_key:
                kwargs["api_key"] = self.api_key
        if provider_of(model) in ("ollama", "ollama_chat") and self.context_tokens > 0:
            kwargs["num_ctx"] = self.context_tokens
        return kwargs

    def fast_kwargs_for(self, model: str) -> dict[str, object]:
        """Wie :meth:`llm_kwargs_for`, aber auf Tempo getrimmt.

        Fuer Vorpruefung und Planung: kein Denk-Modus (Qwen3 & Co. wuerden
        sonst erst seitenweise ueberlegen, bevor drei Stichworte kommen) und
        ein kleines Kontextfenster -- die Prompts sind ein paar hundert
        Token lang, ein 16k-Fenster kostet dort nur Ladezeit.
        """
        kwargs = self.llm_kwargs_for(model)
        if provider_of(model) in ("ollama", "ollama_chat"):
            kwargs["num_ctx"] = 2048
            kwargs["reasoning_effort"] = "disable"
            return kwargs
        # In der Cloud dasselbe Problem, nur teurer: ein Denk-Modell ueberlegt
        # sekundenlang, bevor drei Stichworte kommen -- und diese Sekunden
        # liegen vor JEDER Anfrage. `drop_params` laesst LiteLLM den Wunsch
        # stillschweigend weglassen, wenn der Anbieter ihn nicht kennt; das
        # ist hier gefahrlos, weil diese Aufrufe keine Werkzeuge mitschicken,
        # die dabei verlorengehen koennten.
        kwargs["reasoning_effort"] = "low"
        kwargs["drop_params"] = True
        return kwargs

    def missing_requirements(self) -> list[str]:
        """Liste menschenlesbarer Hinweise auf fehlende Pflichtangaben."""
        problems: list[str] = []
        model_issue = model_problem(self.model)
        if model_issue:
            problems.append(model_issue)
        key_name = self.api_key_name
        if key_name and not _env_str(key_name):
            problems.append(f"{key_name} fehlt (fuer Modell {self.model})")
        backend_key = SEARCH_BACKEND_KEYS.get(self.search_backend, "")
        if backend_key and not _env_str(backend_key):
            problems.append(f"{backend_key} fehlt (fuer Suchmaschine {self.search_backend})")
        if self.search_backend == "searxng" and not self.searxng_url:
            problems.append("CORTEX_SEARXNG_URL fehlt (fuer Suchmaschine searxng)")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings aus der Umgebung lesen (Ergebnis wird gecacht)."""
    env_path = load_env()
    data_dir = Path(_env_str("CORTEX_DATA_DIR") or str(Path.home() / ".cortex"))
    settings = Settings(
        model=_env_str("CORTEX_MODEL", DEFAULT_MODEL),
        vision_model=_env_str("CORTEX_VISION_MODEL"),
        api_base=_env_str("CORTEX_API_BASE"),
        search_backend=_env_str("CORTEX_SEARCH_BACKEND", "duckduckgo").lower(),
        search_engines=_env_str("CORTEX_SEARCH_ENGINES"),
        searxng_url=_env_str("CORTEX_SEARXNG_URL"),
        location=_env_str("CORTEX_LOCATION"),
        lang=_env_str("CORTEX_LANG", "de"),
        country=_env_str("CORTEX_COUNTRY", "de"),
        max_tool_calls=_env_int("CORTEX_MAX_TOOL_CALLS", 20),
        llm_retries=_env_int("CORTEX_LLM_RETRIES", 3),
        max_tool_chars=_env_int("CORTEX_MAX_TOOL_CHARS", 8000),
        keep_full_results=_env_int("CORTEX_KEEP_FULL_RESULTS", 4),
        max_subagents=_env_int("CORTEX_MAX_SUBAGENTS", 12),
        subagents_auto=_env_bool("CORTEX_SUBAGENTS_AUTO", True),
        triage_timeout=float(_env_int("CORTEX_TRIAGE_TIMEOUT", 5)),
        planner_timeout=float(_env_int("CORTEX_PLANNER_TIMEOUT", 20)),
        context_tokens=_env_int("CORTEX_CONTEXT_TOKENS", 16384),
        subagent_model=_env_str("CORTEX_SUBAGENT_MODEL"),
        subagent_budget=_env_int("CORTEX_SUBAGENT_BUDGET", 6),
        subagent_parallel=_env_int("CORTEX_SUBAGENT_PARALLEL", 0),
        ha_url=_env_str("CORTEX_HA_URL"),
        ha_token=_env_str("HA_TOKEN") or _env_str("CORTEX_HA_TOKEN"),
        ha_control=_env_bool("CORTEX_HA_CONTROL", False),
        memory_enabled=_env_bool("CORTEX_MEMORY", True),
        memory_key=_env_str("CORTEX_MEMORY_KEY"),
        lan_enabled=_env_bool("CORTEX_LAN_ENABLED", True),
        lan_subnet=_env_str("CORTEX_LAN_SUBNET"),
        fetch_timeout=float(_env_int("CORTEX_FETCH_TIMEOUT", 15)),
        cache_ttl_hours=_env_int("CORTEX_CACHE_TTL_HOURS", 24),
        search_variants=_env_int("CORTEX_SEARCH_VARIANTS", 3),
        storage_url=_env_str("CORTEX_STORAGE_URL"),
        storage_access=_env_str("CORTEX_STORAGE_ACCESS", "read"),
        google_enabled=_env_bool("CORTEX_GOOGLE", False),
        google_client_id=_env_str("GOOGLE_CLIENT_ID"),
        google_client_secret=_env_str("GOOGLE_CLIENT_SECRET"),
        enable_playwright=_env_bool("CORTEX_ENABLE_PLAYWRIGHT", True),
        data_dir=data_dir,
        env_path=env_path,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def reset_settings_cache() -> None:
    """Nach dem Schreiben einer neuen `.env` aufrufen."""
    get_settings.cache_clear()


def write_env_file(values: dict[str, str], path: Path | None = None) -> Path:
    """Schreibt/aktualisiert Schluessel in einer `.env`, ohne Fremdzeilen zu verlieren."""
    target = path or find_env_file() or DEFAULT_ENV_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    existing: list[str] = (
        target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    )
    remaining = dict(values)
    out: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.extend(f"{key}={value}" for key, value in remaining.items())

    target.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    # Exotische Dateisysteme koennen chmod verweigern -- kein Grund abzubrechen.
    with contextlib.suppress(OSError):
        target.chmod(0o600)
    return target
