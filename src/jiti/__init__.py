"""jiti — Just In Time Implementation.

Declare a function or method by its typed interface; jiti has an LLM write, validate, and
cache a real implementation you can read, edit, and own.
"""

from jiti.decorator import jiti
from jiti.errors import ConflictError, GenerationError, JitiError, RealBodyError
from jiti.model import AnthropicModel, Model
from jiti.strategy import Codegen, Strategy

__all__ = [
    "AnthropicModel",
    "Codegen",
    "ConflictError",
    "GenerationError",
    "JitiError",
    "Model",
    "RealBodyError",
    "Strategy",
    "jiti",
]
