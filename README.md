# Cortex

**Cortex** ist der Rechercheagent, mit dem man chatten kann — er durchsucht
eigenständig das Internet, liest die gefundenen Seiten und gibt die Ergebnisse
ausgewertet zurück, mit Quelle zu jeder Angabe. **Und er kennt dein Zuhause:** er sieht
ins eigene Netz, liest Home Assistant und — wenn du es erlaubst — deinen Kalender und
dein Postfach. Also auch Fragen, deren Antwort im Web gar nicht stehen kann.

Ein Befehl, drei Wege: im Terminal (`cortex`), im Browser (`cortex web`) und vom Handy
aus (`cortex web --lan`) — überall derselbe Agent mit denselben Einstellungen.

```
$ cortex

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
# 1. Installieren (aus diesem Repo -- Cortex liegt noch nicht auf PyPI)
git clone https://github.com/jonasenriklaumen-a11y/thing-finder-
cd thing-finder-
uv tool install .

# 2. Einrichten -- fragt nach Modell und API-Key, testet beide
cortex setup

# 3. Loslegen -- im Terminal
cortex

# ... oder im Browser (oeffnet sich von selbst)
cortex web
```

**Aktualisieren** — beide Befehle müssen *im Repo-Verzeichnis* laufen, nicht im
Home-Verzeichnis:

```bash
cd ~/thing-finder-          # dorthin, wo du geklont hast
git pull
uv tool install . --force --reinstall
cortex --version            # zeigt, ob die neue Version aktiv ist
```

