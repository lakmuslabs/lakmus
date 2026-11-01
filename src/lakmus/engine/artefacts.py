"""Writing what a run produced: the cases file, the report, and the run file last.

Three choices here are worth reading before the code.

**The run file is written last.** It is the index: it names the cases file and carries the
aggregates. Writing it first would mean a directory killed mid-write still looks complete, so its
presence is what tells a reader that the other files are there.

**Each file is written beside its destination and renamed.** A reader never sees a half-written
artefact, which is the same rule the cache follows.

**The cases file is byte-reproducible.** The gzip modification time is zeroed, so two writes of one
result produce identical bytes and a digest over the file means something.

This lives in `engine` rather than in `results`, where the format it serialises lives, for one
concrete reason: an answer embeds a `TargetOutput`, which belongs to `targets`, and `targets`
already imports `results`. Moving the records there would close that edge into a cycle.
"""

import gzip
import io
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from lakmus.engine.run import AnswerRecord, CaseRecord, EngineResult
from lakmus.errors import OutputRefused
from lakmus.results.models import RunResult

CASES_FORMAT = "0.1"
"""The cases file's own version. It moves with `run.json`'s format, and a reader tells the header
line from a case line by this key."""

RUN_FILENAME = "run.json"
CASES_FILENAME = "cases.jsonl.gz"
REPORT_FILENAME = "report.html"
RUNS_DIRECTORY = Path("lakmus-runs")


@dataclass(frozen=True, slots=True)
class WrittenRun:
    """Where the three files landed. `report` is absent when none was rendered."""

    directory: Path
    run: Path
    cases: Path
    report: Path | None = None


def default_directory(run_id: str) -> Path:
    """`lakmus-runs/<run_id>/`. The id is a ULID, so the directory sorts chronologically."""
    return RUNS_DIRECTORY / run_id


def write(
    directory: Path,
    result: EngineResult,
    *,
    keep_prompts: bool = False,
    html: str | None = None,
) -> WrittenRun:
    """Write a run's artefacts into a directory that does not already hold one."""
    if keep_prompts and not _prompts_were_recorded(result.cases):
        # A `ValueError` because this is a caller contract, not a user's mistake: no command line
        # can reach it. The CLI turns anything unexpected into exit 3 with the reason, which is
        # what a programming error deserves.
        raise ValueError(
            "keep_prompts was asked of the writer but the run did not record any prompt: the "
            "header would claim a field no line carries. Pass keep_prompts to execute() too."
        )

    _prepare(directory)
    cases = directory / CASES_FILENAME
    _atomic_write(cases, _cases_payload(result, keep_prompts=keep_prompts))

    report: Path | None = None
    if html is not None:
        report = directory / REPORT_FILENAME
        _atomic_write(report, html.encode("utf-8"))

    run = directory / RUN_FILENAME
    _atomic_write(run, result.run.model_dump_json(indent=2).encode("utf-8") + b"\n")
    return WrittenRun(directory=directory, run=run, cases=cases, report=report)


def cases_header(run: RunResult, *, keep_prompts: bool) -> dict[str, Any]:
    """The first line of the cases file.

    A cases file that gets separated from its run file would otherwise be anonymous evidence, so
    it names the run and the suite that produced it.
    """
    return {
        "lakmus_cases": CASES_FORMAT,
        "run_id": run.run_id,
        "suite": run.suite.model_dump(mode="json"),
        "keep_prompts": keep_prompts,
    }


def case_line(record: CaseRecord, *, keep_prompts: bool) -> dict[str, Any]:
    """One case and one repeat: the answers it produced and the scores they earned."""
    return {
        "case_id": record.case_id,
        "repeat": record.repeat,
        "answers": [_answer_line(answer, keep_prompts=keep_prompts) for answer in record.answers],
        "scores": {
            metric_id: {"value": score.value, "details": dict(score.details)}
            for metric_id, score in record.scores.items()
        },
    }


def _answer_line(answer: AnswerRecord, *, keep_prompts: bool) -> dict[str, Any]:
    output = answer.output
    line: dict[str, Any] = {}
    if answer.variant is not None:
        # Only a pair has sides. A `null` on every single-answer line would be a field that means
        # nothing for the suites that are not pair suites.
        line["variant"] = answer.variant
    line["input_sha256"] = answer.input_sha256
    line["cached"] = answer.cached
    line["attempts"] = answer.attempts
    line["latency_ms"] = output.latency_ms
    line["content"] = output.content
    line["usage"] = None if output.usage is None else output.usage.model_dump(mode="json")
    line["cost_usd"] = output.cost_usd
    line["error"] = None if output.error is None else output.error.model_dump(mode="json")
    line["trace"] = [step.model_dump(mode="json") for step in output.trace]
    if keep_prompts and answer.prompt is not None:
        line["prompt"] = answer.prompt
    return line


def _cases_payload(result: EngineResult, *, keep_prompts: bool) -> bytes:
    lines: Iterable[Mapping[str, Any]] = [
        cases_header(result.run, keep_prompts=keep_prompts),
        *(case_line(record, keep_prompts=keep_prompts) for record in result.cases),
    ]
    text = "".join(f"{_dumps(line)}\n" for line in lines)
    buffer = io.BytesIO()
    # No filename in the header and no timestamp, so the bytes depend only on the content.
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, compresslevel=9) as compressed:
        compressed.write(text.encode("utf-8"))
    return buffer.getvalue()


def _dumps(line: Mapping[str, Any]) -> str:
    """One line of JSON, with accents left as accents so the file is readable in `zless`.

    Total by construction, which is not fastidiousness: a scorer's `details` is whatever a suite
    author put in it, and losing a paid run's whole evidence file to one odd value in one case
    would be the wrong trade. `default=` covers values; it is never consulted for a mapping *key*,
    so keys are coerced on the way in — a `Mapping[str, Any]` whose nested keys are tuples is a
    type annotation nobody enforced, not a reason to fail here.
    """
    return json.dumps(_writable(line), ensure_ascii=False, separators=(",", ":"), default=str)


def _writable(value: Any, depth: int = 0) -> Any:
    """The same structure with every mapping key spelled as a string."""
    if depth > 64:
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _writable(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_writable(item, depth + 1) for item in value]
    return value


def _prompts_were_recorded(records: Iterable[CaseRecord]) -> bool:
    return all(answer.prompt is not None for record in records for answer in record.answers)


def reserve(directory: Path) -> None:
    """Take a directory for a run before the first call is paid for.

    Creating it is the point, not a side effect: a check that only listed the directory said
    nothing about whether it could be written, so a read-only parent — the ordinary CI workspace
    — was discovered after the last call. Creating it now also narrows the window in which another
    run can take the same path to one where that run would be writing into a directory this one
    made.

    The cost is an empty directory left behind by a run that then failed for another reason, which
    is litter rather than lost evidence.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputRefused(f"{directory} cannot be written: {exc}") from exc
    refuse_if_occupied(directory)


def refuse_if_occupied(directory: Path) -> None:
    """Refuse a directory that already holds something, without creating it.

    There is no force: overwriting the last run is how evidence disappears, and naming another
    directory costs one word.
    """
    try:
        existing = sorted(entry.name for entry in directory.iterdir())
    except FileNotFoundError:
        return
    except OSError as exc:
        raise OutputRefused(f"{directory} cannot be read: {exc}") from exc
    if existing:
        raise OutputRefused(
            f"{directory} already holds {', '.join(existing[:3])}"
            f"{'…' if len(existing) > 3 else ''}. Name another directory with --out."
        )


def _prepare(directory: Path) -> None:
    reserve(directory)


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.partial")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
