"""The one place the HTTP-to-domain mapping lives, so four adapters cannot disagree about a 429."""

from datetime import UTC, datetime

import pytest

from lakmus.errors import TargetConfigError, TargetRateLimited, TargetUnavailable
from lakmus.targets.base import (
    TargetError,
    TargetOutput,
    provider_message,
    raise_or_describe,
    redact,
    retry_after_seconds,
)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_rejected_or_missing_endpoint_ends_the_run(status: int) -> None:
    """These will fail for every case, so there is nothing to record against one."""
    with pytest.raises(TargetConfigError):
        raise_or_describe(status_code=status, body="nope", adapter="openai")


@pytest.mark.parametrize("status", [408, 409, 425, 500, 502, 503, 504])
def test_a_retryable_failure_is_raised_for_the_engine_to_handle(status: int) -> None:
    with pytest.raises(TargetUnavailable):
        raise_or_describe(status_code=status, body="later", adapter="openai")


def test_rate_limiting_carries_what_the_provider_asked_for() -> None:
    with pytest.raises(TargetRateLimited) as caught:
        raise_or_describe(
            status_code=429, body="slow down", headers={"retry-after": "30"}, adapter="openai"
        )

    assert caught.value.retry_after_s == 30.0


def test_rate_limiting_without_advice_still_raises() -> None:
    with pytest.raises(TargetRateLimited) as caught:
        raise_or_describe(status_code=429, body="slow down", adapter="openai")

    assert caught.value.retry_after_s is None


@pytest.mark.parametrize("status", [400, 413, 422])
def test_a_rejected_case_is_recorded_rather_than_raised(status: int) -> None:
    """One over-long case must not end a run of five thousand."""
    error = raise_or_describe(status_code=status, body="too long", adapter="openai")

    assert error.kind == "bad_request"
    assert error.status_code == status


@pytest.mark.parametrize(
    "body",
    [
        "The model `gpt-zzz` does not exist",
        "model_not_found",
        "Incorrect API key provided",
        "no such model: llama-99",
    ],
)
def test_a_400_that_names_a_configuration_fault_ends_the_run(body: str) -> None:
    """Providers use 400 for both, so the body decides — and only on a short, explicit list."""
    with pytest.raises(TargetConfigError):
        raise_or_describe(status_code=400, body=body, adapter="openai")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("30", 30.0, id="seconds"),
        pytest.param("0", 0.0, id="zero"),
        pytest.param("-5", 0.0, id="never-negative"),
        pytest.param("soon", None, id="unparseable"),
    ],
)
def test_retry_after_in_seconds(raw: str, expected: float | None) -> None:
    assert retry_after_seconds({"retry-after": raw}) == expected


def test_retry_after_as_a_date_becomes_a_duration() -> None:
    now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)

    seconds = retry_after_seconds({"retry-after": "Sun, 01 Nov 2026 12:01:00 GMT"}, now=now)

    assert seconds == 60.0


def test_a_date_already_past_waits_no_time_at_all() -> None:
    now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)

    assert retry_after_seconds({"retry-after": "Sun, 01 Nov 2026 11:00:00 GMT"}, now=now) == 0.0


@pytest.mark.parametrize(
    ("body", "leaked"),
    [
        pytest.param("Authorization: Bearer abc.def.ghi", "abc.def.ghi", id="bearer-token"),
        pytest.param("x-api-key: sk-live-abcdef123456", "sk-live-abcdef123456", id="header"),
        pytest.param("api_key=hunter2 rejected", "hunter2", id="assignment"),
        pytest.param("key sk-proj-abcdef123456 invalid", "sk-proj-abcdef123456", id="prefix"),
    ],
)
def test_a_provider_message_is_quoted_without_its_secrets(body: str, leaked: str) -> None:
    """Error bodies have been known to echo the request, headers included."""
    assert leaked not in redact(body)


@pytest.mark.parametrize(
    "body",
    [
        "maximum context length is 8192 tokens, your prompt used 9000",
        "the token budget of 512 was exceeded",
    ],
)
def test_a_message_about_tokens_survives_intact(body: str) -> None:
    """Over-redaction costs debugging, and provider messages are full of token limits."""
    assert redact(body) == body


