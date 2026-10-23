"""Фикстуры pytest."""

from __future__ import annotations

from pathlib import Path

import pytest

from medintake.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "var"
    return Settings(data_dir=data, inbox_root=data / "inbox")
