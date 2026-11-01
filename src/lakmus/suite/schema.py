"""The published JSON Schema for a manifest.

Generated from `Manifest`, written to disk and committed; a test fails when the committed copy
drifts. Regenerate with `make schema`.

The generated files live in `schemas/` rather than `schema/`: a data directory sharing a name with
this module resolves only by the rule that a file module beats a namespace package, which is a
trap for packaging tools.
"""

from pathlib import Path
from typing import Any

from lakmus import schemas
from lakmus.suite.models import FORMAT_VERSION, Manifest

NAME = "suite"
TITLE = f"Lakmus suite manifest {FORMAT_VERSION}"
SCHEMA_PATH = Path(__file__).parent / "schemas" / f"{NAME}-{FORMAT_VERSION}.json"
SCHEMA_ID = schemas.schema_id(FORMAT_VERSION, NAME)


def build() -> dict[str, Any]:
    return schemas.build(Manifest, format_version=FORMAT_VERSION, name=NAME, title=TITLE)


def render() -> str:
    return schemas.render(Manifest, format_version=FORMAT_VERSION, name=NAME, title=TITLE)


def write() -> Path:
    return schemas.write(
        Manifest, SCHEMA_PATH, format_version=FORMAT_VERSION, name=NAME, title=TITLE
    )


if __name__ == "__main__":
    print(write())
