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
from typing import Any

from aquaticy.memory import secure_file

SESSION_DAYS = 30

#: Das Kontingent eines normalen Kontos steht seit 9.5.14 in aquaticy/quota.py
#: (5-Stunden-Sitzung und Woche). Ein Pro-Konto hat keines.
EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")
USERNAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{2,40}$")
PRO_CODE_RE = re.compile(r"^[A-Z0-9]{9}$")
PRO_CODE_IN_TEXT_RE = re.compile(r"(?<![A-Z0-9])[A-Z0-9]{9}(?![A-Z0-9])", re.IGNORECASE)

#: Der Ultra-Code (seit 9.5.17): 14 Zeichen, mit Buchstabe, Ziffer UND
#: Sonderzeichen -- ein eigenes, sicheres Passwort, unabhängig vom Pro-Code.
ULTRA_SPECIALS = "!@#$%&*+-=?"
ULTRA_CODE_LEN = 14


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    email: str
    plan: str
    created_at: float
    username: str = ""
    #: Die zuletzt gesehene Adresse und wann (seit 9.5.16 Lion, fuer `aquaticy
    #: list` und Ai-guard). Im Klartext -- der Betreiber sperrt damit gezielt.
    last_ip: str = ""
    last_seen: float = 0.0

    @property
    def pro(self) -> bool:
        return self.plan == "pro"

    @property
    def ultra(self) -> bool:
        return self.plan == "ultra"

    @property
    def elevated(self) -> bool:
        """Pro oder Ultra -- alles, was über ein normales Konto hinausgeht."""
        return self.plan in ("pro", "ultra")

    @property
    def plan_label(self) -> str:
        return {"ultra": "Ultra", "pro": "Pro"}.get(self.plan, "Normal")


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


#: Wie streng eine Sitzung an die Adresse gebunden ist (AQUATICY_SESSION_IP):
#: "netz" (Standard): dasselbe /16-Netz bei IPv4, dasselbe /48 bei IPv6;
#: "genau": dieselbe Adresse; "aus": gar nicht.
SESSION_IP_MODE = (os.environ.get("AQUATICY_SESSION_IP", "netz").strip().lower() or "netz")


def ip_scope(ip: str) -> str:
    """Das, woran eine Sitzung haengt: das Netz der Adresse (oder die Adresse)."""
    import ipaddress

    try:
        adresse = ipaddress.ip_address(str(ip or "").split("%", 1)[0])
    except ValueError:
        return str(ip or "")
    if isinstance(adresse, ipaddress.IPv6Address) and adresse.ipv4_mapped is not None:
        adresse = adresse.ipv4_mapped
    if SESSION_IP_MODE == "genau":
        return str(adresse)
    praefix = 16 if adresse.version == 4 else 48
    return str(ipaddress.ip_network(f"{adresse}/{praefix}", strict=False))


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if len(value) > 254 or not EMAIL_RE.fullmatch(value):
        raise ValueError("Bitte gib eine gültige E-Mail-Adresse ein.")
    return value


#: Mindestlaenge neuer Passwoerter (seit 9.5.15; vorher 7). Bestehende
#: Passwoerter bleiben gueltig -- die Grenze gilt beim Anlegen.
MIN_PASSWORD = 12


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Das Passwort braucht mindestens {MIN_PASSWORD} Zeichen.")
    if len(password) > 128:
        raise ValueError("Das Passwort darf höchstens 128 Zeichen lang sein.")


def normalize_username(username: str) -> str:
    value = " ".join((username or "").strip().split())
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("Der Nutzername braucht 2 bis 40 sichtbare Zeichen.")
    return value


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


def valid_ultra_code(code: str) -> bool:
    """14 Zeichen, dabei mindestens ein Buchstabe, eine Ziffer und ein Sonderzeichen."""
    code = str(code or "")
    if len(code) != ULTRA_CODE_LEN or any(c.isspace() for c in code):
        return False
    hat_buchstabe = any(c.isalpha() for c in code)
    hat_ziffer = any(c.isdigit() for c in code)
    hat_zeichen = any(c in ULTRA_SPECIALS for c in code)
    erlaubt = all(c.isalnum() or c in ULTRA_SPECIALS for c in code)
    return hat_buchstabe and hat_ziffer and hat_zeichen and erlaubt


def new_ultra_code() -> str:
    """Ein neuer Ultra-Code -- 14 Zeichen mit garantierter Vielfalt."""
    buchstaben = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz"
    ziffern = "23456789"
    pool = buchstaben + ziffern + ULTRA_SPECIALS
    while True:
        rest = [secrets.choice(pool) for _ in range(ULTRA_CODE_LEN - 3)]
        code = [secrets.choice(buchstaben), secrets.choice(ziffern),
                secrets.choice(ULTRA_SPECIALS), *rest]
        secrets.SystemRandom().shuffle(code)
        gebaut = "".join(code)
        if valid_ultra_code(gebaut):
            return gebaut


