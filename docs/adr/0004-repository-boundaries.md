# 0004. Repository boundaries inside the lakmuslabs organisation

- Status: accepted
- Date: 2026-11-01
- Deciders: JIJI

## Context

Lakmus is not one deliverable: a Python package, a Claude Code plugin, a web hub and community-authored suites all carry the Lakmus name, have different release cadences and different audiences. They share one contract, the suite format, and one artefact, `run.json`. Putting everything in one repository couples unrelated release cycles and makes a single tag ambiguous; creating a repository per idea now fragments a project that has no code yet. The organisation currently holds zero repositories, so the boundaries can be drawn once, before any public URL exists.

## Decision

One repository per independently released deliverable. Repositories never import each other's source; they communicate only through published artefacts.

| Repository | Owns | Released as | Created |
| --- | --- | --- | --- |
| `lakmuslabs/lakmus` | CLI, engine, suite format spec, JSON Schemas, flagship suites | PyPI `lakmus`, tag `v<semver>` | now |
| `lakmuslabs/claude-plugins` | marketplace + `lakmus-dev` plugin | marketplace ref, tag `plugin/v<semver>` | now |
| `lakmuslabs/.github` | org profile, shared issue/PR templates, reusable workflows | nothing; consumed by ref | now |
| `lakmuslabs/hub` | hub API and web front | deployment | after M6 |
| `lakmuslabs/suites` | community suites | tag `<domain>/<name>/v<semver>` | at the first external suite |

Repositories are not prefixed with `lakmus-`: the organisation already carries the name, and GitHub keeps redirects if a rename is ever needed. `CLAUDE.md` is updated to refer to `lakmuslabs/hub`.

**The spec stays in `lakmuslabs/lakmus`.** It has a single implementer until the hub exists; a dedicated repository would only add synchronisation. What must be stable is not the file path but the schema URL. On every `v*` tag, a workflow publishes the generated schemas to GitHub Pages:

```text
https://lakmuslabs.github.io/lakmus/schemas/<format-version>/suite.json
https://lakmuslabs.github.io/lakmus/schemas/<format-version>/run.json
```

A published `<format-version>` path is immutable. Any change to a schema requires a new format version (ADR + spec header bump, per `docs/spec/suite-format.md`). The hub validates suites and runs against that URL, not against the Python models, so it stays language-independent.

**Tag namespaces are disjoint, one per deliverable.** `v*` is reserved for the tool and is the only pattern that triggers `release.yml`; suite releases use `suite/<domain>/<name>/v<semver>`. This keeps tool and suite versioning independent inside one repository and lets flagship suites move to `lakmuslabs/suites` later without changing a single suite id or tag.

**Shared conventions live in `lakmuslabs/.github`** (`CONVENTIONS.md`: commits, review, CI, release; plus reusable workflows). Each repository keeps its own `CLAUDE.md` with what is specific to it and links to that file instead of copying it.

This decision does not depend on the outcome of ADR 0003: whether the engine is ours or wraps an existing harness, the boundaries and the schema URL are unchanged.

## Alternatives considered

- **Monorepo** (tool + hub + suites + plugin) — one tag cannot describe four deliverables with four cadences; a hub deploy would be triggered by a suite typo; contributors would clone a web front to fix a scorer.
- **Spec in its own repository from day one** — the hub does not exist yet, so the only effect today is two repositories to keep in step for every format change. The immutable schema URL already gives the decoupling a separate repository would buy.
- **Community suites in `lakmuslabs/lakmus`** — suite contributions and tool contributions have different reviewers and different risk (a bad suite publishes a wrong claim about a vendor). Mixing them also mixes issue trackers.
- **`lakmus-` prefix on every repository** — redundant under an organisation named `lakmuslabs`, and longer in every clone path and CI reference.

## Consequences

- Positive: each deliverable versions and releases on its own; the hub depends on a frozen, documented URL rather than on Python internals; flagship suites can be extracted later without breaking published ids.
- Negative / costs: conventions are duplicated in intent across repositories unless `.github/CONVENTIONS.md` is actually maintained; a change spanning spec and hub needs two pull requests; the schema-publishing workflow is extra machinery to own from M2.
- Impact on the suite format spec: none. The spec's content is unchanged; only its publication is defined.
- Impact on `run.json` / leaderboard comparability: none today. From M2 on, `lakmus_format` resolves to an immutable schema URL, which makes a run verifiable by any consumer without the CLI.

## Implementation plan

1. Create `lakmuslabs/.github` with the org profile README and `CONVENTIONS.md`; move nothing else there yet.
2. Make `lakmus-repo/` a repository: add `LICENSE` (Apache-2.0), `README.md` (referenced by `pyproject.toml`, currently missing — the build fails without it), `.gitignore`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`. Proof: `uv build` succeeds.
3. Make `claude-plugins/` a repository: add `LICENSE`, `README.md`, `.gitignore`, and a CI job running `claude plugin validate`. Proof: the job is green.
4. Pin the tag namespaces: keep `release.yml` on `tags: ["v*"]`, document `suite/**` in `CONVENTIONS.md`. Proof: a `suite/x/y/v0.0.1` tag does not trigger `release.yml`.
5. At M2, with the first generated schema: add `docs.yml` publishing `schemas/<format-version>/` to Pages, with a job that fails if a path already published on Pages differs from the one being published. Proof: a test tag that mutates an existing schema fails CI.
6. Update `CLAUDE.md` (`lakmuslabs/hub`, link to `CONVENTIONS.md`) and `docs/architecture/ARCHITECTURE.md` ("Out of scope" section names the repositories and the schema URL).
7. Set this ADR to `accepted` once JIJI confirms; create `lakmuslabs/hub` and `lakmuslabs/suites` only when their triggers above are met.
