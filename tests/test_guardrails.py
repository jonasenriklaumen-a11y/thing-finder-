"""Tests fuer den Rechtsrahmen (aquaticy/guardrails.py) -- ohne echtes Modell.

Der Pruefer ist hier durchgaengig gestellt: was er antwortet, legt jeder Test
selbst fest. Geprueft wird, was Aquaticy mit seinem Urteil macht -- nicht, wie
gut ein bestimmtes Modell urteilt.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aquaticy import guardrails, preferences, web
from aquaticy.agent import Agent
from aquaticy.config import Settings, get_settings, guard_on, reset_settings_cache
from aquaticy.guardrails import (
    BY_ID,
    GENERIC,
    RULES,
    Guard,
    judge,
    judge_prompt,
    parse_verdict,
    refusal_text,
    rules_prompt,
    subagent_note,
    tool_refusal,
)
from aquaticy.tools import Toolbox

ERLAUBT = '{"zulaessig": true, "regel": "", "grund": "passt"}'
NEIN_NAME = '{"zulaessig": false, "regel": "name", "grund": "fremder Name"}'


class Pruefer:
    """Ein gestellter Rechtspruefer, der mitschreibt, was er gefragt wurde."""

    def __init__(self, *antworten: str | Exception) -> None:
        self.antworten = list(antworten)
        self.gefragt: list[tuple[str, str]] = []

    def __call__(self, prompt: str, model: str, settings: Any) -> str:
        self.gefragt.append((prompt, model))
        antwort = self.antworten.pop(0) if len(self.antworten) > 1 else self.antworten[0]
        if isinstance(antwort, Exception):
            raise antwort
        return antwort


@pytest.fixture
def pruefer(monkeypatch: pytest.MonkeyPatch):
    """Setzt einen eigenen Pruefer ein -- statt des allgemeinen "zulaessig"."""

    def setzen(*antworten: str | Exception) -> Pruefer:
        gestellt = Pruefer(*antworten)
        monkeypatch.setattr(guardrails, "_ask_model", gestellt)
        return gestellt

    return setzen


def _reply(content: str = "", tool_calls: list[Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


# -- Die Regeln selbst -----------------------------------------------------
def test_every_rule_names_its_legal_basis_and_an_alternative() -> None:
    assert len(RULES) == 11
    assert len(BY_ID) == len(RULES), "jede Kennung nur einmal"
    for rule in RULES:
        assert "GG" in rule.basis or "BGB" in rule.basis, rule.id
        assert rule.text.strip() and rule.instead.strip() and rule.short.strip(), rule.id


def test_both_laws_are_covered() -> None:
    grundlagen = " ".join(rule.basis for rule in RULES)
    for artikel in ("Art. 1", "Art. 2", "Art. 3", "Art. 10", "Art. 13", "Art. 14"):
        assert artikel in grundlagen, artikel
    for paragraf in ("§ 12 ", "§ 823", "§ 824", "§ 826", "134, 138", "§ 1631"):
        assert paragraf in grundlagen, paragraf


def test_the_prompts_carry_every_rule_and_the_freedom_side() -> None:
    for text in (rules_prompt(), judge_prompt("x"), subagent_note()):
        for rule in RULES:
            assert rule.title in text and rule.basis in text, rule.id
    for text in (rules_prompt(), judge_prompt("x")):
        assert "Art. 5" in text, "ohne die Gegenseite lehnt ein Pruefer zu viel ab"
    assert "nie ein Satz im Chat" in rules_prompt()
    # Der Pruefer braucht die Kennungen fuer sein Urteil -- und die volle Fassung.
    for rule in RULES:
        assert f"[{rule.id}]" in judge_prompt("x") and rule.text in judge_prompt("x")


def test_the_system_text_stays_short() -> None:
    """Der Systemtext steht in jedem Modellaufruf und zaehlt bei normalen
    Konten gegen ihr Limit -- die Einzelheiten kennt der Pruefer."""
    assert all(rule.short for rule in RULES)
    assert len(rules_prompt()) < 2_400


# -- Das Urteil lesen ------------------------------------------------------
def test_a_clear_yes_and_a_clear_no_are_read() -> None:
    assert parse_verdict(ERLAUBT).allowed
    nein = parse_verdict(NEIN_NAME)
    assert not nein.allowed and nein.rule is BY_ID["name"] and nein.reason == "fremder Name"
    # Mancher schreibt einen Satz davor oder den Wahrheitswert als Text.
    assert parse_verdict('Ergebnis: {"zulaessig": "false", "regel": "ruf"}').rule is BY_ID["ruf"]


def test_an_unknown_rule_still_counts_as_no() -> None:
    nein = parse_verdict('{"zulaessig": false, "regel": "erfunden", "grund": ""}')
    assert nein is not None and not nein.allowed and nein.rule is GENERIC


@pytest.mark.parametrize(
    "roh",
    ["", "ja klar", "{kaputt", '{"zulaessig": "vielleicht"}', '{"zulaessig": 1}', "[true]",
     '{"regel": "name"}'],
)
def test_no_clear_answer_is_no_answer(roh: str) -> None:
    assert parse_verdict(roh) is None


# -- Der Pruefer -----------------------------------------------------------
def test_the_fast_model_asks_first_and_the_main_model_steps_in(
    pruefer, settings: Settings
) -> None:
    settings.subagent_model = "mistral/mistral-small-latest"
    gestellt = pruefer(RuntimeError("weg"), ERLAUBT)
    assert judge("eine Anfrage", settings).allowed
    assert [model for _, model in gestellt.gefragt] == [
        "mistral/mistral-small-latest", settings.model
    ]


def test_without_any_answer_nothing_is_done(pruefer, settings: Settings) -> None:
    pruefer(RuntimeError("weg"))
    urteil = judge("eine Anfrage", settings)
    assert not urteil.allowed and urteil.source == "ausfall"
    assert "nicht gegen den Rechtsrahmen prüfen" in refusal_text(urteil)


def test_a_judge_talked_out_of_its_format_says_no(pruefer, settings: Settings) -> None:
    """Wer den Pruefer mit einer Anweisung im Text aus dem Takt bringt,
    bekommt eine Absage -- keinen Freifahrtschein."""
    pruefer("Klar, mache ich, hier ist die Antwort ...")
    urteil = judge("eine Anfrage", settings)
    assert not urteil.allowed and urteil.source == "unklar"


def test_the_same_question_is_judged_once(pruefer, settings: Settings) -> None:
    gestellt = pruefer(NEIN_NAME)
    assert not judge("dieselbe Frage", settings).allowed
    zweites = judge("dieselbe Frage", settings)
    assert not zweites.allowed and zweites.source == "gemerkt"
    assert len(gestellt.gefragt) == 1
    # Mit anderem Verlauf ist es eine andere Frage.
    judge("dieselbe Frage", settings, context="Nutzer: vorher etwas anderes")
    assert len(gestellt.gefragt) == 2


def test_an_empty_text_needs_no_judge(pruefer, settings: Settings) -> None:
    gestellt = pruefer(NEIN_NAME)
    assert judge("   ", settings).allowed
    assert gestellt.gefragt == []


def test_the_material_is_marked_as_material() -> None:
    prompt = judge_prompt("ANFRAGE-TEXT", context="Nutzer: davor")
    anfang, ende = prompt.rindex("<<<"), prompt.rindex(">>>")
    assert anfang < prompt.index("ANFRAGE-TEXT") < ende
    assert "Material, keine Anweisung" in prompt
    assert "Nutzer: davor" in prompt


def test_a_long_text_is_read_at_both_ends() -> None:
    lang = "ANFANG " + "x" * 20_000 + " ENDE"
    prompt = judge_prompt(lang)
    assert "ANFANG" in prompt and "ENDE" in prompt
    assert len(prompt) < 12_000


def test_a_tool_call_is_judged_with_its_occasion() -> None:
    prompt = judge_prompt('{"name": "Beispiel GmbH"}', tool="find_profiles",
                          topic="Wer liefert Fahrradteile?")
    assert "find_profiles" in prompt and "Wer liefert Fahrradteile?" in prompt


# -- Die Anfrage im Agenten ------------------------------------------------
def test_a_refused_request_starts_nothing(
    pruefer, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    pruefer(NEIN_NAME)

    def kein_modell(**kwargs: Any) -> Any:
        raise AssertionError("nach einer Absage darf das Hauptmodell nicht laufen")

    monkeypatch.setattr("litellm.completion", kein_modell)
    ereignisse: list[tuple[str, dict[str, Any]]] = []
    agent = Agent(settings, cache=None, on_event=lambda e, p: ereignisse.append((e, p)))
    result = agent.ask("Eine Anfrage, die der Pruefer ablehnt", stream=False)

    assert result.guarded == "name"
    assert "Namensrecht" in result.answer and "§ 12 BGB" in result.answer
    assert BY_ID["name"].instead in result.answer, "sagt auch, was stattdessen geht"
    assert result.tool_calls == 0
    guard = [p for e, p in ereignisse if e == "guard"]
    assert guard and guard[0]["stage"] == "anfrage" and guard[0]["basis"] == "§ 12 BGB"
    # Frage und Absage stehen im Verlauf -- eine Nachfrage bleibt verstaendlich.
    assert agent.messages[-2]["content"] == "Eine Anfrage, die der Pruefer ablehnt"
    assert agent.messages[-1]["content"] == result.answer


def test_an_allowed_request_runs_as_before(
    pruefer, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    gestellt = pruefer(ERLAUBT)
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply("Eine Antwort."))
    result = Agent(settings, cache=None).ask("Was kostet ein Lastenrad?", stream=False)
    assert result.answer == "Eine Antwort." and not result.guarded
    assert len(gestellt.gefragt) == 1


def test_a_greeting_costs_no_check(
    pruefer, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    gestellt = pruefer(NEIN_NAME)
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply("Hallo!"))
    Agent(settings, cache=None).ask("hallo", stream=False)
    assert gestellt.gefragt == []


def test_the_judge_sees_the_answer_before_the_follow_up(
    pruefer, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Eine kurze Nachfrage ist nur mit dem Vorigen zu verstehen -- auch mit
    der letzten Antwort, nicht nur mit der letzten Frage."""
    gestellt = pruefer(ERLAUBT)
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply("ERSTE-ANTWORT"))
    agent = Agent(settings, cache=None)
    agent.ask("Erste Frage zum Thema", stream=False)
    agent.ask("und die zweite dazu?", stream=False)
    zweiter = gestellt.gefragt[-1][0]
    assert "Erste Frage zum Thema" in zweiter and "ERSTE-ANTWORT" in zweiter