def ultra_code_for(data_dir: Path) -> str:
    """Liest den lokalen Ultra-Code oder erzeugt ihn einmalig und geschützt."""
    configured = os.environ.get("AQUATICY_ULTRA_CODE", "").strip()
    if configured:
        if not valid_ultra_code(configured):
            raise ValueError(
                "AQUATICY_ULTRA_CODE muss 14 Zeichen lang sein und mindestens einen "
                "Buchstaben, eine Ziffer und ein Sonderzeichen enthalten.")
        return configured
    path = Path(data_dir) / "ultra-code.txt"
    if path.is_file():
        code = path.read_text(encoding="utf-8").strip()
        if valid_ultra_code(code):
            return code
    code = new_ultra_code()
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

    #: Ab so vielen Eintraegen wird aufgeraeumt (seit 9.5.15). Bis dahin blieb
    #: fuer jede Adresse, die je angefragt hatte, ein Eintrag fuer immer liegen.
    SWEEP_AT = 10_000

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._events) >= self.SWEEP_AT:
                self._sweep(now)
            events = self._events[key]
            while events and now - events[0] >= self.window:
                events.popleft()
            if len(events) >= self.attempts:
                return False
            events.append(now)
            return True

    def _sweep(self, now: float) -> None:
        """Wirft alle Eintraege weg, deren letzte Anfrage aus dem Fenster ist."""
        for key in [k for k, ev in self._events.items() if not ev or now - ev[-1] >= self.window]:
            self._events.pop(key, None)

    def __len__(self) -> int:
        return len(self._events)


