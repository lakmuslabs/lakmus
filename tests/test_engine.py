"""The engine, against a scripted target. No network anywhere in this file.

Named after ADR 0003's parity list where a test covers one of its items, because that list is the
reason this project has an engine of its own rather than a wrapper around someone else's.
"""

import asyncio
from datetime import timedelta
from pathlib import Path
from random import Random
from typing import Any, ClassVar

import pytest
import yaml

from fake_target import FakeTarget
from harness import MOMENT, never_sleeps, planned, run_engine, single_suite
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
from lakmus.results.models import TargetFingerprint, cache_digest
from lakmus.results.statistics import sample_indices
from lakmus.scorers import BUILTIN_SCORERS
from lakmus.suite.loader import MANIFEST_FILENAME, load, validate
from lakmus.targets.base import TargetError, TargetKind

# --- ADR 0003 parity: two retry levels ---------------------------------------------------------


async def test_parity_a_retryable_error_is_retried_and_a_terminal_one_is_recorded(
    valid_suite: Path,
) -> None:
    root = single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    target = FakeTarget(script=[TargetUnavailable("later"), "oui"])

    result = await run_engine(root, target)

    assert len(target.calls) == 2
    assert result.cases[0].answers[0].attempts == 2
    assert result.run.summary["accuracy"].value == 1.0


async def test_parity_a_case_that_never_answers_is_recorded_not_fatal(valid_suite: Path) -> None:
    root = single_suite(
        valid_suite,
        cases=[
            {"id": "c-0", "question": "q ?", "expected": "oui"},
            {"id": "c-1", "question": "q ?", "expected": "oui"},
        ],
    )
    target = FakeTarget(
        answer=lambda case: TargetUnavailable("down") if case.case_id == "c-0" else "oui"
    )

    result = await run_engine(root, target, policy=RetryPolicy(attempts=2))

    summary = result.run.summary["accuracy"]
    assert summary.n_errors == 1
    assert summary.n_cases == 1
    assert result.run.status == "complete"


async def test_parity_the_retry_budget_is_spent_and_then_recorded(valid_suite: Path) -> None:
    root = single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    target = FakeTarget(answer=lambda case: TargetUnavailable("down"))

    result = await run_engine(root, target, policy=RetryPolicy(attempts=3))

    assert len(target.calls) == 3
    error = result.cases[0].answers[0].output.error
    assert error is not None
    assert (error.kind, error.attempts) == ("unavailable", 3)


async def test_a_provider_that_says_when_to_come_back_is_obeyed(valid_suite: Path) -> None:
    root = single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    target = FakeTarget(script=[TargetRateLimited("slow", retry_after_s=12.0), "oui"])
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    run_plan = planned(root)
    await target.setup(run_plan.target_config(identity="scripted", model=None))
    await execute(run_plan, target=target, clock=lambda: MOMENT, entropy=Random(1), sleep=record)

    assert waits == [12.0]


# --- ADR 0003 parity: timeouts -----------------------------------------------------------------


async def test_parity_a_per_attempt_timeout_is_retried_then_recorded(valid_suite: Path) -> None:
    root = single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])

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
    run_plan = planned(root)
    await target.setup(run_plan.target_config(identity="scripted", model=None))
    result = await execute(
        run_plan,
        target=target,
        clock=lambda: MOMENT,
        entropy=Random(1),
        sleep=never_sleeps,
        policy=RetryPolicy(attempts=2),
    )

    error = result.cases[0].answers[0].output.error
    assert error is not None
    assert error.kind == "timeout"
    assert len(target.calls) == 2


# --- ADR 0003 parity: bounded concurrency ------------------------------------------------------


