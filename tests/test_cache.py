"""Tests fuer den SQLite-Cache und den Verlauf."""

from __future__ import annotations

import time
from pathlib import Path

from scoutr.cache import Cache, cache_key


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
