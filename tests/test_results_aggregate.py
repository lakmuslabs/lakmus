"""Per-metric summaries, breakdowns and verdicts."""

import pytest

from lakmus.results.aggregate import CaseObservation, MetricObservations, aggregate, summarise
from lakmus.results.models import ThresholdApplied

SEED = 42


def _cases(
    values: list[float], groups: dict[str, str] | None = None
) -> tuple[CaseObservation, ...]:
    return tuple(
        CaseObservation(case_id=f"c-{index}", scores=(value,), groups=dict(groups or {}))
        for index, value in enumerate(values)
    )


def _observations(**overrides: object) -> MetricObservations:
    fields: dict[str, object] = {
        "metric_id": "score_gap",
        "deterministic": True,
        "cases": _cases([0.01] * 20),
    }
    fields.update(overrides)
    return MetricObservations(**fields)  # type: ignore[arg-type]


def test_a_case_without_a_score_is_refused() -> None:
    """A case that produced nothing belongs in n_errors, not in an average."""
    with pytest.raises(ValueError, match="n_errors"):
        CaseObservation(case_id="c-1", scores=())


def test_a_metric_over_no_cases_has_no_value() -> None:
    """Recording 0.0 would be a measurement nobody made."""
    summary = summarise(_observations(cases=(), n_errors=12), seed=SEED)

    assert summary.value is None
    assert summary.n_cases == 0
    assert summary.n_errors == 12
    assert summary.ci_unavailable_reason is not None


def test_a_gate_that_could_not_be_evaluated_does_not_pass() -> None:
    """A run where every case failed must not exit 0.

    Reporting `None` here would read as "no threshold declared", which is a different thing.
    """
    summary = summarise(
        _observations(cases=(), n_errors=20, threshold=ThresholdApplied(max=0.05)), seed=SEED
    )

    assert summary.value is None
    assert summary.passed is False
    assert summary.conclusive is None


def test_errors_are_counted_beside_the_cases_that_worked() -> None:
    summary = summarise(_observations(cases=_cases([0.0] * 3), n_errors=97), seed=SEED)

    assert (summary.n_cases, summary.n_errors) == (3, 97)


def test_observations_count_repeats_and_cases_separately() -> None:
    cases = (
        CaseObservation(case_id="c-0", scores=(0.1, 0.2, 0.3)),
        CaseObservation(case_id="c-1", scores=(0.4,)),
    )

    summary = summarise(_observations(cases=cases), seed=SEED)

    assert (summary.n_cases, summary.n_observations) == (2, 4)


def test_a_single_case_gets_no_interval_and_says_why() -> None:
    summary = summarise(_observations(cases=_cases([0.2])), seed=SEED)

    assert summary.ci95 is None
    assert summary.ci_unavailable_reason is not None
    assert summary.conclusive is None


def test_without_a_threshold_there_is_no_verdict() -> None:
    summary = summarise(_observations(threshold=None), seed=SEED)

    assert (summary.passed, summary.conclusive) == (None, None)


@pytest.mark.parametrize(
    ("values", "threshold", "passed", "conclusive"),
    [
        pytest.param(
            [0.01] * 20, ThresholdApplied(max=0.05), True, True, id="clearly-under-a-maximum"
        ),
        pytest.param(
            [0.20] * 20, ThresholdApplied(max=0.05), False, True, id="clearly-over-a-maximum"
        ),
        pytest.param(
            [0.0, 0.2] * 10, ThresholdApplied(max=0.05), False, True, id="mean-over-a-maximum"
        ),
        pytest.param(
            [0.0, 0.1] * 10, ThresholdApplied(max=0.05), True, False, id="straddling-a-maximum"
        ),
        pytest.param(
            [0.99] * 20, ThresholdApplied(min=0.9), True, True, id="clearly-over-a-minimum"
        ),
        pytest.param(
            [0.5] * 20, ThresholdApplied(min=0.9), False, True, id="clearly-under-a-minimum"
        ),
        pytest.param(
            [0.5] * 20, ThresholdApplied(min=0.2, max=0.8), True, True, id="inside-a-band"
        ),
    ],
)
def test_the_verdict_and_whether_a_rerun_would_agree(
    values: list[float], threshold: ThresholdApplied, passed: bool, conclusive: bool
) -> None:
    summary = summarise(_observations(cases=_cases(values), threshold=threshold), seed=SEED)

    assert (summary.passed, summary.conclusive) == (passed, conclusive)


