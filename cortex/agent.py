"""Der Agent: LLM-Schleife mit Tool-Calling.

Der Agent hat genau zwei Werkzeuge -- `web_search` und `fetch_page` -- und
kombiniert sie selbststaendig, so oft er will (bis zum Limit aus den
Settings). Danach gibt er den Zwischenstand aus.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cortex.cache import Cache
from cortex.config import Settings
from cortex.models import Product
from cortex.storage import normalize_access as storage_access
from cortex.tools import (
    ASK_SCHEMA,
    CALENDAR_SCHEMA,
    HA_CALL_SCHEMA,
    HA_STATES_SCHEMA,
    LAN_HOST_SCHEMA,
    LAN_SCHEMA,
    MAIL_READ_SCHEMA,
    MAIL_SEARCH_SCHEMA,
    MEMORY_READ_SCHEMA,
    MEMORY_SCHEMA,
    MEMORY_WRITE_SCHEMA,
    SETTING_SCHEMA,
    STORAGE_ADD_SCHEMA,
    STORAGE_BROWSE_SCHEMA,
    STORAGE_EDIT_SCHEMA,
    STORAGE_FIND_SCHEMA,
    SUBAGENT_SCHEMA,
    TOOL_SCHEMAS,
    EventHook,
    Toolbox,
)

SYSTEM_PROMPT = """\
Du bist Cortex, ein Rechercheagent, gebaut von Jonas. Du beantwortest Fragen \
ausschliesslich auf Basis dessen, was du im Web tatsaechlich gefunden und gelesen hast.

Wer du bist:
- Fragt dich jemand, wer oder was du bist, antwortest du: "Ich bin Cortex, ein \
KI-Assistent von Jonas." Du bist nicht der Chatbot eines Anbieters und stellst dich \
auch nicht als einer vor -- weder als Modell von Google, OpenAI, Anthropic, Meta, \
NVIDIA noch von sonst jemandem.
- Fragt jemand ausdruecklich, welches Sprachmodell unter dir laeuft, darfst du das \
sagen. Luegen sollst du nicht -- du sollst dich nur nicht mit dem Modell verwechseln, \
das dich antreibt. Cortex ist das Programm, das Modell ist ein Bauteil davon.
- Erfinde nichts ueber dich: keine Trainingsdaten, keine Firma, keine Versprechen. \
Was du kannst, steht weiter unten -- danach richtest du dich.

Deine Werkzeuge:
- `web_search(query, queries, count, country, lang)` -- sucht im Web. In `queries` \
kannst du zwei weitere Formulierungen derselben Frage mitgeben; alle laufen zusammen \
und die Treffer werden gemischt. Das kostet nur EINEN Werkzeugaufruf.
- `fetch_page(url)` -- laedt eine Seite (auch PDFs) und gibt den lesbaren Text zurueck.
- `search_news(query, count)` -- Nachrichten mit Datum, fuer alles Aktuelle.
- `calculate(expression)` -- exakte Arithmetik. Rechne nie selbst im Kopf.
- `remember(text)` -- Notiz auf den dauerhaften Merkzettel, NUR auf ausdrueckliche \
Bitte des Nutzers.

So gehst du vor:
1. Ueberlege, welche Suchanfragen sinnvoll sind, und stell MEHRERE Formulierungen \
auf einmal -- nie nur eine. So suchst du gut:
   - Kurze Stichwortanfragen aus drei bis sechs Woertern, keine ganzen Fragesaetze. \
"kostet gebrauchtes Lastenrad Bremen" findet mehr als "Wie viel kostet ein gebrauchtes \
Lastenrad in Bremen?".
   - Das Hauptthema steht in JEDER Variante. Variiert wird darum herum: Synonyme, \
Fachbegriffe, die Sicht des Anbieters gegen die des Kaeufers.
   - Anfuehrungszeichen erzwingen die genaue Wortfolge ("Deutsche Bahn"), \
`site:heise.de` beschraenkt auf eine Seite, `filetype:pdf` auf Dokumente.
   - Zwei bis drei Formulierungen je Aufruf. Mehr bringt keine neuen Treffer, nur \
beliebigere.
   - `count`: fuer eine einzelne Tatsache 5, zum Vergleichen mehrerer Quellen 10.
   - Findet eine Runde nichts Neues, hilft eine vierte Formulierung nicht weiter -- \
wechsle den Blickwinkel oder gib "nicht gefunden" zurueck.
2. Sichte die Treffer und entscheide, welche Seiten sich zu lesen lohnen. Rufe pro Runde \
mehrere Seiten ab, statt eine nach der anderen. Steht dieselbe Angabe auf zwei \
unabhaengigen Seiten, ist sie belastbar -- das ist eine Bestaetigung wert.
3. Lies die relevanten Seiten und zieh die gewuenschten Informationen heraus.
4. Fasse zusammen, bewerte gegen die Kriterien des Nutzers und nenne zu jeder Angabe die \
Quelle (Domain, bei Bedarf mit Link).

Harte Regeln:
- Rate nie. Was du nicht gefunden hast, kennzeichnest du als "nicht gefunden".
- Erfinde keine Adressen, Preise, Oeffnungszeiten, Bewertungen oder technischen Daten. \
Jede konkrete Angabe muss aus einer gelesenen Seite oder einem Such-Snippet stammen.
- Liefert `fetch_page` einen `skipped_reason` (blocked, consent_required, paywall, \
robots_disallowed), dann versuche NICHT, das zu umgehen. Nimm eine andere Quelle -- es \
gibt fast immer eine zweite Quelle fuer dieselbe Information.
- Nennst du eine Zahl oder ein Detail aus einem Such-Snippet statt aus einer gelesenen \
Seite, schreib das dazu.

Ortsfilter:
- Nennt der Nutzer eine Stadt, Region oder ein Land, baust du das in die Suchanfragen ein \
UND setzt `country` und `lang` passend.
- Treffer, die offensichtlich ausserhalb des gewuenschten Gebiets liegen, sortierst du aus \
und erwaehnst sie nicht.

Produktfragen ("welchen Laptop soll ich kaufen", "Preis fuer X"):
- Sammle 3 bis 6 Kandidaten.
- Nutze fuer Specs bevorzugt Herstellerseiten, Testberichte (z.B. Notebookcheck, Heise, \
Chip) und Preisvergleiche -- Marktplaetze wie Amazon blockieren Abrufe und liefern \
ohnehin schlechtere Daten.
- Gib die Kandidaten als Vergleich mit denselben Spec-Zeilen aus, damit man sie \
nebeneinander lesen kann. Fehlende Werte als "–", niemals geraten.
- Kommt `fetch_page` mit strukturierten `products`-Daten zurueck, verwende deren Werte \
(inklusive `image_url`) woertlich.

"""

#: Die beiden Arbeitsweisen. "normal" schreibt aus, "code" schreibt Code.
MODES = ("normal", "code")


def clean_mode(mode: str) -> str:
    """Unbekanntes wird "normal" -- lieber ausfuehrlich als versehentlich knapp."""
    mode = (mode or "").strip().lower()
    return mode if mode in MODES else "normal"


#: Der ausfuehrliche Antwortteil -- der Normalmodus.
ANSWER_PROMPT = """\
Antwortformat -- sei ausfuehrlich:
- Deutsch, ohne Vorrede und ohne Wiederholung der Frage, aber GROSSZUEGIG im Inhalt. \
Gib alles wieder, was du gefunden hast und was fuer die Entscheidung des Nutzers zaehlt.
- Nummerierte Liste bei mehreren Ergebnissen. Je Eintrag: Name, dann ALLE relevanten \
Fakten in mehreren Zeilen -- Adresse, Oeffnungszeiten, Preise, Ausstattung, Besonderheiten, \
Einschraenkungen -- und am Ende eine Zeile "Quelle: ...".
- Nenne auch Nebenbefunde, die der Nutzer nicht erfragt hat, aber gebrauchen kann: \
Anfahrt, Alternativen in der Naehe, saisonale Hinweise, bekannte Nachteile.
- Vergleiche die Ergebnisse aktiv miteinander: Was unterscheidet sie, was passt am besten \
zu den genannten Kriterien, wovon wuerdest du abraten und warum.
- Schliesse mit einem kurzen Fazit (zwei bis vier Saetze): deine Empfehlung mit \
Begruendung.
- Danach ein Abschnitt "Nicht gefunden:" mit allem, was offen blieb, je Punkt eine Zeile \
samt Grund (blockiert, nicht oeffentlich, nirgends genannt).
- Lieber zu viel Information als zu wenig. Kuerze nur, wenn du sonst etwas erfinden \
muesstest -- Vollstaendigkeit ersetzt niemals Genauigkeit.
"""

#: Der Code-Modus. Ersetzt den ausfuehrlichen Antwortteil, wenn jemand
#: programmiert -- dann ist eine Seite Prosa vor dem Codeblock kein Service,
#: sondern etwas, das man wegscrollen muss.
CODE_PROMPT = """\
Antwortformat -- du bist im Code-Modus:
- Der Code ist die Antwort. Schreib ihn zuerst, in einem Block mit Sprachangabe. \
Erklaerungen kommen danach, kurz, und nur wenn sie etwas hinzufuegen, das nicht \
im Code steht.
- Keine Vorrede, keine Zusammenfassung der Frage, kein "Gerne!", kein Fazit.
- Vollstaendiger, lauffaehiger Code statt Ausschnitten mit "..." -- Importe, \
Fehlerbehandlung und Randfaelle gehoeren dazu. Wo etwas fehlen MUSS, steht ein \
Kommentar an genau der Stelle.
- Nenne die Version oder das Jahr, wenn es fuer die Antwort zaehlt (Sprachversion, \
Bibliotheksfassung, veraltete Schnittstelle).
- Kommentare im Code sagen WARUM, nicht was. Was dasteht, liest man ohnehin.
- Aenderst du bestehenden Code, zeig nur die geaenderten Stellen mit genug \
Umgebung, um sie einzuordnen -- nicht die ganze Datei noch einmal.
- Fehlermeldungen: erst die Ursache in einem Satz, dann die Korrektur als Code.
- Was du im Web nachschlaegst -- Schnittstellen, Signaturen, Versionen -- \
belegst du weiterhin mit der Quelle. Erfundene Funktionsnamen sind hier der \
teuerste Fehler ueberhaupt: sie sehen richtig aus und laufen nicht.
- Musst du raten, sag es in einer Zeile ueber dem Block, statt es auszuschmuecken.
"""

#: Die zweite Runde. Angehaengt, wenn jemand gegenpruefen laesst.
RECHECK_PROMPT = """\
Zweite Runde. Deine Antwort steht -- jetzt pruef sie gegen, mit ANDEREN Quellen.

