"""Shared fake Anthropic client for engine tests — canned tool-use responses, no network."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Block:
    type: str
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    text: str = ""


@dataclass
class Response:
    content: list[Block]


class ScriptedClient:
    """Returns canned responses in order; only `messages.create(...)` is used."""

    def __init__(self, script: list[Response]) -> None:
        self.script = script
        self.calls = 0

    @property
    def messages(self) -> "ScriptedClient":
        return self

    def create(self, **kwargs: Any) -> Response:
        response = self.script[self.calls]
        self.calls += 1
        return response


def submit(name: str, impl: str, tests: str, quality: int | None = None) -> Response:
    payload: dict[str, Any] = {"impl": impl, "tests": tests}
    if quality is not None:
        payload["quality"] = quality
    use = Block(type="tool_use", id=f"t-{name}", name="submit", input=payload)
    return Response(content=[use])


def submit_test(name: str, impl: str) -> Response:
    use = Block(type="tool_use", id=f"t-{name}", name="submit_test", input={"impl": impl})
    return Response(content=[use])
