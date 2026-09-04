"""Die zwei Werkzeuge des Agenten: `web_search` und `fetch_page`.

Mehr braucht es nicht. Instagram, Amazon, Branchenbuch oder Ladenwebsite --
alles sind einfach Suchtreffer, die gelesen werden koennen. Es gibt bewusst
keine plattformspezifischen Scraper.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cortex.cache import Cache, cache_key
from cortex.config import Settings
from cortex.extract import extract_product, has_spec_heading
from cortex.fetch import Fetcher, load_rules
from cortex.models import PageResult, Product, SearchResult, domain_of
from cortex.queries import MAX_VARIANTS, variants
from cortex.search import (
    OPEN_BACKEND_NAMES,
    SearchError,
    search_broadly,
    search_news,
    search_web,
)

#: Callback fuer die Live-Anzeige: (event, payload)
EventHook = Callable[[str, dict[str, Any]], None]
#: LLM-Fallback fuer Specs: (seitentext, url) -> {"CPU": "...", ...}
SpecExtractor = Callable[[str, str], dict[str, str]]
#: Rueckfrage an den Nutzer: (frage, moeglichkeiten) -> antwort ("" = keine)
AskHandler = Callable[[str, list[str]], str]

#: So oft darf der Agent je Anfrage nachfragen. Wer dreimal fragt, hat die
#: Anfrage nicht verstanden -- dann ist eine begruendete Annahme besser.
MAX_QUESTIONS = 2

#: Drittes Werkzeug, das nur der Hauptagent bekommt -- Subagenten duerfen
#: keine weiteren Subagenten starten.
SUBAGENT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "research_subtasks",
        "description": (
            "Gibt mehrere unabhaengige Teilfragen an Rechercheassistenten ab, die "
            "parallel suchen und lesen. Nutze das, wenn die Anfrage in Teile zerfaellt, "
            "die sich getrennt beantworten lassen -- etwa mehrere Kandidaten, mehrere "
            "Orte oder mehrere Kriterien. Jede Teilfrage muss fuer sich verstaendlich "
            "sein und den noetigen Zusammenhang selbst mitbringen (Ort, Produkt, "
            "Kriterium). Du bekommst je Teilfrage eine Zusammenfassung mit Quellen "
            "zurueck und fasst daraus die Antwort zusammen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Zwei bis vier eigenstaendige Teilfragen, jeweils ein ganzer Satz."
                    ),
                }
            },
            "required": ["tasks"],
        },
    },
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Sucht im Web und liefert Titel, URL und Snippet je Treffer. "
                "Du kannst in EINEM Aufruf mehrere Formulierungen derselben Frage "
                "mitgeben (Feld 'queries'); die Trefferlisten werden zusammengefuehrt, "
                "und was mehrere Anfragen uebereinstimmend oben haben, steht vorn. "
                "Das kostet nur einen Werkzeugaufruf statt drei. "
                "Schreibe kurze Stichwortanfragen aus drei bis sechs Woertern, keine "
                "ganzen Fragesaetze, und lass das Hauptthema in jeder Variante stehen. "
                "Anfuehrungszeichen erzwingen die genaue Wortfolge, 'site:domain.de' "
                "beschraenkt auf eine Seite. Baue Ort, Stadt oder Region direkt ein, "
                "wenn der Nutzer einen Ort genannt hat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Die Suchanfrage."},
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Weitere Formulierungen derselben Frage (hoechstens zwei "
                            "zusaetzliche). Andere Woerter, anderer Blickwinkel -- nicht "
                            "dieselbe Anfrage zweimal."
                        ),
                    },
                    "count": {
                        "type": "integer",
                        "description": (
                            "Gewuenschte Trefferzahl (1-20). Fuer eine einzelne Tatsache "
                            "reichen 5, zum Vergleichen mehrerer Quellen nimm 10."
                        ),
                    },
                    "country": {
                        "type": "string",
                        "description": "ISO-Laendercode fuer den Ortsfilter, z.B. 'de'.",
                    },
                    "lang": {
                        "type": "string",
                        "description": "ISO-Sprachcode der Ergebnisse, z.B. 'de'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Laedt eine Seite und gibt den lesbaren Textinhalt zurueck (Navigation, Werbung "
                "und Cookie-Banner sind entfernt). Bei Produktseiten kommen zusaetzlich "
                "strukturierte Daten (Name, Bild-URL, Preis, Specs) zurueck. "
                "Seiten hinter Paywall, Login oder Captcha werden uebersprungen und mit einem "
                "Grund gemeldet -- dann nimm einfach eine andere Quelle."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Vollstaendige http(s)-URL."},
                },
                "required": ["url"],
            },
        },
    },
]



NEWS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_news",
        "description": (
            "Sucht in aktuellen Nachrichten -- fuer Ereignisse, Ankuendigungen und alles, "
            "wo das Datum zaehlt. Jeder Treffer traegt Datum und Quelle im Snippet. "
            "Fuer zeitlose Fakten nimm weiter web_search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Die Suchanfrage."},
                "count": {"type": "integer", "description": "Trefferzahl (1-20, Default 8)."},
            },
            "required": ["query"],
        },
    },
}

CALC_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": (
            "Rechnet einen arithmetischen Ausdruck exakt aus -- Preise pro Einheit, "
            "Durchschnitte, Rabatte, Umrechnungen. Erlaubt: Zahlen (auch 1.099,99), "
            "+ - * / // % ** und Klammern. Rechne NIE selbst im Kopf, benutze dieses "
            "Werkzeug."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Der Ausdruck, z.B. '(1099 + 1149) / 2'.",
                }
            },
            "required": ["expression"],
        },
    },
}

#: Etwas fuer spaeter behalten. Der Merkzettel ist fuer ausdrueckliche
#: Bitten des Nutzers; der Speicher fuer alles, was der Agent selbst als
#: wichtig erkennt.
MEMORY_WRITE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "save_memory",
        "description": (
            "Legt eine Notiz im Langzeitspeicher ab, die in spaeteren Gespraechen wieder "
            "auftaucht. Nimm es fuer Dinge, die laenger gelten: Vorlieben, Wohnort, "
            "Ausstattung, laufende Vorhaben, wichtige Ergebnisse einer Recherche. Nicht "
            "fuer Belangloses und nicht fuer etwas, das gleich wieder veraltet ist. "
            "Schreib in ganzen Saetzen, damit die Notiz spaeter allein verstaendlich ist. "
            "Nur Text -- Bilder und Dateien gehoeren nicht hierher."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Die Notiz, ein bis drei Saetze, aus sich heraus verstaendlich.",
                },
                "topic": {
                    "type": "string",
                    "description": "Ein Stichwort zum Wiederfinden, z.B. 'Wohnort' oder 'Laptop'.",
                },
            },
            "required": ["text"],
        },
    },
}

#: Im Speicher nachsehen.
MEMORY_READ_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "recall_memory",
        "description": (
            "Sieht im Langzeitspeicher nach, was du frueher ueber den Nutzer oder ein "
            "Thema festgehalten hast. Nimm es GLEICH ZU BEGINN, wenn die Frage von "
            "persoenlichen Umstaenden abhaengt -- Ort, Ausstattung, Vorlieben, frueher "
            "Besprochenes. Lieber einmal zu viel nachsehen als den Nutzer wiederholt "
            "dasselbe fragen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Wonach du suchst. Leer lassen zeigt die juengsten Notizen."
                    ),
                }
            },
        },
    },
}

#: Das eigene Netz ansehen. Kein Ersatz fuer die Websuche, sondern die Antwort
#: auf Fragen, die im Web gar nicht stehen koennen.
LAN_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "lan_scan",
        "description": (
            "Sieht nach, welche Geraete im eigenen Heimnetz erreichbar sind, und was auf "
            "ihnen laeuft (Weboberflaeche, Drucker, SSH, Home Assistant ...). Benutze es "
            "fuer Fragen wie 'welche Geraete haengen bei mir im Netz', 'laeuft mein "
            "Drucker', 'auf welcher Adresse ist X'. Der Durchlauf dauert einige Sekunden "
            "-- rufe ihn hoechstens einmal je Anfrage auf und arbeite dann mit dem "
            "Ergebnis weiter."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subnet": {
                    "type": "string",
                    "description": (
                        "Netz wie '192.168.1.0/24'. Leer lassen -- dann nimmt Cortex AI das "
                        "eigene. Nur private Netze sind erlaubt."
                    ),
                },
                "thorough": {
                    "type": "boolean",
                    "description": (
                        "Alle bekannten Ports statt der zwoelf haeufigsten. Deutlich "
                        "langsamer, nur wenn der schnelle Durchlauf nichts gefunden hat."
                    ),
                },
            },
        },
    },
}

#: Ein einzelnes Geraet gezielt pruefen -- schneller als das ganze Netz.
LAN_HOST_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "lan_check",
        "description": (
            "Prueft EIN Geraet im Heimnetz: antwortet es, und welche Dienste laufen "
            "darauf? Schneller als lan_scan. Nimm es, wenn die Adresse oder der Name "
            "schon bekannt ist ('ist 192.168.1.50 noch da', 'laeuft der Drucker')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "Adresse oder Name im Netz, z.B. 192.168.1.50 oder nas.local",
                }
            },
            "required": ["host"],
        },
    },
}

#: Home Assistant lesen.
HA_STATES_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ha_states",
        "description": (
            "Liest Zustaende aus Home Assistant: Temperaturen, Lichter, Schalter, "
            "Sensoren, Anwesenheit. Fuer alle Fragen ueber das eigene Haus ('wie warm "
            "ist es im Wohnzimmer', 'ist das Licht an', 'steht die Waschmaschine'). "
            "Ohne Angaben kommt eine Uebersicht der Bereiche -- damit findest du "
            "zuerst heraus, was es ueberhaupt gibt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Suchwort im Namen oder in der Kennung, z.B. 'wohnzimmer'.",
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "Bereich wie light, switch, sensor, binary_sensor, climate, "
                        "cover, lock, media_player, person."
                    ),
                },
            },
        },
    },
}

#: Home Assistant schalten -- nur wenn ausdruecklich erlaubt.
HA_CALL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ha_call",
        "description": (
            "Schaltet etwas in Home Assistant, z.B. domain='light', service='turn_on', "
            "entity_id='light.wohnzimmer'. Nimm vorher ha_states, um die genaue Kennung "
            "zu erfahren -- rate sie nie. Bei Schloessern, Alarmanlagen, Toren und "
            "Heizungen wird der Nutzer zusaetzlich gefragt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Bereich, z.B. light oder switch."},
                "service": {
                    "type": "string",
                    "description": "Dienst, z.B. turn_on, turn_off, toggle.",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Genaue Kennung aus ha_states, z.B. light.kueche.",
                },
                "data": {
                    "type": "object",
                    "description": "Weitere Angaben, z.B. {\"brightness_pct\": 40}.",
                },
            },
            "required": ["domain", "service"],
        },
    },
}

#: Nur fuer den Hauptagenten: ein Subagent sitzt niemandem gegenueber, den er
#: fragen koennte.
ASK_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Stellt dem Nutzer EINE Rueckfrage und wartet auf seine Antwort. Benutze "
            "das nur, wenn die Anfrage ohne die Antwort in eine ganz andere Richtung "
            "laufen koennte -- wenn also Budget, Ort, Zweck, Zeitraum oder das gemeinte "
            "Produkt offen sind und die moeglichen Antworten zu voellig verschiedenen "
            "Ergebnissen fuehren. Frag NICHT nach Kleinigkeiten, nicht um dich "
            "abzusichern und nicht nach etwas, das du selbst herausfinden kannst: dann "
            "triff lieber die naheliegende Annahme, sag sie dazu und arbeite weiter. "
            "Frag am besten VOR der Recherche, nicht mittendrin, und hoechstens zweimal "
            "je Anfrage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Die Rueckfrage, ein einzelner klarer Satz.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Zwei bis vier Antwortmoeglichkeiten zum Anklicken, jeweils "
                        "wenige Woerter. Nur angeben, wenn es wirklich abgrenzbare "
                        "Moeglichkeiten gibt -- sonst weglassen."
                    ),
                },
            },
            "required": ["question"],
        },
    },
}

#: Nur fuer den Hauptagenten -- Subagenten sollen keine Notizen anlegen.
MEMORY_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": (
            "Schreibt eine kurze Notiz auf den dauerhaften Merkzettel des Nutzers -- er "
            "gilt ueber Sitzungen hinweg. Benutze es NUR, wenn der Nutzer ausdruecklich "
            "darum bittet (\"merk dir\", \"notier dir\"), nie von dir aus."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Die Notiz, ein bis zwei Saetze."}
            },
            "required": ["text"],
        },
    },
}


# News-Suche und Rechner gehoeren zum Kern -- auch Subagenten sollen aktuelle
# Meldungen finden und richtig rechnen koennen.
# ---------------------------------------------------------------------------
# Gmail und Kalender -- nur lesen, und nur wenn eingeschaltet
# ---------------------------------------------------------------------------
CALENDAR_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "calendar_events",
        "description": (
            "Liest Termine aus dem Google Kalender des Nutzers -- nur lesend, "
            "es wird nie etwas eingetragen oder geaendert. Nimm das fuer Fragen "
            "nach Terminen ('was habe ich morgen vor', 'wann ist der Zahnarzt') "
            "und um Zeitangaben einzuordnen, bevor du im Web suchst."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Wie viele Tage im Voraus (1-365, Default 7).",
                },
                "query": {
                    "type": "string",
                    "description": "Optionaler Suchbegriff im Titel oder Ort.",
                },
                "count": {"type": "integer", "description": "Hoechstzahl Termine (1-20)."},
            },
        },
    },
}

MAIL_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "mail_search",
        "description": (
            "Sucht in den Mails des Nutzers und liefert Absender, Betreff, Datum "
            "und die ersten Zeilen -- nur lesend, es wird nie etwas verschickt, "
            "beantwortet oder geloescht. Die Anfrage nutzt die Gmail-Syntax: "
            "'from:dhl', 'subject:Rechnung', 'newer_than:7d', 'is:unread', "
            "'has:attachment'. Fuer den vollen Text danach mail_read benutzen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail-Suchanfrage."},
                "count": {"type": "integer", "description": "Hoechstzahl Mails (1-20)."},
            },
        },
    },
}

MAIL_READ_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "mail_read",
        "description": (
            "Liest den Text einer einzelnen Mail. Die Kennung kommt aus "
            "mail_search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Kennung aus mail_search."},
            },
            "required": ["message_id"],
        },
    },
}


# ---------------------------------------------------------------------------
# Lagerverwaltung -- Raum > Moebel > Artikel
# ---------------------------------------------------------------------------
STORAGE_FIND_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "storage_find",
        "description": (
            "Sucht im Lager des Nutzers nach einem Artikel -- ueber die Nummer "
            "('B42', auch Teiltreffer wie 'B4') oder den Namen. Jeder Treffer nennt "
            "den vollen Ort: Raum, Moebel, Artikelnummer und Bestand. Nimm das fuer "
            "Fragen wie 'wo liegt das Ladekabel' oder 'wie viele Schrauben habe ich'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Artikelnummer oder Name."},
                "limit": {"type": "integer", "description": "Hoechstzahl Treffer (1-50)."},
            },
            "required": ["query"],
        },
    },
}

STORAGE_BROWSE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "storage_browse",
        "description": (
            "Sieht sich das Lager an, ohne zu suchen. Ohne Angabe kommen die Raeume "
            "mit ihren Kennzahlen; mit 'room_id' die Moebel darin; mit "
            "'furniture_id' die Artikel darin. So findest du die Kennungen, die du "
            "zum Anlegen oder Aendern brauchst -- rate sie nie."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "room_id": {"type": "integer", "description": "Kennung eines Raums."},
                "furniture_id": {"type": "integer", "description": "Kennung eines Moebels."},
            },
        },
    },
}

STORAGE_ADD_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "storage_add",
        "description": (
            "Legt etwas im Lager an: einen Artikel in einem Moebel (mit "
            "'furniture_id'), ein Moebel in einem Raum (mit 'room_id'), oder einen "
            "Raum (nur mit 'name'). Die Artikelnummer vergibt der Server, nie du. "
            "Hol dir die Kennungen vorher mit storage_browse."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name des neuen Eintrags."},
                "furniture_id": {
                    "type": "integer",
                    "description": "In dieses Moebel -- ergibt einen Artikel.",
                },
                "room_id": {
                    "type": "integer",
                    "description": "In diesen Raum -- ergibt ein Moebel.",
                },
                "quantity": {"type": "integer", "description": "Anfangsbestand (Default 1)."},
            },
            "required": ["name"],
        },
    },
}

STORAGE_EDIT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "storage_edit",
        "description": (
            "Aendert einen vorhandenen Artikel: 'name' benennt ihn um, 'quantity' "
            "setzt den Bestand auf einen Wert, 'delta' zaehlt hoch oder runter "
            "(-1 = einer entnommen). Nimm 'delta', wenn etwas dazukommt oder "
            "weggeht, und 'quantity' nur beim Nachzaehlen -- sonst ueberschreibst "
            "du, was jemand anderes gerade geaendert hat. Geloescht wird nie."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer", "description": "Kennung aus storage_find."},
                "name": {"type": "string", "description": "Neuer Name."},
                "quantity": {"type": "integer", "description": "Bestand auf diesen Wert setzen."},
                "delta": {"type": "integer", "description": "Bestand um diesen Wert aendern."},
            },
            "required": ["item_id"],
        },
    },
}


# ---------------------------------------------------------------------------
# Einstellungen aus dem Gespraech heraus
# ---------------------------------------------------------------------------
SETTING_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "change_setting",
        "description": (
            "Aendert eine Einstellung, wenn der Nutzer darum bittet -- etwa 'mach den "
            "Hintergrund weiss', 'such lieber auf Englisch', 'nimm weniger Teilfragen'. "
            "Moegliche Namen: aussehen (hell/dunkel), ort, sprache, land, suchmaschine, "
            "formulierungen, subagenten (an/aus), subagenten_anzahl, werkzeug_budget, "
            "kontextfenster, browser_fallback (an/aus), modell. "
            "NICHT aenderbar sind Zugangsdaten und alles, was mir mehr Zugriff gaebe: "
            "Schalten im Haus, Mail und Kalender, Schreibrechte im Lager, Netzzugriff, "
            "Gedaechtnis. Frag danach gar nicht erst -- verweise auf die Einstellungen. "
            "Aendere nur, worum ausdruecklich gebeten wurde, eine Sache je Aufruf, und "
            "sag hinterher in einem Satz, was jetzt gilt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "setting": {"type": "string", "description": "Name der Einstellung."},
                "value": {"type": "string", "description": "Der neue Wert."},
            },
            "required": ["setting", "value"],
        },
    },
}


TOOL_SCHEMAS.extend([NEWS_SCHEMA, CALC_SCHEMA])


@dataclass
class ToolStats:
    """Was in einem Durchlauf passiert ist -- fuer Ausgabe und Verlauf."""

    searches: list[str] = field(default_factory=list)
    news_searches: list[str] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    products: list[Product] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    calculations: int = 0
    notes_saved: int = 0
    #: Rueckfragen zaehlen NICHT als Werkzeugaufruf -- eine Nachfrage soll den
    #: Rechercheetat nicht schmaelern.
    questions: int = 0
    memories: int = 0
    lan_scans: int = 0
    ha_reads: int = 0
    ha_calls: int = 0
    google_reads: int = 0
    storage_reads: int = 0
    storage_writes: int = 0
    settings_changed: int = 0

    @property
    def tool_calls(self) -> int:
        return (
            len(self.searches)
            + len(self.news_searches)
            + len(self.fetched)
            + len(self.skipped)
            + self.calculations
            + self.notes_saved
            + self.memories
            + self.lan_scans
            + self.ha_reads
            + self.ha_calls
            + self.google_reads
            + self.storage_reads
            + self.storage_writes
            + self.settings_changed
        )

    def reset(self) -> None:
        self.searches.clear()
        self.news_searches.clear()
        self.fetched.clear()
        self.skipped.clear()
        self.products.clear()
        self.sources.clear()
        self.calculations = 0
        self.notes_saved = 0
        self.questions = 0
        self.memories = 0
        self.lan_scans = 0
        self.ha_reads = 0
        self.ha_calls = 0
        self.google_reads = 0
        self.storage_reads = 0
        self.storage_writes = 0
        self.settings_changed = 0


class Toolbox:
    """Fuehrt die Tool-Calls des Agenten aus."""

    def __init__(
        self,
        settings: Settings,
        cache: Cache | None = None,
        on_event: EventHook | None = None,
        spec_extractor: SpecExtractor | None = None,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.on_event = on_event
        self.spec_extractor = spec_extractor
        self.rules = load_rules()
        self.stats = ToolStats()
        #: Setzt der Agent, wenn Subagenten erlaubt sind.
        self.subagent_runner: Callable[[list[str]], list[dict[str, Any]]] | None = None
        #: Setzt die Oberflaeche, wenn jemand da ist, der antworten kann.
        self.ask_handler: AskHandler | None = None
        #: Wird beim ersten Zugriff geoeffnet, nicht beim Start -- wer den
        #: Speicher nie benutzt, soll auch keine Datei dafuer anlegen.
        self._memory_store: Any = None
        self._fetcher = fetcher or Fetcher(
            user_agent=settings.user_agent,
            timeout=settings.fetch_timeout,
            delay_seconds=settings.request_delay_seconds,
            rules=self.rules,
            enable_browser=settings.enable_playwright,
        )
        #: Suchtreffer nach URL, damit blockierte Seiten wenigstens als Link taugen.
        self.seen_results: dict[str, SearchResult] = {}
        #: Der Google-Zugriff wird erst beim ersten Aufruf gebaut -- ohne
        #: verbundenes Konto soll gar nichts davon geladen werden.
        self._google_client: Any = None
        #: Ebenso das Lager -- ohne eingetragene Adresse wird nichts gebaut.
        self._storage_client: Any = None
        #: Wird gerufen, wenn sich eine Einstellung geaendert hat. Die
        #: Oberflaeche baut daraufhin den Agenten fuer die naechste Frage neu.
        self.on_settings_changed: Any = None

    def close(self) -> None:
        self._fetcher.close()
        if self._google_client is not None:
            self._google_client.close()
        if self._storage_client is not None:
            self._storage_client.close()

    def _emit(self, event: str, **payload: Any) -> None:
        if self.on_event:
            self.on_event(event, payload)

    # -- Werkzeug 1 -------------------------------------------------------
    def _plan_queries(self, query: str, extra: list[str] | None) -> list[str]:
        """Welche Anfragen wirklich rausgehen.

        Zuerst, was das Modell selbst formuliert hat -- semantische Varianten
        kann es besser als jede Wortliste. Dazu die Stichwortfassung der
        ersten Anfrage. Mehr als drei werden es nicht: ab der vierten
        Formulierung nehmen die Treffer nicht mehr zu, nur noch der Streuung.
        """
        wanted: list[str] = []
        for candidate in [query, *(extra or [])]:
            candidate = " ".join((candidate or "").split())
            if candidate and candidate.lower() not in {q.lower() for q in wanted}:
                wanted.append(candidate)
        if not wanted:
            return []
        limit = max(1, min(int(self.settings.search_variants or 1), MAX_VARIANTS))
        if len(wanted) < limit:
            for candidate in variants(wanted[0], extra=limit - len(wanted)):
                if candidate.lower() not in {q.lower() for q in wanted}:
                    wanted.append(candidate)
        return wanted[:limit]

    def web_search(
        self,
        query: str,
        count: int = 0,
        country: str = "",
        lang: str = "",
        queries: list[str] | None = None,
    ) -> dict[str, Any]:
        """Sucht im Web und liefert Treffer als einfache Dicts.

        Gesucht wird nicht mit einer Formulierung, sondern mit bis zu drei --
        den eigenen des Modells und einer Stichwortfassung. Die Listen werden
        anschliessend zusammengemischt, wobei zaehlt, was mehrere Anfragen
        uebereinstimmend weit oben haben.
        """
        query = (query or "").strip()
        count = int(count or self.settings.max_results_default)
        country = (country or self.settings.country or "de").lower()
        lang = (lang or self.settings.lang or "de").lower()

        wanted = self._plan_queries(query, queries)
        label = query or (wanted[0] if wanted else "")
        self.stats.searches.append(label)
        self._emit("search", query=label, count=count, queries=wanted)

        if not wanted:
            return {"query": query, "results": [], "error": "Leere Suchanfrage."}

        key = cache_key(
            "search",
            self.settings.search_backend,
            self.settings.search_engines,
            "|".join(wanted),
            count,
            country,
            lang,
        )
        cached = self.cache.get(key) if self.cache else None
        used = wanted
        if cached is not None:
            results = [SearchResult(**item) for item in cached]
        else:
            def configured(query_text: str, wanted_count: int) -> list[SearchResult]:
                return search_web(
                    query_text,
                    count=wanted_count,
                    country=country,
                    lang=lang,
                    backend=self.settings.search_backend,
                    engines=self.settings.search_engines,
                    instance_url=self.settings.searxng_url,
                )

            def plain(query_text: str, wanted_count: int) -> list[SearchResult]:
                """Die offene Metasuche -- ohne Key, praktisch immer erreichbar."""
                return search_web(query_text, count=wanted_count, country=country, lang=lang)

            try:
                results, used = search_broadly(wanted, count, configured)
            except SearchError as exc:
                # Fallback: faellt das konfigurierte Backend aus (SearXNG down,
                # API-Limit), uebernimmt die offene Metasuche -- die braucht
                # nichts und ist praktisch immer da.
                if self.settings.search_backend in OPEN_BACKEND_NAMES:
                    self._emit("error", message=str(exc))
                    return {"query": label, "results": [], "error": str(exc)}
                self._emit("fallback", source=self.settings.search_backend, target="metasuche")
                try:
                    results, used = search_broadly(wanted, count, plain)
                except SearchError as second:
                    self._emit("error", message=str(second))
                    return {"query": label, "results": [], "error": str(second)}
            if self.cache:
                self.cache.set(
                    key,
                    [result.model_dump() for result in results],
                    kind="search",
                    label=label,
                )

        for result in results:
            self.seen_results.setdefault(result.url, result)

        self._emit("search_done", query=label, hits=len(results), queries=used)
        return {
            "query": label,
            "queries": used,
            "country": country,
            "lang": lang,
            "results": [result.as_tool_dict() for result in results],
        }

    # -- Werkzeug 2 -------------------------------------------------------
    def fetch_page(self, url: str) -> dict[str, Any]:
        """Laedt eine Seite und gibt lesbaren Text plus Produktdaten zurueck."""
        url = (url or "").strip()
        self._emit("fetch", url=url)

        key = cache_key("page", url)
        cached = self.cache.get(key) if self.cache else None
        if cached is not None:
            page = PageResult(**cached)
            page.via = "cache"
        else:
            page = self._fetcher.fetch(url, want_products=True)
            self._maybe_llm_specs(page)
            # Voruebergehende Fehler nicht cachen: ein Timeout von jetzt sagt
            # nichts darueber, ob die Seite in einer Stunde erreichbar ist.
            # Stabile Ergebnisse (Inhalt, blocked, paywall) duerfen 24 h liegen.
            transient = page.skipped_reason in ("timeout", "network_error")
            if self.cache and not transient:
                self.cache.set(key, page.model_dump(), kind="page", label=page.title or url)

        if page.ok:
            self.stats.fetched.append(page.final_url or url)
            self.stats.sources.append(
                {
                    "url": page.final_url or url,
                    "title": page.title,
                    "domain": page.source_domain or domain_of(url),
                }
            )
            for product in page.products:
                self.stats.products.append(product)
            self._emit("fetch_done", url=url, title=page.title, words=page.word_count)
        else:
            self.stats.skipped[url] = page.skipped_reason
            self._emit("skip", url=url, reason=page.skipped_reason)

        payload = page.as_tool_dict()
        if not page.ok:
            hint = self.seen_results.get(url)
            if hint:
                # Blockierte Seiten bleiben als Kauf-/Referenzlink brauchbar.
                payload["search_title"] = hint.title
                payload["search_snippet"] = hint.snippet
        return payload

    def _maybe_llm_specs(self, page: PageResult) -> None:
        """LLM-Fallback (Quelle 5), wenn strukturierte Extraktion nichts hergab."""
        if not (page.ok and self.spec_extractor and page.text):
            return
        if not page.product_hint:
            # Kein Produkt in Sicht -- kein Grund, das LLM zu bemuehen.
            return
        if page.products and page.products[0].specs:
            return
        specs = self.spec_extractor(page.text, page.final_url or page.url)
        if not specs:
            return
        if page.products:
            for key, value in specs.items():
                page.products[0].specs.setdefault(key, value)
        else:
            page.products = [
                Product(
                    name=page.title or page.url,
                    url=page.final_url or page.url,
                    specs=specs,
                    source_domain=page.source_domain,
                )
            ]

    # -- Werkzeug: Gmail und Kalender -------------------------------------
    def _google(self) -> Any:
        """Der Zugriff auf das verbundene Konto, einmal je Toolbox aufgebaut."""
        from cortex.google import Google, TokenStore

        if self._google_client is None:
            self._google_client = Google(
                self.settings.google_client_id,
                self.settings.google_client_secret,
                TokenStore(self.settings.data_dir, self.settings.memory_key),
            )
        return self._google_client

    def _google_ready(self) -> str:
        """Leerer String heisst "kann losgehen", sonst steht da der Grund."""
        if not self.settings.google_enabled:
            return (
                "Der Zugriff auf Gmail und Kalender ist abgeschaltet. Der Nutzer "
                "schaltet ihn in den Einstellungen unter 'Gmail & Kalender' ein."
            )
        if not self.settings.google_client_id:
            return (
                "Es fehlen die Zugangsdaten der Google-Anwendung (GOOGLE_CLIENT_ID). "
                "Die Einrichtung steht in der README unter 'Gmail und Kalender'."
            )
        if not self._google().connected():
            return (
                "Es ist kein Google-Konto verbunden. Der Nutzer klickt in den "
                "Einstellungen auf 'Verbinden' -- oder `cortex google` im Terminal."
            )
        return ""

    def calendar_events(
        self, days: int = 7, query: str = "", count: int = 10
    ) -> dict[str, Any]:
        """Liest Termine aus dem Google Kalender."""
        from cortex.google import GoogleError

        problem = self._google_ready()
        if problem:
            return {"error": problem}
        self._emit("calendar", days=days, query=query)
        try:
            events = self._google().events(days=days, query=query, count=count)
        except GoogleError as exc:
            self._emit("error", message=str(exc))
            return {"error": str(exc)}
        self.stats.google_reads += 1
        self._emit("calendar_done", found=len(events))
        return {
            "days": days,
            "count": len(events),
            "events": events,
            "note": (
                "Nur der Hauptkalender. Ein leeres Ergebnis heisst 'nichts eingetragen', "
                "nicht 'nichts los'."
                if not events
                else ""
            ),
        }

    def mail_search(self, query: str = "", count: int = 8) -> dict[str, Any]:
        """Sucht in den Mails des Nutzers -- Kopfzeilen, kein Volltext."""
        from cortex.google import GoogleError

        problem = self._google_ready()
        if problem:
            return {"error": problem}
        self._emit("mail", query=query or "neueste")
        try:
            mails = self._google().search_mail(query=query, count=count)
        except GoogleError as exc:
            self._emit("error", message=str(exc))
            return {"error": str(exc)}
        self.stats.google_reads += 1
        self._emit("mail_done", found=len(mails))
        return {
            "query": query,
            "count": len(mails),
            "mails": mails,
            "note": "Fuer den vollen Text mail_read mit der jeweiligen id aufrufen.",
        }

    def mail_read(self, message_id: str) -> dict[str, Any]:
        """Liest eine einzelne Mail."""
        from cortex.google import GoogleError

        problem = self._google_ready()
        if problem:
            return {"error": problem}
        self._emit("mail", query=f"Mail {message_id}")
        try:
            mail = self._google().read_mail(message_id)
        except GoogleError as exc:
            self._emit("error", message=str(exc))
            return {"error": str(exc)}
        self.stats.google_reads += 1
        self._emit("mail_done", found=1)
        return mail

    # -- Werkzeug: Lagerverwaltung ----------------------------------------
    def _storage(self) -> Any:
        """Der Zugriff aufs Lager, einmal je Toolbox aufgebaut."""
        from cortex.storage import Storage

        if self._storage_client is None:
            self._storage_client = Storage(
                self.settings.storage_url, self.settings.storage_access
            )
        return self._storage_client

    def _storage_ready(self) -> str:
        """Leerer String heisst "kann losgehen", sonst steht da der Grund."""
        from cortex.storage import normalize_access

        if normalize_access(self.settings.storage_access) == "off":
            return (
                "Der Zugriff auf die Lagerverwaltung ist abgeschaltet. Der Nutzer "
                "schaltet ihn in den Einstellungen unter 'Zuhause & Netz' ein."
            )
        if not self.settings.storage_url:
            return (
                "Es ist keine Lagerverwaltung eingetragen. Der Nutzer traegt sie in "
                "den Einstellungen unter 'Zuhause & Netz' ein -- der Knopf 'Suchen' "
                "findet sie im eigenen Netz."
            )
        return ""

    def storage_find(self, query: str, limit: int = 20) -> dict[str, Any]:
        """Sucht einen Artikel im Lager."""
        from cortex.storage import StorageError

        problem = self._storage_ready()
        if problem:
            return {"error": problem}
        self._emit("storage", action="suchen", detail=query)
        try:
            hits = self._storage().search(query, limit=limit)
        except StorageError as exc:
            self._emit("error", message=str(exc))
            return {"error": str(exc)}
        self.stats.storage_reads += 1
        self._emit("storage_done", found=len(hits))
        return {
            "query": query,
            "count": len(hits),
            "items": hits,
            "note": (
                "Nichts gefunden. Das heisst nicht, dass es der Nutzer nicht hat -- "
                "nur, dass es nicht eingetragen ist."
                if not hits
                else ""
            ),
        }

    def storage_browse(self, room_id: int = 0, furniture_id: int = 0) -> dict[str, Any]:
        """Zeigt Raeume, die Moebel eines Raums oder die Artikel eines Moebels."""
        from cortex.storage import StorageError

        problem = self._storage_ready()
        if problem:
            return {"error": problem}
        client = self._storage()
        try:
            if furniture_id:
                self._emit("storage", action="ansehen", detail=f"Moebel {furniture_id}")
                rows, kind = client.items(furniture_id), "items"
            elif room_id:
                self._emit("storage", action="ansehen", detail=f"Raum {room_id}")
                rows, kind = client.furniture(room_id), "furniture"
            else:
                self._emit("storage", action="ansehen", detail="Uebersicht")
                rows, kind = client.rooms(), "rooms"
        except StorageError as exc:
            self._emit("error", message=str(exc))
            return {"error": str(exc)}
        self.stats.storage_reads += 1
        self._emit("storage_done", found=len(rows))
        return {"level": kind, "count": len(rows), kind: rows}

    def storage_add(
        self, name: str, furniture_id: int = 0, room_id: int = 0, quantity: int = 1
    ) -> dict[str, Any]:
        """Legt einen Artikel, ein Moebel oder einen Raum an."""
        from cortex.storage import StorageError

        problem = self._storage_ready()
        if problem:
            return {"error": problem}
        client = self._storage()
        try:
            if furniture_id:
                created = client.add_item(furniture_id, name, quantity)
                kind = "Artikel"
            elif room_id:
                created = client.add_furniture(room_id, name)
                kind = "Moebel"
            else:
                created = client.add_room(name)
                kind = "Raum"
        except StorageError as exc:
            self._emit("error", message=str(exc))
            return {"error": str(exc)}
        self.stats.storage_writes += 1
        self._emit("storage_done", found=1, action=f"{kind} angelegt")
        return {"created": kind, "entry": created}

    def storage_edit(
        self,
        item_id: int,
        name: str = "",
        quantity: int | None = None,
        delta: int = 0,
    ) -> dict[str, Any]:
        """Aendert Name oder Bestand eines Artikels."""
        from cortex.storage import StorageError

        problem = self._storage_ready()
        if problem:
            return {"error": problem}
        client = self._storage()
        self._emit("storage", action="aendern", detail=f"Artikel {item_id}")
        try:
            if delta:
                entry = client.change_quantity(item_id, delta)
            else:
                entry = client.update_item(item_id, name=name, quantity=quantity)
        except StorageError as exc:
            self._emit("error", message=str(exc))
            return {"error": str(exc)}
        self.stats.storage_writes += 1
        self._emit("storage_done", found=1, action="geaendert")
        return {"changed": entry}

    # -- Werkzeug: Einstellungen ------------------------------------------
    def change_setting(self, setting: str, value: str) -> dict[str, Any]:
        """Aendert eine Einstellung -- soweit sie dafuer vorgesehen ist."""
        from cortex import preferences

        preference = preferences.find(setting)
        if preference is None:
            refused = preferences.refusal(setting)
            if refused:
                return {"error": refused}
            return {
                "error": (
                    f"'{setting}' kenne ich nicht als Einstellung. Aendern kann ich: "
                    + ", ".join(preferences.catalogue_names())
                    + ". Alles Weitere steht in den Einstellungen."
                )
            }
        try:
            stored = preferences.coerce(preference, value)
        except preferences.BadValue as exc:
            return {"error": str(exc)}

        self._emit("setting", label=preference.label, value=stored)

        if preference.name == preferences.APPEARANCE:
            # Aussehen ist Sache des Browsers -- es steht in keiner Datei. Die
            # Oberflaeche hoert auf dieses Ereignis und stellt es um.
            self._emit("appearance", theme="light" if stored == "hell" else "dark")
            self.stats.settings_changed += 1
            return {
                "changed": preference.label,
                "value": stored,
                "note": "Gilt sofort und bleibt in diesem Browser gespeichert.",
            }

        try:
            written = preferences.store(preference, stored)
        except OSError as exc:
            return {"error": f"Konnte die Einstellung nicht speichern: {exc}"}

        self.stats.settings_changed += 1
        if self.on_settings_changed is not None:
            self.on_settings_changed()
        self._emit("setting_done", label=preference.label, value=stored)
        return {
            "changed": preference.label,
            "value": stored,
            "path": str(written),
            "note": (
                "Gespeichert. Aktiv ist es ab der naechsten Frage -- diese hier "
                "laeuft noch mit den alten Werten weiter."
            ),
        }

    # -- Dispatch ---------------------------------------------------------
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Fuehrt den Tool-Call *name* mit *arguments* aus."""
        if name == "web_search":
            more = arguments.get("queries") or []
            return self.web_search(
                query=str(arguments.get("query", "")),
                count=int(arguments.get("count") or 0),
                country=str(arguments.get("country") or ""),
                lang=str(arguments.get("lang") or ""),
                # Manche Modelle schicken einen String statt einer Liste.
                queries=[str(item) for item in more] if isinstance(more, list) else [str(more)],
            )
        if name == "fetch_page":
            return self.fetch_page(url=str(arguments.get("url", "")))
        if name == "search_news":
            return self.search_news(
                query=str(arguments.get("query", "")), count=int(arguments.get("count") or 0)
            )
        if name == "calculate":
            return self.calculate(expression=str(arguments.get("expression", "")))
        if name == "remember":
            return self.remember(text=str(arguments.get("text", "")))
        if name == "save_memory":
            return self.save_memory(
                text=str(arguments.get("text", "")), topic=str(arguments.get("topic") or "")
            )
        if name == "recall_memory":
            return self.recall_memory(query=str(arguments.get("query") or ""))
        if name == "lan_scan":
            return self.lan_scan(
                subnet=str(arguments.get("subnet") or ""),
                thorough=bool(arguments.get("thorough")),
            )
        if name == "change_setting":
            return self.change_setting(
                setting=str(arguments.get("setting", "")),
                value=str(arguments.get("value", "")),
            )
        if name == "storage_find":
            return self.storage_find(
                query=str(arguments.get("query", "")),
                limit=int(arguments.get("limit") or 0),
            )
        if name == "storage_browse":
            return self.storage_browse(
                room_id=int(arguments.get("room_id") or 0),
                furniture_id=int(arguments.get("furniture_id") or 0),
            )
        if name == "storage_add":
            return self.storage_add(
                name=str(arguments.get("name", "")),
                furniture_id=int(arguments.get("furniture_id") or 0),
                room_id=int(arguments.get("room_id") or 0),
                quantity=int(arguments.get("quantity") or 1),
            )
        if name == "storage_edit":
            raw_quantity = arguments.get("quantity")
            return self.storage_edit(
                item_id=int(arguments.get("item_id") or 0),
                name=str(arguments.get("name") or ""),
                quantity=None if raw_quantity is None else int(raw_quantity),
                delta=int(arguments.get("delta") or 0),
            )
        if name == "calendar_events":
            return self.calendar_events(
                days=int(arguments.get("days") or 0),
                query=str(arguments.get("query") or ""),
                count=int(arguments.get("count") or 0),
            )
        if name == "mail_search":
            return self.mail_search(
                query=str(arguments.get("query") or ""),
                count=int(arguments.get("count") or 0),
            )
        if name == "mail_read":
            return self.mail_read(message_id=str(arguments.get("message_id", "")))
        if name == "lan_check":
            return self.lan_check(host=str(arguments.get("host", "")))
        if name == "ha_states":
            return self.ha_states(
                search=str(arguments.get("search") or ""),
                domain=str(arguments.get("domain") or ""),
            )
        if name == "ha_call":
            return self.ha_call(
                domain=str(arguments.get("domain", "")),
                service=str(arguments.get("service", "")),
                entity_id=str(arguments.get("entity_id") or ""),
                data=arguments.get("data"),
            )
        if name == "ask_user":
            return self.ask_user(
                question=str(arguments.get("question", "")), options=arguments.get("options")
            )
        if name == "research_subtasks":
            return self.research_subtasks(arguments.get("tasks") or [])
        return {"error": f"Unbekanntes Werkzeug '{name}'."}

    # -- Werkzeug 3: News -------------------------------------------------
    def search_news(self, query: str, count: int = 0) -> dict[str, Any]:
        """News-Suche; faellt bei Ausfall auf die normale Websuche zurueck."""
        query = (query or "").strip()
        count = int(count or self.settings.max_results_default)
        self.stats.news_searches.append(query)
        self._emit("search", query=f"News: {query}", count=count)
        if not query:
            return {"query": query, "results": [], "error": "Leere Suchanfrage."}

        key = cache_key("news", query, count, self.settings.country, self.settings.lang)
        cached = self.cache.get(key) if self.cache else None
        if cached is not None:
            results = [SearchResult(**item) for item in cached]
        else:
            try:
                results = search_news(
                    query, count=count, country=self.settings.country, lang=self.settings.lang
                )
            except SearchError:
                # Fallback: lieber normale Treffer als gar keine.
                self._emit("fallback", source="news", target="websuche")
                try:
                    results = search_web(
                        query,
                        count=count,
                        country=self.settings.country,
                        lang=self.settings.lang,
                    )
                except SearchError as exc:
                    self._emit("error", message=str(exc))
                    return {"query": query, "results": [], "error": str(exc)}
            if self.cache:
                # News veralten schnell -- eine Stunde statt 24.
                self.cache.set(
                    key,
                    [result.model_dump() for result in results],
                    kind="news",
                    label=query,
                    ttl=3600,
                )

        for result in results:
            self.seen_results.setdefault(result.url, result)
        self._emit("search_done", query=query, hits=len(results))
        return {"query": query, "results": [result.as_tool_dict() for result in results]}

    # -- Werkzeug 4: Rechner ----------------------------------------------
    def calculate(self, expression: str) -> dict[str, Any]:
        """Exakte Arithmetik -- damit das Modell nie selbst rechnen muss."""
        from cortex.calc import CalcError, calculate_pretty

        self.stats.calculations += 1
        self._emit("calculate", expression=expression)
        try:
            return {"expression": expression, "result": calculate_pretty(expression)}
        except CalcError as exc:
            return {"expression": expression, "error": str(exc)}

    # -- Werkzeug 5: Merkzettel (nur Hauptagent) --------------------------
    def remember(self, text: str) -> dict[str, Any]:
        """Notiz auf den dauerhaften Merkzettel des Nutzers schreiben."""
        text = (text or "").strip()
        if not text:
            return {"error": "Leere Notiz."}
        if self.cache is None:
            return {"error": "Kein Speicher verfuegbar -- Notiz nicht abgelegt."}
        note_id = self.cache.add_note(text)
        self.stats.notes_saved += 1
        self._emit("remember", text=text)
        return {"saved": True, "note_id": note_id, "text": text}

    # -- Werkzeug: Langzeitspeicher ---------------------------------------
    def _memory(self) -> Any:
        """Den Speicher oeffnen -- oder None, wenn er abgeschaltet ist."""
        if not self.settings.memory_enabled:
            return None
        if self._memory_store is None:
            from cortex.memory import Memory

            self._memory_store = Memory(
                self.settings.db_path, self.settings.data_dir, self.settings.memory_key
            )
        return self._memory_store

    def save_memory(self, text: str, topic: str = "") -> dict[str, Any]:
        """Legt eine Textnotiz im Langzeitspeicher ab."""
        from cortex.memory import MemoryFull

        store = self._memory()
        if store is None:
            return {
                "error": (
                    "Der Speicher ist abgeschaltet. Der Nutzer kann ihn in den "
                    "Einstellungen wieder einschalten."
                )
            }
        text = (text or "").strip()
        if not text:
            return {"error": "Leere Notiz."}
        try:
            entry = store.remember(text, topic)
        except MemoryFull as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {"error": str(exc)}
        self.stats.memories += 1
        self._emit("memory_save", text=entry.text, topic=entry.topic)
        return {"saved": True, "id": entry.id, "topic": entry.topic}

    def recall_memory(self, query: str = "") -> dict[str, Any]:
        """Sucht im Langzeitspeicher."""
        store = self._memory()
        if store is None:
            return {
                "found": 0,
                "entries": [],
                "note": "Der Speicher ist abgeschaltet -- frag den Nutzer direkt.",
            }
        self._emit("memory_read", query=query or "alles")
        entries = store.recall(query)
        self.stats.memories += 1
        return {
            "found": len(entries),
            "entries": [entry.as_dict() for entry in entries],
            "note": (
                "Nichts gefunden. Das heisst nur, dass nichts abgelegt wurde -- "
                "frag den Nutzer, statt zu raten."
                if not entries
                else ""
            ),
        }

    # -- Werkzeug: das eigene Netz ----------------------------------------
    def lan_scan(self, subnet: str = "", thorough: bool = False) -> dict[str, Any]:
        """Sieht nach, welche Geraete im Heimnetz erreichbar sind."""
        from cortex.lan import NotPrivate, scan

        if not self.settings.lan_enabled:
            return {"error": "Der Netzzugriff ist abgeschaltet (CORTEX_LAN_ENABLED=false)."}
        target = (subnet or self.settings.lan_subnet or "").strip()
        self._emit("lan_scan", subnet=target or "eigenes Netz")
        try:
            devices = scan(target, quick=not thorough)
        except NotPrivate as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {
                "error": (
                    f"{exc} Trag dein Netz unter CORTEX_LAN_SUBNET ein, z.B. 192.168.1.0/24."
                )
            }
        except OSError as exc:
            return {"error": f"Netzdurchlauf fehlgeschlagen: {exc}"}

        from cortex.lan import container_hint

        self.stats.lan_scans += 1
        self._emit("lan_done", found=len(devices))
        note = (
            "Nur erreichbare Geraete. Ein fehlendes Geraet kann auch schlafen "
            "oder eine Firewall haben -- das ist kein Beweis fuer 'nicht vorhanden'."
        )
        hint = container_hint(target)
        return {
            "subnet": target or "automatisch erkannt",
            "count": len(devices),
            "devices": [device.as_dict() for device in devices],
            "note": f"{hint} {note}".strip() if hint else note,
        }

    def lan_check(self, host: str) -> dict[str, Any]:
        """Prueft ein einzelnes Geraet im Netz."""
        import socket

        from cortex.lan import FULL_PORTS, check_host

        if not self.settings.lan_enabled:
            return {"error": "Der Netzzugriff ist abgeschaltet (CORTEX_LAN_ENABLED=false)."}
        host = (host or "").strip()
        if not host:
            return {"error": "Keine Adresse angegeben."}
        try:
            address = socket.gethostbyname(host)
        except OSError:
            return {"host": host, "reachable": False, "error": f"{host} ist nicht aufloesbar."}

        import ipaddress

        from cortex.lan import is_private_net

        try:
            network = ipaddress.ip_network(f"{address}/32")
        except ValueError:
            return {"host": host, "reachable": False, "error": "Keine gueltige IPv4-Adresse."}
        if not is_private_net(network):
            return {
                "host": host,
                "reachable": False,
                "error": f"{address} liegt ausserhalb des privaten Netzes -- Cortex AI prueft "
                "nur das eigene Heimnetz.",
            }

        self._emit("lan_check", host=host)
        device = check_host(address, FULL_PORTS)
        self.stats.lan_scans += 1
        if device is None:
            return {
                "host": host,
                "address": address,
                "reachable": False,
                "note": "Kein Port antwortet. Geraet aus, im Ruhezustand oder abgeschottet.",
            }
        return {"host": host, "reachable": True, **device.as_dict()}

    # -- Werkzeug: Home Assistant -----------------------------------------
    def ha_states(self, search: str = "", domain: str = "") -> dict[str, Any]:
        """Liest Zustaende aus Home Assistant."""
        from cortex.homeassistant import MAX_ENTITIES, HomeAssistantError, from_settings

        client = from_settings(self.settings)
        if not client.configured:
            from cortex.lan import container_hint

            hint = container_hint()
            return {
                "error": (
                    "Home Assistant ist nicht verbunden. Der Nutzer richtet das mit "
                    "`cortex connect-ha` ein -- das dauert eine Minute."
                    + (f" Hinweis: {hint}" if hint else "")
                )
            }
        self._emit("ha_read", search=search or domain or "Uebersicht")
        try:
            if not search and not domain:
                counts = client.domains()
                self.stats.ha_reads += 1
                return {
                    "overview": counts,
                    "note": (
                        "Uebersicht der Bereiche. Ruf ha_states erneut mit `domain` oder "
                        "`search` auf, um die einzelnen Geraete zu sehen."
                    ),
                }
            found = client.find(search=search, domain=domain)
        except HomeAssistantError as exc:
            return {"error": str(exc)}

        self.stats.ha_reads += 1
        return {
            "count": len(found),
            "entities": [entity.as_dict() for entity in found],
            "note": (
                f"Hoechstens {MAX_ENTITIES} Eintraege. Bei mehr: genauer suchen."
                if len(found) >= MAX_ENTITIES
                else ""
            ),
        }

    def ha_call(
        self, domain: str, service: str, entity_id: str = "", data: Any = None
    ) -> dict[str, Any]:
        """Schaltet etwas in Home Assistant -- mit mehreren Sicherungen."""
        from cortex.homeassistant import (
            ALLOWED_DOMAINS,
            PROTECTED_DOMAINS,
            HomeAssistantError,
            from_settings,
        )

        client = from_settings(self.settings)
        if not client.configured:
            return {"error": "Home Assistant ist nicht verbunden (`cortex connect-ha`)."}
        if not self.settings.ha_control:
            return {
                "error": (
                    "Cortex AI darf nur nachsehen, nicht schalten. Der Nutzer schaltet das "
                    "in den Einstellungen frei (CORTEX_HA_CONTROL=true). Sag ihm das, "
                    "statt es zu umgehen."
                )
            }
        domain = (domain or "").strip().lower()
        service = (service or "").strip().lower()
        entity_id = (entity_id or "").strip()
        if not domain or not service:
            return {"error": "Bereich und Dienst muessen angegeben sein."}
        if domain not in ALLOWED_DOMAINS:
            return {
                "error": (
                    f"Cortex AI schaltet im Bereich '{domain}' nicht. Erlaubt sind: "
                    f"{', '.join(sorted(ALLOWED_DOMAINS))}."
                )
            }

        # Schloesser, Alarmanlagen, Tore, Heizungen: hier wird nachgefragt,
        # auch wenn Schalten erlaubt ist. Ein missverstandener Satz soll nicht
        # die Haustuer aufschliessen.
        if domain in PROTECTED_DOMAINS:
            target = entity_id or domain
            if self.ask_handler is None:
                return {
                    "error": (
                        f"'{domain}' ist bestaetigungspflichtig, aber hier kann niemand "
                        "bestaetigen. Bitte den Nutzer, es selbst zu tun."
                    )
                }
            question = f"Soll ich wirklich {service} fuer {target} ausfuehren?"
            self._emit("ask", question=question, options=["ja", "nein"])
            answer = (self.ask_handler(question, ["ja", "nein"]) or "").strip().lower()
            self._emit("ask_done", question=question, answer=answer)
            if answer not in ("ja", "j", "yes", "ok", "mach", "los"):
                return {
                    "done": False,
                    "note": f"Vom Nutzer nicht bestaetigt (Antwort: {answer!r}).",
                }

        self._emit("ha_call", domain=domain, service=service, entity_id=entity_id)
        try:
            changed = client.call(domain, service, entity_id, data)
        except HomeAssistantError as exc:
            return {"error": str(exc)}
        self.stats.ha_calls += 1
        return {
            "done": True,
            "service": f"{domain}.{service}",
            "entity_id": entity_id,
            "changed": len(changed),
        }

    # -- Werkzeug 6: Rueckfrage (nur fuer den Hauptagenten) ---------------
    def ask_user(self, question: str, options: Any = None) -> dict[str, Any]:
        """Fragt beim Nutzer nach und wartet auf die Antwort.

        Bleibt die Antwort aus, ist das kein Fehler: das Modell soll dann eine
        Annahme treffen und weiterarbeiten, statt stehenzubleiben.
        """
        question = (question or "").strip()
        if not question:
            return {"error": "Leere Rueckfrage."}
        if self.ask_handler is None:
            return {
                "answered": False,
                "note": (
                    "Hier kann gerade niemand antworten. Triff die naheliegende "
                    "Annahme, nenne sie in der Antwort und arbeite weiter."
                ),
            }
        if self.stats.questions >= MAX_QUESTIONS:
            return {
                "answered": False,
                "note": (
                    f"Schon {self.stats.questions} Rueckfragen gestellt -- das reicht. "
                    "Triff jetzt eine begruendete Annahme und arbeite weiter."
                ),
            }

        choices = [str(item).strip() for item in (options or []) if str(item).strip()][:4]
        self.stats.questions += 1
        self._emit("ask", question=question, options=choices)
        try:
            answer = (self.ask_handler(question, choices) or "").strip()
        except Exception as exc:
            return {"answered": False, "note": f"Rueckfrage fehlgeschlagen: {exc}"}
        self._emit("ask_done", question=question, answer=answer)
        if not answer:
            return {
                "answered": False,
                "note": (
                    "Keine Antwort bekommen. Triff die naheliegende Annahme, nenne sie "
                    "und arbeite weiter."
                ),
            }
        return {"answered": True, "question": question, "answer": answer}

    # -- Werkzeug 7 (nur fuer den Hauptagenten) ---------------------------
    def research_subtasks(self, tasks: Any) -> dict[str, Any]:
        """Gibt Teilfragen an parallele Subagenten ab."""
        if self.subagent_runner is None:
            return {"error": "Subagenten sind in dieser Sitzung nicht aktiv."}
        if not isinstance(tasks, list):
            return {"error": "`tasks` muss eine Liste von Teilfragen sein."}
        clean = [str(task).strip() for task in tasks if str(task).strip()]
        if not clean:
            return {"error": "Keine Teilfragen angegeben."}
        results = self.subagent_runner(clean)
        return {"results": results}


def looks_like_product_page(html: str, url: str) -> bool:
    """Kleine Hilfe fuer Tests und den LLM-Fallback."""
    from selectolax.parser import HTMLParser

    if extract_product(html, url) is not None:
        return True
    return has_spec_heading(HTMLParser(html))
