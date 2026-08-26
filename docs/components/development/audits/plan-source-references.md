# Audit: Plan references in source

## What we check

Every reference to a plan file (`PLAN-*.md`) written into source code
or configuration must resolve in the repository it is written in, or
else be an absolute URL.

Comments and configuration cite plans to say where a decision is
recorded: "pinned at 50 MB rather than scaled to system memory; the
deferral is recorded in `docs/plans/PLAN-session-001-feedback.md`".
That pointer is the only trail from the code to the reasoning behind
it, and it is the trail a reader follows when they want to change the
code.

Nothing renders these pointers. A markdown link in `docs/` breaks
visibly and `docs-external-links` audits it; a path inside a `//`
comment or a YAML key is inert text no renderer resolves. So a renamed
or archived plan rots the pointer silently, and the first person to
notice is someone who went looking for the reasoning and did not find
it -- at which point the comment is worse than none, because it asserts
a record exists.

The check runs `git ls-files`, skips markdown files (they are
`docs-external-links`' scope) and files over 2 MB, and resolves each
`PLAN-<name>.md` it finds in what remains:

* **Path-qualified references** (`docs/plans/PLAN-foo.md`) resolve as
  written, from the repository root and then from `docs/`. The second
  position exists for mkdocs navigation, which addresses pages relative
  to the documentation root.
* **Bare filenames** (`PLAN-foo.md`) match any markdown file under
  `docs/plans/` at any depth, so a plan archived into
  `docs/plans/completed/` still resolves. A bare filename names no
  directory, so there is no path for it to be wrong about.

Three shapes are not flagged:

* **Absolute URLs.** Text matching a `scheme://` URL is removed before
  scanning. A plan in another repository cannot resolve locally and
  should be written as
  `https://github.com/<org>/<repo>/blob/<default-branch>/docs/plans/PLAN-foo.md`
  -- the same rule `docs-external-links` and `readme-absolute-links`
  apply, for the same reason: a reference read somewhere other than
  where it lives has to be absolute.
* **`PLAN-TEMPLATE.md`.** Not a plan but the template plans are written
  from, living at the repository root and held there by the
  `plan-template` audit.
* **Lines carrying `audit-ok: plan-reference`**, for the rare line
  where a `PLAN-*.md` string is not a pointer -- a filename pattern in
  a linter config, a fixture naming a plan that deliberately does not
  exist.

Test suites are scanned like any other source: a test file carries
prose pointers too and they rot the same way -- instar's
`tests/test_adversarial.py` cites a plan that no longer exists, in its
module docstring -- so skipping files for having "test" in the name
would hide exactly the finding this audit is for. A suite whose plan
paths genuinely are all fixtures carries `audit-ok:
plan-reference-file` once near the top with a sentence saying why. That
exempts the whole file, prose included, so prefer the per-line form.

A repository with no plan references outside markdown is N/A.

This audit composes with `plan-phase-references`, which governs what
documentation prose may cite, and `plan-index`, which governs whether a
plan is registered. This one governs only whether a pointer written in
code still lands on a file.

## Template

No template. Fix each reference at its source:

* the plan moved to `docs/plans/completed/` -- update the path, or drop
  to the bare filename, which resolves either way;
* the plan was renamed -- update the name;
* the plan lives in another repository -- rewrite it as an absolute
  `https://github.com/...` URL;
* the plan never existed, or the reference is not a pointer -- delete
  it, or mark the line `audit-ok: plan-reference`;
* the whole file is fixtures rather than pointers -- mark it once with
  `audit-ok: plan-reference-file`, and say why.

Rewording is not a fix on its own: the point of the pointer is that a
reader can reach the reasoning, so a reference that cannot be made to
resolve should be replaced by the reasoning itself, not deleted.

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
| divergulent | compliant | - |
| instar | non-compliant | shakenfist/instar#516 |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **instar** (Status): 2 of 197 plan reference(s) in source or configuration do not resolve (update the path, or use an absolute https://github.com/... URL for a plan in another repository): src/crates/qcow2-write-exec/src/growth.rs:13 -> docs/plans/PLAN-qcow2-write-infrastructure-phase-07-write.md, tests/test_adversarial.py:8 -> PLAN-adversarial-images.md
<!-- consistency-audit:end -->
