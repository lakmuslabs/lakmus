"""When two runs may be placed side by side."""

from datetime import UTC, datetime
from typing import Any

import pytest

from lakmus.results import comparable
from lakmus.results.models import (
    Latency,
    MetricSummary,
    RunConfig,
    RunResult,
    SuiteRef,
    TargetFingerprint,
    ToolInfo,
)


def _run(**overrides: Any) -> RunResult:
    fields: dict[str, Any] = {
        "lakmus_format": "0.1",
        "run_id": "01JC0000000000000000000000",
        "created_at": datetime(2026, 11, 1, tzinfo=UTC),
        "tool": ToolInfo(name="lakmus", version="0.0.1", python="3.12.13"),
        "suite": SuiteRef(
            id="rh/equite-tri-cv-fr",
            version="1.2.0",
            inputs_sha256="a" * 64,
            content_sha256="b" * 64,
        ),
        "target": TargetFingerprint.create(adapter="openai", kind="model", identity="gpt-x"),
        "config": RunConfig(repeats=1, temperature=0.0, seed=0, timeout_s=60, concurrency=8),
        "summary": {
            "score_gap": MetricSummary(
                value=0.01,
                ci95=(0.0, 0.02),
                n_cases=20,
                n_observations=20,
                n_errors=0,
                deterministic=True,
            )
        },
        "latency_ms": Latency(p50=100, p95=300),
    }
    fields.update(overrides)
    return RunResult.model_validate(fields)


def test_two_runs_of_the_same_suite_are_comparable() -> None:
    assert comparable(_run(), _run())


def test_a_different_target_is_the_whole_point_of_comparing() -> None:
    other = TargetFingerprint.create(adapter="ollama", kind="model", identity="mistral-small")

    assert comparable(_run(), _run(target=other))


def test_metadata_that_cannot_change_a_measurement_does_not_break_comparability() -> None:
    """Title, licence and the like live outside inputs_sha256 by design."""
    later = _run(created_at=datetime(2027, 1, 1, tzinfo=UTC))

    assert comparable(_run(), later)


def test_a_different_suite_digest_is_not_comparable() -> None:
    other = SuiteRef(
        id="rh/equite-tri-cv-fr", version="1.2.0", inputs_sha256="c" * 64, content_sha256="b" * 64
    )

    assert not comparable(_run(), _run(suite=other))


def test_an_override_breaks_comparability_although_the_digest_is_unchanged() -> None:
    """The hole a digest alone leaves: `--temperature 0.7` measures something else."""
    hotter = RunConfig(
        repeats=1,
        temperature=0.7,
        seed=0,
        timeout_s=60,
        concurrency=8,
        overrides={"temperature": 0.7},
    )

    assert not comparable(_run(), _run(config=hotter))


def test_two_runs_with_the_same_override_are_comparable_with_each_other() -> None:
    config = RunConfig(
        repeats=1,
        temperature=0.7,
        seed=0,
        timeout_s=60,
        concurrency=8,
        overrides={"temperature": 0.7},
    )

    assert comparable(_run(config=config), _run(config=config))


def test_two_runs_overridden_to_different_values_are_not_comparable() -> None:
    """The hole the names alone left: both runs override temperature, to different numbers."""

    def at(temperature: float) -> RunConfig:
        return RunConfig(
            repeats=1,
            temperature=temperature,
            seed=0,
            timeout_s=60,
            concurrency=8,
            overrides={"temperature": temperature},
        )

    assert not comparable(_run(config=at(0.2)), _run(config=at(1.5)))


@pytest.mark.parametrize("seed", [0, 7])
def test_a_different_seed_alone_does_not_break_comparability(seed: int) -> None:
    """A seed changes which cases a bootstrap draws, not what was measured."""
    config = RunConfig(repeats=1, temperature=0.0, seed=seed, timeout_s=60, concurrency=8)

    assert comparable(_run(), _run(config=config))
