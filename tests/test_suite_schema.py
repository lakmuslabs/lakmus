"""The published schema and the models must not drift.

Other implementations, the hub included, validate manifests against the committed copy rather
than against these models. A copy that is out of date is a contract that is out of date.
"""

import json
from typing import Any

from lakmus.suite import schema
from lakmus.suite.models import FORMAT_VERSION


def test_the_committed_schema_matches_the_models() -> None:
    assert schema.SCHEMA_PATH.read_text(encoding="utf-8") == schema.render(), (
        "The committed schema is stale. Regenerate it with: make schema"
    )


def test_the_schema_carries_the_identity_a_published_copy_needs() -> None:
    built: dict[str, Any] = schema.build()

    assert built["$id"].endswith(f"/{FORMAT_VERSION}/suite.json")
    assert built["$schema"] == schema.METASCHEMA


def test_the_schema_describes_the_file_an_author_writes() -> None:
    """Field names come from the aliases, not from the Python attribute names."""
    properties = json.loads(schema.SCHEMA_PATH.read_text(encoding="utf-8"))["properties"]
    output = schema.build()["$defs"]["OutputSpec"]["properties"]

    assert "schema" in output
    assert "schema_" not in output
    assert set(properties) >= {"lakmus", "id", "version", "title", "language", "license"}
