"""Opt-in logging for jiti — silent unless `JITI_LOG` is set.

Set `JITI_LOG=info` (or `debug`, or `1`) to see, per generation, each LLM call: which
function is being generated, the cascade depth, the turn, how long it took, token usage, and
an approximate cost. Downstream users get nothing by default.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, NamedTuple

logger = logging.getLogger("jiti")
logger.addHandler(logging.NullHandler())


class Price(NamedTuple):
    """Approximate USD per million tokens."""

    input: float
    output: float
    cache_write: float
    cache_read: float


# Approximate pricing (USD per million tokens) — a labeled estimate, NOT billing-accurate.
# Update these as pricing changes; unknown models simply log token counts without a cost.
_PRICES: dict[str, Price] = {
    "claude-opus-4-7": Price(input=15.0, output=75.0, cache_write=18.75, cache_read=1.5),
    "claude-sonnet-4-6": Price(input=3.0, output=15.0, cache_write=3.75, cache_read=0.3),
    "claude-haiku-4-5": Price(input=1.0, output=5.0, cache_write=1.25, cache_read=0.1),
}


def configure() -> None:
    """Enable logging to stderr when `JITI_LOG` is set; stay silent otherwise. Idempotent."""
    setting = os.environ.get("JITI_LOG", "").strip()
    if not setting:
        return
    level = (
        logging.INFO
        if setting in {"1", "true", "yes"}
        else getattr(logging, setting.upper(), logging.INFO)
    )
    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("jiti %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)


def cost(model: str, usage: Any) -> float | None:
    """Estimated USD for one call from its token usage; None if the model is unknown."""
    price = _PRICES.get(model)
    if price is None or usage is None:
        return None
    return (
        _tokens(usage, "input_tokens") * price.input
        + _tokens(usage, "output_tokens") * price.output
        + _tokens(usage, "cache_creation_input_tokens") * price.cache_write
        + _tokens(usage, "cache_read_input_tokens") * price.cache_read
    ) / 1_000_000


def log_start(key: str, depth: int) -> None:
    logger.info("%sgenerating %s", _indent(depth), key)


def log_llm_call(key: str, turn: int, depth: int, seconds: float, usage: Any, model: str) -> None:
    logger.info("%s%s turn %d — %.1fs%s", _indent(depth), key, turn, seconds, _usage(usage, model))


def log_done(key: str, depth: int, seconds: float, total_cost: float | None) -> None:
    suffix = f" ~${total_cost:.4f}" if total_cost else ""
    logger.info("%scommitted %s — %.1fs%s", _indent(depth), key, seconds, suffix)


def _usage(usage: Any, model: str) -> str:
    if usage is None:
        return ""
    parts = [
        f"in={_k(_tokens(usage, 'input_tokens'))}",
        f"out={_k(_tokens(usage, 'output_tokens'))}",
    ]
    cache_read = _tokens(usage, "cache_read_input_tokens")
    if cache_read:
        parts.append(f"cache_read={_k(cache_read)}")
    estimated = cost(model, usage)
    if estimated is not None:
        parts.append(f"~${estimated:.4f}")
    return " " + " ".join(parts)


def _tokens(usage: Any, name: str) -> int:
    return getattr(usage, name, 0) or 0


def _k(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def _indent(depth: int) -> str:
    return "  " * max(depth - 1, 0)
