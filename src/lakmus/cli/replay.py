"""`lakmus replay`."""

from pathlib import Path
from typing import Annotated

import typer

from lakmus.cli import consent, runner
from lakmus.engine.plan import DEFAULT_CONCURRENCY
from lakmus.engine.run import DEFAULT_EARLY_ABORT
from lakmus.errors import ExitCode


def replay(
    run_file: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, metavar="RUN", help="A run.json to re-run."),
    ],
    suite: Annotated[
        Path,
        typer.Option(
            "--suite",
            exists=True,
            file_okay=False,
            dir_okay=True,
            help="The suite that run measured. It must still digest to the same inputs.",
        ),
    ],
    target: Annotated[
        str | None,
        typer.Option("--target", "-t", help="Defaults to the target the run recorded."),
    ] = None,
    api_key_env: Annotated[
        str | None, typer.Option("--api-key-env", metavar="NAME", help="Not recorded in a run.")
    ] = None,
    concurrency: Annotated[
        int, typer.Option("--concurrency", min=1, help="Not restored: it cannot change a result.")
    ] = DEFAULT_CONCURRENCY,
    early_abort: Annotated[
        int, typer.Option("--early-abort", min=0, help="0 disables it.")
    ] = DEFAULT_EARLY_ABORT,
    out: Annotated[Path | None, typer.Option("--out", metavar="DIR")] = None,
    cache_dir: Annotated[Path | None, typer.Option("--cache-dir", metavar="DIR")] = None,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Call the target for every answer.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Import this suite's scorers without asking.")
    ] = False,
    no_code: Annotated[bool, typer.Option("--no-code", help="Never import them.")] = False,
    keep_prompts: Annotated[bool, typer.Option("--keep-prompts")] = False,
    no_report: Annotated[bool, typer.Option("--no-report", help="Skip report.html.")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
) -> None:
    """Re-run a recorded configuration, against the same target or another one.

    Everything comparability depends on comes from the run file: the overrides, and through the
    suite digest the manifest values they were applied to. Naming another target is the point of
    the command — it measures a second one under a configuration that is provably identical,
    rather than one a human retyped.

    One recorded target cannot be reconstructed: an endpoint whose URL carried a query string.
    A fingerprint is published, and a query string is a plausible place for a credential, so
    ADR 0006 strips it — which leaves this command with an endpoint that is not the one measured.
    Pass `--target` for those.
    """
    recorded = runner.read_run_or_exit(run_file)
    if recorded.status != "complete":
        # The command's whole claim is that its result is comparable with the run it replays. A
        # partial run is comparable with nothing, so replaying one would pay for a full suite to
        # produce a pair `lakmus compare` then refuses.
        typer.secho(
            f"{run_file} did not finish, so nothing can be comparable with it. Use `lakmus run` "
            f"to measure this suite again.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(ExitCode.USAGE)
    fingerprint = recorded.target
    runner.perform(
        runner.Options(
            suite=suite,
            target=target or f"{fingerprint.adapter}:{fingerprint.identity}",
            api_key_env=api_key_env,
            # The recorded parameters, so a replay against another target asks it the same thing.
            parameters=dict(fingerprint.parameters),
            # Exactly what the run recorded, and nothing else: adding the manifest's own values as
            # overrides would make the replay incomparable with the run it replays.
            overrides=dict(recorded.config.overrides),
            concurrency=concurrency,
            bootstrap_resamples=recorded.config.bootstrap_resamples,
            early_abort=early_abort,
            out=out,
            cache_dir=cache_dir,
            use_cache=not no_cache,
            keep_prompts=keep_prompts,
            write_report=not no_report,
            as_json=as_json,
            quiet=quiet,
            consent=consent.build(yes=yes, no_code=no_code),
            require_inputs_sha256=recorded.suite.inputs_sha256,
        )
    )
