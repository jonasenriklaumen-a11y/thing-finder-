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
# 1. Installieren
uv tool install scoutr

# 2. Einrichten -- fragt nach Modell und API-Key, testet beide
scoutr setup

# 3. Loslegen
scoutr
```

`scoutr setup` fragt genau zwei Dinge ab:

| Was | Wo bekommt man es | Pflicht? |
|---|---|---|
| LLM-Anbieter + Key | [Anthropic](https://console.anthropic.com/settings/keys) · [OpenAI](https://platform.openai.com/api-keys) · [Google](https://aistudio.google.com/app/apikey) · oder lokal per [Ollama](https://ollama.com) ganz ohne Key | ja |
| Suchmaschine | **DuckDuckGo** ist Standard und braucht **keinen Key**. Optional [Brave Search](https://brave.com/search/api/) oder [Tavily](https://app.tavily.com/home) | nein |

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
scoutr --image foto.jpg
```

Ein Vision-Modell beschreibt, was auf dem Bild zu sehen ist (Produkt, Logo, Schild,
Text), daraus werden Suchbegriffe — danach läuft die normale Recherche. Im Chat geht
dasselbe mit `/image pfad.jpg`.

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
| `SCOUTR_API_BASE` | eigene Basis-URL (Ollama, Proxy) | — |
| `SCOUTR_SEARCH_BACKEND` | `duckduckgo`, `brave`, `tavily` | `duckduckgo` |
| `SCOUTR_LOCATION` | Standard-Ortsfilter | — |
| `SCOUTR_LANG` / `SCOUTR_COUNTRY` | Sprache / Land der Suche | `de` / `de` |
| `SCOUTR_MAX_TOOL_CALLS` | Werkzeug-Budget je Anfrage | `20` |
| `SCOUTR_FETCH_TIMEOUT` | Timeout je Seitenabruf (s) | `15` |
| `SCOUTR_CACHE_TTL_HOURS` | Gültigkeit des Response-Cache | `24` |
| `SCOUTR_ENABLE_PLAYWRIGHT` | Stufe-3-Fallback erlauben | `true` |

Da LiteLLM als LLM-Schicht dient, sind Anthropic, OpenAI und lokale Modelle per Ollama
austauschbar:

```bash
scoutr --model openai/gpt-4o
scoutr --model ollama/llama3.1          # dazu SCOUTR_API_BASE=http://localhost:11434
```

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
  cache.py       # SQLite-Cache und Verlauf
  config.py      # Settings aus .env
  selectors.yaml # Selektor- und Marker-Listen, ohne Code erweiterbar
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
