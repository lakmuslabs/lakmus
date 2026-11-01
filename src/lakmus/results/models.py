"""The `run.json` model, format 0.1.

A run file has to let someone replay a result, compare it with another, and judge how much to
trust it, years after the run. So the models are strict in both directions: no unknown field gets
through, and a few invariants are enforced rather than hoped for — a fingerprint cannot claim a
digest it does not have, and it cannot carry a credential.
"""

import hashlib
import json
import math
import re
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

FORMAT_VERSION = "0.1"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Ulid = Annotated[str, StringConstraints(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")]

ParameterValue = float | int | str | bool


class _Strict(BaseModel):
    # allow_inf_nan=False because this model is written to a file: `json.dumps` spells a NaN as the
    # bare token `NaN`, which no JSON parser accepts, and the published schema types these as
    # numbers. A scorer that returns one is a scorer error, not a result.
    model_config = ConfigDict(
        frozen=True, extra="forbid", regex_engine="python-re", allow_inf_nan=False
    )


_SECRET_NAME = re.compile(
    r"api[-_]?key|secret|password|passwd|authorization|bearer"
    r"|access[-_]?token|refresh[-_]?token",
    re.IGNORECASE,
)
"""Names that have no business in a published record. Deliberately narrow: `max_tokens` is a
generation parameter and must keep working."""

_SECRET_ASSIGNMENT = re.compile(rf"(?:{_SECRET_NAME.pattern})\s*[=:]", re.IGNORECASE)
"""The same names as an assignment, for an identity that is a command line rather than a URL —
`mcp-stdio:"python server.py --api-key=..."` is syntax the architecture names."""


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
    """Reduce a target identity to its canonical, publishable form.

    A fingerprint is published, and an endpoint URL arrives with userinfo, a query string or an
    upper-case scheme. Userinfo and the query do not survive — both are plausible places for a
    token — and the scheme and host are lower-cased, so two spellings of one endpoint do not
    digest differently.

    The fragment is kept, which userinfo and the query are not. It is how a target names the model
    behind an endpoint (`https://host/v1#llama-3`), so dropping it would let two different models
    share a fingerprint — a failure in the one field that decides whether two results describe the
    same thing. It is covered by the credential checks like the rest of the identity.

    What this cannot do is clean an identity that is not a URL — a command line, for instance.
    There is no safe general way to strip a secret out of one, so the model refuses such an
    identity instead of pretending to sanitise it.
    """
    parts = urlsplit(identity)
    if not parts.netloc:
        return identity
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    authority = f"{parts.scheme.lower()}://" if parts.scheme else "//"
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    return f"{authority}{host}{parts.path}{fragment}"


def names_a_credential(name: str) -> bool:
    """Whether a parameter name has no business in a published record.

    Shared so that the command line can refuse one before a run is paid for, rather than leaving
    the fingerprint to refuse it after.
    """
    return bool(_SECRET_NAME.search(name))


def canonical_parameter(value: ParameterValue) -> str:
    """One spelling per value, so that a digest is reproducible outside Python.

    `json.dumps` writes numbers the way CPython does: `1e+22` where other runtimes write `1e22`,
    and `0.0` where `0` means the same thing. A fingerprint digest is compared across
    implementations and used as a cache key, so every value becomes a string under this rule:
    booleans as `true`/`false`, a float with no fractional part as an integer, and an exponent
    with no plus sign and no leading zeroes.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"a parameter must be a finite number, got {value!r}")
        if value == int(value):
            return str(int(value))
        return _EXPONENT.sub(
            lambda match: "e" + ("" if match.group(1) == "+" else "-") + match.group(2),
            repr(value),
        )
    return value


_EXPONENT = re.compile(r"e([+-])0*(\d+)")


def cache_digest(fingerprint: "TargetFingerprint") -> str:
    """The fingerprint digest a cache key uses: everything except `declared_version`.

    A provider reveals the version it served only in a response, so the full digest changes after
    the first call. Keying the cache on it would make every entry written before that response
    unreusable, which is the opposite of what a cache is for.
    """
    return fingerprint_digest(
        adapter=fingerprint.adapter,
        kind=fingerprint.kind,
        identity=fingerprint.identity,
        declared_version=None,
        parameters=dict(fingerprint.parameters),
    )


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
            "parameters": {name: canonical_parameter(v) for name, v in parameters.items()},
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
    def _carries_no_credential(self) -> Self:
        parts = urlsplit(self.identity)
        if parts.username or parts.password or parts.query:
            raise ValueError(
                "a target identity must carry no userinfo and no query string: a fingerprint is "
                "published, and either is a plausible place for a credential"
            )
        if _SECRET_ASSIGNMENT.search(self.identity):
            raise ValueError(
                "a target identity must not assign a credential; pass it through an environment "
                "variable named in the configuration instead"
            )
        named = sorted(name for name in self.parameters if _SECRET_NAME.search(name))
        if named:
            raise ValueError(
                "a fingerprint is published, so it carries no credential: remove "
                + ", ".join(named)
            )
        return self

    @model_validator(mode="after")
    def _identity_is_canonical(self) -> Self:
        canonical = sanitise_identity(self.identity)
        if self.identity != canonical:
            raise ValueError(
                f"a target identity must be in canonical form: {canonical!r}, not "
                f"{self.identity!r}. Two spellings of one endpoint would digest differently."
            )
        return self

    @model_validator(mode="after")
    def _digest_holds(self) -> Self:
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
    """Bounded concurrency the run used. Provenance for the latencies, not part of
    comparability: it cannot change a measurement."""
    bootstrap_resamples: Annotated[int, Field(gt=0)] = 1000
    """Recorded because an interval is only recomputable from the seed when the resample count is
    known too."""
    overrides: dict[str, float | int | str | bool] = {}
    """Manifest fields this invocation replaced, **and what it replaced them with**.

    The names alone are not enough: two runs overridden to different temperatures would have
    satisfied `comparable()`, which is the exact case this field exists to catch.
    """


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
        if (self.threshold is None) != (self.passed is None):
            raise ValueError("passed is set exactly when a threshold is declared")
        if self.threshold is None and self.conclusive is not None:
            raise ValueError("conclusive needs a threshold")
        return self


class Cost(_Strict):
    usd: float | None = None
    """`None` when the model is absent from the price table. Never an estimate: a number whose
    provenance cannot be stated is worse than no number."""
    pricing_as_of: date | None = None
    """The date the price was true on. A price changes under an unchanged model name, so a figure
    without this is not reproducible."""
    pricing_source: str | None = None
    tokens_in: Annotated[int, Field(ge=0)] = 0
    tokens_out: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def _a_figure_carries_its_provenance(self) -> Self:
        if self.usd is None:
            if self.pricing_as_of is not None or self.pricing_source is not None:
                raise ValueError("pricing provenance without a cost")
        elif self.pricing_as_of is None or self.pricing_source is None:
            raise ValueError("a cost needs the date and the source of the price it used")
        return self


class Latency(_Strict):
    """Latency percentiles, by the same interpolation rule as a confidence interval, rounded to
    the nearest millisecond. Sub-millisecond precision would be noise from a network call.

    Both are `None` when the run measured nothing — every answer came from the cache. Reporting
    zero would say the run was instantaneous, which is a number a reader would quote.
    """

    p50: Annotated[int, Field(ge=0)] | None = None
    p95: Annotated[int, Field(ge=0)] | None = None


class RunResult(_Strict):
    """`run.json`."""

    lakmus_format: Literal["0.1"]
    run_id: Ulid
    status: Literal["complete", "interrupted"] = "complete"
    """An interrupted run is readable and inspectable; it is not comparable and not publishable.
    A partial result that could pass for a whole one is the worst artefact this tool could
    produce."""
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
