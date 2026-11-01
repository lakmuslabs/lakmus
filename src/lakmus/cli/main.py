"""Entry point for the `lakmus` command.

Commands live in sibling modules and hold no domain logic: they parse arguments, call into the
suite and the engine, and translate the outcome into an exit code.
"""

from typing import Annotated

import typer

from lakmus import __version__
from lakmus.cli.compare import compare
from lakmus.cli.init import init
from lakmus.cli.replay import replay
from lakmus.cli.run import run
from lakmus.cli.validate import validate

app = typer.Typer(
    name="lakmus",
    help="Run domain test suites against AI models, agents and MCP servers.",
    no_args_is_help=True,
    add_completion=False,
)

app.command()(init)
app.command()(validate)
app.command()(run)
app.command()(compare)
app.command()(replay)


def _print_version(requested: bool) -> None:
    if requested:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Run domain test suites against AI models, agents and MCP servers."""
