import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "suites"


@pytest.fixture
def valid_suite(tmp_path: Path) -> Path:
    """A copy of the valid fixture, free to be mutated by a test."""
    root = tmp_path / "suite"
    shutil.copytree(FIXTURES / "valid", root)
    return root
