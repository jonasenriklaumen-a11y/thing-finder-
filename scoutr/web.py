"""Weboberflaeche fuer scoutr -- derselbe Agent, nur im Browser.

Bewusst ohne Webframework: die Standardbibliothek reicht fuer eine lokale
Ein-Nutzer-Anwendung, und jede zusaetzliche Abhaengigkeit macht die
Installation komplizierter. Der Server laeuft nur auf dem eigenen Rechner.

Die Zwischenschritte gehen als Server-Sent Events an den Browser -- dieselben
Ereignisse, die im Terminal die "[Suche]"- und "[Lese]"-Zeilen erzeugen.
"""

from __future__ import annotations

import contextlib
import json
import queue
import secrets
import socket
import threading
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from scoutr import __version__
from scoutr.cache import Cache
from scoutr.config import (
    DEFAULT_ENV_PATH,
    Settings,
    api_key_name_for,
    find_env_file,
    get_settings,
    load_env,
    reset_settings_cache,
    write_env_file,
)

UI_FILE = Path(__file__).with_name("webui.html")

#: Alles, was sich auch in `scoutr setup` einstellen laesst.
SETTING_KEYS: tuple[str, ...] = (
    "SCOUTR_MODEL",
    "SCOUTR_VISION_MODEL",
    "SCOUTR_SUBAGENT_MODEL",
    "SCOUTR_API_BASE",
    "SCOUTR_SEARCH_BACKEND",
    "SCOUTR_SEARCH_ENGINES",
    "SCOUTR_SEARXNG_URL",
    "SCOUTR_LOCATION",
    "SCOUTR_LANG",
    "SCOUTR_COUNTRY",
    "SCOUTR_SUBAGENTS_AUTO",
    "SCOUTR_MAX_SUBAGENTS",
    "SCOUTR_SUBAGENT_BUDGET",
    "SCOUTR_SUBAGENT_PARALLEL",
    "SCOUTR_MAX_TOOL_CALLS",
    "SCOUTR_CONTEXT_TOKENS",
    "SCOUTR_PLANNER_TIMEOUT",
    "SCOUTR_ENABLE_PLAYWRIGHT",
)

#: Platzhalter im Formular -- ein leeres Key-Feld darf den Key nicht loeschen.
API_KEY_FIELD = "__API_KEY__"

#: Zugangswort fuer den Netzbetrieb. Leer = kein Schutz (nur lokal sinnvoll).
#: Wird von :func:`serve` gesetzt.
TOKEN: str = ""

#: Name des Cookies, in dem der Browser das Zugangswort behaelt.
TOKEN_COOKIE = "scoutr_token"

#: Wird gezeigt, wenn jemand ohne gueltiges Zugangswort anklopft.
DENIED_PAGE = """<!doctype html><html lang="de"><meta charset="utf-8">
<title>scoutr</title>
<body style="background:#0d0f0e;color:#e8ece9;font:15px/1.6 system-ui;
             display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center;max-width:34em;padding:20px">
<h1 style="color:#31c46b;font-size:20px">scoutr</h1>
<p>Diese Oberflaeche ist mit einem Zugangswort geschuetzt.</p>
<p style="color:#8b9590;font-size:13px">Nimm die vollstaendige Adresse, die beim
Start im Terminal steht &mdash; die mit <code>?token=</code> am Ende.</p>
</div></body></html>"""

#: Dieselbe Uebersicht wie `/help` im Terminal, nur als Markdown.
HELP_MARKDOWN = """### Befehle

- `/location <ort>` — Ortsfilter fuer diese Sitzung (leer = aufheben)
- `/model <name>` — Modell wechseln, z. B. `openai/gpt-4o`
- `/image <pfad>` — Bild ansehen lassen und damit recherchieren (Datei oder Ordner)
- `/export html|md|csv` — die letzten Recherchen speichern
- `/history` — fruehere Recherchen
- `/notes` — Merkzettel
- `/clear` — Gespraechsverlauf verwerfen
- `/help` — diese Uebersicht

Dauerhaft aendern lassen sich Modell, Suche und Ort oben unter **Einstellungen**."""


