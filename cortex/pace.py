"""Takt halten, statt gegen die Wand zu laufen.

NVIDIA NIM erlaubt im Freikontingent **vierzig Anfragen pro Minute**. Cortex
schickt im Pro-Modus vierundvierzig Agenten los, jeder mit mehreren Aufrufen.
Ohne Bremse passiert dann Folgendes: die ersten vierzig kommen durch, alles
Weitere bekommt ein 429 zurueck, jede abgelehnte Anfrage wird wiederholt, die
Wiederholungen laufen wieder in dieselbe Grenze -- und aus einer Recherche
werden Minuten, in denen sichtbar nichts passiert. Genau das war gemeint mit
"NVIDIA braucht sehr sehr lange".

Die Loesung ist nicht, weniger zu fragen, sondern **gleichmaessig** zu fragen.
Diese Datei haelt zwei Masse ein, je Anbieter:

* **Anfragen pro Minute.** Zwischen zwei Aufrufen liegt mindestens
  ``60 / rpm`` Sekunden. Vierzig pro Minute heisst: alle anderthalb Sekunden
  einer, dauerhaft -- statt vierzig in der ersten Sekunde und dann eine
  Minute Fehler.
* **Gleichzeitige Anfragen.** Mehr als eine Handvoll offener Verbindungen
  bringt nichts, wenn der Anbieter ohnehin der Reihe nach antwortet; sie
  machen nur jede einzelne Antwort langsamer.

Gezaehlt wird der Beginn einer Anfrage, nicht ihr Ende: bei einer
gestreamten Antwort ist der Platz wieder frei, sobald der Strom laeuft --
gebremst gehoert das Losschicken, nicht das Zuhoeren.

Wer hier nicht steht, wird nicht gebremst: lokale Modelle ueber Ollama laufen
auf dem eigenen Rechner, und ein von Hand eingetragener Anbieter hat Grenzen,
die Cortex nicht kennt -- da waere jede Annahme falsch.

Die Grenzen selbst stehen in `cortex/system.py` bei den Anbietern; hier steht
nur, wie sie eingehalten werden.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

#: Wie lange ein Aufruf hoechstens auf seinen Platz wartet. Danach geht er
#: trotzdem los: lieber ein 429, das der Wiederholungsversuch auffaengt, als
#: eine Oberflaeche, die minutenlang steht.
MAX_WAIT = 30.0


class Gate:
    """Ein Taktgeber je Anbieter: so viele pro Minute, so viele gleichzeitig."""

    def __init__(self, rpm: int = 0, parallel: int = 0) -> None:
        #: 0 heisst: unbegrenzt.
        self.rpm = max(0, int(rpm))
        self.parallel = max(0, int(parallel))
        self.spacing = 60.0 / self.rpm if self.rpm else 0.0
        self._slots = threading.Semaphore(self.parallel) if self.parallel else None
        self._takt = threading.Lock()
        self._last = 0.0
        #: Nur zum Nachsehen in Tests und in der Anzeige.
        self.waited = 0.0

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Haelt einen Platz frei, solange der Aufruf laeuft."""
        if self._slots is not None:
            self._slots.acquire()
        try:
            if self.spacing:
                with self._takt:
                    rest = self.spacing - (time.monotonic() - self._last)
                    if rest > 0:
                        time.sleep(min(rest, MAX_WAIT))
                        self.waited += min(rest, MAX_WAIT)
                    self._last = time.monotonic()
            yield
        finally:
            if self._slots is not None:
                self._slots.release()


#: Ein Taktgeber je Anbieter, ueber den ganzen Prozess. Es hilft nichts, wenn
#: jeder Agent fuer sich das Mass haelt -- die Grenze gilt fuer den Schluessel,
#: nicht fuer den Faden.
_gates: dict[str, Gate] = {}
_lock = threading.Lock()

#: Der Taktgeber fuer alles Ungebremste. Einer fuer alle, weil er nichts tut.
FREE = Gate()


def _env_int(name: str) -> int:
    """Eine Zahl aus der Umgebung -- Unsinn zaehlt als "nicht gesetzt"."""
    try:
        return max(0, int(str(os.environ.get(name, "")).strip() or 0))
    except (TypeError, ValueError):
        return 0


def limits_for(provider: str) -> tuple[int, int]:
    """(Anfragen je Minute, gleichzeitige Anfragen) fuer *provider*.

    Wer einen groesseren Vertrag hat, hebt die Grenzen mit `CORTEX_RPM` und
    `CORTEX_PARALLEL_CALLS` an; beide gelten fuer alle Anbieter.
    """
    from cortex.system import provider_limits

    rpm, parallel = provider_limits(provider)
    return (_env_int("CORTEX_RPM") or rpm, _env_int("CORTEX_PARALLEL_CALLS") or parallel)


def gate_for(model: str) -> Gate:
    """Der Taktgeber fuer das Modell -- oder einer, der nichts tut."""
    from cortex.config import provider_of

    provider = provider_of(model or "")
    rpm, parallel = limits_for(provider)
    if not rpm and not parallel:
        return FREE
    with _lock:
        gate = _gates.get(provider)
        if gate is None or (gate.rpm, gate.parallel) != (rpm, parallel):
            gate = Gate(rpm, parallel)
            _gates[provider] = gate
        return gate


def forget_gates() -> None:
    """Alles vergessen -- fuer Tests und nach einer Aenderung der Grenzen."""
    with _lock:
        _gates.clear()


@contextmanager
def paced(model: str) -> Iterator[None]:
    """Kurzform: ``with paced(model): litellm.completion(...)``."""
    with gate_for(model).slot():
        yield
