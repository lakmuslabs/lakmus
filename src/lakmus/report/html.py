"""The report: one HTML file, no network, no JavaScript.

Those three constraints are not minimalism. A file with no script raises no content-security
question, needs nothing fetched to render, and looks the same in a browser, in a mail client and in
ten years — which matters because this is the artefact people attach to an email. Following the
reader's light or dark theme is therefore a media query and nothing else.

What the report deliberately does not carry: a prompt, a full answer, a key, or anything from the
environment. A reader who needs the answers has `cases.jsonl.gz`; a reader who forwards the report
should not be forwarding the case content with it.

It is built from a `RunResult` alone, so a report can be produced from a run file that has been
sitting on disk for a year. The failures are passed in because they live in the cases file, and
they are optional for the same reason.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from jinja2 import Environment, StrictUndefined, Template

from lakmus.results.models import MetricSummary, RunResult

MAX_FAILURES = 50
"""How many failing cases the table shows. The counts above it are over every failure, so a capped
table never understates how much went wrong."""

NO_VALUE = "—"
TEMPLATE_NAME = "report.html.j2"


@dataclass(frozen=True, slots=True)
class CaseFailure:
    """One case that produced no score for one metric, and why.

    `kind` is the target error's kind when the call itself failed, and `None` when the call
    succeeded and the scorer declined the answer. The distinction is the first thing someone
    debugging a run wants.
    """

    case_id: str
    metric_id: str
    reason: str
    """Why there is no score, as the scorer put it. **The caller redacts and shortens it**: a
    scorer may quote the answer it could not read, a report is the artefact people forward, and
    this package cannot import `targets.redact` without reaching across the layer it sits in.
    `lakmus.cli.runner` does it for every run this tool produces."""
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class _MetricRow:
    metric_id: str
    value: str
    interval: str
    threshold: str
    verdict: str
    note: str
    cases: int
    observations: int
    errors: int
    scoring: str


@dataclass(frozen=True, slots=True)
class _Failures:
    total: int
    shown: tuple[CaseFailure, ...]
    hidden: int
    by_kind: tuple[tuple[str, int], ...]


def render(run: RunResult, *, failures: Sequence[CaseFailure] = ()) -> str:
    """The whole report as a string. The caller decides where it goes."""
    verdict = _verdict(run)
    return _template().render(
        run=run,
        verdict=verdict.label,
        tone=verdict.tone,
        explanation=verdict.explanation,
        metrics=[_row(metric_id, summary) for metric_id, summary in sorted(run.summary.items())],
        failures=_failures(failures),
        overrides=sorted(run.config.overrides.items()),
    )


@dataclass(frozen=True, slots=True)
class _Verdict:
    label: str
    tone: str
    """The banner's style. A separate field because a label with a space in it would not be a
    usable class name."""
    explanation: str


def _verdict(run: RunResult) -> _Verdict:
    """The banner. Neither a pass nor a fail for a run that measured nothing, or not all of it.

    An interrupted run covers a subset nobody chose, and a run where every case failed measured
    nothing at all. Calling either one passed would be the most misleading thing this file could
    say — and a suite that declares no threshold has nothing to fail, so without this the banner
    read PASS over five hundred errors.
    """
    if run.status != "complete":
        return _Verdict(
            "PARTIAL",
            "partial",
            "This run was interrupted before it finished. It is readable, it is not comparable, "
            "and the figures below cover only the cases that ran.",
        )
    failed = sorted(
        metric_id for metric_id, summary in run.summary.items() if summary.passed is False
    )
    if failed:
        return _Verdict("FAIL", "fail", "Thresholds crossed: " + ", ".join(failed) + ".")
    if not any(summary.value is not None for summary in run.summary.values()):
        return _Verdict(
            "NO RESULT",
            "empty",
            "No metric produced a value: every case failed, or no scorer could read an answer. "
            "There is nothing here to quote.",
        )
    gated = sum(1 for summary in run.summary.values() if summary.threshold is not None)
    if not gated:
        return _Verdict(
            "PASS",
            "pass",
            "This suite declares no threshold, so there was nothing to cross.",
        )
    return _Verdict(
        "PASS",
        "pass",
        f"Every declared threshold held ({gated} of {len(run.summary)} metrics gated).",
    )


def _row(metric_id: str, summary: MetricSummary) -> _MetricRow:
    return _MetricRow(
        metric_id=metric_id,
        value=_number(summary.value),
        interval=_interval(summary),
        threshold=_threshold(summary),
        verdict=NO_VALUE if summary.passed is None else "PASS" if summary.passed else "FAIL",
        note="inconclusive" if summary.conclusive is False else "",
        cases=summary.n_cases,
        observations=summary.n_observations,
        errors=summary.n_errors,
        scoring="deterministic" if summary.deterministic else "judged",
    )


def _failures(failures: Sequence[CaseFailure]) -> _Failures:
    counted = Counter(failure.kind or "unscoreable" for failure in failures)
    return _Failures(
        total=len(failures),
        # The first fifty as the caller ordered them, which for a run is by case id — so `c-100`
        # comes before `c-2`. The counts above the table are over every failure, so the cap
        # understates nothing; which fifty are shown is arbitrary and says so.
        shown=tuple(failures[:MAX_FAILURES]),
        hidden=max(0, len(failures) - MAX_FAILURES),
        # Most frequent first: the kind that dominates is the one worth acting on.
        by_kind=tuple(sorted(counted.items(), key=lambda item: (-item[1], item[0]))),
    )


def _number(value: float | None) -> str:
    """Four significant figures. A score is a measurement, not an accountancy figure, and more
    digits than the bootstrap can support would be false precision."""
    return NO_VALUE if value is None else f"{value:.4g}"


def _interval(summary: MetricSummary) -> str:
    if summary.ci95 is None:
        return summary.ci_unavailable_reason or NO_VALUE
    low, high = summary.ci95
    return f"[{_number(low)}, {_number(high)}]"


def _threshold(summary: MetricSummary) -> str:
    applied = summary.threshold
    if applied is None:
        return NO_VALUE
    bounds = []
    if applied.min is not None:
        bounds.append(f"≥ {_number(applied.min)}")
    if applied.max is not None:
        bounds.append(f"≤ {_number(applied.max)}")
    return " and ".join(bounds)


@cache
def _template() -> Template:
    """Compiled once. Autoescaping is on, and the environment has no loader.

    A separate environment from the engine's on purpose: that one is sandboxed because it renders a
    stranger's template over a suite's data, while this one renders ours over a run we produced.
    """
    source = (files("lakmus.report") / "templates" / TEMPLATE_NAME).read_text(encoding="utf-8")
    environment = Environment(
        autoescape=True,
        undefined=StrictUndefined,
        loader=None,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    return environment.from_string(source)
