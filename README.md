# scoutr

Ein KI-Agent, mit dem man chatten kann und der eigenständig das Internet durchsucht,
die gefundenen Seiten liest und die Ergebnisse ausgewertet zurückgibt — mit Quelle zu
jeder Angabe. **Und er kennt dein Zuhause:** er sieht ins eigene Netz und liest Home
Assistant, beantwortet also auch Fragen, die im Web gar nicht stehen können.

Im Terminal (`scoutr`), im Browser (`scoutr web`) und vom Handy aus (`scoutr web --lan`)
— derselbe Agent, dieselben Einstellungen.

```
$ scoutr

> Finde mir gute Cafés in Mönchengladbach mit WLAN

  [Suche] cafés mönchengladbach
  [Suche] café mönchengladbach wlan arbeiten
  [Lese]  4 Seiten...

  Ich habe 6 Cafés gefunden, die zu deiner Anfrage passen:

  1. Café Nordwand — Hindenburgstr. 12
     WLAN ausdrücklich erwähnt, Steckdosen an den Fensterplätzen.
     Quelle: cafe-nordwand.de, Google-Bewertungen 4,6 (212)
  ...

> davon nur die, die sonntags offen haben

  [Lese] 6 Seiten...
  ...
```

## Quickstart in 3 Minuten

```bash
# 1. Installieren (aus diesem Repo -- scoutr liegt noch nicht auf PyPI)
git clone https://github.com/jonasenriklaumen-a11y/thing-finder-
cd thing-finder-
uv tool install .

# 2. Einrichten -- fragt nach Modell und API-Key, testet beide
scoutr setup

# 3. Loslegen -- im Terminal
scoutr

# ... oder im Browser (oeffnet sich von selbst)
scoutr web
```

**Aktualisieren** — beide Befehle müssen *im Repo-Verzeichnis* laufen, nicht im
Home-Verzeichnis:

```bash
cd ~/thing-finder-          # dorthin, wo du geklont hast
git pull
uv tool install . --force --reinstall
scoutr --version            # zeigt, ob die neue Version aktiv ist
```

