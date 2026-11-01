"""Turning a case into the prompt a target sees.

Three settings of the Jinja environment each prevent a wrong measurement rather than a crash:

`StrictUndefined`, because a template referring to a field a case does not have must fail rather
than render an empty string. An empty prompt still gets an answer, and an answer to nothing would
be scored, aggregated and published as though it meant something.

No loader, so `{% include %}` and `{% import %}` cannot reach a file the suite digest does not
cover.

No autoescaping: a prompt is not HTML, and escaping it would change what the target reads.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from lakmus.errors import Code, Finding, SuiteInvalid
from lakmus.scorers import Case

Variant = Literal["a", "b"]
VARIANTS: tuple[Variant, Variant] = ("a", "b")

INPUT_ALGORITHM = "input/1"


@dataclass(frozen=True, slots=True)
class RenderedInput:
    """One prompt, and the digest that identifies it in the cache."""

    case_id: str
    variant: Variant | None
    prompt: str
    expects_json: bool
    sha256: str


class CaseRenderFailed(Exception):
    """This case could not be rendered. Other cases may still be fine."""


def _environment() -> SandboxedEnvironment:
    return SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, loader=None)


def input_digest(prompt: str, *, expects_json: bool) -> str:
    """What the cache keys on: the exact text, and whether a JSON answer was demanded.

    `expects_json` is in the digest because it changes the request the adapter builds, so an
    answer obtained without it is not the same answer.
    """
    canonical = json.dumps(
        {"algorithm": INPUT_ALGORITHM, "prompt": prompt, "expects_json": expects_json},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Renderer:
    """A suite's prompt template, compiled once.

    A template that will not compile is a fault in the suite, the same for every case, so it ends
    the run before anything is paid for. A template that compiles but fails on one case is that
    case's problem: the field it wanted is missing from that case and present in others.
    """

    def __init__(self, source: str, *, expects_json: bool = False) -> None:
        try:
            self._template = _environment().from_string(source)
        except Exception as exc:
            # `lakmus validate` reports this as LKM-204 before a run starts; this is the backstop
            # for a caller using the library, and it carries a finding rather than an empty one.
            raise SuiteInvalid(
                [
                    Finding(
                        code=Code.TEMPLATE_INVALID,
                        location="target.input",
                        message=f"the template will not compile: {type(exc).__name__}: {exc}",
                    )
                ]
            ) from exc
        self.expects_json = expects_json

    def single(self, case: Case) -> tuple[RenderedInput, ...]:
        return (self._render(case, variant=None, fields=dict(case.data)),)

    def pair(self, case: Case) -> tuple[RenderedInput, ...]:
        """Two prompts, one per side, each seeing the case's own fields underneath its own.

        The sides are separate calls: the method requires that the target never see both, because
        seeing both is a different task.
        """
        rendered: list[RenderedInput] = []
        for variant in VARIANTS:
            side = case.data.get(variant)
            if not isinstance(side, Mapping):
                raise CaseRenderFailed(
                    f"case {case.id!r} has no object under {variant!r}; a pair suite needs both"
                )
            fields = {key: value for key, value in case.data.items() if key not in VARIANTS}
            fields.update(side)
            rendered.append(self._render(case, variant=variant, fields=fields))
        return tuple(rendered)

    def _render(
        self, case: Case, *, variant: Variant | None, fields: Mapping[str, Any]
    ) -> RenderedInput:
        context = dict(fields)
        if variant is not None:
            context["variant"] = variant
        try:
            prompt = self._template.render(context)
        except Exception as exc:
            # Any failure, not only Jinja's own: `{{ a + b }}` over a string and a number raises a
            # TypeError, and `{% include %}` without a loader raises one too. Both are this case's
            # data meeting this template, which is the case's fault and not the suite's.
            raise CaseRenderFailed(f"case {case.id!r}: {type(exc).__name__}: {exc}") from exc
        return RenderedInput(
            case_id=case.id,
            variant=variant,
            prompt=prompt,
            expects_json=self.expects_json,
            sha256=input_digest(prompt, expects_json=self.expects_json),
        )
