"""`lakmus validate` and `lakmus init` as a caller in CI experiences them.

The exit code is the contract: 0 valid, 2 not. These tests run without a terminal, which is the
case that matters most — a CI job must never hang on a prompt, and must never run a stranger's
code because nobody was there to say no.
"""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from lakmus.cli.main import app
from lakmus.errors import Code, ExitCode
from lakmus.suite.loader import MANIFEST_FILENAME, validate

runner = CliRunner()


def _output(result: object) -> str:
    """Everything the command printed, wherever it printed it."""
    streams = [getattr(result, "output", "")]
    try:
        streams.append(result.stderr)  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    return "".join(streams)


def test_a_valid_suite_exits_zero(valid_suite: Path) -> None:
    result = runner.invoke(app, ["validate", str(valid_suite), "--no-code"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "valid" in _output(result)


def test_an_invalid_suite_exits_two_and_names_the_code(valid_suite: Path) -> None:
    (valid_suite / "cases" / "pairs.jsonl").unlink()

    result = runner.invoke(app, ["validate", str(valid_suite), "--no-code"])

    assert result.exit_code == ExitCode.USAGE
    assert Code.FILE_MISSING in _output(result)


def test_a_warning_alone_still_exits_zero(valid_suite: Path) -> None:
    (valid_suite / ".gitattributes").unlink()

    result = runner.invoke(app, ["validate", str(valid_suite), "--no-code"])

    assert result.exit_code == ExitCode.SUCCESS
    assert Code.GITATTRIBUTES_MISSING in _output(result)


def test_contradictory_flags_are_refused(valid_suite: Path) -> None:
    result = runner.invoke(app, ["validate", str(valid_suite), "--yes", "--no-code"])

    assert result.exit_code == ExitCode.USAGE
    assert "contradict" in _output(result)


def test_without_a_terminal_suite_code_is_not_imported(valid_suite: Path) -> None:
    (valid_suite / "scorers" / "pair_gap.py").write_text(
        "raise RuntimeError('this must not run')\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["validate", str(valid_suite)])

    assert result.exit_code == ExitCode.SUCCESS
    assert "Not checking" in _output(result)


def test_yes_imports_suite_code_and_reports_what_it_finds(valid_suite: Path) -> None:
    (valid_suite / "scorers" / "pair_gap.py").write_text(
        "import lakmus_dependency_that_does_not_exist\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["validate", str(valid_suite), "--yes"])

    assert result.exit_code == ExitCode.USAGE
    assert Code.SCORER_IMPORT_FAILED in _output(result)


def test_a_missing_directory_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path / "nowhere")])

    assert result.exit_code == ExitCode.USAGE


def test_init_scaffolds_a_suite_that_validates(tmp_path: Path) -> None:
    root = tmp_path / "scaffold"

    result = runner.invoke(app, ["init", "rh/tri-cv", "--dir", str(root), "--language", "fr"])

    assert result.exit_code == ExitCode.SUCCESS
    assert validate(root, run_suite_code=False) == []


def test_init_defaults_the_directory_to_the_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "rh/tri-cv"])

    assert result.exit_code == ExitCode.SUCCESS
    assert (tmp_path / "rh" / "tri-cv" / MANIFEST_FILENAME).is_file()


def test_init_refuses_a_directory_that_is_not_empty(tmp_path: Path) -> None:
    root = tmp_path / "occupied"
    root.mkdir()
    (root / "something").write_text("already here\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "rh/tri-cv", "--dir", str(root)])

    assert result.exit_code == ExitCode.USAGE
    assert "not empty" in _output(result)


@pytest.mark.parametrize("kind", ["agent", "mcp"])
def test_init_writes_no_prompt_for_a_target_that_takes_none(tmp_path: Path, kind: str) -> None:
    root = tmp_path / kind

    result = runner.invoke(app, ["init", f"ops/{kind}-suite", "--dir", str(root), "--kind", kind])

    assert result.exit_code == ExitCode.SUCCESS
    assert not (root / "prompts").exists()
    manifest = yaml.safe_load((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert "input" not in manifest["target"]


def test_no_code_says_what_it_skipped(valid_suite: Path) -> None:
    """A CI log that reads `valid.` must not hide that the scorers were never imported."""
    result = runner.invoke(app, ["validate", str(valid_suite), "--no-code"])

    assert result.exit_code == ExitCode.SUCCESS
    output = _output(result)
    assert "Not checking" in output
    assert "scorers/pair_gap.py" in output


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(["RH/Tri CV"], id="id-is-not-kebab-case"),
        pytest.param(["../escaped/suite"], id="id-tries-to-leave-the-directory"),
        pytest.param(["rh/tri-cv", "--kind", "banana"], id="unknown-target-kind"),
        pytest.param(["rh/tri-cv", "--license", "GPL-3.0-only"], id="licence-not-allowed"),
        pytest.param(["rh/tri-cv", "--language", "français"], id="language-is-not-bcp47"),
    ],
)
def test_init_refuses_a_bad_argument_before_writing_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    """A mistyped argument is a usage error, not a half-written directory and exit code 3."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", *arguments, "--dir", "out"])

    assert result.exit_code == ExitCode.USAGE
    assert list(tmp_path.iterdir()) == []


def test_init_accepts_a_private_licence_reference(tmp_path: Path) -> None:
    root = tmp_path / "private"

    result = runner.invoke(
        app, ["init", "rh/interne", "--dir", str(root), "--license", "LicenseRef-Acme-Internal"]
    )

    assert result.exit_code == ExitCode.SUCCESS
    assert validate(root, run_suite_code=False) == []
