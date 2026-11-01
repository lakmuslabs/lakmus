"""What a scorer is, and what it returns.

A scorer turns one answer into one number. Three rules make the numbers trustworthy, and the
first is the one that matters most.

**An unscoreable case has no score.** A scorer never raises for a bad answer, but it returns a
`Score` with no value, and the engine counts that case as an error rather than averaging it in.
Returning the worst value instead — as is tempting — makes a target that answers garbage score
*perfectly* on any metric where lower is better, which is every fairness metric worth writing.

**Deterministic and pure.** No network, no clock, no randomness, no global state. A scorer that
calls a model says so with `deterministic=False`, and its judge, rubric and prompt are options, so
they land in the comparability digest.

**Explainable.** `details` carries what a human needs to understand a case: expected against got,
the items that matched, the reason it could not be scored.
"""

import inspect
import math
import typing
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from lakmus.errors import ScorerError
from lakmus.targets.base import TargetOutput

Arity = Literal["single", "pair"]

SCORER_SPEC_ATTRIBUTE = "__lakmus_scorer__"

NORMALISERS: Mapping[str, Callable[[str], str]] = {
    "trim": str.strip,
    "collapse_space": lambda text: " ".join(text.split()),
    "nfc": lambda text: unicodedata.normalize("NFC", text),
    "lower": str.casefold,
}
"""Applied in the order the suite lists them.

There is no accent-stripping step. In French `é` and `e` are different answers, and a scorer that
quietly equates them measures something other than what its author wrote. A suite that wants it
writes its own scorer and says so in its README.
"""


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Score(_Strict):
    """One case's number, or the reason there isn't one."""

    value: float | None
    details: Mapping[str, Any] = {}

    @model_validator(mode="after")
    def _details_are_writable_to_a_file(self) -> Self:
        """`details` reaches the cases file for every case.

        `allow_inf_nan` guards `value` but not what a scorer puts in `details`, and `json.dumps`
        spells an infinity as a bare token no JSON parser accepts.
        """
        offenders = sorted(_non_finite_paths(self.details))
        if offenders:
            raise ValueError(f"details hold a value that is not finite: {', '.join(offenders)}")
        return self

    @property
    def scored(self) -> bool:
        return self.value is not None

    @classmethod
    def of(cls, value: float, **details: Any) -> Self:
        return cls(value=value, details=details)

    @classmethod
    def unscoreable(cls, reason: str, **details: Any) -> Self:
        """No number, and why.

        The engine counts this in `n_errors` and leaves it out of the mean, so a broken answer
        cannot pass a threshold by scoring zero on a metric where zero is the best value.
        """
        return cls(value=None, details={"error": reason, **details})


class Case(_Strict):
    """One case as the suite wrote it.

    Every field beyond `id` is the suite's own, so they stay in a mapping rather than becoming
    attributes: a scorer reads `case["variable"]`, and a typo is a `KeyError` at authoring time
    instead of an attribute that quietly exists.
    """

    id: str
    data: Mapping[str, Any] = {}

    def __getitem__(self, field: str) -> Any:
        return self.data[field]

    def get(self, field: str, default: Any = None) -> Any:
        return self.data.get(field, default)


class PairOutputs(_Strict):
    """The two answers of a counterfactual pair."""

    a: TargetOutput
    b: TargetOutput


@dataclass(frozen=True, slots=True)
class ScorerSpec:
    """A scorer and everything a suite, a report or a validator needs to know about it."""

    name: str
    arity: Arity
    higher_is_better: bool
    deterministic: bool
    options: frozenset[str]
    required_options: frozenset[str]
    option_types: Mapping[str, Any]
    """The annotation of each option, so that a value can be checked and not only a name. Without
    it `expect: matched` passes validation and then reports 1.0 for every answer that does *not*
    match."""
    function: Callable[..., Score]

    def call(
        self, case: Case, answer: TargetOutput | PairOutputs, options: Mapping[str, Any]
    ) -> Score:
        """Run the scorer, having checked its options.

        An unknown or missing option is a fault in the suite, the same for every case, so it is
        raised rather than recorded against one case.
        """
        unknown = sorted(set(options) - self.options)
        if unknown:
            raise ScorerError(f"{self.name}: unknown option(s): {', '.join(unknown)}")
        missing = sorted(self.required_options - set(options))
        if missing:
            raise ScorerError(f"{self.name}: missing option(s): {', '.join(missing)}")
        for problem in self.option_problems(options):
            raise ScorerError(f"{self.name}: {problem}")
        return self.function(case, answer, **options)

    def option_problems(self, options: Mapping[str, Any]) -> list[str]:
        """What is wrong with these option values, as sentences.

        Names alone are not enough. An option is the only configuration a scorer has and it sits
        inside the comparability digest, so a value the scorer does not understand has to be
        refused rather than quietly reinterpreted.
        """
        problems: list[str] = []
        for name, value in sorted(options.items()):
            annotation = self.option_types.get(name)
            if annotation is None:
                continue
            problem = _value_problem(annotation, value)
            if problem is not None:
                problems.append(f'option "{name}" {problem}')
        return problems


