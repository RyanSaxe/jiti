"""jiti — Just In Time Implementation.

Declare a function by its typed interface; on the first call, an in-process agent inspects
the real values, explores your codebase, writes and tests a real implementation, and caches
it under `.jiti/`. Every call after that runs that committed code.
"""

from pathlib import Path

from jiti._log import configure
from jiti.decorator import jiti
from jiti.engine import Engine
from jiti.errors import (
    ConflictError,
    GenerationCycleError,
    GenerationError,
    JitiError,
    RealBodyError,
)
from jiti.store import JitiStore

configure()

__all__ = [
    "ConflictError",
    "Engine",
    "GenerationCycleError",
    "GenerationError",
    "JitiError",
    "JitiStore",
    "RealBodyError",
    "clear",
    "jiti",
]


def clear() -> None:
    """Delete the generated mirror at ./.jiti."""
    JitiStore(Path.cwd() / ".jiti").clear()
