# Contributing to Lakmus

Two very different contributions are welcome here, and they need different things from you.

**A test suite** needs domain knowledge: what the right answer is, where a system is likely to cut
corners, what a defensible threshold looks like. It needs very little Python. Start with an issue
describing what you want to measure — the methodology is the hard part and is worth settling before
anyone writes a case.

**Engine or CLI code** needs the rest of this document.

Organisation-wide conventions — language, commit format, tag namespaces, security posture — are in
[CONVENTIONS.md](https://github.com/lakmuslabs/.github/blob/main/CONVENTIONS.md). This file covers
what is specific to this repository.

## Setup

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/lakmuslabs/lakmus
cd lakmus
uv sync
uv run lakmus --help
```

## Checks

`make check` is the gate. It runs exactly what continuous integration runs, in the same order.

```bash
make check                      # format check, lint, types, tests
uv run pytest -q                # unit tests, no network
uv run pytest -q -m live        # integration tests, needs provider keys
uv run ruff format .            # apply formatting
```

Optional, and recommended: `uvx pre-commit install` formats and lints on commit so that `make
check` rarely surprises you.

## Design before implementation

Anything that adds or changes a public interface — a CLI command or flag, the `Target` protocol, a
scorer signature, a manifest field, a `run.json` field, a runtime dependency — starts with a
decision record in [docs/adr/](docs/adr/) and waits for a decision. The template and the criteria
are in the existing records; copy the most recent one.

This is not ceremony. The suite format is a contract with people who will publish results against
it, and `run.json` is consumed by tooling we do not control. Changing either by accident is the one
mistake this project cannot absorb quietly.

`docs/spec/suite-format.md` is the central contract and is not edited without a decision record and
a format version bump.

## Tests

- Derive tests from the specification or the decision record, not from the implementation. If the
  two disagree, the test follows the specification and the mismatch is a finding.
- Unit tests make no network calls, use no real keys and do not sleep. Mock HTTP with
  [respx](https://lundberg.github.io/respx/); use `tmp_path` for files.
- Tests that call a real provider are marked `@pytest.mark.live` and are skipped by default.
- Prefer `pytest.mark.parametrize` with explicit ids over near-identical test functions.
- Include accented text in fixtures wherever text is processed. French content is a first-class
  case, not an edge case.

## Code

Strict typing throughout: `mypy --strict` must pass, and anything crossing a module boundary is a
Pydantic model or a typed dataclass, never a bare `dict`. Every external shape — a manifest, a
case, a provider response, a run file — is parsed into a model at the boundary.

The engine and the adapters are `async`; a blocking call in either is a bug. Retries, backoff and
concurrency limits belong to the engine, so an adapter raises a domain error and returns. Anything
that would make a run irreproducible — a clock, a random source, a seed — is injected, never
global.

Comments explain why, where the reason is not evident from the code. Code that needs a comment to
say what it does usually needs a better name instead.

## Pull requests

Say what changed, why, which decision record it implements, and how you verified it. Keep it to one
logical change. Check suite green. A new runtime dependency is justified in the description, and
`uv.lock` is committed with it.

## Reporting a vulnerability

Do not open a public issue. See [SECURITY.md](SECURITY.md).
