"""The scorer decorator, the built-in scorers, and loading a suite's own scorers."""

from collections.abc import Mapping

from lakmus.scorers.base import (
    NORMALISERS,
    Arity,
    Case,
    PairOutputs,
    Score,
    ScorerSpec,
    normalise_text,
    object_of,
    scorer,
    spec_of,
    text_of,
)
from lakmus.scorers.pairs import pair_flip, pair_gap
from lakmus.scorers.structured import json_field_match, json_schema, precision_recall_f1
from lakmus.scorers.text import exact, regex


def _registry() -> Mapping[str, ScorerSpec]:
    """Built-ins by name.

    Written out rather than discovered: a registry that fills itself on import depends on which
    modules happen to have been imported, and `lakmus validate` must list the same names whatever
    the caller did first.
    """
    functions = (
        exact,
        regex,
        json_schema,
        json_field_match,
        precision_recall_f1,
        pair_gap,
        pair_flip,
    )
    specs: dict[str, ScorerSpec] = {}
    for function in functions:
        spec = spec_of(function)
        if spec is None:  # pragma: no cover - a built-in without the decorator is a bug
            raise RuntimeError(f"{function.__name__} is not decorated with @scorer")
        specs[spec.name] = spec
    return specs


BUILTIN_SCORERS: Mapping[str, ScorerSpec] = _registry()

__all__ = [
    "BUILTIN_SCORERS",
    "NORMALISERS",
    "Arity",
    "Case",
    "PairOutputs",
    "Score",
    "ScorerSpec",
    "exact",
    "json_field_match",
    "json_schema",
    "normalise_text",
    "object_of",
    "pair_flip",
    "pair_gap",
    "precision_recall_f1",
    "regex",
    "scorer",
    "spec_of",
    "text_of",
]