> Voraussetzungen: Python 3.11+ und [uv](https://docs.astral.sh/uv/). Sobald das Paket
> veröffentlicht ist, genügt `uv tool install scoutr`. Zum Entwickeln stattdessen
> `uv venv && uv pip install -e ".[dev]"` und alles mit `uv run scoutr ...` aufrufen.

### Windows

Alles läuft auch unter Windows — in **PowerShell**:

```powershell
# uv installieren, falls noch nicht vorhanden
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

git clone --branch claude/scoutr-ai-web-agent-yogr3j `
  https://github.com/jonasenriklaumen-a11y/thing-finder-.git
cd thing-finder-
uv tool install .
uv tool update-shell        # danach PowerShell neu öffnen

scoutr install-model        # holt Ollama per winget und lädt die Modelle
```

Unterschiede zu Linux und macOS:

* Ollama kommt per `winget install Ollama.Ollama`; ohne winget lädst du den Installer von
  [ollama.com/download](https://ollama.com/download) und startest `scoutr install-model`
  danach erneut.
* Pfade mit Backslash und Anführungszeichen: `scoutr --image "C:\Users\du\Bilder"`
* Pfeiltasten-History im Chat gibt es nur mit `pip install pyreadline3`.
* Der Ollama-Server läuft im Hintergrund, ohne dass ein Konsolenfenster aufgeht.

`scoutr setup` fragt genau zwei Dinge ab:

| Was | Wo bekommt man es | Pflicht? |
|---|---|---|
| LLM-Anbieter + Key | [Anthropic](https://console.anthropic.com/settings/keys) · [OpenAI](https://platform.openai.com/api-keys) · [Google](https://aistudio.google.com/app/apikey) · oder lokal per [Ollama](https://ollama.com) ganz ohne Key | ja |
| Suchmaschine | **Nichts.** Die offene Metasuche ist Standard und braucht weder Key noch Konto. | nein |

Beide werden direkt mit einem Probe-Request getestet, bevor die `.env` geschrieben wird
(nach `~/.config/scoutr/.env`, Rechte `600`).

Ohne Chat, für einen einzelnen Durchlauf:

```bash
scoutr "welche Bahnstrecken in NRW sind gerade gesperrt?"
```

## Beispiel-Session

```
$ scoutr --location "Mönchengladbach" --lang de

╭──────────────────────────────────────────────────────╮
│ scoutr 0.1.0                                         │
│ Modell anthropic/claude-sonnet-4-6 · Suche duckduckgo │
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
  Gespeichert: scoutr-20260822-2043-ich-suche-einen-laptop.html
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
   ausdrückliche Bitte („merk dir …"). Anzeigen mit `/notes` bzw. `scoutr notes`.

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

Vor jeder Planung schaut scoutr kurz auf die Nachricht: **Braucht das überhaupt eine
Recherche?** Offensichtlicher Small-Talk („hallo", „danke") wird per Heuristik erkannt und
kostet keinen einzigen Modellaufruf. Bei allem anderen liefert **ein einziger Aufruf**
Entscheidung *und* Teilfragen — auf dem kleinen Subagenten-Modell, mit abgeschaltetem
Denk-Modus, kleinem Fenster und erzwungenem JSON-Schema. Fällt er aus oder dauert zu
lange (`SCOUTR_PLANNER_TIMEOUT`, Default 20 s), gilt sicherheitshalber „Recherche" —
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

Recherche-Anfragen zerlegt scoutr dann von sich aus in Teilfragen und lässt sie parallel
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

Jeder Subagent hat dieselben zwei Werkzeuge, ein eigenes kleines Budget (Default 6
Aufrufe) und liefert eine knappe Zusammenfassung mit Quellen zurück. Zwei laufen
gleichzeitig — bei lokalen Modellen bringt mehr wenig, weil die GPU ohnehin nacheinander
rechnet. Lässt sich eine Anfrage nicht sinnvoll teilen, entsteht genau eine Teilfrage und
der Ablauf bleibt wie zuvor. Nachfragen wie „nur die mit 4+ Sternen" bekommen das
bisherige Gespräch als Zusammenhang mit, damit die Teilfragen für sich verständlich sind.

Der Hauptagent darf zusätzlich jederzeit selbst weitere Teilfragen abgeben
(`research_subtasks`), wenn ihm im Verlauf etwas fehlt.

**Abschalten** fragt `scoutr setup` direkt ab, oder von Hand:

```bash
SCOUTR_SUBAGENTS_AUTO=false     # nur noch auf Wunsch des Modells
SCOUTR_MAX_SUBAGENTS=0          # ganz aus, zurück zu zwei Werkzeugen
```

#### Eigenes Modell für die Subagenten

Teilfragen sind eng umrissen — dafür reicht ein kleines Modell, das neben dem
Hauptmodell in den Speicher passt. `scoutr install-model` fragt danach; Stand August 2026:

| Modell | ca. Größe | ab VRAM |
|---|---|---|
| `qwen3:0.6b` | 0,5 GB | 2 GB |
| `qwen3:1.7b` | 1,4 GB | 3 GB |
| `gemma4:e2b` | 1,8 GB | 3 GB |
| `qwen2.5:3b` | 1,9 GB | 4 GB |
| `qwen3:4b` | 2,5 GB | 5 GB |

```bash
SCOUTR_SUBAGENT_MODEL=ollama_chat/qwen3:1.7b   # leer = Hauptmodell
```

Wichtig ist hier nur eines: Das Modell muss zuverlässig Werkzeuge aufrufen. Klug sein
darf das Hauptmodell, das die Ergebnisse am Ende zusammenführt.

**Grenzen:** maximal 20 Tool-Calls pro Anfrage, dann wird der Zwischenstand ausgegeben.
Was nicht gefunden wurde, wird als „nicht gefunden" gekennzeichnet — niemals geraten.

## Weboberfläche

```bash
scoutr web
```

Startet eine Oberfläche im Stil eines Chat-Fensters und öffnet den Browser. Es ist
derselbe Agent wie im Terminal: dieselben zwei Werkzeuge, dieselben Subagenten,
derselbe Verlauf, dieselbe `.env`. Die Zwischenschritte („Suche", „Lese", „Teile")
laufen live mit, die Antwort wird Wort für Wort gestreamt.

* **Grün auf Schwarz**, per Knopf umschaltbar auf Weiß. Die Versionsnummer steht klein
  in der Kopfzeile.
* **Einstellungen** öffnet ein Formular mit *allem*, was auch `scoutr setup` fragt:
  Haupt-, Vision- und Subagenten-Modell, API-Key, API-Basis, Suchmaschine samt Engine-
  Liste und SearXNG-URL, Ort/Sprache/Land, Subagenten an/aus samt Budget und
  Parallelität, Werkzeug-Budget, Kontextfenster, Planungs-Zeitlimit und der
  Playwright-Fallback. Gespeichert wird in dieselbe `.env`, danach lädt der Agent neu.
  Ein leeres API-Key-Feld bedeutet „unverändert" — der vorhandene Key bleibt stehen.
* **Dateien anhängen** über die Büroklammer, per Drag-and-drop irgendwo aufs
  Fenster oder mit <kbd>Strg</kbd>+<kbd>V</kbd> aus der Zwischenablage. Bilder gehen
  ans Vision-Modell, PDFs werden ausgelesen, Text-, Markdown-, CSV- und JSON-Dateien
  direkt übernommen. Bis zu 5 Dateien à 25 MB. Ein gescanntes PDF ohne Textebene sagt
  das offen — geraten wird nichts.
* **Rückfragen:** braucht scoutr etwas Entscheidendes (Budget, Ort, welches von
  mehreren Dingen gemeint ist), fragt er nach — mit anklickbaren Antworten oder einem
  Feld zum Selberschreiben. Er fragt höchstens zweimal je Anfrage und nur, wenn die
  Antwort das Ergebnis wirklich ändert; sonst trifft er eine Annahme und sagt sie dazu.
* **Merkzettel** und **Neuer Chat** liegen daneben in der Kopfzeile.
* **Alle Slash-Befehle** aus dem Terminal funktionieren auch hier: `/location`,
  `/model`, `/image`, `/export`, `/history`, `/notes`, `/clear`, `/help`. `/image`
  nimmt einen Dateipfad oder einen Ordner vom selben Rechner — bei mehreren Bildern
  im Ordner nimmt er das neueste.

```bash
scoutr web --port 9000     # anderer Port, falls 8765 belegt ist
scoutr web --no-open       # ohne Browser zu öffnen
```

Der Server läuft auf der Standardbibliothek, braucht also keine zusätzliche
Abhängigkeit. Beenden mit <kbd>Strg</kbd>+<kbd>C</kbd>.

### Vom Handy oder Tablet: `scoutr web --lan`

```bash
scoutr web --lan
```

Damit hört scoutr auf allen Netzwerkkarten und nennt dir jede Adresse, unter der
er erreichbar ist — im heimischen Netz und über Tailscale:

```
╭───────────────────────────────────────────────────────────────────╮
│ scoutr 5.2                                                        │
│ Diese Adresse im Browser oeffnen:                                 │
│   http://192.168.1.44:8765/    im heimischen Netz                 │
│   http://100.81.120.100:8765/  ueber Tailscale                    │
│   http://127.0.0.1:8765/       auf diesem Rechner                 │
│ Modell ollama_chat/gemma4:12b · Suche duckduckgo                  │
│ Kein Zugangswort: Adresse und Port genuegen.                      │
╰───────────────────────────────────────────────────────────────────╯
```

**Mehr als Adresse und Port braucht es nicht** — keine Anmeldung, kein Zugangswort,
nichts einzutippen. Die Adressen ermittelt scoutr selbst, du musst nichts
nachschlagen. Die Tailscale-Adresse erscheint nur, wenn Tailscale auch läuft.

Wer den Zugang trotzdem einschränken will, vergibt ein Wort:

```bash
scoutr web --lan --token familie   # dann nur mit ?token=familie in der Adresse
scoutr web --host 192.168.1.44     # gezielt eine Netzwerkkarte
```

Das Wort hängt hinten an der Adresse. Nur der erste Aufruf braucht es — danach
merkt es sich der Browser, und scoutr nimmt es aus der Adresszeile heraus. Es darf
nur ASCII enthalten (also `gruen`, nicht `grün`): der Browser schickt es als
HTTP-Kopfzeile mit, und die verträgt keine Umlaute. scoutr sagt es dir beim Start,
falls das Wort nicht taugt.

**Wenn das andere Gerät die Seite nicht lädt:** meist blockt die Firewall des
Rechners den Port. Unter Windows fragt die Firewall beim ersten Start nach —
dort „privates Netzwerk" erlauben. Unter Linux mit ufw: `sudo ufw allow 8765/tcp`.
Ohne Tailscale müssen beide Geräte im selben Netz sein (nicht eines im
WLAN-Gastzugang).

Alle Geräte teilen sich **eine** Sitzung — der Gesprächsverlauf ist also derselbe,
egal von wo du weiterfragst. Fragt ein zweites Gerät, während noch eine Recherche
läuft, sieht es „[Warte] Ein anderes Gerät fragt gerade" und kommt danach dran.
Ins offene Internet stellt `--lan` nichts: dafür bräuchte es zusätzlich eine
Portfreigabe im Router. Über Tailscale erreichst du scoutr auch von unterwegs,
ohne eine solche Freigabe — genau dafür ist es da.

## Chat-Interface

| Befehl | Wirkung |
|---|---|
| `/location <ort>` | Ortsfilter setzen (ohne Argument: aufheben) |
| `/model <name>` | Modell wechseln, z. B. `openai/gpt-4o` |
| `/export html\|md\|csv` | Recherche dieser Sitzung speichern |
| `/image <pfad>` | Bild beschreiben lassen, danach damit recherchieren |
| `/history` | frühere Recherchen anzeigen |
| `/notes` | Merkzettel anzeigen (pflegen: `scoutr notes --delete N`) |
| `/clear` | Gesprächsverlauf verwerfen |
| `/help` | Übersicht |
| `/quit` | beenden (auch <kbd>Strg</kbd>+<kbd>D</kbd>) |

Der Kontext bleibt über mehrere Turns erhalten, Nachfragen wie „nur die mit 4+ Sternen"
funktionieren also.

**Umgekehrt fragt scoutr auch selbst nach.** Ist etwas Entscheidendes offen — Budget,
Ort, welches von mehreren Dingen gemeint ist —, stellt er *eine* Rückfrage und wartet
auf die Antwort. Im Terminal tippst du sie ein (oder die Nummer einer angebotenen
Möglichkeit), Enter allein überspringt. Er fragt höchstens zweimal je Anfrage und nur,
wenn die Antwort das Ergebnis wirklich ändert — sonst trifft er lieber eine Annahme und
schreibt sie in die Antwort.

### Flags

```bash
scoutr --location "Mönchengladbach" --lang de   # Ortsfilter vorgeben
scoutr --model openai/gpt-4o                    # Modell für diese Sitzung
scoutr --image foto.jpg                         # Bild als Ausgangspunkt
scoutr --max-calls 30                           # Werkzeug-Budget ändern
scoutr --no-stream                              # Antwort am Stück statt gestreamt
scoutr --download-images                        # Bilder beim Export mitspeichern
```

### Weitere Unterbefehle

```bash
scoutr search "cafés mönchengladbach"    # nur web_search, ohne LLM
scoutr fetch https://example.de/         # nur fetch_page, ohne LLM
scoutr cache                             # Cache-Statistik, --clear leert ihn
scoutr history                           # vergangene Recherchen
scoutr export html -n 3                  # letzte 3 Recherchen exportieren
scoutr config                            # aktive Konfiguration prüfen
scoutr install-model                     # lokales Modell einrichten (ohne Key)
scoutr install-browser                   # Playwright-Fallback aktivieren
scoutr web                               # Oberflaeche im Browser starten
scoutr web --lan                         # auch vom Handy im heimischen Netz
scoutr lan                               # Geraete im eigenen Netz anzeigen
scoutr connect-ha                        # Home Assistant verbinden
```

## Zuhause: Heimnetz und Home Assistant

Manche Fragen kann kein Suchtreffer beantworten. „Welche Geräte hängen hier im Netz",
„läuft mein Drucker noch", „wie warm ist es im Wohnzimmer" — dafür sieht scoutr selbst
nach.

### Das eigene Netz

```bash
scoutr lan                      # zeigt, was erreichbar ist
scoutr lan --thorough           # alle bekannten Ports statt der zwölf häufigsten
scoutr lan --subnet 10.0.0.0/24
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
  Fremde Adressen lehnt scoutr ab, in jeder Schreibweise. Höchstens 512 Adressen am
  Stück, ein `/16` also nicht.
* **Nur die Frage „antwortet da etwas"** — scoutr klopft an, liest den Titel einer
  Weboberfläche und geht weiter. Keine Passwortversuche, keine Schwachstellensuche.
  Ein Gerät, das nicht antwortet, heißt „nicht erreichbar", nie „existiert nicht": es
  kann auch schlafen.

Abschalten: `SCOUTR_LAN_ENABLED=false` oder der Haken in den Einstellungen.

### Home Assistant

```bash
scoutr connect-ha
```

Das sucht die Instanz selbst im Netz, zeigt dir, wo du das Zugriffstoken herbekommst,
testet die Verbindung und schreibt beides in die `.env`. Eine Minute, dann kannst du
fragen:

```
> wie warm ist es im Wohnzimmer
  [Haus]  wohnzimmer
  Im Wohnzimmer sind es 21,4 °C (Stand: 18:29 Uhr).

> welche lichter sind gerade an
  [Haus]  light
  Drei von neun: Küche, Flur und die Stehlampe im Wohnzimmer.
```

In der Weboberfläche geht dasselbe unter **Einstellungen → Zuhause & Netz**: „Suchen"
findet die Instanz, „Testen" prüft das Token und sagt dir, wie viele Geräte scoutr
sieht.

**Schalten ist standardmäßig aus.** Ohne Haken sieht scoutr nur nach. Mit Haken darf er
Licht, Steckdosen, Szenen und Medien bedienen — und selbst dann fragt er bei
**Schlössern, Alarmanlagen, Toren, Rollläden, Heizung und Saugrobotern** jedes Mal
nach, bevor er etwas tut. Ein missverstandener Halbsatz soll nicht die Haustür
aufschließen. Bereiche außerhalb der Liste (etwa `shell_command`) schaltet er
grundsätzlich nicht.

Das Token steht in deiner `.env` und wird nie an den Browser geschickt — die Oberfläche
erfährt nur, *ob* eines gesetzt ist.

## Ortsfilter

Nennt man eine Stadt, Region oder ein Land, baut der Agent das in die Suchanfragen ein
**und** setzt zusätzlich die Länder- und Sprachparameter der Such-API. Treffer, die
offensichtlich außerhalb liegen, sortiert er aus. Zusätzlich vorgebbar per Flag oder
Slash-Befehl:

```bash
scoutr --location "Mönchengladbach" --lang de
```
```
/location Köln
```

## Bild als Eingabe

```bash
scoutr --image foto.jpg              # eine Datei
scoutr --image ~/hallo1234           # ein Ordner -- scoutr sucht das Bild darin
scoutr --image ~/hallo1234 "wo kann ich das kaufen?"
```

Zeigt der Pfad auf einen **Ordner**, nimmt scoutr das einzige Bild darin; sind es mehrere,
listet er sie auf (neueste zuerst) und fragt, welches gemeint ist.

Ein Vision-Modell beschreibt, was auf dem Bild zu sehen ist (Produkt, Logo, Schild,
Text), daraus werden Suchbegriffe — danach läuft die normale Recherche. Im Chat geht
dasselbe mit `/image pfad.jpg`.

Es wird nichts hochgeladen: scoutr liest die Datei von deiner Platte. Zuständig ist
`SCOUTR_VISION_MODEL`; ist das leer, wird das Hauptmodell gefragt — und **Textmodelle
können keine Bilder sehen**. Ein lokales Vision-Modell richtest du so ein:

```bash
scoutr install-model --vision-only
```

Welches Modell gerade zuständig ist, zeigt `scoutr config` in der Zeile „Vision-Modell".

## Im Container laufen lassen

Wer scoutr nicht direkt aufs System installieren will, lässt es in einem Container
laufen. Der isoliert das **Dateisystem**, nicht die Verbindung: das Netz bleibt
uneingeschränkt offen, sonst könnte der Agent nicht recherchieren.

```bash
./scoutr-box --setup            # einmalig: fragt Modell und Key ab, schreibt ./.env
./scoutr-box                    # Chat
./scoutr-box "deine Frage"      # einmalige Recherche
./scoutr-box search "test"      # nur die Suche, ohne LLM
```

Oder direkt mit Compose, ohne den Wrapper:

```bash
docker compose run --rm scoutr                 # Chat
docker compose run --rm scoutr "deine Frage"
docker compose build scoutr                    # nach Codeänderungen
```

### Was der Container sieht — und was nicht

| | |
|---|---|
| **Netz** | vollständig offen, keine Einschränkung — nötig für Suche und Seitenabruf |
| **Dateisystem** | nur `/data` (Cache + Verlauf, Docker-Volume) und `/work` (→ `./exports`) |
| **Benutzer** | nicht `root`, sondern `scoutr` (UID 1000) |
| **Rechte** | `no-new-privileges`, keine Zugriffe aufs Home-Verzeichnis des Hosts |
| **Keys** | kommen aus `./.env`, werden als Umgebungsvariablen hineingereicht |

Exporte (`/export html`) landen in `./exports` und sind damit direkt auf dem Host
lesbar. Cache und Verlauf überleben im Volume `scoutr-data`.

### Zwei Varianten des Images

```bash
docker compose build scoutr                                  # mit Chromium (Default)
SCOUTR_IMAGE_TARGET=slim docker compose build scoutr         # ohne, ~700 MB kleiner
```

Das `browser`-Image bringt Chromium für den JavaScript-Fallback (Stufe 3) mit. Darin
läuft Chromium ohne seine eigene Sandbox (`SCOUTR_BROWSER_NO_SANDBOX=1`) — die Isolation
übernimmt der Container. Bei einer normalen Installation aufs System bleibt die
Browser-Sandbox aktiv.

### Alles im Haus: mit eigener Suchmaschine

Zusammen mit SearXNG geht auch die Suche über keinen fremden Dienst mehr:

```bash
docker compose --profile searxng up -d searxng
echo 'SCOUTR_SEARCH_BACKEND=searxng' >> .env
docker compose --profile searxng run --rm scoutr
```

Die mitgelieferte `docker/searxng/settings.yml` hat die JSON-Ausgabe bereits aktiviert.
Ersetze darin vor dem ersten Start den `secret_key` durch etwas Eigenes
(`openssl rand -hex 32`).

## Suchmaschine — ohne API-Key

Die Suche kostet nichts und braucht **kein Konto**. Standard ist eine offene Metasuche:
`scoutr` fragt über [`ddgs`](https://pypi.org/project/ddgs/) mehrere freie Suchmaschinen
per HTML ab und mischt die Treffer. Fällt eine aus (Rate-Limit, Umbau), übernehmen die
anderen — genau deshalb ist die Metasuche robuster als eine einzelne Engine.

Verfügbar ohne Key: `duckduckgo`, `mojeek`, `startpage`, `brave`, `yahoo`, `wikipedia`.

```bash
# alle offenen Engines (Default, nichts zu tun)
SCOUTR_SEARCH_BACKEND=duckduckgo

# gezielt einschränken, wenn eine Engine bei dir zickt
SCOUTR_SEARCH_ENGINES=duckduckgo,mojeek
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
SCOUTR_SEARCH_BACKEND=searxng
SCOUTR_SEARXNG_URL=http://localhost:8080
```

Öffentliche SearXNG-Instanzen funktionieren auch, haben die JSON-Ausgabe aber oft
abgeschaltet.

### Kommerzielle APIs (optional)

[Brave Search](https://brave.com/search/api/) und [Tavily](https://app.tavily.com/home)
sind eingebaut, brauchen aber einen Key. Nur sinnvoll, wenn dir die offenen Engines nicht
zuverlässig genug sind:

```bash
SCOUTR_SEARCH_BACKEND=brave
BRAVE_API_KEY=...
```

Testen lässt sich jedes Backend ohne LLM:

```bash
scoutr search "cafés mönchengladbach" -n 5
```

## Stabilität

Lokale Modelle scheitern anders als Cloud-Modelle. scoutr fängt die drei häufigsten Fälle
ab:

* **Kontextüberlauf — die häufigste Ursache für „er vergisst die letzte Frage".**
  Läuft das Fenster über, wirft der Anbieter *still* den **Anfang** weg: erst den
  Systemprompt, dann die früheren Fragen. Das Gespräch wirkt dann wie zurückgesetzt.
  scoutr beugt zweifach vor:

  1. **Ein ausreichend großes Fenster anfordern.** Ollama nimmt sonst seinen Default von
     2048–4096 Token — nach einer recherchierten Antwort ist der schon voll. scoutr
     schickt `num_ctx` mit (`SCOUTR_CONTEXT_TOKENS`, Default 16384). Bei Cloud-Anbietern
     entfällt das, die kennen den Parameter nicht.
  2. **Selbst kürzen statt gekürzt werden — und zwar in der richtigen Reihenfolge.**
     Geopfert wird von hinten nach vorn nach Wert: zuerst ältere Werkzeug-Ausgaben →
     Platzhalter (`SCOUTR_KEEP_FULL_RESULTS`, Default 4), dann ältere Vorrecherche-Blöcke
     (die wiederholten sich sonst jeden Turn), dann die verbliebenen Suchergebnisse, dann
     ältere Antworten — und **erst ganz zuletzt die Fragen des Nutzers**. Ein Suchergebnis
     von vorletzter Runde ist ersetzbar, deine Frage nicht: die steht nirgendwo sonst.
     Systemprompt und aktuelle Frage bleiben immer stehen, und Werkzeugaufrufe werden nie
     von ihren Antworten getrennt.
  3. **Kein einzelner Brocken darf das Fenster auffressen.** Ein Suchergebnis oder eine
     angehängte Datei bekommt höchstens gut ein Drittel des Budgets — sonst passt bei
     einem kleinen Fenster schon ein einziges Ergebnis samt Systemprompt nicht mehr hinein,
     und dem Kürzen bliebe nur noch das Gespräch selbst. Obergrenze ist zusätzlich
     `SCOUTR_MAX_TOOL_CHARS` (Default 8000).

  Passt dein Modell mehr, dreh auf — `gemma4:12b` kann 128k, kostet aber VRAM:

  ```bash
  SCOUTR_CONTEXT_TOKENS=32768
  ```
* **Abgestürzter Runner.** Bei `model runner has unexpectedly stopped` entlädt scoutr alle
  Modelle und versucht es erneut, statt den Durchlauf zu verlieren.
* **Wackelige Verbindung.** Timeouts, 502/503 und Rate-Limits werden bis zu
  `SCOUTR_LLM_RETRIES` mal wiederholt (Default 3, mit wachsender Wartezeit). Ein falscher
  API-Key wird *nicht* wiederholt — das würde nur Zeit kosten.

Dazu: Ein Werkzeug, das eine Ausnahme wirft, beendet den Durchlauf nicht mehr, sondern
meldet den Fehler an das Modell, das dann eine andere Quelle nimmt.

## Verhalten beim Seitenabruf

* `robots.txt` wird respektiert (einmal je Origin geholt und zwischengespeichert)
* ehrlicher User-Agent, der scoutr benennt
* maximal 1 Request pro Sekunde und Domain, Timeout 15 s
* bei Fehler oder Blockade: überspringen und mit dem nächsten Treffer weitermachen,
  nicht abbrechen
* kein Umgehen von Logins, Paywalls oder Captchas — ist eine Seite nicht öffentlich
  lesbar, wird sie ausgelassen und im Ergebnis als „nicht öffentlich zugänglich" vermerkt

## Cookie-Banner und Pop-ups

Der Punkt, an dem die meisten simplen Crawler scheitern: Statt des Seiteninhalts wird der
Text des Cookie-Dialogs extrahiert. scoutr löst das in drei Stufen.

**Stufe 1 — gar nicht erst hinklicken (Standardfall).** `fetch_page` holt reines HTML
ohne JavaScript-Ausführung. Consent-Banner sind dann meist nur inaktive DOM-Knoten oder
werden gar nicht erst eingebaut. Vor der Textextraktion fliegen sie per Selektor-Blockliste
raus (`scoutr/selectors.yaml`, ohne Codeänderung erweiterbar), danach übernimmt
trafilatura die restliche Boilerplate-Entfernung. Absätze, die im Wesentlichen aus
Consent-Formulierungen bestehen, werden zusätzlich aus dem Text gestrichen.

**Stufe 2 — erkennen, ob es geklappt hat.** Bleiben nach der Extraktion weniger als
~200 Zeichen übrig und stehen typische Consent-Marker im HTML, gilt der Abruf als
gescheitert. Unterschieden wird zwischen `blocked`, `consent_required`, `paywall` und
`empty` — jeder Grund wird dem Agenten gemeldet, damit er eine andere Quelle nimmt.

**Stufe 3 — Playwright-Fallback.** Optionale Abhängigkeit, nur für Seiten, die ohne
JavaScript nichts liefern:

```bash
uv tool install --with playwright scoutr
scoutr install-browser
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
HTTP-Abrufe aggressiv (403, Captcha-Seite). scoutr baut dafür **keine Umgehung**, sondern
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
| `SCOUTR_MODEL` | LiteLLM-Modell-ID | `anthropic/claude-sonnet-4-6` |
| `SCOUTR_VISION_MODEL` | Modell für `--image` | wie `SCOUTR_MODEL` |
| `SCOUTR_API_BASE` | eigene Basis-URL (Ollama, eigene NIM, Proxy) | — |
| `SCOUTR_API_KEY` | Key für Anbieter ohne eigenen Eintrag | — |
| `SCOUTR_SEARCH_BACKEND` | `duckduckgo`, `searxng`, `brave`, `tavily` | `duckduckgo` |
| `SCOUTR_SEARCH_ENGINES` | Engines der Metasuche einschränken | alle |
| `SCOUTR_SEARXNG_URL` | Adresse der SearXNG-Instanz | — |
| `SCOUTR_LOCATION` | Standard-Ortsfilter | — |
| `SCOUTR_LANG` / `SCOUTR_COUNTRY` | Sprache / Land der Suche | `de` / `de` |
| `SCOUTR_MAX_TOOL_CALLS` | Werkzeug-Budget je Anfrage | `20` |
| `SCOUTR_MAX_SUBAGENTS` | Subagenten je Anfrage (`0` = aus) | `4` |
| `SCOUTR_SUBAGENTS_AUTO` | jede Anfrage automatisch zerlegen | `true` |
| `SCOUTR_TRIAGE_TIMEOUT` | Zeitlimit der Small-Talk-Heuristik (s) | `5` |
| `SCOUTR_PLANNER_TIMEOUT` | Zeitlimit für Prüfung + Planung (s) | `20` |
| `SCOUTR_CONTEXT_TOKENS` | Kontextfenster für lokale Modelle (`0` = Ollama-Default) | `16384` |
| `SCOUTR_SUBAGENT_MODEL` | leichtes Modell für die Subagenten | Hauptmodell |
| `SCOUTR_SUBAGENT_BUDGET` | Werkzeug-Budget je Subagent | `6` |
| `SCOUTR_SUBAGENT_PARALLEL` | gleichzeitige Subagenten (`0` = automatisch) | lokal `2`, Cloud `4` |
| `SCOUTR_LLM_RETRIES` | Versuche bei transienten Fehlern | `3` |
| `SCOUTR_MAX_TOOL_CHARS` | Zeichen je Werkzeug-Ergebnis | `8000` |
| `SCOUTR_KEEP_FULL_RESULTS` | ungekürzte Ergebnisse im Verlauf | `4` |
| `SCOUTR_FETCH_TIMEOUT` | Timeout je Seitenabruf (s) | `15` |
| `SCOUTR_CACHE_TTL_HOURS` | Gültigkeit des Response-Cache | `24` |
| `SCOUTR_ENABLE_PLAYWRIGHT` | Stufe-3-Fallback erlauben | `true` |

### Ganz ohne API-Key: lokales Modell

Ein Befehl, und scoutr richtet sich ein Modell auf deinem Rechner ein:

```bash
scoutr install-model
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
SCOUTR_MODEL=ollama_chat/qwen2.5:7b
SCOUTR_API_BASE=http://localhost:11434
```

Zum Schluss fragt er, ob du **auch Bilder** als Eingabe nutzen willst, und richtet dafür
ein Vision-Modell ein — mit eigenem Sehtest: Das Modell bekommt ein rotes Quadrat gezeigt
und muss die Farbe nennen. Ein Textmodell fällt dabei durch und wird nicht eingetragen.

Ein bestimmtes Modell direkt:

```bash
scoutr install-model --model qwen2.5:14b                  # nur Text
scoutr install-model --vision-model llava:7b              # Text + Bild
scoutr install-model --vision-only --vision-model llava:7b  # nur Bild nachrüsten
scoutr install-model --model qwen2.5:7b --no-vision       # ohne Bild
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
> Werkzeuge aufrufen** — als Hauptmodell ist es damit unbrauchbar, scoutr führt es
> deshalb nur bei den Vision-Modellen. Erst `gemma4` bringt beides mit.

scoutr liest den VRAM per `nvidia-smi` aus und schlägt danach vor — ohne GPU rechnet er
mit 70 % des Arbeitsspeichers, weil auf der CPU nicht alles nutzbar ist. Passt ein
Modell, das beides kann, wird kein zweites geladen.

**Die Hardware empfiehlt, sie entscheidet nicht.** Die Liste zeigt immer alle Modelle,
und du kannst jedes davon wählen — oder einen beliebigen Namen aus dem Ollama-Katalog
eintippen. Passt eines rechnerisch nicht, sagt scoutr das als Hinweis und lädt es
trotzdem: Ein Modell läuft notfalls teilweise auf der CPU, das ist langsam, aber deine
Entscheidung.

Jedes andere Ollama-Modell mit Werkzeug-Unterstützung geht auch — `--model` nimmt jeden
Namen aus dem [Ollama-Katalog](https://ollama.com/library).

> **Das Präfix muss `ollama_chat/` lauten, nicht `ollama/`.** Nur ersteres reicht
> Werkzeuge durch; mit `ollama/` bleibt der Agent stumm. `scoutr install-model` schreibt
> automatisch das richtige.

#### Wenn der Speicher knapp wird

Text- und Vision-Modell gleichzeitig im VRAM sprengen viele Grafikkarten — der
Ollama-Runner stirbt dann mit `model runner has unexpectedly stopped`. scoutr entlädt
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
scoutr --model openai/gpt-4o
scoutr --model nvidia_nim/meta/llama-3.3-70b-instruct   # NVIDIA NIM
scoutr --model ollama_chat/qwen2.5:7b                   # lokal, siehe oben
```

| Anbieter | Modell-Präfix | Key |
|---|---|---|
| Anthropic | `anthropic/` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/` | `OPENAI_API_KEY` |
| Google | `gemini/` | `GEMINI_API_KEY` |
| [NVIDIA NIM](https://build.nvidia.com/) | `nvidia_nim/` | `NVIDIA_NIM_API_KEY` |
| xAI · Together · Cerebras · Perplexity · Groq · DeepSeek · OpenRouter | siehe `.env.example` | jeweils eigener |
| Ollama (lokal) | `ollama_chat/` | — |

**Wichtig: Das Modell muss Tool-Calling (Function Calling) beherrschen.** Ohne das kann
der Agent weder suchen noch Seiten lesen — er antwortet dann aus dem Gedächtnis statt aus
dem Web, was genau das ist, was scoutr vermeiden soll.

Jeden weiteren LiteLLM-Anbieter nutzt du über den Notausgang `SCOUTR_API_KEY`:

```bash
SCOUTR_MODEL=irgendein_anbieter/modell
SCOUTR_API_KEY=dein-key
```

#### NVIDIA NIM im Detail

[build.nvidia.com](https://build.nvidia.com/) gibt dir nach der Anmeldung Startguthaben
und einen Key (`nvapi-...`). Modell auswählen, „Get API Key" klicken, dann:

```bash
SCOUTR_MODEL=nvidia_nim/meta/llama-3.3-70b-instruct
NVIDIA_NIM_API_KEY=nvapi-...
```

Die Modell-ID ist genau die von build.nvidia.com, mit `nvidia_nim/` davor. Achte darauf,
ein Modell zu wählen, das in der Modellkarte Tool-Calling aufführt — nicht alle dort
angebotenen Modelle können das. Eine eigene, selbst gehostete NIM-Instanz erreichst du
über `SCOUTR_API_BASE=http://dein-host:8000/v1`.

SQLite (`~/.scoutr/scoutr.sqlite3`) wird für genau zwei Dinge benutzt: Response-Cache
(TTL 24 h) und Verlauf vergangener Recherchen.

## Aufbau

```
scoutr/
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
  subagents.py   # parallele Rechercheaufträge
  local_model.py # lokale Modelle per Ollama einrichten
  cache.py       # SQLite-Cache und Verlauf
  config.py      # Settings aus .env
  selectors.yaml # Selektor- und Marker-Listen, ohne Code erweiterbar

Dockerfile       # zwei Ziele: slim (ohne Browser) und browser (mit Chromium)
compose.yaml     # scoutr plus optionales SearXNG
scoutr-box       # Wrapper: ./scoutr-box "deine Frage"
```

## Entwicklung

```bash
git clone https://github.com/jonasenriklaumen-a11y/thing-finder-
cd thing-finder-
uv venv && uv pip install -e ".[dev]"

uv run pytest        # alle Tests, Netzwerk und LLM gemockt
uv run ruff check .  # Linting
```

Die Tests fassen kein echtes Netz an: Suchergebnisse und LLM-Antworten sind gemockt,
Seitenabrufe laufen über `httpx.MockTransport` gegen gespeicherte HTML-Fixtures echter
Banner-Layouts (OneTrust, Cookiebot, Usercentrics, Sourcepoint-Consent-Wall, Paywall,
Captcha).

## Lizenz

MIT
