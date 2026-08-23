# scoutr

Ein KI-Agent für die Kommandozeile, mit dem man chatten kann und der eigenständig das
Internet durchsucht, die gefundenen Seiten liest und die Ergebnisse ausgewertet
zurückgibt — mit Quelle zu jeder Angabe.

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

# 3. Loslegen
scoutr
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

Der Agent hat **genau zwei Werkzeuge** und kombiniert sie selbstständig, so oft er will:

1. `web_search(query, count, country, lang)` — schickt eine Suchanfrage ans Web und
   bekommt Titel, URL und Snippet zurück.
2. `fetch_page(url)` — lädt eine Seite und gibt den lesbaren Textinhalt zurück
   (HTML-Ballast, Navigation und Werbung entfernt).

Mehr braucht es nicht. Ob Instagram, Amazon, ein Branchenbuch oder die Website eines
Ladens: Alles sind einfach Suchtreffer, die gelesen werden können. Es gibt bewusst keine
plattformspezifischen Scraper.

Pro Nutzeranfrage:

1. Das LLM überlegt, welche Suchanfragen sinnvoll sind, und formuliert **mehrere**
   Varianten — nicht nur eine.
2. Es sucht, sichtet die Treffer und entscheidet, welche Seiten sich zu lesen lohnen.
3. Es liest die relevanten Seiten und zieht die gewünschten Informationen heraus.
4. Es fasst zusammen, bewertet gegen die Kriterien des Nutzers und nennt zu jeder Angabe
   die Quelle.

**Grenzen:** maximal 20 Tool-Calls pro Anfrage, dann wird der Zwischenstand ausgegeben.
Was nicht gefunden wurde, wird als „nicht gefunden" gekennzeichnet — niemals geraten.

## Chat-Interface

| Befehl | Wirkung |
|---|---|
| `/location <ort>` | Ortsfilter setzen (ohne Argument: aufheben) |
| `/model <name>` | Modell wechseln, z. B. `openai/gpt-4o` |
| `/export html\|md\|csv` | Recherche dieser Sitzung speichern |
| `/image <pfad>` | Bild beschreiben lassen, danach damit recherchieren |
| `/history` | frühere Recherchen anzeigen |
| `/clear` | Gesprächsverlauf verwerfen |
| `/help` | Übersicht |
| `/quit` | beenden (auch <kbd>Strg</kbd>+<kbd>D</kbd>) |

Der Kontext bleibt über mehrere Turns erhalten, Nachfragen wie „nur die mit 4+ Sternen"
funktionieren also.

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
```

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

| Modell | ca. Größe | ab RAM |
|---|---|---|
| `qwen2.5:3b` | 1,9 GB | 4 GB |
| `qwen2.5:7b` | 4,7 GB | 8 GB |
| `llama3.1:8b` | 4,9 GB | 8 GB |
| `qwen2.5:14b` | 9,0 GB | 16 GB |
| `qwen2.5:32b` | 20,0 GB | 32 GB |

**Vision-Modelle** (für `--image` und `/image`) — sie brauchen *kein* Tool-Calling, sie
beschreiben nur; die Recherche danach macht das Hauptmodell:

| Modell | ca. Größe | ab RAM |
|---|---|---|
| `moondream` | 1,7 GB | 4 GB |
| `llava:7b` | 4,7 GB | 8 GB |
| `minicpm-v` | 5,5 GB | 8 GB |
| `llama3.2-vision:11b` | 7,9 GB | 12 GB |
| `llava:13b` | 8,0 GB | 12 GB |

Jedes andere Ollama-Modell mit Werkzeug-Unterstützung geht auch — `--model` nimmt jeden
Namen aus dem [Ollama-Katalog](https://ollama.com/library).

> **Das Präfix muss `ollama_chat/` lauten, nicht `ollama/`.** Nur ersteres reicht
> Werkzeuge durch; mit `ollama/` bleibt der Agent stumm. `scoutr install-model` schreibt
> automatisch das richtige.

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
  export.py      # HTML / Markdown / CSV
  local_model.py # lokales Modell per Ollama einrichten
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
