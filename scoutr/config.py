"""Konfiguration von scoutr.

Alle Einstellungen kommen aus Umgebungsvariablen bzw. der `.env` im
Arbeitsverzeichnis oder unter `~/.config/scoutr/.env`.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

#: Reihenfolge, in der nach einer `.env` gesucht wird (erste gewinnt).
ENV_CANDIDATES: tuple[Path, ...] = (
    Path.cwd() / ".env",
    Path.home() / ".config" / "scoutr" / ".env",
    Path.home() / ".scoutr" / ".env",
)

#: Ort, an den `scoutr setup` schreibt, wenn noch keine `.env` existiert.
DEFAULT_ENV_PATH = Path.home() / ".config" / "scoutr" / ".env"

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
#: davon nutzen, ohne dass scoutr angepasst werden muss.
GENERIC_KEY_NAME = "SCOUTR_API_KEY"


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
    fetch_timeout: float = 15.0
    cache_ttl_hours: int = 24
    max_results_default: int = 8
    user_agent: str = (
        "scoutr/0.1 (+https://github.com/jonasenriklaumen-a11y/thing-finder-; "
        "research agent; contact via repository issues)"
    )
    request_delay_seconds: float = 1.0
    enable_playwright: bool = True
    download_images: bool = False
    data_dir: Path = field(default_factory=lambda: Path.home() / ".scoutr")
    env_path: Path | None = None

    # -- Ableitungen ------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "scoutr.sqlite3"

    @property
    def effective_vision_model(self) -> str:
        return self.vision_model or self.model

    @property
    def api_key_name(self) -> str:
        return api_key_name_for(self.model)

    @property
    def api_key(self) -> str:
        name = self.api_key_name
        return _env_str(name) if name else ""

    def llm_kwargs(self) -> dict[str, object]:
        """Zusatzargumente fuer `litellm.completion`."""
        kwargs: dict[str, object] = {}
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
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
            problems.append("SCOUTR_SEARXNG_URL fehlt (fuer Suchmaschine searxng)")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings aus der Umgebung lesen (Ergebnis wird gecacht)."""
    env_path = load_env()
    data_dir = Path(_env_str("SCOUTR_DATA_DIR") or str(Path.home() / ".scoutr"))
    settings = Settings(
        model=_env_str("SCOUTR_MODEL", DEFAULT_MODEL),
        vision_model=_env_str("SCOUTR_VISION_MODEL"),
        api_base=_env_str("SCOUTR_API_BASE"),
        search_backend=_env_str("SCOUTR_SEARCH_BACKEND", "duckduckgo").lower(),
        search_engines=_env_str("SCOUTR_SEARCH_ENGINES"),
        searxng_url=_env_str("SCOUTR_SEARXNG_URL"),
        location=_env_str("SCOUTR_LOCATION"),
        lang=_env_str("SCOUTR_LANG", "de"),
        country=_env_str("SCOUTR_COUNTRY", "de"),
        max_tool_calls=_env_int("SCOUTR_MAX_TOOL_CALLS", 20),
        fetch_timeout=float(_env_int("SCOUTR_FETCH_TIMEOUT", 15)),
        cache_ttl_hours=_env_int("SCOUTR_CACHE_TTL_HOURS", 24),
        enable_playwright=_env_bool("SCOUTR_ENABLE_PLAYWRIGHT", True),
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
