"""Tests fuer die Auftraege.

Geprueft wird das, was schiefgehen kann, ohne dass es jemand merkt: der
naechste Termin (eine Stunde daneben faellt niemandem auf, bis die Antwort
nachts kommt), das Nicht-Nachholen verpasster Laeufe, und dass ein Auftrag
sich seinen eigenen Agenten baut statt das laufende Gespraech anzufassen.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aquaticy import jobs as auftraege
from aquaticy.jobs import (
    MAX_JOBS,
    SCHEMA,
    Job,
    JobStore,
    Scheduler,
    next_time,
    price_condition_met,
)
from aquaticy.models import Product


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


def test_beobachtung_kann_alle_fuenf_minuten_laufen() -> None:
    jetzt = datetime(2026, 9, 6, 14, 3, 41).timestamp()
    naechster = datetime.fromtimestamp(next_time("minutes5", 0, 0, 0, jetzt))
    assert naechster == datetime(2026, 9, 6, 14, 8, 0)


def test_preisbedingung_nutzt_strukturierte_produktdaten() -> None:
    product = Product(name="Teil", url="https://shop.example/p", price="49,99")
    assert price_condition_met("unter 50 €", [product]) is True
    assert price_condition_met("unter 40 €", [product]) is False
    assert price_condition_met("wenn es günstig ist", [product]) is None


def test_anlegen_und_wieder_loeschen(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "j.db")
    job = store.add("Was gibt es Neues?", rhythm="daily", hour=7, minute=30)
    assert job.question == "Was gibt es Neues?"
    assert job.enabled is True
    assert job.next_run > time.time()
    assert [eintrag.id for eintrag in store.all_jobs()] == [job.id]
    assert store.delete(job.id) is True
    assert store.all_jobs() == []


def test_alte_auftragsdatenbank_wird_fuer_beobachtungen_erweitert(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
    store = JobStore(path)
    job = store.add(
        "Ein oranges Flugzeug ist sichtbar",
        kind="visual",
        source_url="https://camera.example/live",
        rhythm="minutes5",
    )
    assert job.kind == "visual"
    assert job.source_url == "https://camera.example/live"


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


def test_erfuellte_bildbeobachtung_speichert_genau_einen_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[tuple[str, str, str, dict[str, Any]]] = []
    unread: list[tuple[str, str]] = []

    class Result:
        def __init__(self) -> None:
            self.answer = "BEDINGUNG ERFÜLLT\nDas orange Flugzeug ist sichtbar."
            self.visuals = [{"media_id": "1234567890123-0123456789abcdef.jpg"}]

        def meta(self) -> dict[str, Any]:
            return {"visuals": self.visuals}

    class FakeAgent:
        def __init__(self, settings: Any, cache: Any = None) -> None:
            assert cache is None
            self.session_id = "monitor-1"

        def ask(self, frage: str, **kwargs: Any) -> Any:
            assert "inspect_public_visual" in frage
            assert kwargs["visual_sources"] is True
            assert kwargs["structured"] is False
            return Result()

        def close(self) -> None: ...

    class FakeCache:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def add_history(
            self, session: str, question: str, answer: str, meta: dict[str, Any]
        ) -> None:
            saved.append((session, question, answer, meta))

        def mark_unread(self, session: str, reason: str = "") -> None:
            unread.append((session, reason))

    monkeypatch.setattr("aquaticy.agent.Agent", FakeAgent)
    monkeypatch.setattr("aquaticy.cache.Cache", FakeCache)
    job = Job(
        id=1, question="Ein oranges Flugzeug ist sichtbar", rhythm="minutes5",
        hour=0, minute=0, weekday=0, enabled=True, structured=True, created_at=0,
        next_run=0, last_run=0, last_state="", last_chat="", kind="visual",
        source_url="https://camera.example/live",
    )
    settings = type("S", (), {"db_path": ":memory:", "cache_ttl_hours": 1})()
    state, chat = auftraege.run_job(job, settings)
    assert (state, chat) == ("erfüllt", "monitor-1")
    assert saved[0][3]["visuals"][0]["media_id"].endswith(".jpg")
    assert "monitor_checked_at" in saved[0][3]
    assert unread == [("monitor-1", "beobachtung")]


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


# ---------------------------------------------------------------------------
# Sehr kurze Takte
# ---------------------------------------------------------------------------
def test_every_minute_lands_on_the_next_minute() -> None:
    jetzt = time.time()
    wann = next_time("minutes1", 8, 0, 0, jetzt)
    assert 0 < wann - jetzt <= 60


def test_continuously_is_due_again_right_away() -> None:
    """„Die ganze Zeit" heisst: beim naechsten Takt wieder -- nicht irgendwann."""
    jetzt = time.time()
    assert next_time("always", 8, 0, 0, jetzt) <= jetzt


