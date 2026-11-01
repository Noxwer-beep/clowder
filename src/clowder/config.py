"""Where everything lives.

The console owns no data. It knows how to find the four tools and where each of them
keeps its state, and that is the entire contract — if a path moves, this file changes
and nothing else does.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

APP = "clowder"
VERSION = "1.0.0"

HOME = Path.home()
PROJECTS = Path(os.environ.get("CLOWDER_PROJECTS", HOME / "projects"))

ACCENT = "#c9f24d"
SEVERITY_STYLE = {
    "critical": "bold #ff5c5c",
    "high": "bold #ff9474",
    "medium": "#f0c45a",
    "low": "#92acbe",
    "info": "dim",
}
SEVERITY_ORDER = {name: i for i, name in enumerate(
    ("critical", "high", "medium", "low", "info"))}

#: One scale for two vocabularies. A confirmed critical finding outranks a service
#: that is merely down; a dead service outranks a warning or a medium finding.
ATTENTION_RANK = {
    "critical": 0, "fail": 1, "high": 2, "warn": 2, "medium": 3,
    "low": 4, "unknown": 4, "info": 5,
}


@dataclass(frozen=True)
class Tool:
    key: str          # short name used on the command line
    title: str        # what it does, in four words
    project: str      # directory name under ~/projects
    launcher: str     # file to execute inside that directory
    env: str          # environment variable that moves its data dir
    data: str         # default data directory, relative to ~

    def directory(self) -> Path:
        return PROJECTS / self.project

    def command(self) -> Path | None:
        """The tool's entry point: on PATH, or the launcher in its checkout."""
        found = shutil.which(self.key)
        if found:
            return Path(found)
        candidate = self.directory() / self.launcher
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate
        return None

    @property
    def installed(self) -> bool:
        return self.command() is not None

    def data_dir(self) -> Path:
        """Environment first, then ~/.config/clowder/config.toml, then the default."""
        env = os.environ.get(self.env)
        if env:
            return Path(env).expanduser()
        configured = configured_dirs().get(self.key)
        if configured:
            return Path(configured).expanduser()
        return HOME / self.data


TOOLS: tuple[Tool, ...] = (
    Tool("vantage", "scan · fingers the surfaces", "vantage", "vantage",
         "VANTAGE_DATA_DIR", ".local/share/vantage"),
    Tool("warden", "watch · is it still up", "warden", "warden",
         "WARDEN_DATA_DIR", ".local/share/warden"),
    Tool("bastet", "triage · what happened", "bastet", "bastet",
         "BASTET_DATA_DIR", ".local/share/bastet"),
    Tool("whisker", "reads · what is on the wire", "whisker", "whisker",
         "WHISKER_DATA_DIR", ".local/share/whisker"),
)

BY_KEY = {tool.key: tool for tool in TOOLS}


def configured_dirs() -> dict[str, str]:
    """[data] table from ~/.config/clowder/config.toml — for stores kept elsewhere."""
    path = Path(os.environ.get("CLOWDER_CONFIG", HOME / ".config" / APP / "config.toml"))
    if not path.exists():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - <3.11
        return {}
    try:
        payload = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    data = payload.get("data") or {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def data_root() -> Path:
    env = os.environ.get("CLOWDER_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return HOME / ".local" / "share" / APP
