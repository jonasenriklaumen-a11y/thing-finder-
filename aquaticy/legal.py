"""Transparente Rechtstexte fuer die selbst gehostete Weboberflaeche.

Die Seiten beschreiben nur Verhalten, das im Code nachpruefbar ist. Angaben
zum Betreiber kommen aus der Serverumgebung; Aquaticy erfindet weder eine
Firma noch eine Adresse, wenn der Betreiber sie nicht hinterlegt hat.
"""

from __future__ import annotations

import os
from html import escape

LEGAL_VERSION = "2026-09-09"
LEGAL_ROUTES = ("/privacy", "/cookies", "/terms", "/accessibility")


def _operator_details() -> str:
    name = os.environ.get("AQUATICY_OPERATOR_NAME", "").strip()
    email = os.environ.get("AQUATICY_OPERATOR_EMAIL", "").strip()
    address = os.environ.get("AQUATICY_OPERATOR_ADDRESS", "").strip()
    if not any((name, email, address)):
        return (
            "<p><strong>Betreiber dieser Installation:</strong> noch nicht hinterlegt. "
            "Bitte frage die Person oder Organisation, von der du die Webadresse erhalten "
            "hast. Betreiber sollten Name, Kontakt und gegebenenfalls Anschrift über die "
            "AQUATICY_OPERATOR_-Variablen eintragen.</p>"
        )
    rows = []
    if name:
        rows.append(f"<dt>Name</dt><dd>{escape(name)}</dd>")
    if email:
        rows.append(f"<dt>Kontakt</dt><dd>{escape(email)}</dd>")
    if address:
        rows.append(f"<dt>Anschrift</dt><dd>{escape(address)}</dd>")
    return "<h2>Betreiber dieser Installation</h2><dl>" + "".join(rows) + "</dl>"


def _privacy() -> str:
    return f"""
<h1>Datenschutz</h1>
<p class="lead">Aquaticy ist eine selbst gehostete Web-App. Der Betreiber dieser
Installation entscheidet, wo sie läuft und welche optionalen Dienste eingeschaltet sind.</p>
{_operator_details()}
<h2>Welche Daten Aquaticy verarbeitet</h2>
<ul>
  <li><strong>Konto:</strong> E-Mail-Adresse, Tarif und Erstellungszeit. Das Passwort wird
    mit scrypt, einem eigenen Salz und einem geheimen Serverwert gehasht; Klartextpasswörter
    werden nicht gespeichert.</li>
  <li><strong>Sitzung:</strong> zufällige Sitzungsschlüssel sowie Hashes aus IP-Adresse und
    Browserangaben. Diese Werte schützen die Anmeldung und werden nicht für Werbung oder
    Geräteprofile verwendet.</li>
  <li><strong>Eigene Inhalte:</strong> Chats, Einstellungen, Aufträge, Uploads und – falls
    eingeschaltet – gespeicherte Erinnerungen. Jeder Account hat einen getrennten Ordner.</li>
  <li><strong>Nutzung:</strong> verbrauchte Token und belegter Speicher, damit Limits und die
    lokale Verwaltung funktionieren.</li>
</ul>
<h2>Externe Dienste</h2>
<p>Eine Frage kann an den ausgewählten Modellanbieter gehen. Suchbegriffe können an das
gewählte Suchsystem gehen; Ortsanfragen nutzen OpenStreetMap-Dienste. Google, Home Assistant,
LAN-Suche, Lager und die Werkstatt laufen nur, wenn der Betreiber oder Nutzer sie einschaltet.
Die Einstellungsseite zeigt die aktive Auswahl. Für externe Anbieter gelten zusätzlich deren
eigene Datenschutzhinweise.</p>
<p>Aquaticy enthält keine Werbe-, Analyse- oder Tracking-SDKs und verkauft keine Nutzerdaten.</p>
<h2>Speicherdauer und Kontrolle</h2>
<p>Sitzungen laufen nach 30 Tagen ab. Alte Werkstätten werden nach ihrer Leerlaufzeit entfernt.
Chats, Uploads, Erinnerungen und Aufträge bleiben bis zum Löschen durch den Nutzer oder
Betreiber erhalten. In der Web-App kannst du Chats einzeln entfernen, Uploads leeren und
Erinnerungen einsehen oder löschen. Für Auskunft oder die vollständige Löschung des Kontos
wende dich an den Betreiber der Installation.</p>
"""


def _cookies() -> str:
    return """
<h1>Cookie-Richtlinie</h1>
<p class="lead">Aquaticy setzt nur technisch notwendige, nicht aus JavaScript lesbare
Cookies. Es gibt keine Werbe- oder Analyse-Cookies.</p>
<table><thead><tr><th>Cookie</th><th>Zweck</th><th>Dauer</th></tr></thead><tbody>
<tr><td><code>aquaticy_consent</code></td><td>Merkt, dass der notwendige Betrieb erklärt
und bestätigt wurde.</td><td>1 Jahr</td></tr>
<tr><td><code>aquaticy_session</code></td><td>Ordnet den Browser nach der Anmeldung sicher
dem Konto zu.</td><td>30 Tage</td></tr>
<tr><td><code>aquaticy_token</code></td><td>Schützt eine im Netzwerk freigegebene
Installation mit dem Zugangswort des Servers. Wird nur gesetzt, wenn dieser Schutz aktiv ist.</td>
<td>30 Tage</td></tr>
</tbody></table>
<p>Alle Cookies verwenden <code>HttpOnly</code> und <code>SameSite=Strict</code>. Über HTTPS
setzt Aquaticy zusätzlich <code>Secure</code>. Wenn du die notwendigen Cookies ablehnst,
erstellt Aquaticy kein Konto. Ohne sie kann die Mehrbenutzer-Web-App Anmeldungen nicht sicher
auseinanderhalten.</p>
"""


