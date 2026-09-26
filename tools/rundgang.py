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
        #: Liegt das gestellte Modell schon im Speicher? Beim ersten Satz nicht.
        self.modell_geladen = False
        self.stats = self
        #: Ein gespeichertes Bildschirmfoto fuer den User mode (setzt der Rundgang).
        self.bildschirm = ""

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

        # Beim allerersten Satz liegt das oertliche Modell noch auf der
        # Platte. Genau wie beim echten Agenten wird das einmal gesagt --
        # und danach nie wieder, weil es dann im Speicher liegt.
        if not self.modell_geladen:
            self.modell_geladen = True
            self.on_event("model_loading", {"model": "ollama_chat/gemma3:12b"})
            self.on_event("model_ready", {"model": "ollama_chat/gemma3:12b", "seconds": 8.4})

        # Wie beim echten Agenten: was der Rechtsrahmen ablehnt, loest keine
        # einzige Suche aus -- die Absage kommt vor allem anderen.
        if "rechtsrahmen-probe" in text:
            self.on_event(
                "guard",
                {"stage": "anfrage", "tool": "", "rule": "name", "title": "Namensrecht",
                 "basis": "§ 12 BGB", "source": "pruefer"},
            )
            absage = "Das mache ich nicht — **Namensrecht** (§ 12 BGB)."
            self.on_event("answer_chunk", {"text": absage})
            self.on_event("done", {"tool_calls": 0, "hit_limit": False})
            return type("R", (), {"answer": absage, "stopped": False})()
        # Der User mode: eine Werkstatt als Desktop mit Internet. Die Attrappe
        # meldet, was die echte meldet -- Sperre, Desktop, Handgriffe, eine
        # Ablehnung -- und legt das Bildschirmfoto in den Chat.
        if "usermode-probe" in text:
            self.on_event("vm_start", {"runtime": "Docker (gehaertet)", "user_mode": True})
            self.on_event("vm_net", {"locked": True, "ranges": 6})
            self.on_event("vm_desktop", {"ready": True})
            self.on_event("desktop", {"action": "sieht", "detail": "den Bildschirm",
                                      "media_id": self.bildschirm})
            self.on_event("desktop", {"action": "oeffnet", "detail": "Webbrowser (Falkon)"})
            self.on_event("desktop", {"action": "klickt", "detail": "Link Impressum"})
            self.on_event("desktop", {"action": "abgelehnt", "detail": "Alle akzeptieren",
                                      "kind": "alle_akzeptieren"})
            antwort = "Die Seite ist offen; das Cookie-Banner habe ich abgelehnt."
            self.on_event("answer_chunk", {"text": antwort})
            self.on_event("done", {"tool_calls": 4, "hit_limit": False, "visuals": [
                {"kind": "desktop", "media_id": self.bildschirm,
                 "title": "Bildschirm der Werkstatt"}]})
            return type("R", (), {"answer": antwort, "stopped": False})()
        if "bild-probe" in text:
            # Automatische Modellwahl + ein erstelltes Bild (9.5.10).
            self.on_event("model_auto", {"model": "mistral/mistral-large-latest",
                                         "kategorie": "bild", "grund": "Bild erstellen",
                                         "bild": "Mistral Bildgenerierung"})
            self.on_event("image_create", {"prompt": "a lighthouse"})
            self.on_event("image_created", {"media_id": self.bildschirm,
                                            "modell": "Mistral Bildgenerierung"})
            antwort = "Hier ist dein Leuchtturm -- KI-erstellt."
            self.on_event("answer_chunk", {"text": antwort})
            self.on_event("done", {"tool_calls": 1, "hit_limit": False, "visuals": [
                {"kind": "erstellt", "media_id": self.bildschirm,
                 "title": "KI-Bild (Mistral Bildgenerierung)", "caption": "a lighthouse"}]})
            return type("R", (), {"answer": antwort, "stopped": False})()
        if mode in ("code", "pro"):
            self.on_event("code_model", {"model": "mistral/mistral-large-latest"})
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
        # Der Master: er stellt die Einheit auf, bewertet und schickt nach.
        if structured and mode == "pro":
            self.on_event(
                "master_plan",
                {
                    "agents": 44 if "/max" in text else 3,
                    "strong": 2,
                    "plan": "Erst die Anbieter, dann die Preise.",
                    "forced": "/max" in text,
                    "fallback": False,
                },
            )
        if structured:
            self.on_event(
                "subagents",
                {
                    "tasks": ["Was kostet Teil eins?", "Teil zwei"],
                    "roles": ["zahlen", "standard"],
                },
            )
            self.on_event("subagent_done", {"task": "Teil eins"})
        # Die vier Pruefer: im Pro-Modus, sobald gegengeprueft werden soll
        # oder die Denktiefe auf hoch steht.
        if structured and mode == "pro" and (recheck or effort == "high"):
            self.on_event("checkers", {"count": 4})
            self.on_event("check", {"task": "Was kostet Teil eins?"})
            self.on_event(
                "check_done", {"task": "Was kostet Teil eins?", "verdict": "ABWEICHUNG"}
            )
            self.on_event("checks_done", {"checked": 2, "deviations": 1})
        # Die Karte -- fuer das, was keine Suchmaschine kennt.
        if structured and mode == "pro":
            self.on_event("places", {"what": "Fahrradladen", "where": "Bremen"})
            self.on_event("places_done", {"what": "Fahrradladen", "hits": 3})
            self.on_event(
                "master_review",
                {
                    "verdict": "luecken",
                    "missing": ["Die Öffnungszeiten fehlen"],
                    "retries": 1,
                    "round": 1,
                },
            )
            self.on_event(
                "master_retry",
                {
                    "tasks": ["Öffnungszeiten über die Karte"],
                    "round": 1,
                    "missing": ["Die Öffnungszeiten fehlen"],
                },
            )
            self.on_event("master_review", {"verdict": "gut", "missing": [], "round": 2})
        if "frag" in text:
            self.on_event("ask", {"question": "Welches Budget?", "options": ["bis 800 €"]})
            self.ask_handler("Welches Budget?", ["bis 800 €"])
        # Der Fall, in dem Aquaticy merkt, dass seine Antwort nur eine Frage
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
        # Die zweite Runde gibt es im Pro-Modus nicht: dort haben die vier
        # Pruefer schon nebenher gegengelesen. Der echte Agent haelt es
        # genauso (`recheck_on`) -- der gestellte muss es auch, sonst prueft
        # der Rundgang eine Lage, die es nie gibt.
        if recheck and mode != "pro":
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
    os.environ.setdefault("AQUATICY_DATA_DIR", tempfile.mkdtemp(prefix="rundgang-"))
    os.environ.setdefault("AQUATICY_MODEL", "mistral/mistral-large-latest")
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-rundgang")
    from aquaticy import web

    # Am Bauplan, nicht am einzelnen Objekt: seit der Mehrbenutzer-Version
    # bekommt jedes Konto seine eigene Sitzung, und die baut sich ihren Agenten
    # selbst. Wer nur die Standardsitzung umbiegt, sieht den gestellten Agenten
    # nie wieder -- die Oberflaeche telefoniert dann wirklich nach draussen.
    def _gestellter_agent(self: Any) -> FakeAgent:
        # `_agent` mitsetzen wie das Original: der Abbruch greift bewusst
        # ohne Sperre auf dieses Feld zu und wuerde sonst ins Leere laufen.
        self._agent = agent
        return agent

    web.ChatSession.agent = _gestellter_agent            # type: ignore[method-assign]
    web.ChatSession.chat_id = lambda self: agent.session_id  # type: ignore[method-assign]
    web.SESSION._agent = agent

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


