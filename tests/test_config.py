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


# ---------------------------------------------------------------------------
# Modell-IDs pruefen
# ---------------------------------------------------------------------------
def test_valid_models_pass() -> None:
    for model in (
        "anthropic/claude-sonnet-4-6",
        "openai/gpt-4o",
        "nvidia_nim/meta/llama-3.3-70b-instruct",
        "nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b",
    ):
        assert config.model_problem(model) == "", model


def test_missing_provider_prefix_is_caught_with_a_suggestion() -> None:
    """`nvidia/...` statt `nvidia_nim/nvidia/...` -- der haeufige Fehlgriff."""
    problem = config.model_problem("nvidia/nemotron-3-ultra-550b-a55b")
    assert "keinem Anbieter zuordnen" in problem
    assert "nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b" in problem


def test_suggestion_only_when_the_prefix_matches() -> None:
    """Unter einem gueltigen Praefix akzeptiert LiteLLM jede ID -- also nicht raten."""
    assert config.suggest_model("quatsch/modell") == ""
    assert config.suggest_model("voelliger-quatsch") == ""
    problem = config.model_problem("quatsch/modell")
    assert "Meintest du" not in problem
    assert "Beispiele:" in problem


def test_broken_model_shows_up_in_missing_requirements(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SCOUTR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCOUTR_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
    problems = config.get_settings().missing_requirements()
    assert any("nvidia_nim/nvidia/" in problem for problem in problems)


def test_resolve_model_reports_the_provider() -> None:
    assert config.resolve_model("nvidia_nim/meta/llama-3.3-70b-instruct") == "nvidia_nim"
    assert config.resolve_model("anthropic/claude-sonnet-4-6") == "anthropic"
    assert config.resolve_model("quatsch/modell") == ""
    assert config.resolve_model("") == ""


# ---------------------------------------------------------------------------
# Kwargs je Modell -- gemischte Anbieter
# ---------------------------------------------------------------------------
def test_kwargs_for_the_same_provider_are_passed_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = config.Settings(
        model="ollama_chat/gemma4:12b", api_base="http://localhost:11434"
    )
    assert settings.llm_kwargs_for("ollama_chat/llava:7b") == {
        "api_base": "http://localhost:11434",
        "num_ctx": 16384,
    }


def test_kwargs_for_a_foreign_provider_are_dropped() -> None:
    """Die lokale Ollama-Basis-URL darf nie an einen Cloud-Anbieter gehen."""
    settings = config.Settings(
        model="ollama_chat/gemma4:12b",
        api_base="http://localhost:11434",
        vision_model="anthropic/claude-sonnet-4-6",
    )
    assert settings.llm_kwargs_for(settings.effective_vision_model) == {}


def test_main_model_key_is_not_sent_to_other_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-geheim")
    settings = config.Settings(
        model="anthropic/claude-sonnet-4-6", subagent_model="ollama_chat/qwen3:1.7b"
    )
    kwargs = settings.llm_kwargs_for(settings.effective_subagent_model)
    # Kein fremder Key -- aber das Kontextfenster bekommt das lokale Modell.
    assert "api_key" not in kwargs and "api_base" not in kwargs
    assert kwargs["num_ctx"] == 16384



# ---------------------------------------------------------------------------
# Kontextfenster fuer lokale Modelle
# ---------------------------------------------------------------------------
def test_ollama_models_get_a_real_context_window() -> None:
    """Ohne num_ctx nimmt Ollama 2048-4096 Token und wirft bei Ueberlauf
    still den Verlaufsanfang weg -- "er erinnert sich nicht an die letzte
    Frage" ist genau dieses Symptom."""
    settings = config.Settings(model="ollama_chat/gemma4:12b")
    assert settings.llm_kwargs()["num_ctx"] == 16384
    assert config.Settings(model="ollama/llama3.1").llm_kwargs()["num_ctx"] == 16384


def test_cloud_models_never_get_num_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fremde Anbieter kennen den Parameter nicht -- er darf nie mitgehen."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    for model in ("anthropic/claude-sonnet-4-6", "openai/gpt-4o", "nvidia_nim/meta/llama-3.3"):
        settings = config.Settings(model=model)
        assert "num_ctx" not in settings.llm_kwargs(), model


def test_context_tokens_is_configurable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SCOUTR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCOUTR_MODEL", "ollama_chat/gemma4:12b")
    monkeypatch.setenv("SCOUTR_CONTEXT_TOKENS", "32768")
    assert config.get_settings().llm_kwargs()["num_ctx"] == 32768


def test_context_tokens_zero_uses_the_ollama_default() -> None:
    settings = config.Settings(model="ollama_chat/x", context_tokens=0)
    assert "num_ctx" not in settings.llm_kwargs()


# ---------------------------------------------------------------------------
# Parallelitaet der Subagenten
# ---------------------------------------------------------------------------
def test_local_models_run_two_subagents() -> None:
    """Mehr bringt lokal nichts -- die GPU rechnet ohnehin nacheinander."""
    settings = config.Settings(model="ollama_chat/gemma4:12b")
    assert settings.effective_parallel == 2


def test_cloud_models_run_four_subagents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert config.Settings(model="anthropic/claude-sonnet-4-6").effective_parallel == 4


def test_parallelism_follows_the_subagent_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entscheidend ist, wo die Subagenten laufen -- nicht das Hauptmodell."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    settings = config.Settings(
        model="anthropic/claude-sonnet-4-6", subagent_model="ollama_chat/qwen3:1.7b"
    )
    assert settings.effective_parallel == 2


def test_explicit_parallelism_wins() -> None:
    settings = config.Settings(model="ollama_chat/x", subagent_parallel=6)
    assert settings.effective_parallel == 6
