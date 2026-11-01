"""Reduce the official QPV list to the four intercommunalities this suite uses.

Usage:

    python3 -I sources/reduce_qpv.py <listeqp2024-cog2025.csv> > sources/qpv-communes.csv

The official file lists 1,584 priority districts over 840 communes. What the suite needs is far
narrower: which communes of four named intercommunalities hold at least one of them. `sources/
README.md` records the download URL, its date and the digest of the file this was run against.
"""

import csv
import sys

EPCI = {
    "200046977": "Métropole de Lyon",
    "200093201": "Métropole Européenne de Lille",
    "244400404": "Nantes Métropole",
    "243100518": "Toulouse Métropole",
}


def _code(value: str) -> str:
    """The commune code, stripped of the spreadsheet armour the file ships it in.

    The column holds `="69123"` so that a spreadsheet keeps the leading zero of a Corsican or
    overseas code. Read as data rather than opened in a spreadsheet, it is three characters of
    noise around the code.
    """
    return value.replace("=", "").replace('"', "").strip()


def main(source: str) -> int:
    communes: dict[tuple[str, str], set[str]] = {}
    with open(source, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            if row["siren_epci"] not in EPCI:
                continue
            # A district straddling two communes is listed once, under both names joined by "; ".
            for name in row["lib_com"].split(";"):
                key = (row["siren_epci"], name.strip())
                communes.setdefault(key, set()).add(row["code_qp"])

    writer = csv.writer(sys.stdout, delimiter=";", lineterminator="\n")
    writer.writerow(("siren_epci", "epci", "commune", "quartiers"))
    for (siren, commune), districts in sorted(communes.items()):
        writer.writerow((siren, EPCI[siren], commune, len(districts)))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
