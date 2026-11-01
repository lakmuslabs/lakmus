"""The published JSON Schema for a manifest.

The schema is generated from `Manifest`, written to disk, and committed. A test regenerates it and
fails if the committed file differs: the hub and any other implementation validate against the
published copy, so the copy and the models must not drift.

Regenerate with `make schema`.
"""

import json
from pathlib import Path
from typing import Any

from lakmus.suite.models import FORMAT_VERSION, Manifest

SCHEMA_PATH = Path(__file__).parent / "schema" / f"suite-{FORMAT_VERSION}.json"
SCHEMA_ID = f"https://lakmuslabs.github.io/lakmus/schemas/{FORMAT_VERSION}/suite.json"
METASCHEMA = "https://json-schema.org/draft/2020-12/schema"


def build() -> dict[str, Any]:
    """The schema as a dictionary, with the identity a published copy needs."""
    generated = Manifest.model_json_schema(by_alias=True, mode="validation")
    return {
        "$schema": METASCHEMA,
        "$id": SCHEMA_ID,
        "title": f"Lakmus suite manifest {FORMAT_VERSION}",
        **generated,
    }


def render() -> str:
    """The exact bytes of the committed file."""
    return json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write() -> Path:
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(render(), encoding="utf-8")
    return SCHEMA_PATH


if __name__ == "__main__":
    print(write())
