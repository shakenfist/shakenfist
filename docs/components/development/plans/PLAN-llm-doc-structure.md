# Plan: an `llm-doc-structure` consistency audit for AGENTS.md and ARCHITECTURE.md

## Context

`AGENTS.md` and `ARCHITECTURE.md` have accreted exactly the way the
READMEs did before the `readme-structure` audit. Measured across the
fleet today:

| repo | AGENTS.md | ARCHITECTURE.md |
|------|-----------|-----------------|
| ryll | 1015 lines / 7,020 words | 2262 lines / 13,772 words |
| shakenfist | 878 / 6,889 | 1294 / 8,998 |
| instar | 684 / 4,008 | 1387 / 9,872 |
| kerbside | 538 / 3,598 | 500 / 3,050 |
| divergulent | 486 / 4,163 | 478 / 3,842 |
| occystrap | 243 / 1,412 | 950 / 5,084 |
| everything else | ≤ 181 | ≤ 284 |

ryll alone is ~21,000 words (~28k tokens) across the two files.
`AGENTS.md` is auto-loaded into every session, so its whole length is
a fixed per-task tax; `ARCHITECTURE.md` is a large on-demand read that
an agent is instructed to consult and update on every change.

The failure modes are concrete, not theoretical:

* **Restating `docs/`.** ryll's `ARCHITECTURE.md` `## Configuration`
  re-documents the `.vv` file format and connection methods that
  `docs/configuration.md` already covers, and then adds a `make
  test-qemu` runbook that belongs in `docs/development.md`. ryll's
  `AGENTS.md` `## Control socket` is a 70-line mini-manual for the
  wire protocol whose canonical home it *names in its own text*
  (`docs/control-socket-protocol.md`, 1046 lines).
* **Restating each other.** ryll has `## Code Organisation` in both
  files; kerbside has `## Configuration` in both; clingwrap has
  `## Dependencies` in both.
* **Plan history.** ryll's `ARCHITECTURE.md` has four top-level
  `## Phase 5/6/7/8` sections. Counts of `phase <n>` across the two
  files: ryll 19+44, instar 3+46, divergulent 6+7, shakenfist 5+5.
  The existing `plan-phase-references` audit cannot see any of it —
  `iter_doc_content_files` scans only `README.md` and `docs/`.
* **Detail that drifts.** ryll's `AGENTS.md` carries a `Server::run`
  signature sketch in Rust — a copy of code, in a file nothing
  compiles.

Root cause is partly the standing instruction in `~/.claude/CLAUDE.md`
to "always update AGENTS.md and ARCHITECTURE.md ... for any
user-visible change", which literally directs growth on every change.
That wording gets fixed too.

**Intended outcome:** `AGENTS.md` becomes a working guide plus an
index; `ARCHITECTURE.md` becomes a map plus an index; the detail lives
in `docs/` where humans benefit from it as well — and an audit keeps it
that way, structured exactly like `readme-structure` (measurable
proxies in CI, judgment enforced at push time by a shared block).

## What "good" looks like

* **`AGENTS.md` — how to work in this repo.** Conventions, invariants
  and gotchas an agent cannot infer by reading the code, plus curated
  links into `docs/`. Not tutorials, not protocol specs, not
  architecture, not CLI reference.
* **`ARCHITECTURE.md` — how the system is shaped.** Component
  inventory, how data moves between components, key design decisions
  and *why*, plus links to the deep dives. Not configuration
  reference, not runbooks, not phase history.
* **One canonical home per fact.** If `docs/` covers it, link to it.
  Same rule between the two files.

## Implementation

Work happens in a worktree off `shakenfist/development`; the plan file
moves into that worktree so it lands with the change (per CLAUDE.md).

### 1. Audit spec — `audits/llm-doc-structure.md`

New file following the `audits/README.md` structure, modelled closely
on `audits/readme-structure.md`: "What we check", no template, and an
empty `<!-- consistency-audit:begin/end -->` marker block. Pairs with
the existing `llm-tooling` audit, which checks *existence* of these
files; this one checks *shape*. Register it in the audit index table
in `audits/README.md`.

### 2. Automated check — `scripts/audit-check.py`

New `check_llm_doc_structure(repo_path, props)` next to
`check_readme_structure` (~line 2199), reusing `check_file_exists`,
`strip_markdown_code`, `MD_LINK_RE` and `MD_REFDEF_RE`. Accumulates a
`problems` list and returns `pass`/`fail`/`not_applicable` in the same
shape. N/A when neither file exists.

Four measurable proxies:

1. **Length caps.** `AGENTS.md` ≤ 300 lines / 2,500 words;
   `ARCHITECTURE.md` ≤ 500 lines / 4,000 words. Constants beside
   `README_MAX_LINES` / `README_MAX_WORDS`.
2. **Index behaviour.** If `docs/` exists, each present file must
   contain at least one link into `docs/`. (occystrap's `AGENTS.md`
   mentions `docs/` zero times today.)
