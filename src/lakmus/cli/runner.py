"""Turning a suite and a target into a run on disk, and into an exit code.

`run` and `replay` differ only in where their options come from, so the orchestration is here and
each command is left to parse its own arguments. Two rules in this file are the public contract a
CI script reads:

**A partial run never produces a verdict.** An interrupted or self-aborted run returns 3, even when
every threshold in the subset that ran held. Anything else would make Ctrl-C a way to pass a suite.

**The files are written before anything is printed.** A run that has been paid for is on disk even
if the terminal it was printed to has gone away.
"""

import asyncio
import signal
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from random import Random

import typer

from lakmus import __version__
from lakmus.engine import (
    Cache,
    EngineResult,
    FileCache,
    NullCache,
    Progress,
    artefacts,
    check_target,
    execute,
    plan,
)
from lakmus.engine.cache import DEFAULT_ROOT
from lakmus.engine.plan import DEFAULT_CONCURRENCY
from lakmus.engine.run import DEFAULT_EARLY_ABORT
from lakmus.errors import ExitCode, Finding, LakmusError, SuiteInvalid
from lakmus.report import CaseFailure, render
from lakmus.results.models import MetricSummary, ParameterValue, RunResult
from lakmus.results.statistics import RESAMPLES
from lakmus.scorers import BUILTIN_SCORERS
from lakmus.suite.loader import Suite, SuiteCodeConsent, load
from lakmus.targets import pricing
from lakmus.targets.base import redact
from lakmus.targets.registry import create, parse_target

OverrideValue = float | int | str | bool

REASON_LIMIT = 300
"""How much of a scorer's reason reaches the report. A scorer that quotes a whole answer back
would otherwise put that answer into the one file meant to be shared."""


@dataclass(frozen=True, slots=True)
class Options:
    """Everything a run needs that did not come from the suite."""

    suite: Path
    target: str
    api_key_env: str | None = None
    parameters: Mapping[str, ParameterValue] = field(default_factory=dict)
    overrides: Mapping[str, OverrideValue] = field(default_factory=dict)
    """Only what the invocation actually named. Passing the CLI's own defaults here would record
    an override on every run, and comparability compares overrides by name and value."""
    concurrency: int = DEFAULT_CONCURRENCY
    bootstrap_resamples: int = RESAMPLES
    early_abort: int = DEFAULT_EARLY_ABORT
    time_limit_s: float | None = None
    out: Path | None = None
    cache_dir: Path | None = None
    use_cache: bool = True
    keep_prompts: bool = False
    write_report: bool = True
    as_json: bool = False
    quiet: bool = False
    consent: SuiteCodeConsent = False
    require_inputs_sha256: str | None = None
    """Set by `replay`: the suite must still digest to what the recorded run measured."""


