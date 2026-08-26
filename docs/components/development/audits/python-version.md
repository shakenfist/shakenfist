# Audit: Python version and type hints

## What we check

### Version targeting

Measured. Every Python package must declare `requires-python` in
`pyproject.toml`. A package that does not claims to support every
interpreter, which is never true, and leaves pip with nothing to
refuse an install with.

The value is the system Python of the oldest supported operating
system:

* `shakenfist`: the newest Python packaged by supported *host*
  operating systems (currently Debian 12 and Ubuntu 24.04).
* All other Python projects: the oldest system Python from the
  supported client operating systems listed at
  https://images.shakenfist.com/README.

Where `renovate.json` also carries `constraints.python`, the two must
agree. Both describe the same fact, so a disagreement means one was
updated and the other forgotten -- after which renovate goes on
proposing bumps against a floor the package does not claim, and
nothing else notices, because renovate keeps working. See the
[renovate](/components/development/audits/renovate/) audit for which projects need the constraint
at all.

What the check cannot judge is whether the declared floor is the
*right* one: that needs the supported platforms table, which lives in
each project's `ARCHITECTURE.md`. It checks that a floor is stated,
and that everything stating it says the same thing.

### Type hints and version-appropriate syntax

**Delegated to the pre-push review, and not measured here.** Whether
new code carries useful type hints is a judgment call, and mypy's
verdict depends on a per-project configuration and a staged rollout --
`shakenfist` is part way through one and is held to new code rather
than to its whole tree.

Syntax newer than the declared floor is the finding that matters most
in this area, and it is also not mechanically decidable from a grep:
`match`, `X | Y` unions evaluated at runtime, `tomllib` and
`datetime.UTC` each raise on an interpreter the package still claims
to support, and none of them fail in CI when CI runs only the newest
version.

The reviewer is given this standard by the `python-version-discipline`
shared block in each repository's `PUSH-AUDIT.md`. **Coverage for it
is reported by the [push-audit](/components/development/audits/push-audit/) audit**, which checks
that the block is present and current; there is no per-repository
table here, because it would be a second copy of the one that audit
already publishes.

## Template

No template -- these are code-level standards. The reviewer wording is
`templates/shared-blocks/python-version-discipline.md`.

## Projects

The table below covers the version targeting check only.

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | compliant | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
<!-- consistency-audit:end -->