class ChatSession:
    """Haelt den Agenten und serialisiert die Anfragen.

    Eine lokale Oberflaeche hat einen Nutzer; zwei gleichzeitige Anfragen
    wuerden sich nur den Gespraechsverlauf zerschiessen.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agent: Any = None
        self._settings: Settings | None = None

    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    def agent(self) -> Any:
        from scoutr.agent import Agent

        if self._agent is None:
            settings = self.settings()
            cache = Cache(settings.db_path, settings.cache_ttl_hours)
            self._agent = Agent(settings, cache=cache)
        return self._agent

    def reset(self) -> None:
        """Verwirft den Verlauf, behaelt aber den Merkzettel."""
        with self._lock:
            if self._agent is not None:
                self._agent.clear()

    def reload(self) -> None:
        """Nach dem Speichern neuer Einstellungen alles neu aufbauen."""
        with self._lock:
            if self._agent is not None:
                with contextlib.suppress(Exception):
                    self._agent.close()
            self._agent = None
            self._settings = None
            reset_settings_cache()

    def busy(self) -> bool:
        """Laeuft gerade eine Anfrage?"""
        if self._lock.acquire(blocking=False):
            self._lock.release()
            return False
        return True

    def ask(self, message: str, emit: Any) -> Any:
        """Fuehrt eine Anfrage aus und meldet jeden Zwischenschritt an *emit*.

        Das abschliessende "done" kommt vom Agenten selbst -- hier noch eines
        zu senden wuerde die Oberflaeche zweimal abschliessen lassen.
        """
        # Im Netzbetrieb sitzen mehrere Geraete an derselben Sitzung. Wer
        # wartet, soll das sehen und nicht vor einem stummen Fenster sitzen.
        if self.busy():
            emit("waiting", {"reason": "Ein anderes Geraet fragt gerade -- ich bin gleich da."})
        with self._lock:
            agent = self.agent()
            agent.on_event = lambda name, payload: emit(name, payload)
            agent.toolbox.on_event = agent.on_event
            try:
                if message.startswith("/image"):
                    message = self._image_question(agent, message)
                return agent.ask(message, stream=True)
            finally:
                agent.on_event = None
                agent.toolbox.on_event = None

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
        from scoutr.config import model_problem

        command, _, argument = line[1:].partition(" ")
        command = command.lower().strip()
        argument = argument.strip()

        with self._lock:
            if command == "help":
                return {"text": HELP_MARKDOWN}

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

    def _cache(self) -> Cache:
        settings = self.settings()
        return Cache(settings.db_path, settings.cache_ttl_hours)

    def _export(self, fmt: str) -> str:
        """Exportiert die letzten Recherchen -- wie `scoutr export`."""
        from scoutr.export import Turn, export

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

    Im Terminal fragt scoutr bei mehreren Bildern nach; hier nimmt er das
    neueste und sagt es dazu -- eine Rueckfrage mitten im Stream waere
    umstaendlicher als ein zweiter `/image`-Aufruf mit genauem Pfad.
    """
    from scoutr.cli import IMAGE_SUFFIXES, _images_in

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


SESSION = ChatSession()


