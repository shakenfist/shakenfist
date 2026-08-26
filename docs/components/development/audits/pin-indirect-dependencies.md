# Audit: Pinning indirect dependencies

## Who this applies to

Only projects which already exactly pin their own direct dependencies,
detected from the `[project] dependencies` array: at least half of the
entries must carry a `==` (or `===`) specifier. Everything else is not
applicable.

That test is the project declaring its own intent. Pinning a transitive
dependency decides, on a consumer's behalf, which version of a package
they get. In an application we control the runtime environment, so that
is exactly the point -- it makes a broken release three layers away a
build failure rather than a mystery in production. In a library it is
an imposition: a distribution packager building against the versions
their archive ships should not have to fight our idea of the dependency
graph. Our libraries therefore constrain loosely (`>=`) on purpose, and
this audit leaves them alone.

There is deliberately no configured list of applications and libraries.
A project opts in by pinning its direct dependencies, which it cannot
do accidentally, and the split is unambiguous in practice: shakenfist
and kerbside pin about 97% of theirs, while agent-python,
client-python, clingwrap, divergulent, library-utilities and occystrap
pin none.

An earlier version offered libraries a "library variant" recording pins
in a `pinned` extra. That was withdrawn: the base install was left
unconstrained, but the pins still shipped in the published metadata and
Renovate's pep621 manager tracks `optional-dependencies`, so every
recorded version became another stream of bump pull requests.

## What we check

Projects in scope should have:

* `.github/workflows/pin-indirect-dependencies.yml` -- runs daily and
  reconciles the pinned indirect dependency block against what the
  direct dependencies actually require, creating a PR when the block
  changed.
* `tools/pin-indirect-dependencies.sh` -- the reconciler script, copied
  unchanged from the template. It demotes existing pins to pip
  constraints for a fresh resolve; see its header comment for details
  including the `# never-pin: <name>` escape hatch.
* `# START_OF_INDIRECT_DEPS` and `# END_OF_INDIRECT_DEPS` markers in
  `pyproject.toml` delimiting the block the script regenerates (without
  both markers the script refuses to run).

The pinned block lives in `[project] dependencies` alongside the direct
pins.

A `DEPENDENCIES_TOKEN` repository secret with push and PR permissions
is also required. Without it the reconcile still runs and prints its
diff, but the job exits without opening a PR, so the absence is silent
-- check the secret exists when adopting.

## Template

Template: `templates/pin-indirect-dependencies/`
See: `templates/pin-indirect-dependencies/README.md`

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | compliant | - |
<!-- consistency-audit:end -->
