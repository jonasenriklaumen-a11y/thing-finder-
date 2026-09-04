"""Tests fuer die Einstellungen, die Cortex im Gespraech aendern darf."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex import preferences
from cortex.preferences import BadValue


# ---------------------------------------------------------------------------
# Die Grenze
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    ["ha_control", "google", "storage_access", "storage_url", "lan_enabled", "memory"],
)
def test_rights_are_never_changed_from_the_chat(name: str) -> None:
    """Wer Rechte vergibt, darf nicht derselbe sein, der sie bekommt.

    Sonst waere die dreistufige Rechteauswahl beim Lager eine Verabredung
    statt einer Grenze -- ein Satz im Chat wuerde genuegen.
    """
    assert preferences.find(name) is None, f"{name} darf gar nicht erst im Katalog stehen"
    assert preferences.refusal(name), f"{name} braucht eine Begruendung"


def test_the_refusal_says_why_not_just_no() -> None:
    reason = preferences.refusal("storage_access")
    assert "was ich im Lager darf" in reason
    assert "Einstellungen" in reason


@pytest.mark.parametrize(
    "name", ["anthropic_api_key", "ha_token", "google_client_secret", "api_key"]
)
def test_credentials_are_never_entered_from_the_chat(name: str) -> None:
    assert preferences.find(name) is None
    assert "Zugangsdaten" in preferences.refusal(name)


def test_an_unknown_name_is_not_a_refusal() -> None:
    """Zwischen "gibt es nicht" und "darfst du nicht" ist ein Unterschied."""
    assert preferences.refusal("lieblingsfarbe") == ""


def test_nothing_protected_leaked_into_the_catalogue() -> None:
    keys = {preference.key for preference in preferences.CATALOGUE}
    assert not keys & set(preferences.PROTECTED)


# ---------------------------------------------------------------------------
# Namen und Zweitnamen
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "said", ["aussehen", "hintergrund", "Farbe", "theme", "design", " Modus "]
)
def test_the_appearance_is_found_under_several_names(said: str) -> None:
    """Niemand sagt "Erscheinungsbild" -- gesagt wird "mach den Hintergrund weiss"."""
    found = preferences.find(said)
    assert found is not None and found.name == preferences.APPEARANCE


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("weiß", "hell"),
        ("weiss", "hell"),
        ("hell", "hell"),
        ("light", "hell"),
        ("schwarz", "dunkel"),
        ("dark", "dunkel"),
        ("Nacht", "dunkel"),
    ],
)
def test_light_and_dark_are_understood(said: str, expected: str) -> None:
    assert preferences.coerce(preferences.find("hintergrund"), said) == expected


def test_a_nonsense_appearance_is_refused() -> None:
    with pytest.raises(BadValue, match=r"hell.*dunkel"):
        preferences.coerce(preferences.find("aussehen"), "kariert")


# ---------------------------------------------------------------------------
# Werte pruefen
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("said", ["an", "ja", "true", "EIN", "1"])
def test_switches_understand_yes(said: str) -> None:
    assert preferences.coerce(preferences.find("subagenten"), said) == "true"


@pytest.mark.parametrize("said", ["aus", "nein", "false", "0"])
def test_switches_understand_no(said: str) -> None:
    assert preferences.coerce(preferences.find("subagenten"), said) == "false"


def test_a_switch_refuses_anything_else() -> None:
    with pytest.raises(BadValue, match="'an' oder 'aus'"):
        preferences.coerce(preferences.find("subagenten"), "vielleicht")


def test_numbers_keep_to_their_range() -> None:
    """Ein Werkzeug-Budget von 5000 waere kein Wunsch, sondern ein Unfall."""
    budget = preferences.find("werkzeug_budget")
    assert preferences.coerce(budget, "30") == "30"
    with pytest.raises(BadValue, match="zwischen 1 und 60"):
        preferences.coerce(budget, "5000")
    with pytest.raises(BadValue, match="zwischen 1 und 60"):
        preferences.coerce(budget, "0")


def test_a_number_that_is_none_is_refused() -> None:
    with pytest.raises(BadValue, match="eine Zahl"):
        preferences.coerce(preferences.find("formulierungen"), "viele")


def test_a_choice_only_takes_what_exists() -> None:
    engine = preferences.find("suchmaschine")
    assert preferences.coerce(engine, "BRAVE") == "brave"
    with pytest.raises(BadValue, match="duckduckgo"):
        preferences.coerce(engine, "google")


def test_an_empty_text_is_refused() -> None:
    with pytest.raises(BadValue, match="einen Wert"):
        preferences.coerce(preferences.find("sprache"), "   ")


# ---------------------------------------------------------------------------
# Modelle
# ---------------------------------------------------------------------------
def test_a_model_gets_its_provider_prefix() -> None:
    """Wer die ID von der Anbieterseite kopiert, vergisst das Kuerzel."""
    assert preferences.coerce(preferences.find("modell"), "nvidia/nemotron-3-ultra-550b-a55b") == (
        "nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b"
    )


def test_a_broken_model_is_refused_before_it_breaks_the_chat() -> None:
    """Lieber gar nicht wechseln als auf ein Modell, das nicht antwortet."""
    with pytest.raises(BadValue, match="keinem Anbieter"):
        preferences.coerce(preferences.find("modell"), "voelliger-quatsch")


# ---------------------------------------------------------------------------
# Speichern
# ---------------------------------------------------------------------------
def test_storing_writes_the_env_and_takes_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / ".env"
    target.write_text("CORTEX_LOCATION=Bremen\n", encoding="utf-8")
    monkeypatch.setattr("cortex.config.find_env_file", lambda: target)
    monkeypatch.delenv("CORTEX_LOCATION", raising=False)

    written = preferences.store(preferences.find("ort"), "Hamburg")
    assert written == target
    assert "CORTEX_LOCATION=Hamburg" in target.read_text(encoding="utf-8")

    # Ohne override haette die alte Umgebungsvariable gewonnen und die
    # Aenderung waere erst nach einem Neustart wirksam geworden.
    import os

    assert os.environ["CORTEX_LOCATION"] == "Hamburg"


def test_every_catalogue_entry_has_a_key_except_the_appearance() -> None:
    """Das Aussehen lebt im Browser -- alles andere braucht einen .env-Schluessel."""
    for preference in preferences.CATALOGUE:
        if preference.name == preferences.APPEARANCE:
            assert preference.key == ""
        else:
            assert preference.key.startswith("CORTEX_"), preference.name
