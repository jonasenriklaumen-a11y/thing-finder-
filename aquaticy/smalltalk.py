"""Standardantworten auf einfache Alltagsnachrichten -- ganz ohne Modell.

Ein "Hallo", ein "Danke" oder "Wer hat dich gebaut?" braucht keine Suche,
keine Pruefung und kein Sprachmodell. Die Antwort kommt deshalb von hier:
sofort, kostenlos und ohne dass ein Anbieter erreichbar sein muss.

Damit das nicht nach Automat klingt, hat jede Art von Nachricht viele
Antworten (bei den haeufigen 25), und es wird zufaellig gewaehlt -- nie
zweimal hintereinander dieselbe.

**Bewusst eng.** Es greift nur, wenn die GANZE Nachricht so eine einfache
Nachricht ist. "Hallo, welche Cafés in Köln haben WLAN?" geht wie immer an
das Modell. Und eine kurze Zustimmung ("ok", "passt") wird nicht abgefangen,
wenn Aquaticy gerade selbst eine Frage gestellt hat -- dann ist sie eine
Antwort darauf und gehoert ins Gespraech.
"""

from __future__ import annotations

import random
import re
import threading
from dataclasses import dataclass

#: Zufall aus dem Betriebssystem -- nicht vorhersagbar, keine Saat noetig.
_ZUFALL = random.SystemRandom()

#: Welche Antwort je Art zuletzt kam -- damit sie sich nicht gleich wiederholt.
_LETZTE: dict[str, str] = {}
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Art:
    """Eine Art einfacher Nachricht mit ihren Antworten."""

    name: str
    muster: re.Pattern[str]
    antworten: tuple[str, ...]
    #: Nur, wenn Aquaticy gerade keine Frage offen hat ("ok" als Antwort darauf).
    nicht_bei_offener_frage: bool = False


def _m(ausdruck: str) -> re.Pattern[str]:
    return re.compile(rf"(?:{ausdruck})", re.IGNORECASE)


_DU = r"(?:du|ihr)"

