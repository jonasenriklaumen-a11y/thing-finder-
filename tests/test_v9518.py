"""9.5.18 Sunflower: Tempo im Pro- und Code-Modus.

Die Rechtspruefung und die Master-Planung laufen gleichzeitig; losgeschickt
wird trotzdem erst nach dem OK. Die Suche nach dem staerksten Modell laeuft
neben der Pruefung. Alles mit gestellten Aufrufen -- kein Netz, kein Modell.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from aquaticy import master
from aquaticy.agent import Agent, AgentResult
from aquaticy.config import Settings
from aquaticy.tools import Toolbox


@pytest.fixture
def agent(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Agent:
    monkeypatch.setattr(Agent, "_auto_subagents_wanted", lambda self: True)
    monkeypatch.setattr(Agent, "_strongest_model", lambda self, purpose="": "")
    a = Agent(settings, cache=None, toolbox=Toolbox(settings, cache=None))
    a.mode, a.structured, a.online = "pro", True, True
    return a


def test_the_master_plans_while_the_legal_check_runs(
    agent: Agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    zeiten: dict[str, float] = {}
    geplant: list[str] = []

    def planen(question: str, settings: Any, **kw: Any) -> Any:
        zeiten["plan_start"] = time.monotonic()
        geplant.append(question)
        return master.Mission(tasks=[], plan="", fallback=True)

    def pruefen(self: Agent, question: str) -> None:
        zeiten["pruefung_start"] = time.monotonic()
        time.sleep(0.3)
        zeiten["pruefung_ende"] = time.monotonic()
        return None

    monkeypatch.setattr(master, "plan_mission", planen)
    monkeypatch.setattr(Agent, "_legal_check", pruefen)
    agent._prefetch_plan("Vergleiche drei Lastenräder")
    Agent._legal_check(agent, "Vergleiche drei Lastenräder")
    mission = agent._take_prefetched_plan("Vergleiche drei Lastenräder",
                                          max(1, agent.agent_limit), agent.strong_count)
    assert mission is not None and mission.fallback
    assert zeiten["plan_start"] < zeiten["pruefung_ende"], "die Planung wartet nicht mehr"
    assert geplant == ["Vergleiche drei Lastenräder"], "und laeuft nur einmal"


def test_a_refused_request_starts_no_agents(
    agent: Agent, monkeypatch: pytest.MonkeyPatch
) -> None:
    losgeschickt: list[Any] = []
    monkeypatch.setattr(master, "plan_mission",
                        lambda q, s, **kw: master.Mission(tasks=[], plan="", fallback=True))
    monkeypatch.setattr(Agent, "_legal_check",
                        lambda self, q: AgentResult(answer="abgelehnt", guarded="name"))
    monkeypatch.setattr(Agent, "_run_subagents", lambda self, tasks: losgeschickt.append(tasks))
    ergebnis = agent.ask("Finde die Adresse von Max Mustermann", stream=False)
    assert ergebnis.answer == "abgelehnt"
    assert losgeschickt == [], "abgelehnt heisst: kein Agent laeuft los"


def test_a_plan_for_another_question_is_not_reused(agent: Agent,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master, "plan_mission",
                        lambda q, s, **kw: master.Mission(tasks=[], plan="", fallback=True))
    agent._prefetch_plan("Frage A")
    assert agent._take_prefetched_plan("Frage B", max(1, agent.agent_limit),
                                       agent.strong_count) is None


def test_no_prefetch_outside_the_pro_mode(agent: Agent) -> None:
    agent.mode = "normal"
    agent._prefetch_plan("Vergleiche drei Lastenräder")
    assert agent._vorplan is None


def test_the_strongest_model_is_looked_up_once_even_in_parallel(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    aufrufe: list[int] = []

    def langsam(settings: Any, purpose: str = "work") -> str:
        aufrufe.append(1)
        time.sleep(0.2)
        return "mistral/mistral-large-latest"

    monkeypatch.setattr("aquaticy.system.strongest_model", langsam)
    a = Agent(settings, cache=None, toolbox=Toolbox(settings, cache=None))
    a.mode = "code"
    faeden = [threading.Thread(target=a._strongest_model) for _ in range(4)]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join()
    assert len(aufrufe) == 1


def test_the_workshop_is_not_warmed_without_isolation(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aquaticy.sandbox.find_runtime", lambda: None)
    a = Agent(settings, cache=None, toolbox=Toolbox(settings, cache=None))
    a._warm_workshop()      # ohne Podman/Docker: nichts, kein Fehler
    assert a.toolbox._sandbox_box is None
