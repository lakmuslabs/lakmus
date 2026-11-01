"""`lakmus validate`."""

from pathlib import Path
from typing import Annotated

import typer

from lakmus.cli import consent
from lakmus.errors import ExitCode, Finding
from lakmus.scorers import BUILTIN_SCORERS
from lakmus.suite.loader import validate as validate_suite


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
    findings = validate_suite(
        path,
        run_suite_code=consent.build(yes=yes, no_code=no_code),
        scorers=BUILTIN_SCORERS,
    )
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
