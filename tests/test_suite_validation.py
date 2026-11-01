"""Every validation code, exercised by breaking a valid suite one way at a time.

The specification lists the rules; this table lists one way to break each, and asserts the exact
set of codes that comes back. Exact rather than membership is deliberate: an extra finding is as
much a bug as a missing one, because `validate` is what an author trusts.

One static fixture plus declarative mutations, rather than one directory per rule. Twenty
near-identical suites on disk would drift from the models the first time a field changed.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from lakmus.errors import Code
from lakmus.suite.loader import MANIFEST_FILENAME, validate

CASES_FILE = "cases/pairs.jsonl"
SCORER_FILE = "scorers/pair_gap.py"


def _read_manifest(root: Path) -> dict[str, Any]:
    loaded = yaml.safe_load((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _edit_manifest(root: Path, change: Callable[[dict[str, Any]], None]) -> None:
    manifest = _read_manifest(root)
    change(manifest)
    (root / MANIFEST_FILENAME).write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _append_case(root: Path, case: str) -> None:
    with (root / CASES_FILE).open("a", encoding="utf-8") as handle:
        handle.write(f"{case}\n")


def _no_manifest(root: Path) -> None:
    (root / MANIFEST_FILENAME).unlink()


def _unparseable_manifest(root: Path) -> None:
    (root / MANIFEST_FILENAME).write_text("lakmus: [unclosed\n", encoding="utf-8")


def _unknown_field(root: Path) -> None:
    _edit_manifest(root, lambda manifest: manifest.update(treshold={"score_gap": {"max": 1}}))


def _missing_field(root: Path) -> None:
    _edit_manifest(root, lambda manifest: manifest.pop("language"))


def _malformed_id(root: Path) -> None:
    _edit_manifest(root, lambda manifest: manifest.update(id="RH/Tri CV"))


def _future_format(root: Path) -> None:
    _edit_manifest(root, lambda manifest: manifest.update(lakmus="0.2"))


def _missing_case_file(root: Path) -> None:
    (root / CASES_FILE).unlink()


def _no_gitattributes(root: Path) -> None:
    (root / ".gitattributes").unlink()


def _case_is_not_json(root: Path) -> None:
    _append_case(root, '{"id": "p-0003", oops}')


def _case_without_id(root: Path) -> None:
    _append_case(root, json.dumps({"variable": "nom"}))


def _duplicate_case_id(root: Path) -> None:
    _append_case(root, json.dumps({"id": "p-0001", "variable": "nom"}))


def _case_missing_group_field(root: Path) -> None:
    _append_case(root, json.dumps({"id": "p-0003"}))


def _no_cases(root: Path) -> None:
    (root / CASES_FILE).write_text("\n\n", encoding="utf-8")


def _cases_are_not_utf8(root: Path) -> None:
    (root / CASES_FILE).write_bytes(
        '{"id": "p-0001", "variable": "\u00e9chantillon"}\n'.encode("latin-1")
    )


def _cases_source_is_a_directory(root: Path) -> None:
    _edit_manifest(root, lambda manifest: manifest["cases"].update(source="cases"))


def _scorer_that_will_not_import(root: Path) -> None:
    (root / SCORER_FILE).write_text(
        "import lakmus_dependency_that_does_not_exist\n", encoding="utf-8"
    )


def _scorer_without_the_function(root: Path) -> None:
    (root / SCORER_FILE).write_text("other_name = 1\n", encoding="utf-8")


def _duplicate_metric_id(root: Path) -> None:
    def change(manifest: dict[str, Any]) -> None:
        manifest["metrics"].append({"id": "score_gap", "scorer": "builtin:exact_match"})

    _edit_manifest(root, change)


def _threshold_on_unknown_metric(root: Path) -> None:
    def change(manifest: dict[str, Any]) -> None:
        manifest["thresholds"] = {"scoregap": {"max": 0.05}}

    _edit_manifest(root, change)


def _threshold_without_bound(root: Path) -> None:
    def change(manifest: dict[str, Any]) -> None:
        manifest["thresholds"] = {"score_gap": {}}

    _edit_manifest(root, change)


def _forbidden_licence(root: Path) -> None:
    _edit_manifest(root, lambda manifest: manifest.update(license="GPL-3.0-only"))


def _model_target_without_input(root: Path) -> None:
    _edit_manifest(root, lambda manifest: manifest["target"].pop("input"))


@dataclass(frozen=True, slots=True)
class Breakage:
    code: Code
    apply: Callable[[Path], None]
    run_suite_code: bool = False


BREAKAGES = (
    Breakage(Code.NOT_A_SUITE, _no_manifest),
    Breakage(Code.MANIFEST_NOT_YAML, _unparseable_manifest),
    Breakage(Code.MANIFEST_UNKNOWN_FIELD, _unknown_field),
    Breakage(Code.MANIFEST_MISSING_FIELD, _missing_field),
    Breakage(Code.MANIFEST_INVALID_VALUE, _malformed_id),
    Breakage(Code.MANIFEST_FORMAT_UNSUPPORTED, _future_format),
    Breakage(Code.FILE_MISSING, _missing_case_file),
    Breakage(Code.GITATTRIBUTES_MISSING, _no_gitattributes),
    Breakage(Code.CASE_NOT_JSON, _case_is_not_json),
    Breakage(Code.CASE_WITHOUT_ID, _case_without_id),
    Breakage(Code.CASE_DUPLICATE_ID, _duplicate_case_id),
    Breakage(Code.CASE_MISSING_GROUP_FIELD, _case_missing_group_field),
    Breakage(Code.CASES_EMPTY, _no_cases),
    Breakage(Code.SCORER_IMPORT_FAILED, _scorer_that_will_not_import, run_suite_code=True),
    Breakage(Code.SCORER_NOT_CALLABLE, _scorer_without_the_function, run_suite_code=True),
    Breakage(Code.METRIC_DUPLICATE_ID, _duplicate_metric_id),
    Breakage(Code.THRESHOLD_UNKNOWN_METRIC, _threshold_on_unknown_metric),
    Breakage(Code.THRESHOLD_WITHOUT_BOUND, _threshold_without_bound),
    Breakage(Code.LICENCE_NOT_ALLOWED, _forbidden_licence),
    Breakage(Code.TARGET_INPUT_REQUIRED, _model_target_without_input),
)

COVERED_ELSEWHERE = frozenset({Code.PATH_ESCAPES_SUITE})
"""Needs a symbolic link, which is built at test time rather than committed."""


def _codes(root: Path, *, run_suite_code: bool) -> set[Code]:
    return {finding.code for finding in validate(root, run_suite_code=run_suite_code)}


def test_the_specification_example_validates(valid_suite: Path) -> None:
    assert validate(valid_suite, run_suite_code=True) == []


@pytest.mark.parametrize("breakage", BREAKAGES, ids=lambda item: str(item.code))
def test_one_breakage_reports_exactly_one_code(breakage: Breakage, valid_suite: Path) -> None:
    breakage.apply(valid_suite)

    assert _codes(valid_suite, run_suite_code=breakage.run_suite_code) == {breakage.code}


@pytest.mark.parametrize(
    ("break_it", "expected"),
    [
        pytest.param(_cases_are_not_utf8, Code.CASE_NOT_JSON, id="cases-not-utf8"),
        pytest.param(_cases_source_is_a_directory, Code.FILE_MISSING, id="cases-source-is-a-dir"),
    ],
)
def test_a_file_that_cannot_be_read_is_a_finding_not_a_crash(
    valid_suite: Path, break_it: Callable[[Path], None], expected: Code
) -> None:
    """A suite authored in a legacy editor must not take the tool down with it."""
    break_it(valid_suite)

    assert expected in _codes(valid_suite, run_suite_code=False)


def test_a_gitattributes_that_is_not_utf8_is_a_warning_not_a_crash(valid_suite: Path) -> None:
    (valid_suite / ".gitattributes").write_bytes("* -text\n# caf\u00e9\n".encode("latin-1"))

    assert _codes(valid_suite, run_suite_code=False) == {Code.GITATTRIBUTES_MISSING}


def test_scorer_findings_are_reported_alongside_other_errors(valid_suite: Path) -> None:
    """The one-pass promise has to hold even when something else is already wrong.

    Gating the scorer check on a fully valid suite meant an author fixed the licence, re-ran, and
    only then learned the scorer would not import.
    """
    _forbidden_licence(valid_suite)
    _scorer_that_will_not_import(valid_suite)

    assert _codes(valid_suite, run_suite_code=True) == {
        Code.LICENCE_NOT_ALLOWED,
        Code.SCORER_IMPORT_FAILED,
    }


def test_suite_code_behind_a_symlink_is_never_imported(valid_suite: Path, tmp_path: Path) -> None:
    """Consent to import a suite's scorers is not consent to run whatever a link points at."""
    marker = tmp_path / "executed"
    planted = tmp_path / "planted.py"
    planted.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n", encoding="utf-8"
    )
    scorer = valid_suite / "scorers" / "pair_gap.py"
    scorer.unlink()
    scorer.symlink_to(planted)

    codes = _codes(valid_suite, run_suite_code=True)

    assert not marker.exists()
    assert Code.PATH_ESCAPES_SUITE in codes