async def test_parity_concurrency_is_bounded_and_recorded(valid_suite: Path) -> None:
    root = single_suite(
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

    result = await run_engine(root, Counts(), plan={"concurrency": 3})

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
    root = single_suite(valid_suite)
    cache = FileCache(root=tmp_path / "cache", tool_version="test")

    first = FakeTarget()
    await run_engine(root, first, cache=cache)
    second = FakeTarget()
    result = await run_engine(root, second, cache=cache)

    assert len(first.calls) == 3
    assert second.calls == []
    assert all(answer.cached for record in result.cases for answer in record.answers)


async def test_only_an_answer_is_cached_never_an_error(valid_suite: Path, tmp_path: Path) -> None:
    """A provider's transient failure kept forever would be a permanent wrong answer."""
    root = single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    cache = FileCache(root=tmp_path / "cache", tool_version="test")

    failing = FakeTarget(answer=lambda case: TargetError(kind="bad_request", message="nope"))
    await run_engine(root, failing, cache=cache)
    recovered = FakeTarget()
    result = await run_engine(root, recovered, cache=cache)

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
    root = single_suite(valid_suite)

    result = await run_engine(root, FakeTarget(), plan={"bootstrap_resamples": 250})

    assert result.run.config.bootstrap_resamples == 250
    assert result.run.config.seed == 42


# --- interruption and the early abort ----------------------------------------------------------


async def test_an_interrupted_run_is_readable_and_not_comparable(valid_suite: Path) -> None:
    root = single_suite(valid_suite)
    stop = asyncio.Event()
    stop.set()

    result = await run_engine(root, FakeTarget(), stop=stop)

    assert result.run.status == "interrupted"
    assert result.cases == ()
    assert not comparable(result.run, result.run)


async def test_ten_consecutive_failures_end_the_run(valid_suite: Path) -> None:
    """Paying for five thousand cases to learn the target is misconfigured is the expensive
    mistake."""
    root = single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(40)],
    )
    target = FakeTarget(answer=lambda case: TargetError(kind="bad_request", message="nope"))

    result = await run_engine(root, target, plan={"concurrency": 1}, early_abort=10)

    assert result.aborted is True
    assert result.run.status == "interrupted"
    assert len(result.cases) < 40


async def test_the_early_abort_can_be_turned_off(valid_suite: Path) -> None:
    root = single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(12)],
    )
    target = FakeTarget(answer=lambda case: TargetError(kind="bad_request", message="nope"))

    result = await run_engine(root, target, plan={"concurrency": 1}, early_abort=0)

    assert result.aborted is False
    assert len(result.cases) == 12


async def test_one_good_case_among_the_first_ten_keeps_the_run_going(valid_suite: Path) -> None:
    root = single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(12)],
    )
    answers = iter(["oui"] + [TargetError(kind="bad_request", message="nope")] * 11)
    target = FakeTarget(answer=lambda case: next(answers))

    result = await run_engine(root, target, plan={"concurrency": 1}, early_abort=10)

    assert result.aborted is False
    assert len(result.cases) == 12


# --- rendering ---------------------------------------------------------------------------------


async def test_a_case_missing_a_template_field_fails_alone(valid_suite: Path) -> None:
    """The template compiled, so it is this case's fault and not the suite's."""
    root = single_suite(
        valid_suite,
        cases=[
            {"id": "c-0", "question": "q ?", "expected": "oui"},
            {"id": "c-1", "expected": "oui"},
        ],
    )

    result = await run_engine(root, FakeTarget())

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

    result = await run_engine(root, target)

    assert [call.prompt.strip() for call in target.calls] == [
        "a|comptable|Amélie",
        "b|comptable|Khadija",
    ]
    assert result.run.summary["flip"].value == 0.0
    assert len(result.cases[0].answers) == 2


# --- the fingerprint, and the target itself ----------------------------------------------------


async def test_the_fingerprint_is_taken_after_the_calls(valid_suite: Path) -> None:
    """A provider names the version it served only in a response."""
    root = single_suite(valid_suite)

    result = await run_engine(root, FakeTarget())

    assert result.run.target.declared_version == "fake-2026-11-01"


