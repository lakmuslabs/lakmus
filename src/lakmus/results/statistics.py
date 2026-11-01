"""Point estimates and confidence intervals, in the standard library.

Everything here is specified precisely rather than merely implemented, because `run.json` is read
by tools we do not control: a hub that wants to check an interval has to be able to recompute it,
in another language, and get the same digits. That rules out `random`, whose Mersenne Twister no
other runtime reproduces, so the resampling uses a small named generator defined here.

The case is the unit of independence. Repeats of one case are not independent of each other, so a
resample draws cases and averages each drawn case's repeats — a cluster bootstrap. For a
deterministic scorer with one repeat this reduces to the ordinary bootstrap over cases.
"""

from collections.abc import Sequence

RESAMPLES = 1000
LEVEL = 95.0
MINIMUM_CASES = 2
"""Below this, an interval would be invented rather than estimated."""

_MASK64 = (1 << 64) - 1
_GOLDEN_GAMMA = 0x9E3779B97F4A7C15


def _splitmix64(state: int) -> tuple[int, int]:
    """One step of splitmix64: the next state, and a 64-bit draw.

    Chosen because it is three multiplications and three shifts, so another implementation can
    reproduce it from this function alone.
    """
    state = (state + _GOLDEN_GAMMA) & _MASK64
    z = state
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    return state, z ^ (z >> 31)


def _index_below(state: int, bound: int) -> tuple[int, int]:
    """An unbiased index in [0, bound), by rejecting the partial block at the top of the range."""
    limit = (1 << 64) // bound * bound
    while True:
        state, draw = _splitmix64(state)
        if draw < limit:
            return state, draw % bound


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of no values")
    return sum(values) / len(values)


def case_means(cases: Sequence[Sequence[float]]) -> list[float]:
    """One value per case: the mean of that case's repeats."""
    return [mean(repeats) for repeats in cases]


def point_estimate(cases: Sequence[Sequence[float]]) -> float:
    """The mean over cases of each case's mean.

    Not the mean over every observation: a case with five repeats would otherwise weigh five
    times a case with one.
    """
    return mean(case_means(cases))


def percentile(sorted_values: Sequence[float], p: float) -> float:
    """Linear interpolation at rank `(m - 1) * p / 100` of an ascending list.

    One of several defensible definitions, which is exactly why it is written down: an interval is
    only reproducible if everyone interpolates the same way.
    """
    if not sorted_values:
        raise ValueError("percentile of no values")
    if not 0.0 <= p <= 100.0:
        raise ValueError(f"percentile out of range: {p}")
    rank = (len(sorted_values) - 1) * p / 100.0
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def bootstrap_ci(
    cases: Sequence[Sequence[float]],
    *,
    seed: int,
    resamples: int = RESAMPLES,
    level: float = LEVEL,
) -> tuple[float, float] | None:
    """A percentile bootstrap interval over cases, or `None` when there are too few.

    `seed` comes from the run configuration. The generator is seeded once and consumed in order,
    so the interval depends on the seed, the case means and the resample count, and on nothing
    else: no global random source, no salted hash, no iteration order. Integer arithmetic and
    float addition are all it uses, so another implementation of this function reproduces it —
    which is checked by a pinned vector rather than asserted here.
    """
    means = case_means(cases)
    if len(means) < MINIMUM_CASES:
        return None

    count = len(means)
    state = seed & _MASK64
    resampled: list[float] = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(count):
            state, index = _index_below(state, count)
            total += means[index]
        resampled.append(total / count)

    resampled.sort()
    tail = (100.0 - level) / 2.0
    return percentile(resampled, tail), percentile(resampled, 100.0 - tail)
