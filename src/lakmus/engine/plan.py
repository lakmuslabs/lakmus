"""Deciding the shape of a run before any of it is paid for.

Everything here is cheap and offline: reading the cases, compiling the template, agreeing the
arity, resolving the effective configuration. A fault found at this stage costs nothing, which is
the point of doing it all before the first call.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lakmus.engine.render import Renderer
from lakmus.errors import TargetConfigError
from lakmus.results.models import RunConfig, ThresholdApplied
from lakmus.results.statistics import RESAMPLES, sample_indices
from lakmus.scorers import Arity, Case, ScorerSpec
from lakmus.suite.loader import Suite
from lakmus.suite.models import Manifest
from lakmus.targets.base import Target, TargetConfig

DEFAULT_CONCURRENCY = 8

_OVERRIDABLE = ("repeats", "temperature", "seed", "timeout_s")


@dataclass(frozen=True, slots=True)
class PlannedMetric:
    """One metric, with the scorer it resolved to and the options it was given."""

    id: str
    spec: ScorerSpec
    options: Mapping[str, Any]
    group_by: tuple[str, ...]
    threshold: ThresholdApplied | None


@dataclass(frozen=True, slots=True)
class RunPlan:
    suite: Suite
    arity: Arity
    metrics: tuple[PlannedMetric, ...]
    cases: tuple[Case, ...]
    config: RunConfig
    renderer: Renderer

    @property
    def manifest(self) -> Manifest:
        return self.suite.manifest

    def target_config(self, *, identity: str, model: str | None, **extra: Any) -> TargetConfig:
        """The adapter's configuration, with the run's effective values folded in."""
        return TargetConfig(
            identity=identity,
            model=model,
            timeout_s=self.config.timeout_s,
            parameters={
                "temperature": self.config.temperature,
                "seed": self.config.seed,
                **extra.pop("parameters", {}),
            },
            **extra,
        )


def plan(
    suite: Suite,
    *,
    specs: Mapping[str, ScorerSpec],
    overrides: Mapping[str, Any] | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    bootstrap_resamples: int = RESAMPLES,
) -> RunPlan:
    """Turn a validated suite into a run that can be executed, or refuse it."""
    manifest = suite.manifest
    missing = sorted(metric.id for metric in manifest.metrics if metric.id not in specs)
    if missing:
        raise TargetConfigError(
            f"no scorer resolved for {', '.join(missing)}. A suite scorer needs the consent to "
            f"import it; pass --yes."
        )

    arities = {specs[metric.id].arity for metric in manifest.metrics}
    if len(arities) != 1:
        raise TargetConfigError(
            "a suite is either a pair suite or it is not; this one mixes arities. "
            "`lakmus validate` reports it as LKM-505."
        )
    arity = arities.pop()

    config = _effective_config(
        manifest, overrides or {}, concurrency=concurrency, bootstrap_resamples=bootstrap_resamples
    )
    judged = sorted(metric.id for metric in manifest.metrics if not specs[metric.id].deterministic)
    if judged and config.repeats < 2:
        raise TargetConfigError(
            f"{', '.join(judged)} use a non-deterministic scorer, so repeats must be at least 2. "
            f"`lakmus validate` reports it as LKM-506."
        )

    if manifest.target.input is None:
        raise TargetConfigError("this version runs model targets, which need target.input")

    return RunPlan(
        suite=suite,
        arity=arity,
        metrics=tuple(
            PlannedMetric(
                id=metric.id,
                spec=specs[metric.id],
                options=dict(metric.options),
                group_by=tuple(metric.group_by),
                threshold=_threshold(manifest, metric.id),
            )
            for metric in manifest.metrics
        ),
        cases=read_cases(suite.root, manifest, seed=config.seed),
        config=config,
        renderer=Renderer(
            (suite.root / manifest.target.input).read_text(encoding="utf-8"),
            expects_json=manifest.target.output.type == "json",
        ),
    )


def check_target(plan_: RunPlan, target: Target) -> None:
    """Refuse a target of the wrong kind before the first call.

    A manifest that declares a model and a command line that names an MCP server would otherwise
    produce a run that measured something the suite never described.
    """
    if target.kind != plan_.manifest.target.kind:
        raise TargetConfigError(
            f"the suite declares a {plan_.manifest.target.kind} target and "
            f"{target.adapter!r} is a {target.kind} adapter"
        )


def read_cases(root: Path, manifest: Manifest, *, seed: int) -> tuple[Case, ...]:
    """Every case, or a reproducible sample of them.

    The loader has already refused a malformed line, so a line that fails here is skipped rather
    than guessed at: the alternative is a run whose case count nobody can explain.
    """
    cases: list[Case] = []
    source = root / manifest.cases.source
    for line in source.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            cases.append(Case(id=data["id"], data=data))

    if manifest.cases.sample is None:
        return tuple(cases)
    chosen = sample_indices(len(cases), manifest.cases.sample, seed=seed)
    return tuple(cases[index] for index in chosen)


def _threshold(manifest: Manifest, metric_id: str) -> ThresholdApplied | None:
    declared = manifest.thresholds.get(metric_id)
    if declared is None:
        return None
    return ThresholdApplied(min=declared.min, max=declared.max)


def _effective_config(
    manifest: Manifest,
    overrides: Mapping[str, Any],
    *,
    concurrency: int,
    bootstrap_resamples: int,
) -> RunConfig:
    unknown = sorted(set(overrides) - set(_OVERRIDABLE))
    if unknown:
        raise TargetConfigError(
            f"nothing to override called {', '.join(unknown)}; "
            f"overridable: {', '.join(_OVERRIDABLE)}"
        )
    values = {field: getattr(manifest.run, field) for field in _OVERRIDABLE}
    values.update(overrides)
    return RunConfig(
        **values,
        concurrency=concurrency,
        bootstrap_resamples=bootstrap_resamples,
        overrides=dict(sorted(overrides.items())),
    )
