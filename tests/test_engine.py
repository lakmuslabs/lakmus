"""The engine, against a scripted target. No network anywhere in this file.

Named after ADR 0003's parity list where a test covers one of its items, because that list is the
reason this project has an engine of its own rather than a wrapper around someone else's.
"""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from random import Random
from typing import Any, ClassVar

import pytest
import yaml

from fake_target import FakeTarget
from lakmus.engine import FileCache, NullCache, RetryPolicy, execute, plan
from lakmus.engine.cache import cache_key
from lakmus.engine.plan import check_target
from lakmus.errors import (
    Code,
    ScorerError,
    SuiteInvalid,
    TargetConfigError,
    TargetRateLimited,
    TargetUnavailable,
)
from lakmus.results import comparable
from lakmus.scorers import BUILTIN_SCORERS, ScorerSpec
from lakmus.suite.loader import MANIFEST_FILENAME, load, validate
from lakmus.targets.base import TargetError, TargetKind

MOMENT = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
SINGLE_METRICS = [{"id": "accuracy", "scorer": "builtin:exact", "with": {"expected": "expected"}}]


async def _never_sleeps(seconds: float) -> None:
    """The engine's waits are asserted on, never waited through."""
    return None


def _single_suite(root: Path, *, cases: Sequence[Mapping[str, Any]] | None = None) -> Path:
    """A one-metric, single-answer suite built from the fixture."""
    manifest = yaml.safe_load((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    manifest["metrics"] = SINGLE_METRICS
    manifest["thresholds"] = {"accuracy": {"min": 0.9}}
    manifest["target"]["output"] = {"type": "text"}
    # The fixture repeats three times; a test counting calls wants one unless it says so.
    manifest["run"]["repeats"] = 1
    (root / MANIFEST_FILENAME).write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (root / "prompts" / "screener.j2").write_text("{{ question }}\n", encoding="utf-8")
    rows = cases or [
        {"id": f"c-{index}", "question": f"question {index} ?", "expected": "oui"}
        for index in range(3)
    ]
    (root / "cases" / "pairs.jsonl").write_text(
        "\n".join(__import__("json").dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    (root / "scorers").mkdir(exist_ok=True)
    return root


def _planned(root: Path, **options: Any) -> Any:
    suite = load(root, run_suite_code=False, scorers=BUILTIN_SCORERS)
    specs: dict[str, ScorerSpec] = {
        metric.id: BUILTIN_SCORERS[metric.scorer.removeprefix("builtin:")]
        for metric in suite.manifest.metrics
    }
    return plan(suite, specs=specs, **options)


async def _run(root: Path, target: FakeTarget, **options: Any) -> Any:
    run_plan = _planned(root, **options.pop("plan", {}))
    await target.setup(run_plan.target_config(identity="scripted", model=None))
    try:
        return await execute(
            run_plan,
            target=target,
            clock=lambda: MOMENT,
            entropy=Random(42),
            sleep=_never_sleeps,
            **options,
        )
    finally:
        await target.teardown()


# --- ADR 0003 parity: two retry levels ---------------------------------------------------------


async def test_parity_a_retryable_error_is_retried_and_a_terminal_one_is_recorded(
    valid_suite: Path,
) -> None:
    root = _single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    target = FakeTarget(script=[TargetUnavailable("later"), "oui"])

    result = await _run(root, target)

    assert len(target.calls) == 2
    assert result.cases[0].answers[0].attempts == 2
    assert result.run.summary["accuracy"].value == 1.0


async def test_parity_a_case_that_never_answers_is_recorded_not_fatal(valid_suite: Path) -> None:
    root = _single_suite(
        valid_suite,
        cases=[
            {"id": "c-0", "question": "q ?", "expected": "oui"},
            {"id": "c-1", "question": "q ?", "expected": "oui"},
        ],
    )
    target = FakeTarget(
        answer=lambda case: TargetUnavailable("down") if case.case_id == "c-0" else "oui"
    )

    result = await _run(root, target, policy=RetryPolicy(attempts=2))

    summary = result.run.summary["accuracy"]
    assert summary.n_errors == 1
    assert summary.n_cases == 1
    assert result.run.status == "complete"


async def test_parity_the_retry_budget_is_spent_and_then_recorded(valid_suite: Path) -> None:
    root = _single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    target = FakeTarget(answer=lambda case: TargetUnavailable("down"))

    result = await _run(root, target, policy=RetryPolicy(attempts=3))

    assert len(target.calls) == 3
    error = result.cases[0].answers[0].output.error
    assert error is not None
    assert (error.kind, error.attempts) == ("unavailable", 3)


async def test_a_provider_that_says_when_to_come_back_is_obeyed(valid_suite: Path) -> None:
    root = _single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    target = FakeTarget(script=[TargetRateLimited("slow", retry_after_s=12.0), "oui"])
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    run_plan = _planned(root)
    await target.setup(run_plan.target_config(identity="scripted", model=None))
    await execute(run_plan, target=target, clock=lambda: MOMENT, entropy=Random(1), sleep=record)

    assert waits == [12.0]


# --- ADR 0003 parity: timeouts -----------------------------------------------------------------


async def test_parity_a_per_attempt_timeout_is_retried_then_recorded(valid_suite: Path) -> None:
    root = _single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])

    class Hangs(FakeTarget):
        async def invoke(self, case: Any) -> Any:
            self.calls.append(case)
            await asyncio.sleep(10)
            raise AssertionError("unreachable")

    manifest_path = root / MANIFEST_FILENAME
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["run"]["timeout_s"] = 1
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    target = Hangs()
    run_plan = _planned(root)
    await target.setup(run_plan.target_config(identity="scripted", model=None))
    result = await execute(
        run_plan,
        target=target,
        clock=lambda: MOMENT,
        entropy=Random(1),
        sleep=_never_sleeps,
        policy=RetryPolicy(attempts=2),
    )

    error = result.cases[0].answers[0].output.error
    assert error is not None
    assert error.kind == "timeout"
    assert len(target.calls) == 2


# --- ADR 0003 parity: bounded concurrency ------------------------------------------------------


async def test_parity_concurrency_is_bounded_and_recorded(valid_suite: Path) -> None:
    root = _single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(12)],
    )
    live = 0
    peak = 0

    class Counts(FakeTarget):
        async def invoke(self, case: Any) -> Any:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0)
            live -= 1
            return await FakeTarget.invoke(self, case)

    result = await _run(root, Counts(), plan={"concurrency": 3})

    assert peak <= 3
    assert result.run.config.concurrency == 3


