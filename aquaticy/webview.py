"""Was die Oberflaeche anzeigt -- fertig gerechnet und formuliert auf dem Server.

Seit 9.5.14 zeichnet der Browser nur noch. Bis dahin stand in webui.html eine
ganze Reihe von Entscheidungen und Rechnungen, die dort nicht hingehoeren:

* welches Modell gerade *wirklich* antwortet (Code- und Pro-Modus nehmen das
  staerkste) und was in der Statuszeile steht,
* die Liste der Anbieter mit Schluesselnamen und Beispielmodell -- eine
  zweite Kopie dessen, was der Server ohnehin weiss,
* welcher Suchdienst welchen Schluessel braucht,
* wie ein Chat nach Datum einsortiert wird ("Heute", "Gestern" ...),
* Zeitangaben, Dateigroessen und die Beschreibung eines Auftrags,
* die Beschriftung von Rollen und Pruefurteilen,
* Vorschlaege und Begruessungssaetze.

All das steht jetzt hier; der Browser bekommt die fertigen Texte. Im Browser
bleibt nur, was dort laufen muss: zeichnen, auf Klicks reagieren, den
Antwortstrom live darstellen (Markdown), und die Tageszeit des Nutzers fuer
"Guten Morgen" -- die kennt nur sein Geraet.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
WOCHENTAGE_KURZ = ("Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So.")
MONATE_KURZ = ("Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.",
               "Okt.", "Nov.", "Dez.")

#: Die Anbieter im Einstellungsfenster: (Kennung, Name, Schluessel, Beispiel, Form).
PROVIDERS: tuple[tuple[str, str, str, str, str], ...] = (
    ("nvidia_nim", "NVIDIA NIM", "NVIDIA_NIM_API_KEY",
     "nvidia_nim/meta/llama-3.3-70b-instruct", "nvapi-…"),
    ("mistral", "Mistral", "MISTRAL_API_KEY", "mistral/mistral-large-latest", ""),
    ("ollama_chat", "Ollama (lokal)", "", "ollama_chat/gemma4:12b", ""),
)

#: Wie die Rollen der Agenten heissen (Schluessel wie in aquaticy/subagents.py).
ROLE_LABELS = {"zahlen": "Zahlen", "gegenstimmen": "Gegenstimmen", "frisch": "Aktuelles",
               "tiefe": "Spurensuche"}
#: Wie die Urteile der Pruefer heissen.
VERDICT_LABELS = {"BESTAETIGT": "bestätigt", "ABWEICHUNG": "Abweichung gefunden",
                  "UNKLAR": "nichts Belastbares gefunden"}

#: Vorschlaege auf der Startseite -- je Modus.
SUGGESTIONS: dict[str, tuple[str, ...]] = {
    "normal": (
        "Was ist heute in meiner Stadt los?", "Finde ein gutes Café mit WLAN in meiner Nähe",
        "Vergleiche drei Laptops für Bildbearbeitung", "Erkläre mir dieses Thema einfach",
        "Welche Geräte hängen in meinem Netz?", "Plane einen entspannten Tagesausflug",
        "Fasse die wichtigsten Nachrichten zusammen", "Hilf mir bei einer Kaufentscheidung",
        "Prüfe diese Behauptung mit mehreren Quellen",
    ),
    "code": (
        "Schreib ein Python-Skript, das Ordner nach Datum sortiert",
        "Warum wirft dieser Code einen IndexError?", "Baue eine kleine REST-API mit FastAPI",
        "Verbessere die Fehlermeldungen in diesem Programm", "Schreib Tests für diese Funktion",
        "Vereinfache diesen Code ohne sein Verhalten zu ändern",
        "Finde den Fehler in diesem Stacktrace", "Entwirf eine kleine Kommandozeilen-App",
        "Optimiere diese Datenbankabfrage",
    ),
}
#: Der zweite Teil der Begruessung ("Guten Morgen Anna — ...").
GREETINGS: dict[str, tuple[str, ...]] = {
    "normal": ("was möchtest du herausfinden?", "wobei kann ich dir helfen?",
               "was schauen wir uns heute an?"),
    "code": ("was wollen wir bauen?", "welches Problem lösen wir?",
             "woran programmieren wir heute?"),
}


def start_texts() -> dict[str, Any]:
    """Vorschlaege, Begruessungen und Beschriftungen -- einmal mit der Seite."""
    return {
        "suggestions": {mode: list(texte) for mode, texte in SUGGESTIONS.items()},
        "greetings": {mode: list(texte) for mode, texte in GREETINGS.items()},
        "roles": dict(ROLE_LABELS),
        "verdicts": dict(VERDICT_LABELS),
    }


# -- Zahlen und Zeiten ---------------------------------------------------------
def size_text(nbytes: Any) -> str:
    """1234567 -> "1,2 MB"."""
    try:
        n = max(0, int(nbytes or 0))
    except (TypeError, ValueError):
        n = 0
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}".replace(".", ",") + " MB"
    if n >= 1_000:
        return f"{round(n / 1e3)} kB"
    return f"{n} B"


def date_text(roh: Any) -> str:
    """Ein Datum aus dem Speicher ("2026-09-24 13:44:18", UTC) -> "24. Sept. 2026"."""
    text = str(roh or "").strip()
    if not text:
        return ""
    try:
        zeit = datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return text
    if zeit.tzinfo is None:
        zeit = zeit.replace(tzinfo=UTC)
    zeit = zeit.astimezone()
    return f"{zeit.day:02d}. {MONATE_KURZ[zeit.month - 1]} {zeit.year}"


def moment_text(stempel: Any) -> str:
    """Ein Zeitpunkt (Sekunden) -> "Do. 24. Sept., 14:05"."""
    try:
        wert = float(stempel or 0)
    except (TypeError, ValueError):
        return ""
    if wert <= 0:
        return ""
    zeit = datetime.fromtimestamp(wert)
    return (f"{WOCHENTAGE_KURZ[zeit.weekday()]} {zeit.day:02d}. "
            f"{MONATE_KURZ[zeit.month - 1]}, {zeit:%H:%M}")


def iso_moment_text(roh: Any) -> str:
    """Ein ISO-Zeitpunkt ("2026-09-24T13:44:18+00:00") -> "Do. 24. Sept., 15:44"."""
    try:
        zeit = datetime.fromisoformat(str(roh or "").strip())
    except ValueError:
        return ""
    if zeit.tzinfo is None:
        zeit = zeit.replace(tzinfo=UTC)
    return moment_text(zeit.timestamp())


def chat_group(touched: Any, now: float | None = None) -> str:
    """In welche Gruppe ein Chat in der Seitenleiste gehoert."""
    now = time.time() if now is None else now
    try:
        wann = float(touched or 0)
    except (TypeError, ValueError):
        wann = 0.0
    mitternacht = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0,
                                                      microsecond=0).timestamp()
    tag = 86400
    if wann >= mitternacht:
        return "Heute"
    if wann >= mitternacht - tag:
        return "Gestern"
    if wann >= now - 7 * tag:
        return "Letzte 7 Tage"
    if wann >= now - 30 * tag:
        return "Letzte 30 Tage"
    return "Älter"


def with_groups(chats: list[dict[str, Any]], now: float | None = None) -> list[dict[str, Any]]:
    """Haengt jedem Chat seine Gruppe an."""
    return [{**chat, "group": chat_group(chat.get("touched"), now)} for chat in chats]


# -- Auftraege -----------------------------------------------------------------
def _hhmm(stunde: Any, minute: Any) -> str:
    return f"{int(stunde or 0):02d}:{int(minute or 0):02d}"


def job_view(job: dict[str, Any]) -> dict[str, Any]:
    """Ein Auftrag mit fertigen Texten: was, wie oft, wann zuletzt und als naechstes."""
    art = {"visual": "Bildbeobachtung", "price": "Preisbeobachtung",
           "image": "Bildsuche"}.get(str(job.get("kind") or ""), "Recherche")
    minuten = {"minutes5": 5, "minutes15": 15, "minutes30": 30}.get(str(job.get("rhythm")))
    rhythmus = str(job.get("rhythm") or "")
    # "jede Minute" und "die ganze Zeit" fehlten hier bis 9.5.15 -- angezeigt
    # wurde dann "täglich um 08:00".
    if rhythmus == "always":
        takt = "die ganze Zeit"
    elif rhythmus == "minutes1":
        takt = "jede Minute"
    elif minuten:
        takt = f"alle {minuten} Minuten"
    elif rhythmus == "hourly":
        takt = f"stündlich zur Minute {int(job.get('minute') or 0):02d}"
    elif rhythmus == "weekly":
        tag = int(job.get("weekday") or 0)
        takt = (f"wöchentlich, {WOCHENTAGE[tag] if 0 <= tag < 7 else 'Montag'} um "
                f"{_hhmm(job.get('hour'), job.get('minute'))}")
    else:
        takt = f"täglich um {_hhmm(job.get('hour'), job.get('minute'))}"
    teile = [takt]
    teile.append(f"nächstes Mal {moment_text(job.get('next_run'))}" if job.get("enabled")
                 else "angehalten")
    if job.get("last_run"):
        teile.append(f"zuletzt {moment_text(job.get('last_run'))} — {job.get('last_state')}")
    return {**job, "title": f"{art}: {job.get('question', '')}", "when_text": " · ".join(teile),
            "toggle_label": "Anhalten" if job.get("enabled") else "Weiter"}


# -- Einstellungen ---------------------------------------------------------------
def provider_view() -> list[dict[str, str]]:
    """Die Anbieter mit dem Satz, der unter der Auswahl steht."""
    liste = []
    for kennung, name, schluessel, beispiel, form in PROVIDERS:
        if schluessel:
            hinweis = (f"{name} braucht einen Schlüssel ({schluessel}"
                       + (f", beginnt mit {form}" if form else "")
                       + "). Hast du einen eigenen, trag ihn gleich darunter bei "
                       "„Eigene Modelle“ ein — sonst gilt der gestellte, falls der "
                       "Betreiber einen hat.")
        else:
            hinweis = f"{name} läuft auf deinem Rechner. Kein Schlüssel nötig."
        liste.append({
            "id": kennung, "label": name, "key": schluessel, "example": beispiel,
            "key_label": f"({schluessel})" if schluessel else "(nicht nötig)",
            "placeholder": (f"{form}  (leer lassen = unverändert)" if form
                            else "leer lassen = unverändert"),
            "note": hinweis,
        })
    return liste


def search_key_hints(keys: dict[str, str]) -> dict[str, str]:
    """Welcher Suchdienst welchen Schluessel braucht -- als fertiger Hinweis."""
    mit = [name.title() for name, key in keys.items() if key]
    ohne = "nur bei " + " und ".join(mit) + " nötig" if mit else "nicht nötig"
    return {name: (f"({key})" if key else ohne) for name, key in keys.items()} | {"": ohne}


# -- Kopfzeile -------------------------------------------------------------------
def header_view(
    settings: Any,
    stand: dict[str, Any],
    *,
    strong_model: str = "",
    strong_code_model: str = "",
    ha_connected: bool = False,
    problems: list[str] | None = None,
) -> dict[str, Any]:
    """Was oben steht: das Modell, das wirklich antwortet, und die Statuszeile.

    Im Code- und im Pro-Modus antwortet das staerkste erreichbare Modell, sonst
    das eingestellte. *stand* ist der Zustand der Oberflaeche, wie ihn der
    Server fuehrt (aquaticy/uistate.py).
    """
    modus = str(stand.get("mode") or "normal")
    eingestellt = str(getattr(settings, "model", "") or "")
    if modus == "code":
        modell = strong_code_model or strong_model or eingestellt
    elif modus == "pro":
        modell = strong_model or eingestellt
    else:
        modell = eingestellt
    auto = bool(getattr(settings, "auto_model", False)) and modus == "normal"
    name = modell.split("/")[-1] if modell else ""
    teile = [str(getattr(settings, "location", "") or "") or "kein Ortsfilter"]
    if modus == "code" and stand.get("sandbox"):
        teile.append("Werkstatt")
    elif modus != "code" and stand.get("online") is False:
        teile.append("ohne Web")
    if modus != "code" and stand.get("visual_sources"):
        teile.append("Webcams & Satelliten")
    if modus == "pro":
        teile.append("Pro")
    if stand.get("structured"):
        teile.append("strukturiert")
    if stand.get("recheck"):
        teile.append("4 Prüfer" if modus == "pro" else "gegenprüfen")
    if str(stand.get("effort") or "medium") != "medium":
        teile.append(f"Denktiefe {stand.get('effort')}")
    if ha_connected:
        teile.append("Zuhause verbunden")
    if getattr(settings, "memory_enabled", False):
        teile.append("Speicher an")
    return {
        "model": modell,
        "model_label": (("Auto · " if auto else "") + name) if name else "Modell wählen",
        "status": " · ".join(teile),
        "status_title": "; ".join(problems or []),
    }


def export_filename(title: str, suffix: str = "md") -> str:
    """Ein Dateiname aus einem Titel -- nur Zeichen, die jedes Dateisystem mag."""
    import re
    import unicodedata

    roh = unicodedata.normalize("NFC", str(title or "aquaticy"))
    rein = re.sub(r"[^\w]+", "-", roh, flags=re.UNICODE).strip("-_")[:60].strip("-_")
    return f"{rein or 'aquaticy'}.{suffix}"


def picker_view(mode: str, models: list[dict[str, Any]],
                strong: list[dict[str, Any]], limited: bool = True) -> dict[str, Any]:
    """Die Modellauswahl je Modus: Ueberschrift, Liste und welches Feld sie setzt.

    Im Code- und im Pro-Modus antwortet das staerkste erreichbare Modell --
    also stehen dort nur die drei staerksten zur Wahl, und die Wahl setzt das
    starke Modell (AQUATICY_CODE_MODEL), nicht das allgemeine. Sonst haette
    sie beim naechsten Wechsel in den Standardmodus alles umgestellt.
    """
    nur_starke = mode in ("code", "pro")
    liste = list(strong if nur_starke else models)
    # Seit 9.5.14 Seashell: Modelle mit dem eigenen Schluessel des Kontos
    # stehen fuer sich -- sie sieht nur dieses Konto, und sie zaehlen nicht ins
    # Limit. Gruppiert wird nur, wenn es eigene gibt.
    eigene = [m for m in liste if m.get("source") == "own"]
    gruppen: list[dict[str, Any]] = [
        {"title": "Mit deinem Schlüssel",
         # Ohne Limit (Pro, lokal) gibt es nichts, worin es nicht zaehlte.
         "note": "zählt nicht in dein Limit" if limited else "auf deine Rechnung beim Anbieter",
         "models": eigene},
        {"title": "Gestellt", "note": "", "models": [m for m in liste if m.get("source") != "own"]},
    ] if eigene else []
    return {
        "heading": ("Stärkstes Code-Modell wählen" if mode == "code"
                    else "Stärkstes Modell wählen" if nur_starke else "Modell wählen"),
        "models": liste,
        "groups": [g for g in gruppen if g["models"]],
        "field": "AQUATICY_CODE_MODEL" if nur_starke else "AQUATICY_MODEL",
        "empty": ("Kein Modell gefunden. Trag unter Einstellungen → Modell einen eigenen "
                  "Schlüssel ein, oder installiere eins mit aquaticy install-model."),
    }
