## What and why

<!-- What changes, and the problem it solves. One logical change per pull request. -->

## Decision record

<!--
Link the ADR this implements, or state why none is needed (bug fix, refactor with no interface
change, tests, documentation). A change to a public interface without an ADR will be sent back.
-->

## Verification

<!--
How you know it works: the tests you added, and the result of `make check`. Say what you could not
verify.
-->

## Checklist

- [ ] `make check` passes
- [ ] New behaviour is covered by a test derived from the spec or the ADR, not from the implementation
- [ ] No secret in code, tests, fixtures, logs or run files
- [ ] `CHANGELOG.md` updated under Unreleased, if a user would notice this change
- [ ] A new runtime dependency is justified above, and `uv.lock` is committed
