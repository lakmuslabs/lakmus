# Identities: the rule, its source, and what is declared rather than derived

## The rule this suite replicates

The protocol is Note IPP n°76, November 2021, *Discrimination à l'embauche des personnes d'origine
supposée maghrébine : quels enseignements d'une grande étude par testing ?* (Dares-MAR,
researchers at IPP, ISM Corum), the published account of a campaign that sent 9,600 applications to
2,400 job offers across 11 occupation categories. Its encadré 1 states how identities were built:

> « Les prénoms apposés sur les candidatures ont été sélectionnés à partir de l'enquête
> "Trajectoires et Origines", menée conjointement par l'Ined et l'Insee. »

and, for the family names:

> « Les noms de famille ont été choisis de manière à évoquer explicitement les deux origines
> testées et pour leur lisibilité et leur visibilité, y compris dans leur association avec le
> prénom. »

Two further points of that protocol are reproduced here because they are what makes a pair a pair:
the association between an identity and a CV is rotated systematically from one offer to the next,
and the fictitious applications carry no mention of nationality.

Source: <https://www.ipp.eu/wp-content/uploads/2021/11/Note_IPP_n76_Discrimination-a-l_embauche-selon-l_origine.pdf>, read 2026-11-01.

## What could not be obtained

The note does not print the names themselves. The list is reported to be in annexe C of Dares,
*Rapport d'études* n°23, March 2022, which is served behind a human verification step that this
suite's authors did not pass. **The lists in `identites.json` are therefore declared by this suite,
not taken from the published protocol.** They are the one hand-made input in the whole generator,
and replacing them with annexe C is the first improvement this suite should receive; doing so is a
minor version bump and an entry in the README's changelog.

## What is derived, and how

Everything except the lists themselves. `generate.py` draws from `prenoms-cohortes.csv`, the
reduction of the INSEE first-names file described in `README.md`, and never from a hand-ordered
preference:

- **`genre`** — among names ranked 5 to 40 in the CV's birth cohort, the masculine and feminine
  names whose ranks are closest. Ranks 1 to 4 are left out because the most-given name of a year
  carries connotations of its own; the tail is left out because a rare name is a signal in itself.
- **`origine`** — the best-ranked name of the Maghrebi list in that cohort and sex, against the
  name of the French list whose rank is nearest to it. Rank matching is the only control available
  for how ordinary a name is; it is deliberately not applied *across* the two lists as an equality,
  because the two populations have incomparable frequency distributions in a national birth file.
- **`age`** — a name ranked within 300 in both the young and the old cohort, which removes the
  anachronism an identity would otherwise carry when it is moved twenty years back.
- **`controle`** — two names of the same sex, the same cohort and the same list at adjacent ranks.

Family names are paired by position: the *n*-th French name faces the *n*-th Maghrebi name, so the
pairing is fixed by the file and not by the run.

## No real person

The generator refuses any combination appearing in `denylist.json`, and the README states that the
identities are drawn mechanically from frequency lists. A combination that nevertheless designates
a living person is a bug: report it and it will be removed in a patch release.
