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
