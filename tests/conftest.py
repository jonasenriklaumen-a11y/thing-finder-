"""Gemeinsame Test-Fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_html() -> callable:
    """Laedt eine HTML-Fixture nach Namen."""

    def _load(name: str) -> str:
        return (FIXTURE_DIR / name).read_text(encoding="utf-8")

    return _load


@pytest.fixture
def settings(tmp_path: Path):
    """Settings, die nichts ausserhalb von tmp_path anfassen."""
    from scoutr.config import Settings

    return Settings(
        model="openai/gpt-4o",
        data_dir=tmp_path / "data",
        request_delay_seconds=0.0,
        fetch_timeout=5.0,
        enable_playwright=False,
        # Die automatische Vorrecherche wird gezielt in eigenen Tests
        # geprueft -- sonst verbraucht sie hier ueberall einen LLM-Aufruf.
        subagents_auto=False,
    )