> Voraussetzungen: Python 3.11+ und [uv](https://docs.astral.sh/uv/). Sobald das Paket
> veröffentlicht ist, genügt `uv tool install cortex`. Zum Entwickeln stattdessen
> `uv venv && uv pip install -e ".[dev]"` und alles mit `uv run cortex ...` aufrufen.

### Umstieg von einer Version vor 7.5

Mit 7.5 heißt alles einheitlich Cortex — das Programm, der Ordner mit deinen Daten und
die Namen in der `.env`. Bestehende Einstellungen ziehen nicht von selbst mit. Ein
Durchlauf reicht:

1. **Alte Installation entfernen** (`uv tool uninstall <alter-name>`), sonst liegen
   zwei Programme nebeneinander.
2. **Datenordner umbenennen** — darin stecken Verlauf, Merkzettel, der verschlüsselte
   Speicher und die Google-Anmeldung. Verschiebe den alten versteckten Ordner in
   deinem Home-Verzeichnis nach `~/.cortex` und den Ordner unter `~/.config` nach
   `~/.config/cortex`.
3. **Die `.env` anpassen**: alle Schlüssel, die früher mit dem alten Namen begannen,
   heißen jetzt `CORTEX_…`. Die Namen der Anbieter-Schlüssel (`ANTHROPIC_API_KEY`,
   `NVIDIA_NIM_API_KEY`, `GOOGLE_CLIENT_ID` …) bleiben unverändert.

Wenn dir das zu fummelig ist: `cortex setup` legt eine frische `.env` an und fragt
dich durch. Verlauf und Merkzettel sind dann weg, alles andere ist in zwei Minuten
wieder eingerichtet.

### Windows

Alles läuft auch unter Windows — in **PowerShell**:

```powershell
# uv installieren, falls noch nicht vorhanden
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

git clone --branch claude/cortex-ai-web-agent-yogr3j `
  https://github.com/jonasenriklaumen-a11y/thing-finder-.git
cd thing-finder-
uv tool install .
uv tool update-shell        # danach PowerShell neu öffnen

cortex install-model        # holt Ollama per winget und lädt die Modelle
```

Unterschiede zu Linux und macOS:

* Ollama kommt per `winget install Ollama.Ollama`; ohne winget lädst du den Installer von
  [ollama.com/download](https://ollama.com/download) und startest `cortex install-model`
  danach erneut.
* Pfade mit Backslash und Anführungszeichen: `cortex --image "C:\Users\du\Bilder"`
* Pfeiltasten-History im Chat gibt es nur mit `pip install pyreadline3`.
* Der Ollama-Server läuft im Hintergrund, ohne dass ein Konsolenfenster aufgeht.

`cortex setup` fragt genau zwei Dinge ab:

| Was | Wo bekommt man es | Pflicht? |
|---|---|---|
| LLM-Anbieter + Key | [Anthropic](https://console.anthropic.com/settings/keys) · [OpenAI](https://platform.openai.com/api-keys) · [Google](https://aistudio.google.com/app/apikey) · oder lokal per [Ollama](https://ollama.com) ganz ohne Key | ja |
| Suchmaschine | **Nichts.** Die offene Metasuche ist Standard und braucht weder Key noch Konto. | nein |

Beide werden direkt mit einem Probe-Request getestet, bevor die `.env` geschrieben wird
(nach `~/.config/cortex/.env`, Rechte `600`).

Ohne Chat, für einen einzelnen Durchlauf:

```bash
cortex "welche Bahnstrecken in NRW sind gerade gesperrt?"
```

## Beispiel-Session

```
$ cortex --location "Mönchengladbach" --lang de

╭──────────────────────────────────────────────────────╮
│ Cortex AI 7.5.0                                      │
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
  Gespeichert: cortex-20260822-2043-ich-suche-einen-laptop.html
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
   ausdrückliche Bitte („merk dir …"). Anzeigen mit `/notes` bzw. `cortex notes`.

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

Vor jeder Planung schaut cortex kurz auf die Nachricht: **Braucht das überhaupt eine
Recherche?** Offensichtlicher Small-Talk („hallo", „danke") wird per Heuristik erkannt und
kostet keinen einzigen Modellaufruf. Bei allem anderen liefert **ein einziger Aufruf**
Entscheidung *und* Teilfragen — auf dem kleinen Subagenten-Modell, mit abgeschaltetem
Denk-Modus, kleinem Fenster und erzwungenem JSON-Schema. Fällt er aus oder dauert zu
lange (`CORTEX_PLANNER_TIMEOUT`, Default 20 s), gilt sicherheitshalber „Recherche" —
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

Recherche-Anfragen zerlegt cortex dann von sich aus in Teilfragen und lässt sie parallel
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

**Abschalten** fragt `cortex setup` direkt ab, oder von Hand:

```bash
CORTEX_SUBAGENTS_AUTO=false     # nur noch auf Wunsch des Modells
CORTEX_MAX_SUBAGENTS=0          # ganz aus, zurück zu zwei Werkzeugen
```

#### Eigenes Modell für die Subagenten

Teilfragen sind eng umrissen — dafür reicht ein kleines Modell, das neben dem
Hauptmodell in den Speicher passt. `cortex install-model` fragt danach; Stand August 2026:

| Modell | ca. Größe | ab VRAM |
|---|---|---|
| `qwen3:0.6b` | 0,5 GB | 2 GB |
| `qwen3:1.7b` | 1,4 GB | 3 GB |
| `gemma4:e2b` | 1,8 GB | 3 GB |
| `qwen2.5:3b` | 1,9 GB | 4 GB |
| `qwen3:4b` | 2,5 GB | 5 GB |

```bash
CORTEX_SUBAGENT_MODEL=ollama_chat/qwen3:1.7b   # leer = Hauptmodell
```

Wichtig ist hier nur eines: Das Modell muss zuverlässig Werkzeuge aufrufen. Klug sein
darf das Hauptmodell, das die Ergebnisse am Ende zusammenführt.

**Grenzen:** maximal 20 Tool-Calls pro Anfrage, dann wird der Zwischenstand ausgegeben.
Was nicht gefunden wurde, wird als „nicht gefunden" gekennzeichnet — niemals geraten.

## Wer antwortet da eigentlich

Fragt man Cortex, wer er ist, sagt er: *„Ich bin Cortex, ein KI-Assistent von Jonas."*
Nicht „ich bin ein Modell von Google" oder von sonst jemandem — Cortex ist das
Programm, das Sprachmodell darunter ist ein Bauteil davon, austauschbar über die
Einstellungen.

Gelogen wird dabei nicht: Fragt jemand ausdrücklich, welches Modell gerade läuft, sagt
er es. Der Unterschied ist, dass er sich nicht mit dem Modell verwechselt, das ihn
antreibt.

## Weboberfläche

```bash
cortex web
```

Startet eine Oberfläche im Stil eines Chat-Fensters und öffnet den Browser. Es ist
derselbe Agent wie im Terminal: dieselben zwei Werkzeuge, dieselben Subagenten,
derselbe Verlauf, dieselbe `.env`. Die Zwischenschritte („Suche", „Lese", „Teile")
laufen live mit, die Antwort wird Wort für Wort gestreamt.

* **Grün auf Schwarz**, per Knopf umschaltbar auf Weiß. Die Versionsnummer steht klein
  in der Kopfzeile.
* **Einstellungen** öffnet ein Formular mit *allem*, was auch `cortex setup` fragt —
  und ein Test hält das dauerhaft in Deckung: kommt im Terminal eine Frage dazu,
  schlägt er fehl, bis das Formular nachzieht. Enthalten sind:
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
* **Rückfragen:** braucht cortex etwas Entscheidendes (Budget, Ort, welches von
  mehreren Dingen gemeint ist), fragt er nach — mit anklickbaren Antworten oder einem
  Feld zum Selberschreiben. Er fragt höchstens zweimal je Anfrage und nur, wenn die
  Antwort das Ergebnis wirklich ändert; sonst trifft er eine Annahme und sagt sie dazu.
* **Letzte Chats** in der Seitenleiste sind Chats, keine Einzelfragen. Ein Chat
  beginnt mit **Neuer Chat**, bekommt seinen Namen von der ersten Frage darin und
  sammelt alles Weitere, bis du den nächsten startest. Klick auf einen Eintrag holt
  ihn zurück — samt Verlauf, an den der Agent wieder anknüpft. Neueste oben.
* **Merkzettel** und **Neuer Chat** liegen daneben in der Kopfzeile.
* **Mitlesen** unter *Einstellungen → Mitlesen*: Der Haken „Gedanken und Aktionen
  mitlesen" zeigt während der Antwort, was Cortex AI gerade denkt und tut — jede
  Suchanfrage im Wortlaut, jeden Werkzeugaufruf mit seinen Argumenten, was
  zurückkam, und bei Modellen, die ihre Denkschritte offenlegen, auch die. Der
  Schalter wirkt sofort und auch rückwirkend auf die Antwort, die schon dasteht:
  die Zeilen sind die ganze Zeit da, sie werden nur ein- und ausgeblendet.
* **Verbindung testen** im Feld *Suche*: schickt eine winzige Anfrage ans Modell und
  eine Testsuche los — dasselbe, was `cortex setup` am Ende macht. Geprüft wird, was
  gerade im Formular steht, nicht der gespeicherte Stand; so sieht man vor dem
  Speichern, ob ein Schlüssel stimmt.
* **Auslastung** unter *Einstellungen → Auslastung*: Der Haken „Auslastung des
  Rechners anzeigen" blendet Prozessor, Arbeitsspeicher, Festplatte, Grafikkarte und
  den belegten Speicher als Kacheln ein — alle vier Sekunden aufgefrischt, solange das
  Einstellungsfenster offen ist. Standardmäßig aus; der Haken bleibt im Browser
  gemerkt, gefragt wird nur, während du hinschaust.
* **Alle Slash-Befehle** aus dem Terminal funktionieren auch hier: `/location`,
  `/model`, `/image`, `/export`, `/history`, `/notes`, `/clear`, `/help`. `/image`
  nimmt einen Dateipfad oder einen Ordner vom selben Rechner — bei mehreren Bildern
  im Ordner nimmt er das neueste.

```bash
cortex web --port 9000     # anderer Port, falls 8765 belegt ist
cortex web --no-open       # ohne Browser zu öffnen
```

Der Server läuft auf der Standardbibliothek, braucht also keine zusätzliche
Abhängigkeit. Beenden mit <kbd>Strg</kbd>+<kbd>C</kbd>.

### Vom Handy oder Tablet: `cortex web --lan`

```bash
cortex web --lan
```

Damit hört cortex auf allen Netzwerkkarten und nennt dir jede Adresse, unter der
er erreichbar ist — im heimischen Netz und über Tailscale:

```
╭───────────────────────────────────────────────────────────────────╮
│ Cortex AI 7.5.0                                                   │
│ Diese Adresse im Browser oeffnen:                                 │
│   http://192.168.1.44:8765/    im heimischen Netz                 │
│   http://100.81.120.100:8765/  ueber Tailscale                    │
│   http://127.0.0.1:8765/       auf diesem Rechner                 │
│ Modell ollama_chat/gemma4:12b · Suche duckduckgo                  │
│ Kein Zugangswort: Adresse und Port genuegen.                      │
╰───────────────────────────────────────────────────────────────────╯
```

**Mehr als Adresse und Port braucht es nicht** — keine Anmeldung, kein Zugangswort,
nichts einzutippen. Die Adressen ermittelt cortex selbst, du musst nichts
nachschlagen. Die Tailscale-Adresse erscheint nur, wenn Tailscale auch läuft.

Wer den Zugang trotzdem einschränken will, vergibt ein Wort:

```bash
cortex web --lan --token familie   # dann nur mit ?token=familie in der Adresse
cortex web --host 192.168.1.44     # gezielt eine Netzwerkkarte
```

Das Wort hängt hinten an der Adresse. Nur der erste Aufruf braucht es — danach
merkt es sich der Browser, und cortex nimmt es aus der Adresszeile heraus. Es darf
nur ASCII enthalten (also `gruen`, nicht `grün`): der Browser schickt es als
HTTP-Kopfzeile mit, und die verträgt keine Umlaute. cortex sagt es dir beim Start,
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
Portfreigabe im Router. Über Tailscale erreichst du cortex auch von unterwegs,
ohne eine solche Freigabe — genau dafür ist es da.

## Chat-Interface

| Befehl | Wirkung |
|---|---|
| `/location <ort>` | Ortsfilter setzen (ohne Argument: aufheben) |
| `/model <name>` | Modell wechseln, z. B. `openai/gpt-4o` |
| `/export html\|md\|csv` | Recherche dieser Sitzung speichern |
| `/image <pfad>` | Bild beschreiben lassen, danach damit recherchieren |
| `/history` | frühere Recherchen anzeigen |
| `/notes` | Merkzettel anzeigen (pflegen: `cortex notes --delete N`) |
| `/clear` | Gesprächsverlauf verwerfen |
| `/help` | Übersicht |
| `/quit` | beenden (auch <kbd>Strg</kbd>+<kbd>D</kbd>) |

Der Kontext bleibt über mehrere Turns erhalten, Nachfragen wie „nur die mit 4+ Sternen"
funktionieren also.

**Umgekehrt fragt cortex auch selbst nach.** Ist etwas Entscheidendes offen — Budget,
Ort, welches von mehreren Dingen gemeint ist —, stellt er *eine* Rückfrage und wartet
auf die Antwort. Im Terminal tippst du sie ein (oder die Nummer einer angebotenen
Möglichkeit), Enter allein überspringt. Er fragt höchstens zweimal je Anfrage und nur,
wenn die Antwort das Ergebnis wirklich ändert — sonst trifft er lieber eine Annahme und
schreibt sie in die Antwort.

### Flags

```bash
cortex --location "Mönchengladbach" --lang de   # Ortsfilter vorgeben
cortex --model openai/gpt-4o                    # Modell für diese Sitzung
cortex --image foto.jpg                         # Bild als Ausgangspunkt
cortex --max-calls 30                           # Werkzeug-Budget ändern
cortex --no-stream                              # Antwort am Stück statt gestreamt
cortex --download-images                        # Bilder beim Export mitspeichern
```

### Weitere Unterbefehle

```bash
cortex search "cafés mönchengladbach"    # nur web_search, ohne LLM
cortex fetch https://example.de/         # nur fetch_page, ohne LLM
cortex cache                             # Cache-Statistik, --clear leert ihn
cortex history                           # vergangene Recherchen
cortex export html -n 3                  # letzte 3 Recherchen exportieren
cortex config                            # aktive Konfiguration prüfen
cortex install-model                     # lokales Modell einrichten (ohne Key)
cortex install-browser                   # Playwright-Fallback aktivieren
cortex web                               # Oberflaeche im Browser starten
cortex web --lan                         # auch vom Handy im heimischen Netz
cortex lan                               # Geraete im eigenen Netz anzeigen
cortex connect-ha                        # Home Assistant verbinden
```

## Der Speicher: was Cortex AI behält

Ohne Speicher fängt jedes Gespräch bei null an. Mit Speicher merkt sich Cortex AI, was
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

Vier Regeln bestimmen den Aufbau:

* **Nur Text.** Cortex AI legt ab, was es selbst formuliert hat. Bilder und Dateien
  kommen ausschließlich von dir und liegen getrennt.
* **Verschlüsselt.** Die Notizen stehen nicht im Klartext in der Datenbank. Wer die
  Datei kopiert — aus einem Backup, von einem verlorenen Laptop — liest ohne Schlüssel
  nichts.
* **Höchstens 400 MB**, zusammen mit Verlauf und hochgeladenen Dateien. Wird es eng,
  fliegen zuerst alte Uploads raus: ein Bild liegt meist noch woanders, eine Notiz nicht.
* **Abschaltbar** unter *Einstellungen → Speicher*, oder mit `CORTEX_MEMORY=false`.

```bash
/memory            # was liegt drin, wie voll ist es
/forget            # alle Notizen löschen
/uploads           # was du hochgeladen hast
/uploads clear     # alle hochgeladenen Dateien löschen
```

### Was die Verschlüsselung leistet — und was nicht

Der Schlüssel liegt standardmäßig als Datei neben der Datenbank, lesbar nur für dein
Benutzerkonto. Das schützt alles, was die Datei allein betrifft: Backups, Kopien,
Datenträger in fremden Händen. Es schützt **nicht** gegen jemanden, der schon in deinem
Benutzerkonto sitzt — der liest den Schlüssel einfach mit.

Wer auch das abdecken will, setzt eine Passphrase:

```bash
CORTEX_MEMORY_KEY="ein langes Passwort" cortex web
```

Dann wird der Schlüssel bei jedem Start neu abgeleitet und liegt nirgends auf der
Platte. Der Preis: ohne die Passphrase ist der Speicher unwiederbringlich weg.

## Gmail und Google Kalender

„Wann ist mein Zahnarzttermin", „ist die Rechnung schon gekommen", „was habe ich
Donnerstag vor" — dafür muss niemand das Web durchsuchen. Cortex AI kann direkt in
deinem Kalender und deinem Postfach nachsehen, wenn du es erlaubst.

Die Angaben helfen auch bei einer Recherche: Steht der Termin in Hamburg, sucht er für
Hamburg. Nennt die Bestellbestätigung eine Modellnummer, sucht er danach.

**Nur lesen.** Angefragt werden ausschließlich die Leserechte `gmail.readonly` und
`calendar.readonly`. Damit ist technisch ausgeschlossen, dass Cortex AI je eine Mail
verschickt, beantwortet, löscht oder einen Termin ändert — Google lässt es schlicht
nicht zu. Bittest du ihn trotzdem darum, sagt er, dass er das nicht kann.

**Aus, bis du es einschaltest.** Ohne den Haken in den Einstellungen und ohne
verbundenes Konto existieren die Werkzeuge für das Modell gar nicht.

### Einrichten — einmal, etwa fünf Minuten

Google verlangt für den Zugriff auf ein eigenes Konto eine eigene Anwendung. Das klingt
umständlicher, als es ist, und es ist kostenlos.

1. **Projekt anlegen.** Auf [console.cloud.google.com](https://console.cloud.google.com/)
   anmelden, oben links auf die Projektauswahl, *Neues Projekt*. Der Name ist egal,
   zum Beispiel „Cortex".
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
cortex google          # fragt nach ID und Secret, führt durch die Anmeldung
```

   Oder in der Weboberfläche: *Einstellungen → Gmail & Kalender*, Haken setzen,
   Client-ID und Secret einfügen, **speichern**, dann **Verbinden**. Du landest bei
   Google, stimmst zu, und bist zurück.

> **Vom Handy aus?** Google erlaubt für Desktop-Anwendungen nur `localhost` als
> Rückweg. Sitzt dein Browser auf einem anderen Gerät als Cortex, zeigt er nach der
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

### Was mit deinen Daten passiert

* **Die Anmeldedaten bleiben auf deinem Rechner.** Access- und Refresh-Token liegen
  verschlüsselt in `~/.cortex/google.json` (dieselbe Fernet-Schlüsseldatei wie beim
  Speicher, Rechte 600). Der Browser bekommt sie nie zu sehen — nur, *ob* ein Konto
  verbunden ist und welche Adresse es hat.
* **Nichts aus deinem Postfach geht an eine Suchmaschine.** Der Agent hat die
  ausdrückliche Anweisung, niemals Namen, Adressen, Nummern oder Betreffs aus Mails
  und Terminen in eine Suchanfrage zu setzen — die ginge an einen fremden Dienst. Er
  sucht mit allgemeinen Begriffen; das Persönliche bleibt im Gespräch.
* **Beenden jederzeit:** `cortex google --trennen` oder der Knopf *Trennen* in den
  Einstellungen löscht die Anmeldedaten. Den Zugriff selbst entziehst du zusätzlich
  unter [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

## Zuhause: Heimnetz und Home Assistant

Manche Fragen kann kein Suchtreffer beantworten. „Welche Geräte hängen hier im Netz",
„läuft mein Drucker noch", „wie warm ist es im Wohnzimmer" — dafür sieht cortex selbst
nach.

### Das eigene Netz

```bash
cortex lan                      # zeigt, was erreichbar ist
cortex lan --thorough           # alle bekannten Ports statt der zwölf häufigsten
cortex lan --subnet 10.0.0.0/24
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
  Fremde Adressen lehnt cortex ab, in jeder Schreibweise. Höchstens 512 Adressen am
  Stück, ein `/16` also nicht.
* **Nur die Frage „antwortet da etwas"** — cortex klopft an, liest den Titel einer
  Weboberfläche und geht weiter. Keine Passwortversuche, keine Schwachstellensuche.
  Ein Gerät, das nicht antwortet, heißt „nicht erreichbar", nie „existiert nicht": es
  kann auch schlafen.

Abschalten: `CORTEX_LAN_ENABLED=false` oder der Haken in den Einstellungen.

### Home Assistant

**cortex bringt kein Home Assistant mit und startet keins.** Er sucht das, das bei dir
schon läuft, und meldet sich dort mit einem langlebigen Zugriffstoken an — genau wie
jede andere App, der du Zugriff gibst. Deine Installation bleibt unangetastet; cortex
ist nur ein weiterer Client.

```bash
cortex connect-ha
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
findet die Instanz, „Testen" prüft das Token und sagt dir, wie viele Geräte cortex
sieht.

**Schalten ist standardmäßig aus.** Ohne Haken sieht cortex nur nach. Mit Haken darf er
Licht, Steckdosen, Szenen und Medien bedienen — und selbst dann fragt er bei
**Schlössern, Alarmanlagen, Toren, Rollläden, Heizung und Saugrobotern** jedes Mal
nach, bevor er etwas tut. Ein missverstandener Halbsatz soll nicht die Haustür
aufschließen. Bereiche außerhalb der Liste (etwa `shell_command`) schaltet er
grundsätzlich nicht.

Das Token steht in deiner `.env` und wird nie an den Browser geschickt — die Oberfläche
erfährt nur, *ob* eines gesetzt ist. Zurücknehmen kannst du es jederzeit in Home
Assistant selbst: dasselbe Menü, Token löschen, fertig.

### Im Container: die Netzwerkkarte des Rechners

Läuft cortex im Container, hängt er in Dockers eigenem Brücken-Netz — von dort ist dein
Heimnetz **nicht** zu sehen, `cortex lan` und `connect-ha` fänden schlicht nichts.
Deshalb:

```bash
./cortex-box --lan                              # Wrapper, setzt es selbst
CORTEX_NETWORK=host docker compose run --rm cortex
docker run --network host ...                   # ohne Compose
```

Merkt cortex, dass er im Container nur das Container-Netz sieht, sagt er es von sich aus,
statt dich rätseln zu lassen.

## Ortsfilter

Nennt man eine Stadt, Region oder ein Land, baut der Agent das in die Suchanfragen ein
**und** setzt zusätzlich die Länder- und Sprachparameter der Such-API. Treffer, die
offensichtlich außerhalb liegen, sortiert er aus. Zusätzlich vorgebbar per Flag oder
Slash-Befehl:

```bash
cortex --location "Mönchengladbach" --lang de
```
```
/location Köln
```

## Bild als Eingabe

```bash
cortex --image foto.jpg              # eine Datei
cortex --image ~/hallo1234           # ein Ordner -- cortex sucht das Bild darin
cortex --image ~/hallo1234 "wo kann ich das kaufen?"
```

Zeigt der Pfad auf einen **Ordner**, nimmt cortex das einzige Bild darin; sind es mehrere,
listet er sie auf (neueste zuerst) und fragt, welches gemeint ist.

Ein Vision-Modell beschreibt, was auf dem Bild zu sehen ist (Produkt, Logo, Schild,
Text), daraus werden Suchbegriffe — danach läuft die normale Recherche. Im Chat geht
dasselbe mit `/image pfad.jpg`.

Es wird nichts hochgeladen: cortex liest die Datei von deiner Platte. Zuständig ist
`CORTEX_VISION_MODEL`; ist das leer, wird das Hauptmodell gefragt — und **Textmodelle
können keine Bilder sehen**. Ein lokales Vision-Modell richtest du so ein:

```bash
cortex install-model --vision-only
```

Welches Modell gerade zuständig ist, zeigt `cortex config` in der Zeile „Vision-Modell".

## Im Container laufen lassen

Wer cortex nicht direkt aufs System installieren will, lässt es in einem Container
laufen. Der isoliert das **Dateisystem**, nicht die Verbindung: das Netz bleibt
uneingeschränkt offen, sonst könnte der Agent nicht recherchieren.

```bash
./cortex-box --setup            # einmalig: fragt Modell und Key ab, schreibt ./.env
./cortex-box                    # Chat
./cortex-box "deine Frage"      # einmalige Recherche
./cortex-box search "test"      # nur die Suche, ohne LLM
```

Oder direkt mit Compose, ohne den Wrapper:

```bash
docker compose run --rm cortex                 # Chat
docker compose run --rm cortex "deine Frage"
docker compose build cortex                    # nach Codeänderungen
```

### Was der Container sieht — und was nicht

| | |
|---|---|
| **Netz** | vollständig offen, keine Einschränkung — nötig für Suche und Seitenabruf |
| **Dateisystem** | nur `/data` (Cache + Verlauf, Docker-Volume) und `/work` (→ `./exports`) |
| **Benutzer** | nicht `root`, sondern `cortex` (UID 1000) |
| **Rechte** | `no-new-privileges`, keine Zugriffe aufs Home-Verzeichnis des Hosts |
| **Keys** | kommen aus `./.env`, werden als Umgebungsvariablen hineingereicht |

Exporte (`/export html`) landen in `./exports` und sind damit direkt auf dem Host
lesbar. Cache und Verlauf überleben im Volume `cortex-data`.

### Zwei Varianten des Images

```bash
docker compose build cortex                                  # mit Chromium (Default)
CORTEX_IMAGE_TARGET=slim docker compose build cortex         # ohne, ~700 MB kleiner
```

Das `browser`-Image bringt Chromium für den JavaScript-Fallback (Stufe 3) mit. Darin
läuft Chromium ohne seine eigene Sandbox (`CORTEX_BROWSER_NO_SANDBOX=1`) — die Isolation
übernimmt der Container. Bei einer normalen Installation aufs System bleibt die
Browser-Sandbox aktiv.

### Alles im Haus: mit eigener Suchmaschine

Zusammen mit SearXNG geht auch die Suche über keinen fremden Dienst mehr:

```bash
docker compose --profile searxng up -d searxng
echo 'CORTEX_SEARCH_BACKEND=searxng' >> .env
docker compose --profile searxng run --rm cortex
```

Die mitgelieferte `docker/searxng/settings.yml` hat die JSON-Ausgabe bereits aktiviert.
Ersetze darin vor dem ersten Start den `secret_key` durch etwas Eigenes
(`openssl rand -hex 32`).

## Suchmaschine — ohne API-Key

Die Suche kostet nichts und braucht **kein Konto**. Standard ist eine offene Metasuche:
`cortex` fragt über [`ddgs`](https://pypi.org/project/ddgs/) mehrere freie Suchmaschinen
per HTML ab und mischt die Treffer. Fällt eine aus (Rate-Limit, Umbau), übernehmen die
anderen — genau deshalb ist die Metasuche robuster als eine einzelne Engine.

### Wie gesucht wird: mehrere Formulierungen statt einer

Eine einzige Formulierung findet nur, was zufällig genau so im Netz steht. Cortex AI
stellt dieselbe Frage deshalb mehrfach anders und führt die Trefferlisten zusammen —
in der Fachsprache *query fan-out* mit *Reciprocal Rank Fusion*. Drei Regeln aus der
Literatur stecken darin:

1. **Kurze Stichwortanfragen schlagen ganze Sätze.** Aus „Wie viel kostet ein
   gebrauchtes Lastenrad in Bremen?" wird zusätzlich „kostet gebrauchtes Lastenrad
   Bremen". Drei bis sechs inhaltstragende Wörter, das Hauptthema in jeder Variante.
2. **Zwei bis drei Formulierungen, nicht mehr.** Ab der vierten nehmen die Treffer
   nicht mehr zu, nur noch die Streuung. `CORTEX_SEARCH_VARIANTS=1` schaltet es ab.
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
CORTEX_SEARCH_BACKEND=duckduckgo

# gezielt einschränken, wenn eine Engine bei dir zickt
CORTEX_SEARCH_ENGINES=duckduckgo,mojeek
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
CORTEX_SEARCH_BACKEND=searxng
CORTEX_SEARXNG_URL=http://localhost:8080
```

Öffentliche SearXNG-Instanzen funktionieren auch, haben die JSON-Ausgabe aber oft
abgeschaltet.

### Kommerzielle APIs (optional)

[Brave Search](https://brave.com/search/api/) und [Tavily](https://app.tavily.com/home)
sind eingebaut, brauchen aber einen Key. Nur sinnvoll, wenn dir die offenen Engines nicht
zuverlässig genug sind:

```bash
CORTEX_SEARCH_BACKEND=brave
BRAVE_API_KEY=...
```

Testen lässt sich jedes Backend ohne LLM:

```bash
cortex search "cafés mönchengladbach" -n 5
```

## Tempo

Zwei Dinge bestimmen, wie lange eine Antwort dauert: wie oft Cortex das Modell fragen
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
* **Die Vorrecherche ist der größte Posten.** Sie zerlegt die Frage und lässt die Teile
  parallel recherchieren; das ist gründlicher, verdoppelt aber die Wartezeit. Wer es
  eilig hat, schaltet sie in den Einstellungen unter *Subagenten* ab oder setzt
  `CORTEX_SUBAGENTS_AUTO=false`.

Bleibt es zäh, liegt es am Modell, nicht am Weg dorthin: ein kleineres Modell desselben
Anbieters oder ein lokales über `cortex install-model` ist dann der wirksamste Hebel.

## Stabilität

Lokale Modelle scheitern anders als Cloud-Modelle. cortex fängt die drei häufigsten Fälle
ab:

* **Kontextüberlauf — die häufigste Ursache für „er vergisst die letzte Frage".**
  Läuft das Fenster über, wirft der Anbieter *still* den **Anfang** weg: erst den
  Systemprompt, dann die früheren Fragen. Das Gespräch wirkt dann wie zurückgesetzt.
  cortex beugt zweifach vor:

  1. **Ein ausreichend großes Fenster anfordern.** Ollama nimmt sonst seinen Default von
     2048–4096 Token — nach einer recherchierten Antwort ist der schon voll. cortex
     schickt `num_ctx` mit (`CORTEX_CONTEXT_TOKENS`, Default 16384). Bei Cloud-Anbietern
     entfällt das, die kennen den Parameter nicht.
  2. **Selbst kürzen statt gekürzt werden — und zwar in der richtigen Reihenfolge.**
     Geopfert wird von hinten nach vorn nach Wert: zuerst ältere Werkzeug-Ausgaben →
     Platzhalter (`CORTEX_KEEP_FULL_RESULTS`, Default 4), dann ältere Vorrecherche-Blöcke
     (die wiederholten sich sonst jeden Turn), dann die verbliebenen Suchergebnisse, dann
     ältere Antworten — und **erst ganz zuletzt die Fragen des Nutzers**. Ein Suchergebnis
     von vorletzter Runde ist ersetzbar, deine Frage nicht: die steht nirgendwo sonst.
     Systemprompt und aktuelle Frage bleiben immer stehen, und Werkzeugaufrufe werden nie
     von ihren Antworten getrennt.
  3. **Kein einzelner Brocken darf das Fenster auffressen.** Ein Suchergebnis oder eine
     angehängte Datei bekommt höchstens gut ein Drittel des Budgets — sonst passt bei
     einem kleinen Fenster schon ein einziges Ergebnis samt Systemprompt nicht mehr hinein,
     und dem Kürzen bliebe nur noch das Gespräch selbst. Obergrenze ist zusätzlich
     `CORTEX_MAX_TOOL_CHARS` (Default 8000).

  Passt dein Modell mehr, dreh auf — `gemma4:12b` kann 128k, kostet aber VRAM:

  ```bash
  CORTEX_CONTEXT_TOKENS=32768
  ```
* **Abgestürzter Runner.** Bei `model runner has unexpectedly stopped` entlädt cortex alle
  Modelle und versucht es erneut, statt den Durchlauf zu verlieren.
* **Wackelige Verbindung.** Timeouts, 502/503 und Rate-Limits werden bis zu
  `CORTEX_LLM_RETRIES` mal wiederholt (Default 3, mit wachsender Wartezeit). Ein falscher
  API-Key wird *nicht* wiederholt — das würde nur Zeit kosten.

Dazu: Ein Werkzeug, das eine Ausnahme wirft, beendet den Durchlauf nicht mehr, sondern
meldet den Fehler an das Modell, das dann eine andere Quelle nimmt.

## Verhalten beim Seitenabruf

* `robots.txt` wird respektiert (einmal je Origin geholt und zwischengespeichert)
* ehrlicher User-Agent, der cortex benennt
* maximal 1 Request pro Sekunde und Domain, Timeout 15 s
* bei Fehler oder Blockade: überspringen und mit dem nächsten Treffer weitermachen,
  nicht abbrechen
* kein Umgehen von Logins, Paywalls oder Captchas — ist eine Seite nicht öffentlich
  lesbar, wird sie ausgelassen und im Ergebnis als „nicht öffentlich zugänglich" vermerkt

## Cookie-Banner und Pop-ups

Der Punkt, an dem die meisten simplen Crawler scheitern: Statt des Seiteninhalts wird der
Text des Cookie-Dialogs extrahiert. cortex löst das in drei Stufen.

**Stufe 1 — gar nicht erst hinklicken (Standardfall).** `fetch_page` holt reines HTML
ohne JavaScript-Ausführung. Consent-Banner sind dann meist nur inaktive DOM-Knoten oder
werden gar nicht erst eingebaut. Vor der Textextraktion fliegen sie per Selektor-Blockliste
raus (`cortex/selectors.yaml`, ohne Codeänderung erweiterbar), danach übernimmt
trafilatura die restliche Boilerplate-Entfernung. Absätze, die im Wesentlichen aus
Consent-Formulierungen bestehen, werden zusätzlich aus dem Text gestrichen.

**Stufe 2 — erkennen, ob es geklappt hat.** Bleiben nach der Extraktion weniger als
~200 Zeichen übrig und stehen typische Consent-Marker im HTML, gilt der Abruf als
gescheitert. Unterschieden wird zwischen `blocked`, `consent_required`, `paywall` und
`empty` — jeder Grund wird dem Agenten gemeldet, damit er eine andere Quelle nimmt.

**Stufe 3 — Playwright-Fallback.** Optionale Abhängigkeit, nur für Seiten, die ohne
JavaScript nichts liefern:

```bash
uv tool install --with playwright cortex
cortex install-browser
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
HTTP-Abrufe aggressiv (403, Captcha-Seite). cortex baut dafür **keine Umgehung**, sondern
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
| `CORTEX_MODEL` | LiteLLM-Modell-ID | `anthropic/claude-sonnet-4-6` |
| `CORTEX_VISION_MODEL` | Modell für `--image` | wie `CORTEX_MODEL` |
| `CORTEX_API_BASE` | eigene Basis-URL (Ollama, eigene NIM, Proxy) | — |
| `CORTEX_API_KEY` | Key für Anbieter ohne eigenen Eintrag | — |
| `CORTEX_SEARCH_BACKEND` | `duckduckgo`, `searxng`, `brave`, `tavily` | `duckduckgo` |
| `CORTEX_SEARCH_ENGINES` | Engines der Metasuche einschränken | alle |
| `CORTEX_SEARCH_VARIANTS` | Formulierungen je Suche (`1` = aus) | `3` |
| `CORTEX_SEARXNG_URL` | Adresse der SearXNG-Instanz | — |
| `CORTEX_LOCATION` | Standard-Ortsfilter | — |
| `CORTEX_LANG` / `CORTEX_COUNTRY` | Sprache / Land der Suche | `de` / `de` |
| `CORTEX_MAX_TOOL_CALLS` | Werkzeug-Budget je Anfrage | `20` |
| `CORTEX_MAX_SUBAGENTS` | Subagenten je Anfrage (`0` = aus) | `12` |
| `CORTEX_SUBAGENTS_AUTO` | jede Anfrage automatisch zerlegen | `true` |
| `CORTEX_TRIAGE_TIMEOUT` | Zeitlimit der Small-Talk-Heuristik (s) | `5` |
| `CORTEX_PLANNER_TIMEOUT` | Zeitlimit für Prüfung + Planung (s) | `20` |
| `CORTEX_CONTEXT_TOKENS` | Kontextfenster für lokale Modelle (`0` = Ollama-Default) | `16384` |
| `CORTEX_SUBAGENT_MODEL` | leichtes Modell für die Subagenten | Hauptmodell |
| `CORTEX_SUBAGENT_BUDGET` | Werkzeug-Budget je Subagent | `6` |
| `CORTEX_SUBAGENT_PARALLEL` | gleichzeitige Subagenten (`0` = automatisch) | lokal `2`, Cloud: alle |
| `CORTEX_LLM_RETRIES` | Versuche bei transienten Fehlern | `3` |
| `CORTEX_MAX_TOOL_CHARS` | Zeichen je Werkzeug-Ergebnis | `8000` |
| `CORTEX_KEEP_FULL_RESULTS` | ungekürzte Ergebnisse im Verlauf | `4` |
| `CORTEX_FETCH_TIMEOUT` | Timeout je Seitenabruf (s) | `15` |
| `CORTEX_CACHE_TTL_HOURS` | Gültigkeit des Response-Cache | `24` |
| `CORTEX_ENABLE_PLAYWRIGHT` | Stufe-3-Fallback erlauben | `true` |
| `CORTEX_GOOGLE` | Gmail und Kalender lesen dürfen | `false` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | eigene Google-Anwendung | — |

### Ganz ohne API-Key: lokales Modell

Ein Befehl, und cortex richtet sich ein Modell auf deinem Rechner ein:

```bash
cortex install-model
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
CORTEX_MODEL=ollama_chat/qwen2.5:7b
CORTEX_API_BASE=http://localhost:11434
```

Zum Schluss fragt er, ob du **auch Bilder** als Eingabe nutzen willst, und richtet dafür
ein Vision-Modell ein — mit eigenem Sehtest: Das Modell bekommt ein rotes Quadrat gezeigt
und muss die Farbe nennen. Ein Textmodell fällt dabei durch und wird nicht eingetragen.

Ein bestimmtes Modell direkt:

```bash
cortex install-model --model qwen2.5:14b                  # nur Text
cortex install-model --vision-model llava:7b              # Text + Bild
cortex install-model --vision-only --vision-model llava:7b  # nur Bild nachrüsten
cortex install-model --model qwen2.5:7b --no-vision       # ohne Bild
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
> Werkzeuge aufrufen** — als Hauptmodell ist es damit unbrauchbar, cortex führt es
> deshalb nur bei den Vision-Modellen. Erst `gemma4` bringt beides mit.

cortex liest den VRAM per `nvidia-smi` aus und schlägt danach vor — ohne GPU rechnet er
mit 70 % des Arbeitsspeichers, weil auf der CPU nicht alles nutzbar ist. Passt ein
Modell, das beides kann, wird kein zweites geladen.

**Die Hardware empfiehlt, sie entscheidet nicht.** Die Liste zeigt immer alle Modelle,
und du kannst jedes davon wählen — oder einen beliebigen Namen aus dem Ollama-Katalog
eintippen. Passt eines rechnerisch nicht, sagt cortex das als Hinweis und lädt es
trotzdem: Ein Modell läuft notfalls teilweise auf der CPU, das ist langsam, aber deine
Entscheidung.

Jedes andere Ollama-Modell mit Werkzeug-Unterstützung geht auch — `--model` nimmt jeden
Namen aus dem [Ollama-Katalog](https://ollama.com/library).

> **Das Präfix muss `ollama_chat/` lauten, nicht `ollama/`.** Nur ersteres reicht
> Werkzeuge durch; mit `ollama/` bleibt der Agent stumm. `cortex install-model` schreibt
> automatisch das richtige.

#### Wenn der Speicher knapp wird

Text- und Vision-Modell gleichzeitig im VRAM sprengen viele Grafikkarten — der
Ollama-Runner stirbt dann mit `model runner has unexpectedly stopped`. cortex entlädt
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
cortex --model openai/gpt-4o
cortex --model nvidia_nim/meta/llama-3.3-70b-instruct   # NVIDIA NIM
cortex --model ollama_chat/qwen2.5:7b                   # lokal, siehe oben
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
dem Web, was genau das ist, was cortex vermeiden soll.

Jeden weiteren LiteLLM-Anbieter nutzt du über den Notausgang `CORTEX_API_KEY`:

```bash
CORTEX_MODEL=irgendein_anbieter/modell
CORTEX_API_KEY=dein-key
```

#### NVIDIA NIM im Detail

[build.nvidia.com](https://build.nvidia.com/) gibt dir nach der Anmeldung Startguthaben
und einen Key (`nvapi-...`). Modell auswählen, „Get API Key" klicken, dann:

```bash
CORTEX_MODEL=nvidia_nim/meta/llama-3.3-70b-instruct
NVIDIA_NIM_API_KEY=nvapi-...
```

Die Modell-ID ist genau die von build.nvidia.com, mit `nvidia_nim/` davor. Vergisst du
das Kürzel, ergänzt cortex es selbst — kopierst du `nvidia/nemotron-3-ultra-550b-a55b`
von der Seite, wird daraus beim Speichern `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b`.
Am schnellsten geht es in der Weboberfläche: *Einstellungen → Modell → Anbieter: NVIDIA
NIM*, Schlüssel einfügen, **Modell speichern**. Mehr braucht es nicht.

Achte darauf, ein Modell zu wählen, das in der Modellkarte Tool-Calling aufführt — nicht
alle dort angebotenen Modelle können das. Eine eigene, selbst gehostete NIM-Instanz
erreichst du über `CORTEX_API_BASE=http://dein-host:8000/v1`.

### Wenn nach dem Anbieterwechsel „404 page not found" kommt

Der Klassiker: In der `.env` steht noch `CORTEX_API_BASE=http://localhost:11434` vom
lokalen Modell, das Modell zeigt aber längst zu NVIDIA oder Anthropic. Die Anfrage geht
dann an Ollama statt an den Anbieter, und Ollama antwortet mit genau diesem Satz.

cortex lässt eine Basis-URL auf dem Ollama-Port (11434) deshalb weg, sobald das Modell
zu einem anderen Anbieter gehört. Jede andere Adresse bleibt stehen — ein LiteLLM-Proxy
oder ein eigenes NIM im Heimnetz ist ein völlig legitimer Weg zu einem Cloud-Modell und
wird nicht angefasst.

SQLite (`~/.cortex/cortex.sqlite3`) wird für genau zwei Dinge benutzt: Response-Cache
(TTL 24 h) und Verlauf vergangener Recherchen.

## Aufbau

```
cortex/
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
compose.yaml     # cortex plus optionales SearXNG
cortex-box       # Wrapper: ./cortex-box "deine Frage"
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
