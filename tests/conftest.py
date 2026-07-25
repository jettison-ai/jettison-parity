from __future__ import annotations

from pathlib import Path

import pytest

from parity.fixtures import FixtureConfig, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = REPO_ROOT / "configs"
TASKS = REPO_ROOT / "tasks"


@pytest.fixture(scope="session")
def mcp_heavy() -> FixtureConfig:
    return load_config(CONFIGS / "mcp-heavy")


@pytest.fixture(scope="session")
def openclaw_like() -> FixtureConfig:
    return load_config(CONFIGS / "openclaw-like")
