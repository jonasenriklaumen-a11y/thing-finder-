"""Tests fuer die Weboberflaeche -- echter Server, gefaelschter Agent."""

from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cortex import web
from cortex.agent import AgentResult
from cortex.cache import Cache
from cortex.config import Settings


class FakeToolbox:
    def __init__(self) -> None:
        self.on_event: Any = None
        self.ask_handler: Any = None


class FakeAgent:
    """Spielt einen Turn nach: erst Zwischenschritte, dann eine Antwort."""

    def __init__(self, script: list[tuple[str, dict[str, Any]]] | None = None) -> None:
        self.script = script if script is not None else [("answer_chunk", {"text": "Hallo"})]
        self.toolbox = FakeToolbox()
        self.on_event: Any = None
        self.asked: list[str] = []
        self.cleared = 0
        self.closed = 0
        self.raise_error: Exception | None = None
        self.ask_handler: Any = None

    def set_ask_handler(self, handler: Any) -> None:
        self.ask_handler = handler
        self.toolbox.ask_handler = handler

    def ask(self, question: str, *, stream: bool = True) -> AgentResult:
        self.asked.append(question)
        if self.raise_error is not None:
            raise self.raise_error
        for name, payload in self.script:
            if self.on_event:
                self.on_event(name, payload)
        return AgentResult(answer="Hallo", tool_calls=2)

    def clear(self) -> None:
        self.cleared += 1

    def close(self) -> None:
        self.closed += 1


@pytest.fixture
def web_settings(tmp_path: Path) -> Settings:
    return Settings(
        model="openai/gpt-4o",
        data_dir=tmp_path / "data",
        env_path=tmp_path / ".env",
        location="Bremen",
        subagents_auto=False,
    )


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch, web_settings: Settings) -> web.ChatSession:
    """Eine frische Sitzung mit festen Einstellungen statt der echten .env."""
    fresh = web.ChatSession()
    fresh._settings = web_settings
    monkeypatch.setattr(web, "SESSION", fresh)
    return fresh


@pytest.fixture
def client(session: web.ChatSession):
    """Startet den echten Handler auf einem freien Port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method: str, path: str, body: dict[str, Any] | None = None):
        conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data

    # Manche Tests brauchen eine eigene Verbindung -- etwa um sie mitten im
    # Strom zu kappen. Der Port haengt deshalb am Helfer.
    request.port = server.server_address[1]

    try:
        yield request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def sse_events(raw: bytes) -> list[dict[str, Any]]:
    """Zerlegt einen SSE-Strom in die einzelnen Ereignisse."""
    events = []
    for block in raw.decode().split("\n\n"):
        line = block.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


# -- Einstellungen --------------------------------------------------------
def test_current_values_covers_every_setting_key(session: web.ChatSession) -> None:
    values = web.current_values()
    assert set(values) == set(web.SETTING_KEYS)
    assert values["CORTEX_MODEL"] == "openai/gpt-4o"
    assert values["CORTEX_LOCATION"] == "Bremen"
    assert values["CORTEX_SUBAGENTS_AUTO"] == "false"


def test_current_values_are_all_strings(session: web.ChatSession) -> None:
    # Das Formular fuellt nur Text -- Zahlen muessen konvertiert ankommen.
    assert all(isinstance(value, str) for value in web.current_values().values())


def test_save_values_writes_env_and_reloads(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    written = web.save_values(
        {"CORTEX_MODEL": "anthropic/claude-sonnet-4", "CORTEX_LOCATION": "Hamburg"}
    )
    assert written == target
    text = target.read_text()
    assert "CORTEX_MODEL=anthropic/claude-sonnet-4" in text
    assert "CORTEX_LOCATION=Hamburg" in text
    # reload() wirft Agent und Einstellungen weg, damit die neuen greifen.
    assert session._settings is None
    assert session._agent is None


def test_api_key_is_stored_under_the_provider_name(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    web.save_values(
        {"CORTEX_MODEL": "anthropic/claude-sonnet-4", web.API_KEY_FIELD: "sk-ant-neu"}
    )
    assert "ANTHROPIC_API_KEY=sk-ant-neu" in target.read_text()


def test_empty_api_key_never_deletes_the_stored_one(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    target.write_text("OPENAI_API_KEY=sk-alt\n")
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    web.save_values({"CORTEX_MODEL": "openai/gpt-4o", web.API_KEY_FIELD: "   "})
    assert "OPENAI_API_KEY=sk-alt" in target.read_text()


def test_unknown_keys_are_ignored(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    web.save_values({"CORTEX_MODEL": "openai/gpt-4o", "PATH": "/boese"})
    assert "PATH=/boese" not in target.read_text()


# -- Endpunkte ------------------------------------------------------------
def test_index_serves_the_ui(client) -> None:
    status, body = client("GET", "/")
    assert status == 200
    assert b"cortex" in body
    assert b"id=\"version\"" in body


def test_config_endpoint_reports_version_and_values(client) -> None:
    status, body = client("GET", "/api/config")
    assert status == 200
    payload = json.loads(body)
    assert payload["version"] == web.__version__
    assert set(payload["values"]) == set(web.SETTING_KEYS)
    assert payload["key_name"] == "OPENAI_API_KEY"


def test_notes_and_history_endpoints(client) -> None:
    for path, key in (("/api/notes", "notes"), ("/api/history", "history")):
        status, body = client("GET", path)
        assert status == 200
        assert json.loads(body)[key] == []


def test_unknown_path_is_404(client) -> None:
    assert client("GET", "/gibtsnicht")[0] == 404
    assert client("POST", "/api/gibtsnicht", {})[0] == 404


def test_empty_message_is_rejected(client) -> None:
    status, body = client("POST", "/api/chat", {"message": "   "})
    assert status == 400
    assert "error" in json.loads(body)


def test_clear_resets_the_agent(client, session: web.ChatSession) -> None:
    agent = FakeAgent()
    session._agent = agent
    assert client("POST", "/api/clear")[0] == 200
    assert agent.cleared == 1


# -- Streaming ------------------------------------------------------------
def test_chat_streams_steps_and_answer(client, session: web.ChatSession) -> None:
    session._agent = FakeAgent(
        [
            ("search", {"query": "Kaffee Bremen"}),
            ("answer_chunk", {"text": "Es "}),
            ("answer_chunk", {"text": "gibt "}),
            ("done", {"tool_calls": 1, "hit_limit": False}),
        ]
    )
    status, body = client("POST", "/api/chat", {"message": "Wo gibt es Kaffee?"})
    assert status == 200
    events = sse_events(body)
    kinds = [event["type"] for event in events]
    assert kinds == ["search", "chunk", "chunk", "done"]
    assert "".join(e["text"] for e in events if e["type"] == "chunk") == "Es gibt "
    assert events[0]["query"] == "Kaffee Bremen"


def test_done_arrives_exactly_once(client, session: web.ChatSession) -> None:
    # Der Agent sendet sein eigenes "done" -- der Server darf keines anhaengen.
    session._agent = FakeAgent([("done", {"tool_calls": 0, "hit_limit": False})])
    _, body = client("POST", "/api/chat", {"message": "Hallo"})
    assert [e["type"] for e in sse_events(body)].count("done") == 1


def test_done_is_added_when_the_agent_sends_none(client, session: web.ChatSession) -> None:
    # Sonst bliebe im Browser der blinkende Cursor stehen.
    session._agent = FakeAgent([("answer_chunk", {"text": "kurz"})])
    _, body = client("POST", "/api/chat", {"message": "Hallo"})
    assert [e["type"] for e in sse_events(body)][-1] == "done"


def test_agent_crash_becomes_an_error_event(client, session: web.ChatSession) -> None:
    agent = FakeAgent()
    agent.raise_error = RuntimeError("Modell weg")
    session._agent = agent
    status, body = client("POST", "/api/chat", {"message": "Hallo"})
    assert status == 200
    events = sse_events(body)
    assert events[0]["type"] == "error"
    assert "Modell weg" in events[0]["message"]
    assert events[-1]["type"] == "done"


def test_event_hooks_are_released_after_the_turn(client, session: web.ChatSession) -> None:
    agent = FakeAgent()
    session._agent = agent
    client("POST", "/api/chat", {"message": "Hallo"})
    assert agent.on_event is None
    assert agent.toolbox.on_event is None


def test_config_post_saves_and_answers(
    client, session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    status, body = client("POST", "/api/config", {"CORTEX_LOCATION": "Kiel"})
    assert status == 200
    assert json.loads(body)["ok"] is True
    assert "CORTEX_LOCATION=Kiel" in target.read_text()


def test_config_post_reports_failures(
    client, session: web.ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(values, path=None):
        raise OSError("Platte voll")

    monkeypatch.setattr(web, "write_env_file", boom)
    status, body = client("POST", "/api/config", {"CORTEX_LOCATION": "Kiel"})
    assert status == 500
    assert "Platte voll" in json.loads(body)["error"]


# -- Oberflaeche ----------------------------------------------------------
def test_ui_file_offers_every_setting() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    for key in web.SETTING_KEYS:
        assert f'name="{key}"' in html, f"{key} fehlt im Formular"
    assert f'name="{web.API_KEY_FIELD}"' in html


# -- Slash-Befehle --------------------------------------------------------
def test_help_lists_the_commands(client) -> None:
    status, body = client("POST", "/api/command", {"line": "/help"})
    assert status == 200
    text = json.loads(body)["text"]
    for command in ("/location", "/model", "/image", "/export", "/clear"):
        assert command in text


def test_location_command_changes_the_filter(client, session: web.ChatSession) -> None:
    session._agent = FakeAgent()
    session._agent.set_location = lambda value: setattr(  # type: ignore[attr-defined]
        session._settings, "location", value
    )
    status, body = client("POST", "/api/command", {"line": "/location Kiel"})
    assert status == 200
    assert json.loads(body)["reload"] is True
    assert web.current_values()["CORTEX_LOCATION"] == "Kiel"


def test_model_command_rejects_a_model_without_provider(client, session: web.ChatSession) -> None:
    _, body = client("POST", "/api/command", {"line": "/model nemotron-3"})
    text = json.loads(body)["text"]
    assert "openai/gpt-4o" in text  # das bisherige Modell bleibt aktiv


def test_model_command_without_argument_reports_the_current_one(client) -> None:
    _, body = client("POST", "/api/command", {"line": "/model"})
    assert "openai/gpt-4o" in json.loads(body)["text"]


def test_clear_command_empties_the_thread(client, session: web.ChatSession) -> None:
    agent = FakeAgent()
    session._agent = agent
    _, body = client("POST", "/api/command", {"line": "/clear"})
    assert json.loads(body)["clear"] is True
    assert agent.cleared == 1


def test_notes_and_history_commands_on_an_empty_database(client) -> None:
    assert "leer" in json.loads(client("POST", "/api/command", {"line": "/notes"})[1])["text"]
    assert "Verlauf" in json.loads(client("POST", "/api/command", {"line": "/history"})[1])["text"]


def test_export_without_history_says_so(client) -> None:
    _, body = client("POST", "/api/command", {"line": "/export md"})
    assert "exportieren" in json.loads(body)["text"]


def test_unknown_command_is_reported(client) -> None:
    _, body = client("POST", "/api/command", {"line": "/gibtsnicht"})
    assert "/help" in json.loads(body)["text"]


def test_command_endpoint_rejects_plain_text(client) -> None:
    assert client("POST", "/api/command", {"line": "hallo"})[0] == 400


def test_image_goes_through_the_chat_path(
    client, session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "foto.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    agent = FakeAgent([("done", {"tool_calls": 0})])
    agent.describe_image = lambda path: "ein gruener Stuhl"  # type: ignore[attr-defined]
    session._agent = agent
    status, body = client("POST", "/api/chat", {"message": f"/image {image}"})
    assert status == 200
    assert [e["type"] for e in sse_events(body)] == ["done"]
    # Der Agent bekommt die Beschreibung als Frage, nicht den Befehl.
    assert "ein gruener Stuhl" in agent.asked[0]
    assert "foto.png" in agent.asked[0]


def test_image_folder_takes_the_newest_picture(tmp_path: Path) -> None:
    import os
    import time

    old = tmp_path / "alt.png"
    new = tmp_path / "neu.jpg"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    os.utime(old, (time.time() - 500, time.time() - 500))
    assert web.resolve_image(tmp_path) == new


def test_image_folder_without_pictures_raises(tmp_path: Path) -> None:
    (tmp_path / "notiz.txt").write_text("kein Bild")
    with pytest.raises(FileNotFoundError):
        web.resolve_image(tmp_path)


def test_image_error_becomes_an_error_event(client, session: web.ChatSession) -> None:
    session._agent = FakeAgent()
    _, body = client("POST", "/api/chat", {"message": "/image /gibt/es/nicht.png"})
    events = sse_events(body)
    assert events[0]["type"] == "error"
    assert events[-1]["type"] == "done"


def test_saved_settings_take_effect_without_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Im laufenden Prozess gewinnt die Umgebung ueber die .env.

    Ohne override laege die neue Einstellung nur in der Datei -- die
    Oberflaeche meldet aber "sofort aktiv".
    """
    from cortex.config import reset_settings_cache

    env = tmp_path / ".env"
    env.write_text("CORTEX_MODEL=openai/gpt-4o\nCORTEX_LOCATION=Bremen\n")
    monkeypatch.setattr("cortex.config.ENV_CANDIDATES", (env,))
    monkeypatch.setenv("CORTEX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CORTEX_LOCATION", raising=False)
    monkeypatch.delenv("CORTEX_MODEL", raising=False)
    reset_settings_cache()

    fresh = web.ChatSession()
    monkeypatch.setattr(web, "SESSION", fresh)
    assert fresh.settings().location == "Bremen"

    web.save_values({"CORTEX_MODEL": "openai/gpt-4o", "CORTEX_LOCATION": "Hamburg"})
    assert fresh.settings().location == "Hamburg"
    reset_settings_cache()


