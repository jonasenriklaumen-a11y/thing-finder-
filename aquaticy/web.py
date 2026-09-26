"""Weboberflaeche fuer aquaticy -- derselbe Agent, nur im Browser.

Bewusst ohne Webframework: die Standardbibliothek reicht fuer den lokalen
Mehrnutzer-Server, und jede zusaetzliche Abhaengigkeit macht die Installation
komplizierter. Der Server laeuft auf dem eigenen Rechner oder Heimserver.

Die Zwischenschritte gehen als Server-Sent Events an den Browser -- dieselben
Ereignisse, die im Terminal die "[Suche]"- und "[Lese]"-Zeilen erzeugen.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hmac
import json
import logging
import os
import queue
import secrets
import socket
import sqlite3
import threading
import time
import webbrowser
from collections import OrderedDict
from dataclasses import replace
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from aquaticy import VERSION_LABEL, __version__, webview
from aquaticy.auth import Account, AuthStore, RateLimiter, pro_code_for
from aquaticy.cache import Cache
from aquaticy.config import (
    DEFAULT_ENV_PATH,
    SEARCH_BACKEND_KEYS,
    Settings,
    api_key_name_for,
    base_fits,
    find_env_file,
    get_settings,
    guard_on,
    load_env,
    read_env_file,
    reset_settings_cache,
    resolve_model,
    selected_vision_model,
    suggest_model,
    write_env_file,
)
from aquaticy.guardrails import rules_overview
from aquaticy.legal import LEGAL_ROUTES, LEGAL_VERSION, legal_page

UI_FILE = Path(__file__).with_name("webui.html")

#: Standardport der Oberflaeche. Steht hier, weil auch die
#: Google-Rueckleitadresse ihn braucht.
DEFAULT_PORT = 8765

#: So oft geht mindestens ein Byte raus, solange eine Anfrage laeuft.
#: Kurz genug, dass keine Zwischenstation die stille Leitung kappt.
HEARTBEAT_SECONDS = 10.0

#: Wie viele Ereignisse ein Lauf hoechstens aufhebt. Eine lange Recherche
#: kommt auf ein paar tausend; die Grenze ist gegen den Ausreisser, nicht
#: gegen den Alltag.
MAX_RUN_EVENTS = 20_000

#: Was auch ueber die Grenze hinaus noch in den Lauf kommt. Ohne "done"
#: bliebe die Oberflaeche nach einem Ausreisser fuer immer bei "laeuft noch"
#: stehen, obwohl die Antwort laengst fertig ist. Es kommt einmal je Lauf --
#: die Grenze bleibt also eine Grenze.
PAST_THE_LIMIT = frozenset({"done"})

#: So lange kann man einen fertigen Lauf noch abholen, den niemand zu Ende
#: gesehen hat. Danach steht die Antwort ohnehin in den letzten Chats.
RESUME_WINDOW = 15 * 60


class Run:
    """Ein laufender Turn -- auf dem Server, nicht im Browser.

    Bisher lebte eine Anfrage in ihrer Verbindung: wer die Seite verliess,
    das Handy sperrte oder in den Zug fuhr, riss die Leitung ab. Der Agent
    arbeitete zwar weiter (der Faden laeuft), aber die Ereignisse gingen ins
    Leere, und zurueck kam man vor einen leeren Chat -- die Frage musste neu
    gestellt werden, samt aller Wartezeit noch einmal.

    Jetzt schreibt der Turn in diesen Puffer, und die Verbindung liest nur
    daraus. Reisst sie ab, laeuft der Lauf weiter; kommt jemand zurueck,
    haengt er sich ab Ereignis 0 wieder an und sieht den ganzen Verlauf
    nachwachsen, als waere er nie weg gewesen.
    """

    def __init__(self, run_id: str, question: str) -> None:
        self.id = run_id
        self.question = question
        self.started = time.time()
        self.finished = 0.0
        self.events: list[dict[str, Any]] = []
        self.done = False
        #: Hat jemand den Lauf bis zum Schluss gesehen? Wenn nicht, ist er
        #: beim naechsten Laden noch abzuholen.
        self.delivered = False
        self._cond = threading.Condition()
        #: Wurde schon gesagt, dass die Anzeige gekuerzt ist? Einmal reicht.
        self._clipped = False

    def add(self, event: dict[str, Any]) -> None:
        with self._cond:
            if len(self.events) < MAX_RUN_EVENTS or event.get("type") in PAST_THE_LIMIT:
                self.events.append(event)
            elif not self._clipped:
                # Einmal sagen, warum die Anzeige hier aufhoert -- die ganze
                # Antwort steht trotzdem im Verlauf.
                self._clipped = True
                self.events.append(
                    {
                        "type": "note",
                        "text": "Die Anzeige ist hier gekürzt — die vollständige Antwort "
                        "steht in den letzten Chats.",
                    }
                )
            self._cond.notify_all()

    def finish(self) -> None:
        with self._cond:
            self.done = True
            self.finished = time.time()
            self._cond.notify_all()

    def read(self, since: int, timeout: float) -> tuple[list[dict[str, Any]], int, bool]:
        """Wartet auf neue Ereignisse ab *since*.

        Returns:
            Die neuen Ereignisse, den neuen Stand und ob der Lauf fertig ist.
        """
        with self._cond:
            if since >= len(self.events) and not self.done:
                self._cond.wait(timeout)
            return self.events[since:], len(self.events), self.done

    @property
    def resumable(self) -> bool:
        """Lohnt es sich, diesen Lauf beim Laden wieder aufzunehmen?"""
        if not self.done:
            return True
        return not self.delivered and (time.time() - self.finished) < RESUME_WINDOW

    def state(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "events": len(self.events),
            "running": not self.done,
            "resume": self.resumable,
            "seconds": round(time.time() - self.started, 1),
        }


class RunBook:
    """Die Laeufe eines Kontos -- jeder ueber seine Kennung wiederzufinden (9.5.15).

    Eine zweite Anfrage darf warten, bis die erste fertig ist (die Sitzung
    reicht sie nacheinander durch und sagt das auch). Bis 9.5.14 kannte das
    Buch aber nur "den aktuellen" Lauf: der zweite ueberschrieb den ersten,
    und wer die Seite neu lud, haengte sich womoeglich an den falschen. Jetzt
    hat jeder Lauf seine Kennung, ``get`` findet genau ihn, und die
    Oberflaeche nimmt genau den Lauf wieder auf, den ``/api/runstate`` nannte.
    """

    #: So viele fertige Laeufe bleiben abrufbar.
    KEEP = 8

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: Run | None = None
        self._runs: dict[str, Run] = {}

    def start(self, question: str) -> Run:
        """Ein neuer Lauf -- atomar angelegt und unter seiner Kennung abgelegt."""
        with self._lock:
            lauf = Run(secrets.token_hex(8), question)
            self._runs[lauf.id] = lauf
            while len(self._runs) > self.KEEP:
                self._runs.pop(next(iter(self._runs)))
            self.current = lauf
            return lauf

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            return self._runs.get(str(run_id or ""))

    def latest(self) -> Run | None:
        """Der zuletzt gestartete Lauf -- den nennt ``/api/runstate`` samt Kennung."""
        with self._lock:
            return self.current


RUNS = RunBook()

#: Wie lange die Liste der staerksten Modelle gilt. Sie fragt bei Ollama nach;
#: bei jedem Aufruf der Kopfzeile waere das eine Abfrage zu viel.
STRONG_TTL = 30.0

#: Je Zweck eine eigene Liste: fuers Programmieren zaehlen andere Modelle als
#: fuer eine Recherche. Codestral ist beim Code stark und bei der Suche
#: nutzlos -- eine gemeinsame "Bestenliste" waere fuer beide Seiten falsch.
_strong_cache: dict[str, dict[str, Any]] = {}


def strong_models(limit: int = 3, purpose: str = "work") -> list[dict[str, str]]:
    """Die staerksten erreichbaren Modelle -- gemerkt fuer ein paar Sekunden."""
    from aquaticy.system import strongest_models

    purpose = "code" if purpose == "code" else "work"
    settings = SESSION.settings()
    cache_key = f"{settings.data_dir}|{purpose}"
    eintrag = _strong_cache.setdefault(cache_key, {"when": 0.0, "models": []})
    if time.time() - float(eintrag["when"]) > STRONG_TTL:
        try:
            eintrag["models"] = strongest_models(
                settings, limit=max(3, limit), purpose=purpose
            )
        except Exception:
            eintrag["models"] = []
        eintrag["when"] = time.time()
    return list(eintrag["models"])[:limit]


def forget_strong_models(data_dir: Path | None = None) -> None:
    """Nach einer Aenderung an Modell oder Schluesseln neu nachsehen.

    Mit *data_dir* nur fuer dieses Konto: die Schluessel eines Kontos aendern
    nichts an der Rangfolge der anderen, die sollen nicht alle neu rechnen.
    """
    if data_dir is None:
        _strong_cache.clear()
        return
    praefix = f"{data_dir}|"
    for key in list(_strong_cache):
        if key.startswith(praefix):
            _strong_cache.pop(key, None)

#: Alles, was sich auch in `aquaticy setup` einstellen laesst.
SETTING_KEYS: tuple[str, ...] = (
    "AQUATICY_MODEL",
    "AQUATICY_VISION_MODEL",
    "AQUATICY_SUBAGENT_MODEL",
    "AQUATICY_CODE_MODEL",
    "AQUATICY_API_BASE",
    "AQUATICY_SEARCH_BACKEND",
    "AQUATICY_SEARCH_ENGINES",
    "AQUATICY_SEARCH_VARIANTS",
    "AQUATICY_SEARXNG_URL",
    "AQUATICY_LOCATION",
    "AQUATICY_LANG",
    "AQUATICY_COUNTRY",
    "AQUATICY_SUBAGENTS_AUTO",
    "AQUATICY_MAX_SUBAGENTS",
    "AQUATICY_SUBAGENT_BUDGET",
    "AQUATICY_SUBAGENT_PARALLEL",
    "AQUATICY_RPM",
    "AQUATICY_PARALLEL_CALLS",
    "AQUATICY_MAX_TOOL_CALLS",
    "AQUATICY_CONTEXT_TOKENS",
    "AQUATICY_PLANNER_TIMEOUT",
    "AQUATICY_ENABLE_PLAYWRIGHT",
    "AQUATICY_HA_URL",
    "AQUATICY_HA_CONTROL",
    "AQUATICY_GOOGLE",
    "AQUATICY_GOOGLE_WRITE",
    "AQUATICY_STORAGE_URL",
    "AQUATICY_STORAGE_ACCESS",
    "AQUATICY_LAN_ENABLED",
    "AQUATICY_LAN_SUBNET",
    "AQUATICY_MEMORY",
    "AQUATICY_VM_SIZE",
    "AQUATICY_VM_USER_MODE",
    "AQUATICY_LEGAL_GUARD",
    "AQUATICY_AUTO_MODEL",
)

#: Zahlenfelder mit dem Bereich, in dem sie sinnvoll sind. Geprueft wird
#: hier und nicht erst in `config.py`: dort faellt ein unsinniger Wert nur
#: still auf den Standard zurueck -- das Formular meldet dann "gespeichert",
#: und die Einstellung tut trotzdem nichts. Das ist schlimmer als eine
#: Fehlermeldung, weil man es erst Tage spaeter merkt.
NUMBERS: dict[str, tuple[int, int]] = {
    # Wie im Formular und in queries.MAX_VARIANTS -- mehr als drei benutzt die
    # Suche ohnehin nicht (bis 9.5.15 nahm der Server bis 10 an).
    "AQUATICY_SEARCH_VARIANTS": (1, 3),
    "AQUATICY_MAX_SUBAGENTS": (1, 12),
    "AQUATICY_SUBAGENT_BUDGET": (1, 40),
    # 0 heisst "Aquaticy entscheidet" -- das ist der Standard und steht so
    # auch im Formular. Mit 1 als Untergrenze liess sich ein frisches Konto
    # gar nicht speichern.
    "AQUATICY_SUBAGENT_PARALLEL": (0, 12),
    "AQUATICY_RPM": (1, 100_000),
    "AQUATICY_PARALLEL_CALLS": (1, 64),
    "AQUATICY_MAX_TOOL_CALLS": (1, 200),
    "AQUATICY_CONTEXT_TOKENS": (2_000, 2_000_000),
    "AQUATICY_PLANNER_TIMEOUT": (1, 600),
}

#: Felder, die nur bestimmte Woerter annehmen.
CHOICES: dict[str, tuple[str, ...]] = {
    "AQUATICY_STORAGE_ACCESS": ("off", "read", "write"),
    "AQUATICY_VM_SIZE": ("normal", "plus"),
}

#: Wie lang ein Formularwert hoechstens sein darf. Eine Adresse ist keine
#: Textdatei; was laenger ist, ist ein Versehen oder ein Versuch.
MAX_VALUE_CHARS = 2_000

#: Platzhalter im Formular -- ein leeres Key-Feld darf den Key nicht loeschen.
API_KEY_FIELD = "__API_KEY__"

#: Dasselbe fuer das Home-Assistant-Token.
HA_TOKEN_FIELD = "__HA_TOKEN__"

#: Schluessel der Suchmaschine (Brave, Tavily). Dasselbe Spiel wie beim
#: Modell-Key: leer heisst "unveraendert", nicht "loeschen".
SEARCH_KEY_FIELD = "__SEARCH_KEY__"

#: Zugangsdaten der Google-Anwendung. Die ID darf zurueck in den Browser
#: (sie steht ohnehin in jeder Zustimmungsadresse), das Secret nie.
GOOGLE_ID_FIELD = "__GOOGLE_ID__"
GOOGLE_SECRET_FIELD = "__GOOGLE_SECRET__"

#: Groesse einer einzelnen hochgeladenen Datei. Passt zu der Grenze, die
#: aquaticy auch fuer heruntergeladene PDFs zieht.
MAX_UPLOAD_BYTES = 25_000_000

#: So viele Dateien duerfen an einer Nachricht haengen.
MAX_UPLOADS = 5

#: Wie lang eine einzelne Frage sein darf. Alles darueber ist keine Frage
#: mehr, sondern eine Datei -- und die gehoert an die Klammer, wo sie
#: ausgelesen und gekuerzt wird, statt ungeprueft ins Kontextfenster zu
#: laufen.
MAX_MESSAGE_CHARS = 100_000

#: Der ganze Anfragekoerper. Base64 blaeht um ein Drittel auf, dazu kommt der
#: Rest der Nachricht -- ohne Grenze koennte ein einziger Aufruf den Arbeits-
#: speicher fuellen.
MAX_BODY_BYTES = MAX_UPLOAD_BYTES * MAX_UPLOADS * 4 // 3 + 1_000_000

#: Endungen, die als Bild ans Vision-Modell gehen.
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

#: Endungen, deren Inhalt direkt als Text taugt.
TEXT_TYPES = {".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml"}

#: So viele hochgeladene Dateien bleiben liegen. Bilder muessen als Datei auf
#: der Platte stehen, damit das Vision-Modell sie ansehen kann -- ohne Grenze
#: waere der Ordner nach einem Jahr Nutzung voller alter Fotos.
KEEP_UPLOADS = 50

#: So viel Text uebernimmt aquaticy aus einer hochgeladenen Datei. Mehr wuerde
#: das Kontextfenster sprengen, bevor die Recherche ueberhaupt anfaengt.
MAX_FILE_CHARS = 20_000

#: Zugangswort fuer den Netzbetrieb. Leer = kein Schutz (nur lokal sinnvoll).
#: Wird von :func:`serve` gesetzt.
TOKEN: str = ""

#: Name des Cookies, in dem der Browser das Zugangswort behaelt.
TOKEN_COOKIE = "aquaticy_token"

#: Wird gezeigt, wenn jemand ohne gueltiges Zugangswort anklopft.
DENIED_PAGE = """<!doctype html><html lang="de"><meta charset="utf-8">
<title>Aquaticy AI</title>
<body style="background:#0d0f0e;color:#e8ece9;font:15px/1.6 system-ui;
             display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center;max-width:34em;padding:20px">
<h1 style="color:#31c46b;font-size:20px">Aquaticy AI</h1>
<p>Diese Oberflaeche ist mit einem Zugangswort geschuetzt.</p>
<p style="color:#8b9590;font-size:13px">Nimm die vollstaendige Adresse, die beim
Start im Terminal steht &mdash; die mit <code>?token=</code> am Ende.</p>
</div></body></html>"""

AUTH_COOKIE = "aquaticy_session"
CONSENT_COOKIE = "aquaticy_consent"
AUTH: AuthStore | None = None
#: Ai-guard: erkennt Missbrauch über mehrere Chats und sperrt (9.5.16 Lion).
AIGUARD: Any = None
AUTH_LIMIT = RateLimiter(attempts=8, window_seconds=60)
REQUEST_LIMIT = RateLimiter(attempts=240, window_seconds=60)
#: Schluessel testen, je Konto: genug, um einen neuen Schluessel ein paarmal zu
#: probieren -- zu wenig, um den Server als Pruefstelle fuer fremde Schluessel
#: zu missbrauchen (jeder Test ist eine Anfrage beim Anbieter).
KEY_TEST_LIMIT = RateLimiter(attempts=10, window_seconds=60)

_STRING_SETTINGS = {
    "AQUATICY_MODEL": "model",
    "AQUATICY_VISION_MODEL": "vision_model",
    "AQUATICY_SUBAGENT_MODEL": "subagent_model",
    "AQUATICY_CODE_MODEL": "code_model",
    "AQUATICY_API_BASE": "api_base",
    "AQUATICY_SEARCH_BACKEND": "search_backend",
    "AQUATICY_SEARCH_ENGINES": "search_engines",
    "AQUATICY_SEARXNG_URL": "searxng_url",
    "AQUATICY_LOCATION": "location",
    "AQUATICY_LANG": "lang",
    "AQUATICY_COUNTRY": "country",
    "AQUATICY_HA_URL": "ha_url",
    "HA_TOKEN": "ha_token",
    "AQUATICY_GITHUB_TOKEN": "github_token",
    "AQUATICY_LAN_SUBNET": "lan_subnet",
    "AQUATICY_STORAGE_URL": "storage_url",
    "AQUATICY_STORAGE_ACCESS": "storage_access",
    "GOOGLE_CLIENT_ID": "google_client_id",
    "GOOGLE_CLIENT_SECRET": "google_client_secret",
}
_BOOL_SETTINGS = {
    "AQUATICY_SUBAGENTS_AUTO": "subagents_auto",
    "AQUATICY_ENABLE_PLAYWRIGHT": "enable_playwright",
    "AQUATICY_HA_CONTROL": "ha_control",
    "AQUATICY_GOOGLE": "google_enabled",
    "AQUATICY_GOOGLE_WRITE": "google_write",
    "AQUATICY_LAN_ENABLED": "lan_enabled",
    "AQUATICY_MEMORY": "memory_enabled",
    "AQUATICY_VM_USER_MODE": "vm_user_mode",
    "AQUATICY_AUTO_MODEL": "auto_model",
}
_INT_SETTINGS = {
    "AQUATICY_SEARCH_VARIANTS": "search_variants",
    "AQUATICY_MAX_SUBAGENTS": "max_subagents",
    "AQUATICY_SUBAGENT_BUDGET": "subagent_budget",
    "AQUATICY_SUBAGENT_PARALLEL": "subagent_parallel",
    "AQUATICY_RPM": "rpm",
    "AQUATICY_PARALLEL_CALLS": "parallel_calls",
    "AQUATICY_MAX_TOOL_CALLS": "max_tool_calls",
    "AQUATICY_CONTEXT_TOKENS": "context_tokens",
    "AQUATICY_PLANNER_TIMEOUT": "planner_timeout",
}


#: So gross darf das Kontextfenster lokaler Modelle bei normalen Konten sein.
NORMAL_CONTEXT_CAP = 32_768


def header_for(settings: Settings) -> dict[str, Any]:
    """Die Kopfzeile fuer den Zustand, den der Server fuehrt (aquaticy/webview.py)."""
    return webview.header_view(
        settings,
        ui_state().read(),
        strong_model=(strong_models(1) or [{}])[0].get("id", ""),
        strong_code_model=(strong_models(1, purpose="code") or [{}])[0].get("id", ""),
        ha_connected=bool(settings.ha_url and settings.ha_token),
        problems=settings.missing_requirements(),
    )


def storage_view(usage: dict[str, Any]) -> dict[str, Any]:
    """Der Speicherstand mit fertigem Satz ("3,1 von 400 MB belegt · 12 Notizen")."""
    anzahl = int(usage.get("entries") or 0)
    text = (f"{usage.get('used_mb')} von {usage.get('limit_mb')} MB belegt · {anzahl} "
            f"{'Notiz' if anzahl == 1 else 'Notizen'}")
    return {**usage, "text": text, "title": f"{usage.get('used_mb')} von "
            f"{usage.get('limit_mb')} MB belegt"}


def system_gauges(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Die Anzeigen der Auslastung -- Name, Wert, Zusatz, Anteil."""
    anzeigen: list[dict[str, Any]] = []
    cpu, mem, disk, gpu = (data.get(k) or {} for k in ("cpu", "memory", "disk", "gpu"))
    if cpu.get("percent") is not None:
        anzeigen.append({"name": "Prozessor", "value": f"{cpu['percent']} %",
                         "sub": f"{cpu.get('cores')} Kerne · Last {cpu.get('load')}",
                         "percent": cpu["percent"]})
    if mem.get("percent") is not None:
        anzeigen.append({"name": "Arbeitsspeicher", "value": f"{mem.get('used_gb')} GB",
                         "sub": f"von {mem.get('total_gb')} GB", "percent": mem["percent"]})
    if disk.get("percent") is not None:
        anzeigen.append({"name": "Festplatte", "value": f"{disk.get('free_gb')} GB frei",
                         "sub": f"von {disk.get('total_gb')} GB", "percent": disk["percent"]})
    if gpu.get("name"):
        anzeigen.append({"name": "Grafikkarte", "value": f"{gpu.get('percent')} %",
                         "sub": f"{gpu['name']} · {gpu.get('used_gb')} von "
                                f"{gpu.get('total_gb')} GB", "percent": gpu.get("percent")})
    speicher = data.get("storage") or {}
    if speicher:
        anzeigen.append({"name": "Speicher", "value": f"{speicher.get('used_mb')} MB",
                         "sub": f"von {speicher.get('limit_mb')} MB · "
                                f"{speicher.get('entries')} Notizen",
                         "percent": speicher.get("percent")})
    return anzeigen


