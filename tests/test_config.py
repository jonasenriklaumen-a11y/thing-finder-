"""Tests fuer Konfiguration und `.env`-Handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from scoutr import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        "SCOUTR_MODEL",
        "SCOUTR_SEARCH_BACKEND",
        "SCOUTR_LOCATION",
        "SCOUTR_LANG",
        "SCOUTR_COUNTRY",
        "SCOUTR_MAX_TOOL_CALLS",
        "SCOUTR_DATA_DIR",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "BRAVE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "ENV_CANDIDATES", ())
    config.reset_settings_cache()


def test_provider_and_key_name() -> None:
    assert config.provider_of("anthropic/claude-sonnet-4-6") == "anthropic"
    assert config.api_key_name_for("anthropic/claude-sonnet-4-6") == "ANTHROPIC_API_KEY"
    assert config.api_key_name_for("openai/gpt-4o") == "OPENAI_API_KEY"
    assert config.api_key_name_for("ollama/llama3.1") == ""


def test_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCOUTR_DATA_DIR", str(tmp_path / "data"))
    settings = config.get_settings()
    assert settings.model == config.DEFAULT_MODEL
    assert settings.search_backend == "duckduckgo"
    assert settings.max_tool_calls == 20
    assert settings.db_path.parent.is_dir()


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCOUTR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCOUTR_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("SCOUTR_MAX_TOOL_CALLS", "5")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234")
    settings = config.get_settings()
    assert settings.model == "openai/gpt-4o"
    assert settings.max_tool_calls == 5
    assert settings.api_key == "sk-test-1234"
    assert settings.missing_requirements() == []


def test_missing_requirements_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SCOUTR_DATA_DIR", str(tmp_path / "data"))
    settings = config.get_settings()
    assert any("ANTHROPIC_API_KEY" in problem for problem in settings.missing_requirements())


def test_write_env_file_preserves_comments(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("# Kommentar\nSCOUTR_MODEL=alt\nFREMD=behalten\n", encoding="utf-8")

    config.write_env_file({"SCOUTR_MODEL": "neu", "SCOUTR_LANG": "en"}, target)

    content = target.read_text(encoding="utf-8")
    assert "# Kommentar" in content
    assert "SCOUTR_MODEL=neu" in content
    assert "FREMD=behalten" in content
    assert "SCOUTR_LANG=en" in content
    assert "SCOUTR_MODEL=alt" not in content


# ---------------------------------------------------------------------------
# Weitere Anbieter
# ---------------------------------------------------------------------------
def test_nvidia_nim_key_is_recognised() -> None:
    """NVIDIA-Modell-IDs enthalten mehrere Schraegstriche."""
    assert config.provider_of("nvidia_nim/meta/llama-3.3-70b-instruct") == "nvidia_nim"
    assert (
        config.api_key_name_for("nvidia_nim/meta/llama-3.3-70b-instruct")
        == "NVIDIA_NIM_API_KEY"
    )


def test_nvidia_settings_are_complete_with_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SCOUTR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCOUTR_MODEL", "nvidia_nim/meta/llama-3.3-70b-instruct")
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nvapi-test-1234")
    settings = config.get_settings()
    assert settings.missing_requirements() == []
    assert settings.llm_kwargs()["api_key"] == "nvapi-test-1234"


def test_nvidia_without_key_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SCOUTR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCOUTR_MODEL", "nvidia_nim/meta/llama-3.3-70b-instruct")
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    problems = config.get_settings().missing_requirements()
    assert any("NVIDIA_NIM_API_KEY" in problem for problem in problems)


@pytest.mark.parametrize(
    ("model", "key_name"),
    [
        ("xai/grok-2", "XAI_API_KEY"),
        ("together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo", "TOGETHER_API_KEY"),
        ("cerebras/llama-3.3-70b", "CEREBRAS_API_KEY"),
        ("perplexity/sonar", "PERPLEXITYAI_API_KEY"),
    ],
)
def test_further_providers(model: str, key_name: str) -> None:
    assert config.api_key_name_for(model) == key_name


def test_generic_key_covers_unlisted_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jeder LiteLLM-Anbieter laesst sich ueber SCOUTR_API_KEY nutzen."""
    monkeypatch.delenv("SCOUTR_API_KEY", raising=False)
    assert config.api_key_name_for("exotisch/modell") == ""

    monkeypatch.setenv("SCOUTR_API_KEY", "geheim")
    assert config.api_key_name_for("exotisch/modell") == "SCOUTR_API_KEY"


def test_generic_key_does_not_override_known_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCOUTR_API_KEY", "geheim")
    assert config.api_key_name_for("anthropic/claude-sonnet-4-6") == "ANTHROPIC_API_KEY"
    # Ollama braucht weiterhin keinen Key.
    assert config.api_key_name_for("ollama/llama3.1") == ""
