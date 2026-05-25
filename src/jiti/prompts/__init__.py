"""jiti's prompt text, exposed as constants.

`SYSTEM_PROMPT` is jiti's fixed operating contract for the agent. `STYLE_GUIDE` and
`TEST_GUIDE` steer the shape of generated code and tests; both are user-overridable — an
explicit `Engine` argument wins, else the matching env var (a path), else a project file,
else the bundled default beside this module. The guides resolve once at import (like the
`JITI_LOG` toggle); pass `Engine(style=...)`/`Engine(test_guide=...)` for late binding.
"""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

STYLE_ENV = "JITI_STYLE"
STYLE_FILE = "jiti.style.md"
TESTS_ENV = "JITI_TESTS"
TESTS_FILE = "jiti.tests.md"


def _bundled(name: str) -> str:
    return files(__name__).joinpath(name).read_text()


def _resolve(bundled_name: str, env: str, project_file: str) -> str:
    override = os.environ.get(env, "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"{env} points to a missing file: {path}")
        return path.read_text()
    project = Path.cwd() / project_file
    if project.is_file():
        return project.read_text()
    return _bundled(bundled_name)


SYSTEM_PROMPT = _bundled("system.md")
TEST_MODE_PROMPT = _bundled("test_mode.md")
STYLE_GUIDE = _resolve("style.md", STYLE_ENV, STYLE_FILE)
TEST_GUIDE = _resolve("tests.md", TESTS_ENV, TESTS_FILE)
