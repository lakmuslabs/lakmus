"""Pydantic models for `lakmus.yaml`, format 0.1.

The models describe the file as an author writes it, not a convenient in-memory shape: the JSON
Schema generated from them is published for other implementations to validate against, so field
names, aliases and patterns are part of the contract.

`extra="forbid"` throughout. A field name that is silently ignored is how a suite ends up
measuring something other than what its author intended.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

FORMAT_VERSION = "0.1"

ALLOWED_LICENCES = (
    "CC0-1.0",
    "CC-BY-4.0",
    "CC-BY-SA-4.0",
    "ODbL-1.0",
    "Apache-2.0",
    "MIT",
    "BSD-3-Clause",
)
"""Licences a suite may carry. A suite the hub cannot redistribute is refused at authoring time
rather than at publication. A private suite uses a `LicenseRef-` identifier."""

_KEBAB = r"[a-z0-9]+(?:-[a-z0-9]+)*"

SuiteId = Annotated[str, StringConstraints(pattern=rf"^{_KEBAB}/{_KEBAB}$")]
"""`<domain>/<name>`. Lowercase ASCII, because an id becomes a public URL and never changes. It
may transliterate the suite's own language: `rh/equite-tri-cv-fr` is intentional."""

SemVer = Annotated[
    str,
    StringConstraints(
        pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
        r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
        r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
        r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
    ),
]
Slug = Annotated[str, StringConstraints(pattern=rf"^{_KEBAB}$")]
MetricId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")]
LanguageTag = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*|x(?:-[A-Za-z0-9]{1,8})+)$"),
]
"""BCP-47. Single-character subtags are allowed because extensions use them
(`fr-FR-u-co-phonebk`), as does the private-use form (`x-internal`)."""
SpdxId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")]
"""Syntax only. Membership of ALLOWED_LICENCES is checked by the loader, so that a refused licence
reports LKM-601 rather than a generic type error."""

RelPath = Annotated[str, StringConstraints(pattern=r"^(?!/)(?!.*(?:^|/)\.\.?(?:/|$))[^\0]+$")]
"""A path inside the suite. Absolute paths and `..` segments are rejected here; the loader also
resolves symlinks, because a manifest may come from a stranger's repository."""

ScorerRef = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?:builtin:[a-z0-9_]+|[^\s:]+\.py:[A-Za-z_][A-Za-z0-9_]*)$",
    ),
]
"""`builtin:<name>` or `<module>.py:<function>`. Kept as a string so that the published JSON
Schema describes what an author actually writes; `parse_scorer_ref` turns it into one of the two
shapes below."""


@dataclass(frozen=True, slots=True)
class BuiltinScorer:
    name: str


@dataclass(frozen=True, slots=True)
class SuiteScorer:
    module: str
    function: str


def parse_scorer_ref(ref: str) -> BuiltinScorer | SuiteScorer:
    """Split a validated `ScorerRef`.

    Raises `ValueError` for a string that never went through the model.
    """
    head, _, tail = ref.partition(":")
    if not tail:
        raise ValueError(f"not a scorer reference: {ref!r}")
    if head == "builtin":
        return BuiltinScorer(name=tail)
    return SuiteScorer(module=head, function=tail)


class _Strict(BaseModel):
    # python-re rather than the default Rust engine: RelPath reads far better with a lookahead,
    # and the published JSON Schema targets ECMA-262, which supports one.
    model_config = ConfigDict(frozen=True, extra="forbid", regex_engine="python-re")


class OutputSpec(_Strict):
    """What the target is expected to return.

    `schema` is a flat shorthand, not JSON Schema: every declared field is required, there is no
    nesting and no enums. Compiling it to JSON Schema, so that a run file carries a form no
    consumer has to learn, belongs to the milestone that defines the run file.
    """

    type: Literal["text", "json"] = "text"
    schema_: dict[str, Literal["string", "number", "integer", "boolean"]] | None = Field(
        default=None, alias="schema"
    )


class TargetSpec(_Strict):
    kind: Literal["model", "agent", "mcp"]
    input: RelPath | None = None
    output: OutputSpec = OutputSpec()


class CasesSpec(_Strict):
    source: RelPath
    holdout: RelPath | None = None
    """Reserved in 0.1: it validates as a path, is covered by neither digest, and is never read."""
    sample: Annotated[int, Field(gt=0)] | None = None
    """`null` means every case."""


OptionScalar = str | int | float | bool
OptionValue = OptionScalar | list[OptionScalar]


class MetricSpec(_Strict):
    id: MetricId
    scorer: ScorerRef
    group_by: list[str] = []
    options: dict[str, OptionValue] = Field(default_factory=dict, alias="with")
    """What the scorer is configured with: a regex pattern, which case field holds the expected
    answer, a judge model. Part of `metrics`, so part of `inputs_sha256`: changing a pattern is
    changing the measurement, and two runs under different patterns are not comparable."""


class Threshold(_Strict):
    min: float | None = None
    max: float | None = None


class RunSpec(_Strict):
    repeats: Annotated[int, Field(gt=0)] = 1
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.0
    seed: int = 0
    timeout_s: Annotated[int, Field(gt=0)] = 60


class Manifest(_Strict):
    """`lakmus.yaml`.

    `domain` is not a field: it is read from `id`. Two sources for one fact are free to disagree.
    """

    lakmus: Literal["0.1"]
    id: SuiteId
    version: SemVer
    title: Annotated[str, Field(min_length=1, max_length=120)]
    language: LanguageTag
    license: SpdxId
    target: TargetSpec
    cases: CasesSpec
    metrics: Annotated[list[MetricSpec], Field(min_length=1)]
    task: Slug | None = None
    authors: list[str] = []
    maintainers: list[str] = []
    risk_level: Literal["low", "medium", "high"] = "medium"
    thresholds: dict[MetricId, Threshold] = {}
    run: RunSpec = RunSpec()

    @property
    def domain(self) -> str:
        return self.id.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.id.split("/", 1)[1]


def declared_paths(manifest: Manifest) -> list[tuple[str, str]]:
    """Every file the manifest names, as (field, relative path).

    One list, used both to check that the files exist and to decide what the comparability digest
    covers. Two lists would be free to disagree, and a prompt missing from the digest is the kind
    of disagreement nobody notices until two results are compared that should not have been.
    """
    declared = [("cases.source", manifest.cases.source)]
    if manifest.target.input is not None:
        declared.append(("target.input", manifest.target.input))
    for metric in manifest.metrics:
        parsed = parse_scorer_ref(metric.scorer)
        if isinstance(parsed, SuiteScorer):
            declared.append((f"metrics[{metric.id}].scorer", parsed.module))
    return declared
