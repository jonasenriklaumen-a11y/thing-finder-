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


def test_snapshot_path_only_returns_real_files(tmp_path: Path) -> None:
    """Der Bildauftrag reicht eine Datei weiter -- keine erfundene."""
    from aquaticy.media import snapshot_path

    media_id = save_snapshot(tmp_path, b"frame", "image/png")
    pfad = snapshot_path(tmp_path, media_id)
    assert pfad is not None and pfad.read_bytes() == b"frame"
    assert snapshot_path(tmp_path, "../../etc/passwd") is None
    assert snapshot_path(tmp_path, "0000000000000-0000000000000000.png") is None
