"""Weboberflaeche fuer cortex -- derselbe Agent, nur im Browser.

Bewusst ohne Webframework: die Standardbibliothek reicht fuer eine lokale
Ein-Nutzer-Anwendung, und jede zusaetzliche Abhaengigkeit macht die
Installation komplizierter. Der Server laeuft nur auf dem eigenen Rechner.

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
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from cortex import __version__
from cortex.cache import Cache
from cortex.config import (
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
    suggest_model,
    write_env_file,
)

UI_FILE = Path(__file__).with_name("webui.html")

#: Standardport der Oberflaeche. Steht hier, weil auch die
#: Google-Rueckleitadresse ihn braucht.
DEFAULT_PORT = 8765

#: So oft geht mindestens ein Byte raus, solange eine Anfrage laeuft.
#: Kurz genug, dass keine Zwischenstation die stille Leitung kappt.
HEARTBEAT_SECONDS = 10.0

#: Alles, was sich auch in `cortex setup` einstellen laesst.
SETTING_KEYS: tuple[str, ...] = (
    "CORTEX_MODEL",
    "CORTEX_VISION_MODEL",
    "CORTEX_SUBAGENT_MODEL",
    "CORTEX_API_BASE",
    "CORTEX_SEARCH_BACKEND",
    "CORTEX_SEARCH_ENGINES",
    "CORTEX_SEARCH_VARIANTS",
    "CORTEX_SEARXNG_URL",
    "CORTEX_LOCATION",
    "CORTEX_LANG",
    "CORTEX_COUNTRY",
    "CORTEX_SUBAGENTS_AUTO",
    "CORTEX_MAX_SUBAGENTS",
    "CORTEX_SUBAGENT_BUDGET",
    "CORTEX_SUBAGENT_PARALLEL",
    "CORTEX_MAX_TOOL_CALLS",
    "CORTEX_CONTEXT_TOKENS",
    "CORTEX_PLANNER_TIMEOUT",
    "CORTEX_ENABLE_PLAYWRIGHT",
    "CORTEX_HA_URL",
    "CORTEX_HA_CONTROL",
    "CORTEX_GOOGLE",
    "CORTEX_STORAGE_URL",
    "CORTEX_STORAGE_ACCESS",
    "CORTEX_LAN_ENABLED",
    "CORTEX_LAN_SUBNET",
    "CORTEX_MEMORY",
)

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
#: cortex auch fuer heruntergeladene PDFs zieht.
MAX_UPLOAD_BYTES = 25_000_000

#: So viele Dateien duerfen an einer Nachricht haengen.
MAX_UPLOADS = 5

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

#: So viel Text uebernimmt cortex aus einer hochgeladenen Datei. Mehr wuerde
#: das Kontextfenster sprengen, bevor die Recherche ueberhaupt anfaengt.
MAX_FILE_CHARS = 20_000

#: Zugangswort fuer den Netzbetrieb. Leer = kein Schutz (nur lokal sinnvoll).
#: Wird von :func:`serve` gesetzt.
TOKEN: str = ""

#: Name des Cookies, in dem der Browser das Zugangswort behaelt.
TOKEN_COOKIE = "cortex_token"

#: Wird gezeigt, wenn jemand ohne gueltiges Zugangswort anklopft.
DENIED_PAGE = """<!doctype html><html lang="de"><meta charset="utf-8">
<title>Cortex AI</title>
<body style="background:#0d0f0e;color:#e8ece9;font:15px/1.6 system-ui;
             display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center;max-width:34em;padding:20px">
<h1 style="color:#31c46b;font-size:20px">Cortex AI</h1>
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
- `/memory` — zeigen, was im Langzeitspeicher liegt
- `/uploads` — zeigen, was du hochgeladen hast
- `/uploads clear` — alle hochgeladenen Dateien loeschen
- `/forget` — den Langzeitspeicher leeren
- `/help` — diese Uebersicht

