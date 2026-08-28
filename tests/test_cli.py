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
    monkeypatch.setenv("SCOUTR_SUBAGENTS_AUTO", "false")
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
        lambda query, **kwargs: [
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
        lambda query, **kwargs: [
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

    def fake_search(query, **kwargs):
        captured.update(country=kwargs["country"], lang=kwargs["lang"])
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


# ---------------------------------------------------------------------------
# install-model
# ---------------------------------------------------------------------------
def _fake_ollama(monkeypatch: pytest.MonkeyPatch, *, tool_calling: bool = True) -> dict[str, Any]:
    """Stellt eine vollstaendig funktionierende Ollama-Umgebung nach."""
    from types import SimpleNamespace

    from scoutr import local_model as lm

    state: dict[str, Any] = {"pulled": []}
    monkeypatch.setattr(lm, "ollama_binary", lambda: "/usr/bin/ollama")
    monkeypatch.setattr(lm, "server_running", lambda *a, **k: True)
    monkeypatch.setattr(lm, "installed_models", lambda *a, **k: [])
    monkeypatch.setattr(lm, "model_size_gb", lambda *a, **k: 4.7)
    monkeypatch.setattr(lm, "total_memory_gb", lambda: 16.0)
    monkeypatch.setattr(lm, "gpu_hint", lambda: "")

    def pull(name: str, binary: str | None = None):
        state["pulled"].append(name)
        yield "pulling manifest"

    monkeypatch.setattr(lm, "pull_model", pull)

    call = SimpleNamespace(function=SimpleNamespace(name="web_search"))
    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="" if tool_calling else "Einfach so geantwortet.",
                        tool_calls=[call] if tool_calling else None,
                    )
                )
            ]
        ),
    )
    return state


