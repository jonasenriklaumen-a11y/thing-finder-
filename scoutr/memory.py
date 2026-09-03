"""Der Speicher: was Cortex AI ueber Gespraeche hinweg behalten darf.

Der Merkzettel haelt einzelne Saetze fest ("ich wohne in Bremen"). Der
Speicher ist die Stufe darueber: laengere Notizen, die der Agent von sich
aus anlegt und spaeter wiederfindet, wenn sie zur Frage passen.

Drei Regeln bestimmen den Aufbau:

* **Nur Text.** Das Modell darf schreiben, was es formuliert hat -- keine
  Bilder, keine Binaerdateien. Bilder kommen ausschliesslich vom Nutzer und
  liegen getrennt im Upload-Ordner.
* **Eine harte Obergrenze.** Alles zusammen -- Notizen, Verlauf, Uploads --
  bleibt unter 400 MB. Wird es eng, fliegen die aeltesten Uploads raus, denn
  die sind am leichtesten zu ersetzen.
* **Abschaltbar.** Wer nicht will, dass etwas haengenbleibt, schaltet den
  Speicher aus. Dann schreibt der Agent nichts mehr und findet auch nichts.
* **Verschluesselt.** Notizen liegen nicht im Klartext in der Datenbank. Wer
  die Datei in die Hand bekommt -- aus einem Backup, einem kopierten
  Datenverzeichnis, einem verlorenen Laptop -- liest ohne Schluessel nichts.

Was die Verschluesselung leistet und was nicht
----------------------------------------------
Der Schluessel liegt standardmaessig als Datei neben der Datenbank, lesbar nur
fuer den eigenen Benutzer. Das schuetzt gegen alles, was die Datenbankdatei
allein betrifft: Backups, Kopien, Datentraeger in fremden Haenden. Es schuetzt
NICHT gegen jemanden, der schon im laufenden Benutzerkonto sitzt -- der liest
den Schluessel einfach mit. Wer das auch abdecken will, setzt
``SCOUTR_MEMORY_KEY`` auf eine Passphrase: daraus wird der Schluessel bei
jedem Start neu abgeleitet, und auf der Platte liegt gar keiner.
"""

from __future__ import annotations

import contextlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

#: Mehr als das darf der Speicher eines Nutzers nie belegen. Bewusst
#: 400 Millionen Byte und nicht 400 MiB: die Anzeige soll dieselbe Zahl
#: nennen, die hier steht.
MAX_BYTES = 400_000_000

#: Ab hier raeumt der Speicher von sich aus auf, statt bis zum Anschlag zu
#: warten. Wer erst bei 100 Prozent handelt, steht schon davor.
CLEANUP_AT = 0.9

#: Laenge einer einzelnen Notiz. Ein Modell, das Romane ablegt, fuellt sonst
#: den Speicher mit einer einzigen Eingabe.
MAX_ENTRY_CHARS = 8_000

#: So viele Treffer gibt der Speicher hoechstens zurueck.
MAX_RECALL = 8

#: So viele Notizen zieht die Suche hoechstens aus der Datenbank. Weil die
#: Texte verschluesselt sind, kann SQL nicht filtern -- das passiert nach dem
#: Entschluesseln in Python. Bei ein paar tausend Notizen kostet das
#: Millisekunden; darueber hinaus soll es nicht wachsen.
SEARCH_WINDOW = 5_000

#: Name der Schluesseldatei im Datenverzeichnis.
KEY_FILE = "memory.key"

#: Wird der Schluessel aus einer Passphrase abgeleitet, braucht das ein Salz.
#: Es liegt offen -- ein Salz ist kein Geheimnis, es verhindert nur, dass
#: vorberechnete Tabellen fuer alle Installationen zugleich passen.
KEY_SALT = b"cortex-ai-memory-v1"


class Cipher:
    """Verschluesselt einzelne Textfelder mit Fernet (AES-128 plus HMAC).

    Fernet ist bewusst gewaehlt: fertig gebaut, authentifiziert, kein Spielraum
    fuer eigene Fehler. Selbst gebastelte Verschluesselung waere hier genau die
    falsche Sparsamkeit.
    """

    def __init__(self, key_path: Path, passphrase: str = "") -> None:
        from cryptography.fernet import Fernet

        self._fernet = Fernet(
            _key_from_passphrase(passphrase) if passphrase else _key_from_file(key_path)
        )

    def encrypt(self, text: str) -> str:
        if not text:
            return ""
        return self._fernet.encrypt(text.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """Entschluesselt. Klartext aus aelteren Fassungen bleibt lesbar."""
        from cryptography.fernet import InvalidToken

        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError):
            # Kein gueltiges Token: entweder eine Notiz aus der Zeit vor der
            # Verschluesselung -- die soll lesbar bleiben -- oder ein fremder
            # Schluessel. In beiden Faellen ist Durchreichen besser als ein
            # Absturz mitten im Gespraech.
            return token


