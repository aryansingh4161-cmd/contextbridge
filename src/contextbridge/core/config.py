"""Configuration: packaged defaults deep-merged with ~/.contextbridge/config.toml.

Stdlib only (tomllib). Unknown keys are kept, so newer configs don't break older code.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources
from typing import Any

from .errors import CBError
from .paths import DataPaths, ensure_private_file

DEFAULT_CONFIG_NAME = "default_config.toml"


def default_config_text() -> str:
    return resources.files("contextbridge").joinpath(DEFAULT_CONFIG_NAME).read_text("utf-8")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass(frozen=True)
class Config:
    data: dict[str, Any]

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def busy_timeout_ms(self) -> int:
        return int(self.get("database.busy_timeout_ms", 5000))

    @property
    def backups_keep(self) -> int:
        return int(self.get("database.backups_keep", 10))

    @property
    def log_level(self) -> str:
        return str(self.get("logging.level", "INFO")).upper()


def load_config(paths: DataPaths) -> Config:
    defaults = tomllib.loads(default_config_text())
    if not paths.config.exists():
        return Config(defaults)
    try:
        user = tomllib.loads(paths.config.read_text("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CBError(
            "invalid_input",
            f"config file {paths.config} is not valid TOML: {exc}",
            "fix the file or delete it to fall back to defaults",
        ) from exc
    return Config(_deep_merge(defaults, user))


def write_default_config(paths: DataPaths) -> bool:
    """Write the default config if none exists. Returns True if written."""
    if paths.config.exists():
        return False
    paths.config.write_text(default_config_text(), "utf-8")
    ensure_private_file(paths.config)
    return True