def test_install_model_writes_the_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = _fake_ollama(monkeypatch)
    target = tmp_path / ".env"
    result = runner.invoke(
        cli.app,
        ["install-model", "--model", "qwen2.5:7b", "--yes", "--env-file", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert state["pulled"] == ["qwen2.5:7b"]
    content = target.read_text(encoding="utf-8")
    # Muss ollama_chat sein -- das nackte ollama kann kein Tool-Calling.
    assert "SCOUTR_MODEL=ollama_chat/qwen2.5:7b" in content
    assert "SCOUTR_API_BASE=http://localhost:11434" in content
    assert "Werkzeug aufgerufen" in result.output
    # --yes laedt kein Vision-Modell ungefragt nach.
    assert "SCOUTR_VISION_MODEL" not in content


def test_install_model_refuses_a_model_without_tool_calling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ein Modell, das nur redet, wird nicht stillschweigend eingetragen."""
    _fake_ollama(monkeypatch, tool_calling=False)
    target = tmp_path / ".env"
    result = runner.invoke(
        cli.app,
        ["install-model", "--model", "kaputt:1b", "--env-file", str(target)],
        input="n\n",
    )
    assert result.exit_code == 1
    assert "kann keine Werkzeuge aufrufen" in result.output
    assert not target.exists()


def test_install_model_aborts_without_ollama(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scoutr import local_model as lm

    monkeypatch.setattr(lm, "ollama_binary", lambda: None)
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    result = runner.invoke(
        cli.app,
        ["install-model", "--env-file", str(tmp_path / ".env")],
        input="n\n",
    )
    assert result.exit_code == 1
    # Der Befehl wird gezeigt, bevor irgendetwas laeuft.
    assert "install.sh" in result.output


def test_install_model_skips_an_already_loaded_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scoutr import local_model as lm

    state = _fake_ollama(monkeypatch)
    monkeypatch.setattr(lm, "installed_models", lambda *a, **k: ["qwen2.5:7b"])
    result = runner.invoke(
        cli.app,
        ["install-model", "--model", "qwen2.5:7b", "--yes", "--env-file", str(tmp_path / ".env")],
    )
    assert result.exit_code == 0
    assert state["pulled"] == []
    assert "bereits geladen" in result.output


# ---------------------------------------------------------------------------
# Unbekannte Unterbefehle
# ---------------------------------------------------------------------------
def _main_with(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> list[list[str]]:
    """Ruft cli.main() mit *args* und faengt ab, womit die App gestartet wuerde."""
    import sys

    monkeypatch.setattr(sys, "argv", ["scoutr", *args])
    called: list[list[str]] = []
    monkeypatch.setattr(cli, "app", lambda: called.append(list(sys.argv)))
    cli.main()
    return called


def test_unknown_subcommand_is_not_sent_to_the_llm(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`scoutr install-modell` darf keine Recherche ausloesen."""
    import sys

    monkeypatch.setattr(sys, "argv", ["scoutr", "install-modell"])
    monkeypatch.setattr(cli, "app", lambda: pytest.fail("haette nicht starten duerfen"))
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 2
    output = capsys.readouterr().out
    assert "Unbekannter Befehl" in output
    assert "install-model" in output  # Vorschlag
    assert "Anfuehrungszeichen" in output


def test_outdated_installation_gets_a_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Genau der Fall: der Befehl existiert, die Installation kennt ihn nicht."""
    import sys

    monkeypatch.setattr(cli, "COMMANDS", cli.COMMANDS - {"install-model"})
    monkeypatch.setattr(sys, "argv", ["scoutr", "install-model"])
    monkeypatch.setattr(cli, "app", lambda: pytest.fail("haette nicht starten duerfen"))
    with pytest.raises(SystemExit):
        cli.main()
    assert "veraltet" in capsys.readouterr().out


def test_known_subcommands_still_run(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _main_with(monkeypatch, ["install-model"])[0] == ["scoutr", "install-model"]
    assert _main_with(monkeypatch, ["install-browser"])[0] == ["scoutr", "install-browser"]


def test_real_questions_are_never_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fragen mit Leerzeichen, ohne Bindestrich oder mit Grossbuchstaben bleiben Fragen."""
    for question in (
        "cafés in mönchengladbach mit wlan",
        "hallo",
        "E-Bike",
        "was-ist-das?",
        "Laptop-Test",
    ):
        called = _main_with(monkeypatch, [question])
        assert called[0][1] == "chat", question


def test_hyphenated_single_word_question_needs_quotes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Preis der Eindeutigkeit: `scoutr e-bike` wird abgefangen, mit Hinweis."""
    import sys

    monkeypatch.setattr(sys, "argv", ["scoutr", "e-bike"])
    monkeypatch.setattr(cli, "app", lambda: pytest.fail("haette nicht starten duerfen"))
    with pytest.raises(SystemExit):
        cli.main()
    assert 'scoutr "e-bike"' in capsys.readouterr().out


def test_install_model_adds_a_vision_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Text- und Vision-Modell in einem Durchlauf."""
    from scoutr import local_model as lm

    state = _fake_ollama(monkeypatch)
    monkeypatch.setattr(lm, "verify_vision", lambda *a, **k: (True, "Testbild erkannt"))
    target = tmp_path / ".env"
    result = runner.invoke(
        cli.app,
        [
            "install-model",
            "--model", "qwen2.5:7b",
            "--vision-model", "llava:7b",
            "--yes",
            "--env-file", str(target),
        ],
    )
    assert result.exit_code == 0, result.output
    assert state["pulled"] == ["qwen2.5:7b", "llava:7b"]
    content = target.read_text(encoding="utf-8")
    assert "SCOUTR_MODEL=ollama_chat/qwen2.5:7b" in content
    assert "SCOUTR_VISION_MODEL=ollama_chat/llava:7b" in content
    assert "--image" in result.output


def test_vision_only_leaves_the_text_model_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scoutr import local_model as lm

    state = _fake_ollama(monkeypatch)
    monkeypatch.setattr(lm, "verify_vision", lambda *a, **k: (True, "Testbild erkannt"))
    target = tmp_path / ".env"
    target.write_text("SCOUTR_MODEL=anthropic/claude-sonnet-4-6\n", encoding="utf-8")
    result = runner.invoke(
        cli.app,
        ["install-model", "--vision-only", "--vision-model", "llava:7b", "--env-file", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert state["pulled"] == ["llava:7b"]
    content = target.read_text(encoding="utf-8")
    assert "SCOUTR_VISION_MODEL=ollama_chat/llava:7b" in content
    # Das bestehende Hauptmodell bleibt unangetastet.
    assert "SCOUTR_MODEL=anthropic/claude-sonnet-4-6" in content


def test_blind_vision_model_is_not_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ein Modell, das das Testbild nicht erkennt, wird nicht eingetragen."""
    from scoutr import local_model as lm

    _fake_ollama(monkeypatch)
    monkeypatch.setattr(
        lm, "verify_vision", lambda *a, **k: (False, "Das Modell hat das Testbild nicht erkannt")
    )
    target = tmp_path / ".env"
    result = runner.invoke(
        cli.app,
        [
            "install-model",
            "--model", "qwen2.5:7b",
            "--vision-model", "blind:1b",
            "--no-subagents",
            "--env-file", str(target),
        ],
        input="n\n",
    )
    assert result.exit_code == 0, result.output
    content = target.read_text(encoding="utf-8")
    assert "SCOUTR_MODEL=ollama_chat/qwen2.5:7b" in content
    assert "SCOUTR_VISION_MODEL" not in content


def test_no_vision_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = _fake_ollama(monkeypatch)
    target = tmp_path / ".env"
    result = runner.invoke(
        cli.app,
        [
            "install-model",
            "--model", "qwen2.5:7b",
            "--no-vision",
            "--no-subagents",
            "--env-file", str(target),
        ],
    )
    assert result.exit_code == 0, result.output
    assert state["pulled"] == ["qwen2.5:7b"]
    assert "SCOUTR_VISION_MODEL" not in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Bild aus einem Ordner
# ---------------------------------------------------------------------------
def _png(path: Path) -> Path:
    from scoutr.local_model import solid_png

    path.write_bytes(solid_png())
    return path


def _vision_llm(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Vision-Antwort, danach eine normale Antwort."""
    seen: list[Any] = []

    def completion(**kwargs: Any):
        seen.append(kwargs["messages"])
        text = (
            "Ein rotes Fahrrad. Suchbegriffe: rotes Fahrrad"
            if len(seen) == 1
            else "Gefunden. Quelle: beispiel.de"
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))]
        )

    monkeypatch.setattr("litellm.completion", completion)
    return seen


def test_single_image_in_a_folder_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--image ordner/` nimmt das eine Bild, ohne zu fragen."""
    folder = tmp_path / "hallo1234"
    folder.mkdir()
    _png(folder / "foto.jpg")
    _vision_llm(monkeypatch)

    result = runner.invoke(
        cli.app, ["chat", "Was ist das?", "--image", str(folder), "--no-stream"]
    )
    assert result.exit_code == 0, result.output
    assert "Ein Bild gefunden: foto.jpg" in result.output
    assert "Suchbegriffe" in result.output


def test_several_images_are_offered_newest_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os
    import time

    folder = tmp_path / "hallo1234"
    folder.mkdir()
    old = _png(folder / "alt.png")
    new = _png(folder / "neu.png")
    os.utime(old, (time.time() - 9000, time.time() - 9000))
    _vision_llm(monkeypatch)

    result = runner.invoke(
        cli.app, ["chat", "Was ist das?", "--image", str(folder), "--no-stream"], input="1\n"
    )
    assert result.exit_code == 0, result.output
    assert "2 Bilder" in result.output
    # Das neuere steht oben und ist die Vorauswahl.
    position_new = result.output.index(new.name)
    position_old = result.output.index(old.name)
    assert position_new < position_old


def test_folder_without_images_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = tmp_path / "leer"
    folder.mkdir()
    (folder / "notiz.txt").write_text("kein Bild", encoding="utf-8")
    result = runner.invoke(cli.app, ["chat", "Frage", "--image", str(folder)])
    assert result.exit_code == 1
    assert "Keine Bilder" in result.output


def test_missing_path_is_reported(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["chat", "Frage", "--image", str(tmp_path / "weg.jpg")])
    assert result.exit_code == 1
    assert "nicht gefunden" in result.output


def test_slash_image_accepts_a_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    folder = tmp_path / "hallo1234"
    folder.mkdir()
    _png(folder / "ding.png")
    _vision_llm(monkeypatch)

    result = runner.invoke(cli.app, ["chat", "--no-stream"], input=f"/image {folder}\n/quit\n")
    assert "Ein Bild gefunden: ding.png" in result.output


def test_image_is_sent_as_data_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Das Bild geht als data:-URL ans Vision-Modell, nicht als Pfad."""
    folder = tmp_path / "hallo1234"
    folder.mkdir()
    _png(folder / "foto.png")
    seen = _vision_llm(monkeypatch)

    runner.invoke(cli.app, ["chat", "Was ist das?", "--image", str(folder), "--no-stream"])
    content = seen[0][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_memory_is_freed_before_the_sight_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sonst liegen Text- und Vision-Modell gleichzeitig im VRAM."""
    from scoutr import local_model as lm

    _fake_ollama(monkeypatch)
    order: list[str] = []
    monkeypatch.setattr(lm, "free_memory", lambda *a, **k: order.append("frei") or ["qwen2.5:7b"])
    monkeypatch.setattr(
        lm, "verify_vision", lambda *a, **k: order.append("sehtest") or (True, "erkannt")
    )
    result = runner.invoke(
        cli.app,
        [
            "install-model",
            "--model", "qwen2.5:7b",
            "--vision-model", "llava:7b",
            "--yes",
            "--env-file", str(tmp_path / ".env"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert order.index("frei") < order.index("sehtest")
    assert "Speicher freigegeben" in result.output


def test_out_of_memory_offers_a_smaller_vision_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Speichermangel darf nicht als 'Modell ist blind' enden."""
    from scoutr import local_model as lm

    _fake_ollama(monkeypatch)
    monkeypatch.setattr(lm, "free_memory", lambda *a, **k: [])
    attempts: list[str] = []

    def verify(model_id: str, *args: Any, **kwargs: Any):
        attempts.append(model_id)
        if "moondream" in model_id:
            return True, "Testbild erkannt"
        return False, "Der Ollama-Runner ist abgestuerzt -- resource limitations"

    monkeypatch.setattr(lm, "verify_vision", verify)
    target = tmp_path / ".env"
    result = runner.invoke(
        cli.app,
        ["install-model", "--model", "qwen2.5:7b", "--no-subagents", "--env-file", str(target)],
        input="1\n\n1\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert any("moondream" in attempt for attempt in attempts)
    assert "SCOUTR_VISION_MODEL=ollama_chat/moondream" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Empfohlen oder genommen?
# ---------------------------------------------------------------------------
def test_without_yes_the_user_confirms_the_recommendation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Der VRAM waehlt die Vorauswahl -- bestaetigt wird sie vom Nutzer."""
    from scoutr import local_model as lm

    state = _fake_ollama(monkeypatch)
    monkeypatch.setattr(lm, "gpu_vram_gb", lambda: 12.0)
    target = tmp_path / ".env"
    # Leere Eingabe = Vorauswahl uebernehmen, danach kein Vision-Modell.
    result = runner.invoke(
        cli.app, ["install-model", "--no-vision", "--no-subagents", "--env-file", str(target)],
        input="\n",
    )
    assert result.exit_code == 0, result.output
    assert "Auswahl" in result.output
    # Fuer 12 GB ist das ein Modell, das auch Bilder kann.
    assert state["pulled"] == ["gemma4:12b"]


def test_yes_takes_the_recommendation_without_asking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--yes darf nicht an einer Rueckfrage haengen bleiben."""
    from scoutr import local_model as lm

    state = _fake_ollama(monkeypatch)
    monkeypatch.setattr(lm, "gpu_vram_gb", lambda: 12.0)
    target = tmp_path / ".env"
    result = runner.invoke(
        cli.app, ["install-model", "--yes", "--env-file", str(target)], input=""
    )
    assert result.exit_code == 0, result.output
    assert "Ohne Rueckfrage gewaehlt" in result.output
    assert state["pulled"] == ["gemma4:12b"]


def test_a_chosen_model_that_is_too_big_is_only_flagged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Die Hardware empfiehlt -- sie verbietet nichts."""
    from scoutr import local_model as lm

    state = _fake_ollama(monkeypatch)
    monkeypatch.setattr(lm, "gpu_vram_gb", lambda: 12.0)
    target = tmp_path / ".env"
    result = runner.invoke(
        cli.app,
        [
            "install-model",
            "--model", "gemma4:26b",
            "--no-vision",
            "--no-subagents",
            "--env-file", str(target),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Hinweis:" in result.output
    assert "braucht etwa 24 GB" in result.output
    # Trotz Warnung wird geladen, ohne Rueckfrage.
    assert state["pulled"] == ["gemma4:26b"]
    assert "SCOUTR_MODEL=ollama_chat/gemma4:26b" in target.read_text(encoding="utf-8")


def test_no_question_is_asked_about_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ohne jede Eingabe muss der Lauf durchgehen -- es wird nichts gefragt."""
    from scoutr import local_model as lm

    _fake_ollama(monkeypatch)
    monkeypatch.setattr(lm, "gpu_vram_gb", lambda: 4.0)
    result = runner.invoke(
        cli.app,
        [
            "install-model",
            "--model", "qwen2.5:14b",
            "--no-vision",
            "--no-subagents",
            "--env-file", str(tmp_path / ".env"),
        ],
        input="",
    )
    assert result.exit_code == 0, result.output
    assert "Trotzdem laden" not in result.output


def test_a_free_model_name_is_not_second_guessed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unbekannte Namen laufen ohne Warnung durch -- wir kennen ihre Groesse nicht."""
    from scoutr import local_model as lm

    state = _fake_ollama(monkeypatch)
    monkeypatch.setattr(lm, "gpu_vram_gb", lambda: 4.0)
    result = runner.invoke(
        cli.app,
        [
            "install-model",
            "--model", "eigenes-modell:99b",
            "--no-vision",
            "--no-subagents",
            "--yes",
            "--env-file", str(tmp_path / ".env"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert state["pulled"] == ["eigenes-modell:99b"]
    assert "braucht etwa" not in result.output


def test_slash_image_respects_no_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """/image lief immer im Streaming-Modus, egal was --no-stream sagte."""
    image = tmp_path / "foto.png"
    from scoutr.local_model import solid_png

    image.write_bytes(solid_png())
    seen_stream: list[bool] = []

    from scoutr.agent import Agent

    original_ask = Agent.ask

    def spy_ask(self, question, *, stream=True):
        seen_stream.append(stream)
        return original_ask(self, question, stream=stream)

    monkeypatch.setattr(Agent, "ask", spy_ask)
    monkeypatch.setattr(
        "litellm.completion",
        _llm("Ein rotes Bild. Suchbegriffe: rot", "Fertig recherchiert."),
    )
    result = runner.invoke(
        cli.app, ["chat", "--no-stream"], input=f"/image {image}\n/quit\n"
    )
    assert result.exit_code == 0, result.output
    assert seen_stream == [False]
