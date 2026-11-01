"""Point estimates and intervals.

`run.json` is read by tools this project does not control, so the interval has to be reproducible
from the recorded seed alone — on another machine, another Python, another language. These tests
pin the pieces that makes true: the percentile rule and the resampling stream.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from lakmus.results import statistics


@pytest.mark.parametrize(
    ("values", "p", "expected"),
    [
        pytest.param([1.0, 2.0, 3.0, 4.0], 0.0, 1.0, id="lowest"),
        pytest.param([1.0, 2.0, 3.0, 4.0], 100.0, 4.0, id="highest"),
        pytest.param([1.0, 2.0, 3.0, 4.0], 50.0, 2.5, id="between-two-values"),
        pytest.param([1.0, 2.0, 3.0, 4.0], 25.0, 1.75, id="interpolated"),
        pytest.param([7.0], 50.0, 7.0, id="single-value"),
        pytest.param([1.0, 2.0, 3.0], 50.0, 2.0, id="exactly-on-a-value"),
    ],
)
def test_percentile_interpolates_at_the_documented_rank(
    values: list[float], p: float, expected: float
) -> None:
    assert statistics.percentile(values, p) == pytest.approx(expected)


@pytest.mark.parametrize("p", [-0.1, 100.1])
def test_percentile_refuses_a_level_outside_the_range(p: float) -> None:
    with pytest.raises(ValueError):
        statistics.percentile([1.0], p)


def test_the_point_estimate_weighs_every_case_equally() -> None:
    """A case with five repeats must not count five times a case with one."""
    cases = [[0.0, 0.0, 0.0, 0.0, 0.0], [1.0]]

    assert statistics.point_estimate(cases) == pytest.approx(0.5)


def test_an_interval_needs_enough_cases_to_mean_anything() -> None:
    """Two cases that agree would otherwise report certainty from two observations."""
    assert statistics.bootstrap_ci([], seed=0) is None
    assert statistics.bootstrap_ci([[0.5]], seed=0) is None
    assert statistics.bootstrap_ci([[0.5]] * 2, seed=0) is None


@pytest.mark.parametrize(
    ("count", "has_interval"),
    [
        pytest.param(statistics.MINIMUM_CASES - 1, False, id="one-case-short"),
        pytest.param(statistics.MINIMUM_CASES, True, id="exactly-enough"),
    ],
)
def test_the_floor_is_where_it_says_it_is(count: int, has_interval: bool) -> None:
    cases = [[float(index % 2)] for index in range(count)]

    assert (statistics.bootstrap_ci(cases, seed=1) is not None) is has_interval


def test_identical_scores_give_an_interval_of_zero_width() -> None:
    """Legitimate once there are enough of them: every resample really does give 0.25."""
    cases = [[0.25]] * 50

    interval = statistics.bootstrap_ci(cases, seed=7)

    assert interval == (0.25, 0.25)


def test_the_interval_brackets_the_point_estimate() -> None:
    cases = [[float(index % 2)] for index in range(40)]

    low, high = statistics.bootstrap_ci(cases, seed=11) or (0.0, 0.0)

    assert low <= statistics.point_estimate(cases) <= high


def test_the_same_seed_gives_the_same_interval() -> None:
    cases = [[float(index) / 10] for index in range(30)]

    assert statistics.bootstrap_ci(cases, seed=3) == statistics.bootstrap_ci(cases, seed=3)


def test_a_different_seed_gives_a_different_interval() -> None:
    cases = [[float(index) / 10] for index in range(30)]

    assert statistics.bootstrap_ci(cases, seed=3) != statistics.bootstrap_ci(cases, seed=4)


def test_more_cases_narrow_the_interval() -> None:
    few = [[float(index % 2)] for index in range(statistics.MINIMUM_CASES)]
    many = [[float(index % 2)] for index in range(400)]

    narrow = statistics.bootstrap_ci(many, seed=5) or (0.0, 1.0)
    wide = statistics.bootstrap_ci(few, seed=5) or (0.0, 1.0)

    assert narrow[1] - narrow[0] < wide[1] - wide[0]


GOLDEN_SEED = 42
GOLDEN_CASES = [[float(index % 3) / 2] for index in range(25)]
GOLDEN_INTERVAL = (0.32, 0.64)
"""Pinned on purpose: an interval recorded in a run file has to be recomputable by anyone, so this
may change only together with the resampling algorithm."""


def test_the_interval_matches_its_published_vector() -> None:
    assert statistics.bootstrap_ci(GOLDEN_CASES, seed=GOLDEN_SEED) == GOLDEN_INTERVAL


def test_the_interval_does_not_depend_on_the_process(tmp_path: Path) -> None:
    """Nothing here may reach for `random` or a salted hash.

    Two interpreters started with different hash seeds must agree to the last digit, or a run file
    cannot claim its interval is reproducible.
    """
    script = tmp_path / "interval.py"
    script.write_text(
        "from lakmus.results.statistics import bootstrap_ci\n"
        "cases = [[float(i % 3) / 2] for i in range(25)]\n"
        "print(bootstrap_ci(cases, seed=42))\n",
        encoding="utf-8",
    )

    outputs = {
        subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout
        for seed in ("1", "2", "random")
    }

    assert len(outputs) == 1
