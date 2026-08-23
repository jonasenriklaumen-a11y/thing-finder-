"""Tests fuer die lokale Modell-Einrichtung -- Ollama ist durchgaengig gemockt."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from scoutr import local_model as lm

TAGS = {
    "models": [
        {"name": "qwen2.5:7b", "size": 4_700_000_000},
        {"name": "llama3.1:8b", "size": 4_900_000_000},
    ]
}


def _patch_tags(monkeypatch: pytest.MonkeyPatch, payload: Any = TAGS, status: int = 200) -> None:
    def fake_get(url, **kwargs):
        assert url.endswith("/api/tags")
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)


# ---------------------------------------------------------------------------
# Modellauswahl
# ---------------------------------------------------------------------------
def test_all_models_use_the_tool_calling_prefix() -> None:
    """`ollama/` kann kein Tool-Calling -- es muss `ollama_chat/` sein."""
    assert lm.MODEL_PREFIX == "ollama_chat"
    for model in lm.LOCAL_MODELS:
        assert model.model_id.startswith("ollama_chat/")


def test_recommendation_fits_the_memory() -> None:
    assert lm.recommend_model(4).needs_gb <= 4
    assert lm.recommend_model(8).needs_gb <= 8
    assert lm.recommend_model(64).name == "qwen2.5:32b"


def test_tiny_machines_get_the_smallest_model() -> None:
    """Auch bei 2 GB bekommt der Nutzer einen Vorschlag statt einer Fehlermeldung."""
    chosen = lm.recommend_model(2)
    assert chosen.needs_gb == min(model.needs_gb for model in lm.LOCAL_MODELS)


def test_recommendation_without_memory_info() -> None:
    assert lm.recommend_model(None) in lm.LOCAL_MODELS


def test_env_values() -> None:
    values = lm.env_values(lm.LOCAL_MODELS[0])
    assert values["SCOUTR_MODEL"] == "ollama_chat/qwen2.5:7b"
    assert values["SCOUTR_API_BASE"] == "http://localhost:11434"


def test_env_values_adds_the_prefix_to_a_bare_name() -> None:
    assert lm.env_values("qwen2.5:14b")["SCOUTR_MODEL"] == "ollama_chat/qwen2.5:14b"


def test_env_values_keeps_a_full_id() -> None:
    assert lm.env_values("ollama_chat/eigenes:tag")["SCOUTR_MODEL"] == "ollama_chat/eigenes:tag"


def test_env_model_id_resolves_in_litellm() -> None:
    """Die erzeugte ID muss LiteLLM auch wirklich zuordenbar sein."""
    from scoutr.config import resolve_model

    assert resolve_model(lm.env_values(lm.LOCAL_MODELS[0])["SCOUTR_MODEL"]) == "ollama_chat"


# ---------------------------------------------------------------------------
# Server und Modelle
# ---------------------------------------------------------------------------
def test_server_running(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tags(monkeypatch)
    assert lm.server_running() is True


def test_server_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(url, **kwargs):
        raise httpx.ConnectError("nichts da", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", failing)
    assert lm.server_running() is False
    assert lm.installed_models() == []


def test_installed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tags(monkeypatch)
    assert lm.installed_models() == ["qwen2.5:7b", "llama3.1:8b"]


def test_model_size(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tags(monkeypatch)
    assert lm.model_size_gb("qwen2.5:7b") == 4.7
    assert lm.model_size_gb("gibtsnicht") is None


def test_start_server_without_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm, "ollama_binary", lambda: None)
    assert lm.start_server() is False


def test_start_server_short_circuits_when_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm, "ollama_binary", lambda: "/usr/bin/ollama")
    monkeypatch.setattr(lm, "server_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        lm.subprocess, "Popen", lambda *a, **k: pytest.fail("haette nicht starten duerfen")
    )
    assert lm.start_server() is True


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------
def test_install_command_is_shown_not_run_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Installationsbefehl muss sichtbar sein -- nichts laeuft ungefragt."""
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    command = lm.install_command()
    assert command is not None
    assert "ollama.com/install.sh" in " ".join(command)


def test_install_runs_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    seen: list[list[str]] = []
    assert lm.install_ollama(runner=lambda cmd: seen.append(cmd) or 0) is True
    assert "install.sh" in " ".join(seen[0])


def test_failed_install_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    assert lm.install_ollama(runner=lambda cmd: 1) is False


def test_windows_gets_a_hint_instead_of_a_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm.platform, "system", lambda: "Windows")
    assert lm.install_command() is None
    assert "ollama.com/download" in lm.install_hint()


def test_pull_without_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm, "ollama_binary", lambda: None)
    with pytest.raises(lm.LocalModelError, match="nicht installiert"):
        list(lm.pull_model("qwen2.5:7b"))


