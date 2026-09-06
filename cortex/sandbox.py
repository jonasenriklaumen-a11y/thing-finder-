"""Die Werkstatt: eine Wegwerf-Maschine, in der Cortex Code ausfuehren darf.

Im Code-Modus laesst sich eine abgeschottete Umgebung zuschalten. Darin darf
Cortex alles -- Dateien anlegen, Programme starten, Tests laufen lassen --,
nur eines nicht: heraus. Diese Datei ist die Wand.

**Warum ueberhaupt eine Wand.** Code, den ein Sprachmodell schreibt, ist
fremder Code. Er kann falsch sein, er kann bei einem praeparierten Prompt
boesartig sein, und er kann beides sein, ohne dass man es ihm ansieht. Er
darf deshalb nie auf dem Rechner des Nutzers laufen -- auch nicht "nur kurz"
und auch nicht "nur zum Testen".

**Was die Wand ist.** Nach dem Stand der Technik (Stand 2026) reicht ein
gewoehnlicher Container fuer fremden Code nicht mehr: Kernel und Container
teilen sich denselben Kern, und eine Kernel-Luecke fuehrt aus jedem Container
heraus. Die drei belastbaren Stufen sind, von stark nach schwach:

1. **MicroVM** (Firecracker, Kata) -- eigener Kernel auf Hardware-
   Virtualisierung. Was drinnen passiert, bleibt hinter der CPU-Grenze.
2. **gVisor** (`runsc`) -- ein Kern im Nutzerraum faengt die Systemaufrufe ab
   und beantwortet sie selbst; das Programm sieht den echten Kernel nie.
3. **Container ohne Wurzelrechte** (rootless Podman) -- ein Ausbruch landet in
   einem unprivilegierten Nutzernamensraum, nicht bei root.

Cortex nimmt, was da ist, in genau dieser Reihenfolge, und faellt niemals auf
"dann eben direkt auf dem Rechner" zurueck. Ist keine der Stufen vorhanden,
gibt es die Werkstatt nicht, und das Werkzeug sagt, was zu installieren ist.

**Womit die Wand zusaetzlich gehaertet wird** -- jede Zeile hat einen Grund:

* `--network none` -- kein Netz. Weder hinaus noch ins Heimnetz. Damit ist der
  haeufigste Missbrauch (Daten abfliessen lassen, Nachladen von Schadcode) an
  der Wurzel erledigt.
* `--cap-drop ALL` -- keine Linux-Faehigkeiten. Kein Mounten, keine rohen
  Sockets, keine Kernelmodule.
* `--security-opt no-new-privileges` -- ein setuid-Programm kann drinnen keine
  Rechte mehr hinzugewinnen.
* `--read-only` -- das Wurzeldateisystem ist unveraenderlich. Geschrieben wird
  nur in `/work` und in ein kleines `/tmp` im Arbeitsspeicher.
* `--user` auf eine unprivilegierte Kennung -- niemand arbeitet als root.
* `--memory`, `--cpus`, `--pids-limit`, `--ulimit` -- eine Endlosschleife, eine
  Gabelbombe oder ein Speicherfresser bringt den Rechner nicht in die Knie.
* Keine Umgebungsvariablen von aussen. Die Schluessel des Nutzers haben in der
  Werkstatt nichts zu suchen, und sie kommen auch nicht hinein.
* Kein Verzeichnis des Rechners wird hineingereicht. Dateien gehen nur durch
  das Werkzeug hinein und heraus, ueber die Standardeingabe des Prozesses.

**Was danach uebrig bleibt: nichts.** Zwanzig Minuten nach der letzten Nutzung
werden Behaelter und Datentraeger geloescht. Beim naechsten Mal entsteht eine
neue, leere Werkstatt. Auch beim Beenden des Programms wird aufgeraeumt.
"""

from __future__ import annotations

import atexit
import contextlib
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

#: Wie lange die Werkstatt nach der letzten Nutzung stehen bleibt.
IDLE_MINUTES = 20