Dauerhaft aendern lassen sich Modell, Suche und Ort oben unter **Einstellungen**."""


class ChatSession:
    """Haelt den Agenten und serialisiert die Anfragen.

    Eine lokale Oberflaeche hat einen Nutzer; zwei gleichzeitige Anfragen
    wuerden sich nur den Gespraechsverlauf zerschiessen.
    """

    #: So lange wartet eine Rueckfrage auf eine Antwort. Laenger nicht: der
    #: Agent haelt derweil die Sitzung besetzt, und wer den Tab zumacht, soll
    #: sie nicht dauerhaft blockieren.
    ANSWER_TIMEOUT = 180.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agent: Any = None
        self._settings: Settings | None = None
        #: Antworten auf Rueckfragen. Nur eine Anfrage laeuft gleichzeitig,
        #: deshalb genuegt eine Schlange fuer die ganze Sitzung.
        self._answers: queue.Queue[str] = queue.Queue()
        #: Eine Einstellung wurde im Gespraech geaendert -- nach dem Durchlauf
        #: wird der Agent neu gebaut.
        self._reload_after = False

    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    def agent(self) -> Any:
        from cortex.agent import Agent

        if self._agent is None:
            settings = self.settings()
            cache = Cache(settings.db_path, settings.cache_ttl_hours)
            self._agent = Agent(settings, cache=cache)
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
                {"question": entry.question, "answer": entry.answer} for entry in entries
            ],
        }

    def reload(self) -> None:
        """Nach dem Speichern neuer Einstellungen alles neu aufbauen."""
        with self._lock:
            if self._agent is not None:
                with contextlib.suppress(Exception):
                    self._agent.close()
            self._agent = None
            self._settings = None
            reset_settings_cache()

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
        thinking: bool | None = None,
        recheck: bool | None = None,
    ) -> Any:
        """Fuehrt eine Anfrage aus und meldet jeden Zwischenschritt an *emit*.

        Das abschliessende "done" kommt vom Agenten selbst -- hier noch eines
        zu senden wuerde die Oberflaeche zweimal abschliessen lassen.
        """
        # Im Netzbetrieb sitzen mehrere Geraete an derselben Sitzung. Wer
        # wartet, soll das sehen und nicht vor einem stummen Fenster sitzen.
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
        with self._lock:
            self._drain_answers()
            agent = self.agent()
            try:
                agent.on_event = lambda name, payload: emit(name, payload)
                agent.toolbox.on_event = agent.on_event
                # Aendert Cortex im Gespraech eine Einstellung, muss der Agent
                # danach neu gebaut werden -- sonst arbeitet die naechste Frage
                # noch mit den alten Werten weiter.
                agent.toolbox.on_settings_changed = self._settings_dirty
                agent.set_ask_handler(self._ask_browser)
                if message.startswith("/image"):
                    message = self._image_question(agent, message)
                elif attachments:
                    context = self.attachments_text(agent, attachments, emit)
                    message = f"{context}\n\n{message}" if context else message
                return agent.ask(
                    message, stream=True, mode=mode, thinking=thinking, recheck=recheck
                )
            finally:
                agent.on_event = None
                agent.toolbox.on_event = None
                agent.toolbox.on_settings_changed = None
                if self._reload_after:
                    self._reload_after = False
                    self.reload()
                # Der Handler bleibt bestehen -- das Werkzeug soll auch in der
                # naechsten Runde angeboten werden.

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

    def attachments_text(self, agent: Any, attachments: list[dict[str, Any]], emit: Any) -> str:
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
            blocks.append(self._one_attachment(agent, name, data))
        return "\n\n".join(blocks)

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
            from cortex.fetch import extract_pdf_text

            text, title = extract_pdf_text(data)
            if not text:
                return (
                    f"[PDF {name}: kein Text enthalten -- vermutlich ein Scan. "
                    "Gescannte Seiten kann Cortex AI nicht lesen.]"
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
        from cortex.config import model_problem

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
        from cortex.memory import Memory

        settings = self.settings()
        return Memory(settings.db_path, settings.data_dir, settings.memory_key)

    def _memory_overview(self) -> str:
        """Was liegt im Speicher, und wie voll ist er?"""
        from cortex.memory import human_size

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
        from cortex.memory import human_size

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
        """Exportiert die letzten Recherchen -- wie `cortex export`."""
        from cortex.export import Turn, export

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

    Im Terminal fragt cortex bei mehreren Bildern nach; hier nimmt er das
    neueste und sagt es dazu -- eine Rueckfrage mitten im Stream waere
    umstaendlicher als ein zweiter `/image`-Aufruf mit genauem Pfad.
    """
    from cortex.cli import IMAGE_SUFFIXES, _images_in

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
        "CORTEX_MODEL": settings.model,
        "CORTEX_VISION_MODEL": settings.vision_model,
        "CORTEX_SUBAGENT_MODEL": settings.subagent_model,
        "CORTEX_API_BASE": settings.api_base,
        "CORTEX_SEARCH_BACKEND": settings.search_backend,
        "CORTEX_SEARCH_ENGINES": settings.search_engines,
        "CORTEX_SEARCH_VARIANTS": str(settings.search_variants),
        "CORTEX_SEARXNG_URL": settings.searxng_url,
        "CORTEX_LOCATION": settings.location,
        "CORTEX_LANG": settings.lang,
        "CORTEX_COUNTRY": settings.country,
        "CORTEX_SUBAGENTS_AUTO": "true" if settings.subagents_auto else "false",
        "CORTEX_MAX_SUBAGENTS": str(settings.max_subagents),
        "CORTEX_SUBAGENT_BUDGET": str(settings.subagent_budget),
        "CORTEX_SUBAGENT_PARALLEL": str(settings.subagent_parallel),
        "CORTEX_MAX_TOOL_CALLS": str(settings.max_tool_calls),
        "CORTEX_CONTEXT_TOKENS": str(settings.context_tokens),
        "CORTEX_PLANNER_TIMEOUT": str(int(settings.planner_timeout)),
        "CORTEX_ENABLE_PLAYWRIGHT": "true" if settings.enable_playwright else "false",
        "CORTEX_HA_URL": settings.ha_url,
        "CORTEX_HA_CONTROL": "true" if settings.ha_control else "false",
        "CORTEX_GOOGLE": "true" if settings.google_enabled else "false",
        "CORTEX_STORAGE_URL": settings.storage_url,
        "CORTEX_STORAGE_ACCESS": settings.storage_access,
        "CORTEX_LAN_ENABLED": "true" if settings.lan_enabled else "false",
        "CORTEX_LAN_SUBNET": settings.lan_subnet,
        "CORTEX_MEMORY": "true" if settings.memory_enabled else "false",
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
    from cortex.google import Google, TokenStore

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