async def test_a_target_of_the_wrong_kind_is_refused_before_the_first_call(
    valid_suite: Path,
) -> None:
    root = single_suite(valid_suite)
    run_plan = planned(root)

    class AgentShaped(FakeTarget):
        kind: ClassVar[TargetKind] = "agent"

    with pytest.raises(TargetConfigError, match="is a agent adapter"):
        check_target(run_plan, AgentShaped())


# --- what the plan refuses ---------------------------------------------------------------------


def test_a_suite_that_mixes_arities_is_refused(valid_suite: Path) -> None:
    """Validation catches it first; the plan refuses it too, for a caller using the library."""
    root = single_suite(valid_suite)
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
        planned(root)


def test_an_override_of_something_that_is_not_a_run_field_is_refused(valid_suite: Path) -> None:
    root = single_suite(valid_suite)

    with pytest.raises(TargetConfigError, match="nothing to override"):
        planned(root, overrides={"temprature": 0.7})


def test_an_override_is_recorded_so_that_comparability_can_see_it(valid_suite: Path) -> None:
    root = single_suite(valid_suite)

    run_plan = planned(root, overrides={"temperature": 0.7})

    assert run_plan.config.temperature == 0.7
    assert run_plan.config.overrides == {"temperature": 0.7}


async def test_a_scorer_that_cannot_run_ends_the_run(valid_suite: Path) -> None:
    """The same fault for every case, so recording it five thousand times would bury it."""
    root = single_suite(valid_suite)
    manifest = yaml.safe_load((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    manifest["metrics"] = [
        {"id": "accuracy", "scorer": "builtin:exact", "with": {"normalise": ["strip_accents"]}}
    ]
    manifest["thresholds"] = {}
    (root / MANIFEST_FILENAME).write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(ScorerError, match="unknown normalisation step"):
        await run_engine(root, FakeTarget())


# --- a case is an error only when no repeat of it scored ---------------------------------------


async def test_a_case_contributes_the_repeats_that_scored(valid_suite: Path) -> None:
    root = single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    manifest_path = root / MANIFEST_FILENAME
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["run"]["repeats"] = 2
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    target = FakeTarget(script=["oui", TargetError(kind="bad_request", message="nope")])

    result = await run_engine(root, target, cache=NullCache())

    summary = result.run.summary["accuracy"]
    assert (summary.n_cases, summary.n_errors, summary.n_observations) == (1, 0, 1)


# --- ADR 0003 parity: a wall clock as well as a per-attempt timeout ---------------------------


async def test_parity_a_whole_run_time_limit_stops_scheduling(valid_suite: Path) -> None:
    """The second half of the timeout requirement: a limit on the run, not only on an attempt.

    The first version of this test asserted `len(result.cases) < 20` while the real value was 0,
    so it would have passed against an engine that ran nothing at all. It now pins both ends:
    some work happened, and not all of it did.
    """
    root = single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(20)],
    )
    reads = iter(range(1000))
    target = FakeTarget()
    run_plan = planned(root, concurrency=1)
    await target.setup(run_plan.target_config(identity="scripted", model=None))

    result = await execute(
        run_plan,
        target=target,
        # One virtual second per read, so the deadline arrives part-way through the suite.
        clock=lambda: MOMENT.replace(second=next(reads) % 60),
        entropy=Random(1),
        sleep=never_sleeps,
        time_limit_s=4.0,
    )

    assert result.run.status == "interrupted"
    assert 0 < len(result.cases) < 20
    assert len(target.calls) == len(result.cases)


async def test_without_a_limit_a_long_run_is_not_cut_short(valid_suite: Path) -> None:
    root = single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(20)],
    )

    result = await run_engine(root, FakeTarget())

    assert result.run.status == "complete"
    assert len(result.cases) == 20


# --- what only a target that suspends can prove -----------------------------------------------


