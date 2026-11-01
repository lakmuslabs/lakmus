"""`lakmus compare` and `lakmus replay`.

Both commands exist to stop a comparison nobody should draw, so most of these tests are about what
they refuse. The runs are produced by `lakmus run` itself rather than fabricated: a comparison of
two hand-written files would not exercise what makes two real runs comparable.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fake_target import FakeTarget, Reaction
from harness import single_suite
from lakmus.cli.main import app
from lakmus.errors import ExitCode
from lakmus.results import comparable
from lakmus.results.models import RunResult
from lakmus.targets import registry
from lakmus.targets.base import RenderedCase

runner = CliRunner()


class _Adapter:
    def __init__(self) -> None:
        self.answer: Callable[[RenderedCase], Reaction] = lambda case: "oui"
        self.made: list[FakeTarget] = []

    def __call__(self) -> FakeTarget:
        target = FakeTarget(answer=lambda case: self.answer(case))
        self.made.append(target)
        return target


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> _Adapter:
    built = _Adapter()
    monkeypatch.setitem(registry.BUILTIN_TARGETS, "fake", built)
    return built


@pytest.fixture
def suite(valid_suite: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Twenty cases, because a bootstrap interval needs them and a comparison is about intervals."""
    root = single_suite(
        valid_suite,
        cases=[
            {"id": f"c-{index:02d}", "question": f"q {index} ?", "expected": "oui"}
            for index in range(20)
        ],
    )
    monkeypatch.chdir(tmp_path)
    return root


def _err(result: Any) -> str:
    try:
        return str(result.stderr)
    except (AttributeError, ValueError):
        return str(result.output)


def _run(suite: Path, out: Path, *extra: str) -> Path:
    result = runner.invoke(
        app,
        [
            "run",
            str(suite),
            "--target",
            "fake:local",
            "--no-code",
            "--no-cache",
            "--quiet",
            "--no-report",
            "--out",
            str(out),
            *extra,
        ],
    )
    assert result.exit_code in (ExitCode.SUCCESS, ExitCode.THRESHOLD_FAILED), _err(result)
    return out / "run.json"


def _change_the_prompt(suite: Path) -> None:
    """A change the inputs digest covers. A threshold is not one: ADR 0005 leaves thresholds out
    of `inputs_sha256` on purpose, so two runs under different thresholds stay comparable."""
    (suite / "prompts" / "screener.j2").write_text(
        "Answer briefly. {{ question }}\n", encoding="utf-8"
    )


def _rewrite(path: Path, **changes: Any) -> Path:
    """Edit a run file the way a tamperer would: the fields change, the digests do not."""
    document = json.loads(path.read_text(encoding="utf-8"))
    document.update(changes)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- compare ------------------------------------------------------------------------------------


def test_two_runs_of_one_suite_compare(suite: Path, adapter: _Adapter, tmp_path: Path) -> None:
    first = _run(suite, tmp_path / "one")
    second = _run(suite, tmp_path / "two")

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    assert "accuracy" in result.stdout
    assert "overlap" in result.stdout


def test_one_run_is_not_a_comparison(suite: Path, adapter: _Adapter, tmp_path: Path) -> None:
    only = _run(suite, tmp_path / "one")

    result = runner.invoke(app, ["compare", str(only)])

    assert result.exit_code == ExitCode.USAGE
    assert "at least two" in _err(result)


