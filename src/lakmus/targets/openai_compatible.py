"""Adapters for the OpenAI chat-completions wire format.

`openai-compatible` is the general case: any endpoint that speaks `/chat/completions`. `openai`
and `ollama` are the same adapter with different defaults, which is all that separates them.
"""

from typing import Any, ClassVar

from lakmus.targets.base import RenderedCase, TargetError, Usage, redact
from lakmus.targets.http_model import HttpModelTarget, Reading


class OpenAICompatibleTarget(HttpModelTarget):
    adapter: ClassVar[str] = "openai-compatible"
    requires_api_key: ClassVar[bool] = False
    """A self-hosted endpoint may well want no key. `openai` requires one."""

    def build_request(
        self, case: RenderedCase, *, model: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": case.prompt}],
            **parameters,
        }
        if case.expects_json and "response_format" not in body:
            body["response_format"] = {"type": "json_object"}
        return body

    def read_response(self, payload: dict[str, Any]) -> Reading:
        declared_version = payload.get("model")
        usage = _usage(payload.get("usage"), inbound="prompt_tokens", outbound="completion_tokens")

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return Reading(
                declared_version=declared_version,
                error=TargetError(kind="invalid_response", message="no choices in the response"),
            )

        choice = choices[0]
        if not isinstance(choice, dict):
            return Reading(
                declared_version=declared_version,
                error=TargetError(kind="invalid_response", message="a choice is not an object"),
            )

        if choice.get("finish_reason") == "content_filter":
            return Reading(
                declared_version=declared_version,
                usage=usage,
                error=TargetError(kind="refused", message="the provider filtered the content"),
            )

        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            return Reading(
                declared_version=declared_version,
                usage=usage,
                error=TargetError(
                    kind="invalid_response",
                    message=f"no text in the first choice: {redact(str(choice))}",
                ),
            )

        return Reading(text=content, usage=usage, declared_version=declared_version)


class OpenAITarget(OpenAICompatibleTarget):
    adapter: ClassVar[str] = "openai"
    default_base_url: ClassVar[str | None] = "https://api.openai.com/v1"
    default_api_key_env: ClassVar[str | None] = "OPENAI_API_KEY"
    requires_api_key: ClassVar[bool] = True


class OllamaTarget(OpenAICompatibleTarget):
    adapter: ClassVar[str] = "ollama"
    default_base_url: ClassVar[str | None] = "http://localhost:11434/v1"
    requires_api_key: ClassVar[bool] = False
    """Ollama runs on the user's machine; there is no key to leak and none to demand."""


def _usage(raw: object, *, inbound: str, outbound: str) -> Usage | None:
    """Read a usage block, or nothing when the provider sent none.

    Zero is not the same answer as silence: a run that cannot report tokens must not look like a
    run that used none.
    """
    if not isinstance(raw, dict):
        return None
    tokens_in = raw.get(inbound)
    tokens_out = raw.get(outbound)
    if not isinstance(tokens_in, int) or not isinstance(tokens_out, int):
        return None
    return Usage(tokens_in=max(0, tokens_in), tokens_out=max(0, tokens_out))
