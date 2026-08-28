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
    #: Empfohlener freier Speicher in GB (VRAM, sonst Arbeitsspeicher).
    needs_gb: int
    note: str
    #: Kann das Modell Werkzeuge aufrufen? Ohne das kann es nicht recherchieren.
    tools: bool = True
    #: Kann es Bilder ansehen?
    vision: bool = False

    @property
    def model_id(self) -> str:
        """Die ID, wie sie in die `.env` gehoert."""
        return f"{MODEL_PREFIX}/{self.name}"

    @property
    def does_everything(self) -> bool:
        """Deckt dieses Modell Recherche UND Bilder ab?"""
        return self.tools and self.vision


#: Modelle, die BEIDES koennen: Werkzeuge aufrufen und Bilder ansehen.
#: Sie sind die sparsamste Loesung -- nur ein Modell im Speicher statt zwei.
#: Stand August 2026, Groessen sind Richtwerte fuer die Q4-Quantisierung.
DUAL_MODELS: tuple[LocalModel, ...] = (
    LocalModel("gemma4:12b", 6.6, 10, "Vision + Werkzeuge, guter Allrounder", vision=True),
    LocalModel("qwen3-vl:8b", 6.1, 12, "Vision + Werkzeuge, sehr langer Kontext", vision=True),
    LocalModel("gemma4:e4b", 3.5, 6, "Kompakt, laeuft auch auf kleinen Karten", vision=True),
    LocalModel("qwen3-vl:4b", 3.3, 6, "Kleinste Vision-Variante mit Werkzeugen", vision=True),
    LocalModel("gemma4:26b", 16.0, 24, "Deutlich staerker, braucht eine grosse Karte", vision=True),
)

#: Reine Textmodelle -- staerker im Recherchieren, sehen aber nichts.
LOCAL_MODELS: tuple[LocalModel, ...] = (
    LocalModel("qwen3:8b", 5.2, 8, "Sehr zuverlaessig beim Werkzeugaufruf"),
    LocalModel("qwen2.5:7b", 4.7, 8, "Bewaehrt, guter Kompromiss"),
    LocalModel("llama3.1:8b", 4.9, 8, "Breit getestet"),
    LocalModel("qwen2.5:14b", 9.0, 16, "Staerker, braucht mehr Speicher"),
    LocalModel("qwen2.5:3b", 1.9, 4, "Notloesung fuer schwache Rechner"),
)

DEFAULT_MODEL = LOCAL_MODELS[0]

#: Reine Vision-Modelle, falls das Hauptmodell nichts sieht. Sie brauchen
#: KEIN Tool-Calling: sie beschreiben nur, recherchiert wird danach.
#: Achtung Namensfalle: das offizielle `gemma3` beherrscht in Ollama Vision,
#: aber KEIN Tool-Calling -- es taugt nur als Vision-Modell, nie als
#: Hauptmodell. Erst `gemma4` bringt beides mit.
VISION_MODELS: tuple[LocalModel, ...] = (
    LocalModel(
        "gemma3:4b", 3.3, 6, "Offizielles Google-Modell, sieht gut", tools=False, vision=True
    ),
    LocalModel("qwen3-vl:4b", 3.3, 6, "Klein und aktuell", tools=False, vision=True),
    LocalModel(
        "gemma3:12b", 8.1, 12, "Groessere offizielle Gemma-Variante", tools=False, vision=True
    ),
    LocalModel(
        "llava:7b", 4.7, 8, "Bewaehrt, versteht Fotos und Schilder", tools=False, vision=True
    ),
    LocalModel("minicpm-v", 5.5, 8, "Stark bei Text im Bild", tools=False, vision=True),
    LocalModel("moondream", 1.7, 3, "Winzig, fuer schwache Rechner", tools=False, vision=True),
)

DEFAULT_VISION_MODEL = VISION_MODELS[0]