def test_switched_off_means_no_check_and_no_rules(
    pruefer, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    settings.legal_guard = False
    gestellt = pruefer(NEIN_NAME)
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply("Eine Antwort."))
    agent = Agent(settings, cache=None)
    assert agent.guard is None and agent.toolbox.guard is None
    assert "Rechtsrahmen" not in agent.messages[0]["content"]
    assert agent.ask("Eine Anfrage", stream=False).answer == "Eine Antwort."
    assert gestellt.gefragt == []


def test_switched_on_the_rules_are_in_the_system_text(settings: Settings) -> None:
    agent = Agent(settings, cache=None)
    assert "Rechtsrahmen (Grundgesetz und BGB)" in agent.messages[0]["content"]
    assert agent.guard is agent.toolbox.guard, "Anfrage und Werkzeuge an derselben Stelle"


# -- Die Werkzeuge ---------------------------------------------------------
def test_a_refused_tool_call_does_not_run(
    pruefer, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    gestellt = pruefer(NEIN_NAME)
    box = Toolbox(settings, cache=None)
    box.guard.topic = "die Frage des Nutzers"
    monkeypatch.setattr(
        box, "find_profiles",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("darf nicht laufen")),
    )
    ereignisse: list[tuple[str, dict[str, Any]]] = []
    box.on_event = lambda e, p: ereignisse.append((e, p))

    antwort = box.call("find_profiles", {"name": "Beispiel"})
    assert antwort == tool_refusal(parse_verdict(NEIN_NAME))
    assert antwort["skipped_reason"] == "legal_guard"
    assert "die Frage des Nutzers" in gestellt.gefragt[0][0]
    assert ereignisse[0][0] == "guard" and ereignisse[0][1]["tool"] == "find_profiles"


