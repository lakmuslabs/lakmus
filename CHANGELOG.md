# Changelog

All notable changes to the `lakmus` package are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The suite format and the `run.json` schema have their own version numbers, declared in the format
specification and in each run file. Changes to either are called out here under
**Changed** with the version they move to.

## [Unreleased]

### Added

- Suite format specification, draft version 0.1.
- Package skeleton and `lakmus --version`. No suite can be run yet.
- Module layout under `src/lakmus/`, with the dependency rule between layers enforced by a
  test rather than stated in documentation.
- Domain exceptions in `lakmus.errors`, each carrying the process exit code the CLI uses for
  it: `0` success, `1` threshold failed, `2` usage or validation, `3` runtime.
- `lakmus validate`: checks a suite against the format it declares and reports every problem
  in one pass, each with a stable `LKM-` code and the fix. Suite scorers are imported only
  with `--yes`, and never when there is no terminal to ask at.
- `lakmus init`: scaffolds a suite that validates.
- The `run.json` model for format 0.1, with the JSON Schema generated from it at
  `lakmus/results/schemas/run-0.1.json`. A fingerprint cannot claim a digest it does not
  have, and cannot carry a credential.
- Aggregation: per-metric and per-group summaries, threshold verdicts, and a `conclusive`
  flag that is false when the confidence interval straddles the threshold.
- 95% confidence intervals by a cluster bootstrap over cases, computed with a generator any
  implementation can reproduce, so an interval in a run file can be recomputed by whoever
  reads it. Below 20 scored cases there is no interval: the bootstrap covers badly on a handful
  of observations, and two cases that happen to agree would report certainty.
- `comparable()`: two runs are comparable when the format version, `inputs_sha256` and the
  set of configuration overrides all match. The target is deliberately not part of it.
- A target fingerprint refuses to carry a credential: userinfo and query strings are stripped
  from a URL, an identity that assigns a secret is refused, and so is a parameter named like
  one. Scores that are not finite numbers are refused at the scorer boundary, so a run file is
  always valid JSON.
- The `Target` protocol, and the `openai`, `openai-compatible`, `ollama` and `anthropic`
  adapters. An adapter never retries: it raises for a failure that affects the whole run or is
  worth retrying, and records a failure that belongs to one case, so one bad case cannot end a
  run.
- `--target <adapter>:<model-or-endpoint>`, with the model behind an endpoint named after a `#`,
  and repeatable `-P name=value` parameters that keep the type they were written with and enter
  the target fingerprint.
- `pricing.toml`: exact matching on adapter and model, no prefix matching, and every entry
  carrying the date it was true on and its source. The bundled table is empty on purpose, so an
  unpriced model reports no cost rather than a figure nobody can source.
- Provider messages are quoted into a run with credentials removed in every shape a provider
  uses them: a JSON value, a header, an assignment, or a key recognisable by its prefix. A
  message about token limits survives intact. A redirect, and a 200 carrying an error object,
  end a run instead of being recorded against every case.
- The `@scorer` decorator and seven built-in scorers: `exact`, `regex`, `json_schema`,
  `json_field_match`, `precision_recall_f1`, `pair_gap` and `pair_flip`. A case a scorer cannot
  score has no score and is counted as an error, rather than scored at the worst value — which
  on a lower-is-better metric would let a target that answered nothing pass the threshold.
- `metrics[].with` in the suite format: a metric configures its scorer there, and the accepted
  names are the scorer's keyword-only parameters, so a misspelled option is refused by
  `lakmus validate`. Options live inside the comparability digest, because changing a regex is
  changing the measurement.
- Normalisation steps `trim`, `collapse_space`, `nfc` and `lower`, applied in the order given.
  There is no accent-stripping step: in French `é` and `e` are different answers.
- Manifest models for format 0.1, and the JSON Schema generated from them at
  `lakmus/suite/schemas/suite-0.1.json`.
- Two suite digests: `inputs_sha256`, which changes only when a measurement could change and
  is therefore the comparability key, and `content_sha256` for the whole directory. Relative
  paths are normalised to NFC so that an accented filename digests the same on any
  filesystem, and a golden-vector test pins the algorithm.
- Repository baseline: licence, contribution, conduct and security policies, issue and pull request
  templates, dependency updates, and continuous integration on Python 3.11 to 3.13.

[Unreleased]: https://github.com/lakmuslabs/lakmus/commits/main
