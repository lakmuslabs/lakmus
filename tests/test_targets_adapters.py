"""Each adapter against a mocked provider: success, every failure the skill requires, and the
fingerprint.

No network: respx answers every request. The failures are the point — an adapter that only works
when the provider does is the one that loses a five-thousand-case run at case four thousand.
"""

from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from lakmus.errors import TargetConfigError, TargetRateLimited, TargetUnavailable
from lakmus.results.models import ParameterValue
from lakmus.targets import (
    AnthropicTarget,
    OllamaTarget,
    OpenAICompatibleTarget,
    OpenAITarget,
    RenderedCase,
    Target,
    TargetConfig,
)
from lakmus.targets import pricing as pricing_module

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

CASE = RenderedCase(case_id="p-0001", prompt="Le candidat est-il retenu ?")
JSON_CASE = RenderedCase(case_id="p-0002", prompt="Réponds en JSON", expects_json=True)


def _chat_completion(content: str = "Oui", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "gpt-x-2026-10-01",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }
    payload.update(overrides)
    return payload


def _message(text: str = "Oui", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "claude-x-2026-10-01",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 12, "output_tokens": 3},
        "stop_reason": "end_turn",
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")


async def _invoke(target: Target, config: TargetConfig, case: RenderedCase = CASE) -> Any:
    await target.setup(config)
    try:
        return await target.invoke(case)
    finally:
        await target.teardown()


# --- the OpenAI-compatible family ------------------------------------------------------------


@respx.mock
async def test_a_successful_call_returns_content_usage_and_the_served_version() -> None:
    respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=_chat_completion()))
    target = OpenAITarget()

    output = await _invoke(target, TargetConfig(identity="gpt-x"))

    assert output.content == "Oui"
    assert output.usage is not None
    assert (output.usage.tokens_in, output.usage.tokens_out) == (12, 3)
    assert output.error is None
    assert output.latency_ms >= 0
    assert target.fingerprint().declared_version == "gpt-x-2026-10-01"


@respx.mock
async def test_rate_limiting_is_raised_with_the_providers_advice() -> None:
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(429, headers={"retry-after": "7"}, text="slow down")
    )

    with pytest.raises(TargetRateLimited) as caught:
        await _invoke(OpenAITarget(), TargetConfig(identity="gpt-x"))

    assert caught.value.retry_after_s == 7.0


@respx.mock
async def test_a_server_failure_is_raised_for_the_engine_to_retry() -> None:
    respx.post(OPENAI_URL).mock(return_value=httpx.Response(503, text="try later"))

    with pytest.raises(TargetUnavailable):
        await _invoke(OpenAITarget(), TargetConfig(identity="gpt-x"))


@respx.mock
async def test_a_timeout_is_raised_rather_than_recorded() -> None:
    """A timeout is worth another attempt, and only the engine decides how many."""
    respx.post(OPENAI_URL).mock(side_effect=httpx.ReadTimeout("too slow"))

    with pytest.raises(TargetUnavailable, match="no response within"):
        await _invoke(OpenAITarget(), TargetConfig(identity="gpt-x", timeout_s=5))


@respx.mock
async def test_a_connection_that_never_opens_is_raised() -> None:
    respx.post(OPENAI_URL).mock(side_effect=httpx.ConnectError("no route"))

    with pytest.raises(TargetUnavailable):
        await _invoke(OpenAITarget(), TargetConfig(identity="gpt-x"))


@respx.mock
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"choices": []}, id="no-choices"),
        pytest.param({"choices": [{"message": {}}]}, id="no-content"),
        pytest.param({"choices": ["not-an-object"]}, id="choice-is-not-an-object"),
        pytest.param({}, id="empty-body"),
    ],
)
async def test_a_response_that_makes_no_sense_is_recorded_against_the_case(
    payload: dict[str, Any],
) -> None:
    respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=payload))

    output = await _invoke(OpenAITarget(), TargetConfig(identity="gpt-x"))

    assert output.error is not None
    assert output.error.kind == "invalid_response"


@respx.mock
async def test_a_body_that_is_not_json_is_recorded_against_the_case() -> None:
    respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, text="<html>502</html>"))

    output = await _invoke(OpenAITarget(), TargetConfig(identity="gpt-x"))

    assert output.error is not None
    assert output.error.kind == "invalid_response"


