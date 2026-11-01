"""What each digest is allowed to notice.

The two digests exist because one digest cannot answer both questions. These tests are the
contract: `inputs_sha256` must move when a measurement could change and must not move otherwise,
because published results are compared on it.
"""

from pathlib import Path

import pytest
import yaml

from lakmus.suite.hashing import content_sha256, inputs_sha256
from lakmus.suite.loader import MANIFEST_FILENAME, load


def _digests(root: Path) -> tuple[str, str]:
    suite = load(root, run_suite_code=False)
    return suite.inputs_sha256, suite.content_sha256


def _edit_manifest(root: Path, field: str, value: object) -> None:
    path = root / MANIFEST_FILENAME
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest[field] = value
    path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")


def test_digests_are_stable_across_calls(valid_suite: Path) -> None:
    assert _digests(valid_suite) == _digests(valid_suite)


def test_editing_the_readme_changes_only_the_content_digest(valid_suite: Path) -> None:
    before_inputs, before_content = _digests(valid_suite)

    (valid_suite / "README.md").write_text("# Fixed a typo\n", encoding="utf-8")
    after_inputs, after_content = _digests(valid_suite)

    assert after_inputs == before_inputs
    assert after_content != before_content


@pytest.mark.parametrize(
    ("relative_path", "contents"),
    [
        pytest.param("cases/pairs.jsonl", '{"id": "p-0001", "variable": "nom"}\n', id="a-case"),
        pytest.param("prompts/screener.j2", "Autre consigne : {{ a }}\n", id="a-prompt"),
        pytest.param(
            "scorers/pair_gap.py", "def mean_abs_gap(c, o):\n    return 1.0\n", id="a-scorer"
        ),
        pytest.param("scorers/helper.py", "RATIO = 2\n", id="a-new-file-beside-a-scorer"),
    ],
)
def test_editing_an_input_changes_both_digests(
    valid_suite: Path, relative_path: str, contents: str
) -> None:
    before_inputs, before_content = _digests(valid_suite)

    (valid_suite / relative_path).write_text(contents, encoding="utf-8")
    after_inputs, after_content = _digests(valid_suite)

    assert after_inputs != before_inputs
    assert after_content != before_content


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("title", "Un autre titre", id="title"),
        pytest.param("thresholds", {"score_gap": {"max": 0.5}}, id="thresholds"),
        pytest.param("authors", ["someone-else"], id="authors"),
    ],
)
def test_metadata_and_thresholds_leave_the_inputs_digest_alone(
    valid_suite: Path, field: str, value: object
) -> None:
    """A threshold decides the verdict, not the measurement.

    A leaderboard has to compare measurements taken under different thresholds, so moving a
    threshold must not make two runs incomparable.
    """
    before_inputs, before_content = _digests(valid_suite)

    _edit_manifest(valid_suite, field, value)
    after_inputs, after_content = _digests(valid_suite)

    assert after_inputs == before_inputs
    assert after_content != before_content


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param(
            "run", {"repeats": 5, "temperature": 0, "seed": 42, "timeout_s": 60}, id="run"
        ),
        pytest.param("cases", {"source": "cases/pairs.jsonl", "sample": 1}, id="cases"),
    ],
)
def test_measurement_sections_change_the_inputs_digest(
    valid_suite: Path, field: str, value: object
) -> None:
    before_inputs, _ = _digests(valid_suite)

    _edit_manifest(valid_suite, field, value)
    after_inputs, _ = _digests(valid_suite)

    assert after_inputs != before_inputs


def test_no_digest_covers_the_reserved_holdout(valid_suite: Path) -> None:
    holdout = valid_suite / "cases" / "holdout.enc"
    holdout.write_bytes(b"not-really-encrypted")
    _edit_manifest(
        valid_suite, "cases", {"source": "cases/pairs.jsonl", "holdout": "cases/holdout.enc"}
    )
    before_inputs, before_content = _digests(valid_suite)

    holdout.write_bytes(b"different-ciphertext-entirely")
    after_inputs, after_content = _digests(valid_suite)

    assert (after_inputs, after_content) == (before_inputs, before_content)


def test_a_digest_is_not_computed_from_the_root_path(valid_suite: Path, tmp_path: Path) -> None:
    """Moving a suite must not change what it is."""
    import shutil

    elsewhere = tmp_path / "moved" / "deeper"
    shutil.copytree(valid_suite, elsewhere)

    assert _digests(elsewhere) == _digests(valid_suite)


def test_the_algorithm_name_is_part_of_the_digest(valid_suite: Path) -> None:
    """The two digests cover different sets, so they must never collide."""
    suite = load(valid_suite, run_suite_code=False)

    assert inputs_sha256(valid_suite, suite.manifest) != content_sha256(valid_suite, suite.manifest)