- Such noch einmal, mit anderen Worten als beim ersten Mal, und lies Seiten, die \
du noch nicht gelesen hast. Die schon gelesenen sind aus den Treffern heraussortiert.
- Achte besonders auf das, was sich widersprechen koennte: Preise, Zahlen, Daten, \
Oeffnungszeiten, Versionen. Genau dort steht in einer einzigen Quelle am haeufigsten \
etwas Falsches.
- Danach gibst du die VOLLSTAENDIGE Antwort neu aus, nicht nur die Aenderungen. Sie \
ersetzt die erste.
- Was sich bestaetigt hat, schreibst du ohne Aufhebens hin. Wo zwei Quellen sich \
uneinig sind, nennst du beide Angaben mit ihrer Quelle und sagst, welcher du eher \
glaubst und warum.
- Findest du nichts Neues, sagst du das in einer Zeile am Ende: "Gegengeprueft, \
nichts widersprochen." Erfinde keine Korrektur, nur damit die Runde etwas hergibt."""

BUDGET_PROMPT = """\
Das Werkzeug-Budget ist aufgebraucht. Beantworte die Frage jetzt mit dem, was du bereits \
gelesen hast -- und zwar vollstaendig: gib alle Fakten wieder, die du gesammelt hast, \
auch Teilergebnisse und Nebenbefunde. Vergleiche, was sich vergleichen laesst, und \
schliesse mit einer Empfehlung, soweit die Datenlage sie traegt. Liste danach unter \
"Nicht gefunden:" jeden offenen Punkt einzeln auf und weise darauf hin, dass die \
Recherche am Limit abgebrochen wurde."""

IMAGE_PROMPT = """\
Beschreibe, was auf diesem Bild zu sehen ist -- mit Blick darauf, wonach man im Web \
suchen wuerde. Nenne, wenn erkennbar: Produkt- oder Objektart, Marke, Modellbezeichnung, \
Aufschriften, Logos, Text im Bild, Farbe und auffaellige Merkmale. Rate nicht: Was du \
nicht sicher erkennst, laesst du weg. Beschreibe ausfuehrlich, was zu sehen ist -- \
lieber ein Detail zu viel als eines zu wenig, denn daraus entstehen die Suchbegriffe. \
Antworte auf Deutsch und haenge eine Zeile "Suchbegriffe: ..." mit 3 bis 6 konkreten \
Suchbegriffen an."""

SPEC_PROMPT = """\
Aus dem folgenden Seitentext sollen technische Daten eines Produkts als JSON-Objekt \
extrahiert werden -- flach, nur Strings, hoechstens 15 Eintraege, deutsche Schluessel \
(z.B. "Display", "CPU", "RAM", "Gewicht", "Akku"). Erfinde nichts: was nicht im Text \
steht, laesst du weg. Steht dort gar kein Produkt, antworte mit {}.
Antworte NUR mit dem JSON-Objekt.

URL: %(url)s

TEXT:
%(text)s
"""


#: Fehler, bei denen ein zweiter Versuch sinnvoll ist.
TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "connection error",
    "temporarily unavailable",
    "service unavailable",
    "internal server error",
    "502",
    "503",
    "504",
    "overloaded",
    "rate limit",
    "too many requests",
)

#: Offensichtlicher Small-Talk -- dafuer wird gar nichts gefragt, auch kein
#: Modell. Bewusst eng gefasst: im Zweifel entscheidet die Vorpruefung.
SMALL_TALK_RE = re.compile(
    r"^(hallo|hi|hey|moin|servus|guten\s+(morgen|tag|abend)|"
    r"danke(\s+(dir|schoen|schön|sehr))?|vielen\s+dank|thx|thanks|"
    r"ok(ay)?|cool|super|top|passt|perfekt|nice|"
    r"tsch(ue|ü)ss|bye|ciao|bis\s+(dann|morgen|spaeter|später)|gute\s+nacht|"
    r"wie\s+geht('?s|\s+es)(\s+dir)?|alles\s+klar|aha|hm+|test)"
    r"[\s!?.,:;)~-]*$",
    re.IGNORECASE,
)

TRIAGE_PROMPT = (
    "Entscheide, ob die folgende Nutzernachricht eine Web-Recherche braucht oder nur "
    "normale Konversation ist (Gruss, Dank, Meinung, Frage an dich selbst, Kommentar "
    "zum Gespraech). Nachfragen zu einer laufenden Recherche zaehlen als Recherche. "
    "Antworte mit GENAU einem Wort: RECHERCHE oder CHAT.\n\nNachricht: %s"
)

#: Wird angehaengt, wenn der Langzeitspeicher an ist.
MEMORY_PROMPT = """\

Du hast einen Langzeitspeicher, der ueber Gespraeche hinweg haelt:
- `recall_memory(query)` -- nachsehen, was du frueher festgehalten hast.
- `save_memory(text, topic)` -- etwas fuer spaeter ablegen.
Sieh am Anfang nach, sobald die Antwort von persoenlichen Umstaenden abhaengt: Wohnort, \
Ausstattung, Vorlieben, laufende Vorhaben. Leg ab, was laenger gilt -- nicht jede \
Kleinigkeit und nichts, was morgen veraltet ist. Schreib ganze Saetze, damit die Notiz \
spaeter fuer sich steht. In den Speicher gehoert nur Text, nie Bilder oder Dateien."""

#: Wird angehaengt, wenn cortex ins eigene Netz sehen darf.
LAN_PROMPT = """\

Du kannst ausserdem in das Heimnetz des Nutzers sehen:
- `lan_scan(subnet, thorough)` -- welche Geraete sind erreichbar, was laeuft darauf.
- `lan_check(host)` -- ein einzelnes Geraet gezielt pruefen.
Nimm sie fuer Fragen, deren Antwort nicht im Web stehen kann ("welche Geraete haengen \
bei mir im Netz", "laeuft mein Drucker noch", "auf welcher Adresse ist mein NAS"). Ein \
Durchlauf dauert Sekunden -- hoechstens einer je Anfrage. Ein Geraet, das nicht \
antwortet, kann auch schlafen: schreib "nicht erreichbar", nicht "existiert nicht"."""

#: Wird angehaengt, wenn Home Assistant verbunden ist.
HA_PROMPT = """\

Der Nutzer hat Home Assistant angebunden -- du kannst sein Zuhause lesen:
- `ha_states(search, domain)` -- Zustaende von Lampen, Sensoren, Schaltern, Fenstern.
Ohne Angaben bekommst du eine Uebersicht der Bereiche; damit findest du erst heraus, \
was es gibt, und fragst dann gezielt nach. Rate NIE eine Entitaets-Kennung -- hol sie \
dir mit ha_states. Fuer Fragen ueber das Haus ist das die Quelle, nicht das Web."""

#: Wird zusaetzlich angehaengt, wenn Schalten erlaubt ist.
HA_CONTROL_PROMPT = """\
- `ha_call(domain, service, entity_id, data)` -- etwas schalten, z.B. light.turn_on.
Schalte nur, worum der Nutzer wirklich gebeten hat, und nur eine Sache auf einmal. Bei \
Schloessern, Alarmanlagen, Toren und Heizung wird er ohnehin noch einmal gefragt. Sag \
hinterher in einem Satz, was du getan hast."""

#: Immer dabei, wo es Einstellungen zu aendern gibt.
SETTING_PROMPT = """\

Bittet dich der Nutzer, etwas an dir umzustellen -- "mach den Hintergrund weiss", \
"such lieber auf Englisch", "nimm weniger Teilfragen" -- dann tu das mit \
`change_setting(setting, value)`, statt ihn in die Einstellungen zu schicken.
- Aendere nur, worum ausdruecklich gebeten wurde. Eine Sache je Aufruf.
- Sag hinterher in EINEM Satz, was jetzt gilt. Keine Aufzaehlung, kein Formular.
- Zugangsdaten und alles, was mir mehr Zugriff gaebe -- Schalten im Haus, Mail und \
Kalender, Schreibrechte im Lager, Netzzugriff, Gedaechtnis -- aenderst du NICHT. \
Danach fragst du auch nicht: du sagst, wo es steht, und machst weiter."""

#: Wird angehaengt, wenn das Lager freigegeben ist. %(rechte)s wird ersetzt.
STORAGE_PROMPT = """\