# --- ADR 0003 parity: the cache key ------------------------------------------------------------


def test_parity_a_changed_temperature_misses_the_cache() -> None:
    """The generation configuration is inside the fingerprint, so it is inside the key."""
    cold = cache_key(target_digest="a" * 64, input_sha256="b" * 64, repeat=0)
    warm = cache_key(target_digest="c" * 64, input_sha256="b" * 64, repeat=0)
    second_repeat = cache_key(target_digest="a" * 64, input_sha256="b" * 64, repeat=1)

    assert len({cold, warm, second_repeat}) == 3


async def test_parity_a_second_run_pays_for_nothing(valid_suite: Path, tmp_path: Path) -> None:
    """Resuming is re-running: the cache answers by content, so there is no --resume flag."""
    root = _single_suite(valid_suite)
    cache = FileCache(root=tmp_path / "cache", tool_version="test")

    first = FakeTarget()
    await _run(root, first, cache=cache)
    second = FakeTarget()
    result = await _run(root, second, cache=cache)

    assert len(first.calls) == 3
    assert second.calls == []
    assert all(answer.cached for record in result.cases for answer in record.answers)


async def test_only_an_answer_is_cached_never_an_error(valid_suite: Path, tmp_path: Path) -> None:
    """A provider's transient failure kept forever would be a permanent wrong answer."""
    root = _single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    cache = FileCache(root=tmp_path / "cache", tool_version="test")

    failing = FakeTarget(answer=lambda case: TargetError(kind="bad_request", message="nope"))
    await _run(root, failing, cache=cache)
    recovered = FakeTarget()
    result = await _run(root, recovered, cache=cache)

    assert len(recovered.calls) == 1
    assert result.run.summary["accuracy"].value == 1.0


def test_a_corrupt_cache_entry_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    cache = FileCache(root=tmp_path / "cache", tool_version="test")
    key = cache_key(target_digest="a" * 64, input_sha256="b" * 64, repeat=0)
    path = cache.path_for(key)
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")

    assert cache.get(key) is None


