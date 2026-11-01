"""Loading and validating a suite directory.

Validation runs in stages and collects findings rather than raising at the first problem, so that
one `lakmus validate` tells an author everything that is wrong. A stage that cannot run because an
earlier one failed is skipped, not guessed at: there is nothing useful to say about case ids when
the manifest itself did not parse.

Several checks that could be model validators live here instead — the licence allowed list,
`target.kind = model` needing `target.input`, a threshold needing a bound. Pydantic would report
them all as one generic value error, and the point of the code table is that each rejection names
its own rule.

A suite is untrusted input. Its Python is imported only when the caller grants it, and only for a
module that is a real file inside the suite directory.
"""

import difflib
import hashlib
import importlib.util
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, TypeAlias, cast

import yaml
from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from lakmus.errors import Code, Finding, SuiteInvalid
from lakmus.suite import hashing

if TYPE_CHECKING:  # pragma: no cover - a type, injected at runtime
    from lakmus.scorers.base import ScorerSpec

from lakmus.suite.models import (
    ALLOWED_LICENCES,
    FORMAT_VERSION,
    BuiltinScorer,
    Manifest,
    MetricSpec,
    SuiteScorer,
    declared_paths,
    parse_scorer_ref,
)

MANIFEST_FILENAME = "lakmus.yaml"
GITATTRIBUTES_LINE = "* -text"

SuiteCodeConsent: TypeAlias = bool | Callable[[tuple[str, ...]], bool]
"""Whether to import a suite's Python. A callable is asked only when there is something to import,
and is given the module paths, so that a caller can prompt with the real list."""

_LICENCE_REF_PREFIX = "LicenseRef-"

_PYDANTIC_CODES = {
    "extra_forbidden": Code.MANIFEST_UNKNOWN_FIELD,
    "missing": Code.MANIFEST_MISSING_FIELD,
}


@dataclass(frozen=True, slots=True)
class Suite:
    """A suite that validated, with the digests that identify it."""

    root: Path
    manifest: Manifest
    inputs_sha256: str
    content_sha256: str
    case_ids: tuple[str, ...]


def load(
    root: Path,
    *,
    run_suite_code: SuiteCodeConsent = False,
    scorers: "Mapping[str, ScorerSpec] | None" = None,
) -> Suite:
    """Validate a suite and return it. Raises `SuiteInvalid` carrying every finding."""
    findings, suite = inspect(root, run_suite_code=run_suite_code, scorers=scorers)
    if suite is None:
        raise SuiteInvalid(findings)
    return suite


def validate(
    root: Path,
    *,
    run_suite_code: SuiteCodeConsent = False,
    scorers: "Mapping[str, ScorerSpec] | None" = None,
) -> list[Finding]:
    """Every finding for a suite. No finding of severity `error` means the suite is valid."""
    return inspect(root, run_suite_code=run_suite_code, scorers=scorers)[0]


def inspect(
    root: Path,
    *,
    run_suite_code: SuiteCodeConsent = False,
    scorers: "Mapping[str, ScorerSpec] | None" = None,
) -> tuple[list[Finding], Suite | None]:
    """Run every stage that can run. The suite is returned only when nothing is wrong.

    `scorers` is the built-in registry, injected rather than imported so that loading a suite does
    not drag in the adapter stack. Without it the scorer checks are skipped, which is what a
    caller that only wants the manifest wants.
    """
    findings: list[Finding] = []
    manifest_path = root / MANIFEST_FILENAME

    if not manifest_path.is_file():
        findings.append(
            Finding(
                code=Code.NOT_A_SUITE,
                location=str(root),
                message=f"not a suite: no {MANIFEST_FILENAME} here.",
            )
        )
        return findings, None

    raw = _read_manifest(manifest_path, findings)
    if raw is None:
        return findings, None

    manifest = _parse_manifest(raw, findings)
    if manifest is None:
        return findings, None

    findings += _check_licence(manifest)
    findings += _check_target(manifest)
    findings += _check_metrics(manifest)
    path_findings = _check_paths(root, manifest)
    findings += path_findings
    findings += _check_gitattributes(root)

    case_ids = _check_cases(root, manifest, findings)
    findings += _check_builtin_scorers(manifest, scorers)
    findings += _check_scorers(
        root,
        manifest,
        consent=run_suite_code,
        rejected=_rejected_paths(path_findings),
        scorers=scorers,
    )

    if any(finding.severity == "error" for finding in findings):
        return findings, None

    return findings, Suite(
        root=root,
        manifest=manifest,
        inputs_sha256=hashing.inputs_sha256(root, manifest),
        content_sha256=hashing.content_sha256(root, manifest),
        case_ids=case_ids,
    )