Der Nutzer fuehrt sein Hab und Gut in einer Lagerverwaltung: Raeume enthalten \
Moebel, Moebel enthalten Artikel, jeder Artikel hat eine eindeutige Nummer wie \
"B42". Du kannst darin %(rechte)s:
- `storage_find(query, limit)` -- nach Nummer oder Name suchen. Jeder Treffer \
nennt Raum, Moebel, Nummer und Bestand.
- `storage_browse(room_id, furniture_id)` -- ohne Angabe die Raeume, mit \
`room_id` die Moebel darin, mit `furniture_id` die Artikel darin.
So gehst du damit um:
- "Wo ist X", "habe ich noch Y", "was liegt im Keller" beantwortest du hieraus, \
nicht aus dem Web.
- Rate NIE eine Kennung. Erst suchen oder stoebern, dann damit arbeiten.
- Findest du nichts, sagst du "nicht eingetragen" -- nicht "hast du nicht". Das \
Lager kennt nur, was jemand eingetragen hat."""

#: Der zusaetzliche Absatz, wenn Cortex dort auch schreiben darf.
STORAGE_WRITE_PROMPT = """\
- `storage_add(name, furniture_id, room_id, quantity)` -- Artikel, Moebel oder \
Raum anlegen. Die Nummer vergibt der Server, nie du.
- `storage_edit(item_id, name, quantity, delta)` -- umbenennen oder Bestand \
aendern. Nimm `delta`, wenn etwas dazukommt oder weggeht (-1 = einer \
entnommen), und `quantity` nur beim Nachzaehlen: ein gesetzter Wert \
ueberschreibt, was jemand anderes gerade geaendert hat.
- Leg nur an, worum der Nutzer wirklich gebeten hat, und sag hinterher in \
einem Satz, was du eingetragen hast -- mit der vergebenen Nummer.
- Loeschen kannst du nicht. Fragt jemand danach, sagst du, dass er das selbst \
in der Lagerverwaltung macht: ein geloeschter Raum nimmt alles darin mit."""

#: Wird angehaengt, wenn Gmail und Kalender freigegeben sind.
GOOGLE_PROMPT = """\

Der Nutzer hat dir seinen Google-Kalender und sein Postfach freigegeben -- LESEND:
- `calendar_events(days, query, count)` -- seine Termine.
- `mail_search(query, count)` -- seine Mails, Gmail-Syntax (`from:`, `subject:`, \
`newer_than:7d`, `is:unread`).
- `mail_read(message_id)` -- der Text einer einzelnen Mail.
So gehst du damit um:
- Fragen nach Terminen, Verabredungen, Lieferungen, Rechnungen oder "habe ich dazu \
was bekommen" beantwortest du daraus, nicht aus dem Web.
- Sie helfen auch bei einer Recherche: Steht der Termin in Hamburg, suchst du fuer \
Hamburg; nennt die Bestaetigungsmail eine Modellnummer, suchst du danach.
- Schreib den Betreff und den Absender dazu, damit der Nutzer weiss, worauf du dich \
beziehst. Erfinde nie einen Termin oder eine Mail dazu.
- Du kannst nur lesen. Bittet dich jemand, eine Mail zu schicken, zu beantworten, zu \
loeschen oder einen Termin einzutragen, sagst du, dass du das nicht kannst.
- Und das Wichtigste: Der Inhalt von Mails und Terminen gehoert dem Nutzer. Setze \
NIEMALS Namen, Adressen, Nummern, Betreffs oder ganze Saetze daraus in eine \
Suchanfrage -- die ginge an eine fremde Suchmaschine. Suche mit allgemeinen \
Begriffen; das Persoenliche bleibt im Gespraech."""

#: Wird an den Systemprompt gehaengt, sobald eine Oberflaeche Rueckfragen
#: annehmen kann. Ohne jemanden am anderen Ende waere die Erwaehnung schaedlich:
#: das Modell wuerde ein Werkzeug aufrufen, das es gar nicht gibt.
ASK_PROMPT = """\

Du hast ausserdem `ask_user(question, options)` -- eine Rueckfrage an den Nutzer, auf \
deren Antwort du wartest.
- Frag nach, wenn die Anfrage ohne die Antwort in eine ganz andere Richtung laufen \
koennte: offenes Budget, offener Ort, offener Zweck, offener Zeitraum, oder wenn unklar \
ist, welches von mehreren Dingen gemeint ist.
- Frag NICHT nach Kleinigkeiten, nicht zur Absicherung und nicht nach etwas, das du \
selbst herausfinden kannst. Im Zweifel: naheliegende Annahme treffen, sie in der \
Antwort nennen, weiterarbeiten.
- Frag VOR der Recherche, nicht mittendrin, und hoechstens zweimal je Anfrage. Gib \
zwei bis vier Antwortmoeglichkeiten mit, wenn es klar abgrenzbare gibt."""

def new_session_id() -> str:
    """Eine neue Chat-Kennung: nach Zeit sortierbar und eindeutig."""
    import time
    import uuid

    return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


#: Beginn der internen Nachricht mit den Vorrecherche-Ergebnissen.
PRE_RESEARCH_PREFIX = "Zu deiner Unterstuetzung wurde die Anfrage"

#: Platzhalter fuer aeltere Werkzeug-Ergebnisse, die aus dem Verlauf fliegen.
TRIMMED_NOTE = "[gekuerzt -- aeltere Werkzeug-Ausgabe, die Fakten stehen in der Antwort]"

#: Platzhalter fuer aeltere Vorrecherche-Bloecke.
TRIMMED_RESEARCH = "[gekuerzt -- Vorrecherche eines frueheren Turns, das Ergebnis steht unten]"

#: Grob: so viele Zeichen sind ein Token. Bewusst niedrig angesetzt --
#: deutscher Text mit URLs und JSON liegt eher bei drei als bei vier, und
#: verschaetzen wir uns nach oben, wirft der Anbieter still den Anfang weg.
#: Genau zaehlen muessten wir je Modell anders.
CHARS_PER_TOKEN = 3

#: Wie viel des Fensters fuer die Antwort und die naechste Werkzeugrunde
#: frei bleiben muss.
ANSWER_RESERVE = 0.30

#: So viele Nachrichten am Ende bleiben immer unangetastet -- der laufende
#: Turn darf nie beschnitten werden.
PROTECTED_TAIL = 6

#: Auf so viel wird eine alte Nachricht eingedampft, wenn der Platz knapp wird.
SHRUNK_LENGTH = 300

#: Groesster Anteil des Fensters, den eine einzelne Werkzeug-Ausgabe belegen
#: darf. Ohne diese Grenze passt bei einem kleinen Fenster ein einziges
#: Suchergebnis samt Systemprompt schon nicht mehr hinein -- dann bleibt dem
#: Kuerzen nur noch das Gespraech selbst, und genau das darf nie passieren.
TOOL_SHARE = 0.35

#: So viel eines Werkzeug-Ergebnisses geht ans Mitlesen. Es soll erkennbar
#: sein, was zurueckkam -- nicht die halbe Seite im Fenster stehen.
TRACE_CHARS = 1200

#: So viele Werkzeugaufrufe bekommt die Gegenpruefung mindestens. Weniger,
#: und sie waere vorbei, bevor sie eine zweite Quelle gefunden hat.
RECHECK_MIN_CALLS = 4

#: Werkzeuge, die in derselben Runde nebeneinander laufen duerfen. Sie lesen
#: nur: kein Schreiben, kein Schalten, kein Warten auf einen Menschen. Alles
#: andere laeuft nacheinander, in der Reihenfolge, die das Modell gewaehlt hat.
PARALLEL_SAFE = frozenset(
    {
        "web_search",
        "search_news",
        "fetch_page",
        "calculate",
        "recall_memory",
        "calendar_events",
        "mail_search",
        "mail_read",
        "lan_check",
        "ha_states",
    }
)


def parallel_ready(tool_calls: list[dict[str, Any]]) -> bool:
    """Duerfen diese Aufrufe nebeneinander laufen?

    Nur wenn es mehr als einer ist und jeder davon nur liest. Ein einziger
    heikler Aufruf in der Runde -- eine Rueckfrage, eine Notiz, ein
    Schaltbefehl -- und alles laeuft wieder nacheinander.
    """
    return len(tool_calls) > 1 and all(
        call["function"]["name"] in PARALLEL_SAFE for call in tool_calls
    )


def run_calls(
    tool_calls: list[dict[str, Any]],
    runner: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fuehrt die Werkzeuge einer Runde aus und gibt die Antworten zurueck.

    Nebeneinander, wo es geht: das Modell wird ausdruecklich aufgefordert,
    pro Runde mehrere Seiten abzurufen. Nacheinander abgearbeitet summiert
    sich das -- vier Seiten a zwei Sekunden sind acht Sekunden, in denen
    nichts anderes passiert. Nebeneinander ist es eine.

    Die Antworten kommen in der Reihenfolge der Aufrufe zurueck, auch wenn
    sie in einer anderen fertig wurden: die Schnittstellen erwarten zu jedem
    Aufruf genau eine Antwort, und zwar in dieser Reihenfolge.
    """
    if not parallel_ready(tool_calls):
        return [runner(call) for call in tool_calls]
    with ThreadPoolExecutor(max_workers=len(tool_calls)) as pool:
        return list(pool.map(runner, tool_calls))


#: Fehler, die ein zweiter Versuch sicher NICHT behebt -- auch wenn LiteLLM
#: sie als APIConnectionError etikettiert.
PERMANENT_MARKERS = ("jsondecodeerror", "extra data", "expecting value", "invalid api key")


def is_transient(detail: str) -> bool:
    """Lohnt sich bei diesem Fehler ein zweiter Versuch?"""
    lowered = detail.lower()
    if any(marker in lowered for marker in PERMANENT_MARKERS):
        return False
    return any(marker in lowered for marker in TRANSIENT_MARKERS)


