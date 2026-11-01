# Changelog

All notable changes to the `lakmus` package are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The suite format and the `run.json` schema have their own version numbers, declared in the format
specification and in each run file. Changes to either are called out here under
**Changed** with the version they move to.

## [Unreleased]

## [0.1.0] - 2026-11-01

First release. The CLI runs a suite against a target and writes a result that someone who was
not there can read.

### Added

- Suite format specification, draft version 0.1.
- Package skeleton and `lakmus --version`.
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
- Both the name and the value of a scorer option are checked against the scorer's signature, so
  a typo cannot silently invert a metric: `expect: matched` on `regex` is refused rather than
  scoring 1 for every answer that does not match.
- A published catalogue of the built-in scorers at `lakmus/scorers/schemas/scorers-0.1.json`,
  generated from the registry: each scorer's arity, direction and determinism, and for every
  option whether it is required and which values it accepts. Another implementation can refuse a
  bad option itself rather than trusting this one to.
- The execution engine: sandboxed Jinja2 rendering where a missing field fails rather than
  rendering nothing, bounded concurrency, a timeout per attempt and an optional limit on the
  whole run, retries with exponential backoff and jitter that obey a provider's `Retry-After`,
  and a local cache so that re-running a suite pays only for what failed.
- A run that was interrupted, or that stopped itself after ten consecutive failures, records
  `status: "interrupted"` and is refused by `comparable()`. A partial result can be read; it
  cannot be compared or published.
- A pair suite renders each case twice, once per side, and the two sides are separate calls: the
  target never sees both, because seeing both is a different task.
- `config.overrides` records what an override replaced a value with, not only which field was
  replaced: two runs of one suite at different temperatures are no longer comparable.
- `latency_ms` and `cost` report nothing rather than zero when every answer came from the cache,
  so a resumed run does not claim to have been instantaneous or free.
- `lakmus run`: one suite against one target, writing `run.json`, `cases.jsonl.gz` and
  `report.html` into `lakmus-runs/<run_id>/`, and exiting with the verdict. A suite drops into CI
  with no wrapper script.
- A partial run never produces a verdict: a run that was interrupted or stopped itself exits 3
  even when every threshold in the subset that ran held. Any other choice would make an
  interruption a way to pass a suite.
- Only the overrides an invocation actually named are recorded, never the command's own defaults,
  so a run invoked with no override stays comparable with every other run of that suite.
- The run file is written last, after the cases and the report, and each file is renamed into
  place: the presence of `run.json` is what tells a reader the directory is complete. An output
  directory that already holds something is refused before the first call rather than overwritten.
- `cases.jsonl.gz`: one line per case and repeat with every answer, score and error, opened by a
  header naming the run it belongs to. Byte-reproducible for a given result, and readable with
  accents intact. The rendered prompts are in it only under `--keep-prompts`.
- `report.html`: a single file with no JavaScript and nothing fetched over the network, which
  follows the reader's light or dark theme. It carries the aggregates, the provenance and the
  failures, and never a prompt or an answer — those stay in the cases file.
- An interrupt stops a run cleanly: it takes no new case, lets the calls in flight finish and
  still writes all three files. A second interrupt abandons them.
- `lakmus compare`: refuses two runs that did not measure the same thing and names which key
  differs — the suite digest, an override and its two values, or a run that did not finish. For
  comparable runs it reports whether the two intervals overlap, which is a statement about the
  measurement rather than a significance test this project never defined.
- `lakmus replay`: re-runs a recorded configuration, refusing a suite that no longer digests to
  what that run measured. Naming another target measures it under a configuration that is
  provably identical to the first, rather than one a human retyped.
- Manifest models for format 0.1, and the JSON Schema generated from them at
  `lakmus/suite/schemas/suite-0.1.json`.
- Two suite digests: `inputs_sha256`, which changes only when a measurement could change and
  is therefore the comparability key, and `content_sha256` for the whole directory. Relative
  paths are normalised to NFC so that an accented filename digests the same on any
  filesystem, and a golden-vector test pins the algorithm.
- `suites/rh/equite-tri-cv-fr` 0.1.0, the flagship suite: 120 counterfactual pairs of French CVs
  over four posts, differing by gender, presumed origin, age, the priority-district status of the
  commune, or an unexplained gap, and by nothing else. Values are drawn by a committed generator
  from reductions of the INSEE first-names file and the ANCT list of priority districts, whose
  URLs, dates and digests the suite records. French agreements are carried by markers over an
  invertible lexicon, so that a gendered pair can be proved to differ by the name and its
  agreements alone.
- `suites/rh/equite-tri-cv-fr-controle` 0.1.0: the same CVs under two first names of the same sex,
  cohort and frequency band. It measures how far a target moves when nothing meaningful changes,
  which is the floor the flagship suite's thresholds have to clear.

- Repository baseline: licence, contribution, conduct and security policies, issue and pull request
  templates, dependency updates, and continuous integration on Python 3.11 to 3.13.

### Changed

- The source distribution leaves `suites/` out. The suites are CC-BY-4.0 and this package is
  Apache-2.0; they are not installed, not needed to build, and shipping them inside an artefact
  whose metadata names a single licence would make that metadata wrong.

[Unreleased]: https://github.com/lakmuslabs/lakmus/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/lakmuslabs/lakmus/releases/tag/v0.1.0
