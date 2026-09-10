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
MEDIA_ID = re.compile(r"^[0-9]{13}-[0-9a-f]{16}(?:-fest)?\.(?:jpg|png|webp|gif)$")
MAX_SNAPSHOTS = 200

#: Marke im Dateinamen fuer Bilder, die bleiben muessen. Ein Schnappschuss aus
#: einer Recherche ist eine Momentaufnahme und darf altern; das Foto, das
#: jemand fuer einen Auftrag hochgeladen hat, ist die Frage selbst -- waere es
#: nach zweihundert Webcam-Bildern weg, suchte der Auftrag nach nichts mehr.
KEEP_MARK = "-fest"


def _directory(data_dir: Path | str) -> Path:
    path = Path(data_dir) / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_snapshot(
    data_dir: Path | str, content: bytes, content_type: str, *, keep: bool = False
) -> str:
    """Store the exact inspected frame and return an opaque media id.

    Mit *keep* bleibt das Bild vom Aufraeumen verschont -- fuer alles, worauf
    sich spaeter noch etwas beruft.
    """
    mime = str(content_type or "").split(";", 1)[0].lower()
    extension = MIME_EXTENSIONS.get(mime)
    if not extension or not content:
        raise ValueError("Nicht unterstütztes oder leeres Bild.")
    directory = _directory(data_dir)
    digest = hashlib.sha256(content).hexdigest()[:16]
    marke = KEEP_MARK if keep else ""
    media_id = f"{int(time.time() * 1000):013d}-{digest}{marke}{extension}"
    target = directory / media_id
    if not target.exists():
        target.write_bytes(content)
    snapshots = sorted(
        (
            item
            for item in directory.iterdir()
            if item.is_file() and MEDIA_ID.fullmatch(item.name) and KEEP_MARK not in item.name
        ),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old in snapshots[MAX_SNAPSHOTS:]:
        with suppress(OSError):
            old.unlink()
    return media_id


def snapshot_path(data_dir: Path | str, media_id: str) -> Path | None:
    """Der Dateipfad eines Schnappschusses -- oder `None`.

    Gebraucht dort, wo nicht die Bytes, sondern eine Datei erwartet wird:
    ein Bildauftrag reicht sein Vergleichsbild an das Vision-Modell weiter.
    Dieselbe strenge Pruefung wie beim Lesen -- ein Name mit Schraegstrichen
    oder Punkten kommt hier gar nicht erst durch.
    """
    wanted = str(media_id or "").strip()
    if not MEDIA_ID.fullmatch(wanted):
        return None
    path = Path(data_dir) / "media" / wanted
    return path if path.is_file() else None


def delete_snapshot(data_dir: Path | str, media_id: str) -> bool:
    """Loescht einen Schnappschuss. `False`, wenn es ihn nicht gab.

    Gedacht fuer den Fall, dass das, was sich darauf berief, verschwindet --
    ein geloeschter Auftrag zum Beispiel. Ein festgehaltenes Bild wird sonst
    nie wieder aufgeraeumt.
    """
    path = snapshot_path(data_dir, media_id)
    if path is None:
        return False
    with suppress(OSError):
        path.unlink()
        return True
    return False


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