def _key_from_file(path: Path) -> bytes:
    """Liest den Schluessel oder legt beim ersten Mal einen an."""
    from cryptography.fernet import Fernet

    if path.is_file():
        key = path.read_bytes().strip()
        if key:
            return key
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    # Nur der eigene Benutzer darf ihn lesen. Auf Dateisystemen ohne Rechte
    # (etwa FAT auf einem USB-Stick) schlaegt das fehl -- kein Grund
    # abzubrechen, aber der Schutz ist dort eben schwaecher.
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return key


def _key_from_passphrase(passphrase: str) -> bytes:
    """Leitet den Schluessel aus einer Passphrase ab -- nichts auf der Platte."""
    import base64

    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    raw = Scrypt(salt=KEY_SALT, length=32, n=2**14, r=8, p=1).derive(
        passphrase.encode("utf-8")
    )
    return base64.urlsafe_b64encode(raw)


@dataclass
class Entry:
    """Eine abgelegte Notiz."""

    id: int
    text: str
    topic: str
    created_at: str

    def as_dict(self) -> dict[str, str | int]:
        return {"id": self.id, "topic": self.topic, "text": self.text, "when": self.created_at}


class MemoryFull(RuntimeError):
    """Der Speicher ist voll und laesst sich nicht weiter aufraeumen."""


