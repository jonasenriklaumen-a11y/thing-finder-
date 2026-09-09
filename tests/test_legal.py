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


def test_operator_details_are_escaped(monkeypatch) -> None:
    monkeypatch.setenv("AQUATICY_OPERATOR_NAME", '<script>alert("x")</script>')
    page = legal_page("/privacy").decode("utf-8")
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_unconfigured_operator_is_not_invented(monkeypatch) -> None:
    for key in (
        "AQUATICY_OPERATOR_NAME",
        "AQUATICY_OPERATOR_EMAIL",
        "AQUATICY_OPERATOR_ADDRESS",
    ):
        monkeypatch.delenv(key, raising=False)
    page = legal_page("/terms").decode("utf-8")
    assert "noch nicht hinterlegt" in page
