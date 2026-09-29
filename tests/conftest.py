from __future__ import annotations

from pathlib import Path

import pytest

from vecho.config import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(home=tmp_path / "home")