def save_values(payload: dict[str, Any]) -> Path:
    """Schreibt die Formularwerte in die `.env` und laedt neu."""
    values = {
        key: str(payload.get(key, "")).strip() for key in SETTING_KEYS if key in payload
    }
    for key in ("CORTEX_MODEL", "CORTEX_VISION_MODEL", "CORTEX_SUBAGENT_MODEL"):
        if values.get(key):
            values[key] = fix_model_id(values[key])
    ha_token = str(payload.get(HA_TOKEN_FIELD, "")).strip()
    if ha_token:
        values["HA_TOKEN"] = ha_token
    api_key = str(payload.get(API_KEY_FIELD, "")).strip()
    if api_key:
        # Nur setzen, wenn wirklich etwas eingetippt wurde -- ein leeres Feld
        # bedeutet "unveraendert", nicht "loeschen".
        key_name = api_key_name_for(values.get("CORTEX_MODEL", "") or SESSION.settings().model)
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
        backend = values.get("CORTEX_SEARCH_BACKEND", "") or SESSION.settings().search_backend
        backend_key_name = SEARCH_BACKEND_KEYS.get(backend, "")
        if backend_key_name:
            values[backend_key_name] = search_key
    target = find_env_file() or DEFAULT_ENV_PATH
    written = write_env_file(values, target)
    # In einem laufenden Prozess gewinnen bereits gesetzte Umgebungsvariablen
    # ueber die .env. Ohne override laege die neue Einstellung zwar in der
    # Datei, waere aber erst nach einem Neustart aktiv -- die Oberflaeche
    # meldet aber "sofort aktiv", und das soll auch stimmen.
    load_env(written, override=True)
    SESSION.reload()
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

    server_version = f"cortex/{__version__}"

    def log_message(self, fmt: str, *args: Any) -> None:
        return  # keine Zugriffsprotokolle in der Konsole

    def send_response(self, code: int, message: str | None = None) -> None:
        # Merken, dass die Antwort laeuft -- danach kann kein Fehlerblatt mehr
        # hinterhergeschickt werden (siehe _guarded).
        self.responded = True
        super().send_response(code, message)

    def _guarded(self, handler: Any) -> None:
        """Faengt alles ab, was in einer Route schiefgeht.

        Ohne das druckt die Standardbibliothek eine seitenlange Ablaufverfolgung
        in die Konsole, und der Browser bekommt gar nichts -- er versucht es
        dann immer wieder. Eine Zeile im Terminal und ein 500 sind brauchbarer.
        """
        self.responded = False
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
                    self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

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
        return any(
            candidate and same_secret(candidate, TOKEN)
            for candidate in (
                self._query_token(),
                self._cookie_token(),
                self.headers.get("X-Cortex-Token") or "",
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

    def _get(self) -> None:
        if not self._authorized():
            self._deny()
            return
        route = self._route()
        if route in ("/", "/index.html"):
            self._send_ui()
        elif route == "/google":
            self._google_return()
        elif route == "/api/config":
            settings = SESSION.settings()
            self._json(
                {
                    "version": __version__,
                    "values": current_values(),
                    "key_name": api_key_name_for(settings.model),
                    "search_key_name": SEARCH_BACKEND_KEYS.get(settings.search_backend, ""),
                    "env_path": str(settings.env_path or DEFAULT_ENV_PATH),
                    "problems": settings.missing_requirements(),
                    # Schluessel selbst gehen nie an den Browser -- nur, ob welche da sind.
                    "ha_connected": bool(settings.ha_url and settings.ha_token),
                    "search_key_set": bool(settings.search_api_key),
                    "google": google_state(settings),
                }
            )
        elif route == "/api/chats":
            settings = SESSION.settings()
            cache = Cache(settings.db_path, settings.cache_ttl_hours)
            self._json(
                {
                    "chats": cache.recent_chats(limit=40),
                    "current": SESSION.chat_id(),
                }
            )
        elif route == "/api/models":
            from cortex.system import available_models

            self._json({"models": available_models(SESSION.settings())})
        elif route == "/api/system":
            from cortex.system import snapshot

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
        if route == "/api/chat":
            self._chat()
        elif route == "/api/clear":
            SESSION.reset()
            self._json({"ok": True, "current": SESSION.chat_id()})
        elif route == "/api/open":
            wanted = str(self._read_json().get("session_id", "")).strip()
            if not wanted:
                self._json({"ok": False, "error": "keine Chat-Kennung"}, 400)
                return
            self._json({"ok": True, **SESSION.open_chat(wanted)})
        elif route == "/api/ha":
            self._json(self._ha_probe(self._read_json()))
        elif route == "/api/probe":
            self._json(self._probe(self._read_json()))
        elif route == "/api/google":
            self._json(self._google(self._read_json()))
        elif route == "/api/storage":
            self._json(self._storage_probe(self._read_json()))
        elif route == "/api/chat-edit":
            self._json(self._chat_edit(self._read_json()))
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
            except Exception as exc:
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
                return
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

        from cortex.google import GoogleError, exchange_code

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
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Cortex AI</title>
<style>body{{font:15px/1.6 system-ui,sans-serif;margin:0;display:grid;place-items:center;
min-height:100vh;background:#faf9f6;color:#26241f}}
main{{max-width:30em;padding:32px;text-align:center}}
h1{{color:{colour};font-size:20px;margin:0 0 12px}}
p{{margin:0 0 8px;color:#57534a}}</style></head><body><main>
<h1>{title}</h1><p>{escape(message)}</p>
<p>Du kannst dieses Fenster schliessen und zu Cortex AI zurueckgehen.</p>
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
        from cortex.storage import Storage, StorageError, discover

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
        from cortex.google import GoogleError, consent_url, exchange_code

        action = str(payload.get("action", "state")).strip()
        settings = SESSION.settings()
        redirect = google_redirect(self.headers.get("Host", ""))

        if action == "start":
            client_id = str(payload.get("client_id", "")).strip() or settings.google_client_id
            try:
                return {"ok": True, "url": consent_url(client_id, redirect), "redirect": redirect}
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
        """Testet Modell und Suchmaschine -- dasselbe, was `cortex setup` macht.

        Geprueft werden die Werte aus dem Formular, nicht die gespeicherten:
        so sieht man VOR dem Speichern, ob ein Schluessel stimmt. Ein leeres
        Schluesselfeld heisst weiterhin "unveraendert", also greift dann der
        gespeicherte.
        """
        from cortex.probe import check_llm, check_search

        settings = SESSION.settings()
        model = fix_model_id(str(payload.get("CORTEX_MODEL", "")).strip()) or settings.model
        api_key = str(payload.get(API_KEY_FIELD, "")).strip()
        if not api_key:
            name = api_key_name_for(model)
            api_key = os.environ.get(name, "") if name else ""
        api_base = str(payload.get("CORTEX_API_BASE", settings.api_base) or "").strip()
        if api_base and not base_fits(api_base, model):
            # Dieselbe Regel wie im Betrieb -- sonst testet man etwas anderes,
            # als spaeter laeuft, und der Test luegt.
            api_base = ""

        backend = str(payload.get("CORTEX_SEARCH_BACKEND", "")).strip() or settings.search_backend
        search_key = str(payload.get(SEARCH_KEY_FIELD, "")).strip()
        if not search_key:
            name = SEARCH_BACKEND_KEYS.get(backend, "")
            search_key = os.environ.get(name, "") if name else ""
        engines = str(payload.get("CORTEX_SEARCH_ENGINES", settings.search_engines) or "").strip()
        instance = str(payload.get("CORTEX_SEARXNG_URL", settings.searxng_url) or "").strip()

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
        `cortex connect-ha`: Knopf druecken, Adresse steht da, Token einfuegen,
        Knopf druecken, fertig.
        """
        from cortex.homeassistant import HomeAssistant, HomeAssistantError, discover

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

    def _sse(self, text: str) -> bool:
        """Schreibt ein Stueck in den Ereignisstrom. `False` = niemand mehr da.

        Bricht die Verbindung ab -- Tab zu, Seite neu geladen, Handy im
        Standby -- dann wird der Lauf beendet. Frueher lief er im Hintergrund
        zu Ende und hielt die Sitzung dabei besetzt; die naechste Frage
        bekam dann minutenlang "Ein anderes Geraet fragt gerade" zu sehen,
        obwohl gar kein anderes Geraet da war. Es war die eigene, laengst
        verlassene Anfrage.
        """
        try:
            self.wfile.write(text.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            SESSION.stop()
            return False
        return True

    def _chat(self) -> None:
        """Fuehrt die Anfrage aus und streamt die Ereignisse als SSE."""
        payload = self._read_json()
        message = str(payload.get("message", "")).strip()
        raw = payload.get("attachments")
        attachments = (
            [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        )
        # Arbeitsweise und Denken gehoeren zur einzelnen Frage, nicht zur
        # Sitzung: dieselbe Person will mal eine ausfuehrliche Recherche und
        # im naechsten Satz nur den Code. Der Browser schickt beides mit.
        mode = str(payload.get("mode", "")).strip()
        thinking = payload.get("thinking")
        thinking = None if thinking is None else bool(thinking)
        recheck = payload.get("recheck")
        recheck = None if recheck is None else bool(recheck)
        if not message and not attachments:
            self._json({"error": "leere Nachricht"}, 400)
            return
        if not message:
            # Nur Dateien, kein Text: das ist eine vollstaendige Bitte.
            message = "Sieh dir das Angehaengte an und sag mir, worum es geht."

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        # Ohne das puffert manch ein Zwischenstueck die Antwort und nichts
        # kommt an, bevor alles fertig ist.
        self.send_header("Connection", "close")
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
                SESSION.ask(
                    message, emit, attachments, mode=mode, thinking=thinking, recheck=recheck
                )
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

        # Sofort ein Lebenszeichen: erst damit steht die Verbindung fuer den
        # Browser wirklich, und man sieht, dass etwas passiert.
        self._sse(": los\n\n")

        while True:
            try:
                event = events.get(timeout=HEARTBEAT_SECONDS)
            except queue.Empty:
                # Ein Cloud-Modell schweigt zwischen zwei Schritten gern eine
                # halbe Minute. Ohne ein Byte in der Leitung legt irgendwer in
                # der Kette auf -- der Browser, das Handy-Funkmodul, ein Proxy
                # -- und die Oberflaeche meldet "network error", obwohl die
                # Recherche noch laeuft. Ein Doppelpunkt ist ein Kommentar im
                # SSE-Format: er haelt die Leitung warm und wird nicht
                # angezeigt.
                if not self._sse(": warte\n\n"):
                    break
                continue
            if event is None:
                break
            if not self._sse(f"data: {json.dumps(event, ensure_ascii=False)}\n\n"):
                break


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
        from cortex.config import model_problem

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
    global TOKEN

    TOKEN = token
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