def test_the_tick_is_short_enough_for_the_shortest_rhythm() -> None:
    """Ein Takt von einer Minute koennte „jede Minute" um bis zu 59 s verfehlen."""
    assert auftraege.TICK_SECONDS <= 30


# ---------------------------------------------------------------------------
# Bildauftrag: nach einem fotografierten Gegenstand suchen
# ---------------------------------------------------------------------------
def _bild_settings(tmp_path: Path) -> Any:
    return type(
        "S",
        (),
        {"db_path": tmp_path / "a.sqlite3", "cache_ttl_hours": 1, "data_dir": tmp_path},
    )()


def test_an_image_job_needs_its_image(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    with pytest.raises(ValueError, match="fehlt das hochgeladene Bild"):
        store.add("Handy gesucht", kind="image")


def test_an_image_job_may_search_the_whole_web(tmp_path: Path) -> None:
    """Anders als eine Kamera braucht die Bildsuche keine feste Adresse."""
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.add("Handy gesucht", kind="image", image_id="abc.jpg")
    assert job.kind == "image" and job.source_url == "" and job.image_id == "abc.jpg"


def test_a_camera_job_still_needs_its_page(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    with pytest.raises(ValueError, match="öffentliche Quelladresse"):
        store.add("Ist es hell?", kind="visual")


def test_the_image_description_is_kept_after_the_first_look(tmp_path: Path) -> None:
    """Das Foto aendert sich nicht -- es jede Minute neu anzusehen waere teuer."""
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.add("Handy gesucht", kind="image", image_id="abc.jpg")
    store.set_image_note(job.id, "Ein schwarzes Smartphone mit drei Kameras")
    wieder = store.get(job.id)
    assert wieder is not None
    assert wieder.image_note.startswith("Ein schwarzes Smartphone")


def test_a_found_offer_needs_a_read_page_and_an_address(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """„Ja, gibt es" ist kein Fund. Der Nutzer will den Laden, nicht die Zuversicht."""
    class Agent:
        def __init__(self, settings: Any, cache: Any = None) -> None:
            self.session_id = "bild-1"

        def ask(self, frage: str, **kwargs: Any) -> Any:
            return type("R", (), {
                "answer": "BEDINGUNG ERFÜLLT\nGibt es sicher irgendwo zu kaufen.",
                "sources": [], "visuals": [], "products": [],
            })()

        def close(self) -> None: ...

    monkeypatch.setattr("aquaticy.agent.Agent", Agent)
    job = Job(
        id=1, question="Handy gesucht", rhythm="hourly", hour=8, minute=0, weekday=0,
        enabled=True, structured=False, created_at=0.0, next_run=0.0, last_run=0.0,
        last_state="", last_chat="", kind="image", image_id="abc.jpg",
        image_note="Ein schwarzes Smartphone",
    )
    zustand, chat = auftraege.run_job(job, _bild_settings(tmp_path))
    assert zustand == "kein Angebot gefunden" and chat == ""


def test_a_real_offer_creates_the_chat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gemerkt: list[tuple[str, str]] = []

    class Agent:
        def __init__(self, settings: Any, cache: Any = None) -> None:
            self.session_id = "bild-2"

        def ask(self, frage: str, **kwargs: Any) -> Any:
            assert "Ein schwarzes Smartphone" in frage, "das Gesehene gehört in die Frage"
            return type("R", (), {
                "answer": (
                    "BEDINGUNG ERFÜLLT\nPixel 9 bei laden.example für 599 €: "
                    "https://laden.example/pixel-9"
                ),
                "sources": [{"url": "https://laden.example/pixel-9"}],
                "visuals": [], "products": [],
            })()

        def close(self) -> None: ...

    class FakeCache:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def add_history(self, *args: Any, **kwargs: Any) -> None: ...

        def mark_unread(self, session_id: str, reason: str = "") -> None:
            gemerkt.append((session_id, reason))

    monkeypatch.setattr("aquaticy.agent.Agent", Agent)
    monkeypatch.setattr("aquaticy.cache.Cache", FakeCache)
    job = Job(
        id=1, question="Handy gesucht", rhythm="hourly", hour=8, minute=0, weekday=0,
        enabled=True, structured=False, created_at=0.0, next_run=0.0, last_run=0.0,
        last_state="", last_chat="", kind="image", image_id="abc.jpg",
        image_note="Ein schwarzes Smartphone",
    )
    zustand, chat = auftraege.run_job(job, _bild_settings(tmp_path))
    assert zustand == "erfüllt" and chat == "bild-2"
    assert gemerkt == [("bild-2", "beobachtung")]


def test_the_offer_address_is_read_from_the_answer() -> None:
    from aquaticy.jobs import offer_url

    assert offer_url("Bei https://laden.example/x für 9 €.") == "https://laden.example/x"
    assert offer_url("Kein Angebot gefunden.") == ""
