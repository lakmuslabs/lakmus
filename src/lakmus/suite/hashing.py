"""The two digests that identify a suite.

A single digest over every file would mean that fixing a typo in a README breaks comparability
with every result published before it, while the format's own versioning rules call a
documentation change minor. So there are two, with different jobs:

- `inputs_sha256` answers "are these two runs measuring the same thing?". It covers only what can
  change a measurement. Thresholds are deliberately outside it: they change the verdict, not the
  measurement, and a leaderboard needs to compare measurements across different thresholds.
- `content_sha256` answers "is this the same directory?". It covers everything else too.

Both are computed over a canonical text whose first line names the algorithm, so that the
algorithm itself can change under a declared version rather than silently.
"""

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Iterator
from pathlib import Path

from lakmus.suite.models import Manifest, declared_paths

INPUTS_ALGORITHM = "inputs/1"
CONTENT_ALGORITHM = "content/1"

_EXCLUDED_DIRS = frozenset({".git", "__pycache__"})
_EXCLUDED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
"""Artefacts an operating system or editor leaves behind. They are never part of a suite, and
leaving them in would make two checkouts of one commit disagree on what they measured."""

_EXCLUDED_SUFFIXES = (".pyc", ".pyo")

_INPUT_DIRS = ("prompts", "scorers")

_CHUNK = 1 << 20


def hash_file(path: Path) -> str:
    """SHA-256 of a file's bytes, exactly as they are on disk.

    Nothing is normalised, line endings included: a digest that calls two different files
    identical is not a digest. The cost is that a CRLF checkout produces a different digest, which
    is why a suite carries a `.gitattributes` pinning `* -text`.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _is_noise(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    return (
        bool(_EXCLUDED_DIRS.intersection(relative.parts))
        or path.name in _EXCLUDED_NAMES
        or path.suffix in _EXCLUDED_SUFFIXES
    )


def _walk(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file() or _is_noise(root, path):
            continue
        yield path


def symlinks(root: Path) -> list[str]:
    """Symbolic links anywhere in the suite.

    No digest covers a link, because a link has no content of its own. That would let a suite hide
    a prompt or a scorer behind one and still claim a stable `inputs_sha256`, so the loader turns
    every link it finds here into a finding instead.
    """
    found = [
        path
        for path in root.rglob("*")
        if path.is_symlink() and not _EXCLUDED_DIRS.intersection(path.relative_to(root).parts)
    ]
    return sorted(_canonical_name(root, path) for path in found)


def input_files(root: Path, manifest: Manifest) -> list[Path]:
    """Files whose contents can change a measurement.

    Every file the manifest declares, plus the conventional directories wholesale: a scorer is
    free to import a helper beside it, and that helper changes results just as much. Relying on
    the directories alone would miss a suite that keeps its prompt somewhere else, which the
    format permits.
    """
    selected: set[Path] = set()
    for directory in _INPUT_DIRS:
        candidate = root / directory
        if candidate.is_dir():
            selected.update(_walk(candidate))

    holdout = root / manifest.cases.holdout if manifest.cases.holdout else None
    for _field, relative in declared_paths(manifest):
        path = root / relative
        if path.is_symlink() or not path.is_file() or _is_noise(root, path):
            continue
        if holdout is not None and path == holdout:
            continue
        selected.add(path)

    return sorted(selected)


def content_files(root: Path, manifest: Manifest) -> list[Path]:
    """Every file in the suite except the reserved holdout, which no digest covers."""
    holdout = root / manifest.cases.holdout if manifest.cases.holdout else None
    return sorted(path for path in _walk(root) if holdout is None or path != holdout)


def _canonical_name(root: Path, path: Path) -> str:
    """The relative path as it goes into a digest.

    Normalised to NFC: the same accented filename is stored decomposed on some filesystems and
    composed on others, and suites carry accented names. Without this, copying a suite between two
    machines changes what it claims to have measured.
    """
    return unicodedata.normalize("NFC", path.relative_to(root).as_posix())


def _file_lines(root: Path, paths: Iterable[Path]) -> list[str]:
    named = sorted((_canonical_name(root, path), path) for path in paths)
    return [f"file {hash_file(path)} {name}\n" for name, path in named]


def _digest(algorithm: str, lines: Iterable[str]) -> str:
    text = f"{algorithm}\n" + "".join(lines)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def measured_manifest_digest(manifest: Manifest) -> str:
    """SHA-256 of the manifest sections that describe a measurement.

    `target`, `cases` and `metrics` and `run`, serialised canonically. Title, licence, authors and
    thresholds are excluded because changing them leaves the numbers a run produces untouched.
    """
    measured = manifest.model_dump(
        mode="json", by_alias=True, include={"target", "cases", "metrics", "run"}
    )
    canonical = json.dumps(measured, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def inputs_sha256(root: Path, manifest: Manifest) -> str:
    """The comparability key: equal values mean two runs measured the same thing."""
    lines = [f"manifest {measured_manifest_digest(manifest)}\n"]
    lines += _file_lines(root, input_files(root, manifest))
    return _digest(INPUTS_ALGORITHM, lines)


def content_sha256(root: Path, manifest: Manifest) -> str:
    """Provenance for the whole directory."""
    return _digest(CONTENT_ALGORITHM, _file_lines(root, content_files(root, manifest)))