MINIMAL_MANIFEST = """\
lakmus: "0.1"
id: ops/golden
version: 1.0.0
title: Golden vector
language: en
license: MIT
target:
  kind: model
  input: prompts/p.j2
cases:
  source: cases/c.jsonl
metrics:
  - id: exact
    scorer: builtin:exact_match
"""


def _minimal_suite(root: Path) -> Path:
    (root / "cases").mkdir(parents=True)
    (root / "prompts").mkdir()
    (root / MANIFEST_FILENAME).write_text(MINIMAL_MANIFEST, encoding="utf-8")
    (root / "cases" / "c.jsonl").write_text('{"id": "c-1"}\n', encoding="utf-8")
    (root / "prompts" / "p.j2").write_text("{{ id }}\n", encoding="utf-8")
    (root / ".gitattributes").write_text("* -text\n", encoding="utf-8")
    return root


GOLDEN_INPUTS_SHA256 = "001e7beee52e321180b75d52ae4facbd179d657322dd226d68713c108f4cebc1"
"""Pinned on purpose. `inputs_sha256` is the cross-implementation comparability key, so this value
may change only together with the algorithm name in `hashing.INPUTS_ALGORITHM`."""


def test_the_inputs_digest_matches_its_published_vector(tmp_path: Path) -> None:
    root = _minimal_suite(tmp_path / "golden")

    suite = load(root, run_suite_code=False)

    assert suite.inputs_sha256 == GOLDEN_INPUTS_SHA256


@pytest.mark.parametrize(
    ("relative_path", "contents"),
    [
        pytest.param("tpl/screener.j2", "Autre consigne : {{ a }}\n", id="prompt-outside-prompts"),
        pytest.param(
            "lib/gap.py", "def mean_abs_gap(c, o):\n    return 999.0\n", id="scorer-outside-scorers"
        ),
    ],
)
def test_a_declared_input_outside_the_usual_directory_is_still_covered(
    valid_suite: Path, relative_path: str, contents: str
) -> None:
    """The format lets a suite keep its prompt or scorer anywhere.

    Covering only `prompts/` and `scorers/` meant such a suite could change its prompt entirely
    while `inputs_sha256` stayed put, which is the one thing that digest must never do.
    """
    moved = valid_suite / relative_path
    moved.parent.mkdir(parents=True, exist_ok=True)
    if relative_path.endswith(".j2"):
        (valid_suite / "prompts" / "screener.j2").rename(moved)
        _edit_manifest(
            valid_suite,
            "target",
            {
                "kind": "model",
                "input": relative_path,
                "output": {"type": "json", "schema": {"score": "number", "decision": "string"}},
            },
        )
    else:
        (valid_suite / "scorers" / "pair_gap.py").rename(moved)
        _edit_manifest(
            valid_suite,
            "metrics",
            [
                {
                    "id": "score_gap",
                    "scorer": f"{relative_path}:mean_abs_gap",
                    "group_by": ["variable"],
                },
                {"id": "decision_flip_rate", "scorer": "builtin:pair_flip"},
            ],
        )
    before_inputs, _ = _digests(valid_suite)

    moved.write_text(contents, encoding="utf-8")
    after_inputs, _ = _digests(valid_suite)

    assert after_inputs != before_inputs


def test_an_accented_filename_digests_the_same_however_the_filesystem_stores_it(
    tmp_path: Path,
) -> None:
    """NFC and NFD spell the same name with different bytes.

    macOS and Linux disagree about which to store, and suites carry accented French names. Without
    normalisation, copying a suite between two machines changed what it claimed to have measured.
    """
    import unicodedata

    composed = _minimal_suite(tmp_path / "nfc")
    decomposed = _minimal_suite(tmp_path / "nfd")
    name = "écran.j2"
    (composed / "prompts" / unicodedata.normalize("NFC", name)).write_text("x\n", encoding="utf-8")
    (decomposed / "prompts" / unicodedata.normalize("NFD", name)).write_text(
        "x\n", encoding="utf-8"
    )

    assert _digests(composed) == _digests(decomposed)


@pytest.mark.parametrize("junk", [".DS_Store", "Thumbs.db", "__pycache__/pair_gap.cpython-312.pyc"])
def test_what_an_operating_system_leaves_behind_does_not_change_a_digest(
    valid_suite: Path, junk: str
) -> None:
    """Two checkouts of one commit must agree on what they measured.

    On macOS a stray .DS_Store inside prompts/ is routine, and it used to move the comparability
    key.
    """
    before = _digests(valid_suite)

    noise = valid_suite / "prompts" / junk
    noise.parent.mkdir(parents=True, exist_ok=True)
    noise.write_bytes(b"\x00junk")

    assert _digests(valid_suite) == before