#: Leichte Modelle fuer die Subagenten. Sie bekommen eng umrissene
#: Teilfragen und muessen vor allem eines koennen: zuverlaessig Werkzeuge
#: aufrufen. Klein ist hier wichtiger als klug -- mehrere laufen parallel
#: neben dem Hauptmodell, und der Speicher ist endlich.
#: Stand August 2026, Groessen sind Richtwerte fuer Q4.
SUBAGENT_MODELS: tuple[LocalModel, ...] = (
    LocalModel("qwen3:1.7b", 1.4, 3, "Klein und flink, ruft zuverlaessig Werkzeuge auf"),
    LocalModel("qwen2.5:3b", 1.9, 4, "Etwas groesser, etwas gruendlicher"),
    LocalModel("gemma4:e2b", 1.8, 3, "Sparsamste Gemma-Variante"),
    LocalModel("qwen3:4b", 2.5, 5, "Merklich besser, wenn Platz ist"),
    LocalModel("qwen3:0.6b", 0.5, 2, "Winzig -- nur fuer sehr enge Teilfragen"),
)

DEFAULT_SUBAGENT_MODEL = SUBAGENT_MODELS[0]


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
    if system == "Windows" and shutil.which("winget"):
        return ["winget", "install", "--id", "Ollama.Ollama", "-e", "--accept-package-agreements"]
    return None


def install_hint() -> str:
    """Was der Nutzer tun soll, wenn wir nicht automatisch installieren koennen."""
    system = platform.system()
    if system == "Darwin":
        return "Lade Ollama von https://ollama.com/download -- oder `brew install ollama`."
    if system == "Windows":
        return (
            "Installiere Ollama mit `winget install Ollama.Ollama` oder lade den "
            "Installer von https://ollama.com/download herunter."
        )
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
    # Unter Windows kennt Popen `start_new_session` nicht; dort loesen
    # Creation-Flags den Prozess von der Konsole und unterdruecken das
    # aufpoppende Fenster.
    extra: dict[str, object] = {}
    if platform.system() == "Windows":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
        if flags:
            extra["creationflags"] = flags
    else:
        extra["start_new_session"] = True

    try:
        subprocess.Popen(
            [binary, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **extra,
        )
    except (OSError, ValueError):
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


def loaded_models(base_url: str = DEFAULT_OLLAMA_URL) -> list[str]:
    """Modelle, die gerade im Speicher liegen (`ollama ps`)."""
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/api/ps", timeout=5)
        if response.status_code != 200:
            return []
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    return [str(item.get("name", "")) for item in payload.get("models", []) if item.get("name")]


def unload_model(name: str, base_url: str = DEFAULT_OLLAMA_URL) -> bool:
    """Wirft *name* aus dem Speicher (keep_alive=0).

    Zwei Modelle gleichzeitig sprengen auf vielen Rechnern den VRAM -- der
    Ollama-Runner stirbt dann mitten im Aufruf. Vor dem Sehtest raeumen wir
    deshalb auf. Fehler sind egal: schlimmstenfalls bleibt alles geladen.
    """
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/api/generate",
            json={"model": name, "keep_alive": 0},
            timeout=30,
        )
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def free_memory(base_url: str = DEFAULT_OLLAMA_URL) -> list[str]:
    """Entlaedt alle laufenden Modelle. Gibt zurueck, was entladen wurde."""
    return [name for name in loaded_models(base_url) if unload_model(name, base_url)]


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
        detail = f"{type(exc).__name__}: {exc}"
        if resource_problem(detail):
            return False, (
                "Der Ollama-Runner ist abgestuerzt -- dem Rechner ging der Speicher aus. "
                "Ein kleineres Modell sollte durchlaufen."
            )
        return False, detail

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
    if platform.system() == "Windows":
        return _windows_memory_gb()
    return None


def _windows_memory_gb() -> float | None:
    """Arbeitsspeicher unter Windows ueber GlobalMemoryStatusEx."""
    try:
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = (
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            )

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return round(status.ullTotalPhys / 1_073_741_824, 1)
    except (AttributeError, OSError, ValueError):
        return None


def _recommend(models: tuple[LocalModel, ...], memory_gb: float | None) -> LocalModel:
    """Groesstes Modell aus *models*, das in den Speicher passt."""
    available = memory_gb if memory_gb is not None else total_memory_gb()
    if available is None:
        return models[0]
    fitting = [model for model in models if model.needs_gb <= available]
    if not fitting:
        return min(models, key=lambda model: model.needs_gb)
    return max(fitting, key=lambda model: model.size_gb)


