"""Parsing `--target`, and finding the adapter it names.

Parsing and availability are separate on purpose: the syntax accepts an adapter this version has
not implemented yet, and `create` is what refuses it, with the list of what does exist. A string
whose prefix is not an adapter is never silently treated as a model name.
"""

import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from importlib.metadata import entry_points
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

from lakmus.errors import TargetConfigError
from lakmus.results.models import ParameterValue
from lakmus.targets.anthropic import AnthropicTarget
from lakmus.targets.base import Target
from lakmus.targets.openai_compatible import (
    OllamaTarget,
    OpenAICompatibleTarget,
    OpenAITarget,
)

ENTRY_POINT_GROUP = "lakmus.targets"
URL_PREFIXES = ("http://", "https://")

BUILTIN_TARGETS: Mapping[str, Callable[[], Target]] = {
    # Factories rather than classes: an adapter satisfies `Target` structurally, and what
    # the registry promises is that calling the entry gives you one.
    AnthropicTarget.adapter: AnthropicTarget,
    OllamaTarget.adapter: OllamaTarget,
    OpenAICompatibleTarget.adapter: OpenAICompatibleTarget,
    OpenAITarget.adapter: OpenAITarget,
}


@dataclass(frozen=True, slots=True)
class TargetReference:
    """A parsed `--target`. The model is separate because an endpoint must not carry a fragment."""

    adapter: str
    identity: str
    model: str | None = None

    _URL_PREFIXES: ClassVar[tuple[str, ...]] = URL_PREFIXES


def parse_target(text: str) -> TargetReference:
    """Read `<adapter>:<identity>`, a bare URL, or `<adapter>:"<command>"`."""
    candidate = text.strip()
    if not candidate:
        raise TargetConfigError("a target is required: --target <adapter>:<model-or-endpoint>")

    if candidate.startswith(URL_PREFIXES):
        identity, model = _split_fragment(candidate)
        return TargetReference(adapter="http", identity=identity, model=model)

    adapter, separator, rest = candidate.partition(":")
    if not separator or not adapter:
        raise TargetConfigError(
            f"{text!r} is not a target. Write <adapter>:<model-or-endpoint>, for example "
            f"openai:gpt-x or openai-compatible:https://host/v1#llama-3. "
            f"Adapters: {', '.join(adapter_names())}."
        )

    rest = _unquote(rest.strip())
    if not rest:
        raise TargetConfigError(f"{text!r} names the {adapter} adapter but nothing to run against")

    if rest.startswith(URL_PREFIXES):
        identity, model = _split_fragment(rest)
        return TargetReference(adapter=adapter, identity=identity, model=model)
    return TargetReference(adapter=adapter, identity=rest)


def parse_parameter(text: str) -> tuple[str, ParameterValue]:
    """Read one `-P name=value`.

    The value is parsed as a TOML scalar, so `top_p=0.9` is a float and `stream=false` is a
    boolean rather than everything arriving as a string. Anything that is not valid TOML is taken
    as a plain string, which is what makes `style=concise` work without quoting.
    """
    name, separator, raw = text.partition("=")
    name = name.strip()
    if not separator or not name:
        raise TargetConfigError(f"{text!r} is not a parameter. Write -P name=value.")

    value: Any
    try:
        value = tomllib.loads(f"value = {raw.strip()}")["value"]
    except (tomllib.TOMLDecodeError, ValueError):
        value = raw.strip()

    if not isinstance(value, str | bool | int | float):
        raise TargetConfigError(
            f"-P {name}: only a string, number or boolean is accepted, not {type(value).__name__}. "
            f"A parameter is recorded in the target fingerprint, which holds scalars."
        )
    return name, value


def adapter_names() -> list[str]:
    """Every adapter this installation can build, built-in or installed alongside."""
    return sorted(set(BUILTIN_TARGETS) | set(_from_entry_points()))


def create(adapter: str) -> Target:
    """Build an adapter by name.

    An adapter published by an installed package is imported without asking. That is deliberate,
    and is not the posture taken for a suite's scorers: an adapter is here because someone
    installed a package, a decision with its own consent step, while a scorer arrives inside data
    that may have been cloned from a stranger.
    """
    implementation = BUILTIN_TARGETS.get(adapter) or _from_entry_points().get(adapter)
    if implementation is None:
        raise TargetConfigError(
            f"no adapter named {adapter!r}. Available: {', '.join(adapter_names())}."
        )
    return implementation()


@cache
def _from_entry_points() -> Mapping[str, Callable[[], Target]]:
    found: dict[str, Callable[[], Target]] = {}
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        if entry.name in BUILTIN_TARGETS:
            continue
        try:
            loaded = entry.load()
        except Exception as exc:
            raise TargetConfigError(
                f"the {entry.name!r} adapter is installed but will not import: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        found[entry.name] = loaded
    return found


def _split_fragment(url: str) -> tuple[str, str | None]:
    """Take the model out of a URL fragment.

    An endpoint must not carry a fragment — it would be sent nowhere and mean nothing to the
    server — but one string per target is what a run file records, so the fragment is how a target
    names the model behind an endpoint.
    """
    parts = urlsplit(url)
    identity = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    return identity, parts.fragment or None


def _unquote(text: str) -> str:
    for quote in ('"', "'"):
        if len(text) >= 2 and text.startswith(quote) and text.endswith(quote):
            return text[1:-1]
    return text
