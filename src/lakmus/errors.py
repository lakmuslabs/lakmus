"""Exceptions raised deliberately by Lakmus.

Each one carries the process exit code the CLI must use for it. Keeping the code on the exception
means a new error cannot be introduced without deciding how a caller in CI should read it, which a
mapping table somewhere in `cli` would let us forget.
"""

from enum import IntEnum


class ExitCode(IntEnum):
    """Process exit codes. These are a public interface: CI scripts branch on them."""

    SUCCESS = 0
    THRESHOLD_FAILED = 1
    USAGE = 2
    RUNTIME = 3


class LakmusError(Exception):
    """Base class for every error this project raises on purpose."""

    exit_code: ExitCode = ExitCode.RUNTIME


class SuiteInvalid(LakmusError):
    """A suite does not satisfy the format version it declares."""

    exit_code = ExitCode.USAGE


class TargetConfigError(LakmusError):
    """A target cannot be built as configured: unknown adapter, missing key, bad endpoint."""

    exit_code = ExitCode.USAGE


class RetryableTargetError(LakmusError):
    """Base class for target failures the engine retries with backoff.

    Adapters raise these and return; deciding how often and how long to wait is the engine's job,
    so that every adapter inherits one retry policy instead of inventing its own.
    """


class TargetRateLimited(RetryableTargetError):
    """The target refused the call for rate reasons."""


class TargetUnavailable(RetryableTargetError):
    """The target could not be reached, timed out, or failed server-side."""


class TargetBadRequest(LakmusError):
    """The target rejected the call in a way that retrying cannot fix."""


class ScorerError(LakmusError):
    """A scorer could not run at all.

    A scorer that merely cannot score one case returns its worst value with an explanation
    instead, so that one bad output never ends a run.
    """
