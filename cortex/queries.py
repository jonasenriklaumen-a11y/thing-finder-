"""Aus einer Frage mehrere Suchanfragen machen -- und die Trefferlisten mischen.

Hintergrund: Eine einzige Formulierung findet immer nur das, was zufaellig
genau so im Netz steht. Wer dieselbe Frage zwei- oder dreimal anders stellt,
deckt deutlich mehr ab -- das ist der Kern dessen, was in der Literatur
"query fan-out" oder "multi-query retrieval" heisst.

Drei Erkenntnisse aus der Recherche stecken hier drin:

1. **Kurze Stichwortanfragen schlagen ganze Saetze.** Drei bis sechs
   inhaltstragende Woerter, das Hauptthema in jeder Variante. Ein
   ausgeschriebener Fragesatz verwaessert die Anfrage nur.
2. **Massvolle Auffaecherung.** Zwei bis drei Varianten bringen mehr Treffer;
   ab der vierten kippt es und die Ergebnisse werden beliebiger. Deshalb ist
   hier bei drei Schluss.
3. **Reciprocal Rank Fusion (RRF)** zum Mischen. Die Listen verschiedener
   Anfragen lassen sich nicht ueber ihre Punktzahlen vergleichen -- die
   bedeuten je Engine etwas anderes. RRF rechnet nur mit dem Platz:
   `1 / (k + Rang)`, aufsummiert ueber alle Listen. Was in mehreren Listen
   weit oben steht, gewinnt. Genau dafuer wurde es gebaut.

Alles hier ist reine Textarbeit -- kein Modellaufruf, kein Netz. Damit kostet
die bessere Suche keine Wartezeit und funktioniert auch mit einem kleinen
lokalen Modell.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from cortex.models import SearchResult, domain_of

#: Die uebliche Konstante aus der RRF-Veroeffentlichung. Sie daempft den
#: Vorsprung der ersten Plaetze -- ohne sie wuerde Platz 1 alles erschlagen.
RRF_K = 60

#: Mehr als drei Formulierungen verwaessern die Treffer, statt sie zu mehren.
MAX_VARIANTS = 3

#: Hoechstens so viele Treffer je Domain im gemischten Ergebnis. Sonst belegt
#: ein einziges Portal die ganze erste Seite und die Sicht wird einseitig.
PER_DOMAIN = 2

#: Fuellwoerter, die eine Suchanfrage nur laenger machen. Deutsch und
#: Englisch gemischt, weil Fragen oft gemischt gestellt werden.
_STOPWORD_TEXT = """
    aber alle allem allen aller alles als also am an andere anderem anderen ander
    auch auf aus bei beim bin bis bist da damit dann das dass dem den denn der
    deren des dessen die dies diese diesem diesen dieser dieses doch dort du
    durch ein eine einem einen einer eines er es etwa etwas euch euer fuer für
    gab gibt hab habe haben hat hatte hier hin ich ihm ihn ihnen ihr ihre im in
    ins ist ja jede jedem jeden jeder jedes jetzt kann kannst kein keine koennen
    können könnte legen machen mal man mehr mein meine mich mir mit muss musst
    nach nicht nichts noch nun nur ob oder ohne schon sehr sein seine seit sich
    sie sind so soll sollen sollte sondern um und uns unser unter viel viele vom
    von vor waere wäre war waren was wann warum weil welche welchem welchen
    welcher welches wenn wer werde werden wie wieso wieviel will wir wird wirst
    wo wollen worden wurde wurden zu zum zur zwar zwischen
    a about all an and any are as at be been but by can could did do does for
    from get give had has have how i in into is it its me most my no not of on
    or our should so some tell than that the their them there these they this
    to was we were what when where which who why will with would you your
"""
STOPWORDS = frozenset(_STOPWORD_TEXT.split())

#: Frageeinleitungen, die vorne wegkoennen -- sie stehen nie in der Antwort.
_STARTER_TEXT = """
    was wer wie wo wann warum wieso weshalb welche welcher welches welchem
    welchen wieviel wieviele gibt kennst suche finde zeig zeige nenne erklaer
    erklaere erkläre sag sage brauche moechte möchte will