def anmelden(pg: Any, port: int) -> None:
    """Legt ein Konto an und geht durch die Tuer.

    Seit der Mehrbenutzer-Version steht vor der Oberflaeche eine Einwilligung
    und eine Anmeldung. Der Rundgang legt sich dafuer ein Ultra-Konto an: nur
    damit ist wirklich jeder Teil der Oberflaeche zu sehen, den er abgeht
    (seit 9.5.17 gehoeren Netz-Features und User mode zu Ultra).
    """
    from aquaticy.auth import ultra_code_for
    from aquaticy.config import get_settings

    pg.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
    pg.wait_for_selector("#consent-card:not([hidden])", timeout=10_000)
    pg.click("#consent-yes")
    pg.wait_for_selector("#login-card:not([hidden])", timeout=10_000)
    pg.check('input[name="plan"][value="ultra"]')
    pg.fill("#auth-username", "Rundgang")
    pg.fill("#auth-email", "rundgang@example.org")
    pg.fill("#auth-password", "rundgang-geheim")
    pg.fill("#auth-pro-code", ultra_code_for(get_settings().data_dir))
    pg.check("#auth-terms")
    pg.click("#auth-submit")
    # Nach dem Anlegen laedt die Seite selbst neu; dann ist die Tuer zu.
    pg.wait_for_selector("#auth-gate", state="hidden", timeout=15_000)


def warte_auf_text(pg: Any, auswahl: str, teil: str, sekunden: float = 10.0) -> str:
    """Wartet, bis *teil* im Text von *auswahl* steht -- und gibt den Text zurueck.

    ``wait_for_function`` scheidet aus: die Content-Security-Policy der Seite
    verbietet ``eval``, und genau so wertet Playwright die Bedingung aus.
    """
    ende = time.time() + sekunden
    text = ""
    while time.time() < ende:
        text = pg.inner_text(auswahl)
        if teil in text:
            break
        pg.wait_for_timeout(100)
    return text


