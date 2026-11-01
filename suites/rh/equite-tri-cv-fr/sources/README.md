# Where the values come from

Every value this suite varies is drawn from a public file by a committed script, so that a reader
can repeat the draw instead of trusting it. This note records what was downloaded, when, and the
digest of the exact bytes the reduction was run against. The identities themselves are the one
exception, and `identites.md` says so plainly.

## INSEE — Fichier des prénoms (état civil)

- Page: <https://www.insee.fr/fr/statistiques/8595130>
- File: `prenoms-2025-nat_csv.zip` (4 MB), downloaded 2026-11-01
- SHA-256 of the extracted `prenoms-2025-nat.csv`:
  `5ec80c06cd9266cc5680885c6a219148bcdfaa53d65086f4ea9d2c584431fbfd`
- Reduction: `python3 -I sources/reduce_prenoms.py prenoms-2025-nat.csv > sources/prenoms-cohortes.csv`
- Kept: birth years 1950 to 2005, ranks 1 to 300, both sexes.

Two properties of the file matter to how it is read here. Counts have been rounded to the nearest
multiple of five since the July 2025 edition, which changes no rank this suite uses. Names too rare
to publish are aggregated under `_PRENOMS_RARES`, which the reduction drops: it is a bucket, not a
name.

## ANCT — Quartiers prioritaires de la politique de la ville

- Dataset: <https://www.data.gouv.fr/datasets/quartiers-prioritaires-de-la-politique-de-la-ville-qpv>
- File: `listeqp2024-cog2025.csv` (193 KB), downloaded 2026-11-01
- SHA-256: `2dbf361b8e6d9436f062a5c726737a8ca1883545bba32504c7e8cda485368005`
- Reduction: `python3 -I sources/reduce_qpv.py listeqp2024-cog2025.csv > sources/qpv-communes.csv`
- Kept: the communes of four intercommunalities that hold at least one priority district, under the
  geography in force since 1 January 2024.

## Etalab — Découpage administratif (communes)

- API: `https://geo.api.gouv.fr/communes?codeEpci=<siren>&fields=nom,code,population&format=json`
- Read 2026-11-01 for the four intercommunalities, saved as `communes-epci.json`.

This is what makes the absence of a priority district verifiable. A commune of one of those four
intercommunalities that does not appear in `qpv-communes.csv` holds none, and `population` is what
the two sides of an address pair are matched on.

## What the suite does not claim

The address variable contrasts two communes, not two streets. An earlier draft matched addresses on
public-transport time to the workplace; that needs routing data this suite does not carry, and an
invented street inside a commune would not be inside a priority district's perimeter in any
verifiable way. The commune is what a recruiter reads on a CV, and whether it holds a priority
district is a published fact.