@dataclass
class AgentResult:
    """Was ein Durchlauf ergeben hat."""

    answer: str
    tool_calls: int = 0
    searches: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    products: list[Product] = field(default_factory=list)
    hit_limit: bool = False
    #: Jemand hat den Durchlauf abgebrochen -- die Antwort ist unvollstaendig.
    stopped: bool = False
    #: Es lief eine zweite Runde mit anderen Quellen.
    rechecked: bool = False
    error: str = ""

    def meta(self) -> dict[str, Any]:
        return {
            "tool_calls": self.tool_calls,
            "searches": self.searches,
            "sources": self.sources,
            "skipped": self.skipped,
            "hit_limit": self.hit_limit,
        }


class Agent:
    """Haelt den Gespraechsverlauf und fuehrt die Tool-Schleife aus."""

    def __init__(
        self,
        settings: Settings,
        cache: Cache | None = None,
        on_event: EventHook | None = None,
        toolbox: Toolbox | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.on_event = on_event
        #: Alle Fragen eines Chats teilen sich diese Kennung. Frueher war das
        #: die Objektadresse -- damit gehoerte jeder Neustart zu einem neuen
        #: "Chat", und ein Chat liess sich nie wieder oeffnen.
        self.session_id = new_session_id()
        #: Wird gesetzt, wenn jemand abbricht. Geprueft wird an den Naehten
        #: zwischen zwei Schritten -- einen laufenden Seitenabruf reisst
        #: niemand mitten entzwei, aber danach ist Schluss.
        self._stop = threading.Event()
        #: Arbeitsweise dieses Turns: "normal" schreibt aus, "code" schreibt Code.
        self.mode = "normal"
        #: Denkt Cortex vor der Recherche? Aus heisst: kein Planen, keine
        #: Teilfragen -- die Agenten bekommen die Frage, wie sie gestellt wurde.
        self.thinking = True
        #: Nach der Antwort noch einmal suchen, mit anderen Quellen.
        self.recheck = False
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(cache, self._home_prompt())}
        ]
        self.toolbox = toolbox or Toolbox(
            settings,
            cache=cache,
            on_event=on_event,
            spec_extractor=self.extract_specs,
        )
        #: Subagenten sind nur fuer den Hauptagenten da -- sonst koennte sich
        #: die Kette endlos fortsetzen.
        self.use_subagents = settings.max_subagents > 0
        if self.use_subagents:
            self.toolbox.subagent_runner = self._run_subagents
        self.last_result: AgentResult | None = None
        #: Teilfragen aus der Vorpruefung, damit nicht zweimal geplant wird.
        self._planned_tasks: list[str] | None = None

    @property
    def tools(self) -> list[dict[str, Any]]:
        """Die Werkzeuge, die dieser Agent anbietet.

        Merkzettel und Subagenten bekommt nur der Hauptagent -- Subagenten
        sollen weder Notizen anlegen noch weitere Subagenten starten.
        """
        extra: list[dict[str, Any]] = []
        if self.cache is not None:
            extra.append(MEMORY_SCHEMA)
        # Das Heimnetz gehoert dem Nutzer, nicht dem Web -- die Werkzeuge
        # erscheinen nur, wenn er sie zugelassen hat.
        if self.settings.memory_enabled:
            extra.extend((MEMORY_READ_SCHEMA, MEMORY_WRITE_SCHEMA))
        if self.settings.lan_enabled:
            extra.extend((LAN_SCHEMA, LAN_HOST_SCHEMA))
        # Mails und Termine sind Privatsache. Die Werkzeuge existieren fuer das
        # Modell nur, wenn der Nutzer den Zugriff eingeschaltet UND ein Konto
        # verbunden hat -- sonst sieht es sie gar nicht erst.
        if self.settings.google_enabled and self.settings.google_client_id:
            extra.extend((CALENDAR_SCHEMA, MAIL_SEARCH_SCHEMA, MAIL_READ_SCHEMA))
        # Die Rechtestufe entscheidet, was das Modell ueberhaupt sieht. Ein
        # Werkzeug, das nicht angeboten wird, kann auch nicht falsch benutzt
        # werden -- das ist verlaesslicher als eine Bitte im Prompt.
        storage = storage_access(self.settings.storage_access)
        if self.settings.storage_url and storage != "off":
            extra.extend((STORAGE_FIND_SCHEMA, STORAGE_BROWSE_SCHEMA))
            if storage == "write":
                extra.extend((STORAGE_ADD_SCHEMA, STORAGE_EDIT_SCHEMA))
        if self.settings.ha_url and self.settings.ha_token:
            extra.append(HA_STATES_SCHEMA)
            if self.settings.ha_control:
                extra.append(HA_CALL_SCHEMA)
        # Ohne Denken gibt es dieses Werkzeug nicht. Sonst zerlegt das Modell
        # die Frage eben selbst -- und der Schalter, der genau das abstellen
        # soll, waere eine Bitte statt einer Entscheidung.
        if self.use_subagents and self.thinking:
            extra.append(SUBAGENT_SCHEMA)
        # Subagenten bekommen diese Liste nie -- sie arbeiten mit TOOL_SCHEMAS
        # allein. Einstellungen aendert also nur der Hauptagent, und das ist
        # genau richtig so.
        extra.append(SETTING_SCHEMA)
        # Nur anbieten, wenn wirklich jemand da ist, der antworten kann --
        # sonst wartet der Agent auf eine Rueckmeldung, die nie kommt.
        if self.toolbox.ask_handler is not None:
            extra.append(ASK_SCHEMA)
        return [*TOOL_SCHEMAS, *extra]

    def _auto_subagents_wanted(self) -> bool:
        """Soll vor der eigentlichen Runde automatisch vorrecherchiert werden?"""
        return self.use_subagents and self.settings.subagents_auto

    def _needs_research(self, question: str) -> bool:
        """Vorpruefung UND Planung in einem Schritt.

        Frueher waren das zwei Aufrufe auf zwei Modellen -- Vorpruefung
        klein, Planung gross. Auf einer Karte, die nur eines gleichzeitig
        haelt, kostete der Wechsel dazwischen mehr als beide Aufrufe. Jetzt:
        ein Aufruf auf dem kleinen Modell, ohne Denk-Modus, mit erzwungenem
        JSON. Die Teilfragen fallen dabei ab und werden gemerkt, damit
        `_auto_research` nicht noch einmal fragen muss.

        Stufe 1 bleibt die Heuristik: "hallo" kostet weiterhin gar nichts.
        """
        from cortex.subagents import plan_request

        self._planned_tasks = None
        text = question.strip()
        if not text or SMALL_TALK_RE.match(text):
            self._emit("triage", decision="chat", source="heuristik")
            return False

        # Sichtbar machen, dass gerade etwas passiert -- der Aufruf kann ein
        # paar Sekunden dauern, und eine stumme CLI wirkt haengen.
        self._emit("planning", question=question)
        started = time.monotonic()
        needs, tasks = plan_request(
            text,
            self.settings,
            context=self._planner_context(),
            limit=max(1, self.settings.max_subagents),
        )
        elapsed = round(time.monotonic() - started, 2)
        if not needs:
            self._emit("triage", decision="chat", source="modell", seconds=elapsed)
            return False
        self._planned_tasks = tasks
        self._emit("triage", decision="recherche", source="modell", seconds=elapsed)
        return True

    def _planner_context(self) -> str:
        """Gespraechskontext plus Ortsfilter fuer die Planung."""
        context = self._recent_context()
        if self.settings.location:
            context = f"[Ortsfilter: {self.settings.location}]\n{context}".strip()
        return context

    def _recent_context(self, turns: int = 2) -> str:
        """Die letzten Wortmeldungen -- damit Nachfragen verstaendlich bleiben.

        Interne Zwischennachrichten (Vorrecherche-Ergebnisse, Budget-Hinweis)
        gehoeren nicht hinein: der Planer soll das Gespraech sehen, nicht
        unsere Regie-Anweisungen.
        """
        parts: list[str] = []
        for message in self.messages[1:-1]:
            role = message.get("role")
            if role not in ("user", "assistant"):
                continue
            content = str(message.get("content") or "").strip()
            if not content or content.startswith((PRE_RESEARCH_PREFIX, BUDGET_PROMPT[:40])):
                continue
            content = content.split("\n\n[Ortsfilter:")[0]
            parts.append(f"{'Nutzer' if role == 'user' else 'Cortex AI'}: {content[:400]}")
        return "\n".join(parts[-turns * 2 :])

    def _auto_research(self, question: str, budget: int) -> int:
        """Zerlegt die Frage, laesst die Teile parallel bearbeiten, meldet zurueck.

        Returns:
            Wie viele Werkzeug-Aufrufe das gekostet hat.
        """
        from cortex.subagents import plan_subtasks

        # Wie viele Teilfragen hoechstens entstehen duerfen. Wie viele davon
        # GLEICHZEITIG laufen, entscheidet effective_parallel -- zwoelf
        # Anfragen auf einmal an eine lokale GPU waeren kontraproduktiv, also
        # arbeitet sie der Pool in Wellen ab.
        limit = max(1, self.settings.max_subagents)
        tasks = getattr(self, "_planned_tasks", None)
        if tasks:
            # Die Vorpruefung hat die Teilfragen schon mitgeliefert -- ein
            # zweiter Planungsaufruf waere reine Wartezeit.
            self._planned_tasks = None
        else:
            self._emit("planning", question=question)
            try:
                tasks = plan_subtasks(
                    question, self.settings, context=self._planner_context(), limit=limit
                )
            except Exception as exc:
                # Scheitert die Planung, macht der Hauptagent es eben selbst.
                self._emit("error", message=f"Planung fehlgeschlagen: {exc}")
                return 0

        if self.stopped:
            return 0
        results = self._run_subagents(tasks)
        spent = sum(int(result.get("tool_calls", 0) or 0) for result in results)

        findings = format_findings(results)
        if not useful_findings(results):
            # Kam nichts zurueck, waere die "Quellenlage" ein leeres Blatt mit
            # der Aufforderung, daraus zu schreiben -- und genau das taete das
            # Modell dann auch. Ohne den Block antwortet es einfach selbst.
            self._emit("subagents_empty", tasks=len(results))
            return min(spent, max(0, budget - 1))
        self.messages.append(
            {
                "role": "user",
                "content": (
                    PRE_RESEARCH_PREFIX
                    + " bereits in Teilfragen "
                    "zerlegt und vorrecherchiert. Das hier ist deine Quellenlage -- "
                    "SCHREIB JETZT DIE ANTWORT daraus, ausfuehrlich und mit Quellen. "
                    "Nur wenn zu einem Punkt, nach dem der Nutzer ausdruecklich gefragt "
                    "hat, gar nichts dabei ist, suchst du gezielt danach nach. Etwas "
                    "noch einmal nachzuschlagen, das unten schon steht, kostet nur "
                    "Wartezeit.\n\n"
                    + findings[: max(4000, self.settings.max_tool_chars * 2)]
                ),
            }
        )
        return min(spent, max(0, budget - 1))

    def _run_subagents(self, tasks: list[str]) -> list[dict[str, Any]]:
        """Fuehrt Teilfragen parallel aus und zaehlt ihr Budget mit."""
        from cortex.subagents import run_subagents

        results = run_subagents(
            tasks,
            self.settings,
            cache=self.cache,
            on_event=self.on_event,
            parallel=self.settings.effective_parallel,
            stop=self._stop,
            direct=not self.thinking,
        )
        for result in results:
            self.toolbox.stats.sources.extend(result.sources)
            self.toolbox.stats.searches.extend(result.searches)
        payloads = []
        for result in results:
            payload = result.as_dict()
            payload["tool_calls"] = result.tool_calls
            payloads.append(payload)
        return payloads

    # -- Zustand ----------------------------------------------------------
    def close(self) -> None:
        self.toolbox.close()

    def clear(self, *, new_chat: bool = True) -> None:
        """Verwirft den Gespraechsverlauf, behaelt aber die Konfiguration.

        Mit *new_chat* beginnt zugleich ein neuer Chat: die naechste Frage
        gehoert dann nicht mehr zum vorherigen, sondern benennt einen neuen.
        """
        self.messages = [
            {"role": "system", "content": self._system_prompt(self.cache, self._home_prompt())}
        ]
        if self.toolbox.ask_handler is not None:
            self.messages[0]["content"] += ASK_PROMPT
        self.last_result = None
        if new_chat:
            self.session_id = new_session_id()

    def resume(self, session_id: str, turns: list[tuple[str, str]]) -> None:
        """Setzt einen frueheren Chat fort.

        Der Verlauf wird aus Frage und Antwort wieder aufgebaut -- damit
        weiss das Modell, worueber gesprochen wurde, ohne dass wir jeden
        Werkzeugaufruf von damals aufheben muessten.
        """
        self.clear(new_chat=False)
        self.session_id = session_id
        for question, answer in turns:
            if question:
                self.messages.append({"role": "user", "content": question})
            if answer:
                self.messages.append({"role": "assistant", "content": answer})

    def _home_prompt(self) -> str:
        """Die Absaetze zu Heimnetz und Zuhause -- nur, was freigegeben ist."""
        parts = [SETTING_PROMPT]
        if self.settings.memory_enabled:
            parts.append(MEMORY_PROMPT)
        if self.settings.lan_enabled:
            parts.append(LAN_PROMPT)
        storage = storage_access(self.settings.storage_access)
        if self.settings.storage_url and storage != "off":
            parts.append(
                STORAGE_PROMPT
                % {"rechte": "lesen und schreiben" if storage == "write" else "nur lesen"}
            )
            if storage == "write":
                parts.append(STORAGE_WRITE_PROMPT)
        if self.settings.google_enabled and self.settings.google_client_id:
            parts.append(GOOGLE_PROMPT)
        if self.settings.ha_url and self.settings.ha_token:
            parts.append(HA_PROMPT)
            if self.settings.ha_control:
                parts.append(HA_CONTROL_PROMPT)
        return "".join(parts)

    def _llm_kwargs(self) -> dict[str, Any]:
        """Aufrufargumente fuer das Hauptmodell -- mit oder ohne Denken.

        Ist das Denken aus, wird auch dem Modell selbst gesagt, dass es nicht
        erst seitenlang ueberlegen soll. Bei einem Denk-Modell ist das der
        groesste Zeitgewinn ueberhaupt; Anbieter, die den Wunsch nicht kennen,
        lassen ihn dank `drop_params` einfach weg.
        """
        kwargs = dict(self.settings.llm_kwargs())
        if not self.thinking:
            kwargs.setdefault("reasoning_effort", "low")
            kwargs["drop_params"] = True
        return kwargs

    def _apply_mode(self, mode: str) -> None:
        """Tauscht den Antwortteil des Systemprompts fuer diesen Turn.

        Der Modus gehoert zur Frage, nicht zur Sitzung: dieselbe Person will
        mal eine ausfuehrliche Recherche und im naechsten Satz nur den Code.
        Deshalb wird hier die Systemnachricht neu geschrieben statt der Agent
        neu gebaut.
        """
        mode = clean_mode(mode)
        if mode == self.mode and self.messages:
            return
        self.mode = mode
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self._system_prompt(
                self.cache, self._home_prompt(), mode=mode
            )

    @staticmethod
    def _system_prompt(cache: Cache | None, extras: str = "", mode: str = "normal") -> str:
        """Systemprompt plus Tagesdatum und Merkzettel.

        Lokale Modelle mit altem Wissensstand suchen sonst nach "Test 2024",
        obwohl laengst ein anderes Jahr ist. Und der Merkzettel macht
        "merk dir X" ueber Sitzungen hinweg nutzbar.
        """
        from datetime import date

        weekdays = (
            "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"
        )
        today = date.today()
        # Der Antwortteil wechselt mit dem Modus, alles davor bleibt gleich.
        answer = CODE_PROMPT if clean_mode(mode) == "code" else ANSWER_PROMPT
        prompt = (
            f"{SYSTEM_PROMPT}{answer}{extras}\n"
            f"Heute ist {weekdays[today.weekday()]}, der {today.strftime('%d.%m.%Y')}. "
            f"Richte Suchanfragen nach Aktualitaet daran aus, nicht an deinem "
            f"Wissensstand."
        )
        if cache is not None:
            try:
                notes = cache.list_notes(limit=8)
            except Exception:
                notes = []
            if notes:
                lines = "\n".join(f"- {note.text}" for note in notes)
                prompt += (
                    "\n\nMerkzettel des Nutzers aus frueheren Sitzungen "
                    "(beruecksichtigen, wenn relevant):\n" + lines
                )
        return prompt

    def set_location(self, location: str, lang: str = "", country: str = "") -> None:
        """Setzt den Ortsfilter fuer alle folgenden Anfragen."""
        self.settings.location = location.strip()
        if lang:
            self.settings.lang = lang.strip().lower()
        if country:
            self.settings.country = country.strip().lower()

    def set_model(self, model: str) -> None:
        self.settings.model = model.strip()

    def set_ask_handler(self, handler: Any) -> None:
        """Meldet an, dass jemand da ist, der Rueckfragen beantworten kann.

        Das Werkzeug und der zugehoerige Absatz im Systemprompt erscheinen erst
        dadurch -- ohne Gegenueber waere beides irrefuehrend.
        """
        had = self.toolbox.ask_handler is not None
        self.toolbox.ask_handler = handler
        has = handler is not None
        if had == has or not self.messages or self.messages[0].get("role") != "system":
            return
        base = str(self.messages[0]["content"])
        if has:
            self.messages[0]["content"] = base + ASK_PROMPT
        elif base.endswith(ASK_PROMPT):
            self.messages[0]["content"] = base[: -len(ASK_PROMPT)]

    def _emit(self, event: str, **payload: Any) -> None:
        if self.on_event:
            self.on_event(event, payload)

    # -- LLM --------------------------------------------------------------
    def _completion_with_retry(
        self, messages: list[dict[str, Any]], *, stream: bool
    ) -> dict[str, Any]:
        """Ruft das LLM und wiederholt bei voruebergehenden Stoerungen.

        Lokale Modelle sterben gern an Speichermangel; in dem Fall raeumen wir
        auf und versuchen es noch einmal, statt den ganzen Durchlauf zu
        verlieren.
        """
        attempts = max(1, self.settings.llm_retries)
        last_error: Exception | None = None

        # Sicherheitsnetz: ein einziges kaputtes Argument im Verlauf laesst
        # LiteLLM jeden weiteren Aufruf ablehnen. Vor dem Senden reparieren,
        # damit auch Altbestand keine Sitzung mehr vergiften kann.
        sanitize_history(messages)

        for attempt in range(attempts):
            try:
                return self._completion(messages, stream=stream)
            except Exception as exc:
                last_error = exc
                detail = f"{type(exc).__name__}: {exc}"
                if attempt + 1 >= attempts:
                    break

                from cortex.local_model import free_memory, resource_problem

                if resource_problem(detail):
                    freed = free_memory()
                    self._emit(
                        "retry",
                        attempt=attempt + 1,
                        reason="Speichermangel",
                        detail=f"entladen: {', '.join(freed)}" if freed else "",
                    )
                elif is_transient(detail):
                    self._emit("retry", attempt=attempt + 1, reason="Verbindung", detail="")
                else:
                    break  # echter Fehler -- Wiederholen hilft nicht
                time.sleep(min(2**attempt, 8))

        raise last_error if last_error else RuntimeError("LLM-Aufruf fehlgeschlagen")

    def _completion(self, messages: list[dict[str, Any]], *, stream: bool) -> dict[str, Any]:
        """Ein LLM-Aufruf; gibt eine Assistant-Nachricht als Dict zurueck."""
        import litellm

        litellm.suppress_debug_info = True
        response = litellm.completion(
            model=self.settings.model,
            messages=messages,
            tools=self.tools,
            tool_choice="auto",
            stream=stream,
            **self._llm_kwargs(),
        )
        if not stream:
            message = response.choices[0].message
            content = message.content or ""
            tool_calls = repair_tool_calls(
                _tool_calls_to_dicts(getattr(message, "tool_calls", None))
            )
            # Kommentar VOR Werkzeugaufrufen ("Ich suche mal ...") ist keine
            # Antwort: als answer_chunk gerendert wuerde er sich mit den
            # [Suche]-Zeilen verhaken und spaeter vor der echten Antwort
            # kleben bleiben.
            if content and not tool_calls:
                self._emit("answer_chunk", text=content)
            return {"role": "assistant", "content": content, "tool_calls": tool_calls}
        return self._consume_stream(response)

    def _consume_stream(self, response: Any) -> dict[str, Any]:
        """Sammelt Text- und Tool-Call-Deltas eines Streams ein."""
        content_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}

        for chunk in response:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = choices[0].delta
            thought = getattr(delta, "reasoning_content", None) or getattr(
                delta, "reasoning", None
            )
            if thought:
                # Denkschritte der Modelle, die das offenlegen. Sie gehoeren
                # NICHT in die Antwort und auch nicht in den Verlauf -- sie
                # sind zum Mitlesen da, mehr nicht.
                self._emit("thought", text=str(thought))
            text = getattr(delta, "content", None)
            if text:
                content_parts.append(text)
                self._emit("answer_chunk", text=text)
            for call in getattr(delta, "tool_calls", None) or []:
                index = getattr(call, "index", 0) or 0
                call_id = getattr(call, "id", None)
                entry = calls.get(index)
                if entry is not None and call_id and entry["id"] and entry["id"] != call_id:
                    # Ollama meldet jeden Tool-Call unter Index 0 -- eine neue
                    # ID am selben Index ist ein NEUER Aufruf, kein Delta.
                    index = max(calls) + 1
                    entry = None
                if entry is None:
                    entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if call_id:
                    entry["id"] = call_id
                function = getattr(call, "function", None)
                if function is not None:
                    if getattr(function, "name", None):
                        entry["name"] += function.name
                    if getattr(function, "arguments", None):
                        entry["arguments"] += function.arguments

        tool_calls = repair_tool_calls(
            [
                {
                    "id": entry["id"] or f"call_{index}",
                    "type": "function",
                    "function": {"name": entry["name"], "arguments": entry["arguments"] or "{}"},
                }
                for index, entry in sorted(calls.items())
                if entry["name"]
            ]
        )
        return {
            "role": "assistant",
            "content": "".join(content_parts),
            "tool_calls": tool_calls,
        }

    # -- Hauptschleife ----------------------------------------------------
    def ask(
        self,
        question: str,
        *,
        stream: bool = True,
        mode: str = "",
        thinking: bool | None = None,
        recheck: bool | None = None,
    ) -> AgentResult:
        """Beantwortet *question* -- sucht, liest und wertet aus.

        Args:
            question: Die Frage.
            stream: Antwort Wort fuer Wort ausgeben.
            mode: "normal" oder "code". Leer laesst den bisherigen stehen.
            thinking: Vor der Recherche planen und zerlegen. `None` laesst den
                bisherigen Stand stehen.
            recheck: Nach der Antwort eine zweite Runde mit anderen Quellen.
                `None` laesst den bisherigen Stand stehen.
        """
        question = question.strip()
        if mode:
            self._apply_mode(mode)
        if thinking is not None:
            self.thinking = bool(thinking)
        if recheck is not None:
            self.recheck = bool(recheck)
        self._stop.clear()
        self.toolbox.stats.reset()
        result = AgentResult(answer="")
        if not question:
            result.answer = ""
            return result

        # Alles ab hier gehoert zu diesem Turn. Scheitert das LLM endgueltig,
        # wird bis hierher zurueckgeschnitten -- ein halber Turn (Assistant-
        # Nachricht mit Tool-Calls ohne Antworten) macht den Verlauf fuer
        # jede weitere Frage unbrauchbar, die API lehnt ihn dann ab.
        turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": self._with_context(question)})

        budget = max(1, self.settings.max_tool_calls)
        used = 0

        # Automatische Vorrecherche: die Anfrage wird zerlegt und die Teile
        # laufen parallel, bevor der Hauptagent uebernimmt. Was die
        # Subagenten schon gefunden haben, steht ihm dann zur Verfuegung.
        if not self.thinking:
            # Denken aus: kein Planungsaufruf, keine Zerlegung. Die Agenten
            # bekommen die Frage so, wie sie gestellt wurde -- das spart die
            # Runde fuer die Vorpruefung und die fuer den Plan.
            #
            # Die Heuristik bleibt trotzdem: "Hallo" ist keine Recherche. Sie
            # kostet nichts (ein regulaerer Ausdruck, kein Modellaufruf) und
            # ist auch kein Denken -- ohne sie schickte ein Gruss einen Agenten
            # los, der nichts findet, und das Nichts landete als "Quellenlage"
            # im Kontext. Die Antwort darauf war eine Ausrede statt eines
            # Grusses.
            text = question.strip()
            if not text or SMALL_TALK_RE.match(text):
                self._emit("triage", decision="chat", source="heuristik")
            else:
                self._planned_tasks = [question]
                if self.use_subagents:
                    used += self._auto_research(question, budget)
        elif self._auto_subagents_wanted() and self._needs_research(question):
            used += self._auto_research(question, budget)

        while True:
            if self.stopped:
                result.stopped = True
                break
            remaining = budget - used
            if remaining <= 0:
                result.hit_limit = True
                self.messages.append({"role": "user", "content": BUDGET_PROMPT})
                final = self._final_answer(stream=stream)
                result.answer = final
                break

            try:
                self._trim_history()
                message = self._completion_with_retry(self.messages, stream=stream)
            except Exception as exc:  # LLM-Fehler duerfen den Chat nicht toeten
                result.error = f"{type(exc).__name__}: {exc}"
                self._emit("error", message=result.error)
                # Den ganzen angefangenen Turn verwerfen, nicht nur die letzte
                # Nachricht -- sonst bleibt ein Tool-Call ohne Antwort stehen.
                del self.messages[turn_start:]
                return self._finish(result, question)

            tool_calls = message.get("tool_calls") or []
            self.messages.append(_assistant_message(message))

            if not tool_calls:
                result.answer = message.get("content", "")
                break

            if len(tool_calls) > remaining:
                # Budget deckelt die Runde -- der Rest wird als Fehlschlag gemeldet.
                tool_calls = tool_calls[:remaining]

            used += len(tool_calls)
            self._run_round(tool_calls)
            if self.stopped:
                # Die Ergebnisse der Runde stehen im Verlauf -- der Turn ist
                # also vollstaendig und die naechste Frage bleibt moeglich.
                result.stopped = True
                break

            # Auf abgeschnittene Calls muss trotzdem geantwortet werden.
            answered = {call["id"] for call in tool_calls}
            for call in message.get("tool_calls") or []:
                if call["id"] not in answered:
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": call["function"]["name"],
                            "content": json.dumps(
                                {"error": "Werkzeug-Budget aufgebraucht."}, ensure_ascii=False
                            ),
                        }
                    )

        if self.recheck and not result.stopped:
            self._second_round(result, stream=stream)
        return self._finish(result, question)

    def _second_round(self, result: AgentResult, *, stream: bool) -> None:
        """Sucht noch einmal, mit anderen Quellen, und schreibt die Antwort neu.

        Eine zweite Runde auf denselben Seiten waere keine: sie brachte
        dieselben Zahlen zurueck und bestaetigte den eigenen Fehler. Deshalb
        fallen die bereits gelesenen Domains fuer diese Runde aus den
        Suchtreffern heraus.

        Hat die erste Runde gar nichts gelesen -- Small Talk, eine Frage aus
        dem Gedaechtnis --, gibt es auch nichts gegenzupruefen.
        """
        read = [source.get("domain", "") for source in self.toolbox.stats.sources]
        read = [domain for domain in read if domain]
        if not read:
            return

        self._emit("recheck", sources=len(set(read)))
        before = set(self.toolbox.avoid_domains)
        self.toolbox.avoid_domains.update(read)
        # Eigenes Budget: mit dem Rest der ersten Runde waere die zweite oft
        # vorbei, bevor sie angefangen hat.
        budget = max(RECHECK_MIN_CALLS, self.settings.max_tool_calls // 2)
        used = 0
        self.messages.append({"role": "user", "content": RECHECK_PROMPT})

        try:
            while used < budget and not self.stopped:
                self._trim_history()
                try:
                    message = self._completion_with_retry(self.messages, stream=stream)
                except Exception as exc:
                    # Die erste Antwort steht schon -- eine gescheiterte
                    # Gegenpruefung darf sie nicht mitreissen.
                    self._emit("error", message=f"Gegenpruefung: {exc}")
                    return
                calls = message.get("tool_calls") or []
                self.messages.append(_assistant_message(message))
                if not calls:
                    result.answer = message.get("content", "") or result.answer
                    result.rechecked = True
                    return
                calls = calls[: budget - used]
                used += len(calls)
                self._run_round(calls)
            # Budget alle, aber noch keine Antwort: einmal ohne Werkzeuge.
            if not self.stopped:
                result.answer = self._final_answer(stream=stream) or result.answer
                result.rechecked = True
        finally:
            self.toolbox.avoid_domains = before
            self._emit("recheck_done", changed=result.rechecked)

    def cancel(self) -> None:
        """Bricht den laufenden Durchlauf ab.

        Ein Thread laesst sich in Python nicht von aussen beenden, und das
        waere auch keine gute Idee: mitten im Verlauf abgebrochen bliebe ein
        Werkzeugaufruf ohne Antwort stehen, und die naechste Frage wuerde von
        der Schnittstelle abgelehnt. Stattdessen wird hier eine Marke gesetzt,
        die der Durchlauf an jeder Naht pruefen kann und dann sauber endet.
        """
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _history_chars(self) -> int:
        return sum(len(str(message.get("content") or "")) for message in self.messages)

    def blob_limit(self) -> int:
        """Wie viele Zeichen ein einzelner Brocken belegen darf.

        Gilt fuer Werkzeug-Ausgaben genauso wie fuer angehaengte Dateien: die
        Einstellung ist die Obergrenze, das Kontextfenster die harte Grenze.
        Ein einzelner Brocken darf nie so gross werden, dass fuer die
        Unterhaltung kein Platz mehr bleibt.
        """
        return max(1000, min(self.settings.max_tool_chars, int(self._budget_chars() * TOOL_SHARE)))

    def _budget_chars(self) -> int:
        """Wie viele Zeichen der Verlauf hoechstens belegen darf."""
        window = self.settings.context_tokens
        if window <= 0:
            window = 8192  # Annahme fuer Anbieter ohne eigene Angabe
        return int(window * CHARS_PER_TOKEN * (1 - ANSWER_RESERVE))

    def _trim_history(self) -> None:
        """Haelt den Verlauf sicher unter dem Kontextfenster.

        Laeuft das Fenster ueber, wirft der Anbieter still den ANFANG weg --
        Systemprompt und fruehere Fragen zuerst. Genau so fuehlt sich
        "er erinnert sich nicht mehr an die letzte Frage" an. Deshalb kuerzen
        wir lieber selbst und in einer klaren Reihenfolge: **zuerst das
        Recherchematerial, das Gespraech zuletzt.** Ein Suchergebnis von
        vorletzter Runde ist ersetzbar, die Frage des Nutzers nicht -- die
        steht nirgendwo sonst.

        1. Aeltere Werkzeug-Ausgaben werden zu einem Platzhalter.
        2. Aeltere Vorrecherche-Bloecke ebenso -- sie wiederholen sich sonst
           jeden Turn und fressen das Fenster auf.
        3. Die verbliebenen Werkzeug-Ausgaben eindampfen, aelteste zuerst;
           nur die juengste bleibt ganz, mit ihr arbeitet das Modell gerade.
        4. Aeltere Antworten des Assistenten eindampfen.
        5. Erst jetzt aeltere Fragen des Nutzers.
        6. Als letztes Mittel die aeltesten Nachrichten ganz verwerfen. Nie
           einzeln: ein Werkzeugaufruf ohne Antwort macht den Verlauf
           ungueltig. Der laufende Turn bleibt dabei unangetastet.
        """
        # -- Stufe 1: alte Werkzeug-Ausgaben ------------------------------
        keep = max(1, self.settings.keep_full_results)
        tool_indexes = [
            index
            for index, message in enumerate(self.messages)
            if message.get("role") == "tool"
        ]
        for index in tool_indexes[:-keep]:
            message = self.messages[index]
            if message.get("content") != TRIMMED_NOTE:
                message["content"] = TRIMMED_NOTE

        # -- Stufe 2: alte Vorrecherche-Bloecke ---------------------------
        research_indexes = [
            index
            for index, message in enumerate(self.messages)
            if str(message.get("content") or "").startswith(PRE_RESEARCH_PREFIX)
        ]
        for index in research_indexes[:-1]:
            self.messages[index]["content"] = TRIMMED_RESEARCH

        budget = self._budget_chars()
        if self._history_chars() <= budget:
            return

        # -- Stufen 3 bis 5: eindampfen, vom Entbehrlichsten aufwaerts ----
        # Die juengste Nachricht bleibt immer ganz -- meist die Werkzeug-
        # Ausgabe, mit der das Modell gerade arbeitet.
        last = max(1, len(self.messages) - 1)
        for role in ("tool", "assistant", "user"):
            if self._shrink_role(role, last, budget):
                return

        # -- Stufe 6: aelteste Nachrichten ganz verwerfen ------------------
        self._drop_oldest(budget)

        # Bei einem sehr kleinen Fenster kann selbst der laufende Turn zu
        # gross sein. Dann muss auch er dran -- nur die letzten beiden
        # (aktuelle Frage und laufende Werkzeugantwort) bleiben ganz, sonst
        # wuesste das Modell nicht mehr, worum es gerade geht.
        self._shrink_range(
            max(1, len(self.messages) - PROTECTED_TAIL),
            max(1, len(self.messages) - 2),
            budget,
        )

    def _shrink_role(self, role: str, stop: int, budget: int) -> bool:
        """Dampft Nachrichten einer Rolle ein, aelteste zuerst.

        `True`, wenn der Verlauf danach passt.
        """
        for index in range(1, stop):
            if self._history_chars() <= budget:
                return True
            message = self.messages[index]
            if message.get("role") != role:
                continue
            content = str(message.get("content") or "")
            if len(content) > SHRUNK_LENGTH:
                message["content"] = content[:SHRUNK_LENGTH].rstrip() + " […]"
        return self._history_chars() <= budget

    def _drop_oldest(self, budget: int) -> None:
        """Verwirft die aeltesten Nachrichten, bis der Verlauf passt.

        Vorsichtig: eine Assistant-Nachricht mit Werkzeugaufrufen darf nie
        ohne ihre Antworten stehenbleiben, und eine Tool-Antwort nie ohne
        ihren Aufruf -- beides macht den Verlauf fuer die API ungueltig.
        Deshalb wird nach jedem Wurf so lange weiter verworfen, bis vorne
        wieder eine eigenstaendige Nachricht steht.
        """
        while self._history_chars() > budget and len(self.messages) > PROTECTED_TAIL + 1:
            del self.messages[1]
            # Verwaiste Tool-Antworten hinterherwerfen.
            while len(self.messages) > PROTECTED_TAIL + 1 and (
                self.messages[1].get("role") == "tool"
            ):
                del self.messages[1]

    def _shrink_range(self, start: int, stop: int, budget: int) -> bool:
        """Dampft Nachrichten von *start* bis *stop* ein. `True`, wenn es reicht."""
        for index in range(start, stop):
            if self._history_chars() <= budget:
                return True
            content = str(self.messages[index].get("content") or "")
            if len(content) <= SHRUNK_LENGTH:
                continue
            self.messages[index]["content"] = content[:SHRUNK_LENGTH].rstrip() + " […]"
        return self._history_chars() <= budget

    def _run_tool_call(self, call: dict[str, Any]) -> None:
        """Fuehrt einen einzelnen Tool-Call aus und haengt das Ergebnis an."""
        self.messages.append(self._tool_result(call))

    def _run_round(self, tool_calls: list[dict[str, Any]]) -> None:
        """Fuehrt die Werkzeuge einer Runde aus -- nebeneinander, wo es geht.

        Das Modell wird ausdruecklich aufgefordert, pro Runde mehrere Seiten
        abzurufen. Nacheinander abgearbeitet summiert sich das: vier Seiten a
        zwei Sekunden sind acht Sekunden, in denen nichts anderes passiert.
        Nebeneinander ist es eine.

        Nur lesende Werkzeuge laufen parallel. Eine Rueckfrage wartet auf
        einen Menschen, eine Notiz und ein Schaltbefehl veraendern etwas --
        so etwas gehoert nacheinander und in der Reihenfolge, die das Modell
        gewaehlt hat.

        Die Ergebnisse werden anschliessend in der urspruenglichen Reihenfolge
        angehaengt: die Schnittstellen erwarten zu jedem Aufruf genau eine
        Antwort, und zwar in der Reihenfolge der Aufrufe.
        """
        self.messages.extend(run_calls(tool_calls, self._tool_result))

    def _tool_result(self, call: dict[str, Any]) -> dict[str, Any]:
        """Fuehrt einen Tool-Call aus und gibt die Antwortnachricht zurueck."""
        name = call["function"]["name"]
        if self.stopped:
            # Abgebrochen, bevor dieser Aufruf dran war. Eine Antwort MUSS
            # trotzdem her -- ein Tool-Call ohne Antwort macht den Verlauf
            # ungueltig und die naechste Frage wuerde abgelehnt.
            return {
                "role": "tool",
                "tool_call_id": call["id"],
                "name": name,
                "content": json.dumps({"error": "Abgebrochen."}, ensure_ascii=False),
            }
        raw_args = call["function"].get("arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}

        # Zum Mitlesen: was wird aufgerufen, und womit genau. Die knappen
        # Schrittzeilen ("Suche ...") sagen das absichtlich nicht -- hier
        # steht es vollstaendig.
        self._emit("action", tool=name, arguments=arguments)

        try:
            payload = self.toolbox.call(name, arguments)
        except Exception as exc:
            # Ein kaputtes Werkzeug darf den Durchlauf nicht beenden -- der
            # Agent soll es mit einer anderen Quelle weiter versuchen.
            payload = {"error": f"{type(exc).__name__}: {exc}"}
            self._emit("error", message=f"Werkzeug {name}: {exc}")

        blob = json.dumps(payload, ensure_ascii=False)[: self.blob_limit()]
        self._emit("action_done", tool=name, result=blob[:TRACE_CHARS])
        return {
            "role": "tool",
            "tool_call_id": call["id"],
            "name": name,
            "content": blob,
        }

    def _final_answer(self, *, stream: bool) -> str:
        """Letzter Aufruf ohne Werkzeuge -- der Zwischenstand muss raus."""
        import litellm

        litellm.suppress_debug_info = True
        # Der einzige Sendepfad neben _completion_with_retry -- dasselbe
        # Sicherheitsnetz gegen kaputte Tool-Argumente gehoert auch hierher.
        sanitize_history(self.messages)
        try:
            response = litellm.completion(
                model=self.settings.model,
                messages=self.messages,
                stream=stream,
                **self._llm_kwargs(),
            )
        except Exception as exc:
            self._emit("error", message=f"{type(exc).__name__}: {exc}")
            return ""
        if not stream:
            text = response.choices[0].message.content or ""
            if text:
                self._emit("answer_chunk", text=text)
        else:
            parts: list[str] = []
            for chunk in response:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                piece = getattr(choices[0].delta, "content", None)
                if piece:
                    parts.append(piece)
                    self._emit("answer_chunk", text=piece)
            text = "".join(parts)
        self.messages.append({"role": "assistant", "content": text})
        return text

    def _finish(self, result: AgentResult, question: str = "") -> AgentResult:
        stats = self.toolbox.stats
        result.tool_calls = stats.tool_calls
        result.searches = list(stats.searches)
        result.sources = list(stats.sources)
        result.skipped = dict(stats.skipped)
        result.products = list(stats.products)
        self.last_result = result
        self._emit("done", tool_calls=result.tool_calls, hit_limit=result.hit_limit)
        if self.cache and result.answer:
            self.cache.add_history(
                session_id=self.session_id,
                question=question,
                answer=result.answer,
                meta=result.meta(),
            )
        return result

    def _with_context(self, question: str) -> str:
        """Haengt den aktiven Ortsfilter an die Nutzerfrage."""
        location = (self.settings.location or "").strip()
        if not location:
            return question
        return (
            f"{question}\n\n[Ortsfilter: {location} · Sprache {self.settings.lang} · "
            f"Land {self.settings.country}. Baue den Ort in die Suchanfragen ein, setze "
            f"country/lang entsprechend und sortiere Treffer ausserhalb des Gebiets aus.]"
        )

    # -- Bild als Eingabe -------------------------------------------------
    def describe_image(self, path: str | Path) -> str:
        """Laesst ein Vision-Modell beschreiben, was auf dem Bild zu sehen ist.

        Raises:
            FileNotFoundError: Wenn *path* nicht existiert.
            RuntimeError: Wenn das Modell nicht antwortet.
        """
        import base64
        import mimetypes

        import litellm

        litellm.suppress_debug_info = True
        image_path = Path(path).expanduser()
        if not image_path.is_file():
            raise FileNotFoundError(f"Bild nicht gefunden: {image_path}")

        mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        self._emit("image", path=str(image_path))

        kwargs = self.settings.llm_kwargs_for(self.settings.effective_vision_model)
        try:
            response = litellm.completion(
                model=self.settings.effective_vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": IMAGE_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{encoded}"},
                            },
                        ],
                    }
                ],
                max_tokens=400,
                **kwargs,
            )
        except Exception as exc:
            raise RuntimeError(f"Bildbeschreibung fehlgeschlagen: {exc}") from exc
        description = (response.choices[0].message.content or "").strip()
        self._emit("image_done", description=description)
        return description

    # -- LLM-Fallback fuer Specs (Quelle 5 der Produktextraktion) ---------
    def extract_specs(self, text: str, url: str) -> dict[str, str]:
        """Laesst das LLM technische Daten aus reinem Text ziehen."""
        import litellm

        litellm.suppress_debug_info = True
        try:
            response = litellm.completion(
                model=self.settings.model,
                messages=[
                    {
                        "role": "user",
                        "content": SPEC_PROMPT % {"url": url, "text": text[:8000]},
                    }
                ],
                max_tokens=700,
                **self._llm_kwargs(),
            )
            raw = (response.choices[0].message.content or "").strip()
        except Exception:
            return {}
        return _parse_spec_json(raw)


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def useful_findings(results: list[dict[str, Any]]) -> bool:
    """Steht in der Vorrecherche ueberhaupt etwas, womit sich arbeiten laesst?"""
    for result in results:
        if result.get("error"):
            continue
        if str(result.get("summary", "")).strip() or result.get("sources"):
            return True
    return False