def test_pull_streams_progress_and_detects_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        def __init__(self, returncode: int) -> None:
            self.stdout = iter(["pulling manifest\n", "downloading 42%\n", "\n"])
            self._returncode = returncode

        def wait(self) -> int:
            return self._returncode

    monkeypatch.setattr(lm, "ollama_binary", lambda: "/usr/bin/ollama")

    monkeypatch.setattr(lm.subprocess, "Popen", lambda *a, **k: FakeProcess(0))
    assert list(lm.pull_model("qwen2.5:7b")) == ["pulling manifest", "downloading 42%"]

    monkeypatch.setattr(lm.subprocess, "Popen", lambda *a, **k: FakeProcess(1))
    with pytest.raises(lm.LocalModelError, match="fehlgeschlagen"):
        list(lm.pull_model("qwen2.5:7b"))


# ---------------------------------------------------------------------------
# Tool-Calling-Pruefung -- der entscheidende Test
# ---------------------------------------------------------------------------
def _reply(content: str = "", tool_calls: Any = None) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def test_verify_accepts_a_model_that_calls_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    call = SimpleNamespace(function=SimpleNamespace(name="web_search"))
    captured: dict[str, Any] = {}

    def completion(**kwargs: Any):
        captured.update(kwargs)
        return _reply(tool_calls=[call])

    monkeypatch.setattr("litellm.completion", completion)
    ok, detail = lm.verify_tool_calling("ollama_chat/qwen2.5:7b")
    assert ok
    assert "web_search" in detail
    # Die Probe bietet tatsaechlich ein Werkzeug an.
    assert captured["tools"][0]["function"]["name"] == "web_search"
    assert captured["api_base"] == lm.DEFAULT_OLLAMA_URL


def test_verify_rejects_a_model_that_only_talks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Genau der Fall, der scoutr unbrauchbar macht: Antwort ohne Werkzeugaufruf."""
    monkeypatch.setattr(
        "litellm.completion", lambda **kwargs: _reply(content="In Berlin ist es sonnig.")
    )
    ok, detail = lm.verify_tool_calling("ollama_chat/kaputt")
    assert not ok
    assert "kein Werkzeug" in detail
    assert "Berlin" in detail


def test_verify_reports_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(**kwargs: Any):
        raise ConnectionError("Server weg")

    monkeypatch.setattr("litellm.completion", failing)
    ok, detail = lm.verify_tool_calling("ollama_chat/qwen2.5:7b")
    assert not ok
    assert "Server weg" in detail


def test_verify_handles_dict_shaped_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: _reply(tool_calls=[{"function": {"name": "web_search"}}]),
    )
    ok, detail = lm.verify_tool_calling("ollama_chat/qwen2.5:7b")
    assert ok and "web_search" in detail


# ---------------------------------------------------------------------------
# Vision-Modelle und Sehtest
# ---------------------------------------------------------------------------
def test_vision_models_use_the_right_prefix() -> None:
    for model in lm.VISION_MODELS:
        assert model.model_id.startswith("ollama_chat/")


def test_vision_recommendation_fits_the_memory() -> None:
    assert lm.recommend_vision_model(4).needs_gb <= 4
    assert lm.recommend_vision_model(64).needs_gb <= 64
    assert lm.recommend_vision_model(2).name == "moondream"


def test_vision_env_values() -> None:
    assert lm.vision_env_values("llava:7b") == {"SCOUTR_VISION_MODEL": "ollama_chat/llava:7b"}
    assert lm.vision_env_values(lm.VISION_MODELS[0])["SCOUTR_VISION_MODEL"].startswith(
        "ollama_chat/"
    )


def test_probe_image_is_a_valid_png() -> None:
    """Das Testbild muss ein echtes PNG sein -- sonst prueft der Sehtest nichts."""
    import struct

    data = lm.solid_png()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", data[16:24])
    assert width == height == 64
    assert data[24] == 8 and data[25] == 2  # 8 Bit, Farbtyp RGB
    assert data.endswith(b"IEND\xae\x42\x60\x82")


def test_probe_image_has_the_expected_colour() -> None:
    """Die Antwort auf den Sehtest muss vorher feststehen."""
    import zlib

    data = lm.solid_png()
    start = data.index(b"IDAT") + 4
    end = data.index(b"IEND") - 4
    pixels = zlib.decompress(data[start:end])
    # Erste Zeile: Filterbyte, dann RGB-Tripel.
    assert pixels[0] == 0
    assert tuple(pixels[1:4]) == lm.PROBE_COLOR
    assert "rot" in lm.PROBE_COLOR_WORDS


def test_verify_vision_accepts_a_model_that_sees(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def completion(**kwargs: Any):
        captured.update(kwargs)
        return _reply(content="Rot")

    monkeypatch.setattr("litellm.completion", completion)
    ok, detail = lm.verify_vision("ollama_chat/llava:7b")
    assert ok
    assert "erkannt" in detail
    # Es wurde tatsaechlich ein Bild mitgeschickt.
    content = captured["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_verify_vision_rejects_a_text_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Textmodell raet oder redet drumherum -- das faellt auf."""
    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: _reply(content="Ich kann keine Bilder sehen."),
    )
    ok, detail = lm.verify_vision("ollama_chat/qwen2.5:7b")
    assert not ok
    assert "nicht erkannt" in detail


