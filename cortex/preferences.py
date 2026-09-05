"""Einstellungen, die Cortex auf Bitte selbst aendern darf.

"Mach den Hintergrund weiss", "such lieber auf Englisch", "nimm weniger
Subagenten" -- solche Bitten sollen im Gespraech erledigt sein und nicht in
einem Formular. Dieses Modul sagt, welche Einstellungen dafuer in Frage
kommen, wie ein Wert dafuer aussehen muss, und schreibt ihn weg.

**Die Grenze.** Aenderbar ist, was Geschmack, Sprache und Aufwand betrifft.
Nicht aenderbar ist alles, was Cortex mehr Zugriff auf fremdes Eigentum gaebe:

* API-Schluessel und Zugangstoken jeder Art,
* ob er im Haus schalten darf (``HA_CONTROL``),
* ob er Mail und Kalender lesen darf (``GOOGLE``),
* ob und wie er ins Lager schreiben darf (``STORAGE_ACCESS``),
* ob er ins Heimnetz sehen darf (``LAN_ENABLED``),
* ob er sich Dinge merken darf (``MEMORY``).

Das ist kein Misstrauen gegen das Modell, sondern eine Bauweise: Wer die
Rechte vergibt, darf nicht derselbe sein, der sie bekommt. Sonst waere die
dreistufige Rechteauswahl beim Lager eine Verabredung statt einer Grenze --
ein Satz im Chat wuerde genuegen, um sie aufzuheben. Diese Schalter bleiben
beim Menschen vor der Oberflaeche.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Aussehen ist Sache des Browsers und steht in keiner `.env` -- deshalb
#: dieser Sonderfall statt eines Schluessels.
APPEARANCE = "aussehen"

#: Dasselbe gilt fuer das Farbschema -- auch das kennt nur der Browser.
PALETTE = "farbschema"

#: Katalogname -> Name im Stilblock der Oberflaeche. Das Standardschema
#: traegt dort keinen eigenen Namen: es sind die Werte in ``:root``.
PALETTE_IDS: dict[str, str] = {
    "standard": "",
    "nord": "nord",
    "catppuccin": "catppuccin",
    "gruvbox": "gruvbox",
    "tokyo_night": "tokyonight",
    "solarized": "solarized",
    "dracula": "dracula",
    "rose_pine": "rosepine",
}


class NotChangeable(ValueError):
    """Diese Einstellung wird nicht aus dem Gespraech heraus geaendert."""


class BadValue(ValueError):
    """Der Wert passt nicht zu dieser Einstellung."""


@dataclass(frozen=True)
class Preference:
    """Eine Einstellung, die im Gespraech geaendert werden darf."""

    name: str
    key: str
    kind: str
    label: str
    choices: tuple[str, ...] = ()
    low: int = 0
    high: int = 0
    aliases: tuple[str, ...] = field(default_factory=tuple)


CATALOGUE: tuple[Preference, ...] = (
    Preference(
        APPEARANCE,
        "",
        "auswahl",
        "Erscheinungsbild",
        choices=("hell", "dunkel"),
        aliases=("hintergrund", "farbe", "theme", "design", "modus"),
    ),
    Preference(
        PALETTE,
        "",
        "auswahl",
        "Farbschema",
        choices=tuple(PALETTE_IDS),
        aliases=("farbschemata", "farben", "farbmuster", "farbkombination", "schema"),
    ),
    Preference(
        "ort",
        "CORTEX_LOCATION",
        "text",
        "Standard-Ort",
        aliases=("standort", "stadt", "ortsfilter"),
    ),
    Preference("sprache", "CORTEX_LANG", "text", "Sprache der Suche"),
    Preference("land", "CORTEX_COUNTRY", "text", "Land der Suche"),
    Preference(
        "suchmaschine",
        "CORTEX_SEARCH_BACKEND",
        "auswahl",
        "Suchmaschine",
        choices=("duckduckgo", "searxng", "brave", "tavily"),
    ),
    Preference(
        "formulierungen",
        "CORTEX_SEARCH_VARIANTS",
        "zahl",
        "Formulierungen je Suche",
        low=1,
        high=3,
    ),
    Preference(
        "subagenten",
        "CORTEX_SUBAGENTS_AUTO",
        "schalter",
        "Fragen automatisch aufteilen",
        aliases=("vorrecherche", "aufteilen"),
    ),
    Preference(
        "subagenten_anzahl",
        "CORTEX_MAX_SUBAGENTS",
        "zahl",
        "Teilfragen je Anfrage",
        low=0,
        high=24,
    ),
    Preference(
        "werkzeug_budget",
        "CORTEX_MAX_TOOL_CALLS",
        "zahl",
        "Werkzeug-Budget je Anfrage",
        low=1,
        high=60,
        aliases=("budget", "werkzeuge"),
    ),
    Preference(
        "kontextfenster",
        "CORTEX_CONTEXT_TOKENS",
        "zahl",
        "Kontextfenster lokaler Modelle",
        low=0,
        high=200_000,
    ),
    Preference(
        "browser_fallback",
        "CORTEX_ENABLE_PLAYWRIGHT",
        "schalter",
        "Seiten notfalls im Browser laden",
        aliases=("playwright",),
    ),
    Preference("modell", "CORTEX_MODEL", "modell", "Hauptmodell", aliases=("ki", "llm")),
)

#: Was ausdruecklich NICHT im Gespraech geaendert wird, mit dem Grund dazu.
#: Der Grund wandert in die Ablehnung -- eine blosse Absage waere nutzlos.
PROTECTED: dict[str, str] = {
    "CORTEX_HA_CONTROL": "ob ich im Haus schalten darf",
    "CORTEX_GOOGLE": "ob ich Mail und Kalender lesen darf",
    "CORTEX_STORAGE_ACCESS": "was ich im Lager darf",
    "CORTEX_STORAGE_URL": "welches Lager angebunden ist",
    "CORTEX_LAN_ENABLED": "ob ich ins Heimnetz sehen darf",
    "CORTEX_HA_URL": "welches Zuhause angebunden ist",
    "CORTEX_MEMORY": "ob ich mir Dinge merken darf",
}

_YES = frozenset({"an", "ein", "ja", "true", "1", "aktiv", "on", "yes"})
_NO = frozenset({"aus", "nein", "false", "0", "inaktiv", "off", "no"})

#: Woran ein heller bzw. dunkler Wunsch zu erkennen ist.
_LIGHT = frozenset({"hell", "weiss", "weiß", "light", "tag", "hellmodus"})
_DARK = frozenset({"dunkel", "schwarz", "dark", "nacht", "dunkelmodus"})


def find(name: str) -> Preference | None:
    """Sucht eine Einstellung ueber ihren Namen oder einen gelaeufigen Zweitnamen."""
    wanted = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not wanted:
        return None
    for preference in CATALOGUE:
        if wanted == preference.name or wanted in preference.aliases:
            return preference
    return None


def refusal(name: str) -> str:
    """Die Begruendung, wenn *name* absichtlich nicht aenderbar ist.

    Leerer String heisst: der Name ist einfach unbekannt, keine Grenze.
    """
    wanted = (name or "").strip().upper().replace(" ", "_").replace("-", "_")
    if not wanted.startswith("CORTEX_"):
        wanted = f"CORTEX_{wanted}"
    reason = PROTECTED.get(wanted)
    if reason:
        return (
            f"Das aendere ich nicht aus dem Gespraech heraus -- es entscheidet, "
            f"{reason}. Wer Rechte vergibt, darf nicht derselbe sein, der sie "
            f"bekommt. Das steht in den Einstellungen und gehoert dorthin."
        )
    if "KEY" in wanted or "TOKEN" in wanted or "SECRET" in wanted:
        return (
            "Zugangsdaten trage ich nicht aus dem Gespraech heraus ein. Sie "
            "gehoeren in die Einstellungen, wo sie niemand mitliest."
        )
    return ""


def coerce(preference: Preference, value: str) -> str:
    """Macht aus einer Nutzereingabe den Wert, der gespeichert wird.

    Raises:
        BadValue: Wenn der Wert nicht zu dieser Einstellung passt.
    """
    raw = str(value if value is not None else "").strip()

    if preference.name == APPEARANCE:
        low = raw.lower()
        if low in _LIGHT:
            return "hell"
        if low in _DARK:
            return "dunkel"
        raise BadValue("Fuer das Erscheinungsbild geht 'hell' oder 'dunkel'.")

    if preference.name == PALETTE:
        # "Tokyo Night", "rose-pine", "Rosé Pine" -- alles derselbe Wunsch.
        raw = (
            raw.lower()
            .replace("é", "e")
            .replace(" ", "_")
            .replace("-", "_")
        )
        if raw in ("gruen", "grün", "jetzig", "normal"):
            raw = "standard"

    if preference.kind == "schalter":
        low = raw.lower()
        if low in _YES:
            return "true"
        if low in _NO:
            return "false"
        raise BadValue(f"{preference.label}: 'an' oder 'aus'.")

    if preference.kind == "zahl":
        try:
            number = int(float(raw))
        except ValueError:
            raise BadValue(
                f"{preference.label}: eine Zahl zwischen {preference.low} und "
                f"{preference.high}."
            ) from None
        if not preference.low <= number <= preference.high:
            raise BadValue(
                f"{preference.label}: nur zwischen {preference.low} und {preference.high} "
                f"({number} liegt daneben)."
            )
        return str(number)

    if preference.kind == "auswahl":
        low = raw.lower()
        if low not in preference.choices:
            raise BadValue(
                f"{preference.label}: moeglich ist {', '.join(preference.choices)}."
            )
        return low

    if preference.kind == "modell":
        from cortex.config import model_problem
        from cortex.web import fix_model_id

        model = fix_model_id(raw)
        problem = model_problem(model)
        if problem:
            # Lieber gar nicht wechseln als auf ein Modell, das nicht antwortet.
            raise BadValue(problem.replace("\n", " "))
        return model

    if not raw:
        raise BadValue(f"{preference.label}: dafuer braucht es einen Wert.")
    return raw


def store(preference: Preference, value: str) -> Path:
    """Schreibt den Wert in die `.env` und laedt sie neu.

    Returns:
        Die Datei, in die geschrieben wurde.
    """
    from cortex.config import (
        DEFAULT_ENV_PATH,
        find_env_file,
        load_env,
        reset_settings_cache,
        write_env_file,
    )

    target = find_env_file() or DEFAULT_ENV_PATH
    written = write_env_file({preference.key: value}, target)
    # Ohne override gewaenne die schon gesetzte Umgebungsvariable, und die
    # Aenderung waere zwar in der Datei, aber erst nach einem Neustart wirksam.
    load_env(written, override=True)
    reset_settings_cache()
    return written


def catalogue_names() -> list[str]:
    """Die Namen, die das Modell benutzen darf."""
    return [preference.name for preference in CATALOGUE]
