# Lakmus

A CLI and library that runs domain test suites against models, agents and MCP servers. The unit is
the suite, not the model. This repository holds the CLI and the engine.

Everything committed is in English: code, comments, documentation, commit messages, CLI output.
Organisation-wide conventions — commit format, tag namespaces, CI, security posture — are in
[CONVENTIONS.md](https://github.com/lakmuslabs/.github/blob/main/CONVENTIONS.md) and are not
repeated here.

## Before writing code

A change that adds or alters a public interface — a CLI command or flag, the `Target` protocol, a
scorer signature, a manifest field, a `run.json` field, a runtime dependency — is settled as a
change proposal issue before it is implemented. A bug fix or a refactor with no interface change
needs no proposal; state a three-line rationale and continue.

The suite format is the contract with anyone who publishes a result against it. It does not change
without a recorded decision and a format version bump, and results are never compared across
format majors.

Tests come from the specification or the proposal, never from the current implementation. Where the
two disagree, the test follows the specification and the mismatch is a finding.

`make check` is green before a task is finished.

## Commands

```bash
uv sync
make check                   # format, lint, types, tests
uv run pytest -q             # unit tests, no network
uv run pytest -q -m live     # real providers, needs keys
uv run lakmus --help
```

## Layout

```text
src/lakmus/
  cli/        # Typer commands, no domain logic
  suite/      # loading, Pydantic manifest models, validation
  engine/     # one run: rendering, execution, cache, retries
  targets/    # Target protocol and adapters
  scorers/    # built-in scorers, decorator, loading suite scorers
  results/    # run.json model, aggregation, confidence intervals
  report/     # static HTML report
  errors.py   # domain exceptions
tests/
suites/       # flagship suites maintained by the project
```

`targets`, `scorers`, `suite` and `results` never import `engine` or `cli`.

## Code rules

- Strict typing. Every external shape — manifest, case, `run.json`, provider response — is parsed
  into a Pydantic model at the boundary. No bare `dict` between modules.
- `engine` and `targets` are async. A blocking call in either is a bug.
- Retries, backoff and concurrency limits belong to the engine. An adapter raises a domain error
  and returns.
- Seed, clock and randomness are injected, never global.
- Exit codes: 0 success, 1 threshold failed, 2 usage or validation error, 3 runtime error.
- A comment explains a why that is not evident. Anything else is a naming problem.
- A new dependency is justified in the proposal or the pull request, and `uv.lock` is committed
  with it.

## Never

- Write an API key or any secret anywhere. Keys come from environment variables named in the
  configuration.
- Touch `cases/holdout*` files. A hook refuses it.
- `git push`, tag, release or publish to PyPI without being asked.
- Put AI attribution in a commit message or a pull request description.
- Build the hub here.
- Add an abstraction outside the scope of the current milestone.

## Scope of v0.1

Suite format 0.1, the `init`, `validate` and `run` commands, the `openai`, `anthropic`, `ollama`
and `openai-compatible` adapters, the base scorers, `run.json`, and a local cache.

## Tooling

This repository enables `lakmus-dev` from
[lakmuslabs/claude-plugins](https://github.com/lakmuslabs/claude-plugins): skills for authoring
suites, adapters and scorers, reviewers for code, security and suite methodology, and a hook that
refuses writes to secrets and to reserved holdout cases. `.claude/settings.json` registers the
marketplace; installing the plugin takes the one command in that repository's README.
