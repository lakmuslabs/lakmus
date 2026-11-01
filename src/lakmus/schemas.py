"""Publishing a JSON Schema generated from a Pydantic model.

Other implementations — the hub included — validate against the committed copies rather than
against these models, so a copy that is out of date is a contract that is out of date. One
generator serves every published schema; a test per schema fails when its copy drifts.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

METASCHEMA = "https://json-schema.org/draft/2020-12/schema"
BASE_URL = "https://lakmuslabs.github.io/lakmus/schemas"


def schema_id(format_version: str, name: str) -> str:
    """The immutable URL a published schema is served at.

    A path that has been published never changes; a change to a schema takes a new format version.
    """
    return f"{BASE_URL}/{format_version}/{name}.json"


def build(model: type[BaseModel], *, format_version: str, name: str, title: str) -> dict[str, Any]:
    generated = model.model_json_schema(by_alias=True, mode="validation")
    return {
        "$schema": METASCHEMA,
        "$id": schema_id(format_version, name),
        "title": title,
        **generated,
    }


def render(model: type[BaseModel], *, format_version: str, name: str, title: str) -> str:
    """The exact bytes of a committed copy."""
    document = build(model, format_version=format_version, name=name, title=title)
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(
    model: type[BaseModel], path: Path, *, format_version: str, name: str, title: str
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render(model, format_version=format_version, name=name, title=title), encoding="utf-8"
    )
    return path
