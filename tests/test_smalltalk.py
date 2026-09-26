"""Standardantworten ohne Modell (9.5.18 Sunflower)."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import pytest

from aquaticy import smalltalk
from aquaticy.agent import Agent
from aquaticy.config import Settings
from aquaticy.tools import Toolbox


@pytest.fixture
def toolbox(settings: Settings) -> Toolbox:
    return Toolbox(settings, cache=None)


def _art(name: str) -> smalltalk.Art:
    return next(a for a in smalltalk.ARTEN if a.name == name)


def test_hallo_has_25_different_answers() -> None:
    antworten = _art("begruessung").antworten
    assert len(antworten) == 25 and len(set(antworten)) == 25


@pytest.mark.parametrize("name", ["begruessung", "wie_gehts", "danke", "abschied",
                                  "zustimmung", "identitaet", "schoepfer", "faehigkeiten"])
def test_the_frequent_kinds_have_25_unique_answers(name: str) -> None:
    antworten = _art(name).antworten
    assert len(antworten) == 25 and len(set(antworten)) == 25, name


def test_every_answer_is_clean_german_text() -> None:
    for art in smalltalk.ARTEN:
        assert art.antworten, art.name
        for text in art.antworten:
            assert text == text.strip() and "  " not in text, (art.name, text)
            assert text[-1] in ".!?)" or text.endswith("…"), (art.name, text)


def test_answers_are_random_and_never_repeat_back_to_back() -> None:
    gesehen = [smalltalk.antwort("Hallo") for _ in range(60)]
    assert len(set(gesehen)) > 10, "zufaellig -- nicht immer derselbe Satz"
    assert all(a != b for a, b in pairwise(gesehen))


@pytest.mark.parametrize(
    ("frage", "art"),
    [
        ("Hallo!", "begruessung"), ("hey aquaticy 👋", "begruessung"), ("Moin", "begruessung"),
        ("Guten Morgen!", "morgen"), ("Guten Abend", "abend"),
        ("Wie geht's dir?", "wie_gehts"), ("hallo wie gehts", "wie_gehts"),
        ("Wer hat dich erschaffen?", "schoepfer"), ("Wer hat dich programmiert?", "schoepfer"),
        ("Von wem bist du?", "schoepfer"), ("Wer steckt hinter dir?", "schoepfer"),
        ("Bist du ChatGPT?", "fremde_ki"), ("Wer bist du?", "identitaet"),
        ("Wie heißt du?", "identitaet"), ("Bist du ein Mensch?", "mensch"),
        ("Was kannst du alles?", "faehigkeiten"), ("Danke dir!", "danke"),
        ("super, danke!", "danke"), ("Tschüss", "abschied"), ("Gute Nacht", "gute_nacht"),
        ("ok", "zustimmung"), ("Du bist toll", "lob"), ("sorry", "entschuldigung"),
        ("Wie alt bist du?", "alter"), ("Sprichst du Englisch?", "sprachen"),
        ("test", "test"),
    ],
)
def test_simple_messages_are_recognised(frage: str, art: str) -> None:
    gefunden = smalltalk.art_von(frage)
    assert gefunden is not None and gefunden.name == art, frage


@pytest.mark.parametrize("frage", [
    "hallo, welche cafés in köln haben wlan?", "danke -- und was kostet das teurere?",
    "wie geht das mit dem export?", "test von notebooks bis 1200 euro", "ok und sonntags?",
    "Wer hat das Telefon erfunden?", "was kannst du mir über Bremen sagen",
    "Bist du sicher?", "hilfe bei meiner Steuererklärung", "wer bin ich laut meinem Profil?",
    "Wer hat dich gestern angerufen und was wollte er?",
])
def test_real_questions_go_to_the_model(frage: str) -> None:
    assert smalltalk.art_von(frage) is None, frage


def test_the_creator_answer_names_jonas_and_aquaticy() -> None:
    for text in _art("schoepfer").antworten:
        assert "Jonas" in text, text


def test_ok_after_a_question_from_aquaticy_goes_to_the_model() -> None:
    assert smalltalk.art_von("ok") is not None
    assert smalltalk.art_von("ok", frage_offen=True) is None
    # Ein Gruss bleibt ein Gruss, auch nach einer Rueckfrage.
    assert smalltalk.art_von("danke", frage_offen=True) is not None


def test_the_agent_answers_without_any_model_and_types_it_out(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    monkeypatch.setattr("litellm.completion",
                        lambda **kw: pytest.fail("Standardantwort braucht kein Modell"))
    monkeypatch.setattr("aquaticy.agent.STANDARD_TYPING_SECONDS", 0)
    events: list[tuple[str, dict[str, Any]]] = []
    agent = Agent(settings, cache=None, toolbox=toolbox,
                  on_event=lambda name, payload: events.append((name, payload)))
    result = agent.ask("Wer hat dich gebaut?", stream=True)
    stuecke = [p["text"] for n, p in events if n == "answer_chunk"]
    assert len(stuecke) > 3, "beim Streamen Wort fuer Wort -- wie ein Modell"
    assert "".join(stuecke) == result.answer
    assert result.answer in _art("schoepfer").antworten


def test_ok_after_a_question_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, toolbox: Toolbox
) -> None:
    agent = Agent(settings, cache=None, toolbox=toolbox)
    agent.messages.append({"role": "assistant", "content": "Soll ich weitersuchen?"})
    assert agent._last_reply_asks() is True
    agent.messages.append({"role": "assistant", "content": "Hier ist die Liste."})
    assert agent._last_reply_asks() is False


def test_ai_guard_does_not_ask_a_model_about_small_talk(settings: Settings) -> None:
    from aquaticy.aiguard import classify, forget_judgements

    forget_judgements()
    assert classify("Wer hat dich erschaffen?", settings,
                    ask=lambda p, s: pytest.fail("kein Modell fuer Small Talk")) == (False, "")
