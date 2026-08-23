# Audit: Dependency name normalization

## What we check

Python projects with `pyproject.toml` must not pin the same
distribution more than once under spellings that PEP 503 treats as a
single package. Names are compared in their canonical form: lowercase,
with any run of `-`, `_` and `.` collapsed to a single `-`. So
`typing-extensions` and `typing_extensions` are one package, as are
`prometheus-client`/`prometheus_client` and `pydantic-core`/
`pydantic_core`.

Within a single dependency array we flag a canonical name that carries:

* two or more exact (`==`) pins with no extras -- differing only by
  spelling; or
* two or more conflicting exact versions.

### Why this matters

A duplicate exact pin is silently harmless while both copies sit at the
same version -- `uv`/`pip` canonicalise the names and dedupe identical
pins. But the instant one spelling is bumped (typically by a Renovate
PR) the two diverge and dependency resolution fails as unsatisfiable:

```
Because you require typing-extensions==4.15.0 and
typing-extensions==4.16.0, your requirements are unsatisfiable.
```

Renovate also treats the two spellings as separate packages and opens a
duplicate PR per spelling, neither of which can pass CI on its own. This
was first hit in `shakenfist` (duplicate PRs #3398/#3399); the same
latent duplicates existed for `prometheus-client` and `pydantic-core`.

### What is *not* flagged

* A direct dependency declared with a floor or extras alongside the
  exact pin appended by the indirect-dependency workflow -- e.g.
  `psutil>=5.9.4` with `psutil==7.2.2`, or `gunicorn[gevent]==25.3.0`
  with `gunicorn==25.3.0`. These are intentional and resolve fine.
* The same name pinned once in the main `dependencies` array and once
  in an `[project.optional-dependencies]` group -- these are separate
  install-time scopes.

### Root cause and prevention

The duplicates originate in the `pin-indirect-dependencies` workflow,
whose "already pinned?" check must compare names in canonical form. A
check that treats `-` and `_` as distinct re-adds a hyphen-pinned dep
under `pip freeze`'s underscore spelling. See the
[Pin indirect dependencies](/components/development/audits/pin-indirect-dependencies/) audit and the
template's workflow for the normalised comparison.

## Template

No dedicated template. The fix is to consolidate the duplicate pins to a
single canonical spelling in `pyproject.toml`, and to ensure the
`pin-indirect-dependencies.yml` workflow compares dependency names in
PEP 503 canonical form.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-23T06:45:38.740880+00:00

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
