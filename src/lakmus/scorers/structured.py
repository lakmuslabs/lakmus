"""Scorers for an answer that is a JSON object."""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from lakmus.errors import ScorerError
from lakmus.scorers.base import Case, Score, describable, normalise_text, object_of, scorer
from lakmus.targets.base import TargetOutput

DEFAULT_NORMALISE = ("trim", "nfc")

_TYPE_CHECKS: Mapping[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


@scorer(name="json_schema", higher_is_better=True)
def json_schema(
    case: Case,
    output: TargetOutput,
    *,
    schema: Mapping[str, str],
) -> Score:
    """The share of declared fields the answer provides with the declared type.

    `schema` is the flat shorthand the manifest uses in `target.output.schema`, repeated here
    because a scorer receives a case and an answer, not the manifest. ADR 0005 deferred compiling
    that shorthand; until it does, the repetition is the honest cost.
    """
    answer = object_of(output)
    if answer is None:
        return Score.unscoreable("the answer is not a JSON object")
    if not schema:
        raise ScorerError("json_schema: the schema option declares no field")

    missing: list[str] = []
    wrong: dict[str, str] = {}
    for field, declared in schema.items():
        expected_type = _TYPE_CHECKS.get(declared)
        if expected_type is None:
            # The same mistake for every case, so it ends the run rather than being recorded
            # thousands of times with the one useful fact buried in them.
            raise ScorerError(
                f"json_schema: unknown declared type {declared!r} for field {field!r}; "
                f"known types: {', '.join(_TYPE_CHECKS)}"
            )
        if field not in answer:
            missing.append(field)
        elif not _is_a(answer[field], declared, expected_type):
            wrong[field] = type(answer[field]).__name__

    satisfied = len(schema) - len(missing) - len(wrong)
    return Score.of(
        satisfied / len(schema),
        missing=sorted(missing),
        wrong_type=wrong,
        declared=sorted(schema),
    )


def _is_a(value: Any, declared: str, expected_type: type | tuple[type, ...]) -> bool:
    # A boolean is an int in Python, which it is not in a schema: True is not the number 1.
    if declared in {"number", "integer"} and isinstance(value, bool):
        return False
    return isinstance(value, expected_type)


@scorer(name="json_field_match", higher_is_better=True)
def json_field_match(
    case: Case,
    output: TargetOutput,
    *,
    field: str,
    expected: str = "expected",
    normalise: Sequence[str] = DEFAULT_NORMALISE,
) -> Score:
    """1 when one field of the answer equals the expected value."""
    answer = object_of(output)
    if answer is None:
        return Score.unscoreable("the answer is not a JSON object")
    if field not in answer:
        return Score.unscoreable(f"the answer has no field {field!r}")
    if expected not in case.data:
        return Score.unscoreable(f"the case has no field {expected!r}")

    got = _as_comparable(answer[field], normalise)
    want = _as_comparable(case[expected], normalise)
    return Score.of(
        1.0 if got == want else 0.0,
        field=field,
        expected=describable(want),
        got=describable(got),
    )


def _as_comparable(value: Any, normalise: Sequence[str]) -> Any:
    """Compare text as text and everything else as itself.

    Normalising a number into a string would make `0.80` and `0.8` differ, which is not what a
    suite means by an exact field match.
    """
    return normalise_text(value, normalise) if isinstance(value, str) else value


@scorer(name="precision_recall_f1", higher_is_better=True)
def precision_recall_f1(
    case: Case,
    output: TargetOutput,
    *,
    expected: str = "expected",
    predicted: str = "predicted",
    report: Literal["f1", "precision", "recall"] = "f1",
) -> Score:
    """One of precision, recall or F1 over two lists of items.

    A metric is one number with one threshold, so this reports one of the three and a suite that
    wants all three declares three metrics. All three are in `details` either way.
    """
    answer = object_of(output)
    if answer is None:
        return Score.unscoreable("the answer is not a JSON object")

    wanted = case.get(expected)
    got = answer.get(predicted)
    if not isinstance(wanted, list):
        return Score.unscoreable(f"the case field {expected!r} is not a list")
    if not isinstance(got, list):
        return Score.unscoreable(f"the answer field {predicted!r} is not a list")

    wanted_set = {_hashable(item) for item in wanted}
    got_set = {_hashable(item) for item in got}
    if not wanted_set and not got_set:
        # Nothing to find and nothing wrongly found is a correct answer. Scoring it zero gives the
        # worst value to a target that got the case exactly right.
        return Score.of(
            1.0,
            reported=report,
            precision=1.0,
            recall=1.0,
            f1=1.0,
            true_positives=0,
            missed=[],
            spurious=[],
        )

    true_positives = len(wanted_set & got_set)
    precision = true_positives / len(got_set) if got_set else 0.0
    recall = true_positives / len(wanted_set) if wanted_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    scores = {"precision": precision, "recall": recall, "f1": f1}
    return Score.of(
        scores[report],
        reported=report,
        precision=precision,
        recall=recall,
        f1=f1,
        true_positives=true_positives,
        missed=sorted(str(item) for item in wanted_set - got_set),
        spurious=sorted(str(item) for item in got_set - wanted_set),
    )


def _hashable(item: Any, depth: int = 0) -> Any:
    """Items are compared as sets, and a suite may list objects.

    Duplicates vanish: the sets make `["a", "a", "b"]` and `["a", "b"]` the same set of items.
    That is what precision and recall over extracted items mean, and the format specification
    says so.
    """
    if depth > 32:
        raise ScorerError("precision_recall_f1: an item is nested too deeply to compare")
    if isinstance(item, dict):
        return tuple(sorted((key, _hashable(value, depth + 1)) for key, value in item.items()))
    if isinstance(item, list):
        return tuple(_hashable(value, depth + 1) for value in item)
    return item