#: Grenzen. Ein Kern, ein Gigabyte Arbeitsspeicher, vier Gigabyte Platte.
CPUS = 1
MEMORY_MB = 1024
DISK_GB = 4

#: Prozesse und offene Dateien. Beides deckelt eine Gabelbombe.
PID_LIMIT = 256
FILE_LIMIT = 512

#: Wie lange ein einzelner Befehl laufen darf, und wie lange hoechstens.
COMMAND_TIMEOUT = 30
MAX_TIMEOUT = 120

#: Wie viel Ausgabe zurueckkommt. Der Rest wird abgeschnitten -- ein `yes`
#: ohne Deckel wuerde sonst das Kontextfenster fuellen.
MAX_OUTPUT = 8000

#: Wie viel eine einzelne Datei beim Lesen liefern darf.
MAX_READ_BYTES = 200_000

#: Das Abbild. Klein, mit Python und den ueblichen Werkzeugen.
DEFAULT_IMAGE = "python:3.12-slim"

#: Das Arbeitsverzeichnis in der Werkstatt. Nur hier darf geschrieben werden.
WORKDIR = "/work"

#: Die Kennung, unter der drinnen gearbeitet wird -- nicht root.
RUN_AS = "1000:1000"


class SandboxUnavailable(RuntimeError):
    """Es gibt keine belastbare Abschottung auf diesem Rechner."""


@dataclass(frozen=True)
class Runtime:
    """Womit die Werkstatt betrieben wird."""

    binary: str
    #: "gvisor", "podman" oder "docker" -- absteigend nach Staerke.
    kind: str
    label: str
    #: Zusaetzliche Argumente fuer `run`, etwa die gVisor-Laufzeit.
    extra: tuple[str, ...] = ()

    @property
    def strength(self) -> str:
        """Ein Satz darueber, wie stark die Wand hier ist."""
        return {
            "gvisor": (
                "gVisor: die Systemaufrufe beantwortet ein Kern im Nutzerraum, "
                "der echte Kernel wird nicht angefasst."
            ),
            "podman": (
                "Podman ohne Wurzelrechte: ein Ausbruch landet in einem "
                "unprivilegierten Nutzernamensraum, nicht bei root."
            ),
            "docker": (
                "Docker mit gehaertetem Profil: kein Netz, keine Faehigkeiten, "
                "unveraenderliches Wurzeldateisystem. Der Kernel ist geteilt -- "
                "fuer noch mehr Abstand waere gVisor oder eine MicroVM noetig."
            ),
        }[self.kind]


def _runs(binary: str, *args: str, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    """Ruft die Laufzeit auf -- ohne Shell, damit nichts interpretiert wird."""
    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _works(binary: str) -> bool:
    """Antwortet die Laufzeit ueberhaupt? Ein installierter Client ohne
    laufenden Dienst ist so gut wie keiner."""
    try:
        return _runs(binary, "info", "--format", "{{.ServerVersion}}").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _has_gvisor(binary: str) -> bool:
    """Kennt diese Laufzeit `runsc`?"""
    try:
        found = _runs(binary, "info", "--format", "{{.Runtimes}}")
    except (OSError, subprocess.SubprocessError):
        return False
    return found.returncode == 0 and "runsc" in found.stdout


def find_runtime() -> Runtime | None:
    """Sucht die staerkste verfuegbare Abschottung.

    Returns:
        Die Laufzeit, oder `None`, wenn es keine gibt. Dann gibt es auch keine
        Werkstatt -- ein Rueckfall auf den Rechner selbst waere genau das, was
        diese Datei verhindern soll.
    """
    for binary in ("podman", "docker"):
        if not shutil.which(binary) or not _works(binary):
            continue
        if _has_gvisor(binary):
            return Runtime(binary, "gvisor", f"{binary} + gVisor", ("--runtime", "runsc"))
    if shutil.which("podman") and _works("podman"):
        return Runtime("podman", "podman", "Podman (ohne Wurzelrechte)")
    if shutil.which("docker") and _works("docker"):
        return Runtime("docker", "docker", "Docker (gehaertet)")
    return None


@dataclass
class RunResult:
    """Was ein Befehl in der Werkstatt hinterlassen hat."""

    exit_code: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "seconds": round(self.seconds, 2),
        }
        if self.timed_out:
            payload["note"] = (
                "Abgebrochen: der Befehl lief laenger als erlaubt. Lass ihn "
                "kuerzer laufen oder teile ihn auf."
            )
        if self.truncated:
            payload["truncated"] = True
        return payload


