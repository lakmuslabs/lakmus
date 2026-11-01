"""What a run leaves on disk: the directory, the cases file, and the order they are written in.

Every assertion here comes from ADR 0010. The results are produced by the real engine over a
scripted target rather than fabricated, because a fabricated `EngineResult` is exactly the shape
nobody would be checking.
"""

import asyncio
import gzip
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fake_target import FakeTarget
from harness import pair_suite, run_engine, single_suite
from lakmus.engine import EngineResult, artefacts
from lakmus.errors import OutputRefused
from lakmus.results.models import RunResult
from lakmus.scorers import Score

CASES = [
    {"id": "c-0", "question": "2 + 2 ?", "expected": "quatre"},
    {"id": "c-1", "question": "Combien de départements ?", "expected": "cent-un"},
]


async def _single(tmp_suite: Path, **options: Any) -> EngineResult:
    root = single_suite(tmp_suite, cases=CASES)
    return await run_engine(root, FakeTarget(answer=lambda case: "quatre"), **options)


def _lines(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


async def test_a_run_writes_the_three_files_and_the_run_parses_back(
    valid_suite: Path, tmp_path: Path
) -> None:
    result = await _single(valid_suite)

    written = artefacts.write(tmp_path / "out", result, html="<!doctype html><p>hello")

    assert written.run.name == "run.json"
    assert written.cases.name == "cases.jsonl.gz"
    assert written.report is not None and written.report.name == "report.html"
    reread = RunResult.model_validate_json(written.run.read_text(encoding="utf-8"))
    assert reread == result.run
    assert reread.cases == "cases.jsonl.gz"


async def test_no_report_is_written_when_none_is_rendered(
    valid_suite: Path, tmp_path: Path
) -> None:
    result = await _single(valid_suite)

    written = artefacts.write(tmp_path / "out", result)

    assert written.report is None
    assert not (tmp_path / "out" / "report.html").exists()


async def test_the_run_file_is_written_last_so_its_presence_means_the_directory_is_complete(
    valid_suite: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await _single(valid_suite)
    original = artefacts._atomic_write

    def fail_on_the_report(path: Path, payload: bytes) -> None:
        if path.name == "report.html":
            raise OSError("disk full")
        original(path, payload)

    monkeypatch.setattr(artefacts, "_atomic_write", fail_on_the_report)

    with pytest.raises(OSError, match="disk full"):
        artefacts.write(tmp_path / "out", result, html="<p>")

    assert (tmp_path / "out" / "cases.jsonl.gz").exists()
    assert not (tmp_path / "out" / "run.json").exists()


async def test_a_non_empty_directory_is_refused_rather_than_overwritten(
    valid_suite: Path, tmp_path: Path
) -> None:
    result = await _single(valid_suite)
    occupied = tmp_path / "out"
    occupied.mkdir()
    (occupied / "run.json").write_text("{}", encoding="utf-8")

    with pytest.raises(OutputRefused):
        artefacts.write(occupied, result)

    assert (occupied / "run.json").read_text(encoding="utf-8") == "{}"


async def test_an_empty_directory_that_already_exists_is_accepted(
    valid_suite: Path, tmp_path: Path
) -> None:
    result = await _single(valid_suite)
    (tmp_path / "out").mkdir()

    written = artefacts.write(tmp_path / "out", result)

    assert written.run.exists()


async def test_the_cases_file_opens_with_a_header_naming_the_run(
    valid_suite: Path, tmp_path: Path
) -> None:
    result = await _single(valid_suite)

    written = artefacts.write(tmp_path / "out", result)

    header = _lines(written.cases)[0]
    assert header["lakmus_cases"] == artefacts.CASES_FORMAT
    assert header["run_id"] == result.run.run_id
    assert header["suite"]["inputs_sha256"] == result.run.suite.inputs_sha256
    assert header["keep_prompts"] is False
    assert "case_id" not in header


async def test_one_line_per_case_and_repeat_carrying_the_scores_it_earned(
    valid_suite: Path, tmp_path: Path
) -> None:
    root = single_suite(valid_suite, cases=CASES)
    result = await run_engine(
        root, FakeTarget(answer=lambda case: "quatre"), plan={"overrides": {"repeats": 2}}
    )

    written = artefacts.write(tmp_path / "out", result)

    cases = _lines(written.cases)[1:]
    assert [(line["case_id"], line["repeat"]) for line in cases] == [
        ("c-0", 0),
        ("c-0", 1),
        ("c-1", 0),
        ("c-1", 1),
    ]
    first = cases[0]
    assert first["scores"]["accuracy"]["value"] == 1.0
    answer = first["answers"][0]
    assert answer["content"] == "quatre"
    assert answer["cached"] is False
    assert answer["attempts"] == 1
    assert answer["error"] is None
    assert "variant" not in answer


async def test_a_pair_case_records_both_sides_under_their_variant(
    valid_suite: Path, tmp_path: Path
) -> None:
    root = pair_suite(valid_suite)
    result = await run_engine(root, FakeTarget(answer=lambda case: {"decision": "retenu"}))

    written = artefacts.write(tmp_path / "out", result)

    answers = _lines(written.cases)[1]["answers"]
    assert [answer["variant"] for answer in answers] == ["a", "b"]
    assert len({answer["input_sha256"] for answer in answers}) == 2


async def test_the_prompt_is_absent_unless_the_run_asked_to_keep_it(
    valid_suite: Path, tmp_path: Path
) -> None:
    withheld = await _single(valid_suite)
    kept = await _single(valid_suite, keep_prompts=True)

    without = artefacts.write(tmp_path / "without", withheld)
    with_prompts = artefacts.write(tmp_path / "with", kept, keep_prompts=True)

    assert "prompt" not in _lines(without.cases)[1]["answers"][0]
    assert _lines(without.cases)[0]["keep_prompts"] is False
    kept_line = _lines(with_prompts.cases)[1]["answers"][0]
    assert kept_line["prompt"] == "2 + 2 ?"
    assert _lines(with_prompts.cases)[0]["keep_prompts"] is True


async def test_keeping_prompts_is_refused_when_the_run_did_not_record_them(
    valid_suite: Path, tmp_path: Path
) -> None:
    """Writing the flag without the text would produce a file whose header lies."""
    result = await _single(valid_suite)

    with pytest.raises(ValueError, match="keep_prompts"):
        artefacts.write(tmp_path / "out", result, keep_prompts=True)


async def test_the_cases_file_is_byte_reproducible(valid_suite: Path, tmp_path: Path) -> None:
    result = await _single(valid_suite)

    first = artefacts.write(tmp_path / "one", result)
    second = artefacts.write(tmp_path / "two", result)

    assert first.cases.read_bytes() == second.cases.read_bytes()
    # Asserted on the header itself, not only on two writes being equal: gzip stores whole
    # seconds, so two writes inside the same second agree even when the timestamp is the clock's.
    assert first.cases.read_bytes()[4:8] == b"\x00\x00\x00\x00"


async def test_a_detail_json_cannot_write_does_not_cost_the_run_its_evidence(
    valid_suite: Path, tmp_path: Path
) -> None:
    """`details` is whatever a suite author put in it, and a nested key is unchecked.

    `json.dumps(default=...)` is never consulted for a mapping key, so one odd key used to raise
    out of the writer and leave a paid run with no files at all.
    """
    result = await _single(valid_suite)
    odd = Score(value=1.0, details={"matched": {(1, 2): "a key no JSON has"}})
    record = replace(result.cases[0], scores={"accuracy": odd})
    result = replace(result, cases=(record, *result.cases[1:]))

    written = artefacts.write(tmp_path / "out", result)

    assert "(1, 2)" in _lines(written.cases)[1]["scores"]["accuracy"]["details"]["matched"]
    assert written.run.exists()


async def test_an_unwritable_directory_is_refused_before_a_run_rather_than_after(
    valid_suite: Path, tmp_path: Path
) -> None:
    """`reserve` creates the directory, which is the only way to learn it can be written."""
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        with pytest.raises(OutputRefused, match="cannot be written"):
            artefacts.reserve(parent / "run")
    finally:
        parent.chmod(0o700)


async def test_french_content_is_stored_unescaped(valid_suite: Path, tmp_path: Path) -> None:
    root = single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "é"}])
    result = await run_engine(root, FakeTarget(answer=lambda case: "Amélie Durand"))

    written = artefacts.write(tmp_path / "out", result)

    with gzip.open(written.cases, "rt", encoding="utf-8") as handle:
        assert "Amélie Durand" in handle.read()


async def test_an_interrupted_run_still_writes_every_file(
    valid_suite: Path, tmp_path: Path
) -> None:
    stop = asyncio.Event()
    stop.set()
    result = await _single(valid_suite, stop=stop)

    written = artefacts.write(tmp_path / "out", result, html="<p>")

    assert result.run.status == "interrupted"
    assert written.run.exists() and written.cases.exists()
    assert written.report is not None and written.report.exists()
    assert _lines(written.cases)[1:] == []