def scorer(
    *,
    name: str,
    higher_is_better: bool,
    deterministic: bool = True,
    arity: Arity = "single",
) -> Callable[[Callable[..., Score]], Callable[..., Score]]:
    """Mark a function as a scorer and record what it accepts.

    The keyword-only parameters are the option schema: there is no second place to declare them,
    so `lakmus validate` can reject a misspelled option by reading the signature a reader is
    already looking at.
    """

    def decorate(function: Callable[..., Score]) -> Callable[..., Score]:
        optional, required = _keyword_only(function)
        spec = ScorerSpec(
            name=name,
            arity=arity,
            higher_is_better=higher_is_better,
            deterministic=deterministic,
            options=optional | required,
            required_options=required,
            option_types=_option_types(function, optional | required),
            function=function,
        )
        setattr(function, SCORER_SPEC_ATTRIBUTE, spec)
        return function

    return decorate


def spec_of(function: object) -> ScorerSpec | None:
    """The spec a `@scorer` attached, or `None` for a plain callable."""
    candidate = getattr(function, SCORER_SPEC_ATTRIBUTE, None)
    return candidate if isinstance(candidate, ScorerSpec) else None


def _keyword_only(function: Callable[..., Score]) -> tuple[frozenset[str], frozenset[str]]:
    optional: set[str] = set()
    required: set[str] = set()
    for parameter in inspect.signature(function).parameters.values():
        if parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
            continue
        if parameter.default is inspect.Parameter.empty:
            required.add(parameter.name)
        else:
            optional.add(parameter.name)
    return frozenset(optional), frozenset(required)


def normalise_text(text: str, steps: Sequence[str]) -> str:
    """Apply the named normalisation steps in order.

    An unknown step is the same mistake for every case, so it is raised.
    """
    result = text
    for step in steps:
        transform = NORMALISERS.get(step)
        if transform is None:
            raise ScorerError(
                f"unknown normalisation step {step!r}; known steps: {', '.join(NORMALISERS)}"
            )
        result = transform(result)
    return result


def describable(value: Any) -> Any:
    """A value safe to put in `details`.

    A scorer that echoes a raw answer back for a human to read must not be stopped by it: an
    infinity in `details` fails the check that keeps the cases file valid JSON, which would turn
    declining a case into raising — the one thing a scorer must never do.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, Mapping):
        return {key: describable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [describable(item) for item in value]
    return value


def text_of(output: TargetOutput) -> str | None:
    """The answer as text, or `None` when it is not text."""
    return output.content if isinstance(output.content, str) else None


def object_of(output: TargetOutput) -> Mapping[str, Any] | None:
    """The answer as a JSON object, or `None` when it is not one."""
    return output.content if isinstance(output.content, dict) else None


def _option_types(function: Callable[..., Score], names: frozenset[str]) -> Mapping[str, Any]:
    """The resolved annotation of each option, or nothing when it cannot be resolved."""
    try:
        hints = typing.get_type_hints(function)
    except Exception:  # pragma: no cover - a scorer with an unresolvable annotation
        return {}
    return {name: hints[name] for name in names if name in hints}


def _value_problem(annotation: Any, value: Any) -> str | None:
    """Why a value does not fit an annotation, or `None` when it does."""
    origin = typing.get_origin(annotation)
    arguments = typing.get_args(annotation)

    if origin is Literal:
        if value not in arguments:
            allowed = ", ".join(repr(option) for option in arguments)
            return f"must be one of {allowed}, not {value!r}"
        return None

    if origin in {list, tuple} or annotation in {list, tuple}:
        if not isinstance(value, list | tuple):
            return f"must be a list, not {type(value).__name__}"
        return None

    if origin is dict or annotation is dict:
        if not isinstance(value, dict):
            return f"must be a mapping, not {type(value).__name__}"
        return None

    if origin is not None and origin is not Literal:
        # Sequence[str], Mapping[str, str] and friends resolve to an abstract origin.
        if issubclass(origin, Mapping) and not isinstance(value, Mapping):
            return f"must be a mapping, not {type(value).__name__}"
        if not issubclass(origin, Mapping) and isinstance(value, str):
            return "must be a list, not a string"
        return None

    if annotation is bool:
        return None if isinstance(value, bool) else f"must be true or false, not {value!r}"
    if annotation is str:
        return None if isinstance(value, str) else f"must be text, not {type(value).__name__}"
    if annotation in {int, float}:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return f"must be a number, not {type(value).__name__}"
        return None
    return None


def _non_finite_paths(value: Any, path: str = "details", depth: int = 0) -> list[str]:
    """Where a mapping holds a float that cannot be written to a file."""
    if depth > 32:
        raise ScorerError("details are nested too deeply to write to a file")
    if isinstance(value, bool):
        return []
    if isinstance(value, float) and not math.isfinite(value):
        return [path]
    if isinstance(value, Mapping):
        return [
            problem
            for key, item in value.items()
            for problem in _non_finite_paths(item, f"{path}.{key}", depth + 1)
        ]
    if isinstance(value, list | tuple):
        return [
            problem
            for index, item in enumerate(value)
            for problem in _non_finite_paths(item, f"{path}[{index}]", depth + 1)
        ]
    return []
