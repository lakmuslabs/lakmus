# Lakmus suite format

Format version: **0.1** (draft)

A suite is a directory whose root contains `lakmus.yaml`. This document is the contract between suite authors, the CLI and the hub. Any change requires an ADR and a format version bump.

## Directory layout

```text
<suite>/
├── lakmus.yaml        # required
├── README.md          # required
├── LICENSE            # required
├── cases/             # required, at least one .jsonl file
├── prompts/           # required for target.kind = model
└── scorers/           # optional, Python modules
```

## Manifest (`lakmus.yaml`)

```yaml
lakmus: "0.1"                 # format version, required
id: rh/equite-tri-cv-fr      # <domain>/<name>, kebab-case, required
version: 1.2.0               # semver, required
title: Équité du tri de CV en français
domain: rh
task: classement-candidats
language: fr                 # BCP-47
license: CC-BY-4.0           # SPDX identifier
authors: [jiji]
maintainers: [jiji]
risk_level: high             # low | medium | high

target:
  kind: model                # model | agent | mcp
  input: prompts/screener.j2
  output:
    type: json               # text | json
    schema: {score: number, decision: string}

cases:
  source: cases/pairs.jsonl
  holdout: cases/holdout.enc # optional
  sample: null               # null = all cases

metrics:
  - id: score_gap
    scorer: scorers/pair_gap.py:mean_abs_gap
    group_by: [variable]
  - id: decision_flip_rate
    scorer: builtin:pair_flip

thresholds:
  score_gap: {max: 0.05}
  decision_flip_rate: {max: 0.02}

run:
  repeats: 3
  temperature: 0
  seed: 42
  timeout_s: 60
```

## Cases

One JSON object per line. Required: `id` (string, unique within the suite). Every other field is free and read by the prompt template and scorers. Recommended: `meta.category`, `meta.difficulty`.

```json
{"id": "p-0042", "variable": "nom", "a": {"cv": "...", "name": "..."}, "b": {"cv": "...", "name": "..."}, "meta": {"poste": "comptable"}}
```

## Validation rules (`lakmus validate`)

1. Manifest matches the JSON Schema for its declared format version.
2. Every referenced file exists; case ids are unique; JSONL lines parse.
3. `license` is a valid SPDX identifier from the allowed list.
4. Custom scorers import and expose the expected signature.
5. Every threshold refers to a declared metric.
6. `target.kind = model` requires `target.input`.

## Versioning

- **Major**: cases added/removed/changed, metric definition changed, output contract changed. Results across majors are never compared.
- **Minor**: thresholds, documentation, metadata.
- **Patch**: typos.
