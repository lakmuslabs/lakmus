"""A suite's own scorer, used by the fixture to exercise the scorer checks."""

from lakmus.scorers import Case, PairOutputs, Score, object_of, scorer


@scorer(name="mean_abs_gap", arity="pair", higher_is_better=False)
def mean_abs_gap(case: Case, outputs: PairOutputs, *, field: str = "score") -> Score:
    """The absolute difference between the two scores, or nothing when either is missing."""
    first = object_of(outputs.a) or {}
    second = object_of(outputs.b) or {}
    if field not in first or field not in second:
        return Score.unscoreable(f"one answer has no field {field!r}")
    return Score.of(
        abs(float(first[field]) - float(second[field])), a=first[field], b=second[field]
    )
