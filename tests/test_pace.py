"""Takt halten: die Bremse, die aus 429-Wellen gleichmaessige Anfragen macht."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from cortex import pace


@pytest.fixture(autouse=True)
def _sauber(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Kein Taktgeber aus einem anderen Test, keine Umgebung von aussen."""
    monkeypatch.delenv("CORTEX_RPM", raising=False)
    monkeypatch.delenv("CORTEX_PARALLEL_CALLS", raising=False)
    pace.forget_gates()
    yield
    pace.forget_gates()


# ---------------------------------------------------------------------------
# Der Taktgeber selbst
# ---------------------------------------------------------------------------
def test_a_gate_without_limits_never_waits() -> None:
    gate = pace.Gate()
    start = time.monotonic()
    for _ in range(5):
        with gate.slot():
            pass
    assert time.monotonic() - start < 0.1
    assert gate.waited == 0.0


def test_calls_are_spread_over_the_minute() -> None:
    """Sechzig pro Minute heisst: eine Sekunde Abstand -- hier 600, also 0,1s."""
    gate = pace.Gate(rpm=600)
    assert gate.spacing == pytest.approx(0.1)
    start = time.monotonic()
    for _ in range(3):
        with gate.slot():
            pass
    # Der erste Aufruf geht sofort los, die beiden anderen warten je 0,1s.
    assert time.monotonic() - start >= 0.15


def test_only_so_many_calls_at_a_time() -> None:
    """Der zweite Aufruf kommt erst hinein, wenn der erste fertig ist."""
    gate = pace.Gate(parallel=1)
    drin = threading.Event()
    weiter = threading.Event()
    zweiter_drin = threading.Event()

    def erster() -> None:
        with gate.slot():
            drin.set()
            weiter.wait(2.0)

    def zweiter() -> None:
        with gate.slot():
            zweiter_drin.set()

    a = threading.Thread(target=erster)
    a.start()
    assert drin.wait(2.0)
    b = threading.Thread(target=zweiter)
    b.start()
    assert not zweiter_drin.wait(0.2)  # der Platz ist belegt
    weiter.set()
    assert zweiter_drin.wait(2.0)  # und jetzt frei
    a.join()
    b.join()


def test_the_wait_has_an_upper_bound() -> None:
    """Lieber ein 429, das der Wiederholungsversuch auffaengt, als Stillstand."""
    gate = pace.Gate(rpm=1)  # eine Minute Abstand
    assert gate.spacing == pytest.approx(60.0)
    assert pace.MAX_WAIT < 60.0


# ---------------------------------------------------------------------------
# Welche Grenzen fuer wen gelten
# ---------------------------------------------------------------------------
def test_the_known_providers_have_limits() -> None:
    assert pace.limits_for("nvidia_nim") == (40, 4)
    assert pace.limits_for("mistral") == (240, 8)


def test_local_and_unknown_providers_are_never_throttled() -> None:
    """Ollama laeuft auf dem eigenen Rechner, und Fremdes kennt Cortex nicht."""
    assert pace.limits_for("ollama_chat") == (0, 0)
    assert pace.limits_for("fremd") == (0, 0)
    assert pace.limits_for("") == (0, 0)
    assert pace.gate_for("ollama_chat/qwen2.5:7b") is pace.FREE
    assert pace.gate_for("fremd/modell") is pace.FREE
    assert pace.gate_for("") is pace.FREE


def test_bigger_contracts_can_raise_the_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_RPM", "600")
    monkeypatch.setenv("CORTEX_PARALLEL_CALLS", "32")
    assert pace.limits_for("nvidia_nim") == (600, 32)
    gate = pace.gate_for("nvidia_nim/meta/llama-3.3-70b-instruct")
    assert (gate.rpm, gate.parallel) == (600, 32)


def test_nonsense_in_the_environment_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_RPM", "viele")
    assert pace.limits_for("nvidia_nim") == (40, 4)


def test_every_call_to_a_provider_shares_one_gate() -> None:
    """Die Grenze gilt fuer den Schluessel, nicht fuer den Faden."""
    eins = pace.gate_for("nvidia_nim/meta/llama-3.3-70b-instruct")
    zwei = pace.gate_for("nvidia_nim/meta/llama-3.1-8b-instruct")
    assert eins is zwei
    assert eins is not pace.gate_for("mistral/mistral-small-latest")


def test_changed_limits_replace_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    alt = pace.gate_for("mistral/mistral-large-latest")
    monkeypatch.setenv("CORTEX_RPM", "600")
    neu = pace.gate_for("mistral/mistral-large-latest")
    assert neu is not alt
    assert neu.rpm == 600


def test_paced_is_the_short_form() -> None:
    with pace.paced("nvidia_nim/meta/llama-3.3-70b-instruct"):
        pass
    assert pace.gate_for("nvidia_nim/meta/llama-3.3-70b-instruct").rpm == 40
