"""Ai-guard: erkennt, wenn jemand Aquaticy fuer Angriffe missbrauchen will (9.5.16 Lion).

Aquaticy hilft bei Recherche, Schreiben und -- fuer Fachleute -- bei
Verteidigung: Schadcode verstehen, eine Lücke absichern, einen Angriff
erkennen. Was es **nicht** tut, ist jemandem beim Angreifen helfen: eine
Schaddatei bauen, eine Anleitung für einen DDoS-Angriff schreiben, in fremde
Systeme einbrechen, Zugangsdaten stehlen. Der Rechtsrahmen
(:mod:`aquaticy.guardrails`) lehnt das schon je Anfrage ab. Ai-guard sieht
eine Stufe darueber: **über mehrere Chats hinweg** -- versucht dieselbe Person
das immer wieder?

**Wie es entscheidet.** Jede Nachricht eines angemeldeten Kontos wird
eingeschaetzt (dasselbe schnelle Modell wie der Rechtsprüfer). Ist sie ein
Anhaltspunkt -- eine klare Bitte um Schadcode, eine Angriffsanleitung, einen
Einbruch --, wird sie am Konto vermerkt. Ein Anhaltspunkt allein sperrt
niemanden: ein Fachbegriff, eine Frage aus Neugier, ein missverstandener Satz
soll kein Bann sein. Erst **zwei** Anhaltspunkte -- zwei getrennte Nachrichten
mit klarer Missbrauchsabsicht -- sperren das Konto. Auch eine Ablehnung durch
den Rechtsrahmen (Grundgesetz, BGB) zählt als Anhaltspunkt.

**Was gespeichert wird.** Nur der Anlass: Zeitpunkt, Art (etwa "Schadcode"),
ein kurzer Vermerk und der Chat. Nie der ganze Nachrichtentext. Die Daten
dienen allein dieser Prüfung -- das steht so in den Nutzungsbedingungen und
klein unten in den Einstellungen.

**Wer sperrt und entsperrt.** Automatisch bei zwei Anhaltspunkten. Von Hand
über das Terminal:

    aquaticy ban "name"        # Konto sperren
    aquaticy ban 203.0.113.7   # Adresse sperren
    aquaticy unban "name"      # wieder freigeben

Aus dem Chat lässt sich daran nichts ändern -- wie bei allen Schutzgrenzen.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

    from aquaticy.config import Settings

#: So viele Anhaltspunkte sperren ein Konto. Einer ist ein Verdacht -- der
#: darf ein Fachwort oder eine missverstandene Frage sein; zwei ist ein Muster.
NEEDED = 2

LOG = logging.getLogger("aquaticy.aiguard")

#: So lange gilt ein einmal gefaelltes Urteil ueber einen Text (Stunden), damit
#: dasselbe nicht zweimal ein Modell kostet.
DECISION_TTL = 3600.0

#: Kürzer als das schaut sich Ai-guard gar nicht erst an -- ein Gruss ist kein
#: Angriff, und jede Prüfung kostet.
MIN_LENGTH = 12

#: Version der Regeln. Ändert sie sich, gelten alte Urteile nicht mehr.
GUARD_VERSION = "2026-09-26"


@dataclass(frozen=True, slots=True)
class Flag:
    """Ein Anhaltspunkt -- der Anlass, nicht der Text."""

    at: float
    kind: str
    detail: str
    chat: str


@dataclass(frozen=True, slots=True)
class Ban:
    """Eine Sperre -- für ein Konto oder eine Adresse."""

    subject: str
    at: float
    reason: str
    by: str


# ---------------------------------------------------------------------------
# Die Einschätzung durch das Modell
# ---------------------------------------------------------------------------
JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "missbrauch": {"type": "boolean"},
        "art": {"type": "string"},
        "grund": {"type": "string"},
    },
    "required": ["missbrauch"],
    "additionalProperties": False,
}


def judge_prompt(text: str, context: str = "") -> str:
    """Der Text, mit dem das Modell eine Nachricht einschätzt."""
    davor = f"\nWas vorher im Chat stand:\n{context.strip()[:1500]}\n" if context.strip() else ""
    return (
        "Du bist ein Sicherheitsfilter. Entscheide, ob die folgende Nachricht eines "
        "Nutzers Aquaticy für einen ANGRIFF missbrauchen will. Das ist der Fall bei:\n"
        "- der Bitte, Schadsoftware zu schreiben oder zu bauen (Viren, Ransomware, "
        "Trojaner, Keylogger, Stealer);\n"
        "- einer Anleitung für einen Angriff auf fremde Systeme (DDoS, Einbruch, "
        "Exploit gegen ein System, das dem Nutzer nicht gehört);\n"
        "- dem Stehlen von Zugangsdaten, Phishing, dem Umgehen von Anmeldungen;\n"
        "- Anleitungen für Waffen, Sprengstoff oder andere Mittel, um Menschen zu schaden.\n\n"
        "KEIN Missbrauch ist: Verteidigung und Bildung -- Schadcode verstehen, eine "
        "eigene Lücke absichern, einen Angriff erkennen, ein Pentest mit Auftrag, eine "
        "CTF-Aufgabe, allgemeine Sicherheitsfragen. Im Zweifel: kein Missbrauch.\n"
        f"{davor}\n"
        f"Nachricht:\n{text.strip()[:2000]}\n\n"
        'Antworte NUR mit JSON: {"missbrauch": true|false, "art": "<zwei bis vier Wörter>", '
        '"grund": "<kurz>"}'
    )


def _ask_model(prompt: str, settings: Settings) -> str:
    """Ein Aufruf beim schnellen Modell -- die einzige Stelle mit Netz."""
    import litellm

    from aquaticy import metering
    from aquaticy.pace import key_of as pace_key_of
    from aquaticy.pace import paced

    model = _fast_model(settings)
    litellm.suppress_debug_info = True
    with paced(model, pace_key_of(settings, model)):
        response = metering.completion(
            settings,
            enforce=False,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            timeout=max(15.0, float(getattr(settings, "planner_timeout", 20)) * 2),
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "aiguard", "schema": JUDGE_SCHEMA},
            },
            **settings.fast_kwargs_for(model),
        )
    return str(response.choices[0].message.content or "").strip()


def _fast_model(settings: Settings) -> str:
    from aquaticy.system import fast_model

    return fast_model(settings) or str(getattr(settings, "model", "") or "")


def parse_judgement(raw: str) -> tuple[bool, str] | None:
    """Liest die Antwort des Modells. `None` = keine klare Antwort."""
    raw = (raw or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    flag = payload.get("missbrauch")
    if isinstance(flag, str) and flag.strip().lower() in ("true", "false"):
        flag = flag.strip().lower() == "true"
    if not isinstance(flag, bool):
        return None
    art = " ".join(str(payload.get("art") or "").split())[:60] or "Missbrauch"
    return flag, art


_cache: OrderedDict[str, tuple[float, tuple[bool, str] | None]] = OrderedDict()
_cache_lock = threading.Lock()


def forget_judgements() -> None:
    """Leert den Urteilsspeicher -- für Tests."""
    with _cache_lock:
        _cache.clear()


def classify(
    text: str,
    settings: Settings,
    *,
    context: str = "",
    ask: Callable[[str, Settings], str] | None = None,
) -> tuple[bool, str] | None:
    """Ist *text* ein Anhaltspunkt? Returns: (True/False, Art) -- oder None (unklar).

    Kurze Nachrichten und ein leerer Text sind nie ein Anhaltspunkt. Fällt die
    Einschätzung aus (kein Modell, kein Urteil), ist das ausdrücklich KEIN
    Anhaltspunkt: Ai-guard sperrt nur, was es sicher erkennt -- der Rechtsrahmen
    ist die Stelle, die im Zweifel ablehnt.
    """
    text = (text or "").strip()
    if len(text) < MIN_LENGTH:
        return False, ""
    schluessel = hashlib.sha256(
        "\x1f".join((GUARD_VERSION, context, text)).encode("utf-8", "replace")
    ).hexdigest()
    jetzt = time.monotonic()
    with _cache_lock:
        bekannt = _cache.get(schluessel)
        if bekannt is not None and bekannt[0] > jetzt:
            _cache.move_to_end(schluessel)
            return bekannt[1]
    frage = ask or _ask_model
    try:
        roh = frage(judge_prompt(text, context), settings)
        urteil = parse_judgement(roh)
    except Exception:
        urteil = None
    with _cache_lock:
        if len(_cache) > 2048:
            _cache.clear()
        _cache[schluessel] = (jetzt + DECISION_TTL, urteil)
    return urteil


# ---------------------------------------------------------------------------
# Der Speicher: Anhaltspunkte und Sperren, im Konto-Ordner
# ---------------------------------------------------------------------------
def _norm_ip(ip: str) -> str:
    """Eine Adresse in einheitlicher Schreibweise -- oder "" wenn keine."""
    try:
        adresse = ipaddress.ip_address(str(ip or "").split("%", 1)[0].strip())
    except ValueError:
        return ""
    if isinstance(adresse, ipaddress.IPv6Address) and adresse.ipv4_mapped is not None:
        adresse = adresse.ipv4_mapped
    return str(adresse)


class AiGuard:
    """Anhaltspunkte und Sperren, in derselben Datenbank wie die Konten."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._setup()

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
                CREATE TABLE IF NOT EXISTS aiguard_flags (
                    user_id TEXT NOT NULL,
                    at      REAL NOT NULL,
                    kind    TEXT NOT NULL,
                    detail  TEXT NOT NULL DEFAULT '',
                    chat    TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS aiguard_flags_user ON aiguard_flags(user_id);
                CREATE TABLE IF NOT EXISTS aiguard_bans (
                    subject TEXT PRIMARY KEY,
                    at      REAL NOT NULL,
                    reason  TEXT NOT NULL DEFAULT '',
                    by      TEXT NOT NULL DEFAULT ''
                );
                """
            )

    # -- Anhaltspunkte ----------------------------------------------------
    def note(self, user_id: str, kind: str, detail: str = "", chat: str = "",
             *, enforce: bool = True) -> bool:
        """Vermerkt einen Anhaltspunkt. Returns: ob das Konto jetzt gesperrt ist.

        Innerhalb eines Chats zählt derselbe Anlass nur einmal -- sonst wäre
        eine einzige Nachricht, mehrfach geschickt, schon ein Bann. Zwei
        getrennte Anlässe (:data:`NEEDED`) lösen aus.

        Args:
            enforce: Ob bei Erreichen der Schwelle wirklich gesperrt wird.
                Für Ultra-Konten ``False`` (seit 9.5.17): dann nur eine Warnung
                im Terminal, kein Bann. Für Pro/Normal ``True`` -- gesperrt, und
                der Grund steht im Terminal.
        """
        user_id = str(user_id or "")
        if not user_id:
            return False
        with self._lock, self._connect() as conn, closing(conn.cursor()) as cur:
            schon = cur.execute(
                "SELECT 1 FROM aiguard_flags WHERE user_id=? AND kind=? AND chat=? AND chat!=''",
                (user_id, str(kind), str(chat)),
            ).fetchone()
            neu = schon is None
            if neu:
                cur.execute(
                    "INSERT INTO aiguard_flags (user_id, at, kind, detail, chat) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user_id, time.time(), str(kind)[:60], str(detail)[:200], str(chat)[:80]),
                )
            (anzahl,) = cur.execute(
                "SELECT COUNT(*) FROM aiguard_flags WHERE user_id=?", (user_id,)
            ).fetchone()
            erreicht = int(anzahl) >= NEEDED
            gesperrt = erreicht and enforce
            if gesperrt:
                grund = f"{int(anzahl)} Anhaltspunkte für Missbrauch (zuletzt: {kind})"
                cur.execute(
                    "INSERT INTO aiguard_bans (subject, at, reason, by) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(subject) DO NOTHING",
                    (f"user:{user_id}", time.time(), "Ai-guard: " + grund, "ai-guard"),
                )
        # Ins Terminal (seit 9.5.17): bei Pro/Normal der Grund der Sperre, bei
        # Ultra nur eine Warnung.
        if gesperrt:
            LOG.warning("Ai-guard: Konto %s gesperrt — %s", user_id, kind)
            print(f"[Ai-guard] Konto {user_id} gesperrt — Grund: {kind}", flush=True)
        elif erreicht and not enforce and neu:
            LOG.warning("Ai-guard: Ultra-Konto %s auffällig (%d) — nur Warnung, kein Bann",
                        user_id, int(anzahl))
            print(f"[Ai-guard] Ultra-Konto {user_id} auffällig ({int(anzahl)} Anhaltspunkte, "
                  f"zuletzt: {kind}) — nur Warnung, kein Bann.", flush=True)
        return gesperrt

    def flags(self, user_id: str) -> list[Flag]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT at, kind, detail, chat FROM aiguard_flags WHERE user_id=? ORDER BY at",
                (str(user_id),),
            ).fetchall()
        return [Flag(float(r["at"]), str(r["kind"]), str(r["detail"]), str(r["chat"]))
                for r in rows]

    def flag_count(self, user_id: str) -> int:
        with self._connect() as conn:
            (anzahl,) = conn.execute(
                "SELECT COUNT(*) FROM aiguard_flags WHERE user_id=?", (str(user_id),)
            ).fetchone()
        return int(anzahl)

    # -- Sperren ----------------------------------------------------------
    def ban_user(self, user_id: str, reason: str = "", by: str = "terminal") -> None:
        self._ban(f"user:{user_id!s}", reason or "Von Hand gesperrt", by)

    def ban_ip(self, ip: str, reason: str = "", by: str = "terminal") -> str:
        """Sperrt eine Adresse. Returns: die normalisierte Adresse, oder "" wenn ungültig."""
        adresse = _norm_ip(ip)
        if adresse:
            self._ban(f"ip:{adresse}", reason or "Von Hand gesperrt", by)
        return adresse

    def _ban(self, subject: str, reason: str, by: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO aiguard_bans (subject, at, reason, by) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(subject) DO UPDATE SET at=excluded.at, reason=excluded.reason, "
                "by=excluded.by",
                (subject, time.time(), str(reason)[:200], str(by)[:60]),
            )

    def unban_user(self, user_id: str) -> bool:
        """Gibt ein Konto frei -- samt seiner Anhaltspunkte, sonst sperrt es sich sofort neu."""
        with self._lock, self._connect() as conn:
            weg = conn.execute("DELETE FROM aiguard_bans WHERE subject=?",
                               (f"user:{user_id!s}",)).rowcount
            conn.execute("DELETE FROM aiguard_flags WHERE user_id=?", (str(user_id),))
        return bool(weg)

    def unban_ip(self, ip: str) -> bool:
        adresse = _norm_ip(ip)
        if not adresse:
            return False
        with self._lock, self._connect() as conn:
            weg = conn.execute("DELETE FROM aiguard_bans WHERE subject=?",
                               (f"ip:{adresse}",)).rowcount
        return bool(weg)

    def is_banned(self, user_id: str = "", ip: str = "") -> Ban | None:
        """Ist dieses Konto oder diese Adresse gesperrt? Returns: die Sperre oder None."""
        subjects = []
        if user_id:
            subjects.append(f"user:{user_id!s}")
        adresse = _norm_ip(ip)
        if adresse:
            subjects.append(f"ip:{adresse}")
        if not subjects:
            return None
        platz = ",".join("?" for _ in subjects)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT subject, at, reason, by FROM aiguard_bans WHERE subject IN ({platz}) "
                "ORDER BY at LIMIT 1",
                subjects,
            ).fetchone()
        return Ban(str(row["subject"]), float(row["at"]), str(row["reason"]),
                   str(row["by"])) if row else None

    def bans(self) -> list[Ban]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT subject, at, reason, by FROM aiguard_bans ORDER BY at"
            ).fetchall()
        return [Ban(str(r["subject"]), float(r["at"]), str(r["reason"]), str(r["by"]))
                for r in rows]


#: Der Satz, den ein gesperrtes Konto zu sehen bekommt.
BANNED_MESSAGE = (
    "Dieses Konto ist gesperrt. Ai-guard hat wiederholt Anfragen erkannt, die "
    "Aquaticy für Angriffe missbrauchen wollten (Schadsoftware, Angriffsanleitungen "
    "oder Ähnliches). Wenn du das für einen Irrtum hältst, wende dich an den Betreiber "
    "dieser Installation."
)


def guard_for(data_dir: Path | str) -> AiGuard:
    """Ai-guard für die Kontendatenbank eines Datenordners."""
    return AiGuard(Path(data_dir) / "accounts.sqlite3")


def check_message(
    guard: AiGuard,
    account: Any,
    text: str,
    settings: Settings,
    *,
    chat: str = "",
    context: str = "",
    ask: Callable[[str, Settings], str] | None = None,
) -> tuple[bool, str]:
    """Prüft eine Nachricht eines angemeldeten Kontos. Returns: (gesperrt, Grund).

    Ist das Konto schon gesperrt, kommt sofort (True, Grund). Sonst wird die
    Nachricht eingeschätzt; ein Anhaltspunkt wird vermerkt und sperrt beim
    zweiten Mal.
    """
    if account is None:
        return False, ""
    user_id = str(getattr(account, "id", "") or "")
    laufend = guard.is_banned(user_id=user_id, ip=str(getattr(account, "last_ip", "") or ""))
    if laufend is not None:
        return True, BANNED_MESSAGE
    urteil = classify(text, settings, context=context, ask=ask)
    if not urteil or not urteil[0]:
        return False, ""
    darf_bannen = not bool(getattr(account, "ultra", False))
    gesperrt = guard.note(user_id, urteil[1], detail=urteil[1], chat=chat, enforce=darf_bannen)
    return (True, BANNED_MESSAGE) if gesperrt else (False, "")