def test_ordinary_tools_are_not_judged_one_by_one(pruefer, settings: Settings) -> None:
    gestellt = pruefer(NEIN_NAME)
    box = Toolbox(settings, cache=None)
    assert "error" not in box.call("calculate", {"expression": "2+2"})
    assert gestellt.gefragt == []


def test_an_allowed_tool_call_runs(
    pruefer, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    pruefer(ERLAUBT)
    box = Toolbox(settings, cache=None)
    monkeypatch.setattr(box, "find_profiles", lambda **kwargs: {"profiles": ["ok"]})
    assert box.call("find_profiles", {"name": "Beispiel GmbH"}) == {"profiles": ["ok"]}


def test_a_guard_without_topic_still_judges(pruefer, settings: Settings) -> None:
    gestellt = pruefer(ERLAUBT)
    assert Guard(settings).check_call("mail_draft", {"subject": "Hallo"}).allowed
    assert "(unbekannt)" in gestellt.gefragt[0][0]


# -- Die Subagenten --------------------------------------------------------
def test_subagents_get_the_rules_behind_their_role_and_before_the_task(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from aquaticy.subagents import run_subagents

    gesehen: list[str] = []

    def completion(**kwargs: Any) -> Any:
        gesehen.append(kwargs["messages"][0]["content"])
        return _reply("fertig")

    monkeypatch.setattr("litellm.completion", completion)
    run_subagents(["AUFTRAG-XY"], settings, parallel=1)
    auftrag = gesehen[0]
    assert auftrag.startswith("Du bist")
    assert auftrag.index("Rechtsrahmen") < auftrag.index("AUFTRAG-XY")

    settings.legal_guard = False
    gesehen.clear()
    run_subagents(["AUFTRAG-XY"], settings, parallel=1)
    assert "Rechtsrahmen" not in gesehen[0]


# -- Einstellungen ---------------------------------------------------------
@pytest.mark.parametrize("roh", ["", "true", "an", "True ", "jaa", "1", "irgendwas"])
def test_a_typo_never_switches_the_guard_off(roh: str) -> None:
    assert guard_on(roh)


@pytest.mark.parametrize("roh", ["false", "0", "aus", "AUS", " nein ", "off", "no"])
def test_only_a_clear_off_switches_it_off(roh: str) -> None:
    assert not guard_on(roh)


def test_the_env_switch_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AQUATICY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("aquaticy.config.load_env", lambda *a, **k: None)
    for roh, erwartet in (("aus", False), ("an", True), ("", True)):
        monkeypatch.setenv("AQUATICY_LEGAL_GUARD", roh)
        reset_settings_cache()
        assert get_settings().legal_guard is erwartet
    reset_settings_cache()


def test_the_chat_cannot_switch_it_off(settings: Settings) -> None:
    box = Toolbox(settings, cache=None)
    for name in ("legal_guard", "rechtsrahmen", "Leitplanken", "guardrails"):
        antwort = box.change_setting(name, "aus")
        assert "Grundgesetz und BGB" in antwort["error"], name
    assert "AQUATICY_LEGAL_GUARD" in preferences.PROTECTED
    assert preferences.find("legal_guard") is None


def _konto(tmp_path: Path, plan: str, zeile: str) -> Settings:
    profil = tmp_path / plan
    profil.mkdir(parents=True)
    (profil / ".env").write_text(zeile + "\n", encoding="utf-8")
    return web._profile_settings(profil, plan)


def test_a_normal_account_keeps_the_guard_whatever_its_file_says(tmp_path: Path) -> None:
    assert _konto(tmp_path, "normal", "AQUATICY_LEGAL_GUARD=false").legal_guard is True


def test_only_an_ultra_account_may_switch_it_off(tmp_path: Path) -> None:
    # Seit 9.5.17 hält auch Pro die Leitplanken -- abschalten kann nur Ultra.
    assert _konto(tmp_path, "pro", "AQUATICY_LEGAL_GUARD=false").legal_guard is True
    assert _konto(tmp_path / "u", "ultra", "AQUATICY_LEGAL_GUARD=false").legal_guard is False
    assert _konto(tmp_path / "u2", "ultra", "AQUATICY_LEGAL_GUARD=an").legal_guard is True


@pytest.fixture
def sitzung(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> web.ChatSession:
    frisch = web.ChatSession()
    frisch._settings = Settings(
        model="mistral/mistral-large-latest", data_dir=tmp_path / "data",
        env_path=tmp_path / ".env", subagents_auto=False,
    )
    monkeypatch.setattr(web, "SESSION", frisch)
    # Ohne Konto schreibt save_values in die gefundene .env -- im Test die
    # eigene, nie die echte im Heimatverzeichnis.
    monkeypatch.setattr(web, "find_env_file", lambda: tmp_path / ".env")
    return frisch


def test_only_ultra_may_save_it_off(sitzung: web.ChatSession, tmp_path: Path) -> None:
    sitzung.account = web.Account("n", "n@example.org", "normal", 0)
    with pytest.raises(ValueError, match="Ultra-Konto"):
        web.save_values({"AQUATICY_LEGAL_GUARD": "false"})
    assert not (tmp_path / ".env").exists(), "abgelehnt heisst: nichts geschrieben"
    # An bleibt fuer jeden erlaubt.
    web.save_values({"AQUATICY_LEGAL_GUARD": "true"})

    # Seit 9.5.17 darf auch Pro es nicht mehr abschalten.
    sitzung.account = web.Account("p", "p@example.org", "pro", 0)
    with pytest.raises(ValueError, match="Ultra-Konto"):
        web.save_values({"AQUATICY_LEGAL_GUARD": "aus"})

    sitzung.account = web.Account("u", "u@example.org", "ultra", 0)
    web.save_values({"AQUATICY_LEGAL_GUARD": "aus"})
    assert "AQUATICY_LEGAL_GUARD=false" in (tmp_path / ".env").read_text()


def test_the_form_shows_the_switch_and_the_rules(sitzung: web.ChatSession) -> None:
    assert web.current_values()["AQUATICY_LEGAL_GUARD"] == "true"
    html = web.UI_FILE.read_text(encoding="utf-8")
    assert '<legend>Dev settings</legend>' in html
    assert 'name="AQUATICY_LEGAL_GUARD"' in html
    overview = guardrails.rules_overview()
    assert [item["id"] for item in overview] == [rule.id for rule in RULES]
    json.dumps(overview)  # geht so an den Browser


# -- Auftraege -------------------------------------------------------------
def test_a_refused_job_switches_itself_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dieselbe Frage wird beim naechsten Takt wieder abgelehnt -- ohne
    Abschalten liefe der Auftrag ewig ins selbe Nein."""
    from aquaticy import jobs

    class Abgelehnt:
        def __init__(self, settings: Any, cache: Any = None) -> None:
            self.session_id = "c"

        def ask(self, frage: str, **kwargs: Any) -> Any:
            return SimpleNamespace(answer="Das mache ich nicht.", guarded="name")

        def close(self) -> None: ...

    monkeypatch.setattr("aquaticy.agent.Agent", Abgelehnt)
    monkeypatch.setattr("aquaticy.cache.Cache", lambda *a, **k: None)
    store = jobs.JobStore(tmp_path / "j.db")
    job = store.add("Eine Frage")
    with store._connect() as conn:
        conn.execute("UPDATE jobs SET next_run = 1 WHERE id = ?", (job.id,))
    settings = SimpleNamespace(db_path=tmp_path / "j.db", cache_ttl_hours=1)

    assert jobs.run_job(store.get(job.id), settings)[0].startswith(jobs.GUARD_STATE)
    assert jobs.Scheduler(lambda: settings).tick() == 1
    danach = store.get(job.id)
    assert not danach.enabled
    assert danach.last_state == f"{jobs.GUARD_STATE}: name"


def test_an_outage_is_not_a_refusal(
    pruefer, monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path
) -> None:
    """Faellt das Pruefmodell nur kurz aus, bleibt ein Auftrag an: nicht
    geprueft ist nicht abgelehnt."""
    from aquaticy import jobs

    pruefer(RuntimeError("weg"))
    result = Agent(settings, cache=None).ask("Eine Anfrage", stream=False)
    assert result.error == guardrails.UNAVAILABLE and not result.guarded

    class Ausfall:
        def __init__(self, settings: Any, cache: Any = None) -> None:
            self.session_id = "c"

        def ask(self, frage: str, **kwargs: Any) -> Any:
            return result

        def close(self) -> None: ...

    monkeypatch.setattr("aquaticy.agent.Agent", Ausfall)
    monkeypatch.setattr("aquaticy.cache.Cache", lambda *a, **k: None)
    store = jobs.JobStore(tmp_path / "j.db")
    job = store.add("Eine Frage")
    with store._connect() as conn:
        conn.execute("UPDATE jobs SET next_run = 1 WHERE id = ?", (job.id,))
    ziel = SimpleNamespace(db_path=tmp_path / "j.db", cache_ttl_hours=1)
    assert jobs.Scheduler(lambda: ziel).tick() == 1
    danach = store.get(job.id)
    assert danach.enabled and danach.last_state == guardrails.UNAVAILABLE


def test_a_refusal_reaches_the_browser_through_the_real_session(
    pruefer, monkeypatch: pytest.MonkeyPatch, sitzung: web.ChatSession
) -> None:
    """Der echte Agent in der echten Sitzung: die Absage kommt als Schritt,
    als Text und mit einem Abschluss an -- und das Hauptmodell schweigt."""
    pruefer(NEIN_NAME)
    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("kein Hauptmodell")),
    )
    gesehen: list[tuple[str, dict[str, Any]]] = []
    sitzung.ask("Eine Anfrage", lambda name, payload: gesehen.append((name, payload)))
    namen = [name for name, _ in gesehen]
    assert "guard" in namen and "answer_chunk" in namen and namen[-1] == "done"
    assert namen.index("guard") < namen.index("answer_chunk")