def perform(options: Options) -> None:
    """Run it, write it, print it, and exit with the verdict."""
    try:
        code = asyncio.run(_run_and_write(options))
    except SuiteInvalid as exc:
        _print_findings(options.suite, exc.findings)
        raise typer.Exit(ExitCode.USAGE) from None
    except LakmusError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(exc.exit_code) from None
    except KeyboardInterrupt:
        # The second signal. The first one asked the engine to stop and said so.
        typer.secho("abandoned.", err=True, fg=typer.colors.YELLOW)
        raise typer.Exit(ExitCode.RUNTIME) from None
    except Exception as exc:
        # Exit 1 means a declared threshold failed, and a CI gate branches on it. An uncaught
        # exception would reach the shell as 1 through the interpreter and report a failure that
        # never happened, so anything unexpected is a runtime error and says what it was.
        typer.secho(f"{type(exc).__name__}: {_one_line(exc)}", err=True, fg=typer.colors.RED)
        typer.secho(
            "This is a bug in lakmus. Answers already paid for are in the cache, so re-running "
            "costs only what failed.",
            err=True,
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(ExitCode.RUNTIME) from None
    raise typer.Exit(code)


def read_run_or_exit(path: Path) -> RunResult:
    """`read_run`, with the refusal turned into the exit code a command owes its caller."""
    try:
        return read_run(path)
    except LakmusError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(exc.exit_code) from None


def exit_code_for(run: RunResult) -> ExitCode:
    """The verdict as a process exit code.

    A run that did not finish measured a subset nobody chose, so it has no verdict to report
    whatever its thresholds say.
    """
    if run.status != "complete":
        return ExitCode.RUNTIME
    if any(summary.passed is False for summary in run.summary.values()):
        return ExitCode.THRESHOLD_FAILED
    return ExitCode.SUCCESS


def read_run(path: Path) -> RunResult:
    """Read a `run.json`, refusing anything that is not one."""
    try:
        return RunResult.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _BadArgument(f"{path}: {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise _BadArgument(
            f"{path} is not a lakmus run file, or is from an unsupported format version: "
            f"{_one_line(exc)}"
        ) from exc


class _BadArgument(LakmusError):
    """An argument this command cannot use. The answer is a different argument, so: exit 2."""

    exit_code = ExitCode.USAGE


async def _run_and_write(options: Options) -> ExitCode:
    suite = load(options.suite, run_suite_code=options.consent, scorers=BUILTIN_SCORERS)
    _refuse_drift(options, suite)
    if options.out is not None:
        # Taken before the first call, so that a mistyped or unwritable --out costs nothing. The
        # writer checks again at the end, which is what catches another run that took it meanwhile.
        artefacts.reserve(options.out)

    run_plan = plan(
        suite,
        overrides=dict(options.overrides),
        concurrency=options.concurrency,
        bootstrap_resamples=options.bootstrap_resamples,
    )
    reference = parse_target(options.target)
    target = create(reference.adapter)
    check_target(run_plan, target)

    await target.setup(
        run_plan.target_config(
            identity=reference.identity,
            model=reference.model,
            api_key_env=options.api_key_env,
            pricing=pricing.load(),
            parameters=dict(options.parameters),
        )
    )
    try:
        with _stop_on_signal(quiet=options.quiet) as stop:
            result = await execute(
                run_plan,
                target=target,
                clock=_now,
                # Seeded from the run's own seed, so that the run id and the jitter are both
                # reproducible from the recorded configuration.
                entropy=Random(run_plan.config.seed),  # noqa: S311 — reproducibility, not secrecy
                cache=_cache(options),
                stop=stop,
                early_abort=options.early_abort,
                time_limit_s=options.time_limit_s,
                tool_version=__version__,
                keep_prompts=options.keep_prompts,
                progress=_progress(options),
            )
            # Written while the handler is still installed. Outside it, an interrupt between the
            # last answer and the last file reached Python's default handler and discarded a run
            # that was complete and entirely paid for.
            written = _write(options, result)
    finally:
        await target.teardown()

    _print(options, result, written)
    return exit_code_for(result.run)


def _write(options: Options, result: EngineResult) -> artefacts.WrittenRun:
    directory = options.out or artefacts.default_directory(result.run.run_id)
    return artefacts.write(
        directory, result, keep_prompts=options.keep_prompts, html=_html(options, result)
    )


def _html(options: Options, result: EngineResult) -> str | None:
    """The report, or none and a warning.

    The same rule the cache follows: a presentation layer must not be able to end a run that has
    already been paid for. The aggregates are in `run.json` either way, and a report can be built
    from that file alone.
    """
    if not options.write_report:
        return None
    try:
        return render(result.run, failures=_failures(result))
    except Exception as exc:
        typer.secho(
            f"The report could not be rendered ({type(exc).__name__}: {_one_line(exc)}); "
            f"the run file and the cases file are written anyway.",
            err=True,
            fg=typer.colors.YELLOW,
        )
        return None


def _print(options: Options, result: EngineResult, written: artefacts.WrittenRun) -> None:
    if options.as_json:
        typer.echo(result.run.model_dump_json(indent=2))
    else:
        typer.echo(_table(result.run))

    typer.secho(f"{written.directory}", err=True)
    if result.aborted:
        typer.secho(
            "Stopped early: every one of the first completed cases failed, which is a "
            "misconfiguration rather than a measurement. Pass --early-abort 0 to run it anyway.",
            err=True,
            fg=typer.colors.YELLOW,
        )
    elif result.run.status != "complete":
        typer.secho(
            "Interrupted: the result covers only the cases that ran, and is not comparable. "
            "Everything answered is cached, so re-running pays only for the rest.",
            err=True,
            fg=typer.colors.YELLOW,
        )
    if options.keep_prompts:
        typer.secho(
            f"{written.cases.name} holds the rendered prompts, so it carries the case content.",
            err=True,
            fg=typer.colors.YELLOW,
        )


def _failures(result: EngineResult) -> list[CaseFailure]:
    """One entry per case and metric that produced no score at all.

    Per case rather than per repeat, and only when no repeat scored, so that the table says the
    same thing as `n_errors` in the summary. A case that failed once and succeeded once is a
    measurement that was taken, not a failure.
    """
    scored: dict[tuple[str, str], bool] = {}
    reasons: dict[tuple[str, str], str] = {}
    kinds: dict[str, str] = {}
    for record in result.cases:
        for answer in record.answers:
            if answer.output.error is not None:
                # The first kind seen for this case, across its repeats and both sides of a pair.
                # A case that timed out once and was refused once shows one of them; the cases
                # file has every answer, and this column is a hint about where to look.
                kinds.setdefault(record.case_id, answer.output.error.kind)
        for metric_id, score in record.scores.items():
            key = (record.case_id, metric_id)
            scored[key] = scored.get(key, False) or score.scored
            if not score.scored:
                # A scorer may quote the answer it could not read, and an answer is whatever the
                # model wrote — a key it was shown among it. The report is the artefact designed
                # to be forwarded, so the reason is redacted and shortened before it gets there.
                reasons.setdefault(
                    key,
                    redact(str(score.details.get("error", "no score")), limit=REASON_LIMIT),
                )
    return [
        CaseFailure(
            case_id=case_id,
            metric_id=metric_id,
            reason=reasons[(case_id, metric_id)],
            kind=kinds.get(case_id),
        )
        for (case_id, metric_id), was_scored in sorted(scored.items())
        if not was_scored
    ]


def _table(run: RunResult) -> str:
    rows = [("METRIC", "VALUE", "95% CI", "THRESHOLD", "VERDICT", "CASES", "ERRORS")]
    for metric_id, summary in sorted(run.summary.items()):
        rows.append(
            (
                metric_id,
                _number(summary.value),
                _interval(summary.ci95),
                _bounds(summary),
                _verdict_of(summary),
                str(summary.n_cases),
                str(summary.n_errors),
            )
        )
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]
    verdict = "PASS" if exit_code_for(run) == ExitCode.SUCCESS else "FAIL"
    if run.status != "complete":
        verdict = "PARTIAL"
    lines.append("")
    lines.append(f"{run.suite.id} {run.suite.version}: {verdict}")
    return "\n".join(lines)


