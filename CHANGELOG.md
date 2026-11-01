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
- Repository baseline: licence, contribution, conduct and security policies, issue and pull request
  templates, dependency updates, and continuous integration on Python 3.11 to 3.13.

[Unreleased]: https://github.com/lakmuslabs/lakmus/commits/main
