"""The loop: one suite, one target, bounded concurrency, and a result.

Three behaviours here are worth reading before the code.

**A failed case is recorded, not fatal.** Only a fault that is the same for every case — a
misconfigured target, a scorer that cannot run — ends a run. One case in five thousand that the
provider refused is an error in the run file, and ADR 0006 already makes a metric with no scored
case report a failed verdict rather than silence.

**Ten consecutive errors end the run anyway.** A run whose first ten completed cases all failed is
misconfigured, and paying for five thousand to learn that is the expensive mistake.

**An interrupted run is readable and not comparable.** It stops scheduling, lets the calls in
flight finish, and returns what it has with `status="interrupted"`. Everything already answered is
in the cache, so nothing is lost and a re-run pays only for the rest.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from random import Random
from typing import Any

from lakmus.engine.cache import Cache, NullCache, cache_key
from lakmus.engine.plan import RunPlan
from lakmus.engine.render import CaseRenderFailed, RenderedInput, Variant
from lakmus.engine.retry import RetryPolicy
from lakmus.errors import RetryableTargetError, TargetRateLimited
from lakmus.results import ulid
from lakmus.results.aggregate import CaseObservation, MetricObservations, aggregate
from lakmus.results.models import (
    Cost,
    Latency,
    RunResult,
    SuiteRef,
    ToolInfo,
    cache_digest,
)
from lakmus.results.statistics import percentile
from lakmus.scorers import Case, PairOutputs, Score
from lakmus.targets.base import RenderedCase, Target, TargetError, TargetOutput, redact
from lakmus.targets.pricing import ModelPrice

DEFAULT_EARLY_ABORT = 10

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]
Progress = Callable[[int, int], None]
"""Units finished, units planned. Called once per finished unit, from the loop's own task, so an
implementation that blocks holds the run up."""


@dataclass(frozen=True, slots=True)
class AnswerRecord:
    """One call, or one cache hit."""

    case_id: str
    repeat: int
    variant: Variant | None
    input_sha256: str
    output: TargetOutput
    cached: bool
    attempts: int
    prompt: str | None = None
    """The text the target was sent, present only under `keep_prompts`.

    Absent by default because a record of five thousand prompts is five thousand prompts in a
    file people share, and ADR 0009 settled that the digest is what identifies an input."""


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """One case, one repeat: the answers it produced and the scores they earned.

    A pair produces two answers and one set of scores, which is why the answers are nested rather
    than the scores repeated.
    """

    case_id: str
    repeat: int
    answers: tuple[AnswerRecord, ...]
    scores: Mapping[str, Score]

    @property
    def scored(self) -> bool:
        return any(score.scored for score in self.scores.values())


@dataclass(frozen=True, slots=True)
class EngineResult:
    run: RunResult
    cases: tuple[CaseRecord, ...]
    aborted: bool = False
    """The run stopped itself after ten consecutive errors, as distinct from being interrupted
    from outside. Both leave `status="interrupted"`; only this says the tool made the call."""


@dataclass(slots=True)
class _Progress:
    """Shared state: how many units finished, and whether to stop starting new ones.

    A unit is one case and one repeat, so with three repeats the ten-unit threshold is reached
    after three and a third cases. The intent — catch a misconfigured run before it is paid for —
    is served either way, and counting whole cases would mean waiting for every repeat of each
    before deciding.
    """

    early_abort: int
    completed: int = 0
    errored: int = 0
    aborted: bool = False
    stop: asyncio.Event = field(default_factory=asyncio.Event)

    def finished(self, *, scored: bool) -> None:
        self.completed += 1
        if not scored:
            self.errored += 1
        if (
            self.early_abort
            and self.completed >= self.early_abort
            and self.errored == self.completed
        ):
            self.aborted = True
            self.stop.set()

    @property
    def interrupted(self) -> bool:
        return self.stop.is_set()


async def execute(
    plan: RunPlan,
    *,
    target: Target,
    clock: Clock,
    entropy: Random,
    cache: Cache | None = None,
    policy: RetryPolicy | None = None,
    sleep: Sleep = asyncio.sleep,
    stop: asyncio.Event | None = None,
    early_abort: int = DEFAULT_EARLY_ABORT,
    time_limit_s: float | None = None,
    tool_version: str = "",
    keep_prompts: bool = False,
    progress: Progress | None = None,
) -> EngineResult:
    """Run the plan against the target. The target is already set up by the caller."""
    store = cache if cache is not None else NullCache()
    retries = policy if policy is not None else RetryPolicy()
    counter = _Progress(early_abort=early_abort)
    if stop is not None:
        counter.stop = stop

    started_at = clock()
    # Drawn before anything else touches `entropy`: a run id that depended on how many retries
    # happened would not be reproducible from a seed, which ADR 0006 says it is.
    run_id = ulid.generate(lambda: started_at, entropy)
    target_digest = cache_digest(target.fingerprint())
    deadline = None if time_limit_s is None else started_at.timestamp() + time_limit_s

    units = [
        (position, case, repeat)
        for position, case in enumerate(plan.cases)
        for repeat in range(plan.config.repeats)
    ]
    taken = 0
    done: list[tuple[int, int, CaseRecord]] = []

    def should_stop() -> bool:
        if counter.interrupted:
            return True
        if deadline is not None and clock().timestamp() >= deadline:
            # Out of time: stop taking new work and keep what is answered. There is no default
            # limit, because a large suite legitimately runs for hours.
            counter.stop.set()
            return True
        return False

    async def worker() -> None:
        """Take units until there are none left, or until something says to stop.

        The check is here, before each unit, rather than once per coroutine. Every unit suspends,
        so a design that created one coroutine per unit and checked at its start would pass the
        check before any unit had finished and never look again — which is what the first version
        of this engine did, and it made the abort, the time limit and the interruption do nothing
        against any target that awaits.
        """
        nonlocal taken
        while True:
            # Work exhausted is checked first, and deliberately: `should_stop` reads the clock and
            # sets the stop event when the deadline has gone, so asking it after the last unit had
            # already been taken stamped a run that completed every case as interrupted — losing
            # the verdict of a run whose final call simply returned after the limit.
            if taken >= len(units) or should_stop():
                return
            position, case, repeat = units[taken]
            taken += 1
            record = await _one_unit(
                plan,
                case,
                repeat,
                target=target,
                store=store,
                retries=retries,
                sleep=sleep,
                entropy=entropy,
                target_digest=target_digest,
                stopping=should_stop,
                keep_prompts=keep_prompts,
            )
            done.append((position, repeat, record))
            counter.finished(scored=record.scored)
            if progress is not None:
                progress(len(done), len(units))

    workers = max(1, min(plan.config.concurrency, len(units)))
    try:
        async with asyncio.TaskGroup() as group:
            for _ in range(workers):
                group.create_task(worker())
    except BaseExceptionGroup as grouped:
        # A TaskGroup cancels its siblings, which is the point: a configuration fault must stop
        # the run rather than leave the other workers spending. The original exception is what
        # the CLI needs in order to choose an exit code, so it is unwrapped here.
        raise _first_leaf(grouped) from None

    # Completion order depends on timing; the result must not. Ordered by the case's position in
    # the suite, then by repeat.
    records = tuple(record for _, _, record in sorted(done, key=lambda item: (item[0], item[1])))

    return EngineResult(
        aborted=counter.aborted,
        run=_build_run(
            plan,
            records,
            target=target,
            created_at=started_at,
            run_id=run_id,
            tool_version=tool_version,
            interrupted=counter.interrupted,
        ),
        cases=records,
    )


async def _one_unit(
    plan: RunPlan,
    case: Case,
    repeat: int,
    *,
    target: Target,
    store: Cache,
    retries: RetryPolicy,
    sleep: Sleep,
    entropy: Random,
    target_digest: str,
    stopping: Callable[[], bool],
    keep_prompts: bool,
) -> CaseRecord:
    try:
        rendered = plan.renderer.pair(case) if plan.arity == "pair" else plan.renderer.single(case)
    except CaseRenderFailed as exc:
        # The template compiled, so this is the case's fault and not the suite's: it lacks a field
        # other cases have. Ten of these in a row still ends the run.
        return CaseRecord(
            case_id=case.id,
            repeat=repeat,
            answers=(),
            scores={
                metric.id: Score.unscoreable(f"the case could not be rendered: {exc}")
                for metric in plan.metrics
            },
        )

    answers = [
        await _answer(
            each,
            repeat,
            target=target,
            store=store,
            retries=retries,
            sleep=sleep,
            entropy=entropy,
            target_digest=target_digest,
            timeout_s=plan.config.timeout_s,
            stopping=stopping,
            keep_prompts=keep_prompts,
        )
        for each in rendered
    ]
    return CaseRecord(
        case_id=case.id,
        repeat=repeat,
        answers=tuple(answers),
        scores=_score(plan, case, answers),
    )


async def _answer(
    rendered: RenderedInput,
    repeat: int,
    *,
    target: Target,
    store: Cache,
    retries: RetryPolicy,
    sleep: Sleep,
    entropy: Random,
    target_digest: str,
    timeout_s: int,
    stopping: Callable[[], bool],
    keep_prompts: bool,
) -> AnswerRecord:
    key = cache_key(target_digest=target_digest, input_sha256=rendered.sha256, repeat=repeat)
    hit = await asyncio.to_thread(store.get, key)
    if hit is not None:
        return AnswerRecord(
            case_id=rendered.case_id,
            repeat=repeat,
            variant=rendered.variant,
            input_sha256=rendered.sha256,
            output=hit,
            cached=True,
            attempts=0,
            prompt=rendered.prompt if keep_prompts else None,
        )

    output, attempts = await _call_with_retries(
        rendered,
        target=target,
        retries=retries,
        sleep=sleep,
        entropy=entropy,
        timeout_s=timeout_s,
        stopping=stopping,
    )
    await asyncio.to_thread(store.put, key, output)
    return AnswerRecord(
        case_id=rendered.case_id,
        repeat=repeat,
        variant=rendered.variant,
        input_sha256=rendered.sha256,
        output=output,
        cached=False,
        attempts=attempts,
        prompt=rendered.prompt if keep_prompts else None,
    )


async def _call_with_retries(
    rendered: RenderedInput,
    *,
    target: Target,
    retries: RetryPolicy,
    sleep: Sleep,
    entropy: Random,
    timeout_s: int,
    stopping: Callable[[], bool],
) -> tuple[TargetOutput, int]:
    """Call the target, retrying what the adapter said was worth retrying.

    An adapter never retries, so the attempt count ends up in the run file. A `TargetConfigError`
    is not caught here: it is the same fault for every case and ends the run.
    """
    case = _rendered_case(rendered)
    last: RetryableTargetError | None = None

    spent = 0
    for attempt in range(1, retries.attempts + 1):
        spent = attempt
        try:
            async with asyncio.timeout(timeout_s):
                return await target.invoke(case), attempt
        except RetryableTargetError as exc:
            last = exc
        except TimeoutError:
            last = _TimedOut(f"no answer within {timeout_s}s")

        if attempt == retries.attempts or stopping():
            # Nothing is gained by paying for another attempt once the run is stopping.
            break
        wait = retries.wait_for(
            attempt,
            jitter=entropy.random(),
            retry_after_s=getattr(last, "retry_after_s", None),
        )
        await sleep(wait)

    return (
        TargetOutput(
            latency_ms=0,
            error=TargetError(
                kind="timeout" if isinstance(last, _TimedOut) else _kind_of(last),
                # Redacted here as well as at each adapter's raise site. This is the one point
                # every adapter passes through, including one installed from another package, and
                # the message reaches the cases file, which is published surface.
                message=redact(str(last)) if last else "the target did not answer",
                attempts=spent,
            ),
        ),
        spent,
    )


class _TimedOut(RetryableTargetError):
    """The engine's own per-attempt timeout, as distinct from the adapter's."""


def _first_leaf(grouped: BaseExceptionGroup[BaseException]) -> BaseException:
    """The first exception inside a possibly nested group."""
    for contained in grouped.exceptions:
        if isinstance(contained, BaseExceptionGroup):
            return _first_leaf(contained)
        return contained
    return grouped


def _kind_of(error: RetryableTargetError | None) -> Any:
    return "rate_limited" if isinstance(error, TargetRateLimited) else "unavailable"


def _rendered_case(rendered: RenderedInput) -> RenderedCase:
    return RenderedCase(
        case_id=rendered.case_id,
        prompt=rendered.prompt,
        expects_json=rendered.expects_json,
    )


def _score(plan: RunPlan, case: Case, answers: Sequence[AnswerRecord]) -> dict[str, Score]:
    """Score a case's answers.

    No special casing for a failed answer: a scorer is handed the `TargetOutput` it got, and every
    built-in declines a case it cannot read. That is what keeps the decision about an unscoreable
    case in one place.
    """
    scores: dict[str, Score] = {}
    for metric in plan.metrics:
        if plan.arity == "pair":
            if len(answers) != 2:
                scores[metric.id] = Score.unscoreable("a pair needs two answers")
                continue
            payload: Any = PairOutputs(a=answers[0].output, b=answers[1].output)
        else:
            payload = answers[0].output
        scores[metric.id] = metric.spec.call(case, payload, metric.options)
    return scores


def _observations(plan: RunPlan, records: Sequence[CaseRecord]) -> list[MetricObservations]:
    """Group per-repeat scores back into per-case observations.

    A case counts as an error only when no repeat of it scored. A case where one repeat scored and
    another did not contributes the repeats that did: dropping the case entirely would throw away
    a measurement that was taken.
    """
    observations: list[MetricObservations] = []
    for metric in plan.metrics:
        # A judge-scored metric needs two repeats of a case to say anything about its own
        # variance, so a case that lost one of them is an error rather than half a measurement.
        # Passing it on raised out of aggregation and threw away the whole paid run.
        minimum = 1 if metric.spec.deterministic else 2
        values: dict[str, list[float]] = {}
        groups: dict[str, Mapping[str, str]] = {}
        for record in records:
            score = record.scores.get(metric.id)
            if score is None or score.value is None:
                values.setdefault(record.case_id, [])
                continue
            values.setdefault(record.case_id, []).append(score.value)

        for case in plan.cases:
            if case.id in values:
                groups[case.id] = {
                    field: str(case.get(field)) for field in metric.group_by if field in case.data
                }

        cases = tuple(
            CaseObservation(case_id=case_id, scores=tuple(scored), groups=groups.get(case_id, {}))
            for case_id, scored in sorted(values.items())
            if len(scored) >= minimum
        )
        observations.append(
            MetricObservations(
                metric_id=metric.id,
                deterministic=metric.spec.deterministic,
                cases=cases,
                n_errors=sum(1 for scored in values.values() if len(scored) < minimum),
                threshold=metric.threshold,
                group_by=metric.group_by,
            )
        )
    return observations


def _build_run(
    plan: RunPlan,
    records: Sequence[CaseRecord],
    *,
    target: Target,
    created_at: datetime,
    run_id: str,
    tool_version: str,
    interrupted: bool,
) -> RunResult:
    summary, by_group = aggregate(
        _observations(plan, records),
        seed=plan.config.seed,
        resamples=plan.config.bootstrap_resamples,
    )
    answers = [answer for record in records for answer in record.answers]
    manifest = plan.manifest

    return RunResult(
        lakmus_format="0.1",
        run_id=run_id,
        status="interrupted" if interrupted else "complete",
        created_at=created_at,
        tool=ToolInfo(name="lakmus", version=tool_version, python=_python_version()),
        suite=SuiteRef(
            id=manifest.id,
            version=manifest.version,
            inputs_sha256=plan.suite.inputs_sha256,
            content_sha256=plan.suite.content_sha256,
        ),
        # Asked for now, after the calls: a provider names the version it served only in a
        # response, so a fingerprint taken at setup would be missing it.
        target=target.fingerprint(),
        config=plan.config,
        summary=summary,
        by_group=by_group,
        latency_ms=_latency(answers),
        cost=_cost(answers),
    )


def _latency(answers: Sequence[AnswerRecord]) -> Latency:
    """Percentiles by the same interpolation rule as a confidence interval, to the millisecond."""
    measured = sorted(float(answer.output.latency_ms) for answer in answers if not answer.cached)
    if not measured:
        # Every answer came from the cache, so this run measured nothing. Zero would read as
        # instantaneous, which is a number a reader would quote.
        return Latency()
    return Latency(p50=round(percentile(measured, 50.0)), p95=round(percentile(measured, 95.0)))


def _cost(answers: Sequence[AnswerRecord]) -> Cost:
    """What the run cost, with the provenance of the price that says so.

    A call with no price contributes nothing rather than zero, and a run with no priced call has
    no figure at all — which is different from costing nothing.
    """
    # Only what this run paid for. A cached answer cost money once, in another run, and counting
    # it here would report a spend that did not happen.
    fresh = [answer for answer in answers if not answer.cached]
    priced = [answer.output for answer in fresh if answer.output.cost_usd is not None]
    tokens_in = sum(a.output.usage.tokens_in for a in fresh if a.output.usage is not None)
    tokens_out = sum(a.output.usage.tokens_out for a in fresh if a.output.usage is not None)
    if not priced:
        return Cost(tokens_in=tokens_in, tokens_out=tokens_out)

    price: ModelPrice | None = next((o.price for o in priced if o.price is not None), None)
    if price is None:
        return Cost(tokens_in=tokens_in, tokens_out=tokens_out)
    return Cost(
        usd=sum(output.cost_usd or 0.0 for output in priced),
        pricing_as_of=price.as_of,
        pricing_source=price.source,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def _python_version() -> str:
    import sys

    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