3. **Cross-file duplication.** Flag H2 headings appearing in *both*
   files, normalised to lowercase. Fleet-wide this fires on exactly
   three repos — precise, not noisy.
4. **Duplication with `docs/`.** Flag an H2/H3 heading whose
   normalised text matches a `docs/<slug>.md` filename stem
   (`configuration` ↔ `docs/configuration.md`). Fires on kerbside
   (×3), ryll (×1), instar (×1) today.

Checks 3 and 4 are suppressible with an
`<!-- audit-ok: llm-doc-structure -->` marker on the heading line,
following the `PHASE_REFERENCE_OK` precedent.

Register in the dispatch table (~line 2924) and in
`scripts/audit_common.py`: `AUDIT_METADATA['llm-doc-structure']` with
`'spec': 'audits/llm-doc-structure.md', 'template': None`, plus
`ISSUE_TITLES['llm-doc-structure'] = 'AGENTS.md / ARCHITECTURE.md
structure'` and the display-name map at `audit-check.py:83`.

Unit tests in `scripts/test_audit_check.py` alongside the
`check_readme_structure` test at line 164: one pass case, one per
proxy, one N/A, one suppression-marker case.

### 3. Extend `plan-phase-references` (your choice: extend, don't fork)

One seam: `iter_doc_content_files` (`audit-check.py:2264`) also yields
top-level `AGENTS.md` and `ARCHITECTURE.md` when present. The existing
fenced-code, inline-code and `audit-ok` suppression logic then applies
unchanged. Update the "What we check" text in
`audits/plan-phase-references.md` to say so, and its N/A condition
(currently "neither a top-level README.md nor a docs/ directory").

This marks ryll, instar, divergulent and shakenfist non-compliant on
that audit as soon as it runs — expected and intended.

### 4. Shared block — `templates/shared-blocks/llm-doc-discipline.md`

New versioned block (`v1`) mirroring `readme-discipline.md`, carrying
the judgment half of the policy: what each file is for, one canonical
home per fact, no reference manuals / runbooks / plan history, and
"growth in either file is itself a finding — move it to `docs/`".

Add `'llm-doc-discipline'` to the `required=[...]` list in
`check_push_audit` (`audit-check.py:2515`), update that function's
docstring, `audits/push-audit.md`, and the required-blocks list in
`templates/shared-blocks/README.md`. Every repo with a `PUSH-AUDIT.md`
(all in-scope except client-python) then needs the block embedded —
the `push-audit` audit files those issues automatically.

### 5. Root-cause fix — `~/.claude/CLAUDE.md`

Rewrite the "Updating documentation" bullet so it stops instructing
growth. Replacement intent: document user-visible changes in `docs/`;
touch `AGENTS.md` only when a *convention* changes and
`ARCHITECTURE.md` only when the *shape of the system* changes; both
files summarise and link rather than restate. Keeps the existing
"propose moving misplaced content into `docs/`" clause, which already
points the right way.

### 6. Regenerate and document

`PROJECT-CONSISTENCY-AUDITS.md` gets the new criterion. The compliance
tables in the affected audit specs are regenerated by the workflow —
never by hand.

## Migration (separate commits, after the audit lands)

The audit will file issues on ~7 repos. Trimming a 2262-line
`ARCHITECTURE.md` is judgment-heavy, so:

* **ryll and kerbside by hand**, one commit per file, as the reference
  examples — they are the two you named and the two worst cases. Each
  trim is a *move*: the detail lands in a `docs/` page (new pages such
  as `docs/spice-protocol.md`, `docs/display-pipeline.md` for ryll's
  600+ lines of protocol and image-format detail) before it leaves
  `ARCHITECTURE.md`, and the section is replaced by a one-paragraph
  summary plus a link.
* **shakenfist, instar, occystrap, divergulent** follow via the normal
  consistency-issue flow, using the ryll/kerbside commits as the
  worked example.

I will not start the migration without a separate go-ahead.

## Verification

* `python3 -m pytest scripts/test_audit_check.py` — new and existing
  cases pass.
* `python3 scripts/audit-check.py` against local checkouts (dry run,
  no issue filing) and confirm the reported non-compliance matches the
  table in Context: AGENTS — divergulent, instar, kerbside, ryll,
  shakenfist; ARCHITECTURE — instar, occystrap, ryll, shakenfist; plus
  the duplication hits on kerbside, ryll, instar, clingwrap.
* Confirm the small repos (agent-python, client-python, clingwrap,
  cloudgood, sfui, kerbside-patches, client-python-k3s) stay
  compliant — no busywork for repos that are already fine.
* Confirm `plan-phase-references` now reports the ryll/instar/
  divergulent/shakenfist hits, and that nothing inside `docs/plans/`
  or fenced code blocks is flagged.
* `pre-commit run --all-files` before proposing any commit.
