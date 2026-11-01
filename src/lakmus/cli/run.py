"""`lakmus run`."""

from pathlib import Path
from typing import Annotated

import typer

from lakmus.cli import consent, runner
from lakmus.engine.plan import DEFAULT_CONCURRENCY
from lakmus.engine.run import DEFAULT_EARLY_ABORT
from lakmus.errors import ExitCode, TargetConfigError
from lakmus.results.models import ParameterValue
from lakmus.targets.registry import parse_parameter

OVERRIDABLE = ("repeats", "temperature", "seed", "timeout_s")


def run(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            metavar="SUITE",
            help="The suite directory.",
        ),
    ],
    target: Annotated[
        str,
        typer.Option(
            "--target",
            "-t",
            help="<adapter>:<model-or-endpoint>, for example openai:gpt-x or "
            "openai-compatible:https://host/v1#llama-3.",
        ),
    ],
    api_key_env: Annotated[
        str | None,
        typer.Option(
            "--api-key-env",
            metavar="NAME",
            help="Environment variable holding the key. Defaults to the adapter's own.",
        ),
    ] = None,
    parameter: Annotated[
        list[str] | None,
        typer.Option(
            "--parameter",
            "-P",
            metavar="NAME=VALUE",
            help="Passed to the target and recorded in its fingerprint. Repeatable.",
        ),
    ] = None,
    repeats: Annotated[
        int | None, typer.Option("--repeats", min=1, help="Override the manifest.")
    ] = None,
    temperature: Annotated[
        float | None, typer.Option("--temperature", min=0.0, max=2.0, help="Override the manifest.")
    ] = None,
    seed: Annotated[int | None, typer.Option("--seed", help="Override the manifest.")] = None,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", metavar="SECONDS", min=1, help="Override the manifest."),
    ] = None,
    concurrency: Annotated[
        int, typer.Option("--concurrency", min=1, help="Calls in flight at once.")
    ] = DEFAULT_CONCURRENCY,
    early_abort: Annotated[
        int,
        typer.Option(
            "--early-abort",
            min=0,
            help="End the run after this many consecutive failures. 0 disables it.",
        ),
    ] = DEFAULT_EARLY_ABORT,
    time_limit: Annotated[
        float | None,
        typer.Option("--time-limit", metavar="SECONDS", min=0.0, help="Wall-clock budget."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            metavar="DIR",
            help="Where the three files go. Defaults to lakmus-runs/<run_id>/.",
        ),
    ] = None,
    cache_dir: Annotated[
        Path | None, typer.Option("--cache-dir", metavar="DIR", help="Defaults to ~/.cache/lakmus.")
    ] = None,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Call the target for every answer.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Import this suite's scorers without asking.")
    ] = False,
    no_code: Annotated[bool, typer.Option("--no-code", help="Never import them.")] = False,
    keep_prompts: Annotated[
        bool,
        typer.Option("--keep-prompts", help="Write the rendered prompt into the cases file."),
    ] = False,
    no_report: Annotated[bool, typer.Option("--no-report", help="Skip report.html.")] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print run.json on stdout instead of the table.")
    ] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="No progress on stderr.")] = False,
) -> None:
    """Run a suite against a target, and exit with the verdict."""
    runner.perform(
        runner.Options(
            suite=path,
            target=target,
            api_key_env=api_key_env,
            parameters=_parameters(parameter or []),
            overrides=_overrides(
                repeats=repeats, temperature=temperature, seed=seed, timeout_s=timeout
            ),
            concurrency=concurrency,
            early_abort=early_abort,
            time_limit_s=time_limit,
            out=out,
            cache_dir=cache_dir,
            use_cache=not no_cache,
            keep_prompts=keep_prompts,
            write_report=not no_report,
            as_json=as_json,
            quiet=quiet,
            consent=consent.build(yes=yes, no_code=no_code),
        )
    )


def _overrides(**given: float | int | None) -> dict[str, runner.OverrideValue]:
    """Only what the invocation named.

    Passing this command's own defaults would record four overrides on every run, and two results
    are comparable only when their overrides match by name and by value. Every run would then be
    comparable with nothing but a run invoked in the same words.
    """
    return {name: value for name, value in given.items() if value is not None}


def _parameters(given: list[str]) -> dict[str, ParameterValue]:
    parsed: dict[str, ParameterValue] = {}
    for text in given:
        try:
            name, value = parse_parameter(text)
        except TargetConfigError as exc:
            typer.secho(str(exc), err=True, fg=typer.colors.RED)
            raise typer.Exit(ExitCode.USAGE) from None
        parsed[name] = value
    return parsed
