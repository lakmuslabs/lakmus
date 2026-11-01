"""`lakmus compare`.

Comparability is decided by `results.comparable`, not by this command, and a refusal names which
of the three keys differs rather than leaving someone to diff two JSON files.

What the table reports about a difference is whether the two intervals overlap — not whether the
difference is significant. An overlap of two bootstrap intervals is a conservative statement about
a measurement; a significance claim would be a test this project never defined.
"""

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from lakmus.cli import runner
from lakmus.errors import ExitCode
from lakmus.results import comparable
from lakmus.results.models import MetricSummary, RunResult


def compare(
    runs: Annotated[
        list[Path],
        typer.Argument(exists=True, dir_okay=False, metavar="RUN...", help="Two or more run.json."),
    ],
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the comparison as JSON instead of a table.")
    ] = False,
) -> None:
    """Put two or more comparable runs side by side."""
    if len(runs) < 2:
        _refuse("compare needs at least two runs.")

    results = [runner.read_run_or_exit(path) for path in runs]
    reference = results[0]
    for path, result in zip(runs[1:], results[1:], strict=True):
        if comparable(reference, result):
            continue
        _refuse(
            f"{runs[0]} and {path} did not measure the same thing: {_difference(reference, result)}"
        )

    metrics = sorted(reference.summary)
    for path, result in zip(runs[1:], results[1:], strict=True):
        if sorted(result.summary) != metrics:
            # Two comparable runs share an inputs digest, and ADR 0005 puts the metric list inside
            # it. Different metrics behind the same digest means a run file was edited after it
            # was written, which is not a comparison to draw.
            _refuse(
                f"{runs[0]} and {path} share a suite digest but not their metrics "
                f"({', '.join(metrics)} against {', '.join(sorted(result.summary))}). "
                f"One of these files has been edited."
            )

    typer.echo(_json(runs, results, metrics) if as_json else _table(runs, results, metrics))


def _difference(left: RunResult, right: RunResult) -> str:
    """Which of comparability's three keys differs. Status first, because it is not a key at all
    but the condition under which the keys mean anything.

    The format version is not checked here: `run.json` declares `0.1` and nothing else parses, so
    a file from another format is already refused when it is read.
    """
    if left.status != "complete" or right.status != "complete":
        partial = ", ".join(
            name
            for name, result in (("the first", left), ("the second", right))
            if result.status != "complete"
        )
        return f"{partial} run was interrupted, so it covers a subset nobody chose."
    if left.suite.inputs_sha256 != right.suite.inputs_sha256:
        return (
            f"the suite differs — inputs digest {left.suite.inputs_sha256[:12]}… against "
            f"{right.suite.inputs_sha256[:12]}… "
            f"({left.suite.id} {left.suite.version} against {right.suite.id} "
            f"{right.suite.version})."
        )
    named = sorted(set(left.config.overrides) | set(right.config.overrides))
    differing = [
        f"{name}: {left.config.overrides.get(name, 'from the manifest')!r} against "
        f"{right.config.overrides.get(name, 'from the manifest')!r}"
        for name in named
        if left.config.overrides.get(name) != right.config.overrides.get(name)
    ]
    if differing:
        return "the configuration was overridden differently — " + "; ".join(differing) + "."
    # Unreachable while this function and `comparable()` agree: comparability is equality of the
    # status gate, the inputs digest and the overrides, and each has a branch above. Kept, and
    # kept loud, because the day the two disagree is the day someone needs to be told.
    return "they differ in a way this version cannot name, which is a bug in lakmus."


def _table(paths: list[Path], results: list[RunResult], metrics: list[str]) -> str:
    lines = [
        f"run {index + 1}: {result.run_id}  {result.target.adapter}:{result.target.identity}  "
        f"{path}"
        for index, (path, result) in enumerate(zip(paths, results, strict=True))
    ]
    lines.append("")

    rows = [("METRIC", "RUN", "VALUE", "95% CI", "DELTA", "INTERVALS")]
    for metric_id in metrics:
        first = results[0].summary[metric_id]
        for index, result in enumerate(results):
            summary = result.summary[metric_id]
            rows.append(
                (
                    metric_id if index == 0 else "",
                    f"{index + 1}",
                    _number(summary.value),
                    _interval(summary.ci95),
                    "-" if index == 0 else _delta(first.value, summary.value),
                    "-" if index == 0 else _overlap(first, summary),
                )
            )
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines += [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]
    return "\n".join(lines)


def _json(paths: list[Path], results: list[RunResult], metrics: list[str]) -> str:
    document: dict[str, Any] = {
        "runs": [
            {
                "file": str(path),
                "run_id": result.run_id,
                "target": f"{result.target.adapter}:{result.target.identity}",
                "fingerprint": result.target.sha256,
            }
            for path, result in zip(paths, results, strict=True)
        ],
        "metrics": {
            metric_id: [
                {
                    "run": index,
                    "value": result.summary[metric_id].value,
                    "ci95": list(result.summary[metric_id].ci95 or ()) or None,
                    "intervals_overlap": (
                        None
                        if index == 0
                        else _overlapping(
                            results[0].summary[metric_id].ci95, result.summary[metric_id].ci95
                        )
                    ),
                }
                for index, result in enumerate(results)
            ]
            for metric_id in metrics
        },
    }
    return json.dumps(document, indent=2, ensure_ascii=False)


def _refuse(message: str) -> None:
    typer.secho(message, err=True, fg=typer.colors.RED)
    raise typer.Exit(ExitCode.USAGE)


def _delta(first: float | None, other: float | None) -> str:
    if first is None or other is None:
        return "-"
    return f"{other - first:+.4g}"


def _overlap(first: MetricSummary, other: MetricSummary) -> str:
    overlapping = _overlapping(first.ci95, other.ci95)
    if overlapping is None:
        return "-"
    return "overlap" if overlapping else "disjoint"


def _overlapping(
    first: tuple[float, float] | None, other: tuple[float, float] | None
) -> bool | None:
    if first is None or other is None:
        return None
    return not (first[1] < other[0] or other[1] < first[0])


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:.4g}"


def _interval(interval: tuple[float, float] | None) -> str:
    if interval is None:
        return "-"
    return f"[{_number(interval[0])}, {_number(interval[1])}]"
