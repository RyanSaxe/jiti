"""Opt-in logging for jiti — silent unless `JITI_LOG` is set.

Set `JITI_LOG=info` (or `debug`, or `1`) to see, per generation, each LLM call: which
function is being generated, the cascade depth, the turn, how long it took, token usage, and
an approximate cost. Downstream users get nothing by default.
"""

from __future__ import annotations

import atexit
import logging
import os
import sys
from typing import Any, NamedTuple

from jiti.core.models import Model

logger = logging.getLogger("jiti")
logger.addHandler(logging.NullHandler())

_session_generations: int = 0
_session_cost: float = 0.0
_summary_registered: bool = False
_own_handler: logging.StreamHandler | None = None
"""The handler `configure()` attached, if any — the one the atexit summary may repoint."""


class Price(NamedTuple):
    """Approximate USD per million tokens."""

    input: float
    output: float
    cache_write: float
    cache_read: float


# Approximate pricing (USD per million tokens) — a labeled estimate, NOT billing-accurate.
# Update these as pricing changes; unknown models simply log token counts without a cost.
_PRICES: dict[str, Price] = {
    Model.OPUS_4_8: Price(input=5.0, output=25.0, cache_write=6.25, cache_read=0.5),
    Model.SONNET_4_6: Price(input=3.0, output=15.0, cache_write=3.75, cache_read=0.3),
    Model.HAIKU_4_5: Price(input=1.0, output=5.0, cache_write=1.25, cache_read=0.1),
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
    global _own_handler, _summary_registered
    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("jiti %(message)s"))
        logger.addHandler(handler)
        _own_handler = handler
    logger.setLevel(level)
    if not _summary_registered:
        atexit.register(_log_session_summary)
        _summary_registered = True


def record_generation(spent: float) -> None:
    """Accumulate one finished generation's cost into the session total."""
    global _session_generations, _session_cost
    _session_generations += 1
    _session_cost += spent


def _log_session_summary() -> None:
    if _session_generations == 0:
        return
    if sys.stderr is None or getattr(sys.stderr, "closed", False):
        return  # interpreter teardown already closed stderr — nowhere left to write
    if _own_handler is not None:
        # By atexit our handler's stream may be dead in ways `closed` doesn't reveal —
        # pytest's capture file, for example, gets torn down at session end. We own this
        # handler, so swap its stream directly instead of via `setStream`, which flushes
        # the old (closed) stream and raises. User-attached handlers are theirs; we never
        # touch their streams.
        _own_handler.stream = sys.stderr
    suffix = f" ~${_session_cost:.4f}" if _session_cost else ""
    label = "generation" if _session_generations == 1 else "generations"
    logger.info("session: %d %s%s", _session_generations, label, suffix)


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


def log_done(
    key: str,
    depth: int,
    seconds: float,
    total_cost: float | None,
    llm_seconds: float = 0.0,
    llm_calls: int = 0,
) -> None:
    cost_suffix = f" ~${total_cost:.4f}" if total_cost else ""
    llm_suffix = ""
    if llm_calls:
        label = "call" if llm_calls == 1 else "calls"
        llm_suffix = f" (llm {llm_seconds:.1f}s across {llm_calls} {label})"
    logger.info("%scommitted %s — %.1fs%s%s", _indent(depth), key, seconds, llm_suffix, cost_suffix)


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
    for candidate in _TOKEN_ALIASES.get(name, (name,)):
        value = usage.get(candidate, 0) if isinstance(usage, dict) else getattr(usage, candidate, 0)
        if value:
            return int(value)
    return 0


_TOKEN_ALIASES = {
    "input_tokens": ("input_tokens", "prompt_tokens"),
    "output_tokens": ("output_tokens", "completion_tokens"),
    "cache_creation_input_tokens": ("cache_creation_input_tokens",),
    "cache_read_input_tokens": ("cache_read_input_tokens",),
}


def _k(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def _indent(depth: int) -> str:
    return "  " * max(depth - 1, 0)
