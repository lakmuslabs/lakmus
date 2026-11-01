"""`lakmus run` as a caller in CI experiences it: three files and an exit code.

No network anywhere: the `fake` adapter is registered into the target registry for the duration of
a test, so the command under test is the real one all the way down to the engine.
"""

import gzip
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from fake_target import FakeTarget, Reaction
from harness import single_suite
from lakmus.cli import runner as cli_runner
from lakmus.cli.main import app
from lakmus.cli.runner import exit_code_for
from lakmus.engine import artefacts
from lakmus.errors import ExitCode
from lakmus.results.models import RunResult
from lakmus.suite.loader import MANIFEST_FILENAME
from lakmus.targets import registry
from lakmus.targets.base import RenderedCase, TargetError

runner = CliRunner()

FAILING_CASES = [{"id": "c-0", "question": "q ?", "expected": "non"}]


class _Adapter:
    """The `fake:` adapter as the registry sees it: a factory, and what it built."""

    def __init__(self) -> None:
        self.made: list[FakeTarget] = []
        self.answer: Callable[[RenderedCase], Reaction] = lambda case: "oui"

    def __call__(self) -> FakeTarget:
        target = FakeTarget(answer=lambda case: self.answer(case))
        self.made.append(target)
        return target

    @property
    def calls(self) -> int:
        return sum(len(target.calls) for target in self.made)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> _Adapter:
    built = _Adapter()
    monkeypatch.setitem(registry.BUILTIN_TARGETS, "fake", built)
    return built


