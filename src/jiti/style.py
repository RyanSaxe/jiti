"""The style guide fed to the generation agent, resolved from the user's project or jiti's own.

Generated code should match the conventions of the project it lands in, so jiti injects a
style guide into the prompt. It's layered: an explicit `Engine(style=...)` wins, then the
`JITI_STYLE` env var (a path), then a `jiti.style.md` in the project root, then the concise
default that ships with jiti.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

STYLE_ENV = "JITI_STYLE"
STYLE_FILENAME = "jiti.style.md"


def default_style() -> str:
    """jiti's bundled default style guide."""
    return files("jiti").joinpath("style.md").read_text()


def resolve_style() -> str:
    """The style guide for the default engine: env var, else project file, else the default."""
    override = _env_style_path()
    if override is not None:
        return override.read_text()
    project = Path.cwd() / STYLE_FILENAME
    if project.is_file():
        return project.read_text()
    return default_style()


def _env_style_path() -> Path | None:
    raw = os.environ.get(STYLE_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{STYLE_ENV} points to a missing file: {path}")
    return path
