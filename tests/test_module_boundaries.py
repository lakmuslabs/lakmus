"""The dependency rule, enforced rather than documented.

`suite`, `targets`, `scorers` and `results` describe data and talk to the outside world; `engine`
orchestrates them and `cli` drives it. An import in the other direction turns a layered design into
a cycle, and it costs nothing to catch here instead of in review.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
LOWER_PACKAGES = ("suite", "targets", "scorers", "results")
UPPER_PACKAGES = ("lakmus.engine", "lakmus.cli")


def _package_of(module_path: Path) -> str:
    parts = module_path.relative_to(SRC).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
        return ".".join(parts)
    return ".".join(parts[:-1])


def _resolve(package: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    ancestors = package.split(".")[: -(node.level - 1) or None]
    return ".".join([*ancestors, node.module] if node.module else ancestors)


def _imports_of(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    package = _package_of(module_path)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(_resolve(package, node))
    return imported


@pytest.mark.parametrize("package", LOWER_PACKAGES)
def test_package_does_not_import_the_layers_above_it(package: str) -> None:
    offenders = [
        f"{path.relative_to(SRC)} imports {name}"
        for path in sorted((SRC / "lakmus" / package).rglob("*.py"))
        for name in sorted(_imports_of(path))
        if any(name == upper or name.startswith(f"{upper}.") for upper in UPPER_PACKAGES)
    ]
    assert not offenders, "\n".join(offenders)


def test_every_package_named_in_the_rule_exists() -> None:
    """Without this, renaming a package would make the rule above pass by scanning nothing."""
    missing = [
        name
        for name in (*LOWER_PACKAGES, "engine", "cli", "report")
        if not (SRC / "lakmus" / name / "__init__.py").is_file()
    ]
    assert not missing
