"""The flagship suite's own invariants.

The suite's claim is that the two sides of a pair differ by the tested variable and by nothing
else. Everything here exists to make that claim falsifiable without re-running the generator's own
logic: the sides are compared after masking, field by field, and the test asserts both that
masking the variable makes them identical and that masking anything else does not.
"""

import importlib.util
import itertools
import json
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lakmus.engine.plan import plan
from lakmus.scorers import BUILTIN_SCORERS
from lakmus.suite.loader import load

SUITE = Path(__file__).resolve().parents[1] / "suites" / "rh" / "equite-tri-cv-fr"
CONTROLE = SUITE.parent / "equite-tri-cv-fr-controle"
CASES = SUITE / "cases" / "pairs.jsonl"
MINIMUM_FOR_AN_INTERVAL = 20

# The sdist leaves `suites/` out, so these tests have nothing to read when run from one.
pytestmark = pytest.mark.skipif(
    not SUITE.is_dir(), reason="the suites are not part of the source distribution"
)

Masker = Callable[[str, Mapping[str, Any]], str]

# Which maskers each variable is allowed to move. Masking all of them must make the two sides
# identical; masking any of the others must leave them identical already.
OWNED: Mapping[str, frozenset[str]] = {
    "genre": frozenset({"identite", "accord"}),
    "origine": frozenset({"identite"}),
    "age": frozenset({"age", "dates"}),
    "adresse": frozenset({"commune"}),
    "interruption": frozenset({"dates"}),
}


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("suite_generate", SUITE / "generate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def cases() -> list[dict[str, Any]]:
    return _cases()


def _mask_identite(text: str, _: Mapping[str, Any]) -> str:
    """Masks the heading, not every occurrence of the name's tokens.

    Replacing the tokens wherever they appear let anything of the same token count ride along: an
    identity rendered as `BOUKHARI (algerienne)` against `DUPONT (francaise)` masked to the same
    text and every invariant stayed green, while the suite shipped the one thing the protocol it
    replicates forbids.
    """
    return re.sub(r"(?m)\A# .+$", "# «NOM»", text)


def _mask_accord(text: str, _: Mapping[str, Any]) -> str:
    lexique = json.loads((SUITE / "lexique.json").read_text(encoding="utf-8"))
    for lemme, (masculin, feminin) in lexique.items():
        for forme in (feminin, masculin):
            text = re.sub(re.escape(forme), lemme, text, flags=re.IGNORECASE)
    return text


def _mask_commune(text: str, _: Mapping[str, Any]) -> str:
    return re.sub(r"(?m)^[^\n]+ — (\d+) ans$", r"«COMMUNE» — \1 ans", text)


def _mask_age(text: str, _: Mapping[str, Any]) -> str:
    """Only the age on the header line.

    A wildcard over `\\d+ ans` let the age variable mask any duration written in digits, so a CV
    saying "15 ans d'expérience" would have had its experience free to move under it.
    """
    return re.sub(r"(?m)^(.+ — )\d+( ans)$", r"\1«AGE»\2", text)


def _mask_dates(text: str, _: Mapping[str, Any]) -> str:
    return re.sub(r"\d{2}/\d{4}", "«DATE»", text)


MASKERS: Mapping[str, Masker] = {
    "identite": _mask_identite,
    "accord": _mask_accord,
    "commune": _mask_commune,
    "age": _mask_age,
    "dates": _mask_dates,
}
# Applied in this order, not in the mapping's: the commune is recognised by the age that follows
# it on the same line, so masking the age first would hide the commune from its own masker.
ORDER = ("identite", "accord", "commune", "age", "dates")


def _masked(side: Mapping[str, Any], applied: frozenset[str]) -> str:
    text = str(side["cv"])
    for name in ORDER:
        if name in applied:
            text = MASKERS[name](text, side)
    return text


def _pairs(
    cases: list[dict[str, Any]],
) -> Iterator[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    for case in cases:
        yield str(case["id"]), str(case["variable"]), case["a"], case["b"]


def test_masking_every_field_makes_the_two_sides_identical(cases: list[dict[str, Any]]) -> None:
    every = frozenset(MASKERS)
    for case_id, _, a, b in _pairs(cases):
        assert _masked(a, every) == _masked(b, every), case_id


def test_the_tested_variable_is_the_only_thing_that_moves(cases: list[dict[str, Any]]) -> None:
    """Masking anything the variable does not own must leave the sides already identical.

    This is the half that catches a generator that changed two things at once: a pair where the
    address moved along with the name passes the test above and fails this one.
    """
    for case_id, variable, a, b in _pairs(cases):
        for name in MASKERS:
            if name in OWNED[variable]:
                continue
            applied = frozenset(MASKERS) - {name}
            assert _masked(a, applied) == _masked(b, applied), f"{case_id}: {name} moved"


def test_a_pair_actually_differs(cases: list[dict[str, Any]]) -> None:
    """Without this, a generator emitting the same CV twice would satisfy everything above."""
    for case_id, variable, a, b in _pairs(cases):
        applied = frozenset(MASKERS) - OWNED[variable]
        assert _masked(a, applied) != _masked(b, applied), case_id


def test_each_couple_falls_on_one_side_as_often_as_on_the_other(
    cases: list[dict[str, Any]],
) -> None:
    """Balance measured per couple, on each case's own two values.

    Counting labels only works where every case of a variable shares them. The age couples are
    relative to each CV's entry age, so their labels differ from one CV to the next; ordering each
    case's own pair makes the check the same one for every variable.
    """
    groupes: dict[tuple[str, int, str], list[bool]] = {}
    for case in cases:
        gauche, droite = str(case["a"]["valeur"]), str(case["b"]["valeur"])
        key = (str(case["variable"]), int(case["couple"]), str(case["seniorite"]))
        groupes.setdefault(key, []).append(gauche < droite)
    for key, orientations in sorted(groupes.items()):
        premiers = sum(orientations)
        assert abs(2 * premiers - len(orientations)) <= 1, (key, premiers, len(orientations))


def test_every_group_a_reader_will_quote_can_carry_an_interval(
    cases: list[dict[str, Any]],
) -> None:
    """A headline number with no interval under it is the thing this suite must not publish."""
    for field in ("variable", "poste", "seniorite"):
        counts: dict[str, int] = {}
        for case in cases:
            counts[case[field]] = counts.get(case[field], 0) + 1
        too_small = {name: n for name, n in counts.items() if n < MINIMUM_FOR_AN_INTERVAL}
        assert not too_small, f"{field}: {too_small}"


@pytest.mark.parametrize(
    ("controle", "committed"),
    [(False, CASES), (True, CONTROLE / "cases" / "pairs.jsonl")],
)
def test_the_committed_cases_are_what_the_generator_produces(
    controle: bool, committed: Path
) -> None:
    built = _module().build(controle=controle)
    rebuilt = "".join(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in built)
    assert rebuilt == committed.read_text(encoding="utf-8")


def test_every_agreement_marker_has_an_entry_and_every_entry_is_used() -> None:
    lexique = json.loads((SUITE / "lexique.json").read_text(encoding="utf-8"))
    written = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((SUITE / "cv").glob("*.yaml"))
    )
    used = set(re.findall(r"\{g:([^}]*)\}", written))
    assert used - set(lexique) == set(), "a marker with no entry renders as itself"
    assert set(lexique) - used == set(), "an unused entry is a form nothing proves correct"
    for lemme, formes in lexique.items():
        assert formes[0] != formes[1], f"{lemme} has one form; a marker on it proves nothing"


def test_no_identity_lands_on_the_denylist(cases: list[dict[str, Any]]) -> None:
    refusees = set(
        json.loads((SUITE / "sources" / "denylist.json").read_text(encoding="utf-8"))[
            "combinaisons"
        ]
    )
    drawn = {case[side]["identite"] for case in cases for side in ("a", "b")}
    assert drawn & refusees == set()


@pytest.mark.parametrize("root", [SUITE, CONTROLE])
def test_the_suite_loads_and_validates(root: Path) -> None:
    """`make check` validates both suites, which keeps a broken manifest out of a commit.

    `BUILTIN_SCORERS` is passed because the loader checks nothing about a `builtin:` reference
    without it — not the name, not its options. Loading without the catalogue would accept a
    misspelled scorer and an option no scorer takes, which is most of what can go wrong here.
    """
    suite = load(root, run_suite_code=True, scorers=BUILTIN_SCORERS)
    assert suite.manifest.id == f"rh/{root.name}"
    assert len(suite.manifest.metrics) == 3

    run_plan = plan(suite)
    assert run_plan.arity == "pair", "every metric of a pair suite has to be a pair scorer"
    assert len(run_plan.cases) == len(_cases()) if root == SUITE else len(run_plan.cases) == 24


def test_a_feminine_side_is_written_in_the_feminine(cases: list[dict[str, Any]]) -> None:
    """The agreement, not only the name.

    Nothing above catches a renderer that always picks the masculine form: the two sides would
    still differ by the first name, and the pair would still look well formed. It would also be
    the one bug that makes the suite's own claim false, since a woman's CV written throughout in
    the masculine is not the counterfactual of a man's.
    """
    lexique = json.loads((SUITE / "lexique.json").read_text(encoding="utf-8"))
    for case in cases:
        if case["variable"] != "genre":
            continue
        stem = str(case["id"]).rsplit("-genre-", 1)[0]
        markers = set(
            re.findall(
                r"\{g:([^}]*)\}", (SUITE / "cv" / f"{stem}.yaml").read_text(encoding="utf-8")
            )
        )
        assert markers, f"{stem} carries no agreement marker, so its genre pair tests only a name"
        for side in ("a", "b"):
            feminine = case[side]["valeur"] == "féminin"
            for lemme in sorted(markers):
                masculin, feminin = lexique[lemme]
                attendu = feminin if feminine else masculin
                text = str(case[side]["cv"]).casefold()
                assert attendu.casefold() in text, f"{case['id']} {side}: {attendu} missing"
                if not feminine:
                    assert feminin.casefold() not in text, f"{case['id']} {side}: {feminin} present"


def _readme_pairs() -> set[tuple[str, str]]:
    """Every couple the README prints, as it prints it.

    The README is the public face of the method: a reader who objects to a name has to be able to
    object to the name that was actually used.
    """
    pairs: set[tuple[str, str]] = set()
    for line in (SUITE / "README.md").read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        for cell in line.strip("|").split("|"):
            for item in cell.split(","):
                for separator in (" ⟷ ", " / "):
                    if separator not in item:
                        continue
                    left, right = (part.strip() for part in item.split(separator, 1))
                    # Skips the header row, whose own cell happens to carry the separator.
                    if left[:1].isupper() and right[:1].isupper():
                        pairs.add((left, right))
    return pairs


def test_the_readme_prints_the_couples_the_generator_drew(cases: list[dict[str, Any]]) -> None:
    """Equality, not inclusion: the README may not omit a couple either.

    It is the public face of the method. A reader who objects to a name has to be able to object to
    the name that was used, and to see every one of them.
    """
    populations = {
        commune["nom"]: commune["population"]
        for intercommunalite in json.loads(
            (SUITE / "sources" / "communes-epci.json").read_text(encoding="utf-8")
        ).values()
        for commune in intercommunalite
    }
    drawn: set[tuple[str, str]] = set()
    for case in cases:
        a, b = case["a"], case["b"]
        if case["variable"] == "genre":
            masculin = a if a["valeur"] == "masculin" else b
            feminin = b if masculin is a else a
            drawn.add((str(masculin["identite"]).split()[0], str(feminin["identite"]).split()[0]))
        if case["variable"] == "origine":
            francaise = a if a["valeur"] == "française" else b
            maghrebine = b if francaise is a else a
            drawn.add((str(francaise["identite"]), str(maghrebine["identite"])))
        if case["variable"] == "adresse":
            sans = a if a["valeur"] == "hors quartier prioritaire" else b
            avec = b if sans is a else a
            drawn.add(
                (
                    f"{_commune(sans)} ({populations[_commune(sans)]:,})".replace(",", " "),
                    f"{_commune(avec)} ({populations[_commune(avec)]:,})".replace(",", " "),
                )
            )

    assert _readme_pairs() == drawn


def _commune(side: Mapping[str, Any]) -> str:
    return str(side["cv"]).splitlines()[2].split(" — ")[0]


def _periodes(cv: str) -> list[tuple[int, int]]:
    """Each job's months, as the rendered CV prints them, oldest first."""
    reference = 2026 * 12
    bornes: list[tuple[int, int]] = []
    # Only the jobs: the formation block is a dated heading too, and it sits one month before the
    # first job, which would read as a hole in every CV.
    emploi, _, _ = cv.partition("## Formation")
    for ligne in emploi.splitlines():
        if not ligne.startswith("### ") or "—" not in ligne:
            continue
        dates = re.findall(r"(\d{2})/(\d{4})", ligne.split("—")[0])
        if not dates:
            continue
        debut = int(dates[0][1]) * 12 + int(dates[0][0]) - 1
        fin = int(dates[1][1]) * 12 + int(dates[1][0]) - 1 if len(dates) > 1 else reference
        bornes.append((debut, fin))
    return sorted(bornes)


def test_the_declared_value_matches_the_rendered_cv(cases: list[dict[str, Any]]) -> None:
    """The label and the bytes it describes, checked against each other.

    `valeur` is what the cases file, a report and a reader all quote. Nothing tied it to the text:
    a generator opening a seventy-two-month hole while labelling it eighteen months passed every
    other test here.
    """
    for case in cases:
        for side in ("a", "b"):
            if case["variable"] == "age":
                entete = str(case[side]["cv"]).splitlines()[2]
                assert entete.endswith(str(case[side]["valeur"])), case["id"]
            periodes = _periodes(str(case[side]["cv"]))
            trous = [
                suivante[0] - precedente[1] for precedente, suivante in itertools.pairwise(periodes)
            ]
            # Only `interruption` may open a hole, and only the one it declares. Without the
            # first half, another variable could carry a gap of its own and be read as its own
            # effect: an age pair that also skipped two years passed every other test here.
            attendu = 0
            if case["variable"] == "interruption" and (
                found := re.search(r"(\d+) mois", str(case[side]["valeur"]))
            ):
                attendu = int(found.group(1))
            assert max(trous, default=0) == attendu, f"{case['id']} {side}: {trous}"


def test_each_lexicon_entry_is_one_word_in_two_genders() -> None:
    """Masking maps every form of a lemma to the lemma, so the lexicon is where a lie would hide.

    `"expérimenté": ["expérimenté", "débutante"]` is invisible to every masked comparison, and
    `test_a_feminine_side_is_written_in_the_feminine` would assert the sabotage is present.
    """
    lexique = json.loads((SUITE / "lexique.json").read_text(encoding="utf-8"))
    for lemme, formes in lexique.items():
        masculin, feminin = formes
        assert masculin != feminin, f"{lemme}: one form proves nothing"
        assert len(masculin.split()) == len(feminin.split()), f"{lemme}: differing word counts"
        commun = sum(
            1
            for _ in itertools.takewhile(
                lambda p: p[0] == p[1], zip(masculin, feminin, strict=False)
            )
        )
        plus_court = min(len(masculin), len(feminin))
        assert commun >= plus_court - 4 and commun >= plus_court * 0.6, (
            f"{lemme}: {masculin!r} and {feminin!r} do not share a stem"
        )


def test_no_case_carries_a_mention_of_nationality() -> None:
    """The protocol this suite replicates forbids it, and the CV heading is where it would land."""
    for path in (CASES, CONTROLE / "cases" / "pairs.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            for side in ("a", "b"):
                entete = str(case[side]["cv"]).splitlines()[0]
                assert re.fullmatch(r"# [^\W\d_][\w'-]*(?: [A-ZÀ-Ü'-]+)+", entete), entete
                assert entete == f"# {case[side]['identite']}", case["id"]
