"""Tests fuer den Master -- den Leiter der Rechercheeinheit.

Der Master darf die Recherche besser machen. Was er nie darf: sie
verhindern. Die Haelfte dieser Datei prueft deshalb, was passiert, wenn er
ausfaellt, Unsinn schickt oder sich nicht an seine Grenzen haelt.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from cortex.config import Settings
from cortex.master import (
    MAX_RETRY,
    MAX_ROUNDS,
    Review,
    plan_mission,
    review_results,
)


def _reply(payload: Any) -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _antwortet(monkeypatch: pytest.MonkeyPatch, payload: Any) -> list[dict[str, Any]]:
    gesehen: list[dict[str, Any]] = []

    def completion(**kwargs: Any):
        gesehen.append(kwargs)
        return _reply(payload)

    monkeypatch.setattr("litellm.completion", completion)
    return gesehen


# ---------------------------------------------------------------------------
# Beauftragen
# ---------------------------------------------------------------------------
def test_every_agent_gets_its_own_role(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Der Unterschied zum alten Planer: nicht nur was, sondern auch wie."""
    _antwortet(
        monkeypatch,
        {
            "plan": "Erst die Anbieter, dann die Preise.",
            "agenten": [
                {"auftrag": "Cafés mit WLAN in Bremen", "rolle": "sucht Betreiberseiten"},
                {"auftrag": "Preise für Kaffee in Bremen", "rolle": "achtet auf Preise",
                 "schwer": True},
            ],
        },
    )
    mission = plan_mission("Wo arbeiten in Bremen?", settings, limit=44, strong=2)

    assert mission.fallback is False
    assert mission.plan.startswith("Erst die Anbieter")
    assert [task.text for task in mission.tasks] == [
        "Cafés mit WLAN in Bremen",
        "Preise für Kaffee in Bremen",
    ]
    assert mission.tasks[0].angle == "sucht Betreiberseiten"
    assert mission.tasks[1].strong is True
    # Die Rolle für die Technik-Hinweise fällt dabei von selbst ab.
    assert mission.tasks[1].role == "zahlen"


def test_the_master_is_told_how_many_and_how_strong(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    gesehen = _antwortet(monkeypatch, {"plan": "", "agenten": [{"auftrag": "A", "rolle": "r"}]})
    plan_mission("Frage", settings, limit=44, strong=2)
    prompt = gesehen[0]["messages"][0]["content"]
    assert "bis zu 44 Agenten" in prompt
    assert "Hoechstens 2 Auftraege" in prompt
    assert "entscheidest du an der Frage" in prompt, "ohne /max ist die Zahl seine Wahl"


def test_max_forces_the_full_crew(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    gesehen = _antwortet(monkeypatch, {"plan": "", "agenten": [{"auftrag": "A", "rolle": "r"}]})
    plan_mission("Frage", settings, limit=44, strong=2, forced=True)
    prompt = gesehen[0]["messages"][0]["content"]
    assert "Besetze ALLE 44" in prompt
    assert "entscheidest du an der Frage" not in prompt


def test_more_agents_than_allowed_are_cut(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    _antwortet(
        monkeypatch,
        {
            "plan": "",
            "agenten": [{"auftrag": f"Teil {n}", "rolle": f"r{n}"} for n in range(80)],
        },
    )
    mission = plan_mission("Frage", settings, limit=44, strong=2)
    assert len(mission.tasks) == 44


def test_only_two_may_be_strong(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Sonst laufen vierundvierzig Agenten auf dem teuersten Modell."""
    _antwortet(
        monkeypatch,
        {
            "plan": "",
            "agenten": [
                {"auftrag": f"Teil {n}", "rolle": "r", "schwer": True} for n in range(10)
            ],
        },
    )
    mission = plan_mission("Frage", settings, limit=44, strong=2)
    assert sum(1 for task in mission.tasks if task.strong) == 2
    assert len(mission.tasks) == 10, "die anderen laufen normal weiter"


def test_a_master_that_does_not_answer_is_not_the_end(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Er darf die Recherche besser machen, nie verhindern."""
    def kaputt(**kwargs: Any):
        raise RuntimeError("Zeitlimit")

    monkeypatch.setattr("litellm.completion", kaputt)
    mission = plan_mission("Frage", settings, limit=44)
    assert mission.fallback is True and mission.tasks == []


def test_nonsense_instead_of_json_is_survivable(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    _antwortet(monkeypatch, "ich habe da mal etwas vorbereitet")
    assert plan_mission("Frage", settings).fallback is True

    _antwortet(monkeypatch, {"plan": "ok", "agenten": [{"rolle": "ohne Auftrag"}]})
    assert plan_mission("Frage", settings).fallback is True


# ---------------------------------------------------------------------------
# Bewerten und nachschicken
# ---------------------------------------------------------------------------
def test_gaps_lead_to_a_second_round(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    _antwortet(
        monkeypatch,
        {
            "urteil": "luecken",
            "fehlt": ["Die Öffnungszeiten fehlen"],
            "nachrunde": [{"auftrag": "Öffnungszeiten über die Karte", "rolle": "Spurensuche"}],
        },
    )
    review = review_results("Frage", "### Teil A\nnichts gefunden", settings)
    assert review.ok is False
    assert review.verdict == "luecken"
    assert review.missing == ["Die Öffnungszeiten fehlen"]
    assert [task.text for task in review.retries] == ["Öffnungszeiten über die Karte"]


def test_a_good_result_stays_a_good_result(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    _antwortet(monkeypatch, {"urteil": "gut", "fehlt": [], "nachrunde": []})
    review = review_results("Frage", "### Teil A\nviel gefunden", settings)
    assert review.ok is True and review.retries == []


def test_deeds_count_more_than_the_verdict(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Wer Nachaufträge vergibt, hat Lücken gesehen -- egal was er schreibt."""
    _antwortet(
        monkeypatch,
        {"urteil": "gut", "nachrunde": [{"auftrag": "doch noch etwas", "rolle": "r"}]},
    )
    assert review_results("Frage", "...", settings).ok is False


def test_a_broken_review_does_not_start_a_round(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Eine Nachrunde, die auf einem Fehler beruht, ist teurer als eine, die
    ausbleibt."""
    def kaputt(**kwargs: Any):
        raise RuntimeError("weg")

    monkeypatch.setattr("litellm.completion", kaputt)
    assert review_results("Frage", "...", settings) == Review(ok=True)


def test_the_second_round_stays_small(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Sie soll Lücken schließen, nicht die Recherche wiederholen."""
    _antwortet(
        monkeypatch,
        {
            "urteil": "luecken",
            "nachrunde": [{"auftrag": f"Teil {n}", "rolle": "r"} for n in range(40)],
        },
    )
    review = review_results("Frage", "...", settings, retry=MAX_RETRY)
    assert len(review.retries) == MAX_RETRY


def test_the_review_gets_the_findings(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    gesehen = _antwortet(monkeypatch, {"urteil": "gut"})
    review_results("Wo gibt es Kaffee?", "### Teil A\nCafé Klein, a.de", settings)
    prompt = gesehen[0]["messages"][0]["content"]
    assert "Café Klein" in prompt
    assert "Wo gibt es Kaffee?" in prompt
    assert "die Karte statt der Suchmaschine" in prompt, "die Technik für die zweite Runde"


def test_two_rounds_are_the_measure() -> None:
    """Nach der zweiten liegt es nicht mehr an der Formulierung."""
    assert MAX_ROUNDS == 2
