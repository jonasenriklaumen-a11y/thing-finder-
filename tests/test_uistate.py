"""Tests fuer den Zustand der Oberflaeche.

Der Punkt dieser Datei ist ein einziger Satz: **was aus dem Browser kommt,
ist ein Vorschlag.** Jeder Test hier prueft eine Art, wie ein Vorschlag
falsch sein kann -- und dass dann trotzdem ein brauchbarer Zustand
herauskommt statt eines halben.
"""

from __future__ import annotations

from pathlib import Path

from aquaticy.uistate import (
    EFFORTS,
    FIELDS,
    MODES,
    PALETTES,
    THEMES,
    UIState,
    clean,
    clean_flag,
    defaults,
)


def test_the_default_state_is_complete() -> None:
    stand = defaults()
    assert set(stand) == set(FIELDS)
    assert stand["mode"] == "normal"
    assert stand["effort"] == "medium"
    assert stand["online"] is True
    assert stand["agents"] == 12
    # Alles, was Zeit kostet, ist aus, bis es jemand einschaltet.
    for aus in ("structured", "recheck", "sandbox", "denken", "tracing", "load"):
        assert stand[aus] is False


def test_the_string_false_is_not_true() -> None:
    """`bool("false")` ist wahr -- genau daran schaltet sich ein Schalter ein."""
    assert bool("false") is True, "die Falle, um die es geht"
    assert clean_flag("false", True) is False
    assert clean_flag("0", True) is False
    assert clean_flag("", True) is False
    assert clean_flag("true", False) is True
    assert clean_flag(True, False) is True
    assert clean_flag(1, False) is True
    assert clean_flag(0, True) is False


def test_something_unclear_keeps_the_previous_value() -> None:
    """Raten waere teuer: ein missverstandenes „aus" startet eine Recherche."""
    assert clean_flag("vielleicht", True) is True
    assert clean_flag("vielleicht", False) is False
    assert clean_flag(None, True) is False, "nichts geschickt heisst nein"


def test_an_unknown_mode_does_not_get_through() -> None:
    stand = clean({"mode": "rm -rf", "effort": "maximum", "theme": "neon"})
    assert stand["mode"] == "normal"
    assert stand["effort"] == "medium"
    assert stand["theme"] == "system"


def test_unknown_fields_are_dropped() -> None:
    """Was hier nicht steht, gibt es nicht -- auch nicht als Beifang."""
    stand = clean({"mode": "code", "admin": True, "AQUATICY_HA_CONTROL": "true"})
    assert stand["mode"] == "code"
    assert "admin" not in stand
    assert "AQUATICY_HA_CONTROL" not in stand


def test_a_partial_suggestion_keeps_the_rest() -> None:
    vorher = clean({"mode": "code", "effort": "high", "recheck": True})
    nachher = clean({"mode": "normal"}, base=vorher)
    assert nachher["mode"] == "normal"
    assert nachher["effort"] == "high", "unerwaehnt heisst unveraendert"
    assert nachher["recheck"] is True


def test_nonsense_instead_of_an_object_is_survivable() -> None:
    for muell in (None, [], "kaputt", 42):
        assert clean(muell) == defaults()


def test_every_allowed_value_really_passes() -> None:
    for theme in THEMES:
        assert clean({"theme": theme})["theme"] == theme
    for mode in MODES:
        assert clean({"mode": mode})["mode"] == mode
    for effort in EFFORTS:
        assert clean({"effort": effort})["effort"] == effort
    for palette in PALETTES:
        assert clean({"palette": palette})["palette"] == palette
    assert clean({"agents": 1})["agents"] == 1
    assert clean({"agents": "50"})["agents"] == 50
    assert clean({"agents": 0})["agents"] == 12
    assert clean({"agents": 51})["agents"] == 12


def test_the_state_survives_a_restart(tmp_path: Path) -> None:
    zustand = UIState(tmp_path / "u.db")
    zustand.write({"theme": "dark", "mode": "code", "effort": "high"})
    frisch = UIState(tmp_path / "u.db")
    assert frisch.read()["theme"] == "dark"
    assert frisch.read()["mode"] == "code"
    assert frisch.read()["effort"] == "high"


def test_writing_returns_what_actually_applies(tmp_path: Path) -> None:
    """Nicht was geschickt wurde -- was gilt. Sonst zeigt die Oberflaeche
    einen Zustand an, den der Server gar nicht hat."""
    zustand = UIState(tmp_path / "u.db")
    antwort = zustand.write({"mode": "quatsch", "effort": "high"})
    assert antwort["mode"] == "normal"
    assert antwort["effort"] == "high"
    assert antwort == zustand.read()


def test_a_damaged_row_does_not_break_the_page(tmp_path: Path) -> None:
    """Eine von Hand veraenderte Datenbank ist kein Grund fuer eine
    kaputte Oberflaeche."""
    zustand = UIState(tmp_path / "u.db")
    zustand.write({"theme": "dark"})
    with zustand._connect() as conn:
        conn.execute("UPDATE uistate SET state = 'kein json' WHERE id = 1")
    assert zustand.read() == defaults()

    with zustand._connect() as conn:
        conn.execute("""UPDATE uistate SET state = '{"mode": 17}' WHERE id = 1""")
    assert zustand.read()["mode"] == "normal"


def test_there_is_only_ever_one_row(tmp_path: Path) -> None:
    zustand = UIState(tmp_path / "u.db")
    for theme in ("dark", "light", "system", "dark"):
        zustand.write({"theme": theme})
    with zustand._connect() as conn:
        (anzahl,) = conn.execute("SELECT COUNT(*) FROM uistate").fetchone()
    assert anzahl == 1


def test_resetting_goes_back_to_the_start(tmp_path: Path) -> None:
    zustand = UIState(tmp_path / "u.db")
    zustand.write({"theme": "dark", "mode": "code"})
    assert zustand.reset() == defaults()
    assert zustand.read() == defaults()


def test_the_modes_are_the_same_on_both_sides() -> None:
    """Zwei Listen, eine Wahrheit: was der Agent kennt, muss die Oberflaeche
    durchlassen -- und umgekehrt. Sonst faellt ein gueltiger Modus hier auf
    "normal" zurueck, ohne dass es jemandem auffiele."""
    from aquaticy.agent import MODES as AGENT_MODES

    assert set(MODES) == set(AGENT_MODES)
    assert "pro" in MODES


def test_the_pro_mode_gets_through(tmp_path: Path) -> None:
    zustand = UIState(tmp_path / "u.db")
    assert zustand.write({"mode": "pro"})["mode"] == "pro"
    assert zustand.read()["mode"] == "pro"