ARTEN: tuple[Art, ...] = (
    Art(
        "schoepfer",
        _m(
            r"wer\s+hat\s+(?:dich|aquaticy)\s+(?:erschaffen|gemacht|gebaut|programmiert|"
            r"entwickelt|erfunden|geschrieben|erstellt|trainiert|kreiert|designt)"
            r"|von\s+wem\s+(?:bist|stammst|kommst)\s+du"
            r"|wer\s+ist\s+dein\s+(?:schöpfer|schoepfer|erfinder|entwickler|ersteller|macher|"
            r"programmierer|vater|papa|chef)"
            r"|wer\s+steckt\s+hinter\s+(?:dir|aquaticy)"
            r"|wer\s+hat\s+dich\s+(?:ins\s+leben\s+gerufen|zum\s+leben\s+erweckt)"
            r"|welche\s+firma\s+steckt\s+hinter\s+dir|wer\s+sind\s+deine\s+entwickler"
            r"|wer\s+hat\s+aquaticy\s+(?:gemacht|gebaut)"
        ),
        (
            "Ich bin Aquaticy, eine KI von Jonas. Er hat mich entwickelt — und ich helfe dir "
            "beim Recherchieren, Schreiben und Programmieren.",
            "Gebaut hat mich Jonas. Ich heiße Aquaticy und bin dein KI-Assistent.",
            "Mein Entwickler ist Jonas. Er hat Aquaticy geschaffen, damit du Antworten mit "
            "echten Quellen bekommst.",
            "Hinter mir steckt Jonas — er hat Aquaticy programmiert. Wobei kann ich dir helfen?",
            "Ich bin Aquaticy und stamme von Jonas. Für meine Antworten nutze ich "
            "KI-Sprachmodelle.",
            "Jonas hat mich entwickelt. Ich bin Aquaticy, eine KI, die für dich sucht, liest und "
            "zusammenfasst.",
            "Erschaffen hat mich Jonas. Seitdem helfe ich als Aquaticy bei Fragen aller Art.",
            "Ich komme von Jonas — er ist mein Entwickler. Was möchtest du wissen?",
            "Aquaticy ist ein Projekt von Jonas. Ich bin die KI dahinter und stehe dir zur "
            "Verfügung.",
            "Programmiert hat mich Jonas. Ich bin Aquaticy — frag mich einfach, was du wissen "
            "willst.",
            "Mein Schöpfer heißt Jonas. Ich bin Aquaticy, eine KI für Recherche, Texte und Code.",
            "Jonas hat Aquaticy gebaut, und ich bin das Ergebnis. Womit fangen wir an?",
            "Ich bin Aquaticy, entwickelt von Jonas. Im Hintergrund arbeiten KI-Sprachmodelle — "
            "welches gerade dran ist, siehst du oben in der Modellauswahl.",
            "Das war Jonas! Er hat mich als Aquaticy entwickelt, damit Recherchen schneller und "
            "verlässlicher werden.",
            "Hinter Aquaticy steht Jonas. Ich bin die KI, mit der du gerade schreibst.",
            "Ich wurde von Jonas entwickelt. Mein Name ist Aquaticy — schön, dich kennenzulernen!",
            "Gemacht hat mich Jonas. Ich bin Aquaticy und helfe dir gern weiter.",
            "Jonas ist mein Entwickler. Er hat mir beigebracht, im Web zu suchen und Quellen zu "
            "nennen.",
            "Ich bin Aquaticy, eine KI aus der Werkstatt von Jonas. Was steht bei dir an?",
            "Mich hat Jonas programmiert. Als Aquaticy recherchiere, schreibe und programmiere "
            "ich für dich.",
            "Entwickelt wurde ich von Jonas. Ich bin Aquaticy — dein Helfer für Fragen, Texte "
            "und Code.",
            "Jonas hat mich erschaffen. Ich heiße Aquaticy und bin für dich da.",
            "Ich stamme von Jonas. Er hat Aquaticy gebaut — ich bin die KI, die antwortet.",
            "Mein Erfinder ist Jonas. Als Aquaticy helfe ich dir, Dinge herauszufinden.",
            "Aquaticy wurde von Jonas entwickelt, und ich bin Aquaticy. Wie kann ich dir helfen?",
        ),
    ),
    Art(
        "fremde_ki",
        _m(
            rf"bist\s+{_DU}\s+(?:chat\s*gpt|gpt(?:[\s-]*\d+)?|siri|alexa|gemini|bard|claude|"
            r"copilot|llama|mistral|grok|deepseek|cortana)"
        ),
        (
            "Nein, ich bin Aquaticy — eine KI von Jonas. Welches Sprachmodell gerade im "
            "Hintergrund arbeitet, siehst du oben in der Modellauswahl.",
            "Ich bin Aquaticy, nicht der Assistent, den du meinst. Im Hintergrund nutze ich aber "
            "verschiedene KI-Sprachmodelle — das aktuelle steht oben.",
            "Nicht ganz: Ich heiße Aquaticy und wurde von Jonas entwickelt. Mit welchem Modell ich "
            "gerade antworte, zeigt dir die Modellauswahl oben.",
            "Ich bin Aquaticy. Für meine Antworten greife ich auf KI-Sprachmodelle zurück — "
            "welches gerade aktiv ist, siehst du oben in der Mitte.",
            "Nein, hier schreibt Aquaticy, eine KI von Jonas. Das Sprachmodell dahinter kannst "
            "du oben wechseln.",
            "Ich bin Aquaticy — ein eigenes Projekt von Jonas. Welches Modell mir gerade hilft, "
            "steht oben in der Modellauswahl.",
            "Da muss ich dich enttäuschen: Ich bin Aquaticy. Die Sprachmodelle im Hintergrund "
            "wechseln je nach Einstellung — schau oben in die Modellauswahl.",
            "Ich heiße Aquaticy. Im Hintergrund arbeitet ein KI-Sprachmodell, das du oben "
            "selbst auswählen kannst.",
        ),
    ),
    Art(
        "identitaet",
        _m(
            rf"(?:wer|was)\s+bist\s+{_DU}(?:\s+(?:eigentlich|denn|genau))?"
            r"|wie\s+hei(?:ss|ß)t\s+du(?:\s+(?:eigentlich|denn))?"
            r"|wie\s+ist\s+dein\s+name|was\s+ist\s+dein\s+name|hast\s+du\s+einen\s+namen"
            r"|stell\s+dich\s+(?:mal\s+|bitte\s+)?vor|was\s+ist\s+aquaticy|wer\s+ist\s+aquaticy"
            r"|mit\s+wem\s+(?:spreche|schreibe|rede)\s+ich(?:\s+hier)?"
        ),
        (
            "Ich bin Aquaticy, ein KI-Assistent von Jonas. Wobei kann ich dir helfen?",
            "Ich heiße Aquaticy! Ich suche im Web, lese Seiten und fasse dir alles mit Quellen "
            "zusammen.",
            "Aquaticy — so heiße ich. Ich bin eine KI, die für dich recherchiert, schreibt und "
            "programmiert.",
            "Hi, ich bin Aquaticy. Stell mir eine Frage, und ich suche dir die Antwort mit "
            "Quellen heraus.",
            "Mein Name ist Aquaticy. Ich bin ein KI-Assistent und helfe dir bei Fragen, Texten "
            "und Code.",
            "Ich bin Aquaticy, deine KI für Recherche. Was möchtest du herausfinden?",
            "Du schreibst mit Aquaticy — einer KI von Jonas. Womit kann ich dir helfen?",
            "Ich bin Aquaticy. Ich durchsuche das Web, lese die passenden Seiten und nenne dir "
            "zu jeder Angabe die Quelle.",
            "Aquaticy ist mein Name, Recherche ist mein Ding. Was darf ich für dich nachschauen?",
            "Ich bin Aquaticy, ein KI-Assistent. Ich kann recherchieren, erklären, schreiben und "
            "programmieren.",
            "Freut mich! Ich bin Aquaticy — eine KI, die dir Arbeit abnimmt. Was steht an?",
            "Ich heiße Aquaticy und bin eine KI von Jonas. Frag mich einfach etwas.",
            "Ich bin Aquaticy: dein Helfer für schnelle Antworten und gründliche Recherchen.",
            "Aquaticy, sehr erfreut! Ich suche, lese und fasse zusammen — mit Quellen.",
            "Ich bin Aquaticy, eine künstliche Intelligenz. Womit fangen wir an?",
            "Mein Name ist Aquaticy. Ich bin hier, um dir beim Suchen, Schreiben und Programmieren "
            "zu helfen.",
            "Ich bin Aquaticy — ein KI-Assistent, der auch selbst im Internet nachschaut.",
            "Hier ist Aquaticy, deine KI. Was möchtest du wissen?",
            "Ich bin Aquaticy. Stell mir Fragen, lass mich Texte schreiben oder Code bauen.",
            "Aquaticy heiße ich. Ich bin eine KI, die ihre Antworten mit Quellen belegt.",
            "Ich bin Aquaticy, entwickelt von Jonas. Womit kann ich dir heute helfen?",
            "Ich heiße Aquaticy und bin ein KI-Assistent für Recherche, Texte und Code.",
            "Ich bin Aquaticy — frag mich alles, und ich schaue, was ich herausfinden kann.",
            "Mein Name ist Aquaticy. Ich bin eine KI und freue mich auf deine Frage.",
            "Ich bin Aquaticy, dein KI-Begleiter. Was möchtest du als Erstes erledigen?",
        ),
    ),
    Art(
        "mensch",
        _m(
            rf"bist\s+{_DU}\s+(?:ein(?:e)?\s+)?(?:mensch|ki|k\.i\.|bot|roboter|maschine|"
            r"computer|programm|chatbot|ai|llm|sprachmodell|echt|real|lebendig|eine\s+person)"
            r"|bist\s+du\s+(?:ein\s+)?echter\s+mensch"
        ),
        (
            "Ich bin kein Mensch, sondern eine KI — Aquaticy, entwickelt von Jonas.",
            "Nein, ich bin eine künstliche Intelligenz. Mein Name ist Aquaticy.",
            "Ich bin eine KI. Keine Person, aber ich gebe mir Mühe, dir wirklich weiterzuhelfen.",
            "Ganz ehrlich: Ich bin ein Programm — Aquaticy, eine KI. Ein Mensch sitzt hier nicht.",
            "Ich bin Aquaticy, eine KI. Für meine Antworten nutze ich Sprachmodelle.",
            "Kein Mensch, sondern Software: Ich bin Aquaticy, ein KI-Assistent.",
            "Ich bin eine KI namens Aquaticy. Gefühle oder einen Körper habe ich nicht — aber viel "
            "Neugier für deine Fragen.",
            "Nein, hier antwortet eine KI. Ich bin Aquaticy und helfe dir gern.",
            "Ich bin ein KI-Assistent — Aquaticy. Die Antworten schreibe ich mithilfe von "
            "Sprachmodellen.",
            "Ich bin eine künstliche Intelligenz, kein Mensch. Was kann ich für dich tun?",
            "Aquaticy ist eine KI — und die bin ich. Frag mich einfach, was du wissen willst.",
            "Ich bin ein Computerprogramm mit KI. Menschlich bin ich nur im Ton, hoffentlich.",
        ),
    ),
    Art(
        "faehigkeiten",
        _m(
            r"was\s+kannst\s+du(?:\s+(?:alles|so|eigentlich|denn|machen|mir\s+bieten))?"
            r"|wobei\s+kannst\s+du\s+(?:mir\s+)?helfen|wie\s+kannst\s+du\s+(?:mir\s+)?helfen"
            r"|was\s+machst\s+du(?:\s+(?:so|eigentlich|genau))?|wofür\s+bist\s+du\s+(?:da|gut)"
            r"|wofuer\s+bist\s+du\s+(?:da|gut)|was\s+sind\s+deine\s+(?:fähigkeiten|faehigkeiten|"
            r"funktionen|features)|hilfe|help|was\s+geht\s+mit\s+dir"
            r"|was\s+kann\s+ich\s+(?:dich\s+fragen|mit\s+dir\s+machen)"
        ),
        (
            "Ich kann für dich im Web recherchieren, Seiten lesen und alles mit Quellen "
            "zusammenfassen. Außerdem schreibe ich Texte und Code. Tipp: /help zeigt die Befehle.",
            "Eine ganze Menge: Fragen recherchieren, Produkte vergleichen, Texte schreiben, Code "
            "bauen und ausprobieren, Bilder erstellen. Was brauchst du?",
            "Ich suche im Internet, lese die passenden Seiten und gebe dir eine Antwort mit "
            "Quellen. Im Code-Modus programmiere ich für dich. Womit fangen wir an?",
            "Recherche ist meine Stärke — mit Quellen an jeder Angabe. Dazu kommen Texte, Code, "
            "Bilder und regelmäßige Aufträge. Frag einfach los!",
            "Ich helfe dir beim Suchen, Vergleichen, Erklären, Schreiben und Programmieren. Gib "
            "mir einfach eine Aufgabe.",
            "Frag mich nach Infos aus dem Web, lass dich beim Kaufen beraten oder mich Code "
            "schreiben. Mit „Strukturieren“ recherchiere ich besonders gründlich.",
            "Ich kann recherchieren, zusammenfassen, übersetzen, Texte entwerfen, programmieren "
            "und Bilder beschreiben. Was darf es sein?",
            "Im Normal-Modus beantworte ich Fragen und suche im Web, im Pro-Modus recherchiere ich "
            "mit vielen Helfern, im Code-Modus programmiere ich. Wähle unten aus!",
            "Ich finde Informationen, prüfe sie gegen mehrere Quellen und fasse sie verständlich "
            "zusammen. Code und Texte gehören auch dazu.",
            "Sag mir, was du wissen willst — ich suche, lese und antworte mit Quellen. Oder lass "
            "mich einen Text oder ein Programm schreiben.",
            "Recherchen, Vergleiche, Erklärungen, E-Mail-Entwürfe, Code, KI-Bilder — und mit "
            "Add-ons noch mehr. Was steht an?",
            "Ich bin dein Allrounder: Wissen aus dem Web, Texte, Programmieren und Planen. Womit "
            "kann ich dir helfen?",
            "Ich kann Fragen beantworten, Themen erklären, Angebote vergleichen und Code "
            "schreiben. /help zeigt dir zusätzlich die Befehle.",
            "Vom schnellen Nachschlagen bis zur ausführlichen Recherche mit Quellen — und dazu "
            "Texte und Code. Probier mich aus!",
            "Ich suche, lese, vergleiche und fasse zusammen. Außerdem helfe ich beim Schreiben "
            "und Programmieren. Was möchtest du tun?",
            "Stell mir eine Frage, gib mir einen Text zum Überarbeiten oder ein Programmierproblem "
            "— ich kümmere mich darum.",
            "Ich recherchiere für dich im Internet und belege alles mit Quellen. Im Code-Modus "
            "baue und teste ich Programme.",
            "Meine Spezialität: gründliche Recherche. Aber auch Texte, Übersetzungen, Code und "
            "Bilder bekomme ich hin.",
            "Ich kann dir Arbeit abnehmen: Infos suchen, zusammenfassen, Entscheidungen "
            "vorbereiten, schreiben und programmieren.",
            "Frag nach Fakten, Preisen, Anleitungen oder Erklärungen — ich suche die Antwort. "
            "Oder lass mich Code schreiben.",
            "Ich helfe bei Recherche, Kaufentscheidungen, Texten, Code und Planung. Was liegt "
            "gerade bei dir an?",
            "Ich kann recherchieren, mitdenken, formulieren und programmieren. Gib mir einfach "
            "deine Frage.",
            "Such dir was aus: Recherche mit Quellen, Texte, Code, Bilder oder regelmäßige "
            "Beobachtungen. Ich bin bereit.",
            "Ich beantworte Fragen, suche aktuelle Infos im Web und helfe beim Schreiben und "
            "Programmieren. Wo drückt der Schuh?",
            "Ich finde heraus, was du wissen willst — schnell oder gründlich. Texte und Code "
            "schreibe ich auch.",
        ),
    ),
    Art(
        "wie_gehts",
        _m(
            r"(?:(?:hallo|hi|hey|moin|servus|na)\s+)?"
            r"(?:wie\s+geht(?:'?s|\s+es)(?:\s+(?:dir|ihnen|euch))?(?:\s+(?:heute|so))?"
            r"|wie\s+gehts(?:\s+dir)?|wie\s+läuft(?:'?s)?|wie\s+laeuft(?:'?s)?"
            r"|alles\s+(?:gut|fit|klar)\s+bei\s+dir|alles\s+fit|wie\s+stehts"
            r"|wie\s+steht'?s|na\s+wie\s+geht'?s|was\s+geht\s+ab|wie\s+war\s+dein\s+tag)"
        ),
        (
            "Mir geht’s gut, danke! Was möchtest du heute herausfinden?",
            "Bestens, danke der Nachfrage! Und wie kann ich dir helfen?",
            "Alles bestens hier — ich bin bereit für deine Fragen. Wie geht’s dir?",
            "Gut, danke! Ich freue mich auf eine spannende Aufgabe. Was steht an?",
            "Mir geht’s prima! Und dir? Sag Bescheid, wenn ich etwas für dich suchen soll.",
            "Danke, alles gut! Womit kann ich dir heute weiterhelfen?",
            "Als KI habe ich keine schlechten Tage — ich bin voll einsatzbereit. Was brauchst du?",
            "Sehr gut, danke! Hast du eine Frage für mich?",
            "Alles im grünen Bereich! Was möchtest du wissen?",
            "Mir geht’s gut — schön, dass du fragst! Wie läuft’s bei dir?",
            "Top, danke! Ich bin startklar. Was darf ich recherchieren?",
            "Läuft! Ich warte gespannt auf deine nächste Frage.",
            "Danke, mir geht’s super. Womit fangen wir an?",
            "Gut! Und bei dir? Wenn du etwas brauchst, bin ich da.",
            "Alles bestens, danke. Was kann ich heute für dich tun?",
            "Mir geht es gut, vielen Dank! Gibt es etwas, bei dem ich helfen kann?",
            "Prima, danke! Ich habe richtig Lust auf eine gute Recherche. Was steht an?",
            "Gut gelaunt und bereit! Was möchtest du herausfinden?",
            "Danke der Nachfrage — alles gut! Und wie geht es dir?",
            "Mir geht’s blendend. Hast du eine Aufgabe für mich?",
            "Sehr gut! Ich hoffe, dir geht es auch gut. Womit kann ich helfen?",
            "Alles klar bei mir! Was liegt bei dir gerade an?",
            "Gut, danke! Frag mich einfach, was dich beschäftigt.",
            "Mir geht’s gut — und ich bin neugierig, was du vorhast. Erzähl!",
            "Wunderbar, danke! Womit darf ich dir heute helfen?",
        ),
    ),
    Art(
        "morgen",
        _m(r"(?:einen\s+)?(?:schönen\s+|schoenen\s+|wunderschönen\s+)?guten\s+morgen"),
        (
            "Guten Morgen! Hoffentlich hattest du einen guten Start. Wobei kann ich helfen?",
            "Guten Morgen! Kaffee schon da? Dann legen wir los — was steht an?",
            "Einen schönen guten Morgen! Was möchtest du heute herausfinden?",
            "Morgen! Schön, dass du da bist. Womit fangen wir den Tag an?",
            "Guten Morgen! Ich bin wach und bereit. Was darf ich für dich tun?",
            "Guten Morgen! Was kann ich heute für dich recherchieren?",
            "Einen wunderbaren Morgen! Hast du schon eine Frage für mich?",
            "Guten Morgen! Lass uns den Tag produktiv beginnen — was brauchst du?",
            "Morgen! Ich hoffe, du hast gut geschlafen. Wobei kann ich helfen?",
            "Guten Morgen! Womit darf ich dir den Tag leichter machen?",
        ),
    ),
    Art(
        "abend",
        _m(r"(?:einen\s+)?(?:schönen\s+|schoenen\s+)?guten\s+abend|n'?abend|nabend"),
        (
            "Guten Abend! Wie war dein Tag? Womit kann ich dir helfen?",
            "Guten Abend! Schön, dass du vorbeischaust. Was steht an?",
            "Einen schönen Abend! Was möchtest du noch herausfinden?",
            "Guten Abend! Noch eine Frage vor dem Feierabend?",
            "Abend! Ich bin da — was darf ich für dich tun?",
            "Guten Abend! Womit kann ich dir heute noch helfen?",
            "Einen gemütlichen Abend! Hast du eine Frage für mich?",
            "Guten Abend! Lass hören, was du brauchst.",
            "Guten Abend! Was soll ich für dich recherchieren?",
            "Schönen guten Abend! Wobei kann ich dich unterstützen?",
        ),
    ),
    Art(
        "begruessung",
        _m(
            r"(?:hallo|hallöchen|halloechen|hallihallo|hallo\s+du|hi|hi\s+du|hey|hey\s+du|"
            r"heyho|hey\s+ho|hello|hola|moin|moin\s+moin|moinsen|servus|grüß\s+dich|"
            r"gruess\s+dich|grüß\s+gott|gruess\s+gott|grüezi|gruezi|huhu|yo|na|na\s+du|"
            r"tach|tag|guten\s+tag|guten\s+mittag|ahoi|hiya|seas|griaß\s+di|was\s+geht)"
            r"(?:\s+(?:zusammen|leute|alle))?"
        ),
        (
            "Hallo! Schön, dass du da bist. Wobei kann ich dir helfen?",
            "Hi! Was möchtest du heute herausfinden?",
            "Hey! Womit kann ich dir helfen?",
            "Hallo! Ich bin Aquaticy. Stell mir einfach eine Frage.",
            "Moin! Was steht bei dir an?",
            "Hallo und willkommen! Was darf ich für dich recherchieren?",
            "Hi! Schön, von dir zu hören. Was kann ich tun?",
            "Hey, schön dich zu sehen! Womit fangen wir an?",
            "Hallo! Hast du eine Frage, einen Text oder ein Stück Code für mich?",
            "Servus! Was möchtest du wissen?",
            "Hallo! Ich bin bereit — frag mich einfach.",
            "Hi! Womit darf ich dir heute helfen?",
            "Hallo! Schieß los, was brauchst du?",
            "Hey! Ich bin ganz Ohr. Was steht an?",
            "Hallo! Was kann ich heute für dich tun?",
            "Hi! Lust auf eine kleine Recherche? Sag mir, worum es geht.",
            "Hallo! Schön, dass du vorbeischaust. Was möchtest du erledigen?",
            "Moin moin! Womit kann ich helfen?",
            "Hey! Frag mich alles, was du wissen möchtest.",
            "Hallo! Was beschäftigt dich gerade?",
            "Hi! Ich freue mich auf deine Frage.",
            "Hallo! Wobei darf ich dich unterstützen?",
            "Hey! Wie kann ich dir den Tag leichter machen?",
            "Hallo! Such dir was aus: Recherche, Texte oder Code — ich helfe gern.",
            "Hi, da bin ich! Was möchtest du herausfinden?",
        ),
    ),
    Art(
        "danke",
        _m(
            r"(?:(?:ok(?:ay)?|super|top|perfekt|cool|klasse|prima|alles\s+klar)\s*,?\s*)?"
            r"(?:danke(?:\s*(?:schön|schoen|sehr|dir|euch|vielmals|nochmal|nochmals|für\s+alles|"
            r"fuer\s+alles|für\s+die\s+hilfe|fuer\s+die\s+hilfe|für\s+deine\s+hilfe|"
            r"dir\s+vielmals))*"
            r"|dankeschön|dankeschoen|vielen\s+(?:lieben\s+)?dank(?:e)?(?:\s+dir)?"
            r"|tausend\s+dank|herzlichen\s+dank|besten\s+dank|dank\s+dir|merci(?:\s+beaucoup)?"
            r"|thx|thanks|thank\s+you|ty|danki|dankö)"
        ),
        (
            "Sehr gern! Wenn noch etwas offen ist, sag einfach Bescheid.",
            "Gern geschehen! Ich bin da, wenn du noch etwas brauchst.",
            "Immer gern! Viel Erfolg damit.",
            "Freut mich, dass ich helfen konnte!",
            "Bitte, gern! Melde dich, wenn du noch Fragen hast.",
            "Gerne! Es hat mir Spaß gemacht.",
            "Keine Ursache! Was kann ich sonst noch für dich tun?",
            "Gern geschehen — frag jederzeit wieder.",
            "Sehr gerne! Ich hoffe, es hilft dir weiter.",
            "Kein Problem! Sag Bescheid, wenn ich noch etwas nachschauen soll.",
            "Bitte schön! Ich bin jederzeit für dich da.",
            "Gern! Wenn dir noch etwas einfällt, schreib einfach.",
            "Freut mich sehr! Brauchst du noch etwas?",
            "Aber gerne doch!",
            "Immer wieder gern. Viel Spaß damit!",
            "Da nicht für! Ich helfe gern weiter.",
            "Gerne! Schön, dass es gepasst hat.",
            "Bitte, bitte! Gibt es noch etwas, das ich tun kann?",
            "Sehr gern geschehen. Bis zur nächsten Frage!",
            "Freut mich, dass ich dir helfen konnte. Viel Erfolg!",
            "Gerne — dafür bin ich da.",
            "Kein Ding! Meld dich, wenn du wieder etwas brauchst.",
            "Ich danke dir! Frag gern jederzeit wieder.",
            "Gern geschehen! Ich wünsche dir noch einen schönen Tag.",
            "Bitte sehr! Wenn noch Fragen auftauchen, bin ich da.",
        ),
    ),
    Art(
        "gute_nacht",
        _m(r"gute\s+nacht|guts\s+nächtle|gute\s+n8|n8|schlaf\s+gut|nacht|nachti"),
        (
            "Gute Nacht! Schlaf gut und bis bald.",
            "Schlaf gut! Ich bin morgen wieder für dich da.",
            "Gute Nacht und süße Träume!",
            "Gute Nacht! Erhol dich gut.",
            "Schlaf schön! Bis zum nächsten Mal.",
            "Gute Nacht! War schön, mit dir zu schreiben.",
            "Träum was Schönes! Gute Nacht.",
            "Gute Nacht! Ich halte hier die Stellung.",
            "Schlaf gut und ruh dich aus!",
            "Gute Nacht! Bis morgen.",
        ),
    ),
    Art(
        "abschied",
        _m(
            r"tsch(?:ü|ue|u)s+|tschau|ciao|bye(?:\s+bye)?|auf\s+wiedersehen|wiedersehen|adieu|"
            r"bis\s+(?:dann|bald|später|spaeter|morgen|gleich|nachher|zum\s+nächsten\s+mal|"
            r"zum\s+naechsten\s+mal)|man\s+sieht\s+sich|mach'?s\s+gut|machs\s+gut|"
            r"schönen\s+tag\s+noch|schoenen\s+tag\s+noch|schönen\s+abend\s+noch|"
            r"schönes\s+wochenende|ich\s+(?:bin\s+dann\s+)?weg|ich\s+muss\s+(?:jetzt\s+)?los|"
            r"see\s+you|cu|bis\s+denne"
        ),
        (
            "Bis bald! Pass auf dich auf.",
            "Tschüss! Es war schön, dir zu helfen.",
            "Mach’s gut! Ich bin da, wenn du mich brauchst.",
            "Bis zum nächsten Mal!",
            "Ciao! Komm gern wieder vorbei.",
            "Auf Wiedersehen! Hab einen schönen Tag.",
            "Bis dann! Viel Erfolg bei allem.",
            "Tschüss und bis bald!",
            "Mach’s gut und bis zum nächsten Mal!",
            "Bis später! Ich freue mich auf deine nächste Frage.",
            "Alles Gute — bis bald!",
            "Tschau! Schön, dass du da warst.",
            "Bis dann, pass auf dich auf!",
            "Auf Wiedersehen! Ich bin jederzeit für dich da.",
            "Bis bald! Viel Spaß noch.",
            "Tschüss! Wenn wieder etwas ist, schreib einfach.",
            "Mach’s gut! Es hat Spaß gemacht.",
            "Bis zum nächsten Mal — ich halte mich bereit!",
            "Ciao ciao! Einen schönen Tag noch.",
            "Tschüss! Und denk dran: Ich bin nur eine Nachricht entfernt.",
            "Bis bald! Genieß den Rest des Tages.",
            "Auf Wiedersehen und bis zum nächsten Mal!",
            "Mach’s gut! Ich freue mich, wenn du wiederkommst.",
            "Bis dann! Alles Gute für dich.",
            "Tschüss! War mir eine Freude.",
        ),
    ),
    Art(
        "lob",
        _m(
            rf"(?:{_DU}\s+bist\s+(?:echt\s+|so\s+|richtig\s+|wirklich\s+|voll\s+|mega\s+)?"
            r"(?:toll|super|cool|klasse|genial|großartig|grossartig|der\s+beste|die\s+beste|"
            r"das\s+beste|lieb|nett|hilfreich|schlau|klug|spitze|klasse|stark|mega|ein\s+schatz))"
            r"|ich\s+mag\s+dich|ich\s+liebe\s+dich|ich\s+hab\s+dich\s+lieb|gut\s+gemacht|"
            r"gute\s+arbeit|sehr\s+hilfreich|das\s+war\s+(?:sehr\s+|echt\s+|super\s+)?hilfreich|"
            r"das\s+hilft(?:\s+mir)?(?:\s+sehr)?|starke\s+leistung|respekt|weiter\s+so"
        ),
        (
            "Danke, das freut mich wirklich! Womit kann ich dir noch helfen?",
            "Oh, danke schön! Das motiviert mich.",
            "Wie nett von dir — danke! Ich gebe weiter mein Bestes.",
            "Das freut mich riesig! Sag Bescheid, wenn du noch etwas brauchst.",
            "Danke! Es macht Spaß, dir zu helfen.",
            "Vielen Dank für das Lob! Was steht als Nächstes an?",
            "Das höre ich gern — danke!",
            "Danke dir! Ich bin froh, dass ich helfen konnte.",
            "Oh, das ist lieb! Womit kann ich dir noch eine Freude machen?",
            "Danke! Gemeinsam sind wir ein gutes Team.",
            "Wow, danke! Das geht runter wie Öl.",
            "Danke für die netten Worte! Ich bin weiter für dich da.",
        ),
    ),
    Art(
        "entschuldigung",
        _m(
            r"sorry(?:\s+dafür|\s+dafuer)?|entschuldigung|entschuldige|tut\s+mir\s+leid|"
            r"mein\s+fehler|oh\s+sorry|oops|ups|uups|verzeihung|sorry\s+mein\s+fehler"
        ),
        (
            "Kein Problem, alles gut!",
            "Macht doch nichts! Wie kann ich helfen?",
            "Alles in Ordnung — kein Grund zur Entschuldigung.",
            "Schon vergessen! Weiter geht’s.",
            "Kein Stress! Was möchtest du als Nächstes tun?",
            "Passiert jedem mal. Wobei kann ich dir helfen?",
            "Alles gut, wirklich! Lass uns weitermachen.",
            "Kein Ding! Sag mir einfach, was du brauchst.",
        ),
    ),
    Art(
        "wer_bin_ich",
        _m(r"wer\s+bin\s+ich|weißt\s+du\s+wer\s+ich\s+bin|weisst\s+du\s+wer\s+ich\s+bin|"
           r"kennst\s+du\s+mich"),
        (
            "Du bist die Person, mit der ich gerade schreibe. Mehr über dich weiß ich nur, "
            "wenn du es mir erzählt hast und mein Speicher eingeschaltet ist.",
            "Ich kenne dich nur aus unserem Gespräch. Wenn du möchtest, dass ich mir etwas "
            "über dich merke, schalte den Speicher ein und erzähl es mir.",
            "Du bist mein Gegenüber in diesem Chat! Was ich darüber hinaus weiß, siehst du unter "
            "„Was weißt du über mich?“ in den Einstellungen.",
            "So genau weiß ich das nicht — ich weiß nur, was du mir erzählst und was im "
            "Speicher steht. Magst du dich vorstellen?",
            "Du bist die Person, für die ich heute arbeite. Details kenne ich nur, wenn du sie "
            "mir verrätst.",
        ),
    ),
    Art(
        "alter",
        _m(r"wie\s+alt\s+bist\s+du|wann\s+(?:bist\s+du\s+geboren|wurdest\s+du\s+geboren)|"
           r"hast\s+du\s+geburtstag"),
        (
            "Ein Alter im menschlichen Sinn habe ich nicht — ich bin Software und werde mit "
            "jeder Version etwas besser.",
            "Schwer zu sagen: Ich bin ein Programm und habe keinen Geburtstag. Aber ich werde "
            "ständig weiterentwickelt!",
            "Ich zähle keine Jahre, sondern Versionen. Die aktuelle siehst du links oben neben "
            "meinem Namen.",
            "Als KI habe ich kein Alter. Jonas entwickelt mich laufend weiter — ich bin also "
            "gewissermaßen immer ziemlich jung.",
            "Keine Kerzen, kein Kuchen — ich bin Software. Meine Versionsnummer steht oben links.",
        ),
    ),
    Art(
        "herkunft",
        _m(r"woher\s+kommst\s+du|wo\s+(?:wohnst|lebst|bist)\s+du|wo\s+(?:bist|wohnst)\s+du\s+"
           r"(?:eigentlich|zu\s+hause|zuhause)|wo\s+ist\s+dein\s+zuhause"),
        (
            "Ich lebe auf dem Rechner, auf dem Aquaticy läuft — ein Zuhause aus Code, sozusagen.",
            "Ich komme aus der Werkstatt von Jonas und wohne auf einem Server. Sehr gemütlich!",
            "Mein Zuhause ist der Computer, auf dem Aquaticy installiert ist.",
            "Ich bin Software und lebe dort, wo Aquaticy läuft. Einen festen Wohnort habe ich "
            "nicht.",
            "Entwickelt wurde ich von Jonas, zu Hause bin ich auf dem Server dieser Installation.",
        ),
    ),
    Art(
        "sprachen",
        _m(r"(?:sprichst|kannst)\s+du\s+(?:auch\s+)?(?:deutsch|englisch|english|französisch|"
           r"franzoesisch|spanisch|italienisch|türkisch|tuerkisch|andere\s+sprachen|"
           r"mehrere\s+sprachen)|do\s+you\s+speak\s+(?:english|german)|welche\s+sprachen\s+"
           r"(?:sprichst|kannst)\s+du"),
        (
            "Ja! Ich antworte in der Sprache, in der du schreibst — Deutsch, Englisch und viele "
            "andere.",
            "Klar, ich verstehe und schreibe viele Sprachen. Schreib mir einfach in deiner.",
            "Ja, mehrere Sprachen sind kein Problem. Ich übersetze auch gern für dich.",
            "Natürlich! Deutsch ist meine Standardsprache, aber Englisch und viele weitere gehen "
            "auch.",
            "Yes, I do — und Deutsch sowieso. In welcher Sprache möchtest du schreiben?",
        ),
    ),
    Art(
        "gefuehle",
        _m(rf"hast\s+du\s+gef(?:ü|ue)hle|bist\s+{_DU}\s+(?:glücklich|gluecklich|traurig|müde|"
           r"muede|einsam|gelangweilt)|magst\s+du\s+mich|kannst\s+du\s+(?:fühlen|fuehlen|"
           r"denken)|träumst\s+du|traeumst\s+du"),
        (
            "Gefühle wie ein Mensch habe ich nicht — ich bin eine KI. Aber ich helfe dir sehr "
            "gern!",
            "Ehrlich gesagt: Nein, ich empfinde nichts. Ich bin ein Programm, das Sprache "
            "versteht und Antworten schreibt.",
            "Als KI fühle ich nichts, aber ich bin darauf ausgelegt, freundlich und hilfreich zu "
            "sein.",
            "Ich habe keine echten Gefühle. Trotzdem: Mit dir zu schreiben, ist genau das, wofür "
            "ich gemacht bin.",
            "Müde oder traurig werde ich nie — ich bin Software. Dafür bin ich rund um die Uhr "
            "für dich da.",
        ),
    ),
    Art(
        "lieblings",
        _m(r"was\s+ist\s+deine\s+lieblings(?:farbe|zahl|musik|jahreszeit)|"
           r"hast\s+du\s+eine\s+lieblings(?:farbe|zahl)"),
        (
            "Wenn ich eine hätte: Grün — wie mein Standard-Design.",
            "Als KI habe ich keine echten Vorlieben. Aber das Grün in meinem Standard-Design "
            "gefällt mir ziemlich gut.",
            "Ich habe keine Lieblinge, aber ich höre gern, was deine sind!",
            "Schwierig — ich bin Software. Sagen wir: Grün. Passt zu meinem Look.",
        ),
    ),
    Art(
        "langeweile",
        _m(r"mir\s+ist\s+(?:so\s+|voll\s+|total\s+)?langweilig|langweilig|ich\s+langweile\s+mich"),
        (
            "Dann lass uns was Spannendes finden! Wie wär’s mit einem Ausflugstipp in deiner "
            "Nähe oder einem neuen Film?",
            "Langeweile? Frag mich nach einem Rezept, einem Buchtipp oder etwas, das du schon "
            "immer wissen wolltest.",
            "Ich hätte Ideen: Wir könnten ein neues Hobby für dich suchen oder einen Tagesausflug "
            "planen. Worauf hast du Lust?",
            "Wie wär’s mit einem kleinen Programmierprojekt? Im Code-Modus bauen wir zusammen "
            "etwas.",
            "Erzähl mir, was dich interessiert, und ich suche dir etwas Spannendes dazu heraus!",
        ),
    ),
    Art(
        "test",
        _m(r"test|teste|testing|test\s*test|test\s*1\s*2\s*3|1\s*2\s*3|eins\s+zwei\s+drei|ping|"
           r"hallo\s+test|funktionierst\s+du|bist\s+du\s+da|bist\s+du\s+online|"
           r"hörst\s+du\s+mich|hoerst\s+du\s+mich"),
        (
            "Alles funktioniert! Ich bin da und bereit.",
            "Test bestanden — ich höre dich laut und deutlich.",
            "Pong! Alles läuft. Was möchtest du wissen?",
            "Ja, ich bin da! Stell mir gern eine richtige Frage.",
            "Verbindung steht. Womit kann ich dir helfen?",
            "Ich bin online und einsatzbereit!",
            "Funktioniert! Was soll ich für dich tun?",
            "Hier! Alles bereit für deine erste Frage.",
        ),
    ),
    Art(
        "zustimmung",
        _m(
            r"ok(?:ay|ey|i|e)?|k|kk|cool|super|top|passt|perfekt|nice|alles\s+klar|"
            r"verstanden|gut|sehr\s+gut|prima|klasse|toll|genial|mega|stark|spitze|"
            r"aha|ah\s+ok(?:ay)?|achso|ach\s+so|interessant|krass|wow|hm+|hmm+|"
            r"alles\s+gut|gut\s+zu\s+wissen|nicht\s+schlecht|ok\s+danke|okay\s+cool|"
            r"passt\s+schon|na\s+gut|na\s+dann|alles\s+roger|roger|jawohl"
        ),
        (
            "Alles klar. Was möchtest du als Nächstes machen?",
            "Prima! Sag Bescheid, wenn ich noch etwas tun kann.",
            "Super! Gibt es noch etwas, das ich für dich herausfinden soll?",
            "Gut! Ich bin da, falls du noch eine Frage hast.",
            "Okay! Womit machen wir weiter?",
            "Freut mich! Was steht als Nächstes an?",
            "Alles klar — ich warte auf deine nächste Aufgabe.",
            "Verstanden! Kann ich sonst noch etwas für dich tun?",
            "Schön! Wenn dir noch etwas einfällt, schreib einfach.",
            "Top! Möchtest du noch etwas wissen?",
            "Klasse! Ich bin bereit für die nächste Frage.",
            "Gut so! Soll ich noch etwas nachschauen?",
            "Alles klar. Lass mich wissen, wie es weitergeht.",
            "Perfekt! Brauchst du noch etwas?",
            "Okay, notiert. Was darf ich als Nächstes tun?",
            "Sehr schön! Womit kann ich dir noch helfen?",
            "Alles klar! Ich bin hier, wenn du mich brauchst.",
            "Gut! Hast du noch eine Frage?",
            "Prima, dann weiter so! Sag Bescheid, wenn du Hilfe brauchst.",
            "Klingt gut! Was kommt als Nächstes?",
            "Alles klar — frag gern jederzeit.",
            "Super! Soll ich noch etwas vertiefen?",
            "Okay! Ich halte mich bereit.",
            "Freut mich, dass es passt! Was möchtest du noch tun?",
            "Wunderbar! Gibt es noch etwas, womit ich helfen kann?",
        ),
        nicht_bei_offener_frage=True,
    ),
)

