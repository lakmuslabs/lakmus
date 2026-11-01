"""The local cache, so that a resumed run never pays twice.

Only successes are stored. An error costs one call to reproduce and may well not recur: a
provider's transient 400 kept forever would be a permanent wrong answer. The cost is that a
resumed run re-pays for the cases that failed, which is the right way round.
"""

import hashlib
import json
import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from lakmus.results.models import FORMAT_VERSION
from lakmus.targets.base import TargetOutput

CACHE_ALGORITHM = "cache/1"
DEFAULT_ROOT = Path.home() / ".cache" / "lakmus"


def cache_key(*, target_digest: str, input_sha256: str, repeat: int) -> str:
    """The key, exactly as ADR 0009 specifies it.

    `target_digest` omits `declared_version`; the generation configuration is already inside it, so
    a run at another temperature cannot read an entry written at this one. `output_format` is here
    because the shape of a stored output is a contract, and a later version reading an old entry
    would be reading a different type.
    """
    canonical = json.dumps(
        {
            "algorithm": CACHE_ALGORITHM,
            "target": target_digest,
            "input": input_sha256,
            "repeat": repeat,
            "output_format": FORMAT_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Cache(Protocol):
    def get(self, key: str) -> TargetOutput | None: ...
    def put(self, key: str, output: TargetOutput) -> None: ...


@dataclass(frozen=True, slots=True)
class NullCache:
    """For `--no-cache`, and for a test that wants every call to happen."""

    def get(self, key: str) -> TargetOutput | None:
        return None

    def put(self, key: str, output: TargetOutput) -> None:
        return None


@dataclass(frozen=True, slots=True)
class FileCache:
    """One JSON file per entry, under two characters of the key."""

    root: Path = DEFAULT_ROOT
    tool_version: str = ""

    def path_for(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> TargetOutput | None:
        path = self.path_for(key)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            return TargetOutput.model_validate(document["output"])
        except (OSError, ValueError, KeyError, TypeError):
            # A corrupt or half-written entry is a miss, never an error: a cache must not be able
            # to end a run that would otherwise have worked.
            return None

    def put(self, key: str, output: TargetOutput) -> None:
        """Store an answer, or give up quietly.

        Never raises. A read-only cache directory, a full disk or a race with another run must not
        end a run whose calls have already been paid for — which is the rule `get` states and this
        method used to break.
        """
        if output.error is not None:
            return
        path = self.path_for(key)
        document = {
            "algorithm": CACHE_ALGORITHM,
            "tool": self.tool_version,
            "written_at": datetime.now(UTC).isoformat(),
            "output": output.model_dump(mode="json"),
        }
        # Written beside and moved into place, so that an interrupted write leaves no half-entry
        # for the next run to read. The suffix is unique per writer: two runs of the same suite
        # against the same target are the case this cache exists for, so they will collide.
        temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.partial")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
