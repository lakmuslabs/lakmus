# 0002. A suite is a Git-versioned directory

- Status: accepted
- Date: 2026-11-01
- Deciders: JIJI

## Context
Suites must be reviewable, versioned and open to contributions.

## Decision
A suite is a plain directory (`lakmus.yaml`, cases, templates, optional scorers) living in a Git repository. The hub indexes repositories and snapshots tagged versions; it does not own the source.

## Alternatives considered
- Database-backed suites edited in a web UI: reinvents diffs, review and history.

## Consequences
- Positive: pull requests, history and forks for free; works with any Git host.
- Negative: non-developer experts need help to contribute (web editor later).
- Impact on the suite format spec: defines it.
