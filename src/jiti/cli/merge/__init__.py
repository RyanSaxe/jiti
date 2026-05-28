"""The `jiti merge` command — fold generated code from `.jiti/` back into source."""

from jiti.cli.merge.orchestrator import run_merge, source_files

__all__ = ["run_merge", "source_files"]
