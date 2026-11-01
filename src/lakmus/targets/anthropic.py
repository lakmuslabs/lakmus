"""The Anthropic messages adapter.

Written apart from the OpenAI-compatible family because it shares neither the request nor the
response shape. What it does share is the lifecycle in `HttpModelTarget`.
"""

from typing import Any, ClassVar

from lakmus.targets.base import RenderedCase, TargetConfig, TargetError, Usage, redact
from lakmus.targets.http_model import HttpModelTarget, Reading

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
"""The API requires a limit and offers no default. Recorded in the fingerprint like any other
parameter, because it can truncate an answer and so change a score."""


class AnthropicTarget(HttpModelTarget):
    adapter: ClassVar[str] = "anthropic"
    default_base_url: ClassVar[str | None] = "https://api.anthropic.com"
    default_api_key_env: ClassVar[str | None] = "ANTHROPIC_API_KEY"
    path: ClassVar[str] = "/v1/messages"

    async def setup(self, config: TargetConfig) -> None:
        # The API version and the token limit both change what comes back, so they are parameters
        # rather than hidden constants: they belong in the digest.
        parameters = dict(config.parameters)
        parameters.setdefault("anthropic_version", API_VERSION)
        parameters.setdefault("max_tokens", DEFAULT_MAX_TOKENS)
        await super().setup(config.model_copy(update={"parameters": parameters}))

    def _headers(self, api_key: str | None) -> dict[str, str]:
        version = str(self._pending_version or API_VERSION)
        headers = {"anthropic-version": version}
        if api_key:
            headers["x-api-key"] = api_key
        return headers

    @property
    def _pending_version(self) -> object:
        config = self._config
        return None if config is None else config.parameters.get("anthropic_version")

    def build_request(
        self, case: RenderedCase, *, model: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        body = {key: value for key, value in parameters.items() if key != "anthropic_version"}
        body["model"] = model
        body["messages"] = [{"role": "user", "content": case.prompt}]
        return body

    def read_response(self, payload: dict[str, Any]) -> Reading:
        declared_version = payload.get("model")
        usage = _usage(payload.get("usage"))

        if payload.get("stop_reason") == "refusal":
            return Reading(
                declared_version=declared_version,
                usage=usage,
                error=TargetError(kind="refused", message="the provider declined to answer"),
            )

        blocks = payload.get("content")
        if not isinstance(blocks, list):
            return Reading(
                declared_version=declared_version,
                usage=usage,
                error=TargetError(kind="invalid_response", message="no content in the response"),
            )

        text = "".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block
        )
        if not text:
            return Reading(
                declared_version=declared_version,
                usage=usage,
                error=TargetError(
                    kind="invalid_response",
                    message=f"no text block in the response: {redact(str(blocks))}",
                ),
            )

        return Reading(text=text, usage=usage, declared_version=declared_version)


def _usage(raw: object) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    tokens_in = raw.get("input_tokens")
    tokens_out = raw.get("output_tokens")
    if not isinstance(tokens_in, int) or not isinstance(tokens_out, int):
        return None
    return Usage(tokens_in=max(0, tokens_in), tokens_out=max(0, tokens_out))
