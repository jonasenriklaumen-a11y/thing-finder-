"""Add-ons: installieren, schalten, anmelden -- und was das Modell davon sieht.

Keiner dieser Tests spricht mit GitHub, Open-Meteo oder einem Feed im Netz:
die Antworten kommen aus einem httpx.MockTransport, der gleichzeitig
mitschreibt, WAS gefragt wurde (nur GET? welcher Server?). Die Werkstatt ist
gestellt; der echte Behaelter kommt in test_addons_live.py dran.
"""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest

from aquaticy import addons
from aquaticy.config import Settings


@pytest.fixture
def konto(tmp_path: Path) -> Settings:
    ordner = tmp_path / "konto"
    ordner.mkdir()
    return Settings(data_dir=ordner, env_path=ordner / ".env")


def _pro(settings: Settings, user_mode: bool = True) -> Settings:
    settings.vm_user_mode = user_mode
    return settings


def _mock(antworten: dict[str, Any], gefragt: list[httpx.Request]) -> httpx.Client:
    def handler(anfrage: httpx.Request) -> httpx.Response:
        gefragt.append(anfrage)
        for anfang, antwort in antworten.items():
            if str(anfrage.url).startswith(anfang):
                if isinstance(antwort, httpx.Response):
                    return antwort
                return httpx.Response(200, json=antwort)
        return httpx.Response(404, json={"message": "Not Found"})

    return httpx.Client(transport=httpx.MockTransport(handler))


# -- Katalog ---------------------------------------------------------------------
def test_the_catalogue_has_what_was_asked_for_plus_suggestions() -> None:
    for gewuenscht in ("github", "whatsapp", "signal", "blender"):
        assert gewuenscht in addons.CATALOG
    vorschlaege = [a for a in addons.CATALOG.values() if a.vorschlag]
    assert len(vorschlaege) >= 2
    for addon in addons.CATALOG.values():
        assert addon.login in {"token", "qr", "keine", "feeds"}, addon.id
        assert addon.summary and addon.anmelden, addon.id
        if addon.programm == "webapp":
            assert addon.adresse.startswith("https://")
        if addon.login == "qr":
            assert addon.werkstatt, "QR-Anmeldungen laufen in der Werkstatt"


def test_signal_says_honestly_that_there_is_no_signal_web() -> None:
    assert "Signal Web" in addons.CATALOG["signal"].summary
    assert addons.CATALOG["signal"].programm == "signal"


# -- Installieren, Schalten, Deinstallieren ----------------------------------------
def test_every_addon_has_install_even_without_download(konto: Settings) -> None:
    addons.install(konto, "github", pro=False)
    eintrag = addons.load_state(konto)["github"]
    assert eintrag["installed"] and eintrag["enabled"]
    assert "github" in addons.active_ids(konto, pro=False)


def test_normal_accounts_get_no_workshop_addons(konto: Settings) -> None:
    for addon_id in ("whatsapp", "signal", "telegram", "blender"):
        with pytest.raises(addons.AddOnError, match="Ultra"):
            addons.install(konto, addon_id, pro=False)
    assert addons.load_state(konto) == {}


def test_workshop_addons_only_count_with_the_user_mode(konto: Settings) -> None:
    addons._update(konto, "whatsapp", installed=True, enabled=True)
    assert "whatsapp" not in addons.active_ids(_pro(konto, user_mode=False))
    assert "whatsapp" in addons.active_ids(_pro(konto))
    assert "whatsapp" not in addons.active_ids(_pro(konto), pro=False)


def test_switching_off_takes_the_volume_out_of_the_workshop(konto: Settings) -> None:
    _pro(konto)
    addons._update(konto, "whatsapp", installed=True, enabled=True)
    addons._update(konto, "blender", installed=True, enabled=True)
    addons._update(konto, addons.FIREFOX, installed=True)
    vorher = addons.mounts(konto)
    assert set(vorher.values()) == {"/addons/whatsapp", "/addons/blender", "/addons/_firefox"}
    assert all(name.startswith(addons.volume_prefix(konto)) for name in vorher)
    addons.set_enabled(konto, "whatsapp", False, pro=True)
    nachher = addons.mounts(konto)
    assert set(nachher.values()) == {"/addons/blender"}, "ohne Web-App auch kein Firefox"
    konto.vm_user_mode = False
    assert addons.mounts(konto) == {}


def test_accounts_never_share_volumes(tmp_path: Path) -> None:
    a = Settings(data_dir=tmp_path / "a")
    b = Settings(data_dir=tmp_path / "b")
    assert addons.volume_name(a, "whatsapp") != addons.volume_name(b, "whatsapp")
    from aquaticy.sandbox import ADDON_VOLUME_RE

    assert ADDON_VOLUME_RE.fullmatch(addons.volume_name(a, "_firefox"))