def current_values() -> dict[str, str]:
    """Aktuelle Einstellungen als Formularwerte."""
    settings = SESSION.settings()
    return {
        "SCOUTR_MODEL": settings.model,
        "SCOUTR_VISION_MODEL": settings.vision_model,
        "SCOUTR_SUBAGENT_MODEL": settings.subagent_model,
        "SCOUTR_API_BASE": settings.api_base,
        "SCOUTR_SEARCH_BACKEND": settings.search_backend,
        "SCOUTR_SEARCH_ENGINES": settings.search_engines,
        "SCOUTR_SEARXNG_URL": settings.searxng_url,
        "SCOUTR_LOCATION": settings.location,
        "SCOUTR_LANG": settings.lang,
        "SCOUTR_COUNTRY": settings.country,
        "SCOUTR_SUBAGENTS_AUTO": "true" if settings.subagents_auto else "false",
        "SCOUTR_MAX_SUBAGENTS": str(settings.max_subagents),
        "SCOUTR_SUBAGENT_BUDGET": str(settings.subagent_budget),
        "SCOUTR_SUBAGENT_PARALLEL": str(settings.subagent_parallel),
        "SCOUTR_MAX_TOOL_CALLS": str(settings.max_tool_calls),
        "SCOUTR_CONTEXT_TOKENS": str(settings.context_tokens),
        "SCOUTR_PLANNER_TIMEOUT": str(int(settings.planner_timeout)),
        "SCOUTR_ENABLE_PLAYWRIGHT": "true" if settings.enable_playwright else "false",
    }


def save_values(payload: dict[str, Any]) -> Path:
    """Schreibt die Formularwerte in die `.env` und laedt neu."""
    values = {
        key: str(payload.get(key, "")).strip() for key in SETTING_KEYS if key in payload
    }
    api_key = str(payload.get(API_KEY_FIELD, "")).strip()
    if api_key:
        # Nur setzen, wenn wirklich etwas eingetippt wurde -- ein leeres Feld
        # bedeutet "unveraendert", nicht "loeschen".
        key_name = api_key_name_for(values.get("SCOUTR_MODEL", "") or SESSION.settings().model)
        if key_name:
            values[key_name] = api_key
    target = find_env_file() or DEFAULT_ENV_PATH
    written = write_env_file(values, target)
    # In einem laufenden Prozess gewinnen bereits gesetzte Umgebungsvariablen
    # ueber die .env. Ohne override laege die neue Einstellung zwar in der
    # Datei, waere aber erst nach einem Neustart aktiv -- die Oberflaeche
    # meldet aber "sofort aktiv", und das soll auch stimmen.
    load_env(written, override=True)
    SESSION.reload()
    return written


class Handler(BaseHTTPRequestHandler):
    """Sehr kleiner Router -- eine Handvoll Endpunkte."""

    server_version = f"scoutr/{__version__}"

    def log_message(self, fmt: str, *args: Any) -> None:
        return  # keine Zugriffsprotokolle in der Konsole

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

    def _authorized(self) -> bool:
        """Prueft das Zugangswort -- aus Adresse, Cookie oder Kopfzeile.

        Ohne gesetztes TOKEN ist alles erlaubt; so bleibt der rein lokale
        Betrieb genauso einfach wie vorher.
        """
        if not TOKEN:
            return True
        for candidate in (
            self._query_token(),
            self._cookie_token(),
            self.headers.get("X-Scoutr-Token") or "",
        ):
            if candidate and secrets.compare_digest(candidate, TOKEN):
                return True
        return False

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
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, ValueError):
            return {}

    # -- Routen -----------------------------------------------------------
    # Namen von BaseHTTPRequestHandler vorgegeben.
    def do_GET(self) -> None:
        if not self._authorized():
            self._deny()
            return
        route = self._route()
        if route in ("/", "/index.html"):
            self._send_ui()
        elif route == "/api/config":
            settings = SESSION.settings()
            self._json(
                {
                    "version": __version__,
                    "values": current_values(),
                    "key_name": api_key_name_for(settings.model),
                    "env_path": str(settings.env_path or DEFAULT_ENV_PATH),
                    "problems": settings.missing_requirements(),
                }
            )
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

    def do_POST(self) -> None:
        if not self._authorized():
            self._deny()
            return
        route = self._route()
        if route == "/api/chat":
            self._chat()
        elif route == "/api/clear":
            SESSION.reset()
            self._json({"ok": True})
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
            except Exception as exc:
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
                return
            self._json({"ok": True, "path": str(written)})
        else:
            self._json({"error": "unbekannter Pfad"}, 404)

    def _send_ui(self) -> None:
        """Liefert die Oberflaeche und legt das Zugangswort als Cookie ab.

        So braucht nur der erste Aufruf die lange Adresse; danach kennt der
        Browser das Wort von selbst.
        """
        body = UI_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if TOKEN:
            self.send_header(
                "Set-Cookie",
                f"{TOKEN_COOKIE}={TOKEN}; Path=/; Max-Age=2592000; SameSite=Strict",
            )
        self.end_headers()
        self.wfile.write(body)

    def _chat(self) -> None:
        """Fuehrt die Anfrage aus und streamt die Ereignisse als SSE."""
        message = str(self._read_json().get("message", "")).strip()
        if not message:
            self._json({"error": "leere Nachricht"}, 400)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        seen_done = threading.Event()

        def emit(name: str, payload: dict[str, Any]) -> None:
            # Der Renderer im Terminal nennt den Text "text"; im Browser
            # heisst das Ereignis "chunk", damit das Frontend es direkt
            # anhaengen kann.
            kind = "chunk" if name == "answer_chunk" else name
            if kind == "done":
                seen_done.set()
            events.put({"type": kind, **payload})

        def run() -> None:
            try:
                SESSION.ask(message, emit)
            except Exception as exc:
                events.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                # Ohne ein "done" bliebe im Browser der blinkende Cursor
                # stehen -- die Oberflaeche waere scheinbar haengen.
                if not seen_done.is_set():
                    events.put({"type": "done"})
                events.put(None)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()

        while True:
            event = events.get()
            if event is None:
                break
            try:
                self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # Tab geschlossen -- die Recherche laeuft im Hintergrund aus.
                break


