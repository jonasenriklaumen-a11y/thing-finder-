"""Stufe 3: Playwright-Fallback fuer Seiten, die ohne JavaScript nichts liefern.

Optionale Abhaengigkeit -- Installation ueber `cortex install-browser`.

Datenschutz-Voreinstellungen dieses Moduls:

* Immer die datensparsamste Option: **ablehnen statt akzeptieren**. "Alle
  akzeptieren" wird nie geklickt.
* Gibt es keinen Ablehnen-Button, werden die Overlay-Knoten aus dem DOM
  entfernt und die Scroll-Sperre geloest -- der Inhalt liegt fast immer
  schon im DOM.
* Newsletter-Layer, App-Install-Banner und Push-Abfragen werden nur
  entfernt, nie angeklickt. Browser-Berechtigungen werden generell verweigert.
* Pro Seitenabruf ein frischer Browser-Kontext, keine Cookies ueber Aufrufe
  hinweg. Keine Anmeldung, keine Formulare, keine gespeicherten Zugangsdaten.
"""

from __future__ import annotations

import contextlib
import os
import re
from typing import Any, Protocol

from cortex.fetch import SiteRules, load_rules

#: Wie lange warten wir maximal auf Netzruhe?
NETWORK_IDLE_TIMEOUT_MS = 6_000
#: Wie lange darf ein einzelner Klick brauchen?
CLICK_TIMEOUT_MS = 2_500

#: Chromium-Argumente fuer den Betrieb im Container. Dort steht der eigene
#: Sandbox-Mechanismus des Browsers meist nicht zur Verfuegung -- was
#: vertretbar ist, weil der ganze Prozess bereits im Container isoliert
#: laeuft. Ausserhalb eines Containers bleibt die Browser-Sandbox aktiv.
CONTAINER_ARGS = ("--no-sandbox", "--disable-dev-shm-usage")

#: Entfernt Overlays und loest die Scroll-Sperre.
REMOVE_OVERLAYS_JS = """
(selectors) => {
  let removed = 0;
  for (const selector of selectors) {
    let nodes;
    try { nodes = document.querySelectorAll(selector); } catch (e) { continue; }
    for (const node of nodes) { node.remove(); removed += 1; }
  }
  // Scroll-Sperre loesen -- viele CMPs frieren das Dokument ein.
  for (const element of [document.body, document.documentElement]) {
    if (!element) continue;
    element.style.overflow = '';
    element.style.position = 'static';
    element.style.height = '';
    element.classList.remove('modal-open', 'no-scroll', 'noscroll', 'overflow-hidden');
  }
  return removed;
}
"""


class Clickable(Protocol):
    """Das Wenige, das wir von einem Playwright-Element brauchen."""

    def is_visible(self) -> bool: ...
    def inner_text(self) -> str: ...
    def click(self, **kwargs: Any) -> None: ...


class Scope(Protocol):
    """Seite oder Frame."""

    def query_selector_all(self, selector: str) -> list[Clickable]: ...


def _visible_elements(scope: Scope, selector: str) -> list[Clickable]:
    try:
        elements = scope.query_selector_all(selector)
    except Exception:
        return []
    visible: list[Clickable] = []
    for element in elements:
        try:
            if element.is_visible():
                visible.append(element)
        except Exception:
            continue
    return visible


def click_known_reject_button(scope: Scope, selectors: list[str]) -> str | None:
    """Klickt den ersten sichtbaren Ablehnen-Button einer bekannten CMP."""
    for selector in selectors:
        for element in _visible_elements(scope, selector):
            try:
                element.click(timeout=CLICK_TIMEOUT_MS)
                return selector
            except Exception:
                continue
    return None


