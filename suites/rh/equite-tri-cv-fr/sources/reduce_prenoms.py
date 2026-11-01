"""Reduce the INSEE first-names file to the cohorts this suite draws identities from.

Usage:

    python3 -I sources/reduce_prenoms.py <prenoms-YYYY-nat.csv> > sources/prenoms-cohortes.csv

The official file is 16 MB and covers every birth year since 1900. The suite needs the rank of a
name within one birth year, for the years its CVs and its age variable use, so committing the
reduction keeps the repository small and the draw reproducible without a download. `sources/
README.md` records the download URL, its date and the digest of the file this was run against.
"""

import csv
import sys

FIRST_YEAR = 1950
LAST_YEAR = 2005
KEPT_RANKS = 300
# The file aggregates every name too rare to publish under this label; it is not a name.
AGGREGATE = "_PRENOMS_RARES"


def main(source: str) -> int:
    kept: list[tuple[str, str, str, str, str]] = []
    with open(source, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            if row["prenom"] == AGGREGATE or not row["periode"].isdigit():
                continue
            if not FIRST_YEAR <= int(row["periode"]) <= LAST_YEAR:
                continue
            if int(row["rang"]) > KEPT_RANKS:
                continue
            kept.append((row["periode"], row["sexe"], row["prenom"], row["rang"], row["valeur"]))

    kept.sort(key=lambda line: (int(line[0]), line[1], int(line[3])))
    writer = csv.writer(sys.stdout, delimiter=";", lineterminator="\n")
    writer.writerow(("annee", "sexe", "prenom", "rang", "naissances"))
    writer.writerows(kept)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