def test_a_symlink_is_refused(valid_suite: Path) -> None:
    outside = valid_suite.parent / "outside.j2"
    outside.write_text("{{ a }}\n", encoding="utf-8")
    (valid_suite / "prompts" / "linked.j2").symlink_to(outside)

    assert _codes(valid_suite, run_suite_code=False) == {Code.PATH_ESCAPES_SUITE}


def test_every_code_is_covered_by_a_test() -> None:
    """A new code without a test would otherwise ship unexercised."""
    covered = {breakage.code for breakage in BREAKAGES} | COVERED_ELSEWHERE

    assert set(Code) - covered == set()


def test_findings_are_reported_together_not_one_at_a_time(valid_suite: Path) -> None:
    _forbidden_licence(valid_suite)
    _duplicate_case_id(valid_suite)
    _no_gitattributes(valid_suite)

    assert _codes(valid_suite, run_suite_code=False) == {
        Code.LICENCE_NOT_ALLOWED,
        Code.CASE_DUPLICATE_ID,
        Code.GITATTRIBUTES_MISSING,
    }


def test_an_unknown_field_suggests_the_field_it_resembles(valid_suite: Path) -> None:
    _unknown_field(valid_suite)

    findings = validate(valid_suite)

    assert 'Did you mean "thresholds"?' in findings[0].message


def test_consent_is_asked_with_the_modules_it_would_import(valid_suite: Path) -> None:
    asked: list[tuple[str, ...]] = []

    def refuse(modules: tuple[str, ...]) -> bool:
        asked.append(modules)
        return False

    assert validate(valid_suite, run_suite_code=refuse) == []
    assert asked == [(SCORER_FILE,)]


def test_consent_is_not_asked_when_the_suite_has_no_code_of_its_own(valid_suite: Path) -> None:
    def change(manifest: dict[str, Any]) -> None:
        manifest["metrics"] = [{"id": "score_gap", "scorer": "builtin:pair_gap"}]
        manifest["thresholds"] = {"score_gap": {"max": 0.05}}

    _edit_manifest(valid_suite, change)
    (valid_suite / SCORER_FILE).unlink()
    asked: list[tuple[str, ...]] = []

    def refuse(modules: tuple[str, ...]) -> bool:
        asked.append(modules)
        return False

    assert validate(valid_suite, run_suite_code=refuse) == []
    assert asked == []
