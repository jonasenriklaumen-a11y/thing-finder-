"""Tests fuer den SQLite-Cache und den Verlauf."""

from __future__ import annotations

import time
from pathlib import Path

from cortex.cache import Cache, cache_key


def test_set_get_roundtrip(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3", ttl_hours=24)
    cache.set("k1", {"a": 1, "b": ["x"]}, kind="search", label="test")
    assert cache.get("k1") == {"a": 1, "b": ["x"]}


def test_miss_returns_none(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    assert cache.get("unbekannt") is None


def test_expiry(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3", ttl_hours=24)
    cache.set("k", "wert", ttl=0)
    time.sleep(0.01)
    assert cache.get("k") is None


def test_purge_and_clear(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    cache.set("a", 1, kind="search", ttl=0)
    cache.set("b", 2, kind="page")
    assert cache.purge_expired() == 1
    assert cache.stats() == {"page": 1}
    assert cache.clear("page") == 1
    assert cache.stats() == {}


def test_cache_key_is_stable_and_distinct() -> None:
    assert cache_key("search", "abc", 5) == cache_key("search", "abc", 5)
    assert cache_key("search", "abc", 5) != cache_key("search", "abc", 6)
    assert cache_key("page", "abc").startswith("page:")


def test_history(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    cache.add_history("s1", "Frage 1", "Antwort 1", {"tool_calls": 3})
    cache.add_history("s1", "Frage 2", "Antwort 2")
    cache.add_history("s2", "Andere Session", "...")

    entries = cache.recent_history(limit=10, session_id="s1")
    assert [entry.question for entry in entries] == ["Frage 1", "Frage 2"]
    assert entries[0].meta == {"tool_calls": 3}
    assert len(cache.recent_history(limit=10)) == 3


# ---------------------------------------------------------------------------
# Chats umbenennen und loeschen
# ---------------------------------------------------------------------------
def test_a_chat_is_named_after_its_first_question(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    cache.add_history("s1", "Wo liegt das Ladekabel?", "Im Keller.")
    cache.add_history("s1", "Und die Zange?", "Daneben.")
    chat = cache.recent_chats()[0]
    assert chat["title"] == "Wo liegt das Ladekabel?"
    assert chat["turns"] == 2
    assert chat["renamed"] is False
    assert chat["touched"] > 0, "fuer die Gruppierung nach Datum"


def test_a_renamed_chat_keeps_its_new_name(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    cache.add_history("s1", "Wo liegt das Ladekabel?", "Im Keller.")
    cache.rename_chat("s1", "  Werkzeug   suchen  ")
    chat = cache.recent_chats()[0]
    assert chat["title"] == "Werkzeug suchen", "Leerraum wird zusammengefasst"
    assert chat["renamed"] is True


def test_an_empty_name_falls_back_to_the_first_question(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    cache.add_history("s1", "Wo liegt das Ladekabel?", "Im Keller.")
    cache.rename_chat("s1", "Eigener Name")
    cache.rename_chat("s1", "   ")
    assert cache.recent_chats()[0]["title"] == "Wo liegt das Ladekabel?"


def test_a_very_long_name_is_cut(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    cache.add_history("s1", "Frage", "Antwort")
    assert len(cache.rename_chat("s1", "N" * 500)) == 120


def test_deleting_a_chat_removes_it_completely(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    cache.add_history("s1", "Erste", "A")
    cache.add_history("s1", "Zweite", "B")
    cache.add_history("s2", "Andere", "C")
    cache.rename_chat("s1", "Eigener Name")

    assert cache.delete_chat("s1") == 2
    remaining = cache.recent_chats()
    assert [chat["session_id"] for chat in remaining] == ["s2"]
    assert cache.chat_history("s1") == []

    # Der eigene Name darf nicht als Leiche zurueckbleiben und einen spaeteren
    # Chat mit derselben Kennung falsch benennen.
    cache.add_history("s1", "Ganz neue Frage", "D")
    assert cache.recent_chats()[0]["title"] == "Ganz neue Frage"


def test_deleting_something_that_is_not_there_is_no_error(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    assert cache.delete_chat("gibtesnicht") == 0
    assert cache.delete_chat("") == 0


# ---------------------------------------------------------------------------
# Chats durchsuchen
# ---------------------------------------------------------------------------
def _gespraeche(cache: Cache) -> None:
    cache.add_history("s1", "Wie kündige ich meinen Mietvertrag?", "Schriftlich, drei Monate.")
    cache.add_history("s1", "Und die Kaution?", "Die kommt nach der Abnahme zurück.")
    cache.add_history("s2", "Hallo", "Hallo! Was kann ich für dich tun?")
    cache.add_history("s3", "Rezept für Brot", "Mehl, Wasser, Salz, Hefe.")
    cache.rename_chat("s2", "Kurzer Gruß")


def test_suche_findet_ueber_die_frage(tmp_path):
    cache = Cache(tmp_path / "c.db")
    _gespraeche(cache)
    treffer = cache.search_chats("mietvertrag")
    assert [chat["session_id"] for chat in treffer] == ["s1"]
    assert "Mietvertrag" in treffer[0]["snippet"]


def test_suche_findet_auch_im_wortlaut_der_antwort(tmp_path):
    cache = Cache(tmp_path / "c.db")
    _gespraeche(cache)
    treffer = cache.search_chats("Hefe")
    assert [chat["session_id"] for chat in treffer] == ["s3"]
    assert "Hefe" in treffer[0]["snippet"]


def test_suche_findet_ueber_den_eigenen_namen(tmp_path):
    """Wer den Chat umbenannt hat, soll ihn ueber den neuen Namen finden."""
    cache = Cache(tmp_path / "c.db")
    _gespraeche(cache)
    treffer = cache.search_chats("Gruß")
    assert [chat["session_id"] for chat in treffer] == ["s2"]
    assert treffer[0]["title"] == "Kurzer Gruß"


def test_suche_ist_gross_klein_egal(tmp_path):
    cache = Cache(tmp_path / "c.db")
    _gespraeche(cache)
    assert cache.search_chats("MIETVERTRAG")
    assert cache.search_chats("mietVERTRAG")


def test_jokerzeichen_sind_keine_joker(tmp_path):
    """Ein % in der Suche darf nicht alles finden -- sonst waere jede Suche sinnlos."""
    cache = Cache(tmp_path / "c.db")
    _gespraeche(cache)
    assert cache.search_chats("%") == []
    assert cache.search_chats("_") == []


def test_leere_suche_liefert_nichts(tmp_path):
    cache = Cache(tmp_path / "c.db")
    _gespraeche(cache)
    assert cache.search_chats("   ") == []


def test_ein_chat_erscheint_nur_einmal(tmp_path):
    """Zwei Treffer im selben Chat sind ein Treffer, kein Doppel."""
    cache = Cache(tmp_path / "c.db")
    cache.add_history("s1", "Brot backen", "Brot braucht Mehl.")
    cache.add_history("s1", "Noch mehr Brot", "Brot braucht Zeit.")
    treffer = cache.search_chats("Brot")
    assert len(treffer) == 1
    assert treffer[0]["turns"] == 2


# ---------------------------------------------------------------------------
# Ungelesenes: was ein Auftrag nachts geantwortet hat
# ---------------------------------------------------------------------------
def test_an_unread_chat_says_so(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    cache.add_history(session_id="nachts", question="Was ist neu?", answer="Einiges.")
    cache.add_history(session_id="normal", question="Und sonst?", answer="Nichts.")

    assert all(chat["unread"] is False for chat in cache.recent_chats())

    cache.mark_unread("nachts", reason="auftrag")
    zustand = {chat["session_id"]: chat["unread"] for chat in cache.recent_chats()}
    assert zustand == {"nachts": True, "normal": False}
    assert cache.unread_chats() == {"nachts"}


def test_reading_it_ends_the_glow(tmp_path: Path) -> None:
    """Danach sieht der Chat aus wie jeder andere."""
    cache = Cache(tmp_path / "c.sqlite3")
    cache.add_history(session_id="nachts", question="Frage", answer="Antwort")
    cache.mark_unread("nachts")
    cache.clear_unread("nachts")
    assert cache.unread_chats() == set()
    assert cache.recent_chats()[0]["unread"] is False


def test_marking_twice_is_still_one_chat(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    cache.add_history(session_id="nachts", question="Frage", answer="Antwort")
    cache.mark_unread("nachts")
    cache.mark_unread("nachts")
    assert cache.unread_chats() == {"nachts"}
    # Ohne Kennung passiert gar nichts -- das ist kein Fehler, nur nichts.
    cache.mark_unread("")
    cache.clear_unread("")
    assert cache.unread_chats() == {"nachts"}


def test_the_search_shows_it_too(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "c.sqlite3")
    cache.add_history(session_id="nachts", question="Baustellen in Bremen", answer="Drei.")
    cache.mark_unread("nachts")
    treffer = cache.search_chats("Baustellen")
    assert treffer and treffer[0]["unread"] is True
