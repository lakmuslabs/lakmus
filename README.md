# Lakmus

A CLI that runs domain test suites against models, agents and MCP servers. The unit is the suite,
not the model: a directory in Git, versioned and reviewable, that states what is measured and the
thresholds that must hold.

> **Status: pre-release.** The suite format is at draft version 0.1 and the engine is in
> development. The command set below is the v0.1 target; not all of it exists yet.

## A suite

```text
rh/equite-tri-cv-fr/
├── lakmus.yaml          # what is measured, how, and the thresholds that must hold
├── cases/pairs.jsonl    # the inputs, one JSON object per line
├── prompts/screener.j2  # how a case becomes a prompt
├── scorers/pair_gap.py  # optional, when the domain needs its own scoring
├── README.md            # what the suite claims, and what it does not
└── LICENSE
```

Nothing in it is specific to a provider. The manifest carries the thresholds, so a run ends with a
verdict rather than a number to interpret:

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

The specification — every field, the validation rules, the versioning policy — is published with
release 0.1.

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

Every run writes a `run.json` carrying the tool version, a hash of every file in the suite, a
fingerprint of the target, the full configuration, and the aggregates with 95% bootstrap confidence
intervals. Two results are comparable when the suite hash and the format version match, and
`lakmus replay` re-runs a recorded configuration. Temperature is 0 by default, the seed is
explicit, and a scorer that calls a model declares itself non-deterministic.

Runs are local. The CLI calls targets directly with your keys, read from environment variables
named in the suite or on the command line, and writes results to a directory you choose. No
account, no server.

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) covers setup and the checks; the conventions shared across the
organisation are in
[CONVENTIONS.md](https://github.com/lakmuslabs/.github/blob/main/CONVENTIONS.md). A change to a
public interface starts as a change proposal issue and is settled before implementation.

Writing a suite needs domain knowledge more than Python. Open an issue describing what you want to
measure.

## Licence

[Apache-2.0](LICENSE). Suites carry their own licence, declared in their manifest.
