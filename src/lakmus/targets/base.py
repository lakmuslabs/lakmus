"""The Target protocol, what a call to one returns, and how a failure is classified.

Two rules shape everything here, and an adapter should never have to decide either again.

An adapter **raises** when a failure will affect every case — a missing key, an unknown model, an
endpoint that does not resolve — and when a failure is worth retrying. Retries, backoff and
concurrency belong to the engine, so an adapter that retried would hide the attempt count from the
run file.

An adapter **returns** `TargetOutput(error=...)` when the failure belongs to this case: content
too long, content the provider refused, a response that does not parse. One bad case must not end
a run of five thousand.
"""

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lakmus.errors import (
    TargetConfigError,
    TargetRateLimited,
    TargetUnavailable,
)
from lakmus.results.models import ParameterValue, TargetFingerprint
from lakmus.targets.pricing import ModelPrice, PricingTable

TargetKind = Literal["model", "agent", "mcp"]

_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})
_CONFIG_STATUSES = frozenset({401, 403, 404})
_CASE_STATUSES = frozenset({400, 413, 422})

_CONFIG_FAULT = re.compile(
    r"model[_ ]not[_ ]found|unknown model|does not exist"
    r"|invalid[_ ]api[_ ]key|incorrect api key|no such model",
    re.IGNORECASE,
)
"""Markers that turn an otherwise case-level 400 into a configuration fault.

Matched against the provider's own error text, never the whole body: providers echo the request,
and a case whose prompt happens to contain "does not exist" would otherwise end the run.
"""

_PROVIDER_ERROR_KEYS = ("message", "code", "type", "detail", "error_description")

_CREDENTIAL_STRONG = (
    r"authorization|x-api-key|api[_-]?key|apikey"
    r"|access[_-]?token|refresh[_-]?token|bearer"
)
"""Names that are a credential wherever they appear, separator or not."""

_CREDENTIAL_WEAK = r"password|passwd|secret|token"
"""Names that are a credential only when something is assigned to them. A provider message about
a token budget must survive intact."""

_SCHEME = r"(?:bearer|basic|token|digest)\s+"

_JSON_SECRET = re.compile(
    rf'(?i)("(?:{_CREDENTIAL_STRONG}|{_CREDENTIAL_WEAK})"\s*:\s*)"[^"]*"',
)
_STRONG_PAIR = re.compile(
    rf"(?i)\b({_CREDENTIAL_STRONG})\b\s*[:=]?\s*(?:{_SCHEME})?[^\s,;\"}}\]]+",
)
_WEAK_PAIR = re.compile(
    rf"(?i)\b({_CREDENTIAL_WEAK})\b\s*[:=]\s*(?:{_SCHEME})?[^\s,;\"}}\]]+",
)
_PREFIXED_SECRET = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{8,}")

_MAX_MESSAGE = 500


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Usage(_Strict):
    """What the provider says the call consumed. Absent when it says nothing."""

    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)


class Step(_Strict):
    """One step of an agent or MCP interaction.

    Minimal on purpose: nothing in this milestone fills a trace, and designing a trace format for
    callers that do not exist yet would be designing it twice. The milestone that adds the agent
    adapter settles its real shape.
    """

    name: str
    latency_ms: int = Field(ge=0)
    content: str | None = None


class TargetError(_Strict):
    """A failure recorded against one case rather than raised.

    A record, not an exception: it is written into the run and an exception does not serialise.
    `kind` names the same failures the exceptions in `lakmus.errors` do, as a closed set.
    """

    kind: Literal[
        "rate_limited",
        "unavailable",
        "bad_request",
        "timeout",
        "invalid_response",
        "refused",
    ]
    message: str
    """Redacted. Never a header, a key, or a full request body."""
    status_code: int | None = None
    attempts: int = Field(default=1, ge=1)
    """How many times the engine tried before this was recorded."""


class TargetOutput(_Strict):
    """What one call to a target produced."""

    content: str | dict[str, Any] | None = None
    latency_ms: int = Field(ge=0)
    trace: tuple[Step, ...] = ()
    usage: Usage | None = None
    cost_usd: float | None = None
    price: ModelPrice | None = None
    """The price that produced `cost_usd`. Carried so that the run file can record the date the
    figure was true on without re-deriving which entry was used."""
    error: TargetError | None = None

    @model_validator(mode="after")
    def _a_success_has_content(self) -> Self:
        if self.error is None and self.content is None:
            raise ValueError("a call that did not fail must have content")
        return self


class RenderedCase(_Strict):
    """One case, already turned into a prompt by the engine.

    An adapter never sees a manifest, a template or a scorer: it is handed text and asked for an
    answer, which is what keeps an adapter testable without a suite.
    """

    case_id: str
    repeat: int = Field(default=0, ge=0)
    prompt: str
    expects_json: bool = False
    """The suite declared a JSON output. Enforced by the provider where it can be, and advisory
    where it cannot."""