@pytest.fixture
def suite(valid_suite: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A passing single-answer suite, with the working directory beside it."""
    root = single_suite(valid_suite)
    monkeypatch.chdir(tmp_path)
    return root


def _err(result: Any) -> str:
    try:
        return str(result.stderr)
    except (AttributeError, ValueError):
        return str(result.output)


def _cases(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _invoke(suite: Path, *extra: str, cache: Path | None = None) -> Any:
    arguments = ["run", str(suite), "--target", "fake:local", "--no-code", "--quiet"]
    arguments += ["--cache-dir", str(cache)] if cache is not None else ["--no-cache"]
    return runner.invoke(app, [*arguments, *extra])


def test_a_passing_suite_writes_three_files_and_exits_zero(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    result = _invoke(suite)

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    directories = list((tmp_path / "lakmus-runs").iterdir())
    assert len(directories) == 1
    assert sorted(entry.name for entry in directories[0].iterdir()) == [
        "cases.jsonl.gz",
        "report.html",
        "run.json",
    ]
    assert "accuracy" in result.stdout
    assert "PASS" in result.stdout


def test_the_output_directory_is_named_after_the_run_id(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    _invoke(suite)

    directory = next((tmp_path / "lakmus-runs").iterdir())
    written = RunResult.model_validate_json((directory / "run.json").read_text(encoding="utf-8"))
    assert directory.name == written.run_id


def test_a_failed_threshold_exits_one_and_still_writes_the_files(
    valid_suite: Path, adapter: _Adapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = single_suite(valid_suite, cases=FAILING_CASES)
    monkeypatch.chdir(tmp_path)

    result = _invoke(root, "--out", str(tmp_path / "out"))

    assert result.exit_code == ExitCode.THRESHOLD_FAILED, _err(result)
    assert (tmp_path / "out" / "run.json").exists()
    assert "FAIL" in result.stdout


def test_an_invalid_suite_exits_two_and_runs_nothing(
    valid_suite: Path, adapter: _Adapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = single_suite(valid_suite)
    (root / "cases" / "pairs.jsonl").unlink()
    monkeypatch.chdir(tmp_path)

    result = _invoke(root)

    assert result.exit_code == ExitCode.USAGE
    assert adapter.calls == 0
    assert not (tmp_path / "lakmus-runs").exists()


def test_an_unknown_adapter_exits_two(suite: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["run", str(suite), "--target", "nosuchthing:x", "--no-code", "--no-cache"]
    )

    assert result.exit_code == ExitCode.USAGE
    assert "nosuchthing" in _err(result)


def test_an_occupied_output_directory_is_refused_before_the_first_call(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    occupied = tmp_path / "out"
    occupied.mkdir()
    (occupied / "run.json").write_text("{}", encoding="utf-8")

    result = _invoke(suite, "--out", str(occupied))

    assert result.exit_code == ExitCode.USAGE
    assert adapter.calls == 0, "a mistyped --out must cost nothing"
    assert (occupied / "run.json").read_text(encoding="utf-8") == "{}"


def test_json_puts_the_run_file_on_stdout(suite: Path, adapter: _Adapter) -> None:
    result = _invoke(suite, "--json")

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    assert RunResult.model_validate_json(result.stdout).summary["accuracy"].value == 1.0


def test_no_report_leaves_the_other_two_files(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    result = _invoke(suite, "--no-report", "--out", str(tmp_path / "out"))

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    assert not (tmp_path / "out" / "report.html").exists()
    assert (tmp_path / "out" / "cases.jsonl.gz").exists()


def test_prompts_are_kept_in_the_cases_file_and_never_in_the_report(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    out = tmp_path / "out"

    result = _invoke(suite, "--keep-prompts", "--out", str(out))

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    lines = _cases(out / "cases.jsonl.gz")
    assert lines[0]["keep_prompts"] is True
    prompt = lines[1]["answers"][0]["prompt"]
    assert prompt.startswith("question 0 ?")
    assert prompt not in (out / "report.html").read_text(encoding="utf-8")
    assert "carries the case content" in _err(result)


def test_without_the_flag_no_prompt_reaches_the_cases_file(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    _invoke(suite, "--out", str(tmp_path / "out"))

    assert "prompt" not in _cases(tmp_path / "out" / "cases.jsonl.gz")[1]["answers"][0]


def test_a_parameter_reaches_the_recorded_fingerprint(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    result = _invoke(suite, "-P", "top_p=0.9", "--out", str(tmp_path / "out"))

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    written = RunResult.model_validate_json(
        (tmp_path / "out" / "run.json").read_text(encoding="utf-8")
    )
    assert written.target.parameters["top_p"] == 0.9


def test_a_parameter_that_names_a_credential_is_refused(suite: Path, adapter: _Adapter) -> None:
    result = _invoke(suite, "-P", "api_key=sk-live-1234")

    assert result.exit_code == ExitCode.USAGE
    assert adapter.calls == 0
    assert "sk-live-1234" not in _err(result)


def test_a_run_with_no_flags_records_no_override(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    """Recording the command's own defaults would make every run incomparable with every other."""
    _invoke(suite, "--out", str(tmp_path / "out"))

    written = RunResult.model_validate_json(
        (tmp_path / "out" / "run.json").read_text(encoding="utf-8")
    )
    assert written.config.overrides == {}
    assert written.config.concurrency == 8, "not an override: it cannot change a measurement"


def test_an_override_is_recorded_with_its_value(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    _invoke(suite, "--temperature", "0.7", "--seed", "7", "--out", str(tmp_path / "out"))

    written = RunResult.model_validate_json(
        (tmp_path / "out" / "run.json").read_text(encoding="utf-8")
    )
    assert written.config.overrides == {"seed": 7, "temperature": 0.7}
    assert written.config.temperature == 0.7


def test_a_second_run_reads_the_cache_and_pays_for_nothing(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"

    first = _invoke(suite, "--out", str(tmp_path / "one"), cache=cache)
    calls_after_first = adapter.calls
    second = _invoke(suite, "--out", str(tmp_path / "two"), cache=cache)

    assert first.exit_code == ExitCode.SUCCESS and second.exit_code == ExitCode.SUCCESS
    assert adapter.calls == calls_after_first, "every answer was already in the cache"
    written = RunResult.model_validate_json(
        (tmp_path / "two" / "run.json").read_text(encoding="utf-8")
    )
    assert written.latency_ms.p50 is None, "a cached run measured no latency"
    assert all(
        line["answers"][0]["cached"] for line in _cases(tmp_path / "two" / "cases.jsonl.gz")[1:]
    )


def test_contradictory_consent_flags_are_refused(suite: Path, adapter: _Adapter) -> None:
    result = runner.invoke(
        app,
        ["run", str(suite), "--target", "fake:local", "--yes", "--no-code", "--no-cache"],
    )

    assert result.exit_code == ExitCode.USAGE
    assert adapter.calls == 0


def test_a_run_of_nothing_but_failures_stops_early_and_exits_three(
    valid_suite: Path, adapter: _Adapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(20)],
    )
    monkeypatch.chdir(tmp_path)
    adapter.answer = lambda case: TargetError(kind="bad_request", message="refused", attempts=1)

    result = _invoke(root, "--early-abort", "2", "--out", str(tmp_path / "out"))

    assert result.exit_code == ExitCode.RUNTIME, _err(result)
    assert adapter.calls < 20
    assert "Stopped early" in _err(result)
    written = RunResult.model_validate_json(
        (tmp_path / "out" / "run.json").read_text(encoding="utf-8")
    )
    assert written.status == "interrupted"


def test_the_report_lists_the_failures_the_run_recorded(
    valid_suite: Path, adapter: _Adapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = single_suite(valid_suite)
    monkeypatch.chdir(tmp_path)
    adapter.answer = lambda case: TargetError(kind="timeout", message="no answer", attempts=3)

    _invoke(root, "--early-abort", "0", "--out", str(tmp_path / "out"))

    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    # The kind in its own cell. A bare `"timeout" in html` was satisfied by the provenance line,
    # which prints the run's `timeout 60s` whether anything failed or not.
    assert ">timeout<" in html
    assert "produced no score" in html
    assert "c-0" in html


def test_an_interrupted_run_has_no_verdict_whatever_its_thresholds_say(
    valid_suite: Path, adapter: _Adapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise an interruption would be a way to pass a suite."""
    root = single_suite(valid_suite)
    monkeypatch.chdir(tmp_path)
    _invoke(root, "--out", str(tmp_path / "out"))
    written = RunResult.model_validate_json(
        (tmp_path / "out" / "run.json").read_text(encoding="utf-8")
    )

    assert exit_code_for(written) == ExitCode.SUCCESS
    assert all(summary.passed for summary in written.summary.values())
    assert exit_code_for(written.model_copy(update={"status": "interrupted"})) == ExitCode.RUNTIME


_ECHOING_SCORER = '''"""A scorer that quotes the answer it could not read.

A supported pattern: `describable()` exists so that a scorer echoing a raw answer back for a
human is not stopped from doing it.
"""

from lakmus.scorers import Score, scorer


@scorer(name="echo", higher_is_better=True)
def echo(case, output):
    return Score.unscoreable(f"could not read a verdict out of {output.content!r}")
'''


def _echoing_suite(root: Path) -> Path:
    """A suite whose own scorer quotes the answer into its reason."""
    manifest = yaml.safe_load((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    manifest["metrics"] = [{"id": "echo", "scorer": "scorers/echoing.py:echo"}]
    manifest["thresholds"] = {"echo": {"min": 0.5}}
    (root / MANIFEST_FILENAME).write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (root / "scorers" / "echoing.py").write_text(_ECHOING_SCORER, encoding="utf-8")
    return root


def test_a_reason_that_quotes_the_answer_is_redacted_before_it_reaches_the_report(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    """The report is the artefact designed to be forwarded, and an answer is whatever the model
    wrote — including a key it was shown."""
    root = _echoing_suite(suite)
    adapter.answer = lambda case: "Authorization: Bearer sk-live-SUPERSECRET0123456789"

    result = runner.invoke(
        app,
        [
            "run",
            str(root),
            "--target",
            "fake:local",
            "--yes",
            "--no-cache",
            "--quiet",
            "--early-abort",
            "0",
            "--out",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == ExitCode.THRESHOLD_FAILED, _err(result)
    html = (tmp_path / "out" / "report.html").read_text(encoding="utf-8")
    assert "could not read a verdict" in html, "the reason is still there"
    assert "sk-live-SUPERSECRET0123456789" not in html
    assert "[redacted]" in html


def test_an_unexpected_failure_exits_three_rather_than_claiming_a_threshold_failed(
    suite: Path, adapter: _Adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 1 is what a CI gate reads as "the suite did not meet its thresholds"."""

    def boom(*arguments: object, **keywords: object) -> None:
        raise TypeError("something no one anticipated")

    monkeypatch.setattr(artefacts, "write", boom)

    result = _invoke(suite)

    assert result.exit_code == ExitCode.RUNTIME
    assert "TypeError" in _err(result)
    assert "bug in lakmus" in _err(result)


def test_a_report_that_will_not_render_does_not_cost_the_other_two_files(
    suite: Path, adapter: _Adapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule the cache already follows: presentation must not end a run that was paid for."""

    def boom(*arguments: object, **keywords: object) -> str:
        raise RuntimeError("the template is broken")

    monkeypatch.setattr(cli_runner, "render", boom)
    out = tmp_path / "out"

    result = _invoke(suite, "--out", str(out))

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    assert (out / "run.json").exists() and (out / "cases.jsonl.gz").exists()
    assert not (out / "report.html").exists()
    assert "could not be rendered" in _err(result)


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_an_unwritable_output_directory_is_refused_before_the_first_call(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    """A read-only workspace is the ordinary CI case, and it used to be found after the last
    call."""
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        result = _invoke(suite, "--out", str(parent / "run"))
    finally:
        parent.chmod(0o700)

    assert result.exit_code == ExitCode.USAGE
    assert adapter.calls == 0
    assert "cannot be written" in _err(result)
