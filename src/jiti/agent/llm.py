"""LiteLLM adapter for jiti's tool-use loop."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from litellm import completion as litellm_completion
from litellm import completion_cost, get_llm_provider

from jiti.core.errors import JitiError


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


class LiteLLMClient:
    def __init__(self, completion: Completion = litellm_completion) -> None:
        self.completion = completion

    def complete(
        self,
        *,
        model: str,
        max_tokens: int,
        system: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        messages: Sequence[dict[str, Any]],
    ) -> LLMResponse:
        response = self.completion(
            model=model,
            max_tokens=max_tokens,
            messages=_messages(model, system, messages),
            tools=_tools(tools),
        )
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
        if _is_tool_result_list(content):
            out.extend(
                {"role": "tool", "tool_call_id": result.tool_use_id, "content": result.content}
                for result in content
            )
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


def _is_tool_result_list(content: Any) -> bool:
    return isinstance(content, list) and all(isinstance(item, ToolResult) for item in content)


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
        content.append(
            ToolCall(
                id=str(_field(call, "id")),
                name=str(_field(function, "name")),
                input=_json_object(raw_args),
            )
        )
    return content


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    loaded = json.loads(str(raw))
    if not isinstance(loaded, dict):
        raise JitiError("tool call arguments must decode to a JSON object.")
    return loaded


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