def test_verify_vision_rejects_an_empty_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content=""))
    ok, detail = lm.verify_vision("ollama_chat/stumm")
    assert not ok
    assert "nichts geantwortet" in detail


def test_verify_vision_reports_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(**kwargs: Any):
        raise ConnectionError("Server weg")

    monkeypatch.setattr("litellm.completion", failing)
    ok, detail = lm.verify_vision("ollama_chat/llava:7b")
    assert not ok and "Server weg" in detail


@pytest.mark.parametrize("answer", ["rot", "Rot.", "ROT", "red", "Das Bild ist rot."])
def test_colour_words_are_matched_loosely(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    monkeypatch.setattr("litellm.completion", lambda **kwargs: _reply(content=answer))
    ok, _ = lm.verify_vision("ollama_chat/llava:7b")
    assert ok, answer


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
def test_windows_uses_winget_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm.platform, "system", lambda: "Windows")
    monkeypatch.setattr(lm.shutil, "which", lambda name: "C:\\\\winget.exe")
    command = lm.install_command()
    assert command is not None
    assert "Ollama.Ollama" in command


def test_windows_without_winget_gets_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm.platform, "system", lambda: "Windows")
    monkeypatch.setattr(lm.shutil, "which", lambda name: None)
    assert lm.install_command() is None
    hint = lm.install_hint()
    assert "winget" in hint and "ollama.com/download" in hint


def test_windows_server_start_uses_creationflags(monkeypatch: pytest.MonkeyPatch) -> None:
    """`start_new_session` gibt es unter Windows nicht -- dort brauchen wir Flags."""
    captured: dict[str, Any] = {}

    monkeypatch.setattr(lm.platform, "system", lambda: "Windows")
    monkeypatch.setattr(lm, "ollama_binary", lambda: "C:\\\\ollama.exe")
    monkeypatch.setattr(lm, "server_running", lambda *a, **k: True)
    monkeypatch.setattr(lm.subprocess, "DETACHED_PROCESS", 8, raising=False)
    monkeypatch.setattr(lm.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    calls = {"n": 0}

    def fake_running(*args: Any, **kwargs: Any) -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # erst nach dem Start erreichbar

    monkeypatch.setattr(lm, "server_running", fake_running)
    monkeypatch.setattr(
        lm.subprocess, "Popen", lambda *a, **k: captured.update(k) or SimpleNamespaceStub()
    )
    assert lm.start_server(wait_seconds=2) is True
    assert "creationflags" in captured
    assert "start_new_session" not in captured


class SimpleNamespaceStub:
    """Popen-Ersatz, der nichts tut."""


def test_posix_server_start_uses_new_session(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm, "ollama_binary", lambda: "/usr/bin/ollama")
    calls = {"n": 0}

    def fake_running(*args: Any, **kwargs: Any) -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(lm, "server_running", fake_running)
    monkeypatch.setattr(
        lm.subprocess, "Popen", lambda *a, **k: captured.update(k) or SimpleNamespaceStub()
    )
    assert lm.start_server(wait_seconds=2) is True
    assert captured["start_new_session"] is True
    assert "creationflags" not in captured


def test_windows_memory_is_read_via_ctypes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne die Abfrage bekaeme jeder Windows-Rechner denselben Vorschlag."""
    monkeypatch.setattr(lm.platform, "system", lambda: "Windows")
    monkeypatch.setattr(lm, "_windows_memory_gb", lambda: 31.9)
    monkeypatch.setattr(lm.Path, "is_file", lambda self: False)
    assert lm.total_memory_gb() == 31.9
    assert lm.recommend_model(31.9).needs_gb <= 31.9


def test_windows_memory_failure_is_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lm.platform, "system", lambda: "Windows")
    monkeypatch.setattr(lm, "_windows_memory_gb", lambda: None)
    monkeypatch.setattr(lm.Path, "is_file", lambda self: False)
    assert lm.total_memory_gb() is None
    assert lm.recommend_model(None) in lm.LOCAL_MODELS