def test_a_straddling_interval_is_what_makes_a_pass_inconclusive() -> None:
    """The case worth telling a reader about: it passed, and a rerun might not."""
    summary = summarise(
        _observations(cases=_cases([0.0, 0.1] * 10), threshold=ThresholdApplied(max=0.05)),
        seed=SEED,
    )
    low, high = summary.ci95 or (0.0, 0.0)

    assert low < 0.05 < high
    assert summary.passed is True
    assert summary.conclusive is False


def test_a_breakdown_is_one_group_field_at_a_time() -> None:
    cases = _cases([0.0] * 5, {"variable": "nom"}) + _cases([0.4] * 5, {"variable": "age"})

    summary, by_group = aggregate([_observations(cases=cases, group_by=("variable",))], seed=SEED)

    assert set(by_group["score_gap"]) == {"variable"}
    buckets = by_group["score_gap"]["variable"]
    assert set(buckets) == {"age", "nom"}
    assert buckets["nom"].value == pytest.approx(0.0)
    assert buckets["age"].value == pytest.approx(0.4)
    assert summary["score_gap"].value == pytest.approx(0.2)


def test_a_case_missing_a_group_field_is_left_out_rather_than_bucketed() -> None:
    """A breakdown must never claim more cases than it saw."""
    cases = _cases([0.1] * 4, {"variable": "nom"}) + _cases([0.9] * 2)

    _, by_group = aggregate([_observations(cases=cases, group_by=("variable",))], seed=SEED)

    assert by_group["score_gap"]["variable"]["nom"].n_cases == 4
    assert set(by_group["score_gap"]["variable"]) == {"nom"}


def test_a_group_field_no_case_carries_produces_no_breakdown() -> None:
    _, by_group = aggregate([_observations(group_by=("absent",))], seed=SEED)

    assert by_group == {}


def test_a_small_bucket_gets_no_interval() -> None:
    cases = _cases([0.1], {"variable": "nom"}) + _cases([0.2] * 4, {"variable": "age"})

    _, by_group = aggregate([_observations(cases=cases, group_by=("variable",))], seed=SEED)

    assert by_group["score_gap"]["variable"]["nom"].ci95 is None
    assert by_group["score_gap"]["variable"]["age"].ci95 is not None


def test_the_same_metric_declared_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="declared twice"):
        aggregate([_observations(), _observations()], seed=SEED)


def test_aggregation_is_reproducible_from_the_seed() -> None:
    observations = [_observations(cases=_cases([0.0, 0.3] * 15))]

    assert aggregate(observations, seed=SEED) == aggregate(observations, seed=SEED)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
def test_a_score_that_is_not_a_finite_number_is_refused(score: float) -> None:
    """A scorer is third-party code, and a NaN reaches a run file as a token no parser accepts."""
    with pytest.raises(ValueError, match="finite number"):
        CaseObservation(case_id="c-0", scores=(score,))


def test_a_judge_scored_metric_needs_more_than_one_repeat() -> None:
    """With one repeat the judge's variance is invisible and the interval would understate it."""
    with pytest.raises(ValueError, match=r"at least\s+two repeats"):
        MetricObservations(
            metric_id="helpfulness",
            deterministic=False,
            cases=_cases([0.8, 0.9]),
        )


def test_a_judge_scored_metric_with_repeats_is_accepted() -> None:
    cases = tuple(CaseObservation(case_id=f"c-{index}", scores=(0.8, 0.9)) for index in range(10))

    summary = summarise(
        MetricObservations(metric_id="helpfulness", deterministic=False, cases=cases), seed=SEED
    )

    assert summary.deterministic is False
    assert summary.n_observations == 20
    assert summary.ci95 is not None


@pytest.mark.parametrize(
    ("values", "passed", "conclusive"),
    [
        pytest.param([0.0, 1.0] * 10, True, False, id="interval-straddles-both-bounds"),
        pytest.param([0.95] * 20, False, True, id="interval-wholly-above-the-maximum"),
        pytest.param([0.05] * 20, False, True, id="interval-wholly-below-the-minimum"),
    ],
)
def test_a_two_sided_band_is_judged_at_both_ends(
    values: list[float], passed: bool, conclusive: bool
) -> None:
    summary = summarise(
        _observations(cases=_cases(values), threshold=ThresholdApplied(min=0.4, max=0.6)),
        seed=SEED,
    )

    assert (summary.passed, summary.conclusive) == (passed, conclusive)