async def test_the_early_abort_actually_saves_the_calls(valid_suite: Path) -> None:
    """The claim is that a misconfigured run costs ten cases rather than five thousand.

    With a fake that never awaited, every coroutine passed its only check before any unit had
    finished, so all forty cases were called and the abort saved nothing. The run was still
    marked interrupted, which is how it looked correct.
    """
    root = single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(40)],
    )
    target = FakeTarget(answer=lambda case: TargetError(kind="bad_request", message="nope"))

    result = await run_engine(root, target, plan={"concurrency": 1}, early_abort=10)

    assert result.aborted is True
    assert len(target.calls) <= 12
    assert len(result.cases) <= 12


async def test_an_interruption_stops_scheduling_rather_than_only_labelling(
    valid_suite: Path,
) -> None:
    """What M6's Ctrl-C handler depends on: setting the event has to stop the spending."""
    root = single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(20)],
    )
    stop = asyncio.Event()
    calls_before_stop = 3

    class Stops(FakeTarget):
        async def invoke(self, case: Any) -> Any:
            if len(self.calls) >= calls_before_stop:
                stop.set()
            return await FakeTarget.invoke(self, case)

    target = Stops()
    result = await run_engine(root, target, plan={"concurrency": 1}, stop=stop)

    assert result.run.status == "interrupted"
    assert len(target.calls) <= calls_before_stop + 2
    assert 0 < len(result.cases) < 20


async def test_a_configuration_fault_stops_the_other_workers_too(valid_suite: Path) -> None:
    """A config error used to end the caller while the remaining workers kept spending."""
    root = single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(20)],
    )

    class Misconfigured(FakeTarget):
        async def invoke(self, case: Any) -> Any:
            self.calls.append(case)
            await asyncio.sleep(0)
            raise TargetConfigError("the model does not exist")

    target = Misconfigured()

    with pytest.raises(TargetConfigError, match="does not exist"):
        await run_engine(root, target, plan={"concurrency": 4})

    assert len(target.calls) <= 8


async def test_a_judge_scored_case_that_loses_a_repeat_is_an_error_not_a_crash(
    valid_suite: Path,
) -> None:
    """One rate-limit exhaustion used to throw away a whole paid judge-scored run.

    Aggregation refuses a non-deterministic metric whose case has fewer than two repeats, so a
    partial case reached it and raised, which is the opposite of "a failed case is recorded, not
    fatal".
    """
    root = single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    manifest_path = root / MANIFEST_FILENAME
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["run"]["repeats"] = 2
    manifest["metrics"] = [{"id": "judged", "scorer": "scorers/judge.py:judged"}]
    manifest["thresholds"] = {}
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    (root / "scorers" / "judge.py").write_text(
        "from lakmus.scorers import Case, Score, scorer, text_of\n"
        "from lakmus.targets.base import TargetOutput\n\n\n"
        '@scorer(name="judged", higher_is_better=True, deterministic=False)\n'
        "def judged(case: Case, output: TargetOutput) -> Score:\n"
        "    answer = text_of(output)\n"
        "    return Score.of(1.0) if answer else Score.unscoreable('no answer')\n",
        encoding="utf-8",
    )
    target = FakeTarget(script=["oui", TargetError(kind="bad_request", message="nope")])

    suite = load(root, run_suite_code=True, scorers=BUILTIN_SCORERS)
    specs = {"judged": _suite_spec(root)}
    run_plan = plan(suite, specs=specs)
    await target.setup(run_plan.target_config(identity="scripted", model=None))
    result = await execute(
        run_plan, target=target, clock=lambda: MOMENT, entropy=Random(1), sleep=never_sleeps
    )

    summary = result.run.summary["judged"]
    assert result.run.status == "complete"
    assert (summary.n_cases, summary.n_errors) == (0, 1)
    assert summary.passed is None


