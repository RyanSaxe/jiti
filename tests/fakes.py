"""Shared fake LiteLLM completion for engine tests — canned tool-use responses, no network."""

from dataclasses import dataclass, field
from types import SimpleNamespace
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
    """Returns canned LiteLLM-shaped responses in order."""

    def __init__(self, script: list[Response]) -> None:
        self.script = script
        self.calls = 0

    def __call__(self, **kwargs: Any) -> Any:
        response = self.script[self.calls]
        self.calls += 1
        return _litellm_response(response)


def submit(
    name: str,
    body: str,
    tests: str,
    helpers: str = "",
    quality: int | None = None,
) -> Response:
    """Build a scripted `submit` tool-call: body-only contract; jiti splices the def line."""
    payload: dict[str, Any] = {"body": body, "helpers": helpers, "tests": tests}
    if quality is not None:
        payload["quality"] = quality
    use = Block(type="tool_use", id=f"t-{name}", name="submit", input=payload)
    return Response(content=[use])


def submit_test(name: str, impl: str) -> Response:
    use = Block(type="tool_use", id=f"t-{name}", name="submit_test", input={"impl": impl})
    return Response(content=[use])


def _litellm_response(response: Response) -> Any:
    text = "\n".join(block.text for block in response.content if block.type == "text").strip()
    tool_calls = [
        SimpleNamespace(
            id=block.id,
            function=SimpleNamespace(name=block.name, arguments=block.input),
        )
        for block in response.content
        if block.type == "tool_use"
    ]
    message = SimpleNamespace(content=text or None, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