def normales_konto(browser: Any, port: int, log: Protokoll, fehler: list[str]) -> None:
    """Ein normales Konto: die Rechts-Leitplanken bleiben an, was es auch tut.

    In einem eigenen Fenster ohne die Kekse des Pro-Kontos. Geprueft wird
    zweimal: die Oberflaeche sperrt den Schalter, und der Server lehnt ab,
    wenn jemand an der Oberflaeche vorbei trotzdem "aus" schickt.
    """
    log.abschnitt("20a. Normales Konto")
    kontext = browser.new_context(viewport={"width": 1340, "height": 900})
    pg = kontext.new_page()
    pg.on("pageerror", lambda e: fehler.append(f"Skriptfehler (normal): {e}"))
    pg.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
    pg.wait_for_selector("#consent-card:not([hidden])", timeout=10_000)
    pg.click("#consent-yes")
    pg.wait_for_selector("#login-card:not([hidden])", timeout=10_000)
    pg.click("#tab-register")
    pg.check('input[name="plan"][value="normal"]')
    pg.fill("#auth-username", "Normal")
    pg.fill("#auth-email", "normal@example.org")
    pg.fill("#auth-password", "normal-geheim")
    pg.check("#auth-terms")
    pg.click("#auth-submit")
    pg.wait_for_selector("#auth-gate", state="hidden", timeout=15_000)
    pg.click("#btn-settings")
    pg.wait_for_selector("#overlay.open", state="visible")
    pg.wait_for_timeout(700)
    # 9.5.14: Sitzung und Woche als Balken in Prozent -- keine Tokenzahlen.
    pg.click('#secnav button:has-text("Nutzung")')
    pg.wait_for_timeout(700)
    grenzen = pg.inner_text("#limits")
    log.pruefe("Aktuelle Sitzung" in grenzen and "Diese Woche" in grenzen
               and "% genutzt" in grenzen,
               f"Nutzung: zwei Balken in Prozent ({grenzen.splitlines()[:1]})")
    log.pruefe(pg.locator('#limits [role="progressbar"]').count() == 2,
               "beide als Fortschrittsbalken lesbar (für Screenreader)")
    log.pruefe("%" in pg.inner_text("#account-tokens")
               and "Token" not in pg.inner_text("#account-tokens"),
               f"im Konto steht Prozent: {pg.inner_text('#account-tokens')!r}")
    log.pruefe(not pg.inner_text("#zaehler").strip(),
               "Tokenzahlen sieht ein normales Konto nicht")
    # 9.5.17: "Eigene Modelle" -- erst da, wenn man oben selbst etwas hinzufuegt.
    # Die Schluessel bleiben dabei nur bei diesem Konto und nie im Browser.
    log.pruefe(pg.is_hidden("#sec-schluessel"),
               "Eigene Modelle ist nicht da, solange nichts Eigenes eingetragen ist")
    log.pruefe(pg.locator('#secnav button[data-section="sec-schluessel"]').is_hidden(),
               "und hat oben auch keine Sprungmarke")
    pg.click('#secnav button:has-text("Modell")')
    pg.wait_for_timeout(500)
    pg.select_option("#provider", "mistral")
    pg.wait_for_timeout(600)
    log.pruefe(pg.is_visible("#sec-schluessel"),
               "nach der Wahl eines Anbieters erscheint „Eigene Modelle“")
    sichtbar = pg.eval_on_selector_all(
        "#keys .key-row", "es => es.filter(e => !e.hidden).map(e => e.dataset.name)")
    log.pruefe(sichtbar == ["MISTRAL_API_KEY"], f"nur der passende Schlüssel steht da: {sichtbar}")
    pg.locator("#sec-schluessel").scroll_into_view_if_needed()
    log.pruefe(pg.inner_text("#keys-summary") == "Du hast keinen API-Schlüssel hinzugefügt.",
               "darunter steht, dass noch keiner hinterlegt ist")
    log.pruefe("nicht in dein Limit" in pg.inner_text("#keys-quota"),
               "und dass eigene Schlüssel nicht ins Limit zählen")
    zeile = pg.locator('#keys .key-row[data-name="MISTRAL_API_KEY"]')
    zeile.locator("input").fill("rundgang-mistral-4711x")
    zeile.locator("button", has_text="Speichern").click()
    satz = warte_auf_text(pg, "#keys-summary", "hinzugefügt: Mistral.")
    log.pruefe(satz == "Du hast einen API-Schlüssel hinzugefügt: Mistral.",
               f"nach dem Speichern: {satz!r}")
    status = pg.locator('#keys .key-row[data-name="MISTRAL_API_KEY"] .key-name span').inner_text()
    log.pruefe(status.startswith("Hinterlegt ••••711x"),
               f"nur die letzten vier Zeichen: {status!r}")
    log.pruefe("rundgang-mistral" not in pg.content(), "der Schlüssel steht nirgends auf der Seite")
    pg.once("dialog", lambda dialog: dialog.accept())
    pg.locator('#keys .key-row[data-name="MISTRAL_API_KEY"] button',
               has_text="Entfernen").click()
    satz = warte_auf_text(pg, "#keys-summary", "keinen")
    log.pruefe(satz == "Du hast keinen API-Schlüssel hinzugefügt.", "und wieder entfernt")
    log.pruefe(
        pg.is_checked("#legalguard") and pg.is_enabled("#legalguard"),
        "der Schalter ist an und nicht ausgegraut",
    )
    deckkraft = pg.eval_on_selector("#dev-settings", "e => getComputedStyle(e).opacity")
    log.pruefe(deckkraft == "1", f"die Dev settings sind nicht blass ({deckkraft})")
    pg.click('#secnav button:has-text("Dev settings")')
    pg.wait_for_timeout(700)
    pg.click("#legalguard")
    pg.wait_for_selector("#guardbox.open", state="visible")
    pg.wait_for_timeout(300)
    log.pruefe(
        "nur mit einem Ultra-Konto" in pg.inner_text("#guard-title"),
        f"beim Draufdruecken kommt der Hinweis: {pg.inner_text('#guard-title')[:60]!r}",
    )
    log.pruefe(not pg.is_visible("#guard-cancel"), "dort gibt es nichts abzubrechen")
    pg.click("#guard-ok")
    pg.wait_for_selector("#guardbox", state="hidden")
    log.pruefe(pg.is_checked("#legalguard"), "und die Leitplanken bleiben an")
    pg.click('#settings button[type="submit"]')
    pg.wait_for_timeout(1200)
    log.pruefe(
        "Gespeichert" in pg.inner_text("#savenote"),
        f"Speichern klappt trotzdem: {pg.inner_text('#savenote')[:50]!r}",
    )
    antwort = pg.evaluate(
        """async () => {
          const r = await fetch("/api/config", {method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({AQUATICY_LEGAL_GUARD: "false"})});
          return await r.json();
        }"""
    )
    log.pruefe(
        not antwort.get("ok") and "Ultra" in str(antwort.get("error", "")),
        f"am Formular vorbei lehnt der Server ab: {str(antwort.get('error'))[:60]!r}",
    )
    werte = pg.evaluate("async () => (await (await fetch('/api/config')).json()).values")
    log.pruefe(
        werte.get("AQUATICY_LEGAL_GUARD") == "true",
        "und danach steht der Schalter weiter auf an",
    )
    # 9.5.9: der User mode gehoert zu Pro -- gleiche Art wie die Leitplanken.
    pg.click("#btn-settings")
    pg.wait_for_selector("#overlay.open", state="visible")
    pg.wait_for_timeout(700)
    pg.click('#secnav button:has-text("Werkstatt")')
    pg.wait_for_timeout(700)
    log.pruefe(pg.is_enabled("#usermode") and not pg.is_checked("#usermode"),
               "User mode: der Schalter ist aus und nicht ausgegraut")
    pg.click("#usermode")
    pg.wait_for_selector("#guardbox.open", state="visible")
    log.pruefe("nur mit einem Ultra-Konto" in pg.inner_text("#guard-title"),
               "beim Draufdruecken kommt der Pro-Hinweis")
    pg.click("#guard-ok")
    pg.wait_for_selector("#guardbox", state="hidden")
    log.pruefe(not pg.is_checked("#usermode"), "und der User mode bleibt aus")
    # 9.5.11: wohin der Server Anfragen schickt, legt bei normalen Konten der Betreiber fest.
    log.pruefe(pg.eval_on_selector('[name="AQUATICY_API_BASE"]', "e => e.readOnly")
               and pg.eval_on_selector('[name="AQUATICY_SEARXNG_URL"]', "e => e.readOnly"),
               "Modell- und SearXNG-Adresse sind schreibgeschuetzt")
    antwort = pg.evaluate(
        """async () => {
          const r = await fetch("/api/config", {method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({AQUATICY_API_BASE: "http://192.168.1.1:11434"})});
          return await r.json();
        }"""
    )
    log.pruefe(not antwort.get("ok") and "Ultra" in str(antwort.get("error", "")),
               "am Formular vorbei lehnt der Server eine eigene Adresse ab")
    antwort = pg.evaluate(
        """async () => {
          const r = await fetch("/api/config", {method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({AQUATICY_VM_USER_MODE: "true"})});
          return await r.json();
        }"""
    )
    log.pruefe(not antwort.get("ok") and "Ultra" in str(antwort.get("error", "")),
               "am Formular vorbei lehnt der Server den User mode ab")
    pg.click("#btn-addons")
    pg.wait_for_selector("#addon-list .addon", timeout=10_000)
    pg.wait_for_timeout(400)
    pg.locator('.addon[data-id="whatsapp"] button', has_text="Installieren").click()
    pg.wait_for_selector("#guardbox.open", state="visible")
    log.pruefe("nur mit einem Ultra-Konto" in pg.inner_text("#guard-title"),
               "WhatsApp installieren: Hinweis statt Installation")
    pg.click("#guard-ok")
    pg.wait_for_selector("#guardbox", state="hidden")
    antwort = pg.evaluate(
        """async () => {
          const r = await fetch("/api/addons", {method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({action: "install", id: "signal"})});
          return await r.json();
        }"""
    )
    log.pruefe(not antwort.get("ok") and "Ultra" in str(antwort.get("error", "")),
               "am Fenster vorbei lehnt der Server Werkstatt-Add-ons ab")
    pg.locator('.addon[data-id="feeds"] button', has_text="Installieren").click()
    pg.wait_for_selector('.addon[data-id="feeds"] textarea', timeout=10_000)
    log.pruefe(True, "Add-ons ohne Werkstatt (RSS-Feeds) gehen auch mit dem normalen Konto")
    antwort = pg.evaluate(
        """async () => {
          const r = await fetch("/api/config", {method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({AQUATICY_AUTO_MODEL: "true"})});
          return await r.json();
        }"""
    )
    werte = pg.evaluate("async () => (await (await fetch('/api/config')).json()).values")
    log.pruefe(antwort.get("ok") and werte.get("AQUATICY_AUTO_MODEL") == "true",
               "die automatische Modellwahl geht auch mit dem normalen Konto")
    kontext.close()


def zweite_seite(browser: Any, quelle: Any, **optionen: Any) -> Any:
    """Ein weiteres Fenster -- angemeldet wie das erste.

    `new_page` legt jedes Mal einen frischen Kontext an, und der weiss nichts
    von der Anmeldung: die Oberflaeche zeigte dort wieder die Einwilligung.
    Also die Kekse des ersten Fensters mitnehmen.
    """
    kontext = browser.new_context(**optionen)
    kontext.add_cookies(quelle.context.cookies())
    return kontext.new_page()


def konto_einstellungen() -> Any:
    """Die Einstellungen des angemeldeten Kontos -- nicht die des Servers.

    Jedes Konto hat seinen eigenen Ordner. Wer den Verlauf in den globalen
    schreibt, legt ihn an einer Stelle ab, an der die Oberflaeche nie
    nachsieht -- die Seitenleiste bliebe leer.
    """
    from aquaticy import web

    konten = web.AUTH.accounts() if web.AUTH is not None else []
    if not konten:
        from aquaticy.config import get_settings

        return get_settings()
    return web.SESSIONS.get(konten[0]).settings()


