"""Konten, Sitzungen und Schutz vor automatisierten Anmeldeversuchen."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import subprocess
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from aquaticy.memory import secure_file

SESSION_DAYS = 30
NORMAL_TOKEN_LIMIT = 200_000
EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")
PRO_CODE_RE = re.compile(r"^[A-Z0-9]{9}$")
PRO_CODE_IN_TEXT_RE = re.compile(r"(?<![A-Z0-9])[A-Z0-9]{9}(?![A-Z0-9])", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    email: str
    plan: str
    created_at: float

    @property
    def pro(self) -> bool:
        return self.plan == "pro"


def _password_hash(password: str, salt: bytes) -> bytes:
    """Leitet einen langsamen, speicherharten Schluessel aus dem Passwort ab."""
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**15,
        r=8,
        p=1,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )


def _secret_hash(value: str, pepper: bytes) -> str:
    return hmac.new(pepper, value.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if len(value) > 254 or not EMAIL_RE.fullmatch(value):
        raise ValueError("Bitte gib eine gültige E-Mail-Adresse ein.")
    return value


def validate_password(password: str) -> None:
    if len(password) < 15:
        raise ValueError("Das Passwort braucht mindestens 15 Zeichen. Eine Passphrase ist ideal.")
    if len(password) > 128:
        raise ValueError("Das Passwort darf höchstens 128 Zeichen lang sein.")


def new_pro_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(9))


def code_from_input(value: str) -> str:
    """Holt einen neunstelligen Code aus einer kopierten Terminalzeile.

    Menschen kopieren gelegentlich ``Pro-Code: ABC123XYZ (9 Zeichen)`` statt
    nur des Codes. Die Prüfung bleibt streng: Es muss genau ein eigenständiger
    neunstelliger alphanumerischer Block vorhanden sein.
    """
    matches = PRO_CODE_IN_TEXT_RE.findall(value or "")
    return matches[0].upper() if len(matches) == 1 else ""


def pro_code_for(data_dir: Path) -> str:
    """Liest den lokalen Pro-Code oder erzeugt ihn einmalig und geschützt."""
    configured = os.environ.get("AQUATICY_PRO_CODE", "").strip().upper()
    if configured:
        if not PRO_CODE_RE.fullmatch(configured):
            raise ValueError("AQUATICY_PRO_CODE muss genau 9 Buchstaben oder Ziffern enthalten.")
        return configured
    path = data_dir / "pro-code.txt"
    if path.is_file():
        code = path.read_text(encoding="utf-8").strip().upper()
        if PRO_CODE_RE.fullmatch(code):
            return code
    code = new_pro_code()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code + "\n", encoding="utf-8")
    secure_file(path)
    return code


def secure_directory(path: Path) -> None:
    """Beschraenkt ein Datenverzeichnis samt neu angelegter Dateien.

    Unter Windows reicht ``chmod`` nicht: dort entfernen wir die geerbten
    Rechte und geben nur dem Konto des Serverprozesses Vollzugriff. Damit
    erben auch SQLite-WAL-Dateien und neue Uploads dieselbe Grenze.
    """
    path.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        path.chmod(0o700)
    if os.name != "nt":
        return
    user = ""
    with suppress(OSError, subprocess.TimeoutExpired):
        identity = subprocess.run(
            ["whoami"], check=False, capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        user = (identity.stdout or "").strip()
    user = user or os.environ.get("USERNAME", "").strip()
    if not user:
        return
    with suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            [
                "icacls", str(path), "/inheritance:r", "/grant:r",
                f"{user}:(OI)(CI)F",
            ],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


class RateLimiter:
    """Kleine gleitende Grenze je Client, ohne externe Infrastruktur."""

    def __init__(self, attempts: int = 12, window_seconds: int = 60) -> None:
        self.attempts = attempts
        self.window = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] >= self.window:
                events.popleft()
            if len(events) >= self.attempts:
                return False
            events.append(now)
            return True


class AuthStore:
    """SQLite-Ablage für Konten und undurchsichtige Browser-Sitzungen."""

    def __init__(self, data_dir: Path, pro_code: str) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "accounts.sqlite3"
        self.users_dir = self.data_dir / "users"
        self.pro_code = pro_code
        self._lock = threading.RLock()
        secure_directory(self.data_dir)
        secure_directory(self.users_dir)
        self._pepper = self._load_pepper()
        self._setup()

    def _load_pepper(self) -> bytes:
        path = self.data_dir / "auth.key"
        if path.is_file():
            value = path.read_bytes()
            if len(value) >= 32:
                return value
        value = secrets.token_bytes(32)
        path.write_bytes(value)
        secure_file(path)
        return value

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _setup(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash BLOB NOT NULL,
                    password_salt BLOB NOT NULL,
                    plan TEXT NOT NULL CHECK(plan IN ('normal','pro')),
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    device_hash TEXT NOT NULL,
                    ip_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);
                """
            )
        secure_file(self.db_path)

    def profile_dir(self, user_id: str) -> Path:
        return self.users_dir / user_id

    def _account(self, row: sqlite3.Row | None) -> Account | None:
        if row is None:
            return None
        return Account(str(row["id"]), str(row["email"]), str(row["plan"]), row["created_at"])

    def register(self, email: str, password: str, plan: str, pro_code: str = "") -> Account:
        email = normalize_email(email)
        validate_password(password)
        plan = (plan or "normal").strip().lower()
        if plan not in ("normal", "pro"):
            raise ValueError("Wähle ein normales oder ein Pro-Konto.")
        if plan == "pro" and not hmac.compare_digest(code_from_input(pro_code), self.pro_code):
            raise ValueError("Der Pro-Code stimmt nicht.")
        salt = secrets.token_bytes(16)
        user_id = secrets.token_hex(16)
        now = time.time()
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, email, _password_hash(password, salt), salt, plan, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Für diese E-Mail-Adresse gibt es bereits ein Konto.") from exc
        folder = self.profile_dir(user_id)
        secure_directory(folder)
        return Account(user_id, email, plan, now)

    def authenticate(self, email: str, password: str) -> Account | None:
        try:
            email = normalize_email(email)
        except ValueError:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            # Gleiche teure Arbeit auch für unbekannte Adressen: weniger Hinweise
            # für automatisches Durchprobieren von Konten.
            _password_hash(password, b"\0" * 16)
            return None
        actual = _password_hash(password, bytes(row["password_salt"]))
        if not hmac.compare_digest(actual, bytes(row["password_hash"])):
            return None
        return self._account(row)

    def create_session(self, account: Account, device: str, ip: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _secret_hash(token, self._pepper),
                    account.id,
                    _secret_hash(device, self._pepper),
                    _secret_hash(ip, self._pepper),
                    now,
                    now + SESSION_DAYS * 86400,
                ),
            )
        return token

    def session_account(self, token: str, device: str) -> Account | None:
        if not token:
            return None
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id "
                "WHERE token_hash=? AND device_hash=? AND expires_at>=?",
                (_secret_hash(token, self._pepper), _secret_hash(device, self._pepper), now),
            ).fetchone()
        return self._account(row)

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash=?",
                (_secret_hash(token, self._pepper),),
            )

    def accounts(self) -> list[Account]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [account for row in rows if (account := self._account(row)) is not None]


def folder_bytes(folder: Path) -> int:
    total = 0
    if not folder.is_dir():
        return total
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total
