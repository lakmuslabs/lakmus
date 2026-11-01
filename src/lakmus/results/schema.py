"""The published JSON Schema for a run file.

Generated from `RunResult`, written to disk and committed; a test fails when the committed copy
drifts. Regenerate with `make schema`.
"""

from pathlib import Path
from typing import Any

from lakmus import schemas
from lakmus.results.models import FORMAT_VERSION, RunResult

NAME = "run"
TITLE = f"Lakmus run result {FORMAT_VERSION}"
SCHEMA_PATH = Path(__file__).parent / "schemas" / f"{NAME}-{FORMAT_VERSION}.json"
SCHEMA_ID = schemas.schema_id(FORMAT_VERSION, NAME)


def build() -> dict[str, Any]:
    return schemas.build(RunResult, format_version=FORMAT_VERSION, name=NAME, title=TITLE)


def render() -> str:
    return schemas.render(RunResult, format_version=FORMAT_VERSION, name=NAME, title=TITLE)


def write() -> Path:
    return schemas.write(
        RunResult, SCHEMA_PATH, format_version=FORMAT_VERSION, name=NAME, title=TITLE
    )


if __name__ == "__main__":
    print(write())
