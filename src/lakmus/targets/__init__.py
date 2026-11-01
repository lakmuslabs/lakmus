"""The Target protocol, the built-in adapters, and the adapter registry."""

from lakmus.targets.anthropic import AnthropicTarget
from lakmus.targets.base import (
    RenderedCase,
    Step,
    Target,
    TargetConfig,
    TargetError,
    TargetKind,
    TargetOutput,
    Usage,
)
from lakmus.targets.http_model import HttpModelTarget, Reading
from lakmus.targets.openai_compatible import (
    OllamaTarget,
    OpenAICompatibleTarget,
    OpenAITarget,
)
from lakmus.targets.pricing import ModelPrice, PricingTable
from lakmus.targets.registry import (
    BUILTIN_TARGETS,
    TargetReference,
    adapter_names,
    create,
    parse_parameter,
    parse_target,
)

__all__ = [
    "BUILTIN_TARGETS",
    "AnthropicTarget",
    "HttpModelTarget",
    "ModelPrice",
    "OllamaTarget",
    "OpenAICompatibleTarget",
    "OpenAITarget",
    "PricingTable",
    "Reading",
    "RenderedCase",
    "Step",
    "Target",
    "TargetConfig",
    "TargetError",
    "TargetKind",
    "TargetOutput",
    "TargetReference",
    "Usage",
    "adapter_names",
    "create",
    "parse_parameter",
    "parse_target",
]
