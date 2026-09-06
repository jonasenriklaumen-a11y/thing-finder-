"""Tests fuer die Werkstatt.

Ein echter Behaelter laeuft hier nicht -- in der Pruefkette gibt es weder
Docker noch Podman, und ein Test, der ein Abbild aus dem Netz zieht, waere
kein Test mehr, sondern eine Wette. Geprueft wird deshalb genau das, was man
ohne Laufzeit pruefen kann und was zaehlt: **welche Befehlszeile gebaut wird**.
Dort steht die Sicherheit. Faellt eine Haertung heraus, faellt hier ein Test.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from cortex import sandbox as werkstatt


class FakeRun:
    """Merkt sich jede Befehlszeile und antwortet, wie man es ihr sagt."""

    def __init__(self, antworten: dict[str, tuple[int, str, str]] | None = None) -> None:
        self.aufrufe: list[list[str]] = []
        self.antworten = antworten or {}

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.aufrufe.append(list(args))
        for schluessel, (code, out, err) in self.antworten.items():
            if schluessel in args:
                return subprocess.CompletedProcess(args, code, out, err)
        return subprocess.CompletedProcess(args, 0, "", "")

    def zeile(self, enthaelt: str) -> list[str]:
        """Die erste Zeile, in der *enthaelt* vorkommt."""
        for aufruf in self.aufrufe:
            if enthaelt in aufruf:
                return aufruf
        raise AssertionError(f"keine Zeile mit {enthaelt!r} in {self.aufrufe}")


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> tuple[werkstatt.Sandbox, FakeRun]:
    fake = FakeRun({"inspect": (0, "true\n", "")})
    monkeypatch.setattr(subprocess, "run", fake)
    sandkasten = werkstatt.Sandbox(image="python:3.12-slim")
    sandkasten.runtime = werkstatt.Runtime("docker", "docker", "Docker (gehaertet)")
    return sandkasten, fake


# ---------------------------------------------------------------------------
# Die Wand
# ---------------------------------------------------------------------------
def test_the_container_is_locked_down(box: tuple[werkstatt.Sandbox, FakeRun]) -> None:
    """Jede dieser Zeilen hat einen Grund -- keine darf verschwinden."""
    sandkasten, fake = box
    sandkasten.ensure()
    zeile = fake.zeile("--detach")

    def paar(flagge: str) -> str:
        return zeile[zeile.index(flagge) + 1]

    assert paar("--network") == "none", "kein Netz -- weder hinaus noch ins Heimnetz"
    assert paar("--cap-drop") == "ALL", "keine Linux-Faehigkeiten"
    assert paar("--security-opt") == "no-new-privileges"
    assert "--read-only" in zeile, "unveraenderliches Wurzeldateisystem"
    assert paar("--user") == werkstatt.RUN_AS, "nicht als root"
    assert paar("--memory") == "1024m"
    assert paar("--memory-swap") == "1024m", "kein Auslagern -- sonst waere das Limit keins"
    assert paar("--cpus") == "1"
    assert paar("--pids-limit") == str(werkstatt.PID_LIMIT), "gegen die Gabelbombe"
    assert "/tmp:rw,noexec,nosuid,size=64m" in zeile


def test_nothing_of_the_computer_goes_in(box: tuple[werkstatt.Sandbox, FakeRun]) -> None:
    """Kein Verzeichnis, keine Umgebungsvariable, kein Schluessel."""
    sandkasten, fake = box
    sandkasten.ensure()
    zeile = fake.zeile("--detach")

    mounts = [zeile[i + 1] for i, teil in enumerate(zeile) if teil == "-v"]
    assert mounts, "ein Arbeitsverzeichnis braucht es"
    for mount in mounts:
        quelle = mount.split(":")[0]
        assert not quelle.startswith("/"), f"{mount} reicht ein Verzeichnis des Rechners hinein"
        assert quelle.startswith("cortex-werkstatt-"), mount

    umgebung = [zeile[i + 1] for i, teil in enumerate(zeile) if teil == "--env"]
    erlaubt = {"HOME", "PATH", "LANG", "PYTHONDONTWRITEBYTECODE"}
    for eintrag in umgebung:
        name = eintrag.split("=", 1)[0]
        assert name in erlaubt, f"{name} hat in der Werkstatt nichts zu suchen"


def test_the_strongest_runtime_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """gVisor vor Podman vor Docker -- und niemals der Rechner selbst."""
    vorhanden: set[str] = set()
    monkeypatch.setattr(
        werkstatt.shutil, "which", lambda name: f"/usr/bin/{name}" if name in vorhanden else None
    )
    monkeypatch.setattr(werkstatt, "_works", lambda binary: binary in vorhanden)
    monkeypatch.setattr(werkstatt, "_has_gvisor", lambda binary: "gvisor" in vorhanden)

    assert werkstatt.find_runtime() is None, "ohne Laufzeit gibt es keine Werkstatt"

    vorhanden = {"docker"}
    gefunden = werkstatt.find_runtime()
    assert gefunden is not None and gefunden.kind == "docker"

    vorhanden = {"docker", "podman"}
    gefunden = werkstatt.find_runtime()
    assert gefunden is not None and gefunden.kind == "podman", "ohne Wurzelrechte ist besser"

    vorhanden = {"docker", "podman", "gvisor"}
    gefunden = werkstatt.find_runtime()
    assert gefunden is not None and gefunden.kind == "gvisor"
    assert "--runtime" in gefunden.extra and "runsc" in gefunden.extra


def test_without_a_runtime_nothing_runs_on_the_computer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der wichtigste Test der Datei: es gibt keinen Rueckfall."""
    monkeypatch.setattr(werkstatt, "find_runtime", lambda: None)
    sandkasten = werkstatt.Sandbox()
    with pytest.raises(werkstatt.SandboxUnavailable) as gescheitert:
        sandkasten.ensure()
    assert "fuehre ich nichts aus" in str(gescheitert.value)


