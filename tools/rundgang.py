#!/usr/bin/env python3
"""Rundgang durch die Weboberflaeche -- einmal alles anfassen, wie ein Nutzer.

Die Unit-Tests pruefen die Bausteine. Dieses Skript prueft, was daraus im
Browser wird: ob die Knoepfe da sind, ob sie etwas tun, ob die Fenster auf-
und wieder zugehen, ob nichts quer steht und ob die Konsole still bleibt.
Genau das faellt in einer Zusammenstellung von Einzeltests durch das Raster --
zwei Elemente mit demselben Namen, eine Variable, die vor ihrer Zeile benutzt
wird, ein Knopf, dessen Klick ins Leere geht.

Der Agent dahinter ist gestellt: keine Modelle, keine Suchanfragen, kein Netz.
Geprueft wird die Oberflaeche, nicht der Anbieter -- und so laeuft der Rundgang
auch dort, wo weder Schluessel noch Internet vorhanden sind.

    python tools/rundgang.py              # alles
    python tools/rundgang.py --nur chat   # nur die Abschnitte, die so heissen
    python tools/rundgang.py --bilder     # zusaetzlich Bildschirmfotos ablegen

Der Rueckgabewert ist 0, wenn nichts zu beanstanden war, sonst die Anzahl der
Beanstandungen -- damit laesst er sich in eine Pruefkette haengen.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

CHROMIUM = "/opt/pw-browsers/chromium"


# ---------------------------------------------------------------------------
# Der gestellte Agent
# ---------------------------------------------------------------------------
class FakeAgent:
    """Verhaelt sich wie der echte Agent -- ohne Modell, ohne Netz.

    Er merkt sich, was die Oberflaeche ihm geschickt hat (Modus, Struktur,
    Gegenprobe, Denktiefe), damit der Rundgang pruefen kann, ob die Schalter
    wirklich ankommen und nicht nur huebsch aussehen.
    """

    def __init__(self) -> None:
        self.on_event: Any = None
        self.toolbox = self
        self.session_id = "rundgang"
        self.ask_handler: Any = None
        self.gesehen: list[dict[str, Any]] = []
        self.abgebrochen = False
        self.stats = self

    # -- was der Server vom Agenten erwartet -------------------------------
    def set_ask_handler(self, handler: Any) -> None:
        self.ask_handler = handler

    def close(self) -> None: ...

    def cancel(self) -> None:
        self.abgebrochen = True

    def clear(self, new_chat: bool = True) -> None: ...

    def resume(self, session_id: str, turns: list[tuple[str, str]]) -> None:
        self.session_id = session_id

    def ask(
        self,
        message: str,
        stream: bool = True,
        mode: str = "",
        structured: bool | None = None,
        recheck: bool | None = None,
        effort: str = "",
        online: bool | None = None,
        sandbox: bool | None = None,
    ) -> Any:
        self.gesehen.append(
            {
                "text": message,
                "modus": mode,
                "struktur": structured,
                "gegenprobe": recheck,
                "tiefe": effort,
                "web": online,
                "werkstatt": sandbox,
            }
        )
        text = message.lower()

        if mode in ("code", "pro"):
            self.on_event("code_model", {"model": "anthropic/claude-opus-5"})
        if sandbox and mode == "code":
            self.on_event("vm_start", {"runtime": "Docker (gehaertet)"})
            self.on_event("vm_write", {"path": "/work/loesung.py"})
            self.on_event("vm_run", {"command": "python loesung.py"})
            self.on_event("vm_done", {"exit_code": 0, "seconds": 0.4})
        # Denkschritte kommen tokenweise -- genau wie beim echten Modell,
        # damit der Rundgang auch das Zusammenwachsen in einer Zeile sieht.
        for stueck in ("Erst ", "die Frage ", "sortieren."):
            self.on_event("thought", {"text": stueck})
        # Eine Mitlese-Zeile, die keine Denkzeile ist -- damit der Rundgang
        # die beiden auseinanderhalten kann.
        self.on_event("action", {"tool": "web_search", "arguments": {"query": "x"}})
        if structured:
            self.on_event("subagents", {"tasks": ["Teil eins", "Teil zwei"]})
            self.on_event("subagent_done", {"task": "Teil eins"})
        if "frag" in text:
            self.on_event("ask", {"question": "Welches Budget?", "options": ["bis 800 €"]})
            self.ask_handler("Welches Budget?", ["bis 800 €"])
        # Der Fall, in dem Cortex merkt, dass seine Antwort nur eine Frage
        # war: das Angefangene wird verworfen und neu angesetzt.
        if "neuansatz" in text:
            self.on_event("answer_chunk", {"text": "Fuer welchen Ort soll ich nachsehen?"})
            self.on_event("answer_reset", {"reason": "rueckfrage"})
        if "langsam" in text:
            for _ in range(40):
                time.sleep(0.1)
                if self.abgebrochen:
                    break
        self.on_event("search", {"query": "beispiel"})
        self.on_event("fetch", {"url": "https://example.org/a"})
        if recheck:
            self.on_event("recheck", {"sources": 2})
            self.on_event("recheck_done", {"changed": True})
        antwort = "```python\nprint('hallo')\n```" if mode == "code" else "Eine Antwort."
        self.on_event("answer_chunk", {"text": antwort})
        self.on_event("done", {"tool_calls": 2, "hit_limit": False})
        return type("R", (), {"answer": antwort, "stopped": self.abgebrochen})()


# ---------------------------------------------------------------------------
# Protokoll
# ---------------------------------------------------------------------------
class Protokoll:
    """Sammelt Befunde und schreibt sie mit, waehrend der Rundgang laeuft."""

    def __init__(self) -> None:
        self.probleme: list[str] = []
        self.geprueft = 0

    def abschnitt(self, titel: str) -> None:
        print(f"\n{titel}")

    def pruefe(self, ok: bool, text: str) -> bool:
        self.geprueft += 1
        print(("  ok   " if ok else "  FEHL ") + text)
        if not ok:
            self.probleme.append(text)
        return bool(ok)


def freier_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def starte_server(agent: FakeAgent) -> int:
    """Startet die Oberflaeche mit dem gestellten Agenten."""
    os.environ.setdefault("CORTEX_DATA_DIR", tempfile.mkdtemp(prefix="rundgang-"))
    os.environ.setdefault("CORTEX_MODEL", "anthropic/claude-sonnet-5")
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-rundgang")
    from cortex import web

    web.SESSION._agent = agent
    web.SESSION.agent = lambda: agent          # type: ignore[method-assign]
    web.SESSION.chat_id = lambda: agent.session_id  # type: ignore[method-assign]

    port = freier_port()
    threading.Thread(
        target=lambda: web.serve(port=port, open_browser=False), daemon=True
    ).start()
    # Warten, bis der Server wirklich antwortet -- eine feste Pause ist
    # entweder zu kurz (dann schlaegt der Rundgang grundlos fehl) oder zu lang.
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return port
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Der Server ist nicht hochgekommen.")


def lege_chats_an() -> None:
    """Zwei Chats in den Verlauf, damit die Seitenleiste etwas zu zeigen hat."""
    from cortex.cache import Cache
    from cortex.config import get_settings

    settings = get_settings()
    cache = Cache(settings.db_path, settings.cache_ttl_hours)
    cache.add_history("alt-1", "Welcher Laptop bis 1200 Euro?", "Antwort", {})
    cache.add_history("rundgang", "Was kostet ein Lastenrad?", "Antwort", {})


# ---------------------------------------------------------------------------
# Die Abschnitte des Rundgangs
# ---------------------------------------------------------------------------
def rundgang(pg: Any, log: Protokoll, agent: FakeAgent, bilder: Path | None,
             nur: set[str]) -> None:
    def dran(name: str) -> bool:
        return not nur or name in nur

    def foto(name: str) -> None:
        if bilder:
            pg.screenshot(path=str(bilder / f"{name}.png"))

    laeuft = """(sel) => {
      const e = document.querySelector(sel);
      if (!e) return "fehlt";
      return (e.getAnimations() || []).map(a => a.animationName || "?").join(",");
    }"""

    if dran("start"):
        log.abschnitt("1. Der erste Eindruck")
        log.pruefe(pg.is_visible("#greeting"), "Begrüßung steht da")
        log.pruefe(pg.inner_text("#version").startswith("v"), "Version in der Kopfzeile")
        log.pruefe(pg.locator(".chip").count() >= 2, "Beispielfragen vorhanden")
        log.pruefe(pg.locator(".recent").count() == 2, "zwei Chats in der Seitenleiste")
        log.pruefe(
            pg.eval_on_selector("body", "e => e.scrollWidth <= window.innerWidth + 1"),
            "nichts steht seitlich über",
        )
        foto("01-start")

    if dran("chat"):
        log.abschnitt("2. Eine Frage stellen")
        pg.fill("#input", "Was kostet ein Lastenrad?")
        pg.click("#send")
        pg.wait_for_selector(".msg.bot .bubble", state="visible")
        pg.wait_for_timeout(700)
        log.pruefe("Eine Antwort" in pg.inner_text("#thread"), "Antwort erscheint")
        log.pruefe(pg.locator(".msg.user").count() == 1, "die Frage steht dabei")
        schritte = pg.inner_text(".steps >> nth=-1")
        log.pruefe("[Suche]" in schritte, f"Zwischenschritte sichtbar: {schritte[:40]!r}")
        log.pruefe(
            agent.gesehen[-1]["struktur"] is False,
            f"Standard ist das Gespräch, nicht die Recherche ({agent.gesehen[-1]})",
        )

        log.abschnitt("3. Abbrechen")
        pg.fill("#input", "langsam bitte")
        pg.click("#send")
        pg.wait_for_selector("#stop", state="visible")
        log.pruefe(not pg.is_visible("#clip"), "das Anhängen weicht dem Abbruch")
        pg.click("#stop")
        pg.wait_for_timeout(1200)
        log.pruefe(agent.abgebrochen, "der Lauf wird wirklich gestoppt")
        log.pruefe(pg.is_visible("#clip"), "danach ist das Anhängen wieder da")
        agent.abgebrochen = False

    if dran("modi"):
        log.abschnitt("4. Modi und Schalter")
        pg.click('#modes .mode[data-mode="code"]')
        pg.wait_for_timeout(200)
        log.pruefe(
            pg.eval_on_selector("body", "e => e.classList.contains('code-mode')"),
            "Code-Modus schaltet um",
        )
        pg.fill("#input", "Schreib mir eine Funktion")
        pg.click("#send")
        pg.wait_for_timeout(900)
        log.pruefe(agent.gesehen[-1]["modus"] == "code", "der Modus kommt an")
        log.pruefe("[Code]" in pg.inner_text(".steps >> nth=-1"),
                   "das stärkste Modell wird genannt")
        log.pruefe(pg.locator(".bubble pre").count() >= 1, "Code steht im Block")
        pg.click('#modes .mode[data-mode="normal"]')

        pg.click("#btn-model")
        pg.wait_for_timeout(500)
        log.pruefe(pg.is_visible("#structure") and pg.is_visible("#recheck"),
                   "Strukturieren und Gegenprüfen stehen bereit")
        log.pruefe(pg.locator("#efforts .eff").count() == 3, "drei Stufen der Denktiefe")
        pg.click('#efforts .eff[data-effort="high"]')
        pg.wait_for_timeout(200)
        pg.check("#structure")
        pg.check("#recheck")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        log.pruefe("gegenprüfen" in pg.inner_text("#status"), "die Kopfzeile sagt es")
        pg.fill("#input", "Was kostet ein Lastenrad?")
        pg.click("#send")
        pg.wait_for_timeout(1200)
        letzte = agent.gesehen[-1]
        log.pruefe(letzte["struktur"] is True and letzte["gegenprobe"] is True,
                   f"beide Schalter kommen an ({letzte})")
        log.pruefe(letzte["tiefe"] == "high", f"die Denktiefe kommt an ({letzte['tiefe']})")
        log.pruefe("Denktiefe high" in pg.inner_text("#status"),
                   "und steht in der Kopfzeile")
        schritte = pg.inner_text(".steps >> nth=-1")
        log.pruefe("[Teile]" in schritte, "strukturiert wird zerlegt")
        log.pruefe("[Gegenprobe]" in schritte, "die Gegenprobe meldet sich")
        log.pruefe("Gegengeprüft" in pg.inner_text(".msg.bot >> nth=-1"),
                   "und steht als Vermerk an der Antwort")
        breite = pg.eval_on_selector(".answer-note", "e => e.getBoundingClientRect().width")
        log.pruefe(breite > 200, f"der Vermerk steht in einer Zeile ({breite:.0f}px)")
        log.pruefe(
            pg.eval_on_selector(".brand svg", "e => e.getBoundingClientRect().width") >= 20,
            "und das Logo in der Seitenleiste ist unversehrt",
        )
        pg.click("#btn-model")
        pg.wait_for_timeout(400)
        pg.uncheck("#structure")
        pg.uncheck("#recheck")
        pg.keyboard.press("Escape")
        foto("04-modi")

    if dran("werkstatt"):
        log.abschnitt("4a. Werkstatt im Code-Modus")
        pg.click('#modes .mode[data-mode="code"]')
        pg.wait_for_timeout(300)
        pg.click("#btn-model")
        pg.wait_for_timeout(500)
        log.pruefe(pg.is_visible("#werkstatt"), "der Schalter steht im Code-Modus bereit")
        log.pruefe(not pg.is_visible("#online"), "Im Web suchen ist hier verschwunden")
        log.pruefe(not pg.is_visible("#recheck"), "Gegenprüfen ebenso")
        log.pruefe(pg.is_visible("#efforts"), "die Denktiefe bleibt")
        pg.check("#werkstatt")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        log.pruefe("Werkstatt" in pg.inner_text("#status"), "die Kopfzeile sagt es")
        pg.fill("#input", "Schreib ein Skript und führ es aus")
        pg.click("#send")
        pg.wait_for_timeout(1200)
        letzte = agent.gesehen[-1]
        log.pruefe(letzte["werkstatt"] is True, f"der Schalter kommt an ({letzte['werkstatt']})")
        log.pruefe(letzte["web"] is True,
                   "und nachschlagen darf er weiterhin — nur die Maschine hat kein Netz")
        schritte = pg.inner_text(".steps >> nth=-1")
        log.pruefe("[Werkstatt]" in schritte, "die Werkstatt meldet sich")
        log.pruefe("lief durch" in schritte, "und sagt, was herauskam")
        foto("04a-werkstatt")
        pg.click('#modes .mode[data-mode="normal"]')
        pg.wait_for_timeout(300)
        pg.click("#btn-model")
        pg.wait_for_timeout(400)
        log.pruefe(not pg.is_visible("#werkstatt"), "im Standardmodus ist sie wieder weg")
        log.pruefe(pg.is_visible("#online") and pg.is_visible("#recheck"),
                   "dafür sind Web und Gegenprüfen zurück")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)

    if dran("web"):
        log.abschnitt("4b. Ohne Web")
        pg.click("#btn-model")
        pg.wait_for_timeout(400)
        log.pruefe(pg.is_checked("#online"), "Suchen ist von Haus aus an")
        pg.uncheck("#online")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        log.pruefe("ohne Web" in pg.inner_text("#status"), "die Kopfzeile sagt es")
        pg.fill("#input", "Was weißt du selbst?")
        pg.click("#send")
        pg.wait_for_timeout(1100)
        log.pruefe(agent.gesehen[-1]["web"] is False,
                   f"der Schalter kommt an ({agent.gesehen[-1]['web']})")
        pg.click("#btn-model")
        pg.wait_for_timeout(400)
        pg.check("#online")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        log.pruefe("ohne Web" not in pg.inner_text("#status"), "und wieder zurück")

    if dran("pro"):
        log.abschnitt("4c. Pro-Modus")
        pg.click('#modes .mode[data-mode="pro"]')
        pg.wait_for_timeout(300)
        log.pruefe(
            pg.eval_on_selector("body", "e => e.classList.contains('pro-mode')"),
            "Pro-Modus schaltet um",
        )
        # Optisch derselbe Knopf: gleiche Hoehe, gleiche Schrift wie die
        # anderen beiden. Nur die Leistung dahinter ist eine andere.
        masse = pg.eval_on_selector_all(
            "#modes .mode",
            "els => els.map(e => [Math.round(e.getBoundingClientRect().height),"
            " getComputedStyle(e).fontSize, getComputedStyle(e).borderRadius])",
        )
        log.pruefe(len(masse) == 3, f"drei Knoepfe nebeneinander ({len(masse)})")
        log.pruefe(len({str(m) for m in masse}) == 1,
                   f"alle drei sehen gleich aus ({masse})")
        pg.click("#btn-model")
        pg.wait_for_timeout(500)
        log.pruefe(pg.is_checked("#structure"),
                   "Strukturieren geht beim Wechsel an -- ohne das keine Agenten")
        log.pruefe(not pg.is_visible("#recheck"), "Gegenprüfen gibt es hier nicht")
        log.pruefe(pg.is_visible("#online") and pg.is_visible("#denken")
                   and pg.is_visible("#structure"),
                   "alles andere aus dem Standardmodus steht bereit")
        log.pruefe(not pg.is_visible("#werkstatt"), "die Werkstatt bleibt beim Code")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        log.pruefe("Pro" in pg.inner_text("#status"), "die Kopfzeile sagt es")
        pg.fill("#input", "Welche Lastenräder gibt es in Bremen?")
        pg.click("#send")
        pg.wait_for_timeout(1200)
        letzte = agent.gesehen[-1]
        log.pruefe(letzte["modus"] == "pro", f"der Modus kommt an ({letzte['modus']})")
        log.pruefe(letzte["gegenprobe"] is not True,
                   f"und die Gegenprobe bleibt aus ({letzte['gegenprobe']})")
        schritte = pg.inner_text(".steps >> nth=-1")
        log.pruefe("[Pro]" in schritte, "das stärkste Modell wird genannt")
        log.pruefe("[Code]" not in schritte, "und zwar als Pro, nicht als Code")
        foto("04c-pro")
        pg.click('#modes .mode[data-mode="normal"]')
        pg.wait_for_timeout(700)
        pg.click("#btn-model")
        pg.wait_for_timeout(400)
        log.pruefe(pg.is_visible("#recheck"), "im Standardmodus ist es wieder da")
        # Der Schalter bleibt umlegbar -- und der Rundgang laesst die Lage so
        # zurueck, wie er sie vorgefunden hat.
        pg.uncheck("#structure")
        pg.wait_for_timeout(200)
        log.pruefe(not pg.is_checked("#structure"), "und Strukturieren geht wieder aus")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)

    if dran("vorschlaege"):
        log.abschnitt("4d. Vorschläge passen zum Modus")
        pg.reload()
        pg.wait_for_selector("#chips")
        vorschlag = pg.inner_text("#chips")
        log.pruefe("Café" in vorschlag or "Netz" in vorschlag,
                   f"im Standardmodus geht es ums Suchen ({vorschlag[:40]!r})")
        log.pruefe(not pg.is_visible("#chips-code"), "die Coding-Vorschläge sind weg")
        pg.click('#modes .mode[data-mode="code"]')
        pg.wait_for_timeout(400)
        log.pruefe(pg.is_visible("#chips-code"), "im Code-Modus stehen die anderen da")
        log.pruefe(not pg.is_visible("#chips"), "und die Suchvorschläge sind weg")
        code_text = pg.inner_text("#chips-code")
        log.pruefe("Python" in code_text or "API" in code_text,
                   f"es geht ums Programmieren ({code_text[:40]!r})")
        pg.click('#modes .mode[data-mode="pro"]')
        pg.wait_for_timeout(400)
        log.pruefe(pg.is_visible("#chips") and not pg.is_visible("#chips-code"),
                   "im Pro-Modus wieder die Suchvorschläge")
        pg.click('#modes .mode[data-mode="normal"]')
        pg.wait_for_timeout(400)
        # Ein Klick auf einen Vorschlag fuellt die Eingabe.
        pg.click("#chips .chip >> nth=0")
        pg.wait_for_timeout(200)
        log.pruefe(len(pg.input_value("#input")) > 5, "ein Klick füllt die Eingabe")
        pg.fill("#input", "")

    if dran("rueckfrage"):
        log.abschnitt("5. Rückfrage")
        pg.fill("#input", "Frag mich was")
        pg.click("#send")
        pg.wait_for_selector("#askbox.open", state="visible")
        log.pruefe(pg.locator(".ask-opt").count() == 1, "die Antwortmöglichkeit steht da")
        log.pruefe(pg.is_visible("#ask-free"), "und ein Feld zum Selberschreiben")
        foto("05-rueckfrage")
        pg.click(".ask-opt >> nth=0")
        pg.wait_for_timeout(900)
        log.pruefe(not pg.is_visible("#askbox.open"), "danach ist das Fenster zu")
        log.pruefe("Budget" in pg.inner_text("#thread"), "die Frage bleibt im Verlauf")

    if dran("chats"):
        log.abschnitt("6. Letzte Chats")
        vorher = pg.locator(".recent").count()
        pg.click(".recent >> nth=0 >> .more")
        pg.wait_for_timeout(300)
        log.pruefe(pg.is_visible(".chatmenu"), "das Menü geht auf")
        pg.click('.chatmenu button:has-text("Umbenennen")')
        pg.wait_for_timeout(300)
        pg.fill(".recent input.rename", "Mein Chat")
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(800)
        log.pruefe("Mein Chat" in pg.inner_text("#recents"), "der neue Name steht da")
        pg.click(".recent >> nth=0 >> .more")
        pg.wait_for_timeout(300)
        pg.once("dialog", lambda d: d.accept())
        pg.click(".chatmenu button.danger")
        pg.wait_for_timeout(900)
        log.pruefe(pg.locator(".recent").count() == vorher - 1, "und Löschen löscht")
        if pg.locator(".recent").count():
            pg.click(".recent >> nth=0 >> .name")
            pg.wait_for_timeout(900)
            log.pruefe(pg.locator(".msg").count() >= 2, "ein alter Chat lässt sich öffnen")
        pg.click("#btn-new")
        pg.wait_for_timeout(700)
        log.pruefe(
            pg.eval_on_selector("body", "e => e.classList.contains('start')"),
            "Neuer Chat beginnt leer",
        )

    if dran("einstellungen"):
        log.abschnitt("7. Einstellungen")
        pg.click("#btn-settings")
        pg.wait_for_selector("#overlay.open", state="visible")
        pg.wait_for_timeout(700)
        marken = pg.locator("#secnav button").count()
        abschnitte = pg.locator("#settings fieldset").count()
        log.pruefe(marken == abschnitte, f"{marken} Sprungmarken zu {abschnitte} Abschnitten")
        for feld in ("CORTEX_MODEL", "CORTEX_CODE_MODEL", "CORTEX_LOCATION",
                     "CORTEX_LAN_SUBNET", "CORTEX_STORAGE_URL"):
            log.pruefe(pg.locator(f'[name="{feld}"]').count() == 1, f"{feld} im Formular")
        pg.click('#secnav button:has-text("Suche")')
        pg.wait_for_timeout(900)
        kopf = pg.eval_on_selector(".sheet-head", "e => e.getBoundingClientRect().bottom")
        ziel = pg.eval_on_selector(
            '#settings fieldset:has(legend:text-is("Suche"))',
            "e => e.getBoundingClientRect().top",
        )
        log.pruefe(ziel >= kopf - 4, "der Sprung landet unter dem stehenden Kopf")
        for knopf, notiz in (
            ("#probe", "#probe-note"),
            ("#storage-test", "#lager-note"),
            ("#storage-find", "#lager-note"),
            ("#ha-test", "#ha-note"),
        ):
            pg.click(knopf)
            pg.wait_for_timeout(1400)
            gesagt = pg.inner_text(notiz).strip()
            log.pruefe(bool(gesagt), f"{knopf} meldet etwas: {gesagt[:50]!r}")
        pg.check("#showload")
        pg.wait_for_timeout(1600)
        log.pruefe(bool(pg.inner_text("#usage-note").strip()), "die Auslastung zeigt Zahlen")
        pg.check("#showtrace")
        pg.wait_for_timeout(200)
        log.pruefe(
            pg.eval_on_selector("body", "e => e.classList.contains('tracing')"),
            "Mitlesen schaltet sich ein",
        )
        pg.uncheck("#showtrace")
        foto("07-einstellungen")
        pg.click("#cancel")
        pg.wait_for_timeout(500)
        log.pruefe(not pg.is_visible("#overlay.open"), "das Fenster geht wieder zu")

    if dran("aussehen"):
        log.abschnitt("8. Erscheinungsbild")
        pg.click("#btn-theme")
        pg.wait_for_selector("#themebox.open", state="visible")
        karten = pg.locator("#palettes .pal").count()
        log.pruefe(karten >= 8, f"{karten} Farbschemata zur Auswahl")
        log.pruefe(pg.locator(".pal >> nth=0").inner_text().startswith("Standard"),
                   "Standard steht vorn")
        ids = pg.eval_on_selector_all(
            "#palettes .pal", "es => es.map(e => e.dataset.palette)"
        )
        farben = set()
        for modus in ("light", "dark"):
            pg.click(f'[data-tmode="{modus}"]')
            pg.wait_for_timeout(150)
            for pid in ids:
                pg.click(f'.pal[data-palette="{pid}"]')
                pg.wait_for_timeout(90)
                gesetzt = pg.get_attribute("html", "data-palette") or ""
                if gesetzt != pid:
                    log.pruefe(False, f"Schema {pid or 'standard'} wird nicht gesetzt")
                farben.add(
                    pg.eval_on_selector("body", "e => getComputedStyle(e).backgroundColor")
                )
        log.pruefe(len(farben) >= len(ids), f"{len(farben)} verschiedene Untergründe")
        foto("08-aussehen")
        pg.click("#theme-reset")
        pg.wait_for_timeout(300)
        log.pruefe(pg.get_attribute("html", "data-palette") is None, "Standard kommt zurück")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        log.pruefe(not pg.is_visible("#themebox.open"), "Escape schließt")

    if dran("anhang"):
        log.abschnitt("9. Dateien anhängen")
        beispiel = Path(tempfile.gettempdir()) / "rundgang.txt"
        beispiel.write_text("Ein Textanhang.\n", encoding="utf-8")
        pg.set_input_files("#picker", str(beispiel))
        pg.wait_for_timeout(600)
        log.pruefe(pg.locator("#files .file").count() == 1, "die Datei erscheint")
        if pg.locator("#files .file .x").count():
            pg.click("#files .file .x")
            pg.wait_for_timeout(300)
            log.pruefe(pg.locator("#files .file").count() == 0, "und lässt sich entfernen")

    if dran("befehle"):
        log.abschnitt("10. Slash-Befehle")
        pg.fill("#input", "/help")
        pg.click("#send")
        pg.wait_for_timeout(900)
        log.pruefe("/clear" in pg.inner_text("#thread"), "/help zeigt die Befehle")
        pg.click("#btn-notes")
        pg.wait_for_timeout(900)
        log.pruefe(
            "erkzettel" in pg.inner_text("#thread") or "otiz" in pg.inner_text("#thread"),
            "der Merkzettel antwortet",
        )

    if dran("denken"):
        log.abschnitt("10a. Denken sichtbar machen")
        pg.click('#modes .mode[data-mode="normal"]')
        pg.wait_for_timeout(300)
        pg.click("#btn-model")
        pg.wait_for_selector("#picker-models", state="visible")
        pg.wait_for_timeout(400)
        log.pruefe(pg.is_visible("#denken"), "der Denken-Schalter steht im Standardmodus")
        log.pruefe(pg.is_visible("#structure"),
                   "und er hat Strukturieren nicht ersetzt -- beide sind da")
        zeile = pg.inner_text('label[for="denken"]')
        log.pruefe("Denken" in zeile, "er heisst Denken")

        # Der Schalter ist ein echtes Ankreuzfeld -- nur anders angezogen.
        log.pruefe(
            pg.eval_on_selector("#denken", "e => e.type") == "checkbox",
            "unter dem Anstrich steckt ein Ankreuzfeld",
        )
        breite = pg.eval_on_selector("#denken", "e => e.getBoundingClientRect().width")
        log.pruefe(40 <= breite <= 50, f"er ist eine Pille, kein Haken ({breite:.0f}px)")
        vorher = pg.eval_on_selector(
            "#denken", "e => getComputedStyle(e).backgroundColor")
        pg.check("#denken")
        pg.wait_for_timeout(400)
        nachher = pg.eval_on_selector(
            "#denken", "e => getComputedStyle(e).backgroundColor")
        log.pruefe(vorher != nachher, f"angeschaltet wechselt er die Farbe ({nachher})")
        weg = pg.eval_on_selector(
            "#denken", "e => getComputedStyle(e, '::after').transform")
        log.pruefe(weg not in ("none", ""), f"und der Knopf faehrt hinueber ({weg})")
        log.pruefe(
            pg.eval_on_selector(
                "#denken",
                "e => getComputedStyle(e).transitionDuration !== '0s'"),
            "der Wechsel ist animiert, nicht hart",
        )
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)
        log.pruefe(
            pg.eval_on_selector("body", "e => e.classList.contains('denken')"),
            "der Schalter merkt sich seinen Stand",
        )

        pg.fill("#input", "Denk mal nach")
        pg.click("#send")
        pg.wait_for_selector(".msg.bot .bubble", state="visible")
        pg.wait_for_timeout(800)
        log.pruefe(
            pg.locator(".trace.think:visible").count() > 0,
            "der Denken-Block steht jetzt im Chat",
        )
        log.pruefe(
            pg.locator(".trace:not(.think):visible").count() == 0,
            "die uebrigen Mitlese-Zeilen bleiben weg -- die gehoeren zum Mitlesen",
        )
        pg.click("#btn-model")
        pg.wait_for_timeout(400)
        pg.uncheck("#denken")
        pg.wait_for_timeout(300)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)
        log.pruefe(
            pg.locator(".trace.think:visible").count() == 0,
            "ausgeschaltet verschwindet er wieder -- auch rueckwirkend",
        )
        # Und das Mitlesen darf ihn nicht durch die Hintertuer wieder
        # hereinlassen: sonst stuende er da, obwohl "Denken" aus ist.
        pg.evaluate("document.body.classList.add('tracing')")
        pg.wait_for_timeout(300)
        log.pruefe(
            pg.locator(".trace.think:visible").count() == 0,
            "auch das Mitlesen zeigt die Gedanken nicht -- dafuer gibt es Denken",
        )
        log.pruefe(
            pg.locator(".trace:not(.think):visible").count() > 0,
            "die uebrigen Mitlese-Zeilen zeigt es sehr wohl",
        )
        pg.evaluate("document.body.classList.remove('tracing')")
        pg.wait_for_timeout(200)
        foto("10a-denken")

    if dran("einchat"):
        log.abschnitt("10b. Ein Chat bleibt ein Chat")
        pg.click('#modes .mode[data-mode="normal"]')
        pg.wait_for_timeout(300)
        pg.click("#btn-new")
        pg.wait_for_timeout(700)
        # Nicht "Frage" nennen: die Attrappe stellt bei allem mit "frag"
        # eine Rueckfrage und wartet dann auf eine Antwort.
        for nummer in (1, 2, 3):
            pg.fill("#input", f"Thema {nummer}")
            pg.click("#send")
            # Die Antwort steht schon da, waehrend der Lauf noch laeuft.
            # Gewartet wird deshalb darauf, dass das Abbrechen verschwindet
            # -- der Senden-Knopf taugt nicht dafuer, er ist auch bei leerem
            # Eingabefeld aus.
            pg.wait_for_selector("#stop", state="hidden", timeout=25000)
            pg.wait_for_timeout(700)
        log.pruefe(
            pg.locator(".msg.user").count() == 3,
            f"drei Fragen stehen im selben Verlauf ({pg.locator('.msg.user').count()})",
        )
        # Die Leiste zaehlt Chats, nicht Fragen: aus dreien darf einer werden.
        namen = pg.eval_on_selector_all(
            "#recents .recent .name", "es => es.map(e => e.textContent.trim())"
        )
        log.pruefe(
            sum(1 for name in namen if name.startswith("Thema ")) <= 1,
            f"und in der Leiste steht dafuer ein Eintrag, nicht drei ({namen})",
        )

        # Dass der Server ueber alle Fragen hinweg denselben Chat meint, kann
        # der Rundgang nicht pruefen: hier steckt eine Attrappe in der
        # Sitzung, deren Kennung fest ist. Das liegt bei den Tests in
        # tests/test_web.py, die die echte ChatSession benutzen.

        log.abschnitt("10c. Arbeitsweise wechseln")
        vorher = pg.locator(".msg").count()
        log.pruefe(vorher > 0, "im Chat steht etwas")
        pg.click('#modes .mode[data-mode="code"]')
        pg.wait_for_timeout(900)
        log.pruefe(pg.locator(".msg").count() == 0, "der Wechsel raeumt den Chat")
        log.pruefe(
            pg.eval_on_selector("body", "e => e.classList.contains('start')"),
            "und die Begruessung ist wieder da",
        )
        pg.click('#modes .mode[data-mode="code"]')
        pg.wait_for_timeout(600)
        log.pruefe(
            pg.eval_on_selector("body", "e => e.classList.contains('code-mode')"),
            "zweimal derselbe Modus aendert nichts",
        )
        pg.click('#modes .mode[data-mode="normal"]')
        pg.wait_for_timeout(600)

    if dran("zustand"):
        log.abschnitt("10d. Der Zustand liegt beim Server")
        # Etwas umstellen, neu laden, nachsehen: was der Server weiss,
        # ueberlebt das Neuladen -- und den Wechsel des Geraets.
        pg.click("#btn-model")
        pg.wait_for_timeout(400)
        pg.check("#recheck")
        pg.wait_for_timeout(200)
        pg.click('#efforts .eff[data-effort="high"]')
        pg.wait_for_timeout(500)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)

        gemerkt = pg.evaluate("() => Object.keys(localStorage)")
        log.pruefe(
            [name for name in gemerkt if name != "cortex-token"] == [],
            f"der Browser haelt nichts fest ausser dem Zugangswort ({gemerkt})",
        )

        pg.reload(wait_until="networkidle")
        pg.wait_for_timeout(500)
        log.pruefe(
            pg.eval_on_selector("#recheck", "e => e.checked"),
            "nach dem Neuladen steht die Gegenprobe noch an",
        )
        log.pruefe(
            pg.eval_on_selector('#efforts .eff[data-effort="high"]',
                                "e => e.classList.contains('on')"),
            "und die Denktiefe auch",
        )
        # Der Server sagt dasselbe wie der Bildschirm.
        vom_server = pg.evaluate(
            "async () => await (await fetch('/api/prefs')).json()")
        log.pruefe(
            vom_server.get("recheck") is True and vom_server.get("effort") == "high",
            f"der Server weiss es selbst ({vom_server.get('effort')}, "
            f"recheck={vom_server.get('recheck')})",
        )

        # Und er glaubt nicht alles: Unsinn faellt auf den Standard zurueck.
        geprueft = pg.evaluate(
            """async () => await (await fetch("/api/prefs", {
                 method: "POST", headers: {"Content-Type": "application/json"},
                 body: JSON.stringify({ mode: "rm -rf", effort: "unendlich",
                                        admin: true, structured: "false" }),
               })).json()"""
        )
        log.pruefe(
            geprueft.get("mode") == "normal" and geprueft.get("effort") == "high",
            f"unerlaubte Werte kommen nicht durch ({geprueft.get('mode')}, "
            f"{geprueft.get('effort')})",
        )
        log.pruefe("admin" not in geprueft, "unbekannte Felder auch nicht")
        log.pruefe(
            geprueft.get("structured") is False,
            'die Zeichenkette "false" heisst aus, nicht an',
        )

        # Aufraeumen, damit die folgenden Abschnitte nicht darauf stossen.
        pg.evaluate(
            """async () => await fetch("/api/prefs", {
                 method: "POST", headers: {"Content-Type": "application/json"},
                 body: JSON.stringify({ recheck: false, effort: "medium" }),
               })"""
        )
        pg.reload(wait_until="networkidle")
        pg.wait_for_timeout(400)

    if dran("neuansatz"):
        log.abschnitt("10e. Aus einer Frage wird ein Neuansatz")
        pg.fill("#input", "neuansatz bitte")
        pg.click("#send")
        pg.wait_for_selector("#stop", state="hidden", timeout=25000)
        pg.wait_for_timeout(600)
        letzte = pg.inner_text(".msg.bot .bubble >> nth=-1")
        log.pruefe(
            "Fuer welchen Ort" not in letzte,
            f"die verworfene Frage steht nicht mehr da ({letzte[:50]!r})",
        )
        log.pruefe(bool(letzte.strip()), "die richtige Antwort steht da")

    if dran("suche"):
        log.abschnitt("11. Chats durchsuchen")
        vorher = pg.locator(".recent").count()
        pg.fill("#chatsuche", "Lastenrad")
        pg.wait_for_timeout(600)
        gefunden = pg.locator(".recent").count()
        log.pruefe(gefunden <= vorher, f"die Liste wird enger ({vorher} -> {gefunden})")
        if gefunden:
            log.pruefe(pg.locator(".recent mark").count() > 0, "der Treffer ist hervorgehoben")
        pg.fill("#chatsuche", "gibtesnichtxyz")
        pg.wait_for_timeout(600)
        log.pruefe(pg.locator(".recents-leer").count() == 1, "nichts gefunden wird gesagt")
        pg.click("#suche-weg")
        pg.wait_for_timeout(600)
        log.pruefe(pg.locator(".recent").count() == vorher, "und die Liste kommt zurück")
        foto("11-suche")

    if dran("export"):
        log.abschnitt("12. Mitnehmen")
        pg.fill("#input", "Kurz und knapp bitte")
        pg.click("#send")
        pg.wait_for_selector(".msg.bot .bubble", state="visible")
        pg.wait_for_timeout(700)
        log.pruefe(pg.locator(".answer-tools").count() > 0, "unter der Antwort steht ein Knopf")
        pg.hover(".msg.bot >> nth=-1")
        pg.click('.answer-tools >> nth=-1 >> [data-do="copy"]')
        pg.wait_for_timeout(400)
        beschriftung = pg.inner_text('.answer-tools >> nth=-1 >> [data-do="copy"]')
        log.pruefe(beschriftung in ("Kopiert", "Ging nicht"),
                   f"das Kopieren meldet sich: {beschriftung!r}")

    if dran("speicher"):
        log.abschnitt("13. Was Cortex über mich weiß")
        pg.click("#btn-settings")
        pg.wait_for_selector("#overlay.open", state="visible")
        pg.click("#btn-memory")
        pg.wait_for_selector("#membox.open", state="visible")
        # Das Fenster ist offen, bevor die Antwort des Servers da ist --
        # ohne dieses Warten prueft man eine noch leere Liste.
        pg.wait_for_timeout(800)
        log.pruefe(
            pg.locator("#merkliste .merk").count() > 0
            or pg.locator("#merkliste .merk-leer").count() == 1,
            "das Fenster sagt, was drinsteht -- oder dass nichts drinsteht",
        )
        foto("13-speicher")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        log.pruefe(not pg.is_visible("#membox.open"), "Escape schließt es")
        log.pruefe(pg.is_visible("#overlay.open"), "die Einstellungen bleiben offen")

        log.abschnitt("14. Nutzung")
        log.pruefe(pg.locator("#zaehler .kachel").count() == 3, "drei Kacheln beim Zähler")
        log.pruefe("Token" in pg.inner_text("#zaehler"), "sie sprechen von Token")

        log.abschnitt("15. Aufträge")
        pg.fill("#job-frage", "Was gibt es Neues bei Lastenrädern?")
        pg.select_option("#job-rhythm", "weekly")
        pg.wait_for_timeout(200)
        log.pruefe(pg.is_visible("#job-tag-feld"), "wöchentlich fragt nach dem Wochentag")
        pg.click("#job-add")
        pg.wait_for_timeout(700)
        log.pruefe(pg.locator(".auftrag").count() == 1, "der Auftrag steht in der Liste")
        log.pruefe("wöchentlich" in pg.inner_text(".auftrag"), "mit seinem Rhythmus")
        pg.select_option("#job-rhythm", "hourly")
        pg.wait_for_timeout(200)
        log.pruefe(not pg.is_visible("#job-tag-feld"), "stündlich braucht keinen Wochentag")
        foto("15-auftraege")

        log.abschnitt("16. Google")
        log.pruefe(pg.locator("#google-write").count() == 1, "der Schalter zum Ändern ist da")
        log.pruefe(
            "Verschickt wird nie eine Mail" in pg.inner_text("#google-write + label"),
            "und sagt, dass nichts verschickt wird",
        )
        pg.click("#cancel")
        pg.wait_for_timeout(500)

    if dran("werkstatt2"):
        log.abschnitt("17. Werkstatt: Dateien")
        # Die Modusknoepfe liegen in der Kopfzeile, der Werkstatt-Schalter in
        # der Modellauswahl. Waehrend die offen ist, liegt eine Sperrflaeche
        # ueber der Kopfzeile -- also erst umschalten, dann aufklappen.
        pg.click('#modes .mode[data-mode="code"]')
        pg.wait_for_timeout(300)
        pg.click("#btn-model")
        pg.wait_for_timeout(500)
        if not pg.is_checked("#werkstatt"):
            pg.check("#werkstatt")
        pg.wait_for_timeout(300)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        log.pruefe(pg.is_visible("#btn-vmfiles"), "im Code-Modus gibt es den Dateiknopf")
        pg.click("#btn-vmfiles")
        pg.wait_for_selector("#vmbox.open", state="visible")
        pg.wait_for_timeout(800)
        log.pruefe(
            pg.locator("#vmliste .merk-leer").count() == 1,
            "ohne laufende Werkstatt wird das gesagt, statt eine leere Liste zu zeigen",
        )
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(500)
        pg.click('#modes .mode[data-mode="normal"]')
        pg.wait_for_timeout(400)
        log.pruefe(not pg.is_visible("#btn-vmfiles"), "im Standardmodus verschwindet er wieder")

    if dran("bewegung"):
        log.abschnitt("18. Bewegung")
        pg.click("#btn-settings")
        pg.wait_for_selector("#overlay.open", state="visible")
        log.pruefe("sheet-in" in pg.evaluate(laeuft, "#overlay .sheet"),
                   "das Fenster läuft ein")
        pg.wait_for_timeout(500)
        pg.click("#cancel")
        pg.wait_for_timeout(60)
        log.pruefe("sheet-out" in pg.evaluate(laeuft, "#overlay .sheet"),
                   "und blendet beim Schließen aus")
        pg.wait_for_timeout(400)
        oben = pg.evaluate(
            """() => [...document.querySelectorAll("body > *")]
                 .filter(e => { const r = e.getBoundingClientRect();
                   return r.top <= 2 && r.height > 0 && r.height <= 8 && r.width > 200; })
                 .map(e => e.id || e.className)"""
        )
        log.pruefe(not oben, f"keine Statusleiste am oberen Rand ({oben})")


def geraet(pg: Any, log: Protokoll, name: str, nummer: str,
           bilder: Path | None) -> None:
    """Was auf jedem Geraet stimmen muss -- geprueft in jeder Groesse.

    Es sind wenige Dinge, aber es sind die, die man auf dem eigenen
    Bildschirm nie sieht: dass nichts seitlich uebersteht, dass kein Fenster
    breiter ist als das Fenster, dass die Knoepfe gross genug fuer einen
    Daumen sind und dass sich nichts ueberlappt.
    """
    log.abschnitt(f"{nummer}. {name}")
    breite = pg.evaluate("() => window.innerWidth")

    log.pruefe(
        pg.eval_on_selector("body", "e => e.scrollWidth <= window.innerWidth + 1"),
        f"nichts steht seitlich ueber ({breite}px breit)",
    )
    # Die Kopfzeile und die Modellauswahl duerfen sich nicht schneiden.
    pg.click("#btn-model")
    pg.wait_for_timeout(500)
    ueberschnitten = pg.evaluate(
        """() => {
             const p = document.querySelector("#picker-models").getBoundingClientRect();
             const b = document.querySelector(".topbar").getBoundingClientRect();
             return p.top < b.bottom - 1;
           }"""
    )
    log.pruefe(not ueberschnitten, "die Modellauswahl liegt unter der Kopfzeile")
    log.pruefe(
        pg.eval_on_selector("#picker-models",
                            "e => e.getBoundingClientRect().width <= window.innerWidth"),
        "und passt in die Breite",
    )
    # Ein Schalter, den man mit dem Daumen treffen soll, braucht Flaeche.
    hoehe = pg.eval_on_selector("#online",
                                "e => e.closest('label').getBoundingClientRect().height")
    log.pruefe(hoehe >= 40, f"die Schalterzeilen sind {hoehe:.0f}px hoch")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(400)

    # Das Einstellungsfenster ist das laengste -- wenn eines quer laeuft,
    # dann dieses.
    if breite < 900:
        pg.click("#btn-side")
        pg.wait_for_timeout(400)
    pg.click("#btn-settings")
    pg.wait_for_selector("#overlay.open", state="visible")
    pg.wait_for_timeout(700)
    log.pruefe(
        pg.eval_on_selector("#overlay .sheet", "e => e.scrollWidth <= e.clientWidth + 1"),
        "das Formular passt in die Breite",
    )
    log.pruefe(
        pg.eval_on_selector("#overlay .sheet",
                            "e => e.getBoundingClientRect().height <= window.innerHeight"),
        "und in die Hoehe",
    )
    spalten = pg.eval_on_selector(
        ".row", "e => getComputedStyle(e).gridTemplateColumns.split(' ').length"
    )
    erwartet = 1 if breite < 600 else 2
    log.pruefe(
        spalten == erwartet,
        f"die Formularreihen stehen zu {spalten} (erwartet {erwartet})",
    )
    if bilder:
        pg.screenshot(path=str(bilder / f"{nummer}-{name.lower().replace(' ', '-')}.png"))
    pg.click("#cancel")
    pg.wait_for_timeout(500)


def handy(pg: Any, log: Protokoll, bilder: Path | None) -> None:
    """Dasselbe noch einmal, aber auf einem schmalen Schirm."""
    log.abschnitt("19. Auf dem Handy")
    log.pruefe(
        pg.eval_on_selector("body", "e => e.classList.contains('collapsed')")
        or pg.eval_on_selector("aside", "e => e.getBoundingClientRect().right <= 1"),
        "die Seitenleiste ist eingeklappt",
    )
    pg.click("#btn-side")
    pg.wait_for_timeout(400)
    pg.click("#btn-settings")
    pg.wait_for_selector("#overlay.open", state="visible")
    pg.wait_for_timeout(600)
    log.pruefe(
        pg.eval_on_selector("#overlay .sheet", "e => e.scrollWidth <= e.clientWidth + 1"),
        "das Formular passt in die Breite",
    )
    log.pruefe(
        pg.eval_on_selector("body", "e => e.scrollWidth <= window.innerWidth + 1"),
        "die Seite scrollt nicht quer",
    )
    if bilder:
        pg.screenshot(path=str(bilder / "12-handy.png"))
    pg.click("#cancel")
    pg.wait_for_timeout(400)
    pg.click("#btn-side")
    pg.wait_for_timeout(300)
    pg.click("#btn-theme")
    pg.wait_for_selector("#themebox.open", state="visible")
    spalten = pg.eval_on_selector(
        "#palettes", "e => getComputedStyle(e).gridTemplateColumns.split(' ').length"
    )
    log.pruefe(spalten >= 2, f"die Farbkarten stehen zu {spalten} nebeneinander")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    # Drei Arbeitsweisen statt zwei -- die Knopfzeile ueber der Eingabe ist
    # die engste Stelle der ganzen Oberflaeche. Sie muss in eine Zeile passen,
    # ohne dass etwas umbricht oder hinausragt.
    log.pruefe(
        pg.eval_on_selector(".crow", "e => e.scrollWidth <= e.clientWidth + 1"),
        "die Knopfzeile passt in die Breite",
    )
    hoehen = pg.eval_on_selector_all(
        "#modes .mode", "els => els.map(e => Math.round(e.getBoundingClientRect().top))"
    )
    log.pruefe(len(set(hoehen)) == 1, f"alle drei Modi stehen nebeneinander ({hoehen})")
    log.pruefe(
        pg.eval_on_selector("#send", "e => e.getBoundingClientRect().width") >= 28,
        "der Senden-Knopf wird nicht zusammengedrückt",
    )


def ohne_bewegung(pg: Any, log: Protokoll) -> None:
    """Wer im System weniger Bewegung eingestellt hat, bekommt keine."""
    log.abschnitt("20. Weniger Bewegung")
    pg.click("#btn-settings")
    pg.wait_for_selector("#overlay.open", state="visible")
    laeuft = """(sel) => {
      const e = document.querySelector(sel);
      return e ? (e.getAnimations() || []).length : -1;
    }"""
    log.pruefe(pg.evaluate(laeuft, "#overlay .sheet") == 0, "keine Animation")
    pg.click("#cancel")
    pg.wait_for_timeout(400)
    log.pruefe(not pg.is_visible("#overlay.open"), "trotzdem geht das Fenster zu")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nur", default="", help="nur diese Abschnitte, mit Komma getrennt")
    parser.add_argument("--bilder", action="store_true", help="Bildschirmfotos ablegen")
    parser.add_argument("--ordner", default="", help="wohin die Bilder sollen")
    args = parser.parse_args()

    bilder = None
    if args.bilder:
        bilder = Path(args.ordner or tempfile.mkdtemp(prefix="rundgang-bilder-"))
        bilder.mkdir(parents=True, exist_ok=True)

    agent = FakeAgent()
    port = starte_server(agent)
    lege_chats_an()

    from playwright.sync_api import sync_playwright

    log = Protokoll()
    nur = {teil.strip() for teil in args.nur.split(",") if teil.strip()}
    fehler: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=CHROMIUM if Path(CHROMIUM).exists() else None
        )
        seite = browser.new_page(viewport={"width": 1340, "height": 900}, color_scheme="light")
        seite.on("pageerror", lambda e: fehler.append(f"Skriptfehler: {e}"))
        seite.on(
            "console",
            lambda m: fehler.append(f"Konsole: {m.text}") if m.type == "error" else None,
        )
        seite.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

        rundgang(seite, log, agent, bilder, nur)

        # Dieselbe Oberflaeche auf drei Groessen. Was auf dem eigenen
        # Bildschirm gut aussieht, muss es auf den anderen zweien nicht.
        if not nur or "geraete" in nur:
            for name, nummer, breit, hoch in (
                ("Handy", "19a", 390, 844),
                ("Tablet hoch", "19b", 820, 1180),
                ("Tablet quer", "19c", 1180, 820),
                ("Grosser Schirm", "19d", 1512, 900),
            ):
                gross = breit >= 900
                seite2 = browser.new_page(
                    viewport={"width": breit, "height": hoch},
                    is_mobile=not gross, has_touch=not gross,
                )
                seite2.on(
                    "pageerror",
                    lambda e, n=name: fehler.append(f"Skriptfehler ({n}): {e}"),
                )
                seite2.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                geraet(seite2, log, name, nummer, bilder)
                seite2.close()

        if not nur or "handy" in nur:
            klein = browser.new_page(
                viewport={"width": 390, "height": 780}, is_mobile=True, has_touch=True
            )
            klein.on("pageerror", lambda e: fehler.append(f"Skriptfehler (Handy): {e}"))
            klein.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            handy(klein, log, bilder)
            klein.close()

        if not nur or "ruhig" in nur:
            ruhig = browser.new_page(
                viewport={"width": 1200, "height": 800}, reduced_motion="reduce"
            )
            ruhig.on("pageerror", lambda e: fehler.append(f"Skriptfehler (ruhig): {e}"))
            ruhig.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            ohne_bewegung(ruhig, log)
            ruhig.close()

        log.abschnitt("21. Die Konsole")
        log.pruefe(not fehler, f"keine Fehler im Browser ({fehler[:3]})")
        browser.close()

    print(f"\n== {log.geprueft} geprüft, {len(log.probleme)} beanstandet")
    for problem in log.probleme:
        print("   -", problem)
    if bilder:
        print(f"\nBilder: {bilder}")
    return len(log.probleme)


if __name__ == "__main__":
    raise SystemExit(main())
