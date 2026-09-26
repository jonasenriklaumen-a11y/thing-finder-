"""Das Kontingent normaler Konten: ein 5-Stunden-Fenster und eine Woche.

Bis 9.5.13 hatte ein normales Konto 150.000 Token -- fuer immer. Seit 9.5.14
gilt, wie bei Claude:

* **Sitzung (5 Stunden): 200.000 Token.** Die Sitzung beginnt mit der ersten
  Nachricht und endet fuenf Stunden spaeter. Danach ist sie weg; die naechste
  Nachricht beginnt eine neue, wieder mit vollem Budget.
* **Woche: 1.500.000 Token.** Die Woche beginnt zur Uhrzeit der
  Kontoerstellung: wer sein Konto an einem Dienstag um 14:32 angelegt hat,
  bekommt jeden Dienstag um 14:32 eine frische Woche -- in der Ortszeit des
  Servers, also auch ueber die Zeitumstellung hinweg um 14:32.

Wo es liegt: in der Kontendatenbank (``accounts.sqlite3``), an der Kennung des
Kontos -- nicht im Profilordner, nicht im Browser. Wer Verlauf, Speicher oder
Chats loescht, loescht damit nicht seinen Verbrauch. Gerechnet wird nur hier
auf dem Server; der Browser bekommt Prozent und Uhrzeiten, keine Tokenzahlen.

Pro-Konten und der lokale Betrieb ohne Konten haben kein Kontingent.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

#: Budget eines 5-Stunden-Fensters.
SESSION_TOKENS = 200_000
#: So lange dauert eine Sitzung.
SESSION_SECONDS = 5 * 3600
#: Budget einer Woche.
WEEK_TOKENS = 1_500_000
#: Wie lange Einzelposten aufgehoben werden. Laenger als eine Woche braucht
#: sie niemand; aelteres wird beim Eintragen weggeraeumt.
KEEP_SECONDS = 8 * 86400

WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

#: Ein Schloss je Datei: Eintragen und Pruefen sollen sich nicht ueberholen.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(str(path), threading.Lock())


class QuotaExceeded(RuntimeError):
    """Das Kontingent ist aufgebraucht -- mit dem Satz, der das erklaert."""

    def __init__(self, message: str, which: str = "", status: dict[str, Any] | None = None):
        super().__init__(message)
        self.which = which
        self.status = status or {}


def week_window(created_at: float, now: float) -> tuple[float, float]:
    """Beginn und Ende der laufenden Woche.

    Die Woche beginnt am Wochentag und zur Uhrzeit der Kontoerstellung --
    gerechnet in der Ortszeit, damit "Dienstag 14:32" auch nach der
    Zeitumstellung Dienstag 14:32 bleibt.
    """
    created_at = float(created_at or 0.0)
    if now <= created_at:
        anfang = datetime.fromtimestamp(created_at)
        return created_at, (anfang + timedelta(days=7)).timestamp()
    erstellt = datetime.fromtimestamp(created_at)
    jetzt = datetime.fromtimestamp(now)
    zurueck = (jetzt.weekday() - erstellt.weekday()) % 7
    anfang = datetime.combine(jetzt.date() - timedelta(days=zurueck), erstellt.time())
    if anfang > jetzt:
        anfang -= timedelta(days=7)
    return max(anfang.timestamp(), created_at), (anfang + timedelta(days=7)).timestamp()


def _prozent(used: int, limit: int) -> int:
    """Ganze Prozent, aufgerundet -- 0 nur, wenn wirklich nichts verbraucht ist."""
    if used <= 0:
        return 0
    return min(100, max(1, -(-used * 100 // limit)))


def _wochentag(ts: float) -> str:
    return WOCHENTAGE[datetime.fromtimestamp(ts).weekday()]


def _uhrzeit(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _dauer(sekunden: float) -> str:
    """ "3 Std. 12 Min." -- wie lange noch, fuer Menschen."""
    minuten = max(1, int(-(-max(0.0, sekunden) // 60)))
    stunden, minuten = divmod(minuten, 60)
    tage, stunden = divmod(stunden, 24)
    teile = []
    if tage:
        teile.append(f"{tage} {'Tag' if tage == 1 else 'Tage'}")
    if stunden:
        teile.append(f"{stunden} Std.")
    if minuten and not tage:
        teile.append(f"{minuten} Min.")
    return " ".join(teile) or "1 Min."


def reset_text(ts: float, now: float) -> str:
    """Wann es zurueckgesetzt wird -- so, wie man es sagen wuerde."""
    dann = datetime.fromtimestamp(ts)
    jetzt = datetime.fromtimestamp(now)
    if ts - now <= SESSION_SECONDS or (ts - now < 86400 and dann.date() == jetzt.date()):
        return f"in {_dauer(ts - now)} (um {_uhrzeit(ts)})"
    if dann.date() == (jetzt + timedelta(days=1)).date():
        return f"morgen um {_uhrzeit(ts)}"
    return f"{WOCHENTAGE[dann.weekday()]}, {dann.strftime('%d.%m.')} um {_uhrzeit(ts)}"


class Quota:
    """Das Kontingent eines Kontos -- gespeichert in der Kontendatenbank."""

    def __init__(self, db_path: Path | str, account_id: str, created_at: float,
                 factor: float = 1.0) -> None:
        self.db_path = Path(db_path)
        self.account_id = str(account_id)
        self.created_at = float(created_at or 0.0)
        # Der Tarif skaliert das Kontingent (seit 9.5.17): Normal = 1, Pro = 2.
        # Ultra hat gar keins (dort wird kein Quota-Objekt angelegt).
        self.factor = max(1.0, float(factor or 1.0))
        self._lock = _lock_for(self.db_path)
        self._setup()

    @property
    def session_tokens(self) -> int:
        return int(SESSION_TOKENS * self.factor)

    @property
    def week_tokens(self) -> int:
        return int(WEEK_TOKENS * self.factor)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _setup(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS token_usage (
                    account_id TEXT NOT NULL,
                    at         REAL NOT NULL,
                    tokens     INTEGER NOT NULL,
                    model      TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS token_usage_idx ON token_usage(account_id, at);
                CREATE TABLE IF NOT EXISTS token_sessions (
                    account_id TEXT PRIMARY KEY,
                    started_at REAL NOT NULL
                );
                """
            )

    # -- Fenster --------------------------------------------------------------
    def _session_start(self, conn: sqlite3.Connection, now: float) -> float | None:
        row = conn.execute(
            "SELECT started_at FROM token_sessions WHERE account_id = ?", (self.account_id,)
        ).fetchone()
        if row is None:
            return None
        start = float(row["started_at"])
        return start if start <= now < start + SESSION_SECONDS else None

    def _sum(self, conn: sqlite3.Connection, since: float) -> int:
        row = conn.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM token_usage WHERE account_id = ? AND at >= ?",
            (self.account_id, since),
        ).fetchone()
        return int(row[0] or 0)

    def begin(self, now: float | None = None) -> float:
        """Beginnt eine Sitzung, falls gerade keine laeuft. Returns: ihr Beginn."""
        now = time.time() if now is None else now
        with self._lock, self._connect() as conn:
            start = self._session_start(conn, now)
            if start is None:
                start = now
                conn.execute(
                    "INSERT INTO token_sessions (account_id, started_at) VALUES (?, ?) "
                    "ON CONFLICT(account_id) DO UPDATE SET started_at = excluded.started_at",
                    (self.account_id, start),
                )
            return start

    def record(self, tokens: int, model: str = "", now: float | None = None) -> None:
        """Traegt Verbrauch ein. Ohne laufende Sitzung beginnt hier eine."""
        tokens = max(0, int(tokens or 0))
        if tokens <= 0:
            return
        now = time.time() if now is None else now
        self.begin(now)
        with self._lock, self._connect() as conn:
            # Auch nachtraeglich Gebuchtes (Serverarbeit) nie ueber das Limit.
            tokens = min(tokens, self._frei(conn, now))
            if tokens <= 0:
                return
            conn.execute(
                "INSERT INTO token_usage (account_id, at, tokens, model) VALUES (?, ?, ?, ?)",
                (self.account_id, now, tokens, str(model or "")[:120]),
            )
            conn.execute(
                "DELETE FROM token_usage WHERE account_id = ? AND at < ?",
                (self.account_id, now - KEEP_SECONDS),
            )

    # -- Reservieren (seit 9.5.15) ------------------------------------------------
    def reserve(self, tokens: int, model: str = "", now: float | None = None) -> int:
        """Prueft und bucht in EINER Transaktion -- Returns: die Nummer der Buchung.

        Bis 9.5.14 wurde erst geprueft und nach dem Aufruf gebucht. Zwanzig
        Agenten, die gleichzeitig pruefen, sahen alle "noch frei" -- und
        zusammen ueberzogen sie das Limit weit. Jetzt sperrt ``BEGIN
        IMMEDIATE`` die Datenbank fuer die Dauer von Pruefen und Eintragen,
        auch ueber Prozessgrenzen hinweg. Reserviert wird hoechstens, was noch
        frei ist; nach dem Aufruf verrechnet ``settle`` die echten Zahlen.

        Raises:
            QuotaExceeded: Sitzung oder Woche geben nichts mehr her.
        """
        bedarf = max(1, int(tokens or 0))
        now = time.time() if now is None else now
        woche_anfang, _ = week_window(self.created_at, now)
        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("BEGIN IMMEDIATE")
                start = self._session_start(conn, now)
                if start is None:
                    start = now
                    conn.execute(
                        "INSERT INTO token_sessions (account_id, started_at) VALUES (?, ?) "
                        "ON CONFLICT(account_id) DO UPDATE SET started_at = excluded.started_at",
                        (self.account_id, start),
                    )
                frei = min(self.session_tokens - self._sum(conn, start),
                           self.week_tokens - self._sum(conn, woche_anfang))
                zeile = None
                # Nur, wenn der GANZE Bedarf passt (seit 9.5.16). Bis dahin
                # wurde reserviert, was noch frei war, der Aufruf lief trotzdem,
                # und hinterher stand der volle Verbrauch da: 200.099 statt
                # hoechstens 200.000 Token.
                if frei <= 0 or bedarf > frei:
                    conn.execute("ROLLBACK")
                else:
                    zeile = conn.execute(
                        "INSERT INTO token_usage (account_id, at, tokens, model) "
                        "VALUES (?, ?, ?, ?)",
                        (self.account_id, now, bedarf,
                         ("reserviert:" + str(model or ""))[:120]),
                    )
                    conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        if zeile is None:
            # Ausserhalb der Sperre: check() liest selbst und wirft mit dem
            # passenden Satz (Sitzung oder Woche, wann es weitergeht).
            self.check(bedarf, now)
            raise QuotaExceeded(
                "Der Rest deines Kontingents reicht für diese Anfrage nicht mehr. Eine "
                "kürzere Frage oder ein neuer Chat braucht weniger.", "session")
        return int(zeile.lastrowid or 0)

    def _frei(self, conn: sqlite3.Connection, now: float, ohne: int = 0) -> int:
        """Was in Sitzung und Woche noch frei ist -- ohne die Buchung *ohne*."""
        woche_anfang, _ = week_window(self.created_at, now)
        start = self._session_start(conn, now)
        abzug = 0
        if ohne:
            zeile = conn.execute(
                "SELECT tokens, at FROM token_usage WHERE rowid = ? AND account_id = ?",
                (int(ohne), self.account_id)).fetchone()
            abzug = int(zeile["tokens"]) if zeile is not None else 0
        sitzung = (self._sum(conn, start) - abzug) if start is not None else 0
        woche = self._sum(conn, woche_anfang) - abzug
        return max(0, min(self.session_tokens - sitzung, self.week_tokens - woche))

    def settle(self, reservation: int, tokens: int, model: str = "") -> None:
        """Ersetzt eine Reservierung durch den echten Verbrauch (0 = freigeben).

        Nie ueber die Grenze (seit 9.5.16): lag der echte Verbrauch ueber der
        Schaetzung, wird hoechstens bis zum Limit gebucht -- den Rest traegt
        der Betreiber, nicht das Konto.
        """
        tokens = max(0, int(tokens or 0))
        with self._lock, self._connect() as conn:
            if tokens > 0:
                tokens = min(tokens, self._frei(conn, time.time(), ohne=int(reservation)))
            if tokens <= 0:
                conn.execute("DELETE FROM token_usage WHERE rowid = ? AND account_id = ?",
                             (int(reservation), self.account_id))
            else:
                conn.execute(
                    "UPDATE token_usage SET tokens = ?, model = ? WHERE rowid = ? "
                    "AND account_id = ?",
                    (tokens, str(model or "")[:120], int(reservation), self.account_id),
                )

    # -- Stand ----------------------------------------------------------------
    def status(self, now: float | None = None) -> dict[str, Any]:
        """Der Stand in Prozent und Uhrzeiten -- das, was der Browser zeigt.

        Tokenzahlen stehen bewusst nicht darin: angezeigt wird der Anteil.
        """
        now = time.time() if now is None else now
        woche_anfang, woche_ende = week_window(self.created_at, now)
        with self._lock, self._connect() as conn:
            start = self._session_start(conn, now)
            sitzung = self._sum(conn, start) if start is not None else 0
            woche = self._sum(conn, woche_anfang)
        sitzung_prozent = _prozent(sitzung, self.session_tokens)
        woche_prozent = _prozent(woche, self.week_tokens)
        return {
            "limited": True,
            "session": {
                "label": "Aktuelle Sitzung",
                "hint": "5-Stunden-Fenster, beginnt mit deiner ersten Nachricht",
                "percent": sitzung_prozent,
                "active": start is not None,
                "started_at": start,
                "resets_at": start + SESSION_SECONDS if start is not None else None,
                "resets_text": (
                    reset_text(start + SESSION_SECONDS, now) if start is not None
                    else "beginnt mit deiner nächsten Nachricht"
                ),
                "exhausted": sitzung >= self.session_tokens,
            },
            "week": {
                "label": "Diese Woche",
                "hint": (
                    f"setzt sich jeden {_wochentag(self.created_at)} um "
                    f"{_uhrzeit(self.created_at)} zurück (Zeit deiner Kontoerstellung)"
                ),
                "percent": woche_prozent,
                "started_at": woche_anfang,
                "resets_at": woche_ende,
                "resets_text": reset_text(woche_ende, now),
                "exhausted": woche >= self.week_tokens,
            },
            "_used": {"session": sitzung, "week": woche},
        }

    def remaining(self, now: float | None = None) -> int:
        """Was hoechstens noch geht -- das Kleinere aus Sitzung und Woche."""
        stand = self.status(now)
        return max(0, min(self.session_tokens - stand["_used"]["session"],
                          self.week_tokens - stand["_used"]["week"]))

    def check(self, need: int = 0, now: float | None = None) -> None:
        """Wirft ``QuotaExceeded``, wenn Sitzung oder Woche nichts mehr hergeben."""
        now = time.time() if now is None else now
        stand = self.status(now)
        benutzt = stand["_used"]
        if benutzt["week"] >= self.week_tokens or self.week_tokens - benutzt["week"] < need:
            raise QuotaExceeded(
                "Dein Wochenkontingent ist aufgebraucht. Es setzt sich "
                f"{when_phrase(stand['week']['resets_text'])} zurück. Mit einem Pro-Konto gibt es "
                "kein Limit.", "week", public(stand))
        if stand["session"]["active"] and (
            benutzt["session"] >= self.session_tokens
            or self.session_tokens - benutzt["session"] < need
        ):
            raise QuotaExceeded(
                "Das Kontingent dieser 5-Stunden-Sitzung ist aufgebraucht. Es setzt sich "
                f"{when_phrase(stand['session']['resets_text'])} zurück. Mit einem Pro-Konto gibt "
                "es kein Limit.", "session", public(stand))


def when_phrase(text: str) -> str:
    """ "Montag, ..." -> "am Montag, ..." -- damit der Satz stimmt."""
    return text if text.startswith(("in ", "morgen")) else f"am {text}"


def public(stand: dict[str, Any]) -> dict[str, Any]:
    """Der Stand ohne Tokenzahlen -- so geht er an den Browser."""
    return {key: wert for key, wert in stand.items() if not key.startswith("_")}


#: So sieht der Stand fuer Konten ohne Kontingent aus (Pro, lokal).
UNLIMITED: dict[str, Any] = {"limited": False}
