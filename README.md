# Aquaticy

**Aquaticy** recherchiert für dich. Du stellst eine Frage, Aquaticy durchsucht das Web,
liest die passenden Seiten und fasst das Ergebnis mit Quellen zusammen. Mit einem
Pro-Konto kann Aquaticy außerdem dein Heimnetz, Home Assistant und deine
Lagerverwaltung einbeziehen.

Du kannst Aquaticy im Terminal (`aquaticy`), im Browser (`aquaticy web`) oder vom Handy
aus (`aquaticy web --lan`) nutzen.

```
$ aquaticy

> Finde mir gute Cafés in Mönchengladbach mit WLAN

  [Suche] cafés mönchengladbach
  [Suche] café mönchengladbach wlan arbeiten
  [Lese]  4 Seiten...

  Ich habe 6 Cafés gefunden, die zu deiner Anfrage passen:

  1. Café Nordwand — Hindenburgstr. 12
     WLAN ausdrücklich erwähnt, Steckdosen an den Fensterplätzen.
     Quelle: die gelesene Café-Seite; Stand und Link stehen direkt an der Angabe
  ...

> davon nur die, die sonntags offen haben

  [Lese] 6 Seiten...
  ...
```

## Quickstart in 3 Minuten

```bash
# 1. Installieren (aus diesem Repo -- Aquaticy liegt noch nicht auf PyPI)
git clone https://github.com/jonasenriklaumen-a11y/thing-finder-
cd thing-finder-
uv tool install .

# 2. Einrichten -- fragt nach Modell und API-Key, testet beide
aquaticy setup

# 3. Loslegen -- im Terminal
aquaticy

# ... oder im Browser (oeffnet sich von selbst)
aquaticy web
```

**Aktualisieren** — beide Befehle müssen *im Repo-Verzeichnis* laufen, nicht im
Home-Verzeichnis:

```bash
cd ~/thing-finder-          # dorthin, wo du geklont hast
git pull
uv tool install . --force --reinstall
aquaticy --version            # zeigt, ob die neue Version aktiv ist
```