def recommend_model(memory_gb: float | None = None) -> LocalModel:
    """Waehlt das groesste Textmodell, das in den Speicher passt."""
    return _recommend(LOCAL_MODELS, memory_gb)


def recommend_vision_model(memory_gb: float | None = None) -> LocalModel:
    """Waehlt das groesste Vision-Modell, das in den Speicher passt."""
    return _recommend(VISION_MODELS, memory_gb)


def recommend_dual_model(memory_gb: float | None = None) -> LocalModel:
    """Waehlt das groesste Modell, das Werkzeuge UND Bilder beherrscht."""
    return _recommend(DUAL_MODELS, memory_gb)


def recommend_subagent_model(
    memory_gb: float | None = None, main: LocalModel | None = None
) -> LocalModel:
    """Waehlt ein leichtes Modell fuer die Subagenten.

    Gerechnet wird mit dem Platz, der neben dem Hauptmodell uebrig bleibt --
    beide liegen im Betrieb gleichzeitig im Speicher.
    """
    available = memory_gb if memory_gb is not None else usable_memory_gb()
    if available is not None and main is not None:
        available = max(1.0, available - main.size_gb - 1.0)
    return _recommend(SUBAGENT_MODELS, available)


def known_model(name: str) -> LocalModel | None:
    """Findet *name* im Katalog -- oder `None` bei einem freien Namen."""
    bare = name.split("/", 1)[-1].strip()
    for model in (*DUAL_MODELS, *LOCAL_MODELS, *VISION_MODELS, *SUBAGENT_MODELS):
        if model.name == bare:
            return model
    return None


def too_big(name: str, memory_gb: float | None = None) -> str:
    """Warnt, wenn ein Modell nicht in den Speicher passt.

    Returns:
        Leerer String, wenn alles passt oder die Groesse unbekannt ist --
        bei freien Namen raten wir nicht.
    """
    model = known_model(name)
    if model is None:
        return ""
    available = memory_gb if memory_gb is not None else usable_memory_gb()
    if available is None or model.needs_gb <= available:
        return ""
    return (
        f"{model.name} braucht etwa {model.needs_gb} GB, verfuegbar sind {available} GB. "
        "Der Ollama-Runner stirbt dann meist mitten im Aufruf."
    )


def fits_together(main: LocalModel, vision: LocalModel, memory_gb: float | None) -> bool:
    """Passen zwei Modelle gleichzeitig in den Speicher?

    scoutr entlaedt zwar zwischen den Aufrufen, aber wer beides parallel
    halten kann, spart bei jedem Bild das Nachladen.
    """
    if memory_gb is None:
        return False
    # Etwas Luft fuer Kontext und Grafikausgabe.
    return main.size_gb + vision.size_gb + 1.5 <= memory_gb


def plan_setup(memory_gb: float | None = None) -> tuple[LocalModel, LocalModel | None, str]:
    """Schlaegt eine Aufteilung vor: (Hauptmodell, Vision-Modell, Begruendung).

    Ist das Hauptmodell selbst bildfaehig, entfaellt das zweite Modell -- das
    ist auf knappen Karten deutlich sparsamer.
    """
    available = memory_gb if memory_gb is not None else usable_memory_gb()

    dual = recommend_dual_model(available)
    if available is not None and dual.needs_gb <= available:
        return dual, None, (
            f"{dual.name} kann Werkzeuge und Bilder -- ein Modell reicht "
            f"({dual.size_gb} GB statt zwei getrennter)."
        )

    main = recommend_model(available)
    vision = recommend_vision_model(available)
    if fits_together(main, vision, available):
        return main, vision, (
            f"{main.name} und {vision.name} passen zusammen in {available} GB."
        )
    return main, vision, (
        "Beide Modelle passen nicht gleichzeitig hinein -- scoutr laedt sie "
        "abwechselnd, das kostet bei jedem Bild ein paar Sekunden."
    )


