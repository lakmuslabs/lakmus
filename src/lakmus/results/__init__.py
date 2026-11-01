"""The run.json model: aggregation, confidence intervals, and threshold verdicts."""

from lakmus.results.aggregate import CaseObservation, MetricObservations, aggregate
from lakmus.results.comparison import comparable
from lakmus.results.models import (
    FORMAT_VERSION,
    Cost,
    GroupSummary,
    Latency,
    MetricSummary,
    RunConfig,
    RunResult,
    SuiteRef,
    TargetFingerprint,
    ThresholdApplied,
    ToolInfo,
)

__all__ = [
    "FORMAT_VERSION",
    "CaseObservation",
    "Cost",
    "GroupSummary",
    "Latency",
    "MetricObservations",
    "MetricSummary",
    "RunConfig",
    "RunResult",
    "SuiteRef",
    "TargetFingerprint",
    "ThresholdApplied",
    "ToolInfo",
    "aggregate",
    "comparable",
]
