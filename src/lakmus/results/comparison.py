"""When two runs measured the same thing.

Comparability is a function rather than a judgement, because a leaderboard that decides it by
eye will eventually put two different measurements side by side. The target is deliberately
absent: comparing targets is the point.
"""

from lakmus.results.models import RunResult


def comparable(left: RunResult, right: RunResult) -> bool:
    """Whether two results may be placed side by side.

    Equal format version, equal `inputs_sha256` — which ADR 0005 defined as covering exactly what
    can change a measurement — and the same set of configuration overrides, since an override
    changes the measurement without touching the digest.
    """
    if left.status != "complete" or right.status != "complete":
        # A partial run measured a subset nobody chose. It can be read; it cannot be compared.
        return False
    return _key(left) == _key(right)


def _key(result: RunResult) -> tuple[str, str, tuple[tuple[str, object], ...]]:
    """The three things that have to match. Compared together rather than one `and` at a time,
    so that the key is one expression a reader can check against the decision record.

    The overrides are compared by name *and* value: two runs of one suite at different
    temperatures measured different things.
    """
    return (
        result.lakmus_format,
        result.suite.inputs_sha256,
        tuple(sorted(result.config.overrides.items())),
    )
