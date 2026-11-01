"""Building a run without a network: a suite on disk, a plan, and the engine over a fake target.

Shared by the engine tests and by the tests of what M6 writes, because an artefact test wants a
genuine `EngineResult` and not a fabricated one. A fabricated result is exactly the artefact whose
shape nobody is checking.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from random import Random
from typing import Any

import yaml

from fake_target import FakeTarget
from lakmus.engine import EngineResult, RunPlan, execute, plan
from lakmus.scorers import BUILTIN_SCORERS, ScorerSpec
from lakmus.suite.loader import MANIFEST_FILENAME, load

MOMENT = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
SINGLE_METRICS = [{"id": "accuracy", "scorer": "builtin:exact", "with": {"expected": "expected"}}]


async def never_sleeps(seconds: float) -> None:
    """The engine's waits are asserted on, never waited through."""
    return None


def single_suite(root: Path, *, cases: Sequence[Mapping[str, Any]] | None = None) -> Path:
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
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    (root / "scorers").mkdir(exist_ok=True)
    return root


def planned(root: Path, **options: Any) -> RunPlan:
    suite = load(root, run_suite_code=False, scorers=BUILTIN_SCORERS)
    specs: dict[str, ScorerSpec] = {
        metric.id: BUILTIN_SCORERS[metric.scorer.removeprefix("builtin:")]
        for metric in suite.manifest.metrics
    }
    return plan(suite, specs=specs, **options)


async def run_engine(
    root: Path,
    target: FakeTarget,
    *,
    clock: Callable[[], datetime] | None = None,
    **options: Any,
) -> EngineResult:
    run_plan = planned(root, **options.pop("plan", {}))
    await target.setup(run_plan.target_config(identity="scripted", model=None))
    try:
        return await execute(
            run_plan,
            target=target,
            clock=clock if clock is not None else (lambda: MOMENT),
            entropy=Random(42),
            sleep=never_sleeps,
            **options,
        )
    finally:
        await target.teardown()


def pair_suite(root: Path, *, cases: Sequence[Mapping[str, Any]] | None = None) -> Path:
    """A pair suite scored by a built-in, so that no suite code has to be imported."""
    manifest = yaml.safe_load((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    manifest["metrics"] = [
        {"id": "flip", "scorer": "builtin:pair_flip", "with": {"field": "decision"}}
    ]
    manifest["thresholds"] = {"flip": {"max": 0.1}}
    manifest["run"]["repeats"] = 1
    (root / MANIFEST_FILENAME).write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (root / "prompts" / "screener.j2").write_text("Candidature : {{ name }}\n", encoding="utf-8")
    rows = cases or [
        {"id": "p-0001", "variable": "nom", "a": {"name": "Amélie"}, "b": {"name": "Khadija"}}
    ]
    (root / "cases" / "pairs.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    return root