def lege_chats_an() -> None:
    """Zwei Chats in den Verlauf, damit die Seitenleiste etwas zu zeigen hat."""
    from aquaticy.cache import Cache

    settings = konto_einstellungen()
    cache = Cache(settings.db_path, settings.cache_ttl_hours)
    cache.add_history("alt-1", "Welcher Laptop bis 1200 Euro?", "Antwort", {})
    cache.add_history("rundgang", "Was kostet ein Lastenrad?", "Antwort", {})
    # Ein Chat, in dem ein Auftrag nachts geantwortet hat: der soll leuchten,
    # bis ihn jemand oeffnet.
    cache.add_history("auftrag-nacht", "Was gibt es Neues?", "Einiges.", {})
    cache.mark_unread("auftrag-nacht", reason="auftrag")


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
        log.pruefe(
            pg.is_visible(".ki-hinweis") and "KI-generiert" in pg.inner_text(".ki-hinweis"),
            "unten steht, dass die Texte von einer KI kommen",
        )
        log.pruefe(pg.locator(".recent").count() == 3, "drei Chats in der Seitenleiste")
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
        # Der erste Satz an ein Modell, das erst in den Speicher muss.
        log.pruefe("[Modell]" in schritte, "der Ladehinweis steht beim ersten Satz da")
        log.pruefe("ist bereit (8.4s)" in schritte, "und wird danach zu einer Fertigmeldung")
        log.pruefe(
            "gemma3" not in schritte,
            "und nennt keinen Modellnamen -- der sagt niemandem etwas",
        )
        log.pruefe(
            pg.locator(".step.load").count() == 0,
            "das Pulsieren hoert auf, wenn das Modell bereit ist",
        )
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

    if dran("auswahl"):
        log.abschnitt("4g. Die Modellauswahl -- ganz, nicht halb")
        pg.click("#btn-model")
        pg.wait_for_selector("#picker-models", state="visible")
        pg.wait_for_timeout(500)
        hoehen = pg.eval_on_selector(
            "#picker-models",
            "e => [Math.round(e.scrollHeight), Math.round(e.clientHeight),"
            " Math.round(e.getBoundingClientRect().bottom), window.innerHeight]",
        )
        log.pruefe(hoehen[2] <= hoehen[3] + 1,
                   f"das Fenster endet im Bild ({hoehen[2]} von {hoehen[3]})")
        # Der Fuss ist die letzte Zeile -- kommt man dort hin, kommt man
        # ueberall hin. Frueher scrollte nur die Modellliste, und alles
        # darunter war unerreichbar.
        pg.locator("#picker-models .picker-foot").scroll_into_view_if_needed()
        pg.wait_for_timeout(300)
        kasten = pg.locator("#picker-models .picker-foot").bounding_box()
        log.pruefe(kasten is not None and kasten["y"] + kasten["height"] <= hoehen[3] + 1,
                   "und der Fuss ist erreichbar")
        log.pruefe(pg.is_visible("#recheck"), "die Schalter auch")
        # Die Ueberschrift steht in Grossbuchstaben -- das macht das CSS.
        log.pruefe(pg.inner_text("#picker-head").lower().startswith("modell"),
                   "im Standardmodus stehen alle Modelle zur Wahl")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)

        pg.click('#modes .mode[data-mode="pro"]')
        pg.wait_for_timeout(700)
        pg.click("#btn-model")
        pg.wait_for_selector("#picker-models", state="visible")
        pg.wait_for_timeout(500)
        log.pruefe(pg.inner_text("#picker-head").lower().startswith("stärkstes"),
                   f"im Pro-Modus nur die stärksten ({pg.inner_text('#picker-head')!r})")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)

        # Im Code-Modus ist "am staerksten" ein anderes Modell: eines fuers
        # Programmieren. Das soll auch dranstehen.
        pg.click('#modes .mode[data-mode="code"]')
        pg.wait_for_timeout(700)
        pg.click("#btn-model")
        pg.wait_for_selector("#picker-models", state="visible")
        pg.wait_for_timeout(500)
        log.pruefe("code" in pg.inner_text("#picker-head").lower(),
                   f"im Code-Modus die Code-Modelle ({pg.inner_text('#picker-head')!r})")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)
        pg.click('#modes .mode[data-mode="normal"]')
        pg.wait_for_timeout(700)

    if dran("ungelesen"):
        log.abschnitt("4h. Ein Auftrag hat geantwortet")
        leuchtet = pg.locator(".recent.neu")
        log.pruefe(leuchtet.count() == 1, f"ein Chat leuchtet ({leuchtet.count()})")
        log.pruefe("Neues" in leuchtet.inner_text(), "und zwar der vom Auftrag")
        log.pruefe(
            pg.eval_on_selector("body", "e => e.classList.contains('hat-neues')"),
            "der Knopf zur Leiste trägt den Punkt",
        )
        leuchtet.locator(".name").click()
        pg.wait_for_timeout(1200)
        log.pruefe(pg.locator(".recent.neu").count() == 0,
                   "geöffnet heißt gelesen -- danach sieht er aus wie jeder andere")
        log.pruefe(
            not pg.eval_on_selector("body", "e => e.classList.contains('hat-neues')"),
            "und der Punkt am Knopf ist weg",
        )

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
        log.pruefe(pg.is_visible("#recheck"), "Gegenprüfen gibt es auch hier")
        # Derselbe Schalter, andere Bedeutung -- und genau das steht dran.
        erklaerung = pg.inner_text('label[for="recheck"]')
        log.pruefe("Vier Prüfer" in erklaerung,
                   f"und es steht dran, was er hier heißt ({erklaerung[:60]!r})")
        log.pruefe("doppelt so lang" not in erklaerung,
                   "die Erklärung aus dem Standardmodus ist weg")
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
        schritte = pg.inner_text(".steps >> nth=-1")
        log.pruefe("[Pro]" in schritte, "das stärkste Modell wird genannt")
        log.pruefe("[Code]" not in schritte, "und zwar als Pro, nicht als Code")
        log.pruefe("· Zahlen" in schritte, "die Rolle steht an der Teilfrage")
        log.pruefe("[Master]" in schritte, "der Master stellt die Einheit auf")
        log.pruefe("3 Agenten" in schritte and "starken Modell" in schritte,
                   "mit Zahl und starken Agenten")
        log.pruefe("Erst die Anbieter" in schritte, "und sagt, was er vorhat")
        log.pruefe("[Karte]" in schritte, "die Karte wird befragt")
        log.pruefe("Lücken" in schritte, "er bewertet die Rückmeldungen")
        log.pruefe("[Nachrunde]" in schritte, "und schickt nach")
        log.pruefe("die Rückmeldungen tragen" in schritte, "am Ende trägt es")

        # Und jetzt die vier Pruefer: Schalter an, noch einmal fragen.
        pg.click("#btn-model")
        pg.wait_for_timeout(400)
        pg.check("#recheck")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(500)
        log.pruefe("4 Prüfer" in pg.inner_text("#status"),
                   f"die Kopfzeile sagt es ({pg.inner_text('#status')})")
        pg.fill("#input", "Was kosten Lastenräder in Bremen?")
        pg.click("#send")
        pg.wait_for_timeout(1400)
        log.pruefe(agent.gesehen[-1]["gegenprobe"] is True,
                   "der Schalter kommt an")
        schritte = pg.inner_text(".steps >> nth=-1")
        log.pruefe("[Prüfer]" in schritte, "die Prüfer melden sich")
        log.pruefe("prüfen mit, während" in schritte,
                   "und sagen, dass sie nebenher laufen")
        log.pruefe("Abweichung gefunden" in schritte, "ihr Urteil steht da")
        log.pruefe("[Gegenprobe]" not in schritte,
                   "und die zweite Runde entfällt dafür")
        vermerk = pg.inner_text(".msg.bot >> nth=-1")
        log.pruefe("Gegengeprüft" in vermerk and "abweichenden" in vermerk,
                   "und an der Antwort steht der Vermerk")
        foto("04c-pro")
        pg.click("#btn-model")
        pg.wait_for_timeout(400)
        pg.uncheck("#recheck")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)
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

    if dran("max"):
        log.abschnitt("4e. /max stellt die volle Mannschaft auf")
        pg.click('#modes .mode[data-mode="pro"]')
        pg.wait_for_timeout(600)
        pg.fill("#input", "/max Was kosten Lastenräder in Bremen?")
        pg.click("#send")
        pg.wait_for_timeout(1300)
        letzte = agent.gesehen[-1]
        log.pruefe(letzte["text"].startswith("/max"),
                   f"der Befehl kommt beim Agenten an ({letzte['text'][:20]!r})")
        schritte = pg.inner_text(".steps >> nth=-1")
        log.pruefe("44 Agenten" in schritte, "und es sind alle")
        log.pruefe("volle Mannschaft" in schritte, "die Anzeige sagt es")
        # Ohne Frage ist es keine Recherche, sondern eine Erklärung.
        pg.fill("#input", "/max")
        pg.click("#send")
        pg.wait_for_timeout(900)
        log.pruefe("volle Mannschaft" in pg.inner_text(".msg.bot >> nth=-1"),
                   "/max allein erklärt sich")
        pg.click('#modes .mode[data-mode="normal"]')
        pg.wait_for_timeout(700)

    if dran("weiterlaufen"):
        log.abschnitt("4f. Die Anfrage überlebt das Weggehen")
        pg.fill("#input", "Das dauert langsam etwas")
        pg.click("#send")
        pg.wait_for_timeout(700)
        log.pruefe(pg.is_visible("#stop"), "die Anfrage läuft")
        # Weg von der Seite -- und zurück. Frueher war die Anfrage damit weg.
        pg.reload()
        # Nicht auf die Begruessung warten: sie ist weg, sobald der
        # wiederaufgenommene Lauf im Chat steht. Genau darum geht es hier.
        pg.wait_for_selector("#input")
        pg.wait_for_timeout(1500)
        schritte = pg.inner_text(".steps >> nth=-1")
        log.pruefe("[Weiter]" in schritte, "der Lauf wird wieder aufgenommen")
        log.pruefe(pg.locator(".msg.user").count() >= 1, "die Frage steht wieder da")
        pg.wait_for_selector("#stop", state="hidden", timeout=30000)
        log.pruefe("Eine Antwort" in pg.inner_text(".msg.bot >> nth=-1"),
                   "und die Antwort kommt an, ohne dass jemand neu fragt")
        # Ein zweites Laden holt denselben Lauf nicht noch einmal.
        pg.reload()
        pg.wait_for_selector("#input")
        pg.wait_for_timeout(1000)
        log.pruefe(pg.locator(".msg").count() == 0,
                   "wer ihn zu Ende gesehen hat, bekommt ihn nicht wieder")

    if dran("vorschlaege"):
        log.abschnitt("4d. Vorschläge passen zum Modus")
        pg.reload()
        pg.wait_for_selector("#chips")
        vorschlag = pg.inner_text("#chips")
        # Wie bei den Coding-Vorschlaegen: die drei gezeigten wechseln bei
        # jedem Aufruf. Nach einem festen Wort zu suchen war ein Muenzwurf --
        # geprueft wird, dass jeder Vorschlag aus der richtigen Liste stammt.
        aus_liste = pg.evaluate(
            """() => [...document.querySelectorAll("#chips .chip")]
                     .every(c => TEXTE.suggestions.normal.includes(c.textContent.trim()))"""
        )
        log.pruefe(aus_liste,
                   f"im Standardmodus geht es ums Suchen ({vorschlag[:40]!r})")
        log.pruefe(not pg.is_visible("#chips-code"), "die Coding-Vorschläge sind weg")
        pg.click('#modes .mode[data-mode="code"]')
        pg.wait_for_timeout(400)
        log.pruefe(pg.is_visible("#chips-code"), "im Code-Modus stehen die anderen da")
        log.pruefe(not pg.is_visible("#chips"), "und die Suchvorschläge sind weg")
        # Die Vorschlaege wechseln bei jedem Aufruf. Nach einem festen Wort zu
        # suchen war deshalb ein Muenzwurf -- gepruefte wird stattdessen, dass
        # jeder gezeigte Vorschlag wirklich aus der Coding-Liste stammt.
        aus_liste = pg.evaluate(
            """() => [...document.querySelectorAll("#chips-code .chip")]
                     .every(c => TEXTE.suggestions.code.includes(c.textContent.trim()))"""
        )
        code_text = pg.inner_text("#chips-code")
        log.pruefe(aus_liste, f"es geht ums Programmieren ({code_text[:40]!r})")
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

    if dran("rechtsrahmen"):
        log.abschnitt("6a. Rechtsrahmen")
        pg.click("#btn-new")
        pg.wait_for_timeout(700)
        pg.fill("#input", "rechtsrahmen-probe: schreib in fremdem Namen")
        pg.click("#send")
        pg.wait_for_selector("#stop", state="hidden", timeout=25000)
        pg.wait_for_timeout(700)
        schritte = pg.inner_text(".steps >> nth=-1")
        log.pruefe(
            "[Rechtsrahmen]" in schritte and "§ 12 BGB" in schritte,
            f"die Absage nennt Regel und Rechtsgrundlage: {schritte[:70]!r}",
        )
        log.pruefe(
            pg.locator(".steps >> nth=-1").locator(".step.warn").count() >= 1,
            "und ist als Warnung markiert",
        )
        log.pruefe(
            "[Suche]" not in schritte,
            "vor der Absage lief keine einzige Suche",
        )
        log.pruefe(
            "Namensrecht" in pg.inner_text(".msg.bot >> nth=-1"),
            "die Antwort sagt, warum",
        )
        pg.click("#btn-new")
        pg.wait_for_timeout(700)

    if dran("usermode"):
        log.abschnitt("6b. User mode")
        # Ein echtes JPEG, gespeichert wie jedes Bildschirmfoto der Werkstatt:
        # die Oberflaeche soll es aus dem Speicher des Kontos laden.
        from aquaticy.media import save_snapshot

        foto_bytes = pg.screenshot(type="jpeg", quality=60,
                                   clip={"x": 0, "y": 0, "width": 320, "height": 200})
        agent.bildschirm = save_snapshot(konto_einstellungen().data_dir, foto_bytes,
                                         "image/jpeg")
        pg.click("#btn-new")
        pg.wait_for_timeout(700)
        pg.fill("#input", "usermode-probe: oeffne die Seite")
        pg.click("#send")
        pg.wait_for_selector("#stop", state="hidden", timeout=25000)
        pg.wait_for_timeout(900)
        schritte = pg.inner_text(".steps >> nth=-1")
        log.pruefe("Internet an, Heimnetz gesperrt" in schritte,
                   "die Netzsperre steht in den Schritten")
        log.pruefe("Desktop bereit" in schritte, "der Desktop meldet sich")
        log.pruefe("[Desktop]" in schritte and "klickt: Link Impressum" in schritte,
                   f"die Handgriffe stehen da: {schritte[-120:]!r}")
        log.pruefe(
            pg.locator(".steps >> nth=-1").locator(".step.warn").filter(
                has_text="abgelehnt").count() == 1,
            "eine Ablehnung ist als Warnung markiert",
        )
        log.pruefe(
            pg.locator(".steps >> nth=-1").locator("img.shot").count() == 1,
            "das Bildschirmfoto steht klein beim Schritt",
        )
        karte = pg.locator(".result-card.visual").filter(has_text="Bildschirm der Werkstatt")
        log.pruefe(karte.count() == 1, "und gross unter der Antwort")
        log.pruefe(
            karte.locator("img").count() == 1
            and pg.eval_on_selector(".result-card.visual img", "e => e.naturalWidth") > 0,
            "das Bild laedt wirklich",
        )
        log.pruefe(
            karte.locator("a.source").count() == 0,
            "ohne 'Bildquelle' -- ein Bildschirmfoto hat keine Webadresse",
        )
        pg.click("#btn-new")
        pg.wait_for_timeout(700)
        log.abschnitt("6d. Automatische Modellwahl und KI-Bild")
        pg.fill("#input", "bild-probe: Erstelle mir ein Bild von einem Leuchtturm")
        pg.click("#send")
        pg.wait_for_selector("#stop", state="hidden", timeout=25000)
        pg.wait_for_timeout(900)
        schritte = pg.inner_text(".steps >> nth=-1")
        log.pruefe("[Modell]" in schritte and "Bild erstellen" in schritte,
                   f"die Wahl steht im Verlauf: {schritte[-140:]!r}")
        log.pruefe("erstellt mit Mistral Bildgenerierung" in schritte, "und wer gemalt hat")
        karte = pg.locator(".result-card.visual").filter(has_text="KI-Bild")
        log.pruefe(karte.count() == 1, "das Bild steht als Karte unter der Antwort")
        log.pruefe("KI-erstellt" in karte.inner_text(), "beschriftet als KI-erstellt")
        log.pruefe(karte.locator("a", has_text="Herunterladen").count() == 1,
                   "mit Herunterladen statt einer Bildquelle")
        log.pruefe(pg.eval_on_selector(".result-card.visual img", "e => e.naturalWidth") > 0,
                   "das Bild laedt wirklich")
        pg.click("#btn-new")
        pg.wait_for_timeout(700)

    if dran("addons"):
        log.abschnitt("6c. Add-ons und Login-Apps")
        pg.click("#btn-settings")
        pg.wait_for_selector("#overlay.open", state="visible")
        pg.wait_for_timeout(700)
        pg.click('#secnav button:has-text("Werkstatt")')
        pg.wait_for_timeout(700)
        neben = pg.evaluate("""() => {
          const s = document.querySelector('#usermode').getBoundingClientRect();
          const k = document.querySelector('#btn-addons').getBoundingClientRect();
          const l = document.querySelector('#btn-loginapps').getBoundingClientRect();
          return {gleicheZeile: Math.abs(s.top - k.top) < 30, darunter: l.top > s.top};
        }""")
        log.pruefe(neben["gleicheZeile"], "der Add-ons-Knopf steht beim User-mode-Schalter")
        log.pruefe(neben["darunter"], "die Login-Apps stehen darunter")
        pg.click("#btn-addons")
        pg.wait_for_selector("#addonbox.open", state="visible")
        pg.wait_for_selector("#addon-list .addon", timeout=10_000)
        pg.wait_for_timeout(500)
        karten = pg.locator("#addon-list .addon")
        namen = [karten.nth(i).locator(".addon-name").inner_text() for i in range(karten.count())]
        log.pruefe(
            {"GitHub", "WhatsApp Web", "Signal", "Blender"} <= set(namen),
            f"die gewuenschten Add-ons sind da: {namen}",
        )
        log.pruefe(pg.locator(".addon-badge", has_text="Vorschlag").count() >= 2,
                   "und Vorschlaege dazu")
        log.pruefe(
            all(karten.nth(i).locator("button", has_text="Installieren").count() == 1
                for i in range(karten.count())),
            "jedes Add-on hat einen Installieren-Knopf -- auch WhatsApp Web",
        )
        whatsapp = pg.locator('.addon[data-id="whatsapp"]')
        log.pruefe("User mode" in whatsapp.inner_text(),
                   "WhatsApp sagt, dass es den User mode braucht")
        wetter = pg.locator('.addon[data-id="wetter"]')
        wetter.locator("button", has_text="Installieren").click()
        pg.wait_for_selector('.addon[data-id="wetter"] .schalter', timeout=10_000)
        wetter = pg.locator('.addon[data-id="wetter"]')
        log.pruefe(wetter.locator(".schalter").is_checked(), "installiert ist es gleich an")
        log.pruefe(wetter.locator("button", has_text="Deinstallieren").count() == 1,
                   "und laesst sich wieder deinstallieren")
        log.pruefe("Keine Anmeldung" in wetter.inner_text(), "darunter: wie man sich anmeldet")
        log.pruefe(wetter.locator(".addon-rechte .seg button").count() == 2,
                   "darunter die Rechte als einfache Wahl")
        wetter.locator(".seg button", has_text="Nur mein Ort").click()
        pg.wait_for_timeout(900)
        knopf = pg.locator('.addon[data-id="wetter"] .seg button', has_text="Nur mein Ort")
        log.pruefe(knopf.get_attribute("aria-checked") == "true", "die Wahl ist markiert")
        rechte = pg.evaluate("""async () => (await (await fetch('/api/addons')).json())
            .addons.find(a => a.id === 'wetter').rechte""")
        log.pruefe(rechte.get("orte") == "meiner", "und beim Server gespeichert")
        wetter.locator(".schalter").click()
        pg.wait_for_timeout(900)
        log.pruefe(not pg.locator('.addon[data-id="wetter"] .schalter').is_checked(),
                   "ausschalten klappt")
        stand = pg.evaluate("async () => (await (await fetch('/api/addons')).json()).active")
        log.pruefe("wetter" not in stand, "und der Server sieht es auch aus")
        pg.locator('.addon[data-id="github"] button', has_text="Installieren").click()
        pg.wait_for_selector('.addon[data-id="github"] input[type=password]', timeout=10_000)
        log.pruefe(True, "nach dem Installieren von GitHub steht das Token-Feld darunter")
        pg.locator('.addon[data-id="wetter"] button', has_text="Deinstallieren").click()
        pg.wait_for_selector("#guardbox.open", state="visible")
        log.pruefe("deinstallieren" in pg.inner_text("#guard-title"),
                   "vor dem Deinstallieren wird gefragt")
        pg.click("#guard-ok")
        pg.wait_for_selector("#guardbox", state="hidden")
        pg.wait_for_timeout(900)
        log.pruefe(
            pg.locator('.addon[data-id="wetter"] button', has_text="Installieren").count() == 1,
            "deinstalliert steht wieder Installieren da",
        )
        foto("addons")
        pg.click("#addon-close")
        pg.wait_for_selector("#addonbox", state="hidden")
        pg.click("#btn-loginapps")
        pg.wait_for_selector("#screenbox.open", state="visible")
        pg.wait_for_timeout(900)
        log.pruefe("läuft gerade nicht" in pg.inner_text("#screen-leer"),
                   "Login-Apps: ohne Werkstatt ein ehrlicher Hinweis statt eines kaputten Bildes")
        log.pruefe(pg.get_attribute("#screen-text", "type") == "password",
                   "das Tippfeld zeigt Passwoerter nicht")
        pg.click("#screen-close")
        pg.wait_for_selector("#screenbox", state="hidden")
        pg.click('#secnav button:has-text("Dev settings")')
        pg.wait_for_timeout(700)
        log.pruefe(pg.locator('#dev-settings [name="AQUATICY_AUTO_MODEL"]').count() == 1,
                   "Dev settings: Schalter 'Modell automatisch wählen'")
        pg.check("#automodel")
        pg.click('#settings button[type="submit"]')
        pg.wait_for_timeout(1500)
        log.pruefe("Auto · " in pg.inner_text("#model-name"),
                   f"oben steht Auto: {pg.inner_text('#model-name')!r}")
        pg.click("#btn-settings")
        pg.wait_for_selector("#overlay.open", state="visible")
        pg.wait_for_timeout(700)
        pg.uncheck("#automodel")
        pg.click('#settings button[type="submit"]')
        pg.wait_for_timeout(1500)
        log.pruefe("Auto" not in pg.inner_text("#model-name"), "und wieder aus")
        if pg.is_visible("#overlay.open"):
            pg.click("#cancel")
        pg.wait_for_timeout(500)

    if dran("einstellungen"):
        log.abschnitt("7. Einstellungen")
        pg.click("#btn-settings")
        pg.wait_for_selector("#overlay.open", state="visible")
        pg.wait_for_timeout(700)
        marken = pg.locator("#secnav button").count()
        abschnitte = pg.locator("#settings fieldset").count()
        log.pruefe(marken == abschnitte, f"{marken} Sprungmarken zu {abschnitte} Abschnitten")
        for feld in ("AQUATICY_MODEL", "AQUATICY_CODE_MODEL", "AQUATICY_LOCATION",
                     "AQUATICY_LAN_SUBNET", "AQUATICY_STORAGE_URL"):
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
        # Werkstatt: der User mode -- ein Schalter mit Erklaerung.
        log.pruefe(
            pg.locator('#workshop-settings [name="AQUATICY_VM_USER_MODE"]').count() == 1,
            "der User mode steht bei der Werkstatt",
        )
        log.pruefe(not pg.is_checked("#usermode"), "und ist ab Werk aus")
        log.pruefe("Bilder" in pg.inner_text("#usermode-note"),
                   "der Hinweis nennt, was er braucht (ein Modell, das Bilder versteht)")
        # Dev settings: der Schalter fuer die Rechts-Leitplanken, nur mit Pro.
        log.pruefe(
            pg.locator('#secnav button:has-text("Dev settings")').count() == 1,
            "die Dev settings haben eine Sprungmarke",
        )
        log.pruefe(
            pg.is_checked("#legalguard") and pg.is_enabled("#legalguard"),
            "die Rechts-Leitplanken stehen auf an, und Pro darf sie umstellen",
        )
        regeln = pg.locator("#legal-rule-list li").count()
        log.pruefe(regeln == 11, f"die Regeln stehen in der Liste ({regeln})")
        pg.click('#secnav button:has-text("Dev settings")')
        pg.wait_for_timeout(700)
        pg.click("#legalguard")
        pg.wait_for_selector("#guardbox.open", state="visible")
        pg.wait_for_timeout(300)
        log.pruefe(
            "wirklich sicher" in pg.inner_text("#guard-title")
            and pg.inner_text("#guard-cancel").strip() == "Abbrechen"
            and pg.inner_text("#guard-ok").strip() == "Weiter",
            "Ausschalten fragt: wirklich sicher? -- mit Abbrechen und Weiter",
        )
        log.pruefe(pg.is_checked("#legalguard"), "solange gefragt wird, bleibt er an")
        pg.click("#guard-cancel")
        pg.wait_for_selector("#guardbox", state="hidden")
        log.pruefe(
            pg.is_checked("#legalguard"),
            "wer abbricht, behaelt die Leitplanken",
        )
        pg.click("#legalguard")
        pg.wait_for_selector("#guardbox.open", state="visible")
        pg.wait_for_timeout(300)
        pg.click("#guard-ok")
        pg.wait_for_selector("#guardbox", state="hidden")
        log.pruefe(not pg.is_checked("#legalguard"), "mit Weiter geht er aus")
        pg.click("#legalguard")
        pg.wait_for_timeout(400)
        log.pruefe(
            pg.is_checked("#legalguard") and not pg.is_visible("#guardbox.open"),
            "Einschalten geht ohne Rueckfrage",
        )
        pg.click("#legalguard")
        pg.wait_for_selector("#guardbox.open", state="visible")
        pg.keyboard.press("Escape")
        pg.wait_for_selector("#guardbox", state="hidden")
        log.pruefe(
            pg.is_checked("#legalguard") and pg.is_visible("#overlay.open"),
            "Escape bricht nur die Rueckfrage ab, die Einstellungen bleiben offen",
        )
        foto("07-einstellungen")
        pg.click("#cancel")
        pg.wait_for_timeout(500)
        log.pruefe(not pg.is_visible("#overlay.open"), "das Fenster geht wieder zu")

    if dran("aussehen"):
        log.abschnitt("8. Design")
        pg.click("#btn-theme")
        pg.wait_for_selector("#themebox.open", state="visible")
        log.pruefe(pg.inner_text("#theme-title") == "Design", "das Fenster heißt Design")
        namen = pg.eval_on_selector_all("#palettes .pal .pname", "es => es.map(e => e.textContent)")
        log.pruefe(namen == ["Standard", "Schlicht", "Design selber erstellen"],
                   f"nur Standard, Schlicht und der eigene Designer: {namen}")
        # Standard und Schlicht, jeweils hell und dunkel: vier verschiedene Untergründe.
        farben = set()
        for modus in ("light", "dark"):
            pg.click(f'[data-tmode="{modus}"]')
            pg.wait_for_timeout(150)
            for pid in ("", "mono"):
                pg.click(f'.pal[data-palette="{pid}"]')
                pg.wait_for_timeout(120)
                gesetzt = pg.get_attribute("html", "data-palette") or ""
                if gesetzt != pid:
                    log.pruefe(False, f"Design {pid or 'standard'} wird nicht gesetzt")
                farben.add(
                    pg.eval_on_selector("body", "e => getComputedStyle(e).backgroundColor")
                )
        log.pruefe(len(farben) == 4, f"{len(farben)} verschiedene Untergründe")
        # Schlicht: hell weiß mit schwarzer Schrift, dunkel genau umgekehrt.
        pg.click('[data-tmode="light"]')
        pg.click('.pal[data-palette="mono"]')
        pg.wait_for_timeout(400)
        hell = pg.eval_on_selector("body", "e => [getComputedStyle(e).backgroundColor,"
                                           " getComputedStyle(e).color]")
        log.pruefe(hell == ["rgb(255, 255, 255)", "rgb(0, 0, 0)"],
                   f"Schlicht hell: weiß mit schwarzer Schrift {hell}")
        pg.click('[data-tmode="dark"]')
        pg.wait_for_timeout(400)
        dunkel = pg.eval_on_selector("body", "e => [getComputedStyle(e).backgroundColor,"
                                             " getComputedStyle(e).color]")
        log.pruefe(dunkel == ["rgb(0, 0, 0)", "rgb(255, 255, 255)"],
                   f"Schlicht dunkel: genau umgekehrt {dunkel}")
        # Der eigene Designer.
        pg.click('[data-tmode="light"]')
        pg.click('.pal[data-palette="custom"]')
        pg.wait_for_selector("#designer:not([hidden])", timeout=5_000)
        log.pruefe(True, "Design selber erstellen öffnet die Farbwahl")
        pg.fill("#dz-accent", "#1e5bd6")
        pg.fill("#dz-bg", "#ffffff")
        pg.fill("#dz-sidebar", "#2b2b2b")
        pg.wait_for_timeout(600)
        log.pruefe(pg.get_attribute("html", "data-palette") == "custom", "das eigene Design gilt")
        akzent = pg.eval_on_selector(
            "html", "e => getComputedStyle(e).getPropertyValue('--accent').trim()")
        log.pruefe(akzent == "#1e5bd6", f"der Akzent ist blau statt grün ({akzent})")
        leiste = pg.eval_on_selector("aside", "e => [getComputedStyle(e).backgroundColor,"
                                               " getComputedStyle(e).color]")
        log.pruefe(leiste[0] == "rgb(43, 43, 43)", f"die Seitenleiste ist dunkelgrau {leiste}")
        log.pruefe(leiste[1] == "rgb(255, 255, 255)",
                   "und ihre Schrift wird hell, damit man sie lesen kann")
        grund = pg.eval_on_selector("body", "e => getComputedStyle(e).backgroundColor")
        log.pruefe(grund == "rgb(255, 255, 255)", f"der Hintergrund ist weiß ({grund})")
        pg.wait_for_timeout(500)
        gemerkt = pg.evaluate("async () => (await (await fetch('/api/prefs')).json())")
        log.pruefe(gemerkt.get("palette") == "custom"
                   and gemerkt.get("design", {}).get("sidebar") == "#2b2b2b",
                   "und beim Konto gespeichert")
        foto("08-design")
        pg.reload(wait_until="networkidle")
        pg.wait_for_timeout(500)
        nachher = pg.eval_on_selector(
            "html", "e => getComputedStyle(e).getPropertyValue('--accent').trim()")
        log.pruefe(nachher == "#1e5bd6", "nach dem Neuladen ist es noch da")
        pg.click("#btn-theme")
        pg.wait_for_selector("#themebox.open", state="visible")
        pg.click("#theme-reset")
        pg.wait_for_timeout(300)
        log.pruefe(pg.get_attribute("html", "data-palette") is None, "Standard kommt zurück")
        akzent = pg.eval_on_selector(
            "html", "e => getComputedStyle(e).getPropertyValue('--accent').trim()")
        log.pruefe(akzent in ("#3d7d55", "#7cb894"), f"und mit ihm das Grün ({akzent})")
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
        pg.fill("#input", "Hallo")
        log.pruefe(not pg.eval_on_selector("#input", "e => e.classList.contains('is-command')"),
                   "normaler Text leuchtet nicht")
        pg.fill("#input", "/max")
        farbe = pg.eval_on_selector("#input", "e => [e.classList.contains('is-command'),"
                                              " getComputedStyle(e).color,"
                                              " getComputedStyle(e).textShadow]")
        akzent = pg.eval_on_selector(
            "html", "e => getComputedStyle(e).getPropertyValue('--accent-text').trim()")
        log.pruefe(farbe[0] and farbe[2] != "none",
                   f"ein /Befehl leuchtet im Akzentton ({farbe[1]}, Akzent {akzent})")
        pg.fill("#input", "/help")
        pg.click("#send")
        pg.wait_for_timeout(300)
        log.pruefe(not pg.eval_on_selector("#input", "e => e.classList.contains('is-command')"),
                   "nach dem Absenden leuchtet die leere Zeile nicht mehr")
        pg.wait_for_timeout(900)
        log.pruefe("/clear" in pg.inner_text("#thread"), "/help zeigt die Befehle")
        # 9.5.13: /export kommt als Download im Browser an -- nicht als Pfad
        # auf dem Server, mit dem man an einem anderen Geraet nichts anfaengt.
        try:
            with pg.expect_download(timeout=8000) as geladen:
                pg.fill("#input", "/export md")
                pg.click("#send")
            datei = geladen.value
            inhalt = Path(datei.path()).read_text(encoding="utf-8")
            log.pruefe(datei.suggested_filename.endswith(".md") and "Lastenrad" in inhalt,
                       f"/export lädt die Datei herunter ({datei.suggested_filename})")
        except Exception as exc:  # der Rundgang meldet, statt abzubrechen
            log.pruefe(False, f"/export lädt die Datei herunter ({type(exc).__name__})")
        pg.wait_for_timeout(300)
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
            [name for name in gemerkt if name != "aquaticy-token"] == [],
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
        log.abschnitt("13. Was Aquaticy über mich weiß")
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
        # Seit 9.5.16: kurze Takte gibt es fuer Recherchen nicht -- jeder Lauf
        # waere ein neuer Chat.
        log.pruefe(
            pg.eval_on_selector('#job-rhythm option[value="always"]', "o => o.disabled"),
            "eine Recherche läuft nicht „die ganze Zeit“",
        )
        # Durchgehend beobachten: keine Uhrzeit, aber ein Wort zu den Kosten.
        pg.select_option("#job-kind", "visual")
        pg.wait_for_timeout(200)
        pg.select_option("#job-rhythm", "always")
        pg.wait_for_timeout(200)
        log.pruefe(not pg.is_visible("#job-zeit-feld"), "durchgehend hat keine Uhrzeit")
        log.pruefe(pg.is_visible("#job-takt-hinweis"), "und sagt, dass es Token kostet")
        # Die Bildsuche: Bildfeld statt Pflichtadresse.
        pg.select_option("#job-kind", "image")
        pg.wait_for_timeout(200)
        log.pruefe(pg.is_visible("#job-bild-feld"), "die Bildsuche fragt nach einem Bild")
        log.pruefe(
            "Leer = überall suchen" in pg.inner_text("#job-source-warum"),
            "und die Adresse ist dort freiwillig",
        )
        pg.select_option("#job-kind", "research")
        pg.select_option("#job-rhythm", "hourly")
        pg.wait_for_timeout(200)
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
        # Erst durch die Tuer, dann den Verlauf anlegen: er gehoert in den
        # Ordner des Kontos, und den gibt es erst nach der Anmeldung.
        anmelden(seite, port)
        lege_chats_an()
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
                seite2 = zweite_seite(
                    browser, seite,
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
            klein = zweite_seite(
                browser, seite,
                viewport={"width": 390, "height": 780}, is_mobile=True, has_touch=True
            )
            klein.on("pageerror", lambda e: fehler.append(f"Skriptfehler (Handy): {e}"))
            klein.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            handy(klein, log, bilder)
            klein.close()

        if not nur or "ruhig" in nur:
            ruhig = zweite_seite(
                browser, seite,
                viewport={"width": 1200, "height": 800}, reduced_motion="reduce"
            )
            ruhig.on("pageerror", lambda e: fehler.append(f"Skriptfehler (ruhig): {e}"))
            ruhig.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            ohne_bewegung(ruhig, log)
            ruhig.close()

        if not nur or "normal" in nur:
            normales_konto(browser, port, log, fehler)

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
