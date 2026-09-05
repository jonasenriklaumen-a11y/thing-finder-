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
    ) -> Any:
        self.gesehen.append(
            {
                "text": message,
                "modus": mode,
                "struktur": structured,
                "gegenprobe": recheck,
                "tiefe": effort,
                "web": online,
            }
        )
        text = message.lower()

        if mode == "code":
            self.on_event("code_model", {"model": "anthropic/claude-opus-5"})
        if structured:
            self.on_event("subagents", {"tasks": ["Teil eins", "Teil zwei"]})
            self.on_event("subagent_done", {"task": "Teil eins"})
        if "frag" in text:
            self.on_event("ask", {"question": "Welches Budget?", "options": ["bis 800 €"]})
            self.ask_handler("Welches Budget?", ["bis 800 €"])
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

    if dran("bewegung"):
        log.abschnitt("11. Bewegung")
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


def handy(pg: Any, log: Protokoll, bilder: Path | None) -> None:
    """Dasselbe noch einmal, aber auf einem schmalen Schirm."""
    log.abschnitt("12. Auf dem Handy")
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


def ohne_bewegung(pg: Any, log: Protokoll) -> None:
    """Wer im System weniger Bewegung eingestellt hat, bekommt keine."""
    log.abschnitt("13. Weniger Bewegung")
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

        log.abschnitt("14. Die Konsole")
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
