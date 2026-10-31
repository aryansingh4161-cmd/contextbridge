"""Runtime data directory layout (spec §13) with enforced permissions (spec §9).

Stdlib only: this module is imported on the fast CLI path (status line, hooks).
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600
ENV_HOME = "CONTEXTBRIDGE_HOME"


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "contextbridge.db"

    @property
    def config(self) -> Path:
        return self.root / "config.toml"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def telemetry(self) -> Path:
        return self.root / "telemetry"

    def subdirs(self) -> list[Path]:
        return [self.backups, self.exports, self.logs, self.telemetry, self.telemetry / "state"]


def resolve_paths(root: str | os.PathLike[str] | None = None) -> DataPaths:
    """Pick the data dir: explicit arg > $CONTEXTBRIDGE_HOME > ~/.contextbridge."""
    if root is None:
        root = os.environ.get(ENV_HOME) or (Path.home() / ".contextbridge")
    return DataPaths(Path(root).expanduser().resolve())


def ensure_private_dir(path: Path) -> None:
    """Create `path` (and parents) and force mode 700 on it."""
    path.mkdir(parents=True, exist_ok=True)
    if stat.S_IMODE(path.stat().st_mode) != DIR_MODE:
        path.chmod(DIR_MODE)


def ensure_private_file(path: Path) -> None:
    """Force mode 600 on an existing file (no-op if missing)."""
    if path.exists() and stat.S_IMODE(path.stat().st_mode) != FILE_MODE:
        path.chmod(FILE_MODE)


def ensure_layout(paths: DataPaths) -> None:
    ensure_private_dir(paths.root)
    for d in paths.subdirs():
        ensure_private_dir(d)
