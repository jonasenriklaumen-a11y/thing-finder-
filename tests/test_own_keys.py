"""Seashell (9.5.14): eigene API-Schluessel -- je Konto, und nur fuer dieses Konto.

Was hier festgehalten wird:

* Der Schluesselbund gehoert genau einem Konto. In der Datenbank steht ein
  Schluessel nur verschluesselt, umkopiert in ein anderes Konto ist er
  wertlos, und in den Browser geht er nie -- nur die letzten vier Zeichen.
* Ein eigener Schluessel geht nur an seinen Anbieter (oder an eine Adresse,
  die das Konto selbst eingetragen hat), nie an einen Server des Betreibers.
  Ein Schluessel des Betreibers geht nie an eine Adresse des Kontos.
* Modelle mit eigenem Schluessel zaehlen nicht ins Kontingent. Was auf dem
  Server arbeitet -- Werkstatt, Seitenabrufe, Suchen --, zaehlt immer.
* Die Modelle eines eigenen Schluessels sieht nur dieses Konto.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from aquaticy import guardrails, keyvault, metering, web
from aquaticy.config import NO_KEY, Settings
from aquaticy.keyvault import KeyVault, VaultError, load_secret, masked, scrub, summary
from aquaticy.quota import SESSION_TOKENS, Quota, QuotaExceeded
from tests import fake_llm

GEHEIM = b"s" * 32
EIGEN_MISTRAL = "eigen-mistral-geheim-1111"
EIGEN_NVIDIA = "nvapi-eigen-geheim-2222"


# -- Der Schluesselbund ---------------------------------------------------------------
def _tresor(tmp_path: Path, konto: str = "konto-a", geheim: bytes = GEHEIM) -> KeyVault:
    return KeyVault(tmp_path / "konten.sqlite3", konto, geheim)


def test_a_key_is_stored_encrypted_and_only_shown_masked(tmp_path: Path) -> None:
    tresor = _tresor(tmp_path)
    tresor.set("MISTRAL_API_KEY", f"  {EIGEN_MISTRAL}  ")
    assert tresor.keys() == {"MISTRAL_API_KEY": EIGEN_MISTRAL}
    assert tresor.count() == 1 and tresor.names() == ["MISTRAL_API_KEY"]
    [eintrag] = tresor.public()
    assert eintrag["hint"] == "••••1111" and eintrag["added_at"] > 0
    assert "geheim" not in json.dumps(eintrag), "der Browser bekommt nie den Schluessel"
    assert b"geheim" not in (tmp_path / "konten.sqlite3").read_bytes(), "nur verschluesselt"
    tresor.set("MISTRAL_API_KEY", "eigen-mistral-neu-9999")
    assert tresor.keys() == {"MISTRAL_API_KEY": "eigen-mistral-neu-9999"}
    assert tresor.count() == 1, "ersetzt, nicht doppelt"
    assert tresor.remove("MISTRAL_API_KEY") and not tresor.remove("MISTRAL_API_KEY")
    assert tresor.keys() == {} and tresor.public() == [] and tresor.count() == 0


def test_another_account_can_neither_see_nor_use_the_keys(tmp_path: Path) -> None:
    anna, bernd = _tresor(tmp_path, "konto-a"), _tresor(tmp_path, "konto-b")
    anna.set("NVIDIA_NIM_API_KEY", EIGEN_NVIDIA)
    assert bernd.keys() == {} and bernd.public() == [] and bernd.count() == 0
    # Wer die Zeile in ein anderes Konto umkopiert, hat nichts davon: jedes
    # Konto hat seinen eigenen Schluessel zum Schluesselbund.
    with sqlite3.connect(tmp_path / "konten.sqlite3") as conn:
        conn.execute(
            "INSERT INTO api_keys SELECT 'konto-b', name, token, hint, added_at "
            "FROM api_keys WHERE account_id = 'konto-a'"
        )
    assert bernd.keys() == {} and bernd.public() == [] and bernd.count() == 0
    # Und wer nur die Datenbank hat, ohne das Geheimnis des Servers, auch nicht.
    assert _tresor(tmp_path, "konto-a", b"x" * 32).keys() == {}
    assert anna.keys() == {"NVIDIA_NIM_API_KEY": EIGEN_NVIDIA}


def test_only_known_slots_and_real_looking_keys_are_taken(tmp_path: Path) -> None:
    tresor = _tresor(tmp_path)
    for name, wert in [
        ("OPENAI_API_KEY", "sk-abcdefgh1234"),        # kein Platz dafuer
        ("AQUATICY_HA_TOKEN", "x" * 20),               # erst recht nicht
        ("MISTRAL_API_KEY", ""),
        ("MISTRAL_API_KEY", "kurz"),
        ("MISTRAL_API_KEY", "mit leerzeichen drin"),
        ("MISTRAL_API_KEY", "zeilen\numbruch-1234"),
        ("MISTRAL_API_KEY", "schlüssel-mit-umlaut"),
        ("MISTRAL_API_KEY", "x" * 401),
    ]:
        with pytest.raises(VaultError):
            tresor.set(name, wert)
    assert tresor.keys() == {}
    with pytest.raises(ValueError):
        KeyVault(tmp_path / "k.sqlite3", "", GEHEIM)


def test_the_summary_says_none_one_or_several() -> None:
    assert summary([]) == "Du hast keinen API-Schlüssel hinzugefügt."
    assert summary(["TAVILY_API_KEY"]) == "Du hast einen API-Schlüssel hinzugefügt: Tavily."
    assert summary(["TAVILY_API_KEY", "NVIDIA_NIM_API_KEY", "MISTRAL_API_KEY"]) == (
        "Du hast 3 API-Schlüssel hinzugefügt: NVIDIA NIM, Mistral, Tavily."
    )
    assert summary(["GIBT_ES_NICHT"]) == "Du hast keinen API-Schlüssel hinzugefügt."


def test_masking_and_scrubbing() -> None:
    assert masked("nvapi-abcdefgh1234") == "••••1234"
    assert masked("kurz1234") == "••••", "bei kurzen verraet schon das Ende zu viel"
    text = f"Incorrect API key provided: {EIGEN_MISTRAL}. Again: {EIGEN_MISTRAL}!"
    assert scrub(text, [EIGEN_MISTRAL, "", "kurz"]) == (
        "Incorrect API key provided: ••••. Again: ••••!"
    )


def test_the_server_secret_is_made_once_and_kept_private(tmp_path: Path) -> None:
    pfad = tmp_path / "vault.key"
    erst = load_secret(pfad)
    assert len(erst) == 32 and load_secret(pfad) == erst
    if os.name == "posix":
        assert stat.S_IMODE(pfad.stat().st_mode) == 0o600


# -- Wohin ein Schluessel geht ----------------------------------------------------------
def _eigen(**mehr: Any) -> Settings:
    werte: dict[str, Any] = {
        "model": "mistral/mistral-large-latest",
        "api_keys": {"MISTRAL_API_KEY": EIGEN_MISTRAL},
        "own_key_names": frozenset({"MISTRAL_API_KEY"}),
    }
    werte.update(mehr)
    return Settings(**werte)


def test_an_own_key_is_used_for_its_provider_and_only_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "betreiber-mistral-0000")
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nvapi-betreiber-0000")
    settings = _eigen()
    for modell in ("mistral/mistral-large-latest", "mistral/mistral-small-latest"):
        assert settings.key_source(modell) == "own"
        assert settings.llm_kwargs_for(modell) == {"api_key": EIGEN_MISTRAL}
    fremd = "nvidia_nim/meta/llama-3.3-70b-instruct"
    assert settings.key_source(fremd) == "operator"
    assert EIGEN_MISTRAL not in json.dumps(settings.llm_kwargs_for(fremd))
    # Ohne eigenen Schluessel gilt der gestellte.
    ohne = Settings(model="mistral/mistral-large-latest")
    assert ohne.key_source(ohne.model) == "operator"
    assert ohne.api_key == "betreiber-mistral-0000"


def test_a_model_on_the_operators_server_stays_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    # Der Betreiber stellt openai/fake-modell auf seinem eigenen Server. Ein
    # allgemeiner eigener Schluessel darf dieses Modell nicht an sich reissen
    # -- sonst ginge er an den Server des Betreibers, und das Modell zaehlte
    # nicht mehr.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-betreiber-0000")
    monkeypatch.delenv("AQUATICY_API_KEY", raising=False)
    basis = "http://betreiber.example/v1"
    settings = Settings(
        model="openai/fake-modell", api_base=basis, api_base_for="openai/fake-modell",
        api_keys={"AQUATICY_API_KEY": "eigen-allgemein-3333"},
        own_key_names=frozenset({"AQUATICY_API_KEY"}),
    )
    assert settings.key_source("openai/fake-modell") == "operator"
    weg = settings.llm_kwargs_for("openai/fake-modell")
    assert weg.get("api_base") == basis and "eigen-allgemein" not in json.dumps(weg)
    # Bei einem anderen Anbieter ist der allgemeine eigene Schluessel gemeint.
    assert settings.key_source("groq/llama-3.1-8b-instant") == "own"
    assert settings.llm_kwargs_for("groq/llama-3.1-8b-instant") == {
        "api_key": "eigen-allgemein-3333"
    }


def test_the_operators_address_belongs_to_the_operators_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bis 9.5.14 galt die Adresse des Betreibers fuer den Anbieter des Modells,
    # das ein Konto gewaehlt hatte -- ein Mistral-Modell landete so beim LM
    # Studio des Betreibers.
    monkeypatch.setenv("MISTRAL_API_KEY", "betreiber-mistral-0000")
    basis = "http://lmstudio.example:1234/v1"
    settings = Settings(model="mistral/mistral-large-latest", api_base=basis,
                        api_base_for="openai/lokales-modell")
    assert "api_base" not in settings.llm_kwargs_for("mistral/mistral-large-latest")
    assert settings.llm_kwargs_for("openai/lokales-modell")["api_base"] == basis
    # Lokal ohne Konten bleibt es beim Hauptmodell.
    lokal = Settings(model="openai/lokales-modell", api_base=basis)
    assert lokal.llm_kwargs_for("openai/lokales-modell")["api_base"] == basis


def test_no_operator_key_goes_to_an_address_the_account_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-betreiber-0000")
    monkeypatch.setenv("AQUATICY_API_KEY", "betreiber-allgemein-0000")
    eigene_adresse = "http://mein-rechner.example/v1"
    settings = Settings(model="openai/mein-modell", api_base=eigene_adresse,
                        own_api_base=eigene_adresse, api_base_for="openai/fake-modell")
    assert settings.llm_kwargs_for("openai/mein-modell") == {
        "api_base": eigene_adresse, "api_key": NO_KEY,
    }
    # Mit eigenem Schluessel geht der eigene dorthin -- und nur der.
    settings.api_keys = {"AQUATICY_API_KEY": "eigen-allgemein-3333"}
    settings.own_key_names = frozenset({"AQUATICY_API_KEY"})
    assert settings.key_source("openai/mein-modell") == "own"
    assert settings.llm_kwargs_for("openai/mein-modell") == {
        "api_key": "eigen-allgemein-3333", "api_base": eigene_adresse,
    }


def test_own_keys_have_their_own_pace_without_showing_the_key() -> None:
    from aquaticy import pace

    settings = _eigen()
    takt = settings.pace_key(settings.model)
    assert takt.startswith("own:") and len(takt) == 20 and "geheim" not in takt
    anders = _eigen(api_keys={"MISTRAL_API_KEY": "eigen-mistral-anders-4444"})
    assert anders.pace_key(anders.model) not in ("", takt)
    assert Settings(model="mistral/mistral-large-latest").pace_key("mistral/x") == ""
    pace.forget_gates()
    try:
        gestellt = pace.gate_for("mistral/mistral-large-latest")
        assert pace.gate_for("mistral/mistral-large-latest", takt) is not gestellt
        assert pace.gate_for("mistral/mistral-large-latest", takt) is pace.gate_for(
            "mistral/mistral-small-latest", takt), "ein Schluessel, ein Takt"
    finally:
        pace.forget_gates()


def test_every_key_counts_as_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nvapi-betreiber-0000")
    settings = _eigen(search_keys={"TAVILY_API_KEY": "tvly-eigen-geheim-5555"})
    assert {"nvapi-betreiber-0000", EIGEN_MISTRAL, "tvly-eigen-geheim-5555"} <= set(
        settings.secrets())
    assert settings.search_key_source == "operator"  # duckduckgo braucht keinen
    settings.search_backend = "tavily"
    settings.own_key_names = frozenset({"MISTRAL_API_KEY", "TAVILY_API_KEY"})
    assert settings.search_key_source == "own"


# -- Was zaehlt ---------------------------------------------------------------------------
@pytest.fixture
def kontingent(tmp_path: Path) -> Quota:
    return Quota(tmp_path / "konten.sqlite3", "konto-a", time.time() - 3600)


def _verbraucht(kontingent: Quota) -> int:
    return SESSION_TOKENS - kontingent.remaining()


def test_own_key_calls_do_not_count_but_provided_ones_do(
    tmp_path: Path, kontingent: Quota
) -> None:
    from aquaticy.usage import UsageLog

    settings = _eigen(data_dir=tmp_path / "profil", quota=kontingent)
    metering.record(settings, "mistral/mistral-large-latest", 1_000, 500)
    assert _verbraucht(kontingent) == 0
    assert not kontingent.status()["session"]["active"], "die Sitzung beginnt nicht einmal"
    metering.record(settings, "nvidia_nim/meta/llama-3.3-70b-instruct", 1_000, 500)
    assert _verbraucht(kontingent) == 1_500
    # Die Statistik des Profils kennt beide Aufrufe.
    assert UsageLog(settings.db_path).total_tokens() == 3_000


def test_at_the_limit_own_keys_still_answer_but_server_work_stops(
    tmp_path: Path, kontingent: Quota
) -> None:
    settings = _eigen(data_dir=tmp_path / "profil", quota=kontingent)
    kontingent.record(SESSION_TOKENS, "gestellt")
    metering.check(settings, model="mistral/mistral-large-latest")  # eigener Schluessel
    with pytest.raises(QuotaExceeded):
        metering.check(settings, model="nvidia_nim/meta/llama-3.3-70b-instruct")
    for art in metering.WORK_COSTS:
        with pytest.raises(QuotaExceeded):
            metering.check_work(settings, art)
    with pytest.raises(QuotaExceeded):
        metering.check_image(settings, "mistral/pixtral-large-latest")


def test_server_work_is_charged_in_tokens(tmp_path: Path, kontingent: Quota) -> None:
    settings = _eigen(data_dir=tmp_path / "profil", quota=kontingent)
    assert metering.work_cost("werkstatt", 0) == 150, "eine angefangene Sekunde zaehlt"
    assert metering.work_cost("werkstatt", 2.2) == 450
    assert metering.work_cost("gibt-es-nicht") == 0
    metering.charge_work(settings, "werkstatt", 2.2)
    metering.charge_work(settings, "suche", 2)
    metering.charge_work(settings, "seite")
    assert _verbraucht(kontingent) == 450 + 100 + 100
    # Ohne Kontingent (Pro, lokal) zaehlt nichts -- und nichts bricht.
    metering.charge_work(_eigen(quota=None), "werkstatt", 100)


def test_an_image_with_an_own_key_only_costs_the_filing(
    tmp_path: Path, kontingent: Quota
) -> None:
    settings = _eigen(data_dir=tmp_path / "profil", quota=kontingent,
                      api_keys={"NVIDIA_NIM_API_KEY": EIGEN_NVIDIA},
                      own_key_names=frozenset({"NVIDIA_NIM_API_KEY"}))
    metering.charge_image(settings, "nvidia_nim/stabilityai/stable-diffusion-3-medium")
    assert _verbraucht(kontingent) == metering.work_cost("bild")
    metering.charge_image(settings, "gemini/imagen-3.0-generate-002")
    assert _verbraucht(kontingent) == metering.work_cost("bild") + metering.IMAGE_TOKENS


def test_tools_book_their_server_work_and_refuse_at_the_limit(
    tmp_path: Path, kontingent: Quota, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy.tools import WORK_TOOLS, Toolbox

    settings = _eigen(data_dir=tmp_path / "profil", quota=kontingent)
    box = Toolbox(settings, cache=None)
    ausgefuehrt: list[str] = []

    def ausfuehren(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        ausgefuehrt.append(name)
        return {"results": [], "queries": ["eins", "zwei"]} if name == "web_search" else {
            "result": 42}

    monkeypatch.setattr(box, "_call", ausfuehren)
    box.call("calculate", {"expression": "6*7"})
    assert _verbraucht(kontingent) == 0, "Rechnen ist keine Serverarbeit"
    box.call("web_search", {"query": "x"})
    assert _verbraucht(kontingent) == metering.work_cost("suche", 2)
    kontingent.record(SESSION_TOKENS, "gestellt")
    antwort = box.call("web_search", {"query": "y"})
    assert antwort["kontingent"] is True and "5-Stunden-Sitzung" in antwort["error"]
    assert ausgefuehrt == ["calculate", "web_search"], "abgelehnt, bevor etwas arbeitet"
    assert box.call("calculate", {"expression": "1+1"}) == {"result": 42}
    assert set(WORK_TOOLS.values()) <= set(metering.WORK_COSTS)


# -- Ende zu Ende: zwei Konten, ein Betreiber, ein "Anbieter" ------------------------------
#: Der echte Rechtspruefer -- conftest ersetzt ihn sonst durch "zulaessig".
ECHTER_PRUEFER = guardrails._ask_model
NVIDIA = "nvidia_nim/meta/llama-3.3-70b-instruct"


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from aquaticy import config
    from aquaticy.auth import AuthStore

    betreiber, betreiber_port = fake_llm.starte()
    anbieter, anbieter_port = fake_llm.starte()
    fake_llm.ANFRAGEN.clear()
    for key, wert in {
        "AQUATICY_MODEL": "openai/fake-modell",
        "AQUATICY_API_BASE": f"http://127.0.0.1:{betreiber_port}/v1",
        "OPENAI_API_KEY": "sk-betreiber-fake",
        # Hinter eigenen NVIDIA-Schluesseln steht hier der zweite Testserver --
        # der "Anbieter". Sonst ginge die Anfrage ins Netz.
        "NVIDIA_NIM_API_BASE": f"http://127.0.0.1:{anbieter_port}/v1",
        "AQUATICY_SUBAGENTS_AUTO": "false",
        "AQUATICY_DATA_DIR": str(tmp_path / "daten"),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }.items():
        monkeypatch.setenv(key, wert)
    for key in ("NVIDIA_NIM_API_KEY", "MISTRAL_API_KEY", "AQUATICY_API_KEY",
                "BRAVE_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    config.reset_settings_cache()
    monkeypatch.setattr(guardrails, "_ask_model", ECHTER_PRUEFER)
    monkeypatch.setattr(web, "AUTH", AuthStore(tmp_path / "konten", "PROKEY234"))
    monkeypatch.setattr(web, "SESSIONS", web.SessionRegistry())
    monkeypatch.setattr(web, "SESSION", web.SessionProxy())
    monkeypatch.setattr(web, "strong_models", lambda *args, **kwargs: [])
    monkeypatch.setattr(web, "AUTH_LIMIT", web.RateLimiter(attempts=100, window_seconds=60))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"port": httpd.server_address[1], "betreiber": betreiber_port,
           "anbieter": anbieter_port, "wurzel": tmp_path}
    httpd.shutdown()
    httpd.server_close()
    betreiber.shutdown()
    anbieter.shutdown()
    config.reset_settings_cache()


def _req(port: int, method: str, path: str, body: Any = None,
         cookie: str = "") -> tuple[int, bytes]:
    conn = HTTPConnection("127.0.0.1", port, timeout=120)
    headers = {"Content-Type": "application/json", "User-Agent": "Seashell"}
    if cookie:
        headers["Cookie"] = cookie
    conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    antwort = conn.getresponse()
    ergebnis = antwort.status, antwort.read()
    kopf = antwort.getheader("Set-Cookie") or ""
    conn.close()
    if kopf:
        _req.letzter_keks = kopf.split(";", 1)[0]  # type: ignore[attr-defined]
    return ergebnis


def _konto(port: int, plan: str = "normal", code: str = "") -> str:
    _req(port, "POST", "/api/consent", {"accepted": True})
    zustimmung = _req.letzter_keks  # type: ignore[attr-defined]
    status, daten = _req(port, "POST", "/api/auth/register", {
        "email": f"{plan}{time.time_ns()}@example.org", "username": f"Seashell{time.time_ns()}",
        "password": "ein langes Passwort", "plan": plan, "pro_code": code,
        "terms_accepted": True}, zustimmung)
    assert status == 200, daten
    return zustimmung + "; " + _req.letzter_keks  # type: ignore[attr-defined]


def _json(port: int, method: str, path: str, cookie: str, body: Any = None) -> Any:
    status, daten = _req(port, method, path, body, cookie)
    assert status == 200, (path, status, daten[:300])
    return json.loads(daten)


def _chat(port: int, cookie: str, text: str) -> list[dict[str, Any]]:
    status, daten = _req(port, "POST", "/api/chat", {"message": text}, cookie)
    assert status == 200, daten[:300]
    return [json.loads(z[6:]) for z in daten.decode().splitlines() if z.startswith("data: ")]


def _antwort(ereignisse: list[dict[str, Any]]) -> str:
    return "".join(e.get("text", "") for e in ereignisse if e.get("type") == "chunk")


def _anfragen() -> list[dict[str, Any]]:
    return [json.loads(zeile) for zeile in fake_llm.ANFRAGEN]


def _modell_waehlen(port: int, cookie: str, modell: str) -> None:
    antwort = _json(port, "POST", "/api/config", cookie, {"AQUATICY_MODEL": modell})
    assert antwort.get("ok") is not False, antwort


def test_the_key_area_says_what_is_stored_and_never_the_key(server: dict[str, Any]) -> None:
    port = server["port"]
    anna = _konto(port)
    leer = _json(port, "GET", "/api/keys", anna)
    assert leer["count"] == 0 and leer["account"] is True
    assert leer["summary"] == "Du hast keinen API-Schlüssel hinzugefügt."
    assert {s["name"] for s in leer["slots"]} == {s.name for s in keyvault.SLOTS}
    assert "nur zu deinem Konto" in leer["privacy"] and "Limit" in leer["quota_note"]

    antwort = _json(port, "POST", "/api/keys", anna,
                    {"action": "set", "name": "NVIDIA_NIM_API_KEY", "value": EIGEN_NVIDIA})
    assert antwort["ok"] and antwort["count"] == 1
    assert antwort["summary"] == "Du hast einen API-Schlüssel hinzugefügt: NVIDIA NIM."
    platz = next(s for s in antwort["slots"] if s["name"] == "NVIDIA_NIM_API_KEY")
    assert platz["set"] and platz["status"].startswith("Hinterlegt ••••2222")
    antwort = _json(port, "POST", "/api/keys", anna, {
        "action": "set", "name": "TAVILY_API_KEY", "value": "tvly-eigen-geheim-5555"})
    assert antwort["summary"] == (
        "Du hast 2 API-Schlüssel hinzugefügt: NVIDIA NIM, Tavily.")

    # Was nicht passt, wird nicht angenommen -- mit einem Satz dazu.
    for falsch in ({"action": "set", "name": "OPENAI_API_KEY", "value": "sk-abcdefgh1234"},
                   {"action": "set", "name": "MISTRAL_API_KEY", "value": "zu kurz"},
                   {"action": "loeschen", "name": "MISTRAL_API_KEY"}):
        status, daten = _req(port, "POST", "/api/keys", falsch, anna)
        assert status == 400 and json.loads(daten)["error"]

    # Kein Endpunkt gibt den Schluessel heraus -- auch nicht dem Besitzer.
    for pfad in ("/api/keys", "/api/config", "/api/account", "/api/usage",
                 "/api/models?mode=code", "/api/header", "/"):
        status, daten = _req(port, "GET", pfad, cookie=anna)
        assert status == 200, pfad
        assert b"eigen-geheim" not in daten, f"{pfad} verraet einen Schluessel"
    # Auf der Platte nur verschluesselt, in keiner .env, nicht in der Umgebung.
    for datei in server["wurzel"].rglob("*"):
        if datei.is_file():
            assert b"eigen-geheim" not in datei.read_bytes(), datei
    assert not any("eigen-geheim" in wert for wert in os.environ.values())

    antwort = _json(port, "POST", "/api/keys", anna,
                    {"action": "remove", "name": "TAVILY_API_KEY"})
    assert antwort["count"] == 1 and "entfernt" in antwort["message"]


def test_keys_and_their_models_belong_to_one_account(server: dict[str, Any]) -> None:
    port = server["port"]
    anna, bernd = _konto(port), _konto(port)
    _json(port, "POST", "/api/keys", anna,
          {"action": "set", "name": "NVIDIA_NIM_API_KEY", "value": EIGEN_NVIDIA})

    # Bernd sieht Annas Schluessel nicht -- und nicht die Modelle dazu.
    bei_bernd = _json(port, "GET", "/api/keys", bernd)
    assert bei_bernd["count"] == 0 and not any(s["set"] for s in bei_bernd["slots"])
    modelle_bernd = _json(port, "GET", "/api/models?mode=chat", bernd)
    assert not any(m["id"].startswith("nvidia_nim/") for m in modelle_bernd["models"])
    assert all(m.get("source") != "own" for m in modelle_bernd["models"])

    # Anna sieht sie, markiert, in einer eigenen Gruppe -- die gestellten dazu.
    modelle_anna = _json(port, "GET", "/api/models?mode=chat", anna)
    eigene = [m for m in modelle_anna["models"] if m.get("source") == "own"]
    assert {m["id"] for m in eigene} >= {NVIDIA}
    assert all(m["source_label"] == "Dein Schlüssel" for m in eigene)
    gestellt = [m for m in modelle_anna["models"] if m.get("source") == "operator"]
    assert any(m["id"] == "openai/fake-modell" for m in gestellt)
    gruppen = modelle_anna["picker"]["groups"]
    assert [g["title"] for g in gruppen] == ["Mit deinem Schlüssel", "Gestellt"]
    assert "nicht in dein Limit" in gruppen[0]["note"]

    # Bernd kann Annas Modell waehlen -- aber es laeuft nicht mit ihrem
    # Schluessel. Bei ihm ist es gestellt (hier: gar kein Schluessel da).
    _modell_waehlen(port, bernd, NVIDIA)
    fake_llm.ANFRAGEN.clear()
    _chat(port, bernd, "Bernd fragt mit dem NVIDIA-Modell")
    assert not any(a["auth"].startswith("Bearer nvapi") for a in _anfragen())


def test_an_own_key_model_runs_at_the_provider_and_does_not_count(
    server: dict[str, Any],
) -> None:
    port = server["port"]
    anna, bernd = _konto(port), _konto(port)
    _json(port, "POST", "/api/keys", anna,
          {"action": "set", "name": "NVIDIA_NIM_API_KEY", "value": EIGEN_NVIDIA})
    _modell_waehlen(port, anna, NVIDIA)

    fake_llm.ANFRAGEN.clear()
    ereignisse = _chat(port, anna, "Anna fragt mit eigenem Schluessel: rechne 6 mal 7")
    assert "42" in _antwort(ereignisse), ereignisse
    anfragen = _anfragen()
    assert anfragen, "das Modell wurde gefragt"
    assert all(a["port"] == server["anbieter"] for a in anfragen), "nur beim Anbieter"
    assert all(a["auth"].startswith("Bearer nvapi") for a in anfragen), "mit Annas Schluessel"
    assert any(a["rf"] == "urteil" for a in anfragen), "auch der Rechtspruefer"
    konto = _json(port, "GET", "/api/account", anna)
    assert konto["usage"]["session"]["percent"] == 0, "das Modell zaehlt nicht ins Limit"

    # Bernd hat keinen eigenen Schluessel: gestellt, beim Betreiber, gezaehlt.
    fake_llm.ANFRAGEN.clear()
    assert "42" in _antwort(_chat(port, bernd, "Bernd rechnet 6 mal 7"))
    anfragen = _anfragen()
    assert all(a["port"] == server["betreiber"] for a in anfragen)
    assert all(a["auth"] == "Bearer sk-be" for a in anfragen)
    assert _json(port, "GET", "/api/account", bernd)["usage"]["session"]["percent"] >= 1
    # Annas Schluessel ist nie beim Betreiber angekommen.
    assert _json(port, "GET", "/api/account", anna)["usage"]["session"]["percent"] == 0


def test_at_the_limit_an_own_key_still_answers_but_server_tools_stop(
    server: dict[str, Any],
) -> None:
    port = server["port"]
    vorher = set((server["wurzel"] / "konten" / "users").iterdir()) if (
        server["wurzel"] / "konten" / "users").exists() else set()
    anna = _konto(port)
    profil = (set((server["wurzel"] / "konten" / "users").iterdir()) - vorher).pop()
    _json(port, "POST", "/api/keys", anna,
          {"action": "set", "name": "NVIDIA_NIM_API_KEY", "value": EIGEN_NVIDIA})
    _modell_waehlen(port, anna, NVIDIA)
    web.AUTH.quota(web.AUTH.account(profil.name)).record(SESSION_TOKENS, "gestellt")

    antwort = _antwort(_chat(port, anna, "Am Limit, aber mit eigenem Schluessel: rechne"))
    assert "42" in antwort, "das eigene Modell antwortet weiter"
    antwort = _antwort(_chat(
        port, anna, 'Am Limit suchen: WERKZEUG:web_search {"query": "Seashell"}'))
    assert "5-Stunden-Sitzung" in antwort and "kontingent" in antwort, antwort

    # Zurueck zum gestellten Modell: das zaehlt -- und ist am Limit gesperrt.
    _modell_waehlen(port, anna, "openai/fake-modell")
    status, daten = _req(port, "POST", "/api/chat", {"message": "Und gestellt?"}, anna)
    assert status == 429 and json.loads(daten)["code"] == "quota"


def test_a_provider_that_quotes_the_key_never_gets_it_to_the_browser(
    server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Der Verbindungstest prueft nebenbei die Suchmaschine -- hier ohne Netz.
    monkeypatch.setattr("aquaticy.probe.check_search", lambda *args, **kwargs: (True, "ok"))
    port = server["port"]
    anna = _konto(port)
    zitiert = "nvapi-leak-geheim-7777"   # der Testserver zitiert Schluessel mit "leak"
    _json(port, "POST", "/api/keys", anna,
          {"action": "set", "name": "NVIDIA_NIM_API_KEY", "value": zitiert})
    _modell_waehlen(port, anna, NVIDIA)

    test = _json(port, "POST", "/api/keys", anna,
                 {"action": "test", "name": "NVIDIA_NIM_API_KEY"})
    assert test["ok"] is False and zitiert not in json.dumps(test)
    assert "Incorrect API key" in test["message"] or "401" in test["message"], test
    probe = _json(port, "POST", "/api/probe", anna, {})
    assert probe["llm"]["ok"] is False and zitiert not in json.dumps(probe)
    _, daten = _req(port, "POST", "/api/chat", {"message": "Und der Chat?"}, anna)
    assert zitiert.encode() not in daten and b"leak-geheim" not in daten


def test_a_key_typed_into_the_settings_form_goes_into_the_vault(
    server: dict[str, Any],
) -> None:
    # Die alte Form (Schluesselfeld neben dem Modell) gibt es in der
    # Oberflaeche nicht mehr -- kommt sie trotzdem an, landet der Schluessel
    # im Schluesselbund, nie in der .env des Profils.
    port = server["port"]
    konten = server["wurzel"] / "konten" / "users"
    vorher = set(konten.iterdir()) if konten.exists() else set()
    anna = _konto(port)
    profil = (set(konten.iterdir()) - vorher).pop()
    _json(port, "POST", "/api/config", anna, {
        "AQUATICY_MODEL": "mistral/mistral-large-latest",
        web.API_KEY_FIELD: "form-mistral-geheim-8888",
    })
    tresor = web.AUTH.vault(web.AUTH.account(profil.name))
    assert tresor.keys() == {"MISTRAL_API_KEY": "form-mistral-geheim-8888"}
    assert "form-mistral" not in (profil / ".env").read_text(encoding="utf-8")
    status, daten = _req(port, "POST", "/api/config", {
        web.API_KEY_FIELD: "kein schluessel"}, anna)
    assert status == 400 and "API-Schlüssel" in json.loads(daten)["error"]


def test_keys_from_an_old_profile_env_move_into_the_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy import config
    from aquaticy.auth import AuthStore

    monkeypatch.setenv("AQUATICY_DATA_DIR", str(tmp_path / "daten"))
    config.reset_settings_cache()
    store = AuthStore(tmp_path / "konten", "PROKEY234")
    monkeypatch.setattr(web, "AUTH", store)
    konto = store.register("alt@example.org", "ein langes Passwort", "normal",
                           username="Alt", terms_accepted=True, terms_version="1")
    profil = store.profile_dir(konto.id)
    profil.mkdir(parents=True, exist_ok=True)
    (profil / ".env").write_text(
        "# aus 9.5.13\nAQUATICY_LOCATION=Köln\nMISTRAL_API_KEY=alt-mistral-geheim-6666\n",
        encoding="utf-8",
    )
    try:
        settings = web._profile_settings(profil, "normal", konto)
        assert settings.api_keys["MISTRAL_API_KEY"] == "alt-mistral-geheim-6666"
        assert settings.own_key_names == frozenset({"MISTRAL_API_KEY"})
        assert settings.location == "Köln"
        env = (profil / ".env").read_text(encoding="utf-8")
        assert "alt-mistral" not in env, "raus aus dem Klartext"
        assert "AQUATICY_LOCATION=Köln" in env and "# aus 9.5.13" in env
        assert store.vault(konto).keys() == {"MISTRAL_API_KEY": "alt-mistral-geheim-6666"}
        # Ein zweites Laden aendert nichts mehr.
        wieder = web._profile_settings(profil, "normal", konto)
        assert wieder.api_keys["MISTRAL_API_KEY"] == "alt-mistral-geheim-6666"
    finally:
        config.reset_settings_cache()


def test_the_version_carries_its_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    import aquaticy
    from aquaticy import cli, config
    from aquaticy.auth import AuthStore, pro_code_for

    assert f"{aquaticy.__version__} {aquaticy.__codename__}" == aquaticy.VERSION_LABEL
    assert aquaticy.__codename__ == "Sunflower"
    runner = CliRunner()
    assert aquaticy.VERSION_LABEL in runner.invoke(cli.app, ["version"]).output
    # "aquaticy list" zeigt, wie viele eigene Schluessel ein Konto hat --
    # nie welche, nie was darin steht.
    monkeypatch.setenv("AQUATICY_DATA_DIR", str(tmp_path))
    config.reset_settings_cache()
    try:
        store = AuthStore(tmp_path, pro_code_for(tmp_path))
        konto = store.register("liste@example.org", "ein langes Passwort", "normal",
                               username="Liste", terms_accepted=True, terms_version="1")
        store.vault(konto).set("TAVILY_API_KEY", "tvly-liste-geheim-9999")
        ausgabe = runner.invoke(cli.app, ["list"], env={"COLUMNS": "200"}).output
        assert "Schlüss" in ausgabe  # Spalte "Eigene Schlüssel" (ggf. gekürzt)
        zeile = next(z for z in ausgabe.splitlines() if z.startswith("Liste"))
        assert "1" in zeile.split(), "wie viele -- sonst nichts"
        assert "geheim" not in ausgabe and "9999" not in ausgabe
    finally:
        config.reset_settings_cache()


def test_the_general_key_is_never_tested_against_the_operators_model(
    server: dict[str, Any],
) -> None:
    # Das Hauptmodell ist gestellt (openai/fake-modell auf dem Server des
    # Betreibers). Den allgemeinen Schluessel bekaeme es im Betrieb nie -- also
    # wird er auch nicht dorthin oder an dessen Anbieter "getestet".
    port = server["port"]
    anna = _konto(port)
    _json(port, "POST", "/api/keys", anna,
          {"action": "set", "name": "AQUATICY_API_KEY", "value": "allg-eigen-geheim-1717"})
    fake_llm.ANFRAGEN.clear()
    test = _json(port, "POST", "/api/keys", anna, {"action": "test", "name": "AQUATICY_API_KEY"})
    assert test["ok"] is False and "Hauptmodell" in test["message"]
    assert fake_llm.ANFRAGEN == [], "der Schluessel ging nirgendwohin"
    # Und das gestellte Modell laeuft weiter mit dem Schluessel des Betreibers.
    assert "42" in _antwort(_chat(port, anna, "Gestellt trotz allgemeinem Schluessel: rechne"))
    assert all(a["auth"] == "Bearer sk-be" and a["port"] == server["betreiber"]
               for a in _anfragen())


def test_key_tests_are_limited_per_account(
    server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from aquaticy.auth import RateLimiter

    monkeypatch.setattr(web, "KEY_TEST_LIMIT", RateLimiter(attempts=2, window_seconds=60))
    port = server["port"]
    anna, bernd = _konto(port), _konto(port)
    frage = {"action": "test", "name": "TAVILY_API_KEY"}
    for _ in range(2):
        assert _req(port, "POST", "/api/keys", frage, anna)[0] == 200
    status, daten = _req(port, "POST", "/api/keys", frage, anna)
    assert status == 429 and "in einer Minute" in json.loads(daten)["error"]
    assert _req(port, "POST", "/api/keys", frage, bernd)[0] == 200, "je Konto gezaehlt"


def test_the_picker_puts_own_models_in_their_own_group() -> None:
    from aquaticy.webview import picker_view

    eigen = {"id": "nvidia_nim/x", "source": "own"}
    gestellt = {"id": "ollama_chat/y", "source": "operator"}
    assert picker_view("normal", [gestellt], [])["groups"] == [], "ohne eigene keine Gruppen"
    mit = picker_view("normal", [gestellt, eigen], [])
    assert [g["title"] for g in mit["groups"]] == ["Mit deinem Schlüssel", "Gestellt"]
    assert mit["groups"][0]["models"] == [eigen] and mit["groups"][0]["note"] == (
        "zählt nicht in dein Limit")
    # Ohne Limit (Pro) gibt es nichts, worin es nicht zaehlte.
    assert picker_view("normal", [eigen], [], limited=False)["groups"] == [
        {"title": "Mit deinem Schlüssel", "note": "auf deine Rechnung beim Anbieter",
         "models": [eigen]}]