def click_reject_by_text(scope: Scope, pattern: str) -> str | None:
    """Generischer Fallback: sichtbarer Button, dessen Text auf *pattern* passt."""
    if not pattern:
        return None
    regex = re.compile(pattern, re.IGNORECASE)
    for selector in ("button", '[role="button"]', "a.button", "input[type=button]"):
        for element in _visible_elements(scope, selector):
            try:
                label = (element.inner_text() or "").strip()
            except Exception:
                continue
            if not label or len(label) > 60 or not regex.search(label):
                continue
            try:
                element.click(timeout=CLICK_TIMEOUT_MS)
                return label
            except Exception:
                continue
    return None


def remove_overlays(page: Any, selectors: list[str]) -> int:
    """Entfernt Overlay-Knoten per JavaScript und loest die Scroll-Sperre."""
    try:
        return int(page.evaluate(REMOVE_OVERLAYS_JS, selectors) or 0)
    except Exception:
        return 0


def dismiss_consent(page: Any, rules: SiteRules | None = None) -> str:
    """Lehnt Consent ab oder raeumt das Overlay weg.

    Returns:
        `cmp:<selector>`, `text:<label>`, `removed:<n>` oder `nothing`.
    """
    rules = rules or load_rules()

    selector = click_known_reject_button(page, rules.cmp_reject_selectors)
    if selector:
        return f"cmp:{selector}"

    # Sourcepoint und Quantcast rendern ihren Dialog in einem iFrame.
    for frame in getattr(page, "frames", [])[1:]:
        selector = click_known_reject_button(frame, rules.cmp_reject_selectors)
        if selector:
            return f"cmp:{selector}"
        label = click_reject_by_text(frame, rules.reject_text_pattern)
        if label:
            return f"text:{label}"

    label = click_reject_by_text(page, rules.reject_text_pattern)
    if label:
        return f"text:{label}"

    # Kein Ablehnen-Button? Dann NICHT akzeptieren, sondern das Overlay
    # entfernen -- der Inhalt liegt fast immer schon im DOM.
    removed = remove_overlays(page, rules.overlay_remove_selectors)
    return f"removed:{removed}" if removed else "nothing"


def launch_args() -> list[str]:
    """Zusaetzliche Chromium-Argumente aus der Umgebung.

    `CORTEX_BROWSER_NO_SANDBOX=1` schaltet die Browser-eigene Sandbox ab --
    im Container-Image ist das gesetzt, auf dem blanken System nicht.
    """
    flag = os.environ.get("CORTEX_BROWSER_NO_SANDBOX", "").strip().lower()
    return list(CONTAINER_ARGS) if flag in {"1", "true", "yes", "on", "ja"} else []


def playwright_available() -> bool:
    """Ist Playwright installiert?"""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def render_page(
    url: str,
    user_agent: str,
    timeout: float = 15.0,
    rules: SiteRules | None = None,
) -> str | None:
    """Rendert *url* im Browser und gibt das HTML nach dem Aufraeumen zurueck.

    Gibt `None` zurueck, wenn Playwright fehlt oder die Seite nicht laedt.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    rules = rules or load_rules()
    timeout_ms = int(max(timeout, 5.0) * 1000)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=launch_args())
            try:
                # Frischer Kontext je Abruf -- nichts wird uebernommen.
                context = browser.new_context(
                    user_agent=user_agent,
                    locale="de-DE",
                    permissions=[],  # Notifications, Geolocation & Co. verweigert
                    java_script_enabled=True,
                    accept_downloads=False,
                )
                context.grant_permissions([])
                context.set_default_timeout(timeout_ms)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # Seiten mit Dauer-Polling werden nie "idle" -- das ist kein Fehler.
                with contextlib.suppress(Exception):
                    page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)

                dismiss_consent(page, rules)
                # Nach dem Klick baut sich die Seite oft neu auf.
                with contextlib.suppress(Exception):
                    page.wait_for_load_state("networkidle", timeout=3_000)
                remove_overlays(page, rules.overlay_remove_selectors)

                html = page.content()
                context.close()
                return html
            finally:
                browser.close()
    except Exception:
        return None