@respx.mock
async def test_content_the_provider_filtered_is_a_measurement_not_a_malfunction() -> None:
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200,
            json=_chat_completion(
                choices=[{"message": {"content": None}, "finish_reason": "content_filter"}]
            ),
        )
    )

    output = await _invoke(OpenAITarget(), TargetConfig(identity="gpt-x"))

    assert output.error is not None
    assert output.error.kind == "refused"


async def test_a_missing_key_fails_before_a_single_case_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(TargetConfigError, match=r"\$OPENAI_API_KEY is not set"):
        await OpenAITarget().setup(TargetConfig(identity="gpt-x"))


@respx.mock
async def test_a_self_hosted_endpoint_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    url = "https://llama.internal/v1/chat/completions"
    respx.post(url).mock(return_value=httpx.Response(200, json=_chat_completion()))

    output = await _invoke(
        OpenAICompatibleTarget(),
        TargetConfig(identity="https://llama.internal/v1", model="llama-3"),
    )

    assert output.content == "Oui"


@respx.mock
async def test_ollama_runs_locally_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    respx.post(OLLAMA_URL).mock(return_value=httpx.Response(200, json=_chat_completion()))

    output = await _invoke(OllamaTarget(), TargetConfig(identity="mistral-small"))

    assert output.content == "Oui"


async def test_an_endpoint_without_a_model_says_so() -> None:
    with pytest.raises(TargetConfigError, match="no model"):
        await OpenAICompatibleTarget().setup(TargetConfig(identity="https://llama.internal/v1"))


# --- JSON output -----------------------------------------------------------------------------


@respx.mock
async def test_a_suite_expecting_json_gets_a_parsed_object() -> None:
    route = respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(
            200, json=_chat_completion('{"score": 0.8, "decision": "retenu"}')
        )
    )

    output = await _invoke(OpenAITarget(), TargetConfig(identity="gpt-x"), JSON_CASE)

    assert output.content == {"score": 0.8, "decision": "retenu"}
    assert route.calls.last.request.read().decode().count("json_object") == 1


@respx.mock
async def test_prose_where_json_was_declared_is_recorded_against_the_case() -> None:
    respx.post(OPENAI_URL).mock(
        return_value=httpx.Response(200, json=_chat_completion("Je pense que oui."))
    )

    output = await _invoke(OpenAITarget(), TargetConfig(identity="gpt-x"), JSON_CASE)

    assert output.error is not None
    assert output.error.kind == "invalid_response"


# --- the fingerprint -------------------------------------------------------------------------


@respx.mock
async def test_every_parameter_sent_is_in_the_fingerprint() -> None:
    """A result measured at a different temperature is a different measurement."""
    route = respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=_chat_completion()))
    parameters: dict[str, ParameterValue] = {
        "temperature": 0.7,
        "top_p": 0.9,
        "seed": 11,
        "stream": False,
    }
    target = OpenAITarget()

    await _invoke(target, TargetConfig(identity="gpt-x", parameters=parameters))
    fingerprint = target.fingerprint()
    sent = route.calls.last.request.read().decode()

    assert fingerprint.parameters == parameters
    for name in parameters:
        assert name in sent
    assert fingerprint.identity == "https://api.openai.com/v1#gpt-x"


@respx.mock
async def test_a_proxy_in_front_of_a_model_is_a_different_target() -> None:
    """Why the endpoint is in the identity and not only the model name."""
    respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=_chat_completion()))
    respx.post("https://proxy.internal/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_completion())
    )

    direct = OpenAITarget()
    await _invoke(direct, TargetConfig(identity="gpt-x"))
    through_proxy = OpenAICompatibleTarget()
    await _invoke(through_proxy, TargetConfig(identity="https://proxy.internal/v1", model="gpt-x"))

    assert direct.fingerprint().sha256 != through_proxy.fingerprint().sha256


@respx.mock
async def test_a_provider_that_names_no_version_leaves_the_field_empty() -> None:
    """The report is to warn about this rather than invent a version."""
    payload = _chat_completion()
    del payload["model"]
    respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=payload))
    target = OpenAITarget()

    await _invoke(target, TargetConfig(identity="gpt-x"))

    assert target.fingerprint().declared_version is None


# --- cost ------------------------------------------------------------------------------------


