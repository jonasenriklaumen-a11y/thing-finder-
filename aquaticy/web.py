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
import json
import os
import queue
import secrets
import socket
import threading
import time
import webbrowser
from dataclasses import replace
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from dotenv import dotenv_values

from aquaticy import __version__
from aquaticy.auth import NORMAL_TOKEN_LIMIT, Account, AuthStore, RateLimiter, pro_code_for
from aquaticy.cache import Cache
from aquaticy.config import (
    DEFAULT_ENV_PATH,
    SEARCH_BACKEND_KEYS,
    Settings,
    api_key_name_for,
    base_fits,
    find_env_file,
    get_settings,
    load_env,
    reset_settings_cache,
    resolve_model,
    selected_vision_model,
    suggest_model,
    write_env_file,
)
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

    def add(self, event: dict[str, Any]) -> None:
        with self._cond:
            if len(self.events) < MAX_RUN_EVENTS:
                self.events.append(event)
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
    """Haelt den laufenden Turn. Es gibt immer nur einen -- die Sitzung
    reicht die Anfragen ohnehin nacheinander durch."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: Run | None = None

    def start(self, question: str) -> Run:
        with self._lock:
            self.current = Run(secrets.token_hex(8), question)
            return self.current

    def latest(self) -> Run | None:
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


def forget_strong_models() -> None:
    """Nach einer Aenderung an Modell oder Schluesseln neu nachsehen."""
    _strong_cache.clear()

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
)

#: Zahlenfelder mit dem Bereich, in dem sie sinnvoll sind. Geprueft wird
#: hier und nicht erst in `config.py`: dort faellt ein unsinniger Wert nur
#: still auf den Standard zurueck -- das Formular meldet dann "gespeichert",
#: und die Einstellung tut trotzdem nichts. Das ist schlimmer als eine
#: Fehlermeldung, weil man es erst Tage spaeter merkt.
NUMBERS: dict[str, tuple[int, int]] = {
    "AQUATICY_SEARCH_VARIANTS": (1, 10),
    "AQUATICY_MAX_SUBAGENTS": (1, 12),
    "AQUATICY_SUBAGENT_BUDGET": (1, 40),
    "AQUATICY_SUBAGENT_PARALLEL": (1, 12),
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
AUTH_LIMIT = RateLimiter(attempts=8, window_seconds=60)
REQUEST_LIMIT = RateLimiter(attempts=240, window_seconds=60)

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
}
_INT_SETTINGS = {
    "AQUATICY_SEARCH_VARIANTS": "search_variants",
    "AQUATICY_MAX_SUBAGENTS": "max_subagents",
    "AQUATICY_SUBAGENT_BUDGET": "subagent_budget",
    "AQUATICY_SUBAGENT_PARALLEL": "subagent_parallel",
    "AQUATICY_MAX_TOOL_CALLS": "max_tool_calls",
    "AQUATICY_CONTEXT_TOKENS": "context_tokens",
    "AQUATICY_PLANNER_TIMEOUT": "planner_timeout",
}


def _profile_settings(profile: Path, plan: str) -> Settings:
    """Kopiert die Servervorgaben und legt die Werte eines Kontos darueber."""
    base = get_settings()
    settings = replace(
        base,
        data_dir=profile,
        env_path=profile / ".env",
        api_keys=dict(base.api_keys),
        search_keys=dict(base.search_keys),
    )
    raw = {
        str(key): str(value or "")
        for key, value in dotenv_values(settings.env_path).items()
        if key
    }
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
    for key in ("MISTRAL_API_KEY", "NVIDIA_NIM_API_KEY", "AQUATICY_API_KEY"):
        if raw.get(key):
            settings.api_keys[key] = raw[key]
    for key in SEARCH_BACKEND_KEYS.values():
        if key and raw.get(key):
            settings.search_keys[key] = raw[key]
    if plan != "pro":
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
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings

#: Dieselbe Uebersicht wie `/help` im Terminal, nur als Markdown.
HELP_MARKDOWN = """### Befehle

