from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from contextbridge.core.app import App, open_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated data dir; also exported so subprocesses use it, never ~/.contextbridge."""
    h = tmp_path / "cbhome"
    monkeypatch.setenv("CONTEXTBRIDGE_HOME", str(h))
    return h


@pytest.fixture
def app(home: Path) -> Generator[App]:
    a = open_app(home, log_to_stderr=False)
    yield a
    a.close()