def gpu_vram_gb() -> float | None:
    """Groesster freier VRAM einer NVIDIA-Karte in GB, sonst `None`.

    Fuer die Modellwahl zaehlt der VRAM, nicht der Arbeitsspeicher: laeuft ein
    Modell nicht vollstaendig auf der Karte, wird es zaeh oder stirbt.
    """
    if not shutil.which("nvidia-smi"):
        return None
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sizes: list[float] = []
    for line in output.stdout.strip().splitlines():
        try:
            sizes.append(int(line.strip()) / 1024)
        except ValueError:
            continue
    return round(max(sizes), 1) if sizes else None


def usable_memory_gb() -> float | None:
    """Wonach sich die Modellwahl richtet: VRAM, sonst Arbeitsspeicher."""
    vram = gpu_vram_gb()
    if vram:
        return vram
    memory = total_memory_gb()
    # Ohne GPU laeuft alles auf der CPU; dort ist nicht der ganze RAM nutzbar.
    return round(memory * 0.7, 1) if memory else None


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


# ---------------------------------------------------------------------------
# Sehtest: kann das Modell ein Bild wirklich anschauen?
# ---------------------------------------------------------------------------
#: Marker, an denen ein Absturz wegen Speichermangels zu erkennen ist.
RESOURCE_MARKERS = (
    "model runner has unexpectedly stopped",
    "resource limitations",
    "out of memory",
    "cudamalloc",
    "insufficient memory",
    "failed to allocate",
)


def resource_problem(detail: str) -> bool:
    """Deutet die Fehlermeldung auf zu wenig Speicher hin?"""
    lowered = detail.lower()
    return any(marker in lowered for marker in RESOURCE_MARKERS)


#: Farbe des Testbildes und die Woerter, die eine richtige Antwort enthaelt.
PROBE_COLOR = (220, 20, 20)
PROBE_COLOR_WORDS = ("rot", "red", "rouge", "rosso")

VISION_PROBE_MESSAGE = (
    "Welche Farbe hat dieses Bild? Antworte mit einem einzigen Wort."
)


def solid_png(rgb: tuple[int, int, int] = PROBE_COLOR, size: int = 64) -> bytes:
    """Erzeugt ein einfarbiges PNG -- ohne zusaetzliche Abhaengigkeit.

    Damit laesst sich pruefen, ob ein Modell das Bild tatsaechlich sieht:
    Die richtige Antwort ist bekannt.
    """
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    scanline = b"\x00" + bytes(rgb) * size
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanline * size, 9))
        + chunk(b"IEND", b"")
    )


def verify_vision(model_id: str, api_base: str = DEFAULT_OLLAMA_URL) -> tuple[bool, str]:
    """Prueft an einem echten Bild, ob *model_id* sehen kann.

    Gezeigt wird ein rotes Quadrat und nach der Farbe gefragt. Nennt das
    Modell die Farbe, schaut es das Bild wirklich an -- ein Textmodell kann
    das nicht.
    """
    import base64

    encoded = base64.b64encode(solid_png()).decode("ascii")
    try:
        import litellm

        litellm.suppress_debug_info = True
        response = litellm.completion(
            model=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROBE_MESSAGE},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ],
                }
            ],
            api_base=api_base,
            max_tokens=20,
            timeout=180,
        )
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if resource_problem(detail):
            return False, (
                "Der Ollama-Runner ist abgestuerzt -- dem Rechner ging der Speicher aus. "
                "Das sagt nichts darueber, ob das Modell sehen kann."
            )
        return False, detail

    answer = (response.choices[0].message.content or "").strip()
    if not answer:
        return False, "Das Modell hat nichts geantwortet."
    if any(word in answer.lower() for word in PROBE_COLOR_WORDS):
        return True, f"Testbild erkannt (Antwort: {answer[:40]})"
    return False, (
        f"Das Modell hat das Testbild nicht erkannt (Antwort: {answer[:60]}). "
        "Vermutlich kann es keine Bilder sehen."
    )


def vision_env_values(model: LocalModel | str) -> dict[str, str]:
    """Der `.env`-Eintrag fuer das Vision-Modell."""
    model_id = model.model_id if isinstance(model, LocalModel) else str(model)
    if "/" not in model_id:
        model_id = f"{MODEL_PREFIX}/{model_id}"
    return {"SCOUTR_VISION_MODEL": model_id}


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
