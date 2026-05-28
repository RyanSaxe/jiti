"""The set of Claude models jiti knows how to drive, with their canonical IDs."""

from __future__ import annotations

import os
from enum import StrEnum

from jiti.core.errors import JitiError

ENV_VAR = "JITI_MODEL"


class Model(StrEnum):
    """Anthropic model IDs jiti supports. Values are accepted as-is by the Anthropic SDK."""

    OPUS_4_7 = "claude-opus-4-7"
    SONNET_4_6 = "claude-sonnet-4-6"
    HAIKU_4_5 = "claude-haiku-4-5"


DEFAULT_MODEL = Model.OPUS_4_7


def resolve_default() -> Model:
    """Pick the default model, honoring `JITI_MODEL` if set to one of the enum values."""
    setting = os.environ.get(ENV_VAR, "").strip()
    if not setting:
        return DEFAULT_MODEL
    try:
        return Model(setting)
    except ValueError as error:
        valid = ", ".join(model.value for model in Model)
        raise JitiError(f"{ENV_VAR}={setting!r} is not a known model. Valid: {valid}.") from error
