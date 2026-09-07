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

#: Cortex ist auf zwei Anbieter spezialisiert: **NVIDIA NIM** und **Mistral**.
#:
#: Das ist eine Entscheidung, keine Sparmassnahme. Eine Liste mit dreizehn
#: Anbietern sieht grosszuegig aus, bedeutet aber dreizehnmal "irgendein
#: Standardmodell, ungetestet, mit unbekannten Grenzen". Zwei Anbieter kann
#: man kennen: welches Modell wofuer, wie schnell es antwortet, wie viele
#: Anfragen pro Minute es vertraegt -- und genau danach richtet sich Cortex
#: dann auch (siehe PROVIDER_LIMITS und `cortex/pace.py`).
#:
#: Lokale Modelle ueber Ollama bleiben davon unberuehrt: sie sind kein
#: Anbieter, sondern der Weg, ganz ohne einen auszukommen.
#:
#: Diese Texte liest der Nutzer -- hier gehoeren echte Umlaute hin, anders
#: als in Kommentaren und Codenamen.
PROVIDER_NOTES: dict[str, str] = {
    "nvidia_nim": "Offene Modelle bei NVIDIA, großzügiges Freikontingent (40 Anfragen/Minute)",
    "mistral": "Europäischer Anbieter, antwortet schnell, Server in der EU",
    "ollama_chat": "Läuft auf deinem Rechner: keine Kosten, nichts verlässt das Haus",
}

#: Wie ein Anbieter in der Auswahl heisst. "ollama_chat" sagt niemandem etwas.
PROVIDER_LABELS: dict[str, str] = {
    "nvidia_nim": "NVIDIA",
    "mistral": "Mistral",
    "ollama_chat": "lokal",
    "ollama": "lokal",
}

#: Das Arbeitspferd je Anbieter: gut genug fuer alles, was Cortex den ganzen
#: Tag tut -- recherchieren, lesen, zusammenfassen.
PROVIDER_MODELS: dict[str, str] = {
    "nvidia_nim": "nvidia_nim/meta/llama-3.3-70b-instruct",
    "mistral": "mistral/mistral-large-latest",
}

#: Das schnelle kleine Modell je Anbieter. Es macht die Arbeit, bei der es
#: auf Tempo ankommt und nicht auf Tiefe: die Vorpruefung vor jeder Frage,
#: die Planung -- und vor allem die Rechercheagenten, von denen es viele
#: gleichzeitig gibt. Ein 70B-Modell fuer "such mir die Oeffnungszeiten"
#: kostet Sekunden, die sich mit jedem Agenten summieren.
FAST_MODELS: dict[str, str] = {
    "nvidia_nim": "nvidia_nim/meta/llama-3.1-8b-instruct",
    "mistral": "mistral/mistral-small-latest",
}

#: Fuers Programmieren das jeweils staerkste Modell eines Anbieters -- dort,
#: wo es ein anderes ist als das Arbeitspferd.
CODING_MODELS: dict[str, str] = {
    "nvidia_nim": "nvidia_nim/qwen/qwen2.5-coder-32b-instruct",
    "mistral": "mistral/codestral-latest",
}

#: Grobe Rangfolge. Sie entscheidet, welcher der eingerichteten Anbieter im
#: Code- und im Pro-Modus zum Zug kommt. Mistral steht vorn, weil es
#: spuerbar schneller antwortet; wer es anders sieht, traegt unter
#: CORTEX_CODE_MODEL sein Modell selbst ein.
CODING_ORDER: tuple[str, ...] = ("mistral", "nvidia_nim")

