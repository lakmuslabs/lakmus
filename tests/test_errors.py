import pytest

from lakmus.errors import (
    ExitCode,
    LakmusError,
    RetryableTargetError,
    ScorerError,
    SuiteInvalid,
    TargetBadRequest,
    TargetConfigError,
    TargetRateLimited,
    TargetUnavailable,
)


def test_exit_codes_are_the_documented_values() -> None:
    assert (ExitCode.SUCCESS, ExitCode.THRESHOLD_FAILED, ExitCode.USAGE, ExitCode.RUNTIME) == (
        0,
        1,
        2,
        3,
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(SuiteInvalid, ExitCode.USAGE, id="suite-invalid-is-a-usage-error"),
        pytest.param(TargetConfigError, ExitCode.USAGE, id="target-config-is-a-usage-error"),
        pytest.param(TargetRateLimited, ExitCode.RUNTIME, id="rate-limited-is-a-runtime-error"),
        pytest.param(TargetUnavailable, ExitCode.RUNTIME, id="unavailable-is-a-runtime-error"),
        pytest.param(TargetBadRequest, ExitCode.RUNTIME, id="bad-request-is-a-runtime-error"),
        pytest.param(ScorerError, ExitCode.RUNTIME, id="scorer-error-is-a-runtime-error"),
    ],
)
def test_each_error_carries_its_exit_code(error: type[LakmusError], expected: ExitCode) -> None:
    assert error.exit_code is expected
    assert issubclass(error, LakmusError)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        pytest.param(TargetRateLimited, True, id="rate-limited"),
        pytest.param(TargetUnavailable, True, id="unavailable"),
        pytest.param(TargetBadRequest, False, id="bad-request"),
        pytest.param(TargetConfigError, False, id="config-error"),
    ],
)
def test_only_transient_target_failures_are_retryable(
    error: type[LakmusError], retryable: bool
) -> None:
    assert issubclass(error, RetryableTargetError) is retryable
