"""Invariants the run file enforces rather than hopes for."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from lakmus.results.models import (
    Latency,
    MetricSummary,
    RunConfig,
    RunResult,
    SuiteRef,
    TargetFingerprint,
    ThresholdApplied,
    ToolInfo,
    sanitise_identity,
)

DIGEST = "a" * 64


def _fingerprint() -> TargetFingerprint:
    return TargetFingerprint.create(
        adapter="openai", kind="model", identity="gpt-x", parameters={"temperature": 0.0}
    )


def _summary(**overrides: object) -> MetricSummary:
    fields: dict[str, object] = {
        "value": 0.5,
        "ci95": (0.4, 0.6),
        "n_cases": 10,
        "n_observations": 10,
        "n_errors": 0,
        "deterministic": True,
    }
    fields.update(overrides)
    return MetricSummary.model_validate(fields)


def _run(**overrides: object) -> RunResult:
    fields: dict[str, object] = {
        "lakmus_format": "0.1",
        "run_id": "01JC0000000000000000000000",
        "created_at": datetime(2026, 11, 1, tzinfo=UTC),
        "tool": ToolInfo(name="lakmus", version="0.0.1", python="3.12.13"),
        "suite": SuiteRef(
            id="rh/equite-tri-cv-fr",
            version="1.2.0",
            inputs_sha256=DIGEST,
            content_sha256="b" * 64,
        ),
        "target": _fingerprint(),
        "config": RunConfig(repeats=1, temperature=0.0, seed=0, timeout_s=60, concurrency=8),
        "summary": {"score_gap": _summary()},
        "latency_ms": Latency(p50=120, p95=400),
    }
    fields.update(overrides)
    return RunResult.model_validate(fields)


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        pytest.param("gpt-x", "gpt-x", id="a-model-name-is-left-alone"),
        pytest.param(
            "https://user:secret@api.example.com/v1/chat",
            "https://api.example.com/v1/chat",
            id="userinfo-is-removed",
        ),
        pytest.param(
            "https://api.example.com/v1?api_key=sk-live-123",
            "https://api.example.com/v1",
            id="query-string-is-removed",
        ),
        pytest.param(
            "http://localhost:8000/agent", "http://localhost:8000/agent", id="port-is-kept"
        ),
    ],
)
def test_an_identity_keeps_nothing_that_could_be_a_credential(identity: str, expected: str) -> None:
    assert sanitise_identity(identity) == expected


def test_a_fingerprint_cannot_be_built_around_a_credential() -> None:
    fingerprint = TargetFingerprint.create(
        adapter="openai-compatible",
        kind="model",
        identity="https://me:hunter2@api.example.com/v1?token=abc",
    )

    assert fingerprint.identity == "https://api.example.com/v1"


def test_a_fingerprint_carrying_a_credential_is_refused() -> None:
    """`create` sanitises; the validator means a hand-written record cannot slip one past it."""
    with pytest.raises(ValidationError, match="no userinfo"):
        TargetFingerprint(
            adapter="openai",
            kind="model",
            identity="https://me:hunter2@api.example.com/v1",
            sha256=DIGEST,
        )


def test_a_fingerprint_cannot_claim_a_digest_it_does_not_have() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        TargetFingerprint(adapter="openai", kind="model", identity="gpt-x", sha256=DIGEST)


def test_the_digest_changes_with_every_part_of_the_identity() -> None:
    base = _fingerprint()
    others = [
        TargetFingerprint.create(
            adapter="ollama", kind="model", identity="gpt-x", parameters={"temperature": 0.0}
        ),
        TargetFingerprint.create(
            adapter="openai", kind="agent", identity="gpt-x", parameters={"temperature": 0.0}
        ),
        TargetFingerprint.create(
            adapter="openai", kind="model", identity="gpt-y", parameters={"temperature": 0.0}
        ),
        TargetFingerprint.create(
            adapter="openai",
            kind="model",
            identity="gpt-x",
            declared_version="2026-10-01",
            parameters={"temperature": 0.0},
        ),
        TargetFingerprint.create(
            adapter="openai", kind="model", identity="gpt-x", parameters={"temperature": 0.7}
        ),
    ]

    assert {other.sha256 for other in others}.isdisjoint({base.sha256})
    assert len({other.sha256 for other in others}) == len(others)


@pytest.mark.parametrize(
    "created_at",
    [
        pytest.param(datetime(2026, 11, 1), id="naive"),
        pytest.param(datetime(2026, 11, 1, tzinfo=timezone(timedelta(hours=2))), id="offset"),
    ],
)
def test_a_run_must_be_stamped_in_utc(created_at: datetime) -> None:
    with pytest.raises(ValidationError, match="UTC"):
        _run(created_at=created_at)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(("temperature", "repeats"), id="unsorted"),
        pytest.param(("repeats", "repeats"), id="duplicated"),
    ],
)
def test_overrides_are_canonical_because_comparability_compares_them(
    overrides: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError, match="sorted"):
        RunConfig(
            repeats=1,
            temperature=0.0,
            seed=0,
            timeout_s=60,
            concurrency=8,
            overrides=overrides,
        )


def test_a_metric_without_an_interval_has_to_say_why() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        _summary(ci95=None)
    with pytest.raises(ValidationError, match="exactly one"):
        _summary(ci_unavailable_reason="too few cases")


def test_a_verdict_without_a_threshold_is_refused() -> None:
    with pytest.raises(ValidationError, match="need a threshold"):
        _summary(passed=True)


def test_an_interval_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match=r"\(low, high\)"):
        _summary(ci95=(0.6, 0.4))


def test_a_threshold_needs_a_bound() -> None:
    with pytest.raises(ValidationError, match="needs min, max"):
        ThresholdApplied()
    with pytest.raises(ValidationError, match="must not exceed"):
        ThresholdApplied(min=1.0, max=0.0)


def test_a_breakdown_cannot_name_a_metric_the_summary_does_not() -> None:
    with pytest.raises(ValidationError, match="absent from summary"):
        _run(by_group={"unknown_metric": {}})


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        _run(leaderboard_rank=1)


def test_a_run_round_trips_through_its_own_json() -> None:
    run = _run()

    assert RunResult.model_validate(run.model_dump_json_document()) == run