def _read_manifest(path: Path, findings: list[Finding]) -> dict[str, Any] | None:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        findings.append(
            Finding(
                code=Code.MANIFEST_NOT_YAML,
                location=MANIFEST_FILENAME,
                message=f"not valid YAML: {_one_line(exc)}",
            )
        )
        return None
    if not isinstance(loaded, dict):
        findings.append(
            Finding(
                code=Code.MANIFEST_NOT_YAML,
                location=MANIFEST_FILENAME,
                message="the manifest must be a mapping of fields.",
            )
        )
        return None
    return loaded


def _parse_manifest(raw: dict[str, Any], findings: list[Finding]) -> Manifest | None:
    try:
        return Manifest.model_validate(raw)
    except ValidationError as exc:
        findings += [_finding_for(error, raw) for error in exc.errors()]
        return None


def _finding_for(error: ErrorDetails, raw: dict[str, Any]) -> Finding:
    location = _pointer(error["loc"])
    kind = str(error["type"])

    if kind == "literal_error" and error["loc"] == ("lakmus",):
        declared = raw.get("lakmus")
        return Finding(
            code=Code.MANIFEST_FORMAT_UNSUPPORTED,
            location=f"{MANIFEST_FILENAME}: {location}",
            message=f'format "{declared}" is not supported; this is lakmus {FORMAT_VERSION}.',
        )

    code = _PYDANTIC_CODES.get(kind, Code.MANIFEST_INVALID_VALUE)
    if code is Code.MANIFEST_UNKNOWN_FIELD:
        message = f'unknown field "{error["loc"][-1]}".{_suggestion(error["loc"])}'
    elif code is Code.MANIFEST_MISSING_FIELD:
        message = f'"{location}" is required.'
    else:
        message = f"{error['msg']}, got {error['input']!r}."
    return Finding(code=code, location=f"{MANIFEST_FILENAME}: {location}", message=message)


