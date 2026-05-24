"""The provider seam: anything that turns a prompt into text can drive jiti.

The `Model` protocol is one method, so swapping providers (or stubbing one in tests) is
trivial. `AnthropicModel` is the default, backed by the Anthropic SDK.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from anthropic import Anthropic

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 8192


@runtime_checkable
class Model(Protocol):
    """Turn a prompt into completion text."""

    def complete(self, prompt: str) -> str: ...


class AnthropicModel:
    """Default `Model`, backed by the Anthropic SDK (Claude)."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        api_key: str | None = None,
    ) -> None:
        from anthropic import Anthropic

        self._client: Anthropic = Anthropic(api_key=api_key) if api_key else Anthropic()
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, prompt: str) -> str:
        from anthropic.types import TextBlock

        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in message.content if isinstance(block, TextBlock))