def test_switching_needs_an_install(konto: Settings) -> None:
    with pytest.raises(addons.AddOnError, match="nicht installiert"):
        addons.set_enabled(konto, "wetter", True, pro=True)


def test_uninstall_removes_state_token_and_volumes(
    konto: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import sandbox as werkstatt

    geloescht: list[str] = []
    monkeypatch.setattr(werkstatt, "find_runtime", lambda: SimpleNamespace(binary="docker"))
    monkeypatch.setattr(werkstatt, "remove_addon_volume",
                        lambda runtime, volume: geloescht.append(volume) or True)
    _pro(konto)
    addons._update(konto, "whatsapp", installed=True, enabled=True)
    addons._update(konto, addons.FIREFOX, installed=True)
    addons.uninstall(konto, "whatsapp")
    assert addons.volume_name(konto, "whatsapp") in geloescht
    firefox = addons.volume_name(konto, addons.FIREFOX)
    assert firefox in geloescht, "letzte Web-App nimmt Firefox mit"
    assert addons.load_state(konto) == {}

    addons.install(konto, "github", pro=True)
    addons._write_secret(konto, addons.GITHUB_TOKEN_KEY, "ghp_" + "a" * 36)
    addons.uninstall(konto, "github")
    assert f"{addons.GITHUB_TOKEN_KEY}=\n" in konto.env_path.read_text()
    assert addons.github_token(konto) == ""


def test_firefox_stays_while_another_web_app_needs_it(
    konto: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import sandbox as werkstatt

    geloescht: list[str] = []
    monkeypatch.setattr(werkstatt, "find_runtime", lambda: SimpleNamespace(binary="docker"))
    monkeypatch.setattr(werkstatt, "remove_addon_volume",
                        lambda runtime, volume: geloescht.append(volume) or True)
    for addon_id in ("whatsapp", "telegram"):
        addons._update(konto, addon_id, installed=True, enabled=True)
    addons.uninstall(konto, "whatsapp")
    assert addons.volume_name(konto, addons.FIREFOX) not in geloescht


def test_the_browser_view_carries_no_secret(konto: Settings) -> None:
    addons.install(konto, "github", pro=True)
    geheim = "ghp_" + "S" * 36
    addons._write_secret(konto, addons.GITHUB_TOKEN_KEY, geheim)
    ansicht = addons.public_view(konto, pro=True)
    assert geheim not in json.dumps(ansicht)
    github = next(a for a in ansicht["addons"] if a["id"] == "github")
    assert github["login"]["token_set"] is True


def test_the_state_file_ignores_what_it_does_not_know(konto: Settings) -> None:
    (konto.data_dir / addons.STATE_FILE).write_text(json.dumps(
        {"addons": {"boese": {"installed": True}, "github": "kaputt",
                    "wetter": {"installed": True, "enabled": True}}}))
    assert set(addons.load_state(konto)) == {"wetter"}
    (konto.data_dir / addons.STATE_FILE).write_text("{kein json")
    assert addons.load_state(konto) == {}


# -- Installation in der Wegwerf-Werkstatt (gestellt) ----------------------------
class FakeBox:
    """Nimmt die Aufrufe des Installers entgegen und antwortet wie aquaticy-addons."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.aufrufe: list[tuple[str, ...]] = []
        self.gestoppt = False
        self.runtime = None
        FakeBox.letzte = self

    antworten: ClassVar[dict[str, dict[str, Any]]] = {}
    letzte: ClassVar[FakeBox | None] = None

    def ensure(self) -> str:
        return "box"

    def addon_helper(self, *args: str, timeout: int = 0) -> subprocess.CompletedProcess[str]:
        self.aufrufe.append(args)
        antwort = FakeBox.antworten.get(args[0] + ":" + (args[1] if len(args) > 1 else ""),
                                        FakeBox.antworten.get(args[0], {"ok": True}))
        return subprocess.CompletedProcess(args, 0, "Fortschritt\n" + json.dumps(antwort), "")

    def stop(self, reason: str = "") -> None:
        self.gestoppt = True


@pytest.fixture
def fake_werkstatt(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from aquaticy import sandbox as werkstatt

    angelegt: list[str] = []
    monkeypatch.setattr(werkstatt, "find_runtime", lambda: SimpleNamespace(binary="docker"))
    monkeypatch.setattr(werkstatt, "ensure_addon_volume",
                        lambda runtime, volume, image: angelegt.append(volume))
    monkeypatch.setattr(werkstatt, "Sandbox", FakeBox)
    FakeBox.antworten = {}
    return angelegt


def test_a_web_app_loads_firefox_first_then_its_profile(
    konto: Settings, fake_werkstatt: list[str]
) -> None:
    FakeBox.antworten = {
        "status": {"ok": True, "installiert": False},
        "install:firefox": {"ok": True, "version": "140.3.0esr"},
        "webapp": {"ok": True},
    }
    fertig: list[bool] = []
    addons.install(_pro(konto), "whatsapp", pro=True, wait=True,
                   on_done=lambda: fertig.append(True))
    box = FakeBox.letzte
    assert box is not None and box.gestoppt, "die Wegwerf-Werkstatt ist wieder weg"
    assert box.kwargs["user_mode"] and box.kwargs["headless"]
    assert set(box.kwargs["addon_mounts"].values()) == {"/addons/whatsapp", "/addons/_firefox"}
    befehle = [a[:2] for a in box.aufrufe]
    assert befehle == [("status", "/addons/_firefox"), ("install", "firefox"),
                       ("webapp", "/addons/whatsapp")]
    webapp = box.aufrufe[-1]
    assert "https://web.whatsapp.com/" in webapp
    assert "KI-gesteuert" in webapp[webapp.index("--zusatz") + 1]
    zustand = addons.load_state(konto)
    assert zustand["whatsapp"]["installed"] and zustand["whatsapp"]["status"] == "bereit"
    assert zustand[addons.FIREFOX]["version"] == "140.3.0esr"
    assert fertig == [True], "danach wird der Agent neu gebaut"
    assert len(fake_werkstatt) == 2


def test_a_failed_install_says_why_and_counts_as_not_installed(
    konto: Settings, fake_werkstatt: list[str]
) -> None:
    FakeBox.antworten = {"install": {"ok": False, "fehler": "Pruefsumme stimmt nicht"}}
    addons.install(_pro(konto), "blender", pro=True, wait=True)
    eintrag = addons.load_state(konto)["blender"]
    assert eintrag["status"] == "fehler" and "Pruefsumme" in eintrag["message"]
    assert not eintrag["installed"]
    assert "blender" not in addons.active_ids(konto)
    assert FakeBox.letzte is not None and FakeBox.letzte.gestoppt


def test_no_runtime_means_no_install(konto: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy import sandbox as werkstatt

    monkeypatch.setattr(werkstatt, "find_runtime", lambda: None)
    addons.install(_pro(konto), "signal", pro=True, wait=True)
    eintrag = addons.load_state(konto)["signal"]
    assert eintrag["status"] == "fehler" and "Docker" in eintrag["message"]


# -- GitHub ------------------------------------------------------------------------
def test_github_login_checks_the_token_and_stores_it(konto: Settings) -> None:
    addons.install(konto, "github", pro=False)
    gefragt: list[httpx.Request] = []
    client = _mock({"https://api.github.com/user": httpx.Response(
        200, json={"login": "jonas"}, headers={"x-oauth-scopes": "repo, read:user"})}, gefragt)
    ergebnis = addons.github_login(konto, "ghp_" + "x" * 36, client=client)
    assert ergebnis["who"] == "jonas" and "schreiben" in ergebnis["warning"]
    assert gefragt[0].headers["authorization"] == "Bearer ghp_" + "x" * 36
    assert "aquaticy/" in gefragt[0].headers["user-agent"]
    assert addons.github_token(konto) == "ghp_" + "x" * 36
    assert "AQUATICY_GITHUB_TOKEN=ghp_" in konto.env_path.read_text()
    assert addons.load_state(konto)["github"]["login"]["who"] == "jonas"


def test_github_login_refuses_bad_tokens(konto: Settings) -> None:
    addons.install(konto, "github", pro=False)
    with pytest.raises(addons.AddOnError, match="Token"):
        addons.github_login(konto, "nicht ein token; rm -rf")
    client = _mock({"https://api.github.com/user": httpx.Response(401)}, [])
    with pytest.raises(addons.AddOnError, match="kennt"):
        addons.github_login(konto, "ghp_" + "y" * 36, client=client)
    assert addons.github_token(konto) == ""


def test_github_login_needs_the_install(konto: Settings) -> None:
    with pytest.raises(addons.AddOnError, match="installieren"):
        addons.github_login(konto, "ghp_" + "z" * 36)


def test_github_only_ever_reads(konto: Settings) -> None:
    gefragt: list[httpx.Request] = []
    datei = base64.b64encode(b"print('hallo')\n").decode()
    client = _mock({
        "https://api.github.com/repos/jonas/app/issues/7/comments": [{"user": {"login": "a"},
                                                                      "body": "ok"}],
        "https://api.github.com/repos/jonas/app/issues/7": {"number": 7, "title": "Fehler",
                                                             "body": "kaputt"},
        "https://api.github.com/repos/jonas/app/issues": [
            {"number": 1, "title": "Issue"}, {"number": 2, "title": "PR", "pull_request": {}}],
        "https://api.github.com/repos/jonas/app/contents/src/main.py": {
            "encoding": "base64", "size": 15, "content": datei},
    }, gefragt)
    token = "ghp_" + "t" * 36
    issues = addons.github_call(token, "issues", repo="jonas/app", client=client)
    assert [i["nummer"] for i in issues["issues"]] == [1], "Pull Requests sind keine Issues"
    issue = addons.github_call(token, "issue", repo="jonas/app", number="7", client=client)
    assert issue["titel"] == "Fehler" and issue["kommentare"][0]["text"] == "ok"
    inhalt = addons.github_call(token, "datei", repo="jonas/app", path="src/main.py",
                                client=client)
    assert inhalt["text"] == "print('hallo')\n"
    assert {anfrage.method for anfrage in gefragt} == {"GET"}
    assert {anfrage.url.host for anfrage in gefragt} == {"api.github.com"}


@pytest.mark.parametrize(
    ("args", "fehler"),
    [
        ({"action": "loeschen"}, "Unbekannte Aktion"),
        ({"action": "repo", "repo": "nur-ein-teil"}, "besitzer/name"),
        ({"action": "repo", "repo": "a/b/../c"}, "besitzer/name"),
        ({"action": "issue", "repo": "a/b", "number": "sieben"}, "ganze Zahl"),
        ({"action": "issue", "repo": "a/b", "number": -1}, "ganze Zahl"),
        ({"action": "datei", "repo": "a/b", "path": "../../etc/passwd"}, "Pfad"),
        ({"action": "datei", "repo": "a/b", "path": "x", "ref": "main;rm"}, "Branch"),
    ],
)
def test_github_arguments_are_checked_before_asking(args: dict[str, Any], fehler: str) -> None:
    gefragt: list[httpx.Request] = []
    aktion = args.pop("action")
    antwort = addons.github_call("ghp_" + "t" * 36, aktion, client=_mock({}, gefragt), **args)
    assert fehler in antwort["error"]
    assert gefragt == []


def test_github_without_token_asks_nothing() -> None:
    assert "nicht angemeldet" in addons.github_call("", "repos")["error"]


# -- Wetter ------------------------------------------------------------------------
def test_weather_finds_the_place_and_translates_the_codes() -> None:
    gefragt: list[httpx.Request] = []
    client = _mock({
        addons.OPEN_METEO_GEO: {"results": [
            {"name": "Paris", "country": "Vereinigte Staaten", "admin1": "Texas",
             "latitude": 33.6, "longitude": -95.5},
            {"name": "Paris", "country": "Frankreich", "admin1": "Île-de-France",
             "latitude": 48.85, "longitude": 2.35}]},
        addons.OPEN_METEO: {
            "current": {"temperature_2m": 18.2, "weather_code": 61, "wind_speed_10m": 12},
            "daily": {"time": ["2026-09-23", "2026-09-24"], "weather_code": [3, 95],
                      "temperature_2m_max": [19, 21], "temperature_2m_min": [11, 12]}},
    }, gefragt)
    wetter = addons.weather("Paris, Frankreich", days=2, client=client)
    assert wetter["ort"].endswith("Frankreich")
    assert wetter["jetzt"]["wetter"] == "leichter Regen"
    assert [t["wetter"] for t in wetter["tage"]] == ["bedeckt", "Gewitter"]
    vorhersage = gefragt[-1]
    assert vorhersage.url.params["latitude"] == "48.85"
    assert vorhersage.url.params["forecast_days"] == "2"
    assert "Open-Meteo" in wetter["quelle"]


def test_weather_without_a_place_asks_nothing() -> None:
    gefragt: list[httpx.Request] = []
    assert "Ort" in addons.weather("  ", client=_mock({}, gefragt))["error"]
    assert gefragt == []


def test_weather_clamps_the_days() -> None:
    gefragt: list[httpx.Request] = []
    client = _mock({addons.OPEN_METEO_GEO: {"results": [
        {"name": "Bremen", "latitude": 53, "longitude": 8.8}]}, addons.OPEN_METEO: {}}, gefragt)
    addons.weather("Bremen", days=99, client=client)
    assert gefragt[-1].url.params["forecast_days"] == "7"


# -- RSS-Feeds ---------------------------------------------------------------------
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Alt</title><link>https://n.example/alt</link>
<pubDate>Mon, 21 Sep 2026 08:00:00 +0200</pubDate>
<description>&lt;p&gt;Wahl&lt;/p&gt;</description></item>
<item><title>Neu</title><link>/neu</link><pubDate>Tue, 22 Sep 2026 08:00:00 +0200</pubDate>
<description>Wetter</description></item></channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title>Atom-Eintrag</title><link href="https://a.example/1"/>
<updated>2026-09-23T06:00:00Z</updated><summary>Bahn</summary></entry></feed>"""


def test_feeds_are_parsed_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy import fetch

    monkeypatch.setattr(fetch, "public_web_url", lambda url: True)
    gefragt: list[httpx.Request] = []
    client = _mock({
        "https://n.example/robots.txt": httpx.Response(404),
        "https://a.example/robots.txt": httpx.Response(404),
        "https://n.example/feed": httpx.Response(200, content=RSS),
        "https://a.example/atom": httpx.Response(200, content=ATOM),
    }, gefragt)
    ergebnis = addons.read_feeds(["https://n.example/feed", "https://a.example/atom"],
                                 client=client)
    assert [e["titel"] for e in ergebnis["eintraege"]] == ["Atom-Eintrag", "Neu", "Alt"]
    assert ergebnis["eintraege"][1]["link"] == "https://n.example/neu"
    assert ergebnis["eintraege"][2]["zusammenfassung"] == "Wahl", "ohne HTML"
    gefiltert = addons.read_feeds(["https://n.example/feed"], query="wetter", client=client)
    assert [e["titel"] for e in gefiltert["eintraege"]] == ["Neu"]


def test_feeds_respect_robots_txt(monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy import fetch

    monkeypatch.setattr(fetch, "public_web_url", lambda url: True)
    gefragt: list[httpx.Request] = []
    client = _mock({
        "https://n.example/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /\n"),
        "https://n.example/feed": httpx.Response(200, content=RSS),
    }, gefragt)
    ergebnis = addons.read_feeds(["https://n.example/feed"], client=client)
    assert ergebnis["eintraege"] == [] and "robots" in ergebnis["probleme"][0]
    assert all(anfrage.url.path == "/robots.txt" for anfrage in gefragt)


def test_feeds_never_reach_into_the_home_network() -> None:
    gefragt: list[httpx.Request] = []
    ergebnis = addons.read_feeds(["http://192.168.1.10/feed", "http://localhost/x"],
                                 client=_mock({}, gefragt))
    assert len(ergebnis["probleme"]) == 2 and gefragt == []


def test_redirects_into_the_home_network_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy import fetch

    monkeypatch.setattr(fetch, "public_web_url", lambda url: "192.168." not in url)
    gefragt: list[httpx.Request] = []
    client = _mock({
        "https://n.example/robots.txt": httpx.Response(404),
        "https://n.example/feed": httpx.Response(302, headers={"location":
                                                               "http://192.168.1.1/admin"}),
    }, gefragt)
    ergebnis = addons.read_feeds(["https://n.example/feed"], client=client)
    assert ergebnis["probleme"] and not any(a.url.host == "192.168.1.1" for a in gefragt)


def test_feeds_with_entities_are_not_read() -> None:
    bombe = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss>&a;</rss>'
    with pytest.raises(ValueError):
        addons.parse_feed(bombe, "https://x.example/")


def test_the_feed_list_is_checked(konto: Settings) -> None:
    addons.install(konto, "feeds", pro=False)
    assert addons.set_feeds(konto, "https://a.example/rss\n\nhttps://a.example/rss\n") == [
        "https://a.example/rss"]
    for boese in ("http://192.168.0.1/rss", "http://fritz.box/rss", "ftp://x.example/rss",
                  "http://10.1.2.3/"):
        with pytest.raises(addons.AddOnError):
            addons.set_feeds(konto, [boese])
    with pytest.raises(addons.AddOnError, match="Höchstens"):
        addons.set_feeds(konto, [f"https://f{i}.example/rss" for i in range(21)])


# -- Was das Modell sieht -------------------------------------------------------------
def test_tools_exist_only_for_switched_on_addons(konto: Settings) -> None:
    from aquaticy.tools import addon_schemas_for

    def namen() -> list[str]:
        return [s["function"]["name"] for s in addon_schemas_for(konto)]

    assert namen() == []
    addons.install(konto, "github", pro=False)
    assert namen() == [], "ohne Anmeldung kein GitHub-Werkzeug"
    konto.github_token = "ghp_" + "a" * 36
    assert namen() == ["github"]
    addons.install(konto, "wetter", pro=False)
    addons.install(konto, "feeds", pro=False)
    assert namen() == ["github", "weather"], "ohne eingetragene Feeds kein Feed-Werkzeug"
    addons.set_feeds(konto, ["https://a.example/rss"])
    assert namen() == ["github", "weather", "read_feeds"]
    addons.set_enabled(konto, "github", False, pro=False)
    assert "github" not in namen()


def test_blender_in_the_user_mode_comes_from_the_addon(konto: Settings) -> None:
    from aquaticy.tools import vm_schemas_for

    def namen() -> list[str]:
        return [s["function"]["name"] for s in vm_schemas_for(konto)]

    assert "blender_run" in namen(), "ohne User mode bleibt es wie bisher"
    _pro(konto)
    assert "blender_run" not in namen()
    addons._update(konto, "blender", installed=True, enabled=True)
    assert "blender_run" in namen()
    oeffnen = next(s for s in vm_schemas_for(konto) if s["function"]["name"] == "desktop_open")
    assert "blender" in oeffnen["function"]["description"]
    assert "%(addons)s" not in oeffnen["function"]["description"]


def test_the_prompt_names_only_active_addons(konto: Settings) -> None:
    assert addons.prompt_for(konto) == ""
    addons.install(konto, "wetter", pro=False)
    text = addons.prompt_for(konto)
    assert "Wetter" in text and "nur nach ausdruecklichem" in text
    assert "WhatsApp Web" not in text.split("\n")[1]


def test_the_toolbox_refuses_switched_off_addons(konto: Settings) -> None:
    from aquaticy.tools import Toolbox

    box = Toolbox.__new__(Toolbox)
    box.settings = konto
    box.guard = None
    assert "nicht installiert" in box.call("weather", {"place": "Bremen"})["error"]
    assert "nicht installiert" in box.call("github", {"action": "repos"})["error"]


def test_the_desktop_opens_only_active_addon_apps(konto: Settings) -> None:
    from aquaticy.desktop import Desktop

    aufrufe: list[tuple] = []

    class Box:
        user_mode = True

        def desktop(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            aufrufe.append(args)
            return subprocess.CompletedProcess(args, 0, b'{"neu": []}', b"")

    _pro(konto)
    desktop = Desktop(Box(), konto, vision=lambda bild, prompt: "{}")
    assert "nicht installiert" in desktop.open("whatsapp")["error"]
    addons._update(konto, "whatsapp", installed=True, enabled=True)
    assert "ohne target" in desktop.open("whatsapp", "https://evil.example/")["error"]
    assert aufrufe == []
    desktop.open("whatsapp")
    assert aufrufe == [("open", "whatsapp")]


@pytest.mark.parametrize("name", ["whatsapp", "addons", "github", "blender", "login_apps",
                                  "add_ons"])
def test_the_chat_cannot_switch_addons(name: str) -> None:
    from aquaticy.preferences import refusal

    assert refusal(name), name


def test_the_helper_and_the_host_agree_on_the_addon_apps() -> None:
    import importlib.machinery
    import importlib.util

    from aquaticy import desktop

    loader = importlib.machinery.SourceFileLoader(
        "aquaticy_desktop_helfer_addons",
        str(Path(__file__).resolve().parent.parent / "docker" / "desktop" / "aquaticy-desktop"),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    helfer = importlib.util.module_from_spec(spec)
    loader.exec_module(helfer)
    assert set(desktop.ADDON_APPS) == {name for name, befehl in helfer.APPS.items() if not befehl}
    assert set(addons.APP_OF.values()) == set(desktop.ADDON_APPS)


# -- Das Fenster im Web ------------------------------------------------------------
def _sitzung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan: str,
             user_mode: bool = True) -> Any:
    from aquaticy import web

    konto = SimpleNamespace(plan=plan, username="u")
    profil = tmp_path / f"konto-{plan}"
    profil.mkdir(exist_ok=True)
    sitzung = web.ChatSession(account=konto, profile=profil)
    if user_mode and plan in ("pro", "ultra"):
        (profil / ".env").write_text("AQUATICY_VM_USER_MODE=true\n", encoding="utf-8")
    monkeypatch.setattr(web, "SESSION", sitzung)
    return sitzung


def test_normal_accounts_can_use_the_addons_without_workshop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import web

    _sitzung(tmp_path, monkeypatch, "normal")
    antwort, status = web.addon_action({"action": "install", "id": "wetter"})
    assert status == 200 and "wetter" in antwort["active"]
    antwort, status = web.addon_action({"action": "install", "id": "whatsapp"})
    assert status == 400 and "Ultra" in antwort["error"]
    antwort, status = web.addon_action({"action": "disable", "id": "wetter"})
    assert status == 200 and antwort["active"] == []
    antwort, status = web.addon_action({"action": "uninstall", "id": "wetter"})
    assert status == 200 and not any(a["installed"] for a in antwort["addons"])


def test_unknown_actions_and_addons_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import web

    _sitzung(tmp_path, monkeypatch, "pro")
    assert web.addon_action({"action": "hack", "id": "wetter"})[1] == 400
    assert web.addon_action({"action": "install", "id": "../x"})[1] == 400


def test_the_token_goes_in_but_never_comes_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import web

    sitzung = _sitzung(tmp_path, monkeypatch, "normal")
    geheim = "ghp_" + "G" * 36

    def pruefe(settings: Any, token: str, **kwargs: Any) -> dict[str, Any]:
        addons._write_secret(settings, addons.GITHUB_TOKEN_KEY, token)
        return {"who": "jonas", "warning": ""}

    monkeypatch.setattr(addons, "github_login", pruefe)
    web.addon_action({"action": "install", "id": "github"})
    antwort, status = web.addon_action({"action": "token", "id": "github", "token": geheim})
    assert status == 200 and "jonas" in antwort["message"]
    assert geheim not in json.dumps(antwort)
    assert sitzung.settings().github_token == geheim, "nach dem Neuaufbau aus der .env gelesen"
    assert geheim not in json.dumps(web.current_values())


def test_login_opens_the_app_for_the_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import sandbox as werkstatt
    from aquaticy import web

    sitzung = _sitzung(tmp_path, monkeypatch, "ultra")
    addons._update(sitzung.settings(), "signal", installed=True, enabled=True)
    geoeffnet: list[tuple] = []

    class Box:
        def desktop(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            geoeffnet.append(args)
            return subprocess.CompletedProcess(args, 0, b"{}", b"")

    monkeypatch.setattr(werkstatt, "shared", lambda settings: Box())
    antwort, status = web.addon_action({"action": "login", "id": "signal"})
    assert status == 200 and "QR-Code" in antwort["message"]
    assert geoeffnet == [("open", "signal")]
    antwort, status = web.addon_action({"action": "login_done", "id": "signal"})
    signal = next(a for a in antwort["addons"] if a["id"] == "signal")
    assert signal["login"]["state"] == "angemeldet" and signal["login"]["who"] == "laut dir"


def test_login_needs_the_user_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy import web

    sitzung = _sitzung(tmp_path, monkeypatch, "pro", user_mode=False)
    addons._update(sitzung.settings(), "whatsapp", installed=True, enabled=True)
    antwort, status = web.addon_action({"action": "login", "id": "whatsapp"})
    assert status == 400 and "User mode" in antwort["error"]


def test_a_busy_session_keeps_its_workshop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aquaticy import web

    sitzung = _sitzung(tmp_path, monkeypatch, "pro")
    addons._update(sitzung.settings(), "blender", installed=True, enabled=True)
    monkeypatch.setattr(sitzung, "busy", lambda: True)
    _, status = web.addon_action({"action": "disable", "id": "blender"})
    assert status == 409 and addons.load_state(sitzung.settings())["blender"]["enabled"]


class Werkstatt:
    alive = True
    user_mode = True

    def __init__(self) -> None:
        self.aufrufe: list[tuple] = []

    def desktop(self, *args: str, stdin: bytes | None = None, timeout: float = 0,
                start: bool = True) -> subprocess.CompletedProcess[bytes]:
        self.aufrufe.append((args, stdin, start))
        return subprocess.CompletedProcess(args, 0, b"{}", b"")


def test_the_human_types_directly_into_the_workshop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import sandbox as werkstatt
    from aquaticy import web

    _sitzung(tmp_path, monkeypatch, "ultra")
    box = Werkstatt()
    monkeypatch.setattr(werkstatt, "shared", lambda settings: box)
    assert web.workshop_input({"art": "click", "x": 640, "y": 400, "double": True})[1] == 200
    passwort = "Geheim!Passwort-123 äöü"
    assert web.workshop_input({"art": "type", "text": passwort})[1] == 200
    assert web.workshop_input({"art": "key", "key": "Return"})[1] == 200
    assert box.aufrufe == [
        (("click", "640", "400", "--double"), None, False),
        (("type",), passwort.encode(), False),
        (("key", "Return"), None, False),
    ], "nur an die laufende Werkstatt, nichts startet"
    # Das Passwort geht an die Werkstatt -- und sonst nirgendwohin.
    for datei in (tmp_path / "konto-pro").rglob("*"):
        if datei.is_file():
            assert passwort.encode() not in datei.read_bytes(), datei


@pytest.mark.parametrize(
    "eingabe",
    [
        {"art": "click", "x": 5000, "y": 1}, {"art": "click", "x": "a", "y": 1},
        {"art": "key", "key": "ctrl+alt+Delete"}, {"art": "key", "key": "Return; rm"},
        {"art": "type", "text": ""}, {"art": "type", "text": "x" * 1001},
        {"art": "shell", "text": "ls"}, {"art": "open", "url": "file:///etc/passwd"},
        {"art": "open", "url": "javascript:alert(1)"},
    ],
)
def test_wrong_input_never_reaches_the_workshop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, eingabe: dict[str, Any]
) -> None:
    from aquaticy import sandbox as werkstatt
    from aquaticy import web

    _sitzung(tmp_path, monkeypatch, "ultra")
    box = Werkstatt()
    monkeypatch.setattr(werkstatt, "shared", lambda settings: box)
    assert web.workshop_input(eingabe)[1] == 400
    assert box.aufrufe == []


def test_direct_input_is_ultra_and_user_mode_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import sandbox as werkstatt
    from aquaticy import web

    box = Werkstatt()
    monkeypatch.setattr(werkstatt, "shared", lambda settings: box)
    _sitzung(tmp_path, monkeypatch, "normal")
    assert web.workshop_input({"art": "key", "key": "Return"})[1] == 403
    # Seit 9.5.17 gehoert der User mode zu Ultra -- auch Pro bekommt 403.
    _sitzung(tmp_path, monkeypatch, "pro")
    assert web.workshop_input({"art": "key", "key": "Return"})[1] == 403
    _sitzung(tmp_path, monkeypatch, "ultra", user_mode=False)
    assert web.workshop_input({"art": "key", "key": "Return"})[1] == 400
    assert box.aufrufe == []


def test_opening_a_page_is_the_one_input_that_may_start_the_workshop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import sandbox as werkstatt
    from aquaticy import web

    _sitzung(tmp_path, monkeypatch, "ultra")
    box = Werkstatt()
    box.alive = False
    monkeypatch.setattr(werkstatt, "shared", lambda settings: box)
    assert web.workshop_input({"art": "key", "key": "Return"})[1] == 404
    assert web.workshop_input({"art": "open", "url": "https://github.com/login"})[1] == 200
    assert box.aufrufe == [(("open", "browser", "https://github.com/login"), None, True)]


def test_the_ui_has_the_addon_window_and_login_apps() -> None:
    from aquaticy import web

    html = web.UI_FILE.read_text(encoding="utf-8")
    for teil in ('id="btn-addons"', 'id="addonbox"', 'id="btn-loginapps"', 'id="screenbox"',
                 "Installieren", "Deinstallieren", "/api/addons", "/api/werkstatt/eingabe",
                 "Das gibt es leider nur mit einem Ultra-Konto"):
        assert teil in html, teil
    # Der Add-ons-Knopf sitzt direkt beim User-mode-Schalter, die Login-Apps darunter.
    zeile = html.index('id="usermode"')
    assert zeile < html.index('id="btn-addons"') < html.index('id="btn-loginapps"')
    assert html.index('id="btn-loginapps"') < html.index('id="usermode-note"')


def test_the_login_screen_asks_quietly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne Werkstatt: 204 statt 404 -- sonst fuellt das Nachfragen die Konsole."""
    from aquaticy import sandbox as werkstatt
    from aquaticy import web
    from tests.test_desktop import _hole

    sitzung = web.ChatSession()
    sitzung._settings = Settings(data_dir=tmp_path / "d", vm_user_mode=True)
    monkeypatch.setattr(web, "SESSION", sitzung)
    box = Werkstatt()
    box.alive = False
    monkeypatch.setattr(werkstatt, "shared", lambda settings: box)
    assert _hole("/api/werkstatt/bildschirm?leise=1&t=1", sitzung)[0] == 204
    assert _hole("/api/werkstatt/bildschirm", sitzung)[0] == 404


def test_github_survives_unexpected_answers() -> None:
    """Gefunden beim Fuzzen: eine Liste, wo ein Objekt stehen sollte."""
    client = _mock({"https://api.github.com/": []}, [])
    for aktion in ("ich", "repo", "suche"):
        antwort = addons.github_call("ghp_" + "t" * 36, aktion, repo="a/b", query="x",
                                     client=client)
        assert "unerwartet" in antwort["error"], aktion