# -- Netzbetrieb ----------------------------------------------------------
@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> str:
    """Setzt ein Zugangswort, wie es `cortex web --lan` tut."""
    monkeypatch.setattr(web, "TOKEN", "geheim123")
    return "geheim123"


def raw_request(port: int, method: str, path: str, headers: dict[str, str] | None = None):
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    result = (response.status, dict(response.getheaders()), data)
    conn.close()
    return result


@pytest.fixture
def port(session: web.ChatSession):
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_without_a_token_everything_stays_open(port: int) -> None:
    # Der rein lokale Betrieb soll so einfach bleiben wie vorher.
    assert raw_request(port, "GET", "/")[0] == 200


def test_guarded_server_refuses_strangers(port: int, guarded: str) -> None:
    status, _, body = raw_request(port, "GET", "/")
    assert status == 401
    assert "Zugangswort" in body.decode()
    assert raw_request(port, "GET", "/api/config")[0] == 401


def test_token_in_the_address_opens_the_door(port: int, guarded: str) -> None:
    status, headers, _ = raw_request(port, "GET", f"/?token={guarded}")
    assert status == 200
    # ... und wird als Cookie hinterlegt, damit nur der erste Aufruf ihn braucht.
    assert f"{web.TOKEN_COOKIE}={guarded}" in headers.get("Set-Cookie", "")


def test_token_as_cookie_or_header_works(port: int, guarded: str) -> None:
    assert raw_request(port, "GET", "/api/config",
                       {"Cookie": f"{web.TOKEN_COOKIE}={guarded}"})[0] == 200
    assert raw_request(port, "GET", "/api/config", {"X-Cortex-Token": guarded})[0] == 200


def test_a_wrong_token_is_refused(port: int, guarded: str) -> None:
    assert raw_request(port, "GET", f"/?token={guarded}x")[0] == 401
    assert raw_request(port, "GET", "/api/config", {"X-Cortex-Token": "falsch"})[0] == 401
    assert raw_request(port, "POST", "/api/chat", {"X-Cortex-Token": ""})[0] == 401


def test_query_string_does_not_break_routing(port: int) -> None:
    # /?token=... ist immer noch die Startseite, nicht ein unbekannter Pfad.
    assert raw_request(port, "GET", "/?token=egal")[0] == 200
    assert raw_request(port, "GET", "/api/config?x=1")[0] == 200


def test_lan_urls_name_the_reachable_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "lan_address", lambda: "192.168.1.44")
    monkeypatch.setattr(web, "tailscale_address", lambda: "")
    urls = web.urls_for("0.0.0.0", 8765)
    assert urls[0] == "http://192.168.1.44:8765/"
    # Die eigene Maschine steht zuletzt -- die macht der Browser beim Start auf.
    assert urls[-1] == "http://127.0.0.1:8765/"


def test_tailscale_address_is_listed_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "lan_address", lambda: "192.168.1.44")
    monkeypatch.setattr(web, "tailscale_address", lambda: "100.81.120.100")
    found = web.addresses_for("0.0.0.0", 8765)
    assert [url for url, _ in found] == [
        "http://192.168.1.44:8765/",
        "http://100.81.120.100:8765/",
        "http://127.0.0.1:8765/",
    ]
    assert "Tailscale" in dict(found)["http://100.81.120.100:8765/"]


def test_tailscale_range_is_recognised() -> None:
    assert web.is_tailscale("100.81.120.100")
    assert web.is_tailscale("100.64.0.1")
    assert web.is_tailscale("100.127.255.254")
    assert not web.is_tailscale("100.128.0.1")  # ausserhalb von /10
    assert not web.is_tailscale("100.63.255.255")
    assert not web.is_tailscale("192.168.1.4")
    assert not web.is_tailscale("")


