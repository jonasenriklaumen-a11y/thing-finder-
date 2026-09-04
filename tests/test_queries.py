"""Tests fuer Anfrage-Varianten und das Mischen der Trefferlisten."""

from __future__ import annotations

import pytest

from cortex import queries
from cortex.models import SearchResult


def hit(url: str, title: str = "T", snippet: str = "S") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


# ---------------------------------------------------------------------------
# Stichwortfassung
# ---------------------------------------------------------------------------
def test_a_question_becomes_keywords() -> None:
    """Ganze Fragesaetze verwaessern die Suche -- Stichwoerter treffen."""
    assert queries.keywords("Wie viel kostet ein gebrauchtes Lastenrad in Bremen?") == (
        "kostet gebrauchtes Lastenrad Bremen"
    )


def test_keywords_keep_at_most_six_words() -> None:
    long = "Vergleich Preis Leistung Haltbarkeit Garantie Ersatzteile Zubehoer Farbe"
    assert len(queries.keywords(long).split()) == 6


def test_question_words_at_the_front_are_dropped() -> None:
    assert not queries.keywords("Was ist das?").startswith("Was")


def test_keywords_survive_an_empty_query() -> None:
    assert queries.keywords("") == ""
    assert queries.keywords("und der die das") == ""


# ---------------------------------------------------------------------------
# Varianten
# ---------------------------------------------------------------------------
def test_the_original_query_always_comes_first() -> None:
    """Das Modell hat sich etwas dabei gedacht -- das steht nicht zur Debatte."""
    original = "Wie viel kostet ein Lastenrad in Bremen?"
    assert queries.variants(original)[0] == original


def test_a_keyword_query_gets_no_second_variant() -> None:
    """"Lastenrad Bremen" ist schon die Stichwortfassung -- nichts zu tun."""
    assert queries.variants("Lastenrad Bremen") == ["Lastenrad Bremen"]


def test_quotes_are_left_alone() -> None:
    """Wer zitiert, meint die genaue Wortfolge. Daran wird nicht gedreht."""
    assert queries.variants('"Deutsche Bahn" Streik') == ['"Deutsche Bahn" Streik']


def test_no_automatic_quoting_of_german_nouns() -> None:
    """Im Deutschen ist Grossschreibung kein Hinweis auf einen Eigennamen.

    Frueher wurde daraus das sinnlose Zitat "Euro Test" -- das verengt die
    Suche, statt sie zu verbessern.
    """
    for variant in queries.variants("beste Kaffeemuehle unter 200 Euro Test"):
        assert '"' not in variant


def test_variants_can_be_switched_off() -> None:
    assert queries.variants("Wie teuer ist ein Lastenrad?", extra=0) == [
        "Wie teuer ist ein Lastenrad?"
    ]


def test_never_more_than_three_variants() -> None:
    """Ab der vierten Formulierung streut es nur noch."""
    assert len(queries.variants("Wie teuer ist ein Lastenrad in Bremen?", extra=9)) <= 3


def test_an_empty_query_yields_nothing() -> None:
    assert queries.variants("   ") == []


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------
def test_a_hit_found_by_two_queries_wins() -> None:
    """Genau dafuer ist RRF da: Uebereinstimmung schlaegt einen ersten Platz."""
    first = [hit("https://a.de/1"), hit("https://b.de/1")]
    second = [hit("https://c.de/1"), hit("https://b.de/1")]
    merged = queries.fuse([first, second], count=5)
    assert merged[0].url == "https://b.de/1"


def test_ranks_are_renumbered_without_gaps() -> None:
    merged = queries.fuse([[hit("https://a.de/1"), hit("https://b.de/1")]], count=5)
    assert [result.rank for result in merged] == [1, 2]


def test_the_richer_snippet_survives_a_duplicate() -> None:
    thin = [hit("https://a.de/1", snippet="x")]
    rich = [hit("https://a.de/1", snippet="ein deutlich aussagekraeftigerer Text")]
    merged = queries.fuse([thin, rich], count=5)
    assert merged[0].snippet == "ein deutlich aussagekraeftigerer Text"


def test_one_site_does_not_fill_the_whole_list() -> None:
    """Sonst belegt ein Portal die erste Seite und die Sicht wird einseitig."""
    portal = [hit(f"https://portal.de/{i}") for i in range(5)]
    others = [hit("https://a.de/1"), hit("https://b.de/1")]
    merged = queries.fuse([portal, others], count=4)
    hosts = [result.source_domain for result in merged]
    assert hosts.count("portal.de") == queries.PER_DOMAIN


def test_the_domain_cap_never_shortens_the_list() -> None:
    """Lieber ein zweiter Treffer derselben Seite als eine halbe Liste."""
    portal = [hit(f"https://portal.de/{i}") for i in range(6)]
    merged = queries.fuse([portal], count=5)
    assert len(merged) == 5


def test_results_without_a_url_are_dropped() -> None:
    assert queries.fuse([[hit(""), hit("https://a.de/1")]], count=5) == [
        result for result in queries.fuse([[hit("https://a.de/1")]], count=5)
    ]


def test_fusing_nothing_gives_nothing() -> None:
    assert queries.fuse([], count=5) == []


@pytest.mark.parametrize("count", [1, 2, 3])
def test_the_count_is_respected(count: int) -> None:
    lists = [[hit(f"https://a{i}.de/1") for i in range(6)]]
    assert len(queries.fuse(lists, count=count)) == count
