"""The one place the HTTP-to-domain mapping lives, so four adapters cannot disagree about a 429."""

from datetime import UTC, datetime

import pytest

from lakmus.errors import TargetConfigError, TargetRateLimited, TargetUnavailable
from lakmus.targets.base import (
    TargetError,
    TargetOutput,
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
