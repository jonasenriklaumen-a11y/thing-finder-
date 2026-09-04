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
