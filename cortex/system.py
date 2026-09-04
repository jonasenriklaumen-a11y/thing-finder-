"""Systemauslastung und verfuegbare Modelle -- ohne Zusatzpakete.

Zwei Fragen beantwortet dieses Modul:

* **Wie ausgelastet ist der Rechner gerade?** Prozessor, Arbeitsspeicher,
  Platte, Grafikkarte. Alles aus Bordmitteln: unter Linux aus ``/proc``,
  sonst ueber ``os`` und ``shutil``. Was sich nicht ermitteln laesst, fehlt
  einfach -- geraten wird nichts.
* **Welche Modelle stehen zur Auswahl?** Die lokal installierten Ollama-
  Modelle und die Anbieter, fuer die ein Schluessel hinterlegt ist.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

#: Kurzbeschreibungen der Anbieter. Was das Modell kann, in einem Halbsatz --
#: mehr passt nicht neben eine Auswahlliste.
#: Diese Texte liest der Nutzer -- hier gehoeren echte Umlaute hin, anders
#: als in Kommentaren und Codenamen.
PROVIDER_NOTES: dict[str, str] = {
    "anthropic": "Versteht lange Texte gut, stark bei ausführlichen Recherchen",
    "openai": "Breit einsetzbar, schnell, erkennt auch Bilder",
    "gemini": "Sehr großes Kontextfenster, günstig",
    "nvidia_nim": "Offene Modelle bei NVIDIA, großzügiges Freikontingent",
    "xai": "Grok-Modelle, kennt aktuelle Ereignisse",
    "groq": "Antwortet außergewöhnlich schnell",
    "deepseek": "Günstig und stark beim Schlussfolgern",
    "together_ai": "Viele offene Modelle unter einem Schlüssel",
    "fireworks_ai": "Offene Modelle, schnelle Antworten",
    "cerebras": "Schreibt die Antwort extrem schnell",
    "perplexity": "Sucht von sich aus im Web mit",
    "openrouter": "Ein Schlüssel für viele Anbieter",
    "mistral": "Europäischer Anbieter",
    "ollama_chat": "Läuft auf deinem Rechner: keine Kosten, nichts verlässt das Haus",
}

#: Wie ein Anbieter in der Auswahl heisst. "ollama_chat" sagt niemandem etwas.
PROVIDER_LABELS: dict[str, str] = {
    "ollama_chat": "lokal",
    "ollama": "lokal",
    "nvidia_nim": "NVIDIA",
    "together_ai": "Together",
    "fireworks_ai": "Fireworks",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Google",
    "xai": "xAI",
    "groq": "Groq",
    "deepseek": "DeepSeek",
    "mistral": "Mistral",
    "perplexity": "Perplexity",
    "openrouter": "OpenRouter",
    "cerebras": "Cerebras",
}

#: Ein uebliches Modell je Anbieter, damit die Auswahl nicht leer bleibt.
PROVIDER_MODELS: dict[str, str] = {
    "anthropic": "anthropic/claude-sonnet-4-6",
    "openai": "openai/gpt-4o",
    "gemini": "gemini/gemini-2.0-flash",
    "nvidia_nim": "nvidia_nim/meta/llama-3.3-70b-instruct",
    "xai": "xai/grok-2-latest",
    "groq": "groq/llama-3.3-70b-versatile",
    "deepseek": "deepseek/deepseek-chat",
    "together_ai": "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "fireworks_ai": "fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct",
    "cerebras": "cerebras/llama3.3-70b",
    "perplexity": "perplexity/sonar",
    "openrouter": "openrouter/meta-llama/llama-3.3-70b-instruct",
    "mistral": "mistral/mistral-large-latest",
}

#: Anbieter, deren Schluessel unter diesem Namen in der Umgebung steht.
PROVIDER_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "nvidia_nim": "NVIDIA_NIM_API_KEY",
    "xai": "XAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "together_ai": "TOGETHER_API_KEY",
    "fireworks_ai": "FIREWORKS_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "perplexity": "PERPLEXITYAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


# ---------------------------------------------------------------------------
# Auslastung
# ---------------------------------------------------------------------------
def cpu_load() -> dict[str, Any]:
    """Prozessorlast als Anteil der verfuegbaren Kerne."""
    cores = os.cpu_count() or 1
    try:
        one_minute = os.getloadavg()[0]
    except (OSError, AttributeError):
        return {"cores": cores}  # Windows kennt keine Lastdurchschnitte
    return {
        "cores": cores,
        "load": round(one_minute, 2),
        "percent": min(100, round(one_minute / cores * 100)),
    }


def memory_use() -> dict[str, Any]:
    """Arbeitsspeicher in GB. Leer, wenn er sich nicht ermitteln laesst."""
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        values: dict[str, int] = {}
        for line in meminfo.read_text().splitlines():
            name, _, rest = line.partition(":")
            number = rest.strip().split(" ")[0]
            if number.isdigit():
                values[name] = int(number)
        total = values.get("MemTotal", 0) * 1024
        free = values.get("MemAvailable", values.get("MemFree", 0)) * 1024
        if total:
            return _memory_dict(total, total - free)

    # macOS und alles andere: sysconf kennt Seitenzahl und -groesse.
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return {}
    return {"total_gb": round(total / 1_000_000_000, 1)}


def _memory_dict(total: int, used: int) -> dict[str, Any]:
    return {
        "total_gb": round(total / 1_000_000_000, 1),
        "used_gb": round(used / 1_000_000_000, 1),
        "percent": round(used / total * 100) if total else 0,
    }


def disk_use(path: Path | str = "/") -> dict[str, Any]:
    """Plattenbelegung des Datentraegers, auf dem *path* liegt."""
    try:
        usage = shutil.disk_usage(str(path))
    except OSError:
        return {}
    return {
        "total_gb": round(usage.total / 1_000_000_000, 1),
        "used_gb": round(usage.used / 1_000_000_000, 1),
        "free_gb": round(usage.free / 1_000_000_000, 1),
        "percent": round(usage.used / usage.total * 100) if usage.total else 0,
    }


def gpu_use() -> dict[str, Any]:
    """Grafikkarte ueber nvidia-smi. Leer, wenn keine da ist."""
    if not shutil.which("nvidia-smi"):
        return {}
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    line = (out.stdout or "").strip().splitlines()
    if not line:
        return {}
    parts = [part.strip() for part in line[0].split(",")]
    if len(parts) < 4:
        return {}
    try:
        used, total, load = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return {}
    return {
        "name": parts[0],
        "used_gb": round(used / 1024, 1),
        "total_gb": round(total / 1024, 1),
        "percent": load,
        "memory_percent": round(used / total * 100) if total else 0,
    }


def snapshot(data_dir: Path | str | None = None) -> dict[str, Any]:
    """Alles auf einmal -- fuer die Anzeige in den Einstellungen."""
    return {
        "system": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
        "cpu": cpu_load(),
        "memory": memory_use(),
        "disk": disk_use(data_dir or Path.home()),
        "gpu": gpu_use(),
    }


# ---------------------------------------------------------------------------
# Auswahl der Modelle
# ---------------------------------------------------------------------------
def available_models(settings: Any) -> list[dict[str, str]]:
    """Alles, worauf sich gerade umschalten laesst.

    Zuerst die lokal installierten Ollama-Modelle, dann die Anbieter, fuer
    die ein Schluessel hinterlegt ist. Was keinen Schluessel hat, taucht nicht
    auf -- eine Auswahl, die beim Anklicken scheitert, hilft niemandem.
    """
    from cortex.config import provider_of
    from cortex.local_model import DEFAULT_OLLAMA_URL, installed_models, known_model

    found: list[dict[str, str]] = []
    base = getattr(settings, "api_base", "") or DEFAULT_OLLAMA_URL
    for name in installed_models(base):
        model = known_model(name)
        found.append(
            {
                "id": f"ollama_chat/{name}",
                "label": name,
                "kind": "lokal",
                "note": model.note if model else "Läuft auf deinem Rechner",
            }
        )

    current = getattr(settings, "model", "")
    for provider, key_name in PROVIDER_KEYS.items():
        if not os.environ.get(key_name, "").strip():
            continue
        model_id = PROVIDER_MODELS.get(provider, "")
        if not model_id:
            continue
        # Laeuft gerade ein anderes Modell dieses Anbieters, ist das gemeint.
        if provider_of(current) == provider:
            model_id = current
        found.append(
            {
                "id": model_id,
                "label": model_id.split("/", 1)[-1],
                "kind": PROVIDER_LABELS.get(provider, provider),
                "note": PROVIDER_NOTES.get(provider, "Über die Schnittstelle des Anbieters"),
            }
        )

    # Das laufende Modell gehoert in die Liste, auch wenn es sonst nirgends
    # auftaucht -- sonst steht die Auswahl auf nichts.
    if current and not any(item["id"] == current for item in found):
        provider = provider_of(current)
        found.insert(
            0,
            {
                "id": current,
                "label": current.split("/", 1)[-1],
                "kind": PROVIDER_LABELS.get(provider, provider or "eigenes"),
                "note": "Aktuell eingestellt",
            },
        )
    return found
