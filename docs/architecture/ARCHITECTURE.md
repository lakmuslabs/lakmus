# Lakmus architecture (CLI and engine)

Status: v0.1 target. Source of truth for module boundaries and interfaces. Changes require an ADR.

## Principles

1. **Local first.** The CLI works with no account and no hub. Calls to targets leave the user's machine with the user's keys.
2. **A suite is a directory.** `lakmus.yaml` + case files + templates + optional scorers, versioned in Git. No proprietary storage.
3. **A run is a self-contained file.** `run.json` holds everything needed to replay and compare: tool version, suite hash, target fingerprint, config, aggregates.
4. **Deterministic where possible.** Fixed seed, temperature 0 by default, deterministic scorers preferred; LLM judges are explicit and flagged.
5. **Third-party code is untrusted.** Custom scorers and adapters run only after confirmation or in sandbox mode.

## Modules

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `cli` | Typer commands, argument parsing, exit codes, console output | all below |
| `suite` | Load a suite from a path, validate the manifest (Pydantic), compute the suite hash | — |
| `engine` | Render cases, schedule calls with bounded concurrency, retries, cache, call scorers | `suite`, `targets`, `scorers`, `results` |
| `targets` | `Target` protocol, built-in adapters, entry-point registry | — |
| `scorers` | `@scorer` decorator, built-in scorers, loading suite scorers | — |
| `results` | `RunResult` model (`run.json`), aggregation, bootstrap CI, threshold verdicts | — |
| `report` | Static HTML report from a `RunResult` | `results` |

Rule: `targets`, `scorers`, `suite` and `results` never import `engine` or `cli`.

## Run pipeline

1. **Resolve**: load suite, validate manifest, hash every file (SHA-256) into the suite hash.
2. **Render**: Jinja2 sandboxed environment renders the prompt template per case; hash each rendered input.
3. **Execute**: `asyncio` with a semaphore (`--concurrency`, default 8); per-case timeout; exponential backoff with jitter on `TargetRateLimited` / `TargetUnavailable` (max 5 attempts).
4. **Cache**: key = (target fingerprint hash, rendered input hash, repeat index); stored under `~/.cache/lakmus/` as one JSON file per entry. A resumed run never pays twice.
5. **Score**: each metric's scorer per case (and per repeat).
6. **Aggregate**: mean, 95% bootstrap CI (1,000 resamples, seed from run config), per-group values.
7. **Verdict**: compare to thresholds; exit code 1 if any threshold fails.
8. **Write**: `run.json` + `cases.jsonl.gz` + `report.html` in the output directory (default `./lakmus-runs/<run_id>/`).

## Core interfaces

```python
class Target(Protocol):
    kind: Literal["model", "agent", "mcp"]

    async def setup(self, config: TargetConfig) -> None: ...
    async def invoke(self, case: RenderedCase) -> TargetOutput: ...
    async def teardown(self) -> None: ...
    def fingerprint(self) -> TargetFingerprint: ...


class TargetOutput(BaseModel):
    content: str | dict[str, Any]
    trace: list[Step] = []
    usage: Usage | None = None
    cost_usd: float | None = None
    latency_ms: int
    error: TargetError | None = None


@scorer(name="exact_match", higher_is_better=True, deterministic=True)
def exact_match(case: Case, output: TargetOutput) -> Score: ...
```

Target syntax on the CLI: `<adapter>:<model-or-endpoint>`, e.g. `openai:gpt-x`, `ollama:mistral-small`, `http://localhost:8000/agent`, `mcp-stdio:"python server.py"`.

## run.json (format 0.1)

Top-level fields: `lakmus_format`, `run_id` (ULID), `created_at`, `tool`, `suite` {id, version, sha256}, `target` (fingerprint), `config`, `summary` {metric: {value, ci95, threshold, pass}}, `by_group`, `cost`, `latency_ms` {p50, p95}, `cases` (relative path), `signature` (null in v0.1). The JSON Schema lives in `src/lakmus/results/schema/run-0.1.json` and is generated from the Pydantic model.

## Errors and exit codes

`lakmus.errors`: `SuiteInvalid`, `TargetConfigError`, `TargetRateLimited`, `TargetUnavailable`, `TargetBadRequest`, `ScorerError`.
Exit codes: 0 success · 1 threshold failed · 2 usage or validation error · 3 runtime error.

## Out of scope for this repository

Hub API, web front, accounts and hosted execution live in `lakmuslabs/hub`; community suites will
live in `lakmuslabs/suites`. See [ADR 0004](../adr/0004-repository-boundaries.md) for the
boundaries.

Those repositories never import this one's source. They consume the artefacts it publishes: the
`lakmus` package, suite archives, and the JSON Schemas served at an immutable, format-versioned URL
(`https://lakmuslabs.github.io/lakmus/schemas/<format-version>/{suite,run}.json`, published from
`src/lakmus/results/schema/` on each `v*` tag). A consumer validates a run against that URL without
installing the CLI.
