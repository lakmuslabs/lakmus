"""A target that answers from a script, so that the engine can be tested without a network.

Every reaction is either an answer or an exception, consumed in order. That is what lets a test
say "the second attempt succeeds" or "this one never does" without a provider, a clock or a key.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import Any, ClassVar

from lakmus.results.models import TargetFingerprint
from lakmus.targets.base import (
    RenderedCase,
    TargetConfig,
    TargetError,
    TargetKind,
    TargetOutput,
    Usage,
)

Reaction = str | Mapping[str, Any] | BaseException | TargetError


class FakeTarget:
    """A scripted model target."""

    adapter: ClassVar[str] = "fake"
    kind: ClassVar[TargetKind] = "model"

    def __init__(
        self,
        *,
        script: Sequence[Reaction] | None = None,
        answer: Callable[[RenderedCase], Reaction] | None = None,
        declared_version: str | None = "fake-2026-11-01",
        latency_ms: int = 7,
    ) -> None:
        self._script = list(script or [])
        self._answer = answer
        self._declared_version = declared_version
        self._latency_ms = latency_ms
        self._config: TargetConfig | None = None
        self.calls: list[RenderedCase] = []
        self.setups = 0
        self.teardowns = 0
        self.version_known = False

    async def setup(self, config: TargetConfig) -> None:
        self._config = config
        self.setups += 1

    async def teardown(self) -> None:
        self.teardowns += 1

    def fingerprint(self) -> TargetFingerprint:
        config = self._config
        return TargetFingerprint.create(
            adapter=self.adapter,
            kind=self.kind,
            identity=f"fake://local#{config.identity if config else 'none'}",
            # Only known once a call has come back, exactly as a real provider behaves.
            declared_version=self._declared_version if self.version_known else None,
            parameters=dict(config.parameters) if config else {},
        )

    async def invoke(self, case: RenderedCase) -> TargetOutput:
        self.calls.append(case)
        # Every real adapter awaits I/O here, and a fake that does not runs the engine in a
        # regime no adapter produces: a loop that only checked its stop conditions before its
        # first await looked correct against a fake that never had one.
        await asyncio.sleep(0)
        reaction = self._next(case)
        if isinstance(reaction, BaseException):
            raise reaction
        self.version_known = True
        if isinstance(reaction, TargetError):
            return TargetOutput(latency_ms=self._latency_ms, error=reaction)
        return TargetOutput(
            content=dict(reaction) if isinstance(reaction, Mapping) else reaction,
            latency_ms=self._latency_ms,
            usage=Usage(tokens_in=10, tokens_out=2),
        )

    def _next(self, case: RenderedCase) -> Reaction:
        if self._script:
            return self._script.pop(0)
        if self._answer is not None:
            return self._answer(case)
        return "oui"
