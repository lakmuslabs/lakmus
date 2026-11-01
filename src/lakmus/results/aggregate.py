"""Turning per-case scores into the summaries a run file carries.

The input is deliberately plain: a metric, its cases, and each case's repeat scores. Aggregation
knows nothing about targets, prompts or manifests, which is what lets it be tested with a table
instead of a run.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from lakmus.results.models import GroupSummary, MetricSummary, ThresholdApplied
from lakmus.results.statistics import MINIMUM_CASES as MINIMUM_CASES_FOR_CI
from lakmus.results.statistics import RESAMPLES, bootstrap_ci, point_estimate

_TOO_FEW_CASES = f"fewer than {MINIMUM_CASES_FOR_CI} scored cases"


@dataclass(frozen=True, slots=True)
class CaseObservation:
    """One case's scores for one metric, and the groups it belongs to."""

    case_id: str
    scores: tuple[float, ...]
    groups: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.scores:
            raise ValueError(f"case {self.case_id!r} has no score; count it in n_errors instead")
        if not all(math.isfinite(score) for score in self.scores):
            # A scorer is third-party code. Let a NaN through and it reaches a run file as the
            # bare token `NaN`, which no JSON parser accepts, and a verdict nobody measured.
            raise ValueError(
                f"case {self.case_id!r} has a score that is not a finite number: {self.scores}"
            )


@dataclass(frozen=True, slots=True)
class MetricObservations:
    """Everything aggregation needs for one metric."""

    metric_id: str
    deterministic: bool
    cases: tuple[CaseObservation, ...] = ()
    n_errors: int = 0
    threshold: ThresholdApplied | None = None
    group_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.deterministic:
            return
        single = sorted(case.case_id for case in self.cases if len(case.scores) < 2)
        if single:
            # With one repeat a judge's variance is invisible, and the interval would understate
            # the uncertainty without saying so. The engine sets repeats; this is the boundary
            # where the requirement can still be caught.
            raise ValueError(
                f"metric {self.metric_id!r} is not deterministic, so every case needs at least "
                f"two repeats; these have one: {', '.join(single)}"
            )


def _satisfies(value: float, threshold: ThresholdApplied) -> bool:
    return (threshold.min is None or value >= threshold.min) and (
        threshold.max is None or value <= threshold.max
    )


def _verdict(
    value: float | None, ci95: tuple[float, float] | None, threshold: ThresholdApplied | None
) -> tuple[bool | None, bool | None]:
    """Whether the run passed, and whether a rerun would agree.

    The verdict is on the point estimate so that a gate is predictable and does not move with the
    number of cases. `conclusive` carries what the interval adds: false means the interval
    straddles the threshold, so this verdict could go the other way next time.
    """
    if threshold is None:
        return None, None
    if value is None:
        # A gate was declared and nothing was measured. Reporting `None` here would read as
        # "no gate", and a run where every case failed would exit 0.
        return False, None
    passed = _satisfies(value, threshold)
    if ci95 is None:
        return passed, None
    low, high = ci95
    wholly_inside = _satisfies(low, threshold) and _satisfies(high, threshold)
    wholly_outside = (threshold.min is not None and high < threshold.min) or (
        threshold.max is not None and low > threshold.max
    )
    return passed, wholly_inside or wholly_outside


def _interval(
    cases: Sequence[CaseObservation], *, seed: int, resamples: int
) -> tuple[tuple[float, float] | None, str | None]:
    if len(cases) < MINIMUM_CASES_FOR_CI:
        return None, _TOO_FEW_CASES
    ci95 = bootstrap_ci([case.scores for case in cases], seed=seed, resamples=resamples)
    if ci95 is None:
        return None, _TOO_FEW_CASES
    return ci95, None


def summarise(
    observations: MetricObservations, *, seed: int, resamples: int = RESAMPLES
) -> MetricSummary:
    """One metric's entry in `summary`."""
    cases = observations.cases
    value = point_estimate([case.scores for case in cases]) if cases else None
    ci95, reason = _interval(cases, seed=seed, resamples=resamples)
    passed, conclusive = _verdict(value, ci95, observations.threshold)

    return MetricSummary(
        value=value,
        ci95=ci95,
        ci_unavailable_reason=reason,
        n_cases=len(cases),
        n_observations=sum(len(case.scores) for case in cases),
        n_errors=observations.n_errors,
        deterministic=observations.deterministic,
        threshold=observations.threshold,
        passed=passed,
        conclusive=conclusive,
    )


def summarise_groups(
    observations: MetricObservations, *, seed: int, resamples: int = RESAMPLES
) -> dict[str, dict[str, GroupSummary]]:
    """One metric's entry in `by_group`: group field, then group value.

    A case that does not carry a group field is left out of that field's breakdown rather than
    bucketed under a stand-in value, so that a breakdown never claims more cases than it saw.
    """
    breakdown: dict[str, dict[str, GroupSummary]] = {}
    for group_field in observations.group_by:
        buckets: dict[str, list[CaseObservation]] = {}
        for case in observations.cases:
            group_value = case.groups.get(group_field)
            if group_value is not None:
                buckets.setdefault(group_value, []).append(case)
        if not buckets:
            continue
        breakdown[group_field] = {
            group_value: GroupSummary(
                value=point_estimate([case.scores for case in bucket]),
                ci95=_interval(bucket, seed=seed, resamples=resamples)[0],
                n_cases=len(bucket),
            )
            for group_value, bucket in sorted(buckets.items())
        }
    return breakdown


def aggregate(
    metrics: Sequence[MetricObservations], *, seed: int, resamples: int = RESAMPLES
) -> tuple[dict[str, MetricSummary], dict[str, dict[str, dict[str, GroupSummary]]]]:
    """Every metric's summary and breakdown, keyed by metric id."""
    duplicates = sorted(
        {m.metric_id for m in metrics if sum(1 for o in metrics if o.metric_id == m.metric_id) > 1}
    )
    if duplicates:
        raise ValueError(f"metric declared twice: {', '.join(duplicates)}")

    summary = {m.metric_id: summarise(m, seed=seed, resamples=resamples) for m in metrics}
    by_group = {
        m.metric_id: breakdown
        for m in metrics
        if (breakdown := summarise_groups(m, seed=seed, resamples=resamples))
    }
    return summary, by_group