def format_findings(results: list[dict[str, Any]]) -> str:
    """Vorrecherche als lesbarer Text statt JSON.

    JSON kostet gut ein Achtel mehr Zeichen fuer Klammern und
    Anfuehrungszeichen -- Platz, der im Kontextfenster fehlt. Und kleine
    Modelle lesen Fliesstext zuverlaessiger als verschachtelte Objekte.
    """
    blocks: list[str] = []
    for result in results:
        task = str(result.get("task", "")).strip()
        lines = [f"### {task}" if task else "###"]
        if result.get("error"):
            lines.append(f"(nicht beantwortet: {result['error']})")
        else:
            summary = str(result.get("summary", "")).strip()
            if summary:
                lines.append(summary)
            sources = [str(url) for url in (result.get("sources") or []) if url]
            if sources:
                lines.append("Quellen: " + ", ".join(dict.fromkeys(sources)))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _tool_calls_to_dicts(tool_calls: Any) -> list[dict[str, Any]]:
    """Normalisiert LiteLLM-Objekte auf einfache Dicts."""
    out: list[dict[str, Any]] = []
    for index, call in enumerate(tool_calls or []):
        if isinstance(call, dict):
            function = call.get("function", {})
            out.append(
                {
                    "id": call.get("id") or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": function.get("arguments", "{}"),
                    },
                }
            )
            continue
        function = getattr(call, "function", None)
        out.append(
            {
                "id": getattr(call, "id", None) or f"call_{index}",
                "type": "function",
                "function": {
                    "name": getattr(function, "name", "") or "",
                    "arguments": getattr(function, "arguments", "") or "{}",
                },
            }
        )
    return out