class Memory:
    """Textspeicher in derselben SQLite-Datei wie Cache und Verlauf."""

    def __init__(
        self, db_path: Path | str, data_dir: Path | None = None, passphrase: str = ""
    ) -> None:
        self.db_path = Path(db_path)
        #: Der Ordner, dessen Groesse mitzaehlt (Uploads liegen dort).
        self.data_dir = Path(data_dir) if data_dir else self.db_path.parent
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._cipher = Cipher(self.data_dir / KEY_FILE, passphrase)
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _setup(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            # Kein Index auf topic: das Feld ist verschluesselt, ein Index
            # darauf wuerde nur Platz kosten und nie greifen.

    # -- Groesse ----------------------------------------------------------
    def used_bytes(self) -> int:
        """Wie viel Platz Speicher, Verlauf und Uploads zusammen belegen."""
        total = 0
        for path in (self.db_path, *self._sidecars()):
            with contextlib.suppress(OSError):
                total += path.stat().st_size
        total += folder_bytes(self.data_dir / "uploads")
        return total

    def _sidecars(self) -> tuple[Path, ...]:
        """SQLite legt neben der Datei noch Journal und Write-Ahead-Log an."""
        return (
            self.db_path.with_name(self.db_path.name + "-wal"),
            self.db_path.with_name(self.db_path.name + "-shm"),
            self.db_path.with_name(self.db_path.name + "-journal"),
        )

    def usage(self) -> dict[str, object]:
        """Belegung in Zahlen, die sich anzeigen lassen."""
        used = self.used_bytes()
        return {
            "used_bytes": used,
            "limit_bytes": MAX_BYTES,
            "used_mb": round(used / 1_000_000, 1),
            "limit_mb": round(MAX_BYTES / 1_000_000),
            "percent": round(used / MAX_BYTES * 100, 1),
            "entries": self.count(),
            "uploads": folder_bytes(self.data_dir / "uploads"),
        }

    def full(self) -> bool:
        return self.used_bytes() >= MAX_BYTES

    # -- Schreiben --------------------------------------------------------
    def remember(self, text: str, topic: str = "") -> Entry:
        """Legt eine Notiz ab.

        Raises:
            ValueError: Bei leerem Text.
            MemoryFull: Wenn auch nach dem Aufraeumen kein Platz ist.
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("Leerer Text.")
        text = text[:MAX_ENTRY_CHARS]
        topic = (topic or "").strip()[:80]

        if self.used_bytes() > MAX_BYTES * CLEANUP_AT:
            self.make_room()
        if self.full():
            raise MemoryFull(
                f"Der Speicher ist voll ({MAX_BYTES // 1_000_000} MB). "
                "Loesche Hochgeladenes oder alte Notizen."
            )

        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "INSERT INTO memory (topic, text) VALUES (?, ?)",
                (self._cipher.encrypt(topic), self._cipher.encrypt(text)),
            )
            new_id = int(cur.lastrowid or 0)
            row = conn.execute("SELECT * FROM memory WHERE id = ?", (new_id,)).fetchone()
        return self._read(row)

    # -- Lesen ------------------------------------------------------------
    def recall(self, query: str = "", limit: int = MAX_RECALL) -> list[Entry]:
        """Sucht Notizen zu *query*; ohne Suchwort die juengsten.

        Gesucht wird nach dem Entschluesseln in Python -- verschluesselte
        Felder kann SQL nicht durchsehen. Dafuer holt die Abfrage nur ein
        begrenztes Fenster der juengsten Notizen.
        """
        needle = (query or "").strip().lower()
        limit = max(1, min(limit, MAX_RECALL))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM memory ORDER BY id DESC LIMIT ?",
                (limit if not needle else SEARCH_WINDOW,),
            ).fetchall()
        entries = [self._read(row) for row in rows]
        if not needle:
            return entries[:limit]

        hits = [entry for entry in entries if needle in f"{entry.topic} {entry.text}".lower()]
        if not hits:
            # Kein Volltreffer -- einzelne Woerter versuchen, damit
            # "welches Cafe kennst du" auch die Notiz zu "Cafe" findet.
            words = [word for word in needle.split() if len(word) > 3][:4]
            hits = [
                entry
                for entry in entries
                if any(word in f"{entry.topic} {entry.text}".lower() for word in words)
            ]
        return hits[:limit]

    def count(self) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0])

    def all_entries(self, limit: int = 200) -> list[Entry]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM memory ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._read(row) for row in rows]

    def _read(self, row: sqlite3.Row) -> Entry:
        """Eine Zeile in eine lesbare Notiz verwandeln."""
        return Entry(
            id=int(row["id"]),
            text=self._cipher.decrypt(str(row["text"] or "")),
            topic=self._cipher.decrypt(str(row["topic"] or "")),
            created_at=str(row["created_at"] or ""),
        )

    # -- Loeschen ---------------------------------------------------------
    def forget(self, entry_id: int) -> bool:
        with closing(self._connect()) as conn, conn:
            return conn.execute("DELETE FROM memory WHERE id = ?", (entry_id,)).rowcount > 0

    def clear(self) -> int:
        """Loescht alle Notizen und gibt zurueck, wie viele es waren."""
        with closing(self._connect()) as conn, conn:
            count = int(conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0])
            conn.execute("DELETE FROM memory")
        self.compact()
        return count

    def clear_uploads(self) -> tuple[int, int]:
        """Loescht alle hochgeladenen Dateien. (Anzahl, Bytes)"""
        folder = self.data_dir / "uploads"
        removed = freed = 0
        if not folder.is_dir():
            return 0, 0
        for path in folder.iterdir():
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError:
                continue
            removed += 1
            freed += size
        return removed, freed

    def make_room(self) -> int:
        """Schafft Platz: erst alte Uploads, dann die aeltesten Notizen.

        Uploads zuerst, weil ein Bild fast immer noch irgendwo liegt -- eine
        selbst geschriebene Notiz nicht.
        """
        freed = 0
        folder = self.data_dir / "uploads"
        if folder.is_dir():
            files = sorted(
                (item for item in folder.iterdir() if item.is_file()),
                key=lambda item: item.stat().st_mtime,
            )
            for path in files:
                if self.used_bytes() <= MAX_BYTES * 0.7:
                    break
                try:
                    size = path.stat().st_size
                    path.unlink()
                    freed += size
                except OSError:
                    continue

        if self.used_bytes() > MAX_BYTES * CLEANUP_AT:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "DELETE FROM memory WHERE id IN "
                    "(SELECT id FROM memory ORDER BY id ASC LIMIT 100)"
                )
            self.compact()
        return freed

    def compact(self) -> None:
        """Gibt geloeschten Platz an das Dateisystem zurueck."""
        try:
            with closing(self._connect()) as conn:
                conn.execute("VACUUM")
        except sqlite3.Error:
            pass  # VACUUM scheitert bei offener Transaktion -- nicht schlimm


def folder_bytes(folder: Path) -> int:
    """Wie viel Platz alle Dateien in *folder* belegen."""
    if not folder.is_dir():
        return 0
    total = 0
    for path in folder.iterdir():
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def human_size(count: int) -> str:
    """1536000 -> '1,5 MB'. Fuer Anzeigen, nicht fuer Rechnungen."""
    if count < 1000:
        return f"{count} B"
    if count < 1_000_000:
        return f"{count / 1000:.0f} KB"
    return f"{count / 1_000_000:.1f} MB".replace(".", ",")