# --- ADR 0003 parity: the bootstrap is recorded ------------------------------------------------


async def test_parity_the_seed_and_the_resample_count_are_recorded(valid_suite: Path) -> None:
    root = _single_suite(valid_suite)

    result = await _run(root, FakeTarget(), plan={"bootstrap_resamples": 250})

    assert result.run.config.bootstrap_resamples == 250
    assert result.run.config.seed == 42


# --- interruption and the early abort ----------------------------------------------------------


async def test_an_interrupted_run_is_readable_and_not_comparable(valid_suite: Path) -> None:
    root = _single_suite(valid_suite)
    stop = asyncio.Event()
    stop.set()

    result = await _run(root, FakeTarget(), stop=stop)

    assert result.run.status == "interrupted"
    assert result.cases == ()
    assert not comparable(result.run, result.run)


async def test_ten_consecutive_failures_end_the_run(valid_suite: Path) -> None:
    """Paying for five thousand cases to learn the target is misconfigured is the expensive
    mistake."""
    root = _single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(40)],
    )
    target = FakeTarget(answer=lambda case: TargetError(kind="bad_request", message="nope"))

    result = await _run(root, target, plan={"concurrency": 1}, early_abort=10)

    assert result.aborted is True
    assert result.run.status == "interrupted"
    assert len(result.cases) < 40


async def test_the_early_abort_can_be_turned_off(valid_suite: Path) -> None:
    root = _single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(12)],
    )
    target = FakeTarget(answer=lambda case: TargetError(kind="bad_request", message="nope"))

    result = await _run(root, target, plan={"concurrency": 1}, early_abort=0)

    assert result.aborted is False
    assert len(result.cases) == 12


async def test_one_good_case_among_the_first_ten_keeps_the_run_going(valid_suite: Path) -> None:
    root = _single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(12)],
    )
    answers = iter(["oui"] + [TargetError(kind="bad_request", message="nope")] * 11)
    target = FakeTarget(answer=lambda case: next(answers))

    result = await _run(root, target, plan={"concurrency": 1}, early_abort=10)

    assert result.aborted is False
    assert len(result.cases) == 12


# --- rendering ---------------------------------------------------------------------------------


async def test_a_case_missing_a_template_field_fails_alone(valid_suite: Path) -> None:
    """The template compiled, so it is this case's fault and not the suite's."""
    root = _single_suite(
        valid_suite,
        cases=[
            {"id": "c-0", "question": "q ?", "expected": "oui"},
            {"id": "c-1", "expected": "oui"},
        ],
    )

    result = await _run(root, FakeTarget())

    summary = result.run.summary["accuracy"]
    assert (summary.n_cases, summary.n_errors) == (1, 1)
    broken = next(record for record in result.cases if record.case_id == "c-1")
    assert broken.answers == ()
    assert "could not be rendered" in str(broken.scores["accuracy"].details["error"])


