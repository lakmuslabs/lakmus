"""The shared lifecycle of a model reached over HTTP.

Three of the four adapters in this milestone speak the same wire format, so the client lifecycle,
the status mapping, the timing and the cost lookup live here once. Anthropic does not share that
format and is written separately rather than bent into this shape.

A subclass supplies three things: how to build a request, how to read a response, and what its
defaults are. It never retries, never catches a retryable failure, and never logs a header.
"""

import json
import os
import time
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from lakmus.errors import TargetConfigError, TargetUnavailable
from lakmus.results.models import TargetFingerprint
from lakmus.targets.base import (
    RenderedCase,
    TargetConfig,
    TargetError,
    TargetKind,
    TargetOutput,
    Usage,
    raise_or_describe,
    redact,
)


@dataclass(frozen=True, slots=True)
class Reading:
    """What a subclass made of one successful response body."""

    text: str | None = None
    usage: Usage | None = None
    declared_version: str | None = None
    error: TargetError | None = None


class HttpModelTarget:
    """A model behind an HTTP endpoint."""

    adapter: ClassVar[str] = "http-model"
    kind: ClassVar[TargetKind] = "model"
    default_base_url: ClassVar[str | None] = None
    default_api_key_env: ClassVar[str | None] = None
    requires_api_key: ClassVar[bool] = True
    path: ClassVar[str] = "/chat/completions"

    def __init__(self) -> None:
        self._config: TargetConfig | None = None
        self._client: httpx.AsyncClient | None = None
        self._base_url = ""
        self._model = ""
        self._declared_version: str | None = None

    # --- lifecycle ---------------------------------------------------------------------------

    async def setup(self, config: TargetConfig) -> None:
        self._config = config
        self._base_url = self._resolve_base_url(config)
        self._model = self._resolve_model(config)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(float(config.timeout_s)),
            headers=self._headers(self._resolve_key(config)),
        )

    async def teardown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def fingerprint(self) -> TargetFingerprint:
        """What was measured, as far as the provider has disclosed it.

        `declared_version` is whatever the response echoed, so it is empty until a call has come
        back. The engine records the fingerprint at the end of a run for that reason.
        """
        config = self._require_config()
        return TargetFingerprint.create(
            adapter=self.adapter,
            kind=self.kind,
            identity=f"{self._base_url}#{self._model}",
            declared_version=self._declared_version,
            parameters=dict(config.parameters),
        )

    # --- one call ----------------------------------------------------------------------------

    async def invoke(self, case: RenderedCase) -> TargetOutput:
        client = self._require_client()
        config = self._require_config()
        body = self.build_request(case, model=self._model, parameters=dict(config.parameters))

        started = time.perf_counter()
        try:
            response = await client.post(self.path, json=body)
        except httpx.TimeoutException as exc:
            raise TargetUnavailable(
                f"{self.adapter}: no response within {config.timeout_s}s"
            ) from exc
        except httpx.RequestError as exc:
            raise TargetUnavailable(f"{self.adapter}: {redact(str(exc))}") from exc
        latency_ms = round((time.perf_counter() - started) * 1000)

        if response.status_code >= 400:
            # Raises for anything run-level or retryable; returns a record for this case alone.
            error = raise_or_describe(
                status_code=response.status_code,
                body=response.text,
                headers=response.headers,
                adapter=self.adapter,
            )
            return TargetOutput(error=error, latency_ms=latency_ms)

        try:
            payload = response.json()
        except ValueError:
            return self._invalid(response.text, latency_ms, "the body is not JSON")
        if not isinstance(payload, dict):
            return self._invalid(response.text, latency_ms, "the body is not a JSON object")

        return self._interpret(payload, case, latency_ms)

    def _interpret(
        self, payload: dict[str, Any], case: RenderedCase, latency_ms: int
    ) -> TargetOutput:
        reading = self.read_response(payload)
        if reading.declared_version:
            self._declared_version = reading.declared_version
        if reading.error is not None:
            return TargetOutput(error=reading.error, latency_ms=latency_ms)

        content: str | dict[str, Any] = reading.text or ""
        if case.expects_json:
            try:
                parsed = json.loads(reading.text or "")
            except ValueError:
                return self._invalid(
                    reading.text or "", latency_ms, "the suite expects JSON and this is not JSON"
                )
            if not isinstance(parsed, dict):
                return self._invalid(
                    reading.text or "", latency_ms, "the suite expects a JSON object"
                )
            content = parsed

        return TargetOutput(
            content=content,
            latency_ms=latency_ms,
            usage=reading.usage,
            cost_usd=self._cost(reading.usage),
        )

    # --- what a subclass provides -------------------------------------------------------------

    def build_request(
        self, case: RenderedCase, *, model: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    def read_response(self, payload: dict[str, Any]) -> Reading:
        raise NotImplementedError

    # --- helpers ------------------------------------------------------------------------------

    def _headers(self, api_key: str | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def _resolve_base_url(self, config: TargetConfig) -> str:
        candidate = config.base_url or (
            config.identity if config.identity.startswith(("http://", "https://")) else None
        )
        resolved = candidate or self.default_base_url
        if not resolved:
            raise TargetConfigError(
                f"{self.adapter}: no endpoint. Give one as the target "
                f"({self.adapter}:https://host/v1#model) or with --base-url."
            )
        return resolved.rstrip("/")

    def _resolve_model(self, config: TargetConfig) -> str:
        resolved = config.model or (
            None if config.identity.startswith(("http://", "https://")) else config.identity
        )
        if not resolved:
            raise TargetConfigError(
                f"{self.adapter}: no model. Name it after a # in the target "
                f"({self.adapter}:https://host/v1#llama-3)."
            )
        return resolved

    def _resolve_key(self, config: TargetConfig) -> str | None:
        variable = config.api_key_env or self.default_api_key_env
        if variable is None:
            return None
        value = os.environ.get(variable)
        if not value and self.requires_api_key:
            raise TargetConfigError(
                f"{self.adapter}: ${variable} is not set. Keys come from the environment, never "
                f"from a suite or the command line."
            )
        return value or None

    def _cost(self, usage: Usage | None) -> float | None:
        config = self._require_config()
        if usage is None or config.pricing is None:
            return None
        priced = config.pricing.cost_usd(
            self.adapter, self._model, tokens_in=usage.tokens_in, tokens_out=usage.tokens_out
        )
        return None if priced is None else priced[0]

    def _invalid(self, body: str, latency_ms: int, why: str) -> TargetOutput:
        return TargetOutput(
            error=TargetError(kind="invalid_response", message=f"{why}: {redact(body)}"),
            latency_ms=latency_ms,
        )

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise TargetConfigError(f"{self.adapter}: setup() has not run")
        return self._client

    def _require_config(self) -> TargetConfig:
        if self._config is None:
            raise TargetConfigError(f"{self.adapter}: setup() has not run")
        return self._config