class TargetConfig(_Strict):
    """Everything an adapter needs to set itself up.

    `api_key_env` names the variable to read. The value never appears here, in a log, in a
    fingerprint or in an error message.
    """

    identity: str
    model: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    parameters: dict[str, ParameterValue] = {}
    timeout_s: int = Field(default=60, gt=0)
    pricing: PricingTable | None = None
    """Absent means costs are not computed, which is different from costing zero."""


class Target(Protocol):
    """A thing under test: a model, an agent, or an MCP server.

    `adapter` and `kind` are class variables because they describe the adapter, not the instance.
    Declaring them as instance attributes here would make every real adapter fail the protocol,
    since none of them lets an instance change which adapter it is.
    """

    adapter: ClassVar[str]
    kind: ClassVar[TargetKind]

    async def setup(self, config: TargetConfig) -> None: ...
    async def invoke(self, case: RenderedCase) -> TargetOutput: ...
    async def teardown(self) -> None: ...
    def fingerprint(self) -> TargetFingerprint: ...


def redact(text: str, *, limit: int = _MAX_MESSAGE) -> str:
    """Shorten a provider message and strip anything that looks like a credential.

    A provider error body is quoted into the run file, and bodies echo the request — headers
    included. Three shapes are covered, because a provider will use any of them: a JSON string
    value, a header or assignment, and a key recognisable by its own prefix. An authentication
    scheme (`Bearer`, `Basic`) is skipped rather than mistaken for the secret after it.
    """
    collapsed = " ".join(text.split())
    collapsed = _JSON_SECRET.sub(r'\1"[redacted]"', collapsed)
    collapsed = _STRONG_PAIR.sub(r"\1 [redacted]", collapsed)
    collapsed = _WEAK_PAIR.sub(r"\1 [redacted]", collapsed)
    collapsed = _PREFIXED_SECRET.sub("[redacted]", collapsed)
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit]}..."


def provider_message(body: str) -> str:
    """The provider's own error text, without the request it may have echoed.

    A body is usually `{"error": {"message": ...}}`. Anything that is not JSON is returned whole,
    since there is no structure to separate the provider's words from a quoted prompt.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        return body
    if not isinstance(payload, dict):
        return body

    error = payload.get("error")
    if isinstance(error, str):
        return error
    candidates = error if isinstance(error, dict) else payload
    parts = [
        str(candidates[key]) for key in _PROVIDER_ERROR_KEYS if isinstance(candidates.get(key), str)
    ]
    return " ".join(parts) if parts else ""


def retry_after_seconds(headers: Mapping[str, str], *, now: datetime | None = None) -> float | None:
    """Parse `Retry-After`, in either of its two forms.

    The HTTP-date form needs the current time to become a duration. That is a clock read, and it
    is allowed here for the same reason a measured latency is: it affects how long the engine
    waits, never a digest, a verdict or an interval.
    """
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        pass
    try:
        deadline = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    return max(0.0, (deadline - reference).total_seconds())


def raise_or_describe(
    *,
    status_code: int,
    body: str,
    headers: Mapping[str, str] | None = None,
    adapter: str,
) -> TargetError:
    """Classify an HTTP failure: raise for the run, return a record for the case.

    The one place the mapping lives, so that four adapters cannot disagree about what a 429 means.
    """
    headers = headers or {}
    message = redact(body)

    if 300 <= status_code < 400:
        location = (headers.get("location") or headers.get("Location") or "unknown").strip()
        raise TargetConfigError(
            f"{adapter}: HTTP {status_code}: the endpoint redirects to {location!r}. "
            f"Point the target at the final URL; redirects are not followed, because a redirect "
            f"can move a request carrying a key to another host."
        )

    if status_code in _CONFIG_STATUSES:
        raise TargetConfigError(f"{adapter}: HTTP {status_code}: {message}")

    if status_code == 429:
        raise TargetRateLimited(
            f"{adapter}: rate limited: {message}",
            retry_after_s=retry_after_seconds(headers),
        )

    if status_code in _RETRYABLE_STATUSES or status_code >= 500:
        raise TargetUnavailable(f"{adapter}: HTTP {status_code}: {message}")

    if status_code in _CASE_STATUSES:
        if names_a_configuration_fault(body):
            raise TargetConfigError(f"{adapter}: HTTP {status_code}: {message}")
        return TargetError(kind="bad_request", message=message, status_code=status_code)

    # Anything else in the 4xx range is this case's problem until a provider proves otherwise.
    return TargetError(kind="bad_request", message=message, status_code=status_code)


def names_a_configuration_fault(body: str) -> bool:
    """Whether a 4xx body says the fault is the configuration rather than this case.

    Read from the provider's own error text only. Searching the whole body let a case prompt
    containing "does not exist" end a run of thousands.
    """
    return bool(_CONFIG_FAULT.search(provider_message(body)))
