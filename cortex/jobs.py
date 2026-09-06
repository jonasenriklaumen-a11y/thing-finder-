"""Auftraege: Fragen, die Cortex von selbst stellt.

Manche Fragen stellt man nicht einmal, sondern immer wieder -- "was ist in
meiner Branche passiert?", "gibt es Neues zu diesem Gesetz?". Ein Auftrag ist
genau das: eine gespeicherte Frage mit einem Rhythmus. Cortex stellt sie sich
selbst, recherchiert und legt die Antwort als Chat ab. Beim naechsten Oeffnen
steht sie in der Seitenleiste, als haette man sie selbst gestellt.

Zwei Entscheidungen, die den Rest erklaeren:

* **Ein eigener Agent je Lauf.** Ein Auftrag darf das laufende Gespraech nicht
  anfassen -- weder seinen Verlauf noch seine Einstellungen. Er baut sich also
  seinen eigenen Agenten, laesst ihn arbeiten und wirft ihn weg.
* **Verpasste Laeufe werden nicht nachgeholt.** Wer den Rechner eine Woche aus
  hat, will beim Einschalten nicht sieben Recherchen auf einmal. Der naechste
  Termin wird von jetzt an neu berechnet.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

#: Wie oft ein Auftrag laufen kann. Feiner waere Spielerei: wer eine Frage
#: alle fuenf Minuten stellt, will keinen Auftrag, sondern eine Anzeige.
RHYTHMS = ("hourly", "daily", "weekly")

#: Deutsche Namen fuer die Anzeige -- und fuer den Chat-Titel.
RHYTHM_NAMES = {
    "hourly": "stündlich",
    "daily": "täglich",
    "weekly": "wöchentlich",
}

#: Wie oft nachgesehen wird, ob etwas ansteht. Eine Minute ist genau genug
#: fuer einen Rhythmus, der in Stunden rechnet.
TICK_SECONDS = 60

#: Mehr Auftraege waeren keine Erleichterung mehr, sondern eine zweite
#: To-do-Liste, die man auch noch pflegen muss.
MAX_JOBS = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    question   TEXT    NOT NULL,
    rhythm     TEXT    NOT NULL DEFAULT 'daily',
    hour       INTEGER NOT NULL DEFAULT 8,
    minute     INTEGER NOT NULL DEFAULT 0,
    weekday    INTEGER NOT NULL DEFAULT 0,
    enabled    INTEGER NOT NULL DEFAULT 1,
    structured INTEGER NOT NULL DEFAULT 1,
    created_at REAL    NOT NULL DEFAULT 0,
    next_run   REAL    NOT NULL DEFAULT 0,
    last_run   REAL    NOT NULL DEFAULT 0,
    last_state TEXT    NOT NULL DEFAULT '',
    last_chat  TEXT    NOT NULL DEFAULT ''
);
"""


@dataclass(slots=True)
class Job:
    """Ein Auftrag, so wie er in der Datenbank steht."""

    id: int
    question: str
    rhythm: str
    hour: int
    minute: int
    weekday: int
    enabled: bool
    structured: bool
    created_at: float
    next_run: float
    last_run: float
    last_state: str
    last_chat: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "rhythm": self.rhythm,
            "rhythm_name": RHYTHM_NAMES.get(self.rhythm, self.rhythm),
            "hour": self.hour,
            "minute": self.minute,
            "weekday": self.weekday,
            "enabled": self.enabled,
            "structured": self.structured,
            "created_at": self.created_at,
            "next_run": self.next_run,
            "last_run": self.last_run,
            "last_state": self.last_state,
            "last_chat": self.last_chat,
        }


def clean_rhythm(value: str) -> str:
    wanted = (value or "").strip().lower()
    return wanted if wanted in RHYTHMS else "daily"


def next_time(rhythm: str, hour: int, minute: int, weekday: int, now: float = 0.0) -> float:
    """Wann der Auftrag das naechste Mal faellig ist.

    Gerechnet wird in Ortszeit: wer "jeden Morgen um acht" sagt, meint acht
    Uhr bei sich, nicht in UTC.
    """
    jetzt = datetime.fromtimestamp(now or time.time())
    rhythm = clean_rhythm(rhythm)
    hour = max(0, min(23, int(hour)))
    minute = max(0, min(59, int(minute)))

    if rhythm == "hourly":
        ziel = jetzt.replace(minute=minute, second=0, microsecond=0)
        if ziel <= jetzt:
            ziel += timedelta(hours=1)
        return ziel.timestamp()

    ziel = jetzt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if rhythm == "daily":
        if ziel <= jetzt:
            ziel += timedelta(days=1)
        return ziel.timestamp()

    # wöchentlich: 0 = Montag, wie datetime.weekday()
    wunsch = max(0, min(6, int(weekday)))
    vorlauf = (wunsch - ziel.weekday()) % 7
    ziel += timedelta(days=vorlauf)
    if ziel <= jetzt:
        ziel += timedelta(days=7)
    return ziel.timestamp()