- `/location <ort>` — Ortsfilter fuer diese Sitzung (leer = aufheben)
- `/model <name>` — Modell wechseln, z. B. `mistral/mistral-large-latest`
- `/max <frage>` — im Pro-Modus mit voller Mannschaft recherchieren
- `/image <pfad>` — Bild ansehen lassen und damit recherchieren (Datei oder Ordner)
- `/export html|md|csv` — die letzten Recherchen speichern
- `/history` — fruehere Recherchen
- `/notes` — Merkzettel
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

    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = (
                _profile_settings(self.profile, self.plan)
                if self.profile is not None
                else get_settings()
            )
        return self._settings

    @property
    def plan(self) -> str:
        return self.account.plan if self.account is not None else "pro"

    @property
    def pro(self) -> bool:
        return self.plan == "pro"

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
                with contextlib.suppress(Exception):
                    entries = cache.chat_history(weiter)
                    self._agent.resume(
                        weiter, [(entry.question, entry.answer) for entry in entries]
                    )
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
            self.agent().resume(
                session_id, [(entry.question, entry.answer) for entry in entries]
            )
        return {
            "session_id": session_id,
            "title": entries[0].question,
            "turns": [
                {
                    "question": entry.question,
                    "answer": entry.answer,
                    "products": entry.meta.get("products", []),
                    "visuals": entry.meta.get("visuals", []),
                }
                for entry in entries
            ],
        }

    def reload(self) -> None:
        """Nach dem Speichern neuer Einstellungen alles neu aufbauen.

        Die Werkstatt gehoert dazu: haette jemand ihre Grenzen geaendert,
        arbeitete die laufende sonst noch mit den alten weiter.

        Der Chat bleibt derselbe. Wer waehrend eines Gespraechs das Modell
        wechselt, will ein anderes Modell -- nicht ein anderes Gespraech.
        """
        with self._lock:
            old_settings = self._settings
            if self._agent is not None:
                self._carry_over = str(getattr(self._agent, "session_id", ""))
                with contextlib.suppress(Exception):
                    self._agent.close()
            self._agent = None
            self._settings = None
            if self.profile is None:
                reset_settings_cache()
            with contextlib.suppress(Exception):
                from aquaticy.sandbox import forget_shared

                forget_shared(old_settings)

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
        with contextlib.suppress(Exception):
            from aquaticy import sandbox as werkstatt

            antwort = werkstatt.shared(self.settings()).put_bytes(f"eingang/{schlicht}", data)
            if isinstance(antwort, dict) and antwort.get("written"):
                return str(antwort["written"])
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
            path = self._store(name, data)
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
        folder = self.settings().data_dir / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{int(time.time() * 1000)}-{name}"
        target.write_bytes(data)
        _prune_uploads(folder)
        return target

    def _image_question(self, agent: Any, line: str) -> str:
        """Macht aus `/image <pfad>` eine Frage, die das Bild beschreibt."""
        argument = line[len("/image") :].strip()
        if not argument:
            raise ValueError("Nutzung: /image pfad/zum/bild.jpg (oder ein Ordner)")
        image = resolve_image(Path(argument).expanduser())
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
                notes = self._cache().list_notes()
                if not notes:
                    return {"text": "Der Merkzettel ist leer. Sag im Chat einfach *merk dir …*"}
                lines = "\n".join(f"- {note.text}" for note in notes)
                return {"text": f"### Merkzettel\n{lines}"}

            if command == "history":
                entries = self._cache().recent_history(limit=15)
                if not entries:
                    return {"text": "Noch keine Recherchen im Verlauf."}
                lines = "\n".join(f"- {entry.question}" for entry in entries)
                return {"text": f"### Frueher gefragt\n{lines}"}

            if command == "export":
                return {"text": self._export(argument or "html")}

            if command in ("quit", "exit", "q"):
                return {"text": "Im Browser reicht es, das Fenster zu schliessen."}

        return {"text": f"Unbekannter Befehl `/{command}` — `/help` zeigt alle."}

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
            f"### Hochgeladen\n{len(files)} Dateien, zusammen {total}.\n\n{lines}"
            "\n\nAlles loeschen: `/uploads clear`"
        )

    def _cache(self) -> Cache:
        settings = self.settings()
        return Cache(settings.db_path, settings.cache_ttl_hours)

    def _export(self, fmt: str) -> str:
        """Exportiert die letzten Recherchen -- wie `aquaticy export`."""
        from aquaticy.export import Turn, export

        entries = self._cache().recent_history(limit=5)
        if not entries:
            return "Noch nichts zu exportieren — stell erst eine Frage."
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
            path = export(turns, fmt, directory=Path.cwd())
        except ValueError as exc:
            return str(exc)
        return f"Gespeichert: `{path}`"


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
    if account.id in USER_SCHEDULERS:
        return
    from aquaticy.jobs import Scheduler

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
        "AQUATICY_RPM": os.environ.get("AQUATICY_RPM", "").strip(),
        "AQUATICY_PARALLEL_CALLS": os.environ.get("AQUATICY_PARALLEL_CALLS", "").strip(),
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
    if not session.pro and (pro_integration or plus_workshop):
        raise ValueError(
            "LAN-Suche, Home Assistant, Lagerverwaltung und die Plus-Werkstatt "
            "brauchen ein Pro-Konto."
        )
    values = {
        key: str(payload.get(key, "")).strip() for key in SETTING_KEYS if key in payload
    }
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
    api_key = str(payload.get(API_KEY_FIELD, "")).strip()
    if api_key:
        # Nur setzen, wenn wirklich etwas eingetippt wurde -- ein leeres Feld
        # bedeutet "unveraendert", nicht "loeschen".
        key_name = api_key_name_for(values.get("AQUATICY_MODEL", "") or session.settings().model)
        if key_name:
            values[key_name] = api_key
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
            values[backend_key_name] = search_key
    target = (
        session.settings().env_path
        if session.profile is not None
        else find_env_file() or DEFAULT_ENV_PATH
    )
    written = write_env_file(values, target)
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
    try:
        stand = ui_state().read()
    except Exception:  # pragma: no cover - eine Seite ohne Zustand ist besser als keine
        from aquaticy.uistate import defaults

        stand = defaults()

    attrs = ""
    if stand.get("theme") in ("light", "dark"):
        attrs += f' data-theme="{stand["theme"]}"'
    if stand.get("palette"):
        attrs += f' data-palette="{stand["palette"]}"'
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
    boot = (
        f"window.__AQUATICY_STATE__ = {roh};"
        f"window.__AQUATICY_VERSION__ = {json.dumps(__version__)};"
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
        except Exception as exc:
            print(f"  [Fehler] {self.command} {self.path}: {type(exc).__name__}: {exc}")
            if not self.responded:
                with contextlib.suppress(OSError):
                    self._json({"error": "Der Server konnte die Anfrage nicht verarbeiten."}, 500)
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
        return str(self.client_address[0] if self.client_address else "unknown")

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
        return AUTH.session_account(self._cookie(AUTH_COOKIE), self._device())

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
        length = int(self.headers.get("Content-Length") or 0)
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
            return json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, ValueError):
            return {}  # kaputtes JSON ist eine leere Anfrage, kein Absturz

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
            self._json({"ok": JobStore(settings.db_path).delete(nummer)})
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

    def _job_edit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Auftrag anlegen, anhalten, weiterlaufen lassen oder sofort ausfuehren."""
        from aquaticy.jobs import JobStore, run_job

        settings = SESSION.settings()
        store = JobStore(settings.db_path)
        action = str(payload.get("action", "add")).strip().lower()

        if action == "add":
            try:
                kind = str(payload.get("kind", "research")).strip().lower()
                if kind == "visual" and not selected_vision_model(settings):
                    return {
                        "ok": False,
                        "error": "Die Bildbeobachtung braucht ein Vision-Modell unter Modell.",
                    }
                job = store.add(
                    str(payload.get("question", "")),
                    rhythm=str(payload.get("rhythm", "daily")),
                    hour=int(payload.get("hour", 8) or 0),
                    minute=int(payload.get("minute", 0) or 0),
                    weekday=int(payload.get("weekday", 0) or 0),
                    structured=bool(payload.get("structured", True)),
                    kind=kind,
                    source_url=str(payload.get("source_url", "")),
                )
            except (ValueError, TypeError) as exc:
                return {"ok": False, "error": str(exc)}
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
            return {"ok": store.delete(nummer)}
        if action == "run":
            # Sofort ausfuehren laeuft im Hintergrund: eine Recherche dauert
            # Minuten, so lange darf keine Anfrage offen stehen.
            job = store.get(nummer)
            if job is None:
                return {"ok": False, "error": "Diesen Auftrag gibt es nicht."}

            def sofort() -> None:
                state, chat = run_job(job, settings)
                store.note_run(job.id, state, chat)
                if state == "erfüllt":
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
            from aquaticy.fetch import Fetcher

            settings = SESSION.settings()
            with Fetcher(
                user_agent=settings.user_agent,
                timeout=settings.fetch_timeout,
                delay_seconds=settings.request_delay_seconds,
                enable_browser=settings.enable_playwright,
            ) as fetcher:
                visual, error = fetcher.load_public_visual(target)
            if visual is None:
                self._json({"error": error or "Das Bild ist nicht erreichbar."}, 404)
                return
            self._send(200, visual.content, visual.content_type)
        elif route == "/api/account":
            account = self._account()
            if account is None:
                self._json({"error": "Bitte melde dich an."}, 401)
                return
            from aquaticy.usage import UsageLog

            used = UsageLog(SESSION.settings().db_path).total_tokens()
            self._json(
                {
                    "email": account.email,
                    "username": account.username,
                    "plan": account.plan,
                    "tokens_used": used,
                    "token_limit": None if account.pro else NORMAL_TOKEN_LIMIT,
                    "tokens_left": None if account.pro else max(0, NORMAL_TOKEN_LIMIT - used),
                }
            )
        elif route == "/google":
            self._google_return()
        elif route == "/api/config":
            settings = SESSION.settings()
            self._json(
                {
                    "version": __version__,
                    "account": (
                        {
                            "email": SESSION.account.email,
                            "username": SESSION.account.username,
                            "plan": SESSION.plan,
                            "pro": SESSION.pro,
                        }
                        if SESSION.account is not None
                        else {"email": "lokal", "username": "", "plan": "pro", "pro": True}
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
                }
            )
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
                    "chats": (
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
            self._json({"title": title, "markdown": chat_markdown(title, entries)})
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
                    "files": box.list_files() if box.alive else [],
                }
            )
        elif route == "/api/werkstatt/datei":
            self._workshop_file()
        elif route == "/api/jobs":
            from aquaticy.jobs import RHYTHM_NAMES, JobStore

            store = JobStore(SESSION.settings().db_path)
            self._json(
                {
                    "jobs": [job.as_dict() for job in store.all_jobs()],
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
            zweck = (parse_qs(urlsplit(self.path).query).get("purpose") or [""])[0].strip()
            self._json(
                {
                    "models": available_models(SESSION.settings()),
                    # Fuer den Code- und den Pro-Modus: nur die staerksten.
                    "strong": strong_models(3, purpose=zweck),
                }
            )
        elif route == "/api/memory":
            # Abgeschaltet heisst abgeschaltet: dann wird auch nichts gezeigt.
            if not SESSION.settings().memory_enabled:
                self._json({"enabled": False, "entries": []})
            else:
                store = SESSION.memory()
                self._json(
                    {
                        "enabled": True,
                        "usage": store.usage(),
                        "entries": [entry.as_dict() for entry in store.all_entries(limit=300)],
                    }
                )
        elif route == "/api/usage":
            from aquaticy.usage import UsageLog

            settings = SESSION.settings()
            self._json(UsageLog(settings.db_path).summary())
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
                payload["storage"] = SESSION.memory().usage()
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
            if not SESSION.pro:
                self._json({"ok": False, "error": "Home Assistant braucht Pro."}, 403)
                return
            self._json(self._ha_probe(self._read_json()))
        elif route == "/api/probe":
            self._json(self._probe(self._read_json()))
        elif route == "/api/google":
            self._json(self._google(self._read_json()))
        elif route == "/api/storage":
            if not SESSION.pro:
                self._json({"ok": False, "error": "Die Lagerverwaltung braucht Pro."}, 403)
                return
            self._json(self._storage_probe(self._read_json()))
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
            forget_strong_models()
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
        settings = SESSION.settings()
        if denied:
            self._google_page(False, f"Google hat abgelehnt: {denied}")
            return
        if not code:
            self._google_page(False, "Google hat keinen Code mitgeschickt.")
            return
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
            return {"ok": True, "title": cache.rename_chat(session_id, str(payload.get("title")))}
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
                return {
                    "ok": True,
                    "url": consent_url(client_id, redirect, write=bool(darf_aendern)),
                    "redirect": redirect,
                    "write": bool(darf_aendern),
                }
            except GoogleError as exc:
                return {"ok": False, "error": str(exc)}

        if action == "finish":
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
        from aquaticy.probe import check_llm, check_search

        settings = SESSION.settings()
        model = fix_model_id(str(payload.get("AQUATICY_MODEL", "")).strip()) or settings.model
        api_key = str(payload.get(API_KEY_FIELD, "")).strip()
        if not api_key:
            name = api_key_name_for(model)
            api_key = os.environ.get(name, "") if name else ""
        api_base = str(payload.get("AQUATICY_API_BASE", settings.api_base) or "").strip()
        if api_base and not base_fits(api_base, model):
            # Dieselbe Regel wie im Betrieb -- sonst testet man etwas anderes,
            # als spaeter laeuft, und der Test luegt.
            api_base = ""

        backend = str(payload.get("AQUATICY_SEARCH_BACKEND", "")).strip() or settings.search_backend
        search_key = str(payload.get(SEARCH_KEY_FIELD, "")).strip()
        if not search_key:
            name = SEARCH_BACKEND_KEYS.get(backend, "")
            search_key = os.environ.get(name, "") if name else ""
        engines = str(payload.get("AQUATICY_SEARCH_ENGINES", settings.search_engines) or "").strip()
        instance = str(payload.get("AQUATICY_SEARXNG_URL", settings.searxng_url) or "").strip()

        llm_ok, llm_msg = check_llm(model, api_key, api_base)
        search_ok, search_msg = check_search(backend, search_key, engines, instance)
        return {
            "ok": llm_ok and search_ok,
            "llm": {"ok": llm_ok, "message": llm_msg, "model": model},
            "search": {"ok": search_ok, "message": search_msg, "backend": backend},
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

        settings = SESSION.settings()
        url = str(payload.get("url") or settings.ha_url).strip()
        token = str(payload.get("token") or "").strip() or settings.ha_token
        if not url or not token:
            return {"ok": False, "error": "Adresse und Token werden beide gebraucht."}
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
        if not SESSION.pro:
            from aquaticy.usage import UsageLog

            used = UsageLog(SESSION.settings().db_path).total_tokens()
            if used >= NORMAL_TOKEN_LIMIT:
                self._json(
                    {
                        "error": (
                            "Dein Kontingent von 400.000 Token ist aufgebraucht. "
                            "Mit einem Pro-Konto gibt es kein Tokenlimit."
                        ),
                        "code": "token_limit",
                        "tokens_used": used,
                        "token_limit": NORMAL_TOKEN_LIMIT,
                    },
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

        # Der Lauf gehoert ab hier dem Server, nicht der Verbindung. Reisst
        # sie ab, laeuft er weiter und kann spaeter zu Ende gesehen werden.
        session = SESSION.current() if isinstance(SESSION, SessionProxy) else SESSION
        lauf = current_runs().start(message)
        seen_done = threading.Event()

        def emit(name: str, payload: dict[str, Any]) -> None:
            # Der Renderer im Terminal nennt den Text "text"; im Browser
            # heisst das Ereignis "chunk", damit das Frontend es direkt
            # anhaengen kann.
            kind = "chunk" if name == "answer_chunk" else name
            if kind == "done":
                seen_done.set()
            lauf.add({"type": kind, **payload})

        def run() -> None:
            previous = getattr(_REQUEST, "session", None)
            _REQUEST.session = session
            try:
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
        """Haengt sich an den laufenden Turn -- oder sagt, dass es keinen gibt."""
        lauf = current_runs().latest()
        if lauf is None:
            self._json({"error": "kein Lauf"}, 404)
            return
        roh = (parse_qs(urlsplit(self.path).query).get("since") or ["0"])[0]
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

    *token* schuetzt den Zugang; ohne ist die Oberflaeche fuer jeden offen,
    der die Adresse erreicht. Fuer den Netzbetrieb setzt die Kommandozeile
    deshalb von sich aus eines.
    """
    global AUTH, TOKEN

    TOKEN = token
    data_dir = get_settings().data_dir
    code = pro_code_for(data_dir)
    AUTH = AuthStore(data_dir, code)
    print(f"  Pro-Code: {code} (9 Zeichen, geheim halten)")
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