# ---------------------------------------------------------------------------
# Arbeiten darin
# ---------------------------------------------------------------------------
def test_a_command_never_touches_a_shell_of_this_computer(
    box: tuple[werkstatt.Sandbox, FakeRun]
) -> None:
    """Der Befehl geht als Argument an die Laufzeit -- interpretiert wird er
    erst drinnen. Sonst waere ein `; rm -rf ~` aus dem Modell ein Problem."""
    sandkasten, fake = box
    sandkasten.run("echo hallo; rm -rf /")
    zeile = fake.zeile("exec")
    assert zeile[-1] == "echo hallo; rm -rf /", "der Befehl steht unzerlegt am Ende"
    assert zeile[-2] == "-c" and zeile[-3] == "sh"
    assert "--user" in zeile and zeile[zeile.index("--user") + 1] == werkstatt.RUN_AS


def test_paths_stay_inside_the_workshop() -> None:
    assert werkstatt.safe_path("loesung.py") == "/work/loesung.py"
    assert werkstatt.safe_path("/work/unter/a.txt") == "/work/unter/a.txt"
    for boese in ("../../etc/passwd", "/etc/passwd", "/work/../etc/shadow", ""):
        with pytest.raises(ValueError):
            werkstatt.safe_path(boese)


def test_long_output_is_cut(box: tuple[werkstatt.Sandbox, FakeRun]) -> None:
    """Ein `yes` ohne Deckel wuerde das Kontextfenster fuellen."""
    sandkasten, fake = box
    fake.antworten = {"exec": (0, "x" * 50_000, ""), "inspect": (0, "true\n", "")}
    ergebnis = sandkasten.run("yes")
    assert len(ergebnis.stdout) < werkstatt.MAX_OUTPUT + 100
    assert ergebnis.truncated
    assert "gekuerzt" in ergebnis.stdout


