"""Private, user-scoped snapshots used by visual research and monitoring."""

from __future__ import annotations

import hashlib
import re
import time
from contextlib import suppress
from pathlib import Path

MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MEDIA_ID = re.compile(r"^[0-9]{13}-[0-9a-f]{16}\.(?:jpg|png|webp|gif)$")
MAX_SNAPSHOTS = 200


def _directory(data_dir: Path | str) -> Path:
    path = Path(data_dir) / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_snapshot(data_dir: Path | str, content: bytes, content_type: str) -> str:
    """Store the exact inspected frame and return an opaque media id."""
    mime = str(content_type or "").split(";", 1)[0].lower()
    extension = MIME_EXTENSIONS.get(mime)
    if not extension or not content:
        raise ValueError("Nicht unterstütztes oder leeres Bild.")
    directory = _directory(data_dir)
    digest = hashlib.sha256(content).hexdigest()[:16]
    media_id = f"{int(time.time() * 1000):013d}-{digest}{extension}"
    target = directory / media_id
    if not target.exists():
        target.write_bytes(content)
    snapshots = sorted(
        (item for item in directory.iterdir() if item.is_file() and MEDIA_ID.fullmatch(item.name)),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old in snapshots[MAX_SNAPSHOTS:]:
        with suppress(OSError):
            old.unlink()
    return media_id


def load_snapshot(data_dir: Path | str, media_id: str) -> tuple[bytes, str] | None:
    """Read one snapshot without accepting paths or cross-user locations."""
    wanted = str(media_id or "").strip()
    if not MEDIA_ID.fullmatch(wanted):
        return None
    path = Path(data_dir) / "media" / wanted
    try:
        data = path.read_bytes()
    except OSError:
        return None
    extension = path.suffix.lower()
    mime = next((kind for kind, ext in MIME_EXTENSIONS.items() if ext == extension), "")
    return (data, mime) if data and mime else None