def usage_view(settings: Settings) -> dict[str, Any]:
    """Der Verbrauch fuer den Browser: Prozent und Uhrzeiten, keine Tokenzahlen.

    Alles hier rechnet der Server -- die Oberflaeche zeichnet nur die Balken.
    """
    from datetime import datetime

    from aquaticy.quota import public

    jetzt = time.time()
    stand_um = "Stand: " + datetime.fromtimestamp(jetzt).strftime("%H:%M")
    kontingent = getattr(settings, "quota", None)
    if kontingent is None:
        return {"limited": False, "text": "Kein Limit", "summary": "Kein Limit",
                "updated_at": jetzt, "updated_text": stand_um}
    try:
        stand = public(kontingent.status(jetzt))
    except Exception:  # pragma: no cover - eine kaputte Datenbank ist kein 500
        return {"limited": True, "error": "Der Verbrauch ließ sich nicht lesen.",
                "summary": "nicht verfügbar", "updated_at": jetzt, "updated_text": stand_um}
    stand["updated_at"] = jetzt
    stand["updated_text"] = stand_um
    stand["summary"] = (f"Sitzung {stand['session']['percent']} % · "
                        f"Woche {stand['week']['percent']} %")
    stand["warning"] = _usage_warning(stand)
    return stand


def _usage_warning(stand: dict[str, Any]) -> str:
    """Ein Satz, sobald ein Limit knapp wird (ab 80 %) -- sonst leer."""
    from aquaticy.quota import when_phrase

    for key, name in (("week", "deines Wochenlimits"), ("session", "dieser Sitzung")):
        teil = stand.get(key) or {}
        prozent = int(teil.get("percent") or 0)
        wann = when_phrase(str(teil.get("resets_text") or ""))
        if teil.get("exhausted"):
            return f"Das Limit {name} ist erreicht — es setzt sich {wann} zurück."
        if prozent >= 80:
            return f"Du hast {prozent} % {name} genutzt — es setzt sich {wann} zurück."
    return ""


def account_quota(profile: Path, account: Account | None = None) -> Any:
    """Das Kontingent eines normalen Kontos (aquaticy/quota.py).

    Es liegt in der Kontendatenbank an der Kennung des Kontos. Nur ohne
    Kontenverwaltung (Tests, kaputter Start) landet es im Profilordner --
    ein normales Konto ohne Kontingent gibt es nicht.
    """
    from aquaticy.quota import Quota

    if AUTH is not None:
        konto = account or AUTH.account(profile.name)
        if konto is not None:
            return AUTH.quota(konto)
    erstellt = float(getattr(account, "created_at", 0.0) or 0.0)
    return Quota(profile / "quota.sqlite3", getattr(account, "id", "") or profile.name, erstellt)


def key_slot_for(model: str) -> str:
    """In welchen Platz des Schluesselbunds ein Schluessel fuer *model* gehoert.

    Ein Anbieter, den Aquaticy kennt, hat seinen eigenen Platz; lokale Modelle
    brauchen keinen; alles andere kommt in den allgemeinen Platz.
    """
    from aquaticy.config import GENERIC_KEY_NAME, PROVIDER_KEYS, provider_of

    anbieter = provider_of(model or "")
    if anbieter in PROVIDER_KEYS:
        return PROVIDER_KEYS[anbieter]
    return GENERIC_KEY_NAME if model else ""


#: Bilder, die ``/api/media?url=`` schon geholt hat: (Datenordner, Adresse) ->
#: (Zeitpunkt, Bytes, Typ). Die Oberflaeche fragt dasselbe Bild beim
#: Neuzeichnen und Wiederoeffnen eines Chats oft mehrmals an.
_MEDIA_CACHE: OrderedDict[tuple[str, str], tuple[float, bytes, str]] = OrderedDict()
_MEDIA_LOCK = threading.Lock()
MEDIA_CACHE_TTL = 600.0
MEDIA_CACHE_BYTES = 48_000_000
_MEDIA_FETCHERS: dict[tuple[str, float, float], Any] = {}


def _media_fetcher(settings: Settings) -> Any:
    """Ein gemeinsamer Abrufer fuer Bilder -- mit gemeinsamer Drossel je Domain.

    Bis 9.5.15 baute jedes Bild einen eigenen, samt eigener Drossel: zwanzig
    Bilder einer Seite gingen dann gleichzeitig an denselben Server. Ohne
    Browser: ein Bild braucht keinen, und ein Bildaufruf darf auf dem Server
    kein Chromium starten.
    """
    from aquaticy.fetch import Fetcher

    schluessel = (settings.user_agent, float(settings.fetch_timeout),
                  float(settings.request_delay_seconds))
    with _MEDIA_LOCK:
        abrufer = _MEDIA_FETCHERS.get(schluessel)
        if abrufer is None:
            abrufer = Fetcher(user_agent=settings.user_agent, timeout=settings.fetch_timeout,
                              delay_seconds=settings.request_delay_seconds,
                              enable_browser=False)
            _MEDIA_FETCHERS[schluessel] = abrufer
        return abrufer


def media_by_url(settings: Settings, target: str) -> tuple[int, bytes, str]:
    """Holt ein oeffentliches Bild fuer die Oberflaeche. Returns: (Status, Inhalt, Typ).

    Seit 9.5.16 zaehlt der Abruf bei normalen Konten ins Kontingent wie jede
    andere Serverarbeit ("seite"); ein Bild aus dem Zwischenspeicher nicht.
    """
    from aquaticy import metering

    schluessel = (str(settings.data_dir), target)
    jetzt = time.monotonic()
    with _MEDIA_LOCK:
        treffer = _MEDIA_CACHE.get(schluessel)
        if treffer and jetzt - treffer[0] < MEDIA_CACHE_TTL:
            _MEDIA_CACHE.move_to_end(schluessel)
            return 200, treffer[1], treffer[2]
    try:
        vorab = metering.reserve_work(settings, "seite")
    except metering.QuotaExceeded as exc:
        return 429, str(exc).encode("utf-8"), "text/plain"
    try:
        visual, error = _media_fetcher(settings).load_public_visual(target)
    finally:
        vorab.cancel()
    if visual is None:
        return 404, (error or "Das Bild ist nicht erreichbar.").encode("utf-8"), "text/plain"
    metering.charge_work(settings, "seite")
    with _MEDIA_LOCK:
        _MEDIA_CACHE[schluessel] = (jetzt, visual.content, visual.content_type)
        while sum(len(e[1]) for e in _MEDIA_CACHE.values()) > MEDIA_CACHE_BYTES:
            _MEDIA_CACHE.popitem(last=False)
    return 200, visual.content, visual.content_type


def trusted_proxies() -> list[Any]:
    """Die Proxys, deren ``X-Forwarded-For`` gilt (``AQUATICY_TRUSTED_PROXIES``).

    Leer (Standard): keinem -- sonst koennte sich jeder mit einer Kopfzeile eine
    beliebige Adresse geben und damit Anfragegrenze und IP-Sperre umgehen.
    """
    import ipaddress

    netze = []
    for teil in os.environ.get("AQUATICY_TRUSTED_PROXIES", "").split(","):
        with contextlib.suppress(ValueError):
            if teil.strip():
                netze.append(ipaddress.ip_network(teil.strip(), strict=False))
    return netze


def client_ip(direkt: str, forwarded: str = "", real_ip: str = "") -> str:
    """Die Adresse des Menschen am anderen Ende.

    Hinter einem eingetragenen Proxy (``AQUATICY_TRUSTED_PROXIES``) die, die der
    Proxy meldet -- sonst teilten sich alle Nutzer dessen eine Adresse: eine
    Anfragegrenze fuer alle, und eine IP-Sperre traefe jeden (seit 9.5.16).
    Genommen wird der LETZTE Eintrag: den hat der eigene Proxy angehaengt, alle
    davor kann der Browser selbst mitgeschickt haben.
    """
    import ipaddress

    try:
        quelle = ipaddress.ip_address(direkt.split("%", 1)[0])
    except ValueError:
        return direkt
    if not any(quelle in netz for netz in trusted_proxies()):
        return direkt
    for kandidat in [*reversed(forwarded.split(",")), real_ip]:
        kandidat = kandidat.strip()
        with contextlib.suppress(ValueError):
            if kandidat:
                return str(ipaddress.ip_address(kandidat))
    return direkt


#: Was die laufende Werkstatt festlegt -- aendert sich eines davon, wird sie
#: neu aufgebaut (siehe ChatSession.reload).
WORKSHOP_FIELDS: tuple[str, ...] = (
    "vm_size", "vm_image", "vm_idle_minutes", "vm_memory_mb", "vm_disk_gb", "vm_cpus",
    "vm_user_mode", "vm_desktop_image", "user_agent", "data_dir",
)


def workshop_changed(alt: Any, neu: Any) -> bool:
    """Braucht die Werkstatt nach diesem Speichern einen Neuaufbau?"""
    return any(getattr(alt, feld, None) != getattr(neu, feld, None) for feld in WORKSHOP_FIELDS)


def delete_job(store: Any, nummer: int, settings: Settings) -> bool:
    """Loescht einen Auftrag -- samt seinem Vergleichsbild.

    Das hochgeladene Foto haengt an genau diesem Auftrag und ist vom
    Aufraeumen ausgenommen. Bis 9.5.16 raeumte es nur der POST-Weg weg;
    ``DELETE /api/jobs`` liess es fuer immer auf dem Server liegen.
    """
    job = store.get(nummer)
    geloescht = bool(store.delete(nummer))
    if geloescht and job is not None and getattr(job, "image_id", ""):
        from aquaticy.media import delete_snapshot

        with contextlib.suppress(OSError, ValueError):
            delete_snapshot(settings.data_dir, job.image_id)
    return geloescht


def _chat_untrusted(entries: list[Any]) -> bool:
    """Stand in diesem Chat schon fremder Text (Webseite, Mail, Anhang)?

    Seit 9.5.16 am Verlauf vermerkt. Aeltere Eintraege tragen den Vermerk
    nicht -- dort gilt: wer Quellen gelesen hat, hatte fremden Text.
    """
    for entry in entries:
        meta = getattr(entry, "meta", None) or {}
        if meta.get("untrusted") or meta.get("sources") or meta.get("visuals"):
            return True
    return False


def _resume(agent: Any, session_id: str, entries: list[Any], untrusted: bool) -> None:
    """``agent.resume`` -- mit dem Vermerk "fremder Text", wo der Agent ihn kennt."""
    import inspect

    turns = [(entry.question, entry.answer) for entry in entries]
    try:
        kennt = "untrusted" in inspect.signature(agent.resume).parameters
    except (TypeError, ValueError):
        kennt = False
    if kennt:
        agent.resume(session_id, turns, untrusted=untrusted)
    else:
        agent.resume(session_id, turns)
        toolbox = getattr(agent, "toolbox", None)
        if toolbox is not None and untrusted:
            with contextlib.suppress(Exception):
                toolbox.untrusted_seen = True


def account_vault(profile: Path, account: Account | None = None) -> Any:
    """Der Schluesselbund eines Kontos (aquaticy/keyvault.py).

    Er liegt in der Kontendatenbank an der Kennung des Kontos. Nur ohne
    Kontenverwaltung (Tests, kaputter Start) im Profilordner.
    """
    from aquaticy.keyvault import KeyVault, load_secret

    if AUTH is not None:
        konto = account or AUTH.account(profile.name)
        if konto is not None:
            return AUTH.vault(konto)
    kennung = getattr(account, "id", "") or profile.name
    return KeyVault(profile / "keys.sqlite3", kennung, load_secret(profile / "vault.key"))


def _move_keys_into_vault(tresor: Any, env_path: Path | None, raw: dict[str, str]) -> None:
    """Schluessel aus der .env des Kontos (bis 9.5.14) wandern in den Schluesselbund.

    Danach stehen sie nicht mehr im Klartext in der Datei. Ein Schluessel, den
    der Schluesselbund schon hat, gewinnt -- er ist der neuere.
    """
    from aquaticy.keyvault import SLOT_BY_NAME, VaultError

    alte = {name: raw[name].strip() for name in SLOT_BY_NAME if raw.get(name, "").strip()}
    if not alte:
        return
    vorhanden = set(tresor.names())
    for name, wert in alte.items():
        if name not in vorhanden:
            with contextlib.suppress(VaultError):
                tresor.set(name, wert)
    if env_path is not None:
        remove_env_keys(env_path, set(alte))


def remove_env_keys(path: Path, names: set[str]) -> None:
    """Streicht Zeilen aus einer .env -- fuer Schluessel, die dort nicht mehr hingehoeren."""
    from aquaticy.memory import secure_file

    if not path.is_file() or not names:
        return
    zeilen = path.read_text(encoding="utf-8").splitlines()
    bleiben = [
        zeile for zeile in zeilen
        if zeile.strip().startswith("#") or "=" not in zeile
        or zeile.split("=", 1)[0].strip().removeprefix("export ").strip() not in names
    ]
    if bleiben != zeilen:
        path.write_text("\n".join(bleiben).rstrip("\n") + "\n", encoding="utf-8")
        secure_file(path)


def _profile_settings(profile: Path, plan: str, account: Account | None = None) -> Settings:
    """Kopiert die Servervorgaben und legt die Werte eines Kontos darueber."""
    base = get_settings()
    settings = replace(
        base,
        data_dir=profile,
        env_path=profile / ".env",
        api_keys=dict(base.api_keys),
        search_keys=dict(base.search_keys),
    )
    # Ohne ${VAR}-Ersetzung (config.read_env_file): sonst holte ein Wert wie
    # "${MISTRAL_API_KEY}" einen Schluessel des Servers in dieses Konto.
    raw = read_env_file(settings.env_path)
    for key, attr in _STRING_SETTINGS.items():
        if key in raw:
            setattr(settings, attr, raw[key].strip())
    for key, attr in _BOOL_SETTINGS.items():
        if key in raw:
            setattr(settings, attr, raw[key].strip().lower() in {"1", "true", "yes", "on", "ja"})
    for key, attr in _INT_SETTINGS.items():
        if key in raw:
            with contextlib.suppress(ValueError):
                setattr(settings, attr, int(raw[key]))
    if "AQUATICY_LEGAL_GUARD" in raw:
        # Nicht ueber _BOOL_SETTINGS: dort waere jeder Tippfehler "aus".
        settings.legal_guard = guard_on(raw["AQUATICY_LEGAL_GUARD"])
    # Die eigenen API-Schluessel des Kontos (9.5.14 Seashell): aus seinem
    # Schluesselbund, verschluesselt, an der Kennung des Kontos. Schluessel,
    # die frueher in der .env des Profils standen, wandern einmal hinein.
    from aquaticy.keyvault import SEARCH_KEY_NAMES

    tresor = account_vault(profile, account)
    _move_keys_into_vault(tresor, settings.env_path, raw)
    eigene = tresor.keys()
    for name, wert in eigene.items():
        (settings.search_keys if name in SEARCH_KEY_NAMES else settings.api_keys)[name] = wert
    settings.own_key_names = frozenset(eigene)
    # Das GitHub-Token des Kontos liegt verschluesselt im Schluesselbund (seit
    # 9.5.16) -- ein altes aus der .env wandert einmal hinein. Das Token des
    # Betreibers bekommt ein Konto nie: es koennte dessen private Repos lesen.
    from aquaticy.addons import GITHUB_TOKEN_KEY

    settings.secret_vault = tresor
    altes = raw.get(GITHUB_TOKEN_KEY, "").strip()
    if altes:
        with contextlib.suppress(Exception):
            if not tresor.secret(GITHUB_TOKEN_KEY):
                tresor.set_secret(GITHUB_TOKEN_KEY, altes)
            remove_env_keys(settings.env_path, {GITHUB_TOKEN_KEY})
    try:
        settings.github_token = tresor.secret(GITHUB_TOKEN_KEY)
    except Exception:
        settings.github_token = ""
    # Den Such-Schluessel des Betreibers teilt ein normales Konto nur bei der
    # Suchmaschine, die der Betreiber selbst gewaehlt hat (seit 9.5.16).
    settings.operator_search_backends = (
        None if plan == "ultra" else frozenset({(base.search_backend or "").lower()}))
    # Eine Modell-Adresse, die das Konto selbst eingetragen hat (nur Pro, s.u.).
    eigene_adresse = raw.get("AQUATICY_API_BASE", "").strip()
    settings.own_api_base = (
        eigene_adresse if plan == "ultra" and eigene_adresse
        and eigene_adresse != (base.api_base or "") else ""
    )
    # Die Adresse des Betreibers gilt fuer SEIN Modell, nicht fuer das, das
    # dieses Konto waehlt (Settings.route).
    settings.api_base_for = base.model
    if plan != "ultra":
        # Netz-Features (Heimnetz, Home Assistant, Lager, Werkstatt-Desktop)
        # gehören seit 9.5.17 nur noch zu Ultra -- Normal UND Pro sind hier
        # gleich beschränkt. Der Unterschied ist das Kontingent (Pro doppelt).
        settings.lan_enabled = False
        settings.ha_url = ""
        settings.ha_token = ""
        settings.ha_control = False
        settings.storage_url = ""
        settings.storage_access = "off"
        settings.vm_size = "normal"
        settings.vm_cpus = 1
        settings.vm_memory_mb = 1024
        settings.vm_disk_gb = 4
        # Die Rechts-Leitplanken lassen sich nur mit Ultra abschalten (seit
        # 9.5.17 auch Pro nicht mehr). Steht in der .env trotzdem "aus", gilt
        # hier "an".
        settings.legal_guard = True
        settings.vm_user_mode = False
        # 5-Stunden-Sitzung und Woche, am Konto gespeichert (aquaticy/quota.py).
        # Pro bekommt über AUTH.quota() den doppelten Faktor.
        settings.quota = account_quota(profile, account)
        # Wohin der Server Anfragen schickt, bestimmt der Betreiber -- eine
        # eigene Modell- oder SearXNG-Adresse gibt es nur mit Ultra.
        settings.api_base = base.api_base
        settings.searxng_url = base.searxng_url
        obergrenze = max(int(base.context_tokens or 0), NORMAL_CONTEXT_CAP)
        settings.context_tokens = min(int(settings.context_tokens or 0), obergrenze)
    else:
        settings.quota = None
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings

#: Dieselbe Uebersicht wie `/help` im Terminal, nur als Markdown.
HELP_MARKDOWN = """### Befehle

- `/location <ort>` — Ortsfilter fuer diese Sitzung (leer = aufheben)
- `/model <name>` — Modell wechseln, z. B. `mistral/mistral-large-latest`
- `/max <frage>` — im Pro-Modus mit voller Mannschaft recherchieren
- `/image <pfad>` — Bild ansehen lassen und damit recherchieren (Datei oder Ordner)
- `/export html|md|csv` — die letzten Recherchen herunterladen
- `/history` — fruehere Recherchen
- `/notes` — Merkzettel (`/notes delete <nr>` loescht eine Notiz, `/notes clear` alle)
- `/clear` — Gespraechsverlauf verwerfen
- `/memory` — zeigen, was im Langzeitspeicher liegt
- `/uploads` — zeigen, was du hochgeladen hast
- `/uploads clear` — alle hochgeladenen Dateien loeschen
- `/forget` — den Langzeitspeicher leeren
- `/help` — diese Uebersicht

Dauerhaft aendern lassen sich Modell, Suche und Ort oben unter **Einstellungen**."""


