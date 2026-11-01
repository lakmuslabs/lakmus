# Lakmus

Write a test suite for the work you actually do, then run it against any model, agent or MCP
server.

Public benchmarks measure what can be measured at scale. They say little about whether a system
holds up on a specific job — screening CVs under French labour law, reading a commercial lease,
triaging a support queue in your product's vocabulary. Lakmus makes that test a first-class,
shareable object: a directory in Git that anyone can review, fork and run.

The unit is the test suite, not the model.

> **Status: pre-release.** The suite format is at draft version 0.1 and the engine is in
> development. The command set below is the v0.1 target; not all of it exists yet. The format may
> change before 0.1 is tagged, and such changes are recorded in `docs/adr/`.

## What a suite looks like

A suite is a directory. Nothing in it is specific to a provider.

```text
rh/equite-tri-cv-fr/
├── lakmus.yaml          # what is measured, how, and the thresholds that must hold
├── cases/pairs.jsonl    # the inputs, one JSON object per line
├── prompts/screener.j2  # how a case becomes a prompt
├── scorers/pair_gap.py  # optional: scoring this domain needs
├── README.md            # what the suite claims, and what it does not
└── LICENSE
```

The manifest declares the metrics and the thresholds, so a run has a verdict rather than a number
needing interpretation:

```yaml
lakmus: "0.1"
id: rh/equite-tri-cv-fr
version: 1.2.0
title: Équité du tri de CV en français
language: fr
risk_level: high

target:
  kind: model
  input: prompts/screener.j2
  output:
    type: json

metrics:
  - id: score_gap
    scorer: scorers/pair_gap.py:mean_abs_gap
    group_by: [variable]

thresholds:
  score_gap: {max: 0.05}
```

The full contract is in [docs/spec/suite-format.md](docs/spec/suite-format.md).

## Running a suite

```bash
uv tool install lakmus

lakmus validate ./rh/equite-tri-cv-fr
lakmus run ./rh/equite-tri-cv-fr --target openai:gpt-x
lakmus run ./rh/equite-tri-cv-fr --target ollama:mistral-small
lakmus compare lakmus-runs/01J.../run.json lakmus-runs/01K.../run.json
```

A target is an adapter and an endpoint: a hosted model, a local model, an agent behind HTTP, or an
MCP server over stdio. The exit code is the verdict — `0` passed, `1` a threshold failed, `2` bad
usage or an invalid suite, `3` a runtime error — so a suite drops into CI without a wrapper script.

## Reproducibility

Every run writes a `run.json` that carries the tool version, a hash of every file in the suite, a
fingerprint of the target, the full configuration, and the aggregates with 95% bootstrap confidence
intervals. Two results are comparable when the suite hash and the format version match, and
`lakmus replay` re-runs a recorded configuration without guesswork. Temperature is 0 by default and
the seed is explicit; a scorer that calls a model declares itself as non-deterministic.

Nothing leaves your machine. The CLI calls targets directly with your keys, read from environment
variables named in the suite or on the command line, and writes results to a local directory. There
is no account and no server in the loop. A hub for publishing and comparing results is planned as a
separate project that consumes `run.json`; publishing will be a deliberate step.

## Documentation

- [Architecture](docs/architecture/ARCHITECTURE.md) — modules, the run pipeline, the core interfaces.
- [Suite format](docs/spec/suite-format.md) — the contract between suite authors, the CLI and the hub.
- [Decision records](docs/adr/) — why the project is built the way it is.

## Contributing

Domain expertise is worth more here than Python. If you know a field well enough to say how a
system should behave in it, that is the hard part of a good suite — open an issue and we will work
out the mechanics together.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before a first pull request, and
[CONVENTIONS.md](https://github.com/lakmuslabs/.github/blob/main/CONVENTIONS.md) for the
conventions shared across the organisation. Interfaces are designed before they are implemented:
anything touching a public interface starts with a decision record.

## Licence

[Apache-2.0](LICENSE). Suites carry their own licence, declared in their manifest.