@respx.mock
async def test_a_priced_model_reports_what_the_call_cost(tmp_path: Path) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text(
        '[[model]]\nadapter = "openai"\nmodel = "gpt-x"\n'
        "input_per_mtok_usd = 2.0\noutput_per_mtok_usd = 10.0\n"
        'as_of = 2026-10-01\nsource = "https://example.com/pricing"\n',
        encoding="utf-8",
    )
    respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=_chat_completion()))

    output = await _invoke(
        OpenAITarget(),
        TargetConfig(identity="gpt-x", pricing=pricing_module.load(path)),
    )

    assert output.cost_usd == pytest.approx((12 * 2.0 + 3 * 10.0) / 1_000_000)
    assert pricing_module.load(path).find("openai", "gpt-x").as_of == date(2026, 10, 1)  # type: ignore[union-attr]


@respx.mock
async def test_without_a_price_table_a_call_costs_nothing_rather_than_zero() -> None:
    respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=_chat_completion()))

    output = await _invoke(OpenAITarget(), TargetConfig(identity="gpt-x"))

    assert output.cost_usd is None


@respx.mock
async def test_a_provider_that_reports_no_usage_is_not_a_provider_that_used_nothing() -> None:
    payload = _chat_completion()
    del payload["usage"]
    respx.post(OPENAI_URL).mock(return_value=httpx.Response(200, json=payload))

    output = await _invoke(OpenAITarget(), TargetConfig(identity="gpt-x"))

    assert output.usage is None


# --- anthropic -------------------------------------------------------------------------------


@respx.mock
async def test_anthropic_returns_content_usage_and_its_version() -> None:
    route = respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(200, json=_message()))
    target = AnthropicTarget()

    output = await _invoke(target, TargetConfig(identity="claude-x"))

    assert output.content == "Oui"
    assert output.usage is not None
    assert (output.usage.tokens_in, output.usage.tokens_out) == (12, 3)
    assert target.fingerprint().declared_version == "claude-x-2026-10-01"
    assert route.calls.last.request.headers["anthropic-version"] == "2023-06-01"
    assert route.calls.last.request.headers["x-api-key"] == "sk-test-not-a-real-key"


@respx.mock
async def test_anthropic_records_the_token_limit_and_api_version_in_the_fingerprint() -> None:
    """Both change what comes back, so neither is a hidden constant."""
    respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(200, json=_message()))
    target = AnthropicTarget()

    await _invoke(target, TargetConfig(identity="claude-x"))

    assert target.fingerprint().parameters["max_tokens"] == 4096
    assert target.fingerprint().parameters["anthropic_version"] == "2023-06-01"


@respx.mock
async def test_anthropic_sends_the_token_limit_but_not_the_api_version_in_the_body() -> None:
    route = respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(200, json=_message()))

    await _invoke(AnthropicTarget(), TargetConfig(identity="claude-x"))
    sent = route.calls.last.request.read().decode()

    assert "max_tokens" in sent
    assert "anthropic_version" not in sent


@respx.mock
async def test_anthropic_declining_to_answer_is_recorded_as_refused() -> None:
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(200, json=_message(stop_reason="refusal"))
    )

    output = await _invoke(AnthropicTarget(), TargetConfig(identity="claude-x"))

    assert output.error is not None
    assert output.error.kind == "refused"


@respx.mock
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"content": []}, id="no-blocks"),
        pytest.param({"content": [{"type": "thinking"}]}, id="no-text-block"),
        pytest.param({}, id="empty-body"),
    ],
)
async def test_anthropic_response_that_makes_no_sense_is_recorded(
    payload: dict[str, Any],
) -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(200, json=payload))

    output = await _invoke(AnthropicTarget(), TargetConfig(identity="claude-x"))

    assert output.error is not None
    assert output.error.kind == "invalid_response"


@respx.mock
async def test_anthropic_rate_limiting_is_raised_like_any_other() -> None:
    respx.post(ANTHROPIC_URL).mock(return_value=httpx.Response(429, text="slow down"))

    with pytest.raises(TargetRateLimited):
        await _invoke(AnthropicTarget(), TargetConfig(identity="claude-x"))


async def test_anthropic_without_a_key_fails_at_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(TargetConfigError, match=r"\$ANTHROPIC_API_KEY is not set"):
        await AnthropicTarget().setup(TargetConfig(identity="claude-x"))