def _verdict_of(summary: MetricSummary) -> str:
    """`PASS?` is a pass whose interval straddles the bound: it held, and a rerun might not."""
    if summary.passed is None:
        return "-"
    if not summary.passed:
        return "FAIL"
    return "PASS?" if summary.conclusive is False else "PASS"


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:.4g}"


def _interval(interval: tuple[float, float] | None) -> str:
    if interval is None:
        return "-"
    return f"[{_number(interval[0])}, {_number(interval[1])}]"


def _bounds(summary: MetricSummary) -> str:
    applied = summary.threshold
    if applied is None:
        return "-"
    parts = []
    if applied.min is not None:
        parts.append(f">= {_number(applied.min)}")
    if applied.max is not None:
        parts.append(f"<= {_number(applied.max)}")
    return " ".join(parts)


def _cache(options: Options) -> Cache:
    if not options.use_cache:
        return NullCache()
    return FileCache(root=options.cache_dir or DEFAULT_ROOT, tool_version=__version__)


def _progress(options: Options) -> Progress | None:
    """A counter, and only where someone is watching it.

    A CI log does not want one line per unit, and a pipe is not a place for a carriage return.
    """
    if options.quiet or not sys.stderr.isatty():
        return None

    def show(done: int, total: int) -> None:
        end = "\n" if done == total else ""
        typer.echo(f"\r{done}/{total} units{end}", err=True, nl=False)

    return show


@contextmanager
def _stop_on_signal(*, quiet: bool) -> Iterator[asyncio.Event]:
    """Let the first interrupt stop the run cleanly, and the second abandon it.

    The first sets the engine's stop event: it takes no new unit, lets the calls in flight finish
    and still writes all three files. The second restores the default handler and re-raises, which
    is what someone pressing it twice is asking for.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: set[signal.Signals] = set()

    def restore() -> None:
        for number in list(installed):
            with suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(number)
            installed.discard(number)

    def request(number: signal.Signals) -> None:
        if stop.is_set():
            restore()
            signal.raise_signal(number)
            return
        stop.set()
        if not quiet:
            typer.secho(
                "Stopping: finishing the calls in flight. Interrupt again to abandon them.",
                err=True,
                fg=typer.colors.YELLOW,
            )

    for number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(number, request, number)
        except (NotImplementedError, RuntimeError, ValueError):
            # A platform without signal handling, or a loop off the main thread.
            continue
        installed.add(number)
    if not installed and not quiet:
        # Said rather than left to be discovered: here the first interrupt is the second one's
        # behaviour — it arrives as a KeyboardInterrupt at an arbitrary await and no file is
        # written. Someone running a long suite should know that before they press it.
        typer.secho(
            "This platform does not allow a clean interrupt: Ctrl-C will abandon the run and "
            "write nothing. Answers already paid for stay in the cache.",
            err=True,
            fg=typer.colors.YELLOW,
        )
    try:
        yield stop
    finally:
        restore()


def _refuse_drift(options: Options, suite: Suite) -> None:
    expected = options.require_inputs_sha256
    if expected is None or suite.inputs_sha256 == expected:
        return
    raise _BadArgument(
        f"{options.suite} no longer digests to what that run measured "
        f"({suite.inputs_sha256[:12]}… against {expected[:12]}…). A changed suite is a different "
        f"measurement: use `lakmus run` for it."
    )


def _print_findings(path: Path, findings: Sequence[Finding]) -> None:
    for finding in sorted(findings, key=lambda item: (item.severity != "error", item.code)):
        colour = typer.colors.RED if finding.severity == "error" else typer.colors.YELLOW
        typer.secho(str(finding), err=True, fg=colour)
    errors = sum(1 for finding in findings if finding.severity == "error")
    typer.secho(f"{path}: {errors} error(s); nothing was run.", err=True, bold=True)


def _now() -> datetime:
    return datetime.now(UTC)


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:300]