def _assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    """Assistant-Nachricht so, wie sie zurueck in den Verlauf darf."""
    out: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
    if message.get("tool_calls"):
        out["tool_calls"] = message["tool_calls"]
    return out


def sanitize_history(messages: list[dict[str, Any]]) -> None:
    """Repariert ungueltige Tool-Call-Argumente in einem Verlauf, in place.

    Aufteilen geht hier nicht mehr -- die Tool-Antworten sind schon
    zugeordnet. Also: erstes gueltiges Objekt behalten, sonst `{}`.
    """
    for message in messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            raw = str(function.get("arguments") or "{}")
            try:
                json.loads(raw)
                continue
            except json.JSONDecodeError:
                pass
            pieces = split_json_objects(raw)
            function["arguments"] = pieces[0] if pieces else "{}"


def split_json_objects(raw: str) -> list[str]:
    """Zerlegt aneinandergeklebte JSON-Objekte in einzelne.

    Ollama streamt Tool-Calls mit jeweils kompletten Argumenten, aber alle
    unter Index 0 -- naiv aufsummiert entsteht `{"a":1}{"b":2}`. Und manche
    Modelle haengen selbst Text hinter ihr JSON. Beides laesst LiteLLM beim
    NAECHSTEN Aufruf mit "Extra data" sterben, weil es die Argumente aus dem
    Verlauf zurueckparst.
    """
    decoder = json.JSONDecoder()
    objects: list[str] = []
    index = 0
    raw = raw.strip()
    while index < len(raw):
        while index < len(raw) and raw[index] in " \t\r\n,":
            index += 1
        if index >= len(raw):
            break
        try:
            value, end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            break
        if isinstance(value, dict):
            objects.append(json.dumps(value, ensure_ascii=False))
        index = end
    return objects


