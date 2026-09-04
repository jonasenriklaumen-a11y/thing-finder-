"""Tests fuer Stufe 3 -- mit einer Attrappe statt echtem Browser."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from cortex.browser import (
    click_known_reject_button,
    click_reject_by_text,
    dismiss_consent,
    launch_args,
    remove_overlays,
    render_page,
)
from cortex.fetch import load_rules


class FakeElement:
    """Ein anklickbares Element."""

    def __init__(self, text: str = "", visible: bool = True, broken: bool = False) -> None:
        self.text = text
        self.visible = visible
        self.broken = broken
        self.clicked = False

    def is_visible(self) -> bool:
        return self.visible

    def inner_text(self) -> str:
        return self.text

    def click(self, **kwargs: Any) -> None:
        if self.broken:
            raise RuntimeError("Element nicht klickbar")
        self.clicked = True


class FakePage:
    """Minimale Playwright-Attrappe."""

    def __init__(
        self,
        elements: dict[str, list[FakeElement]] | None = None,
        frames: list[FakePage] | None = None,
    ) -> None:
        self.elements = elements or {}
        self.frames = [self, *(frames or [])]
        self.evaluated: list[tuple[str, Any]] = []
        self.removed = 0

    def query_selector_all(self, selector: str) -> list[FakeElement]:
        return self.elements.get(selector, [])

    def evaluate(self, script: str, argument: Any = None) -> int:
        self.evaluated.append((script, argument))
        return self.removed


RULES = load_rules()


def test_onetrust_reject_button_is_clicked() -> None:
    button = FakeElement("Alle ablehnen")
    page = FakePage({"#onetrust-reject-all-handler": [button]})
    assert dismiss_consent(page, RULES) == "cmp:#onetrust-reject-all-handler"
    assert button.clicked


@pytest.mark.parametrize(
    "selector",
    [
        "#onetrust-reject-all-handler",
        "#CybotCookiebotDialogBodyButtonDecline",
        '[data-testid="uc-deny-all-button"]',
        "#didomi-notice-disagree-button",
    ],
)
def test_all_known_cmps_are_covered(selector: str) -> None:
    button = FakeElement("Ablehnen")
    page = FakePage({selector: [button]})
    assert dismiss_consent(page, RULES) == f"cmp:{selector}"
    assert button.clicked


def test_accept_button_is_never_clicked() -> None:
    accept = FakeElement("Alle akzeptieren")
    page = FakePage({"button": [accept]})
    result = dismiss_consent(page, RULES)
    assert not accept.clicked
    assert not result.startswith("text:")


def test_generic_text_fallback() -> None:
    accept = FakeElement("Alle akzeptieren")
    reject = FakeElement("Nur notwendige Cookies")
    page = FakePage({"button": [accept, reject]})
    assert dismiss_consent(page, RULES) == "text:Nur notwendige Cookies"
    assert reject.clicked
    assert not accept.clicked


@pytest.mark.parametrize(
    "label",
    ["Ablehnen", "Reject all", "Decline", "Necessary only", "Continue without accepting"],
)
def test_reject_labels_in_both_languages(label: str) -> None:
    button = FakeElement(label)
    page = FakePage({"button": [button]})
    assert click_reject_by_text(page, RULES.reject_text_pattern) == label


def test_invisible_buttons_are_ignored() -> None:
    hidden = FakeElement("Ablehnen", visible=False)
    page = FakePage({"button": [hidden]})
    assert click_reject_by_text(page, RULES.reject_text_pattern) is None
    assert not hidden.clicked


def test_unclickable_element_falls_through() -> None:
    broken = FakeElement("Ablehnen", broken=True)
    page = FakePage({"#onetrust-reject-all-handler": [broken]})
    page.removed = 3
    assert dismiss_consent(page, RULES) == "removed:3"


def test_iframe_dialog_is_handled() -> None:
    """Sourcepoint/Quantcast rendern in einem iFrame."""
    button = FakeElement("Ablehnen")
    frame = FakePage({"button": [button]})
    page = FakePage({}, frames=[frame])
    assert dismiss_consent(page, RULES) == "text:Ablehnen"
    assert button.clicked


def test_without_reject_button_overlays_are_removed() -> None:
    page = FakePage({})
    page.removed = 2
    assert dismiss_consent(page, RULES) == "removed:2"
    script, selectors = page.evaluated[0]
    assert "document.body" in script
    assert "overflow" in script
    assert "#onetrust-consent-sdk" in selectors


def test_scroll_lock_is_released() -> None:
    page = FakePage({})
    remove_overlays(page, ["#x"])
    script = page.evaluated[0][0]
    assert "element.style.overflow = ''" in script
    assert "element.style.position = 'static'" in script


def test_newsletter_and_app_banners_are_only_removed() -> None:
    newsletter = FakeElement("Jetzt Newsletter abonnieren")
    page = FakePage({"button": [newsletter]})
    dismiss_consent(page, RULES)
    assert not newsletter.clicked
    assert any("newsletter" in str(arg).lower() for _, arg in page.evaluated)


def test_nothing_to_do() -> None:
    page = FakePage({})
    assert dismiss_consent(page, RULES) == "nothing"


def test_broken_scope_does_not_raise() -> None:
    class ExplodingPage:
        frames: ClassVar[list[Any]] = []

        def query_selector_all(self, selector: str) -> list[FakeElement]:
            raise RuntimeError("Seite weg")

        def evaluate(self, script: str, argument: Any = None) -> int:
            raise RuntimeError("Seite weg")

    assert dismiss_consent(ExplodingPage(), RULES) == "nothing"
    assert click_known_reject_button(ExplodingPage(), ["#x"]) is None


def test_render_page_without_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne installiertes Playwright faellt der Abruf sauber aus."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any):
        if name.startswith("playwright"):
            raise ImportError("kein playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert render_page("https://example.de/", "cortex/0.1") is None


def test_fetcher_uses_browser_only_for_consent_walls(fixture_html, monkeypatch) -> None:
    """Stufe 2 entscheidet, ob Stufe 3 ueberhaupt loslaeuft."""
    import httpx

    from cortex.fetch import Fetcher, RobotsPolicy

    rendered = fixture_html("plain_article.html")
    calls: list[str] = []

    def fake_render(url: str, **kwargs: Any) -> str:
        calls.append(url)
        return rendered

    monkeypatch.setattr("cortex.browser.render_page", fake_render)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, text=fixture_html("consent_wall.html"), headers={"content-type": "text/html"}
        )

    fetcher = Fetcher("cortex-test/0.1", timeout=5, delay_seconds=0, enable_browser=True)
    fetcher._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    fetcher.robots = RobotsPolicy(fetcher._client, "cortex-test/0.1")

    page = fetcher.fetch("https://zeitung.example/artikel")
    assert calls == ["https://zeitung.example/artikel"]
    assert page.ok
    assert page.via == "browser"
    assert "Franzbrötchen" in page.text


def test_paywalls_never_trigger_the_browser(fixture_html, monkeypatch) -> None:
    """Eine Bezahlschranke wird nicht mit dem Browser aufgebrochen."""
    import httpx

    from cortex.fetch import Fetcher, RobotsPolicy

    calls: list[str] = []
    monkeypatch.setattr(
        "cortex.browser.render_page", lambda url, **kwargs: calls.append(url) or ""
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, text=fixture_html("paywall_article.html"), headers={"content-type": "text/html"}
        )

    fetcher = Fetcher("cortex-test/0.1", timeout=5, delay_seconds=0, enable_browser=True)
    fetcher._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    fetcher.robots = RobotsPolicy(fetcher._client, "cortex-test/0.1")

    page = fetcher.fetch("https://zeitung.example/artikel")
    assert calls == []
    assert page.skipped_reason == "paywall"


# ---------------------------------------------------------------------------
# Betrieb im Container
# ---------------------------------------------------------------------------
def test_browser_sandbox_stays_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auf dem blanken System behaelt Chromium seine eigene Sandbox."""
    monkeypatch.delenv("CORTEX_BROWSER_NO_SANDBOX", raising=False)
    assert launch_args() == []


@pytest.mark.parametrize("value", ["1", "true", "yes", "ja"])
def test_container_flag_disables_the_browser_sandbox(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Im Container uebernimmt der Container die Isolation."""
    monkeypatch.setenv("CORTEX_BROWSER_NO_SANDBOX", value)
    assert launch_args() == ["--no-sandbox", "--disable-dev-shm-usage"]


def test_unset_like_values_keep_the_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_BROWSER_NO_SANDBOX", "0")
    assert launch_args() == []
