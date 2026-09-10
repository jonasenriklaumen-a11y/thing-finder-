"""Die Werkzeuge des Agenten -- allen voran `web_search` und `fetch_page`.

Instagram, Amazon, Branchenbuch oder Ladenwebsite: alles sind Suchtreffer,
die gelesen werden koennen. Es gibt bewusst **keine plattformspezifischen
Scraper**. `find_profiles` ist keine Ausnahme davon, sondern die Regel in
Reinform: es stellt Suchanfragen mit `site:` und liest, was die Suchmaschine
oeffentlich kennt -- kein Durchprobieren von Profiladressen, kein Umgehen
einer Sperre. Was eine Seite nicht hergibt, bleibt beim Titel und dem
Ausschnitt.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aquaticy.cache import Cache, cache_key
from aquaticy.config import Settings
from aquaticy.extract import extract_product, has_spec_heading
from aquaticy.fetch import Fetcher, load_rules
from aquaticy.models import PageResult, Product, SearchResult, domain_of
from aquaticy.queries import MAX_VARIANTS, keywords, mentions_place, variants, with_place
from aquaticy.search import (
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
VisualInspector = Callable[[str, str], str]

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
                        "So viele eigenstaendige Teilfragen, wie Assistenten bereitstehen "
                        "-- die Zahl steht im Systemtext. Jede ein ganzer Satz, jede fuer "
                        "sich verstaendlich (Ort, Produkt, Zeitraum, Kriterium), keine "
                        "zwei zum selben Feld."
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

PUBLIC_VISUAL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "inspect_public_visual",
        "description": (
            "Öffnet ein frei zugängliches Webcam-, Satelliten-, Karten- oder Straßenbild "
            "(oder eine Seite, die es enthält), nimmt einen Schnappschuss und lässt ihn "
            "vom Vision-Modell beschreiben. "
            "Nutze nur öffentliche Quellen und übergib den sichtbaren Sachverhalt, den "
            "du prüfen möchtest. Das Ergebnis ist eine Beobachtung, kein Ereignisnachweis."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Öffentliche http(s)-Adresse."},
                "question": {"type": "string", "description": "Was im Bild geprüft werden soll."},
            },
            "required": ["url", "question"],
        },
    },
}



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

#: Wo eine Marke, eine Firma oder eine Person ausser auf der eigenen Seite
#: noch steht. Die Reihenfolge ist die Reihenfolge der Ausbeute: bei Marken
#: und Laeden bringt Instagram am meisten, bei Firmen LinkedIn, und Wikipedia
#: klaert, ob es ueberhaupt dieselbe Sache ist.
#:
#: Gesucht wird ueber die Suchmaschine mit `site:` -- nicht, indem Adressen
#: durchprobiert werden. Das ist der Unterschied zwischen Recherche und
#: Abgrasen: wir fragen, was oeffentlich indexiert ist, statt ein Verzeichnis
#: von Profilnamen abzuklappern. Und was eine Seite nicht hergibt (Instagram
#: und LinkedIn sperren Abrufe aus), bleibt eben beim Titel und dem
#: Suchausschnitt -- umgangen wird nichts.
PROFILE_SITES: tuple[tuple[str, str], ...] = (
    ("Instagram", "instagram.com"),
    ("LinkedIn", "linkedin.com"),
    ("Facebook", "facebook.com"),
    ("X", "x.com"),
    ("YouTube", "youtube.com"),
    ("Wikipedia", "wikipedia.org"),
    ("TikTok", "tiktok.com"),
    ("Trustpilot", "trustpilot.com"),
    ("kununu", "kununu.com"),
    ("Yelp", "yelp.com"),
    ("GitHub", "github.com"),
    ("Reddit", "reddit.com"),
)

#: So viele Plattformen fragt ein Aufruf hoechstens ab. Zwoelf Suchanfragen
#: auf einmal sind fuer eine offene Suchmaschine ein Ausschlag.
MAX_PROFILE_SITES = 8

PROFILE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "find_profiles",
        "description": (
            "Sucht zu einer Marke, Firma, Einrichtung oder Person alles, was es "
            "ausserhalb der eigenen Website gibt: Instagram, LinkedIn, Facebook, X, "
            "YouTube, Wikipedia, Bewertungsportale. Nimm das IMMER, wenn nach einem "
            "Namen gefragt ist -- oeffentliche Profile stehen oft aktueller da als "
            "die Website (Oeffnungszeiten, Angebote, Neues), und manche Anbieter "
            "haben ueberhaupt nur ein Profil und keine Seite. Danach liest du die "
            "gefundenen Adressen mit `fetch_page`; was sich sperrt, bleibt beim "
            "Titel und dem Ausschnitt -- umgangen wird nichts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Der Name, moeglichst genau. Ort dazu hilft.",
                },
                "platforms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Nur diese Plattformen (Instagram, LinkedIn, Facebook, X, "
                        "YouTube, Wikipedia, TikTok, Trustpilot, kununu, Yelp, "
                        "GitHub, Reddit). Leer = die acht ergiebigsten."
                    ),
                },
            },
            "required": ["name"],
        },
    },
}

PLACES_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "local_places",
        "description": (
            "Sucht in der KARTE (OpenStreetMap) statt in einer Suchmaschine: kleine "
            "Laeden, Werkstaetten, Praxen, Vereine, Spielplaetze -- mit Adresse, "
            "Website, Telefon und Oeffnungszeiten. Nimm das immer dann, wenn es um "
            "etwas Oertliches geht und die Websuche nur Portale und Verzeichnisse "
            "ausspuckt: wer keine Website hat oder nichts fuer Suchmaschinen tut, "
            "steht trotzdem in der Karte -- eingetragen von Leuten vor Ort. Die "
            "gefundenen Websites kannst du danach mit `fetch_page` lesen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "what": {
                    "type": "string",
                    "description": (
                        "Was gesucht wird: 'Cafe', 'Fahrradladen', 'Schreiner', "
                        "'Spielplatz' -- oder ein Name, wenn du einen suchst."
                    ),
                },
                "where": {
                    "type": "string",
                    "description": "Der Ort. Leer lassen heisst: der eingestellte Ortsfilter.",
                },
                "radius_km": {
                    "type": "number",
                    "description": "Umkreis in Kilometern (0,2 bis 15). Standard 4.",
                },
            },
            "required": ["what"],
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
                        "Netz wie '192.168.1.0/24'. Leer lassen -- dann nimmt Aquaticy AI das "
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



CALENDAR_ADD_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "calendar_add",
        "description": (
            "Traegt einen Termin in den Hauptkalender des Nutzers ein. Vor dem "
            "Eintragen wird der Nutzer gefragt -- ein missverstandener Satz soll "
            "keinen Termin erfinden. Zeiten als ISO-8601 in Ortszeit: "
            "'2026-09-08T14:00:00'. Fuer einen ganzen Tag nur das Datum "
            "('2026-09-08') und whole_day=true. Fehlt dir eine Angabe (welcher "
            "Tag? wie lange?), frag mit ask_user nach, statt sie zu erfinden."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Der Titel des Termins."},
                "start": {"type": "string", "description": "Anfang, ISO-8601."},
                "end": {
                    "type": "string",
                    "description": "Ende, ISO-8601. Leer = eine Stunde.",
                },
                "description": {"type": "string", "description": "Notiz zum Termin."},
                "location": {"type": "string", "description": "Ort."},
                "whole_day": {"type": "boolean", "description": "Ganztaegig?"},
            },
            "required": ["summary", "start"],
        },
    },
}

CALENDAR_EDIT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "calendar_edit",
        "description": (
            "Aendert einen bestehenden Termin. Die Kennung kommt aus "
            "calendar_events -- such den Termin also erst, statt zu raten. "
            "Angegeben wird nur, was sich aendern soll; alles andere bleibt. "
            "Auch hier wird der Nutzer vorher gefragt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Kennung aus calendar_events."},
                "summary": {"type": "string"},
                "start": {"type": "string", "description": "Neuer Anfang, ISO-8601."},
                "end": {"type": "string", "description": "Neues Ende, ISO-8601."},
                "description": {"type": "string"},
                "location": {"type": "string"},
                "whole_day": {"type": "boolean"},
            },
            "required": ["event_id"],
        },
    },
}

MAIL_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "mail_draft",
        "description": (
            "Legt einen Mail-ENTWURF in Gmail an. Verschickt wird nichts -- dafuer "
            "fehlen Aquaticy die Rechte, nicht nur der Wille; der Entwurf steht "
            "danach in Gmail unter 'Entwuerfe' und geht erst hinaus, wenn ein "
            "Mensch auf Senden drueckt. Sag das dem Nutzer auch so. Vor dem "
            "Anlegen wird gefragt."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Empfaenger, kann leer bleiben."},
                "subject": {"type": "string", "description": "Betreff."},
                "body": {"type": "string", "description": "Der Text der Mail."},
                "cc": {"type": "string", "description": "Kopie an."},
            },
            "required": ["subject", "body"],
        },
    },
}

GOOGLE_WRITE_SCHEMAS: tuple[dict[str, Any], ...] = (
    CALENDAR_ADD_SCHEMA,
    CALENDAR_EDIT_SCHEMA,
    MAIL_DRAFT_SCHEMA,
)

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
# Die Werkstatt -- nur im Code-Modus, nur wenn sie eingeschaltet ist
# ---------------------------------------------------------------------------
#: Vorlage fuer die vm_run-Beschreibung -- die Zahlen haengen von der
#: gewaehlten Werkstatt-Groesse ab (normal/plus, siehe AQUATICY_VM_SIZE) und
#: werden erst in `vm_schemas_for` eingesetzt. Falsche Zahlen waeren
#: schlimmer als gar keine: das Modell plant damit, wie viel es sich leisten
#: kann.
VM_RUN_DESCRIPTION = (
    "Fuehrt einen Shell-Befehl in der abgeschotteten Werkstatt aus und gibt "
    "Ausgabe und Rueckgabewert zurueck. Dort darfst du alles: Dateien anlegen, "
    "Programme starten, Tests laufen lassen. Es gibt KEIN Netz (kein pip "
    "install, kein curl), {memory_mb} MB Arbeitsspeicher, {cpus} {kern_wort} "
    "und {disk_gb} GB Platte unter /work. Nutze es, um deinen Code wirklich "
    "auszuprobieren, statt zu behaupten, er laufe."
)

VM_RUN_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "vm_run",
        "description": VM_RUN_DESCRIPTION.format(
            memory_mb=1024, cpus=1, kern_wort="Prozessorkern", disk_gb=4
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Der Befehl, z.B. 'python loesung.py' oder 'pytest -q'.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Sekunden, hoechstens 120. Standard 30.",
                },
            },
            "required": ["command"],
        },
    },
}

VM_WRITE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "vm_write",
        "description": (
            "Legt eine Datei in der Werkstatt an oder ueberschreibt sie. Pfade liegen "
            "unter /work. So bringst du deinen Code hinein, bevor du ihn ausfuehrst."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "z.B. 'loesung.py'."},
                "text": {"type": "string", "description": "Der vollstaendige Inhalt."},
            },
            "required": ["path", "text"],
        },
    },
}

VM_READ_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "vm_read",
        "description": "Liest eine Datei aus der Werkstatt (unter /work).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}

VM_FILES_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "vm_files",
        "description": (
            "Listet auf, was in der Werkstatt liegt -- Pfad und Groesse. Anhaenge des "
            "Nutzers landen unter /work/eingang. Was du unter /work ablegst, kann der "
            "Nutzer sich herunterladen; sag ihm also, wie die Datei heisst."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Ordner, Standard /work.",
                }
            },
        },
    },
}

#: Blender arbeitet nur im Code-Modus, in der Werkstatt -- nie auf dem
#: Rechner des Nutzers, und nie ohne die Abschottung. Ob es das Werkzeug
#: wirklich gibt, haengt am Werkstatt-Abbild (AQUATICY_VM_IMAGE): das
#: mitgelieferte Standardabbild bringt es nicht mit, sondern nur eins, das
#: Blender selbst enthaelt (siehe docker/workshop-blender.Dockerfile). Fehlt
#: es, kommt "command not found" zurueck -- das ist keine Stoerung, sondern
#: die ehrliche Antwort.
BLENDER_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "blender_run",
        "description": (
            "Fuehrt ein Python-Skript headless in Blender aus (die bpy-API) und "
            "erstellt oder bearbeitet damit ein 3D-Design: Modelle, Szenen, "
            "Materialien, Renderings. Das Skript landet als Datei in der Werkstatt "
            "und startet dann mit 'blender --background --python <datei>'. "
            "Schreib normalen bpy-Code hinein, z.B. bpy.ops.mesh.primitive_cube_add(...), "
            "bpy.ops.wm.save_as_mainfile(filepath='/work/design.blend'), oder fuers "
            "Rendern bpy.context.scene.render.filepath = '/work/bild.png' gefolgt von "
            "bpy.ops.render.render(write_still=True). Fertige Dateien liegen danach "
            "unter /work -- mit vm_files findest du sie, der Nutzer kann sie sich "
            "herunterladen. Blender braucht mehr Rechenleistung als ein Skript: bei "
            "der Werkstatt-Groesse 'normal' kann ein Rendering am Zeitlimit oder am "
            "Speicher scheitern, 'plus' (Einstellungen -> Werkstatt) schafft mehr. "
            "Blender ist nur da, wenn die Werkstatt mit einem Blender-faehigen Abbild "
            "laeuft -- fehlt es, kommt 'command not found' zurueck, dann sag dem "
            "Nutzer, dass dafür ein anderes Werkstatt-Abbild noetig ist."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "Vollstaendiger bpy-Python-Code, der in Blender laeuft.",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Dateiname fuer das Skript, z.B. 'design.py'. Standard 'design.py'."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Sekunden, hoechstens 120. Standard 90 -- Rendern braucht "
                        "laenger als ein gewoehnlicher Befehl."
                    ),
                },
            },
            "required": ["script"],
        },
    },
}

VM_SCHEMAS: tuple[dict[str, Any], ...] = (
    VM_RUN_SCHEMA,
    VM_WRITE_SCHEMA,
    VM_READ_SCHEMA,
    VM_FILES_SCHEMA,
    BLENDER_SCHEMA,
)


def vm_schemas_for(settings: Any) -> tuple[dict[str, Any], ...]:
    """Die Werkstatt-Werkzeuge, mit den tatsaechlichen Grenzen im Text.

    Die Zahlen in `vm_run` haengen von der gewaehlten Werkstatt-Groesse ab
    (normal/plus, siehe AQUATICY_VM_SIZE) -- ein Werkzeugtext mit falschen
    Zahlen waere schlimmer als gar keiner, das Modell plant damit, wie viel
    es sich leisten kann.
    """
    import copy

    cpus = max(1, int(getattr(settings, "vm_cpus", 1) or 1))
    memory_mb = max(1, int(getattr(settings, "vm_memory_mb", 1024) or 1024))
    disk_gb = max(1, int(getattr(settings, "vm_disk_gb", 4) or 4))
    schemas = copy.deepcopy(VM_SCHEMAS)
    for schema in schemas:
        if schema["function"]["name"] == "vm_run":
            schema["function"]["description"] = VM_RUN_DESCRIPTION.format(
                memory_mb=memory_mb,
                cpus=cpus,
                kern_wort="Prozessorkern" if cpus == 1 else "Prozessorkerne",
                disk_gb=disk_gb,
            )
    return schemas


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
            "Moegliche Namen: aussehen (hell/dunkel), farbschema (standard, nord, "
            "catppuccin, gruvbox, tokyo_night, solarized, dracula, rose_pine), "
            "ort, sprache, land, suchmaschine, "
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


TOOL_SCHEMAS.extend([NEWS_SCHEMA, PLACES_SCHEMA, PROFILE_SCHEMA, CALC_SCHEMA])


@dataclass
class ToolStats:
    """Was in einem Durchlauf passiert ist -- fuer Ausgabe und Verlauf."""

    searches: list[str] = field(default_factory=list)
    news_searches: list[str] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    products: list[Product] = field(default_factory=list)
    visuals: list[dict[str, str]] = field(default_factory=list)
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
    google_writes: int = 0
    storage_reads: int = 0
    storage_writes: int = 0
    settings_changed: int = 0
    #: Aufrufe in der Werkstatt -- ausfuehren, schreiben, lesen.
    vm_calls: int = 0

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
            + self.google_writes
            + self.storage_reads
            + self.storage_writes
            + self.settings_changed
            + self.vm_calls
        )

    def reset(self) -> None:
        self.searches.clear()
        self.news_searches.clear()
        self.fetched.clear()
        self.skipped.clear()
        self.products.clear()
        self.visuals.clear()
        self.sources.clear()
        self.calculations = 0
        self.notes_saved = 0
        self.questions = 0
        self.memories = 0
        self.lan_scans = 0
        self.ha_reads = 0
        self.ha_calls = 0
        self.google_reads = 0
        self.google_writes = 0
        self.storage_reads = 0
        self.storage_writes = 0
        self.settings_changed = 0
        self.vm_calls = 0


class Toolbox:
    """Fuehrt die Tool-Calls des Agenten aus."""

    def __init__(
        self,
        settings: Settings,
        cache: Cache | None = None,
        on_event: EventHook | None = None,
        spec_extractor: SpecExtractor | None = None,
        fetcher: Fetcher | None = None,
        visual_inspector: VisualInspector | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.on_event = on_event
        self.spec_extractor = spec_extractor
        self.visual_inspector = visual_inspector
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
        #: In der Gegenpruefung: Quellen, die schon gelesen wurden. Sie fallen
        #: dann aus den Suchtreffern heraus -- eine zweite Runde, die dieselben
        #: Seiten noch einmal liest, ist keine zweite Runde.
        self.avoid_domains: set[str] = set()
        #: Traegt jede gelesene Seite selbst in `avoid_domains` ein. Teilen
        #: sich mehrere Agenten dieselbe Menge, meidet jeder von ihnen, was ein
        #: anderer schon gelesen hat -- so kommen aus einer Zerlegung wirklich
        #: verschiedene Quellen zurueck und nicht dreimal dieselbe Seite.
        self.claim_sources = False
        #: Der Google-Zugriff wird erst beim ersten Aufruf gebaut -- ohne
        #: verbundenes Konto soll gar nichts davon geladen werden.
        self._google_client: Any = None
        #: Ebenso das Lager -- ohne eingetragene Adresse wird nichts gebaut.
        self._storage_client: Any = None
        #: Und die Werkstatt: sie entsteht erst, wenn wirklich Code laufen soll.
        self._sandbox_box: Any = None
        #: Wird gerufen, wenn sich eine Einstellung geaendert hat. Die
        #: Oberflaeche baut daraufhin den Agenten fuer die naechste Frage neu.
        self.on_settings_changed: Any = None

    def close(self, *, close_fetcher: bool = True) -> None:
        """Gibt eigene Ressourcen frei.

        Mehrere Subagenten teilen sich absichtlich einen Fetcher. In diesem
        Fall schliesst nur ihr Koordinator ihn, nachdem alle fertig sind.
        """
        if close_fetcher:
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
        wanted = wanted[:limit]

        # Und dann der Ortsfilter. Bisher war er eine Bitte im Systemtext --
        # das Hauptmodell hielt sich meistens daran, die Subagenten sahen ihn
        # nie, und bei "Cafes mit WLAN" kamen Treffer aus dem ganzen
        # Sprachraum zurueck. Jetzt kommt eine Fassung MIT Ort dazu, sofern
        # nicht ohnehin schon einer dasteht. Sie ersetzt keine der anderen
        # Anfragen, sondern tritt daneben: beim Mischen (RRF) gewinnt, was
        # mehrere Listen uebereinstimmend oben haben -- bei einer oertlichen
        # Frage also das Oertliche, bei einer allgemeinen bleibt es beim
        # Bisherigen.
        ort = (self.settings.location or "").strip()
        if ort and not any(mentions_place(anfrage, ort) for anfrage in wanted):
            mit_ort = with_place(keywords(wanted[0]) or wanted[0], ort)
            if mit_ort.lower() not in {anfrage.lower() for anfrage in wanted}:
                wanted.append(mit_ort)
        return wanted

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

        skipped = 0
        if self.avoid_domains:
            fresh = [
                result
                for result in results
                if (result.source_domain or domain_of(result.url)) not in self.avoid_domains
            ]
            skipped = len(results) - len(fresh)
            # Bleibt nach dem Aussortieren nichts uebrig, ist die alte Liste
            # immer noch besser als gar keine -- dann sagt die Notiz es eben.
            if fresh:
                results = fresh

        self._emit("search_done", query=label, hits=len(results), queries=used)
        payload = {
            "query": label,
            "queries": used,
            "country": country,
            "lang": lang,
            "results": [result.as_tool_dict() for result in results],
        }
        if skipped:
            payload["note"] = (
                f"{skipped} Treffer von bereits gelesenen Seiten sind aussortiert -- "
                "du sollst in dieser Runde andere Quellen finden."
            )
        return payload

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
            domain = page.source_domain or domain_of(url)
            self.stats.sources.append(
                {
                    "url": page.final_url or url,
                    "title": page.title,
                    "domain": domain,
                }
            )
            if self.claim_sources and domain:
                # Ab jetzt gehoert diese Seite mir -- andere Agenten suchen
                # weiter, ohne sie noch einmal vorgeschlagen zu bekommen.
                self.avoid_domains.add(domain)
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
        from aquaticy.google import Google, TokenStore

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
                "Einstellungen auf 'Verbinden' -- oder `aquaticy google` im Terminal."
            )
        return ""

    def calendar_events(
        self, days: int = 7, query: str = "", count: int = 10
    ) -> dict[str, Any]:
        """Liest Termine aus dem Google Kalender."""
        from aquaticy.google import GoogleError

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
        from aquaticy.google import GoogleError

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
        from aquaticy.google import GoogleError

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

    # -- Werkzeug: bei Google etwas aendern -------------------------------
    # Drei Dinge sind hier festgeschrieben und stehen nicht im Prompt, wo ein
    # geschickter Satz sie wegreden koennte:
    #
    # 1. Ohne "Aendern erlaubt" gibt es diese Werkzeuge gar nicht (agent.py).
    # 2. Vor jedem Schreiben wird gefragt. Kann niemand antworten, wird nicht
    #    geschrieben -- lieber gar nichts als etwas Ungefragtes im Kalender.
    # 3. Verschickt wird nie eine Mail. Es gibt nur Entwuerfe, und dafuer
    #    fehlt Aquaticy sogar das Recht bei Google selbst.
    def _google_write_ready(self) -> str:
        problem = self._google_ready()
        if problem:
            return problem
        if not getattr(self.settings, "google_write", False):
            return (
                "Aquaticy AI darf bei Google nur lesen. Der Nutzer schaltet das "
                "Aendern in den Einstellungen unter 'Gmail & Kalender' frei und "
                "verbindet danach neu. Sag ihm das, statt es zu umgehen."
            )
        return ""

    def _confirm(self, question: str) -> str:
        """Fragt nach. Returns: "" wenn zugestimmt, sonst der Grund dagegen."""
        if self.ask_handler is None:
            return (
                "Hier kann gerade niemand bestaetigen -- und ohne Bestaetigung "
                "wird bei Google nichts geaendert. Bitte den Nutzer, es selbst "
                "zu tun."
            )
        self._emit("ask", question=question, options=["ja", "nein"])
        answer = (self.ask_handler(question, ["ja", "nein"]) or "").strip().lower()
        self._emit("ask_done", question=question, answer=answer)
        if answer in ("ja", "j", "yes", "ok", "mach", "los", "klar"):
            return ""
        return f"Vom Nutzer nicht bestaetigt (Antwort: {answer!r})."

    def calendar_add(
        self,
        summary: str,
        start: str,
        end: str = "",
        description: str = "",
        location: str = "",
        whole_day: bool = False,
    ) -> dict[str, Any]:
        """Traegt einen Termin ein -- nach Rueckfrage."""
        from aquaticy.google import GoogleError

        problem = self._google_write_ready()
        if problem:
            return {"error": problem}
        wann = start + (f" bis {end}" if end else "")
        nein = self._confirm(f"Soll ich \u201e{summary}\u201c am {wann} eintragen?")
        if nein:
            return {"created": False, "note": nein}
        self._emit("calendar_write", what="anlegen", summary=summary, start=start)
        try:
            answer = self._google().create_event(
                summary,
                start,
                end,
                description=description,
                location=location,
                whole_day=bool(whole_day),
            )
        except GoogleError as exc:
            self._emit("error", message=str(exc))
            return {"error": str(exc)}
        self.stats.google_writes += 1
        self._emit("calendar_written", summary=summary)
        return answer

    def calendar_edit(
        self,
        event_id: str,
        summary: str = "",
        start: str = "",
        end: str = "",
        description: str = "",
        location: str = "",
        whole_day: bool = False,
    ) -> dict[str, Any]:
        """Aendert einen Termin -- nach Rueckfrage."""
        from aquaticy.google import GoogleError

        problem = self._google_write_ready()
        if problem:
            return {"error": problem}
        was = ", ".join(
            teil
            for teil in (
                f"Titel \u201e{summary}\u201c" if summary else "",
                f"Zeit {start}" + (f" bis {end}" if end else "") if start else "",
                f"Ort {location}" if location else "",
                "Notiz" if description else "",
            )
            if teil
        )
        nein = self._confirm(f"Soll ich den Termin wirklich aendern ({was})?")
        if nein:
            return {"updated": False, "note": nein}
        self._emit("calendar_write", what="aendern", summary=summary or event_id)
        try:
            answer = self._google().update_event(
                event_id,
                summary=summary,
                start=start,
                end=end,
                description=description,
                location=location,
                whole_day=bool(whole_day),
            )
        except GoogleError as exc:
            self._emit("error", message=str(exc))
            return {"error": str(exc)}
        self.stats.google_writes += 1
        self._emit("calendar_written", summary=summary or event_id)
        return answer

    def mail_draft(self, subject: str, body: str, to: str = "", cc: str = "") -> dict[str, Any]:
        """Legt einen Mail-Entwurf an -- nach Rueckfrage. Verschickt wird nie."""
        from aquaticy.google import GoogleError

        problem = self._google_write_ready()
        if problem:
            return {"error": problem}
        an = to or "ohne Empfaenger"
        nein = self._confirm(
            f"Soll ich einen Entwurf an {an} mit dem Betreff "
            f"\u201e{subject}\u201c anlegen? (Verschickt wird nichts.)"
        )
        if nein:
            return {"drafted": False, "note": nein}
        self._emit("mail_write", subject=subject, to=to)
        try:
            answer = self._google().create_draft(to, subject, body, cc=cc)
        except GoogleError as exc:
            self._emit("error", message=str(exc))
            return {"error": str(exc)}
        self.stats.google_writes += 1
        self._emit("mail_written", subject=subject)
        return answer

    # -- Werkzeug: Lagerverwaltung ----------------------------------------
    def _storage(self) -> Any:
        """Der Zugriff aufs Lager, einmal je Toolbox aufgebaut."""
        from aquaticy.storage import Storage

        if self._storage_client is None:
            self._storage_client = Storage(
                self.settings.storage_url, self.settings.storage_access
            )
        return self._storage_client

    def _storage_ready(self) -> str:
        """Leerer String heisst "kann losgehen", sonst steht da der Grund."""
        from aquaticy.storage import normalize_access

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
        from aquaticy.storage import StorageError

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
        from aquaticy.storage import StorageError

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
        from aquaticy.storage import StorageError

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
        from aquaticy.storage import StorageError

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
        from aquaticy import preferences

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

        if preference.name in (preferences.APPEARANCE, preferences.PALETTE):
            # Aussehen ist Sache des Browsers -- es steht in keiner Datei. Die
            # Oberflaeche hoert auf dieses Ereignis und stellt es um.
            if preference.name == preferences.APPEARANCE:
                self._emit("appearance", theme="light" if stored == "hell" else "dark")
            else:
                self._emit("appearance", palette=preferences.PALETTE_IDS[stored])
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
    # -- Werkzeug: die Werkstatt ------------------------------------------
    def _sandbox(self) -> Any:
        """Die gemeinsame Werkstatt -- gebaut beim ersten Zugriff."""
        from aquaticy import sandbox as werkstatt

        if self._sandbox_box is None:
            self._sandbox_box = werkstatt.shared(self.settings, on_event=self._emit_pair)
        else:
            self._sandbox_box.on_event = self._emit_pair
        return self._sandbox_box

    def _emit_pair(self, event: str, payload: dict[str, Any]) -> None:
        """Die Werkstatt meldet als (Name, Nutzlast) -- der Rest als Schlagworte."""
        if self.on_event:
            self.on_event(event, payload)

    def vm_run(self, command: str, timeout: int = 0) -> dict[str, Any]:
        """Fuehrt einen Befehl in der Werkstatt aus."""
        from aquaticy.sandbox import COMMAND_TIMEOUT, SandboxUnavailable

        try:
            box = self._sandbox()
            result = box.run(command, timeout=int(timeout or COMMAND_TIMEOUT))
        except SandboxUnavailable as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # pragma: no cover - Laufzeit meldet Unerwartetes
            return {"error": f"Die Werkstatt antwortet nicht: {exc}"}
        self.stats.vm_calls += 1
        return result.as_dict()

    def vm_write(self, path: str, text: str) -> dict[str, Any]:
        """Legt eine Datei in der Werkstatt an."""
        from aquaticy.sandbox import SandboxUnavailable

        try:
            answer = self._sandbox().write(path, text)
        except SandboxUnavailable as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # pragma: no cover
            return {"error": f"Die Werkstatt antwortet nicht: {exc}"}
        self.stats.vm_calls += 1
        return answer

    def vm_read(self, path: str) -> dict[str, Any]:
        """Liest eine Datei aus der Werkstatt."""
        from aquaticy.sandbox import SandboxUnavailable

        try:
            answer = self._sandbox().read(path)
        except SandboxUnavailable as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # pragma: no cover
            return {"error": f"Die Werkstatt antwortet nicht: {exc}"}
        self.stats.vm_calls += 1
        return answer

    def vm_files(self, path: str = "") -> dict[str, Any]:
        """Listet auf, was in der Werkstatt liegt."""
        from aquaticy.sandbox import WORKDIR, SandboxUnavailable

        try:
            dateien = self._sandbox().list_files(path or WORKDIR)
        except SandboxUnavailable as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # pragma: no cover
            return {"error": f"Die Werkstatt antwortet nicht: {exc}"}
        self.stats.vm_calls += 1
        return {"files": dateien, "count": len(dateien)}

    def blender_run(self, script: str, filename: str = "", timeout: int = 0) -> dict[str, Any]:
        """Schreibt ein bpy-Skript in die Werkstatt und laesst Blender es headless laufen.

        Zwei Schritte in einem: die Datei muss existieren, bevor Blender sie
        oeffnen kann. Fehlt Blender im Werkstatt-Abbild, kommt das als ganz
        gewoehnlicher Fehler zurueck ("command not found") -- kein Sonderfall.
        """
        import shlex

        from aquaticy.sandbox import BLENDER_TIMEOUT, SandboxUnavailable

        name = (filename or "design.py").strip() or "design.py"
        if not name.endswith(".py"):
            name += ".py"
        try:
            box = self._sandbox()
            geschrieben = box.write(name, script)
            if "error" in geschrieben:
                return geschrieben
            pfad = geschrieben["written"]
            result = box.run(
                f"blender --background --python {shlex.quote(pfad)}",
                timeout=int(timeout or BLENDER_TIMEOUT),
            )
        except SandboxUnavailable as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # pragma: no cover
            return {"error": f"Die Werkstatt antwortet nicht: {exc}"}
        self.stats.vm_calls += 1
        payload = result.as_dict()
        payload["script"] = pfad
        return payload

    @staticmethod
    def _anzahl(wert: Any) -> int:
        """Eine Trefferzahl aus dem, was das Modell geschickt hat.

        Manche Modelle schreiben "fuenf" oder schicken gleich eine Liste. Das
        ist kein Grund, den Aufruf scheitern zu lassen -- die Zahl ist ein
        Wunsch, und ohne sie gilt eben die Voreinstellung.
        """
        try:
            return int(wert or 0)
        except (TypeError, ValueError):
            return 0

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Fuehrt den Tool-Call *name* mit *arguments* aus."""
        if name == "web_search":
            more = arguments.get("queries") or []
            return self.web_search(
                query=str(arguments.get("query", "")),
                count=self._anzahl(arguments.get("count")),
                country=str(arguments.get("country") or ""),
                lang=str(arguments.get("lang") or ""),
                # Manche Modelle schicken einen String statt einer Liste.
                queries=[str(item) for item in more] if isinstance(more, list) else [str(more)],
            )
        if name == "fetch_page":
            return self.fetch_page(url=str(arguments.get("url", "")))
        if name == "inspect_public_visual":
            if self.visual_inspector is None:
                return {"error": "Es ist kein Vision-Modell ausgewählt."}
            source_url = str(arguments.get("url", "")).strip()
            loaded = None
            loader = getattr(self._fetcher, "load_public_visual", None)
            if loader is not None:
                import base64

                loaded, error = loader(source_url)
                url = loaded.url if loaded else ""
                vision_url = (
                    f"data:{loaded.content_type};base64,"
                    + base64.b64encode(loaded.content).decode("ascii")
                    if loaded
                    else ""
                )
                mime_type = loaded.content_type if loaded else ""
            else:
                url, error = self._fetcher.find_public_visual(source_url)
                vision_url = url
                mime_type = ""
            if error:
                return {"error": error}
            question = str(arguments.get("question", "")).strip()
            try:
                analysis = self.visual_inspector(vision_url, question)
            except Exception as exc:
                return {"error": str(exc), "url": url}
            checked_at = datetime.now(UTC).isoformat(timespec="seconds")
            media_id = ""
            if loaded:
                try:
                    from aquaticy.media import save_snapshot

                    media_id = save_snapshot(
                        self.settings.data_dir, loaded.content, loaded.content_type
                    )
                except (OSError, ValueError):
                    media_id = ""
            self.stats.fetched.append(url)
            visual = {
                "url": url,
                "source_url": source_url,
                "title": question or "Öffentliches aktuelles Bild",
                "kind": "public_visual",
            }
            if loaded:
                visual["captured_at"] = checked_at
            if media_id:
                visual["media_id"] = media_id
            if mime_type:
                visual["mime_type"] = mime_type
            self.stats.visuals.append(visual)
            return {
                **visual,
                "checked_at": checked_at,
                "observation": analysis,
            }
        if name == "search_news":
            return self.search_news(
                query=str(arguments.get("query", "")),
                count=self._anzahl(arguments.get("count")),
            )
        if name == "find_profiles":
            roh = arguments.get("platforms") or []
            return self.find_profiles(
                name=str(arguments.get("name", "")),
                platforms=[str(item) for item in roh] if isinstance(roh, list) else [],
            )
        if name == "local_places":
            try:
                radius = float(arguments.get("radius_km") or 0)
            except (TypeError, ValueError):
                radius = 0.0
            return self.local_places(
                what=str(arguments.get("what", "")),
                where=str(arguments.get("where") or ""),
                radius_km=radius,
            )
        if name == "vm_run":
            return self.vm_run(
                command=str(arguments.get("command", "")),
                timeout=int(arguments.get("timeout") or 0),
            )
        if name == "vm_write":
            return self.vm_write(
                path=str(arguments.get("path", "")), text=str(arguments.get("text", ""))
            )
        if name == "vm_read":
            return self.vm_read(path=str(arguments.get("path", "")))
        if name == "vm_files":
            return self.vm_files(path=str(arguments.get("path", "") or ""))
        if name == "blender_run":
            return self.blender_run(
                script=str(arguments.get("script", "")),
                filename=str(arguments.get("filename", "") or ""),
                timeout=int(arguments.get("timeout") or 0),
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
        if name == "calendar_add":
            return self.calendar_add(
                summary=str(arguments.get("summary", "")),
                start=str(arguments.get("start", "")),
                end=str(arguments.get("end", "") or ""),
                description=str(arguments.get("description", "") or ""),
                location=str(arguments.get("location", "") or ""),
                whole_day=bool(arguments.get("whole_day", False)),
            )
        if name == "calendar_edit":
            return self.calendar_edit(
                event_id=str(arguments.get("event_id", "")),
                summary=str(arguments.get("summary", "") or ""),
                start=str(arguments.get("start", "") or ""),
                end=str(arguments.get("end", "") or ""),
                description=str(arguments.get("description", "") or ""),
                location=str(arguments.get("location", "") or ""),
                whole_day=bool(arguments.get("whole_day", False)),
            )
        if name == "mail_draft":
            return self.mail_draft(
                subject=str(arguments.get("subject", "")),
                body=str(arguments.get("body", "")),
                to=str(arguments.get("to", "") or ""),
                cc=str(arguments.get("cc", "") or ""),
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
        # Auch hier gilt der Ortsfilter: "Baustellen" ohne Ort ist eine
        # andere Frage als "Baustellen Bremen".
        query = with_place((query or "").strip(), self.settings.location)
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

    # -- Werkzeug 4: Profile ----------------------------------------------
    def find_profiles(self, name: str, platforms: list[str] | None = None) -> dict[str, Any]:
        """Sucht, was es zu einem Namen ausserhalb der eigenen Website gibt.

        Eine Suche nach einer Marke liefert die Website und danach zehn
        Portale. Was fehlt, ist genau das, was ein Mensch als naechstes
        aufmacht: Instagram, LinkedIn, die Bewertungen. Dort steht oft mehr
        und aktuelleres als auf der Seite -- und mancher Laden hat ueberhaupt
        nur ein Profil.

        Gesucht wird ueber die Suchmaschine mit `site:`, nicht durch
        Durchprobieren von Adressen: wir fragen, was oeffentlich indexiert
        ist. Was sich beim Lesen sperrt, bleibt beim Titel und dem Ausschnitt.
        """
        name = " ".join((name or "").split())
        if not name:
            return {"profiles": [], "error": "Ohne Namen geht es nicht."}

        gewuenscht = {str(item).strip().lower() for item in (platforms or []) if str(item).strip()}
        sites = [
            (label, domain)
            for label, domain in PROFILE_SITES
            if not gewuenscht or label.lower() in gewuenscht or domain in gewuenscht
        ]
        if not gewuenscht:
            sites = sites[:MAX_PROFILE_SITES]
        sites = sites[:MAX_PROFILE_SITES]

        key = cache_key("profiles", name.lower(), "|".join(domain for _, domain in sites))
        cached = self.cache.get(key) if self.cache else None
        self.stats.searches.append(f"Profile: {name}")
        self._emit("profiles", name=name, platforms=[label for label, _ in sites])
        if cached is not None and isinstance(cached, dict):
            self._emit("profiles_done", name=name, hits=len(cached.get("profiles", [])))
            return dict(cached)

        def eine(position: int, label: str, domain: str) -> tuple[str, list[SearchResult]]:
            # Leicht versetzt starten: acht Anfragen in derselben Millisekunde
            # sind fuer eine offene Suchmaschine ein Ausschlag.
            if position:
                time.sleep(min(position * 0.25, 2.0))
            try:
                treffer = search_web(
                    f'{name} site:{domain}',
                    count=3,
                    country=self.settings.country,
                    lang=self.settings.lang,
                    backend=self.settings.search_backend,
                    engines=self.settings.search_engines,
                    instance_url=self.settings.searxng_url,
                )
            except SearchError:
                return label, []
            return label, treffer

        gefunden: list[dict[str, str]] = []
        leer: list[str] = []
        with ThreadPoolExecutor(max_workers=max(1, min(4, len(sites)))) as pool:
            auftraege = [
                pool.submit(eine, position, label, domain)
                for position, (label, domain) in enumerate(sites)
            ]
            ergebnisse = {}
            for auftrag in auftraege:
                try:
                    label, treffer = auftrag.result()
                except Exception:
                    continue
                ergebnisse[label] = treffer

        for label, domain in sites:
            treffer = ergebnisse.get(label, [])
            # Suchmaschinen liefern zu `site:` gern auch Nachbarn -- was nicht
            # von der Plattform kommt, gehoert hier nicht hin.
            passend = [
                item
                for item in treffer
                if domain in (item.source_domain or domain_of(item.url) or "")
            ]
            if not passend:
                leer.append(label)
                continue
            bester = passend[0]
            self.seen_results.setdefault(bester.url, bester)
            gefunden.append(
                {
                    "platform": label,
                    "url": bester.url,
                    "title": (bester.title or "").strip(),
                    "snippet": (bester.snippet or "").strip()[:300],
                }
            )

        payload: dict[str, Any] = {
            "name": name,
            "profiles": gefunden,
            "not_found": leer,
            "note": (
                "Oeffentliche Treffer, ueber die Suchmaschine gefunden. Lies die "
                "Adressen mit `fetch_page` weiter; sperrt sich eine Seite "
                "(Instagram und LinkedIn tun das oft), nimm Titel und Ausschnitt "
                "und sag dazu, woher du es hast. Pruef bei jedem Treffer, ob er "
                "wirklich zu dem Namen gehoert -- gleiche Namen gibt es haeufig."
            ),
        }
        if self.cache:
            self.cache.set(key, payload, kind="profiles", label=name, ttl=6 * 3600)
        self._emit("profiles_done", name=name, hits=len(gefunden))
        return payload

    # -- Werkzeug 5: die Karte --------------------------------------------
    def local_places(self, what: str, where: str = "", radius_km: float = 0.0) -> dict[str, Any]:
        """Sucht Orte in OpenStreetMap statt in einer Suchmaschine.

        Das haerteste Suchproblem ist die kleine Sache um die Ecke: der
        Fahrradladen in der Nebenstrasse, die Werkstatt ohne Website. Eine
        Suchmaschine kennt sie nicht oder erst auf Seite vier -- in der Karte
        stehen sie, mit Adresse, Telefon und, wenn es eine gibt, der Website.

        Faellt die Karte aus, ist das kein Abbruch: es kommt ein Ergebnis mit
        Begruendung zurueck und die Antwort entsteht eben aus dem Web.
        """
        from aquaticy.places import DEFAULT_RADIUS_M, PlacesError, find_places

        what = " ".join((what or "").split())
        where = " ".join((where or "").split()) or (self.settings.location or "").strip()
        if not what:
            return {"results": [], "error": "Ohne Suchwort geht es nicht."}
        if not where:
            return {
                "results": [],
                "error": (
                    "Kein Ort bekannt. Nenn einen Ort in `where` oder stell einen "
                    "Ortsfilter ein -- ohne Ort gibt es keine Umgebung."
                ),
            }
        try:
            radius_m = int(float(radius_km or 0) * 1000) or DEFAULT_RADIUS_M
        except (TypeError, ValueError, OverflowError):
            return {
                "results": [],
                "error": "Der Umkreis muss eine Zahl in Kilometern sein.",
            }

        key = cache_key("places", what.lower(), where.lower(), radius_m)
        cached = self.cache.get(key) if self.cache else None
        self._emit("places", what=what, where=where)
        if cached is not None:
            treffer = list(cached.get("results", [])) if isinstance(cached, dict) else []
            self._emit("places_done", what=what, hits=len(treffer))
            return dict(cached) if isinstance(cached, dict) else {"results": treffer}

        try:
            orte, ortsname = find_places(
                what,
                where,
                self.settings.user_agent,
                radius_m=radius_m,
                timeout=max(15.0, self.settings.fetch_timeout),
            )
        except PlacesError as exc:
            self._emit("places_done", what=what, hits=0)
            self._emit(
                "note",
                text=f"{exc} Ich suche stattdessen im Web weiter.",
            )
            return {"results": [], "error": str(exc), "fallback": "web_search"}

        payload: dict[str, Any] = {
            "what": what,
            "place": ortsname,
            "radius_km": round(radius_m / 1000, 1),
            "results": [ort.as_dict() for ort in orte],
        }
        if not orte:
            payload["note"] = (
                "In der Karte steht dazu nichts im Umkreis. Ein groesserer Umkreis "
                "oder ein anderes Suchwort kann helfen."
            )
        else:
            payload["note"] = (
                "Aus OpenStreetMap (Stand der Karte, nicht der Betreiber). Was eine "
                "Website hat, kannst du mit `fetch_page` nachlesen; Oeffnungszeiten "
                "gehoeren gegengeprueft, sie veralten in der Karte am schnellsten."
            )
        if self.cache:
            # Die Karte aendert sich langsam -- ein Tag ist reichlich.
            self.cache.set(key, payload, kind="places", label=f"{what} in {where}")
        self._emit("places_done", what=what, hits=len(orte))
        return payload

    # -- Werkzeug 6: Rechner ----------------------------------------------
    def calculate(self, expression: str) -> dict[str, Any]:
        """Exakte Arithmetik -- damit das Modell nie selbst rechnen muss."""
        from aquaticy.calc import CalcError, calculate_pretty

        self.stats.calculations += 1
        self._emit("calculate", expression=expression)
        try:
            return {"expression": expression, "result": calculate_pretty(expression)}
        except CalcError as exc:
            return {"expression": expression, "error": str(exc)}

    # -- Werkzeug 7: Merkzettel (nur Hauptagent) --------------------------
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
            from aquaticy.memory import Memory

            self._memory_store = Memory(
                self.settings.db_path, self.settings.data_dir, self.settings.memory_key
            )
        return self._memory_store

    def save_memory(self, text: str, topic: str = "") -> dict[str, Any]:
        """Legt eine Textnotiz im Langzeitspeicher ab."""
        from aquaticy.memory import MemoryFull

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
        from aquaticy.lan import NotPrivate, scan

        if not self.settings.lan_enabled:
            return {"error": "Der Netzzugriff ist abgeschaltet (AQUATICY_LAN_ENABLED=false)."}
        target = (subnet or self.settings.lan_subnet or "").strip()
        self._emit("lan_scan", subnet=target or "eigenes Netz")
        try:
            devices = scan(target, quick=not thorough)
        except NotPrivate as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {
                "error": (
                    f"{exc} Trag dein Netz unter AQUATICY_LAN_SUBNET ein, z.B. 192.168.1.0/24."
                )
            }
        except OSError as exc:
            return {"error": f"Netzdurchlauf fehlgeschlagen: {exc}"}

        from aquaticy.lan import container_hint

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

        from aquaticy.lan import FULL_PORTS, check_host

        if not self.settings.lan_enabled:
            return {"error": "Der Netzzugriff ist abgeschaltet (AQUATICY_LAN_ENABLED=false)."}
        host = (host or "").strip()
        if not host:
            return {"error": "Keine Adresse angegeben."}
        try:
            address = socket.gethostbyname(host)
        except OSError:
            return {"host": host, "reachable": False, "error": f"{host} ist nicht aufloesbar."}

        import ipaddress

        from aquaticy.lan import is_private_net

        try:
            network = ipaddress.ip_network(f"{address}/32")
        except ValueError:
            return {"host": host, "reachable": False, "error": "Keine gueltige IPv4-Adresse."}
        if not is_private_net(network):
            return {
                "host": host,
                "reachable": False,
                "error": f"{address} liegt ausserhalb des privaten Netzes -- Aquaticy AI prueft "
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
        from aquaticy.homeassistant import MAX_ENTITIES, HomeAssistantError, from_settings

        client = from_settings(self.settings)
        if not client.configured:
            from aquaticy.lan import container_hint

            hint = container_hint()
            return {
                "error": (
                    "Home Assistant ist nicht verbunden. Der Nutzer richtet das mit "
                    "`aquaticy connect-ha` ein -- das dauert eine Minute."
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
        from aquaticy.homeassistant import (
            ALLOWED_DOMAINS,
            PROTECTED_DOMAINS,
            HomeAssistantError,
            from_settings,
        )

        client = from_settings(self.settings)
        if not client.configured:
            return {"error": "Home Assistant ist nicht verbunden (`aquaticy connect-ha`)."}
        if not self.settings.ha_control:
            return {
                "error": (
                    "Aquaticy AI darf nur nachsehen, nicht schalten. Der Nutzer schaltet das "
                    "in den Einstellungen frei (AQUATICY_HA_CONTROL=true). Sag ihm das, "
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
                    f"Aquaticy AI schaltet im Bereich '{domain}' nicht. Erlaubt sind: "
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
