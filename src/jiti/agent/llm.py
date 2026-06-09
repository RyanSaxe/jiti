"""LiteLLM adapter for jiti's tool-use loop."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from litellm import completion as litellm_completion
from litellm import completion_cost, get_llm_provider

from jiti.core import heartbeat

@dataclass(frozen=True)
class TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"
    parse_error: str | None = None
    """Set when the model's tool-call JSON was malformed. The dispatcher surfaces this as
    a tool error the agent can read and fix on its next turn, instead of letting it raise
    out of the user's `@jiti` call."""


@dataclass(frozen=True)
class ToolResult:
    tool_use_id: str
    content: str
    type: str = "tool_result"


@dataclass(frozen=True)
class LLMResponse:
    content: list[TextBlock | ToolCall]
    usage: Any = None
    cost: float | None = None


Completion = Callable[..., Any]

DEFAULT_NUM_RETRIES = 3
"""How many times litellm should retry a transient provider error (429, 5xx, timeout).
A small positive default means a single blip doesn't bubble out of the user's @jiti call."""


class LiteLLMClient:
    def __init__(
        self,
        completion: Completion = litellm_completion,
        num_retries: int = DEFAULT_NUM_RETRIES,
    ) -> None:
        self.completion = completion
        self.num_retries = num_retries

    def complete(
        self,
        *,
        model: str,
        max_tokens: int,
        system: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        messages: Sequence[dict[str, Any]],
    ) -> LLMResponse:
        # The heartbeat keeps the execution timeout from tripping on legitimate work:
        # while this call is in flight, idle time is zero (see `core.heartbeat`).
        heartbeat.llm_call_started()
        try:
            response = self.completion(
                model=model,
                max_tokens=max_tokens,
                messages=_messages(model, system, messages),
                tools=_tools(tools),
                num_retries=self.num_retries,
            )
        finally:
            heartbeat.llm_call_finished()
        message = response.choices[0].message
        return LLMResponse(
            content=_content(message),
            usage=getattr(response, "usage", None),
            cost=_cost(response, model),
        )


def tool_result(call: ToolCall, content: str) -> ToolResult:
    return ToolResult(tool_use_id=call.id, content=content)


def _messages(
    model: str, system: Sequence[dict[str, Any]], messages: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system_content := _system_content(system, cacheable=_uses_anthropic_cache_control(model)):
        out.append({"role": "system", "content": system_content})
    for message in messages:
        role = str(message["role"])
        content = message["content"]
        if _is_tool_turn(content):
            out.extend(
                {"role": "tool", "tool_call_id": item.tool_use_id, "content": item.content}
                for item in content
                if isinstance(item, ToolResult)
            )
            # Engine-injected guidance (e.g. the refactor nudge) rides along with tool results;
            # OpenAI-format providers need it as a separate user message after the tool replies.
            if text := "\n\n".join(item.text for item in content if isinstance(item, TextBlock)):
                out.append({"role": "user", "content": text})
        elif role == "assistant" and isinstance(content, list):
            out.append(_assistant_message(content))
        else:
            out.append({"role": role, "content": content})
    return out


def _system_content(
    system: Sequence[dict[str, Any]], *, cacheable: bool
) -> str | list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    plain: list[str] = []
    for block in system:
        raw_text = block.get("text")
        if not raw_text:
            continue
        text = str(raw_text)
        if not cacheable:
            plain.append(text)
            continue
        item = {"type": "text", "text": text}
        if cache_control := block.get("cache_control"):
            item["cache_control"] = cache_control
        blocks.append(item)
    return blocks if cacheable else "\n\n".join(plain)


def _uses_anthropic_cache_control(model: str) -> bool:
    try:
        _, provider, _, _ = get_llm_provider(model)
    except Exception:
        return False
    return provider == "anthropic"


def _is_tool_turn(content: Any) -> bool:
    """A tool-results turn: ToolResults plus optional engine-injected TextBlocks."""
    return (
        isinstance(content, list)
        and any(isinstance(item, ToolResult) for item in content)
        and all(isinstance(item, ToolResult | TextBlock) for item in content)
    )


def _assistant_message(content: Sequence[TextBlock | ToolCall]) -> dict[str, Any]:
    text = "\n".join(block.text for block in content if isinstance(block, TextBlock)).strip()
    calls = [block for block in content if isinstance(block, ToolCall)]
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.input)},
            }
            for call in calls
        ]
    return message


def _tools(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": str(tool["name"]),
                "description": str(tool.get("description", "")),
                "parameters": tool["input_schema"],
            },
        }
        for tool in tools
    ]


def _content(message: Any) -> list[TextBlock | ToolCall]:
    content: list[TextBlock | ToolCall] = []
    text = _field(message, "content")
    if text:
        content.append(TextBlock(text=str(text)))
    for call in _field(message, "tool_calls") or ():
        function = _field(call, "function")
        raw_args = _field(function, "arguments") if function is not None else "{}"
        parsed, parse_error = _json_object(raw_args)
        content.append(
            ToolCall(
                id=str(_field(call, "id")),
                name=str(_field(function, "name")),
                input=parsed,
                parse_error=parse_error,
            )
        )
    return content


def _json_object(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Parse a tool-call arguments payload. Returns `(input, parse_error)` — never raises.

    A malformed payload commonly comes from a truncated `max_tokens` response or a model
    bug; the agent needs to see the error as a tool failure and retry, not have it bubble
    out of the user's call.
    """
    if isinstance(raw, dict):
        return raw, None
    if raw in (None, ""):
        return {}, None
    try:
        loaded = json.loads(str(raw))
    except json.JSONDecodeError as error:
        return {}, f"tool arguments were not valid JSON: {error}"
    if not isinstance(loaded, dict):
        return {}, "tool arguments must decode to a JSON object."
    return loaded, None


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _cost(response: Any, model: str) -> float | None:
    if priced := _completion_cost(response, model):
        return priced
    normalized = _cost_response(response, model)
    if normalized is None:
        return None
    return _completion_cost(normalized, model)


def _completion_cost(response: Any, model: str) -> float | None:
    try:
        value = completion_cost(completion_response=response, model=model)
    except Exception:
        return None
    return float(value) if value else None


def _cost_response(response: Any, model: str) -> dict[str, Any] | None:
    usage = _usage_for_cost(_field(response, "usage"))
    if usage is None:
        return None
    return {"model": str(_field(response, "model") or model), "usage": usage}


def _usage_for_cost(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    prompt_tokens = _token_count(usage, "prompt_tokens", "input_tokens")
    completion_tokens = _token_count(usage, "completion_tokens", "output_tokens")
    total_tokens = _token_count(usage, "total_tokens") or prompt_tokens + completion_tokens
    if not (prompt_tokens or completion_tokens or total_tokens):
        return None
    out = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    for name in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        if count := _token_count(usage, name):
            out[name] = count
    return out


def _token_count(usage: Any, *names: str) -> int:
    for name in names:
        if value := _field(usage, name):
            return int(value)
    return 0
