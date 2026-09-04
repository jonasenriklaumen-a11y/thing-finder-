"""Tests fuer den Langzeitspeicher -- Verschluesselung, Grenze, Suche."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cortex import memory as mem
from cortex.memory import Memory, MemoryFull, human_size


@pytest.fixture
def store(tmp_path: Path) -> Memory:
    return Memory(tmp_path / "cortex.sqlite3", tmp_path)


# -- Ablegen und Wiederfinden ---------------------------------------------
def test_a_note_comes_back(store: Memory) -> None:
    store.remember("Der Nutzer wohnt in Bremen.", topic="Wohnort")
    found = store.recall("Bremen")
    assert len(found) == 1
    assert found[0].text == "Der Nutzer wohnt in Bremen."
    assert found[0].topic == "Wohnort"


def test_searching_falls_back_to_single_words(store: Memory) -> None:
    """"welches Cafe kennst du" soll die Notiz zu "Cafe" finden."""
    store.remember("Lieblingscafe ist das Nordwand.", topic="Orte")
    assert store.recall("welches Cafe kennst du denn")


def test_without_a_query_the_newest_come_first(store: Memory) -> None:
    for index in range(5):
        store.remember(f"Notiz {index}")
    texts = [entry.text for entry in store.recall()]
    assert texts[0] == "Notiz 4"


def test_an_empty_note_is_refused(store: Memory) -> None:
    with pytest.raises(ValueError):
        store.remember("   ")


def test_very_long_notes_are_cut(store: Memory) -> None:
    """Ein Modell, das Romane ablegt, darf den Speicher nicht fuellen."""
    entry = store.remember("x" * 50_000)
    assert len(entry.text) == mem.MAX_ENTRY_CHARS


def test_deleting_one_and_all(store: Memory) -> None:
    first = store.remember("eins")
    store.remember("zwei")
    assert store.forget(first.id) is True
    assert store.forget(first.id) is False
    assert store.count() == 1
    assert store.clear() == 1
    assert store.count() == 0


# -- Verschluesselung -----------------------------------------------------
def test_the_database_holds_no_plain_text(store: Memory, tmp_path: Path) -> None:
    """Wer die Datei kopiert, soll nichts lesen koennen."""
    secret = "Der Nutzer wohnt in der Musterstrasse 5."
    store.remember(secret, topic="Wohnort")
    raw = (tmp_path / "cortex.sqlite3").read_bytes()
    assert secret.encode() not in raw
    assert b"Musterstrasse" not in raw
    assert b"Wohnort" not in raw


def test_a_stolen_database_stays_unreadable(store: Memory, tmp_path: Path) -> None:
    secret = "Streng vertraulich."
    store.remember(secret)
    thief_dir = tmp_path / "dieb"
    thief_dir.mkdir()
    (thief_dir / "cortex.sqlite3").write_bytes((tmp_path / "cortex.sqlite3").read_bytes())
    thief = Memory(thief_dir / "cortex.sqlite3", thief_dir)  # eigener Schluessel
    assert secret not in thief.all_entries()[0].text


def test_the_key_file_is_private(store: Memory, tmp_path: Path) -> None:
    import stat

    store.remember("irgendwas")
    key = tmp_path / mem.KEY_FILE
    assert key.is_file()
    assert stat.S_IMODE(key.stat().st_mode) == 0o600


def test_the_same_key_reads_the_old_notes(tmp_path: Path) -> None:
    """Neustart darf den Speicher nicht entwerten."""
    first = Memory(tmp_path / "cortex.sqlite3", tmp_path)
    first.remember("bleibt lesbar", topic="Probe")
    again = Memory(tmp_path / "cortex.sqlite3", tmp_path)
    assert again.recall("bleibt")[0].text == "bleibt lesbar"


def test_a_passphrase_needs_no_key_file(tmp_path: Path) -> None:
    """Mit Passphrase liegt gar kein Schluessel auf der Platte."""
    store = Memory(tmp_path / "cortex.sqlite3", tmp_path, passphrase="ein gutes Passwort")
    store.remember("nur mit Passwort lesbar")
    assert not (tmp_path / mem.KEY_FILE).exists()

    same = Memory(tmp_path / "cortex.sqlite3", tmp_path, passphrase="ein gutes Passwort")
    assert same.recall("Passwort")[0].text == "nur mit Passwort lesbar"

    wrong = Memory(tmp_path / "cortex.sqlite3", tmp_path, passphrase="falsches Passwort")
    assert "nur mit Passwort lesbar" not in wrong.all_entries()[0].text


def test_plain_text_from_older_versions_still_reads(store: Memory, tmp_path: Path) -> None:
    """Ein Absturz mitten im Gespraech waere schlimmer als eine alte Notiz."""
    with sqlite3.connect(tmp_path / "cortex.sqlite3") as conn:
        conn.execute("INSERT INTO memory (topic, text) VALUES (?, ?)", ("alt", "Klartext"))
    assert store.all_entries()[0].text == "Klartext"


# -- Die Grenze -----------------------------------------------------------
def test_the_limit_is_four_hundred_megabytes() -> None:
    assert mem.MAX_BYTES == 400_000_000


def test_usage_reports_the_same_number_as_the_limit(store: Memory) -> None:
    """Anzeige und Grenze duerfen nicht auseinanderlaufen."""
    assert store.usage()["limit_mb"] == 400


def test_uploads_count_towards_the_limit(store: Memory, tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    before = store.used_bytes()
    (uploads / "bild.png").write_bytes(b"x" * 500_000)
    assert store.used_bytes() >= before + 500_000


def test_clearing_uploads_frees_the_space(store: Memory, tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "a.png").write_bytes(b"x" * 1000)
    (uploads / "b.png").write_bytes(b"x" * 2000)
    count, freed = store.clear_uploads()
    assert count == 2
    assert freed == 3000
    assert not list(uploads.iterdir())


def test_a_full_store_refuses_more(store: Memory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mem, "MAX_BYTES", 1)  # alles ist ueber der Grenze
    with pytest.raises(MemoryFull):
        store.remember("passt nicht mehr")


def test_making_room_drops_uploads_before_notes(store: Memory, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Bild liegt meist noch woanders -- eine Notiz nicht."""
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "gross.png").write_bytes(b"x" * 2_000_000)
    store.remember("diese Notiz soll bleiben")
    monkeypatch.setattr(mem, "MAX_BYTES", 1_000_000)
    store.make_room()
    assert not list(uploads.iterdir())
    assert store.count() == 1


# -- Anzeige --------------------------------------------------------------
def test_sizes_read_like_a_human_wrote_them() -> None:
    assert human_size(512) == "512 B"
    assert human_size(20_000) == "20 KB"
    assert human_size(1_500_000) == "1,5 MB"
