"""`lakmus validate`."""

import sys
from pathlib import Path
from typing import Annotated

import typer

from lakmus.errors import ExitCode, Finding
from lakmus.suite.loader import SuiteCodeConsent
from lakmus.suite.loader import validate as validate_suite


def _notice(message: str) -> None:
    typer.secho(message, err=True, fg=typer.colors.YELLOW)


def _skipped(modules: tuple[str, ...], reason: str) -> None:
    listed = ", ".join(modules)
    _notice(f"Not checking {len(modules)} scorer module(s) ({listed}): {reason}")


def _consent(*, yes: bool, no_code: bool) -> SuiteCodeConsent:
    """Decide, once, whether a suite's own Python may be imported.

    The decision is handed to the loader rather than taken before it, so that the question is
    asked only when there is something to import and the answer names the real modules.
    """
    if no_code:

        def refuse(modules: tuple[str, ...]) -> bool:
            _skipped(modules, "--no-code was given.")
            return False

        return refuse

    if yes:
        return True

    def ask(modules: tuple[str, ...]) -> bool:
        if not sys.stdin.isatty():
            _skipped(
                modules,
                "importing them runs the suite author's code and there is no terminal to ask at. "
                "Pass --yes to allow it, or --no-code to say so explicitly.",
            )
            return False
        listed = ", ".join(modules)
        return typer.confirm(
            f"Import {len(modules)} scorer module(s) from this suite ({listed})? "
            "This runs its author's code",
            default=False,
        )

    return ask


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

    findings = validate_suite(path, run_suite_code=_consent(yes=yes, no_code=no_code))
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
