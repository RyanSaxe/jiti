"""Opt-in logging: cost estimation and the JITI_LOG toggle."""

import logging

from jiti.core import log as _log


class _Usage:
    input_tokens = 1000
    output_tokens = 500
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 2000


class _OpenAIUsage:
    prompt_tokens = 1000
    completion_tokens = 500


def test_cost_for_a_known_model():
    estimate = _log.cost("claude-sonnet-4-6", _Usage())

    # 1000*3 + 500*15 + 2000*0.3 = 11_100 microdollars -> $0.0111
    assert estimate is not None
    assert round(estimate, 4) == 0.0111


def test_cost_accepts_openai_style_usage_names():
    estimate = _log.cost("claude-sonnet-4-6", _OpenAIUsage())

    assert estimate is not None
    assert round(estimate, 4) == 0.0105


def test_cost_is_none_for_unknown_model_or_missing_usage():
    assert _log.cost("not-a-model", _Usage()) is None
    assert _log.cost("claude-sonnet-4-6", None) is None


def test_configure_attaches_a_handler_when_enabled(monkeypatch):
    monkeypatch.setenv("JITI_LOG", "info")
    logger = logging.getLogger("jiti")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    try:
        _log.configure()
        assert any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers)
        assert logger.level == logging.INFO
    finally:
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