def test_a_hanging_command_is_ended(box: tuple[werkstatt.Sandbox, FakeRun]) -> None:
    sandkasten, fake = box

    def haengt(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        fake.aufrufe.append(list(args))
        if "exec" in args:
            raise subprocess.TimeoutExpired(args, 5)
        return subprocess.CompletedProcess(args, 0, "true\n", "")

    sandkasten.runtime = werkstatt.Runtime("docker", "docker", "Docker")
    original = subprocess.run
    try:
        subprocess.run = haengt  # type: ignore[assignment]
        ergebnis = sandkasten.run("sleep 999", timeout=1)
    finally:
        subprocess.run = original  # type: ignore[assignment]
    assert ergebnis.timed_out and ergebnis.exit_code == 124
    assert "Abgebrochen" in ergebnis.as_dict()["note"]


def test_the_timeout_has_an_upper_bound(box: tuple[werkstatt.Sandbox, FakeRun]) -> None:
    """Wer 10000 Sekunden verlangt, bekommt trotzdem hoechstens das Maximum."""
    sandkasten, fake = box
    aufgezeichnet: dict[str, Any] = {}

    def merke(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        fake.aufrufe.append(list(args))
        if "exec" in args:
            aufgezeichnet["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args, 0, "true\n", "")

    original = subprocess.run
    try:
        subprocess.run = merke  # type: ignore[assignment]
        sandkasten.run("echo", timeout=10_000)
    finally:
        subprocess.run = original  # type: ignore[assignment]
    assert aufgezeichnet["timeout"] <= werkstatt.MAX_TIMEOUT + 5


# ---------------------------------------------------------------------------
# Und wieder weg
# ---------------------------------------------------------------------------
def test_stopping_removes_container_and_volume(box: tuple[werkstatt.Sandbox, FakeRun]) -> None:
    """"Nach zwanzig Minuten sind alle Spuren weg" ist genau das hier."""
    sandkasten, fake = box
    sandkasten.ensure()
    name, volume = sandkasten.name, sandkasten._volume
    sandkasten.stop("Test")

    entfernt = fake.zeile("rm")
    assert name in entfernt and "--force" in entfernt and "--volumes" in entfernt
    aufgeraeumt = [zeile for zeile in fake.aufrufe if "volume" in zeile and "rm" in zeile]
    assert aufgeraeumt and volume in aufgeraeumt[0]
    assert not sandkasten.alive


def test_the_clock_starts_over_with_every_use(box: tuple[werkstatt.Sandbox, FakeRun]) -> None:
    """Zwanzig Minuten ab der letzten Nachricht, nicht ab dem Start."""
    sandkasten, _ = box
    sandkasten.ensure()
    erster = sandkasten._timer
    assert erster is not None
    sandkasten.touch()
    assert sandkasten._timer is not erster, "die Uhr wird neu gestellt"
    assert sandkasten._timer is not None
    sandkasten.stop("Test")
    assert sandkasten._timer is None, "ohne Werkstatt keine Uhr"


def test_a_failed_start_leaves_nothing_standing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Halbe Werkstatt ist schlimmer als keine."""
    fake = FakeRun({"run": (1, "", "kein Abbild")})
    monkeypatch.setattr(subprocess, "run", fake)
    sandkasten = werkstatt.Sandbox()
    sandkasten.runtime = werkstatt.Runtime("docker", "docker", "Docker")
    with pytest.raises(werkstatt.SandboxUnavailable):
        sandkasten.ensure()
    assert not sandkasten.alive
    assert any("rm" in zeile for zeile in fake.aufrufe), "der Rest wird weggeraeumt"


def test_forgotten_workshops_are_swept(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nach einem Absturz soll beim naechsten Start nichts liegen bleiben."""
    fake = FakeRun(
        {"ps": (0, "abc123\ndef456\n", ""), "ls": (0, "cortex-werkstatt-x\nandere\n", "")}
    )
    monkeypatch.setattr(subprocess, "run", fake)
    entfernt = werkstatt.sweep(werkstatt.Runtime("docker", "docker", "Docker"))
    assert entfernt == 2
    geloescht = [zeile for zeile in fake.aufrufe if "volume" in zeile and "rm" in zeile]
    assert any("cortex-werkstatt-x" in zeile for zeile in geloescht)
    assert not any("andere" in zeile for zeile in geloescht), "fremde Datentraeger bleiben"
