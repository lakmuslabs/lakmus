"""The seven built-ins, and the rule that keeps a broken answer from passing a threshold.

French text throughout: accents are the ordinary case for this project, not an edge case, and a
scorer that normalises them away measures something other than what a suite wrote.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from lakmus.errors import ScorerError
from lakmus.scorers import (
    BUILTIN_SCORERS,
    Case,
    PairOutputs,
    Score,
    exact,
    json_field_match,
    json_schema,
    pair_flip,
    pair_gap,
    precision_recall_f1,
    regex,
    scorer,
    spec_of,
)
from lakmus.targets.base import TargetOutput


def _case(**fields: Any) -> Case:
    return Case(id="p-0001", data=fields)


def _text(answer: str) -> TargetOutput:
    return TargetOutput(content=answer, latency_ms=10)


def _json(answer: Mapping[str, Any]) -> TargetOutput:
    return TargetOutput(content=dict(answer), latency_ms=10)


def _pair(first: Mapping[str, Any], second: Mapping[str, Any]) -> PairOutputs:
    return PairOutputs(a=_json(first), b=_json(second))


# --- the rule that matters most ---------------------------------------------------------------


def test_an_answer_that_cannot_be_scored_has_no_score() -> None:
    """Scoring it zero would let a target that answered nothing pass `score_gap: {max: 0.05}`.

    Zero is the *best* value on a lower-is-better metric, which is every fairness metric worth
    writing. The case is counted as an error instead, and left out of the mean.
    """
    score = pair_gap(_case(), _pair({"nothing": 1}, {"nothing": 2}))

    assert score.value is None
    assert score.scored is False
    assert "error" in score.details


@pytest.mark.parametrize(
    "unscoreable",
    [
        pytest.param(lambda: exact(_case(expected="oui"), _json({"a": 1})), id="exact-not-text"),
        pytest.param(lambda: exact(_case(), _text("oui")), id="exact-no-case-field"),
        pytest.param(lambda: regex(_case(), _json({"a": 1}), pattern="x"), id="regex-not-text"),
        pytest.param(
            lambda: json_schema(_case(), _text("oui"), schema={"score": "number"}),
            id="json-schema-not-an-object",
        ),
        pytest.param(
            lambda: json_field_match(_case(expected="oui"), _text("oui"), field="decision"),
            id="field-match-not-an-object",
        ),
        pytest.param(
            lambda: precision_recall_f1(_case(expected="pas une liste"), _json({"predicted": []})),
            id="prf-expected-not-a-list",
        ),
        pytest.param(
            lambda: pair_flip(_case(), _pair({"x": 1}, {"decision": "retenu"})),
            id="pair-flip-missing-field",
        ),
    ],
)
def test_every_scorer_declines_rather_than_guesses(unscoreable: Any) -> None:
    assert unscoreable().value is None


# --- exact ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected", "normalise", "value"),
    [
        pytest.param("Équité", "Équité", ("trim", "nfc"), 1.0, id="identical-with-accents"),
        pytest.param("  Équité  ", "Équité", ("trim", "nfc"), 1.0, id="trimmed"),
        pytest.param("équité", "Équité", ("trim", "nfc"), 0.0, id="case-matters-by-default"),
        pytest.param(
            "équité", "Équité", ("trim", "nfc", "lower"), 1.0, id="case-folded-on-request"
        ),
        pytest.param("equite", "équité", ("trim", "nfc"), 0.0, id="accents-are-not-stripped"),
        pytest.param("Non", "Oui", ("trim",), 0.0, id="wrong"),
        pytest.param("", "Oui", ("trim",), 0.0, id="empty"),
        pytest.param("a  b", "a b", ("collapse_space",), 1.0, id="space-collapsed"),
    ],
)
def test_exact_compares_normalised_text(
    answer: str, expected: str, normalise: Sequence[str], value: float
) -> None:
    score = exact(_case(expected=expected), _text(answer), normalise=normalise)

    assert score.value == value


def test_the_two_spellings_of_an_accent_are_the_same_answer() -> None:
    """NFC is why: macOS stores `é` decomposed and Linux composed."""
    composed = "équité"
    decomposed = "équité"

    assert exact(_case(expected=composed), _text(decomposed)).value == 1.0


def test_an_unknown_normalisation_step_ends_the_run() -> None:
    """The same mistake for every case, so it is not recorded against one."""
    with pytest.raises(ScorerError, match="unknown normalisation step"):
        exact(_case(expected="oui"), _text("oui"), normalise=("strip_accents",))


# --- regex ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "pattern", "flags", "expect", "value"),
    [
        pytest.param("SIRET 73282932000074", r"\d{14}", "", "match", 1.0, id="found"),
        pytest.param("aucun numéro", r"\d{14}", "", "match", 0.0, id="absent"),
        pytest.param("aucun numéro", r"\d{14}", "", "no_match", 1.0, id="absent-as-required"),
        pytest.param("ÉQUITÉ", "équité", "i", "match", 1.0, id="case-insensitive-accents"),
        pytest.param("ÉQUITÉ", "équité", "", "match", 0.0, id="case-sensitive-by-default"),
        pytest.param("", r".+", "", "match", 0.0, id="empty-answer"),
    ],
)
def test_regex_matches_or_requires_no_match(
    answer: str, pattern: str, flags: str, expect: str, value: float
) -> None:
    score = regex(_case(), _text(answer), pattern=pattern, flags=flags, expect=expect)

    assert score.value == value


@pytest.mark.parametrize(
    ("pattern", "flags", "message"),
    [
        pytest.param("(unclosed", "", "not a valid pattern", id="bad-pattern"),
        pytest.param(".", "z", "unknown flag", id="bad-flag"),
    ],
)
def test_a_pattern_the_suite_got_wrong_ends_the_run(pattern: str, flags: str, message: str) -> None:
    with pytest.raises(ScorerError, match=message):
        regex(_case(), _text("x"), pattern=pattern, flags=flags)


# --- json_schema ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "value"),
    [
        pytest.param({"score": 0.8, "decision": "retenu"}, 1.0, id="complete"),
        pytest.param({"score": 0.8}, 0.5, id="half-the-fields"),
        pytest.param({}, 0.0, id="empty-object"),
        pytest.param({"score": "0.8", "decision": "retenu"}, 0.5, id="wrong-type"),
        pytest.param({"score": True, "decision": "retenu"}, 0.5, id="a-boolean-is-not-a-number"),
        pytest.param({"score": 1, "decision": "retenu"}, 1.0, id="an-integer-is-a-number"),
    ],
)
def test_json_schema_reports_the_share_of_declared_fields(
    answer: Mapping[str, Any], value: float
) -> None:
    score = json_schema(_case(), _json(answer), schema={"score": "number", "decision": "string"})

    assert score.value == value


def test_json_schema_says_which_fields_are_missing() -> None:
    score = json_schema(_case(), _json({"score": 0.8}), schema={"score": "number", "d": "string"})

    assert score.details["missing"] == ["d"]


# --- json_field_match -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected", "value"),
    [
        pytest.param({"decision": "retenu"}, "retenu", 1.0, id="match"),
        pytest.param({"decision": "écarté"}, "écarté", 1.0, id="match-with-accents"),
        pytest.param({"decision": "retenu"}, "écarté", 0.0, id="mismatch"),
        pytest.param({"decision": 0.8}, 0.8, 1.0, id="numbers-compare-as-numbers"),
        pytest.param({"decision": 0.80}, 0.8, 1.0, id="the-same-number-written-two-ways"),
    ],
)
def test_json_field_match_compares_one_field(
    answer: Mapping[str, Any], expected: Any, value: float
) -> None:
    score = json_field_match(_case(expected=expected), _json(answer), field="decision")

    assert score.value == value


# --- precision_recall_f1 ----------------------------------------------------------------------


def test_precision_recall_and_f1_over_two_lists() -> None:
    score = precision_recall_f1(
        _case(expected=["société", "bénéfice", "créance"]),
        _json({"predicted": ["société", "bénéfice", "dette"]}),
    )

    assert score.details["precision"] == pytest.approx(2 / 3)
    assert score.details["recall"] == pytest.approx(2 / 3)
    assert score.value == pytest.approx(2 / 3)
    assert score.details["missed"] == ["créance"]
    assert score.details["spurious"] == ["dette"]


@pytest.mark.parametrize("report", ["f1", "precision", "recall"])
def test_which_of_the_three_is_reported_is_the_suites_choice(report: str) -> None:
    score = precision_recall_f1(
        _case(expected=["a", "b"]),
        _json({"predicted": ["a"]}),
        report=report,
    )

    assert score.value == pytest.approx(score.details[report])


@pytest.mark.parametrize(
    ("expected", "predicted", "value"),
    [
        pytest.param(["a"], [], 0.0, id="nothing-predicted"),
        pytest.param([], ["a"], 0.0, id="nothing-expected"),
        pytest.param([], [], 1.0, id="both-empty-is-a-correct-answer-not-the-worst-one"),
        pytest.param(["a", "b"], ["a", "b"], 1.0, id="perfect"),
    ],
)
def test_precision_recall_f1_at_the_edges(
    expected: list[str], predicted: list[str], value: float
) -> None:
    score = precision_recall_f1(_case(expected=expected), _json({"predicted": predicted}))

    assert score.value == pytest.approx(value)


# --- the pair scorers -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second", "value"),
    [
        pytest.param(0.8, 0.8, 0.0, id="identical-pair-is-neutral"),
        pytest.param(0.8, 0.5, pytest.approx(0.3), id="a-higher"),
        pytest.param(0.5, 0.8, pytest.approx(0.3), id="b-higher-same-gap"),
        pytest.param(1, 0, 1.0, id="integers"),
    ],
)
def test_pair_gap_is_the_absolute_difference(first: float, second: float, value: Any) -> None:
    score = pair_gap(_case(), _pair({"score": first}, {"score": second}))

    assert score.value == value


def test_pair_gap_records_which_side_was_favoured() -> None:
    """A gap with no direction cannot tell you who the system preferred."""
    score = pair_gap(_case(), _pair({"score": 0.9}, {"score": 0.4}))

    assert score.details["direction"] == "a_higher"
    assert (
        pair_gap(_case(), _pair({"score": 0.4}, {"score": 0.9})).details["direction"] == "b_higher"
    )
    assert pair_gap(_case(), _pair({"score": 0.4}, {"score": 0.4})).details["direction"] == "equal"


def test_a_boolean_is_not_a_score() -> None:
    assert pair_gap(_case(), _pair({"score": True}, {"score": False})).value is None


@pytest.mark.parametrize(
    ("first", "second", "value"),
    [
        pytest.param("retenu", "retenu", 0.0, id="identical-pair-is-neutral"),
        pytest.param("retenu", "écarté", 1.0, id="flipped"),
        pytest.param("écarté", "retenu", 1.0, id="flipped-the-other-way"),
    ],
)
def test_pair_flip_reports_whether_the_decision_moved(
    first: str, second: str, value: float
) -> None:
    score = pair_flip(_case(), _pair({"decision": first}, {"decision": second}))

    assert score.value == value


# --- the decorator and the registry -----------------------------------------------------------


def test_the_keyword_only_parameters_are_the_option_schema() -> None:
    """One source for the options, and it is the signature a reader already sees."""
    spec = BUILTIN_SCORERS["regex"]

    assert spec.required_options == {"pattern"}
    assert spec.options == {"pattern", "flags", "expect"}


@pytest.mark.parametrize(
    ("name", "arity", "higher_is_better"),
    [
        pytest.param("exact", "single", True, id="exact"),
        pytest.param("pair_gap", "pair", False, id="pair-gap"),
        pytest.param("pair_flip", "pair", False, id="pair-flip"),
    ],
)
def test_a_scorer_declares_its_arity_and_direction(
    name: str, arity: str, higher_is_better: bool
) -> None:
    spec = BUILTIN_SCORERS[name]

    assert (spec.arity, spec.higher_is_better) == (arity, higher_is_better)


def test_every_built_in_is_deterministic() -> None:
    """A judge-scored scorer says otherwise, and the report flags it. None of these is one."""
    assert all(spec.deterministic for spec in BUILTIN_SCORERS.values())


def test_the_registry_names_exactly_the_seven() -> None:
    assert sorted(BUILTIN_SCORERS) == [
        "exact",
        "json_field_match",
        "json_schema",
        "pair_flip",
        "pair_gap",
        "precision_recall_f1",
        "regex",
    ]


def test_a_plain_callable_is_not_a_scorer() -> None:
    def looks_like_one(case: Case, output: TargetOutput) -> Score:
        return Score.of(1.0)

    assert spec_of(looks_like_one) is None


def test_the_spec_refuses_an_option_the_scorer_does_not_accept() -> None:
    spec = BUILTIN_SCORERS["exact"]

    with pytest.raises(ScorerError, match="unknown option"):
        spec.call(_case(expected="oui"), _text("oui"), {"expceted": "oui"})


def test_the_spec_refuses_a_missing_required_option() -> None:
    with pytest.raises(ScorerError, match="missing option"):
        BUILTIN_SCORERS["regex"].call(_case(), _text("oui"), {})


def test_a_scorer_runs_through_its_spec_with_its_options() -> None:
    score = BUILTIN_SCORERS["regex"].call(
        _case(), _text("SIRET 73282932000074"), {"pattern": r"\d{14}"}
    )

    assert score.value == 1.0


def test_a_decorated_scorer_keeps_working_as_a_function() -> None:
    @scorer(name="always_one", higher_is_better=True)
    def always_one(case: Case, output: TargetOutput) -> Score:
        return Score.of(1.0)

    assert always_one(_case(), _text("x")).value == 1.0
    spec = spec_of(always_one)
    assert spec is not None
    assert spec.name == "always_one"


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_a_score_that_is_not_a_finite_number_is_refused(value: float) -> None:
    with pytest.raises(ValueError, match=r"finite|inf|nan"):
        Score.of(value)


def test_a_case_reads_its_own_fields_by_name() -> None:
    case = _case(variable="nom", meta={"poste": "comptable"})

    assert case["variable"] == "nom"
    assert case.get("absent", "défaut") == "défaut"
    with pytest.raises(KeyError):
        _ = case["absent"]


# --- the gaps the review found -----------------------------------------------------------------


def test_a_declined_case_always_says_why() -> None:
    """`unscoreable` promises a reason, and a reason nobody can read is no reason."""
    score = pair_gap(_case(), _pair({"x": 1}, {"x": 2}))

    assert score.details["error"]
    assert isinstance(score.details["error"], str)


@pytest.mark.parametrize(
    "declined",
    [
        pytest.param(
            lambda: json_field_match(_case(expected="oui"), _json({"other": 1}), field="decision"),
            id="field-absent-from-the-answer",
        ),
        pytest.param(lambda: exact(_case(expected=0.8), _text("0.8")), id="case-field-is-not-text"),
        pytest.param(
            lambda: precision_recall_f1(_case(expected=["a"]), _json({"predicted": "a"})),
            id="predicted-is-not-a-list",
        ),
        pytest.param(
            lambda: pair_gap(_case(), _pair({"score": float("inf")}, {"score": 0.8})),
            id="a-non-finite-score",
        ),
        pytest.param(
            lambda: pair_gap(_case(), _pair({"score": 1e308}, {"score": -1e308})),
            id="a-gap-that-overflows",
        ),
        pytest.param(
            lambda: pair_gap(_case(), _pair({"score": None}, {"score": 0.8})),
            id="a-null-score",
        ),
    ],
)
def test_every_declined_case_declines_with_a_reason(declined: Any) -> None:
    score = declined()

    assert score.value is None
    assert score.details["error"]


@pytest.mark.parametrize(
    "fault",
    [
        pytest.param(lambda: json_schema(_case(), _json({}), schema={}), id="empty-schema"),
        pytest.param(
            lambda: json_schema(_case(), _json({"s": 1}), schema={"s": "numbr"}),
            id="mis-declared-type",
        ),
    ],
)
def test_an_option_fault_ends_the_run_rather_than_every_case(fault: Any) -> None:
    """Recording it five thousand times would bury the one fact that matters."""
    with pytest.raises(ScorerError):
        fault()


def test_duplicates_in_either_list_are_one_item() -> None:
    """Precision and recall over a set of extracted items; the specification says so."""
    score = precision_recall_f1(
        _case(expected=["société", "société", "bénéfice"]),
        _json({"predicted": ["société", "société", "société"]}),
    )

    assert score.details["precision"] == pytest.approx(1.0)
    assert score.details["recall"] == pytest.approx(0.5)


def test_a_correct_empty_answer_is_not_the_worst_answer() -> None:
    """Nothing to find and nothing wrongly found: the target got the case exactly right."""
    score = precision_recall_f1(_case(expected=[]), _json({"predicted": []}))

    assert score.value == 1.0


def test_details_that_cannot_be_written_to_a_file_are_refused() -> None:
    """`details` reaches the cases file, and json.dumps spells an infinity as a bare token."""
    with pytest.raises(ValueError, match="not finite"):
        Score.of(1.0, observed=float("inf"))
    with pytest.raises(ValueError, match="not finite"):
        Score.of(1.0, nested={"values": [1.0, float("nan")]})


@pytest.mark.parametrize("name", sorted(BUILTIN_SCORERS))
def test_every_built_in_declares_an_arity_and_a_direction(name: str) -> None:
    spec = BUILTIN_SCORERS[name]

    assert spec.arity in {"single", "pair"}
    assert isinstance(spec.higher_is_better, bool)
    assert spec.name == name


def test_an_option_value_the_scorer_cannot_use_is_refused_at_call_time() -> None:
    """A scorer reached through the library API has no `validate` in front of it."""
    with pytest.raises(ScorerError, match="must be one of"):
        BUILTIN_SCORERS["regex"].call(_case(), _text("x"), {"pattern": "x", "expect": "matched"})


def test_the_published_option_table_matches_the_registry() -> None:
    """The specification is what another implementation reads, so it cannot drift."""
    spec_text = Path("docs/spec/suite-format.md").read_text(encoding="utf-8")

    for name, spec in sorted(BUILTIN_SCORERS.items()):
        row = next(
            (line for line in spec_text.split("\n") if line.startswith(f"| `{name}` |")), None
        )
        assert row is not None, f"{name} is not in the published option table"
        for option in spec.options:
            assert f"`{option}`" in row, f"{name}: the table does not list {option}"
        for required in spec.required_options:
            cells = [cell.strip() for cell in row.split("|")]
            assert f"`{required}`" in cells[3], f"{name}: {required} is not listed as required"