"""
QUESTION_STARTERS = frozenset(_STARTER_TEXT.split())

_WORD = re.compile(r"[\w\-äöüÄÖÜß.]+", re.UNICODE)
#: Zwei oder mehr grossgeschriebene Woerter hintereinander -- meist ein Name,
#: eine Marke oder ein Produkt. Genau das lohnt sich in Anfuehrungszeichen.
_PROPER = re.compile(r"\b([A-ZÄÖÜ][\wäöüß\-]+(?:\s+[A-ZÄÖÜ0-9][\wäöüß\-]*){1,3})")


def content_words(query: str) -> list[str]:
    """Die inhaltstragenden Woerter einer Anfrage, in ihrer Reihenfolge."""
    words = _WORD.findall(query)
    return [word for word in words if word.lower() not in STOPWORDS and len(word) > 1]


def keywords(query: str, keep: int = 6) -> str:
    """Die Anfrage auf ihre Stichwoerter eindampfen.

    "Wie viel kostet ein gebrauchtes Lastenrad in Bremen?" wird zu
    "kostet gebrauchtes Lastenrad Bremen". Drei bis sechs Woerter sind das
    Mass, das sich in der Praxis bewaehrt hat.
    """
    words = content_words(query)
    while words and words[0].lower() in QUESTION_STARTERS:
        words.pop(0)
    return " ".join(words[:keep])


def quoted_phrase(query: str) -> str:
    """Frueher: einen Eigennamen automatisch in Anfuehrungszeichen setzen.

    Das funktioniert im Englischen, im Deutschen nicht: hier werden *alle*
    Substantive grossgeschrieben, ein grossgeschriebenes Wortpaar ist also
    kein Hinweis auf einen Namen. Aus "beste Kaffeemuehle unter 200 Euro Test"
    wurde so das sinnlose Zitat `"Euro Test"`, das die Suche verengt statt sie
    zu verbessern. Deshalb raet cortex nicht mehr -- Anfuehrungszeichen setzt,
    wer sie meint: das Modell oder der Mensch. Geschriebene bleiben erhalten.
    """
    return ""


def variants(query: str, extra: int = 2) -> list[str]:
    """Die Anfrage plus bis zu *extra* andere Formulierungen.

    Die erste bleibt immer das Original -- das Modell hat sich etwas dabei
    gedacht, und manchmal ist der ganze Satz genau richtig. Dazu kommt die
    Stichwortfassung. Mehr raet cortex nicht: weitere Formulierungen kommen
    vom Modell selbst, das dafuer mehrere Anfragen auf einmal stellen kann --
    Semantik kann es, Wortlisten sind Handarbeit.
    """
    query = " ".join((query or "").split())
    if not query:
        return []
    out = [query]
    if extra <= 0 or '"' in query:
        # Wer zitiert, meint es genau so. Daran wird nicht herumformuliert.
        return out
    candidate = keywords(query).strip()
    if candidate and candidate.lower() != query.lower():
        out.append(candidate)
    return out[: 1 + max(0, extra)][:MAX_VARIANTS]


def fuse(
    lists: Sequence[Iterable[SearchResult]],
    count: int,
    *,
    per_domain: int = PER_DOMAIN,
) -> list[SearchResult]:
    """Mehrere Trefferlisten zu einer machen (Reciprocal Rank Fusion).

    Jeder Treffer bekommt je Liste `1 / (RRF_K + Platz)` gutgeschrieben. Was
    mehrere Anfragen uebereinstimmend weit oben haben, steigt nach vorne --
    ohne dass Punktzahlen verschiedener Engines verglichen werden muessten,
    die ohnehin nichts miteinander zu tun haben.

    Danach wird die Domainvielfalt begrenzt: hoechstens *per_domain* Treffer
    je Seite, damit nicht ein Portal die Liste fuellt. Reicht es dann nicht
    fuer *count*, werden die uebriggebliebenen der Reihe nach nachgefuellt --
    lieber ein zweiter Treffer derselben Seite als eine halbe Liste.
    """
    scores: dict[str, float] = {}
    best: dict[str, SearchResult] = {}
    for results in lists:
        for rank, result in enumerate(results, start=1):
            url = (result.url or "").strip()
            if not url:
                continue
            scores[url] = scores.get(url, 0.0) + 1.0 / (RRF_K + rank)
            keep = best.get(url)
            # Bei Dubletten die aussagekraeftigere Fassung behalten.
            if keep is None or len(result.snippet or "") > len(keep.snippet or ""):
                if keep is not None and not (result.title or "").strip():
                    continue
                best[url] = result

    order = sorted(best.values(), key=lambda item: -scores[item.url.strip()])
    picked: list[SearchResult] = []
    spare: list[SearchResult] = []
    per_host: dict[str, int] = {}
    for result in order:
        host = result.source_domain or domain_of(result.url)
        if per_host.get(host, 0) >= per_domain:
            spare.append(result)
            continue
        per_host[host] = per_host.get(host, 0) + 1
        picked.append(result)

    picked.extend(spare)
    picked = picked[:count]
    for position, result in enumerate(picked, start=1):
        result.rank = position
        result.source_domain = result.source_domain or domain_of(result.url)
    return picked
