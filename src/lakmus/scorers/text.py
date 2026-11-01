"""Scorers for an answer that is text."""

import re
from collections.abc import Sequence
from typing import Literal

from lakmus.errors import ScorerError
from lakmus.scorers.base import Case, Score, normalise_text, scorer, text_of
from lakmus.targets.base import TargetOutput

DEFAULT_NORMALISE = ("trim", "nfc")
"""Trim and compose. Not `lower`: whether case matters is the suite's call, not ours."""

_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}


@scorer(name="exact", higher_is_better=True)
def exact(
    case: Case,
    output: TargetOutput,
    *,
    expected: str = "expected",
    normalise: Sequence[str] = DEFAULT_NORMALISE,
) -> Score:
    """1 when the answer equals the expected text, 0 when it does not."""
    answer = text_of(output)
    if answer is None:
        return Score.unscoreable("the answer is not text")
    if expected not in case.data:
        return Score.unscoreable(f"the case has no field {expected!r}")

    wanted = case[expected]
    if not isinstance(wanted, str):
        return Score.unscoreable(f"the case field {expected!r} is not text")

    got = normalise_text(answer, normalise)
    want = normalise_text(wanted, normalise)
    return Score.of(1.0 if got == want else 0.0, expected=want, got=got)


@scorer(name="regex", higher_is_better=True)
def regex(
    case: Case,
    output: TargetOutput,
    *,
    pattern: str,
    flags: str = "",
    expect: Literal["match", "no_match"] = "match",
) -> Score:
    """1 when the answer matches the pattern, or does not, as the suite asked."""
    answer = text_of(output)
    if answer is None:
        return Score.unscoreable("the answer is not text")

    compiled = _compile(pattern, flags)
    found = compiled.search(answer)
    matched = found is not None
    wanted = expect == "match"
    return Score.of(
        1.0 if matched is wanted else 0.0,
        pattern=pattern,
        expect=expect,
        matched=found.group(0) if found else None,
    )


def _compile(pattern: str, flags: str) -> re.Pattern[str]:
    """Compile once per call site, and fail loudly on a pattern the suite got wrong.

    A bad pattern is the same mistake for every case, so it ends the run rather than scoring
    thousands of cases as unscoreable.
    """
    unknown = sorted(set(flags) - set(_FLAGS))
    if unknown:
        raise ScorerError(
            f"regex: unknown flag(s) {''.join(unknown)!r}; known flags: {''.join(_FLAGS)}"
        )
    combined = re.NOFLAG
    for flag in flags:
        combined |= _FLAGS[flag]
    try:
        return re.compile(pattern, combined)
    except re.error as exc:
        raise ScorerError(f"regex: {pattern!r} is not a valid pattern: {exc}") from exc
