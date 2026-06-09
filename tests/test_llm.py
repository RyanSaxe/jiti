"""LiteLLM message/tool translation for jiti's agent loop."""

from types import SimpleNamespace
from typing import Any

from fakes import ScriptedClient, submit

from jiti import jiti
from jiti.agent.engine import Engine
from jiti.agent.llm import LiteLLMClient, TextBlock, ToolCall, ToolResult
from jiti.core.store import JitiStore


class CapturingCompletion:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def __call__(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        message = SimpleNamespace(
            content="",
            tool_calls=[
                SimpleNamespace(
                    id="call-1",
                    function=SimpleNamespace(name="submit", arguments='{"body": "return x"}'),
                )
            ],
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def test_litellm_client_translates_tools_messages_and_tool_calls():
    completion = CapturingCompletion()
    client = LiteLLMClient(completion=completion)

    response = client.complete(
        model="claude-sonnet-4-6",
        max_tokens=123,
        system=[{"type": "text", "text": "system rules", "cache_control": {"type": "ephemeral"}}],
        tools=[
            {
                "name": "submit",
                "description": "Submit code.",
                "input_schema": {
                    "type": "object",
                    "properties": {"body": {"type": "string"}},
                    "required": ["body"],
                },
            }
        ],
        messages=[
            {"role": "user", "content": "Implement f."},
            {
                "role": "assistant",
                "content": [TextBlock("thinking"), ToolCall("call-0", "inspect", {})],
            },
            {"role": "user", "content": [ToolResult("call-0", "int: 1")]},
        ],
    )

    assert completion.kwargs is not None
    assert completion.kwargs["model"] == "claude-sonnet-4-6"
    assert completion.kwargs["max_tokens"] == 123
    assert completion.kwargs["tools"][0]["function"]["parameters"]["required"] == ["body"]
    assert completion.kwargs["messages"] == [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "system rules",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {"role": "user", "content": "Implement f."},
        {
            "role": "assistant",
            "content": "thinking",
            "tool_calls": [
                {
                    "id": "call-0",
                    "type": "function",
                    "function": {"name": "inspect", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-0", "content": "int: 1"},
    ]
    assert response.content == [ToolCall("call-1", "submit", {"body": "return x"})]


def test_tool_results_with_injected_text_become_tool_then_user_messages():
    """Engine guidance (the refactor nudge) rides along with tool results — the wire format
    must emit the tool replies first, then the guidance as a plain user message."""
    completion = CapturingCompletion()
    client = LiteLLMClient(completion=completion)

    client.complete(
        model="claude-sonnet-4-6",
        max_tokens=123,
        system=[],
        tools=[],
        messages=[
            {
                "role": "user",
                "content": [ToolResult("call-0", "PASSED"), TextBlock("Refactor and resubmit.")],
            },
        ],
    )

    assert completion.kwargs is not None
    assert completion.kwargs["messages"] == [
        {"role": "tool", "tool_call_id": "call-0", "content": "PASSED"},
        {"role": "user", "content": "Refactor and resubmit."},
    ]


def test_litellm_client_flattens_system_text_for_other_providers():
    completion = CapturingCompletion()
    client = LiteLLMClient(completion=completion)

    client.complete(
        model="openai/gpt-4o",
        max_tokens=123,
        system=[{"type": "text", "text": "system rules", "cache_control": {"type": "ephemeral"}}],
        tools=[],
        messages=[],
    )

    assert completion.kwargs is not None
    assert completion.kwargs["messages"][0] == {"role": "system", "content": "system rules"}


def test_litellm_client_uses_litellm_cost_registry():
    message = SimpleNamespace(content="", tool_calls=[])
    response = SimpleNamespace(
        model="claude-sonnet-4-6",
        choices=[SimpleNamespace(message=message)],
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
    )

    client = LiteLLMClient(completion=lambda **_: response)
    result = client.complete(
        model="claude-sonnet-4-6", max_tokens=123, system=[], tools=[], messages=[]
    )

    assert result.cost is not None
    assert round(result.cost, 4) == 0.0105


def provider_slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...


def test_engine_can_generate_through_injected_litellm_completion(tmp_path):
    completion = ScriptedClient(
        [
            submit(
                "provider_slugify",
                "return text.lower()",
                "def test_p():\n    assert provider_slugify('A') == 'a'",
            )
        ]
    )
    engine = Engine(completion=completion, store=JitiStore(tmp_path / ".jiti"))

    assert jiti(engine=engine)(provider_slugify)("HELLO") == "hello"
    assert completion.calls == 1
