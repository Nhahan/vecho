from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeMulti, FakeSwitcher
from vecho import routing
from vecho.config import Config


@pytest.fixture(autouse=True)
def switcher(monkeypatch) -> FakeSwitcher:
    fake = FakeSwitcher()
    monkeypatch.setattr(routing, "OutputSwitcher", lambda *args, **kwargs: fake)
    return fake


@pytest.fixture(autouse=True)
def multi(monkeypatch, switcher) -> FakeMulti:
    fake = FakeMulti(switcher)
    monkeypatch.setattr(routing, "MultiOutput", lambda *args, **kwargs: fake)
    return fake


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(home=tmp_path / "home")