def test_a_long_message_is_cut_rather_than_quoted_whole() -> None:
    assert len(redact("x" * 10_000)) < 600


def test_a_call_that_did_not_fail_must_have_content() -> None:
    with pytest.raises(ValueError, match="must have content"):
        TargetOutput(latency_ms=10)


def test_a_failure_needs_no_content() -> None:
    output = TargetOutput(latency_ms=10, error=TargetError(kind="timeout", message="no response"))

    assert output.content is None


@pytest.mark.parametrize(
    ("body", "leaked"),
    [
        pytest.param('{"api_key": "secret123"}', "secret123", id="json-snake-case"),
        pytest.param('{"apiKey": "camelSecret"}', "camelSecret", id="json-camel-case"),
        pytest.param('{"api-key": "8f3b2c1d9e4a7b"}', "8f3b2c1d9e4a7b", id="json-kebab-case"),
        pytest.param('{"password": "hunter2"}', "hunter2", id="json-password"),
        pytest.param("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz", id="basic-auth"),
        pytest.param(
            "x-api-key: AIzaSyD-abcdefghijklmnop", "AIzaSyD-abcdefghijklmnop", id="no-prefix"
        ),
        pytest.param(
            '{"error":{"message":"bad"},"headers":{"authorization":"Bearer zzz.yyy"}}',
            "zzz.yyy",
            id="echoed-headers",
        ),
    ],
)
def test_a_credential_in_any_shape_a_provider_uses_is_redacted(body: str, leaked: str) -> None:
    """JSON is how a provider echoes a request, and a key without a recognisable prefix is the
    common case outside OpenAI."""
    assert leaked not in redact(body)


def test_several_secrets_in_one_body_all_go() -> None:
    body = '{"api_key": "one", "authorization": "Bearer two", "password": "three"}'

    cleaned = redact(body)

    assert "one" not in cleaned
    assert "two" not in cleaned
    assert "three" not in cleaned


def test_redacting_twice_changes_nothing_more() -> None:
    once = redact('{"api_key": "secret123"}')

    assert redact(once) == once


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param('{"error": {"message": "boom"}}', "boom", id="nested-error"),
        pytest.param('{"error": "boom"}', "boom", id="error-as-a-string"),
        pytest.param('{"message": "boom"}', "boom", id="top-level-message"),
        pytest.param("not json at all", "not json at all", id="not-json"),
        pytest.param("[1, 2]", "[1, 2]", id="json-but-not-an-object"),
    ],
)
def test_the_providers_own_words_are_separated_from_the_rest(body: str, expected: str) -> None:
    assert provider_message(body) == expected


def test_a_case_prompt_that_echoes_a_marker_does_not_end_the_run() -> None:
    """The failure the case-level default exists to prevent.

    A 400 about a bad temperature used to kill a run of thousands because the prompt the provider
    echoed happened to contain "does not exist".
    """
    body = (
        '{"error": {"message": "Invalid value for \'temperature\'"},'
        ' "request": {"messages": [{"role": "user",'
        ' "content": "Le dossier client 42 does not exist, est-il retenu ?"}]}}'
    )

    error = raise_or_describe(status_code=400, body=body, adapter="openai")

    assert error.kind == "bad_request"


def test_a_marker_in_the_providers_own_error_still_ends_the_run() -> None:
    body = '{"error": {"message": "The model `gpt-zzz` does not exist", "code": "model_not_found"}}'

    with pytest.raises(TargetConfigError):
        raise_or_describe(status_code=400, body=body, adapter="openai")


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_ends_the_run_and_names_where_it_points(status: int) -> None:
    """Recorded against every case, it was a run-wide fault repeated five thousand times."""
    with pytest.raises(TargetConfigError, match="redirects to 'https://elsewhere/v1'"):
        raise_or_describe(
            status_code=status,
            body="",
            headers={"location": "https://elsewhere/v1"},
            adapter="openai-compatible",
        )
