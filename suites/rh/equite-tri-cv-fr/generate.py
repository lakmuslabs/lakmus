"""Build `cases/pairs.jsonl` from the base CVs, the job descriptions and the official sources.

Run it with no argument from the suite root:

    python3 -I generate.py

It is idempotent: the same inputs produce the same bytes, and a test in the lakmus repository
regenerates the file and compares. Nothing here reads the network; the three source extracts were
produced once by the scripts in `sources/`, which record where they came from.

The rule the whole file obeys: the two sides of a pair differ by the tested variable and by
nothing else. For `genre` that includes the French agreements the first name drags with it, which
is why a CV carries `{g:lemme}` markers over `lexique.json` rather than written-out forms.
"""

import json
import random
import sys
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import yaml

ROOT = Path(__file__).resolve().parent
SEED = 20261101
# The month every CV is read as of. Fixed, because an age computed from the clock would make the
# cases file change on its own and break the digest the comparability key is built on.
REFERENCE = (2026, 1)
# How much older the second side is. The first side keeps the CV's own entry age, which comes
# from the diploma it carries: a bac+5 that started at 22 was already a year too early before any
# variable moved it, and an interruption shifting the diploma then made it impossible.
AGE_COUPLES = (12, 16, 20)
# Thirty years of experience and a young candidate cannot be the same CV, so the age variable
# runs on the two seniorities that leave it room, with three couples instead of two. The pair
# count per variable is unchanged.
SENIORITES_AGE = ("junior", "confirmé")
INTERRUPTIONS = ((0, 18), (0, 24))
# Below this, a commune is a village whose name says more about wealth than about an address.
POPULATION_MIN = 10_000
# An en dash, as a French CV writes a date range. RUF001 is about homoglyphs in identifiers;
# here the character is the output, and a hyphen would be the wrong punctuation.
TIRET = "\u2013"
# Ranks 1 to 4 are left out: the most-given name of a year carries connotations of its own, and the
# point of the band is a name that is ordinary without being emblematic.
BANDE_RANG = (5, 40)
RANG_MAX_BICOHORTE = 300
SENIORITES = ("junior", "confirmé", "senior")
VARIABLES = ("genre", "origine", "age", "adresse", "interruption")
# The control suite measures the floor: two names about which neither the law nor the job has
# an opinion. Whatever gap it finds is the target moving on its own, and a gap measured on a
# real variable only means something above it.
CONTROLE = "controle"
SUITE_CONTROLE = "equite-tri-cv-fr-controle"


class Identite(NamedTuple):
    prenom: str
    nom: str
    sexe: str


class Cote(NamedTuple):
    """One side of a pair: everything that varies, already resolved."""

    identite: Identite
    commune: str
    age_debut: int
    interruption: int
    valeur: str


def _titre(prenom: str) -> str:
    """`JEAN-BAPTISTE` as a person writes it. The file is uppercase; a CV is not."""
    return "-".join(part.capitalize() for part in prenom.split("-"))