class ChatSession:
    """Haelt den Agenten und serialisiert die Anfragen eines Kontos."""

    #: So lange wartet eine Rueckfrage auf eine Antwort. Laenger nicht: der
    #: Agent haelt derweil die Sitzung besetzt, und wer den Tab zumacht, soll
    #: sie nicht dauerhaft blockieren.
    ANSWER_TIMEOUT = 180.0

    def __init__(self, account: Account | None = None, profile: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._agent: Any = None
        self._settings: Settings | None = None
        self.account = account
        self.profile = profile
        self.runs = RunBook()
        #: Antworten auf Rueckfragen. Nur eine Anfrage laeuft gleichzeitig,
        #: deshalb genuegt eine Schlange fuer die ganze Sitzung.
        self._answers: queue.Queue[str] = queue.Queue()
        #: Eine Einstellung wurde im Gespraech geaendert -- nach dem Durchlauf
        #: wird der Agent neu gebaut.
        self._reload_after = False
        #: Der Chat, in den ein neu gebauter Agent zurueckkehren soll. Leer
        #: heisst: neuer Chat.
        self._carry_over = ""
        #: Hatte der abgebaute Agent schon fremden Text gelesen? (seit 9.5.16)
        self._carry_untrusted = False

    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = (
                _profile_settings(self.profile, self.plan, self.account)
                if self.profile is not None
                else get_settings()
            )
        return self._settings

    @property
    def plan(self) -> str:
        # Ohne Konto (eigener Rechner) hat man vollen Zugriff -- wie Ultra.
        return self.account.plan if self.account is not None else "ultra"

    @property
    def pro(self) -> bool:
        """Erhöhte Stufe -- Pro ODER Ultra (Nutzungsanzeige, Add-ons, Pro-Modus)."""
        return self.plan in ("pro", "ultra")

    @property
    def ultra(self) -> bool:
        """Die Vollstufe: alle Netz-Features und das Abschalten der Leitplanken."""
        return self.plan == "ultra"

    def agent(self) -> Any:
        from aquaticy.agent import Agent

        if self._agent is None:
            settings = self.settings()
            cache = Cache(settings.db_path, settings.cache_ttl_hours)
            self._agent = Agent(settings, cache=cache)
            # Ein neu gebauter Agent faengt sonst einen neuen Chat an -- und
            # das Modell zu wechseln haette das laufende Gespraech mitten
            # entzweigeschnitten: die naechste Frage stuende als eigener
            # Eintrag in der Leiste, und der Agent wuesste nichts mehr von
            # dem, was vorher besprochen wurde.
            if self._carry_over:
                weiter, self._carry_over = self._carry_over, ""
                vorher_fremd, self._carry_untrusted = self._carry_untrusted, False
                with contextlib.suppress(Exception):
                    entries = cache.chat_history(weiter)
                    _resume(self._agent, weiter, entries,
                            vorher_fremd or _chat_untrusted(entries))
        return self._agent

    def reset(self) -> None:
        """Beginnt einen neuen Chat. Der Merkzettel bleibt."""
        with self._lock:
            self.agent().clear()

    def chat_id(self) -> str:
        """Kennung des Chats, in dem gerade geschrieben wird."""
        return str(getattr(self.agent(), "session_id", ""))

    def open_chat(self, session_id: str) -> dict[str, Any]:
        """Oeffnet einen frueheren Chat wieder -- samt Verlauf.

        Der Verlauf geht an den Agenten zurueck, damit Nachfragen wie "und
        davon nur die guenstigen" auch nach Tagen noch funktionieren.
        """
        settings = self.settings()
        cache = Cache(settings.db_path, settings.cache_ttl_hours)
        entries = cache.chat_history(session_id)
        if not entries:
            return {"turns": [], "title": "", "note": "Diesen Chat gibt es nicht mehr."}
        with self._lock:
            _resume(self.agent(), session_id, entries, _chat_untrusted(entries))
        return {
            "session_id": session_id,
            "title": entries[0].question,
            "turns": [
                {
                    "question": entry.question,
                    "answer": entry.answer,
                    "products": entry.meta.get("products", []),
                    "visuals": [
                        {**v, "captured_text": webview.iso_moment_text(v.get("captured_at"))}
                        if isinstance(v, dict) and v.get("captured_at") else v
                        for v in entry.meta.get("visuals", []) or []
                    ],
                }
                for entry in entries
            ],
        }

    def reload(self, workshop: bool = False) -> None:
        """Nach dem Speichern neuer Einstellungen alles neu aufbauen.

        Die Werkstatt gehoert dazu, wenn sich an ihr etwas geaendert hat: haette
        jemand ihre Grenzen geaendert, arbeitete die laufende sonst noch mit den
        alten weiter. *workshop* erzwingt den Neuaufbau (Add-ons: die Werkstatt
        muss ihre Datentraeger loslassen oder neu einhaengen).

        Der Chat bleibt derselbe. Wer waehrend eines Gespraechs das Modell
        wechselt, will ein anderes Modell -- nicht ein anderes Gespraech.
        """
        with self._lock:
            old_settings = self._settings
            if self._agent is not None:
                self._carry_over = str(getattr(self._agent, "session_id", ""))
                # Was der alte Agent schon gelesen hat, gilt fuer den neuen
                # weiter (seit 9.5.16 -- vorher ging es beim Neuaufbau verloren).
                self._carry_untrusted = bool(getattr(
                    getattr(self._agent, "toolbox", None), "untrusted_seen", False))
                with contextlib.suppress(Exception):
                    self._agent.close()
            self._agent = None
            self._settings = None
            if self.profile is None:
                reset_settings_cache()
            # Die Werkstatt nur neu aufbauen, wenn sich an IHR etwas geaendert
            # hat (seit 9.5.16). Bis dahin loeschte jede gespeicherte
            # Einstellung -- schon ein anderes Modell in der Auswahl -- die
            # laufende Werkstatt samt allem, was unter /work lag.
            with contextlib.suppress(Exception):
                from aquaticy.sandbox import forget_shared

                if (workshop or old_settings is None
                        or workshop_changed(old_settings, self.settings())):
                    forget_shared(old_settings)
            # Frisch gelesen wird erst beim naechsten Gebrauch -- wie bisher.
            self._settings = None

    def _settings_dirty(self) -> None:
        """Merkt vor, dass der Agent neu gebaut werden muss.

        Sofort neu bauen geht nicht: der laufende Durchlauf benutzt ihn
        gerade. Also erst, wenn er fertig ist.
        """
        self._reload_after = True

    def stop(self) -> bool:
        """Bricht den laufenden Durchlauf ab. `False` = es lief gerade keiner.

        Gelesen wird hier absichtlich OHNE die Sperre: `agent()` wuerde auf
        das Ende des laufenden Durchlaufs warten, und ein Abbruch, der auf
        das Ende wartet, waere keiner.

        Eine offene Rueckfrage wird gleich mit geschlossen -- sonst haengt der
        Durchlauf noch bis zum Zeitlimit an ihr fest, obwohl er enden soll.
        """
        agent = self._agent
        if agent is None or not self.busy():
            return False
        agent.cancel()
        self.answer("")
        return True

    def busy(self) -> bool:
        """Laeuft gerade eine Anfrage?"""
        if self._lock.acquire(blocking=False):
            self._lock.release()
            return False
        return True

    def ask(
        self,
        message: str,
        emit: Any,
        attachments: list[dict[str, Any]] | None = None,
        mode: str = "",
        structured: bool | None = None,
        recheck: bool | None = None,
        effort: str = "",
        online: bool | None = None,
        sandbox: bool | None = None,
        agents: int | None = None,
        visual_sources: bool | None = None,
    ) -> Any:
        """Fuehrt eine Anfrage aus und meldet jeden Zwischenschritt an *emit*.

        Das abschliessende "done" kommt vom Agenten selbst -- hier noch eines
        zu senden wuerde die Oberflaeche zweimal abschliessen lassen.
        """
        # Im Netzbetrieb sitzen mehrere Geraete an derselben Sitzung. Wer
        # wartet, soll das sehen und nicht vor einem stummen Fenster sitzen.
        reload_after = False
        if self.busy():
            emit(
                "waiting",
                {
                    "reason": (
                        "Es läuft noch eine früher gestellte Anfrage — ich bin "
                        "gleich da."
                    )
                },
            )
        try:
            with self._lock:
                self._drain_answers()
                agent = self.agent()
                previous_agent_limit = getattr(agent, "_agent_limit_override", None)
                try:
                    agent.on_event = lambda name, payload: emit(name, payload)
                    agent.toolbox.on_event = agent.on_event
                    # Aendert Aquaticy im Gespraech eine Einstellung, muss der Agent
                    # danach neu gebaut werden -- sonst arbeitet die naechste Frage
                    # noch mit den alten Werten weiter.
                    agent.toolbox.on_settings_changed = self._settings_dirty
                    agent.set_ask_handler(self._ask_browser)
                    agent._agent_limit_override = agents
                    if message.startswith("/image"):
                        message = self._image_question(agent, message)
                    elif attachments:
                        context = self.attachments_text(
                            agent, attachments, emit, workshop=self._workshop_wanted(
                                agent, mode, sandbox
                            )
                        )
                        message = f"{context}\n\n{message}" if context else message
                        # Ein Anhang ist fremder Text wie eine Webseite (seit
                        # 9.5.16): ein PDF aus dem Netz kann Anweisungen tragen.
                        if context and getattr(agent, "toolbox", None) is not None:
                            agent.toolbox.untrusted_seen = True
                    ask_options = {
                        "stream": True, "mode": mode, "structured": structured,
                        "recheck": recheck, "effort": effort, "online": online,
                        "sandbox": sandbox,
                    }
                    # Testadapter und ältere Erweiterungen kennen den neuen
                    # Schalter noch nicht. Der eingebaute Agent bekommt ihn
                    # ausdrücklich; fremde Agenten behalten ihre Signatur.
                    from aquaticy.agent import Agent as BuiltinAgent
                    if isinstance(agent, BuiltinAgent):
                        ask_options["visual_sources"] = visual_sources
                    return agent.ask(message, **ask_options)
                finally:
                    agent._agent_limit_override = previous_agent_limit
                    agent.on_event = None
                    agent.toolbox.on_event = None
                    agent.toolbox.on_settings_changed = None
                    if self._reload_after:
                        self._reload_after = False
                        reload_after = True
                    # Der Handler bleibt bestehen -- das Werkzeug soll auch in der
                    # naechsten Runde angeboten werden.
        finally:
            # `reload()` nimmt dieselbe Sperre. Es darf daher erst laufen, nachdem
            # der Turn sie freigegeben hat -- auch wenn der Agent mit einem Fehler
            # endet.
            if reload_after:
                self.reload()

    @staticmethod
    def _workshop_wanted(agent: Any, mode: str, sandbox: bool | None) -> bool:
        """Ob dieser Turn in der Werkstatt landet -- vor `agent.ask`.

        Die Anhaenge werden verarbeitet, bevor der Agent den Modus dieses
        Turns kennt. Wer stattdessen `agent.workshop_on` fragt, bekommt den
        Stand der VORIGEN Frage -- und legt die Datei in die falsche Welt
        oder gar nicht ab.
        """
        from aquaticy.agent import clean_mode

        an = getattr(agent, "sandbox", False) if sandbox is None else bool(sandbox)
        welcher = clean_mode(mode) if mode else getattr(agent, "mode", "normal")
        return bool(an) and welcher == "code"

    def _ask_browser(self, question: str, options: list[str]) -> str:
        """Wartet auf die Antwort aus dem Browser.

        Das "ask"-Ereignis ist schon raus, wenn wir hier ankommen -- die
        Oberflaeche zeigt die Frage also bereits an. Bleibt die Antwort aus,
        geben wir auf: das Modell trifft dann eine Annahme und macht weiter.
        Aufgeraeumt wird zu Beginn der Anfrage, nicht hier -- sonst koennte
        eine sehr schnelle Antwort dem eigenen Aufraeumen zum Opfer fallen.
        """
        try:
            return self._answers.get(timeout=self.ANSWER_TIMEOUT)
        except queue.Empty:
            return ""

    def _drain_answers(self) -> None:
        """Antworten aus einer frueheren Runde wegwerfen."""
        while True:
            try:
                self._answers.get_nowait()
            except queue.Empty:
                return

    def answer(self, text: str) -> bool:
        """Nimmt die Antwort aus dem Browser entgegen. `False` = niemand wartet."""
        if not self.busy():
            return False
        self._answers.put(text)
        return True

    def attachments_text(
        self,
        agent: Any,
        attachments: list[dict[str, Any]],
        emit: Any,
        *,
        workshop: bool = False,
    ) -> str:
        """Macht aus hochgeladenen Dateien Text, den das Modell lesen kann.

        Bilder gehen ans Vision-Modell, PDFs durch pypdf, Textdateien direkt.
        Eine Datei, die nicht lesbar ist, beendet nicht die ganze Anfrage --
        sie wird benannt und uebersprungen, wie eine unlesbare Webseite auch.
        """
        blocks: list[str] = []
        for item in attachments[:MAX_UPLOADS]:
            name = safe_name(str(item.get("name") or "datei"))
            try:
                data = base64.b64decode(str(item.get("data") or ""), validate=True)
            except (ValueError, binascii.Error):
                blocks.append(f"[Anhang {name}: konnte nicht gelesen werden]")
                continue
            if not data:
                blocks.append(f"[Anhang {name}: leer]")
                continue
            if len(data) > MAX_UPLOAD_BYTES:
                blocks.append(
                    f"[Anhang {name}: zu gross "
                    f"({len(data) // 1_000_000} MB, erlaubt sind "
                    f"{MAX_UPLOAD_BYTES // 1_000_000} MB)]"
                )
                continue
            emit("upload", {"name": name, "bytes": len(data)})
            block = self._one_attachment(agent, name, data)
            # Ist die Werkstatt an, landet die Datei zusaetzlich unveraendert
            # darin. Sonst koennte das Modell ueber ein Bild reden, es aber
            # nicht oeffnen -- und ein Zip oder eine CSV waere gar nicht erst
            # angekommen.
            gelegt = self._into_workshop(name, data) if workshop else ""
            if gelegt:
                block += f"\n[Liegt in der Werkstatt unter {gelegt}]"
            blocks.append(block)
        return "\n\n".join(blocks)

    def _into_workshop(self, name: str, data: bytes) -> str:
        """Legt einen Anhang in die Werkstatt.

        Returns: der Pfad drinnen, oder "" wenn das Hineinlegen nicht
        geklappt hat. Ein Fehlschlag hier darf die Anfrage nicht abbrechen:
        der Text der Datei steht ja trotzdem da.
        """
        from aquaticy.sandbox import MAX_FILE_BYTES

        if len(data) > MAX_FILE_BYTES:
            return ""
        # Die Werkstatt nimmt nur ASCII-Pfade; "Übung.txt" waere sonst raus.
        schlicht = "".join(
            zeichen if zeichen.isascii() and (zeichen.isalnum() or zeichen in "-_.") else "_"
            for zeichen in name
        ).strip("._") or "datei"
        from aquaticy import metering
        from aquaticy import sandbox as werkstatt

        try:
            # Serverarbeit zaehlt ins Kontingent -- auch mit eigenem Schluessel.
            metering.check_work(self.settings(), "datei")
            antwort = werkstatt.shared(self.settings()).put_bytes(f"eingang/{schlicht}", data)
            if isinstance(antwort, dict) and antwort.get("written"):
                metering.charge_work(self.settings(), "datei")
                return str(antwort["written"])
        except metering.QuotaExceeded:
            return ""
        except Exception:
            # Ohne Werkstatt geht die Datei trotzdem an das Modell -- aber
            # nicht still: das Protokoll sagt, warum sie dort nicht liegt.
            logging.getLogger("aquaticy.web").warning(
                "Anhang nicht in die Werkstatt gelegt", exc_info=True)
        return ""

    def _one_attachment(self, agent: Any, name: str, data: bytes) -> str:
        """Liest eine einzelne Datei aus -- je nach Art auf ihrem eigenen Weg."""
        suffix = Path(name).suffix.lower()
        # Eine angehaengte Datei haengt am Nachrichtentext und wird deshalb
        # beim Kuerzen als letztes angetastet. Umso wichtiger, dass sie von
        # vornherein nicht mehr Platz nimmt, als das Fenster hergibt.
        limit = min(MAX_FILE_CHARS, agent.blob_limit()) if hasattr(agent, "blob_limit") else (
            MAX_FILE_CHARS
        )

        if suffix in IMAGE_TYPES:
            try:
                path = self._store(name, data)
            except OSError as exc:
                return f"[Bild {name}: nicht abgelegt -- {exc}]"
            try:
                description = agent.describe_image(path)
            except Exception as exc:
                return f"[Bild {name}: konnte nicht angesehen werden -- {exc}]"
            return f"[Bild {name}] Darauf ist zu sehen:\n{description}"

        if suffix == ".pdf":
            from aquaticy.fetch import extract_pdf_text

            text, title = extract_pdf_text(data)
            if not text:
                return (
                    f"[PDF {name}: kein Text enthalten -- vermutlich ein Scan. "
                    "Gescannte Seiten kann Aquaticy AI nicht lesen.]"
                )
            head = f"[PDF {name}" + (f", Titel: {title}" if title else "") + "]"
            return f"{head}\n{text[:limit]}"

        try:
            text = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            return f"[Anhang {name}: kein Text und kein bekanntes Bildformat]"
        if not text:
            return f"[Anhang {name}: leer]"
        return f"[Datei {name}]\n{text[:limit]}"

    def _store(self, name: str, data: bytes) -> Path:
        """Legt eine hochgeladene Datei ab -- das Vision-Modell braucht einen Pfad."""
        from aquaticy.budget import ensure_room, forget

        folder = self.settings().data_dir / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        # Uploads zaehlen zum gemeinsamen 400-MB-Deckel (aquaticy/budget.py).
        ensure_room(self.settings().data_dir, len(data))
        target = folder / f"{int(time.time() * 1000)}-{name}"
        target.write_bytes(data)
        forget(self.settings().data_dir)
        _prune_uploads(folder)
        return target

    def _image_question(self, agent: Any, line: str) -> str:
        """Macht aus `/image <pfad>` eine Frage, die das Bild beschreibt."""
        from aquaticy.cli import IMAGE_SUFFIXES

        argument = line[len("/image") :].strip()
        if not argument:
            raise ValueError("Nutzung: /image pfad/zum/bild.jpg (oder ein Ordner)")
        target = Path(argument)
        if self.account is not None:
            # Mit Konten sitzt am Browser nicht der Betreiber. Bis 9.5.12 las
            # `/image` jede Datei, die der Server lesen kann -- auch die
            # hochgeladenen Bilder anderer Konten -- und schickte sie ans
            # Vision-Modell. Jetzt gilt nur der eigene Upload-Ordner.
            uploads = (self.settings().data_dir / "uploads").resolve()
            target = (uploads / target).resolve()
            if not target.is_relative_to(uploads):
                raise PermissionError(
                    "Im Browser geht /image nur mit deinen eigenen hochgeladenen "
                    "Bildern -- häng das Bild einfach an die Nachricht an."
                )
        else:
            target = target.expanduser()
        image = resolve_image(target)
        if image.suffix.lower() not in IMAGE_SUFFIXES:
            # Auch lokal: eine .env ist kein Bild und geht nicht ans Modell.
            raise ValueError(
                f"{image.name} ist kein Bild ({', '.join(IMAGE_SUFFIXES)})."
            )
        description = agent.describe_image(image)
        return (
            f"Auf dem Bild ({image.name}) ist Folgendes zu sehen:\n{description}\n\n"
            "Recherchiere dazu und sag mir, worum es sich handelt."
        )

    def command(self, line: str) -> dict[str, Any]:
        """Fuehrt einen Slash-Befehl aus -- dieselben wie im Terminal.

        `/image` gehoert nicht hierher: das recherchiert und laeuft deshalb
        ueber den normalen Chat-Weg mit Live-Anzeige.
        """
        from aquaticy.config import model_problem

        command, _, argument = line[1:].partition(" ")
        command = command.lower().strip()
        argument = argument.strip()

        with self._lock:
            if command == "help":
                return {"text": HELP_MARKDOWN}

            if command == "max":
                # Allein getippt ist es keine Recherche, sondern eine Frage
                # danach, was der Befehl tut. Mit Frage dahinter kommt er hier
                # gar nicht erst an -- der laeuft ueber den Chat-Weg.
                return {
                    "text": (
                        "**/max** stellt im **Pro-Modus** die volle Mannschaft auf: "
                        "alle Agenten, die beiden starken dazu und -- wenn "
                        "*Gegenprüfen* an ist -- die vier Prüfer.\n\n"
                        "Schreib die Frage dahinter:\n\n"
                        "`/max Welche Fahrradläden in Bremen reparieren Lastenräder?`\n\n"
                        "Ohne den Befehl entscheidet der Master selbst, wie viele "
                        "Agenten die Frage braucht."
                    )
                }

            if command == "location":
                self.agent().set_location(argument)
                return {
                    "text": f"Ortsfilter: **{argument}**" if argument
                    else "Ortsfilter aufgehoben.",
                    "reload": True,
                }

            if command == "model":
                settings = self.settings()
                if not argument:
                    return {"text": f"Aktuelles Modell: **{settings.model}**"}
                problem = model_problem(argument)
                if problem:
                    return {"text": f"{problem}\n\nWeiter mit `{settings.model}`."}
                self.agent().set_model(argument)
                return {"text": f"Modell: **{argument}**", "reload": True}

            if command == "clear":
                if self._agent is not None:
                    self._agent.clear()
                return {"text": "Verlauf verworfen.", "clear": True}

            if command == "memory":
                return {"text": self._memory_overview()}

            if command == "forget":
                return {"text": self._forget()}

            if command == "uploads":
                return {"text": self._uploads(argument)}

            if command == "notes":
                return {"text": self._notes(argument)}

            if command == "history":
                entries = self._cache().recent_history(limit=15)
                if not entries:
                    return {"text": "Noch keine Recherchen im Verlauf."}
                lines = "\n".join(f"- {entry.question}" for entry in entries)
                return {"text": f"### Frueher gefragt\n{lines}"}

            if command == "export":
                return self._export(argument or "html")

            if command in ("quit", "exit", "q"):
                return {"text": "Im Browser reicht es, das Fenster zu schliessen."}

        return {"text": f"Unbekannter Befehl `/{command}` — `/help` zeigt alle."}

    def _notes(self, argument: str) -> str:
        """``/notes`` zeigt den Merkzettel, ``/notes delete 3`` und ``/notes clear`` raeumen auf.

        Bis 9.5.15 ging Loeschen nur in der Kommandozeile -- im Browser stand
        eine Notiz fuer immer im Systemtext jeder Frage.
        """
        cache = self._cache()
        wort, _, rest = argument.partition(" ")
        wort = wort.lower()
        if wort in ("clear", "leeren", "alle"):
            return f"{cache.clear_notes()} Notizen gelöscht. Der Merkzettel ist leer."
        if wort in ("delete", "del", "löschen", "loeschen", "weg"):
            nummer = rest.strip().lstrip("#")
            if not nummer.isdigit():
                return ("Welche Notiz? Zum Beispiel `/notes delete 3` -- die Nummer steht "
                        "bei `/notes`.")
            return (f"Notiz {nummer} gelöscht." if cache.delete_note(int(nummer))
                    else f"Eine Notiz mit der Nummer {nummer} gibt es nicht.")
        notes = cache.list_notes()
        if not notes:
            return "Der Merkzettel ist leer. Sag im Chat einfach *merk dir …*"
        lines = "\n".join(f"- **{note.id}** · {note.text}" for note in notes)
        return (f"### Merkzettel\n{lines}\n\nLöschen: `/notes delete <Nummer>` · "
                "alles: `/notes clear`")

    def memory(self) -> Any:
        """Den Langzeitspeicher oeffnen.

        Nicht `_store` nennen: so heisst schon die Ablage fuer hochgeladene
        Dateien, und die haette diese Methode ueberschrieben.
        """
        from aquaticy.memory import Memory

        settings = self.settings()
        return Memory(settings.db_path, settings.data_dir, settings.memory_key)

    def _memory_overview(self) -> str:
        """Was liegt im Speicher, und wie voll ist er?"""
        from aquaticy.memory import human_size

        settings = self.settings()
        if not settings.memory_enabled:
            return (
                "Der Speicher ist ausgeschaltet. Unter **Einstellungen → Speicher** "
                "laesst er sich wieder einschalten."
            )
        store = self.memory()
        usage = store.usage()
        entries = store.all_entries(limit=30)
        head = (
            f"### Speicher\n{usage['entries']} Notizen · "
            f"{usage['used_mb']} von {usage['limit_mb']} MB belegt "
            f"({usage['percent']} %), davon {human_size(int(usage['uploads']))} Dateien."
        )
        if not entries:
            return f"{head}\n\nNoch nichts abgelegt."
        lines = "\n".join(
            f"- **{entry.topic or 'Notiz'}** — {entry.text}" for entry in entries
        )
        return f"{head}\n\n{lines}"

    def _forget(self) -> str:
        """Den Speicher leeren."""
        if not self.settings().memory_enabled:
            return "Der Speicher ist ausgeschaltet -- da ist nichts zu loeschen."
        count = self.memory().clear()
        if not count:
            return "Der Speicher war schon leer."
        return f"{count} Notizen geloescht. Hochgeladene Dateien bleiben -- `/uploads clear`."

    def _uploads(self, argument: str) -> str:
        """Hochgeladenes zeigen oder loeschen."""
        from aquaticy.memory import human_size

        store = self.memory()
        if argument.strip().lower() in ("clear", "loeschen", "löschen", "weg"):
            count, freed = store.clear_uploads()
            if not count:
                return "Es liegt nichts Hochgeladenes herum."
            word = "Datei" if count == 1 else "Dateien"
            return f"{count} {word} geloescht, {human_size(freed)} wieder frei."

        folder = self.settings().data_dir / "uploads"
        files = sorted(folder.iterdir()) if folder.is_dir() else []
        files = [item for item in files if item.is_file()]
        if not files:
            return "Es liegt nichts Hochgeladenes herum."
        lines = "\n".join(
            # Der Zeitstempel vorne im Dateinamen interessiert niemanden.
            f"- {item.name.split('-', 1)[-1]} ({human_size(item.stat().st_size)})"
            for item in files[-30:]
        )
        total = human_size(sum(item.stat().st_size for item in files))
        return (
            f"### Hochgeladen\n{len(files)} {'Datei' if len(files) == 1 else 'Dateien'}, "
            f"zusammen {total}.\n\n{lines}"
            "\n\nAlles loeschen: `/uploads clear`"
        )

    def _cache(self) -> Cache:
        settings = self.settings()
        return Cache(settings.db_path, settings.cache_ttl_hours)

    def _export(self, fmt: str) -> dict[str, Any]:
        """Exportiert die letzten Recherchen -- wie `aquaticy export`.

        Die Datei geht als Download an den Browser. Bis 9.5.12 landete sie im
        Arbeitsordner des Servers: wer von einem anderen Geraet kam, bekam
        nur einen Pfad, mit dem er nichts anfangen konnte -- und jedes Konto
        konnte dort beliebig viele Dateien anlegen. Nur ohne Konten (lokal,
        ein Nutzer) bleibt zusaetzlich eine Kopie in ``./exports`` liegen,
        wie im Terminal.
        """
        import tempfile

        from aquaticy.export import Turn, export

        entries = self._cache().recent_history(limit=5)
        if not entries:
            return {"text": "Noch nichts zu exportieren — stell erst eine Frage."}
        turns = [
            Turn(
                question=entry.question,
                answer=entry.answer,
                sources=entry.meta.get("sources", []),
                searches=entry.meta.get("searches", []),
                skipped=entry.meta.get("skipped", {}),
            )
            for entry in entries
        ]
        try:
            with tempfile.TemporaryDirectory(prefix="aquaticy-export-") as ordner:
                path = export(turns, fmt, directory=Path(ordner))
                inhalt = path.read_text(encoding="utf-8")
                name = path.name
        except ValueError as exc:
            return {"text": str(exc)}
        except OSError as exc:
            return {"text": f"Der Export ist fehlgeschlagen: {type(exc).__name__}"}
        mime = {
            "html": "text/html;charset=utf-8",
            "md": "text/markdown;charset=utf-8",
            "csv": "text/csv;charset=utf-8",
        }[path.suffix.lstrip(".")]
        text = f"Export fertig: **{name}** — der Download startet."
        if self.account is None:
            with contextlib.suppress(OSError):
                ziel = Path.cwd() / name
                ziel.write_text(inhalt, encoding="utf-8")
                text += f"\n\nEine Kopie liegt in `{ziel}`."
        return {"text": text, "download": {"name": name, "mime": mime, "content": inhalt}}


def resolve_image(target: Path) -> Path:
    """Datei oder Ordner zu genau einem Bild aufloesen.

    Im Terminal fragt aquaticy bei mehreren Bildern nach; hier nimmt er das
    neueste und sagt es dazu -- eine Rueckfrage mitten im Stream waere
    umstaendlicher als ein zweiter `/image`-Aufruf mit genauem Pfad.
    """
    from aquaticy.cli import IMAGE_SUFFIXES, _images_in

    if target.is_file():
        return target
    if target.is_dir():
        images = _images_in(target)
        if not images:
            raise FileNotFoundError(
                f"Keine Bilder in {target}. Gesucht wurde nach: {', '.join(IMAGE_SUFFIXES)}"
            )
        return images[0]
    raise FileNotFoundError(f"Nicht gefunden: {target}")


DEFAULT_SESSION = ChatSession()
_REQUEST = threading.local()


class SessionRegistry:
    """Eine unabhaengige ChatSession je Konto."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    def get(self, account: Account) -> ChatSession:
        with self._lock:
            session = self._sessions.get(account.id)
            if session is None:
                if AUTH is None:  # pragma: no cover - nur bei kaputtem Serverstart
                    return DEFAULT_SESSION
                session = ChatSession(account, AUTH.profile_dir(account.id))
                self._sessions[account.id] = session
            return session


SESSIONS = SessionRegistry()
USER_SCHEDULERS: dict[str, Any] = {}
#: Zwei Anmeldungen desselben Kontos koennen zeitgleich hereinkommen. Ohne
#: Schloss startet dann jede ihren eigenen Taktgeber -- und jeder Auftrag
#: liefe doppelt.
_SCHEDULER_LOCK = threading.Lock()


class SessionProxy:
    """Haelt den bestehenden Code lesbar und waehlt pro Anfrage das Konto."""

    @staticmethod
    def current() -> ChatSession:
        return getattr(_REQUEST, "session", DEFAULT_SESSION)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.current(), name)


SESSION = SessionProxy()


def current_runs() -> RunBook:
    session = SESSION.current() if isinstance(SESSION, SessionProxy) else SESSION
    return session.runs if session.account is not None else RUNS


def start_user_scheduler(account: Account) -> None:
    """Startet den Taktgeber genau einmal fuer das Konto."""
    from aquaticy.jobs import Scheduler

    with _SCHEDULER_LOCK:
        if account.id in USER_SCHEDULERS:
            return
        # Das Kontingent steckt in den Einstellungen des Kontos (settings.quota).
        scheduler = Scheduler(SESSIONS.get(account).settings)
        scheduler.start()
        USER_SCHEDULERS[account.id] = scheduler


def ui_state() -> Any:
    """Der Zustand der Oberflaeche -- er liegt beim Server, nicht im Browser."""
    from aquaticy.uistate import UIState

    return UIState(SESSION.settings().db_path)

#: Der Taktgeber fuer die Auftraege. Wird beim Start gesetzt; im Test laeuft
#: kein Server und damit auch keiner.
SCHEDULER: Any = None


#: Die Modellfelder mit ihrem Namen fuer "Eigene Modelle" (9.5.17).
OWN_MODEL_FIELDS = (
    ("AQUATICY_MODEL", "model", "Hauptmodell"),
    ("AQUATICY_VISION_MODEL", "vision_model", "Bilder"),
    ("AQUATICY_SUBAGENT_MODEL", "subagent_model", "Helfer"),
    ("AQUATICY_CODE_MODEL", "code_model", "Code"),
)


def own_models(settings: Any) -> list[dict[str, str]]:
    """Die Modelle, die ein Konto selbst eingetragen hat -- nicht die des Betreibers.

    Grundlage fuer den Abschnitt "Eigene Modelle": er zeigt nur, was der
    Nutzer oben selbst hinzugefuegt hat. Ohne Konten gibt es keinen
    Unterschied zwischen "eigen" und "gestellt" -- dann ist die Liste leer.
    """
    if getattr(SESSION, "account", None) is None:
        return []
    try:
        basis = get_settings()
    except Exception:  # pragma: no cover - ohne Grundeinstellung nichts vergleichen
        return []
    eigene = []
    for feld, attr, name in OWN_MODEL_FIELDS:
        wert = str(getattr(settings, attr, "") or "")
        if wert and wert != str(getattr(basis, attr, "") or ""):
            eigene.append({"field": feld, "label": name, "model": wert})
    return eigene


def current_values() -> dict[str, str]:
    """Aktuelle Einstellungen als Formularwerte."""
    settings = SESSION.settings()
    return {
        "AQUATICY_MODEL": settings.model,
        "AQUATICY_VISION_MODEL": settings.vision_model,
        "AQUATICY_SUBAGENT_MODEL": settings.subagent_model,
        "AQUATICY_CODE_MODEL": settings.code_model,
        "AQUATICY_API_BASE": settings.api_base,
        "AQUATICY_SEARCH_BACKEND": settings.search_backend,
        "AQUATICY_SEARCH_ENGINES": settings.search_engines,
        "AQUATICY_SEARCH_VARIANTS": str(settings.search_variants),
        "AQUATICY_SEARXNG_URL": settings.searxng_url,
        "AQUATICY_LOCATION": settings.location,
        "AQUATICY_LANG": settings.lang,
        "AQUATICY_COUNTRY": settings.country,
        "AQUATICY_SUBAGENTS_AUTO": "true" if settings.subagents_auto else "false",
        "AQUATICY_MAX_SUBAGENTS": str(settings.max_subagents),
        "AQUATICY_SUBAGENT_BUDGET": str(settings.subagent_budget),
        "AQUATICY_SUBAGENT_PARALLEL": str(settings.subagent_parallel),
        # Leer heisst: was der Anbieter im Freikontingent vertraegt. Der
        # eingetragene Wert gilt dagegen fuer alle Anbieter -- deshalb steht
        # er hier so, wie er in der .env steht, und nicht als Vorgabewert.
        "AQUATICY_RPM": str(settings.rpm) if settings.rpm else "",
        "AQUATICY_PARALLEL_CALLS": str(settings.parallel_calls) if settings.parallel_calls else "",
        "AQUATICY_MAX_TOOL_CALLS": str(settings.max_tool_calls),
        "AQUATICY_CONTEXT_TOKENS": str(settings.context_tokens),
        "AQUATICY_PLANNER_TIMEOUT": str(int(settings.planner_timeout)),
        "AQUATICY_ENABLE_PLAYWRIGHT": "true" if settings.enable_playwright else "false",
        "AQUATICY_HA_URL": settings.ha_url,
        "AQUATICY_HA_CONTROL": "true" if settings.ha_control else "false",
        "AQUATICY_GOOGLE": "true" if settings.google_enabled else "false",
        "AQUATICY_GOOGLE_WRITE": "true" if settings.google_write else "false",
        "AQUATICY_STORAGE_URL": settings.storage_url,
        "AQUATICY_STORAGE_ACCESS": settings.storage_access,
        "AQUATICY_LAN_ENABLED": "true" if settings.lan_enabled else "false",
        "AQUATICY_LAN_SUBNET": settings.lan_subnet,
        "AQUATICY_MEMORY": "true" if settings.memory_enabled else "false",
        "AQUATICY_VM_SIZE": settings.vm_size,
        "AQUATICY_VM_USER_MODE": "true" if settings.vm_user_mode else "false",
        "AQUATICY_LEGAL_GUARD": "true" if settings.legal_guard else "false",
        "AQUATICY_AUTO_MODEL": "true" if settings.auto_model else "false",
    }


def fix_model_id(model: str) -> str:
    """Ergaenzt ein fehlendes Anbieter-Praefix, wenn es eindeutig ist.

    Wer bei NVIDIA "nvidia/nemotron-3-ultra-550b-a55b" von der Webseite
    kopiert, traegt genau das ein -- und LiteLLM findet dazu keinen Anbieter.
    Statt den Nutzer mit "LLM Provider NOT provided" stehenzulassen, setzen
    wir das Praefix davor, das ohnehin gemeint war.
    """
    model = (model or "").strip()
    if not model or resolve_model(model):
        return model
    return suggest_model(model) or model


def google_client(settings: Settings) -> Any:
    """Der Google-Zugriff mit den aktuellen Einstellungen."""
    from aquaticy.google import Google, TokenStore

    return Google(
        settings.google_client_id,
        settings.google_client_secret,
        TokenStore(settings.data_dir, settings.memory_key),
    )


def google_state(settings: Settings) -> dict[str, Any]:
    """Was die Oberflaeche ueber die Google-Anbindung wissen darf.

    Token und Secret bleiben hier -- der Browser erfaehrt nur, ob etwas
    hinterlegt ist und welches Konto verbunden wurde.
    """
    client = google_client(settings)
    try:
        connected = client.connected()
        account = client.account()
    finally:
        client.close()
    return {
        "enabled": settings.google_enabled,
        "write": settings.google_write,
        "has_id": bool(settings.google_client_id),
        "has_secret": bool(settings.google_client_secret),
        "connected": connected,
        "account": account,
    }


#: Offene Google-Anmeldungen (seit 9.5.15): state -> (Konto, Ablauf). Jeder
#: state gilt einmal, zehn Minuten lang, und nur fuer das Konto, das die
#: Anmeldung begonnen hat. Ohne ihn konnte jemand einem angemeldeten Opfer
#: seinen eigenen Google-Code unterschieben (Login-CSRF): dann laese Aquaticy
#: im Konto des Opfers die Mails des Angreifers -- oder umgekehrt.
_GOOGLE_STATES: dict[str, tuple[str, float]] = {}
_GOOGLE_STATES_LOCK = threading.Lock()
GOOGLE_STATE_SECONDS = 600.0


def google_state_new(account_id: str) -> str:
    """Ein neuer, einmaliger state fuer genau dieses Konto."""
    import secrets as _secrets

    state = _secrets.token_urlsafe(32)
    jetzt = time.time()
    with _GOOGLE_STATES_LOCK:
        for alt, (_, bis) in list(_GOOGLE_STATES.items()):
            if bis < jetzt:
                _GOOGLE_STATES.pop(alt, None)
        _GOOGLE_STATES[state] = (account_id, jetzt + GOOGLE_STATE_SECONDS)
    return state


def google_state_take(state: str, account_id: str) -> bool:
    """Loest einen state ein -- genau einmal, rechtzeitig, vom selben Konto."""
    if not state:
        return False
    with _GOOGLE_STATES_LOCK:
        eintrag = _GOOGLE_STATES.pop(state, None)
    if eintrag is None:
        return False
    konto, bis = eintrag
    return bis >= time.time() and hmac.compare_digest(konto, account_id)


def google_redirect(host: str = "") -> str:
    """Wohin Google nach der Zustimmung zurueckschickt.

    Google erlaubt fuer Desktop-Anwendungen nur `localhost` und `127.0.0.1`.
    Sitzt der Browser auf einem anderen Geraet, laeuft die Weiterleitung ins
    Leere -- dann kopiert der Nutzer die Adresse aus der Adresszeile, der
    Code steht darin. Deshalb ist die Adresse hier fest und haengt NICHT vom
    aufrufenden Geraet ab: sie muss mit der in der Google Cloud Console
    eingetragenen uebereinstimmen, sonst lehnt Google ab.
    """
    port = host.rsplit(":", 1)[-1] if ":" in host else str(DEFAULT_PORT)
    if not port.isdigit():
        port = str(DEFAULT_PORT)
    return f"http://localhost:{port}/google"


def check_values(values: dict[str, str]) -> str:
    """Prueft die Formularwerte. Returns: die Beanstandung, oder "".

    Der Browser kann alles schicken -- ein Zahlenfeld mit Buchstaben darin,
    eine Auswahl mit einem Wort, das es nicht gibt, eine Adresse von einer
    Laenge, die niemand tippt. Hier ist die Stelle, an der das auffaellt.
    """
    for key, wert in values.items():
        if len(wert) > MAX_VALUE_CHARS:
            return f"{key}: zu lang ({len(wert)} Zeichen, erlaubt sind {MAX_VALUE_CHARS})."
        if key in NUMBERS and wert:
            von, bis = NUMBERS[key]
            try:
                zahl = int(wert)
            except ValueError:
                return f"{key}: '{wert}' ist keine Zahl."
            if not von <= zahl <= bis:
                return f"{key}: {zahl} liegt ausserhalb von {von} bis {bis}."
        if key in CHOICES and wert and wert not in CHOICES[key]:
            erlaubt = ", ".join(CHOICES[key])
            return f"{key}: '{wert}' gibt es nicht. Erlaubt sind: {erlaubt}."
    return ""


def save_values(payload: dict[str, Any]) -> Path:
    """Schreibt die Formularwerte in die `.env` und laedt neu.

    Raises:
        ValueError: Wenn ein Wert nicht durch die Pruefung kommt. Dann wird
            nichts geschrieben -- halb gespeicherte Einstellungen waeren
            schlimmer als gar keine.
    """
    session = SESSION.current() if isinstance(SESSION, SessionProxy) else SESSION
    pro_integration = any(
        key in payload
        for key in (
            "AQUATICY_HA_URL",
            "AQUATICY_HA_CONTROL",
            HA_TOKEN_FIELD,
            "AQUATICY_LAN_ENABLED",
            "AQUATICY_LAN_SUBNET",
            "AQUATICY_STORAGE_URL",
            "AQUATICY_STORAGE_ACCESS",
        )
    )
    plus_workshop = str(payload.get("AQUATICY_VM_SIZE", "")).strip() == "plus"
    if not session.ultra and (pro_integration or plus_workshop):
        raise ValueError(
            "Heimnetz-Suche, Home Assistant, Lagerverwaltung und die große Werkstatt "
            "gibt es nur mit einem Ultra-Konto."
        )
    if not session.ultra:
        base = get_settings()
        for key, erlaubt in (("AQUATICY_API_BASE", base.api_base),
                             ("AQUATICY_SEARXNG_URL", base.searxng_url)):
            if key in payload and str(payload.get(key) or "").strip() != (erlaubt or ""):
                raise ValueError(
                    "Eigene Adressen für Modell oder Suche gibt es nur mit einem Ultra-Konto — "
                    "sonst legt sie der Betreiber fest."
                )
    guard_off = "AQUATICY_LEGAL_GUARD" in payload and not guard_on(
        str(payload.get("AQUATICY_LEGAL_GUARD", ""))
    )
    if guard_off and not session.ultra:
        raise ValueError(
            "Die Rechts-Leitplanken (Grundgesetz und BGB) lassen sich nur mit einem "
            "Ultra-Konto abschalten."
        )
    user_mode_on = "AQUATICY_VM_USER_MODE" in payload and str(
        payload.get("AQUATICY_VM_USER_MODE", "")
    ).strip().lower() in {"1", "true", "yes", "on", "ja"}
    if user_mode_on and not session.ultra:
        raise ValueError(
            "Der User mode (Werkstatt mit Desktop und Internet) braucht ein Ultra-Konto."
        )
    values = {
        key: str(payload.get(key, "")).strip() for key in SETTING_KEYS if key in payload
    }
    if "AQUATICY_LEGAL_GUARD" in values:
        # Eindeutig speichern -- ein "an" in der Datei liest sonst jeder
        # andere Leser anders.
        values["AQUATICY_LEGAL_GUARD"] = "false" if guard_off else "true"
    beanstandung = check_values(values)
    if beanstandung:
        raise ValueError(beanstandung)
    for key in (
        "AQUATICY_MODEL",
        "AQUATICY_VISION_MODEL",
        "AQUATICY_SUBAGENT_MODEL",
        "AQUATICY_CODE_MODEL",
    ):
        if values.get(key):
            values[key] = fix_model_id(values[key])
    ha_token = str(payload.get(HA_TOKEN_FIELD, "")).strip()
    if ha_token:
        values["HA_TOKEN"] = ha_token
    # Eigene API-Schluessel eines Kontos gehen in seinen Schluesselbund
    # (aquaticy/keyvault.py) -- nie in eine .env, nie in die Umgebung.
    schluessel: dict[str, str] = {}
    api_key = str(payload.get(API_KEY_FIELD, "")).strip()
    if api_key:
        # Nur setzen, wenn wirklich etwas eingetippt wurde -- ein leeres Feld
        # bedeutet "unveraendert", nicht "loeschen".
        key_name = key_slot_for(values.get("AQUATICY_MODEL", "") or session.settings().model)
        if key_name:
            schluessel[key_name] = api_key
    google_id = str(payload.get(GOOGLE_ID_FIELD, "")).strip()
    if google_id:
        values["GOOGLE_CLIENT_ID"] = google_id
    google_secret = str(payload.get(GOOGLE_SECRET_FIELD, "")).strip()
    if google_secret:
        values["GOOGLE_CLIENT_SECRET"] = google_secret
    search_key = str(payload.get(SEARCH_KEY_FIELD, "")).strip()
    if search_key:
        backend = values.get("AQUATICY_SEARCH_BACKEND", "") or session.settings().search_backend
        backend_key_name = SEARCH_BACKEND_KEYS.get(backend, "")
        if backend_key_name:
            schluessel[backend_key_name] = search_key
    if schluessel:
        from aquaticy.keyvault import VaultError, check_key

        try:
            schluessel = {name: check_key(name, wert) for name, wert in schluessel.items()}
        except VaultError as exc:
            raise ValueError(str(exc)) from exc
        if session.profile is None:
            # Lokal ohne Konten: der Betreiber sitzt selbst davor, seine
            # Schluessel stehen wie immer in seiner .env.
            values.update(schluessel)
    target = (
        session.settings().env_path
        if session.profile is not None
        else find_env_file() or DEFAULT_ENV_PATH
    )
    written = write_env_file(values, target)
    if schluessel and session.profile is not None:
        tresor = account_vault(session.profile, session.account)
        for name, wert in schluessel.items():
            tresor.set(name, wert)
    # In einem laufenden Prozess gewinnen bereits gesetzte Umgebungsvariablen
    # ueber die .env. Ohne override laege die neue Einstellung zwar in der
    # Datei, waere aber erst nach einem Neustart aktiv -- die Oberflaeche
    # meldet aber "sofort aktiv", und das soll auch stimmen.
    if session.profile is None:
        load_env(written, override=True)
    else:
        from aquaticy.memory import secure_file

        secure_file(written)
    session.reload()
    return written


def scrub_payload(wert: Any, geheim: list[str]) -> Any:
    """Entfernt Schluessel aus allen Texten eines Ereignisses (auch verschachtelt)."""
    from aquaticy.keyvault import scrub

    if isinstance(wert, str):
        return scrub(wert, geheim)
    if isinstance(wert, dict):
        return {k: scrub_payload(v, geheim) for k, v in wert.items()}
    if isinstance(wert, list):
        return [scrub_payload(v, geheim) for v in wert]
    return wert


def keys_view(session: Any) -> dict[str, Any]:
    """Der Bereich "API-Schluessel": was hinterlegt ist -- nie der Schluessel selbst.

    Fuer ein Konto kommt alles aus seinem Schluesselbund. Lokal ohne Konten
    sitzt der Betreiber selbst davor; seine Schluessel stehen in der .env.
    """
    from aquaticy.keyvault import SLOTS, masked, summary

    eigene: dict[str, dict[str, Any]] = {}
    if session.profile is not None:
        for eintrag in account_vault(session.profile, session.account).public():
            eigene[eintrag["name"]] = eintrag
    else:
        for slot in SLOTS:
            wert = os.environ.get(slot.name, "").strip()
            if wert:
                eigene[slot.name] = {"name": slot.name, "hint": masked(wert), "added_at": 0.0}
    plaetze = []
    for slot in SLOTS:
        eintrag = eigene.get(slot.name)
        gestellt = session.profile is not None and bool(os.environ.get(slot.name, "").strip())
        if eintrag:
            seit = webview.moment_text(eintrag["added_at"]) if eintrag["added_at"] else ""
            status = f"Hinterlegt {eintrag['hint']}" + (f" · seit {seit}" if seit else "")
        else:
            status = "Nicht hinterlegt" + (" — der Betreiber stellt einen" if gestellt else "")
        plaetze.append({
            "name": slot.name, "label": slot.label, "art": slot.art, "note": slot.note,
            "placeholder": (slot.form + "  " if slot.form else "") + "Schlüssel einfügen",
            "set": bool(eintrag), "status": status,
        })
    return {
        "slots": plaetze,
        "count": len(eigene),
        "summary": summary(set(eigene)),
        "account": session.profile is not None,
        "privacy": (
            "Deine Schlüssel gehören nur zu deinem Konto: verschlüsselt gespeichert, nie "
            "im Browser angezeigt — auch dir nicht, nur die letzten vier Zeichen — und für "
            "kein anderes Konto sichtbar oder benutzbar. Sie gehen nur an den Anbieter selbst"
            + (" oder an eine Modell-Adresse, die du selbst eingetragen hast."
               if getattr(session, "ultra", False) else ".")
            if session.profile is not None else
            "Lokal ohne Konten stehen die Schlüssel in deiner .env auf diesem Rechner."
        ),
        "quota_note": (
            "Modelle mit deinem eigenen Schlüssel zählen nicht in dein Limit — dort zählt "
            "nur, was auf dem Server passiert: Werkstatt, Seitenabrufe, Suchen."
            if getattr(session.settings(), "quota", None) is not None else ""
        ),
    }


def keys_action(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Schluessel hinzufuegen, entfernen oder testen -- nur im eigenen Konto.

    Returns: (Antwort, HTTP-Status).
    """
    from aquaticy.keyvault import SLOT_BY_NAME, VaultError, check_key

    session = SESSION.current() if isinstance(SESSION, SessionProxy) else SESSION
    aktion = str(payload.get("action") or "").strip().lower()
    name = str(payload.get("name") or "").strip()
    if name not in SLOT_BY_NAME:
        return {"ok": False, "error": "Diesen Schlüssel gibt es hier nicht."}, 400
    try:
        if aktion == "set":
            wert = check_key(name, str(payload.get("value") or ""))
            if session.profile is not None:
                account_vault(session.profile, session.account).set(name, wert)
            else:
                ziel = write_env_file({name: wert}, find_env_file() or DEFAULT_ENV_PATH)
                load_env(ziel, override=True)
            hinweis = f"{SLOT_BY_NAME[name].label}: gespeichert."
        elif aktion == "remove":
            if session.profile is not None:
                weg = account_vault(session.profile, session.account).remove(name)
            else:
                weg = bool(os.environ.pop(name, "").strip())
                write_env_file({name: ""}, find_env_file() or DEFAULT_ENV_PATH)
            hinweis = f"{SLOT_BY_NAME[name].label}: entfernt." if weg else "Da war keiner."
        elif aktion == "test":
            wer = getattr(session.account, "id", "") or "lokal"
            if not KEY_TEST_LIMIT.allow(wer):
                return {"ok": False, "error": (
                    "Das waren viele Tests in kurzer Zeit — bitte in einer Minute noch einmal."
                )}, 429
            ok, meldung = _test_key(session, name, str(payload.get("value") or "").strip())
            view = keys_view(session)
            return {"ok": ok, "message": meldung, **view}, 200
        else:
            return {"ok": False, "error": "Unbekannte Aktion."}, 400
    except VaultError as exc:
        return {"ok": False, "error": str(exc)}, 400
    session.reload()
    forget_strong_models(session.settings().data_dir)
    return {"ok": True, "message": hinweis, **keys_view(session)}, 200


def _test_key(session: Any, name: str, getippt: str) -> tuple[bool, str]:
    """Prueft einen Schluessel beim Anbieter -- den getippten oder den hinterlegten.

    Eigene Schluessel gehen dabei nur an den Anbieter selbst, nie an eine
    Adresse des Betreibers. Die Antwort wird von Schluesseln gesaeubert.
    """
    from aquaticy.config import PROVIDER_KEYS, provider_of
    from aquaticy.keyvault import SLOT_BY_NAME, check_key, scrub
    from aquaticy.probe import check_llm, check_search
    from aquaticy.system import FAST_MODELS

    settings = session.settings()
    slot = SLOT_BY_NAME[name]
    fehlt = "Es ist noch kein eigener Schlüssel hinterlegt — füg ihn ein und teste dann."
    if getippt:
        wert = check_key(name, getippt)
    elif session.profile is not None:
        # Getestet wird nur der EIGENE -- nie einer, den der Betreiber stellt.
        if name not in settings.own_key_names:
            return False, fehlt
        wert = (settings.search_keys if slot.art == "suche" else settings.api_keys).get(name, "")
    else:
        wert = os.environ.get(name, "").strip()
    if not wert:
        return False, fehlt
    if slot.art == "suche":
        dienst = "brave" if name == "BRAVE_API_KEY" else "tavily"
        ok, meldung = check_search(dienst, wert, "", "")
    else:
        anbieter = next((a for a, n in PROVIDER_KEYS.items() if n == name), "")
        adresse = ""
        if anbieter:
            modell = FAST_MODELS.get(anbieter, "")
        else:
            # Der allgemeine Platz gilt fuer das Hauptmodell -- aber nur, wenn es
            # im Betrieb auch damit liefe. Ein gestelltes Modell auf einem
            # Server des Betreibers bekaeme den Schluessel nie; ihn dann beim
            # Anbieter dieses Modells zu "testen", schickte ihn womoeglich an
            # den falschen Anbieter.
            modell = settings.model if provider_of(settings.model) not in PROVIDER_KEYS else ""
            weg, des_kontos = settings.route(modell) if modell else ("", False)
            if session.profile is not None and weg and not des_kontos:
                modell = ""
            adresse = weg if (des_kontos or session.profile is None) else ""
        if not modell:
            return False, ("Der allgemeine Schlüssel gilt für das Modell eines anderen Anbieters — "
                           "trag zuerst unter Hauptmodell dessen Modell-ID ein (zum Beispiel "
                           "groq/llama-3.3-70b-versatile), dann kann ich testen.")
        from aquaticy.pace import own_gate

        ok, meldung = check_llm(modell, wert, adresse,
                                pace_key=own_gate(wert) if session.profile is not None else "")
        meldung = f"{modell}: {meldung}"
    return ok, scrub(meldung, [wert, *settings.secrets()])


#: Was es im Add-on-Fenster zu tun gibt.
ADDON_ACTIONS = frozenset({
    "install", "uninstall", "enable", "disable", "token", "login", "login_done", "logout",
    "feeds", "rights",
})

#: Aktionen, bei denen die laufende Werkstatt neu aufgebaut werden muss --
#: sie haelt die Datentraeger der Add-ons fest.
_RESTARTS_WORKSHOP = frozenset({"uninstall", "enable", "disable", "logout"})


def addon_action(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Installieren, Schalten, Anmelden -- alles aus dem Add-on-Fenster.

    Returns: (Antwort, HTTP-Status).
    """
    from aquaticy import addons

    session = SESSION.current() if isinstance(SESSION, SessionProxy) else SESSION
    settings = session.settings()
    action = str(payload.get("action") or "").strip().lower()
    addon_id = str(payload.get("id") or "").strip().lower()
    if action not in ADDON_ACTIONS:
        return {"ok": False, "error": "Unbekannte Aktion."}, 400
    try:
        addon = addons.get(addon_id)
        if action in _RESTARTS_WORKSHOP and addon.programm and session.busy():
            return {"ok": False, "error": (
                "Gerade arbeitet Aquaticy noch — die Werkstatt muss dafür neu starten. "
                "Bitte gleich noch einmal."
            )}, 409
        hinweis = ""
        if action == "install":
            addons.install(settings, addon.id, session.ultra,
                           on_done=lambda: session.reload(workshop=True))
            if not addon.programm:
                session.reload(workshop=True)
            hinweis = "Wird installiert …" if addon.programm else "Installiert und eingeschaltet."
        elif action == "uninstall":
            if addon.programm:
                session.reload(workshop=True)          # die Werkstatt laesst den Datentraeger los
            addons.uninstall(session.settings(), addon.id)
            session.reload(workshop=True)
            hinweis = "Deinstalliert — Programm und Anmeldung sind gelöscht."
        elif action in ("enable", "disable"):
            addons.set_enabled(settings, addon.id, action == "enable", session.ultra)
            session.reload(workshop=True)
            hinweis = "Eingeschaltet." if action == "enable" else "Ausgeschaltet."
        elif action == "token":
            if addon.login != "token":
                raise addons.AddOnError(f"{addon.name} meldet sich nicht mit einem Token an.")
            ergebnis = addons.github_login(settings, str(payload.get("token") or ""))
            session.reload()  # GitHub laeuft nicht in der Werkstatt
            hinweis = f"Angemeldet als {ergebnis['who']}." + (
                f" {ergebnis['warning']}" if ergebnis["warning"] else "")
        elif action == "login":
            hinweis = _addon_login(session, addon)
        elif action == "login_done":
            addons.mark_login(settings, addon.id, "angemeldet")
            hinweis = "Gemerkt: angemeldet."
        elif action == "logout":
            if addon.programm:
                session.reload(workshop=True)
            addons.logout(session.settings(), addon.id)
            session.reload(workshop=True)
            hinweis = "Abgemeldet." + (
                " Entferne die Werkstatt auf dem Handy auch unter „Verknüpfte Geräte“."
                if addon.login == "qr" else "")
        elif action == "rights":
            # Wirkt sofort: Werkzeuge und Desktop lesen die Rechte bei jedem
            # Aufruf, der Systemtext wird vor der naechsten Frage erneuert.
            # Kein Neuaufbau -- die Werkstatt laeuft weiter.
            neu = addons.set_rights(settings, addon.id, payload.get("rechte"))
            hinweis = "Gespeichert: " + addons.rights_text(addon.id, neu) + "."
        else:  # feeds
            feeds = addons.set_feeds(settings, payload.get("feeds") or [])
            session.reload()  # Feeds laufen nicht in der Werkstatt
            hinweis = f"{len(feeds)} Feeds gespeichert."
    except addons.AddOnError as exc:
        return {"ok": False, "error": str(exc)}, 400
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500
    view = addons.public_view(session.settings(), session.ultra)
    return {"ok": True, "message": hinweis, **view}, 200


def _addon_login(session: Any, addon: Any) -> str:
    """Oeffnet das Programm in der Werkstatt -- anmelden tut sich der Nutzer selbst."""
    from aquaticy import addons
    from aquaticy import sandbox as werkstatt

    settings = session.settings()
    if addon.login != "qr":
        raise addons.AddOnError(f"{addon.name} braucht keine Anmeldung in der Werkstatt.")
    ok, warum = addons.usable(addon, settings, session.ultra)
    if not ok:
        raise addons.AddOnError(warum)
    if not addons.active(settings, addon.id, session.ultra):
        raise addons.AddOnError(f"{addon.name} ist nicht installiert oder ausgeschaltet.")
    box = werkstatt.shared(settings)
    fertig = box.desktop("open", addons.APP_OF[addon.id], timeout=90)
    if fertig.returncode != 0:
        raise addons.AddOnError(
            f"{addon.name} ließ sich nicht öffnen: "
            + (fertig.stderr.decode("utf-8", "replace").strip() or "keine Meldung")[:300]
        )
    return (
        f"{addon.name} ist in der Werkstatt offen. Scanne den QR-Code unten mit deinem Handy "
        "und drück dann „Ich bin angemeldet“."
    )


#: Tasten, die man im Werkstatt-Bildschirm selbst druecken kann.
INPUT_KEYS = frozenset({
    "Return", "Tab", "BackSpace", "Escape", "Delete", "Up", "Down", "Left", "Right",
    "Home", "End", "Page_Up", "Page_Down", "ctrl+a", "ctrl+c", "ctrl+v", "ctrl+l", "shift+Tab",
    "F5",
})

#: So viel tippt man auf einmal selbst.
MAX_INPUT_CHARS = 1000


def workshop_input(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Der Nutzer bedient die Werkstatt selbst: klicken, tippen, Tasten.

    Das ist der Weg fuer Anmeldungen ("Login-Apps"): Passwoerter und Codes
    tippt der Mensch, nicht Aquaticy. Deshalb laeuft das bewusst NICHT durch
    die Pruefungen aus aquaticy/desktop.py -- die gelten fuer die KI. Und
    deshalb wird der Text nirgends abgelegt: nicht im Verlauf, nicht im
    Protokoll, nicht beim Modell. Er geht einmal an die Werkstatt, fertig.
    """
    from aquaticy import sandbox as werkstatt
    from aquaticy.desktop import HEIGHT, WIDTH

    session = SESSION.current() if isinstance(SESSION, SessionProxy) else SESSION
    if not session.ultra:
        # Seit 9.5.17 gehoert der User mode zu Ultra -- auch Pro kommt nicht dran.
        return {"ok": False, "error": "Der User mode gehört zu Ultra."}, 403
    settings = session.settings()
    if not getattr(settings, "vm_user_mode", False):
        return {"ok": False, "error": "Der User mode ist aus."}, 400
    box = werkstatt.shared(settings)
    art = str(payload.get("art") or "").strip().lower()
    if art == "open":
        # Der einzige Weg, der eine Werkstatt starten darf: der Nutzer will
        # sich selbst irgendwo anmelden und oeffnet dafuer eine Adresse.
        from aquaticy.desktop import URL_RE

        adresse = str(payload.get("url") or "").strip()
        if not URL_RE.match(adresse) or len(adresse) > 2000:
            return {"ok": False, "error": "Bitte eine Adresse mit https:// (oder http://)."}, 400
        try:
            fertig = box.desktop("open", "browser", adresse, timeout=90)
        except Exception as exc:
            return {"ok": False, "error": f"Die Werkstatt startet nicht: {exc}"}, 502
        if fertig.returncode != 0:
            return {"ok": False, "error": fertig.stderr.decode("utf-8", "replace")[:200]}, 400
        return {"ok": True}, 200
    if not box.alive or not box.user_mode:
        return {"ok": False, "error": "Es läuft gerade keine Werkstatt im User mode."}, 404
    try:
        if art == "click":
            x, y = int(payload.get("x")), int(payload.get("y"))
            if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
                return {"ok": False, "error": "Die Stelle liegt nicht auf dem Bildschirm."}, 400
            args = ["click", str(x), str(y)]
            if payload.get("double") is True:
                args.append("--double")
            fertig = box.desktop(*args, timeout=20, start=False)
        elif art == "type":
            text = str(payload.get("text") or "")
            if not text or len(text) > MAX_INPUT_CHARS:
                return {"ok": False, "error": f"1 bis {MAX_INPUT_CHARS} Zeichen."}, 400
            fertig = box.desktop("type", stdin=text.encode("utf-8"), timeout=60, start=False)
        elif art == "key":
            taste = str(payload.get("key") or "")
            if taste not in INPUT_KEYS:
                return {"ok": False, "error": "Diese Taste gibt es hier nicht."}, 400
            fertig = box.desktop("key", taste, timeout=20, start=False)
        else:
            return {"ok": False, "error": "art: open, click, type oder key."}, 400
    except (TypeError, ValueError):
        return {"ok": False, "error": "x und y bitte als Zahlen."}, 400
    except Exception as exc:
        return {"ok": False, "error": f"Die Werkstatt antwortet nicht: {type(exc).__name__}"}, 502
    if fertig.returncode != 0:
        meldung = fertig.stderr.decode("utf-8", "replace").strip()[:200]
        return {"ok": False, "error": meldung or "Das hat nicht geklappt."}, 400
    return {"ok": True}, 200


def _prune_uploads(folder: Path) -> None:
    """Laesst nur die juengsten Dateien liegen."""
    with contextlib.suppress(OSError):
        files = sorted(
            (item for item in folder.iterdir() if item.is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old in files[KEEP_UPLOADS:]:
            with contextlib.suppress(OSError):
                old.unlink()


class TooLarge(ValueError):
    """Der Anfragekoerper sprengt die Grenze -- 413 statt 500."""


class BadRequest(ValueError):
    """Eine Anfrage, die so nicht gemeint sein kann -- 400 statt 500."""


def chat_markdown(title: str, entries: list[Any]) -> str:
    """Ein ganzer Chat als Markdown -- Frage als Ueberschrift, Antwort darunter.

    Markdown, weil die Antworten schon in Markdown geschrieben sind: jedes
    andere Format muesste sie umbauen und dabei etwas verlieren.
    """
    from datetime import datetime

    zeilen = [f"# {title or 'Chat'}", ""]
    for entry in entries:
        when = ""
        with contextlib.suppress(Exception):
            when = datetime.fromtimestamp(entry.created_at).strftime("%d.%m.%Y %H:%M")
        frage = " ".join(str(entry.question or "").split())
        zeilen += [f"## {frage or '(ohne Frage)'}", ""]
        if when:
            zeilen += [f"*{when}*", ""]
        zeilen += [str(entry.answer or "").strip(), ""]
    zeilen += ["---", "", "Aufgezeichnet von Aquaticy AI."]
    return "\n".join(zeilen)


def _design_style(design: dict[str, Any]) -> str:
    """Baut aus dem eigenen Design die CSS-Variablen fuer das <html>-Tag.

    Nur die Grundfarben -- den Rest (Kontrast der Knopfschrift, der helle
    Akzentton, die Schrift auf der Seitenleiste) rechnet das Skript beim
    Laden aus. Jede Farbe ist bereits geprueft (nur ``#rrggbb``), hier wird
    zur Sicherheit noch einmal auf genau dieses Muster geachtet.
    """
    import re as _re

    hexmuster = _re.compile(r"^#[0-9a-fA-F]{6}$")
    teile = []
    accent = str(design.get("accent") or "").strip().lower()
    bg = str(design.get("bg") or "").strip().lower()
    sidebar = str(design.get("sidebar") or "").strip().lower()
    if hexmuster.match(accent):
        teile += [f"--accent:{accent}", f"--accent-text:{accent}"]
    if hexmuster.match(bg):
        teile += [f"--bg:{bg}", f"--surface:{bg}"]
    if hexmuster.match(sidebar):
        teile += [f"--sidebar:{sidebar}"]
    return ";".join(teile)


def with_state(html: str) -> str:
    """Gibt der Seite den Zustand gleich mit auf den Weg.

    Zwei Gruende, das hier zu tun und nicht im Browser:

    * **Es blinkt nicht.** Wer das Erscheinungsbild erst per Skript setzt,
      sieht die Seite einen Wimpernschlag lang hell, bevor sie dunkel wird.
      Steht es im ausgelieferten HTML, ist sie von der ersten Zeile an
      richtig.
    * **Der Browser haelt nichts fest.** Er bekommt gesagt, was gilt. Was
      er selbst gespeichert haette, muesste man ihm glauben -- und das ist
      genau das, was hier nicht mehr passieren soll.
    """
    from aquaticy.uistate import defaults

    if AUTH is not None and getattr(SESSION.current(), "account", None) is None:
        # Vor der Anmeldung gibt es noch kein Konto -- dann die Grundeinstellung,
        # nicht der Zustand des Server-Profils (bis 9.5.15 sah jeder Fremde
        # dessen Farben, Modus und Mitlesen).
        stand = defaults()
    else:
        try:
            stand = ui_state().read()
        except Exception:  # pragma: no cover - eine Seite ohne Zustand ist besser als keine
            stand = defaults()

    attrs = ""
    if stand.get("theme") in ("light", "dark"):
        attrs += f' data-theme="{stand["theme"]}"'
    if stand.get("palette"):
        attrs += f' data-palette="{stand["palette"]}"'
    # Beim eigenen Design gleich die Grundfarben ans <html> schreiben, damit
    # die Seite schon in den richtigen Farben ankommt und nicht kurz aufblitzt.
    if stand.get("palette") == "custom":
        stil = _design_style(stand.get("design") or {})
        if stil:
            attrs += f' style="{stil}"'
    html = html.replace('<html lang="de">', f'<html lang="de"{attrs}>', 1)

    # Die Klassen am Koerper stehen sonst erst, wenn das Skript durch ist.
    koerper = ["start"]
    if stand.get("mode") == "code":
        koerper.append("code-mode")
    if stand.get("mode") == "pro":
        koerper.append("pro-mode")
    if stand.get("tracing"):
        koerper.append("tracing")
    if stand.get("denken"):
        koerper.append("denken")
    html = html.replace('<body class="start">', f'<body class="{" ".join(koerper)}">', 1)

    # `json.dumps` sorgt fuer gueltiges JSON; `</` wird zerlegt, damit ein
    # Wert das Skript nicht vorzeitig beenden kann. Die Werte kommen zwar
    # alle aus einer Weissliste -- aber genau das ist der Punkt: man baut
    # die Absicherung ein, bevor jemand die Liste erweitert.
    roh = json.dumps(stand, ensure_ascii=False).replace("</", "<\\/")
    texte = json.dumps(webview.start_texts(), ensure_ascii=False).replace("</", "<\\/")
    boot = (
        f"window.__AQUATICY_STATE__ = {roh};"
        f"window.__AQUATICY_VERSION__ = {json.dumps(VERSION_LABEL)};"
        # Vorschlaege, Begruessungen, Beschriftungen -- vom Server (9.5.14).
        f"window.__AQUATICY_TEXTS__ = {texte};"
    )
    return html.replace("<script>", f"<script>{boot}</script>\n<script>", 1)


def safe_name(name: str) -> str:
    """Macht aus einem hochgeladenen Namen einen, der gefahrlos auf die Platte darf.

    Der Name kommt vom Browser und damit von aussen: "../../.ssh/authorized_keys"
    waere sonst ein gueltiger Ablageort.
    """
    name = Path(name.replace("\\", "/")).name  # Pfadanteile abschneiden
    cleaned = "".join(
        character if character.isalnum() or character in "-_. " else "_" for character in name
    ).strip(". ")
    return cleaned[:80] or "datei"


def same_secret(candidate: str, secret: str) -> bool:
    """Zeitkonstanter Vergleich zweier Zugangswoerter.

    Ueber Bytes, nicht ueber str: `compare_digest` lehnt Zeichenketten mit
    Nicht-ASCII rundheraus ab. Ein Zugangswort wie "grün" haette damit jeden
    Vergleich zum Fehler gemacht -- auch den mit dem richtigen Wort.
    """
    return secrets.compare_digest(
        candidate.encode("utf-8", "surrogateescape"),
        secret.encode("utf-8", "surrogateescape"),
    )


class Handler(BaseHTTPRequestHandler):
    """Sehr kleiner Router -- eine Handvoll Endpunkte."""

    server_version = "Aquaticy"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        return  # keine Zugriffsprotokolle in der Konsole

    def send_response(self, code: int, message: str | None = None) -> None:
        # Merken, dass die Antwort laeuft -- danach kann kein Fehlerblatt mehr
        # hinterhergeschickt werden (siehe _guarded).
        self.responded = True
        super().send_response(code, message)

    def end_headers(self) -> None:
        """Sicherheitskopfzeilen gelten fuer HTML, JSON, Downloads und SSE."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        super().end_headers()

    def _guarded(self, handler: Any) -> None:
        """Faengt alles ab, was in einer Route schiefgeht.

        Ohne das druckt die Standardbibliothek eine seitenlange Ablaufverfolgung
        in die Konsole, und der Browser bekommt gar nichts -- er versucht es
        dann immer wieder. Eine Zeile im Terminal und ein 500 sind brauchbarer.
        """
        self.responded = False
        route = self._route()
        client = self._client_ip()
        if not REQUEST_LIMIT.allow(client):
            self._json({"error": "Zu viele Anfragen. Bitte warte kurz."}, 429)
            return
        account = self._account()
        public = route in {
            "/",
            "/index.html",
            "/api/auth/status",
            "/api/consent",
            "/api/auth/register",
            "/api/auth/login",
        }
        if AUTH is not None and route.startswith("/api/") and not public and account is None:
            self._json({"error": "Bitte melde dich an."}, 401)
            return
        previous = getattr(_REQUEST, "session", None)
        if account is not None:
            _REQUEST.session = SESSIONS.get(account)
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Tab geschlossen -- kein Grund fuer eine Meldung
        except TooLarge as exc:
            if not self.responded:
                with contextlib.suppress(OSError):
                    self._json({"error": str(exc)}, 413)
        except BadRequest as exc:
            if not self.responded:
                with contextlib.suppress(OSError):
                    self._json({"error": str(exc)}, 400)
        except Exception as exc:
            print(f"  [Fehler] {self.command} {self.path}: {type(exc).__name__}: {exc}")
            if not self.responded:
                with contextlib.suppress(OSError):
                    self._json({"error": "Da ist bei Aquaticy etwas schiefgelaufen. "
                                         "Versuch es gleich noch einmal."}, 500)
        finally:
            if previous is None:
                with contextlib.suppress(AttributeError):
                    del _REQUEST.session
            else:
                _REQUEST.session = previous

    # -- Hilfen -----------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json")

    def _json_cookie(
        self, payload: dict[str, Any], name: str, value: str, max_age: int, status: int = 200
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._set_cookie(name, value, max_age)
        self.end_headers()
        self.wfile.write(body)

    def _route(self) -> str:
        """Pfad ohne Query -- `/?token=...` ist immer noch die Startseite."""
        return urlsplit(self.path).path

    def _query_token(self) -> str:
        return (parse_qs(urlsplit(self.path).query).get("token") or [""])[0]

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        with contextlib.suppress(Exception):
            cookie = SimpleCookie(raw)
            if TOKEN_COOKIE in cookie:
                return cookie[TOKEN_COOKIE].value
        return ""

    def _cookie(self, name: str) -> str:
        raw = self.headers.get("Cookie") or ""
        with contextlib.suppress(Exception):
            cookie = SimpleCookie(raw)
            if name in cookie:
                return cookie[name].value
        return ""

    def _client_ip(self) -> str:
        direkt = str(self.client_address[0] if self.client_address else "unknown")
        return client_ip(direkt, self.headers.get("X-Forwarded-For") or "",
                         self.headers.get("X-Real-IP") or "")

    def _device(self) -> str:
        return "\x1f".join(
            (
                self.headers.get("User-Agent") or "unbekannt",
                self.headers.get("Accept-Language") or "",
            )
        )[:1000]

    def _account(self) -> Account | None:
        if AUTH is None:
            return None
        return AUTH.session_account(self._cookie(AUTH_COOKIE), self._device(),
                                    self._client_ip())

    def _origin_ok(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        parsed = urlsplit(origin)
        return parsed.scheme in {"http", "https"} and parsed.netloc == (
            self.headers.get("Host") or ""
        )

    def _set_cookie(self, name: str, value: str, max_age: int) -> None:
        secure = "; Secure" if isinstance(self.connection, __import__("ssl").SSLSocket) else ""
        self.send_header(
            "Set-Cookie",
            f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict{secure}",
        )

    def _authorized(self) -> bool:
        """Prueft das Zugangswort -- aus Adresse, Cookie oder Kopfzeile.

        Ohne gesetztes TOKEN ist alles erlaubt; so bleibt der rein lokale
        Betrieb genauso einfach wie vorher.
        """
        if not TOKEN:
            return True
        return any(
            candidate and same_secret(candidate, TOKEN)
            for candidate in (
                self._query_token(),
                self._cookie_token(),
                self.headers.get("X-Aquaticy-Token") or "",
            )
        )

    def _deny(self) -> None:
        body = DENIED_PAGE.encode()
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        roh = (self.headers.get("Content-Length") or "0").strip()
        # Nur eine nicht-negative Zahl. "-1" hiesse fuer read(): lies, bis die
        # Verbindung zu ist -- also so viel, wie jemand schickt.
        if not roh.isdigit() or len(roh) > 12:
            raise BadRequest("Ungueltige Laenge der Anfrage.")
        length = int(roh)
        if not length:
            return {}
        if length > MAX_BODY_BYTES:
            # Nicht lesen, nur verwerfen -- sonst zieht ein einziger Aufruf
            # den Arbeitsspeicher leer.
            raise TooLarge(
                f"Anfrage zu gross ({length // 1_000_000} MB). Erlaubt sind "
                f"{MAX_UPLOADS} Dateien à {MAX_UPLOAD_BYTES // 1_000_000} MB."
            )
        try:
            gelesen = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, ValueError):
            return {}  # kaputtes JSON ist eine leere Anfrage, kein Absturz
        # Gueltiges JSON ist noch kein Formular: `[]`, `"text"` und `0` sind
        # alle drei erlaubt und haben kein `.get`. Jede Route, die den Koerper
        # als Feld-Sammlung liest, bekam damit einen Serverfehler statt einer
        # Absage. Was kein Objekt ist, gilt hier als leere Anfrage.
        return gelesen if isinstance(gelesen, dict) else {}

    # -- Routen -----------------------------------------------------------
    # Namen von BaseHTTPRequestHandler vorgegeben.
    def do_GET(self) -> None:
        self._guarded(self._get)

    def do_POST(self) -> None:
        self._guarded(self._post)

    def do_DELETE(self) -> None:
        self._guarded(self._delete)

    def _delete(self) -> None:
        """Loeschen ist ein eigenes Verb -- ein POST "loeschAlles" waere gelogen."""
        if not self._authorized():
            self._deny()
            return
        if not self._origin_ok():
            self._json({"error": "Die Herkunft der Anfrage stimmt nicht."}, 403)
            return
        route = self._route()
        settings = SESSION.settings()
        if route == "/api/jobs":
            from aquaticy.jobs import JobStore

            gemeint = (parse_qs(urlsplit(self.path).query).get("id") or [""])[0].strip()
            try:
                nummer = int(gemeint)
            except ValueError:
                self._json({"ok": False, "error": "Keine gueltige Kennung."}, 400)
                return
            self._json({"ok": delete_job(JobStore(settings.db_path), nummer, settings)})
        elif route == "/api/memory":
            frage = parse_qs(urlsplit(self.path).query)
            if not settings.memory_enabled:
                self._json({"error": "Der Speicher ist abgeschaltet."}, 400)
                return
            store = SESSION.memory()
            eintrag = (frage.get("id") or [""])[0].strip()
            if not eintrag:
                self._json({"forgotten": store.clear()})
                return
            # Eine Kennung, die keine Zahl ist, ist ein Tippfehler und
            # kein Serverfehler -- also 400 statt 500.
            try:
                nummer = int(eintrag)
            except ValueError:
                self._json({"error": "Keine gueltige Kennung."}, 400)
                return
            self._json({"forgotten": bool(store.forget(nummer))})
        else:
            self._json({"error": "unbekannt"}, 404)

    def _job_image(self, payload: dict[str, Any], settings: Settings) -> tuple[str, str]:
        """Nimmt das Vergleichsbild eines Bildauftrags entgegen.

        Es landet im privaten Datenordner des Kontos -- derselbe Ort, an dem
        auch geprueftes Bildmaterial liegt. Zurueck kommt (Kennung, Fehler);
        genau eines von beiden ist gefuellt.
        """
        from aquaticy.media import save_snapshot

        try:
            data = base64.b64decode(str(payload.get("image_data") or ""), validate=True)
        except (ValueError, binascii.Error):
            return "", "Das Bild konnte nicht gelesen werden."
        if not data:
            return "", "Für die Bildsuche fehlt das Bild."
        if len(data) > MAX_UPLOAD_BYTES:
            return "", (
                f"Das Bild ist zu groß (erlaubt sind "
                f"{MAX_UPLOAD_BYTES // 1_000_000} MB)."
            )
        try:
            # `keep`: darauf beruft sich der Auftrag bei jedem Lauf. Ohne das
            # waere das Foto nach zweihundert Schnappschuessen weg.
            return save_snapshot(
                settings.data_dir, data, str(payload.get("image_type") or ""), keep=True
            ), ""
        except (OSError, ValueError):
            return "", "Dieses Bildformat wird nicht unterstützt (JPEG, PNG, WebP, GIF)."

    def _job_edit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Auftrag anlegen, anhalten, weiterlaufen lassen oder sofort ausfuehren."""
        from aquaticy.jobs import JobStore, run_job

        settings = SESSION.settings()
        store = JobStore(settings.db_path)
        action = str(payload.get("action", "add")).strip().lower()

        if action == "add":
            image_id = ""
            saved = False
            try:
                kind = str(payload.get("kind", "research")).strip().lower()
                if kind in ("visual", "image") and not selected_vision_model(settings):
                    return {
                        "ok": False,
                        "error": "Die Bildbeobachtung braucht ein Vision-Modell unter Modell.",
                    }
                if kind == "image":
                    image_id, fehler = self._job_image(payload, settings)
                    if fehler:
                        return {"ok": False, "error": fehler}
                job = store.add(
                    str(payload.get("question", "")),
                    rhythm=str(payload.get("rhythm", "daily")),
                    hour=int(payload.get("hour", 8) or 0),
                    minute=int(payload.get("minute", 0) or 0),
                    weekday=int(payload.get("weekday", 0) or 0),
                    structured=bool(payload.get("structured", True)),
                    kind=kind,
                    source_url=str(payload.get("source_url", "")),
                    image_id=image_id,
                )
                saved = True
            except (ValueError, TypeError) as exc:
                return {"ok": False, "error": str(exc)}
            except sqlite3.Error:
                return {"ok": False, "error": "Der Auftrag konnte nicht gespeichert werden. "
                        "Bitte versuche es erneut."}
            finally:
                if image_id and not saved:
                    from aquaticy.media import delete_snapshot

                    with contextlib.suppress(OSError, ValueError):
                        delete_snapshot(settings.data_dir, image_id)
            return {"ok": True, "job": job.as_dict()}

        try:
            nummer = int(payload.get("id", 0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Keine gueltige Kennung."}

        if action in ("pause", "resume"):
            if not store.set_enabled(nummer, action == "resume"):
                return {"ok": False, "error": "Diesen Auftrag gibt es nicht."}
            return {"ok": True}
        if action == "delete":
            return {"ok": delete_job(store, nummer, settings)}
        if action == "run":
            # Sofort ausfuehren laeuft im Hintergrund: eine Recherche dauert
            # Minuten, so lange darf keine Anfrage offen stehen.
            job = store.get(nummer)
            if job is None:
                return {"ok": False, "error": "Diesen Auftrag gibt es nicht."}

            from aquaticy.jobs import GUARD_STATE, claim_run, release_run

            # Laeuft er schon (Planer oder ein frueherer Klick), nicht doppelt.
            if not claim_run(settings.db_path, job.id):
                return {"ok": False, "error": "Dieser Auftrag läuft gerade schon."}

            def sofort() -> None:
                # Scheitert der Lauf, muss das trotzdem am Auftrag stehen:
                # sonst bleibt er auf "laeuft gerade" haengen und sein
                # naechster Termin in der Vergangenheit.
                try:
                    # Das Kontingent prueft run_job ueber settings.quota.
                    state, chat = run_job(job, settings)
                except Exception as exc:
                    state, chat = (f"Fehler: {type(exc).__name__}", "")
                finally:
                    release_run(settings.db_path, job.id)
                store.note_run(job.id, state, chat)
                if state == "erfüllt" or state.startswith(GUARD_STATE):
                    store.set_enabled(job.id, False)

            threading.Thread(target=sofort, daemon=True).start()
            return {"ok": True, "started": True}
        return {"ok": False, "error": f"Unbekannt: {action}"}

    def _workshop_file(self) -> None:
        """Reicht eine Datei aus der Werkstatt heraus -- zum Herunterladen."""
        from aquaticy import sandbox as werkstatt

        wanted = (parse_qs(urlsplit(self.path).query).get("path") or [""])[0]
        box = werkstatt.shared(SESSION.settings())
        if not box.alive:
            self._json({"error": "Die Werkstatt laeuft gerade nicht."}, 404)
            return
        try:
            data = box.get_bytes(wanted)
        except FileNotFoundError as exc:
            self._json({"error": str(exc)}, 404)
            return
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return
        except Exception as exc:  # pragma: no cover - Laufzeit meldet Unerwartetes
            self._json({"error": f"Die Werkstatt antwortet nicht: {exc}"}, 502)
            return
        name = safe_name(wanted.rsplit("/", 1)[-1] or "datei")
        self.send_response(200)
        # Immer als Anhang: sonst koennte eine HTML-Datei aus der Werkstatt
        # im Browser laufen -- und zwar unter der Adresse von Aquaticy.
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _workshop_screen(self) -> None:
        """Der Bildschirm der Werkstatt im User mode -- so, wie Aquaticy ihn sieht.

        Startet nichts: laeuft keine Werkstatt, gibt es auch kein Bild. Ein
        Blick hinein soll keine Maschine hochfahren.
        """
        from aquaticy import sandbox as werkstatt

        box = werkstatt.shared(SESSION.settings())
        if not box.alive or not box.user_mode:
            if "leise=1" in self.path:
                # Das Login-Fenster fragt alle anderthalb Sekunden -- "nichts
                # da" ist dort der Normalfall, kein Fehler fuer die Konsole.
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            self._json({"error": "Es laeuft gerade keine Werkstatt im User mode."}, 404)
            return
        try:
            data = box.screenshot(start=False)
        except Exception as exc:  # pragma: no cover - Laufzeit meldet Unerwartetes
            self._json({"error": f"Kein Bildschirmfoto bekommen: {exc}"}, 502)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _get(self) -> None:
        if not self._authorized():
            self._deny()
            return
        route = self._route()
        if route in ("/", "/index.html"):
            self._send_ui()
        elif route in LEGAL_ROUTES:
            self._send(200, legal_page(route), "text/html; charset=utf-8")
        elif route == "/api/auth/status":
            account = self._account()
            self._json(
                {
                    "consent": self._cookie(CONSENT_COOKIE) == "yes",
                    "authenticated": account is not None,
                    "account": (
                        {"email": account.email, "username": account.username,
                         "plan": account.plan} if account else None
                    ),
                }
            )
        elif route == "/api/media":
            query = parse_qs(urlsplit(self.path).query)
            media_id = (query.get("id") or [""])[0].strip()
            if media_id:
                from aquaticy.media import load_snapshot

                snapshot = load_snapshot(SESSION.settings().data_dir, media_id)
                if snapshot is None:
                    self._json({"error": "Dieses Bild ist nicht mehr vorhanden."}, 404)
                    return
                data, mime = snapshot
                self._send(200, data, mime)
                return
            target = (query.get("url") or [""])[0].strip()
            if not target or len(target) > 8_000:
                self._json({"error": "Keine gültige Bildadresse."}, 400)
                return
            status, inhalt, art = media_by_url(SESSION.settings(), target)
            if status != 200:
                self._json({"error": inhalt.decode("utf-8")}, status)
                return
            self._send(200, inhalt, art)
        elif route == "/api/account":
            account = self._account()
            if account is None:
                self._json({"error": "Bitte melde dich an."}, 401)
                return
            # Seit 9.5.14: Prozent und Uhrzeiten, keine Tokenzahlen -- gerechnet
            # hier auf dem Server (aquaticy/quota.py).
            self._json(
                {
                    "email": account.email,
                    "username": account.username,
                    "plan": account.plan,
                    "pro": account.elevated,
                    "ultra": account.ultra,
                    "plan_label": account.plan_label,
                    "usage": usage_view(SESSION.settings()),
                }
            )
        elif route == "/google":
            self._google_return()
        elif route == "/api/config":
            settings = SESSION.settings()
            self._json(
                {
                    "version": __version__,
                    "version_label": VERSION_LABEL,
                    "account": (
                        {
                            "email": SESSION.account.email,
                            "username": SESSION.account.username,
                            "plan": SESSION.plan,
                            "pro": SESSION.pro,
                            "ultra": SESSION.ultra,
                        }
                        if SESSION.account is not None
                        else {"email": "lokal", "username": "", "plan": "ultra",
                              "pro": True, "ultra": True}
                    ),
                    "values": current_values(),
                    "key_name": api_key_name_for(settings.model),
                    "search_key_name": SEARCH_BACKEND_KEYS.get(settings.search_backend, ""),
                    "env_path": str(settings.env_path or DEFAULT_ENV_PATH),
                    "problems": settings.missing_requirements(),
                    # Schluessel selbst gehen nie an den Browser -- nur, ob welche da sind.
                    "ha_connected": bool(settings.ha_url and settings.ha_token),
                    "search_key_set": bool(settings.search_api_key),
                    # Im Code- und im Pro-Modus laeuft nicht das eingestellte
                    # Modell, sondern das staerkste erreichbare. Frueher stand
                    # oben trotzdem das alte -- man sah also nicht, womit
                    # gerade gearbeitet wird.
                    "strong_model": (strong_models(1) or [{}])[0].get("id", ""),
                    # Im Code-Modus ist "das staerkste" ein anderes: dort
                    # zaehlt das Modell fuers Programmieren.
                    "strong_code_model": (
                        strong_models(1, purpose="code") or [{}]
                    )[0].get("id", ""),
                    "google": google_state(settings),
                    "legal_rules": rules_overview(),
                    # Seit 9.5.14 fertig vom Server (aquaticy/webview.py):
                    "header": header_for(settings),
                    "providers": webview.provider_view(),
                    "search_key_hints": webview.search_key_hints(SEARCH_BACKEND_KEYS),
                    # "Eigene Modelle" zeigt nur, was das Konto selbst eingetragen hat.
                    "own_models": own_models(settings),
                }
            )
        elif route == "/api/header":
            # Nur die Kopfzeile: welches Modell wirklich antwortet, was gilt.
            self._json(header_for(SESSION.settings()))
        elif route == "/api/run":
            self._run_stream()
        elif route == "/api/runstate":
            self._run_state()
        elif route == "/api/chats":
            settings = SESSION.settings()
            cache = Cache(settings.db_path, settings.cache_ttl_hours)
            # Mit ?q= wird gesucht, ohne kommt die gewohnte Liste. Ein
            # eigener Pfad waere dasselbe in zwei Routen.
            suche = (parse_qs(urlsplit(self.path).query).get("q") or [""])[0].strip()
            self._json(
                {
                    # Die Gruppe ("Heute", "Gestern" ...) rechnet der Server.
                    "chats": webview.with_groups(
                        cache.search_chats(suche, limit=40)
                        if suche
                        else cache.recent_chats(limit=40)
                    ),
                    "query": suche,
                    "current": SESSION.chat_id(),
                }
            )
        elif route == "/api/chatexport":
            # Einen ganzen Chat als Markdown mitnehmen. Bewusst als eigener
            # Weg und nicht ueber /api/open: den Chat exportieren heisst
            # nicht, ihn zu oeffnen.
            settings = SESSION.settings()
            cache = Cache(settings.db_path, settings.cache_ttl_hours)
            wanted = (parse_qs(urlsplit(self.path).query).get("session_id") or [""])[0].strip()
            entries = cache.chat_history(wanted) if wanted else []
            if not entries:
                self._json({"error": "Diesen Chat gibt es nicht."}, 404)
                return
            chats = {chat["session_id"]: chat for chat in cache.recent_chats(limit=200)}
            title = str(chats.get(wanted, {}).get("title") or entries[0].question).strip()
            self._json({"title": title, "markdown": chat_markdown(title, entries),
                        "filename": webview.export_filename(title)})
        elif route == "/api/werkstatt":
            # Was in der Werkstatt liegt. Ist keine da, ist das keine
            # Stoerung -- dann liegt eben nichts da.
            from aquaticy import sandbox as werkstatt

            box = werkstatt.shared(SESSION.settings())
            self._json(
                {
                    "running": bool(box.alive),
                    "status": box.status(),
                    "usage_gb": box.usage_gb() if box.alive else 0.0,
                    "disk_gb": box.disk_gb,
                    "files": [
                        {**datei, "size_text": webview.size_text(datei.get("bytes"))}
                        for datei in (box.list_files() if box.alive else [])
                    ],
                }
            )
        elif route == "/api/werkstatt/datei":
            self._workshop_file()
        elif route == "/api/addons":
            from aquaticy import addons

            self._json(addons.public_view(SESSION.settings(), SESSION.ultra))
        elif route == "/api/keys":
            # Nur, was hinterlegt ist -- nie ein Schluessel selbst.
            self._json(keys_view(SESSION.current() if isinstance(SESSION, SessionProxy)
                                 else SESSION))
        elif route == "/api/werkstatt/bildschirm":
            self._workshop_screen()
        elif route == "/api/jobs":
            from aquaticy.jobs import RHYTHM_NAMES, JobStore

            store = JobStore(SESSION.settings().db_path)
            self._json(
                {
                    "jobs": [webview.job_view(job.as_dict()) for job in store.all_jobs()],
                    "rhythms": [
                        {"id": key, "name": name} for key, name in RHYTHM_NAMES.items()
                    ],
                }
            )
        elif route == "/api/prefs":
            self._json(ui_state().read())
        elif route == "/api/models":
            from aquaticy.system import available_models

            # ?purpose=code liefert unter "strong" die Modelle fuers
            # Programmieren, sonst die Arbeitspferde fuer die Recherche.
            frage = parse_qs(urlsplit(self.path).query)
            zweck = (frage.get("purpose") or [""])[0].strip()
            modus = (frage.get("mode") or [""])[0].strip()
            alle = available_models(SESSION.settings())
            antwort: dict[str, Any] = {
                "models": alle,
                # Fuer den Code- und den Pro-Modus: nur die staerksten.
                "strong": strong_models(3, purpose=zweck),
            }
            if modus:
                # Seit 9.5.14 entscheidet der Server, was die Auswahl zeigt: im
                # Code- und Pro-Modus nur die drei staerksten (ein schwaches
                # Modell ist dort am teuersten), und welches Feld die Wahl setzt.
                antwort["picker"] = webview.picker_view(
                    modus, alle, strong_models(3, purpose="code" if modus == "code" else ""),
                    limited=SESSION.settings().quota is not None,
                )
            self._json(antwort)
        elif route == "/api/memory":
            # Abgeschaltet heisst abgeschaltet: dann wird auch nichts gezeigt.
            if not SESSION.settings().memory_enabled:
                self._json({"enabled": False, "entries": []})
            else:
                store = SESSION.memory()
                self._json(
                    {
                        "enabled": True,
                        "usage": storage_view(store.usage()),
                        "entries": [
                            {**eintrag, "when_text": webview.date_text(eintrag.get("when"))}
                            for eintrag in (e.as_dict() for e in store.all_entries(limit=300))
                        ],
                    }
                )
        elif route == "/api/usage":
            from aquaticy.usage import UsageLog, summary_view

            settings = SESSION.settings()
            # Normale Konten sehen ihr Kontingent in Prozent; die Statistik in
            # Token gibt es nur ohne Kontingent (Pro, lokal). Aufbereitet wird
            # hier -- der Browser zeigt nur noch an.
            self._json({
                "limits": usage_view(settings),
                "stats": (
                    summary_view(UsageLog(settings.db_path).summary())
                    if settings.quota is None else None
                ),
            })
        elif route == "/api/system":
            if not SESSION.pro:
                self._json({"error": "Die Auslastungsanzeige braucht ein Pro-Konto."}, 403)
                return
            from aquaticy.system import snapshot

            settings = SESSION.settings()
            payload = snapshot(settings.data_dir)
            # Nicht "memory" nennen -- das ist im Abbild schon der
            # Arbeitsspeicher, der Schluessel wuerde ihn ueberschreiben.
            if settings.memory_enabled:
                payload["storage"] = storage_view(SESSION.memory().usage())
            payload["gauges"] = system_gauges(payload)
            self._json(payload)
        elif route == "/api/notes":
            settings = SESSION.settings()
            cache = Cache(settings.db_path, settings.cache_ttl_hours)
            self._json({"notes": [{"id": n.id, "text": n.text} for n in cache.list_notes()]})
        elif route == "/api/history":
            settings = SESSION.settings()
            cache = Cache(settings.db_path, settings.cache_ttl_hours)
            self._json(
                {
                    "history": [
                        {"question": entry.question, "answer": entry.answer}
                        for entry in cache.recent_history(limit=20)
                    ]
                }
            )
        else:
            self._json({"error": "unbekannter Pfad"}, 404)

    def _post(self) -> None:
        if not self._authorized():
            self._deny()
            return
        route = self._route()
        if not self._origin_ok():
            self._json({"error": "Die Herkunft der Anfrage stimmt nicht."}, 403)
            return
        if route == "/api/consent":
            accepted = bool(self._read_json().get("accepted"))
            if not accepted:
                self._json_cookie(
                    {"ok": False, "leave": True}, CONSENT_COOKIE, "", 0, status=403
                )
                return
            self._json_cookie({"ok": True}, CONSENT_COOKIE, "yes", 365 * 86400)
        elif route in {"/api/auth/register", "/api/auth/login"}:
            if AUTH is None:
                self._json({"error": "Die Kontoverwaltung ist nicht gestartet."}, 503)
                return
            if self._cookie(CONSENT_COOKIE) != "yes":
                self._json({"error": "Bitte bestätige zuerst den Datenschutzhinweis."}, 403)
                return
            if not AUTH_LIMIT.allow(self._client_ip()):
                self._json({"error": "Zu viele Anmeldeversuche. Bitte warte eine Minute."}, 429)
                return
            payload = self._read_json()
            if route.endswith("register"):
                if not str(payload.get("username", "")).strip():
                    self._json({"ok": False, "error": "Bitte wähle einen Nutzernamen."}, 400)
                    return
                if payload.get("terms_accepted") is not True:
                    self._json(
                        {
                            "ok": False,
                            "error": (
                                "Bitte stimme den Datenschutz- und "
                                "Nutzungsbedingungen ausdrücklich zu."
                            ),
                        },
                        400,
                    )
                    return
                try:
                    account = AUTH.register(
                        str(payload.get("email", "")),
                        str(payload.get("password", "")),
                        str(payload.get("plan", "normal")),
                        str(payload.get("pro_code", "")),
                        username=str(payload.get("username", "")),
                        terms_accepted=True,
                        terms_version=LEGAL_VERSION,
                        ip=self._client_ip(),
                    )
                except ValueError as exc:
                    self._json({"ok": False, "error": str(exc)}, 400)
                    return
            else:
                account = AUTH.authenticate(
                    str(payload.get("email", "")), str(payload.get("password", ""))
                )
                if account is None:
                    self._json({"ok": False, "error": "E-Mail oder Passwort stimmt nicht."}, 401)
                    return
            # Ein gesperrtes Konto (oder eine gesperrte Adresse) kommt nicht
            # herein -- weder mit richtigem Passwort noch über ein neues Konto
            # von derselben Adresse (9.5.16 Lion).
            if AIGUARD is not None:
                from aquaticy.aiguard import BANNED_MESSAGE

                if AIGUARD.is_banned(user_id=account.id, ip=self._client_ip()) is not None:
                    self._json({"ok": False, "error": BANNED_MESSAGE, "code": "banned"}, 403)
                    return
            AUTH.note_seen(account.id, self._client_ip())
            with contextlib.suppress(Exception):
                start_user_scheduler(account)
            token = AUTH.create_session(account, self._device(), self._client_ip())
            self._json_cookie(
                {"ok": True, "account": {"email": account.email,
                  "username": account.username, "plan": account.plan}},
                AUTH_COOKIE,
                token,
                30 * 86400,
            )
        elif route == "/api/auth/logout":
            if AUTH is not None:
                AUTH.logout(self._cookie(AUTH_COOKIE))
            self._json_cookie({"ok": True}, AUTH_COOKIE, "", 0)
        elif route == "/api/chat":
            self._chat()
        elif route == "/api/clear":
            SESSION.reset()
            self._json({"ok": True, "current": SESSION.chat_id()})
        elif route == "/api/open":
            wanted = str(self._read_json().get("session_id", "")).strip()
            if not wanted:
                self._json({"ok": False, "error": "keine Chat-Kennung"}, 400)
                return
            # Geoeffnet ist gelesen: das Leuchten in der Liste hoert auf.
            with contextlib.suppress(Exception):
                settings = SESSION.settings()
                Cache(settings.db_path, settings.cache_ttl_hours).clear_unread(wanted)
            self._json({"ok": True, **SESSION.open_chat(wanted)})
        elif route == "/api/ha":
            if not SESSION.ultra:
                self._json({"ok": False, "error": "Home Assistant gibt es nur mit Ultra."},
                           403)
                return
            self._json(self._ha_probe(self._read_json()))
        elif route == "/api/probe":
            self._json(self._probe(self._read_json()))
        elif route == "/api/google":
            self._json(self._google(self._read_json()))
        elif route == "/api/storage":
            if not SESSION.ultra:
                self._json({"ok": False,
                            "error": "Die Lagerverwaltung gibt es nur mit Ultra."}, 403)
                return
            self._json(self._storage_probe(self._read_json()))
        elif route == "/api/addons":
            antwort, status = addon_action(self._read_json())
            self._json(antwort, status)
        elif route == "/api/keys":
            antwort, status = keys_action(self._read_json())
            self._json(antwort, status)
        elif route == "/api/werkstatt/eingabe":
            antwort, status = workshop_input(self._read_json())
            self._json(antwort, status)
        elif route == "/api/chat-edit":
            self._json(self._chat_edit(self._read_json()))
        elif route == "/api/jobs":
            self._json(self._job_edit(self._read_json()))
        elif route == "/api/prefs":
            # Der Browser schlaegt vor, der Server entscheidet -- zurueck
            # kommt, was wirklich gilt, nicht was geschickt wurde.
            self._json(ui_state().write(self._read_json()))
        elif route == "/api/stop":
            self._json({"ok": SESSION.stop()})
        elif route == "/api/answer":
            text = str(self._read_json().get("text", "")).strip()
            self._json({"ok": SESSION.answer(text)})
        elif route == "/api/command":
            line = str(self._read_json().get("line", "")).strip()
            if not line.startswith("/"):
                self._json({"ok": False, "error": "kein Befehl"}, 400)
                return
            try:
                self._json({"ok": True, **SESSION.command(line)})
            except Exception as exc:
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
        elif route == "/api/config":
            try:
                written = save_values(self._read_json())
            except ValueError as exc:
                # Ein unsinniger Wert ist ein Tippfehler, kein Serverfehler --
                # und die Meldung soll sagen, welches Feld gemeint ist.
                self._json({"ok": False, "error": str(exc)}, 400)
                return
            except Exception as exc:
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
                return
            # Ein neues Modell oder ein neuer Schluessel kann die Rangfolge
            # aendern -- also noch einmal nachsehen statt den alten Stand
            # weiterzureichen.
            forget_strong_models(SESSION.settings().data_dir)
            self._json({"ok": True, "path": str(written)})
        else:
            self._json({"error": "unbekannter Pfad"}, 404)

    def _google_return(self) -> None:
        """Der Rueckweg von Google -- hier landet der Nutzer nach der Zustimmung.

        Sitzt der Browser auf demselben Rechner, ist die Anmeldung damit
        fertig; er sieht eine Seite mit dem Ergebnis. Sitzt er woanders,
        kommt er hier nie an -- dann kopiert er die Adresse in das Feld in
        den Einstellungen, was auf denselben Tausch hinauslaeuft.
        """
        from urllib.parse import parse_qs, urlparse

        from aquaticy.google import GoogleError, exchange_code

        query = parse_qs(urlparse(self.path).query)
        denied = query.get("error", [""])[0]
        code = query.get("code", [""])[0]
        state = query.get("state", [""])[0]
        if denied:
            self._google_page(False, f"Google hat abgelehnt: {denied}")
            return
        if not code:
            self._google_page(False, "Google hat keinen Code mitgeschickt.")
            return
        # Nur fuer die angemeldete Sitzung, die die Anmeldung begonnen hat --
        # und nur mit ihrem einmaligen state (9.5.15). Ohne Konto landeten
        # die Schluessel sonst im Profil des Servers.
        konto = self._account()
        if AUTH is not None and konto is None:
            self._google_page(False, "Bitte melde dich zuerst bei Aquaticy an und verbinde "
                                     "Google dann aus den Einstellungen.")
            return
        if not google_state_take(state, getattr(konto, "id", "") or ""):
            self._google_page(False, "Diese Rückmeldung von Google gehört zu keiner "
                                     "Anmeldung, die du hier begonnen hast (oder sie ist "
                                     "abgelaufen). Bitte in den Einstellungen neu verbinden.")
            return
        settings = SESSION.settings()
        if not settings.google_client_id or not settings.google_client_secret:
            self._google_page(False, "Client-ID und Secret fehlen -- erst speichern.")
            return
        client = google_client(settings)
        try:
            tokens = exchange_code(
                settings.google_client_id,
                settings.google_client_secret,
                code,
                google_redirect(self.headers.get("Host", "")),
            )
            client.remember(tokens)
            account = client.account()
        except GoogleError as exc:
            self._google_page(False, str(exc))
            return
        finally:
            client.close()
        self._google_page(True, f"Verbunden{f' als {account}' if account else ''}.")

    def _google_page(self, ok: bool, message: str) -> None:
        """Eine schlichte Seite als Rueckmeldung -- ohne Skript, ohne Ballast."""
        colour = "#2f6f4e" if ok else "#a4342b"
        title = "Geschafft" if ok else "Das hat nicht geklappt"
        body = f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Aquaticy AI</title>
<style>body{{font:15px/1.6 system-ui,sans-serif;margin:0;display:grid;place-items:center;
min-height:100vh;background:#faf9f6;color:#26241f}}
main{{max-width:30em;padding:32px;text-align:center}}
h1{{color:{colour};font-size:20px;margin:0 0 12px}}
p{{margin:0 0 8px;color:#57534a}}</style></head><body><main>
<h1>{title}</h1><p>{escape(message)}</p>
<p>Du kannst dieses Fenster schliessen und zu Aquaticy AI zurueckgehen.</p>
</main></body></html>"""
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _chat_edit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Benennt einen Chat um oder loescht ihn."""
        session_id = str(payload.get("session_id", "")).strip()
        action = str(payload.get("action", "")).strip()
        if not session_id:
            return {"ok": False, "error": "keine Chat-Kennung"}
        settings = SESSION.settings()
        cache = Cache(settings.db_path, settings.cache_ttl_hours)
        if action == "rename":
            # Ohne Titel: zurueck zur ersten Frage -- nicht der Name "None"
            # (bis 9.5.15 wurde aus einem fehlenden Titel woertlich "None").
            titel = payload.get("title")
            return {"ok": True, "title": cache.rename_chat(
                session_id, titel if isinstance(titel, str) else "")}
        if action == "delete":
            removed = cache.delete_chat(session_id)
            # Der geloeschte Chat war vielleicht der offene -- dann faengt der
            # naechste Satz einen neuen an, statt in ein Nichts zu schreiben.
            if session_id == SESSION.chat_id():
                SESSION.reset()
            return {"ok": True, "removed": removed}
        return {"ok": False, "error": f"unbekannte Aktion '{action}'"}

    def _storage_probe(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Sucht die Lagerverwaltung im Netz oder testet eine eingetippte Adresse."""
        from aquaticy.storage import Storage, StorageError, discover

        settings = SESSION.settings()
        url = str(payload.get("url", "")).strip()
        if not url:
            found = discover(settings.lan_subnet)
            if not found:
                return {
                    "ok": False,
                    "error": (
                        "Nichts gefunden. Läuft die Lagerverwaltung? Sonst die Adresse "
                        "von Hand eintragen, z.B. 192.168.1.5:3000."
                    ),
                }
            return {"ok": True, "url": found[0], "found": found}

        # Getestet wird immer lesend -- ein Verbindungstest soll nichts anlegen.
        client = Storage(url, access="read")
        try:
            info = client.info()
            rooms = client.rooms()
        except StorageError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            client.close()
        items = sum(int(room.get("itemCount") or 0) for room in rooms)
        return {
            "ok": True,
            "url": client.url,
            "name": str(info.get("name") or "Lagerverwaltung"),
            "version": str(info.get("version") or ""),
            "rooms": len(rooms),
            "items": items,
        }

    def _google(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Verbindet, trennt oder meldet den Stand des Google-Kontos.

        Der Ablauf hat zwei Schritte, weil Google dazwischen den Nutzer fragt:
        `start` liefert die Adresse zum Zustimmen, `finish` nimmt den Code
        entgegen, den Google zurueckgibt.
        """
        from aquaticy.google import GoogleError, consent_url, exchange_code

        action = str(payload.get("action", "state")).strip()
        settings = SESSION.settings()
        redirect = google_redirect(self.headers.get("Host", ""))

        if action == "start":
            client_id = str(payload.get("client_id", "")).strip() or settings.google_client_id
            # Wie viel Recht gefragt wird, entscheidet der Schalter im
            # Formular -- nicht der gespeicherte Stand. Wer gerade "Aendern
            # erlaubt" angehakt hat, soll nicht erst speichern muessen.
            darf_aendern = payload.get("write")
            if darf_aendern is None:
                darf_aendern = settings.google_write
            try:
                state = google_state_new(getattr(SESSION.account, "id", "") or "")
                return {
                    "ok": True,
                    "url": consent_url(client_id, redirect, state,
                                       write=bool(darf_aendern)),
                    "redirect": redirect,
                    "write": bool(darf_aendern),
                    "state": state,
                }
            except GoogleError as exc:
                return {"ok": False, "error": str(exc)}

        if action == "finish":
            eingefuegt = str(payload.get("code", ""))
            # Der state steht in der eingefuegten Adresse -- sonst schickt ihn
            # die Oberflaeche aus dem Start mit. Ohne passenden: kein Tausch.
            state = (parse_qs(urlsplit(eingefuegt.strip()).query).get("state") or [""])[0] \
                if "state=" in eingefuegt else str(payload.get("state", ""))
            if not google_state_take(state, getattr(SESSION.account, "id", "") or ""):
                return {"ok": False, "error": (
                    "Dieser Code gehört zu keiner Anmeldung, die du hier begonnen hast (oder "
                    "sie ist abgelaufen). Bitte auf „Mit Google verbinden“ klicken und neu "
                    "zustimmen.")}
            if not settings.google_client_id or not settings.google_client_secret:
                return {
                    "ok": False,
                    "error": (
                        "Erst Client-ID und Secret speichern, dann verbinden -- "
                        "ohne beides kann Google den Code nicht einloesen."
                    ),
                }
            client = google_client(settings)
            try:
                tokens = exchange_code(
                    settings.google_client_id,
                    settings.google_client_secret,
                    str(payload.get("code", "")),
                    redirect,
                )
                client.remember(tokens)
                return {"ok": True, **google_state(SESSION.settings())}
            except GoogleError as exc:
                return {"ok": False, "error": str(exc)}
            finally:
                client.close()

        if action == "disconnect":
            client = google_client(settings)
            try:
                client.disconnect()
            finally:
                client.close()
            return {"ok": True, **google_state(SESSION.settings())}

        return {"ok": True, **google_state(settings), "redirect": redirect}

    def _probe(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Testet Modell und Suchmaschine -- dasselbe, was `aquaticy setup` macht.

        Geprueft werden die Werte aus dem Formular, nicht die gespeicherten:
        so sieht man VOR dem Speichern, ob ein Schluessel stimmt. Ein leeres
        Schluesselfeld heisst weiterhin "unveraendert", also greift dann der
        gespeicherte.
        """
        from aquaticy.config import NO_KEY
        from aquaticy.keyvault import scrub
        from aquaticy.probe import check_llm, check_search

        settings = SESSION.settings()
        konto = SESSION.account is not None
        model = fix_model_id(str(payload.get("AQUATICY_MODEL", "")).strip()) or settings.model
        getippt = str(payload.get(API_KEY_FIELD, "")).strip()
        api_base = str(payload.get("AQUATICY_API_BASE", settings.api_base) or "").strip()
        if not SESSION.ultra:
            # Normale und Pro-Konten testen gegen die Adressen des Betreibers --
            # nicht gegen eine eingetippte (siehe _profile_settings).
            api_base = settings.api_base
        if api_base and not base_fits(api_base, model):
            # Dieselbe Regel wie im Betrieb -- sonst testet man etwas anderes,
            # als spaeter laeuft, und der Test luegt.
            api_base = ""
        des_betreibers = get_settings().api_base or ""
        if getippt:
            # Ein eingetippter Schluessel gehoert dem, der ihn eintippt.
            api_key, quelle = getippt, ("own" if konto else "operator")
            if konto and api_base and api_base == des_betreibers:
                # Ein eigener Schluessel geht nie an eine Adresse des
                # Betreibers -- getestet wird beim Anbieter selbst.
                api_base = ""
        else:
            # So, wie es nach dem Speichern liefe: dasselbe Modell, dieselbe
            # Adresse -- also dieselben Regeln fuer Schluessel und Rechnung
            # (Settings.key_source und llm_kwargs_for). Sonst testet man
            # etwas anderes, als spaeter laeuft.
            vorschau = replace(
                settings, model=model, api_base=api_base,
                own_api_base=(api_base if konto and SESSION.ultra and api_base
                              and api_base != des_betreibers else ""),
            )
            quelle = vorschau.key_source(model)
            weg = vorschau.llm_kwargs_for(model)
            api_key = str(weg.get("api_key") or "")
            api_base = str(weg.get("api_base") or "")
            if not konto and api_base and api_base not in {settings.api_base or "",
                                                           des_betreibers}:
                # Ohne Konten ist man selbst der Betreiber -- trotzdem geht ein
                # gespeicherter Schluessel nie an eine frisch eingetippte
                # Adresse. Nicht bloss leer lassen: dann holte sich LiteLLM
                # ihn selbst aus der Umgebung (bis 9.5.14 so passiert).
                api_key = NO_KEY

        backend = str(payload.get("AQUATICY_SEARCH_BACKEND", "")).strip() or settings.search_backend
        search_key = str(payload.get(SEARCH_KEY_FIELD, "")).strip()
        if not search_key:
            # Wie im Betrieb (Settings.search_key_for): der eigene, sonst der
            # des Betreibers nur, wenn er fuer dieses Konto gilt.
            search_key = settings.search_key_for(backend)
        engines = str(payload.get("AQUATICY_SEARCH_ENGINES", settings.search_engines) or "").strip()
        instance = str(payload.get("AQUATICY_SEARXNG_URL", settings.searxng_url) or "").strip()
        if not SESSION.ultra:
            instance = settings.searxng_url

        # Mit eigenem Schluessel (getippt oder im Schluesselbund) zaehlt der
        # Test nicht -- er laeuft auf Rechnung des Kontos beim Anbieter.
        kontingent = settings.quota if quelle != "own" else None
        try:
            # Der Test laeuft mit dem Schluessel des Betreibers -- also zaehlt er
            # wie jeder andere Aufruf ins Kontingent (seit 9.5.14).
            from aquaticy.pace import own_gate
            from aquaticy.probe import PROBE_QUESTION, PROBE_TOKENS
            from aquaticy.usage import tokens

            # Atomar gebucht, bevor der Test laeuft (9.5.15).
            # Die Buchung bleibt stehen: der Test hat den Anbieter gefragt.
            if kontingent is not None:
                kontingent.reserve(tokens(PROBE_QUESTION) + PROBE_TOKENS, model)
            llm_ok, llm_msg = check_llm(model, api_key, api_base,
                                        pace_key=own_gate(api_key) if quelle == "own" else "")
        except Exception as exc:  # QuotaExceeded: der Satz sagt, wann es weitergeht
            llm_ok, llm_msg = False, str(exc)
        search_ok, search_msg = check_search(backend, search_key, engines, instance)
        # Fehlermeldungen der Anbieter zitieren gern den Schluessel -- nie weitergeben.
        geheim = [api_key, search_key, getippt, *settings.secrets()]
        return {
            "ok": llm_ok and search_ok,
            "llm": {"ok": llm_ok, "message": scrub(llm_msg, geheim), "model": model},
            "search": {"ok": search_ok, "message": scrub(search_msg, geheim),
                       "backend": backend},
        }

    def _ha_probe(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Sucht eine Instanz oder testet die eingetragenen Angaben.

        Damit klappt die Einrichtung in der Oberflaeche genauso wie mit
        `aquaticy connect-ha`: Knopf druecken, Adresse steht da, Token einfuegen,
        Knopf druecken, fertig.
        """
        from aquaticy.homeassistant import HomeAssistant, HomeAssistantError, discover

        if payload.get("action") == "discover":
            try:
                return {"ok": True, "found": discover()}
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        from aquaticy.homeassistant import normalize_url

        settings = SESSION.settings()
        url = str(payload.get("url") or settings.ha_url).strip()
        eingetippt = str(payload.get("token") or "").strip()
        token = eingetippt or settings.ha_token
        if (not eingetippt and settings.ha_token and settings.ha_url
                and normalize_url(url) != normalize_url(settings.ha_url)):
            # Wie bei den Modell-Schluesseln (_probe): der gespeicherte Token
            # geht nur an die Adresse, fuer die er eingerichtet ist. Wer eine
            # andere testet, fuegt den Token dafuer selbst ein.
            return {"ok": False, "error": (
                "Für eine neue Adresse bitte den Token dazu eintragen — der "
                "gespeicherte geht nur an die gespeicherte Adresse."
            )}
        if not url or not token:
            return {"ok": False, "error": "Bitte trag die Adresse und den Zugangsschlüssel ein."}
        client = HomeAssistant(url, token)
        try:
            hello = client.ping()
            counts = client.domains()
        except HomeAssistantError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "name": hello,
            "url": client.url,
            "entities": sum(counts.values()),
            "domains": counts,
        }

    def _send_ui(self) -> None:
        """Liefert die Oberflaeche und legt das Zugangswort als Cookie ab.

        So braucht nur der erste Aufruf die lange Adresse; danach kennt der
        Browser das Wort von selbst.
        """
        body = with_state(UI_FILE.read_text(encoding="utf-8")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if TOKEN:
            self._set_cookie(TOKEN_COOKIE, TOKEN, 30 * 86400)
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, text: str) -> bool:
        """Schreibt ein Stueck in den Ereignisstrom. `False` = niemand mehr da.

        Bricht die Verbindung ab -- Tab zu, Seite neu geladen, Handy im
        Standby -- läuft der Auftrag weiter. Ein Lauf gehört der Sitzung,
        nicht der einzelnen HTTP-Verbindung; das Ergebnis bleibt danach als
        Chat erhalten und kann auf einem anderen Gerät geöffnet werden.
        """
        try:
            self.wfile.write(text.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False
        return True

    def _chat(self) -> None:
        """Fuehrt die Anfrage aus und streamt die Ereignisse als SSE."""
        kontingent = SESSION.settings().quota
        # Laeuft das Hauptmodell mit dem eigenen Schluessel des Kontos, kostet
        # es das Kontingent nichts (9.5.14 Seashell): dann darf der Chat auch am
        # Limit starten -- Serverarbeit lehnen die Werkzeuge im Lauf selbst ab.
        eigenes_modell = SESSION.settings().key_source(SESSION.settings().model) == "own"
        if kontingent is not None and not eigenes_modell:
            from aquaticy.quota import QuotaExceeded

            try:
                kontingent.check()
            except QuotaExceeded as exc:
                # Der Satz nennt, welches Limit und wann es sich zuruecksetzt;
                # "usage" ist derselbe Stand wie unter /api/account.
                self._json(
                    {"error": str(exc), "code": "quota", "which": exc.which,
                     "usage": usage_view(SESSION.settings())},
                    429,
                )
                return
        payload = self._read_json()
        message = str(payload.get("message", "")).strip()[:MAX_MESSAGE_CHARS]
        raw = payload.get("attachments")
        attachments = (
            [item for item in raw if isinstance(item, dict)][:MAX_UPLOADS]
            if isinstance(raw, list)
            else []
        )
        # Arbeitsweise, Struktur und Denktiefe gehoeren zur einzelnen Frage,
        # nicht zur Sitzung: dieselbe Person will mal eine ausfuehrliche
        # Recherche und im naechsten Satz nur den Code.
        #
        # Was der Browser dazu schickt, ist ein Vorschlag. Geprueft wird er
        # gegen dieselben Listen wie im Einstellungsfenster, und was gilt,
        # wird gleich vermerkt -- damit der naechste Aufruf und das naechste
        # Geraet denselben Stand vorfinden. Kommt nichts mit, gilt der
        # gespeicherte Stand: der Server weiss ihn selbst.
        #
        # "thinking" ist der alte Name von "structured" -- eine Oberflaeche
        # aus dem Cache soll deswegen nicht aufhoeren zu arbeiten.
        wunsch = {
            name: payload[name]
            for name in (
                "mode", "effort", "structured", "recheck", "online", "sandbox", "agents",
                "visual_sources"
            )
            if name in payload
        }
        if "structured" not in wunsch and "thinking" in payload:
            wunsch["structured"] = payload["thinking"]
        # Ein Schalter, den der gewaehlte Modus gar nicht zeigt, ist kein
        # Wunsch: er darf den gespeicherten Stand nicht ueberschreiben. Sonst
        # kaeme man aus dem Code-Modus zurueck und das abgeschaltete Web waere
        # wieder an -- ohne dass jemand etwas angefasst haette.
        from aquaticy.uistate import clean as clean_state

        gewuenscht = str(clean_state(wunsch, base=ui_state().read())["mode"])
        if gewuenscht == "code":
            wunsch.pop("online", None)
            wunsch.pop("visual_sources", None)
        stand = ui_state().write(wunsch) if wunsch else ui_state().read()
        mode = str(stand["mode"])
        effort = str(stand["effort"])
        structured = bool(stand["structured"])
        recheck = bool(stand["recheck"])
        online = bool(stand["online"])
        sandbox = bool(stand["sandbox"])
        agents = int(stand.get("agents", 12))
        visual_sources = bool(stand.get("visual_sources", False))
        agents = max(1, min(50 if mode == "pro" else 12, agents))
        # Und was der Modus ausblendet, gilt auch nicht -- das entscheidet der
        # Server, nicht der Browser: im Code-Modus wird immer nachgeschlagen.
        # Das Gegenpruefen geht unveraendert durch; was es bedeutet,
        # entscheidet der Agent am Modus (im Pro-Modus die vier Pruefer,
        # sonst die zweite Runde).
        if mode == "code":
            online = True
            visual_sources = False
        if visual_sources and not selected_vision_model(SESSION.settings()):
            self._json(
                {"error": "Webcams und Satellitenbilder brauchen ein bildfähiges "
                 "Hauptmodell oder ein Vision-Modell unter Einstellungen → Modell."}, 400
            )
            return
        if not message and not attachments:
            self._json({"error": "leere Nachricht"}, 400)
            return
        if not message:
            # Nur Dateien, kein Text: das ist eine vollstaendige Bitte.
            message = "Sieh dir das Angehaengte an und sag mir, worum es geht."

        # Ai-guard (9.5.16 Lion): schon gesperrt? Dann gar nicht erst starten.
        # Die zuletzt gesehene Adresse wird dabei festgehalten (für `aquaticy
        # list` und für eine Adresssperre).
        konto = self._account()
        if konto is not None and AUTH is not None:
            AUTH.note_seen(konto.id, self._client_ip())
        if AIGUARD is not None and konto is not None:
            gesperrt = AIGUARD.is_banned(user_id=konto.id, ip=self._client_ip())
            if gesperrt is not None:
                from aquaticy.aiguard import BANNED_MESSAGE

                self._json({"error": BANNED_MESSAGE, "code": "banned"}, 403)
                return

        # Der Lauf gehoert ab hier dem Server, nicht der Verbindung. Reisst
        # sie ab, laeuft er weiter und kann spaeter zu Ende gesehen werden.
        session = SESSION.current() if isinstance(SESSION, SessionProxy) else SESSION
        lauf = current_runs().start(message)
        if kontingent is not None:
            # Die 5-Stunden-Sitzung beginnt mit der ersten Nachricht -- nicht
            # erst mit dem ersten gezaehlten Token.
            kontingent.begin()
        seen_done = threading.Event()
        # Kein Schluessel -- eigener oder gestellter -- verlaesst den Server in
        # einer Meldung. Anbieter zitieren ihn gern in Fehlertexten.
        geheim = session.settings().secrets()

        def emit(name: str, payload: dict[str, Any]) -> None:
            # Der Renderer im Terminal nennt den Text "text"; im Browser
            # heisst das Ereignis "chunk", damit das Frontend es direkt
            # anhaengen kann.
            kind = "chunk" if name == "answer_chunk" else name
            if geheim and kind not in ("chunk", "thought"):
                payload = scrub_payload(payload, geheim)
            if AIGUARD is not None and konto is not None:
                # Zwei Wege zu einem Anhaltspunkt (9.5.16 Lion, aus demselben
                # Prüf-Aufruf des Rechtsrahmens): eine Ablehnung nach Grundgesetz/
                # BGB ("guard"), oder eine erkannte Missbrauchsabsicht ("abuse").
                anlass = ""
                if kind == "guard" and payload.get("stage") == "anfrage":
                    anlass = "Rechtsrahmen: " + str(payload.get("title") or "")
                elif kind == "abuse":
                    anlass = "Missbrauch: " + str(payload.get("art") or "")
                if anlass:
                    with contextlib.suppress(Exception):
                        if AIGUARD.note(konto.id, anlass, detail=anlass,
                                        chat=session.chat_id(),
                                        enforce=not getattr(konto, "ultra", False)):
                            lauf.add({"type": "banned"})
            if kind == "done":
                seen_done.set()
                # Der Aufnahmezeitpunkt eines Bildes kommt als fertiger Text
                # (9.5.14) -- der Browser rechnet keine Zeiten mehr um.
                payload = {**payload, "visuals": [
                    {**v, "captured_text": webview.iso_moment_text(v.get("captured_at"))}
                    if isinstance(v, dict) and v.get("captured_at") else v
                    for v in payload.get("visuals") or []
                ]} if payload.get("visuals") else payload
            lauf.add({"type": kind, **payload})

        def run() -> None:
            previous = getattr(_REQUEST, "session", None)
            _REQUEST.session = session
            try:
                # Läuft der Rechtsrahmen (normale Konten immer), reitet die
                # Missbrauchserkennung auf dessen Prüf-Aufruf mit -- kein zweiter
                # beim Modell. Nur wo er aus ist (Pro hat ihn abgeschaltet),
                # fragt Ai-guard selbst.
                if (AIGUARD is not None and konto is not None
                        and not session.settings().legal_guard):
                    from aquaticy.aiguard import check_message

                    verdaechtig, grund = check_message(
                        AIGUARD, konto, message, session.settings(),
                        chat=session.chat_id())
                    if verdaechtig:
                        lauf.add({"type": "chunk", "text": grund})
                        lauf.add({"type": "banned"})
                        lauf.add({"type": "done"})
                        seen_done.set()
                        return
                SESSION.ask(
                    message,
                    emit,
                    attachments,
                    mode=mode,
                    structured=structured,
                    recheck=recheck,
                    effort=effort,
                    online=online,
                    sandbox=sandbox,
                    agents=agents,
                    visual_sources=visual_sources,
                )
            except Exception as exc:
                lauf.add({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                # Ohne ein "done" bliebe im Browser der blinkende Cursor
                # stehen -- die Oberflaeche waere scheinbar haengen.
                if not seen_done.is_set():
                    lauf.add({"type": "done"})
                lauf.finish()
                if previous is None:
                    with contextlib.suppress(AttributeError):
                        del _REQUEST.session
                else:
                    _REQUEST.session = previous

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        self._stream_run(lauf, since=0)

    def _run_stream(self) -> None:
        """Haengt sich an einen Turn -- genau den mit dieser Kennung (seit 9.5.15)."""
        frage = parse_qs(urlsplit(self.path).query)
        kennung = (frage.get("id") or [""])[0]
        lauf = current_runs().get(kennung) if kennung else current_runs().latest()
        if lauf is None:
            self._json({"error": "kein Lauf"}, 404)
            return
        roh = (frage.get("since") or ["0"])[0]
        try:
            since = max(0, int(roh))
        except (TypeError, ValueError):
            since = 0
        self._stream_run(lauf, since=since)

    def _run_state(self) -> None:
        """Was gerade laeuft -- fuer die Seite, die eben geladen wurde."""
        lauf = current_runs().latest()
        self._json(lauf.state() if lauf is not None else {"running": False, "resume": False})

    def _stream_run(self, lauf: Run, since: int = 0) -> None:
        """Schickt die Ereignisse eines Laufs als SSE, ab *since*."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        # Ohne das puffert manch ein Zwischenstueck die Antwort und nichts
        # kommt an, bevor alles fertig ist.
        self.send_header("Connection", "close")
        self.end_headers()

        # Sofort ein Lebenszeichen: erst damit steht die Verbindung fuer den
        # Browser wirklich, und man sieht, dass etwas passiert.
        self._sse(": los\n\n")

        stand = since
        while True:
            neue, stand, fertig = lauf.read(stand, HEARTBEAT_SECONDS)
            for event in neue:
                if not self._sse(f"data: {json.dumps(event, ensure_ascii=False)}\n\n"):
                    return
            if fertig and stand <= len(lauf.events):
                # Bis zum Schluss gesehen -- der Lauf muss nicht noch einmal
                # abgeholt werden.
                lauf.delivered = True
                return
            # Ein Cloud-Modell schweigt zwischen zwei Schritten gern eine
            # halbe Minute. Ohne ein Byte in der Leitung legt irgendwer in der
            # Kette auf -- der Browser, das Handy-Funkmodul, ein Proxy -- und
            # die Oberflaeche meldet "network error", obwohl die Recherche
            # noch laeuft. Ein Doppelpunkt ist ein Kommentar im SSE-Format: er
            # haelt die Leitung warm und wird nicht angezeigt.
            if not neue and not self._sse(": warte\n\n"):
                return


def _route_to(target: str) -> str:
    """Welche eigene Adresse benutzt das System, um *target* zu erreichen?

    Der UDP-"Verbindungsaufbau" schickt kein einziges Paket -- er laesst nur
    das Betriebssystem die Route waehlen und verraet damit die Adresse der
    passenden Netzwerkkarte. Das ist zuverlaessiger als
    ``gethostbyname(gethostname())``, das auf vielen Linux-Systemen
    127.0.1.1 liefert.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((target, 9))
        address = probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()
    return "" if address.startswith("127.") else address


def lan_address() -> str:
    """Die eigene Adresse im heimischen Netz."""
    address = _route_to("8.8.8.8")
    if address and not is_tailscale(address):
        return address
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidate = info[4][0]
            if not candidate.startswith("127.") and not is_tailscale(candidate):
                return candidate
    return "" if is_tailscale(address) else address


def is_tailscale(address: str) -> bool:
    """Liegt *address* im Tailscale-Bereich 100.64.0.0/10?"""
    parts = address.split(".")
    if len(parts) != 4 or parts[0] != "100" or not parts[1].isdigit():
        return False
    return 64 <= int(parts[1]) <= 127


def tailscale_address() -> str:
    """Die eigene Tailscale-Adresse, falls es eine gibt.

    100.100.100.100 ist der DNS-Dienst im Tailnet -- die Route dorthin fuehrt
    zwangslaeufig ueber die Tailscale-Karte. Ohne Tailscale gibt es keine
    solche Route und wir bekommen nichts Brauchbares zurueck.
    """
    address = _route_to("100.100.100.100")
    return address if is_tailscale(address) else ""


def is_public_host(host: str) -> bool:
    """Bindet *host* an mehr als nur den eigenen Rechner?"""
    return host not in ("127.0.0.1", "localhost", "::1", "")


def token_problem(token: str) -> str:
    """Prueft ein selbst gewaehltes Zugangswort. Leer = in Ordnung.

    Der Browser schickt das Wort als HTTP-Kopfzeile mit, und die darf nur
    ASCII enthalten -- ein "grün" kaeme dort nie an. Lieber gleich beim Start
    sagen als spaeter bei jedem Aufruf scheitern.
    """
    if not token:
        return ""
    if any(character.isspace() for character in token):
        return "Das Zugangswort darf keine Leerzeichen enthalten."
    if not token.isascii():
        umlauts = "".join(sorted({c for c in token if not c.isascii()}))
        return (
            f"Das Zugangswort darf keine Sonderzeichen enthalten ({umlauts}). "
            "Nimm ein Wort ohne Umlaute -- also 'gruen' statt 'grün'."
        )
    if any(character in token for character in "?&#/%"):
        return "Das Zugangswort darf kein ?, &, #, / oder % enthalten -- das zerlegt die Adresse."
    return ""


def new_token() -> str:
    """Kurzes Zugangswort -- muss auf einem Handy tippbar bleiben."""
    return secrets.token_urlsafe(9)


def addresses_for(host: str, port: int, token: str = "") -> list[tuple[str, str]]:
    """Alle Adressen, unter denen die Oberflaeche erreichbar ist.

    Gibt Paare aus Adresse und Erklaerung zurueck. Die eigene Maschine steht
    immer zuletzt -- die funktioniert garantiert und taugt deshalb als
    Adresse, die der Browser beim Start selbst aufmacht.
    """
    suffix = f"?token={token}" if token else ""
    found: list[tuple[str, str]] = []
    if is_public_host(host):
        if host == "0.0.0.0":  # alle Netzwerkkarten -- per --lan gewaehlt
            lan = lan_address()
            if lan:
                found.append((lan, "im heimischen Netz"))
            tailscale = tailscale_address()
            if tailscale:
                found.append((tailscale, "über Tailscale"))
        else:
            found.append((host, "wie angegeben"))
    found.append(("127.0.0.1", "auf diesem Rechner"))
    return [(f"http://{address}:{port}/{suffix}", note) for address, note in found]


def urls_for(host: str, port: int, token: str = "") -> list[str]:
    """Nur die Adressen aus :func:`addresses_for`, ohne Erklaerungen."""
    return [url for url, _ in addresses_for(host, port, token)]


def _warm_up() -> None:
    """Laedt im Hintergrund, was der erste Seitenaufruf sonst abwarten muesste.

    `model_problem` zieht beim ersten Mal LiteLLM nach -- drei Sekunden, in
    denen die Kopfzeile leer bliebe und der Nutzer sich fragt, ob etwas kaputt
    ist. Also erledigen wir das, waehrend er noch den Browser oeffnet.
    """
    with contextlib.suppress(Exception):
        from aquaticy.config import model_problem

        model_problem(SESSION.settings().model)


def serve(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    token: str = "",
) -> None:
    """Startet die Oberflaeche und blockiert, bis Strg+C kommt.

    Den Zugang schuetzen die Konten: ohne Anmeldung gibt es nichts ausser der
    Anmeldeseite. *token* ist eine freiwillige zweite Schranke davor
    (``aquaticy web --token``) -- gesetzt wird es nur, wenn man es angibt.
    """
    global AUTH, TOKEN

    TOKEN = token
    data_dir = get_settings().data_dir
    code = pro_code_for(data_dir)
    from aquaticy.auth import ultra_code_for

    ultra = ultra_code_for(data_dir)
    AUTH = AuthStore(data_dir, code, ultra)
    global AIGUARD
    from aquaticy.aiguard import guard_for

    AIGUARD = guard_for(data_dir)
    print(f"  Pro-Code:   {code} (9 Zeichen, geheim halten)")
    print(f"  Ultra-Code: {ultra} (14 Zeichen, geheim halten)")
    # Ein harter Abbruch kann eine Werkstatt zurueckgelassen haben. Sie belegt
    # Speicher und hat nichts mehr zu tun -- also weg damit, bevor es losgeht.
    try:
        from aquaticy.sandbox import sweep

        threading.Thread(target=sweep, daemon=True).start()
    except Exception:  # pragma: no cover - Aufraeumen darf nie den Start kosten
        pass
    # Auftraege: was faellig ist, laeuft von selbst. Ein eigener Thread, der
    # jede Minute nachsieht -- kostet nichts, solange nichts ansteht.
    try:
        from aquaticy.jobs import Scheduler

        global SCHEDULER
        SCHEDULER = Scheduler(SESSION.settings)
        SCHEDULER.start()
        for account in AUTH.accounts():
            start_user_scheduler(account)
    except Exception:  # pragma: no cover - Auftraege duerfen den Start nie kosten
        pass
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=_warm_up, daemon=True).start()
    if open_browser:
        # Auf dem eigenen Rechner ist 127.0.0.1 die zuverlaessigste Adresse --
        # die steht immer an letzter Stelle.
        local = urls_for(host, port, token)[-1]
        threading.Timer(0.6, lambda: webbrowser.open(local)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        TOKEN = ""
        AUTH = None
