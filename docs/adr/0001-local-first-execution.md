# 0001. Local-first execution

- Status: accepted
- Date: 2026-11-01
- Deciders: JIJI

## Context
Running suites calls paid APIs and can involve sensitive data. A hub that executes everything would carry costs, see user data and slow adoption.

## Decision
The CLI executes suites on the user's machine or CI with the user's API keys. The hub only stores and displays what users choose to publish. Hosted execution is a later, paid option.

## Alternatives considered
- Hub-side execution from day one: high cost, trust barrier, more security surface.

## Consequences
- Positive: zero infrastructure cost for execution, no data leaves the user unless published, works offline with local models.
- Negative: results on the hub are self-reported; integrity relies on fingerprints, replay and later signatures.
- Impact on the suite format spec: none.
