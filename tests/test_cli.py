from typer.testing import CliRunner

from lakmus import __version__
from lakmus.cli.main import app

runner = CliRunner()


def test_version_flag_prints_the_installed_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_bare_invocation_shows_help_and_fails() -> None:
    result = runner.invoke(app, [])

    assert result.exit_code != 0
    assert "Run domain test suites" in result.stdout
