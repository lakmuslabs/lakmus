"""`lakmus init`."""

import json
from pathlib import Path
from typing import Annotated

import typer

from lakmus.errors import ExitCode
from lakmus.suite.loader import GITATTRIBUTES_LINE, MANIFEST_FILENAME, inspect
from lakmus.suite.models import ALLOWED_LICENCES, FORMAT_VERSION

_MANIFEST = """\
lakmus: "{format_version}"
id: {suite_id}
version: 0.1.0
title: {title}
language: {language}
license: {licence}
risk_level: medium

target:
  kind: {kind}
{target_input}
cases:
  source: cases/cases.jsonl

# One entry per thing this suite measures. A metric's scorer is either a built-in
# (builtin:<name>) or a function in this suite (scorers/<module>.py:<function>).
metrics:
  - id: exact_match
    scorer: builtin:exact_match

# A run fails when a threshold is crossed, so that a suite has a verdict rather than a
# number someone has to interpret.
thresholds:
  exact_match: {{min: 0.9}}

run:
  repeats: 1
  temperature: 0
  seed: 0
  timeout_s: 60
"""

_TARGET_INPUT = "  input: prompts/prompt.j2\n"

_PROMPT = """\
{# Rendered once per case. Every field of a case is available by name. #}
{{ question }}
"""

_CASE = {"id": "example-0001", "question": "2 + 2 ?", "expected": "4"}

_README = """\
# {title}

One sentence stating what a result from this suite supports.

## What this measures

The claim a number from this suite can carry. Prefer something falsifiable over something
flattering: "the answer changes when only the candidate's name changes" rather than "the model is
fair".

## What this does not measure

The limits a reader should know before quoting a result.

## Method

How a case becomes a prompt, and how an answer becomes a score. If the suite uses counterfactual
pairs, say what varies between A and B and what is held identical.

## Data

Where the inputs come from, whether they are synthetic, and under what licence. Record the source,
the date and the selection rule. No real personal data.

## Metrics and thresholds

| Metric | What it means | Threshold | Why that value |
| --- | --- | --- | --- |
| `exact_match` | share of answers matching exactly | min 0.9 | replace with a justified number |

## Known shortcuts

How a target could score well without doing the task, and what this suite does about it.

## Licence

{licence}. Suite content and code are covered separately from the Lakmus tool.
"""

_LICENSE = """\
{licence}

Replace this file with the full text of the licence, available at
https://spdx.org/licenses/{licence}.html
"""


def init(
    suite_id: Annotated[
        str, typer.Argument(metavar="ID", help="The suite id, as <domain>/<name>.")
    ],
    directory: Annotated[
        Path | None,
        typer.Option("--dir", help="Where to write it. Defaults to a path matching the id."),
    ] = None,
    kind: Annotated[
        str, typer.Option("--kind", help="What is under test: model, agent or mcp.")
    ] = "model",
    licence: Annotated[
        str,
        typer.Option("--license", help=f"SPDX identifier. One of: {', '.join(ALLOWED_LICENCES)}."),
    ] = "CC-BY-4.0",
    language: Annotated[
        str, typer.Option("--language", help="BCP-47 tag for the language of the cases.")
    ] = "en",
    title: Annotated[str | None, typer.Option("--title", help="Human-readable title.")] = None,
) -> None:
    """Scaffold a suite directory that validates."""
    root = directory if directory is not None else Path(*suite_id.split("/"))
    if root.exists() and any(root.iterdir()):
        typer.secho(f"{root} is not empty.", err=True, fg=typer.colors.RED)
        raise typer.Exit(ExitCode.USAGE)

    (root / "cases").mkdir(parents=True, exist_ok=True)
    if kind == "model":
        (root / "prompts").mkdir(exist_ok=True)
        (root / "prompts" / "prompt.j2").write_text(_PROMPT, encoding="utf-8")

    resolved_title = title if title is not None else suite_id.split("/")[-1].replace("-", " ")
    (root / MANIFEST_FILENAME).write_text(
        _MANIFEST.format(
            format_version=FORMAT_VERSION,
            suite_id=suite_id,
            title=json.dumps(resolved_title, ensure_ascii=False),
            language=language,
            licence=licence,
            kind=kind,
            target_input=_TARGET_INPUT if kind == "model" else "",
        ),
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        _README.format(title=resolved_title, licence=licence), encoding="utf-8"
    )
    (root / "LICENSE").write_text(_LICENSE.format(licence=licence), encoding="utf-8")
    (root / "cases" / "cases.jsonl").write_text(
        json.dumps(_CASE, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (root / ".gitattributes").write_text(f"{GITATTRIBUTES_LINE}\n", encoding="utf-8")

    findings, _ = inspect(root, run_suite_code=False)
    errors = [finding for finding in findings if finding.severity == "error"]
    if errors:
        # The scaffold is generated from a template rather than from the models, so that it can
        # carry comments. This is what stops the template drifting away from what validates.
        for finding in errors:
            typer.secho(str(finding), err=True, fg=typer.colors.RED)
        typer.secho(
            f"{root} was written but does not validate; this is a bug in lakmus init.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(ExitCode.RUNTIME)

    typer.echo(f"{root}")
    typer.secho(
        f"Edit {MANIFEST_FILENAME} and cases/cases.jsonl, then run: lakmus validate {root}",
        err=True,
    )
