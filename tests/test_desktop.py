"""Tests fuer den User mode (aquaticy/desktop.py) -- ohne Behaelter, ohne Bildmodell.

Die Werkstatt ist hier eine Attrappe, die sich jeden Aufruf merkt, und das
Bildmodell antwortet, was der Test ihm vorgibt. Geprueft wird, was zaehlt:
was Aquaticy tut, wenn das Bildmodell "Absenden", "Captcha" oder "Passwort"
sagt -- und dass bei einer Absage wirklich nichts geklickt oder getippt wird.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aquaticy import desktop as desk
from aquaticy.config import Settings
from aquaticy.desktop import Desktop, kind_of, parse_object, payment_data


class Box:
    """Die Werkstatt als Attrappe: merkt sich Bildschirmfotos und Handgriffe."""

    def __init__(self) -> None:
        self.aufrufe: list[tuple[tuple[str, ...], bytes | None]] = []
        self.fotos: list[dict[str, Any]] = []

    def screenshot(self, *, grid: bool = False, mark: tuple[int, int] | None = None) -> bytes:
        self.fotos.append({"grid": grid, "mark": mark})
        return b"\xff\xd8JPEG" + (b"raster" if grid else b"")

    def desktop(self, *args: str, stdin: bytes | None = None, timeout: float = 60):
        self.aufrufe.append((args, stdin))
        antwort: dict[str, Any] = {"ok": True, "fenster": "Testfenster"}
        if args[0] == "windows":
            antwort = {"fenster": [{"id": "0x1", "klasse": "Falkon", "titel": "T"}],
                       "aktiv": "T"}
        if args[0] == "open":
            antwort = {"ok": True, "fenster": "Neu", "neu": [{"id": "0x2", "titel": "Neu"}]}
        return subprocess.CompletedProcess(args, 0, json.dumps(antwort).encode(), b"")

    def befehle(self) -> list[str]:
        return [args[0] for args, _ in self.aufrufe]


class Auge:
    """Das Bildmodell als Attrappe: antwortet der Reihe nach."""

    def __init__(self, *antworten: str) -> None:
        self.antworten = list(antworten)
        self.gesehen: list[tuple[bytes, str]] = []

    def __call__(self, bild: bytes, prompt: str) -> str:
        self.gesehen.append((bild, prompt))
        return self.antworten.pop(0) if self.antworten else ""


def _desktop(*antworten: str, ask: Any = None) -> tuple[Desktop, Box, Auge, list]:
    box, auge, ereignisse = Box(), Auge(*antworten), []
    gefragt = Desktop(
        box, Settings(), ask=ask, vision=auge,
        emit=lambda event, **payload: ereignisse.append((event, payload)),
    )
    return gefragt, box, auge, ereignisse


def _json(**werte: Any) -> str:
    return json.dumps(werte)


# -- Klicken ---------------------------------------------------------------
def test_a_harmless_click_goes_where_the_image_model_points() -> None:
    d, box, auge, ereignisse = _desktop(
        _json(gefunden=True, x=412, y=250, was="Link", art="harmlos")
    )
    antwort = d.click(target="Link Impressum")
    assert antwort["geklickt"] == [412, 250]
    assert box.aufrufe == [(("click", "412", "250", "--button", "1"), None)]
    assert box.fotos == [{"grid": True, "mark": None}], "gesucht wird auf dem Raster"
    assert "<<<Link Impressum>>>" in auge.gesehen[0][1]
    assert ereignisse[-1][0] == "desktop" and ereignisse[-1][1]["action"] == "klickt"


@pytest.mark.parametrize(
    ("art", "code"),
    [("captcha", "captcha"), ("anmelden", "login"), ("alle_akzeptieren", "consent"),
     ("Alle akzeptieren", "consent")],
)
def test_these_are_never_clicked(art: str, code: str) -> None:
    gefragt: list[str] = []
    d, box, _, _ = _desktop(
        _json(gefunden=True, x=10, y=10, was="Knopf", art=art),
        ask=lambda frage, optionen: gefragt.append(frage) or "ja",
    )
    antwort = d.click(target="der Knopf")
    assert antwort["skipped_reason"] == code
    assert "click" not in box.befehle(), "nichts angeklickt"
    assert gefragt == [], "und nicht einmal gefragt -- das geht auch mit Zustimmung nicht"


@pytest.mark.parametrize("art", ["senden", "kaufen", "loeschen"])
def test_sending_buying_deleting_need_a_yes(art: str) -> None:
    gefragt: list[str] = []
    d, _box, _, _ = _desktop(
        _json(gefunden=True, x=100, y=200, was="Absenden", art=art),
        ask=lambda frage, optionen: gefragt.append(frage) or "ja",
    )
    assert d.click(target="Absenden")["geklickt"] == [100, 200]
    assert gefragt and "Darf ich in der Werkstatt" in gefragt[0]


def test_without_a_yes_nothing_is_sent() -> None:
    d, box, _, _ = _desktop(
        _json(gefunden=True, x=100, y=200, was="Absenden", art="senden"),
        ask=lambda frage, optionen: "nein",
    )
    antwort = d.click(target="Absenden")
    assert antwort["done"] is False and "click" not in box.befehle()


def test_without_anyone_to_ask_nothing_is_sent() -> None:
    d, box, _, _ = _desktop(_json(gefunden=True, x=100, y=200, was="Kaufen", art="kaufen"))
    antwort = d.click(target="Jetzt kaufen")
    assert antwort["skipped_reason"] == "needs_confirmation"
    assert "click" not in box.befehle()


def test_a_click_by_position_is_checked_with_a_mark() -> None:
    d, box, auge, _ = _desktop(_json(was="Menue", art="harmlos"))
    assert d.click(x=640, y=400)["geklickt"] == [640, 400]
    assert box.fotos == [{"grid": False, "mark": (640, 400)}]
    assert "rote Kreis" in auge.gesehen[0][1]


def test_an_unclear_answer_counts_as_unclear_and_asks() -> None:
    gefragt: list[str] = []
    d, box, _, _ = _desktop("das ist kein JSON",
                            ask=lambda frage, optionen: gefragt.append(frage) or "nein")
    antwort = d.click(x=5, y=5)
    assert gefragt and "nicht eindeutig" in gefragt[0]
    assert antwort["done"] is False and "click" not in box.befehle()


@pytest.mark.parametrize(
    ("x", "y"),
    [(None, None), (-1, 5), (1280, 5), (5, 800), ("nan", 5), ("inf", 5), ("abc", 5)],
)
def test_a_position_off_the_screen_is_refused_before_looking(x: Any, y: Any) -> None:
    d, box, auge, _ = _desktop()
    assert "error" in d.click(x=x, y=y)
    assert auge.gesehen == [] and box.aufrufe == []


def test_something_not_found_is_not_clicked() -> None:
    d, box, _, _ = _desktop(_json(gefunden=False))
    assert "nicht zu finden" in d.click(target="Gibt es nicht")["error"]
    assert box.aufrufe == []


def test_a_found_spot_off_the_screen_is_not_clicked() -> None:
    d, box, _, _ = _desktop(_json(gefunden=True, x=5000, y=10, art="harmlos"))
    assert "error" in d.click(target="irgendwas")
    assert box.aufrufe == []


# -- Tippen ----------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    ["4111 1111 1111 1111", "IBAN: DE89 3704 0044 0532 0130 00", "5500-0000-0000-0004"],
)
def test_payment_data_is_never_typed_and_no_model_sees_it(text: str) -> None:
    d, box, auge, _ = _desktop()
    antwort = d.type(text)
    assert antwort["skipped_reason"] == "payment"
    assert auge.gesehen == [] and box.aufrufe == []


def test_a_password_field_gets_nothing() -> None:
    d, box, _, _ = _desktop(_json(feld="passwort", art="harmlos"))
    assert d.type("geheim123")["skipped_reason"] == "login"
    assert "type" not in box.befehle()


def test_a_payment_field_gets_nothing() -> None:
    d, _box, _, _ = _desktop(_json(feld="zahlung", art="harmlos"))
    assert d.type("123")["skipped_reason"] == "payment"


def test_a_captcha_field_gets_nothing() -> None:
    d, _box, _, _ = _desktop(_json(feld="sonstiges", art="captcha"))
    assert d.type("xk7q")["skipped_reason"] == "captcha"


def test_text_goes_in_through_stdin_as_utf8() -> None:
    d, box, _, ereignisse = _desktop(_json(feld="suche", art="harmlos"))
    antwort = d.type("Grüße aus Köln")
    assert antwort["getippt"] == len("Grüße aus Köln")
    assert box.aufrufe[-1] == (("type",), "Grüße aus Köln".encode())
    assert ereignisse[-1][1]["action"] == "tippt"


def test_a_line_break_would_send_a_form_so_it_is_refused_there() -> None:
    d, box, _, _ = _desktop(_json(feld="formular", art="harmlos"))
    assert "desktop_key('Return')" in d.type("Hallo\nWelt")["error"]
    assert "type" not in box.befehle()


def test_line_breaks_are_fine_in_a_document() -> None:
    d, _box, _, _ = _desktop(_json(feld="dokument", art="harmlos"))
    assert d.type("Zeile 1\nZeile 2")["getippt"] == 15


def test_emoji_are_refused_instead_of_typed_wrong() -> None:
    d, box, auge, _ = _desktop()
    assert "nicht eintippen" in d.type("Hallo 😀")["error"]
    assert auge.gesehen == [] and box.aufrufe == []


def test_an_unknown_field_asks_first() -> None:
    gefragt: list[str] = []
    d, _box, _, _ = _desktop("???", ask=lambda frage, optionen: gefragt.append(frage) or "ja")
    assert d.type("hallo")["getippt"] == 5
    assert gefragt


def test_too_much_text_at_once_is_refused() -> None:
    d, _box, _, _ = _desktop()
    assert "vm_write" in d.type("x" * (desk.MAX_TYPE_CHARS + 1))["error"]


# -- Tasten ----------------------------------------------------------------
def test_plain_keys_need_no_look() -> None:
    d, box, auge, _ = _desktop()
    assert d.key("ctrl+l Tab")["gedrueckt"] == ["ctrl+l", "Tab"]
    assert auge.gesehen == []
    # ctrl+l ist kein Blaettern -- also wird einmal nachgesehen, ob vorn ein
    # Messenger mit "Nur lesen" ist (9.5.10). Hier ist es keiner.
    assert box.aufrufe == [(("windows",), None), (("key", "ctrl+l", "Tab"), None)]


@pytest.mark.parametrize("taste", ["Return", "KP_Enter", "space", "shift+Return"])
def test_keys_that_can_submit_are_checked_first(taste: str) -> None:
    d, box, auge, _ = _desktop(_json(was="schickt das Formular ab", art="senden"))
    antwort = d.key(taste)
    assert auge.gesehen, "vorher hingesehen"
    assert antwort["skipped_reason"] == "needs_confirmation"
    assert "key" not in box.befehle()


@pytest.mark.parametrize("keys", ["", "ctrl+l; rm -rf /", "a b c d e f g h i j k", 5])
def test_nonsense_keys_are_refused(keys: Any) -> None:
    d, box, _, _ = _desktop()
    assert "error" in d.key(keys)
    assert box.aufrufe == []


def test_keys_also_come_as_a_list() -> None:
    d, _box, _, _ = _desktop()
    assert d.key(["ctrl+a", "Delete"])["gedrueckt"] == ["ctrl+a", "Delete"]


# -- Scrollen, Oeffnen, Fenster ---------------------------------------------
def test_scrolling_is_clamped_and_centered() -> None:
    d, box, _, _ = _desktop()
    assert d.scroll("runter", 99)["schritte"] == 30
    assert box.aufrufe[-1][0] == ("scroll", "640", "400", "down", "30")
    assert "error" in d.scroll("quer")
    assert "error" in d.scroll("down", 3, x=9999, y=1)


def test_the_browser_opens_only_web_addresses() -> None:
    d, box, _, _ = _desktop()
    for falsch in ("file:///etc/passwd", "javascript:alert(1)", "-x", "ftp://a.b"):
        assert "error" in d.open("browser", falsch), falsch
    assert box.aufrufe == []
    d.open("browser", "https://example.org/")
    assert box.aufrufe[-1][0] == ("open", "browser", "https://example.org/")


def test_files_are_opened_only_below_work() -> None:
    d, box, _, _ = _desktop()
    assert "error" in d.open("writer", "/etc/passwd")
    assert "error" in d.open("writer", "../../etc/passwd")
    d.open("writer", "brief.odt")
    assert box.aufrufe[-1][0] == ("open", "writer", "/work/brief.odt")


def test_only_known_programs_open() -> None:
    d, _box, _, _ = _desktop()
    assert "error" in d.open("rm -rf /")
    assert d.open("Calc")["geoeffnet"].startswith("Tabellenkalkulation")


def test_windows_come_back_without_a_picture() -> None:
    d, box, auge, _ = _desktop()
    assert d.windows()["aktiv"] == "T"
    assert auge.gesehen == [] and box.fotos == []
    assert "error" in d.focus("kein fenster")
    d.focus("0x0040001a")
    assert box.aufrufe[-1][0] == ("focus", "0x0040001a")


def test_look_describes_and_hands_over_the_picture() -> None:
    d, _box, auge, _ = _desktop("Ein Browser mit der Startseite.")
    antwort = d.look("Ist die Seite geladen?")
    assert antwort["beschreibung"] == "Ein Browser mit der Startseite."
    assert antwort["_bild"].startswith(b"\xff\xd8") and b"raster" not in antwort["_bild"]
    assert auge.gesehen[0][0].endswith(b"raster"), "das Modell sieht das Raster"
    assert "Ist die Seite geladen?" in auge.gesehen[0][1]
    assert antwort["fenster"][0]["klasse"] == "Falkon"


def test_without_an_image_model_the_desktop_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(model="mistral/mistral-large-latest", vision_model="")
    d = Desktop(Box(), settings)
    with pytest.raises(desk.DesktopError, match="Vision-Modell"):
        d.look()


# -- Bausteine ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "erwartet"),
    [
        ("4111 1111 1111 1111", True),
        ("Karte: 4111-1111-1111-1111, gültig bis 12/28", True),
        ("Bestellung 1234 4111 1111 1111 1111", True),
        ("3782 822463 10005", True),
        ("de89370400440532013000", True),
        ("IBAN DE89 3704 0044 0532 0130 00 BITTE", True),
        ("GB82 WEST 1234 5698 7654 32", True),
        ("+49 170 1234567", False),
        ("Bestellnummer 4111111111111112", False),
        ("Datum 2026-09-23 12:00", False),
        ("PLZ 41061 Mönchengladbach", False),
        ("DE89 3704 0044 0532 0130 01", False),
        ("1234567890123456789012345678901234", False),
        ("", False),
    ],
)
def test_payment_data_is_recognised(text: str, erwartet: bool) -> None:
    assert payment_data(text) is erwartet


@pytest.mark.parametrize(
    ("roh", "art"),
    [("harmlos", "harmlos"), ("Senden", "senden"), ("löschen", "loeschen"),
     ("alle akzeptieren", "alle_akzeptieren"), ("alle-akzeptieren", "alle_akzeptieren"),
     ("", "unklar"), (None, "unklar"), ("irgendwas", "unklar")],
)
def test_kinds_are_normalised(roh: Any, art: str) -> None:
    assert kind_of(roh) == art


def test_the_first_json_object_is_read() -> None:
    assert parse_object('Klar: {"x": 1, "y": 2} fertig') == {"x": 1, "y": 2}
    assert parse_object("[1, 2]") is None
    assert parse_object("") is None


# -- Im Werkzeugkasten --------------------------------------------------------
def _box_mit_desktop(monkeypatch: pytest.MonkeyPatch, settings: Settings, *antworten: str):
    from aquaticy.tools import Toolbox

    settings.vm_user_mode = True
    werkzeuge = Toolbox(settings, cache=None)
    box = Box()
    monkeypatch.setattr(werkzeuge, "_sandbox", lambda: box)
    werkzeuge.screen_reader = Auge(*antworten)
    return werkzeuge, box


def test_without_user_mode_the_desktop_tools_refuse(settings: Settings) -> None:
    from aquaticy.tools import Toolbox

    werkzeuge = Toolbox(settings, cache=None)
    assert "User mode ist aus" in werkzeuge.call("desktop_look", {})["error"]


def test_the_picture_lands_in_the_chat_not_in_the_model(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    werkzeuge, _ = _box_mit_desktop(monkeypatch, settings, "Ein leerer Desktop.")
    ereignisse: list[tuple[str, dict[str, Any]]] = []
    werkzeuge.on_event = lambda event, payload: ereignisse.append((event, payload))
    antwort = werkzeuge.call("desktop_look", {"question": "Was ist offen?"})
    assert "_bild" not in antwort
    json.dumps(antwort)  # geht so an das Modell
    visual = werkzeuge.stats.visuals[-1]
    assert visual["kind"] == "desktop" and visual["media_id"].endswith(".jpg")
    assert (Path(settings.data_dir) / "media" / visual["media_id"]).exists() or any(
        visual["media_id"] in str(pfad) for pfad in Path(settings.data_dir).rglob("*")
    )
    assert any(e == "desktop" and p.get("media_id") == visual["media_id"] for e, p in ereignisse)


def test_the_legal_guard_reads_what_is_typed(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from aquaticy import guardrails

    monkeypatch.setattr(
        guardrails, "_ask_model",
        lambda prompt, model, s: '{"zulaessig": false, "regel": "name", "grund": "x"}',
    )
    werkzeuge, box = _box_mit_desktop(monkeypatch, settings)
    antwort = werkzeuge.call("desktop_type", {"text": "Mit freundlichen Gruessen, Ihr Chef"})
    assert antwort["skipped_reason"] == "legal_guard"
    assert box.aufrufe == [], "nichts getippt"


def test_opening_an_empty_program_needs_no_legal_check(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from aquaticy import guardrails

    gefragt: list[str] = []
    monkeypatch.setattr(
        guardrails, "_ask_model",
        lambda prompt, model, s: gefragt.append(prompt) or '{"zulaessig": true, "regel": ""}',
    )
    werkzeuge, _ = _box_mit_desktop(monkeypatch, settings)
    werkzeuge.call("desktop_open", {"app": "writer"})
    assert gefragt == []
    werkzeuge.call("desktop_open", {"app": "browser", "target": "https://example.org/"})
    assert len(gefragt) == 1


def test_the_tools_exist_only_in_user_mode(settings: Settings) -> None:
    from aquaticy.tools import vm_schemas_for

    ohne = [s["function"]["name"] for s in vm_schemas_for(settings)]
    assert not [name for name in ohne if name.startswith("desktop_")]
    run = next(s for s in vm_schemas_for(settings) if s["function"]["name"] == "vm_run")
    assert "KEIN Netz" in run["function"]["description"]
    settings.vm_user_mode = True
    mit = [s["function"]["name"] for s in vm_schemas_for(settings)]
    assert {"desktop_look", "desktop_click", "desktop_type", "desktop_key", "desktop_scroll",
            "desktop_open", "desktop_windows"} <= set(mit)
    run = next(s for s in vm_schemas_for(settings) if s["function"]["name"] == "vm_run")
    assert "Internet, aber kein Heimnetz" in run["function"]["description"]


# -- Im Agenten ---------------------------------------------------------------
def _antwort(content: str = "", tool_calls: list[Any] | None = None):
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def _rechnen(nummer: int):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=f"c{nummer}", type="function", index=0,
        function=SimpleNamespace(name="calculate", arguments='{"expression": "1+1"}'),
    )


def test_user_mode_gets_its_prompt_and_a_bigger_budget(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from aquaticy.agent import Agent

    settings.vm_user_mode = True
    settings.max_tool_calls = 1
    runden = [_antwort(tool_calls=[_rechnen(i)]) for i in range(3)] + [_antwort("fertig")]
    monkeypatch.setattr("litellm.completion", lambda **kwargs: runden.pop(0))
    agent = Agent(settings, cache=None)
    result = agent.ask("Bau mir etwas", stream=False, mode="code", sandbox=True)
    assert "USER MODE" in agent.messages[0]["content"]
    assert "Internet, aber kein Heimnetz" in agent.messages[0]["content"]
    assert not result.hit_limit and result.answer == "fertig", "drei Handgriffe gingen durch"


def test_without_user_mode_the_workshop_stays_offline(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from aquaticy.agent import Agent

    monkeypatch.setattr("litellm.completion", lambda **kwargs: _antwort("fertig"))
    agent = Agent(settings, cache=None)
    agent.ask("Schreib ein Skript", stream=False, mode="code", sandbox=True)
    assert "USER MODE" not in agent.messages[0]["content"]
    assert "Was sie NICHT hat: Netz" in agent.messages[0]["content"]


# -- Einstellungen --------------------------------------------------------------
def test_the_chat_cannot_switch_user_mode_on(settings: Settings) -> None:
    from aquaticy import preferences
    from aquaticy.tools import Toolbox

    werkzeuge = Toolbox(settings, cache=None)
    for name in ("vm_user_mode", "user mode", "usermode", "desktop", "vm_desktop_image"):
        assert "error" in werkzeuge.change_setting(name, "an"), name
    assert "AQUATICY_VM_USER_MODE" in preferences.PROTECTED


def test_the_form_carries_the_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy import web

    frisch = web.ChatSession()
    frisch._settings = Settings(data_dir=tmp_path / "d", env_path=tmp_path / ".env")
    monkeypatch.setattr(web, "SESSION", frisch)
    monkeypatch.setattr(web, "find_env_file", lambda: tmp_path / ".env")
    assert web.current_values()["AQUATICY_VM_USER_MODE"] == "false"
    web.save_values({"AQUATICY_VM_USER_MODE": "true"})
    assert "AQUATICY_VM_USER_MODE=true" in (tmp_path / ".env").read_text()
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert 'name="AQUATICY_VM_USER_MODE"' in html


def test_an_account_reads_its_switch(tmp_path: Path) -> None:
    from aquaticy import web

    profil = tmp_path / "konto"
    profil.mkdir()
    (profil / ".env").write_text("AQUATICY_VM_USER_MODE=true\n", encoding="utf-8")
    assert web._profile_settings(profil, "ultra").vm_user_mode is True
    # Seit 9.5.17 gehoert der User mode zu Ultra -- ein "an" in der .env eines
    # normalen oder eines Pro-Kontos zaehlt nicht.
    assert web._profile_settings(profil, "pro").vm_user_mode is False
    assert web._profile_settings(profil, "normal").vm_user_mode is False


def test_a_normal_account_cannot_switch_the_user_mode_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import web

    konto = type("Konto", (), {"plan": "normal", "username": "n"})()
    profil = tmp_path / "konto"
    profil.mkdir()
    frisch = web.ChatSession(account=konto, profile=profil)
    monkeypatch.setattr(web, "SESSION", frisch)
    with pytest.raises(ValueError, match="Ultra"):
        web.save_values({"AQUATICY_VM_USER_MODE": "true"})
    assert not (profil / ".env").exists() or "USER_MODE=true" not in (
        profil / ".env").read_text()
    # Ausschalten geht immer.
    web.save_values({"AQUATICY_VM_USER_MODE": "false"})


# -- Der Bildschirm im Web --------------------------------------------------------
def _hole(pfad: str, session: Any) -> tuple[int, str, bytes]:
    import threading
    from http.client import HTTPConnection
    from http.server import ThreadingHTTPServer

    from aquaticy import web

    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    faden = threading.Thread(target=server.serve_forever, daemon=True)
    faden.start()
    try:
        verbindung = HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        verbindung.request("GET", pfad)
        antwort = verbindung.getresponse()
        return antwort.status, antwort.getheader("Content-Type") or "", antwort.read()
    finally:
        server.shutdown()
        server.server_close()


def test_the_screen_endpoint_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import sandbox as werkstatt
    from aquaticy import web

    frisch = web.ChatSession()
    frisch._settings = Settings(data_dir=tmp_path / "d", vm_user_mode=True)
    monkeypatch.setattr(web, "SESSION", frisch)
    gestartet: list[bool] = []

    class Ruhig:
        alive = False
        user_mode = True

        def screenshot(self, **kwargs: Any) -> bytes:
            gestartet.append(True)
            return b""

    monkeypatch.setattr(werkstatt, "shared", lambda settings: Ruhig())
    status, _, _ = _hole("/api/werkstatt/bildschirm", frisch)
    assert status == 404 and gestartet == [], "ein Blick faehrt keine Werkstatt hoch"


def test_the_screen_endpoint_shows_a_running_desktop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import sandbox as werkstatt
    from aquaticy import web

    frisch = web.ChatSession()
    frisch._settings = Settings(data_dir=tmp_path / "d", vm_user_mode=True)
    monkeypatch.setattr(web, "SESSION", frisch)

    class Laeuft:
        alive = True
        user_mode = True

        def screenshot(self, **kwargs: Any) -> bytes:
            assert kwargs.get("start") is False, "ein Blick von aussen startet nichts"
            return b"\xff\xd8JPEG-DATEN"

    monkeypatch.setattr(werkstatt, "shared", lambda settings: Laeuft())
    status, art, inhalt = _hole("/api/werkstatt/bildschirm", frisch)
    assert status == 200 and art == "image/jpeg" and inhalt.startswith(b"\xff\xd8")


def test_the_boxed_start_says_what_user_mode_needs(monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy import sandbox as werkstatt

    monkeypatch.setenv("AQUATICY_SANDBOXED", "1")
    echt = Path.exists
    monkeypatch.setattr(
        Path, "exists",
        lambda self: False if str(self) == "/dev/net/tun" else echt(self),
    )
    sandkasten = werkstatt.Sandbox(user_mode=True)
    sandkasten.runtime = werkstatt.Runtime("docker", "docker", "Docker (gehaertet)")
    with pytest.raises(werkstatt.SandboxUnavailable, match="/dev/net/tun"):
        sandkasten.ensure()
    # Ohne User mode braucht die Werkstatt kein Netz -- und also kein Geraet.
    ohne = werkstatt.Sandbox(user_mode=False)
    ohne._check_nested_network()
