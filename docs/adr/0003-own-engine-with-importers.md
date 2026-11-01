# 0003. Own execution engine, with importers for existing formats

- Status: proposed
- Date: 2026-11-01
- Deciders: JIJI

## Context
Mature evaluation harnesses exist (promptfoo, Inspect, lm-evaluation-harness). Building on one would speed up v0.1 but tie the suite format, the core of Lakmus, to another project's model.

## Decision
Build a small engine owned by Lakmus around the suite format, and provide `lakmus import` converters from common formats (JSONL, CSV, promptfoo configs).

## Alternatives considered
- Wrap promptfoo: Node.js dependency, format controlled elsewhere.
- Wrap Inspect: strong engine, but research-oriented task model and heavier dependency.

## Consequences
- Positive: full control of the contract, reproducibility fields and MCP/agent support.
- Negative: more code to maintain; must reach parity on basics (retries, cache, concurrency).
- Impact on the suite format spec: none.

## Open question
Validate with JIJI before milestone M2.
