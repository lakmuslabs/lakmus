"""Deciding, once, whether a suite's own Python may be imported.

A suite arrives as data, often cloned from a stranger, and its scorers are code. The question is
therefore asked rather than assumed — and it is handed to the loader as a callback rather than
answered up front, so that it is asked only when there is something to import and the prompt names
the real modules.

Shared by `validate`, `run` and `replay`: a command that asked differently would be a second
posture towards the same risk.
"""

import sys

import typer

from lakmus.errors import ExitCode
from lakmus.suite.loader import SuiteCodeConsent


def build(*, yes: bool, no_code: bool) -> SuiteCodeConsent:
    """The consent callback for a `--yes` / `--no-code` pair."""
    if yes and no_code:
        typer.secho("--yes and --no-code contradict each other.", err=True, fg=typer.colors.RED)
        raise typer.Exit(ExitCode.USAGE)

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


def _skipped(modules: tuple[str, ...], reason: str) -> None:
    listed = ", ".join(modules)
    typer.secho(
        f"Not checking {len(modules)} scorer module(s) ({listed}): {reason}",
        err=True,
        fg=typer.colors.YELLOW,
    )