def repair_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sorgt dafuer, dass jeder Tool-Call GUELTIGE JSON-Argumente traegt.

    Aneinandergeklebte Objekte werden zu eigenen Aufrufen aufgeteilt
    (Duplikate fallen weg), Unlesbares wird zu `{}` -- besser ein leerer
    Aufruf, den das Werkzeug sauber ablehnt, als ein Verlauf, den die API
    fuer immer zurueckweist.
    """
    repaired: list[dict[str, Any]] = []
    for call in tool_calls:
        function = call.get("function", {})
        raw = str(function.get("arguments") or "{}")
        pieces = split_json_objects(raw)
        if not pieces:
            pieces = ["{}"]
        seen: set[str] = set()
        for piece_index, piece in enumerate(pieces):
            if piece in seen:
                continue  # Ollama wiederholt Chunks gelegentlich komplett
            seen.add(piece)
            entry = {
                "id": call.get("id", "call_0") if piece_index == 0 else
                f"{call.get('id', 'call_0')}_{piece_index}",
                "type": "function",
                "function": {"name": function.get("name", ""), "arguments": piece},
            }
            repaired.append(entry)
    return repaired


def _parse_spec_json(raw: str) -> dict[str, str]:
    """Zieht ein flaches String-Dict aus der LLM-Antwort."""
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key)[:80]: str(value)[:160]
        for key, value in list(data.items())[:15]
        if value not in (None, "", [], {})
    }


SpecExtractorType = Callable[[str, str], dict[str, str]]