> Voraussetzungen: Python 3.11+ und [uv](https://docs.astral.sh/uv/). Sobald das Paket
> veröffentlicht ist, genügt `uv tool install aquaticy`. Zum Entwickeln stattdessen
> `uv venv && uv pip install -e ".[dev]"` und alles mit `uv run aquaticy ...` aufrufen.

### Umstieg von Cortex (vor 9.2.3)

Mit 9.2.3 heißt das Programm nicht mehr Cortex, sondern **Aquaticy** — derselbe Agent,
neuer Name, neuer Befehl. Bestehende Einstellungen ziehen nicht von selbst mit. Ein
Durchlauf reicht:

1. **Alte Installation entfernen** (`uv tool uninstall cortex`), sonst liegen zwei
   Programme nebeneinander.
2. **Datenordner umbenennen** — darin stecken Verlauf, Merkzettel, der verschlüsselte
   Speicher und die Google-Anmeldung. Verschiebe den alten versteckten Ordner in
   deinem Home-Verzeichnis von `~/.cortex` nach `~/.aquaticy` und den Ordner unter
   `~/.config` von `~/.config/cortex` nach `~/.config/aquaticy`.
3. **Die `.env` anpassen**: alle Schlüssel, die früher mit `CORTEX_…` begannen, heißen
   jetzt `AQUATICY_…`. Die Namen der Anbieter-Schlüssel (`MISTRAL_API_KEY`,
   `NVIDIA_NIM_API_KEY`, `GOOGLE_CLIENT_ID` …) bleiben unverändert.

Wenn dir das zu fummelig ist: `aquaticy setup` legt eine frische `.env` an und fragt
dich durch. Verlauf und Merkzettel sind dann weg, alles andere ist in zwei Minuten
wieder eingerichtet.

### Umstieg von einer Version vor 7.5

Mit 7.5 hieß alles einheitlich Cortex — das Programm, der Ordner mit den Daten und die
Namen in der `.env`. Wer noch auf einem Stand von davor ist, richtet am einfachsten
gleich neu ein: `aquaticy setup` fragt Modell und Suchmaschine ab und schreibt eine
frische `.env`. Alte, noch ältere Konfigurationsnamen versteht Aquaticy nicht mehr.

### Windows

Alles läuft auch unter Windows — in **PowerShell**:

```powershell
# uv installieren, falls noch nicht vorhanden
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

git clone --branch claude/aquaticy-ai-web-agent-yogr3j `
  https://github.com/jonasenriklaumen-a11y/thing-finder-.git
cd thing-finder-
uv tool install .
uv tool update-shell        # danach PowerShell neu öffnen

aquaticy install-model        # holt Ollama per winget und lädt die Modelle
```

Unterschiede zu Linux und macOS:

* Ollama kommt per `winget install Ollama.Ollama`; ohne winget lädst du den Installer von
  [ollama.com/download](https://ollama.com/download) und startest `aquaticy install-model`
  danach erneut.
* Pfade mit Backslash und Anführungszeichen: `aquaticy --image "C:\Users\du\Bilder"`
* Pfeiltasten-History im Chat gibt es nur mit `pip install pyreadline3`.
* Der Ollama-Server läuft im Hintergrund, ohne dass ein Konsolenfenster aufgeht.

`aquaticy setup` fragt genau zwei Dinge ab:

| Was | Wo bekommt man es | Pflicht? |
|---|---|---|
| LLM-Anbieter + Key | [Mistral](https://console.mistral.ai/api-keys/) · [NVIDIA NIM](https://build.nvidia.com/) · oder lokal per [Ollama](https://ollama.com) ganz ohne Key | ja |
| Suchmaschine | **Nichts.** Die offene Metasuche ist Standard und braucht weder Key noch Konto. | nein |

Beide werden direkt mit einem Probe-Request getestet, bevor die `.env` geschrieben wird
(nach `~/.config/aquaticy/.env`, Rechte `600`).

Ohne Chat, für einen einzelnen Durchlauf:

```bash
aquaticy "welche Bahnstrecken in NRW sind gerade gesperrt?"
```

## Beispiel-Session

```
$ aquaticy --location "Mönchengladbach" --lang de

╭──────────────────────────────────────────────────────╮
│ Aquaticy AI 9.4.3                                      │
│ Modell mistral/mistral-large-latest · Suche duckduckgo │
│ Frag einfach los. /help zeigt die Befehle.           │
╰──────────────────────────────────────────────────────╯

> ich suche einen Laptop bis 1200 € für Bildbearbeitung

  [Suche] laptop bildbearbeitung test 2026
  [Suche] notebook bis 1200 euro farbtreues display
  [Lese]  5 Seiten... (amazon.de übersprungen: blockiert)

  Drei Kandidaten:

  ┌─ Lenovo Yoga Pro 7 (14", Ryzen 7)  ~1.099 € ─────────────────┐
  │ [Bild]    https://.../yoga-pro-7.jpg                         │
  │ Display   14,5" 3K OLED, 100 % DCI-P3                        │
  │ CPU       Ryzen 7 8845HS      RAM  32 GB                     │
  │ Quelle    notebookcheck.com, geizhals.de                     │
  └──────────────────────────────────────────────────────────────┘
  ...

                              Vergleich
  ┌───────────┬─────────────────┬────────────────┬──────────────┐
  │           │ Lenovo Yoga…    │ ThinkPad X1…   │ Zenbook 14…  │
  │ Preis     │ 1.099,00 €      │ 1.149,00 €     │ 1.049,00 €   │
  │ Display   │ 14,5" 3K OLED   │ 14" 2.8K OLED  │ 14" 3K OLED  │
  │ RAM       │ 32 GB           │ 32 GB          │ –            │
  └───────────┴─────────────────┴────────────────┴──────────────┘

> nur die mit mindestens 32 GB RAM

  [Lese] 2 Seiten...
  ...

> /export html
  Gespeichert: aquaticy-20260822-2043-ich-suche-einen-laptop.html
```

## Kernprinzip

Der Agent hat eine Handvoll **generischer Werkzeuge** und kombiniert sie selbstständig,
so oft er will:

1. `web_search(query, count, country, lang)` — schickt eine Suchanfrage ans Web und
   bekommt Titel, URL und Snippet zurück.
2. `fetch_page(url)` — lädt eine Seite **oder ein PDF** und gibt den lesbaren Text
   zurück (HTML-Ballast, Navigation und Werbung entfernt). Datenblätter, Speisekarten
   und Preislisten sind damit keine blinden Flecken mehr.
3. `search_news(query, count)` — Nachrichten mit Datum und Quelle, für alles Aktuelle.
4. `calculate(expression)` — exakte Arithmetik (auch `1.099,99`), damit Preisvergleiche
   nie auf Kopfrechnen kleiner Modelle beruhen.
5. `remember(text)` — schreibt auf einen dauerhaften Merkzettel, aber nur auf
   ausdrückliche Bitte („merk dir …"). Anzeigen mit `/notes` bzw. `aquaticy notes`.

Ob Instagram, Amazon, ein Branchenbuch oder die Website eines Ladens: Alles sind einfach
Suchtreffer, die gelesen werden können. Es gibt bewusst keine plattformspezifischen
Scraper. Der Systemprompt trägt außerdem das **heutige Datum**, damit Modelle mit altem
Wissensstand nicht nach „Test 2024" suchen.

### Eingebaute Fallbacks

| Fällt aus … | … übernimmt |
|---|---|
| konfigurierte Suchmaschine (SearXNG, Brave, Tavily) | die offene Metasuche |
| News-Vertikale | die normale Websuche |
| Subagenten-Modell (nicht geladen, abgestürzt) | das Hauptmodell |
| einzelne Engine der Metasuche | die übrigen Engines |
| JS-lose Seite ohne Inhalt | der Playwright-Browser (Stufe 3) |

Pro Nutzeranfrage:

1. Das LLM überlegt, welche Suchanfragen sinnvoll sind, und formuliert **mehrere**
   Varianten — nicht nur eine.
2. Es sucht, sichtet die Treffer und entscheidet, welche Seiten sich zu lesen lohnen.
3. Es liest die relevanten Seiten und zieht die gewünschten Informationen heraus.
4. Es fasst **ausführlich** zusammen, bewertet gegen die Kriterien des Nutzers und nennt
   zu jeder Angabe die Quelle.

Die Antwort ist bewusst großzügig: Jeder Treffer bekommt alle relevanten Fakten
(Adresse, Zeiten, Preise, Ausstattung, Einschränkungen), dazu Nebenbefunde, die nicht
erfragt waren aber nützen — Anfahrt, Alternativen, bekannte Nachteile. Danach ein
Vergleich der Ergebnisse untereinander, ein kurzes Fazit mit Empfehlung und ein Abschnitt
„Nicht gefunden:" mit jedem offenen Punkt samt Grund. Vollständigkeit ersetzt dabei nie
Genauigkeit: gekürzt wird nur, wo sonst geraten werden müsste.

### Subagenten: Teilfragen parallel

Vor jeder Planung schaut aquaticy kurz auf die Nachricht: **Braucht das überhaupt eine
Recherche?** Offensichtlicher Small-Talk („hallo", „wie geht es dir?", „wer bin ich?",
„danke") bekommt sofort eine kurze, natürliche Standardantwort und kostet keinen einzigen
Modellaufruf. Bei allem anderen liefert **ein einziger Aufruf**
Entscheidung *und* Teilfragen — auf dem kleinen Subagenten-Modell, mit abgeschaltetem
Denk-Modus, kleinem Fenster und erzwungenem JSON-Schema. Fällt er aus oder dauert zu
lange (`AQUATICY_PLANNER_TIMEOUT`, Default 20 s), gilt sicherheitshalber „Recherche" —
lieber einmal zu viel geplant als eine echte Frage unbeantwortet.

Warum das schnell ist — vier Hebel:

| Vorher | Jetzt |
|---|---|
| zwei Aufrufe (Prüfung + Planung) | **einer** |
| Planung auf dem **großen** Modell | auf dem kleinen |
| dadurch 4 Modellwechsel pro Anfrage | **einer** |
| Denk-Modus an, `max_tokens=400`, 16k-Fenster | aus, `200`, 2k-Fenster |

Der dritte Punkt ist auf knappen Karten der größte: Wenn Ollama zwischen großem und
kleinem Modell hin- und herladen muss, kostet allein das mehr als alle Aufrufe zusammen.
Jetzt läuft alles vor der eigentlichen Antwort auf dem kleinen Modell. Auch die
Subagenten arbeiten ohne Denk-Modus — bei vier parallelen summiert sich das.

Recherche-Anfragen zerlegt aquaticy dann von sich aus in Teilfragen und lässt sie parallel
bearbeiten, bevor der Hauptagent übernimmt:

```
> vergleiche das Lenovo Yoga Pro 7, das ThinkPad X1 und das Zenbook 14

  [Plane] zerlege die Anfrage ...
  [Teile] 3 Teilfragen
          Specs und Straßenpreis des Lenovo Yoga Pro 7 (14", Ryzen 7)
          Specs und Straßenpreis des ThinkPad X1 Carbon Gen 12
          Specs und Straßenpreis des Asus Zenbook 14 OLED
  [Fertig] Specs und Straßenpreis des Lenovo Yoga Pro 7   (4 Aufrufe)
  ...
```

**Es sind immer alle.** Ein Planer, der drei Teilfragen liefert, während zwölf Agenten
bereitstehen, lässt zwölf Agenten zu dritt suchen — und die Antwort ist so dünn wie die
Zerlegung. Deshalb wird die Liste auf die volle Zahl **aufgefüllt**: erst fragt der Planer
nach genau so vielen Teilfragen, wie Agenten da sind. Die Zahl stellst du direkt in der
Modellauswahl ein: **1 bis 12** in Normal und Code, **1 bis 50** in Pro. Der Master
entscheidet weiterhin, wie viele davon eine konkrete Frage wirklich braucht; `/max`
setzt die gewählte Mannschaft vollständig ein. Was
dann noch fehlt, entsteht aus derselben Frage unter einem anderen
Blickwinkel — Preise und Kosten, Erfahrungen und Kritik, aktuelle Änderungen, offizielle
Angaben, Alternativen, Tests, Bedingungen, Anfahrt und Öffnungszeiten. Das ist keine
Verlegenheitslösung: genau diese Seiten fehlen sonst in der Antwort, weil niemand danach
gesucht hat. Und weil die Blickwinkel so formuliert sind, wie die Rollenerkennung sie
liest, bekommt der Preis-Agent von selbst die Zahlen-Rolle und der Kritik-Agent die
Gegenstimmen-Rolle. Doppeltes fällt vorher raus; mehr als sich sinnvoll bilden lässt, wird
nicht erfunden.

Ob überhaupt recherchiert wird, entscheidet weiterhin die Vorprüfung: ein „hallo" kostet
keinen Agenten, und im Standardmodus braucht es dafür den Schalter *Strukturieren*.

Jeder Subagent hat dieselben zwei Werkzeuge, ein eigenes kleines Budget (Default 6
Aufrufe, im Pro-Modus 8) und liefert eine knappe Zusammenfassung mit Quellen zurück. Seine Anweisung
ist, die Suche gleich **dreifach zu stellen** — eine Anfrage, dazu zwei andere
Formulierungen über `queries`. Die drei laufen nebeneinander, die Trefferlisten werden
gemischt (RRF), und es kostet trotzdem nur *einen* Aufruf von seinem knappen Budget.
Gleichlautende Teilfragen werden vor dem Start aussortiert: zwei gleiche Aufträge lesen
dieselben Seiten und melden dasselbe zurück — bezahlt wird beides.

**Rollen statt lauter gleicher Agenten.** Viele identische Agenten suchen sonst
mehrfach dasselbe: was oben in den Treffern steht. Deshalb bekommt jede
Teilfrage einen Blickwinkel, abgeleitet aus ihrem Wortlaut — reine Textarbeit, kein
Modellaufruf, keine Wartezeit:

| Rolle | wann | was sie ändert |
| --- | --- | --- |
| **Zahlen** | Preis, Kosten, Gebühr, Tarif, Miete … | jede Zahl mit Einheit, Stand und Quelle; zwei verschiedene Zahlen werden **beide** genannt |
| **Gegenstimmen** | Erfahrung, Kritik, Problem, Mangel, Rückruf … | sucht ausdrücklich nach dem, was nicht in Werbetexten steht — und sagt dazu, wie verbreitet eine Klage ist |
| **Aktuelles** | aktuell, derzeit, neueste, seit wann … | nimmt `search_news`, jede Angabe mit Datum, Altes wird als alt gekennzeichnet |
| **Spurensuche** | klein, lokal, in der Nähe, Verein, Geheimtipp … | Karte zuerst, dann Operatoren, Verzeichnisse, Umwege — und gibt nicht nach zwei Suchen auf |
| *(keine)* | alles andere | der normale Rechercheauftrag, unverändert |

Dazu kommt **Spurensuche** für das, was sich nicht einfach finden lässt (siehe *Was sich
nicht finden lässt*). Und im Pro-Modus vergibt der **Master** die Rollen selbst, in
eigenen Worten und auf den einzelnen Auftrag gemünzt — die Liste oben liefert dann nur
noch die Technik dazu.

Die Rolle steht als kurzer Absatz im Auftrag und ändert sonst nichts: dieselben
Werkzeuge, dasselbe Budget, dieselbe Form der Antwort. Trifft kein Stichwort, bleibt es
beim normalen Auftrag — lieber keine Rolle als eine falsche, die am Thema vorbeisucht.
In den Zwischenschritten steht der Blickwinkel hinter der Teilfrage, und der Hauptagent
bekommt ihn mitgeliefert: was der Gegenstimmen-Agent gefunden hat, ist eine Auswahl und
nicht das ganze Bild. Zwei laufen
gleichzeitig — bei lokalen Modellen bringt mehr wenig, weil die GPU ohnehin nacheinander
rechnet. Lässt sich eine Anfrage nicht sinnvoll teilen, entsteht genau eine Teilfrage und
der Ablauf bleibt wie zuvor. Nachfragen wie „nur die mit 4+ Sternen" bekommen das
bisherige Gespräch als Zusammenhang mit, damit die Teilfragen für sich verständlich sind.

Der Hauptagent darf zusätzlich jederzeit selbst weitere Teilfragen abgeben
(`research_subtasks`), wenn ihm im Verlauf etwas fehlt.

**Abschalten** fragt `aquaticy setup` direkt ab, oder von Hand:

```bash
AQUATICY_SUBAGENTS_AUTO=false     # nur noch auf Wunsch des Modells
AQUATICY_MAX_SUBAGENTS=0          # ganz aus, zurück zu zwei Werkzeugen
```

#### Eigenes Modell für die Subagenten

Teilfragen sind eng umrissen — dafür reicht ein kleines Modell, das neben dem
Hauptmodell in den Speicher passt. `aquaticy install-model` fragt danach; Stand August 2026:

| Modell | ca. Größe | ab VRAM |
|---|---|---|
| `qwen3:0.6b` | 0,5 GB | 2 GB |
| `qwen3:1.7b` | 1,4 GB | 3 GB |
| `gemma4:e2b` | 1,8 GB | 3 GB |
| `qwen2.5:3b` | 1,9 GB | 4 GB |
| `qwen3:4b` | 2,5 GB | 5 GB |

```bash
AQUATICY_SUBAGENT_MODEL=ollama_chat/qwen3:1.7b   # leer = das kleine des Anbieters
```

Wichtig ist hier nur eines: Das Modell muss zuverlässig Werkzeuge aufrufen. Klug sein
darf das Hauptmodell, das die Ergebnisse am Ende zusammenführt.

**Grenzen:** maximal 20 Tool-Calls pro Anfrage, dann wird der Zwischenstand ausgegeben.
Was nicht gefunden wurde, wird als „nicht gefunden" gekennzeichnet — niemals geraten.

## Wer antwortet da eigentlich

Fragt man Aquaticy, wer er ist, sagt er: *„Ich bin Aquaticy, ein KI-Assistent von Jonas."*
Nicht „ich bin ein Modell von Google" oder von sonst jemandem — Aquaticy ist das
Programm, das Sprachmodell darunter ist ein Bauteil davon, austauschbar über die
Einstellungen.

Gelogen wird dabei nicht: Fragt jemand ausdrücklich, welches Modell gerade läuft, sagt
er es. Der Unterschied ist, dass er sich nicht mit dem Modell verwechselt, das ihn
antreibt.

## Weboberfläche

```bash
aquaticy web
```

Startet eine Oberfläche im Stil eines Chat-Fensters und öffnet den Browser. Es ist
derselbe Agent wie im Terminal: dieselben zwei Werkzeuge, dieselben Subagenten,
derselbe Verlauf, dieselbe `.env`. Die Zwischenschritte („Suche", „Lese", „Teile")
laufen live mit, die Antwort wird Wort für Wort gestreamt.

* **Der Zustand liegt beim Server, nicht im Browser.** Erscheinungsbild,
  Farbschema, Arbeitsweise, Denktiefe und alle Schalter der Modellauswahl merkt
  sich Aquaticy selbst — in derselben Datenbank wie den Rest. Das hat drei Gründe.
  **Jedes Gerät zeigt dasselbe:** am Rechner dunkel heißt am Handy dunkel, im
  Code-Modus angefangen heißt auf dem Tablet Code-Modus. **Nichts blinkt:** der
  Zustand steht schon im ausgelieferten HTML, die Seite kommt also von der ersten
  Zeile an richtig an, statt hell zu erscheinen und dann dunkel zu werden.
  **Und der Browser entscheidet nichts.** Was er schickt, ist ein Vorschlag; was
  gilt, prüft der Server gegen eine Liste erlaubter Werte
  (`aquaticy/uistate.py`). Ein erfundener Modus, ein unbekanntes Feld, die
  Zeichenkette `"false"` als Wahrheitswert — nichts davon kommt durch. Im Browser
  bleibt genau eine Sache gespeichert: das Zugangswort für `--lan`, denn das
  *ist* der Ausweis und kann nirgendwo anders liegen.
* **Was du im Formular einträgst, wird geprüft, bevor es gespeichert wird.**
  Buchstaben in einem Zahlenfeld, eine Zahl außerhalb ihres Bereichs, eine
  Auswahl, die es nicht gibt: Aquaticy sagt, welches Feld gemeint ist, und
  schreibt *nichts*. Vorher nahm er alles an, meldete „gespeichert", und die
  Einstellung tat trotzdem nichts — das merkt man erst Tage später.
* **Handy, Tablet und großer Bildschirm** haben jeweils eigene Maße. Das Tablet
  im Hochformat bekam lange das Handy-Layout, weil es unter 900 px breit ist —
  mit Maßen, die für 390 px gedacht waren: die Eingabe von Rand zu Rand, die
  Modellauswahl über die volle Breite, zwei Beispielfragen statt drei. Jetzt
  behält es die Seitenleiste über dem Chat (im Hochformat wäre sie sonst ein
  Drittel der Breite) und bekommt sonst die Maße des Rechners zurück. Der
  Rundgang prüft alle vier Größen einzeln.
* **Die Schalter** sind Schalter, keine Haken: eine Pille, in der ein weißer Knopf
  hin und her fährt, wie auf dem iPhone. Darunter steckt weiterhin ein ganz normales
  Ankreuzfeld — Label, Tabulator, Leertaste und Vorlesehilfen funktionieren
  unverändert. Der Knopf gleitet, die Farbe blendet über, und beim Draufdrücken
  zieht er sich kurz in die Länge und schnellt zurück. Die Farbe kommt aus dem
  gewählten Farbschema, nicht aus iOS: ein festes Apple-Grün sähe in Nord oder
  Dracula wie ein Fremdkörper aus (im Standardschema ist sie ohnehin grün).
* **Hell oder dunkel**, in acht Farbschemata — unter *Erscheinungsbild* unten links
  (siehe weiter unten). Die Versionsnummer steht klein in der Kopfzeile.
* **Einstellungen** öffnet ein Formular mit *allem*, was auch `aquaticy setup` fragt.
  Der Kopf bleibt beim Scrollen stehen und trägt eine Marke je Abschnitt — ein Klick
  springt hin, statt durch das ganze Formular zu scrollen. Ein Test hält den Inhalt
  dauerhaft in Deckung mit dem Terminal: kommt dort eine Frage dazu, schlägt er fehl,
  bis das Formular nachzieht. Enthalten sind Haupt-, Vision- und Subagenten-Modell,
  API-Key, API-Basis, Suchmaschine samt Engine-Liste und SearXNG-URL, Ort/Sprache/Land, Subagenten an/aus samt Budget und
  Parallelität, Werkzeug-Budget, Kontextfenster, Planungs-Zeitlimit und der
  Playwright-Fallback. Gespeichert wird in dieselbe `.env`, danach lädt der Agent neu.
  Ein leeres API-Key-Feld bedeutet „unverändert" — der vorhandene Key bleibt stehen.
* **Dateien anhängen** über die Büroklammer, per Drag-and-drop irgendwo aufs
  Fenster oder mit <kbd>Strg</kbd>+<kbd>V</kbd> aus der Zwischenablage. Bilder gehen
  ans Vision-Modell, PDFs werden ausgelesen, Text-, Markdown-, CSV- und JSON-Dateien
  direkt übernommen. Bis zu 5 Dateien à 25 MB. Ein gescanntes PDF ohne Textebene sagt
  das offen — geraten wird nichts.
* **Rückfragen statt Raten.** Fehlt eine Angabe, ohne die die Antwort auf gut Glück
  raten würde, *muss* Aquaticy fragen — in einem kleinen Fenster über dem Chat, mit
  anklickbaren Antworten oder einem Feld zum Selberschreiben. <kbd>Esc</kbd> heißt
  „überspringen". Das gilt vor allem für den **Ort** (Wetter, Öffnungszeiten, Preise
  vor Ort), den **Zeitraum**, das **Budget**, welches von mehreren gleichnamigen
  Dingen gemeint ist, und bei Code für **Sprache, Version und Zielsystem**.
  „Wie wird das Wetter morgen?" ohne bekannten Ort ist eine Rückfrage — nie
  stillschweigend Berlin. Steht die Angabe schon im Gespräch, im Ortsfilter oder auf
  dem Merkzettel, nimmt er sie von dort. Nach Kleinigkeiten fragt er nicht, und
  höchstens zweimal je Anfrage. Sitzt niemand davor, der antworten könnte (Terminal
  mit `--yes`, Automatik), erfindet er die Angabe trotzdem nicht: dann sagt er in der
  ersten Zeile, was fehlt, und nennt die Annahme, unter der er weitermacht.
* **Letzte Chats** in der Seitenleiste sind Chats, keine Einzelfragen. Ein Chat
  beginnt mit **Neuer Chat**, bekommt seinen Namen von der ersten Frage darin und
  sammelt alles Weitere, bis du den nächsten startest. Er erscheint in dem Moment in
  der Liste, in dem du ihn beginnst — nicht erst, wenn die Antwort fertig ist. Klick
  auf einen Eintrag holt ihn zurück, samt Verlauf, an den der Agent wieder anknüpft.
  Neueste oben, ab dem zweiten Tag nach *Heute*, *Gestern*, *Letzte 7 Tage* gruppiert.
  **Ein Chat bleibt ein Chat**, auch wenn du zwischendurch etwas umstellst:
  wer mitten im Gespräch das Modell wechselt oder eine Einstellung speichert,
  will ein anderes Modell — kein anderes Gespräch. Aquaticy baut sich dafür intern
  neu auf, kehrt danach aber in denselben Chat zurück und kennt den Verlauf noch.
  Ein neuer Chat beginnt nur, wenn du ihn beginnst: über **Neuer Chat** oder
  indem du zwischen *Normal* und *Code* wechselst (dazu unten mehr).
  Über **⋯** lässt sich ein Chat **umbenennen** (der eigene Name überschreibt die
  erste Frage; leer lassen setzt zurück), **exportieren** (der ganze Chat als
  Markdown-Datei) oder **löschen** — Letzteres mit Rückfrage, denn das lässt sich
  nicht rückgängig machen.
* **Chats durchsuchen** über das Feld unter *Letzte Chats*. Gesucht wird in beidem:
  im Namen **und** im Wortlaut der Fragen und Antworten — wer nach „Mietvertrag"
  sucht, findet den Chat auch, wenn er „Frage zur Wohnung" heißt. Unter jedem Treffer
  steht die Fundstelle, der gesuchte Teil hervorgehoben; sonst müsste man jeden
  Treffer öffnen, um zu sehen, warum er einer ist. <kbd>Esc</kbd> oder das × beendet
  die Suche.
* **Antworten mitnehmen.** Unter jeder fertigen Antwort erscheinen beim Darauffahren
  zwei kleine Knöpfe: *Kopieren* legt den Wortlaut in die Zwischenablage,
  *Als Datei* speichert ihn als Markdown. Kopiert wird der Markdown-Text, nicht das
  gerenderte HTML — damit lässt sich weiterarbeiten.
* **Die Modellauswahl** oben in der Mitte zeigt, womit gerade **wirklich**
  gearbeitet wird. Im Code- und im Pro-Modus ist das nicht das eingestellte,
  sondern das stärkste erreichbare Modell — früher stand oben trotzdem das alte,
  man sah also nicht, dass ein anderes antwortet. Und man hat dort jetzt die
  **Wahl**: in Code und Pro stellt Aquaticy die **drei stärksten** Modelle zur
  Auswahl (und nur die — alles andere wäre eine Wahl, die gleich wieder
  überstimmt wird), im Standardmodus wie bisher alle. Was man dort im Code- oder
  Pro-Modus wählt, landet als *Code-Modell* in den Einstellungen und lässt das
  Modell des Standardmodus unangetastet.

  Das Fenster **scrollt jetzt als Ganzes**. Vorher scrollte allein die
  Modellliste, und alles darunter — Denktiefe, die Schalter, der Fuß mit dem Weg
  zu den Einstellungen — lag außerhalb des Bildschirms, ohne jede Möglichkeit,
  dorthin zu kommen: man konnte das Modell wählen, aber nicht, was es tun darf.
* **Ein Auftrag, der geantwortet hat, leuchtet.** Wer sich täglich, stündlich,
  wöchentlich oder monatlich etwas schicken lässt, sitzt beim Antworten nicht
  davor. Der Chat in der Seitenleiste bekommt deshalb einen ruhig pulsierenden
  Punkt und einen kräftigeren Namen, bis man ihn öffnet — danach sieht er aus
  wie jeder andere. Auf dem Handy, wo die Leiste zu ist, trägt der Knopf zur
  Leiste den Punkt. Kein Abzeichen mit Zahl: es geht um „da ist was", nicht um
  „da sind drei". Die Liste sieht einmal pro Minute selbst nach, damit das
  Leuchten auch ohne Neuladen ankommt.
* **Merkzettel** und **Neuer Chat** liegen daneben in der Kopfzeile.
* **Weggehen ist erlaubt.** Eine Anfrage lebt nicht mehr in ihrer Verbindung: der
  Lauf gehört dem Server. Wer die Seite verlässt, das Handy sperrt oder kurz in
  eine andere App wechselt, reißt nur die Leitung ab — Aquaticy arbeitet weiter.
  Beim Zurückkommen hängt sich die Seite von selbst wieder an und lässt den
  ganzen Verlauf ab dem ersten Ereignis nachwachsen (`[Weiter] Die Anfrage lief
  weiter, während du weg warst.`). Auch eine Antwort, die in der Zwischenzeit
  fertig wurde, ist noch da; wer sie einmal bis zum Schluss gesehen hat, bekommt
  sie nicht ein zweites Mal vorgesetzt.
* **Abbrechen:** Sobald eine Anfrage läuft, wird aus *Anhängen* ein
  *Abbrechen*. Ein Klick beendet den Lauf wirklich — nicht nur die Anzeige:
  der Browser hört auf zuzuhören *und* der Agent hört auf zu arbeiten. Was bis
  dahin da war, bleibt stehen. Danach ist der Knopf wieder das Anhängen.
* **Drei Arbeitsweisen**, umschaltbar unten neben *Anhängen*:
  **Normal** ist ein Gespräch — Aquaticy antwortet selbst, in normaler Länge, und
  sucht, wenn die Frage es braucht (alles Aktuelle, Örtliche, Preise, Zahlen,
  Versionen) oder wenn du ihn darum bittest („such mal", „stimmt das?"). Kein
  Bericht, keine Vorrecherche im Hintergrund, keine Agenten: eine Runde zum
  Modell, und das ist die schnellste Betriebsart, die es hier gibt.
  **Pro** ist derselbe Modus mit voller Leistung: dasselbe Antwortformat,
  dieselben Schalter — nur läuft er auf dem **stärksten Modell, das erreichbar
  ist**, und dahinter steht eine ganze Rechercheeinheit statt einer Handvoll
  Agenten.

  **Der Master.** Im Pro-Modus plant nicht mehr ein kleiner Planer, sondern der
  **Master** — und der ist das **Hauptmodell**: dasselbe, das oben in der
  Kopfzeile steht und am Ende die Antwort schreibt. Nichts Kleines nebenher; wer
  die Aufträge verteilt und die Rückmeldungen bewertet, muss die Frage so gut
  verstehen wie der, der sie beantwortet. Er macht drei Dinge:

  1. **Beauftragen.** Er entscheidet innerhalb der am Regler gewählten Grenze,
     wie viele Agenten die Frage braucht — in Pro zwischen **1 und 50** — und gibt jedem einen
     eigenen Auftrag *und* eine eigene
     Rolle, in seinen Worten („sucht Betreiberseiten statt Portale", „achtet
     auf Preise und deren Stand"). Zwei Aufträge darf er als schwer markieren;
     die gehen an die **zwei starken Agenten**, die auf dem starken Modell und
     mit größerem Werkzeug-Budget arbeiten — für das, was am schwersten zu
     finden ist.
  2. **Bewerten.** Wenn alle zurück sind, liest er die Rückmeldungen und sagt,
     was trägt und was nicht: leer, am Thema vorbei, nur Portalseiten ohne
     Inhalt, offensichtlich veraltet.
  3. **Nachschicken.** Was fehlt, vergibt er neu — mit anderer Technik als beim
     ersten Mal. Höchstens zwei Runden: danach liegt es nicht mehr an der
     Formulierung, sondern daran, dass es die Information nicht gibt, und genau
     das gehört dann in die Antwort. **Man sieht es**: `[Master] Lücken: …`,
     `[Nachrunde] 3 Aufträge noch einmal`. Eine Nachrunde ist kein Makel,
     sondern der Grund, warum am Ende etwas dasteht.

  Fällt der Master aus (Zeitlimit, Anbieter weg), plant der kleine Planer wie
  im Standardmodus. Er darf die Recherche besser machen — verhindern darf er
  sie nie.

  **`/max`.** Ohne den Befehl entscheidet der Master die Zahl. Mit ihm sind es
  alle: `/max Welche Fahrradläden in Bremen reparieren Lastenräder?` stellt die
  volle am Regler gewählte Mannschaft auf. Die beiden starken Agenten und,
  wenn *Gegenprüfen* an ist, die vier Prüfer behalten ihre bisherigen Aufgaben. Bleibt der
  Master unter der Zahl, wird mit Blickwinkeln aufgefüllt. `/max` allein
  getippt erklärt sich selbst.

  **Gegenprüfen heißt hier: vier Prüfer.** Sie recherchieren
  nicht, sie kontrollieren: jedes fertige Teilergebnis wird auf **anderen
  Seiten** gegengelesen — und zwar *während* die übrigen Agenten noch suchen,
  nicht danach. Ist die Recherche durch, helfen die frei gewordenen Agenten
  beim Prüfen mit. Deshalb kostet die Gegenprobe hier kaum Zeit, während sie im
  Standardmodus die Zeit verdoppelt. Der Prüfer antwortet mit einem Wort —
  **BESTÄTIGT**, **ABWEICHUNG** oder **UNKLAR** — und seinen Quellen; bei einer
  Abweichung nennt Aquaticy in der Antwort **beide** Angaben mit ihrer Quelle.
  Die vier laufen **nur mit dem Schalter**: die Denktiefe schaltet niemanden
  ein, sie sagt nur, wie lange das Modell überlegt.

  Beim Umschalten geht *Strukturieren* an — ohne das gibt es gar keine Agenten,
  und von Pro bliebe nur ein stärkeres Modell übrig. Der Schalter bleibt ein
  Schalter: wer ihn im Pro-Modus wieder auslegt, behält es so. Jeder Agent
  bekommt hier außerdem **acht statt sechs Werkzeug-Aufrufe** (die starken
  vierzehn): sechs reichen für eine Suche und drei gelesene Seiten, mit acht
  bleibt Luft, einer Quelle noch einen Schritt weit zu folgen.

  **Code** dreht das um: der Codeblock steht zuerst, Erklärungen nur
  wenn sie etwas hinzufügen, das nicht im Code steht. Vollständiger, lauffähiger
  Code statt Ausschnitten mit „…", Kommentare sagen *warum* statt *was*. Und
  Verlangt wird dort: Annahmen in einer Zeile über dem Block statt im Fließtext,
  nur Schnittstellen, die es wirklich gibt, Fehlerbehandlung dort wo sie hingehört
  (kein nacktes `except: pass`), ein Aufrufbeispiel oder ein kurzer Test als Beleg,
  dass es läuft — und bei Änderungen nur die geänderten Stellen statt der ganzen
  Datei. Fehlt Sprache, Version oder Zielsystem, fragt er, statt zu raten. Im Chat
  bekommt jeder Codeblock eine Kopfzeile mit der Sprache und einen **Kopieren**-Knopf.
  Aquaticy nimmt dafür **automatisch das stärkste Modell, das er erreichen kann**
  — beim Programmieren ist ein schwaches Modell am teuersten: Code, der falsch
  aussieht, erkennt man; Code, der falsch *ist*, nicht. Welches es war, steht in
  den Zwischenschritten; wer ein bestimmtes will, trägt es unter *Einstellungen
  → Modell → Code-Modell* ein. Die Quellenpflicht bleibt: erfundene
  Funktionsnamen sind hier der teuerste Fehler überhaupt — sie sehen richtig aus
  und laufen nicht.
* **Virtual Environment** — der Schalter, den es **nur im Code-Modus** gibt (dafür
  verschwinden dort *Im Web suchen* und *Gegenprüfen*: die gehören zur Recherche).
  Was ein Modus ausblendet, entscheidet dabei der Server und nicht der Browser —
  und ein ausgeblendeter Schalter überschreibt den gespeicherten Stand nicht: ein
  Ausflug in den Code-Modus macht das abgeschaltete Web nicht dauerhaft wieder an.
  Angeschaltet bekommt Aquaticy eine abgeschottete Maschine, in der er seinen Code
  **wirklich ausführt**, statt zu behaupten, er laufe: ein Prozessorkern, 1 GB
  Arbeitsspeicher, 4 GB Platte unter `/work`, Python — und **kein Netz**. Er
  schreibt die Datei hinein, startet sie, liest die Ausgabe und behebt, was
  schiefging, bevor er antwortet. Was dabei herauskam, steht in den
  Zwischenschritten.

  **Wie das abgesichert ist.** Code aus einem Sprachmodell ist fremder Code; er
  läuft nie auf deinem Rechner — auch nicht „nur kurz". Aquaticy nimmt die stärkste
  Abschottung, die er findet, und **fällt niemals auf den Rechner selbst zurück**:
  1. **gVisor** (`runsc`) — ein Kern im Nutzerraum beantwortet die Systemaufrufe,
     der echte Kernel wird nicht angefasst.
  2. **Podman ohne Wurzelrechte** — ein Ausbruch landet in einem unprivilegierten
     Nutzernamensraum, nicht bei root.
  3. **Docker mit gehärtetem Profil** — geteilter Kernel, deshalb die letzte Wahl.

  Findet er nichts davon, gibt es die Werkstatt nicht und das Werkzeug sagt, was zu
  installieren ist. Dazu in jedem Fall: `--network none` (kein Netz, weder hinaus
  noch ins Heimnetz), `--cap-drop ALL`, `--security-opt no-new-privileges`,
  `--read-only` (geschrieben wird nur in `/work` und ein 64-MB-`/tmp` im
  Arbeitsspeicher), ein unprivilegierter Benutzer, `--pids-limit` gegen die
  Gabelbombe, Speicher- und CPU-Deckel, **keine** Umgebungsvariablen von außen (deine
  Schlüssel sehen die Werkstatt nie) und **kein** Verzeichnis deines Rechners.
  Dateien gehen nur durch das Werkzeug hinein und heraus.

  **Nachschlagen geht weiter.** Die Werkstatt hat kein Netz — Aquaticy davor schon:
  er darf die Signatur einer Bibliothek im Web nachlesen und den Code dann in der
  Werkstatt ausprobieren. Der Schalter *Im Web suchen* gehört zum Standardmodus; im
  Code-Modus ist Nachschlagen immer erlaubt, damit ein „aus" von nebenan es nicht
  stillschweigend mitnimmt.

  **Dateien hinein und heraus.** Was du anhängst, landet zusätzlich unverändert in
  `/work/eingang` — auch ein Bild oder ein Zip, also alles, was der Textweg nicht
  hergibt. Umgekehrt zeigt der Knopf 🗀 in der Kopfzeile (nur im Code-Modus mit
  eingeschalteter Werkstatt), was gerade unter `/work` liegt, mit Größe und einem
  Knopf zum Herunterladen. Aquaticy selbst sieht dieselbe Liste über `vm_files` und
  kann dir deshalb sagen, wie die Datei heißt, die er gebaut hat.

  **Danach bleibt nichts.** 20 Minuten nach der letzten Nachricht werden Behälter und
  Datenträger gelöscht — die nächste Frage baut eine neue, leere Werkstatt. Beim
  Beenden von Aquaticy ebenso, und beim Start räumt er weg, was ein Absturz
  hinterlassen hat. Feineinstellung über `AQUATICY_VM_IMAGE`,
  `AQUATICY_VM_IDLE_MINUTES`, `AQUATICY_VM_MEMORY_MB`, `AQUATICY_VM_DISK_GB`,
  `AQUATICY_VM_CPUS`.

  **Größe der Werkstatt.** In den Einstellungen unter *Werkstatt* (oder per
  `AQUATICY_VM_SIZE`) wählst du zwischen zwei Größen — gilt für die nächste
  Werkstatt, die entsteht, nicht rückwirkend für eine laufende:

  | Größe | Kerne | Arbeitsspeicher | Speicher |
  |---|---|---|---|
  | `normal` (Standard) | 1 | 1 GB | 4 GB |
  | `plus` (Pro-Konto) | 4 | 6 GB | 20 GB |

  „Normal" reicht für die meisten Programmieraufgaben. „Plus" lohnt sich für
  alles, was mehr Rechenleistung braucht — zum Beispiel Blender (siehe unten).
  Wer einzelne Zahlen von Hand braucht, überschreibt sie weiterhin über
  `AQUATICY_VM_CPUS` / `_MEMORY_MB` / `_DISK_GB`; das gewinnt dann gegenüber
  der gewählten Größe.

  **Blender — nur im Code-Modus.** In der Werkstatt kann Aquaticy auch mit
  Blender arbeiten: 3D-Modelle bauen, Szenen einrichten, Materialien setzen,
  rendern. Das Werkzeug `blender_run(script, filename, timeout)` schreibt ein
  Python-Skript (die `bpy`-API) in die Werkstatt und startet es headless mit
  `blender --background --python <datei>`; fertige Dateien (`.blend`, PNGs,
  Exporte) liegen danach unter `/work` — dieselbe Stelle wie bei jedem
  anderen Code-Auftrag, abrufbar über 🗀 oder `vm_files`.

  Blender selbst bringt das Standardabbild (`python:3.12-slim`) nicht mit —
  absichtlich, sonst würde jede Werkstatt hunderte Megabyte laden, die kaum
  jemand für ein Python-Skript braucht. Ein fertiges Blender-Abbild baust du
  mit dem mitgelieferten `docker/workshop-blender.Dockerfile`:

  ```bash
  docker build -f docker/workshop-blender.Dockerfile -t aquaticy-workshop-blender:local .
  ```

  und trägst es dann ein:

  ```bash
  AQUATICY_VM_IMAGE=aquaticy-workshop-blender:local
  AQUATICY_VM_SIZE=plus
  ```

  Ohne dieses Abbild versucht Aquaticy es trotzdem — `blender_run` meldet dann
  ganz gewöhnlich „command not found", keinen Sonderfehler, und sagt dir das.

  **Eigene VM als zusätzliche Grenze.** Aquaticy erstellt keine virtuelle Maschine
  für den Rechner selbst. Läuft Aquaticy aber in einer eigenen VM, arbeitet die
  Werkstatt innerhalb dieser VM; deren Speicher-, CPU- und Netzwerkgrenzen schützen
  den äußeren Rechner zusätzlich. Die Werkstatt bleibt trotzdem nötig, denn ihre
  Grenzen gelten für jeden einzelnen Code-Auftrag. Die Laufzeit (gVisor, Podman oder
  Docker) muss in der VM installiert sein.
* **Im Web suchen an oder aus**, oben in der Modellauswahl — an ist der Normalfall.
  Im Code-Modus gibt es diesen Schalter nicht: dort zählt die Werkstatt.
  Ausgeschaltet geht Aquaticy nicht mehr hinaus: Suche, Seitenabruf und Agenten werden
  ihm gar nicht erst angeboten (ein Werkzeug anzubieten und den Aufruf dann abzulehnen
  kostet nur Runden). Er antwortet dann aus seinem eigenen Wissen, aus dem Gespräch,
  aus **angehängten Dateien** und aus seinem Speicher — und sagt dazu, woher er es hat
  und wo sein Wissen alt sein könnte. Die örtlichen Werkzeuge bleiben: Rechnen,
  Speicher, Lager, Zuhause. Steht der Schalter auf aus, sagt es die Kopfzeile.
* **Denktiefe: Low, Medium, High**, oben in der Modellauswahl. Wie lange das
  Modell überlegen darf, bevor es antwortet — der eine Regler, der Tempo und
  Gründlichkeit gegeneinander stellt. Medium ist der Standard, Low für flotte
  Fragen, High für schwierige. Die Stufe geht als `reasoning_effort` an den
  Anbieter; wer den Begriff nicht kennt, bekommt ihn dank `drop_params` gar
  nicht erst zu sehen. Steht sie nicht auf Medium, sagt es die Kopfzeile.
* **Normal oder Code** wechselt die Arbeitsweise — und fängt dabei einen neuen Chat
  an, sofern im alten schon etwas steht. Code und Prosa im selben Verlauf zu mischen
  geht selten gut: das Modell wechselt, der Systemtext wechselt, und die halbe
  Unterhaltung davor passt nicht mehr zu dem, was jetzt gefragt ist.
* **Denken an oder aus**, in der Modellauswahl (nur im Standardmodus) — aus ist der
  Normalfall. Angeschaltet denkt Aquaticy sichtbar nach, bevor er antwortet: du siehst
  zu, wie er sich die Antwort zurechtlegt, Wort für Wort in einer Zeile über der
  Antwort. Ausgeschaltet kommt nur das Ergebnis. Auf die Antwort selbst hat der
  Schalter keinen Einfluss — sie wird davon weder besser noch langsamer, und er
  geht auch gar nicht erst an den Server. Er ist etwas anderes als *Strukturieren*
  darunter und ersetzt es nicht: hier geht es darum, was du siehst, dort darum, wie
  gearbeitet wird. Nur Modelle, die ihre Denkschritte überhaupt herausgeben, haben
  dazu etwas zu zeigen. Die Denkschritte hängen **allein** an diesem Schalter:
  *Mitlesen* in den Einstellungen zeigt Suchanfragen, geöffnete Seiten und
  Werkzeugausgaben, aber keine Gedanken. Sonst stünde der Block da, obwohl *Denken*
  aus ist — und der Schalter wäre eine Behauptung.
* **Strukturieren an oder aus**, in der Modellauswahl oben — aus ist der Normalfall.
  (Der Schalter hieß einmal „Denken". Gedacht wird immer; was er umlegt, ist die
  Zerlegung.)
  Angeschaltet wird Aquaticy vom Gesprächspartner zum Rechercheagenten: er zerlegt
  die Frage in Teilfragen, schickt für jede einen Agenten los und schreibt aus
  deren Funden eine ausführliche Antwort mit Quellen, Vergleich, Fazit und einem
  Abschnitt „Nicht gefunden". **Die Agenten teilen sich dabei eine Liste der
  schon gelesenen Seiten und meiden sie** — sonst laufen drei Teilfragen zum
  selben Thema auf dieselben zwei Seiten zu und die Zerlegung bringt keine
  Breite. Das kostet zwei Runden zum Modell, bevor die erste Suche losgeht;
  ausgeschaltet entfallen beide. Steht es an, sagt es die Kopfzeile.
* **Gegenprüfen**, der Schalter unter *Strukturieren* (im Code-Modus nicht — dort zählt
  die Werkstatt). Im **Pro-Modus** bedeutet er etwas anderes, und das steht auch dran:
  dort schickt er die vier Prüfer mit, die nebenher gegenlesen (siehe oben). Im
  Standardmodus ist er die klassische zweite Runde: ist er an, wird nach der Antwort
  garantiert noch einmal gesucht — Aquaticy holt die frischen Treffer selbst, bevor
  das Modell wieder zu Wort kommt, und lässt dabei jede Seite aus, die beim ersten
  Mal dran war. (Vorher konnte das Modell die Aufforderung überlesen und seine
  alte Antwort einfach noch einmal hinschreiben.) Findet die zweite Runde etwas
  anderes, steht es in der Antwort; findet sie nichts Neues, sagt sie das. Gut für
  Zahlen, die nur in einer einzigen Quelle so stehen. Kostet ungefähr die doppelte
  Zeit. Dass gegengeprüft wurde, steht danach als Vermerk **an der Antwort** —
  nicht nur in den Zwischenschritten, die ja nur sieht, wer sie aufklappt.
* **Erscheinungsbild** unten links in der Seitenleiste öffnet ein eigenes Fenster:
  **hell**, **dunkel** oder **wie das System** — und darunter acht Farbschemata.
  *Standard* sind die Farben, die du kennst (warmes Papier, grüner Akzent); dazu
  kommen Nord, Catppuccin, Gruvbox, Tokyo Night, Solarized, Dracula und Rosé Pine,
  jedes in einer hellen und einer dunklen Fassung. Der Aufbau der Oberfläche bleibt
  in jedem Schema exakt derselbe — es wechseln nur die Farben. Die Wahl gilt sofort
  und bleibt in dem Browser gespeichert, in dem du sie triffst.
* **Ein Gruß bleibt ein Gruß.** „Hallo", „danke", „passt" lösen keine Recherche
  aus — auch nicht mit ausgeschaltetem Denken, wo sonst jede Eingabe direkt an
  einen Agenten ging. Und kommt eine Vorrecherche mit leeren Händen zurück,
  wandert dieses Nichts nicht als „Quellenlage" in den Kontext: sonst schreibt
  das Modell eine Erklärung darüber, statt zu antworten.
* **Bewegung.** Fenster wachsen aus der Mitte und blenden beim Schließen wieder
  aus, die Liste der Chats läuft gestaffelt ein, jeder Zwischenschritt kommt von
  links herein, der Senden-Pfeil schnellt kurz nach oben, gewählte Farbschemata
  blenden ineinander statt umzuspringen. Gebaut nach dem, was sich als angenehm
  durchgesetzt hat: Eintritte laufen mit `ease-out` aus, Austritte mit `ease-in`
  an, 130 ms für Kleinigkeiten, 220–300 ms für Fenster, nie mehr als eine halbe
  Sekunde — ein Test hält diese Obergrenze fest. Bewegt werden nur `transform`
  und `opacity`, die den Browser nichts kosten; auch das prüft ein Test. Wer im
  System „weniger Bewegung" eingestellt hat, bekommt gar keine. Fortschritts-
  oder Statusbalken gibt es bewusst nicht.
* **Einstellungen im Gespräch ändern:** „Mach den Hintergrund weiß", „such lieber
  auf Englisch", „nimm weniger Teilfragen" — das erledigt Aquaticy direkt, statt dich
  ins Formular zu schicken. Änderbar sind Erscheinungsbild, Farbschema, Ort,
  Sprache, Land, Suchmaschine, Formulierungen je Suche, Subagenten, Werkzeug-Budget, Kontextfenster,
  Browser-Fallback und das Modell.

  **Nicht änderbar sind Zugangsdaten und alles, was Aquaticy mehr Zugriff gäbe:**
  Schalten im Haus, Mail und Kalender, Schreibrechte im Lager, Netzzugriff,
  Gedächtnis. Das ist kein Misstrauen, sondern Bauweise: Wer Rechte vergibt, darf
  nicht derselbe sein, der sie bekommt — sonst wäre die dreistufige Rechteauswahl beim
  Lager eine Verabredung statt einer Grenze, und ein Satz im Chat würde genügen, um
  sie aufzuheben. Diese Schalter bleiben im Formular.
* **Mitlesen** unter *Einstellungen → Mitlesen*: Der Schalter „Aktionen mitlesen"
  zeigt während der Antwort, was Aquaticy AI gerade tut — jede Suchanfrage im
  Wortlaut, jeden Werkzeugaufruf mit seinen Argumenten und was zurückkam. Die
  Denkschritte gehören ausdrücklich **nicht** dazu; die schaltet *Denken* in der
  Modellauswahl ein. Der Schalter wirkt sofort und auch rückwirkend auf die
  Antwort, die schon dasteht: die Zeilen sind die ganze Zeit da, sie werden nur
  ein- und ausgeblendet.
* **Verbindung testen** im Feld *Suche*: schickt eine winzige Anfrage ans Modell und
  eine Testsuche los — dasselbe, was `aquaticy setup` am Ende macht. Geprüft wird, was
  gerade im Formular steht, nicht der gespeicherte Stand; so sieht man vor dem
  Speichern, ob ein Schlüssel stimmt.
* **Auslastung** für Pro-Konten unter *Einstellungen → Auslastung*: Der Haken „Auslastung des
  Rechners anzeigen" blendet Prozessor, Arbeitsspeicher, Festplatte, Grafikkarte und
  den belegten Speicher als Kacheln ein — alle vier Sekunden aufgefrischt, solange das
  Einstellungsfenster offen ist. Standardmäßig aus; der Haken bleibt im Browser
  gemerkt, gefragt wird nur, während du hinschaust.
* **Nutzung** unter *Einstellungen → Nutzung*: drei Kacheln zeigen, wie viele Token
  heute, in den letzten sieben Tagen und insgesamt durch die Leitung gegangen sind —
  hinein und heraus getrennt, darunter die Aufschlüsselung je Modell. **Ein Token
  sind hier drei Zeichen.** Das ist eine Vereinbarung, keine Messung: jeder Anbieter
  zerlegt Text anders, und die genauen Zahlen bekäme man nur mit dessen eigenem
  Zerleger. Für Größenordnungen reicht es — ob eine Frage hundert oder hunderttausend
  Token gekostet hat, sieht man so. Gezählt wird, was wirklich hinausgeht: der
  Systemtext und das ganze Gespräch bei *jedem* Aufruf (die Schnittstelle ist
  zustandslos, genau so rechnen die Anbieter auch ab) plus die Antwort und die
  Argumente der Werkzeugaufrufe. Bei normalen Konten endet das Kontingent bei insgesamt
  400.000 Token. Pro-Konten bleiben unbegrenzt. Der Zähler lässt sich nicht zurücksetzen.
* **Aufträge** unter *Einstellungen → Aufträge*: Fragen, die Aquaticy AI von selbst
  stellt — stündlich, täglich oder wöchentlich zu einer festen Uhrzeit. Die Antwort
  landet als Chat in der Seitenleiste, als hättest du sie selbst gestellt — und
  **leuchtet dort, bis du sie geöffnet hast**: ein ruhig pulsierender Punkt und ein
  kräftigerer Name. Danach sieht der Chat aus wie jeder andere. Beim Antworten
  sitzt ja niemand davor; ohne das Leuchten ginge die Antwort in der Liste unter.
  Auf dem Handy trägt der Knopf zur Seitenleiste den Punkt, weil die Leiste dort
  zu ist. Jeder
  Auftrag lässt sich anhalten, sofort ausführen (*Jetzt*) und löschen; die Zeile
  darunter sagt, wann er das nächste Mal dran ist und wie der letzte Lauf ausging.
  Zwei Entscheidungen dahinter: Ein Auftrag baut sich seinen **eigenen Agenten**,
  fasst also dein laufendes Gespräch nicht an. Und **verpasste Termine werden nicht
  nachgeholt** — wer den Rechner eine Woche aus hat, will beim Einschalten nicht
  sieben Recherchen auf einmal. Rückfragen kann ein Auftrag nicht stellen, es sitzt
  ja niemand davor; er muss mit dem auskommen, was in der Frage steht. Höchstens
  20 Aufträge — mehr wären eine zweite To-do-Liste, die man auch noch pflegen muss.
* **Alle Slash-Befehle** aus dem Terminal funktionieren auch hier: `/location`,
  `/model`, `/image`, `/export`, `/history`, `/notes`, `/clear`, `/help`. `/image`
  nimmt einen Dateipfad oder einen Ordner vom selben Rechner — bei mehreren Bildern
  im Ordner nimmt er das neueste.

```bash
aquaticy web --port 9000     # anderer Port, falls 8765 belegt ist
aquaticy web --no-open       # ohne Browser zu öffnen
```

Der Server läuft auf der Standardbibliothek, braucht also keine zusätzliche
Abhängigkeit. Beenden mit <kbd>Strg</kbd>+<kbd>C</kbd>.

### Vom Handy oder Tablet: `aquaticy web --lan`

```bash
aquaticy web --lan
```

Damit hört aquaticy auf allen Netzwerkkarten und nennt dir jede Adresse, unter der
er erreichbar ist — im heimischen Netz und über Tailscale:

```
╭───────────────────────────────────────────────────────────────────╮
│ Aquaticy AI 9.4.3                                                   │
│ Diese Adresse im Browser oeffnen:                                 │
│   http://192.168.1.44:8765/    im heimischen Netz                 │
│   http://100.81.120.100:8765/  ueber Tailscale                    │
│   http://127.0.0.1:8765/       auf diesem Rechner                 │
│ Modell ollama_chat/gemma4:12b · Suche duckduckgo                  │
│ Kein Zugangswort: Adresse und Port genuegen.                      │
╰───────────────────────────────────────────────────────────────────╯
```

Beim ersten Besuch erklärt Aquaticy kurz, welche Daten für Anmeldung und getrennte
Konten nötig sind. Danach registrierst du dich oder meldest dich mit einem bestehenden
Konto an. Die Tailscale-Adresse erscheint nur, wenn Tailscale läuft.

Wer den Zugang trotzdem einschränken will, vergibt ein Wort:

```bash
aquaticy web --lan --token familie   # dann nur mit ?token=familie in der Adresse
aquaticy web --host 192.168.1.44     # gezielt eine Netzwerkkarte
```

Das zusätzliche Zugangswort schützt schon die Startseite. Der Browser übernimmt es
beim ersten Aufruf in ein geschütztes Cookie und Aquaticy entfernt es aus der
Adresszeile. Es darf nur ASCII enthalten, also etwa `gruen` statt `grün`.

**Wenn das andere Gerät die Seite nicht lädt:** meist blockt die Firewall des
Rechners den Port. Unter Windows fragt die Firewall beim ersten Start nach —
dort „privates Netzwerk" erlauben. Unter Linux mit ufw: `sudo ufw allow 8765/tcp`.
Ohne Tailscale müssen beide Geräte im selben Netz sein (nicht eines im
WLAN-Gastzugang).

Jedes Konto hat eigene Chats, Einstellungen, Aufträge, Speicherdateien und eine eigene
Werkstatt. Mehrere Konten können gleichzeitig mit Aquaticy arbeiten. Innerhalb eines Kontos werden zwei
gleichzeitig gestellte Fragen geordnet, damit der Gesprächsverlauf verständlich bleibt.
Ins offene Internet stellt `--lan` nichts: dafür bräuchte es zusätzlich eine
Portfreigabe im Router. Über Tailscale erreichst du aquaticy auch von unterwegs,
ohne eine solche Freigabe — genau dafür ist es da.

## Chat-Interface

| Befehl | Wirkung |
|---|---|
| `/max <frage>` | im Pro-Modus mit voller Mannschaft recherchieren |
| `/location <ort>` | Ortsfilter setzen (ohne Argument: aufheben) |
| `/model <name>` | Modell wechseln, z. B. `mistral/mistral-large-latest` |
| `/export html\|md\|csv` | Recherche dieser Sitzung speichern |
| `/image <pfad>` | Bild beschreiben lassen, danach damit recherchieren |
| `/history` | frühere Recherchen anzeigen |
| `/notes` | Merkzettel anzeigen (pflegen: `aquaticy notes --delete N`) |
| `/clear` | Gesprächsverlauf verwerfen |
| `/help` | Übersicht |
| `/quit` | beenden (auch <kbd>Strg</kbd>+<kbd>D</kbd>) |

Der Kontext bleibt über mehrere Turns erhalten, Nachfragen wie „nur die mit 4+ Sternen"
funktionieren also.

**Umgekehrt fragt aquaticy auch selbst nach.** Ist etwas Entscheidendes offen — Budget,
Ort, welches von mehreren Dingen gemeint ist —, stellt er *eine* Rückfrage und wartet
auf die Antwort. Im Terminal tippst du sie ein (oder die Nummer einer angebotenen
Möglichkeit), Enter allein überspringt. Er fragt höchstens zweimal je Anfrage und nur,
wenn die Antwort das Ergebnis wirklich ändert — sonst trifft er lieber eine Annahme und
schreibt sie in die Antwort.

### Flags

```bash
aquaticy --location "Mönchengladbach" --lang de   # Ortsfilter vorgeben
aquaticy --model mistral/mistral-large-latest     # Modell für diese Sitzung
aquaticy --image foto.jpg                         # Bild als Ausgangspunkt
aquaticy --max-calls 30                           # Werkzeug-Budget ändern
aquaticy --no-stream                              # Antwort am Stück statt gestreamt
aquaticy --download-images                        # Bilder beim Export mitspeichern
```

### Weitere Unterbefehle

```bash
aquaticy search "cafés mönchengladbach"    # nur web_search, ohne LLM
aquaticy fetch https://example.de/         # nur fetch_page, ohne LLM
aquaticy cache                             # Cache-Statistik, --clear leert ihn
aquaticy history                           # vergangene Recherchen
aquaticy export html -n 3                  # letzte 3 Recherchen exportieren
aquaticy config                            # aktive Konfiguration prüfen
aquaticy install-model                     # lokales Modell einrichten (ohne Key)
aquaticy install-browser                   # Playwright-Fallback aktivieren
aquaticy web                               # Oberflaeche im Browser starten
aquaticy web --lan                         # auch vom Handy im heimischen Netz
aquaticy lan                               # Geraete im eigenen Netz anzeigen
aquaticy connect-ha                        # Home Assistant verbinden
aquaticy google                            # Gmail und Kalender verbinden (lesend)
aquaticy google --aendern                  # dazu Termine ändern und Entwürfe schreiben
```

## Der Speicher: was Aquaticy AI behält

Ohne Speicher fängt jedes Gespräch bei null an. Mit Speicher merkt sich Aquaticy AI, was
länger gilt — Wohnort, Vorlieben, laufende Vorhaben — und findet es beim nächsten Mal
wieder.

```
> ich suche einen laptop bis 1200 euro für bildbearbeitung
  [Merke] Sucht Laptop bis 1200 Euro für Bildbearbeitung

  (drei Tage später, neues Gespräch)

> gibt es dazu was neues
  [Speicher] laptop
  Du suchst einen Laptop bis 1200 Euro für Bildbearbeitung. Dazu ist neu: …
```

**Persönliches merkt er sich von selbst.** Sagst du beiläufig „ich heiße Jonas",
„ich wohne in Bremen", „Kaffee mag ich nicht", legt Aquaticy das ab, ohne dass du
darum bitten musst — solche Sätze kommen nebenbei und kehren nicht wieder; wer sie
nicht mitschreibt, fragt in zwei Wochen noch einmal danach. Er sagt in einem
Halbsatz dazu, dass er es sich gemerkt hat (heimlich mitschreiben wäre unhöflich),
und legt es unter dem Thema *person* ab. Das hat einen Grund: Alles unter diesem
Thema steht beim nächsten Gespräch **von allein im Systemtext** — es hängt also
nicht daran, ob das Modell auf die Idee kommt, im Speicher nachzusehen. Ein Name ist
nichts, wonach man sucht; er soll einfach dastehen. Zieht du um, schreibt er den
neuen Stand unter dasselbe Thema. Belangloses, Tagesaktuelles und Vermutungen über
dich landen nicht im Speicher, und was du ausdrücklich nicht gespeichert haben
willst, auch nicht.

Der **Merkzettel** (`/notes`) ist etwas anderes: dorthin schreibt er nur auf
ausdrückliche Bitte („merk dir …"), dafür hängt sein Inhalt an *jedem* Gespräch.

Vier Regeln bestimmen den Aufbau:

* **Nur Text.** Aquaticy AI legt ab, was es selbst formuliert hat. Bilder und Dateien
  kommen ausschließlich von dir und liegen getrennt.
* **Verschlüsselt.** Die Notizen stehen nicht im Klartext in der Datenbank. Wer die
  Datei kopiert — aus einem Backup, von einem verlorenen Laptop — liest ohne Schlüssel
  nichts.
* **Höchstens 400 MB**, zusammen mit Verlauf und hochgeladenen Dateien. Wird es eng,
  fliegen zuerst alte Uploads raus: ein Bild liegt meist noch woanders, eine Notiz nicht.
* **Abschaltbar** unter *Einstellungen → Speicher*, oder mit `AQUATICY_MEMORY=false`.

```bash
/memory            # was liegt drin, wie voll ist es
/forget            # alle Notizen löschen
/uploads           # was du hochgeladen hast
/uploads clear     # alle hochgeladenen Dateien löschen
```

### „Was weißt du über mich?"

Ein Gedächtnis, in das man nicht hineinsehen kann, ist keins — es ist ein Gerücht.
In der Weboberfläche steht unter *Einstellungen → Speicher* deshalb der Knopf **Was
weißt du über mich?**. Er öffnet eine Liste: jeder Eintrag mit Thema, Wortlaut und
Datum, daneben ein Knopf, der genau diesen einen Eintrag entfernt. Darunter *Alles
vergessen*, falls es das sein soll. Ist der Speicher abgeschaltet, sagt das Fenster
das — statt eine leere Liste zu zeigen, die man für „er weiß nichts" halten könnte.

### Was die Verschlüsselung leistet — und was nicht

Der Schlüssel liegt standardmäßig als Datei neben der Datenbank, lesbar nur für dein
Benutzerkonto. Das schützt alles, was die Datei allein betrifft: Backups, Kopien,
Datenträger in fremden Händen. Es schützt **nicht** gegen jemanden, der schon in deinem
Benutzerkonto sitzt — der liest den Schlüssel einfach mit.

Wer auch das abdecken will, setzt eine Passphrase:

```bash
AQUATICY_MEMORY_KEY="ein langes Passwort" aquaticy web
```

Dann wird der Schlüssel bei jedem Start neu abgeleitet und liegt nirgends auf der
Platte. Der Preis: ohne die Passphrase ist der Speicher unwiederbringlich weg.

## Die Lagerverwaltung

„Wo liegt das Ladekabel", „habe ich noch 4×40er Schrauben", „was ist alles im
Keller" — dafür gibt es kein Suchergebnis im Web. Aquaticy kann diese Fragen aus der
[Lagerverwaltung](https://github.com/jonasenriklaumen-a11y/storage-system) beantworten,
wenn sie im selben Netz läuft: **Räume → Möbel → Artikel**, jeder Artikel mit einer
eindeutigen Nummer wie `B42`.

### Einrichten

*Einstellungen → Zuhause & Netz → Lagerverwaltung*. **Suchen** klopft das eigene Netz
ab und trägt die Adresse ein; **Testen** sagt, was dort steht. Von Hand geht auch:
`192.168.1.5:3000`.

> Gesucht wird nicht „irgendwas auf Port 3000" — jeder Kandidat wird gefragt, ob er
> sich als Lagerverwaltung zu erkennen gibt (`GET /api/config` nennt `app`, `name` und
> `version`). Auf Port 3000 läuft in vielen Haushalten irgendein anderer
> Entwicklungsserver.

### Was Aquaticy dort darf

Drei Stufen, einzustellen unter *Was Aquaticy dort darf*:

| Stufe | Was geht |
|---|---|
| **Nichts** | Abgeschaltet. Die Werkzeuge existieren für das Modell gar nicht. |
| **Nur lesen** | Suchen, stöbern, zählen. |
| **Lesen und schreiben** | Zusätzlich Artikel, Möbel und Räume anlegen und ändern. |

Die Stufe wirkt an zwei Stellen, nicht an einer: Was nicht erlaubt ist, wird dem Modell
**gar nicht erst angeboten** — und der Client lehnt es zusätzlich ab, falls es doch
danach fragt. Ein Werkzeug, das nicht existiert, kann nicht falsch benutzt werden; das
ist verlässlicher als eine Bitte im Prompt.

**Gelöscht wird in keiner Stufe.** Ein versehentlich angelegter Artikel ist in zehn
Sekunden wieder weg; ein gelöschter Raum nimmt alle Möbel und Artikel darin mit, und
das lässt sich nicht rückgängig machen. Diese Entscheidung gehört einem Menschen vor
der Oberfläche.

> **Und eine Ehrlichkeit dazu:** Die Lagerverwaltung selbst kennt keine Anmeldung — sie
> ist fürs Heimnetz gebaut, wer drin ist, darf schreiben. „Nur lesen" ist damit eine
> Fessel für Aquaticy, **kein Schloss am Server**. Wer ihn über das Heimnetz hinaus
> erreichbar macht, braucht davor einen Reverse Proxy mit Anmeldung.

### Was danach geht

```
> ich habe 8 Schrauben entnommen, trag das ein

  [Lager] suchen schraub
  [Lager] 2 Einträge
  [Lager] ändern Artikel 2
  Eingetragen: A2 „Schrauben 4×40" steht jetzt bei 230 (vorher 238),
  Keller › Regal links.
```

Vier Werkzeuge: `storage_find` (Nummer oder Name), `storage_browse` (Räume → Möbel →
Artikel), `storage_add` und `storage_edit`. Beim Ändern des Bestands nimmt Aquaticy
bewusst die *relative* Änderung (`delta`), nicht den gesetzten Wert: Wenn zwei Leute
gleichzeitig eine Schraube entnehmen, kommen so beide Entnahmen an — ein gesetzter Wert
würde eine davon überschreiben.

## Gmail und Google Kalender

„Wann ist mein Zahnarzttermin", „ist die Rechnung schon gekommen", „was habe ich
Donnerstag vor" — dafür muss niemand das Web durchsuchen. Aquaticy AI kann direkt in
deinem Kalender und deinem Postfach nachsehen, wenn du es erlaubst.

Die Angaben helfen auch bei einer Recherche: Steht der Termin in Hamburg, sucht er für
Hamburg. Nennt die Bestellbestätigung eine Modellnummer, sucht er danach.

**Standardmäßig nur lesen.** Angefragt werden ausschließlich die Leserechte
`gmail.readonly` und `calendar.readonly`. Damit ist technisch ausgeschlossen, dass
Aquaticy AI eine Mail verschickt, beantwortet, löscht oder einen Termin ändert — Google
lässt es schlicht nicht zu. Bittest du ihn trotzdem darum, sagt er, dass er das nicht
kann.

**Ändern ist ein eigener Schalter.** Setzt du in den Einstellungen zusätzlich den Haken
*Ändern erlaubt* und verbindest danach neu, kommen genau zwei Rechte dazu:
`calendar.events` (Termine anlegen und ändern) und `gmail.compose` (**Entwürfe**
schreiben). Bewusst **nicht** `gmail.send`: ein Entwurf lässt sich noch lesen, bevor er
hinausgeht, eine verschickte Mail nicht zurückholen. Aquaticy verschickt nichts und
löscht nichts — dafür holt er sich die Rechte gar nicht erst. Und vor jeder einzelnen
Änderung fragt er in einem Fenster nach; sagst du nein, passiert nichts. Läuft gerade
niemand davor (etwa bei einem Auftrag, siehe unten), wird ebenfalls nichts geändert.

**Aus, bis du es einschaltest.** Ohne den Haken in den Einstellungen und ohne
verbundenes Konto existieren die Werkzeuge für das Modell gar nicht.

### Einrichten — einmal, etwa fünf Minuten

Google verlangt für den Zugriff auf ein eigenes Konto eine eigene Anwendung. Das klingt
umständlicher, als es ist, und es ist kostenlos.

1. **Projekt anlegen.** Auf [console.cloud.google.com](https://console.cloud.google.com/)
   anmelden, oben links auf die Projektauswahl, *Neues Projekt*. Der Name ist egal,
   zum Beispiel „Aquaticy".
2. **Die beiden APIs einschalten.** *APIs und Dienste → Bibliothek*, nach `Gmail API`
   suchen, **Aktivieren**. Dasselbe mit `Google Calendar API`. Ohne diesen Schritt
   antwortet Google später mit „has not been used in project".
3. **Zustimmungsbildschirm.** *APIs und Dienste → OAuth-Zustimmungsbildschirm*,
   Nutzertyp **Extern**. App-Name und deine Mailadresse eintragen. Unter
   **Zielgruppe** dich selbst als **Testnutzer** hinzufügen — das ist der Schritt,
   den fast alle vergessen; ohne ihn lehnt Google die Anmeldung ab.
4. **Zugangsdaten.** *APIs und Dienste → Anmeldedaten → Anmeldedaten erstellen →
   OAuth-Client-ID*, Anwendungstyp **Desktop-App**. Als autorisierte
   Weiterleitungs-URI `http://localhost:8765/google` eintragen (bei anderem Port
   entsprechend anpassen). Google zeigt dir danach **Client-ID** und
   **Client-Secret**.
5. **Verbinden.** Zwei Wege, beide gleichwertig:

```bash
aquaticy google            # fragt nach ID und Secret, führt durch die Anmeldung
aquaticy google --aendern  # dasselbe, aber mit Schreibrechten (siehe oben)
```

   Oder in der Weboberfläche: *Einstellungen → Gmail & Kalender*, Haken setzen,
   Client-ID und Secret einfügen, **speichern**, dann **Verbinden**. Du landest bei
   Google, stimmst zu, und bist zurück. Soll Aquaticy auch ändern dürfen, setz vorher
   den Haken *Ändern erlaubt* — er entscheidet, welche Rechte angefragt werden.
   Schaltest du ihn später um, musst du einmal neu verbinden.

> **Vom Handy aus?** Google erlaubt für Desktop-Anwendungen nur `localhost` als
> Rückweg. Sitzt dein Browser auf einem anderen Gerät als Aquaticy, zeigt er nach der
> Zustimmung eine Fehlerseite — das ist normal. Kopiere die komplette Adresse aus der
> Adresszeile und füge sie in das Feld unter *Verbinden* ein; der Code steht darin.

### Was danach geht

```
> was habe ich diese Woche vor

  [Termine] 7 Tage
  Drei Termine:
  1. Mi 09:30–10:00  Zahnarzt, Bremen
  ...

> habe ich eine Mail von der Bahn bekommen

  [Mail] from:bahn newer_than:30d
  Ja, zwei. Die neuere vom 3. September: „Ihre Reiseverbindung" ...
```

Der Agent bekommt drei Werkzeuge: `calendar_events` (Termine des Hauptkalenders),
`mail_search` (Absender, Betreff, Datum, erste Zeilen — Gmail-Syntax wie `from:dhl`,
`is:unread`, `newer_than:7d`) und `mail_read` (Text einer einzelnen Mail).

Mit *Ändern erlaubt* kommen drei weitere dazu — und nur diese drei:

```
> trag mir Donnerstag 14 Uhr Zahnarzt ein

  Soll ich „Zahnarzt" am 2026-09-10T14:00:00 eintragen?   [ja] [nein]
  Eingetragen: Donnerstag, 10. September, 14:00–15:00.

> schreib der Werkstatt eine Mail, dass ich den Termin verschieben muss

  Soll ich einen Entwurf an werkstatt@example.com mit dem Betreff
  „Terminverschiebung" anlegen? (Verschickt wird nichts.)   [ja] [nein]
  Liegt in Gmail unter „Entwürfe". Du kannst ihn dort lesen und selbst abschicken.
```

`calendar_add` legt einen Termin an, `calendar_edit` ändert einen bestehenden (die
Kennung holt er sich vorher über `calendar_events` — geraten wird sie nie), `mail_draft`
schreibt einen Entwurf. Fehlt eine Angabe — welcher Tag, wie lange, an wen —, fragt er
nach, statt sie zu erfinden.

### Was mit deinen Daten passiert

* **Die Anmeldedaten bleiben auf deinem Rechner.** Access- und Refresh-Token liegen
  verschlüsselt in `~/.aquaticy/google.json` (dieselbe Fernet-Schlüsseldatei wie beim
  Speicher, Rechte 600). Der Browser bekommt sie nie zu sehen — nur, *ob* ein Konto
  verbunden ist und welche Adresse es hat.
* **Nichts aus deinem Postfach geht an eine Suchmaschine.** Der Agent hat die
  ausdrückliche Anweisung, niemals Namen, Adressen, Nummern oder Betreffs aus Mails
  und Terminen in eine Suchanfrage zu setzen — die ginge an einen fremden Dienst. Er
  sucht mit allgemeinen Begriffen; das Persönliche bleibt im Gespräch.
* **Beenden jederzeit:** `aquaticy google --trennen` oder der Knopf *Trennen* in den
  Einstellungen löscht die Anmeldedaten. Den Zugriff selbst entziehst du zusätzlich
  unter [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

## Zuhause: Heimnetz und Home Assistant

Manche Fragen kann kein Suchtreffer beantworten. „Welche Geräte hängen hier im Netz",
„läuft mein Drucker noch", „wie warm ist es im Wohnzimmer" — dafür sieht aquaticy selbst
nach.

### Das eigene Netz

```bash
aquaticy lan                      # zeigt, was erreichbar ist
aquaticy lan --thorough           # alle bekannten Ports statt der zwölf häufigsten
aquaticy lan --subnet 10.0.0.0/24
```

```
Adresse        Name              Läuft dort
192.168.1.1    fritz.box         Webseite, DNS
192.168.1.5    homeassistant     Home Assistant
192.168.1.23   HP-LaserJet       Drucker (IPP), Drucker (RAW)
192.168.1.44   nas               Weboberfläche (Synology), Windows-Freigabe, SSH
```

Im Chat geht dasselbe in Worten: *„welche Geräte hängen in meinem Netz"*, *„ist
192.168.1.23 noch da"*. Zwei Grenzen sind fest verdrahtet:

* **Nur private Netze** — 10.x, 172.16–31.x, 192.168.x und das Tailnet (100.64/10).
  Fremde Adressen lehnt aquaticy ab, in jeder Schreibweise. Höchstens 512 Adressen am
  Stück, ein `/16` also nicht.
* **Nur die Frage „antwortet da etwas"** — aquaticy klopft an, liest den Titel einer
  Weboberfläche und geht weiter. Keine Passwortversuche, keine Schwachstellensuche.
  Ein Gerät, das nicht antwortet, heißt „nicht erreichbar", nie „existiert nicht": es
  kann auch schlafen.

Abschalten: `AQUATICY_LAN_ENABLED=false` oder der Haken in den Einstellungen.

### Home Assistant

**aquaticy bringt kein Home Assistant mit und startet keins.** Er sucht das, das bei dir
schon läuft, und meldet sich dort mit einem langlebigen Zugriffstoken an — genau wie
jede andere App, der du Zugriff gibst. Deine Installation bleibt unangetastet; aquaticy
ist nur ein weiterer Client.

```bash
aquaticy connect-ha
```

Das sucht die Instanz selbst im Netz (erst die üblichen Namen wie `homeassistant.local`,
dann das Netz nach Port 8123), zeigt dir, wo du das Token herbekommst — *Profil →
Sicherheit → Langlebige Zugriffstokens* —, testet die Verbindung und schreibt Adresse
und Token in die `.env`. Eine Minute, dann kannst du fragen:

```
> wie warm ist es im Wohnzimmer
  [Haus]  wohnzimmer
  Im Wohnzimmer sind es 21,4 °C (Stand: 18:29 Uhr).

> welche lichter sind gerade an
  [Haus]  light
  Drei von neun: Küche, Flur und die Stehlampe im Wohnzimmer.
```

In der Weboberfläche geht dasselbe unter **Einstellungen → Zuhause & Netz**: „Suchen"
findet die Instanz, „Testen" prüft das Token und sagt dir, wie viele Geräte aquaticy
sieht.

**Schalten ist standardmäßig aus.** Ohne Haken sieht aquaticy nur nach. Mit Haken darf er
Licht, Steckdosen, Szenen und Medien bedienen — und selbst dann fragt er bei
**Schlössern, Alarmanlagen, Toren, Rollläden, Heizung und Saugrobotern** jedes Mal
nach, bevor er etwas tut. Ein missverstandener Halbsatz soll nicht die Haustür
aufschließen. Bereiche außerhalb der Liste (etwa `shell_command`) schaltet er
grundsätzlich nicht.

Das Token steht in deiner `.env` und wird nie an den Browser geschickt — die Oberfläche
erfährt nur, *ob* eines gesetzt ist. Zurücknehmen kannst du es jederzeit in Home
Assistant selbst: dasselbe Menü, Token löschen, fertig.

### Im Container: die Netzwerkkarte des Rechners

Läuft aquaticy im Container, hängt er in Dockers eigenem Brücken-Netz — von dort ist dein
Heimnetz **nicht** zu sehen, `aquaticy lan` und `connect-ha` fänden schlicht nichts.
Deshalb:

```bash
./aquaticy-box --lan                              # Wrapper, setzt es selbst
docker compose -f compose.yaml -f compose.host.yaml run --rm aquaticy
docker run --network host ...                   # ohne Compose
```

Merkt aquaticy, dass er im Container nur das Container-Netz sieht, sagt er es von sich aus,
statt dich rätseln zu lassen.

## Was sich nicht finden lässt

Das härteste Suchproblem ist nicht die große Frage, sondern die kleine: der
Fahrradladen in der Nebenstraße, die Werkstatt ohne Website, der Verein, dessen
Programm nur als PDF existiert. Suchmaschinen kennen sie nicht oder erst auf
Seite vier, weil niemand für sie optimiert. Dagegen hat Aquaticy drei Mittel.

**Die Karte.** `local_places` fragt **OpenStreetMap** statt einer Suchmaschine —
erst den Ort (Nominatim), dann die Umgebung (Overpass). Zurück kommen Name,
Adresse, Telefon, Öffnungszeiten und, wenn es eine gibt, die **Website**:
eingetragen von Leuten vor Ort, nicht von einer Marketingabteilung. Was dabei
herauskommt, liest Aquaticy danach ganz normal mit `fetch_page`. Für alles
Örtliche ist das der beste erste Griff, nicht der letzte.

Beide Dienste gehören der OpenStreetMap Foundation und sind gespendete
Rechenzeit, keine Selbstbedienung. Ihre Regeln sind eingebaut, nicht nur
kommentiert: **ein Aufruf pro Sekunde** dienstweit (ein Schloss über alle
Agenten — es hilft nichts, wenn jeder für sich höflich ist), **ehrlicher
User-Agent** aus den Einstellungen, **keine systematischen Abfragen** (immer
genau eine Umgebung zu einer Frage eines Menschen, nie ein Raster, nie eine
Liste aller Postleitzahlen), harte Grenzen bei Umkreis (max. 15 km), Trefferzahl
(30) und Zeit. Antwortet die Karte nicht, ist das kein Fehler, sondern ein
leeres Ergebnis mit Begründung — die Antwort entsteht dann eben aus dem Web.

**Die Profile.** `find_profiles` sucht zu einer Marke, Firma, Einrichtung oder
Person alles, was es **außerhalb der eigenen Website** gibt: Instagram, LinkedIn,
Facebook, X, YouTube, Wikipedia, TikTok, Trustpilot, kununu, Yelp, GitHub,
Reddit. Eine gewöhnliche Suche liefert die Website und danach zehn Portale — was
fehlt, ist genau das, was ein Mensch als Nächstes aufmacht. Dort steht oft
Aktuelleres als auf der Seite (Öffnungszeiten, Angebote, Neues), und mancher
Laden hat überhaupt nur ein Profil.

Gesucht wird über die Suchmaschine mit `site:` — **nicht** durch Durchprobieren
von Profiladressen. Das ist der Unterschied zwischen Recherche und Abgrasen: wir
fragen, was öffentlich indexiert ist, statt ein Verzeichnis von Profilnamen
abzuklappern. Was sich beim Lesen sperrt (Instagram und LinkedIn tun das oft),
bleibt beim Titel und dem Suchausschnitt; umgangen wird nichts. Acht Plattformen
je Aufruf, versetzt gestartet, sechs Stunden zwischengespeichert.

**Die Technik der Spurensuche.** Jeder Agent bekommt sie im Auftrag mit, und es
gibt eine eigene Rolle dafür („Spurensuche"), die der Master oder die
Wortwahl der Teilfrage auslöst:

* Suchoperatoren: der genaue Name in Anführungszeichen, `filetype:pdf` für
  Aushänge, Programme, Satzungen und Amtsblätter, `site:` für eine bestimmte
  Seite oder Endung.
* Verzeichnisse, die die Website nennen, die sonst nirgends auftaucht: Das
  Örtliche, Gelbe Seiten, 11880, meinestadt.de, Branchenbücher, das
  Vereinsregister, die Seite der Gemeinde, der Kreis, die Innung.
* Andere Worte: Ortsteil statt Stadt, Umgangssprache statt Fachwort, die alte
  Bezeichnung, Englisch statt Deutsch.
* Der Umweg über eine Nachbarseite: Impressum, Partner, Mitglieder, Presse.

**Der Master lässt nicht locker.** Kommt ein Agent mit leeren Händen zurück,
ist das im Pro-Modus kein Ende, sondern ein Nachauftrag mit anderer Technik
(siehe oben).

## Ortsfilter

Der Ortsfilter war lange eine **Bitte im Systemtext** („baue den Ort in die Suchanfragen
ein"). Das Hauptmodell hielt sich meistens daran — die Subagenten sahen ihn nie, denn für
sie ist ihre Teilfrage alles, was es gibt. Bei „Cafés mit WLAN" kamen dann Treffer aus dem
ganzen Sprachraum zurück. Jetzt passiert es **mechanisch**, an den Stellen, durch die jede
Suche geht:

* **Jede Suchanfrage** bekommt eine zusätzliche Fassung *mit* Ort, sofern nicht ohnehin
  schon einer darinsteht. Sie ersetzt die eigene Anfrage nicht, sie tritt daneben: beim
  Mischen (RRF) gewinnt, was mehrere Listen übereinstimmend oben haben — bei einer
  örtlichen Frage also das Örtliche, bei „Python sortieren" bleibt es beim Bisherigen.
  Gilt für den Hauptagenten, jeden Subagenten, jeden Prüfer und die Nachrichtensuche.
* **Jede Teilfrage** bekommt den Ort mit, bevor sie an einen Agenten geht.
* Aus `Bremen, Deutschland` oder `28195 Bremen` wird dabei `Bremen`: in eine Suchanfrage
  gehört der Name, nicht die Adresse. Steht der Ort schon da, wird nichts angehängt —
  „Bremen Bremen" sucht schlechter.

Dazu wie bisher die Länder- und Sprachparameter der Such-API, und Treffer, die
offensichtlich außerhalb liegen, sortiert der Agent aus. Vorgebbar per Flag oder
Slash-Befehl:

```bash
aquaticy --location "Mönchengladbach" --lang de
```
```
/location Köln
```

## Bild als Eingabe

```bash
aquaticy --image foto.jpg              # eine Datei
aquaticy --image ~/hallo1234           # ein Ordner -- aquaticy sucht das Bild darin
aquaticy --image ~/hallo1234 "wo kann ich das kaufen?"
```

Zeigt der Pfad auf einen **Ordner**, nimmt aquaticy das einzige Bild darin; sind es mehrere,
listet er sie auf (neueste zuerst) und fragt, welches gemeint ist.

Ein Vision-Modell beschreibt, was auf dem Bild zu sehen ist (Produkt, Logo, Schild,
Text), daraus werden Suchbegriffe — danach läuft die normale Recherche. Im Chat geht
dasselbe mit `/image pfad.jpg`.

Es wird nichts hochgeladen: aquaticy liest die Datei von deiner Platte. Zuständig ist
`AQUATICY_VISION_MODEL`; ist das leer, wird das Hauptmodell gefragt — und **Textmodelle
können keine Bilder sehen**. Ein lokales Vision-Modell richtest du so ein:

```bash
aquaticy install-model --vision-only
```

Welches Modell gerade zuständig ist, zeigt `aquaticy config` in der Zeile „Vision-Modell".

## Im Container laufen lassen

Wer aquaticy nicht direkt aufs System installieren will, lässt es in einem Container
laufen. Der isoliert das **Dateisystem**, nicht die Verbindung: das Netz bleibt
uneingeschränkt offen, sonst könnte der Agent nicht recherchieren.

```bash
./aquaticy-box --setup            # einmalig: fragt Modell und Key ab, schreibt ./.env
./aquaticy-box                    # Chat
./aquaticy-box "deine Frage"      # einmalige Recherche
./aquaticy-box search "test"      # nur die Suche, ohne LLM
```

Oder direkt mit Compose, ohne den Wrapper:

```bash
docker compose run --rm aquaticy                 # Chat
docker compose run --rm aquaticy "deine Frage"
docker compose build aquaticy                    # nach Codeänderungen
```

### Was der Container sieht — und was nicht

| | |
|---|---|
| **Netz** | vollständig offen, keine Einschränkung — nötig für Suche und Seitenabruf |
| **Dateisystem** | nur `/data` (Cache + Verlauf, Docker-Volume) und `/work` (→ `./exports`) |
| **Benutzer** | nicht `root`, sondern `aquaticy` (UID 1000) |
| **Rechte** | `no-new-privileges`, keine Zugriffe aufs Home-Verzeichnis des Hosts |
| **Keys** | kommen aus `./.env`, werden als Umgebungsvariablen hineingereicht |

Exporte (`/export html`) landen in `./exports` und sind damit direkt auf dem Host
lesbar. Cache und Verlauf überleben im Volume `aquaticy-data`.

### Zwei Varianten des Images

```bash
docker compose build aquaticy                                  # mit Chromium (Default)
AQUATICY_IMAGE_TARGET=slim docker compose build aquaticy         # ohne, ~700 MB kleiner
```

Das `browser`-Image bringt Chromium für den JavaScript-Fallback (Stufe 3) mit. Darin
läuft Chromium ohne seine eigene Sandbox (`AQUATICY_BROWSER_NO_SANDBOX=1`) — die Isolation
übernimmt der Container. Bei einer normalen Installation aufs System bleibt die
Browser-Sandbox aktiv.

### Alles im Haus: mit eigener Suchmaschine

Zusammen mit SearXNG geht auch die Suche über keinen fremden Dienst mehr:

```bash
docker compose --profile searxng up -d searxng
echo 'AQUATICY_SEARCH_BACKEND=searxng' >> .env
docker compose --profile searxng run --rm aquaticy
```

Die mitgelieferte `docker/searxng/settings.yml` hat die JSON-Ausgabe bereits aktiviert.
Ersetze darin vor dem ersten Start den `secret_key` durch etwas Eigenes
(`openssl rand -hex 32`).

## Suchmaschine — ohne API-Key

Die Suche kostet nichts und braucht **kein Konto**. Standard ist eine offene Metasuche:
`aquaticy` fragt über [`ddgs`](https://pypi.org/project/ddgs/) mehrere freie Suchmaschinen
per HTML ab und mischt die Treffer. Fällt eine aus (Rate-Limit, Umbau), übernehmen die
anderen — genau deshalb ist die Metasuche robuster als eine einzelne Engine.

### Wie gesucht wird: mehrere Formulierungen statt einer

Eine einzige Formulierung findet nur, was zufällig genau so im Netz steht. Aquaticy AI
stellt dieselbe Frage deshalb mehrfach anders und führt die Trefferlisten zusammen —
in der Fachsprache *query fan-out* mit *Reciprocal Rank Fusion*. Drei Regeln aus der
Literatur stecken darin:

1. **Kurze Stichwortanfragen schlagen ganze Sätze.** Aus „Wie viel kostet ein
   gebrauchtes Lastenrad in Bremen?" wird zusätzlich „kostet gebrauchtes Lastenrad
   Bremen". Drei bis sechs inhaltstragende Wörter, das Hauptthema in jeder Variante.
2. **Zwei bis drei Formulierungen, nicht mehr.** Ab der vierten nehmen die Treffer
   nicht mehr zu, nur noch die Streuung. `AQUATICY_SEARCH_VARIANTS=1` schaltet es ab.
3. **Gemischt wird über die Plätze, nicht über Punktzahlen.** Jeder Treffer bekommt je
   Liste `1/(60+Platz)` gutgeschrieben; was mehrere Anfragen übereinstimmend weit oben
   haben, steht am Ende vorn. Punktzahlen verschiedener Engines lassen sich nicht
   vergleichen, Plätze schon.

Dazu kommt eine Grenze von zwei Treffern je Domain, damit nicht ein Portal die ganze
erste Seite belegt — reicht es dann nicht, wird von hinten aufgefüllt.

Das Modell kann in **einem** Werkzeugaufruf mehrere eigene Formulierungen mitgeben
(Feld `queries`). Das kostet ein Budget statt drei, und die Ergebnisse landen in
derselben zusammengeführten Liste. Die Anfragen laufen parallel, aber um
Sekundenbruchteile versetzt: gleichzeitig abgefeuert quittieren die offenen Engines
das gern mit einem Rate-Limit.

Verfügbar ohne Key: `duckduckgo`, `mojeek`, `startpage`, `brave`, `yahoo`, `wikipedia`.

```bash
# alle offenen Engines (Default, nichts zu tun)
AQUATICY_SEARCH_BACKEND=duckduckgo

# gezielt einschränken, wenn eine Engine bei dir zickt
AQUATICY_SEARCH_ENGINES=duckduckgo,mojeek
```

### Eigene Instanz: SearXNG

Wer nichts von fremden Suchmaschinen abhängen will, hostet
[SearXNG](https://docs.searxng.org/) selbst — freie Software, kein Key, keine
Ratenbegrenzung von außen:

```bash
docker run -d -p 8080:8080 searxng/searxng
```

In der `settings.yml` der Instanz muss unter `search.formats` der Eintrag `json` stehen
(sonst antwortet sie mit HTML oder 403). Dann:

```bash
AQUATICY_SEARCH_BACKEND=searxng
AQUATICY_SEARXNG_URL=http://localhost:8080
```

Öffentliche SearXNG-Instanzen funktionieren auch, haben die JSON-Ausgabe aber oft
abgeschaltet.

### Kommerzielle APIs (optional)

[Brave Search](https://brave.com/search/api/) und [Tavily](https://app.tavily.com/home)
sind eingebaut, brauchen aber einen Key. Nur sinnvoll, wenn dir die offenen Engines nicht
zuverlässig genug sind:

```bash
AQUATICY_SEARCH_BACKEND=brave
BRAVE_API_KEY=...
```

Testen lässt sich jedes Backend ohne LLM:

```bash
aquaticy search "cafés mönchengladbach" -n 5
```

## Tempo

Zwei Dinge bestimmen, wie lange eine Antwort dauert: wie oft Aquaticy das Modell fragen
muss, und wie lange das Modell je Frage braucht. Das zweite gehört dem Anbieter — ein
Modell mit 550 Milliarden Parametern antwortet nun einmal langsamer als ein kleines auf
der eigenen Grafikkarte. Am ersten lässt sich etwas machen, und das ist hier gemacht:

* **Mehrere Seiten pro Runde werden gleichzeitig gelesen.** Das Modell wird
  ausdrücklich aufgefordert, in einem Zug mehrere Quellen anzufordern. Nacheinander
  abgearbeitet summiert sich das — vier Seiten à zwei Sekunden sind acht Sekunden, in
  denen nichts anderes passiert. Gemessen an vier Seiten: **6,0 s vorher, 1,5 s jetzt.**
  Das gilt für den Hauptagenten wie für jeden Subagenten.
* **Nur lesende Werkzeuge laufen parallel.** Eine Rückfrage wartet auf einen Menschen,
  eine Notiz und ein Schaltbefehl verändern etwas — so etwas läuft weiter nacheinander
  und in der Reihenfolge, die das Modell gewählt hat.
* **Kein Denk-Modus für die Planung.** Vor jeder Anfrage entscheidet ein kurzer Aufruf,
  ob überhaupt recherchiert werden muss und wie die Teilfragen lauten. Ein
  Denk-Modell überlegt dafür sekundenlang, bevor drei Stichworte kommen. Bei lokalen
  Modellen war das schon abgeschaltet, jetzt auch in der Cloud — dort kostet es am
  meisten.
* **Die Vorrecherche ist der größte Posten — und sie läuft nur noch auf Wunsch.**
  Sie zerlegt die Frage und lässt die Teile parallel recherchieren; das ist
  gründlicher, verdoppelt aber die Wartezeit. Sie hängt jetzt am Schalter *Denken*:
  aus heißt Gespräch, an heißt Recherche. Dauerhaft abschalten geht weiterhin unter
  *Subagenten* oder mit `AQUATICY_SUBAGENTS_AUTO=false`.
* **Der Denkaufwand richtet sich nach der Aufgabe.** Ein Gespräch bekommt
  `reasoning_effort=low`, eine Recherche `medium`, der Code-Modus `high` — dort
  kostet ein Fehler am meisten, weil er erst beim Ausführen auffällt. Anbieter, die
  den Wunsch nicht kennen, lassen ihn dank `drop_params` einfach weg.
* **Der Systemtext bleibt stehen, solange sich die Lage nicht ändert.** Er steht am
  Anfang jeder Anfrage; wer ihn bei jeder Frage neu schreibt, wirft den beim Anbieter
  zwischengespeicherten Prefix weg und zahlt ihn noch einmal — an Geld und an Zeit.

Bleibt es zäh, liegt es am Modell, nicht am Weg dorthin: ein kleineres Modell desselben
Anbieters oder ein lokales über `aquaticy install-model` ist dann der wirksamste Hebel.

## Stabilität

Lokale Modelle scheitern anders als Cloud-Modelle. aquaticy fängt die drei häufigsten Fälle
ab:

* **Kontextüberlauf — die häufigste Ursache für „er vergisst die letzte Frage".**
  Läuft das Fenster über, wirft der Anbieter *still* den **Anfang** weg: erst den
  Systemprompt, dann die früheren Fragen. Das Gespräch wirkt dann wie zurückgesetzt.
  aquaticy beugt zweifach vor:

  1. **Ein ausreichend großes Fenster anfordern.** Ollama nimmt sonst seinen Default von
     2048–4096 Token — nach einer recherchierten Antwort ist der schon voll. aquaticy
     schickt `num_ctx` mit (`AQUATICY_CONTEXT_TOKENS`, Default 16384). Bei Cloud-Anbietern
     entfällt das, die kennen den Parameter nicht.
  2. **Selbst kürzen statt gekürzt werden — und zwar in der richtigen Reihenfolge.**
     Geopfert wird von hinten nach vorn nach Wert: zuerst ältere Werkzeug-Ausgaben →
     Platzhalter (`AQUATICY_KEEP_FULL_RESULTS`, Default 4), dann ältere Vorrecherche-Blöcke
     (die wiederholten sich sonst jeden Turn), dann die verbliebenen Suchergebnisse, dann
     ältere Antworten — und **erst ganz zuletzt die Fragen des Nutzers**. Ein Suchergebnis
     von vorletzter Runde ist ersetzbar, deine Frage nicht: die steht nirgendwo sonst.
     Systemprompt und aktuelle Frage bleiben immer stehen, und Werkzeugaufrufe werden nie
     von ihren Antworten getrennt.
  3. **Kein einzelner Brocken darf das Fenster auffressen.** Ein Suchergebnis oder eine
     angehängte Datei bekommt höchstens gut ein Drittel des Budgets — sonst passt bei
     einem kleinen Fenster schon ein einziges Ergebnis samt Systemprompt nicht mehr hinein,
     und dem Kürzen bliebe nur noch das Gespräch selbst. Obergrenze ist zusätzlich
     `AQUATICY_MAX_TOOL_CHARS` (Default 8000).

  Passt dein Modell mehr, dreh auf — `gemma4:12b` kann 128k, kostet aber VRAM:

  ```bash
  AQUATICY_CONTEXT_TOKENS=32768
  ```
* **Abgestürzter Runner.** Bei `model runner has unexpectedly stopped` entlädt aquaticy alle
  Modelle und versucht es erneut, statt den Durchlauf zu verlieren.
* **Wackelige Verbindung.** Timeouts, 502/503 und Rate-Limits werden bis zu
  `AQUATICY_LLM_RETRIES` mal wiederholt (Default 3, mit wachsender Wartezeit). Ein falscher
  API-Key wird *nicht* wiederholt — das würde nur Zeit kosten.

Dazu: Ein Werkzeug, das eine Ausnahme wirft, beendet den Durchlauf nicht mehr, sondern
meldet den Fehler an das Modell, das dann eine andere Quelle nimmt.

## Verhalten beim Seitenabruf

* `robots.txt` wird respektiert (einmal je Origin geholt und zwischengespeichert)
* ehrlicher User-Agent, der aquaticy benennt
* maximal 1 Request pro Sekunde und Domain, Timeout 15 s
* bei Fehler oder Blockade: überspringen und mit dem nächsten Treffer weitermachen,
  nicht abbrechen
* kein Umgehen von Logins, Paywalls oder Captchas — ist eine Seite nicht öffentlich
  lesbar, wird sie ausgelassen und im Ergebnis als „nicht öffentlich zugänglich" vermerkt

## Cookie-Banner und Pop-ups

Der Punkt, an dem die meisten simplen Crawler scheitern: Statt des Seiteninhalts wird der
Text des Cookie-Dialogs extrahiert. aquaticy löst das in drei Stufen.

**Stufe 1 — gar nicht erst hinklicken (Standardfall).** `fetch_page` holt reines HTML
ohne JavaScript-Ausführung. Consent-Banner sind dann meist nur inaktive DOM-Knoten oder
werden gar nicht erst eingebaut. Vor der Textextraktion fliegen sie per Selektor-Blockliste
raus (`aquaticy/selectors.yaml`, ohne Codeänderung erweiterbar), danach übernimmt
trafilatura die restliche Boilerplate-Entfernung. Absätze, die im Wesentlichen aus
Consent-Formulierungen bestehen, werden zusätzlich aus dem Text gestrichen.

**Stufe 2 — erkennen, ob es geklappt hat.** Bleiben nach der Extraktion weniger als
~200 Zeichen übrig und stehen typische Consent-Marker im HTML, gilt der Abruf als
gescheitert. Unterschieden wird zwischen `blocked`, `consent_required`, `paywall` und
`empty` — jeder Grund wird dem Agenten gemeldet, damit er eine andere Quelle nimmt.

**Stufe 3 — Playwright-Fallback.** Optionale Abhängigkeit, nur für Seiten, die ohne
JavaScript nichts liefern:

```bash
uv tool install --with playwright aquaticy
aquaticy install-browser
```

Dort wird auf Netzruhe gewartet, dann die **Ablehnen**-Schaltfläche der bekannten
Consent-Management-Plattformen geklickt (OneTrust, Cookiebot, Usercentrics, Didomi,
Sourcepoint/Quantcast auch im iFrame), sonst greift ein generischer Fallback über den
Buttontext (`ablehnen|nur notwendig|reject|decline|necessary only`). Gibt es keinen
Ablehnen-Button, wird **nicht** auf „Alle akzeptieren" geklickt: Stattdessen werden die
Overlay-Knoten per JavaScript aus dem DOM entfernt und die Scroll-Sperre gelöst — der
Inhalt liegt fast immer schon im DOM. Newsletter-Layer, App-Install-Banner und
Push-Abfragen werden nur entfernt, nie angeklickt.

### Datenschutz-Voreinstellungen

* immer die datensparsamste Option: ablehnen statt akzeptieren
* pro Seitenabruf ein frischer Browser-Kontext, keine Cookies über Aufrufe hinweg
* Cookie-Jar der HTTP-Abrufe bleibt nur im Speicher, nichts landet auf der Platte
* Browser-Berechtigungen (Notifications, Geolocation) werden generell verweigert
* keine Anmeldung, keine Formulare, keine gespeicherten Zugangsdaten

### Harte Grenze

Paywalls, Login-Wände, Captchas und Altersverifikationen werden **nicht** umgangen — auch
nicht durch Reader-Modi, AMP-Tricks oder Cache-Kopien. Liegt der Inhalt dahinter,
überspringt der Agent die Seite und vermerkt „nicht öffentlich zugänglich". Das ist ein
bewusster Unterschied: Ein Cookie-Overlay verdeckt frei zugänglichen Inhalt, eine Paywall
schützt ihn. Auch eine echte Consent-Wall („Zustimmen oder Abo") ist eine Zugangssperre —
sie wird als `skipped: consent_required` protokolliert, nicht mit gefälschten
Consent-Cookies, Header-Tricks oder Captcha-Lösern angegangen. Es gibt fast immer eine
zweite Quelle für dieselbe Information.

## Produktdaten: Bilder und technische Daten

Zielt die Anfrage auf ein Produkt, reicht Fließtext nicht. `fetch_page` zieht zusätzlich
strukturierte Produktdaten aus der Seite, in dieser Reihenfolge:

1. **JSON-LD** (`<script type="application/ld+json">` mit `@type: Product`) — Name,
   Bild-URL, Preis, Marke, Bewertung, GTIN. Der zuverlässigste Weg.
2. **Open-Graph-Tags** — `og:image`, `og:title`, `product:price:amount`
3. **Microdata** — `itemprop="image"`, `itemprop="price"`
4. **Spec-Tabellen** — `<table>` und Definitionslisten unter „Technische Daten" /
   „Specifications", als Key-Value-Paare geparst
5. **LLM-Fallback** — greift nichts davon, geht der Seitentext ans LLM mit der Bitte, die
   Specs als JSON zu extrahieren (nur bei Produktverdacht, nicht auf jeder Seite)

```python
class Product(BaseModel):
    name: str
    url: str
    image_url: str | None          # absolut, relative URLs werden aufgelöst
    price: str | None
    currency: str | None
    rating: float | None
    specs: dict[str, str]          # {"CPU": "Ryzen 7 7840U", "RAM": "32 GB", ...}
    availability: str | None
    source_domain: str
```

**Zu Amazon & Co.:** Amazon, Zalando und ähnliche Plattformen blocken einfache
HTTP-Abrufe aggressiv (403, Captcha-Seite). aquaticy baut dafür **keine Umgehung**, sondern
erkennt Blockade-Antworten und markiert den Treffer als `blocked`. Der Agent weicht dann
auf frei lesbare Quellen aus — Herstellerseiten, Testberichte (Notebookcheck, Heise,
Chip), Preisvergleiche (Geizhals) und kleinere Shops, die für Specs ohnehin die besseren
Daten liefern. Der Amazon-Treffer bleibt als Kauf-Link mit Titel und Snippet aus der Suche
erhalten, nur eben ohne Seitenabruf.

**Vergleich mehrerer Produkte:** Bei einer Kaufempfehlung sammelt der Agent 3–6 Kandidaten
und gibt sie als Vergleichstabelle aus — gleiche Spec-Zeilen bei allen, damit man sie
nebeneinander lesen kann. Fehlende Werte als „–", nicht geraten.

## Ausgabe von Bildern

* **Im Terminal:** direkt über [`term-image`](https://pypi.org/project/term-image/) oder
  `chafa`, sofern das Terminal es unterstützt (kitty, iTerm2, WezTerm). Fällt automatisch
  auf die reine URL zurück, wenn nicht.
* **`/export html`** erzeugt eine eigenständige HTML-Datei mit Produktbildern,
  Specs-Tabelle und Links — der praktischste Weg, ein Rechercheergebnis anzusehen und
  aufzubewahren.
* **`/export md`** schreibt Markdown mit Bild-Links, **`/export csv`** eine Zeile je
  Produkt.
* Bilder werden verlinkt, nicht heruntergeladen — außer bei `--download-images`, dann
  landen sie in einem Unterordner neben der Exportdatei.

## Konfiguration

Alle Werte kommen aus der `.env` (siehe [`.env.example`](.env.example)):

| Variable | Bedeutung | Default |
|---|---|---|
| `AQUATICY_MODEL` | LiteLLM-Modell-ID | `mistral/mistral-large-latest` |
| `AQUATICY_VISION_MODEL` | Modell für `--image` | wie `AQUATICY_MODEL` |
| `AQUATICY_API_BASE` | eigene Basis-URL (Ollama, eigene NIM, Proxy) | — |
| `AQUATICY_API_KEY` | Key für Anbieter ohne eigenen Eintrag | — |
| `AQUATICY_SEARCH_BACKEND` | `duckduckgo`, `searxng`, `brave`, `tavily` | `duckduckgo` |
| `AQUATICY_SEARCH_ENGINES` | Engines der Metasuche einschränken | alle |
| `AQUATICY_SEARCH_VARIANTS` | Formulierungen je Suche (`1` = aus) | `3` |
| `AQUATICY_SEARXNG_URL` | Adresse der SearXNG-Instanz | — |
| `AQUATICY_LOCATION` | Standard-Ortsfilter | — |
| `AQUATICY_LANG` / `AQUATICY_COUNTRY` | Sprache / Land der Suche | `de` / `de` |
| `AQUATICY_MAX_TOOL_CALLS` | Werkzeug-Budget je Anfrage | `20` |
| `AQUATICY_MAX_SUBAGENTS` | Fallback für Terminal/alte Clients; im Web steht der Regler bei der Modellauswahl (Normal/Code 1–12, Pro 1–50) | `12` |
| `AQUATICY_SUBAGENTS_AUTO` | jede Anfrage automatisch zerlegen | `true` |
| `AQUATICY_TRIAGE_TIMEOUT` | Zeitlimit der Small-Talk-Heuristik (s) | `5` |
| `AQUATICY_PLANNER_TIMEOUT` | Zeitlimit für Prüfung + Planung (s) | `20` |
| `AQUATICY_CONTEXT_TOKENS` | Kontextfenster für lokale Modelle (`0` = Ollama-Default) | `16384` |
| `AQUATICY_SUBAGENT_MODEL` | leichtes Modell für die Subagenten | das schnelle kleine des Anbieters |
| `AQUATICY_SUBAGENT_BUDGET` | Werkzeug-Budget je Subagent | `6` |
| `AQUATICY_SUBAGENT_PARALLEL` | gleichzeitige Subagenten (`0` = automatisch) | lokal `2`, Cloud: alle |
| `AQUATICY_RPM` | Anfragen je Minute an den Anbieter | NVIDIA `40`, Mistral `240` |
| `AQUATICY_PARALLEL_CALLS` | gleichzeitig offene Anfragen | NVIDIA `4`, Mistral `8` |
| `AQUATICY_LLM_RETRIES` | Versuche bei transienten Fehlern | `3` |
| `AQUATICY_MAX_TOOL_CHARS` | Zeichen je Werkzeug-Ergebnis | `8000` |
| `AQUATICY_KEEP_FULL_RESULTS` | ungekürzte Ergebnisse im Verlauf | `4` |
| `AQUATICY_FETCH_TIMEOUT` | Timeout je Seitenabruf (s) | `15` |
| `AQUATICY_CACHE_TTL_HOURS` | Gültigkeit des Response-Cache | `24` |
| `AQUATICY_ENABLE_PLAYWRIGHT` | Stufe-3-Fallback erlauben | `true` |
| `AQUATICY_VM_SIZE` | Größe der Werkstatt: `normal` oder `plus` (Plus nur mit Pro-Konto) | `normal` |
| `AQUATICY_VM_IMAGE` | Abbild für die Werkstatt (z. B. mit Blender) | `python:3.12-slim` |
| `AQUATICY_VM_IDLE_MINUTES` | Werkstatt löschen nach so vielen Minuten Ruhe | `20` |
| `AQUATICY_VM_CPUS` / `_MEMORY_MB` / `_DISK_GB` | Grenzen von Hand statt der Größe | aus `AQUATICY_VM_SIZE` |
| `AQUATICY_STORAGE_URL` | Adresse der Lagerverwaltung im Netz | — |
| `AQUATICY_STORAGE_ACCESS` | `off`, `read` oder `write` | `read` |
| `AQUATICY_GOOGLE` | Gmail und Kalender lesen dürfen | `false` |
| `AQUATICY_GOOGLE_WRITE` | Termine anlegen/ändern und Entwürfe schreiben dürfen (nie verschicken) | `false` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | eigene Google-Anwendung | — |

### Ganz ohne API-Key: lokales Modell

Ein Befehl, und aquaticy richtet sich ein Modell auf deinem Rechner ein:

```bash
aquaticy install-model
```

Der Befehl macht der Reihe nach:

1. **Ollama suchen** — fehlt es, zeigt er den Installationsbefehl und fragt nach. Es
   läuft nichts ungefragt.
2. **Server starten**, falls er nicht schon läuft
3. **Modell auswählen** — er schaut nach freiem Arbeitsspeicher und GPU und schlägt das
   größte Modell vor, das passt
4. **Modell laden** (`ollama pull`, mit Fortschritt)
5. **Tool-Calling an einem echten Aufruf prüfen** — und das ist der Punkt: Ein Modell
   ohne Werkzeugaufrufe würde aus dem Gedächtnis antworten statt aus dem Web. Besteht es
   den Test nicht, wird es nicht eingetragen.

Danach steht in der `.env`:

```bash
AQUATICY_MODEL=ollama_chat/qwen2.5:7b
AQUATICY_API_BASE=http://localhost:11434
```

Zum Schluss fragt er, ob du **auch Bilder** als Eingabe nutzen willst, und richtet dafür
ein Vision-Modell ein — mit eigenem Sehtest: Das Modell bekommt ein rotes Quadrat gezeigt
und muss die Farbe nennen. Ein Textmodell fällt dabei durch und wird nicht eingetragen.

Ein bestimmtes Modell direkt:

```bash
aquaticy install-model --model qwen2.5:14b                  # nur Text
aquaticy install-model --vision-model llava:7b              # Text + Bild
aquaticy install-model --vision-only --vision-model llava:7b  # nur Bild nachrüsten
aquaticy install-model --model qwen2.5:7b --no-vision       # ohne Bild
```

Mit `--yes` läuft alles ohne Rückfragen — ein Vision-Modell wird dann nur geladen, wenn
du es mit `--vision-model` benennst. Mehrere Gigabyte ungefragt herunterzuladen wäre
nicht in Ordnung.

**Ein Modell für alles** (Recherche *und* Bilder) — die sparsamste Variante, weil nur ein
Modell im Speicher liegt. Stand August 2026:

| Modell | ca. Größe | ab VRAM | kann |
|---|---|---|---|
| `qwen3-vl:4b` | 3,3 GB | 6 GB | Suche + Bilder |
| `gemma4:e4b` | 3,5 GB | 6 GB | Suche + Bilder |
| `qwen3-vl:8b` | 6,1 GB | 12 GB | Suche + Bilder |
| `gemma4:12b` | 6,6 GB | 10 GB | Suche + Bilder |
| `gemma4:26b` | 16,0 GB | 24 GB | Suche + Bilder |

**Nur Recherche** (stärker im Suchen, sehen aber nichts):

| Modell | ca. Größe | ab VRAM |
|---|---|---|
| `qwen2.5:3b` | 1,9 GB | 4 GB |
| `qwen2.5:7b` | 4,7 GB | 8 GB |
| `llama3.1:8b` | 4,9 GB | 8 GB |
| `qwen3:8b` | 5,2 GB | 8 GB |
| `qwen2.5:14b` | 9,0 GB | 16 GB |

**Nur Bilder** (brauchen kein Tool-Calling, beschreiben nur):

| Modell | ca. Größe | ab VRAM |
|---|---|---|
| `moondream` | 1,7 GB | 3 GB |
| `gemma3:4b` | 3,3 GB | 6 GB |
| `qwen3-vl:4b` | 3,3 GB | 6 GB |
| `llava:7b` | 4,7 GB | 8 GB |
| `minicpm-v` | 5,5 GB | 8 GB |
| `gemma3:12b` | 8,1 GB | 12 GB |

> **Namensfalle:** Das offizielle `gemma3` kann in Ollama Bilder ansehen, aber **keine
> Werkzeuge aufrufen** — als Hauptmodell ist es damit unbrauchbar, aquaticy führt es
> deshalb nur bei den Vision-Modellen. Erst `gemma4` bringt beides mit.

aquaticy liest den VRAM per `nvidia-smi` aus und schlägt danach vor — ohne GPU rechnet er
mit 70 % des Arbeitsspeichers, weil auf der CPU nicht alles nutzbar ist. Passt ein
Modell, das beides kann, wird kein zweites geladen.

**Die Hardware empfiehlt, sie entscheidet nicht.** Die Liste zeigt immer alle Modelle,
und du kannst jedes davon wählen — oder einen beliebigen Namen aus dem Ollama-Katalog
eintippen. Passt eines rechnerisch nicht, sagt aquaticy das als Hinweis und lädt es
trotzdem: Ein Modell läuft notfalls teilweise auf der CPU, das ist langsam, aber deine
Entscheidung.

Jedes andere Ollama-Modell mit Werkzeug-Unterstützung geht auch — `--model` nimmt jeden
Namen aus dem [Ollama-Katalog](https://ollama.com/library).

> **Das Präfix muss `ollama_chat/` lauten, nicht `ollama/`.** Nur ersteres reicht
> Werkzeuge durch; mit `ollama/` bleibt der Agent stumm. `aquaticy install-model` schreibt
> automatisch das richtige.

#### Wenn der Speicher knapp wird

Text- und Vision-Modell gleichzeitig im VRAM sprengen viele Grafikkarten — der
Ollama-Runner stirbt dann mit `model runner has unexpectedly stopped`. aquaticy entlädt
deshalb vor jedem Test alle laufenden Modelle und erkennt diesen Absturz als das, was er
ist: ein Speicherproblem, kein Urteil über das Modell. Er bietet dann automatisch ein
kleineres an.

Hilft das nicht:

```bash
ollama ps                       # was liegt gerade im Speicher?
ollama stop <modell>            # von Hand entladen
OLLAMA_KEEP_ALIVE=0 ollama serve   # Modelle sofort nach jedem Aufruf entladen
```

Als Faustregel: Text- und Vision-Modell zusammen sollten unter deinem VRAM bleiben. Mit
12 GB passen z. B. `qwen2.5:7b` und `moondream` gut nebeneinander.

Zusammen mit SearXNG (siehe unten) läuft dann alles auf deinem Rechner — kein einziger
Aufruf geht noch an einen fremden Dienst.

### LLM-Anbieter

Da LiteLLM als LLM-Schicht dient, ist der Anbieter austauschbar:

```bash
aquaticy --model mistral/mistral-large-latest             # Mistral
aquaticy --model nvidia_nim/meta/llama-3.3-70b-instruct   # NVIDIA NIM
aquaticy --model ollama_chat/qwen2.5:7b                   # lokal, siehe oben
```

Aquaticy ist auf **zwei** Anbieter eingerichtet: [Mistral](https://console.mistral.ai/api-keys/)
und [NVIDIA NIM](https://build.nvidia.com/). Das ist eine Entscheidung, keine
Sparmaßnahme. Eine Liste mit dreizehn Anbietern sieht großzügig aus, bedeutet aber
dreizehnmal „irgendein Standardmodell, ungetestet, mit unbekannten Grenzen". Zwei
Anbieter kann man kennen — und danach richtet sich Aquaticy dann auch: welches Modell
wofür, wie schnell es antwortet, wie viele Anfragen pro Minute es verträgt.

| Anbieter | Modell-Präfix | Key | Freikontingent |
|---|---|---|---|
| [Mistral](https://console.mistral.ai/api-keys/) | `mistral/` | `MISTRAL_API_KEY` | Server in der EU, antwortet schnell |
| [NVIDIA NIM](https://build.nvidia.com/) | `nvidia_nim/` | `NVIDIA_NIM_API_KEY` | offene Modelle, 40 Anfragen/Minute |
| Ollama (lokal) | `ollama_chat/` | — | kostet nichts, verlässt den Rechner nicht |

Je Anbieter kennt Aquaticy **drei Rollen**, und wählt selbst die passende:

| Rolle | Wofür | Mistral | NVIDIA NIM |
|---|---|---|---|
| Arbeitspferd | Recherche, Lesen, Zusammenfassen, der Master im Pro-Modus | `mistral-large-latest` | `meta/llama-3.3-70b-instruct` |
| das schnelle kleine | Planung, Vorprüfung und die vielen Rechercheagenten | `mistral-small-latest` | `meta/llama-3.1-8b-instruct` |
| fürs Programmieren | Code-Modus | `codestral-latest` | `qwen/qwen2.5-coder-32b-instruct` |

Ein 70B-Modell für „such mir die Öffnungszeiten" kostet Sekunden, und die summieren
sich mit jedem der vierundvierzig Agenten — deshalb laufen die Agenten auf dem kleinen
Modell und nur die Antwort auf dem großen. Wer es anders will, trägt unter
`AQUATICY_SUBAGENT_MODEL` bzw. `AQUATICY_CODE_MODEL` sein eigenes ein.

#### Tempo: Takt halten statt gegen die Wand laufen

NVIDIA erlaubt im Freikontingent **40 Anfragen pro Minute**. Ohne Bremse passiert
Folgendes: die ersten vierzig kommen durch, alles Weitere bekommt ein 429 zurück, jede
abgelehnte Anfrage wird wiederholt, die Wiederholungen laufen wieder in dieselbe Grenze
— und aus einer Recherche werden Minuten, in denen sichtbar nichts passiert.

Aquaticy hält das Maß deshalb selbst ein (`aquaticy/pace.py`): zwischen zwei Aufrufen an
denselben Anbieter liegen mindestens `60 / rpm` Sekunden, und mehr als eine Handvoll
Anfragen sind nie gleichzeitig offen. Prozessweit, denn die Grenze gilt für den
Schlüssel, nicht für den einzelnen Agenten. Lokale Modelle und selbst eingetragene
Anbieter werden nicht gebremst.

| Variable | Bedeutung | Default |
|---|---|---|
| `AQUATICY_RPM` | Anfragen pro Minute, für alle Anbieter | NVIDIA 40, Mistral 240 |
| `AQUATICY_PARALLEL_CALLS` | gleichzeitig offene Anfragen | NVIDIA 4, Mistral 8 |

Wer einen größeren Vertrag hat, hebt beides an.

**Wichtig: Das Modell muss Tool-Calling (Function Calling) beherrschen.** Ohne das kann
der Agent weder suchen noch Seiten lesen — er antwortet dann aus dem Gedächtnis statt aus
dem Web, was genau das ist, was aquaticy vermeiden soll.

Jeden weiteren LiteLLM-Anbieter nutzt du über den Notausgang `AQUATICY_API_KEY` — die
Rollen und Grenzen oben gelten dann nicht, Aquaticy kennt sie für ein fremdes Modell ja
nicht:

```bash
AQUATICY_MODEL=irgendein_anbieter/modell
AQUATICY_API_KEY=dein-key
```

#### NVIDIA NIM im Detail

[build.nvidia.com](https://build.nvidia.com/) gibt dir nach der Anmeldung Startguthaben
und einen Key (`nvapi-...`). Modell auswählen, „Get API Key" klicken, dann:

```bash
AQUATICY_MODEL=nvidia_nim/meta/llama-3.3-70b-instruct
NVIDIA_NIM_API_KEY=nvapi-...
```

Die Modell-ID ist genau die von build.nvidia.com, mit `nvidia_nim/` davor. Vergisst du
das Kürzel, ergänzt aquaticy es selbst — kopierst du `nvidia/nemotron-3-ultra-550b-a55b`
von der Seite, wird daraus beim Speichern `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b`.
Am schnellsten geht es in der Weboberfläche: *Einstellungen → Modell → Anbieter: NVIDIA
NIM*, Schlüssel einfügen, **Modell speichern**. Mehr braucht es nicht.

Achte darauf, ein Modell zu wählen, das in der Modellkarte Tool-Calling aufführt — nicht
alle dort angebotenen Modelle können das. Eine eigene, selbst gehostete NIM-Instanz
erreichst du über `AQUATICY_API_BASE=http://dein-host:8000/v1`.

### Wenn nach dem Anbieterwechsel „404 page not found" kommt

Der Klassiker: In der `.env` steht noch `AQUATICY_API_BASE=http://localhost:11434` vom
lokalen Modell, das Modell zeigt aber längst zu NVIDIA oder Mistral. Die Anfrage geht
dann an Ollama statt an den Anbieter, und Ollama antwortet mit genau diesem Satz.

aquaticy lässt eine Basis-URL auf dem Ollama-Port (11434) deshalb weg, sobald das Modell
zu einem anderen Anbieter gehört. Jede andere Adresse bleibt stehen — ein LiteLLM-Proxy
oder ein eigenes NIM im Heimnetz ist ein völlig legitimer Weg zu einem Cloud-Modell und
wird nicht angefasst.

SQLite hält Konten und Sitzungen unter `~/.aquaticy/`. Jeder Nutzer bekommt unter
`~/.aquaticy/users/` ein eigenes Verzeichnis für Verlauf, Merkzettel, verschlüsselten
Speicher, Aufträge, Token-Zähler und Einstellungen.

## Konten und Pro

Beim ersten Start erzeugt Aquaticy einen geheimen, neunstelligen Pro-Code und zeigt ihn
im Terminal. Später zeigt `aquaticy pro-code` denselben Code erneut. Alternativ setzt du
ihn vor dem Start mit `AQUATICY_PRO_CODE`.

Normale Konten können recherchieren, chatten und ihre eigenen Einstellungen und Daten
nutzen. Nach insgesamt 400.000 Token nehmen sie keine weiteren Modellanfragen an. Der
Zähler lässt sich nicht zurücksetzen. Pro-Konten haben kein Tokenlimit und können
zusätzlich die LAN-Suche, Home Assistant und die Lagerverwaltung verwenden.

```bash
aquaticy list       # E-Mail, Kontotyp, Token- und Speicherverbrauch
aquaticy pro-code   # geheimen Pro-Code anzeigen
```

Passwörter werden mit scrypt und einem eigenen Salz gehasht. Sitzungen liegen in
HttpOnly-Cookies; IP-Adresse und Browsermerkmale speichert Aquaticy nur als Hash für die
Sitzungsprüfung. Es gibt keine Werbe- oder Analyse-Cookies.

Vor der Registrierung zeigt die Web-App verständlich, welche notwendigen Cookies und Daten
sie verwendet. Datenschutz, Cookie-Richtlinie, Nutzungsbedingungen und Hinweise zur
Barrierefreiheit sind schon vor der Anmeldung erreichbar. Die Zustimmung zu Datenschutz und
Nutzungsbedingungen wird bei der Registrierung zusätzlich auf dem Server geprüft und mit der
geltenden Textfassung gespeichert.

Da Aquaticy selbst gehostet wird, muss der jeweilige Serverbetreiber seine echten Kontaktdaten
angeben. Dafür stehen diese Variablen in der serverweiten `.env`; normale Webkonten können sie
nicht ändern:

```dotenv
AQUATICY_OPERATOR_NAME=Name oder Organisation
AQUATICY_OPERATOR_EMAIL=kontakt@example.org
AQUATICY_OPERATOR_ADDRESS=Straße, PLZ Ort
```

Fehlen die Angaben, weist die Rechteseite offen darauf hin, statt eine Firma oder Anschrift zu
erfinden. Aquaticy enthält keine Werbe- oder Tracking-SDKs, veröffentlicht keine
Nutzerbewertungen und verarbeitet selbst keine Zahlungen. Verlangt ein Betreiber Geld für den
Zugang, muss er Preise, Kündigung und Erstattung vor dem Kauf selbst klar ausweisen.

## Aufbau

```
aquaticy/
  cli.py         # Chat-Loop, Slash-Befehle, Unterbefehle
  agent.py       # LLM-Loop mit Tool-Calling
  tools.py       # web_search + fetch_page
  search.py      # austauschbare Such-Backends
  fetch.py       # HTTP-Abruf, robots.txt, Cookie-Stufen 1 + 2
  browser.py     # Playwright-Fallback (Stufe 3)
  extract.py     # Produktdaten aus JSON-LD, OG, Microdata, Tabellen
  render.py      # Live-Anzeige, Produktkarten, Bilder
  web.py         # Weboberflaeche (nur Standardbibliothek)
  webui.html     # die Oberflaeche selbst, eine einzige Datei
  lan.py         # das eigene Netz erkunden, ohne nmap
  homeassistant.py # Zustaende lesen, Dienste aufrufen
  export.py      # HTML / Markdown / CSV
  uistate.py     # der Zustand der Oberfläche -- auf dem Server, geprüft
  sandbox.py     # die Werkstatt: abgeschotteter Behälter für den Code-Modus
  jobs.py        # Aufträge: Fragen, die sich von selbst stellen
  usage.py       # der Token-Zähler (drei Zeichen sind ein Token)
  auth.py        # Konten, Passwort-Hashes, Sitzungen und Limits
  memory.py      # der verschlüsselte Speicher
  google.py      # Gmail und Kalender, lesend und (auf Wunsch) ändernd
  subagents.py   # parallele Rechercheaufträge samt Rollen und Prüfern
  master.py      # der Master: beauftragt, bewertet, schickt nach (Pro-Modus)
  places.py      # die Karte: kleine Läden, die keine Suchmaschine kennt
  local_model.py # lokale Modelle per Ollama einrichten
  cache.py       # SQLite-Cache und Verlauf
  config.py      # Settings aus .env
  selectors.yaml # Selektor- und Marker-Listen, ohne Code erweiterbar

Dockerfile       # zwei Ziele: slim (ohne Browser) und browser (mit Chromium)
compose.yaml     # aquaticy plus optionales SearXNG
aquaticy-box       # Wrapper: ./aquaticy-box "deine Frage"
```

## Entwicklung

```bash
git clone https://github.com/jonasenriklaumen-a11y/thing-finder-
cd thing-finder-
uv venv && uv pip install -e ".[dev]"

uv run pytest        # alle Tests, Netzwerk und LLM gemockt
uv run ruff check .  # Linting

python tools/rundgang.py            # die Oberfläche im Browser durchgehen
python tools/rundgang.py --bilder   # dabei Bildschirmfotos ablegen
python tools/rundgang.py --nur chat,einstellungen   # nur einzelne Abschnitte
```

**Der Rundgang** unter `tools/rundgang.py` bedient die Weboberfläche wie ein Mensch:
Frage stellen, abbrechen, zwischen Normal, Pro und Code wechseln, den Master beim
Nachschicken zusehen, `/max` tippen, mitten im Lauf die Seite neu laden und sich wieder
anhängen, Denken und Gegenprüfen umlegen, Rückfrage
beantworten, Chat umbenennen und löschen, jeden Abschnitt der Einstellungen anspringen,
jeden Prüfknopf drücken, jedes Farbschema in Hell und Dunkel durchklicken, eine Datei
anhängen, Slash-Befehle tippen — dazu dasselbe noch einmal auf einem Handy-Schirm und
einmal mit „weniger Bewegung". Am Ende steht, was geprüft und was beanstandet wurde; der
Rückgabewert ist die Anzahl der Beanstandungen. Der Agent dahinter ist gestellt, es
laufen also weder Modelle noch Suchanfragen. `pytest` führt ihn als eigenen Prozess mit
aus und überspringt ihn, wo Playwright oder der Browser fehlen.

Die Tests fassen kein echtes Netz an: Suchergebnisse und LLM-Antworten sind gemockt,
Seitenabrufe laufen über `httpx.MockTransport` gegen gespeicherte HTML-Fixtures echter
Banner-Layouts (OneTrust, Cookiebot, Usercentrics, Sourcepoint-Consent-Wall, Paywall,
Captcha).

## Lizenz

MIT