async def test_a_pair_is_two_calls_each_seeing_its_own_side(valid_suite: Path) -> None:
    root = valid_suite
    (root / "prompts" / "screener.j2").write_text(
        "{{ variant }}|{{ poste }}|{{ name }}\n", encoding="utf-8"
    )
    manifest = yaml.safe_load((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    manifest["metrics"] = [
        {"id": "flip", "scorer": "builtin:pair_flip", "with": {"field": "decision"}}
    ]
    manifest["thresholds"] = {"flip": {"max": 0.1}}
    manifest["target"]["output"] = {"type": "json"}
    manifest["run"]["repeats"] = 1
    (root / MANIFEST_FILENAME).write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (root / "cases" / "pairs.jsonl").write_text(
        '{"id": "p-0", "poste": "comptable", "variable": "nom",'
        ' "a": {"name": "Amélie"}, "b": {"name": "Khadija"}}\n',
        encoding="utf-8",
    )
    target = FakeTarget(script=[{"decision": "retenu"}, {"decision": "retenu"}])

    result = await _run(root, target)

    assert [call.prompt.strip() for call in target.calls] == [
        "a|comptable|Amélie",
        "b|comptable|Khadija",
    ]
    assert result.run.summary["flip"].value == 0.0
    assert len(result.cases[0].answers) == 2


# --- the fingerprint, and the target itself ----------------------------------------------------


async def test_the_fingerprint_is_taken_after_the_calls(valid_suite: Path) -> None:
    """A provider names the version it served only in a response."""
    root = _single_suite(valid_suite)

    result = await _run(root, FakeTarget())

    assert result.run.target.declared_version == "fake-2026-11-01"


async def test_a_target_of_the_wrong_kind_is_refused_before_the_first_call(
    valid_suite: Path,
) -> None:
    root = _single_suite(valid_suite)
    run_plan = _planned(root)

    class AgentShaped(FakeTarget):
        kind: ClassVar[TargetKind] = "agent"

    with pytest.raises(TargetConfigError, match="is a agent adapter"):
        check_target(run_plan, AgentShaped())


# --- what the plan refuses ---------------------------------------------------------------------


def test_a_suite_that_mixes_arities_is_refused(valid_suite: Path) -> None:
    """Validation catches it first; the plan refuses it too, for a caller using the library."""
    root = _single_suite(valid_suite)
    manifest = yaml.safe_load((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    manifest["metrics"] = [
        {"id": "accuracy", "scorer": "builtin:exact"},
        {"id": "flip", "scorer": "builtin:pair_flip"},
    ]
    manifest["thresholds"] = {}
    (root / MANIFEST_FILENAME).write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )

    findings = validate(root, run_suite_code=False, scorers=BUILTIN_SCORERS)

    assert {finding.code for finding in findings} == {Code.METRIC_ARITY_MIXED}
    with pytest.raises(SuiteInvalid):
        _planned(root)


def test_an_override_of_something_that_is_not_a_run_field_is_refused(valid_suite: Path) -> None:
    root = _single_suite(valid_suite)

    with pytest.raises(TargetConfigError, match="nothing to override"):
        _planned(root, overrides={"temprature": 0.7})


def test_an_override_is_recorded_so_that_comparability_can_see_it(valid_suite: Path) -> None:
    root = _single_suite(valid_suite)

    run_plan = _planned(root, overrides={"temperature": 0.7})

    assert run_plan.config.temperature == 0.7
    assert run_plan.config.overrides == ("temperature",)


async def test_a_scorer_that_cannot_run_ends_the_run(valid_suite: Path) -> None:
    """The same fault for every case, so recording it five thousand times would bury it."""
    root = _single_suite(valid_suite)
    manifest = yaml.safe_load((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    manifest["metrics"] = [
        {"id": "accuracy", "scorer": "builtin:exact", "with": {"normalise": ["strip_accents"]}}
    ]
    manifest["thresholds"] = {}
    (root / MANIFEST_FILENAME).write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(ScorerError, match="unknown normalisation step"):
        await _run(root, FakeTarget())


# --- a case is an error only when no repeat of it scored ---------------------------------------


async def test_a_case_contributes_the_repeats_that_scored(valid_suite: Path) -> None:
    root = _single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    manifest_path = root / MANIFEST_FILENAME
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["run"]["repeats"] = 2
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    target = FakeTarget(script=["oui", TargetError(kind="bad_request", message="nope")])

    result = await _run(root, target, cache=NullCache())

    summary = result.run.summary["accuracy"]
    assert (summary.n_cases, summary.n_errors, summary.n_observations) == (1, 0, 1)


# --- ADR 0003 parity: a wall clock as well as a per-attempt timeout ---------------------------


async def test_parity_a_whole_run_time_limit_stops_scheduling(valid_suite: Path) -> None:
    """The second half of the timeout requirement: a limit on the run, not only on an attempt.

    There is no default, because a large suite legitimately runs for hours and a default that
    killed it would be worse than none.
    """
    root = _single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(20)],
    )
    ticks = iter([MOMENT] + [MOMENT.replace(second=30)] * 100)
    target = FakeTarget()
    run_plan = _planned(root, concurrency=1)
    await target.setup(run_plan.target_config(identity="scripted", model=None))

    result = await execute(
        run_plan,
        target=target,
        clock=lambda: next(ticks),
        entropy=Random(1),
        sleep=_never_sleeps,
        time_limit_s=10.0,
    )

    assert result.run.status == "interrupted"
    assert len(result.cases) < 20


async def test_without_a_limit_a_long_run_is_not_cut_short(valid_suite: Path) -> None:
    root = _single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(20)],
    )

    result = await _run(root, FakeTarget())

    assert result.run.status == "complete"
    assert len(result.cases) == 20
