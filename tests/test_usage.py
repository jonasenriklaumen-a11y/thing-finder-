"""Tests fuer den Token-Zaehler.

Ein Token sind hier drei Zeichen -- das ist eine Vereinbarung, und genau die
wird geprueft. Wer sie aendert, soll es hier merken, nicht erst an einer
Zahl, die im Fenster falsch aussieht.
"""

from __future__ import annotations

from pathlib import Path

from aquaticy.usage import CHARS_PER_TOKEN, UsageLog, message_tokens, tokens


def test_drei_zeichen_sind_ein_token() -> None:
    assert CHARS_PER_TOKEN == 3
    assert tokens("") == 0
    assert tokens("abc") == 1
    # Aufgerundet: ein angefangenes Token zaehlt ganz.
    assert tokens("abcd") == 2
    assert tokens("123456789") == 3


def test_nachrichten_zaehlen_auch_werkzeuge_und_bilder() -> None:
    messages = [
        {"role": "system", "content": "abc"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "abcdef"},
                {"type": "image_url", "image_url": {"url": "data:abc"}},
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"function": {"name": "abc", "arguments": '{"q":"x"}'}},
            ],
        },
    ]
    # 1 + 2 + 3 (data:abc = 8 Zeichen -> 3) + 1 (Name) + 3 (Argumente)
    assert message_tokens(messages) == 1 + 2 + 3 + 1 + 3


def test_bild_zaehlt_mit(tmp_path: Path) -> None:
    ohne = message_tokens([{"role": "user", "content": [{"type": "text", "text": "hallo"}]}])
    mit = message_tokens(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hallo"},
                    {"type": "image_url", "image_url": {"url": "d" * 3000}},
                ],
            }
        ]
    )
    assert mit > ohne + 900


def test_verbrauch_wird_je_modell_summiert(tmp_path: Path) -> None:
    log = UsageLog(tmp_path / "u.db")
    log.record("openai/gpt-4o", 1000, 200)
    log.record("openai/gpt-4o", 500, 200)
    log.record("anthropic/claude-sonnet-5", 100, 10)

    summe = log.summary()
    assert summe["chars_per_token"] == 3
    assert summe["total"] == {"tokens_in": 1600, "tokens_out": 410, "calls": 3}
    assert summe["today"]["calls"] == 3
    modelle = {eintrag["model"]: eintrag for eintrag in summe["models"]}
    assert modelle["openai/gpt-4o"]["tokens_in"] == 1500
    assert modelle["openai/gpt-4o"]["calls"] == 2
    assert modelle["anthropic/claude-sonnet-5"]["tokens_out"] == 10


def test_leerer_aufruf_wird_nicht_eingetragen(tmp_path: Path) -> None:
    log = UsageLog(tmp_path / "u.db")
    log.record("egal", 0, 0)
    assert log.summary()["total"]["calls"] == 0


def test_ohne_modellnamen_landet_es_unter_unbekannt(tmp_path: Path) -> None:
    log = UsageLog(tmp_path / "u.db")
    log.record("   ", 30, 3)
    assert log.summary()["models"][0]["model"] == "unbekannt"


def test_zuruecksetzen_leert_den_stand(tmp_path: Path) -> None:
    log = UsageLog(tmp_path / "u.db")
    log.record("a", 30, 3)
    log.record("b", 30, 3)
    assert log.clear() == 2
    assert log.summary()["total"]["calls"] == 0


def test_ein_kaputter_zaehler_kostet_keine_antwort(tmp_path: Path) -> None:
    """Der Zaehler ist Beiwerk. Faellt er aus, laeuft die Anfrage trotzdem."""
    log = UsageLog(tmp_path / "u.db")
    log.db_path = tmp_path / "nicht" / "existiert" / "x" / "u.db"
    log.db_path.parent.mkdir(parents=True)
    log.db_path.write_text("kein sqlite")
    log.record("a", 30, 3)  # darf nicht werfen
    assert log.summary()["total"]["calls"] == 0