def _cut(text: str, limit: int = MAX_OUTPUT) -> tuple[str, bool]:
    """Kuerzt lange Ausgaben und sagt, ob gekuerzt wurde."""
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return text[:half] + "\n… [gekuerzt] …\n" + text[-half:], True


#: Was in einem Pfad vorkommen darf. Alles andere waere entweder ein Versuch,
#: die Anfuehrungszeichen im `sh -c` zu verlassen, oder ein Tippfehler -- beides
#: will man nicht durchlassen.
PATH_CHARS = re.compile(r"^[A-Za-z0-9._/\- ]+$")


def safe_path(path: str) -> str:
    """Prueft einen Pfad in der Werkstatt.

    Erlaubt ist alles unterhalb von `/work`. Das ist keine Sicherheitsgrenze --
    die ist die Werkstatt selbst --, sondern Ordnung: Dateien, die ausserhalb
    liegen, waeren beim naechsten Start weg und wuerden nur verwirren.

    Raises:
        ValueError: Wenn der Pfad hinausfuehrt.
    """
    import posixpath

    raw = (path or "").strip()
    if not raw:
        raise ValueError("Ohne Pfad geht es nicht.")
    if not PATH_CHARS.match(raw):
        raise ValueError(
            "Im Pfad sind nur Buchstaben, Ziffern, Punkt, Strich, Unterstrich und "
            f"Schraegstrich erlaubt -- '{raw}' hat anderes darin."
        )
    full = raw if raw.startswith("/") else posixpath.join(WORKDIR, raw)
    full = posixpath.normpath(full)
    if full != WORKDIR and not full.startswith(WORKDIR + "/"):
        raise ValueError(f"Nur Pfade unterhalb von {WORKDIR} -- '{raw}' liegt ausserhalb.")
    return full


