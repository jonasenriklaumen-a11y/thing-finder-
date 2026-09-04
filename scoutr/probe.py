"""Verbindungstests fuer Modell und Suchmaschine.

`cortex setup` fragt am Ende "funktioniert das ueberhaupt?" und schickt je
eine winzige Testanfrage los. Dieselbe Frage stellt sich in den
Web-Einstellungen -- deshalb steht die Antwort hier und nicht in der CLI:
Eingetippt wird an zwei Stellen, geprueft wird mit demselben Code.

Beide Funktionen geben `(ok, Text)` zurueck und werfen nie. Ein
fehlgeschlagener Test ist ein Ergebnis, kein Absturz.
"""

from __future__ import annotations

#: Genug fuer "ok", zu wenig, um Geld zu kosten.
PROBE_TOKENS = 8
PROBE_TIMEOUT = 30
PROBE_QUESTION = "Antworte mit genau dem Wort: ok"
PROBE_SEARCH = "wetter berlin"


def check_llm(model: str, api_key: str = "", api_base: str = "") -> tuple[bool, str]:
    """Schickt eine winzige Testanfrage an das Modell."""
    try:
        import litellm

        litellm.suppress_debug_info = True
        kwargs: dict[str, object] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": PROBE_QUESTION}],
            max_tokens=PROBE_TOKENS,
            timeout=PROBE_TIMEOUT,
            **kwargs,
        )
        text = (response.choices[0].message.content or "").strip()
        return True, text or "(leere Antwort, aber Verbindung steht)"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def check_search(
    backend: str, api_key: str = "", engines: str = "", instance_url: str = ""
) -> tuple[bool, str]:
    """Schickt eine Testsuche an das gewaehlte Backend."""
    try:
        from scoutr.search import search_web

        results = search_web(
            PROBE_SEARCH,
            count=3,
            backend=backend,
            api_key=api_key,
            engines=engines,
            instance_url=instance_url,
        )
        if not results:
            return False, "Keine Treffer -- Backend erreichbar, aber ohne Ergebnis."
        return True, f"{len(results)} Treffer, erster: {results[0].title[:60]}"
    except Exception as exc:
        return False, f"{exc}"
