"""Tests fuer die Kommandozeile -- Netzwerk und LLM sind gemockt."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from scoutr import cli
from scoutr.models import PageResult, SearchResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "ENV_CANDIDATES", (), raising=False)
    monkeypatch.setattr("scoutr.config.ENV_CANDIDATES", ())
    monkeypatch.setenv("SCOUTR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCOUTR_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("SCOUTR_LOCATION", raising=False)
    from scoutr.config import reset_settings_cache

    reset_settings_cache()


def test_version() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert "scoutr" in result.output


def test_config_reports_complete_setup() -> None:
    result = runner.invoke(cli.app, ["config"])
    assert result.exit_code == 0
    assert "Konfiguration vollstaendig" in result.output


def test_config_reports_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    from scoutr.config import reset_settings_cache

    reset_settings_cache()
    result = runner.invoke(cli.app, ["config"])
    assert "OPENAI_API_KEY fehlt" in result.output


def test_search_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scoutr.tools.search_web",
        lambda query, count, country, lang, backend, api_key="": [
            SearchResult(
                title="Café Nordwand", url="https://cafe-nordwand.de/", snippet="WLAN", rank=1
            )
        ],
    )
    result = runner.invoke(cli.app, ["search", "cafés mönchengladbach", "-n", "3"])
    assert result.exit_code == 0
    assert "Café Nordwand" in result.output
    assert "cafe-nordwand.de" in result.output


def test_search_command_reports_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from scoutr.search import SearchError

    def failing(*args: Any, **kwargs: Any):
        raise SearchError("kein Netz")

    monkeypatch.setattr("scoutr.tools.search_web", failing)
    result = runner.invoke(cli.app, ["search", "egal"])
    assert result.exit_code == 1
    assert "kein Netz" in result.output


def _patch_fetch(monkeypatch: pytest.MonkeyPatch, page: PageResult) -> None:
    monkeypatch.setattr(
        "scoutr.fetch.Fetcher.fetch", lambda self, url, want_products=False: page
    )


def test_fetch_command(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_fetch(
        monkeypatch,
        PageResult(
            url="https://cafe.de/",
            final_url="https://cafe.de/",
            ok=True,
            title="Café",
            text="Sonntags von 10 bis 18 Uhr geöffnet.",
            source_domain="cafe.de",
        ),
    )
    result = runner.invoke(cli.app, ["fetch", "https://cafe.de/"])
    assert result.exit_code == 0
    assert "Sonntags von 10 bis 18 Uhr" in result.output


def test_fetch_command_reports_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_fetch(
        monkeypatch, PageResult(url="https://amazon.de/x", skipped_reason="blocked")
    )
    result = runner.invoke(cli.app, ["fetch", "https://amazon.de/x"])
    assert result.exit_code == 2
    assert "blocked" in result.output


def test_cache_command_shows_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scoutr.tools.search_web",
        lambda query, count, country, lang, backend, api_key="": [
            SearchResult(title="T", url="https://a.de/")
        ],
    )
    runner.invoke(cli.app, ["search", "frage"])
    assert "search=1" in runner.invoke(cli.app, ["cache"]).output
    assert "geloescht" in runner.invoke(cli.app, ["cache", "--clear"]).output


def test_history_is_empty_at_first() -> None:
    assert "Noch keine Recherchen" in runner.invoke(cli.app, ["history"]).output


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
def _llm(*contents: str):
    responses = [
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))]
        )
        for text in contents
    ]

    def completion(**kwargs: Any):
        return responses.pop(0)

    return completion


def test_one_shot_question(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("litellm.completion", _llm("Zwei Cafés gefunden. Quelle: cafe.de"))
    result = runner.invoke(cli.app, ["chat", "Cafés in MG?", "--no-stream"])
    assert result.exit_code == 0
    assert "Zwei Cafés gefunden" in result.output


def test_chat_aborts_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    from scoutr.config import reset_settings_cache

    reset_settings_cache()
    result = runner.invoke(cli.app, ["chat", "Frage"])
    assert result.exit_code == 1
    assert "Konfiguration unvollstaendig" in result.output


def test_slash_help_and_quit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("litellm.completion", _llm())
    result = runner.invoke(cli.app, ["chat"], input="/help\n/quit\n")
    assert result.exit_code == 0
    assert "/location" in result.output
    assert "Bis dann" in result.output


def test_slash_location_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("litellm.completion", _llm())
    result = runner.invoke(
        cli.app, ["chat"], input="/location Köln\n/model openai/gpt-4o-mini\n/quit\n"
    )
    assert "Ortsfilter: Köln" in result.output
    assert "Modell: openai/gpt-4o-mini" in result.output


def test_slash_clear_and_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("litellm.completion", _llm())
    result = runner.invoke(cli.app, ["chat"], input="/clear\n/quatsch\n/quit\n")
    assert "Verlauf verworfen" in result.output
    assert "Unbekannter Befehl" in result.output


def test_slash_export_writes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("litellm.completion", _llm("Die Antwort mit Quelle: a.de"))
    result = runner.invoke(cli.app, ["chat", "--no-stream"], input="Frage\n/export md\n/quit\n")
    assert "Gespeichert:" in result.output
    exported = list(tmp_path.glob("scoutr-*.md"))
    assert exported and "Die Antwort" in exported[0].read_text(encoding="utf-8")


def test_export_command_uses_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("litellm.completion", _llm("Antwort aus dem Verlauf"))
    runner.invoke(cli.app, ["chat", "Frage", "--no-stream"])
    result = runner.invoke(cli.app, ["export", "html"])
    assert "Gespeichert:" in result.output
    exported = list(tmp_path.glob("scoutr-*.html"))
    assert exported and "Antwort aus dem Verlauf" in exported[0].read_text(encoding="utf-8")


def test_export_command_without_history() -> None:
    result = runner.invoke(cli.app, ["export", "md"])
    assert result.exit_code == 1
    assert "Verlauf ist leer" in result.output


def test_image_input_feeds_the_research(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    image = tmp_path / "foto.jpg"
    image.write_bytes(b"\xff\xd8\xff\xe0 nicht wirklich ein JPEG")
    seen: list[Any] = []

    def completion(**kwargs: Any):
        seen.append(kwargs["messages"])
        text = (
            "Ein silbernes Notebook mit Logo. Suchbegriffe: Notebook silber"
            if len(seen) == 1
            else "Das ist vermutlich ein Lenovo. Quelle: lenovo.com"
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))]
        )

    monkeypatch.setattr("litellm.completion", completion)
    result = runner.invoke(
        cli.app, ["chat", "Was ist das?", "--image", str(image), "--no-stream"]
    )
    assert result.exit_code == 0
    assert "Suchbegriffe" in result.output
    # Das Vision-Modell bekam tatsaechlich ein Bild.
    assert seen[0][0]["content"][1]["type"] == "image_url"
    # Die Beschreibung floss in die Recherche ein.
    assert "silbernes Notebook" in json.dumps(seen[1], ensure_ascii=False)


def test_missing_image_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app, ["chat", "Frage", "--image", str(tmp_path / "gibtsnicht.jpg")]
    )
    assert result.exit_code == 1
    assert "nicht gefunden" in result.output


def test_bare_question_is_routed_to_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys_argv := __import__("sys"), "argv", ["scoutr", "Meine Frage"])
    monkeypatch.setattr("litellm.completion", _llm("Antwort"))
    called: list[list[str]] = []
    monkeypatch.setattr(cli, "app", lambda: called.append(list(sys_argv.argv)))
    cli.main()
    assert called[0][1] == "chat"


def test_known_subcommand_is_not_rerouted(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(sys, "argv", ["scoutr", "config"])
    called: list[list[str]] = []
    monkeypatch.setattr(cli, "app", lambda: called.append(list(sys.argv)))
    cli.main()
    assert called[0] == ["scoutr", "config"]


def test_bare_flags_are_routed_to_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(sys, "argv", ["scoutr", "--location", "Köln", "--lang", "de"])
    called: list[list[str]] = []
    monkeypatch.setattr(cli, "app", lambda: called.append(list(sys.argv)))
    cli.main()
    assert called[0][1] == "chat"


def test_location_flag_reaches_the_search_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """--location/--lang landen in Suchanfrage und API-Parametern."""
    captured: dict[str, Any] = {}

    def fake_search(query, count, country, lang, backend, api_key=""):
        captured.update(country=country, lang=lang)
        return [SearchResult(title="T", url="https://a.de/")]

    monkeypatch.setattr("scoutr.tools.search_web", fake_search)

    tool_call = SimpleNamespace(
        id="c1",
        type="function",
        index=0,
        function=SimpleNamespace(name="web_search", arguments='{"query": "cafés köln"}'),
    )
    responses = [
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool_call]))]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Fertig", tool_calls=None))]
        ),
    ]
    monkeypatch.setattr("litellm.completion", lambda **kwargs: responses.pop(0))

    result = runner.invoke(
        cli.app,
        ["chat", "Cafés?", "--location", "Köln", "--lang", "at", "--no-stream"],
    )
    assert result.exit_code == 0
    assert captured == {"country": "at", "lang": "at"}


def test_version_flag(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    import sys

    monkeypatch.setattr(sys, "argv", ["scoutr", "--version"])
    monkeypatch.setattr(cli, "app", lambda: pytest.fail("app haette nicht laufen duerfen"))
    cli.main()
    assert "scoutr" in capsys.readouterr().out


def test_help_is_not_rerouted(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(sys, "argv", ["scoutr", "--help"])
    called: list[list[str]] = []
    monkeypatch.setattr(cli, "app", lambda: called.append(list(sys.argv)))
    cli.main()
    assert called[0] == ["scoutr", "--help"]
