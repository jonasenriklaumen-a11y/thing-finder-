"""Die Rechtstexte bleiben ehrlich, sicher und ohne externe Seiteneffekte."""

from __future__ import annotations

from aquaticy.legal import LEGAL_ROUTES, legal_page


def test_each_legal_page_is_standalone_and_has_navigation() -> None:
    for route in LEGAL_ROUTES:
        page = legal_page(route).decode("utf-8")
        assert "<!doctype html>" in page
        assert 'lang="de"' in page
        assert "Zurück zu Aquaticy" in page
        assert "<script" not in page


def test_legal_pages_do_not_render_external_values() -> None:
    page = legal_page("/privacy").decode("utf-8")
    assert "<script>alert" not in page


def test_accessibility_page_has_no_repository_link() -> None:
    page = legal_page("/terms").decode("utf-8")
    assert "Nutzungsbedingungen" in page
    accessibility = legal_page("/accessibility").decode("utf-8")
    assert "Projekt auf GitHub" not in accessibility
    assert "github.com" not in accessibility