def _terms() -> str:
    return f"""
<h1>Nutzungsbedingungen</h1>
<p class="lead">Diese Bedingungen gelten für die Aquaticy-Web-App in der Version der
Rechtstexte vom {LEGAL_VERSION}.</p>
{_operator_details()}
<h2>Nutzung und Verantwortung</h2>
<p>Aquaticy recherchiert, fasst Quellen zusammen und kann – nach Freigabe – lokale Werkzeuge
verwenden. KI-Antworten und externe Quellen können falsch, unvollständig oder veraltet sein.
Prüfe wichtige Angaben und Aktionen, bevor du dich darauf verlässt. Nutze die Software nicht,
um Rechte anderer zu verletzen oder unbefugt auf Systeme und Daten zuzugreifen.</p>
<h2>Preise, Gebühren und Erstattung</h2>
<p>Die Aquaticy-Software selbst nimmt keine Zahlungen an, erhebt keine versteckten Gebühren
und wickelt keine Erstattungen ab. Falls der Betreiber dieser Installation Zugang verkauft,
muss er Preis, Laufzeit, Kündigung und Erstattungsregeln vor dem Kauf gesondert und klar
mitteilen. Solche Vereinbarungen bestehen dann zwischen dir und diesem Betreiber.</p>
<h2>Bewertungen und geschäftliche Angaben</h2>
<p>Aquaticy sammelt oder veröffentlicht keine Nutzerbewertungen. Rechercheergebnisse sollen
Behauptungen mit den gelesenen Quellen kennzeichnen. Betreiberangaben stehen oben; fehlende
Angaben werden ausdrücklich als nicht hinterlegt angezeigt.</p>
"""


def _accessibility() -> str:
    return f"""
<h1>Barrierefreiheit</h1>
<p class="lead">Aquaticy soll mit Tastatur, Vergrößerung und unterstützenden Technologien
bedienbar sein.</p>
<ul>
  <li>Interaktive Elemente sind per Tastatur erreichbar und haben sichtbare Fokusmarkierungen.</li>
  <li>Statusmeldungen und neue Chatantworten werden semantisch ausgezeichnet.</li>
  <li>Die Farbpaletten halten für normalen Text ein Kontrastverhältnis von mindestens
    4,5:1 ein.</li>
  <li>Die Oberfläche beachtet die Systemeinstellung für reduzierte Bewegung.</li>
  <li>Dekorative Grafiken sind für Screenreader ausgeblendet; informative Bilder brauchen
    eine Textalternative.</li>
</ul>
<p>Wenn du eine Barriere findest, melde sie bitte beim Betreiber dieser Installation oder im
<a href="https://github.com/jonasenriklaumen-a11y/thing-finder-">Projekt auf GitHub</a>.</p>
{_operator_details()}
"""


_CONTENT = {
    "/privacy": ("Datenschutz", _privacy),
    "/cookies": ("Cookies", _cookies),
    "/terms": ("Nutzungsbedingungen", _terms),
    "/accessibility": ("Barrierefreiheit", _accessibility),
}


def legal_page(route: str) -> bytes:
    """Erzeugt eine eigenständige Seite ohne Skripte oder externe Assets."""
    title, content = _CONTENT[route]
    html = f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>{title} · Aquaticy</title>
<style>
:root{{color-scheme:light dark;font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}}
body{{max-width:760px;margin:0 auto;padding:28px 20px 60px;background:#faf9f5;color:#26251f}}
a{{color:#2c6641}}a:focus-visible{{outline:3px solid #3d7d55;outline-offset:3px}}
nav{{display:flex;gap:8px 18px;flex-wrap:wrap;border-bottom:1px solid #d9d7cb;padding-bottom:18px}}
main{{padding-top:18px}}h1{{font-size:2rem;line-height:1.2}}h2{{margin-top:2rem;font-size:1.25rem}}
.lead{{font-size:1.08rem}}dt{{font-weight:700}}dd{{margin:0 0 .6rem}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #b8b5aa;padding:9px;text-align:left;vertical-align:top}}
code{{overflow-wrap:anywhere}}
@media(prefers-color-scheme:dark){{body{{background:#232320;color:#f2f1ea}}a{{color:#9fd0b1}}
nav{{border-color:#5b5952}}th,td{{border-color:#77746b}}}}
</style></head><body>
<nav aria-label="Rechtliches"><a href="/">Zurück zu Aquaticy</a><a href="/privacy">Datenschutz</a>
<a href="/cookies">Cookies</a><a href="/terms">Nutzungsbedingungen</a>
<a href="/accessibility">Barrierefreiheit</a></nav>
<main>{content()}</main><footer><p>Stand: {LEGAL_VERSION}</p></footer></body></html>"""
    return html.encode("utf-8")
