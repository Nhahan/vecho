from __future__ import annotations

from pathlib import Path

import pytest

from vecho import systemaudio
from vecho.config import Config
from vecho.errors import AudioError


@pytest.fixture(autouse=True)
def no_real_system_audio(monkeypatch):
    """Never build or run the real capture helper in tests; individual tests opt in with fakes."""

    def unavailable(bin_dir):
        raise AudioError("system audio capture is disabled in tests")

    monkeypatch.setattr(systemaudio, "prepare", unavailable)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(home=tmp_path / "home")