PROVIDER_KEYS: dict[str, str] = {
    "nvidia_nim": "NVIDIA_NIM_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}

#: Was der Anbieter vertraegt: (Anfragen je Minute, gleichzeitige Anfragen).
#:
#: Die vierzig Anfragen pro Minute bei NVIDIA sind der Grund, warum Cortex
#: dort "sehr sehr lange" gebraucht hat: vierundvierzig Agenten schicken
#: ihre Anfragen auf einmal los, die Haelfte kommt als 429 zurueck, jede
#: davon wird wiederholt -- und aus einer Recherche werden Minuten Wartezeit,
#: in denen nichts passiert. Cortex haelt das Mass jetzt selbst ein (siehe
#: `cortex/pace.py`): gleichmaessig verteilt statt in Wellen gegen die Wand.
#:
#: Wer einen groesseren Vertrag hat, hebt es mit CORTEX_RPM und
#: CORTEX_PARALLEL_CALLS an.
PROVIDER_LIMITS: dict[str, tuple[int, int]] = {
    "nvidia_nim": (40, 4),
    "mistral": (240, 8),
}


def provider_limits(provider: str) -> tuple[int, int]:
    """(Anfragen je Minute, gleichzeitige Anfragen) fuer einen Anbieter.

    Unbekannt heisst unbegrenzt: lokale Modelle und alles, was jemand von
    Hand eintraegt, drosselt Cortex nicht.
    """
    return PROVIDER_LIMITS.get(provider or "", (0, 0))


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
def strongest_models(
    settings: Any, limit: int = 3, purpose: str = "work"
) -> list[dict[str, str]]:
    """Die staerksten erreichbaren Modelle -- das beste zuerst.

    Args:
        purpose: "code" nimmt je Anbieter das Modell fuers Programmieren
            (Codestral, Qwen-Coder), "work" das Arbeitspferd. Der Unterschied
            zaehlt: ein Code-Modell auf eine Recherchefrage anzusetzen ist
            genauso verkehrt wie umgekehrt.

    Die Rangfolge: ein von Hand eingetragenes Code-Modell schlaegt alles, dann
    kommen die eingerichteten Anbieter nach `CODING_ORDER`, danach die lokal
    installierten Modelle, das groesste zuerst -- bei Ollama ist die Groesse
    der beste Anhaltspunkt, den es ohne Messung gibt.

    Gebraucht wird die Liste im Code- und im Pro-Modus: dort soll man nicht
    aus allem waehlen koennen, sondern aus den staerksten. Ein schwaches
    Modell ist genau dort am teuersten.
    """
    from cortex.config import provider_of
    from cortex.local_model import DEFAULT_OLLAMA_URL, installed_models, model_size_gb

    ranked: list[dict[str, str]] = []

    def dazu(model_id: str, kind: str, note: str) -> None:
        model_id = (model_id or "").strip()
        if not model_id or any(eintrag["id"] == model_id for eintrag in ranked):
            return
        ranked.append(
            {
                "id": model_id,
                "label": model_id.split("/", 1)[-1],
                "kind": kind,
                "note": note,
            }
        )

    wanted = str(getattr(settings, "code_model", "") or "").strip()
    if wanted:
        dazu(wanted, PROVIDER_LABELS.get(provider_of(wanted), "eigenes"), "von dir eingetragen")

    for provider in CODING_ORDER:
        key_name = PROVIDER_KEYS.get(provider, "")
        if not key_name or not os.environ.get(key_name, "").strip():
            continue
        stark = (
            CODING_MODELS.get(provider) if purpose == "code" else ""
        ) or PROVIDER_MODELS.get(provider, "")
        dazu(
            stark,
            PROVIDER_LABELS.get(provider, provider),
            "Fürs Programmieren"
            if purpose == "code" and CODING_MODELS.get(provider) == stark
            else PROVIDER_NOTES.get(provider, "Über die Schnittstelle des Anbieters"),
        )

    base = getattr(settings, "api_base", "") or DEFAULT_OLLAMA_URL
    try:
        local = installed_models(base)
    except Exception:
        local = []
    sized = sorted(
        ((model_size_gb(name, base) or 0.0, name) for name in local), reverse=True
    )
    for groesse, name in sized:
        dazu(
            f"ollama_chat/{name}",
            "lokal",
            f"{groesse:.0f} GB auf deinem Rechner" if groesse else "Läuft auf deinem Rechner",
        )

    return ranked[: max(1, int(limit))]


def strongest_model(settings: Any, purpose: str = "work") -> str:
    """Das staerkste Modell, das gerade erreichbar ist.

    Returns:
        Eine Modell-Kennung, oder "" wenn nichts Besseres zu finden war als
        das ohnehin eingestellte Modell.
    """
    beste = strongest_models(settings, limit=1, purpose=purpose)
    return beste[0]["id"] if beste else ""


def fast_model(settings: Any) -> str:
    """Das schnelle kleine Modell -- fuer Vorpruefung, Planung und Agenten.

    Es richtet sich nach dem HAUPTmodell: wer bei NVIDIA arbeitet, soll seine
    Agenten nicht bei Mistral laufen lassen (zwei Schluessel, zwei Grenzen,
    zwei Rechnungen). Gibt es zum Anbieter kein kleines Modell -- oder laeuft
    das Hauptmodell lokal --, bleibt es beim Hauptmodell.
    """
    from cortex.config import provider_of

    eigen = str(getattr(settings, "subagent_model", "") or "").strip()
    if eigen:
        return eigen
    return FAST_MODELS.get(provider_of(str(getattr(settings, "model", "") or "")), "")


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
        # Dazu das schnelle kleine Modell desselben Anbieters. Nicht jede
        # Frage braucht das Arbeitspferd -- wer vor allem Tempo will, waehlt
        # hier, und niemand muss dafuer eine Modell-ID von Hand eintippen.
        schnell = FAST_MODELS.get(provider, "")
        if schnell and schnell != model_id:
            found.append(
                {
                    "id": schnell,
                    "label": schnell.split("/", 1)[-1],
                    "kind": PROVIDER_LABELS.get(provider, provider),
                    "note": "Klein und schnell: kurze Antworten in Sekunden",
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