def test_a_changed_suite_is_refused_and_the_digest_is_named(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    first = _run(suite, tmp_path / "one")
    _change_the_prompt(suite)
    second = _run(suite, tmp_path / "two")

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == ExitCode.USAGE
    assert "the suite differs" in _err(result)
    for path in (first, second):
        digest = RunResult.model_validate_json(path.read_text(encoding="utf-8"))
        assert digest.suite.inputs_sha256[:12] in _err(result), "name the digests, not the fact"
    # One cause, not a list of every cause there is: a message that mentioned all three would
    # satisfy each of these tests while telling the reader nothing.
    assert "temperature" not in _err(result)
    assert "interrupted" not in _err(result)


def test_two_temperatures_are_refused_with_the_values_named(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    """The case `config.overrides` exists for: same suite, same digest, different measurement."""
    first = _run(suite, tmp_path / "one", "--temperature", "0.2")
    second = _run(suite, tmp_path / "two", "--temperature", "1.5")

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == ExitCode.USAGE
    assert "temperature: 0.2 against 1.5" in _err(result)
    assert "the suite differs" not in _err(result)


def test_an_interrupted_run_cannot_be_compared(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    first = _run(suite, tmp_path / "one")
    second = _rewrite(_run(suite, tmp_path / "two"), status="interrupted")

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == ExitCode.USAGE
    assert "the second run was interrupted" in _err(result)
    assert "the suite differs" not in _err(result)


def test_a_run_file_edited_to_drop_a_metric_is_refused(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    """Two comparable runs share a digest that covers the metric list, so this means tampering."""
    first = _run(suite, tmp_path / "one")
    second = _rewrite(_run(suite, tmp_path / "two"), summary={})

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == ExitCode.USAGE
    assert "edited" in _err(result)


def test_something_that_is_not_a_run_file_is_refused(tmp_path: Path) -> None:
    not_a_run = tmp_path / "notes.json"
    not_a_run.write_text('{"hello": true}', encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["compare", str(not_a_run), str(other)])

    assert result.exit_code == ExitCode.USAGE
    assert "not a lakmus run file" in _err(result)


def test_json_output_carries_both_runs_and_the_overlap(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    first = _run(suite, tmp_path / "one")
    second = _run(suite, tmp_path / "two")

    result = runner.invoke(app, ["compare", str(first), str(second), "--json"])

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    document = json.loads(result.stdout)
    assert len(document["runs"]) == 2
    assert document["metrics"]["accuracy"][1]["intervals_overlap"] is True


def test_two_runs_that_disagree_report_the_delta_and_that_the_intervals_are_apart(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    """The two numbers the command exists to produce. Nothing asserted them."""
    first = _run(suite, tmp_path / "one")
    adapter.answer = lambda case: "non" if case.case_id < "c-12" else "oui"
    second = _run(suite, tmp_path / "two")

    result = runner.invoke(app, ["compare", str(first), str(second)])

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    assert "-0.6" in result.stdout, "twelve of twenty answers changed"
    assert "disjoint" in result.stdout
    assert "overlap" not in result.stdout


def test_the_json_comparison_reports_intervals_that_do_not_overlap(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    first = _run(suite, tmp_path / "one")
    adapter.answer = lambda case: "non" if case.case_id < "c-12" else "oui"
    second = _run(suite, tmp_path / "two")

    result = runner.invoke(app, ["compare", str(first), str(second), "--json"])

    document = json.loads(result.stdout)
    assert document["metrics"]["accuracy"][1]["intervals_overlap"] is False


# --- replay -------------------------------------------------------------------------------------


def test_a_replay_is_comparable_with_the_run_it_replays(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    original = _run(suite, tmp_path / "one", "--temperature", "0.3")

    result = runner.invoke(
        app,
        [
            "replay",
            str(original),
            "--suite",
            str(suite),
            "--no-code",
            "--no-cache",
            "--quiet",
            "--no-report",
            "--out",
            str(tmp_path / "again"),
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    before = RunResult.model_validate_json(original.read_text(encoding="utf-8"))
    after = RunResult.model_validate_json(
        (tmp_path / "again" / "run.json").read_text(encoding="utf-8")
    )
    assert comparable(before, after)
    assert after.config.overrides == before.config.overrides == {"temperature": 0.3}
    assert after.config.repeats == before.config.repeats
    assert after.config.seed == before.config.seed
    assert after.target.sha256 == before.target.sha256


def test_a_replay_against_a_changed_suite_is_refused(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    original = _run(suite, tmp_path / "one")
    _change_the_prompt(suite)

    result = runner.invoke(
        app,
        [
            "replay",
            str(original),
            "--suite",
            str(suite),
            "--no-code",
            "--no-cache",
            "--out",
            str(tmp_path / "again"),
        ],
    )

    assert result.exit_code == ExitCode.USAGE
    assert "different measurement" in _err(result)
    assert not (tmp_path / "again").exists()


def test_a_replay_names_the_recorded_target_when_none_is_given(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    original = _run(suite, tmp_path / "one")

    result = runner.invoke(
        app,
        [
            "replay",
            str(original),
            "--suite",
            str(suite),
            "--no-code",
            "--no-cache",
            "--quiet",
            "--no-report",
            "--out",
            str(tmp_path / "again"),
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    configured = adapter.made[-1].config
    assert configured is not None
    assert configured.identity == "local", "the identity the run recorded, not a guess"


def test_replaying_something_that_is_not_a_run_file_is_refused_without_a_traceback(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    not_a_run = tmp_path / "notes.json"
    not_a_run.write_text('{"hello": true}', encoding="utf-8")

    result = runner.invoke(
        app, ["replay", str(not_a_run), "--suite", str(suite), "--no-code", "--no-cache"]
    )

    assert result.exit_code == ExitCode.USAGE
    assert "not a lakmus run file" in _err(result)
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_an_interrupted_run_cannot_be_replayed(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    """A replay's whole claim is that its result is comparable with the run it replays. A partial
    run is comparable with nothing, so this would have paid for a suite to produce a pair
    `lakmus compare` then refuses."""
    partial = _rewrite(_run(suite, tmp_path / "one"), status="interrupted")

    result = runner.invoke(
        app,
        [
            "replay",
            str(partial),
            "--suite",
            str(suite),
            "--no-code",
            "--no-cache",
            "--out",
            str(tmp_path / "again"),
        ],
    )

    assert result.exit_code == ExitCode.USAGE
    assert "did not finish" in _err(result)
    assert not (tmp_path / "again" / "run.json").exists()


def test_a_replay_sends_the_recorded_parameters_to_the_target(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    original = _run(suite, tmp_path / "one", "-P", "top_p=0.9")

    result = runner.invoke(
        app,
        [
            "replay",
            str(original),
            "--suite",
            str(suite),
            "--no-code",
            "--no-cache",
            "--quiet",
            "--no-report",
            "--out",
            str(tmp_path / "again"),
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    before = RunResult.model_validate_json(original.read_text(encoding="utf-8"))
    after = RunResult.model_validate_json(
        (tmp_path / "again" / "run.json").read_text(encoding="utf-8")
    )
    assert after.target.parameters["top_p"] == 0.9
    assert after.target.sha256 == before.target.sha256


def test_a_replay_restores_the_recorded_resample_count(
    suite: Path, adapter: _Adapter, tmp_path: Path
) -> None:
    """An interval is only recomputable from the seed when the resample count comes back too.

    Nothing on the command line can set it in 0.1, so the recorded value is edited here — the
    only way to tell a restoration from a coincidence.
    """
    original = _run(suite, tmp_path / "one")
    document = json.loads(original.read_text(encoding="utf-8"))
    document["config"]["bootstrap_resamples"] = 250
    original.write_text(json.dumps(document), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "replay",
            str(original),
            "--suite",
            str(suite),
            "--no-code",
            "--no-cache",
            "--quiet",
            "--no-report",
            "--out",
            str(tmp_path / "again"),
        ],
    )

    assert result.exit_code == ExitCode.SUCCESS, _err(result)
    after = RunResult.model_validate_json(
        (tmp_path / "again" / "run.json").read_text(encoding="utf-8")
    )
    assert after.config.bootstrap_resamples == 250