def test_lan_address_never_returns_the_tailscale_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sonst stuende die Tailscale-Adresse zweimal da und die echte gar nicht."""
    monkeypatch.setattr(web, "_route_to", lambda target: "100.81.120.100")
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **k: [])
    assert web.lan_address() == ""


def test_local_urls_stay_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "lan_address", lambda: "192.168.1.44")
    monkeypatch.setattr(web, "tailscale_address", lambda: "100.81.120.100")
    assert web.urls_for("127.0.0.1", 8765) == ["http://127.0.0.1:8765/"]


def test_an_explicit_host_is_used_as_given() -> None:
    assert web.urls_for("192.168.1.9", 80)[0] == "http://192.168.1.9:80/"
    # Ein freiwilliges Zugangswort haengt weiterhin an der Adresse.
    assert web.urls_for("192.168.1.9", 80, "k")[0] == "http://192.168.1.9:80/?token=k"


def test_public_host_detection() -> None:
    assert web.is_public_host("0.0.0.0")
    assert web.is_public_host("192.168.1.9")
    assert not web.is_public_host("127.0.0.1")
    assert not web.is_public_host("localhost")


def test_token_is_short_enough_to_type_on_a_phone() -> None:
    token = web.new_token()
    assert 8 <= len(token) <= 24
    assert token.isascii() and " " not in token
    assert web.new_token() != token


def test_lan_address_is_never_loopback() -> None:
    address = web.lan_address()
    assert not address.startswith("127.")  # "" ist erlaubt, 127.x nie


def test_a_waiting_device_is_told_so(session: web.ChatSession) -> None:
    """Zwei Geraete teilen sich eine Sitzung -- das darf nicht stumm haengen."""
    started, release = threading.Event(), threading.Event()

    class Slow(FakeAgent):
        def ask(self, question: str, *, stream: bool = True):
            started.set()
            release.wait(timeout=5)
            return AgentResult(answer="fertig")

    session._agent = Slow()
    first = threading.Thread(target=lambda: session.ask("eins", lambda n, p: None))
    first.start()
    assert started.wait(timeout=5)

    events: list[str] = []
    second = threading.Thread(
        target=lambda: session.ask("zwei", lambda name, payload: events.append(name))
    )
    second.start()
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    assert "waiting" in events


# -- Aufbau der Oberflaeche ----------------------------------------------
def test_style_and_script_blocks_stay_separate() -> None:
    """Ein Skriptblock im <style> faellt sonst nur im Browser auf."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    for tag in ("<style>", "</style>", "<script>", "</script>"):
        assert html.count(tag) == 1, f"{tag} kommt nicht genau einmal vor"
    style = html[html.index("<style>") : html.index("</style>")]
    for js in ("function ", "=>", "await ", "addEventListener"):
        assert js not in style, f"JavaScript im <style>-Block: {js!r}"
    assert style.count("{") == style.count("}"), "unausgeglichene Klammern im CSS"
    script = html[html.index("<script>") : html.index("</script>")]
    assert "{color:" not in script and "px;" not in script


def test_every_request_carries_the_token() -> None:
    """Keine rohen fetch-Aufrufe -- die kaemen im Netzbetrieb ohne Zugangswort."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert 'fetch("/api' not in html
    assert 'X-Cortex-Token' in html


# -- Zugangswort mit Sonderzeichen ---------------------------------------
def test_comparison_survives_non_ascii(monkeypatch: pytest.MonkeyPatch) -> None:
    """compare_digest lehnt Nicht-ASCII als str ab -- ueber Bytes geht es.

    Sonst scheitert JEDER Vergleich mit einem Wort wie "grün", auch der mit
    dem richtigen: der Server wirft dann bei jedem Aufruf eine Ablaufverfolgung
    und niemand kommt mehr rein.
    """
    assert web.same_secret("grün", "grün")
    assert not web.same_secret("gruen", "grün")
    assert not web.same_secret("grün", "gruen")
    assert web.same_secret("abc", "abc")


def test_a_non_ascii_token_never_crashes_the_server(port: int,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "TOKEN", "grün")
    assert raw_request(port, "GET", "/")[0] == 401
    assert raw_request(port, "GET", "/", {"X-Cortex-Token": "falsch"})[0] == 401
    assert raw_request(port, "GET", "/", {"X-Cortex-Token": "grün"})[0] == 200


def test_token_problem_names_the_bad_character() -> None:
    assert "ü" in web.token_problem("grün")
    assert "Leerzeichen" in web.token_problem("mein wort")
    assert web.token_problem("familie2024") == ""
    assert web.token_problem("") == ""
    for character in "?&#/%":
        assert web.token_problem(f"a{character}b"), f"{character} muesste auffallen"


def test_generated_tokens_are_always_acceptable() -> None:
    for _ in range(50):
        assert web.token_problem(web.new_token()) == ""


# -- Fehler in einer Route ------------------------------------------------
def test_a_broken_route_answers_500_instead_of_dying(
    port: int, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Ohne Auffangnetz druckt die Standardbibliothek eine Ablaufverfolgung
    und der Browser bekommt gar nichts -- er versucht es dann endlos."""

    def boom() -> dict[str, str]:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(web, "current_values", boom)
    status, _, body = raw_request(port, "GET", "/api/config")
    assert status == 500
    assert "kaputt" in json.loads(body)["error"]
    assert "Traceback" not in capfd.readouterr().err
    # Der Server lebt weiter.
    assert raw_request(port, "GET", "/")[0] == 200


# -- Rueckfragen im Browser -----------------------------------------------
class AskingAgent(FakeAgent):
    """Stellt beim Recherchieren eine Rueckfrage und wartet auf die Antwort."""

    def __init__(self) -> None:
        super().__init__()
        self.got: str | None = None

    def ask(self, question: str, *, stream: bool = True) -> AgentResult:
        self.asked.append(question)
        self.on_event("ask", {"question": "Welches Budget?", "options": ["bis 800", "egal"]})
        self.got = self.ask_handler("Welches Budget?", ["bis 800", "egal"])
        self.on_event("answer_chunk", {"text": f"Verstanden: {self.got}"})
        self.on_event("done", {"tool_calls": 0})
        return AgentResult(answer="fertig")


def test_the_browser_can_answer_a_question(client, session: web.ChatSession) -> None:
    agent = AskingAgent()
    session._agent = agent

    answered = threading.Event()

    def reply() -> None:
        # Warten, bis die Rueckfrage wirklich gestellt ist.
        for _ in range(100):
            if session.busy():
                break
            threading.Event().wait(0.02)
        threading.Event().wait(0.05)
        client("POST", "/api/answer", {"text": "bis 800"})
        answered.set()

    threading.Thread(target=reply, daemon=True).start()
    status, body = client("POST", "/api/chat", {"message": "Laptop gesucht"})
    assert status == 200
    assert answered.wait(timeout=5)
    assert agent.got == "bis 800"
    events = sse_events(body)
    assert events[0]["type"] == "ask"
    assert events[0]["options"] == ["bis 800", "egal"]
    assert "Verstanden: bis 800" in "".join(e.get("text", "") for e in events)