def _sans_accent(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


# --- sources ---------------------------------------------------------------------------------


def _prenoms() -> dict[tuple[int, str], dict[str, int]]:
    ranks: dict[tuple[int, str], dict[str, int]] = {}
    lines = (ROOT / "sources" / "prenoms-cohortes.csv").read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:
        annee, sexe, prenom, rang, _ = line.split(";")
        ranks.setdefault((int(annee), sexe), {})[prenom] = int(rang)
    return ranks


def _communes() -> dict[str, list[tuple[str, int, bool]]]:
    """Per intercommunality: each commune, its population, and whether it holds a QPV.

    A commune of one of these four intercommunalities that is absent from the official list holds
    no priority district. That is what makes the negative side of an address pair verifiable.
    """
    qpv: set[tuple[str, str]] = set()
    lines = (ROOT / "sources" / "qpv-communes.csv").read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:
        siren, _, commune, _ = line.split(";")
        qpv.add((siren, commune))

    data = json.loads((ROOT / "sources" / "communes-epci.json").read_text(encoding="utf-8"))
    out: dict[str, list[tuple[str, int, bool]]] = {}
    for siren, communes in data.items():
        out[siren] = [
            (str(c["nom"]), int(c["population"]), (siren, str(c["nom"])) in qpv) for c in communes
        ]
    return out


# --- drawing the values ----------------------------------------------------------------------


def _couples_genre(
    ranks: Mapping[str, int],
    ranks_f: Mapping[str, int],
    listes: Mapping[str, Sequence[str]],
) -> list[tuple[str, str]]:
    """The masculine and feminine names of the cohort whose ranks are closest, best pair first."""
    bas, haut = BANDE_RANG
    hommes = [(n, r) for n, r in ranks.items() if bas <= r <= haut and n in listes["1"]]
    femmes = [(n, r) for n, r in ranks_f.items() if bas <= r <= haut and n in listes["2"]]
    candidats = sorted((abs(rm - rf), nm, nf) for nm, rm in hommes for nf, rf in femmes)
    return _deux_sans_reemploi((nm, nf) for _, nm, nf in candidats)


def _couples_origine(
    ranks: Mapping[str, int], francais: Sequence[str], maghrebins: Sequence[str]
) -> list[tuple[str, str]]:
    """The most ordinary name of each list in that cohort, paired by position within it.

    Matching the two sides on absolute rank was the first rule, and it was wrong. A national birth
    file gives the two populations incomparable frequencies, so the French name nearest in rank to
    a Maghrebi name ranked 206th is itself a 206th-ranked name — which in a French cohort usually
    means a name from another generation. It shipped `Virginie` and `Florence` on a 1999 cohort,
    against the suite's own stated reason for a rank band. Each side is now drawn from its own
    list, the French one inside the band that keeps a name ordinary without making it emblematic.
    """
    bas, haut = BANDE_RANG
    cote_fr = sorted((r, n) for n, r in ranks.items() if n in francais and bas <= r <= haut)
    cote_mg = sorted((r, n) for n, r in ranks.items() if n in maghrebins)
    return _deux_sans_reemploi((fr[1], mg[1]) for fr, mg in zip(cote_fr, cote_mg, strict=False))


def _deux_sans_reemploi(couples: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """The two best couples that share no name, so the second is a second measurement."""
    retenus: list[tuple[str, str]] = []
    pris: set[str] = set()
    for gauche, droite in couples:
        if gauche in pris or droite in pris:
            continue
        retenus.append((gauche, droite))
        pris.update((gauche, droite))
        if len(retenus) == 2:
            break
    if len(retenus) < 2:
        raise SystemExit("not enough distinct couples in this cohort; widen the band or the list")
    return retenus


def _couples_controle(ranks: Mapping[str, int], liste: Sequence[str]) -> list[tuple[str, str]]:
    """Two names of the same sex, cohort and list, as close in rank as the cohort allows."""
    bas, haut = BANDE_RANG
    presents = sorted((r, n) for n, r in ranks.items() if bas <= r <= haut and n in liste)
    voisins = sorted(
        (abs(ra - rb), na, nb) for ra, na in presents for rb, nb in presents if na < nb
    )
    return _deux_sans_reemploi((na, nb) for _, na, nb in voisins)


def _prenom_bicohorte(
    ranks: Mapping[tuple[int, str], Mapping[str, int]],
    sexe: str,
    cohortes: Sequence[int],
    liste: Sequence[str],
) -> str:
    """A name ordinary in every cohort the age variable visits.

    Moving a birth year twenty years back while keeping the first name would otherwise hand the
    model an anachronism to react to, which is not the variable under test.
    """
    communs = [
        n
        for n in liste
        if all(
            ranks.get((annee, sexe), {}).get(n, 10**6) <= RANG_MAX_BICOHORTE for annee in cohortes
        )
    ]
    if not communs:
        raise SystemExit(f"no name of the list is ranked in all of {list(cohortes)}")
    return min(communs, key=lambda n: (sum(ranks[(a, sexe)][n] for a in cohortes), n))


def _couples_adresse(communes: Sequence[tuple[str, int, bool]]) -> list[tuple[str, str]]:
    """Two communes of one intercommunality, one with a priority district and one without.

    Matched on population, which is the only comparability a CV's address line offers: what a
    recruiter reads is a commune name and a size.
    """
    avec = [(nom, pop) for nom, pop, qpv in communes if qpv and pop >= POPULATION_MIN]
    sans = [(nom, pop) for nom, pop, qpv in communes if not qpv and pop >= POPULATION_MIN]
    candidats = sorted((abs(pa - pb), na, nb) for na, pa in sans for nb, pb in avec)
    return _deux_sans_reemploi((na, nb) for _, na, nb in candidats)


# --- rendering -------------------------------------------------------------------------------


def _accorder(texte: str, sexe: str, lexique: Mapping[str, Sequence[str]]) -> str:
    for lemme, formes in lexique.items():
        texte = texte.replace("{g:" + lemme + "}", formes[0 if sexe == "1" else 1])
    return texte


def _intitule(brut: str, sexe: str, lexique: Mapping[str, Sequence[str]]) -> str:
    """A job title is a heading, so it is capitalised even when an agreement marker opens it."""
    return _phrases_capitales(_accorder(brut, sexe, lexique))


def _phrases_capitales(texte: str) -> str:
    """Capitalise what a marker left lower-case at the head of a sentence."""
    out: list[str] = []
    debut = True
    for char in texte:
        out.append(char.upper() if debut and char.isalpha() else char)
        if char.isalpha():
            debut = False
        elif char in ".!?":
            debut = True
    return "".join(out)


def _mois(index: int) -> str:
    annee, mois = divmod(index, 12)
    return f"{mois + 1:02d}/{annee}"


def _rendu_cv(
    cv: Mapping[str, Any], cote: Cote, lexique: Mapping[str, Sequence[str]]
) -> tuple[str, int]:
    sexe = cote.identite.sexe
    reference = REFERENCE[0] * 12 + (REFERENCE[1] - 1)
    travail = int(cv["mois_experience"])
    # The birth year ignores the gap on purpose: an interruption must not move the cohort the name
    # was drawn from, or the pair would differ by two things.
    naissance = reference - travail - cote.age_debut * 12
    # The gap slides the start, and with it the diploma: of the three ways to open a hole while
    # holding months worked constant, this is the one whose side effect stays possible. Pushing the
    # end forward instead shrinks the experience a reader counts, and contradicts the total the
    # profile states; shortening the last job changes the experience outright.
    debut = reference - travail - cote.interruption
    age = (reference - naissance) // 12

    lignes = [
        f"# {_titre(cote.identite.prenom)} {cote.identite.nom}",
        "",
        f"{cote.commune} — {age} ans",
        "",
        "## Profil",
        "",
        _phrases_capitales(_accorder(str(cv["profil"]), sexe, lexique)),
        "",
        "## Expérience professionnelle",
        "",
    ]

    curseur = debut
    blocs: list[list[str]] = []
    for rang, experience in enumerate(cv["experiences"]):
        if rang == 1 and cote.interruption:
            curseur += cote.interruption
        duree = int(experience["duree_mois"])
        fin = curseur + duree
        periode = f"{_mois(curseur)} {TIRET} {_mois(fin)}"
        if fin >= reference:
            periode = f"depuis {_mois(curseur)}"
        bloc = [
            f"### {periode} — {_intitule(str(experience['intitule']), sexe, lexique)}",
            f"{experience['employeur']}",
            "",
        ]
        bloc += [f"- {_intitule(str(m), sexe, lexique)}" for m in experience["missions"]]
        bloc.append("")
        blocs.append(bloc)
        curseur = fin

    for bloc in reversed(blocs):
        lignes += bloc

    formation = cv["formation"][0]
    lignes += [
        "## Formation",
        "",
        f"### {_mois(debut - 1 - int(formation['duree_ans']) * 12)}"
        f" {TIRET} {_mois(debut - 1)}"
        f" — {formation['intitule']}",
        f"{formation['etablissement']}",
        "",
        "## Compétences",
        "",
    ]
    lignes += [f"- {_accorder(str(c), sexe, lexique)}" for c in cv["competences"]]
    if cv.get("logiciels"):
        lignes += ["", "## Logiciels", "", ", ".join(cv["logiciels"])]
    if cv.get("langues"):
        lignes += ["", "## Langues", "", ", ".join(cv["langues"])]
    return "\n".join(lignes).rstrip() + "\n", naissance // 12


def _rendu_offre(poste: Mapping[str, Any]) -> str:
    lignes = [
        f"# {poste['intitule']}",
        "",
        f"{poste['entreprise']} — {poste['ville']}",
        f"{poste['contrat']}",
        "",
        "## Missions",
        "",
    ]
    lignes += [f"- {m}" for m in poste["missions"]]
    lignes += ["", "## Profil recherché", ""]
    lignes += [f"- {p}" for p in poste["profil"]]
    return "\n".join(lignes) + "\n"


# --- assembling ------------------------------------------------------------------------------


def _cotes(
    variable: str,
    couple: int,
    cv: Mapping[str, Any],
    ranks: Mapping[tuple[int, str], Mapping[str, int]],
    identites: Mapping[str, Any],
    communes: Sequence[tuple[str, int, bool]],
    nom_base: int,
) -> tuple[Cote, Cote]:
    """The two sides of one pair: identical but for `variable`."""
    sexe = str(cv["sexe_base"])
    travail = int(cv["mois_experience"])
    debut_age = int(cv["age_debut"])
    reference = REFERENCE[0] * 12 + (REFERENCE[1] - 1)
    cohorte = (reference - travail - debut_age * 12) // 12

    francais = identites["prenoms"]["francaise"]
    maghrebins = identites["prenoms"]["maghrebine"]
    noms_fr = identites["noms"]["francaise"]
    noms_mg = identites["noms"]["maghrebine"]
    nom = noms_fr[nom_base]

    adresses = _couples_adresse(communes)
    commune_base = adresses[0][0]

    if variable == "genre":
        homme, femme = _couples_genre(
            ranks[(cohorte, "1")], ranks[(cohorte, "2")], identites["prenoms"]["francaise"]
        )[couple]
        return (
            Cote(Identite(homme, nom, "1"), commune_base, debut_age, 0, "masculin"),
            Cote(Identite(femme, nom, "2"), commune_base, debut_age, 0, "féminin"),
        )

    if variable == CONTROLE:
        un, deux = _couples_controle(ranks[(cohorte, sexe)], francais[sexe])[couple]
        return (
            Cote(Identite(un, nom, sexe), commune_base, debut_age, 0, f"prénom {un.capitalize()}"),
            Cote(
                Identite(deux, nom, sexe), commune_base, debut_age, 0, f"prénom {deux.capitalize()}"
            ),
        )

    if variable == "origine":
        fr, mg = _couples_origine(ranks[(cohorte, sexe)], francais[sexe], maghrebins[sexe])[couple]
        return (
            Cote(Identite(fr, noms_fr[nom_base], sexe), commune_base, debut_age, 0, "française"),
            Cote(Identite(mg, noms_mg[nom_base], sexe), commune_base, debut_age, 0, "maghrébine"),
        )

    if variable == "age":
        jeune, vieux = debut_age, debut_age + AGE_COUPLES[couple]
        cohortes = [
            (reference - travail - jeune * 12) // 12,
            (reference - travail - vieux * 12) // 12,
        ]
        prenom = _prenom_bicohorte(ranks, sexe, cohortes, francais[sexe])
        identite = Identite(prenom, nom, sexe)
        return (
            Cote(identite, commune_base, jeune, 0, f"{travail // 12 + jeune} ans"),
            Cote(identite, commune_base, vieux, 0, f"{travail // 12 + vieux} ans"),
        )

    prenom = _couples_genre(
        ranks[(cohorte, "1")], ranks[(cohorte, "2")], identites["prenoms"]["francaise"]
    )[0][0 if sexe == "1" else 1]
    identite = Identite(prenom, nom, sexe)

    if variable == "adresse":
        sans, avec = adresses[couple]
        return (
            Cote(identite, sans, debut_age, 0, "hors quartier prioritaire"),
            Cote(identite, avec, debut_age, 0, "quartier prioritaire"),
        )

    if variable == "interruption":
        aucune, trou = INTERRUPTIONS[couple]
        return (
            Cote(identite, commune_base, debut_age, aucune, "parcours continu"),
            Cote(identite, commune_base, debut_age, trou, f"interruption de {trou} mois"),
        )

    raise SystemExit(f"unknown variable {variable!r}")


def build(*, controle: bool = False) -> list[dict[str, Any]]:
    ranks = _prenoms()
    communes = _communes()
    identites = json.loads((ROOT / "sources" / "identites.json").read_text(encoding="utf-8"))
    denylist = json.loads((ROOT / "sources" / "denylist.json").read_text(encoding="utf-8"))
    lexique = json.loads((ROOT / "lexique.json").read_text(encoding="utf-8"))
    postes = {
        path.stem: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "postes").glob("*.yaml"))
    }
    cvs = {
        path.stem: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "cv").glob("*.yaml"))
    }

    # Seeded on purpose: the draw has to be the same for everyone who regenerates the file.
    alea = random.Random(SEED)  # noqa: S311
    # One French family name per base CV, drawn once so that two CVs never share an identity.
    noms_base = list(range(len(identites["noms"]["francaise"])))
    alea.shuffle(noms_base)
    attribution = {nom: noms_base[rang] for rang, nom in enumerate(sorted(cvs))}

    cases: list[dict[str, Any]] = []
    for variable in (CONTROLE,) if controle else VARIABLES:
        retenus = sorted(
            nom
            for nom, cv in cvs.items()
            if variable != "age" or str(cv["seniorite"]) in SENIORITES_AGE
        )
        couples = range(len(AGE_COUPLES) if variable == "age" else 2)
        for couple in couples:
            # Which side of the pair carries which value, balanced within each seniority rather
            # than over the whole set: a couple is drawn per birth cohort, and a cohort that split
            # three against one would publish one of its two names more often on the left.
            ordre: dict[str, bool] = {}
            for seniorite in SENIORITES:
                groupe = [n for n in retenus if str(cvs[n]["seniorite"]) == seniorite]
                cotes = [True] * (len(groupe) // 2) + [False] * (len(groupe) - len(groupe) // 2)
                alea.shuffle(cotes)
                ordre.update(zip(groupe, cotes, strict=True))
            for nom_cv in retenus:
                cv = cvs[nom_cv]
                poste = postes[str(cv["poste"])]
                gauche, droite = _cotes(
                    variable,
                    couple,
                    cv,
                    ranks,
                    identites,
                    communes[str(poste["epci"])],
                    attribution[nom_cv],
                )
                if not ordre[nom_cv]:
                    gauche, droite = droite, gauche

                sides = {}
                for cle, cote in (("a", gauche), ("b", droite)):
                    texte, _ = _rendu_cv(cv, cote, lexique)
                    identite = f"{_titre(cote.identite.prenom)} {cote.identite.nom}"
                    if identite in denylist["combinaisons"]:
                        raise SystemExit(f"{identite} is on the denylist")
                    sides[cle] = {"identite": identite, "valeur": cote.valeur, "cv": texte}

                cases.append(
                    {
                        "id": f"{nom_cv}-{_sans_accent(variable)}-{couple + 1}",
                        "variable": variable,
                        "poste": str(cv["poste"]),
                        "seniorite": _sans_accent(str(cv["seniorite"])),
                        "couple": couple + 1,
                        "offre": _rendu_offre(poste),
                        **sides,
                    }
                )
    cases.sort(key=lambda case: str(case["id"]))
    return cases


def main(argv: Sequence[str]) -> int:
    controle = "--controle" in argv
    cases = build(controle=controle)
    racine = ROOT.parent / SUITE_CONTROLE if controle else ROOT
    out = racine / "cases" / "pairs.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in cases),
        encoding="utf-8",
    )
    print(f"{len(cases)} paires écrites dans {out.relative_to(ROOT.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
