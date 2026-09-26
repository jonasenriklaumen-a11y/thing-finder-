"""Jede Funktion einmal wirklich -- fuer ein Normal-, ein Pro- und ein Ultra-Konto.

Wie tests/test_end_to_end.py: echter Webserver, echte Konten, echtes litellm,
nur das Modell ist gestellt (tests/fake_llm.py). Hier aber in der Breite:
jede Seite, jeder Endpunkt, jeder Slash-Befehl, Chats, Auftraege, Speicher,
Add-ons, Einstellungen und jedes Werkzeug, das ohne Internet auskommt.

Geprueft wird dreierlei: nichts antwortet mit 5xx, die Antworten ergeben
Sinn, und die Pro-Sperren halten. Werkzeuge, die ins Netz gehen (Suche,
Wetter, Orte), bleiben draussen -- die Tests fassen kein echtes Netz an.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from aquaticy import web
from tests import fake_llm
from tests.test_end_to_end import _konto, _req, server  # noqa: F401 - Fixture

PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
).decode()


class Tour:
    """Ruft auf und merkt sich jeden Status -- ein 5xx faellt am Ende auf."""

    def __init__(self, port: int, cookie: str) -> None:
        self.port = port
        self.cookie = cookie
        self.gesehen: list[tuple[str, str, int]] = []

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        status, _, daten = _req(self.port, method, path, body, self.cookie)
        self.gesehen.append((method, path, status))
        try:
            return status, json.loads(daten)
        except ValueError:
            return status, daten

    def chat(self, text: str, **mehr: Any) -> list[dict[str, Any]]:
        status, _, daten = _req(self.port, "POST", "/api/chat", {"message": text, **mehr},
                                self.cookie)
        self.gesehen.append(("POST", "/api/chat", status))
        assert status == 200, daten
        return [json.loads(z[6:]) for z in daten.decode().splitlines() if z.startswith("data: ")]

    def kein_serverfehler(self) -> None:
        kaputt = [eintrag for eintrag in self.gesehen if eintrag[2] >= 500]
        assert not kaputt, kaputt


@pytest.fixture
def tour(server: tuple[int, Path], request: pytest.FixtureRequest,  # noqa: F811
         monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Tour:
    port, _ = server
    monkeypatch.setattr(web, "REQUEST_LIMIT", web.RateLimiter(attempts=10_000, window_seconds=60))
    # Nur hier: das Kontingent ist in test_end_to_end eigens geprueft, und
    # der grosse Systemtext kostet je Frage einige tausend Token.
    monkeypatch.setattr("aquaticy.quota.SESSION_TOKENS", 10**9)
    monkeypatch.setattr("aquaticy.quota.WEEK_TOKENS", 10**10)
    monkeypatch.chdir(tmp_path)
    plan = request.param
    code = {"pro": "PROE2E234", "ultra": "Abcdef1234567!"}.get(plan, "")
    return Tour(port, _konto(port, plan, code))


@pytest.mark.parametrize("tour", ["normal", "pro", "ultra"], indirect=True)
def test_every_page_and_endpoint(tour: Tour, tmp_path: Path) -> None:
    status, konto = tour("GET", "/api/account")
    # pro = Pro oder Ultra (Auslastung); ultra = alles, ohne Limit (seit 9.5.17).
    pro, ultra = konto["pro"], konto["ultra"]
    assert konto["usage"]["limited"] is not ultra
    assert status == 200 and konto["plan"] == ("ultra" if ultra else "pro" if pro else "normal")

    # -- Seiten und Lesewege ------------------------------------------------
    assert tour("GET", "/")[0] == 200
    for route in sorted(web.LEGAL_ROUTES):
        assert tour("GET", route)[0] == 200, route
    for route in ("/api/auth/status", "/api/config", "/api/runstate", "/api/chats",
                  "/api/chats?q=x", "/api/jobs", "/api/prefs", "/api/models",
                  "/api/models?purpose=code", "/api/memory", "/api/usage", "/api/notes",
                  "/api/history", "/api/addons"):
        status, daten = tour("GET", route)
        assert status == 200 and isinstance(daten, dict) and daten, route
    status, config = tour("GET", "/api/config")
    assert config["account"]["pro"] is pro and "sk-fake" not in json.dumps(config)
    assert tour("GET", "/api/system")[0] == (200 if pro else 403)
    assert tour("GET", "/api/run")[0] == 404, "noch kein Lauf"
    assert tour("GET", "/api/werkstatt/bildschirm?leise=1")[0] == 204
    assert tour("GET", "/api/media?id=../../etc/passwd")[0] == 404
    assert tour("GET", "/api/media?url=http://127.0.0.1:1/x.png")[0] == 404
    assert tour("GET", "/api/chatexport?session_id=gibtsnicht")[0] == 404
    assert tour("GET", "/api/gibtsnicht")[0] == 404
    status, seite = tour("GET", "/google?error=access_denied")
    assert status == 200 and b"abgelehnt" in seite

    # -- Einstellungen: Pro-Sperren und Unsinn --------------------------------
    status, stand = tour("POST", "/api/prefs", {"theme": "dark", "agents": 6, "quatsch": 1})
    assert status == 200 and stand["theme"] == "dark" and "quatsch" not in stand
    assert tour("POST", "/api/config", {"AQUATICY_LOCATION": "Bremen"})[0] == 200
    assert tour("POST", "/api/config", {"AQUATICY_SEARCH_VARIANTS": "999"})[0] == 400
    for key, wert in (("AQUATICY_VM_SIZE", "plus"), ("AQUATICY_VM_USER_MODE", "true"),
                      ("AQUATICY_LEGAL_GUARD", "false")):
        assert (tour("POST", "/api/config", {key: wert})[0] == 200) is ultra, key
    if ultra:
        tour("POST", "/api/config", {"AQUATICY_VM_USER_MODE": "false",
                                     "AQUATICY_LEGAL_GUARD": "true"})
    status, probe = tour("POST", "/api/probe", {"AQUATICY_MODEL": "openai/fake-modell"})
    assert status == 200 and probe["llm"]["ok"], probe

    # -- Chat mit Anhaengen ---------------------------------------------------
    ereignisse = tour.chat("Rechne mir 6 mal 7 aus", mode="normal")
    assert "42" in "".join(e.get("text", "") for e in ereignisse if e["type"] == "chunk")
    ereignisse = tour.chat("", attachments=[
        {"name": "notiz.txt", "data": base64.b64encode(b"Milch").decode()},
        {"name": "bild.png", "data": PNG},
        {"name": "../../boese.txt", "data": "!!!"},
    ])
    assert ereignisse[-1]["type"] == "done"
    assert not (tmp_path / "boese.txt").exists()
    ereignisse = tour.chat("/image /etc/passwd")
    assert ereignisse[0]["type"] == "error", "keine Serverdateien ans Bildmodell"
    assert tour("POST", "/api/chat", {"message": ""})[0] == 400
    assert tour("GET", "/api/runstate")[1]["running"] is False
    assert tour("POST", "/api/stop", {})[0] == 200
    assert tour("POST", "/api/answer", {"text": "ja"})[1]["ok"] is False

    # -- Chats ----------------------------------------------------------------
    chats = tour("GET", "/api/chats")[1]["chats"]
    kennung = chats[0]["session_id"]
    assert tour("GET", f"/api/chatexport?session_id={kennung}")[1]["markdown"].startswith("#")
    assert tour("POST", "/api/chat-edit", {"session_id": kennung, "action": "rename",
                                           "title": "Neu"})[1]["ok"]
    assert tour("POST", "/api/open", {"session_id": kennung})[1]["ok"]
    assert tour("POST", "/api/clear", {})[1]["ok"]
    assert tour("POST", "/api/chat-edit", {"session_id": kennung, "action": "x"})[1]["ok"] is False

    # -- Slash-Befehle --------------------------------------------------------
    for zeile, erwartet in (("/help", "/export"), ("/max", "Pro-Modus"),
                            ("/location Bremen", "Bremen"), ("/location", "aufgehoben"),
                            ("/model", "fake-modell"), ("/memory", "Speicher"),
                            ("/notes", "Merkzettel"), ("/history", "Rechne"),
                            ("/uploads", "bild.png"), ("/export pdf", "Unbekanntes Format"),
                            ("/quit", "Fenster"), ("/gibtsnicht", "/help"),
                            ("/uploads clear", "geloescht"), ("/clear", "verworfen")):
        status, antwort = tour("POST", "/api/command", {"line": zeile})
        assert status == 200 and erwartet in antwort["text"], (zeile, antwort)
    status, antwort = tour("POST", "/api/command", {"line": "/export csv"})
    assert antwort["download"]["name"].endswith(".csv") and antwort["download"]["content"]
    assert not list(tmp_path.glob("aquaticy-*")), "kein Export im Ordner des Servers"

    # -- Speicher und Auftraege -----------------------------------------------
    assert tour("DELETE", "/api/memory?id=abc")[0] == 400
    assert tour("DELETE", "/api/memory")[0] == 200
    status, job = tour("POST", "/api/jobs", {"action": "add", "question": "Neues in Bremen",
                                             "rhythm": "weekly", "hour": 9})
    nummer = job["job"]["id"]
    for aktion in ("pause", "resume", "run"):
        assert tour("POST", "/api/jobs", {"action": aktion, "id": nummer})[1]["ok"], aktion
    for _ in range(80):
        stand = next(j for j in tour("GET", "/api/jobs")[1]["jobs"] if j["id"] == nummer)
        if stand.get("last_state"):
            break
        time.sleep(0.25)
    assert stand["last_state"] and "Fehler" not in stand["last_state"], stand
    assert tour("POST", "/api/jobs", {"action": "add", "question": "x", "hour": "abc"}
                )[1]["ok"] is False
    assert tour("POST", "/api/jobs", {"action": "add", "kind": "image", "question": "x",
                                      "image_data": PNG})[1]["ok"] is False
    assert tour("DELETE", f"/api/jobs?id={nummer}")[1]["ok"] is True
    assert tour("DELETE", "/api/jobs?id=x")[0] == 400

    # -- Add-ons ohne Werkstatt -----------------------------------------------
    katalog = {a["id"] for a in tour("GET", "/api/addons")[1]["addons"]}
    assert {"github", "signal", "whatsapp", "telegram", "wetter", "feeds", "blender"} <= katalog
    for kennung in ("wetter", "feeds", "github"):
        status, stand = tour("POST", "/api/addons", {"action": "install", "id": kennung})
        assert status == 200 and kennung in stand["active"], kennung
    assert tour("POST", "/api/addons", {"action": "feeds", "id": "feeds",
                                        "feeds": ["javascript:alert(1)"]})[0] == 400
    assert tour("POST", "/api/addons", {"action": "rights", "id": "github",
                                        "rechte": {"inhalte": "nein"}})[0] == 200
    assert tour("POST", "/api/addons", {"action": "token", "id": "wetter", "token": "x"}
                )[0] == 400
    for aktion in ("disable", "enable"):
        assert tour("POST", "/api/addons", {"action": aktion, "id": "wetter"})[0] == 200
    assert tour("POST", "/api/addons", {"action": "login_done", "id": "signal"})[0] == 400
    if not ultra:
        status, antwort = tour("POST", "/api/addons", {"action": "install", "id": "signal"})
        assert status == 400 and "Ultra" in antwort["error"]
    assert tour("POST", "/api/addons", {"action": "quatsch", "id": "wetter"})[0] == 400
    assert tour("POST", "/api/addons", {"action": "install", "id": "gibtsnicht"})[0] == 400
    for kennung in ("wetter", "feeds", "github"):
        status, stand = tour("POST", "/api/addons", {"action": "uninstall", "id": kennung})
        assert status == 200 and kennung not in stand["active"], kennung

    # -- Werkstatt-Eingabe, Home Assistant, Lager, Google ---------------------
    status, _ = tour("POST", "/api/werkstatt/eingabe", {"art": "type", "text": "x"})
    assert status == (400 if ultra else 403)
    status, antwort = tour("POST", "/api/ha", {"url": "http://127.0.0.1:1", "token": "t"})
    assert (status == 200 and antwort["ok"] is False) if ultra else status == 403
    status, antwort = tour("POST", "/api/storage", {"url": "http://127.0.0.1:1"})
    assert (status == 200 and antwort["ok"] is False) if ultra else status == 403
    assert tour("POST", "/api/google", {"action": "state"})[1]["ok"]
    assert tour("POST", "/api/google", {"action": "start", "client_id": ""})[1]["ok"] is False
    assert tour("POST", "/api/google", {"action": "finish", "code": "x"})[1]["ok"] is False
    assert tour("POST", "/api/google", {"action": "disconnect"})[1]["ok"]

    # -- Abmelden -------------------------------------------------------------
    assert tour("POST", "/api/auth/logout", {})[0] == 200
    assert tour("GET", "/api/config")[0] == 401
    tour.kein_serverfehler()


#: Werkzeuge, die ohne Internet auskommen, mit Argumenten -- und was dabei
#: herauskommen muss.
WERKZEUGE: list[tuple[str, dict[str, Any], str]] = [
    ("calculate", {"expression": "(3+4)*6"}, '"result": "42"'),
    ("remember", {"text": "Ich mag Tee"}, '"saved": true'),
    ("save_memory", {"text": "Wohnt in Bremen", "topic": "Ort"}, '"saved": true'),
    ("recall_memory", {"query": "Bremen"}, "Wohnt in Bremen"),
    ("change_setting", {"setting": "formulierungen", "value": "3"}, '"changed"'),
    ("change_setting", {"setting": "AQUATICY_HA_CONTROL", "value": "true"}, "nicht aus dem"),
    ("change_setting", {"setting": "legal_guard", "value": "aus"}, "nicht aus dem"),
    ("change_setting", {"setting": "AQUATICY_VM_USER_MODE", "value": "an"}, "nicht aus dem"),
    ("fetch_page", {"url": "file:///etc/passwd"}, "invalid_url"),
    ("fetch_page", {"url": "http://127.0.0.1:1/"}, '"ok": false'),
    # Die Rueckfrage beantwortet der Test ueber /api/answer, wie der Browser.
    ("ask_user", {"question": "Welche Farbe?", "options": ["rot", "blau"]}, "blau"),
]


def _antworte(tour: Tour, text: str) -> None:
    """Beantwortet die Rueckfrage, sobald sie da ist -- wie der Browser."""
    for _ in range(100):
        time.sleep(0.1)
        status, _, daten = _req(tour.port, "POST", "/api/answer", {"text": text}, tour.cookie)
        if status == 200 and json.loads(daten).get("ok"):
            return


@pytest.mark.parametrize("tour", ["normal", "pro", "ultra"], indirect=True)
def test_every_offline_tool_runs_through(tour: Tour) -> None:
    for name, argumente, erwartet in WERKZEUGE:
        fake_llm.ANFRAGEN.clear()
        if name == "ask_user":
            threading.Thread(target=_antworte, args=(tour, "blau"), daemon=True).start()
        ereignisse = tour.chat(f"WERKZEUG:{name} {json.dumps(argumente)}")
        angeboten = {n for zeile in fake_llm.ANFRAGEN for n in json.loads(zeile)["tool_names"]}
        assert name in angeboten, (name, sorted(angeboten))
        ergebnis = " ".join(e.get("result", "") for e in ereignisse
                            if e.get("type") == "action_done")
        abstuerze = [e for e in ereignisse if e.get("type") == "error"]
        assert not abstuerze, (name, abstuerze)
        assert erwartet in ergebnis, (name, ergebnis)
        assert ereignisse[-1]["type"] == "done"
    tour.kein_serverfehler()


@pytest.mark.parametrize("tour", ["normal", "pro", "ultra"], indirect=True)
def test_what_each_account_is_offered(tour: Tour) -> None:
    """Was angeboten wird, folgt aus Konto und Einrichtung -- nicht aus dem Zufall."""
    fake_llm.ANFRAGEN.clear()
    tour.chat("WERKZEUG:calculate {}")
    angeboten = {n for zeile in fake_llm.ANFRAGEN for n in json.loads(zeile)["tool_names"]}
    ultra = tour("GET", "/api/account")[1]["ultra"]
    assert ("lan_check" in angeboten) is ultra, "das Heimnetz gehoert zu Ultra"
    # Nicht eingerichtet -> nicht angeboten: kein Werkzeug, das nur scheitern kann.
    for name in ("github", "read_feeds", "create_image", "ha_states", "ha_call",
                 "storage_find", "mail_search", "calendar_events", "desktop_open"):
        assert name not in angeboten, name
