"""`lakmus validate`."""

import sys
from pathlib import Path
from typing import Annotated

import typer

from lakmus.errors import ExitCode, Finding
from lakmus.suite.loader import inspect
from lakmus.suite.models import Manifest, SuiteScorer, parse_scorer_ref


def _uses_suite_code(manifest: Manifest) -> bool:
    return any(
        isinstance(parse_scorer_ref(metric.scorer), SuiteScorer) for metric in manifest.metrics
    )


def _confirm_running_suite_code(module_count: int) -> bool:
    """Ask before importing Python written by whoever wrote the suite.

    Without a terminal the answer is no: a CI job that did not pass `--yes` must not hang, and
    must not silently execute a stranger's code either.
    """
    if not sys.stdin.isatty():
        typer.secho(
            f"Not checking {module_count} suite scorer module(s): importing them runs the "
            "suite author's code. Pass --yes to allow it, or --no-code to say so explicitly.",
            err=True,
            fg=typer.colors.YELLOW,
        )
        return False
    return typer.confirm(
        f"Import {module_count} scorer module(s) from this suite? This runs its author's code",
        default=False,
    )


def validate(
    path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, help="The suite directory."),
    ],
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Import suite scorers without asking.")
    ] = False,
    no_code: Annotated[bool, typer.Option("--no-code", help="Never import suite scorers.")] = False,
) -> None:
    """Check a suite against the format it declares, and report every problem found."""
    if yes and no_code:
        typer.secho("--yes and --no-code contradict each other.", err=True, fg=typer.colors.RED)
        raise typer.Exit(ExitCode.USAGE)

    findings, suite = inspect(path, run_suite_code=False)

    if suite is not None and not no_code and _uses_suite_code(suite.manifest):
        modules = {
            parsed.module
            for metric in suite.manifest.metrics
            if isinstance(parsed := parse_scorer_ref(metric.scorer), SuiteScorer)
        }
        if yes or _confirm_running_suite_code(len(modules)):
            findings, suite = inspect(path, run_suite_code=True)

    _report(path, findings)
    errors = sum(1 for finding in findings if finding.severity == "error")
    raise typer.Exit(ExitCode.USAGE if errors else ExitCode.SUCCESS)


def _report(path: Path, findings: list[Finding]) -> None:
    for finding in sorted(findings, key=lambda item: (item.severity != "error", item.code)):
        colour = typer.colors.RED if finding.severity == "error" else typer.colors.YELLOW
        typer.secho(str(finding), fg=colour)

    errors = sum(1 for finding in findings if finding.severity == "error")
    warnings = len(findings) - errors
    if errors:
        summary = f"{path}: {errors} error(s), {warnings} warning(s)."
    elif warnings:
        summary = f"{path}: valid, with {warnings} warning(s)."
    else:
        summary = f"{path}: valid."
    typer.secho(summary, err=True, bold=True)
