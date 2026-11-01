"""The `run.json` model, format 0.1.

A run file has to let someone replay a result, compare it with another, and judge how much to
trust it, years after the run. So the models are strict in both directions: no unknown field gets
through, and a few invariants are enforced rather than hoped for — a fingerprint cannot claim a
digest it does not have, and it cannot carry a credential.
"""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

FORMAT_VERSION = "0.1"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Ulid = Annotated[str, StringConstraints(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")]

ParameterValue = float | int | str | bool


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", regex_engine="python-re")


class ToolInfo(_Strict):
    name: str
    version: str
    python: str


class SuiteRef(_Strict):
    """Which suite produced this, and the two digests ADR 0005 defined."""

    id: str
    version: str
    inputs_sha256: Sha256
    content_sha256: Sha256


def sanitise_identity(identity: str) -> str:
    """Strip anything from a target identity that could carry a secret.

    A fingerprint is published. An endpoint URL arrives with userinfo, a query string or both, and
    either is a plausible place for a token, so neither survives into the record.
    """
    parts = urlsplit(identity)
    if not parts.scheme or not parts.netloc:
        return identity
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}{parts.path}"


def fingerprint_digest(
    *,
    adapter: str,
    kind: str,
    identity: str,
    declared_version: str | None,
    parameters: dict[str, ParameterValue],
) -> str:
    """SHA-256 over the canonical JSON of what identifies a target."""
    canonical = json.dumps(
        {
            "adapter": adapter,
            "kind": kind,
            "identity": identity,
            "declared_version": declared_version,
            "parameters": parameters,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TargetFingerprint(_Strict):
    """What was measured, in enough detail to say whether two runs measured the same thing.

    Lives here rather than in `targets` because it is part of this file's format: an adapter
    imports it to report itself, which is the one dependency that direction.
    """

    adapter: str
    kind: Literal["model", "agent", "mcp"]
    identity: str
    declared_version: str | None = None
    parameters: dict[str, ParameterValue] = {}
    sha256: Sha256

    @classmethod
    def create(
        cls,
        *,
        adapter: str,
        kind: Literal["model", "agent", "mcp"],
        identity: str,
        declared_version: str | None = None,
        parameters: dict[str, ParameterValue] | None = None,
    ) -> Self:
        """Build a fingerprint, sanitising the identity and computing the digest."""
        safe_identity = sanitise_identity(identity)
        resolved = dict(parameters or {})
        return cls(
            adapter=adapter,
            kind=kind,
            identity=safe_identity,
            declared_version=declared_version,
            parameters=resolved,
            sha256=fingerprint_digest(
                adapter=adapter,
                kind=kind,
                identity=safe_identity,
                declared_version=declared_version,
                parameters=resolved,
            ),
        )

    @model_validator(mode="after")
    def _digest_and_identity_hold(self) -> Self:
        if self.identity != sanitise_identity(self.identity):
            raise ValueError(
                "a target identity must carry no userinfo and no query string: a fingerprint is "
                "published, and either is a plausible place for a credential"
            )
        expected = fingerprint_digest(
            adapter=self.adapter,
            kind=self.kind,
            identity=self.identity,
            declared_version=self.declared_version,
            parameters=self.parameters,
        )
        if self.sha256 != expected:
            raise ValueError("sha256 does not match the fingerprint it claims to digest")
        return self


class RunConfig(_Strict):
    """The values a run actually used, and which of them the invocation replaced."""

    repeats: Annotated[int, Field(gt=0)]
    temperature: Annotated[float, Field(ge=0.0, le=2.0)]
    seed: int
    timeout_s: Annotated[int, Field(gt=0)]
    concurrency: Annotated[int, Field(gt=0)]
    overrides: tuple[str, ...] = ()
    """Manifest fields this invocation replaced. Compared field by field when two runs are checked
    for comparability, so the order is canonical."""

    @model_validator(mode="after")
    def _overrides_are_canonical(self) -> Self:
        if list(self.overrides) != sorted(set(self.overrides)):
            raise ValueError("overrides must be sorted and free of duplicates")
        return self


class ThresholdApplied(_Strict):
    """The bounds this run applied, which an override may have moved."""

    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _at_least_one_bound(self) -> Self:
        if self.min is None and self.max is None:
            raise ValueError("a threshold needs min, max, or both")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


class _Measured(_Strict):
    value: float | None
    ci95: tuple[float, float] | None = None

    @model_validator(mode="after")
    def _interval_is_ordered(self) -> Self:
        if self.ci95 is not None and self.ci95[0] > self.ci95[1]:
            raise ValueError("ci95 must be (low, high)")
        return self


class GroupSummary(_Measured):
    n_cases: Annotated[int, Field(ge=0)]


class MetricSummary(_Measured):
    """One metric over a whole run.

    `value` is `None` when no case produced a score. A metric over zero successful cases has no
    value, and recording 0.0 would be a measurement nobody made.
    """

    ci_unavailable_reason: str | None = None
    n_cases: Annotated[int, Field(ge=0)]
    n_observations: Annotated[int, Field(ge=0)]
    n_errors: Annotated[int, Field(ge=0)]
    """Cases whose target call or scorer failed. Separate from `n_cases` so that a result cannot
    quietly be an average over the subset that happened to work."""
    deterministic: bool
    threshold: ThresholdApplied | None = None
    passed: bool | None = None
    conclusive: bool | None = None
    """False when the interval straddles the threshold: the run passed, and a rerun might not."""

    @model_validator(mode="after")
    def _absences_are_explained(self) -> Self:
        if (self.ci95 is None) == (self.ci_unavailable_reason is None):
            raise ValueError("exactly one of ci95 and ci_unavailable_reason must be set")
        if self.threshold is None and (self.passed is not None or self.conclusive is not None):
            raise ValueError("passed and conclusive need a threshold")
        return self


class Cost(_Strict):
    usd: float | None = None
    """`None` until a price table exists: a number whose provenance cannot be stated is worse than
    no number."""
    tokens_in: Annotated[int, Field(ge=0)] = 0
    tokens_out: Annotated[int, Field(ge=0)] = 0


class Latency(_Strict):
    p50: Annotated[int, Field(ge=0)]
    p95: Annotated[int, Field(ge=0)]


class RunResult(_Strict):
    """`run.json`."""

    lakmus_format: Literal["0.1"]
    run_id: Ulid
    created_at: datetime
    tool: ToolInfo
    suite: SuiteRef
    target: TargetFingerprint
    config: RunConfig
    summary: dict[str, MetricSummary]
    latency_ms: Latency
    by_group: dict[str, dict[str, dict[str, GroupSummary]]] = {}
    """metric, then group field, then group value. One field at a time, not the cross-product of a
    metric's `group_by`: the product explodes, and the format reads as "broken down by each of
    these"."""
    cost: Cost = Cost()
    cases: str = "cases.jsonl.gz"
    signature: None = None
    """Reserved. Local-first execution means a published result is self-reported in 0.1."""

    @model_validator(mode="after")
    def _timestamp_is_utc(self) -> Self:
        offset = self.created_at.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("created_at must be an aware datetime in UTC")
        return self

    @model_validator(mode="after")
    def _groups_belong_to_declared_metrics(self) -> Self:
        unknown = sorted(set(self.by_group) - set(self.summary))
        if unknown:
            raise ValueError(f"by_group names metrics absent from summary: {', '.join(unknown)}")
        return self

    def model_dump_json_document(self) -> dict[str, Any]:
        """The mapping that gets written to disk."""
        return self.model_dump(mode="json", by_alias=True)
