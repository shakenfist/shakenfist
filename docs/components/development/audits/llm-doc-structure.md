# Audit: AGENTS.md / ARCHITECTURE.md structure

## What we check

`AGENTS.md` and `ARCHITECTURE.md` are a **summary and an index**, not
reference manuals. The `llm-tooling` audit checks that they exist;
this audit checks their shape.

* `AGENTS.md` is a working guide: the conventions, invariants and
  gotchas an agent cannot infer by reading the code, plus curated
  links into `docs/`. It is loaded into every session, so every line
  costs context on every task, whether or not the task touches the
  subject.
* `ARCHITECTURE.md` is a map: the component inventory, how data moves
  between components, and why the shape is the way it is. A deep dive
  on a single subsystem belongs in `docs/`, where humans benefit from
  it too.
* One canonical home per fact. If `docs/` covers something, link to
  it rather than restating it -- and the same rule applies between
  `AGENTS.md` and `ARCHITECTURE.md`.

"Is this a good summary" is a judgment call, so the automated check
enforces measurable proxies:

* `AGENTS.md` is at most **300 lines** and **2500 words**;
* `ARCHITECTURE.md` is at most **500 lines** and **4000 words**;
* if `docs/` holds any documentation, each of the two files points
  at a page in it -- a file that indexes `docs/` cannot do so
  without naming something there. Unlike `README.md`, which is
  rendered off the repository landing page and so needs real
  absolute links (see the `readme-absolute-links` audit), these two
  files are read on GitHub and by agents, where a backticked
  `` `docs/design-tokens.md` `` points just as well as a link does,
  so either form counts. `docs/plans/` does not count: a plan is a
  design record, not the documentation these files delegate to, and
  a `docs/` directory holding nothing but `plans/` switches this
  proxy off entirely;
* no `##` heading appears in *both* files, which is the cheapest
  reliable signal that the same subject is documented twice; and
* no `##` or `###` heading matches the filename of a page under
  `docs/` (`## Configuration` against `docs/configuration.md`), which
  is the cheapest reliable signal that a `docs/` page is being
  restated.

Heading comparisons are case-insensitive and treat hyphens as spaces.
Both heading checks skip lines carrying an explicit
`<!-- audit-ok: llm-doc-structure -->` marker, for the case where a
shared heading genuinely covers different ground in each file.

Repositories with neither file are reported as N/A. Plan-phase
history in these files is covered separately by the
`plan-phase-references` audit, which scans them alongside `README.md`
and `docs/`.

Left unchecked these two files accrete the same way READMEs do,
reaching thousands of lines while restating `docs/configuration.md`,
restating a protocol reference in the very section that names that
file as the canonical source, and duplicating a `## Code
Organisation` section between the two.

The judgment half of the policy is enforced at the point where the
bloat is actually created: the documentation-review section of each
repository's pre-push audit file carries the canonical
`llm-doc-discipline` shared block (see the `push-audit` audit), which
instructs the reviewer to send detail to `docs/` and to treat growth
in either file as a finding.

This audit exists because these two files accreted the same way the
READMEs did before the `readme-structure` audit. ryll's reached 1015
and 2262 lines -- around 21,000 words, or roughly 28k tokens of
context -- while restating `docs/configuration.md`, restating
`docs/control-socket-protocol.md` in a section that names that file
as the canonical source, carrying a `make test-qemu` runbook, and
duplicating a `## Code Organisation` section between the two files.

## Template

No template -- these files are project-specific. The fix is to move
detail into `docs/` and leave a short summary plus a link behind. The
move must be a *move*, not a delete: verify the detail survives
somewhere before trimming.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-23T06:45:38.740880+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | N/A | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |
<!-- consistency-audit:end -->
