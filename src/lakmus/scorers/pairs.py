"""Scorers for counterfactual pairs.

A pair is two inputs identical except for one controlled variable. No ground truth is needed: a
fair, robust system gives the same answer to both, so the measurement is how much the answer
moved. Both scorers are lower-is-better and both return 0 for an identical pair, which is the
neutral value a reader should see when nothing changed.
"""

import math
from typing import Any

from lakmus.scorers.base import Case, PairOutputs, Score, describable, object_of, scorer


@scorer(name="pair_gap", arity="pair", higher_is_better=False)
def pair_gap(case: Case, outputs: PairOutputs, *, field: str = "score") -> Score:
    """The absolute difference between the two numeric answers."""
    first = _number(outputs.a, field)
    second = _number(outputs.b, field)
    if first is None or second is None:
        return Score.unscoreable(
            f"one answer has no finite numeric field {field!r}",
            a=describable(_raw(outputs.a, field)),
            b=describable(_raw(outputs.b, field)),
        )

    gap = abs(first - second)
    if not math.isfinite(gap):
        # Two finite numbers at opposite ends of the range still overflow.
        return Score.unscoreable(f"the gap between {first} and {second} is not finite")

    return Score.of(
        gap,
        field=field,
        a=first,
        b=second,
        direction="a_higher" if first > second else "b_higher" if second > first else "equal",
    )


@scorer(name="pair_flip", arity="pair", higher_is_better=False)
def pair_flip(case: Case, outputs: PairOutputs, *, field: str = "decision") -> Score:
    """1 when the decision changed between the two answers, 0 when it held."""
    first = _raw(outputs.a, field)
    second = _raw(outputs.b, field)
    if first is None or second is None:
        return Score.unscoreable(
            f"one answer has no field {field!r}",
            a=describable(first),
            b=describable(second),
        )

    flipped = first != second
    return Score.of(
        1.0 if flipped else 0.0, field=field, a=describable(first), b=describable(second)
    )


def _raw(output: Any, field: str) -> Any:
    answer = object_of(output)
    return None if answer is None else answer.get(field)


def _number(output: Any, field: str) -> float | None:
    """A finite number, or nothing.

    `json.loads` turns `1e400` into an infinity and accepts `NaN`, and a gap computed from either
    is a number no run file can hold. Declining is the rule for a bad answer; raising here would
    end a run on one case.
    """
    value = _raw(output, field)
    # A boolean is an int in Python; it is not a score.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
