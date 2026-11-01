"""The published catalogue of built-in scorers.

A suite declares `builtin:exact` and configures it with a `with` block, so any implementation that
validates a manifest — the hub included — needs to know which built-ins exist and what each
accepts. A Markdown table in a document would not do: the specification is not published, and a
table nobody can read from code drifts from the signatures the moment one changes.

So the catalogue is generated from the registry, committed, and checked by a test. `make schema`
regenerates it.
"""

import json
import typing
from pathlib import Path
from typing import Any, Literal

from lakmus import schemas
from lakmus.scorers import BUILTIN_SCORERS
from lakmus.scorers.base import NORMALISERS, ScorerSpec

FORMAT_VERSION = "0.1"
NAME = "scorers"
TITLE = f"Lakmus built-in scorers {FORMAT_VERSION}"
CATALOGUE_PATH = Path(__file__).parent / "schemas" / f"{NAME}-{FORMAT_VERSION}.json"
CATALOGUE_ID = schemas.schema_id(FORMAT_VERSION, NAME)


def describe_option(annotation: Any) -> dict[str, Any]:
    """One option's accepted shape, in terms a consumer in another language can check."""
    origin = typing.get_origin(annotation)
    arguments = typing.get_args(annotation)

    if origin is Literal:
        return {"type": "string", "choices": sorted(str(value) for value in arguments)}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is str:
        return {"type": "string"}
    if annotation in {int, float}:
        return {"type": "number"}
    if origin is not None and isinstance(origin, type):
        if issubclass(origin, dict | typing.Mapping):
            return {"type": "object", "values": "string"}
        if issubclass(origin, list | tuple | typing.Sequence):
            return {"type": "array", "items": "string"}
    return {"type": "unknown"}


def describe(spec: ScorerSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "arity": spec.arity,
        "higher_is_better": spec.higher_is_better,
        "deterministic": spec.deterministic,
        "options": {
            option: {
                "required": option in spec.required_options,
                **describe_option(spec.option_types.get(option)),
            }
            for option in sorted(spec.options)
        },
    }


def build() -> dict[str, Any]:
    return {
        "$schema": schemas.METASCHEMA,
        "$id": CATALOGUE_ID,
        "title": TITLE,
        "lakmus_format": FORMAT_VERSION,
        "normalisation_steps": sorted(NORMALISERS),
        "scorers": [describe(spec) for _, spec in sorted(BUILTIN_SCORERS.items())],
    }


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write() -> Path:
    CATALOGUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOGUE_PATH.write_text(render(), encoding="utf-8")
    return CATALOGUE_PATH


if __name__ == "__main__":
    print(write())