def _suggestion(loc: tuple[int | str, ...]) -> str:
    """Offer the nearest known field name for an unknown one.

    Walks down to the model the unknown field was found in, so that a typo nested under `target`
    is compared against the fields of `target` rather than those of the manifest.
    """
    model: type[BaseModel] = Manifest
    for step in loc[:-1]:
        field = model.model_fields.get(str(step))
        annotation = field.annotation if field is not None else None
        if not (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
            return ""
        model = annotation
    known = [field.alias or name for name, field in model.model_fields.items()]
    close = difflib.get_close_matches(str(loc[-1]), known, n=1, cutoff=0.6)
    return f' Did you mean "{close[0]}"?' if close else ""


def _check_licence(manifest: Manifest) -> list[Finding]:
    if manifest.license in ALLOWED_LICENCES or manifest.license.startswith(_LICENCE_REF_PREFIX):
        return []
    allowed = ", ".join(ALLOWED_LICENCES)
    return [
        Finding(
            code=Code.LICENCE_NOT_ALLOWED,
            location=f"{MANIFEST_FILENAME}: license",
            message=(
                f'licence "{manifest.license}" is not redistributable by the hub. '
                f"Use one of {allowed}, or a {_LICENCE_REF_PREFIX} identifier for a suite you "
                f"will never publish."
            ),
        )
    ]


def _check_target(manifest: Manifest) -> list[Finding]:
    if manifest.target.kind == "model" and manifest.target.input is None:
        return [
            Finding(
                code=Code.TARGET_INPUT_REQUIRED,
                location=f"{MANIFEST_FILENAME}: target",
                message='target.kind "model" requires target.input.',
            )
        ]
    return []


def _check_metrics(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for metric in manifest.metrics:
        if metric.id in seen:
            findings.append(
                Finding(
                    code=Code.METRIC_DUPLICATE_ID,
                    location=f"{MANIFEST_FILENAME}: metrics",
                    message=f'metric id "{metric.id}" is declared twice.',
                )
            )
        seen.add(metric.id)

    for name, threshold in manifest.thresholds.items():
        if name not in seen:
            close = difflib.get_close_matches(name, sorted(seen), n=1, cutoff=0.6)
            hint = f' Did you mean "{close[0]}"?' if close else ""
            findings.append(
                Finding(
                    code=Code.THRESHOLD_UNKNOWN_METRIC,
                    location=f"{MANIFEST_FILENAME}: thresholds.{name}",
                    message=f'"{name}" is not a declared metric.{hint}',
                )
            )
        if threshold.min is None and threshold.max is None:
            findings.append(
                Finding(
                    code=Code.THRESHOLD_WITHOUT_BOUND,
                    location=f"{MANIFEST_FILENAME}: thresholds.{name}",
                    message='needs "min", "max", or both.',
                )
            )
    return findings


def _check_paths(root: Path, manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    resolved_root = root.resolve()

    for field, relative in declared_paths(manifest):
        target = root / relative
        if not target.is_file():
            what = "is a directory" if target.is_dir() else "does not exist"
            findings.append(
                Finding(
                    code=Code.FILE_MISSING,
                    location=f"{MANIFEST_FILENAME}: {field}",
                    message=f'"{relative}" {what}; a file is required.',
                )
            )
            continue
        if not target.resolve().is_relative_to(resolved_root):
            findings.append(
                Finding(
                    code=Code.PATH_ESCAPES_SUITE,
                    location=f"{MANIFEST_FILENAME}: {field}",
                    message=f'"{relative}" resolves outside the suite.',
                )
            )

    for relative in hashing.symlinks(root):
        findings.append(
            Finding(
                code=Code.PATH_ESCAPES_SUITE,
                location=relative,
                message=(
                    "symbolic links are not part of a suite: no digest covers one, so a link "
                    "could change a result without changing inputs_sha256."
                ),
            )
        )
    return findings


def _rejected_paths(path_findings: Sequence[Finding]) -> frozenset[str]:
    """Locations a path check already refused, so that no later stage touches those files."""
    return frozenset(
        finding.location.split(": ", 1)[-1]
        for finding in path_findings
        if finding.code in {Code.FILE_MISSING, Code.PATH_ESCAPES_SUITE}
    )


def _check_gitattributes(root: Path) -> list[Finding]:
    path = root / ".gitattributes"
    present = False
    if path.is_file():
        try:
            present = GITATTRIBUTES_LINE in path.read_text(encoding="utf-8").split("\n")
        except UnicodeDecodeError:
            present = False
    if present:
        return []
    return [
        Finding(
            code=Code.GITATTRIBUTES_MISSING,
            location=".gitattributes",
            message=(
                f'add "{GITATTRIBUTES_LINE}" so that a checkout on Windows produces the same '
                f"digests as one on Linux."
            ),
            severity="warning",
        )
    ]


def _check_cases(root: Path, manifest: Manifest, findings: list[Finding]) -> tuple[str, ...]:
    source = root / manifest.cases.source
    if not source.is_file():
        return ()

    grouped_by = sorted({field for metric in manifest.metrics for field in metric.group_by})
    first_seen: dict[str, int] = {}

    try:
        lines = source.read_text(encoding="utf-8").split("\n")
    except UnicodeDecodeError as exc:
        findings.append(
            Finding(
                code=Code.CASE_NOT_JSON,
                location=manifest.cases.source,
                message=f"not valid UTF-8: {_one_line(exc)}",
            )
        )
        return ()

    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        location = f"{manifest.cases.source}:{number}"
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            findings.append(
                Finding(
                    code=Code.CASE_NOT_JSON,
                    location=location,
                    message=f"not valid JSON: {_one_line(exc)}",
                )
            )
            continue
        if not isinstance(case, dict):
            findings.append(
                Finding(
                    code=Code.CASE_NOT_JSON,
                    location=location,
                    message="each line must be a JSON object.",
                )
            )
            continue

        identifier = case.get("id")
        if not isinstance(identifier, str) or not identifier:
            findings.append(
                Finding(
                    code=Code.CASE_WITHOUT_ID,
                    location=location,
                    message='case is missing a non-empty string "id".',
                )
            )
            continue
        if identifier in first_seen:
            findings.append(
                Finding(
                    code=Code.CASE_DUPLICATE_ID,
                    location=location,
                    message=(
                        f'case id "{identifier}" was already used at line {first_seen[identifier]}.'
                    ),
                )
            )
            continue
        first_seen[identifier] = number

        for field in grouped_by:
            if field not in case:
                metric = next(m.id for m in manifest.metrics if field in m.group_by)
                findings.append(
                    Finding(
                        code=Code.CASE_MISSING_GROUP_FIELD,
                        location=location,
                        message=f'case has no field "{field}", which metric "{metric}" groups by.',
                    )
                )

    if not first_seen:
        findings.append(
            Finding(
                code=Code.CASES_EMPTY,
                location=manifest.cases.source,
                message="a suite needs at least one case; this file holds none.",
            )
        )

    return tuple(first_seen)


def _scorer_modules(
    root: Path, manifest: Manifest, rejected: frozenset[str]
) -> list[tuple[str, Path]]:
    """Suite scorer modules that are safe to import: a real file, inside the suite, not refused.

    A module whose path already drew a finding is left alone. Executing code reached through a
    symbolic link that leaves the suite would be exactly the escape the finding was reporting.
    """
    resolved_root = root.resolve()
    modules: dict[str, Path] = {}
    for metric in manifest.metrics:
        parsed = parse_scorer_ref(metric.scorer)
        if isinstance(parsed, BuiltinScorer) or parsed.module in rejected:
            continue
        path = root / parsed.module
        if path.is_symlink() or not path.is_file():
            continue
        if not path.resolve().is_relative_to(resolved_root):
            continue
        modules[parsed.module] = path
    return sorted(modules.items())


def _granted(consent: SuiteCodeConsent, modules: tuple[str, ...]) -> bool:
    return consent if isinstance(consent, bool) else consent(modules)


def _check_builtin_scorers(
    manifest: Manifest, scorers: "Mapping[str, ScorerSpec] | None"
) -> list[Finding]:
    """Check every `builtin:` reference and its options, without importing anything."""
    if scorers is None:
        return []

    findings: list[Finding] = []
    for metric in manifest.metrics:
        parsed = parse_scorer_ref(metric.scorer)
        if not isinstance(parsed, BuiltinScorer):
            continue
        spec = scorers.get(parsed.name)
        if spec is None:
            close = difflib.get_close_matches(parsed.name, sorted(scorers), n=1, cutoff=0.6)
            hint = f' Did you mean "{close[0]}"?' if close else ""
            findings.append(
                Finding(
                    code=Code.SCORER_UNKNOWN_BUILTIN,
                    location=f"{MANIFEST_FILENAME}: metrics[{metric.id}].scorer",
                    message=(
                        f'there is no built-in scorer named "{parsed.name}".{hint} '
                        f"Known: {', '.join(sorted(scorers))}."
                    ),
                )
            )
            continue
        findings += _check_options(metric.id, spec, metric.options)
    return findings


def _check_options(
    metric_id: str, spec: "ScorerSpec", options: Mapping[str, object]
) -> list[Finding]:
    """Compare a metric's `with` block against the scorer's keyword-only parameters."""
    findings: list[Finding] = []
    location = f"{MANIFEST_FILENAME}: metrics[{metric_id}].with"

    for name in sorted(set(options) - spec.options):
        close = difflib.get_close_matches(name, sorted(spec.options), n=1, cutoff=0.6)
        hint = f' Did you mean "{close[0]}"?' if close else ""
        accepted = ", ".join(sorted(spec.options)) or "none"
        findings.append(
            Finding(
                code=Code.SCORER_OPTION_REFUSED,
                location=location,
                message=f'"{spec.name}" accepts no option "{name}".{hint} Accepted: {accepted}.',
            )
        )

    missing = sorted(spec.required_options - set(options))
    if missing:
        findings.append(
            Finding(
                code=Code.SCORER_OPTION_REFUSED,
                location=location,
                message=f'"{spec.name}" requires {", ".join(missing)}.',
            )
        )

    for problem in spec.option_problems(options):
        # A name that exists with a value the scorer does not understand is the dangerous case:
        # `expect: matched` used to pass here and then invert the metric.
        findings.append(
            Finding(
                code=Code.SCORER_OPTION_REFUSED,
                location=location,
                message=f'"{spec.name}" {problem}.',
            )
        )
    return findings


def _check_scorers(
    root: Path,
    manifest: Manifest,
    *,
    consent: SuiteCodeConsent,
    rejected: frozenset[str] = frozenset(),
    scorers: "Mapping[str, ScorerSpec] | None" = None,
) -> list[Finding]:
    """Import every suite scorer module, and check the function it names carries `@scorer`."""
    modules = _scorer_modules(root, manifest, rejected)
    if not modules or not _granted(consent, tuple(name for name, _ in modules)):
        return []

    wanted: dict[str, set[str]] = {}
    for metric in manifest.metrics:
        parsed = parse_scorer_ref(metric.scorer)
        if isinstance(parsed, SuiteScorer):
            wanted.setdefault(parsed.module, set()).add(parsed.function)

    by_module: dict[str, list[MetricSpec]] = {}
    for metric in manifest.metrics:
        parsed = parse_scorer_ref(metric.scorer)
        if isinstance(parsed, SuiteScorer):
            by_module.setdefault(parsed.module, []).append(metric)

    findings: list[Finding] = []
    for relative, path in modules:
        module = _import_module(path, relative, findings)
        if module is None:
            continue
        for function_name in sorted(wanted.get(relative, set())):
            function = getattr(module, function_name, None)
            if not callable(function):
                findings.append(
                    Finding(
                        code=Code.SCORER_NOT_CALLABLE,
                        location=relative,
                        message=f'"{function_name}" is missing or is not callable.',
                    )
                )
                continue
            spec = _spec_of(function)
            if spec is None:
                findings.append(
                    Finding(
                        code=Code.SCORER_NOT_CALLABLE,
                        location=relative,
                        message=(
                            f'"{function_name}" must be decorated with @scorer. Without it there '
                            f"is no direction, no arity and no option schema."
                        ),
                    )
                )
                continue
            for metric in by_module.get(relative, []):
                if _suite_scorer_of(metric) == SuiteScorer(relative, function_name):
                    findings += _check_options(metric.id, spec, metric.options)
    return findings


SCORER_SPEC_ATTRIBUTE = "__lakmus_scorer__"
"""The attribute `@scorer` attaches. Named here rather than imported from `lakmus.scorers`:
importing it to read one attribute would pull the whole adapter stack into a manifest load, and
the architecture says this module depends on nothing."""


def _suite_scorer_of(metric: MetricSpec) -> SuiteScorer | None:
    parsed = parse_scorer_ref(metric.scorer)
    return parsed if isinstance(parsed, SuiteScorer) else None


def _spec_of(function: object) -> "ScorerSpec | None":
    """Read the spec `@scorer` attached, without importing the package that defines it."""
    candidate = getattr(function, SCORER_SPEC_ATTRIBUTE, None)
    if candidate is None or not hasattr(candidate, "required_options"):
        return None
    return cast("ScorerSpec", candidate)


def _import_module(path: Path, relative: str, findings: list[Finding]) -> ModuleType | None:
    # A digest, not hash(): PYTHONHASHSEED makes hash() vary between processes, and this project
    # injects randomness rather than inheriting it.
    fingerprint = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
    name = f"lakmus_suite_scorer_{path.stem}_{fingerprint}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        findings.append(
            Finding(
                code=Code.SCORER_IMPORT_FAILED,
                location=relative,
                message="cannot be loaded as a Python module.",
            )
        )
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except KeyboardInterrupt:
        del sys.modules[name]
        raise
    except BaseException as exc:
        # Suite code may raise anything at import, SystemExit included; it must not be able to
        # end `lakmus validate`.
        del sys.modules[name]
        findings.append(
            Finding(
                code=Code.SCORER_IMPORT_FAILED,
                location=relative,
                message=f"import failed: {type(exc).__name__}: {_one_line(exc)}",
            )
        )
        return None
    return module


def _pointer(loc: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in loc) or "(root)"


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())