class AuthStore:
    """SQLite-Ablage für Konten und undurchsichtige Browser-Sitzungen."""

    def __init__(self, data_dir: Path, pro_code: str, ultra_code: str = "") -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "accounts.sqlite3"
        self.users_dir = self.data_dir / "users"
        self.pro_code = pro_code
        # Der Ultra-Code (seit 9.5.17) -- eigenes Passwort, unabhängig vom Pro-Code.
        self.ultra_code = ultra_code or ultra_code_for(Path(data_dir))
        self._lock = threading.RLock()
        secure_directory(self.data_dir)
        secure_directory(self.users_dir)
        self._pepper = self._load_pepper()
        #: Das Geheimnis fuer den Schluesselbund -- erst geladen, wenn es gebraucht wird.
        self._vault_secret: bytes | None = None
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
                    username TEXT NOT NULL,
                    password_hash BLOB NOT NULL,
                    password_salt BLOB NOT NULL,
                    plan TEXT NOT NULL CHECK(plan IN ('normal','pro','ultra')),
                    created_at REAL NOT NULL,
                    terms_version TEXT NOT NULL,
                    terms_accepted_at REAL NOT NULL
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
            # Konten aus 9.4.2 bleiben gültig. Für neue Konten wird die
            # ausdrücklich bestätigte Fassung unten beim INSERT festgehalten.
            # Alte Tabellen (vor 9.5.17) erlauben per CHECK nur normal/pro.
            # Dann die Tabelle einmal ohne die enge Bedingung neu aufbauen,
            # damit Ultra-Konten angelegt werden können.
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
            ).fetchone()
            if sql and "ultra" not in str(sql[0] or ""):
                conn.executescript(
                    """
                    ALTER TABLE users RENAME TO users_alt;
                    CREATE TABLE users (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE,
                        username TEXT NOT NULL,
                        password_hash BLOB NOT NULL,
                        password_salt BLOB NOT NULL,
                        plan TEXT NOT NULL CHECK(plan IN ('normal','pro','ultra')),
                        created_at REAL NOT NULL,
                        terms_version TEXT NOT NULL DEFAULT '',
                        terms_accepted_at REAL NOT NULL DEFAULT 0,
                        last_ip TEXT NOT NULL DEFAULT '',
                        last_seen REAL NOT NULL DEFAULT 0
                    );
                    INSERT INTO users (id, email, username, password_hash, password_salt,
                        plan, created_at, terms_version, terms_accepted_at)
                        SELECT id, email, username, password_hash, password_salt,
                        plan, created_at, terms_version, terms_accepted_at FROM users_alt;
                    DROP TABLE users_alt;
                    """
                )
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(users)")}
            if "terms_version" not in columns:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN terms_version TEXT NOT NULL DEFAULT ''"
                )
            if "terms_accepted_at" not in columns:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN terms_accepted_at REAL NOT NULL DEFAULT 0"
                )
            if "username" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN username TEXT NOT NULL DEFAULT ''")
                conn.execute(
                    "UPDATE users SET username=CASE "
                    "WHEN instr(email, '@') > 1 THEN substr(email, 1, instr(email, '@') - 1) "
                    "ELSE 'Nutzer' END WHERE username=''"
                )
            # Zuletzt gesehene Adresse -- fuer `aquaticy list` und Ai-guard (9.5.16).
            if "last_ip" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN last_ip TEXT NOT NULL DEFAULT ''")
            if "last_seen" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN last_seen REAL NOT NULL DEFAULT 0")
            # Adresse bei der Registrierung -- ein Konto pro Adresse (seit 9.5.17).
            if "created_ip" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN created_ip TEXT NOT NULL DEFAULT ''")
        secure_file(self.db_path)

    def profile_dir(self, user_id: str) -> Path:
        return self.users_dir / user_id

    def _account(self, row: sqlite3.Row | None) -> Account | None:
        if row is None:
            return None
        schluessel = row.keys()
        return Account(
            str(row["id"]), str(row["email"]), str(row["plan"]), row["created_at"],
            str(row["username"]),
            str(row["last_ip"]) if "last_ip" in schluessel and row["last_ip"] else "",
            float(row["last_seen"]) if "last_seen" in schluessel and row["last_seen"] else 0.0,
        )

    def register(
        self,
        email: str,
        password: str,
        plan: str,
        pro_code: str = "",
        *,
        username: str = "",
        terms_accepted: bool = False,
        terms_version: str = "",
        ip: str = "",
    ) -> Account:
        email = normalize_email(email)
        fallback_username = email.split("@", 1)[0]
        if len(fallback_username) < 2:
            fallback_username += "1"
        username = normalize_username(username or fallback_username)
        validate_password(password)
        if not terms_accepted or not terms_version.strip():
            raise ValueError(
                "Bitte stimme den Datenschutz- und Nutzungsbedingungen ausdrücklich zu."
            )
        plan = (plan or "normal").strip().lower()
        if plan not in ("normal", "pro", "ultra"):
            raise ValueError("Wähle ein normales, ein Pro- oder ein Ultra-Konto.")
        if plan == "pro" and not hmac.compare_digest(code_from_input(pro_code), self.pro_code):
            raise ValueError("Der Pro-Code stimmt nicht.")
        # Ultra hat ein eigenes, langes Passwort (14 Zeichen mit Sonderzeichen).
        # Es wird als Ganzes verglichen, nicht als 9-stelliger Block.
        if plan == "ultra" and not hmac.compare_digest(
                (pro_code or "").strip(), self.ultra_code):
            raise ValueError("Der Ultra-Code stimmt nicht.")
        salt = secrets.token_bytes(16)
        user_id = secrets.token_hex(16)
        now = time.time()
        vergeben = ValueError(
            "Mit diesen Angaben lässt sich kein neues Konto anlegen. Hast du schon eins? "
            "Dann melde dich an — sonst wähle einen anderen Nutzernamen."
        )
        from aquaticy.aiguard import _norm_ip

        adresse = _norm_ip(ip)
        # Loopback (der eigene Rechner, Tests) fällt aus der Ein-Konto-pro-
        # Adresse-Regel heraus -- sie richtet sich gegen fremde Anschlüsse.
        pruefe_adresse = adresse
        try:
            import ipaddress as _ip

            if adresse and _ip.ip_address(adresse).is_loopback:
                pruefe_adresse = ""
        except ValueError:
            pruefe_adresse = ""
        try:
            with self._lock, self._connect() as conn:
                # Nutzernamen sind eindeutig (seit 9.5.16) -- sonst traefe
                # `aquaticy ban <name>` womoeglich das falsche Konto.
                if conn.execute("SELECT 1 FROM users WHERE lower(username) = lower(?)",
                                (username,)).fetchone():
                    raise vergeben
                # Ein Konto pro Adresse (seit 9.5.17): gibt es von dieser
                # Adresse schon eins, geht kein zweites.
                if pruefe_adresse and conn.execute(
                        "SELECT 1 FROM users WHERE created_ip = ?", (pruefe_adresse,)).fetchone():
                    raise ValueError(
                        "Von dieser Adresse gibt es schon ein Konto. Pro Anschluss ist ein "
                        "Konto möglich — melde dich mit dem vorhandenen an."
                    )
                conn.execute(
                    "INSERT INTO users "
                    "(id, email, username, password_hash, password_salt, plan, created_at, "
                    "terms_version, terms_accepted_at, created_ip, last_ip, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        email,
                        username,
                        _password_hash(password, salt),
                        salt,
                        plan,
                        now,
                        terms_version.strip(),
                        now,
                        adresse,
                        adresse,
                        now if adresse else 0.0,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            # Ein Satz fuer E-Mail UND Nutzername (seit 9.5.16): vorher sagte die
            # Registrierung genau, welche E-Mail-Adresse hier ein Konto hat.
            raise vergeben from exc
        folder = self.profile_dir(user_id)
        secure_directory(folder)
        return Account(user_id, email, plan, now, username)

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
                    # Das Netz, nicht die genaue Adresse (siehe session_account).
                    _secret_hash(ip_scope(ip), self._pepper),
                    now,
                    now + SESSION_DAYS * 86400,
                ),
            )
        return token

    def session_account(self, token: str, device: str, ip: str | None = None) -> Account | None:
        """Das Konto zu einem Sitzungskeks.

        Args:
            ip: Die Adresse der Anfrage. Seit 9.5.15 wird sie geprueft -- nicht
                genau, sondern ihr Netz (``ip_scope``): ein gestohlener Keks
                gilt aus einem fremden Netz nicht, ein Handy, das im selben
                Netz die Adresse wechselt, bleibt angemeldet. ``None`` nur fuer
                interne Aufrufe ohne Anfrage.
        """
        if not token:
            return None
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT users.*, sessions.ip_hash AS _ip FROM sessions "
                "JOIN users ON users.id=sessions.user_id "
                "WHERE token_hash=? AND device_hash=? AND expires_at>=?",
                (_secret_hash(token, self._pepper), _secret_hash(device, self._pepper), now),
            ).fetchone()
        if row is None:
            return None
        if ip is not None and SESSION_IP_MODE != "aus":
            erwartet = str(row["_ip"] or "")
            if not hmac.compare_digest(erwartet, _secret_hash(ip_scope(ip), self._pepper)):
                return None
        return self._account(row)

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash=?",
                (_secret_hash(token, self._pepper),),
            )

    def account(self, user_id: str) -> Account | None:
        """Ein Konto nach seiner Kennung -- oder None."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (str(user_id),)).fetchone()
        return self._account(row)

    def quota(self, account: Account) -> Any:
        """Das Kontingent eines Kontos (aquaticy/quota.py) -- in dieser Datenbank.

        Es haengt an der Kennung des Kontos, nicht am Profilordner: wer dort
        Chats, Speicher oder Verlauf loescht, loescht nicht seinen Verbrauch.
        """
        from aquaticy.quota import Quota

        # Pro bekommt das doppelte Kontingent (seit 9.5.17); Ultra hat gar
        # keins (dort ruft niemand diese Methode).
        faktor = 2.0 if account.plan == "pro" else 1.0
        return Quota(self.db_path, account.id, float(account.created_at or 0.0), factor=faktor)

    def vault(self, account: Account) -> Any:
        """Der Schluesselbund eines Kontos (aquaticy/keyvault.py) -- in dieser Datenbank.

        Verschluesselt mit einem Schluessel je Konto, abgeleitet aus
        ``vault.key`` neben der Datenbank. Nur fuer genau dieses Konto.
        """
        from aquaticy.keyvault import KeyVault, load_secret

        if self._vault_secret is None:
            self._vault_secret = load_secret(self.data_dir / "vault.key")
        return KeyVault(self.db_path, account.id, self._vault_secret)

    def accounts(self) -> list[Account]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [account for row in rows if (account := self._account(row)) is not None]

    def note_seen(self, user_id: str, ip: str) -> None:
        """Haelt die zuletzt gesehene Adresse eines Kontos fest (im Klartext).

        Fuer `aquaticy list` (der Betreiber sieht, von wo ein Konto kommt) und
        fuer Ai-guard, das damit eine Adresse sperren kann.
        """
        adresse = str(ip or "").strip()[:64]
        if not adresse or adresse == "unknown":
            return
        with suppress(sqlite3.Error), self._lock, self._connect() as conn:
            conn.execute("UPDATE users SET last_ip=?, last_seen=? WHERE id=?",
                         (adresse, time.time(), str(user_id)))

    def account_by_name(self, name: str) -> Account | None:
        """Ein Konto nach Nutzername oder E-Mail (fuer `aquaticy ban/unban`)."""
        wanted = str(name or "").strip()
        if not wanted:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE lower(username)=lower(?) OR lower(email)=lower(?) "
                "ORDER BY created_at LIMIT 1",
                (wanted, normalize_email(wanted) if "@" in wanted else wanted),
            ).fetchone()
        return self._account(row)


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
