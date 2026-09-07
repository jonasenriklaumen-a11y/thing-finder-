"""Tests fuer die Auftraege.

Geprueft wird das, was schiefgehen kann, ohne dass es jemand merkt: der
naechste Termin (eine Stunde daneben faellt niemandem auf, bis die Antwort
nachts kommt), das Nicht-Nachholen verpasster Laeufe, und dass ein Auftrag
sich seinen eigenen Agenten baut statt das laufende Gespraech anzufassen.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aquaticy import jobs as auftraege
from aquaticy.jobs import MAX_JOBS, Job, JobStore, Scheduler, next_time


def test_stuendlich_nimmt_die_naechste_volle_minute() -> None:
    jetzt = datetime(2026, 9, 6, 14, 30, 0).timestamp()
    naechster = datetime.fromtimestamp(next_time("hourly", 0, 15, 0, jetzt))
    assert (naechster.hour, naechster.minute) == (15, 15)


def test_stuendlich_bleibt_in_derselben_stunde_wenn_die_minute_noch_kommt() -> None:
    jetzt = datetime(2026, 9, 6, 14, 5, 0).timestamp()
    naechster = datetime.fromtimestamp(next_time("hourly", 0, 15, 0, jetzt))
    assert (naechster.hour, naechster.minute) == (14, 15)


def test_taeglich_springt_auf_morgen_wenn_die_zeit_vorbei_ist() -> None:
    jetzt = datetime(2026, 9, 6, 14, 0, 0).timestamp()
    naechster = datetime.fromtimestamp(next_time("daily", 8, 0, 0, jetzt))
    assert naechster == datetime(2026, 9, 7, 8, 0, 0)


def test_woechentlich_trifft_den_gewuenschten_tag() -> None:
    # 6.9.2026 ist ein Sonntag (weekday 6).
    jetzt = datetime(2026, 9, 6, 14, 0, 0).timestamp()
    naechster = datetime.fromtimestamp(next_time("weekly", 9, 30, 2, jetzt))
    assert naechster.weekday() == 2  # Mittwoch
    assert (naechster.hour, naechster.minute) == (9, 30)
    assert naechster > datetime(2026, 9, 6, 14, 0, 0)


def test_unbekannter_rhythmus_wird_taeglich() -> None:
    jetzt = datetime(2026, 9, 6, 14, 0, 0).timestamp()
    a = next_time("jaehrlich", 8, 0, 0, jetzt)
    b = next_time("daily", 8, 0, 0, jetzt)
    assert a == b


def test_anlegen_und_wieder_loeschen(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "j.db")
    job = store.add("Was gibt es Neues?", rhythm="daily", hour=7, minute=30)
    assert job.question == "Was gibt es Neues?"
    assert job.enabled is True
    assert job.next_run > time.time()
    assert [eintrag.id for eintrag in store.all_jobs()] == [job.id]
    assert store.delete(job.id) is True
    assert store.all_jobs() == []


def test_ohne_frage_kein_auftrag(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "j.db")
    with pytest.raises(ValueError):
        store.add("   ")


def test_die_liste_hat_eine_obergrenze(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "j.db")
    for nummer in range(MAX_JOBS):
        store.add(f"Frage {nummer}")
    with pytest.raises(ValueError, match="unübersichtlich"):
        store.add("einer zu viel")


def test_angehalten_heisst_nicht_faellig(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "j.db")
    job = store.add("Frage")
    # Faellig machen, indem der Termin in die Vergangenheit gelegt wird.
    with store._connect() as conn:
        conn.execute("UPDATE jobs SET next_run = 1 WHERE id = ?", (job.id,))
    assert len(store.due()) == 1
    store.set_enabled(job.id, False)
    assert store.due() == []


def test_verpasste_laeufe_werden_nicht_nachgeholt(tmp_path: Path) -> None:
    """Eine Woche Urlaub darf beim Einschalten keine sieben Recherchen ausloesen."""
    store = JobStore(tmp_path / "j.db")
    job = store.add("Frage", rhythm="daily", hour=8, minute=0)
    vor_einer_woche = time.time() - 7 * 86400
    with store._connect() as conn:
        conn.execute("UPDATE jobs SET next_run = ? WHERE id = ?", (vor_einer_woche, job.id))
    assert len(store.due()) == 1
    store.note_run(job.id, "fertig", "chat-1")
    frisch = store.get(job.id)
    assert frisch is not None
    assert frisch.next_run > time.time()
    assert frisch.next_run < time.time() + 86400 + 60
    assert store.due() == []
    assert frisch.last_state == "fertig"
    assert frisch.last_chat == "chat-1"


def test_ein_lauf_baut_seinen_eigenen_agenten(monkeypatch: pytest.MonkeyPatch) -> None:
    """Das laufende Gespraech des Nutzers bleibt unberuehrt."""
    gebaut: list[Any] = []
    geschlossen: list[bool] = []

    class FakeAgent:
        def __init__(self, settings: Any, cache: Any = None) -> None:
            gebaut.append(settings)
            self.session_id = "auftrag-1"

        def ask(self, frage: str, **kwargs: Any) -> Any:
            self.gefragt = frage
            self.wie = kwargs
            return type("R", (), {"answer": "Antwort"})()

        def close(self) -> None:
            geschlossen.append(True)

    monkeypatch.setattr("aquaticy.agent.Agent", FakeAgent)
    monkeypatch.setattr("aquaticy.cache.Cache", lambda *a, **k: None)

    job = Job(
        id=1, question="Was ist neu?", rhythm="daily", hour=8, minute=0, weekday=0,
        enabled=True, structured=True, created_at=0.0, next_run=0.0, last_run=0.0,
        last_state="", last_chat="",
    )
    settings = type("S", (), {"db_path": ":memory:", "cache_ttl_hours": 1})()
    zustand, chat = auftraege.run_job(job, settings)
    assert zustand == "fertig"
    assert chat == "auftrag-1"
    assert len(gebaut) == 1
    assert geschlossen == [True]


def test_ein_fehler_im_lauf_bleibt_im_lauf(monkeypatch: pytest.MonkeyPatch) -> None:
    class KaputterAgent:
        def __init__(self, *a: Any, **k: Any) -> None:
            self.session_id = "x"

        def ask(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("Modell weg")

        def close(self) -> None:
            pass

    monkeypatch.setattr("aquaticy.agent.Agent", KaputterAgent)
    monkeypatch.setattr("aquaticy.cache.Cache", lambda *a, **k: None)
    job = Job(
        id=1, question="Frage", rhythm="daily", hour=8, minute=0, weekday=0,
        enabled=True, structured=True, created_at=0.0, next_run=0.0, last_run=0.0,
        last_state="", last_chat="",
    )
    settings = type("S", (), {"db_path": ":memory:", "cache_ttl_hours": 1})()
    zustand, chat = auftraege.run_job(job, settings)
    assert zustand.startswith("Fehler: RuntimeError")
    assert chat == ""


def test_der_taktgeber_fuehrt_faellige_auftraege_aus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobStore(tmp_path / "j.db")
    job = store.add("Frage")
    with store._connect() as conn:
        conn.execute("UPDATE jobs SET next_run = 1 WHERE id = ?", (job.id,))

    gelaufen: list[str] = []

    def statt_dessen(auftrag: Job, settings: Any) -> tuple[str, str]:
        gelaufen.append(auftrag.question)
        return ("fertig", "c1")

    monkeypatch.setattr(auftraege, "run_job", statt_dessen)
    settings = type("S", (), {"db_path": tmp_path / "j.db", "cache_ttl_hours": 1})()
    takt = Scheduler(lambda: settings)
    assert takt.tick() == 1
    assert gelaufen == ["Frage"]
    # Danach ist derselbe Auftrag nicht mehr faellig.
    assert takt.tick() == 0


def test_naechster_termin_liegt_nie_in_der_vergangenheit() -> None:
    jetzt = datetime.now()
    for rhythmus in ("hourly", "daily", "weekly"):
        for stunde in (0, 8, 23):
            wann = datetime.fromtimestamp(next_time(rhythmus, stunde, 0, 3))
            assert wann > jetzt - timedelta(seconds=1)


def test_die_antwort_eines_auftrags_leuchtet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Auftrag stellt seine Frage von selbst -- oft nachts. Die Antwort
    soll auffallen, ohne dass jemand danach sucht."""
    gemerkt: list[tuple[str, str]] = []

    class FakeAgent:
        def __init__(self, settings: Any, cache: Any = None) -> None:
            self.session_id = "auftrag-7"

        def ask(self, frage: str, **kwargs: Any) -> Any:
            return type("R", (), {"answer": "Es gibt Neues."})()

        def close(self) -> None: ...

    class FakeCache:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def mark_unread(self, session_id: str, reason: str = "") -> None:
            gemerkt.append((session_id, reason))

    monkeypatch.setattr("aquaticy.agent.Agent", FakeAgent)
    monkeypatch.setattr("aquaticy.cache.Cache", FakeCache)

    job = Job(
        id=1, question="Was ist neu?", rhythm="daily", hour=8, minute=0, weekday=0,
        enabled=True, structured=True, created_at=0.0, next_run=0.0, last_run=0.0,
        last_state="", last_chat="",
    )
    settings = type("S", (), {"db_path": ":memory:", "cache_ttl_hours": 1})()
    zustand, _ = auftraege.run_job(job, settings)
    assert zustand == "fertig"
    assert gemerkt == [("auftrag-7", "auftrag")]


def test_ohne_antwort_leuchtet_nichts(monkeypatch: pytest.MonkeyPatch) -> None:
    gemerkt: list[str] = []

    class LeererAgent:
        def __init__(self, settings: Any, cache: Any = None) -> None:
            self.session_id = "auftrag-8"

        def ask(self, frage: str, **kwargs: Any) -> Any:
            return type("R", (), {"answer": "   "})()

        def close(self) -> None: ...

    class FakeCache:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def mark_unread(self, session_id: str, reason: str = "") -> None:
            gemerkt.append(session_id)

    monkeypatch.setattr("aquaticy.agent.Agent", LeererAgent)
    monkeypatch.setattr("aquaticy.cache.Cache", FakeCache)
    job = Job(
        id=1, question="Frage", rhythm="daily", hour=8, minute=0, weekday=0,
        enabled=True, structured=False, created_at=0.0, next_run=0.0, last_run=0.0,
        last_state="", last_chat="",
    )
    settings = type("S", (), {"db_path": ":memory:", "cache_ttl_hours": 1})()
    zustand, _ = auftraege.run_job(job, settings)
    assert zustand == "ohne Antwort"
    assert gemerkt == []
