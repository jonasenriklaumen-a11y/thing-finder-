from __future__ import annotations

from pathlib import Path

from aquaticy.media import load_snapshot, save_snapshot


def test_snapshot_is_private_to_its_user_directory(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    media_id = save_snapshot(first, b"exact-frame", "image/jpeg")
    assert load_snapshot(first, media_id) == (b"exact-frame", "image/jpeg")
    assert load_snapshot(second, media_id) is None


def test_snapshot_id_cannot_escape_media_directory(tmp_path: Path) -> None:
    assert load_snapshot(tmp_path, "../secret.jpg") is None
    assert load_snapshot(tmp_path, "not-an-id.png") is None
