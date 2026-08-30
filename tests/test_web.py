"""Tests fuer die Weboberflaeche -- echter Server, gefaelschter Agent."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from scoutr import web
from scoutr.agent import AgentResult
from scoutr.config import Settings


class FakeToolbox:
    def __init__(self) -> None:
        self.on_event: Any = None


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
    assert values["SCOUTR_MODEL"] == "openai/gpt-4o"
    assert values["SCOUTR_LOCATION"] == "Bremen"
    assert values["SCOUTR_SUBAGENTS_AUTO"] == "false"


def test_current_values_are_all_strings(session: web.ChatSession) -> None:
    # Das Formular fuellt nur Text -- Zahlen muessen konvertiert ankommen.
    assert all(isinstance(value, str) for value in web.current_values().values())


def test_save_values_writes_env_and_reloads(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    written = web.save_values(
        {"SCOUTR_MODEL": "anthropic/claude-sonnet-4", "SCOUTR_LOCATION": "Hamburg"}
    )
    assert written == target
    text = target.read_text()
    assert "SCOUTR_MODEL=anthropic/claude-sonnet-4" in text
    assert "SCOUTR_LOCATION=Hamburg" in text
    # reload() wirft Agent und Einstellungen weg, damit die neuen greifen.
    assert session._settings is None
    assert session._agent is None


def test_api_key_is_stored_under_the_provider_name(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    web.save_values(
        {"SCOUTR_MODEL": "anthropic/claude-sonnet-4", web.API_KEY_FIELD: "sk-ant-neu"}
    )
    assert "ANTHROPIC_API_KEY=sk-ant-neu" in target.read_text()


def test_empty_api_key_never_deletes_the_stored_one(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    target.write_text("OPENAI_API_KEY=sk-alt\n")
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    web.save_values({"SCOUTR_MODEL": "openai/gpt-4o", web.API_KEY_FIELD: "   "})
    assert "OPENAI_API_KEY=sk-alt" in target.read_text()


def test_unknown_keys_are_ignored(
    session: web.ChatSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    monkeypatch.setattr(web, "find_env_file", lambda: target)
    web.save_values({"SCOUTR_MODEL": "openai/gpt-4o", "PATH": "/boese"})
    assert "PATH=/boese" not in target.read_text()


# -- Endpunkte ------------------------------------------------------------
def test_index_serves_the_ui(client) -> None:
    status, body = client("GET", "/")
    assert status == 200
    assert b"scoutr" in body
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
    status, body = client("POST", "/api/config", {"SCOUTR_LOCATION": "Kiel"})
    assert status == 200
    assert json.loads(body)["ok"] is True
    assert "SCOUTR_LOCATION=Kiel" in target.read_text()


def test_config_post_reports_failures(
    client, session: web.ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(values, path=None):
        raise OSError("Platte voll")

    monkeypatch.setattr(web, "write_env_file", boom)
    status, body = client("POST", "/api/config", {"SCOUTR_LOCATION": "Kiel"})
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
    assert web.current_values()["SCOUTR_LOCATION"] == "Kiel"


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
    from scoutr.config import reset_settings_cache

    env = tmp_path / ".env"
    env.write_text("SCOUTR_MODEL=openai/gpt-4o\nSCOUTR_LOCATION=Bremen\n")
    monkeypatch.setattr("scoutr.config.ENV_CANDIDATES", (env,))
    monkeypatch.setenv("SCOUTR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("SCOUTR_LOCATION", raising=False)
    monkeypatch.delenv("SCOUTR_MODEL", raising=False)
    reset_settings_cache()

    fresh = web.ChatSession()
    monkeypatch.setattr(web, "SESSION", fresh)
    assert fresh.settings().location == "Bremen"

    web.save_values({"SCOUTR_MODEL": "openai/gpt-4o", "SCOUTR_LOCATION": "Hamburg"})
    assert fresh.settings().location == "Hamburg"
    reset_settings_cache()


# -- Netzbetrieb ----------------------------------------------------------
@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> str:
    """Setzt ein Zugangswort, wie es `scoutr web --lan` tut."""
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
    assert raw_request(port, "GET", "/api/config", {"X-Scoutr-Token": guarded})[0] == 200


def test_a_wrong_token_is_refused(port: int, guarded: str) -> None:
    assert raw_request(port, "GET", f"/?token={guarded}x")[0] == 401
    assert raw_request(port, "GET", "/api/config", {"X-Scoutr-Token": "falsch"})[0] == 401
    assert raw_request(port, "POST", "/api/chat", {"X-Scoutr-Token": ""})[0] == 401


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
    assert 'X-Scoutr-Token' in html


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
    assert raw_request(port, "GET", "/", {"X-Scoutr-Token": "falsch"})[0] == 401
    assert raw_request(port, "GET", "/", {"X-Scoutr-Token": "grün"})[0] == 200


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
