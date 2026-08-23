"""Lokales Modell einrichten -- ohne API-Key, ohne Konto, ohne Cloud.

scoutr benutzt dafuer [Ollama](https://ollama.com). Dieses Modul kuemmert
sich um alles, was dazu noetig ist: Ollama finden oder installieren, den
Server starten, ein Modell laden und -- das Entscheidende -- pruefen, ob
das Modell wirklich Tool-Calling beherrscht. Ohne das kann der Agent weder
suchen noch Seiten lesen.

Nichts hier laeuft ungefragt: Der Installationsbefehl wird angezeigt und
muss bestaetigt werden.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

#: Wo Ollama standardmaessig lauscht.
DEFAULT_OLLAMA_URL = "http://localhost:11434"

#: LiteLLM-Praefix. `ollama_chat` kann Tool-Calling, das nackte `ollama` nicht.
MODEL_PREFIX = "ollama_chat"


@dataclass(frozen=True, slots=True)
class LocalModel:
    """Ein Modellvorschlag aus dem Ollama-Katalog."""

    name: str
    #: Ungefaehre Downloadgroesse in GB -- der genaue Wert kommt nach dem Laden.
    size_gb: float
    #: Empfohlener freier Arbeitsspeicher in GB.
    needs_gb: int
    note: str

    @property
    def model_id(self) -> str:
        """Die ID, wie sie in die `.env` gehoert."""
        return f"{MODEL_PREFIX}/{self.name}"


#: Auswahl an Modellen, die laut Ollama-Katalog Tool-Calling koennen.
#: Groessenangaben sind Richtwerte; jedes andere Ollama-Modell mit
#: Werkzeug-Unterstuetzung laesst sich per `--model` genauso einrichten.
LOCAL_MODELS: tuple[LocalModel, ...] = (
    LocalModel("qwen2.5:7b", 4.7, 8, "Guter Kompromiss aus Tempo und Qualitaet"),
    LocalModel("llama3.1:8b", 4.9, 8, "Bewaehrt, breit getestet"),
    LocalModel("qwen2.5:14b", 9.0, 16, "Deutlich staerker, braucht mehr Speicher"),
    LocalModel("qwen2.5:32b", 20.0, 32, "Fuer Rechner mit viel VRAM"),
    LocalModel("qwen2.5:3b", 1.9, 4, "Notloesung fuer schwache Rechner"),
)

DEFAULT_MODEL = LOCAL_MODELS[0]


class LocalModelError(RuntimeError):
    """Etwas an der lokalen Einrichtung ist schiefgegangen."""


# ---------------------------------------------------------------------------
# Ollama finden und starten
# ---------------------------------------------------------------------------
def ollama_binary() -> str | None:
    """Pfad zur `ollama`-Datei, oder `None`."""
    return shutil.which("ollama")


def install_command() -> list[str] | None:
    """Der plattformabhaengige Installationsbefehl.

    Returns:
        Der Befehl als Liste, oder `None`, wenn wir ihn nicht kennen.
    """
    system = platform.system()
    if system == "Linux":
        return ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
    if system == "Darwin":
        if shutil.which("brew"):
            return ["brew", "install", "ollama"]
        return None
    return None


def install_hint() -> str:
    """Was der Nutzer tun soll, wenn wir nicht automatisch installieren koennen."""
    system = platform.system()
    if system == "Darwin":
        return "Lade Ollama von https://ollama.com/download -- oder `brew install ollama`."
    if system == "Windows":
        return "Lade den Installer von https://ollama.com/download herunter."
    return "Anleitung: https://ollama.com/download"


def server_running(base_url: str = DEFAULT_OLLAMA_URL, timeout: float = 3.0) -> bool:
    """Antwortet unter *base_url* ein Ollama-Server?"""
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def installed_models(base_url: str = DEFAULT_OLLAMA_URL) -> list[str]:
    """Namen der bereits geladenen Modelle."""
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        if response.status_code != 200:
            return []
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    return [str(item.get("name", "")) for item in payload.get("models", []) if item.get("name")]


def start_server(base_url: str = DEFAULT_OLLAMA_URL, wait_seconds: float = 20.0) -> bool:
    """Startet `ollama serve` im Hintergrund und wartet, bis es antwortet."""
    binary = ollama_binary()
    if binary is None:
        return False
    if server_running(base_url):
        return True
    try:
        subprocess.Popen(
            [binary, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if server_running(base_url, timeout=1.5):
            return True
        time.sleep(0.5)
    return False


def install_ollama(runner: Callable[[list[str]], int] | None = None) -> bool:
    """Fuehrt den Installationsbefehl aus. Gibt `True` bei Erfolg zurueck."""
    command = install_command()
    if command is None:
        return False
    run = runner or _default_runner
    return run(command) == 0


def _default_runner(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode


# ---------------------------------------------------------------------------
# Modell laden
# ---------------------------------------------------------------------------
def pull_model(name: str, binary: str | None = None) -> Iterator[str]:
    """Laedt *name* per `ollama pull` und liefert die Fortschrittszeilen.

    Raises:
        LocalModelError: Wenn Ollama fehlt oder der Download scheitert.
    """
    executable = binary or ollama_binary()
    if executable is None:
        raise LocalModelError("Ollama ist nicht installiert.")

    process = subprocess.Popen(
        [executable, "pull", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    last_line = ""
    for line in process.stdout:
        line = line.strip()
        if line:
            last_line = line
            yield line
    if process.wait() != 0:
        raise LocalModelError(f"`ollama pull {name}` fehlgeschlagen: {last_line or 'kein Grund'}")


def model_size_gb(name: str, base_url: str = DEFAULT_OLLAMA_URL) -> float | None:
    """Tatsaechliche Groesse eines geladenen Modells in GB."""
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    for item in payload.get("models", []):
        if str(item.get("name", "")) == name:
            size = item.get("size")
            if isinstance(size, (int, float)) and size > 0:
                return round(size / 1_000_000_000, 1)
    return None


# ---------------------------------------------------------------------------
# Der entscheidende Test: kann das Modell Werkzeuge aufrufen?
# ---------------------------------------------------------------------------
PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Sucht im Web und liefert Treffer.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Die Suchanfrage."}},
            "required": ["query"],
        },
    },
}

PROBE_MESSAGE = (
    "Suche im Web nach dem aktuellen Wetter in Berlin. "
    "Benutze dafuer das Werkzeug web_search."
)


def verify_tool_calling(model_id: str, api_base: str = DEFAULT_OLLAMA_URL) -> tuple[bool, str]:
    """Prueft an einem echten Aufruf, ob *model_id* Werkzeuge aufrufen kann.

    Das ist die Bedingung, unter der scoutr ueberhaupt recherchieren kann --
    ein Modell ohne Tool-Calling wuerde aus dem Gedaechtnis antworten.
    """
    try:
        import litellm

        litellm.suppress_debug_info = True
        response = litellm.completion(
            model=model_id,
            messages=[{"role": "user", "content": PROBE_MESSAGE}],
            tools=[PROBE_TOOL],
            tool_choice="auto",
            api_base=api_base,
            timeout=180,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    message = response.choices[0].message
    calls = getattr(message, "tool_calls", None) or []
    if not calls:
        text = (getattr(message, "content", "") or "").strip()
        return False, (
            "Das Modell hat geantwortet, aber kein Werkzeug aufgerufen"
            + (f": {text[:120]}" if text else ".")
        )

    name = ""
    first = calls[0]
    function = getattr(first, "function", None)
    if function is not None:
        name = getattr(function, "name", "") or ""
    elif isinstance(first, dict):
        name = str(first.get("function", {}).get("name", ""))
    return True, f"Werkzeug aufgerufen: {name or '(ohne Namen)'}"


# ---------------------------------------------------------------------------
# Speicher-Hinweis
# ---------------------------------------------------------------------------
def total_memory_gb() -> float | None:
    """Gesamter Arbeitsspeicher in GB, soweit ermittelbar."""
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        try:
            for line in meminfo.read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1_048_576, 1)
        except (OSError, ValueError, IndexError):
            return None
    if platform.system() == "Darwin":
        try:
            output = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=False
            )
            return round(int(output.stdout.strip()) / 1_073_741_824, 1)
        except (OSError, ValueError):
            return None
    return None


def recommend_model(memory_gb: float | None = None) -> LocalModel:
    """Waehlt das groesste Modell, das in den Speicher passt."""
    available = memory_gb if memory_gb is not None else total_memory_gb()
    if available is None:
        return DEFAULT_MODEL
    fitting = [model for model in LOCAL_MODELS if model.needs_gb <= available]
    if not fitting:
        return min(LOCAL_MODELS, key=lambda model: model.needs_gb)
    return max(fitting, key=lambda model: model.size_gb)


def gpu_hint() -> str:
    """Kurze Notiz zur erkannten GPU, falls `nvidia-smi` vorhanden ist."""
    if not shutil.which("nvidia-smi"):
        return ""
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    line = output.stdout.strip().splitlines()
    return line[0].strip() if line else ""


def env_values(model: LocalModel | str, base_url: str = DEFAULT_OLLAMA_URL) -> dict[str, str]:
    """Die `.env`-Eintraege fuer ein lokales Modell."""
    model_id = model.model_id if isinstance(model, LocalModel) else str(model)
    if "/" not in model_id:
        model_id = f"{MODEL_PREFIX}/{model_id}"
    return {
        "SCOUTR_MODEL": model_id,
        "SCOUTR_API_BASE": base_url.rstrip("/"),
    }


def as_json(model: LocalModel) -> str:
    """Fuer Logs und Tests."""
    return json.dumps(
        {"name": model.name, "id": model.model_id, "size_gb": model.size_gb},
        ensure_ascii=False,
    )
