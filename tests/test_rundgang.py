"""Der Rundgang durch die Oberflaeche, als Teil der Pruefkette.

Die uebrigen Tests pruefen Bausteine und den HTML-Text. Hier laeuft die Seite
wirklich in einem Browser: jeder Knopf wird gedrueckt, jedes Fenster geoeffnet.
Das Skript liegt unter `tools/rundgang.py` und laesst sich auch von Hand
starten -- hier wird es nur als eigener Prozess aufgerufen, damit sein
gestellter Agent nicht in den Zustand der anderen Tests hineinregiert.

Fehlt Playwright oder der Browser, wird uebersprungen statt zu scheitern:
nicht jede Umgebung hat beides, und die uebrige Pruefkette bleibt aussagekraeftig.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKRIPT = REPO / "tools" / "rundgang.py"


def _browser_da() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return Path("/opt/pw-browsers/chromium").exists()


@pytest.mark.skipif(not _browser_da(), reason="Playwright oder Browser fehlen")
def test_the_walkthrough_finds_nothing_to_complain_about() -> None:
    umgebung = dict(os.environ)
    umgebung.pop("CORTEX_DATA_DIR", None)   # das Skript legt sich ein eigenes an
    fertig = subprocess.run(
        [sys.executable, str(SKRIPT)],
        capture_output=True,
        text=True,
        timeout=600,
        env=umgebung,
        cwd=str(REPO),
    )
    print(fertig.stdout[-4000:])
    assert fertig.returncode == 0, fertig.stdout[-4000:] + fertig.stderr[-2000:]
    assert "geprüft" in fertig.stdout
