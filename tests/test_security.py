"""Sicherheitspruefung 9.5.11: was ein kundiger Nutzer versuchen koennte -- und dass es nicht geht.

Jeder Test hier steht fuer eine Stelle, die bei der Pruefung aufgefallen ist
oder die gezielt nachgeprueft wurde:

* Das Kontingent normaler Konten galt nur vor einer Anfrage und nur fuer das
  Hauptmodell. Agenten, Master, Planer, Bildbeschreibung und Bildmodell
  liefen am Zaehler vorbei.
* Eine Einstellung aus dem Chat landete in der `.env` des SERVERS -- ein
  Konto aenderte damit die Grundeinstellungen aller.
* Normale Konten konnten eine eigene Modell- oder SearXNG-Adresse setzen: der
  Server schickte Anfragen dann an beliebige Rechner (auch ins Heimnetz), und
  der Test-Knopf haengte den Schluessel des Betreibers an.
* Eine negative Laenge im Anfragekopf liess den Server lesen, bis die
  Verbindung zu ist.
* Die Pro-Sperren selbst (Pro-Code, User mode, Plus-Werkstatt, LAN, Home
  Assistant, Lager, Leitplanken) -- noch einmal ueber echtes HTTP.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aquaticy import metering, web
from aquaticy.config import Settings
from aquaticy.quota import SESSION_TOKENS, Quota
from aquaticy.usage import UsageLog


def _antwort(text: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=text, tool_calls=None))])


# -- Kontingent: jeder Aufruf zaehlt, und es gilt mitten im Lauf ------------------------
@pytest.fixture
def knapp(tmp_path: Path) -> Settings:
    settings = Settings(model="mistral/mistral-large-latest", data_dir=tmp_path / "d",
                        subagents_auto=False, request_delay_seconds=0.0)
    settings.data_dir.mkdir(parents=True)
    # Ein normales Konto (seit 9.5.14: 5-Stunden-Sitzung und Woche).
    settings.quota = Quota(tmp_path / "konten.sqlite3", "k", time.time() - 3600)
    return settings


def _voll(settings: Settings, bis_auf: int = 0) -> None:
    """Braucht die Sitzung auf -- bis auf *bis_auf* Token."""
    settings.quota.record(SESSION_TOKENS - bis_auf, "m")


def test_every_metered_call_is_counted(knapp: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("litellm.completion", lambda **kw: _antwort("x" * 300))
    metering.completion(knapp, model="m", messages=[{"role": "user", "content": "y" * 300}])
    assert UsageLog(knapp.db_path).total_tokens() >= 150


def test_provider_numbers_win_over_the_estimate(knapp: Settings,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    antwort = _antwort("x")
    antwort.usage = SimpleNamespace(prompt_tokens=400, completion_tokens=50)
    monkeypatch.setattr("litellm.completion", lambda **kw: antwort)
    metering.completion(knapp, model="m", messages=[])
    assert UsageLog(knapp.db_path).total_tokens() == 450


def test_an_exhausted_quota_stops_the_call_before_it_costs(
    knapp: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _voll(knapp)
    gerufen: list[bool] = []
    monkeypatch.setattr("litellm.completion", lambda **kw: gerufen.append(True) or _antwort())
    with pytest.raises(metering.QuotaExceeded, match="Kontingent"):
        metering.completion(knapp, model="m", messages=[])
    assert gerufen == []
    # Der Rechtspruefer zaehlt mit, prueft aber nicht vorher -- er soll im
    # Zweifel ablehnen, nicht ausfallen.
    metering.completion(knapp, enforce=False, model="m", messages=[])
    assert gerufen == [True]


def test_pro_and_local_have_no_quota(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(data_dir=tmp_path)
    UsageLog(settings.db_path).record("m", 10_000_000, 0)
    monkeypatch.setattr("litellm.completion", lambda **kw: _antwort())
    metering.completion(settings, model="m", messages=[])
    assert metering.remaining(settings) is None


def test_all_model_calls_go_through_the_meter() -> None:
    """Kein Aufruf am Zaehler vorbei: ausser im Hauptagenten (der zaehlt selbst)
    ruft niemand litellm.completion direkt."""
    import re

    aufruf = re.compile(r"(=|return)\s*litellm\.completion\(")
    wurzel = Path(__file__).resolve().parent.parent / "aquaticy"
    for datei in wurzel.glob("*.py"):
        text = datei.read_text(encoding="utf-8")
        # probe.py und local_model.py pruefen nur Erreichbarkeit (Einrichtung, lokal).
        if datei.name in ("metering.py", "agent.py", "probe.py", "local_model.py"):
            continue
        assert not aufruf.search(text), f"{datei.name} ruft am Zaehler vorbei"
    agent = (wurzel / "agent.py").read_text(encoding="utf-8")
    # Im Agenten nur die zwei Wege, die selbst zaehlen (_completion und _final_answer).
    assert len(aufruf.findall(agent)) == 2


def test_the_main_agent_stops_mid_run(knapp: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy.agent import Agent

    aufrufe: list[str] = []

    def llm(**kw: Any) -> Any:
        aufrufe.append(kw["model"])
        # Jede Runde verbraucht mehr, als uebrig ist.
        _voll(knapp)
        tool = SimpleNamespace(id=f"c{len(aufrufe)}", type="function", function=SimpleNamespace(
            name="calculate", arguments=json.dumps({"expression": "1+1"})))
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[tool]))])

    monkeypatch.setattr("litellm.completion", llm)
    ergebnis = Agent(knapp, cache=None).ask("Rechne ganz viel", stream=False)
    assert len(aufrufe) == 1, "nach der ersten Runde ist Schluss"
    assert "Kontingent" in ergebnis.answer


def test_subagents_stop_when_the_quota_is_gone(knapp: Settings,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy.subagents import run_subagents

    _voll(knapp)
    gerufen: list[bool] = []
    monkeypatch.setattr("litellm.completion", lambda **kw: gerufen.append(True) or _antwort())
    ergebnisse = run_subagents(["Teil eins", "Teil zwei"], knapp, parallel=2)
    assert gerufen == [], "kein einziger Agent lief auf Kosten des Betreibers"
    assert all(e.error for e in ergebnisse)


def test_an_image_counts_and_needs_room(knapp: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy import images
    from aquaticy.tools import Toolbox

    _voll(knapp, bis_auf=metering.IMAGE_TOKENS + 100)
    monkeypatch.setattr(images, "generate", lambda *a, **k: {
        "bytes": b"\x89PNG\r\n\x1a\n" + b"0" * 32, "mime": "image/png", "modell": "T"})
    box = Toolbox(knapp)
    assert box.create_image("ein Hund")["erstellt"] is True
    assert UsageLog(knapp.db_path).total_tokens() >= metering.IMAGE_TOKENS
    fehler = box.create_image("noch ein Hund")["error"]
    assert "Kontingent" in fehler and "2,5 %" in fehler, "in Prozent, nicht in Token"


# -- Konto-Einstellungen: normale Konten -----------------------------------------------
def _profil(tmp_path: Path, env: str = "") -> Path:
    profil = tmp_path / "konto"
    profil.mkdir(exist_ok=True)
    (profil / ".env").write_text(env, encoding="utf-8")
    return profil


def test_normal_accounts_get_the_quota_and_the_operators_addresses(tmp_path: Path) -> None:
    profil = _profil(tmp_path, "AQUATICY_API_BASE=http://192.168.1.10:8080\n"
                               "AQUATICY_SEARXNG_URL=http://10.0.0.5/\n")
    normal = web._profile_settings(profil, "normal")
    assert normal.quota is not None, "ein normales Konto hat immer ein Kontingent"
    assert normal.api_base == web.get_settings().api_base
    assert normal.searxng_url == web.get_settings().searxng_url
    # Pro ist beim Netz wie Normal (seit 9.5.17): Kontingent (doppelt), keine
    # eigene Adresse. Nur Ultra bekommt die eigene Adresse und kein Limit.
    pro = web._profile_settings(profil, "pro")
    assert pro.quota is not None and pro.api_base == web.get_settings().api_base
    ultra = web._profile_settings(profil, "ultra")
    assert ultra.quota is None and ultra.api_base == "http://192.168.1.10:8080"


def _sitzung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan: str) -> web.ChatSession:
    sitzung = web.ChatSession(account=SimpleNamespace(plan=plan, username=plan),
                              profile=_profil(tmp_path))
    monkeypatch.setattr(web, "SESSION", sitzung)
    return sitzung


@pytest.mark.parametrize(
    "feld", [{"AQUATICY_API_BASE": "http://192.168.1.1:11434"},
             {"AQUATICY_SEARXNG_URL": "http://10.0.0.1:8888"},
             {"AQUATICY_API_BASE": "https://example.org/v1"}],
)
def test_normal_accounts_cannot_redirect_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, feld: dict[str, str]
) -> None:
    _sitzung(tmp_path, monkeypatch, "pro")
    with pytest.raises(ValueError, match="Ultra"):
        web.save_values(feld)


def test_the_full_form_still_saves_for_normal_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sitzung(tmp_path, monkeypatch, "normal")
    werte = web.current_values()
    # Die Oberflaeche schickt bei normalen Konten die Adressen des Betreibers
    # unveraendert mit -- das muss durchgehen.
    web.save_values({key: werte[key] for key in (
        "AQUATICY_MODEL", "AQUATICY_API_BASE", "AQUATICY_SEARXNG_URL", "AQUATICY_LOCATION",
        "AQUATICY_SEARCH_BACKEND", "AQUATICY_AUTO_MODEL")})


def test_the_probe_never_hands_the_servers_key_to_a_typed_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import probe

    monkeypatch.setenv("MISTRAL_API_KEY", "server-geheim")
    gesehen: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        probe, "check_llm",
        lambda model, key, base, **_: gesehen.append((model, key, base)) or (True, ""))
    monkeypatch.setattr(probe, "check_search", lambda *a: (True, ""))
    handler = web.Handler.__new__(web.Handler)
    for plan in ("normal", "pro"):
        _sitzung(tmp_path, monkeypatch, plan)
        handler._probe({"AQUATICY_MODEL": "mistral/mistral-large-latest",
                        "AQUATICY_API_BASE": "https://fremd.example/v1"})
    assert all(key != "server-geheim" or base in ("", web.get_settings().api_base)
               for _, key, base in gesehen), gesehen
    assert gesehen[0][2] != "https://fremd.example/v1", (
        "normal: die eingetippte Adresse zaehlt nicht")
    # Eigene Adressen gibt es seit 9.5.17 nur mit Ultra -- auch Pro testet
    # gegen die des Betreibers.
    assert gesehen[1][2] != "https://fremd.example/v1", "pro: die eingetippte Adresse zaehlt nicht"
    # Wer (mit Ultra) eine fremde Adresse testen will, tippt den Schluessel dafuer selbst.
    _sitzung(tmp_path, monkeypatch, "ultra")
    handler._probe({"AQUATICY_MODEL": "mistral/mistral-large-latest",
                    "AQUATICY_API_BASE": "https://fremd.example/v1", "__API_KEY__": "eigener"})
    assert gesehen[-1][1:] == ("eigener", "https://fremd.example/v1")


def test_a_chat_setting_stays_in_the_account(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy import config
    from aquaticy.tools import Toolbox

    server_env = tmp_path / "server.env"
    server_env.write_text("AQUATICY_SEARCH_VARIANTS=3\n", encoding="utf-8")
    monkeypatch.setattr(config, "find_env_file", lambda: server_env)
    monkeypatch.delenv("AQUATICY_SEARCH_VARIANTS", raising=False)
    profil = _profil(tmp_path)
    settings = web._profile_settings(profil, "normal")
    antwort = Toolbox(settings).change_setting("formulierungen", "2")
    assert antwort.get("changed"), antwort
    assert "AQUATICY_SEARCH_VARIANTS=2" in (profil / ".env").read_text()
    assert server_env.read_text() == "AQUATICY_SEARCH_VARIANTS=3\n", "der Server bleibt unberuehrt"
    assert os.environ.get("AQUATICY_SEARCH_VARIANTS") != "2", "und die Umgebung des Servers auch"


# -- Ueber echtes HTTP -----------------------------------------------------------------
@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from aquaticy.auth import AuthStore

    monkeypatch.setattr(web, "AUTH", AuthStore(tmp_path / "accounts", "PRO234567"))
    monkeypatch.setattr(web, "SESSIONS", web.SessionRegistry())
    monkeypatch.setattr(web, "SESSION", web.SessionProxy())
    monkeypatch.setattr(web, "strong_models", lambda *args, **kwargs: [])
    monkeypatch.setattr(web, "AUTH_LIMIT", web.RateLimiter(attempts=100, window_seconds=60))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    faden = threading.Thread(target=httpd.serve_forever, daemon=True)
    faden.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _anfrage(port: int, method: str, path: str, body: Any = None,
             cookie: str = "") -> tuple[int, dict[str, str], dict[str, Any]]:
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json", "User-Agent": "Sicherheitstest"}
    if cookie:
        headers["Cookie"] = cookie
    conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    antwort = conn.getresponse()
    roh = antwort.read()
    ergebnis = antwort.status, dict(antwort.getheaders()), (json.loads(roh) if roh else {})
    conn.close()
    return ergebnis


def _konto(port: int, plan: str = "normal", code: str = "") -> str:
    _, kopf, _ = _anfrage(port, "POST", "/api/consent", {"accepted": True})
    zustimmung = kopf["Set-Cookie"].split(";", 1)[0]
    status, kopf, daten = _anfrage(port, "POST", "/api/auth/register", {
        "email": f"{plan}{os.urandom(3).hex()}@example.org",
        "username": f"Tester{os.urandom(3).hex()}",
        "password": "ein langes Passwort", "plan": plan, "pro_code": code,
        "terms_accepted": True}, zustimmung)
    assert status == 200, daten
    return zustimmung + "; " + kopf["Set-Cookie"].split(";", 1)[0]


@pytest.mark.parametrize("code", ["", "PRO234568", "pro23456", "PRO234567PRO234567",
                                  "PRO 234567", "' OR 1=1 --"])
def test_pro_needs_the_exact_code(server: int, code: str) -> None:
    _, kopf, _ = _anfrage(server, "POST", "/api/consent", {"accepted": True})
    zustimmung = kopf["Set-Cookie"].split(";", 1)[0]
    status, _, daten = _anfrage(server, "POST", "/api/auth/register", {
        "email": "x@example.org", "username": "Xaver", "password": "ein langes Passwort",
        "plan": "pro", "pro_code": code, "terms_accepted": True}, zustimmung)
    assert status == 400 and "Pro-Code" in daten["error"]


@pytest.mark.parametrize("plan", ["PRO", "admin", "pro ", ["pro"], {"plan": "pro"}])
def test_odd_plans_never_become_pro(server: int, plan: Any) -> None:
    _, kopf, _ = _anfrage(server, "POST", "/api/consent", {"accepted": True})
    zustimmung = kopf["Set-Cookie"].split(";", 1)[0]
    status, _, daten = _anfrage(server, "POST", "/api/auth/register", {
        "email": f"p{os.urandom(3).hex()}@example.org", "username": f"P{os.urandom(3).hex()}",
        "password": "ein langes Passwort", "plan": plan, "terms_accepted": True}, zustimmung)
    assert status == 400 or daten["account"]["plan"] == "normal"


@pytest.mark.parametrize(
    ("pfad", "body"),
    [
        ("/api/config", {"AQUATICY_VM_USER_MODE": "true"}),
        ("/api/config", {"AQUATICY_LEGAL_GUARD": "false"}),
        ("/api/config", {"AQUATICY_VM_SIZE": "plus"}),
        ("/api/config", {"AQUATICY_LAN_ENABLED": "true"}),
        ("/api/config", {"AQUATICY_HA_URL": "http://homeassistant.local:8123"}),
        ("/api/config", {"AQUATICY_STORAGE_ACCESS": "write"}),
        ("/api/config", {"AQUATICY_API_BASE": "http://192.168.1.1"}),
        ("/api/addons", {"action": "install", "id": "whatsapp"}),
        ("/api/addons", {"action": "install", "id": "blender"}),
        ("/api/ha", {"url": "http://192.168.1.2:8123"}),
        ("/api/storage", {"url": "http://192.168.1.3"}),
        ("/api/werkstatt/eingabe", {"art": "key", "key": "Return"}),
    ],
)
def test_pro_locks_hold_over_http(server: int, pfad: str, body: dict[str, Any]) -> None:
    cookie = _konto(server)
    status, _, daten = _anfrage(server, "POST", pfad, body, cookie)
    assert status in (400, 403), (pfad, status, daten)
    status, _, werte = _anfrage(server, "GET", "/api/config", None, cookie)
    werte = werte["values"]
    assert werte["AQUATICY_VM_USER_MODE"] == "false" and werte["AQUATICY_LEGAL_GUARD"] == "true"
    assert werte["AQUATICY_VM_SIZE"] == "normal" and werte["AQUATICY_LAN_ENABLED"] == "false"


def test_the_pro_system_view_stays_closed(server: int) -> None:
    cookie = _konto(server)
    assert _anfrage(server, "GET", "/api/system", None, cookie)[0] == 403


def test_accounts_cannot_see_each_other(server: int) -> None:
    eins, zwei = _konto(server), _konto(server)
    _anfrage(server, "POST", "/api/jobs", {"action": "add", "question": "Wetter Bremen",
                                          "rhythm": "daily"}, eins)
    assert _anfrage(server, "GET", "/api/jobs", None, zwei)[2]["jobs"] == []
    assert _anfrage(server, "GET", "/api/chatexport?session_id=abc", None, zwei)[0] == 404


def _roh(port: int, anfrage: bytes) -> str:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(anfrage)
        sock.shutdown(socket.SHUT_WR)
        daten = b""
        while True:
            stueck = sock.recv(65536)
            if not stueck:
                break
            daten += stueck
    return daten.decode("latin-1")


@pytest.mark.parametrize("laenge", ["-1", "abc", "1e9", " 12 34", "99999999999999999"])
def test_odd_lengths_are_refused_before_reading(server: int, laenge: str) -> None:
    kopf = _roh(server, (
        "POST /api/consent HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
        f"Content-Length: {laenge}\r\n\r\n"
    ).encode())
    status = int(kopf.split(" ", 2)[1])
    assert status in (400, 413), kopf[:200]


def test_the_ui_escapes_server_values() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    start = html.index("function gauge(")
    teil = html[start:html.index("}", html.index("return", start))]
    assert "${name}" not in teil and "esc(String(name))" in teil


def test_normal_accounts_cannot_blow_up_the_local_model(tmp_path: Path) -> None:
    profil = _profil(tmp_path, "AQUATICY_CONTEXT_TOKENS=2000000\n")
    assert web._profile_settings(profil, "normal").context_tokens <= max(
        web.get_settings().context_tokens, web.NORMAL_CONTEXT_CAP)
    # Pro ist jetzt gedeckelt wie Normal (seit 9.5.17); nur Ultra darf hoch.
    assert web._profile_settings(profil, "pro").context_tokens <= max(
        web.get_settings().context_tokens, web.NORMAL_CONTEXT_CAP)
    assert web._profile_settings(profil, "ultra").context_tokens == 2_000_000