class Sandbox:
    """Eine Werkstatt: startet auf Bedarf, raeumt sich selbst weg."""

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        idle_minutes: int = IDLE_MINUTES,
        memory_mb: int = MEMORY_MB,
        disk_gb: int = DISK_GB,
        cpus: int = CPUS,
        on_event: Any = None,
    ) -> None:
        self.image = image or DEFAULT_IMAGE
        self.idle_seconds = max(60, int(idle_minutes) * 60)
        self.memory_mb = max(128, int(memory_mb))
        self.disk_gb = max(1, int(disk_gb))
        self.cpus = max(1, int(cpus))
        self.on_event = on_event
        self.runtime: Runtime | None = None
        self._name = ""
        self._volume = ""
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._quota_warned = False
        #: Kann die Laufzeit eine Platzquote? Wird beim ersten Start geklaert.
        self._quota_ok = True
        #: Wann zuletzt nachgemessen wurde (nur ohne Quote noetig).
        self._last_quota_check = 0.0
        #: Wann zuletzt gearbeitet wurde -- fuer die Anzeige.
        self.last_used = 0.0

    # -- Zustand ----------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def alive(self) -> bool:
        return bool(self._name)

    def _emit(self, event: str, **payload: Any) -> None:
        # Das Aufraeumen laeuft in einem eigenen Thread und meldet sich unter
        # Umstaenden, wenn die Anfrage laengst vorbei ist. Ein Fehler im
        # Empfaenger darf die Werkstatt nicht stehen lassen.
        if not self.on_event:
            return
        with contextlib.suppress(Exception):  # nur Anzeige, nie kritisch
            self.on_event(event, payload)

    # -- Aufbau -----------------------------------------------------------
    def ensure(self) -> str:
        """Startet die Werkstatt, falls sie nicht schon laeuft.

        Returns:
            Der Name des Behaelters.

        Raises:
            SandboxUnavailable: Wenn es keine belastbare Abschottung gibt oder
                der Start scheitert.
        """
        with self._lock:
            if self._name and self._running():
                self._touch_locked()
                return self._name
            self._name = ""
            runtime = self.runtime or find_runtime()
            if runtime is None:
                raise SandboxUnavailable(
                    "Auf diesem Rechner gibt es keine Abschottung, der ich fremden "
                    "Code anvertrauen wuerde. Installiere Podman (ohne Wurzelrechte) "
                    "oder Docker; am besten zusaetzlich gVisor. Auf dem Rechner "
                    "selbst fuehre ich nichts aus."
                )
            self.runtime = runtime
            token = uuid.uuid4().hex[:12]
            self._name = f"cortex-werkstatt-{token}"
            self._volume = f"cortex-werkstatt-{token}"
            self._quota_warned = False
            self._emit("vm_start", runtime=runtime.label, image=self.image)
            try:
                self._create_volume()
                self._start_container()
            except Exception:
                # Halbe Werkstatt ist schlimmer als keine: alles wieder weg.
                self._destroy_locked("Start fehlgeschlagen")
                raise
            self._touch_locked()
            return self._name

    def _create_volume(self) -> None:
        """Legt den Datentraeger an und macht ihn fuer die Kennung schreibbar.

        Der Datentraeger gehoert dieser Werkstatt allein und verschwindet mit
        ihr. Bind-Mounts vom Rechner gibt es bewusst nicht: was drinnen
        passiert, soll drinnen bleiben.
        """
        runtime = self.runtime
        assert runtime is not None
        made = _runs(runtime.binary, "volume", "create", self._volume, timeout=20)
        if made.returncode != 0:
            raise SandboxUnavailable(
                "Datentraeger liess sich nicht anlegen: " + made.stderr.strip()[:300]
            )
        # Einmal kurz als root hinein, nur um die Rechte zu setzen. Dieser
        # Behaelter hat kein Netz, keine Faehigkeiten ausser CHOWN und lebt
        # einen Sekundenbruchteil.
        prepared = _runs(
            runtime.binary,
            "run", "--rm",
            "--network", "none",
            "--cap-drop", "ALL",
            "--cap-add", "CHOWN",
            "--security-opt", "no-new-privileges",
            "--user", "0:0",
            "-v", f"{self._volume}:{WORKDIR}",
            self.image,
            "chown", RUN_AS, WORKDIR,
            timeout=120,
        )
        if prepared.returncode != 0:
            raise SandboxUnavailable(
                "Die Werkstatt liess sich nicht vorbereiten: " + prepared.stderr.strip()[:300]
            )

    def _run_flags(self) -> list[str]:
        """Die Haertung. Jede Zeile steht im Kopf der Datei begruendet."""
        runtime = self.runtime
        assert runtime is not None
        flags = [
            "run", "--detach",
            "--name", self._name,
            *runtime.extra,
            # -- Abschottung --
            "--network", "none",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--user", RUN_AS,
            # -- Grenzen --
            "--memory", f"{self.memory_mb}m",
            "--memory-swap", f"{self.memory_mb}m",
            "--cpus", str(self.cpus),
            "--pids-limit", str(PID_LIMIT),
            "--ulimit", f"nofile={FILE_LIMIT}:{FILE_LIMIT}",
            "--ulimit", f"fsize={self.disk_gb * 1024 * 1024 * 1024}",
            # -- Platz zum Arbeiten --
            "-v", f"{self._volume}:{WORKDIR}",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            # Nicht jede Laufzeit kennt diese Quote (sie braucht xfs mit
            # Projektquoten). Wo sie fehlt, faellt der Start damit aus -- dann
            # startet _start_container ohne sie und wir messen stattdessen nach.
            *(("--storage-opt", f"size={self.disk_gb}G") if self._quota_ok else ()),
            "--workdir", WORKDIR,
            # -- Umgebung: nur das Noetigste, nichts vom Rechner --
            "--env", "HOME=" + WORKDIR,
            "--env", "PATH=/usr/local/bin:/usr/bin:/bin",
            "--env", "LANG=C.UTF-8",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--label", "cortex-werkstatt=1",
        ]
        return flags

    def _start_container(self) -> None:
        runtime = self.runtime
        assert runtime is not None
        args = [
            *self._run_flags(),
            self.image,
            # Nichts tun, aber am Leben bleiben. `sleep infinity` haelt genau
            # einen Prozess und kostet nichts.
            "sleep", "infinity",
        ]
        started = _runs(runtime.binary, *args, timeout=300)
        if started.returncode != 0 and "storage-opt" in started.stderr:
            # Die Laufzeit kann keine Quote -- dann eben ohne, und der Platz
            # wird nach jedem Befehl nachgemessen.
            self._quota_ok = False
            args = [*self._run_flags(), self.image, "sleep", "infinity"]
            started = _runs(runtime.binary, *args, timeout=300)
        if started.returncode != 0:
            raise SandboxUnavailable(
                "Die Werkstatt liess sich nicht starten: "
                + started.stderr.strip()[:300]
                + f" (Fehlt das Abbild? '{runtime.binary} pull {self.image}' holt es einmalig.)"
            )

    def _running(self) -> bool:
        runtime = self.runtime
        if runtime is None or not self._name:
            return False
        found = _runs(runtime.binary, "inspect", "--format", "{{.State.Running}}", self._name)
        return found.returncode == 0 and found.stdout.strip() == "true"

    # -- Arbeiten ---------------------------------------------------------
    def run(self, command: str, timeout: int = COMMAND_TIMEOUT) -> RunResult:
        """Fuehrt *command* in der Werkstatt aus.

        Der Befehl geht als Argument an die Laufzeit, nie durch eine Shell auf
        diesem Rechner -- interpretiert wird er erst drinnen.
        """
        command = (command or "").strip()
        if not command:
            raise ValueError("Ohne Befehl gibt es nichts zu tun.")
        limit = max(1, min(int(timeout or COMMAND_TIMEOUT), MAX_TIMEOUT))
        name = self.ensure()
        runtime = self.runtime
        assert runtime is not None
        self._emit("vm_run", command=command[:200])
        started = time.monotonic()
        try:
            done = _runs(
                runtime.binary,
                "exec", "--user", RUN_AS, "--workdir", WORKDIR, name,
                "sh", "-c", command,
                # Etwas Luft, damit die Laufzeit selbst antworten kann, bevor
                # wir sie abwuergen.
                timeout=limit + 5,
            )
            out, cut_out = _cut(done.stdout)
            err, cut_err = _cut(done.stderr, MAX_OUTPUT // 2)
            result = RunResult(
                exit_code=done.returncode,
                stdout=out,
                stderr=err,
                seconds=time.monotonic() - started,
                truncated=cut_out or cut_err,
            )
        except subprocess.TimeoutExpired:
            # Der Prozess drinnen laeuft womoeglich weiter -- also weg damit.
            self._kill_processes()
            result = RunResult(
                exit_code=124,
                stdout="",
                stderr="",
                seconds=time.monotonic() - started,
                timed_out=True,
            )
        self.touch()
        self._check_quota(result)
        self._emit("vm_done", exit_code=result.exit_code, seconds=round(result.seconds, 2))
        return result

    def _check_quota(self, result: RunResult) -> None:
        """Misst den Platz nach, wo die Laufzeit keine Quote kann.

        Nicht nach jedem Befehl: `du` kostet einen Prozessstart, und in
        dreissig Sekunden laeuft an einem Kern keine Platte voll.
        """
        if self._quota_ok:
            return
        jetzt = time.monotonic()
        if jetzt - self._last_quota_check < 30 and not self._quota_warned:
            return
        self._last_quota_check = jetzt
        voll = self.usage_gb()
        if voll <= self.disk_gb:
            self._quota_warned = False
            return
        if not self._quota_warned:
            self._quota_warned = True
        result.stderr = (
            f"[Werkstatt] {voll} GB belegt, erlaubt sind {self.disk_gb} GB. "
            "Raeum auf (rm), bevor du weiterschreibst.\n" + result.stderr
        )

    def _kill_processes(self) -> None:
        """Beendet, was nach einer Zeitueberschreitung noch laeuft."""
        runtime = self.runtime
        if runtime is None or not self._name:
            return
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            _runs(runtime.binary, "restart", "--time", "1", self._name, timeout=60)

    def write(self, path: str, text: str) -> dict[str, Any]:
        """Legt eine Datei in der Werkstatt an."""
        full = safe_path(path)
        name = self.ensure()
        runtime = self.runtime
        assert runtime is not None
        data = (text or "").encode("utf-8")
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("Die Datei ist zu gross fuer den Weg durch das Werkzeug (4 MB).")
        parent = full.rsplit("/", 1)[0] or WORKDIR
        done = subprocess.run(
            [
                runtime.binary, "exec", "--interactive",
                "--user", RUN_AS, "--workdir", WORKDIR, name,
                "sh", "-c", f'mkdir -p "{parent}" && cat > "{full}"',
            ],
            input=data,
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.touch()
        if done.returncode != 0:
            grund = done.stderr.decode("utf-8", "replace")[:300]
            return {"error": grund or "Schreiben ging nicht."}
        self._emit("vm_write", path=full, bytes=len(data))
        return {"written": full, "bytes": len(data)}

    def read(self, path: str, max_bytes: int = MAX_READ_BYTES) -> dict[str, Any]:
        """Liest eine Datei aus der Werkstatt."""
        full = safe_path(path)
        name = self.ensure()
        runtime = self.runtime
        assert runtime is not None
        limit = max(1, min(int(max_bytes or MAX_READ_BYTES), MAX_READ_BYTES))
        done = _runs(
            runtime.binary,
            "exec", "--user", RUN_AS, "--workdir", WORKDIR, name,
            "sh", "-c", f'head -c {limit + 1} "{full}"',
            timeout=60,
        )
        self.touch()
        if done.returncode != 0:
            return {"error": done.stderr.strip()[:300] or "Datei nicht lesbar."}
        text = done.stdout
        cut = len(text.encode("utf-8", "ignore")) > limit
        if cut:
            text = text[:limit]
        return {"path": full, "text": text, "truncated": cut}

    def usage_gb(self) -> float:
        """Wie voll die Werkstatt ist -- in Gigabyte."""
        if not self.alive or self.runtime is None:
            return 0.0
        done = _runs(
            self.runtime.binary,
            "exec", "--user", RUN_AS, self._name,
            "sh", "-c", f"du -sk {WORKDIR} 2>/dev/null | cut -f1",
            timeout=30,
        )
        try:
            return round(int(done.stdout.strip() or 0) / (1024 * 1024), 2)
        except ValueError:
            return 0.0

    # -- Abbau ------------------------------------------------------------
    def touch(self) -> None:
        """Setzt die Uhr zurueck: erst zwanzig Minuten Ruhe raeumen auf."""
        with self._lock:
            self._touch_locked()

    def _touch_locked(self) -> None:
        self.last_used = time.time()
        if self._timer is not None:
            self._timer.cancel()
        if not self._name:
            self._timer = None
            return
        self._timer = threading.Timer(self.idle_seconds, self._idle_out)
        self._timer.daemon = True
        self._timer.start()

    def _idle_out(self) -> None:
        self.stop("seit 20 Minuten unbenutzt")

    def stop(self, reason: str = "aufgeraeumt") -> None:
        """Loescht Behaelter und Datentraeger -- ohne Rueckstand."""
        with self._lock:
            if not self._name and not self._volume:
                return
            self._destroy_locked(reason)

    def _destroy_locked(self, reason: str) -> None:
        runtime = self.runtime
        name, volume = self._name, self._volume
        self._name, self._volume = "", ""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if runtime is None:
            return
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            if name:
                _runs(runtime.binary, "rm", "--force", "--volumes", name, timeout=60)
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            if volume:
                _runs(runtime.binary, "volume", "rm", "--force", volume, timeout=60)
        if name:
            self._emit("vm_stop", reason=reason)

    def status(self) -> dict[str, Any]:
        """Kurzer Zustandsbericht fuer Anzeige und Werkzeug."""
        if not self.alive:
            return {"running": False}
        ruhe = max(0, int(self.idle_seconds - (time.time() - self.last_used)))
        return {
            "running": True,
            "runtime": self.runtime.label if self.runtime else "",
            "image": self.image,
            "cpus": self.cpus,
            "memory_mb": self.memory_mb,
            "disk_gb": self.disk_gb,
            "idle_left_seconds": ruhe,
        }


def sweep(runtime: Runtime | None = None) -> int:
    """Raeumt vergessene Werkstaetten weg -- etwa nach einem Absturz.

    Erkannt werden sie an ihrer Beschriftung. Beim Start des Servers einmal
    aufgerufen, bleibt von einem harten Abbruch nichts liegen.

    Returns:
        Wie viele Behaelter entfernt wurden.
    """
    runtime = runtime or find_runtime()
    if runtime is None:
        return 0
    found = _runs(
        runtime.binary, "ps", "--all", "--quiet", "--filter", "label=cortex-werkstatt=1",
        timeout=30,
    )
    ids = [line.strip() for line in found.stdout.splitlines() if line.strip()]
    for container in ids:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            _runs(runtime.binary, "rm", "--force", "--volumes", container, timeout=60)
    volumes = _runs(
        runtime.binary, "volume", "ls", "--quiet", timeout=30
    )
    for volume in volumes.stdout.splitlines():
        volume = volume.strip()
        if volume.startswith("cortex-werkstatt-"):
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                _runs(runtime.binary, "volume", "rm", "--force", volume, timeout=60)
    return len(ids)


#: Eine Werkstatt je Programm. Mehrere gleichzeitig waeren mehrere
#: Gigabyte, die im Hintergrund liegen -- und niemand braucht zwei.
_shared: Sandbox | None = None
_shared_lock = threading.Lock()


def shared(settings: Any = None, on_event: Any = None) -> Sandbox:
    """Die gemeinsame Werkstatt dieses Programms."""
    global _shared
    with _shared_lock:
        if _shared is None:
            # Genau einmal: sonst haelt atexit jede je gebaute Werkstatt fest.
            atexit.register(_stop_shared)
            _shared = Sandbox(
                image=getattr(settings, "vm_image", "") or DEFAULT_IMAGE,
                idle_minutes=int(
                    getattr(settings, "vm_idle_minutes", IDLE_MINUTES) or IDLE_MINUTES
                ),
                memory_mb=int(getattr(settings, "vm_memory_mb", MEMORY_MB) or MEMORY_MB),
                disk_gb=int(getattr(settings, "vm_disk_gb", DISK_GB) or DISK_GB),
                cpus=int(getattr(settings, "vm_cpus", CPUS) or CPUS),
            )
        _shared.on_event = on_event
        return _shared


def _stop_shared() -> None:
    """Beim Beenden des Programms: die Werkstatt geht mit."""
    box = _shared
    if box is not None:
        box.stop("Programm beendet")


def forget_shared() -> None:
    """Vergisst die gemeinsame Werkstatt -- fuer Tests und beim Neuaufbau."""
    global _shared
    with _shared_lock:
        if _shared is not None:
            _shared.stop("neu aufgebaut")
        _shared = None
