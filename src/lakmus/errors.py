"""Exceptions and the validation vocabulary.

Each exception carries the process exit code the CLI must use for it. Keeping the code on the
exception means a new error cannot be introduced without deciding how a caller in CI should read
it, which a mapping table somewhere in `cli` would let us forget.
"""

from collections.abc import Sequence
from enum import IntEnum, StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ExitCode(IntEnum):
    """Process exit codes. These are a public interface: CI scripts branch on them."""

    SUCCESS = 0
    THRESHOLD_FAILED = 1
    USAGE = 2
    RUNTIME = 3


class Code(StrEnum):
    """Validation codes.

    Also a public interface: they appear in CI logs and in search queries, so a code keeps its
    meaning for the life of a format version. Retiring one leaves its number unused.
    """

    NOT_A_SUITE = "LKM-001"
    MANIFEST_NOT_YAML = "LKM-100"
    MANIFEST_UNKNOWN_FIELD = "LKM-101"
    MANIFEST_MISSING_FIELD = "LKM-102"
    MANIFEST_INVALID_VALUE = "LKM-103"
    MANIFEST_FORMAT_UNSUPPORTED = "LKM-104"
    FILE_MISSING = "LKM-201"
    PATH_ESCAPES_SUITE = "LKM-202"
    GITATTRIBUTES_MISSING = "LKM-203"
    CASE_NOT_JSON = "LKM-301"
    CASE_WITHOUT_ID = "LKM-302"
    CASE_DUPLICATE_ID = "LKM-303"
    CASE_MISSING_GROUP_FIELD = "LKM-304"
    CASES_EMPTY = "LKM-305"
    SCORER_IMPORT_FAILED = "LKM-401"
    SCORER_NOT_CALLABLE = "LKM-402"
    METRIC_DUPLICATE_ID = "LKM-501"
    THRESHOLD_UNKNOWN_METRIC = "LKM-502"
    THRESHOLD_WITHOUT_BOUND = "LKM-503"
    LICENCE_NOT_ALLOWED = "LKM-601"
    TARGET_INPUT_REQUIRED = "LKM-602"


class Finding(BaseModel):
    """One thing wrong with a suite, with where it is and how to fix it.

    Validation collects findings instead of raising at the first one: an author with six problems
    should learn about six, not run `validate` six times.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: Code
    location: str
    message: str
    severity: Literal["error", "warning"] = "error"

    def __str__(self) -> str:
        return f"{self.code}  {self.location}: {self.message}"


class LakmusError(Exception):
    """Base class for every error this project raises on purpose."""

    exit_code: ExitCode = ExitCode.RUNTIME


class SuiteInvalid(LakmusError):
    """A suite does not satisfy the format version it declares."""

    exit_code = ExitCode.USAGE

    def __init__(self, findings: Sequence[Finding]) -> None:
        self.findings: tuple[Finding, ...] = tuple(findings)
        errors = sum(1 for finding in self.findings if finding.severity == "error")
        super().__init__(f"suite is invalid: {errors} error(s)")


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
