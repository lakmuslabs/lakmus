# Security policy

## Reporting a vulnerability

Report privately through GitHub's advisory form:
<https://github.com/lakmuslabs/lakmus/security/advisories/new>. Do not open a public issue.

Include what an attacker can do, the version or commit you tested, and the smallest reproduction
you have. Expect an acknowledgement within a week. A confirmed issue is fixed on `main`, released,
and disclosed in an advisory crediting you unless you prefer otherwise.

## Supported versions

Pre-release: only the latest commit on `main` is supported. Once 0.1.0 is tagged, the latest minor
release receives fixes.

## Threat model

Lakmus is a local tool that holds the user's API keys and runs suites written by other people. Two
things follow from that, and they shape what counts as a vulnerability.

**A suite is untrusted input.** A suite can carry Python — custom scorers, and later adapters — and
Jinja2 templates. Running a suite you did not write is running that author's code, exactly as
installing a package is. Lakmus reduces the surface rather than removing it: templates render in a
sandboxed Jinja2 environment, third-party code runs only after an explicit confirmation, and suite
paths and archives are checked for traversal and symlink escape. A sandbox strong enough to run a
hostile suite safely is not claimed. Review a suite before running it, as you would a dependency.

**Keys must not leak into artefacts.** Keys are read from environment variables named in
configuration. They are never written to `run.json`, to a report, to the cache or to a log, and
error messages from providers are redacted before they are shown. A key reaching any of those is a
vulnerability.

### In scope

- A key, token or credential appearing in a run file, report, cache entry, log or error message.
- Suite content escaping the template sandbox, or executing without the documented confirmation.
- Path traversal or symlink escape when loading a suite or extracting an archive.
- A network call to a host the configuration did not name.
- A run file or fingerprint that can be forged to misrepresent which target produced a result.
- Dependency confusion or a compromised release artefact.

### Out of scope

- A suite author's code doing what its code says, once you have confirmed running it.
- A target returning harmful, biased or wrong output. Measuring that is what the tool is for.
- Results published to a hub being self-reported. Local-first execution is a deliberate trade-off
  recorded in [ADR 0001](docs/adr/0001-local-first-execution.md); integrity relies on fingerprints
  and replay, and on signatures in a later version.
- Denial of service against your own machine from a suite you chose to run.
