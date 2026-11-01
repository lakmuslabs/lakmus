"""A real `lakmus run` in a subprocess, against a target that awaits, so a test can signal it.

Nothing else can prove the interrupt. A test that sets the engine's stop event itself proves the
engine; it says nothing about whether a signal ever reaches it, which is the whole of what the
command adds. The previous milestone's review found three stop conditions that worked only against
a fake with no `await` in it, so this driver has one.

Invoked as: python signal_driver.py run <suite> --target slow:local ...
It prints `CALL <case id>` on stdout as each call starts, which is how a test knows the run is
under way before it signals.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import ClassVar

from lakmus.cli.main import app
from lakmus.results.models import TargetFingerprint
from lakmus.targets import registry
from lakmus.targets.base import RenderedCase, TargetConfig, TargetKind, TargetOutput, Usage

DELAY = float(os.environ.get("LAKMUS_TEST_DELAY", "0.3"))


class SlowTarget:
    """Answers correctly, slowly, and suspends while it does."""

    adapter: ClassVar[str] = "slow"
    kind: ClassVar[TargetKind] = "model"

    def __init__(self) -> None:
        self._config: TargetConfig | None = None

    async def setup(self, config: TargetConfig) -> None:
        self._config = config

    async def teardown(self) -> None:
        return None

    def fingerprint(self) -> TargetFingerprint:
        return TargetFingerprint.create(
            adapter=self.adapter,
            kind=self.kind,
            identity=self._config.identity if self._config else "unconfigured",
        )

    async def invoke(self, case: RenderedCase) -> TargetOutput:
        print(f"CALL {case.case_id}", flush=True)
        await asyncio.sleep(DELAY)
        return TargetOutput(
            content="oui", latency_ms=int(DELAY * 1000), usage=Usage(tokens_in=1, tokens_out=1)
        )


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    registry.BUILTIN_TARGETS["slow"] = SlowTarget  # type: ignore[index]
    app()