class JobStore:
    """Die Auftraege auf der Platte -- in derselben Datenbank wie alles andere."""

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

    @staticmethod
    def _row(row: sqlite3.Row) -> Job:
        return Job(
            id=int(row["id"]),
            question=str(row["question"]),
            rhythm=str(row["rhythm"]),
            hour=int(row["hour"]),
            minute=int(row["minute"]),
            weekday=int(row["weekday"]),
            enabled=bool(row["enabled"]),
            structured=bool(row["structured"]),
            created_at=float(row["created_at"] or 0.0),
            next_run=float(row["next_run"] or 0.0),
            last_run=float(row["last_run"] or 0.0),
            last_state=str(row["last_state"] or ""),
            last_chat=str(row["last_chat"] or ""),
        )

    def all_jobs(self) -> list[Job]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY next_run").fetchall()
        return [self._row(row) for row in rows]

    def get(self, job_id: int) -> Job | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
        return self._row(row) if row else None

    def add(
        self,
        question: str,
        *,
        rhythm: str = "daily",
        hour: int = 8,
        minute: int = 0,
        weekday: int = 0,
        structured: bool = True,
    ) -> Job:
        """Legt einen Auftrag an.

        Raises:
            ValueError: Ohne Frage, oder wenn die Liste voll ist.
        """
        frage = " ".join(str(question).split())[:500]
        if not frage:
            raise ValueError("Ein Auftrag braucht eine Frage.")
        rhythm = clean_rhythm(rhythm)
        hour = max(0, min(23, int(hour)))
        minute = max(0, min(59, int(minute)))
        weekday = max(0, min(6, int(weekday)))
        with self._lock, self._connect() as conn:
            (anzahl,) = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
            if int(anzahl) >= MAX_JOBS:
                raise ValueError(
                    f"Mehr als {MAX_JOBS} Aufträge werden unübersichtlich — "
                    "lösch erst einen."
                )
            jetzt = time.time()
            cur = conn.execute(
                "INSERT INTO jobs (question, rhythm, hour, minute, weekday, enabled,"
                " structured, created_at, next_run) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    frage,
                    rhythm,
                    hour,
                    minute,
                    weekday,
                    1 if structured else 0,
                    jetzt,
                    next_time(rhythm, hour, minute, weekday, jetzt),
                ),
            )
            neu = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (int(cur.lastrowid or 0),)
            ).fetchone()
        return self._row(neu)

    def set_enabled(self, job_id: int, enabled: bool) -> bool:
        """Haelt einen Auftrag an oder laesst ihn weiterlaufen."""
        with self._lock, self._connect() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
            if job is None:
                return False
            weiter = next_time(
                str(job["rhythm"]), int(job["hour"]), int(job["minute"]), int(job["weekday"])
            )
            conn.execute(
                "UPDATE jobs SET enabled = ?, next_run = ? WHERE id = ?",
                (1 if enabled else 0, weiter, int(job_id)),
            )
        return True

    def delete(self, job_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM jobs WHERE id = ?", (int(job_id),))
            return bool(cur.rowcount)

    def due(self, now: float = 0.0) -> list[Job]:
        """Was jetzt faellig ist."""
        jetzt = now or time.time()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE enabled = 1 AND next_run <= ? ORDER BY next_run",
                (jetzt,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def note_run(self, job_id: int, state: str, chat: str = "") -> None:
        """Traegt ein, was aus einem Lauf geworden ist, und setzt den naechsten an.

        Verpasste Termine werden dabei nicht nachgeholt: der naechste wird von
        jetzt an gerechnet.
        """
        with self._lock, self._connect() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
            if job is None:
                return
            conn.execute(
                "UPDATE jobs SET last_run = ?, last_state = ?, last_chat = ?, next_run = ?"
                " WHERE id = ?",
                (
                    time.time(),
                    str(state)[:300],
                    str(chat)[:80],
                    next_time(
                        str(job["rhythm"]),
                        int(job["hour"]),
                        int(job["minute"]),
                        int(job["weekday"]),
                    ),
                    int(job_id),
                ),
            )


def run_job(job: Job, settings: Any) -> tuple[str, str]:
    """Fuehrt einen Auftrag aus. Returns: (Zustand, Chat-Kennung).

    Der Agent ist ein eigener: das laufende Gespraech des Nutzers bleibt
    unberuehrt, und die Antwort landet als eigener Chat im Verlauf.

    Rueckfragen kann ein Auftrag nicht stellen -- es sitzt ja niemand davor.
    Der Agent bekommt deshalb keinen Rueckfrage-Empfaenger und muss mit dem
    auskommen, was in der Frage steht.
    """
    from cortex.agent import Agent
    from cortex.cache import Cache

    cache = Cache(settings.db_path, settings.cache_ttl_hours)
    agent = Agent(settings, cache=cache)
    try:
        result = agent.ask(
            job.question,
            stream=False,
            mode="normal",
            structured=job.structured,
            recheck=False,
        )
        antwort = str(getattr(result, "answer", "") or "").strip()
        chat = str(getattr(agent, "session_id", ""))
        if not antwort:
            return ("ohne Antwort", chat)
        return ("fertig", chat)
    except Exception as exc:  # pragma: no cover - haengt am Modell
        return (f"Fehler: {type(exc).__name__}: {exc}"[:300], "")
    finally:
        with contextlib.suppress(Exception):
            agent.close()


class Scheduler:
    """Sieht im Takt nach, ob etwas ansteht -- und fuehrt es aus.

    Ein einzelner Thread, kein Prozess: ein Auftrag laeuft nach dem anderen.
    Zwei gleichzeitige Recherchen wuerden sich auf einem kleinen Rechner
    ohnehin gegenseitig ausbremsen.
    """

    def __init__(self, settings_getter: Any, on_run: Any = None) -> None:
        self._settings_getter = settings_getter
        self._on_run = on_run
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="cortex-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            with contextlib.suppress(Exception):
                self.tick()

    def tick(self) -> int:
        """Ein Durchgang. Returns: wie viele Auftraege gelaufen sind."""
        settings = self._settings_getter()
        store = JobStore(settings.db_path)
        gelaufen = 0
        for job in store.due():
            if self._stop.is_set():
                break
            state, chat = run_job(job, settings)
            store.note_run(job.id, state, chat)
            gelaufen += 1
            if self._on_run:
                with contextlib.suppress(Exception):
                    self._on_run(job, state, chat)
        return gelaufen
