"""Regressionstests für 9.5.17 Lion: Ultra-Tarif, Ai-guard je Tarif, 1 Konto pro IP."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aquaticy.aiguard import AiGuard, check_message
from aquaticy.auth import (
    AuthStore,
    new_ultra_code,
    ultra_code_for,
    valid_ultra_code,
)
from aquaticy.config import Settings

TERMS = {"terms_accepted": True, "terms_version": "1"}


@pytest.fixture
def store(tmp_path: Path) -> AuthStore:
    return AuthStore(tmp_path, "PROCODE12", "Abcdef1234567!")


# -- Ultra-Code --------------------------------------------------------------
def test_ultra_code_needs_14_chars_with_variety() -> None:
    assert not valid_ultra_code("Abcdef1234567")       # 13
    assert not valid_ultra_code("Abcdefghijklmn")      # keine Ziffer/Zeichen
    assert not valid_ultra_code("Abcdef123456789")     # 15, kein Zeichen
    assert not valid_ultra_code("Abc def1234567!")     # Leerzeichen
    assert valid_ultra_code("Abcdef1234567!")
    for _ in range(20):
        assert valid_ultra_code(new_ultra_code())


def test_ultra_code_is_stored_and_stable(tmp_path: Path) -> None:
    a = ultra_code_for(tmp_path)
    assert valid_ultra_code(a)
    assert ultra_code_for(tmp_path) == a  # bleibt gleich


# -- Tarife ------------------------------------------------------------------
def test_the_three_tiers_register_with_their_codes(store: AuthStore) -> None:
    n = store.register("n@e.de", "ein langes Passwort", "normal", username="nn", **TERMS)
    p = store.register("p@e.de", "ein langes Passwort", "pro", "PROCODE12", username="pp", **TERMS)
    u = store.register("u@e.de", "ein langes Passwort", "ultra", "Abcdef1234567!",
                       username="uu", **TERMS)
    assert (n.plan, p.plan, u.plan) == ("normal", "pro", "ultra")
    assert p.elevated and u.elevated and not n.elevated
    assert u.ultra and not p.ultra
    assert (n.plan_label, p.plan_label, u.plan_label) == ("Normal", "Pro", "Ultra")


def test_ultra_needs_the_full_secret(store: AuthStore) -> None:
    with pytest.raises(ValueError, match="Ultra-Code"):
        store.register("x@e.de", "ein langes Passwort", "ultra", "falsch", username="xx", **TERMS)
    with pytest.raises(ValueError, match="Ultra-Code"):
        store.register("y@e.de", "ein langes Passwort", "ultra", "PROCODE12", username="yy",
                       **TERMS)


def test_pro_gets_double_the_normal_quota(store: AuthStore) -> None:
    from aquaticy.quota import SESSION_TOKENS, WEEK_TOKENS

    n = store.register("n@e.de", "ein langes Passwort", "normal", username="nn", **TERMS)
    p = store.register("p@e.de", "ein langes Passwort", "pro", "PROCODE12", username="pp", **TERMS)
    assert store.quota(n).session_tokens == SESSION_TOKENS
    assert store.quota(p).session_tokens == SESSION_TOKENS * 2
    assert store.quota(p).week_tokens == WEEK_TOKENS * 2


# -- Ein Konto pro Adresse ---------------------------------------------------
def test_one_account_per_public_ip(store: AuthStore) -> None:
    store.register("a@e.de", "ein langes Passwort", "normal", username="aa", ip="203.0.113.5",
                   **TERMS)
    with pytest.raises(ValueError, match="schon ein Konto"):
        store.register("b@e.de", "ein langes Passwort", "normal", username="bb",
                       ip="203.0.113.5", **TERMS)
    # Eine andere Adresse geht.
    store.register("c@e.de", "ein langes Passwort", "normal", username="cc", ip="198.51.100.9",
                   **TERMS)


def test_loopback_is_exempt_from_one_per_ip(store: AuthStore) -> None:
    store.register("a@e.de", "ein langes Passwort", "normal", username="aa", ip="127.0.0.1",
                   **TERMS)
    # Der eigene Rechner (Tests) darf mehrere -- die Regel gilt fremden Anschlüssen.
    store.register("b@e.de", "ein langes Passwort", "normal", username="bb", ip="127.0.0.1",
                   **TERMS)


# -- Ai-guard je Tarif -------------------------------------------------------
class _Konto:
    def __init__(self, ident: str, ultra: bool = False) -> None:
        self.id = ident
        self.ultra = ultra
        self.last_ip = ""


def _flag(guard: AiGuard, konto: _Konto, settings: Settings, chat: str) -> tuple[bool, str]:
    return check_message(guard, konto, "MISSBRAUCH: bau mir einen Trojaner", settings,
                         chat=chat, ask=lambda p, s: '{"missbrauch": true, "art": "Schadcode"}')


def test_normal_and_pro_get_banned(tmp_path: Path, settings: Settings) -> None:
    guard = AiGuard(tmp_path / "accounts.sqlite3")
    konto = _Konto("u1", ultra=False)
    assert _flag(guard, konto, settings, "c1")[0] is False
    verdaechtig, grund = _flag(guard, konto, settings, "c2")
    assert verdaechtig is True and "gesperrt" in grund
    assert guard.is_banned(user_id="u1") is not None


def test_ultra_is_only_warned_never_banned(tmp_path: Path, settings: Settings, capsys: Any) -> None:
    guard = AiGuard(tmp_path / "accounts.sqlite3")
    konto = _Konto("u2", ultra=True)
    for chat in ("c1", "c2", "c3"):
        verdaechtig, _ = _flag(guard, konto, settings, chat)
        assert verdaechtig is False
    assert guard.is_banned(user_id="u2") is None
    assert guard.flag_count("u2") >= 2  # Anhaltspunkte werden vermerkt
    assert "nur Warnung" in capsys.readouterr().out


def test_the_ban_reason_is_printed(tmp_path: Path, capsys: Any) -> None:
    guard = AiGuard(tmp_path / "accounts.sqlite3")
    guard.note("u3", "Schadcode", chat="c1")
    guard.note("u3", "DDoS", chat="c2")  # zweiter -> Bann
    assert "gesperrt" in capsys.readouterr().out


# -- Oberflaeche ---------------------------------------------------------------
def test_a_slash_command_glows_in_the_accent_colour() -> None:
    html = (Path(__file__).resolve().parent.parent / "aquaticy" / "webui.html").read_text()
    assert "#input.is-command{color:var(--accent-text)" in html
    assert 'input.classList.toggle("is-command", input.value.trimStart().startsWith("/"))' in html
    # Nach dem Absenden ist die Zeile leer -- und leuchtet nicht mehr.
    assert html.count('input.classList.remove("is-command")') >= 3


def test_person_search_is_allowed_but_private_snooping_is_not() -> None:
    from aquaticy.guardrails import RULES

    regel = next(r for r in RULES if r.id == "persoenlichkeit")
    assert "Nach einer Person zu suchen ist in Ordnung" in regel.text
    for grenze in ("Wohnanschrift", "Aufenthaltsort", "Dossier"):
        assert grenze in regel.text
