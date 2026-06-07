"""Per-generation transcript recorder. Streams JSONL events for every turn and tool call.

A transcript captures one generation's full agent loop: each LLM turn (text content, usage,
cost, latency) and each tool call with its input and result. Written to
`.jiti/transcripts/<module-path>/<fn>.jsonl` once the engine finishes (even on failure), so
"why did this generation cost so much / produce that code / fail" becomes answerable after
the fact without re-running.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


@dataclass
class Recorder:
    """Collects ordered events during one generation; writes them as JSONL on demand."""

    events: list[dict[str, Any]] = field(default_factory=list)

    def turn(self, n: int, seconds: float, usage: Any, spent: float, content: list[Any]) -> None:
        self.events.append(
            {
                "type": "turn",
                "n": n,
                "seconds": round(seconds, 3),
                "usage": _usage_dict(usage),
                "cost": round(spent, 6),
                "text": _extract_text(content),
            }
        )

    def tool_call(self, name: str, tool_input: dict[str, Any]) -> None:
        self.events.append({"type": "tool_call", "name": name, "input": tool_input})

    def tool_result(self, name: str, output: str) -> None:
        self.events.append({"type": "tool_result", "name": name, "output": output})

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(event) + "\n" for event in self.events))


def transcript_path(mirror_root: Path, module: str, name: str) -> Path:
    """`.jiti/transcripts/<module-as-dirs>/<name>.jsonl` — parallel to the impl mirror."""
    relpath = Path(*module.split("."))
    return mirror_root / "transcripts" / relpath / f"{name}.jsonl"


def _usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    return {name: _usage_value(usage, name) for name in _USAGE_FIELDS}


def _usage_value(usage: Any, name: str) -> int:
    for candidate in _USAGE_ALIASES.get(name, (name,)):
        value = usage.get(candidate, 0) if isinstance(usage, dict) else getattr(usage, candidate, 0)
        if value:
            return int(value)
    return 0


_USAGE_ALIASES = {
    "input_tokens": ("input_tokens", "prompt_tokens"),
    "output_tokens": ("output_tokens", "completion_tokens"),
    "cache_creation_input_tokens": ("cache_creation_input_tokens",),
    "cache_read_input_tokens": ("cache_read_input_tokens",),
}


def _extract_text(content: list[Any]) -> str:
    parts = (
        getattr(block, "text", "") for block in content if getattr(block, "type", None) == "text"
    )
    return "\n".join(part for part in parts if part)
