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
LanguageTag = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")]
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
    nesting and no enums. The loader compiles it to JSON Schema, and the compiled form is what a
    run file carries, so a consumer never has to learn this notation.
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


class MetricSpec(_Strict):
    id: MetricId
    scorer: ScorerRef
    group_by: list[str] = []


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
