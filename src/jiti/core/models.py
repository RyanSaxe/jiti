"""Model selection for the default engine."""

from __future__ import annotations

import os
from enum import StrEnum

ENV_VAR = "JITI_MODEL"


class Model(StrEnum):
    """Claude model IDs used by default. LiteLLM accepts these values as-is."""

    OPUS_4_8 = "claude-opus-4-8"
    SONNET_4_6 = "claude-sonnet-4-6"
    HAIKU_4_5 = "claude-haiku-4-5"


DEFAULT_MODEL = Model.SONNET_4_6


def resolve_default() -> str:
    """Pick the model id, honoring `JITI_MODEL` when set."""
    setting = os.environ.get(ENV_VAR, "").strip()
    if not setting:
        return DEFAULT_MODEL
    return setting