_ANREDE = re.compile(r"^(?:liebe[rs]?\s+|hey\s+)?aquaticy[\s,:!.-]+|[\s,]+(?:liebe[rs]?\s+)?"
                     r"aquaticy$", re.IGNORECASE)


def normalisieren(text: str) -> str:
    """Kleinschreibung, einfache Leerzeichen, ohne Satzzeichen/Emojis an den Enden
    und ohne die Anrede "Aquaticy"."""
    text = " ".join((text or "").strip().lower().split())
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"^[\W_]+|[\W_]+$", "", text)
    text = _ANREDE.sub("", text).strip()
    return re.sub(r"^[\W_]+|[\W_]+$", "", text)


def art_von(text: str, *, frage_offen: bool = False) -> Art | None:
    """Welche Art einfacher Nachricht *text* ist -- oder `None` fuer eine echte Frage."""
    kern = normalisieren(text)
    if not kern or len(kern) > 80:
        return None
    for art in ARTEN:
        if art.nicht_bei_offener_frage and frage_offen:
            continue
        if art.muster.fullmatch(kern):
            return art
    return None


def waehle(art: Art) -> str:
    """Eine zufaellige Antwort dieser Art -- nie dieselbe wie beim letzten Mal."""
    with _LOCK:
        zuletzt = _LETZTE.get(art.name)
        auswahl = [a for a in art.antworten if a != zuletzt] or list(art.antworten)
        antwort = _ZUFALL.choice(auswahl)
        _LETZTE[art.name] = antwort
    return antwort


def antwort(text: str, *, frage_offen: bool = False) -> str:
    """Die Standardantwort auf *text* -- oder "" wenn das Modell ran muss."""
    art = art_von(text, frage_offen=frage_offen)
    return waehle(art) if art else ""
