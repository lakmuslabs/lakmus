"""The published run schema and the models must not drift."""

from typing import Any

from lakmus import schemas
from lakmus.results import schema
from lakmus.results.models import FORMAT_VERSION


def test_the_committed_schema_matches_the_models() -> None:
    assert schema.SCHEMA_PATH.read_text(encoding="utf-8") == schema.render(), (
        "The committed schema is stale. Regenerate it with: make schema"
    )


def test_the_schema_carries_the_identity_a_published_copy_needs() -> None:
    built: dict[str, Any] = schema.build()

    assert built["$id"] == f"{schemas.BASE_URL}/{FORMAT_VERSION}/run.json"
    assert built["$schema"] == schemas.METASCHEMA


def test_the_schema_names_the_fields_a_consumer_reads() -> None:
    required = set(schema.build()["required"])

    assert required >= {"lakmus_format", "run_id", "suite", "target", "summary"}