def test_an_unanswered_question_gives_up(client, session: web.ChatSession,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Sonst haelt eine offene Frage die Sitzung ewig besetzt."""
    monkeypatch.setattr(web.ChatSession, "ANSWER_TIMEOUT", 0.2)
    agent = AskingAgent()
    session._agent = agent
    status, _ = client("POST", "/api/chat", {"message": "Laptop gesucht"})
    assert status == 200
    assert agent.got == ""  # keine Antwort, aber auch kein Haenger


def test_stale_answers_never_leak_into_the_next_question(
    client, session: web.ChatSession
) -> None:
    """Eine Antwort von vorhin darf die naechste Frage nicht beantworten."""
    assert client("POST", "/api/answer", {"text": "uralt"})[0] == 200
    session._answers.put("noch aelter")

    agent = AskingAgent()
    session._agent = agent

    def reply() -> None:
        for _ in range(100):
            if session.busy():
                break
            threading.Event().wait(0.02)
        threading.Event().wait(0.05)
        client("POST", "/api/answer", {"text": "frisch"})

    threading.Thread(target=reply, daemon=True).start()
    client("POST", "/api/chat", {"message": "Laptop gesucht"})
    assert agent.got == "frisch"


def test_answering_when_nobody_asked(client) -> None:
    status, body = client("POST", "/api/answer", {"text": "hallo"})
    assert status == 200
    assert json.loads(body)["ok"] is False


def test_the_ui_can_show_a_question() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert 'case "ask"' in html
    assert "/api/answer" in html
    assert "askcard" in html


# -- Hochgeladene Dateien -------------------------------------------------
def encode(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode()


def tiny_pdf(text: str) -> bytes:
    from tests.test_fetch import _tiny_pdf

    return _tiny_pdf(text)


def tiny_png() -> bytes:
    from cortex.local_model import solid_png

    return solid_png()


@pytest.fixture
def seeing(session: web.ChatSession) -> FakeAgent:
    agent = FakeAgent()
    agent.describe_image = lambda path: f"ein Bild namens {Path(path).name}"  # type: ignore
    session._agent = agent
    return agent


def attach(session: web.ChatSession, name: str, data: bytes) -> str:
    return session.attachments_text(
        session._agent, [{"name": name, "data": encode(data)}], lambda n, p: None
    )


def test_an_image_goes_to_the_vision_model(session: web.ChatSession, seeing: FakeAgent) -> None:
    text = attach(session, "stuhl.png", tiny_png())
    assert "[Bild stuhl.png]" in text
    assert "ein Bild namens" in text


def test_a_pdf_is_read_as_text(session: web.ChatSession, seeing: FakeAgent) -> None:
    text = attach(session, "preise.pdf", tiny_pdf("Kaffee kostet 3 Euro"))
    assert "[PDF preise.pdf" in text
    assert "Kaffee kostet 3 Euro" in text


def test_a_scanned_pdf_says_so_instead_of_pretending(
    session: web.ChatSession, seeing: FakeAgent
) -> None:
    text = attach(session, "scan.pdf", b"%PDF-1.4\nkein echtes PDF")
    assert "kein Text enthalten" in text
    assert "Scan" in text


def test_a_text_file_is_taken_as_it_is(session: web.ChatSession, seeing: FakeAgent) -> None:
    text = attach(session, "notizen.md", b"# Titel\nInhalt")
    assert "[Datei notizen.md]" in text
    assert "# Titel" in text


def test_long_files_are_cut(session: web.ChatSession, seeing: FakeAgent) -> None:
    text = attach(session, "lang.txt", ("x" * 60_000).encode())
    assert len(text) < web.MAX_FILE_CHARS + 200


def test_broken_base64_does_not_kill_the_turn(session: web.ChatSession, seeing: FakeAgent) -> None:
    text = session.attachments_text(
        session._agent, [{"name": "kaputt.png", "data": "!!!kein base64!!!"}], lambda n, p: None
    )
    assert "konnte nicht gelesen werden" in text


def test_an_oversized_file_is_named_not_swallowed(
    session: web.ChatSession, seeing: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web, "MAX_UPLOAD_BYTES", 100)
    text = attach(session, "gross.txt", b"x" * 500)
    assert "zu gross" in text


def test_a_blind_model_does_not_break_the_upload(session: web.ChatSession) -> None:
    agent = FakeAgent()

    def blind(path):
        raise RuntimeError("Modell sieht nichts")

    agent.describe_image = blind  # type: ignore[method-assign]
    session._agent = agent
    text = attach(session, "foto.png", tiny_png())
    assert "konnte nicht angesehen werden" in text
    assert "Modell sieht nichts" in text


def test_only_five_files_are_taken(session: web.ChatSession, seeing: FakeAgent) -> None:
    many = [{"name": f"f{i}.txt", "data": encode(f"Inhalt {i}".encode())} for i in range(9)]
    text = session.attachments_text(session._agent, many, lambda n, p: None)
    assert text.count("[Datei ") == web.MAX_UPLOADS
    assert "Inhalt 5" not in text


def test_uploads_never_escape_their_folder(session: web.ChatSession, seeing: FakeAgent) -> None:
    """Der Dateiname kommt vom Browser -- also von aussen."""
    attach(session, "../../../boese.png", tiny_png())
    folder = session.settings().data_dir / "uploads"
    written = list(folder.iterdir())
    assert written and all(item.parent == folder for item in written)
    assert not (session.settings().data_dir.parent / "boese.png").exists()


def test_attachments_reach_the_agent_through_the_chat(client, session: web.ChatSession) -> None:
    agent = FakeAgent([("done", {"tool_calls": 0})])
    agent.describe_image = lambda path: "ein gruener Stuhl"  # type: ignore[method-assign]
    session._agent = agent
    status, body = client(
        "POST",
        "/api/chat",
        {"message": "Was ist das?", "attachments": [{"name": "s.png", "data": encode(tiny_png())}]},
    )
    assert status == 200
    assert "ein gruener Stuhl" in agent.asked[0]
    assert agent.asked[0].endswith("Was ist das?")
    assert "upload" in [event["type"] for event in sse_events(body)]


def test_a_file_without_a_question_is_still_a_request(client, session: web.ChatSession) -> None:
    agent = FakeAgent([("done", {"tool_calls": 0})])
    session._agent = agent
    status, _ = client(
        "POST",
        "/api/chat",
        {"message": "", "attachments": [{"name": "n.txt", "data": encode(b"Hallo Welt")}]},
    )
    assert status == 200
    assert "Hallo Welt" in agent.asked[0]


def test_an_empty_request_is_still_rejected(client) -> None:
    assert client("POST", "/api/chat", {"message": "  ", "attachments": []})[0] == 400


def test_a_huge_body_is_refused_before_it_is_read(
    port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Grenze koennte ein einziger Aufruf den Speicher fuellen."""
    monkeypatch.setattr(web, "MAX_BODY_BYTES", 500)
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/api/chat", body=b"x" * 5000,
                 headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    assert response.status == 413
    assert "zu gross" in json.loads(body)["error"]


def test_bad_attachment_shapes_are_ignored(client, session: web.ChatSession) -> None:
    agent = FakeAgent([("done", {"tool_calls": 0})])
    session._agent = agent
    status, _ = client(
        "POST", "/api/chat", {"message": "Hallo", "attachments": ["kein Objekt", 42]}
    )
    assert status == 200
    assert agent.asked[0] == "Hallo"


def test_the_ui_offers_the_upload() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert 'id="picker"' in html and 'type="file"' in html
    assert "readAsDataURL" in html
    assert "dragging" in html  # Ablegen per Drag-and-drop
    assert "clipboardData" in html  # Einfuegen aus der Zwischenablage


def test_old_uploads_are_cleaned_up(session: web.ChatSession, seeing: FakeAgent,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Sonst fuellt sich der Ordner ueber Monate mit alten Fotos."""
    monkeypatch.setattr(web, "KEEP_UPLOADS", 3)
    for index in range(6):
        attach(session, f"bild{index}.png", tiny_png())
    folder = session.settings().data_dir / "uploads"
    assert len(list(folder.iterdir())) == 3
    # Das zuletzt hochgeladene ist noch da.
    assert any("bild5" in item.name for item in folder.iterdir())


# -- Home Assistant in der Oberflaeche ------------------------------------
def test_the_token_never_goes_to_the_browser(client, session: web.ChatSession) -> None:
    """Nur ob eines da ist -- nie das Token selbst."""
    session._settings.ha_url = "http://192.168.1.5:8123"
    session._settings.ha_token = "streng-geheim"
    _, body = client("GET", "/api/config")
    payload = json.loads(body)
    assert payload["ha_connected"] is True
    assert "streng-geheim" not in body.decode()
    assert "CORTEX_HA_URL" in payload["values"]
    assert not any("geheim" in str(value) for value in payload["values"].values())


def test_ha_discovery_reports_what_it_found(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cortex.homeassistant.discover", lambda: ["http://192.168.1.5:8123"])
    status, body = client("POST", "/api/ha", {"action": "discover"})
    assert status == 200
    assert json.loads(body) == {"ok": True, "found": ["http://192.168.1.5:8123"]}


def test_ha_test_needs_both_pieces(client) -> None:
    _, body = client("POST", "/api/ha", {"url": "http://x:8123", "token": ""})
    assert json.loads(body)["ok"] is False
    assert "beide" in json.loads(body)["error"]


def test_ha_test_reports_success(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cortex.homeassistant.HomeAssistant.ping", lambda self: "Zuhause 2026.8")
    monkeypatch.setattr(
        "cortex.homeassistant.HomeAssistant.domains", lambda self: {"light": 4, "sensor": 9}
    )
    _, body = client("POST", "/api/ha", {"url": "192.168.1.5", "token": "t"})
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["name"] == "Zuhause 2026.8"
    assert payload["entities"] == 13
    assert payload["url"] == "http://192.168.1.5:8123"  # Adresse wurde vervollstaendigt


def test_ha_test_passes_the_error_through(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex.homeassistant import HomeAssistantError

    def boom(self):
        raise HomeAssistantError("Token abgelehnt")

    monkeypatch.setattr("cortex.homeassistant.HomeAssistant.ping", boom)
    _, body = client("POST", "/api/ha", {"url": "192.168.1.5", "token": "falsch"})
    assert json.loads(body) == {"ok": False, "error": "Token abgelehnt"}


def test_an_empty_ha_token_keeps_the_stored_one(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    target.write_text("HA_TOKEN=alt-und-gut\n")
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    web.save_values({"CORTEX_HA_URL": "http://192.168.1.5:8123", web.HA_TOKEN_FIELD: "   "})
    assert "HA_TOKEN=alt-und-gut" in target.read_text()


def test_a_new_ha_token_is_written(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    web.save_values({web.HA_TOKEN_FIELD: "neues-token"})
    assert "HA_TOKEN=neues-token" in target.read_text()


# -- Die neue Oberflaeche -------------------------------------------------
def test_the_ui_looks_like_the_new_design() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert "<aside>" in html                      # Seitenleiste
    assert 'id="recents"' in html                 # zuletzt Gefragtes
    assert 'id="greeting"' in html                # Begruessung
    assert "--serif" in html                      # Serifenschrift fuer die Begruessung
    assert "prefers-color-scheme" in html         # folgt dem System


def test_the_palette_is_green_not_orange() -> None:
    """Der Akzent traegt die ganze Oberflaeche -- er muss gruen sein."""
    import re

    html = web.UI_FILE.read_text(encoding="utf-8")
    accents = re.findall(r"--accent:\s*(#[0-9a-fA-F]{6})", html)
    assert accents, "keine Akzentfarbe gefunden"
    for colour in accents:
        red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
        assert green > red and green > blue, f"{colour} ist nicht gruen"


def test_the_ui_shows_the_home_steps() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    for event in ("lan_scan", "lan_done", "lan_check", "ha_read", "ha_call"):
        assert f'case "{event}"' in html, f"{event} wird nicht angezeigt"


def test_the_ui_can_set_up_home_assistant() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert 'id="ha-find"' in html and 'id="ha-test"' in html
    assert "/api/ha" in html
    assert f'name="{web.HA_TOKEN_FIELD}"' in html


def test_the_sidebar_list_can_shrink_and_scroll() -> None:
    """Ohne min-height:0 quetscht ein Flex-Kind mit overflow-y:auto seine
    Zeilen ineinander, statt sauber zu scrollen -- der gemeldete Fehler."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    recents_rule = html[html.index(".recents{") : html.index("}", html.index(".recents{"))]
    assert "min-height:0" in recents_rule


def test_the_sidebar_lists_chats_not_single_questions() -> None:
    """Ein Eintrag ist ein Chat -- deshalb tauchen Wiederholungen wie
    dreimal "hallo" gar nicht erst als drei Zeilen auf."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert "/api/chats" in html
    assert "openChat(chat.session_id)" in html
    assert "chat.turns" in html


# -- Modellauswahl, Auslastung, Speicher ----------------------------------
def test_the_model_list_offers_what_actually_works(
    client, session: web.ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine Auswahl, die beim Anklicken scheitert, hilft niemandem."""
    monkeypatch.setattr("cortex.local_model.installed_models", lambda base: ["gemma4:12b"])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status, body = client("GET", "/api/models")
    assert status == 200
    models = json.loads(body)["models"]
    ids = [item["id"] for item in models]
    assert "ollama_chat/gemma4:12b" in ids
    assert "openai/gpt-4o" in ids
    # Ohne Schluessel keine Zeile -- sonst waehlt man etwas, das nicht laeuft.
    assert not any(item["id"].startswith("anthropic/") for item in models)
    assert all(item["note"] for item in models), "jede Zeile braucht eine Erklaerung"


def test_every_model_says_where_it_runs(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cortex.local_model.installed_models", lambda base: ["qwen3:8b"])
    _, body = client("GET", "/api/models")
    local = [m for m in json.loads(body)["models"] if m["id"].startswith("ollama_chat/")]
    assert local and local[0]["kind"] == "lokal"


def test_the_system_endpoint_reports_the_load(client) -> None:
    status, body = client("GET", "/api/system")
    assert status == 200
    payload = json.loads(body)
    assert payload["cpu"]["cores"] >= 1
    assert "system" in payload and "python" in payload


def test_the_load_includes_the_storage_when_it_is_on(
    client, session: web.ChatSession
) -> None:
    session._settings.memory_enabled = True
    _, body = client("GET", "/api/system")
    storage = json.loads(body)["storage"]
    assert storage["limit_mb"] == 400
    # Der Arbeitsspeicher darf davon nicht ueberschrieben werden.
    assert "memory" in json.loads(body)


def test_no_storage_line_when_memory_is_off(client, session: web.ChatSession) -> None:
    session._settings.memory_enabled = False
    assert "storage" not in json.loads(client("GET", "/api/system")[1])


# -- Slash-Befehle fuer den Speicher --------------------------------------
def test_memory_command_shows_what_is_stored(client, session: web.ChatSession) -> None:
    session.memory().remember("Der Nutzer wohnt in Bremen.", topic="Wohnort")
    _, body = client("POST", "/api/command", {"line": "/memory"})
    text = json.loads(body)["text"]
    assert "Bremen" in text
    assert "von 400 MB" in text


def test_forget_empties_the_store(client, session: web.ChatSession) -> None:
    session.memory().remember("weg damit")
    _, body = client("POST", "/api/command", {"line": "/forget"})
    assert "1 Notizen" in json.loads(body)["text"]
    assert session.memory().count() == 0


def test_uploads_command_lists_and_clears(client, session: web.ChatSession) -> None:
    uploads = session.settings().data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "123-foto.png").write_bytes(b"x" * 2048)

    _, body = client("POST", "/api/command", {"line": "/uploads"})
    listing = json.loads(body)["text"]
    assert "foto.png" in listing
    assert "123-" not in listing, "der Zeitstempel interessiert niemanden"

    _, body = client("POST", "/api/command", {"line": "/uploads clear"})
    assert "geloescht" in json.loads(body)["text"]
    assert not list(uploads.iterdir())


def test_uploads_command_on_an_empty_folder(client) -> None:
    assert "nichts" in json.loads(client("POST", "/api/command", {"line": "/uploads"})[1])["text"]


def test_memory_command_says_when_it_is_off(client, session: web.ChatSession) -> None:
    session._settings.memory_enabled = False
    text = json.loads(client("POST", "/api/command", {"line": "/memory"})[1])["text"]
    assert "ausgeschaltet" in text
    assert "Einstellungen" in text


# -- Handy-Layout ---------------------------------------------------------
def test_the_ui_has_a_phone_layout() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert "@media (max-width:900px)" in html
    assert "@media (max-width:430px)" in html
    assert "env(safe-area-inset-bottom)" in html, "randlose Bildschirme"
    assert "viewport-fit=cover" in html


def test_the_input_does_not_zoom_on_ios() -> None:
    """Unter 16px zoomt iOS beim Antippen ins Feld hinein."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    phone = html[html.index("@media (max-width:900px)") :]
    assert "textarea{font-size:16px}" in phone


def test_newest_chat_comes_first(client, session: web.ChatSession) -> None:
    """Die Reihenfolge kommt jetzt aus der Datenbank, nicht aus dem Browser."""
    cache = Cache(session.settings().db_path, 24)
    cache.add_history(session_id="alt", question="zuerst gefragt", answer="…", meta={})
    cache.add_history(session_id="neu", question="zuletzt gefragt", answer="…", meta={})
    _, body = client("GET", "/api/chats")
    titles = [chat["title"] for chat in json.loads(body)["chats"]]
    assert titles[0] == "zuletzt gefragt"


def test_the_ui_offers_the_model_picker_and_load_button() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert 'id="btn-model"' in html and 'id="model-list"' in html
    assert "/api/models" in html
    assert 'id="showload"' in html and "/api/system" in html
    assert 'id="provider"' in html, "Anbieter zum Auswaehlen"
    assert 'name="CORTEX_MEMORY"' in html


def test_the_ui_offers_reading_along() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert 'id="showtrace"' in html, "der Schalter in den Einstellungen"
    assert "body.tracing .trace" in html, "eingeblendet wird per Klasse"
    for kind in ('case "thought"', 'case "action"', 'case "action_done"'):
        assert kind in html, kind


def test_the_trace_state_is_declared_before_the_read_loop() -> None:
    """`let` in der Leseschleife spaeter zu deklarieren gibt einen ReferenceError.

    Genau das ist passiert: handle() laeuft, bevor eine weiter unten stehende
    let-Zeile ueberhaupt ausgefuehrt wurde -- und die ganze Antwort brach mit
    "Cannot access 'thinking' before initialization" ab.
    """
    html = web.UI_FILE.read_text(encoding="utf-8")
    declared = html.index("thinking = null")
    used = html.index("if (!thinking) thinking = trace(")
    assert declared < used


def test_escape_closes_the_settings_window() -> None:
    """Sonst bleibt es offen und die Auslastung fragt im Hintergrund weiter."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    handler = html[html.index('e.key !== "Escape"') :]
    handler = handler[: handler.index("});")]
    assert "closePicker()" in handler, "erst die Modellliste"
    assert "closeSettings()" in handler, "dann die Einstellungen"


# -- Anbieter-Kuerzel ergaenzen -------------------------------------------
def test_a_missing_provider_prefix_is_added() -> None:
    """Wer bei NVIDIA die Modell-ID kopiert, bekommt sie ohne Kuerzel."""
    assert (
        web.fix_model_id("nvidia/nemotron-3-ultra-550b-a55b")
        == "nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b"
    )


def test_a_complete_model_id_is_left_alone() -> None:
    for model in ("openai/gpt-4o", "anthropic/claude-sonnet-4-6", "ollama_chat/gemma4:12b"):
        assert web.fix_model_id(model) == model


def test_nonsense_stays_nonsense(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raten waere schlimmer als eine ehrliche Fehlermeldung."""
    assert web.fix_model_id("voelliger-quatsch-xyz") == "voelliger-quatsch-xyz"
    assert web.fix_model_id("") == ""


def test_saving_corrects_the_model_id(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    web.save_values({"CORTEX_MODEL": "nvidia/nemotron-3-ultra-550b-a55b"})
    assert "CORTEX_MODEL=nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b" in target.read_text()


def test_the_key_lands_under_the_corrected_provider(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst waere der Schluessel unter dem falschen Namen abgelegt."""
    target = tmp_path / ".env"
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    web.save_values(
        {
            "CORTEX_MODEL": "nvidia/nemotron-3-ultra-550b-a55b",
            web.API_KEY_FIELD: "nvapi-Spf8beispiel",
        }
    )
    assert "NVIDIA_NIM_API_KEY=nvapi-Spf8beispiel" in target.read_text()


def test_vision_and_helper_models_are_corrected_too(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    web.save_values({"CORTEX_VISION_MODEL": "nvidia/nemotron-3-ultra-550b-a55b"})
    assert "CORTEX_VISION_MODEL=nvidia_nim/nvidia/" in target.read_text()


def test_the_ui_has_its_own_save_button_for_the_model() -> None:
    """Fuer einen Modellwechsel soll man nicht durch das ganze Formular scrollen."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert 'id="save-model"' in html
    assert "MODEL_FIELDS" in html
    assert 'id="model-note"' in html


def test_the_ui_shows_what_a_key_looks_like() -> None:
    """Wer den falschen Schluessel einfuegt, soll es sofort sehen."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert "nvapi-" in html
    assert "nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b" in html


# -- Chats statt Einzelfragen ---------------------------------------------
def test_a_chat_is_named_after_its_first_question(client, session: web.ChatSession) -> None:
    """Wie ein Ordner: benannt nach dem, weswegen man ihn angelegt hat."""
    cache = Cache(session.settings().db_path, 24)
    for question in ("Gute Cafes in Bremen?", "davon nur mit WLAN", "und sonntags?"):
        cache.add_history(session_id="c1", question=question, answer="…", meta={})
    _, body = client("GET", "/api/chats")
    chats = json.loads(body)["chats"]
    assert len(chats) == 1, "drei Fragen sind ein Chat, nicht drei"
    assert chats[0]["title"] == "Gute Cafes in Bremen?"
    assert chats[0]["turns"] == 3


def test_a_new_chat_gets_its_own_entry(client, session: web.ChatSession) -> None:
    cache = Cache(session.settings().db_path, 24)
    cache.add_history(session_id="c1", question="erste Sache", answer="…", meta={})
    cache.add_history(session_id="c2", question="ganz andere Sache", answer="…", meta={})
    chats = json.loads(client("GET", "/api/chats")[1])["chats"]
    assert [chat["title"] for chat in chats] == ["ganz andere Sache", "erste Sache"]


def test_opening_a_chat_returns_its_turns(client, session: web.ChatSession) -> None:
    cache = Cache(session.settings().db_path, 24)
    cache.add_history(session_id="c1", question="erste Frage", answer="erste Antwort", meta={})
    cache.add_history(session_id="c1", question="zweite Frage", answer="zweite Antwort", meta={})
    _, body = client("POST", "/api/open", {"session_id": "c1"})
    payload = json.loads(body)
    assert payload["ok"] is True
    assert [turn["question"] for turn in payload["turns"]] == ["erste Frage", "zweite Frage"]
    assert payload["title"] == "erste Frage"


def test_opening_a_chat_restores_the_context(session: web.ChatSession) -> None:
    """Nachfragen wie "und davon nur die guenstigen" muessen weiter gehen."""
    cache = Cache(session.settings().db_path, 24)
    cache.add_history(session_id="c1", question="Laptops bis 1200?", answer="Drei Stueck.",
                      meta={})
    session._agent = FakeAgent()

    from cortex.agent import Agent

    real = Agent(session.settings())
    session._agent = real
    session.open_chat("c1")
    texts = [str(message.get("content") or "") for message in real.messages]
    assert any("Laptops bis 1200?" in text for text in texts)
    assert any("Drei Stueck." in text for text in texts)
    assert real.session_id == "c1"


def test_opening_a_gone_chat_says_so(client) -> None:
    _, body = client("POST", "/api/open", {"session_id": "gibt-es-nicht"})
    payload = json.loads(body)
    assert payload["turns"] == []
    assert "nicht mehr" in payload["note"]


def test_opening_needs_a_chat_id(client) -> None:
    assert client("POST", "/api/open", {"session_id": "  "})[0] == 400


def test_a_new_chat_starts_a_new_entry(client, session: web.ChatSession) -> None:
    """Neuer Chat heisst: die naechste Frage benennt einen neuen Eintrag."""
    from cortex.agent import Agent

    session._agent = Agent(session.settings())
    before = session.chat_id()
    _, body = client("POST", "/api/clear")
    after = json.loads(body)["current"]
    assert after and after != before


# -- Anbieterwechsel: keine fremde Adresse --------------------------------
def test_an_ollama_address_never_reaches_the_cloud() -> None:
    """Ollama antwortet mit "404 page not found" -- das sieht aus wie ein
    Fehler des Anbieters, ist aber nur die falsche Adresse."""
    from cortex.config import Settings as S

    settings = S(model="nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b",
                 api_base="http://localhost:11434")
    assert "api_base" not in settings.llm_kwargs_for(settings.model)


def test_a_local_model_keeps_its_address() -> None:
    from cortex.config import Settings as S

    settings = S(model="ollama_chat/gemma4:12b", api_base="http://localhost:11434")
    assert settings.llm_kwargs_for(settings.model)["api_base"] == "http://localhost:11434"


def test_a_proxy_is_not_mistaken_for_ollama() -> None:
    """Ein LiteLLM-Proxy im Heimnetz ist ein berechtigter Weg zur Cloud."""
    from cortex.config import base_fits

    assert base_fits("http://192.168.1.9:4000", "nvidia_nim/meta/llama")
    assert base_fits("http://localhost:4000", "openai/gpt-4o")
    assert not base_fits("http://192.168.1.9:11434", "openai/gpt-4o")


# ---------------------------------------------------------------------------
# Terminal-Setup und Web-Einstellungen zeigen dasselbe
# ---------------------------------------------------------------------------
def test_everything_setup_asks_for_is_in_the_web_form() -> None:
    """Was `cortex setup` fragt, muss auch im Browser einstellbar sein.

    Der Test liest die Schluessel direkt aus dem Setup-Quelltext -- kommt dort
    eine Frage dazu, faellt er auf, bis das Formular nachzieht.
    """
    import inspect
    import re

    from cortex import cli

    source = inspect.getsource(cli.setup_command)
    asked = set(re.findall(r'"(CORTEX_[A-Z_]+)"', source))
    html = web.UI_FILE.read_text(encoding="utf-8")
    for key in asked:
        assert f'name="{key}"' in html, f"{key} wird im Terminal gefragt, fehlt aber im Formular"


def test_the_search_engine_key_can_be_set_in_the_browser() -> None:
    """Brave und Tavily brauchen einen Schluessel -- den fragt das Terminal ab."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert f'name="{web.SEARCH_KEY_FIELD}"' in html


def test_saving_stores_the_search_key_under_the_right_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / ".env"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    monkeypatch.setattr(web.SESSION, "reload", lambda: None)

    web.save_values({"CORTEX_SEARCH_BACKEND": "brave", web.SEARCH_KEY_FIELD: "bsa-xyz"})
    assert "BRAVE_API_KEY=bsa-xyz" in target.read_text(encoding="utf-8")


def test_an_empty_search_key_means_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / ".env"
    target.write_text("BRAVE_API_KEY=alt\n", encoding="utf-8")
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    monkeypatch.setattr(web.SESSION, "reload", lambda: None)

    web.save_values({"CORTEX_SEARCH_BACKEND": "brave", web.SEARCH_KEY_FIELD: "   "})
    assert "BRAVE_API_KEY=alt" in target.read_text(encoding="utf-8")


def test_the_open_metasearch_needs_no_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ohne Schluesselnamen wird auch nichts geschrieben -- kein Phantomeintrag."""
    target = tmp_path / ".env"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    monkeypatch.setattr(web.SESSION, "reload", lambda: None)

    web.save_values({"CORTEX_SEARCH_BACKEND": "duckduckgo", web.SEARCH_KEY_FIELD: "egal"})
    content = target.read_text(encoding="utf-8")
    assert "egal" not in content


def test_the_probe_endpoint_tests_the_form_values(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Getestet wird, was im Formular steht -- sonst prueft man den alten Stand."""
    seen: dict[str, Any] = {}

    def fake_llm(model, api_key="", api_base=""):
        seen["model"] = model
        seen["key"] = api_key
        return True, "ok"

    def fake_search(backend, api_key="", engines="", instance_url=""):
        seen["backend"] = backend
        return True, "3 Treffer"

    monkeypatch.setattr("cortex.probe.check_llm", fake_llm)
    monkeypatch.setattr("cortex.probe.check_search", fake_search)

    status, body = client(
        "POST",
        "/api/probe",
        {
            "CORTEX_MODEL": "openai/gpt-4o",
            "CORTEX_SEARCH_BACKEND": "brave",
            web.API_KEY_FIELD: "sk-neu",
        },
    )
    assert status == 200
    data = json.loads(body)
    assert data["ok"] is True
    assert seen == {"model": "openai/gpt-4o", "key": "sk-neu", "backend": "brave"}


def test_the_probe_reports_a_failure_without_crashing(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cortex.probe.check_llm", lambda *a, **k: (False, "401 Unauthorized"))
    monkeypatch.setattr("cortex.probe.check_search", lambda *a, **k: (True, "3 Treffer"))
    status, body = client("POST", "/api/probe", {"CORTEX_MODEL": "openai/gpt-4o"})
    data = json.loads(body)
    assert status == 200
    assert data["ok"] is False
    assert "401" in data["llm"]["message"]
    assert data["search"]["ok"] is True


def test_a_leftover_ollama_base_is_ignored_in_the_probe(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst testet man Ollama und bekommt gruenes Licht fuer NVIDIA."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        "cortex.probe.check_llm",
        lambda model, api_key="", api_base="": (seen.update(base=api_base), (True, "ok"))[1],
    )
    monkeypatch.setattr("cortex.probe.check_search", lambda *a, **k: (True, "ok"))
    client(
        "POST",
        "/api/probe",
        {"CORTEX_MODEL": "openai/gpt-4o", "CORTEX_API_BASE": "http://localhost:11434"},
    )
    assert seen["base"] == ""


# ---------------------------------------------------------------------------
# Gmail und Kalender in den Einstellungen
# ---------------------------------------------------------------------------
def test_the_settings_have_their_own_google_section() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert "Gmail &amp; Kalender" in html, "eigene Sparte"
    assert 'name="CORTEX_GOOGLE"' in html, "an- und ausschaltbar"
    assert f'name="{web.GOOGLE_ID_FIELD}"' in html
    assert f'name="{web.GOOGLE_SECRET_FIELD}"' in html
    assert "console.cloud.google.com" in html, "die Anleitung steht dabei"
    assert "Gmail API" in html and "Google Calendar API" in html
    assert "Testnutzer" in html, "der haeufigste Stolperstein"


def test_the_google_secret_never_reaches_the_browser(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wie beim HA-Token: der Browser erfaehrt nur, DASS eines da ist."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id-123.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "streng-geheim")
    web.SESSION.reload()
    _, body = client("GET", "/api/config")
    assert b"streng-geheim" not in body
    data = json.loads(body)
    assert data["google"]["has_secret"] is True
    assert data["google"]["connected"] is False


def test_saving_stores_the_google_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / ".env"
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    monkeypatch.setattr(web.SESSION, "reload", lambda: None)

    web.save_values(
        {
            "CORTEX_GOOGLE": "true",
            web.GOOGLE_ID_FIELD: "id-1.apps.googleusercontent.com",
            web.GOOGLE_SECRET_FIELD: "s3cret",
        }
    )
    content = target.read_text(encoding="utf-8")
    assert "GOOGLE_CLIENT_ID=id-1.apps.googleusercontent.com" in content
    assert "GOOGLE_CLIENT_SECRET=s3cret" in content
    assert "CORTEX_GOOGLE=true" in content


def test_an_empty_google_secret_means_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / ".env"
    target.write_text("GOOGLE_CLIENT_SECRET=alt\n", encoding="utf-8")
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    monkeypatch.setattr(web.SESSION, "reload", lambda: None)

    web.save_values({web.GOOGLE_SECRET_FIELD: "  "})
    assert "GOOGLE_CLIENT_SECRET=alt" in target.read_text(encoding="utf-8")


def test_the_consent_link_is_built_on_request(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id-123.apps.googleusercontent.com")
    web.SESSION.reload()
    status, body = client("POST", "/api/google", {"action": "start"})
    data = json.loads(body)
    assert status == 200 and data["ok"] is True
    assert "accounts.google.com" in data["url"]
    assert "gmail.readonly" in data["url"]
    assert data["redirect"].startswith("http://localhost:")


def test_connecting_without_credentials_says_so(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    web.SESSION.reload()
    _, body = client("POST", "/api/google", {"action": "finish", "code": "abc"})
    data = json.loads(body)
    assert data["ok"] is False
    assert "Client-ID" in data["error"]


def test_the_redirect_matches_what_google_allows() -> None:
    """Google erlaubt fuer Desktop-Anwendungen nur localhost."""
    assert web.google_redirect("192.168.1.5:8765") == "http://localhost:8765/google"
    assert web.google_redirect("") == f"http://localhost:{web.DEFAULT_PORT}/google"
    assert web.google_redirect("kaputt:abc") == f"http://localhost:{web.DEFAULT_PORT}/google"


def test_the_return_from_google_finishes_the_connection(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sitzt der Browser auf demselben Rechner, ist danach alles fertig."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id-1.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s3cret")
    web.SESSION.reload()

    from cortex.google import Tokens

    monkeypatch.setattr(
        "cortex.google.exchange_code",
        lambda cid, secret, code, redirect: Tokens(
            access_token="at", refresh_token="rt", expires_at=time.time() + 3600
        ),
    )
    monkeypatch.setattr("cortex.google.Google.remember", lambda self, tokens: None)
    monkeypatch.setattr("cortex.google.Google.account", lambda self: "jemand@example.com")

    status, body = client("GET", "/google?code=4/0AX")
    assert status == 200
    assert b"jemand@example.com" in body


def test_a_refusal_at_google_is_shown_not_swallowed(client) -> None:
    status, body = client("GET", "/google?error=access_denied")
    assert status == 200
    assert b"access_denied" in body


def test_a_return_without_a_code_says_so(client) -> None:
    status, body = client("GET", "/google")
    assert status == 200
    assert b"keinen Code" in body


# ---------------------------------------------------------------------------
# Der Strom darf nicht abreissen, waehrend das Modell nachdenkt
# ---------------------------------------------------------------------------
def test_the_stream_starts_with_a_sign_of_life(client, session: web.ChatSession) -> None:
    """Erst mit dem ersten Byte steht die Verbindung fuer den Browser wirklich."""
    _, raw = client("POST", "/api/chat", {"message": "hallo"})
    assert raw.startswith(b": los")


def test_a_silent_model_does_not_kill_the_connection(
    client, session: web.ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Cloud-Modell schweigt zwischen zwei Schritten gern eine halbe Minute.

    Ohne ein Byte in der Leitung legt irgendwer in der Kette auf, und die
    Oberflaeche meldete "TypeError: network error", obwohl die Recherche noch
    lief. Der Herzschlag haelt sie warm.
    """
    monkeypatch.setattr(web, "HEARTBEAT_SECONDS", 0.05)

    class Slow:
        on_event = None
        toolbox = None

        def set_ask_handler(self, handler):
            pass

        def ask(self, message, stream=True):
            time.sleep(0.4)
            self.on_event("answer_chunk", {"text": "Da bin ich."})
            self.on_event("done", {"tool_calls": 0, "hit_limit": False})
            return SimpleNamespace(answer="Da bin ich.")

    slow = Slow()
    slow.toolbox = slow
    monkeypatch.setattr(session, "agent", lambda: slow)

    _, raw = client("POST", "/api/chat", {"message": "dauert"})
    assert raw.count(b": warte") >= 2, "waehrend der Stille geht regelmaessig ein Byte raus"
    assert [event["type"] for event in sse_events(raw)] == ["chunk", "done"]


def test_heartbeats_are_not_mistaken_for_events(client, session: web.ChatSession) -> None:
    """Kommentarzeilen duerfen nie als Ereignis durchgehen."""
    _, raw = client("POST", "/api/chat", {"message": "hallo"})
    for event in sse_events(raw):
        assert event["type"] != "warte"


def test_the_ui_ignores_stream_comments() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert 'part.trimStart().startsWith(":")' in html


def test_a_broken_stream_is_explained_in_plain_words() -> None:
    """"TypeError: network error" ist keine Auskunft, mit der jemand etwas anfangen kann."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert "Die Verbindung ist mittendrin abgerissen" in html
    assert "Keine Verbindung zu Cortex" in html
    assert "err instanceof TypeError" in html


# ---------------------------------------------------------------------------
# Abbrechen
# ---------------------------------------------------------------------------
def test_the_upload_button_turns_into_a_stop_button() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert 'id="stop"' in html and "Abbrechen" in html
    assert '$("#clip").hidden = on;' in html, "waehrend der Anfrage weg"
    assert '$("#stop").hidden = !on;' in html, "und der Abbruch da"


def test_hidden_actually_hides() -> None:
    """`.tool` setzt ein eigenes display -- ohne diese Regel bleibt der Knopf da.

    Genau das war der Fall: beide Knoepfe standen nebeneinander, obwohl einer
    das hidden-Attribut trug.
    """
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert "[hidden]{display:none!important}" in html


def test_stopping_reports_that_nothing_was_running(client, session: web.ChatSession) -> None:
    status, body = client("POST", "/api/stop")
    assert status == 200
    assert json.loads(body) == {"ok": False}


def test_stopping_cancels_the_running_agent(
    client, session: web.ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Lauf soll wirklich enden, nicht nur im Browser verschwinden."""
    started = threading.Event()
    cancelled = threading.Event()

    class Slow:
        on_event = None
        toolbox = None

        def set_ask_handler(self, handler):
            pass

        def cancel(self):
            cancelled.set()

        def ask(self, message, stream=True):
            started.set()
            for _ in range(100):
                if cancelled.is_set():
                    break
                time.sleep(0.02)
            self.on_event("done", {"tool_calls": 0, "hit_limit": False})
            return SimpleNamespace(answer="abgebrochen")

    slow = Slow()
    slow.toolbox = slow
    monkeypatch.setattr(session, "agent", lambda: slow)
    # Im Betrieb fuellt `agent()` dieses Feld; der Abbruch liest es absichtlich
    # ohne die Sperre, weil er sonst auf das Ende des Laufs warten wuerde.
    monkeypatch.setattr(session, "_agent", slow)

    worker = threading.Thread(
        target=lambda: client("POST", "/api/chat", {"message": "dauert"}), daemon=True
    )
    worker.start()
    assert started.wait(timeout=5), "die Anfrage muss erst laufen"

    status, body = client("POST", "/api/stop")
    assert status == 200
    assert json.loads(body) == {"ok": True}
    assert cancelled.wait(timeout=5)
    worker.join(timeout=5)
    assert not worker.is_alive(), "der Durchlauf endet danach von selbst"


# ---------------------------------------------------------------------------
# Lagerverwaltung in den Einstellungen
# ---------------------------------------------------------------------------
def test_the_storage_is_set_up_in_the_network_section() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert 'name="CORTEX_STORAGE_URL"' in html
    assert 'name="CORTEX_STORAGE_ACCESS"' in html
    for level in ('value="off"', 'value="read"', 'value="write"'):
        assert level in html, level
    assert 'id="storage-find"' in html and 'id="storage-test"' in html
    # Der Abschnitt gehoert zu "Zuhause & Netz", nicht in ein eigenes Feld.
    section = html[html.index("Zuhause &amp; Netz") :]
    assert "Lagerverwaltung" in section[: section.index("</fieldset>")]


def test_the_settings_say_that_read_only_is_no_lock() -> None:
    """Der Server selbst kennt keine Anmeldung -- das darf nicht verschwiegen werden."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert "keine Anmeldung" in html
    assert "kein Schloss am" in html


def test_the_storage_probe_reports_what_it_found(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Fake:
        url = "http://192.168.1.5:3000"

        def __init__(self, *args, **kwargs):
            pass

        def info(self):
            return {"app": "storage-system", "name": "Lagerverwaltung", "version": "1.0.0"}

        def rooms(self):
            return [{"id": 1, "name": "Keller", "itemCount": 12}]

        def close(self):
            pass

    monkeypatch.setattr("cortex.storage.Storage", Fake)
    status, body = client("POST", "/api/storage", {"url": "192.168.1.5:3000"})
    data = json.loads(body)
    assert status == 200 and data["ok"] is True
    assert data["rooms"] == 1 and data["items"] == 12


def test_the_storage_probe_only_reads(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Verbindungstest darf nichts anlegen -- auch nicht versehentlich."""
    seen: dict[str, Any] = {}

    class Fake:
        url = "http://x"

        def __init__(self, url, access="read", **kwargs):
            seen["access"] = access

        def info(self):
            return {"app": "storage-system"}

        def rooms(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr("cortex.storage.Storage", Fake)
    client("POST", "/api/storage", {"url": "192.168.1.5:3000"})
    assert seen["access"] == "read"


def test_searching_the_network_says_so_when_nothing_answers(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cortex.storage.discover", lambda subnet: [])
    _, body = client("POST", "/api/storage", {})
    data = json.loads(body)
    assert data["ok"] is False
    assert "Nichts gefunden" in data["error"]


def test_a_closed_tab_ends_the_run(
    client, session: web.ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst haelt die verlassene Anfrage die Sitzung minutenlang besetzt.

    Genau das war der Fehler: die naechste Frage bekam "Ein anderes Geraet
    fragt gerade" zu sehen, obwohl gar kein anderes Geraet da war -- es war
    die eigene, laengst geschlossene Anfrage.
    """
    monkeypatch.setattr(web, "HEARTBEAT_SECONDS", 0.05)
    started = threading.Event()
    cancelled = threading.Event()

    class Slow:
        on_event = None
        toolbox = None

        def set_ask_handler(self, handler):
            pass

        def cancel(self):
            cancelled.set()

        def ask(self, message, stream=True):
            started.set()
            for _ in range(200):
                if cancelled.is_set():
                    break
                time.sleep(0.02)
            self.on_event("done", {"tool_calls": 0, "hit_limit": False})
            return SimpleNamespace(answer="")

    slow = Slow()
    slow.toolbox = slow
    monkeypatch.setattr(session, "agent", lambda: slow)
    monkeypatch.setattr(session, "_agent", slow)

    # Verbindung aufbauen, Kopfzeilen abholen, dann einfach weggehen.
    conn = HTTPConnection("127.0.0.1", client.port, timeout=10)
    conn.request(
        "POST",
        "/api/chat",
        body=json.dumps({"message": "dauert"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    conn.getresponse()
    assert started.wait(timeout=5)
    conn.close()

    assert cancelled.wait(timeout=10), "der Lauf muss enden, wenn niemand mehr zuhoert"
    # Der Arbeitsthread braucht noch einen Wimpernschlag, um die Sperre
    # freizugeben -- darauf warten, statt es im selben Atemzug zu pruefen.
    for _ in range(100):
        if not session.busy():
            break
        time.sleep(0.05)
    assert not session.busy(), "und die Sitzung danach wieder frei sein"


def test_the_waiting_notice_does_not_invent_another_device(
    client, session: web.ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer allein ist, soll nicht lesen, jemand anderes sei schuld."""
    monkeypatch.setattr(web, "HEARTBEAT_SECONDS", 0.05)
    running = threading.Event()
    release = threading.Event()

    class Slow:
        on_event = None
        toolbox = None

        def set_ask_handler(self, handler):
            pass

        def cancel(self):
            release.set()

        def ask(self, message, stream=True):
            running.set()
            release.wait(timeout=10)
            self.on_event("done", {"tool_calls": 0, "hit_limit": False})
            return SimpleNamespace(answer="")

    slow = Slow()
    slow.toolbox = slow
    monkeypatch.setattr(session, "agent", lambda: slow)
    monkeypatch.setattr(session, "_agent", slow)

    first = threading.Thread(
        target=lambda: client("POST", "/api/chat", {"message": "erste"}), daemon=True
    )
    first.start()
    assert running.wait(timeout=5)

    result: list[bytes] = []
    second = threading.Thread(
        target=lambda: result.append(client("POST", "/api/chat", {"message": "zweite"})[1]),
        daemon=True,
    )
    second.start()
    time.sleep(0.4)
    release.set()
    second.join(timeout=10)
    first.join(timeout=10)

    waiting = [event for event in sse_events(result[0]) if event["type"] == "waiting"]
    assert waiting, "wer wartet, soll das sehen"
    assert "anderes Geraet" not in waiting[0]["reason"]
    assert "frueher gestellte Anfrage" in waiting[0]["reason"]


# ---------------------------------------------------------------------------
# Letzte Chats: umbenennen, loeschen, sofort sichtbar
# ---------------------------------------------------------------------------
def test_a_chat_can_be_renamed(client, session: web.ChatSession) -> None:
    cache = Cache(session.settings().db_path, 24)
    cache.add_history(session_id="s1", question="Wo liegt das Kabel?", answer="…", meta={})

    status, body = client(
        "POST", "/api/chat-edit", {"action": "rename", "session_id": "s1", "title": "Werkzeug"}
    )
    assert status == 200 and json.loads(body)["title"] == "Werkzeug"

    _, listing = client("GET", "/api/chats")
    assert json.loads(listing)["chats"][0]["title"] == "Werkzeug"


def test_a_chat_can_be_deleted(client, session: web.ChatSession) -> None:
    cache = Cache(session.settings().db_path, 24)
    cache.add_history(session_id="s1", question="Weg damit", answer="…", meta={})
    cache.add_history(session_id="s2", question="Bleibt", answer="…", meta={})

    status, body = client("POST", "/api/chat-edit", {"action": "delete", "session_id": "s1"})
    assert status == 200 and json.loads(body)["removed"] == 1

    _, listing = client("GET", "/api/chats")
    assert [chat["title"] for chat in json.loads(listing)["chats"]] == ["Bleibt"]


def test_deleting_the_open_chat_starts_a_fresh_one(client, session: web.ChatSession) -> None:
    """Sonst schriebe die naechste Frage in einen Verlauf, den es nicht mehr gibt."""
    open_id = session.chat_id()
    cache = Cache(session.settings().db_path, 24)
    cache.add_history(session_id=open_id, question="Offen", answer="…", meta={})

    client("POST", "/api/chat-edit", {"action": "delete", "session_id": open_id})
    assert session.chat_id() != open_id


def test_an_edit_without_an_id_is_refused(client) -> None:
    _, body = client("POST", "/api/chat-edit", {"action": "delete"})
    assert json.loads(body)["ok"] is False


def test_an_unknown_edit_action_is_refused(client, session: web.ChatSession) -> None:
    _, body = client("POST", "/api/chat-edit", {"action": "verbrennen", "session_id": "s1"})
    data = json.loads(body)
    assert data["ok"] is False and "verbrennen" in data["error"]


def test_the_chat_list_carries_what_the_grouping_needs(
    client, session: web.ChatSession
) -> None:
    """Ohne Zeitstempel gaebe es kein "Heute" und kein "Gestern"."""
    cache = Cache(session.settings().db_path, 24)
    cache.add_history(session_id="s1", question="Frage", answer="…", meta={})
    _, body = client("GET", "/api/chats")
    chat = json.loads(body)["chats"][0]
    assert isinstance(chat["touched"], (int, float)) and chat["touched"] > 0


def test_the_sidebar_shows_a_chat_the_moment_it_starts() -> None:
    """Bei einer laengeren Recherche waere die Leiste sonst minutenlang leer."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert "function noteChat(" in html
    assert "noteChat(text.trim()" in html


def test_the_sidebar_groups_by_day() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    for label in ('"Heute"', '"Gestern"', '"Letzte 7 Tage"', '"Älter"'):
        assert label in html, label


def test_the_sidebar_offers_renaming_and_deleting() -> None:
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert "Umbenennen" in html and "Löschen" in html
    assert "function deleteChat(" in html and "function renameChat(" in html


def test_deleting_a_chat_asks_first() -> None:
    """Das laesst sich nicht rueckgaengig machen -- also nicht auf einen Klick."""
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert "confirm(" in html
    assert "nicht rückgängig" in html
