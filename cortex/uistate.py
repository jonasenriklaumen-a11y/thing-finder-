"""Der Zustand der Oberflaeche -- auf dem Server, nicht im Browser.

Bis hierher lagen Erscheinungsbild, Arbeitsweise und die Schalter der
Modellauswahl im ``localStorage``. Das hatte drei Nachteile, und alle drei
faellt man erst spaeter auf die Fuesse:

* **Jedes Geraet wusste etwas anderes.** Am Rechner dunkel, am Handy hell;
  am Rechner Code-Modus, am Handy nicht. Es ist dieselbe Person und dasselbe
  Cortex -- es sollte auch derselbe Zustand sein.
* **Der Browser entschied.** Was er schickte, wurde geglaubt. Ein Tippfehler
  im Skript, ein alter Tab, ein zurechtgebogener Aufruf: alles kam bis zum
  Modell durch. Der Server hatte keine eigene Wahrheit, gegen die er haette
  pruefen koennen.
* **Es blinkte.** Die Seite kam hell an und wurde erst dunkel, nachdem das
  Skript gelaufen war.

Hier liegt jetzt die Wahrheit. Der Browser darf sie **vorschlagen**; was
gilt, entscheidet diese Datei -- jeder Wert gegen eine Liste erlaubter
Werte, alles andere faellt auf den Standard zurueck. Beim Ausliefern der
Seite wird der Zustand gleich mitgegeben, deshalb blinkt auch nichts mehr.

Abgelegt wird als eine einzige Zeile in derselben Datenbank wie der Rest.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: Die Farbschemata. Muessen mit denen in `webui.html` uebereinstimmen --
#: ein Test haelt beide Listen zusammen. Der leere Name ist das Standard-
#: schema; es setzt kein Attribut und braucht deshalb keinen Namen.
PALETTES = (
    "",
    "nord",
    "catppuccin",
    "gruvbox",
    "tokyonight",
    "solarized",
    "dracula",
    "rosepine",
)

#: Hell, dunkel oder das, was das Betriebssystem sagt.
THEMES = ("light", "dark", "system")

#: Die Arbeitsweise. Muss mit `cortex.agent.MODES` uebereinstimmen -- ein
#: Test haelt beide Listen zusammen.
MODES = ("normal", "code", "pro")

#: Wie lange das Modell ueberlegen darf.
EFFORTS = ("low", "medium", "high")

#: Jedes Feld mit seinem Standard und dem, was erlaubt ist. `bool` heisst:
#: ein Wahrheitswert, alles andere eine Liste zulaessiger Zeichenketten.
FIELDS: dict[str, tuple[Any, Any]] = {
    "theme": ("system", THEMES),
    "palette": ("", PALETTES),
    "mode": ("normal", MODES),
    "effort": ("medium", EFFORTS),
    "structured": (False, bool),
    "recheck": (False, bool),
    "online": (True, bool),
    "sandbox": (False, bool),
    "denken": (False, bool),
    "tracing": (False, bool),
    "load": (False, bool),
}

#: Was aus dem Browser als "ja" durchgeht. Alles andere ist nein -- und
#: zwar ausdruecklich auch die Zeichenkette "false", ueber die ein blosses
#: `bool(...)` stolpern wuerde: nicht leer, also wahr, also an. Genau so
#: schaltet sich ein Schalter von selbst ein, den niemand angefasst hat.
_YES = frozenset({True, "1", "true", "True", "on", "ja", "yes"})
_NO = frozenset({False, "0", "false", "False", "off", "nein", "no", "", None})

SCHEMA = """
CREATE TABLE IF NOT EXISTS uistate (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL DEFAULT '{}'
);
"""


def defaults() -> dict[str, Any]:
    """Der Zustand, mit dem eine frische Oberflaeche startet."""
    return {name: standard for name, (standard, _) in FIELDS.items()}


def clean_flag(value: Any, standard: bool) -> bool:
    """Macht aus dem, was der Browser schickt, einen Wahrheitswert.

    Was weder eindeutig ja noch eindeutig nein ist, bleibt beim Standard.
    Raten waere hier die schlechtere Wahl: ein falsch verstandenes "aus"
    schaltet eine Recherche an, die Minuten dauert.
    """
    if isinstance(value, str):
        value = value.strip()
    if value in _YES:
        return True
    if value in _NO:
        return False
    return standard


def clean(raw: Any, *, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Prueft einen Vorschlag aus dem Browser gegen die erlaubten Werte.

    Unbekannte Felder fallen weg, unerlaubte Werte fallen auf den bisherigen
    Stand zurueck. Es kommt immer ein vollstaendiger, gueltiger Zustand
    heraus -- nie ein halber.
    """
    stand = dict(defaults())
    if base:
        for name, wert in base.items():
            if name in FIELDS:
                stand[name] = wert
    if not isinstance(raw, dict):
        return stand
    for name, (standard, erlaubt) in FIELDS.items():
        if name not in raw:
            continue
        wert = raw[name]
        if erlaubt is bool:
            stand[name] = clean_flag(wert, bool(stand.get(name, standard)))
        else:
            text = str(wert or "").strip()
            stand[name] = text if text in erlaubt else stand.get(name, standard)
    return stand


class UIState:
    """Liest und schreibt den Zustand der Oberflaeche."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def read(self) -> dict[str, Any]:
        """Der gueltige Zustand. Faellt immer auf etwas Brauchbares zurueck."""
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT state FROM uistate WHERE id = 1").fetchone()
        except sqlite3.Error:
            return defaults()
        if not row:
            return defaults()
        try:
            gespeichert = json.loads(row["state"])
        except (TypeError, ValueError):
            return defaults()
        # Auch das Gespeicherte wird geprueft: eine von Hand veraenderte
        # Datenbank ist kein Grund, eine kaputte Oberflaeche auszuliefern.
        return clean(gespeichert)

    def write(self, raw: Any) -> dict[str, Any]:
        """Uebernimmt einen Vorschlag und gibt zurueck, was wirklich gilt."""
        with self._lock:
            neu = clean(raw, base=self.read())
            try:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO uistate (id, state) VALUES (1, ?) "
                        "ON CONFLICT(id) DO UPDATE SET state = excluded.state",
                        (json.dumps(neu, ensure_ascii=False),),
                    )
            except sqlite3.Error:
                # Der Zustand ist Bequemlichkeit, keine Bedingung. Laesst er
                # sich nicht ablegen, gilt er trotzdem fuer diesen Aufruf.
                pass
            return neu

    def reset(self) -> dict[str, Any]:
        """Zurueck auf Anfang."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM uistate")
        return defaults()
