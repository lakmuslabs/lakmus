"""Invariants the run file enforces rather than hopes for."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from lakmus.results.models import (
    Latency,
    MetricSummary,
    ParameterValue,
    RunConfig,
    RunResult,
    SuiteRef,
    TargetFingerprint,
    ThresholdApplied,
    ToolInfo,
    canonical_parameter,
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
    with pytest.raises(ValidationError, match="exactly when a threshold"):
        _summary(passed=True)
    with pytest.raises(ValidationError, match="conclusive needs a threshold"):
        _summary(conclusive=True)


def test_a_threshold_without_a_verdict_is_refused() -> None:
    """So that a consumer can never read a declared gate as unevaluated."""
    with pytest.raises(ValidationError, match="exactly when a threshold"):
        _summary(threshold=ThresholdApplied(max=0.6))


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


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        pytest.param("http://[::1]:8000/agent", "http://[::1]:8000/agent", id="ipv6-with-a-port"),
        pytest.param("http://[2001:db8::1]/v1", "http://[2001:db8::1]/v1", id="ipv6-bare"),
        pytest.param("HTTPS://API.Example.com/v1", "https://api.example.com/v1", id="case"),
        pytest.param(
            "https://api.example.com/v1#llama-3",
            "https://api.example.com/v1#llama-3",
            id="fragment-names-the-model-and-is-kept",
        ),
        pytest.param("//user:pw@api.example.com/v1", "//api.example.com/v1", id="no-scheme"),
    ],
)
def test_an_identity_is_reduced_to_one_publishable_spelling(identity: str, expected: str) -> None:
    assert sanitise_identity(identity) == expected
    created = TargetFingerprint.create(adapter="openai-compatible", kind="model", identity=identity)
    assert created.identity == expected


def test_two_spellings_of_one_endpoint_digest_the_same() -> None:
    """Otherwise a cache miss, and two runs of one target that look incomparable."""
    first = TargetFingerprint.create(
        adapter="openai-compatible", kind="model", identity="HTTPS://API.Example.com/v1"
    )
    second = TargetFingerprint.create(
        adapter="openai-compatible", kind="model", identity="https://api.example.com/v1"
    )

    assert first.sha256 == second.sha256


def test_an_identity_that_assigns_a_credential_is_refused() -> None:
    """An MCP server is named by a command line, which cannot be sanitised safely."""
    with pytest.raises(ValidationError, match="must not assign a credential"):
        TargetFingerprint.create(
            adapter="mcp-stdio", kind="mcp", identity="python server.py --api-key=sk-live-1"
        )


def test_a_command_line_without_a_credential_is_left_alone() -> None:
    identity = "python server.py --max-tokens=512 --token-budget=5"

    fingerprint = TargetFingerprint.create(adapter="mcp-stdio", kind="mcp", identity=identity)

    assert fingerprint.identity == identity


@pytest.mark.parametrize(
    "name", ["api_key", "API-KEY", "secret", "password", "authorization", "access_token"]
)
def test_a_parameter_that_names_a_credential_is_refused(name: str) -> None:
    """The rule is that no credential enters a fingerprint, not just none in the URL."""
    with pytest.raises(ValidationError, match="carries no credential"):
        TargetFingerprint.create(
            adapter="openai", kind="model", identity="gpt-x", parameters={name: "sk-live-1"}
        )


def test_a_generation_parameter_that_merely_looks_like_one_is_kept() -> None:
    fingerprint = TargetFingerprint.create(
        adapter="openai", kind="model", identity="gpt-x", parameters={"max_tokens": 512}
    )

    assert fingerprint.parameters == {"max_tokens": 512}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(True, "true", id="true"),
        pytest.param(False, "false", id="false"),
        pytest.param(7, "7", id="int"),
        pytest.param(0.0, "0", id="zero-as-a-float"),
        pytest.param(2.0, "2", id="integral-float"),
        pytest.param(0.7, "0.7", id="fraction"),
        pytest.param(1e22, "10000000000000000000000", id="large-float-has-no-exponent"),
        pytest.param(1e-05, "1e-5", id="exponent-without-leading-zeroes"),
        pytest.param("gpt-x", "gpt-x", id="string"),
    ],
)
def test_a_parameter_has_one_spelling_whatever_runtime_reads_it(
    value: ParameterValue, expected: str
) -> None:
    """`json.dumps` writes 1e+22 where other runtimes write 1e22, and 0.0 where 0 is the same.

    A fingerprint digest is compared across implementations and used as a cache key.
    """
    assert canonical_parameter(value) == expected


def test_the_same_number_written_two_ways_digests_the_same() -> None:
    as_int = TargetFingerprint.create(
        adapter="openai", kind="model", identity="gpt-x", parameters={"temperature": 0}
    )
    as_float = TargetFingerprint.create(
        adapter="openai", kind="model", identity="gpt-x", parameters={"temperature": 0.0}
    )

    assert as_int.sha256 == as_float.sha256


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_parameter_is_refused(value: float) -> None:
    with pytest.raises((ValidationError, ValueError)):
        TargetFingerprint.create(
            adapter="openai", kind="model", identity="gpt-x", parameters={"top_p": value}
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_a_non_finite_measurement_is_refused(value: float) -> None:
    """json.dumps spells a NaN as a bare token no JSON parser accepts."""
    with pytest.raises(ValidationError):
        _summary(value=value)


def test_a_run_file_is_valid_json() -> None:
    document = _run().model_dump_json_document()

    assert json.loads(json.dumps(document, allow_nan=False)) == document


def test_two_models_behind_one_endpoint_do_not_share_a_fingerprint() -> None:
    """Why the fragment survives sanitisation while userinfo and the query do not."""
    first = TargetFingerprint.create(
        adapter="openai-compatible", kind="model", identity="https://host/v1#llama-3"
    )
    second = TargetFingerprint.create(
        adapter="openai-compatible", kind="model", identity="https://host/v1#mistral-small"
    )

    assert first.sha256 != second.sha256