def _suite_spec(root: Path) -> Any:
    """Import the fixture's judge scorer the way the loader does."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("judge_under_test", root / "scorers" / "judge.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from lakmus.scorers import spec_of

    resolved = spec_of(module.judged)
    assert resolved is not None
    return resolved


async def test_an_unwritable_cache_does_not_end_a_run_already_paid_for(
    valid_suite: Path, tmp_path: Path
) -> None:
    """The rule `get` states and `put` used to break."""
    root = single_suite(valid_suite, cases=[{"id": "c-0", "question": "q ?", "expected": "oui"}])
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    read_only.chmod(0o500)
    try:
        result = await run_engine(
            root, FakeTarget(), cache=FileCache(root=read_only / "cache", tool_version="t")
        )
    finally:
        read_only.chmod(0o700)

    assert result.run.summary["accuracy"].value == 1.0


# --- the cache digest, which nothing was testing ----------------------------------------------


def test_the_cache_digest_covers_the_generation_parameters() -> None:
    """ADR 0003 asks for a test that proves a changed temperature misses the cache.

    The first version compared two hand-written digests, so stubbing `cache_digest` to a constant
    left the whole suite green.
    """
    cold = TargetFingerprint.create(
        adapter="fake", kind="model", identity="fake://local", parameters={"temperature": 0.0}
    )
    warm = TargetFingerprint.create(
        adapter="fake", kind="model", identity="fake://local", parameters={"temperature": 0.7}
    )

    assert cache_digest(cold) != cache_digest(warm)


def test_the_cache_digest_ignores_the_version_the_provider_served() -> None:
    """Otherwise every entry written before the first response would be unreusable."""
    unknown = TargetFingerprint.create(adapter="fake", kind="model", identity="fake://local")
    known = TargetFingerprint.create(
        adapter="fake", kind="model", identity="fake://local", declared_version="fake-1"
    )

    assert cache_digest(unknown) == cache_digest(known)
    assert unknown.sha256 != known.sha256


async def test_a_run_at_another_temperature_pays_again(valid_suite: Path, tmp_path: Path) -> None:
    """End to end, through the key: the same suite at a different temperature is a cache miss."""
    root = single_suite(valid_suite)
    cache = FileCache(root=tmp_path / "cache", tool_version="test")

    cold = FakeTarget()
    await run_engine(root, cold, cache=cache)
    warm = FakeTarget()
    await run_engine(root, warm, cache=cache, plan={"overrides": {"temperature": 0.7}})

    assert len(cold.calls) == 3
    assert len(warm.calls) == 3


def test_a_sample_is_the_same_sample_on_another_machine() -> None:
    first = sample_indices(100, 7, seed=42)
    again = sample_indices(100, 7, seed=42)
    other = sample_indices(100, 7, seed=43)

    assert first == again
    assert first != other
    assert len(set(first)) == 7
    assert sample_indices(5, 9, seed=1) == (0, 1, 2, 3, 4)


# --- ADR 0010: the clock must not take a verdict away from a run that finished ------------------


async def test_a_run_that_took_every_unit_is_complete_even_if_the_limit_passed_meanwhile(
    valid_suite: Path,
) -> None:
    """The deadline is only a reason to stop *taking* work.

    Asking the clock before asking whether any work was left stamped a run that scored every case
    as interrupted — exit 3, not comparable, not publishable — whenever its last call happened to
    return after the limit. Against a provider that is a window of about a second on every run.
    """
    root = single_suite(
        valid_suite,
        cases=[{"id": f"c-{index}", "question": "q ?", "expected": "oui"} for index in range(3)],
    )
    # Four reads are what a one-worker run makes before the work runs out: the start, then one
    # before each unit. Everything after that is past the deadline.
    moments = iter([MOMENT] * 4 + [MOMENT + timedelta(seconds=30)] * 50)

    result = await run_engine(
        root,
        FakeTarget(),
        clock=lambda: next(moments),
        time_limit_s=1,
        plan={"concurrency": 1},
    )

    assert result.run.status == "complete"
    assert len(result.cases) == 3
    assert result.run.summary["accuracy"].value == 1.0