def lan_address() -> str:
    """Die eigene Adresse im lokalen Netz.

    Der UDP-"Verbindungsaufbau" schickt kein einziges Paket -- er laesst nur
    das Betriebssystem die Route waehlen und verraet damit, welche der
    Netzwerkkarten nach draussen zeigt. Das ist zuverlaessiger als
    ``gethostbyname(gethostname())``, das auf vielen Linux-Systemen
    127.0.1.1 liefert.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Kein Paket geht raus -- connect() waehlt bei UDP nur die Route.
        probe.connect(("8.8.8.8", 9))
        address = probe.getsockname()[0]
    except OSError:
        address = ""
    finally:
        probe.close()
    if address and not address.startswith("127."):
        return address
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidate = info[4][0]
            if not candidate.startswith("127."):
                return candidate
    return ""


def is_public_host(host: str) -> bool:
    """Bindet *host* an mehr als nur den eigenen Rechner?"""
    return host not in ("127.0.0.1", "localhost", "::1", "")


def new_token() -> str:
    """Kurzes Zugangswort -- muss auf einem Handy tippbar bleiben."""
    return secrets.token_urlsafe(9)


def urls_for(host: str, port: int, token: str = "") -> list[str]:
    """Alle Adressen, unter denen die Oberflaeche erreichbar ist."""
    suffix = f"?token={token}" if token else ""
    if not is_public_host(host):
        return [f"http://127.0.0.1:{port}/{suffix}"]
    hosts = [f"127.0.0.1:{port}"]
    if host == "0.0.0.0":  # alle Netzwerkkarten -- ausdruecklich per --lan gewaehlt
        address = lan_address()
        if address:
            hosts.insert(0, f"{address}:{port}")
    else:
        hosts.insert(0, f"{host}:{port}")
    return [f"http://{item}/{suffix}" for item in hosts]


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    token: str = "",
) -> None:
    """Startet die Oberflaeche und blockiert, bis Strg+C kommt.

    *token* schuetzt den Zugang; ohne ist die Oberflaeche fuer jeden offen,
    der die Adresse erreicht. Fuer den Netzbetrieb setzt die Kommandozeile
    deshalb von sich aus eines.
    """
    global TOKEN

    TOKEN = token
    server = ThreadingHTTPServer((host, port), Handler)
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
