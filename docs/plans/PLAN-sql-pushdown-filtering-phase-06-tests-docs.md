# Phase 6 — Tests, documentation and guardrails

Master plan: [PLAN-sql-pushdown-filtering.md](PLAN-sql-pushdown-filtering.md).
Phase 5: [PLAN-sql-pushdown-filtering-phase-05-adhoc.md](PLAN-sql-pushdown-filtering-phase-05-adhoc.md).

Planning effort: **medium** (sonnet). Small, focused,
mostly-mechanical wrap-up of the SQL-pushdown work —
nothing here changes behaviour. Three short deliverables:
fill the one known test gap, document the pattern, and
land the guardrail.

## Prompt

Before responding to questions or discussion points in
this document, read the four earlier phase plans to
understand what has been delivered (phases 1 through 5,
all complete), the existing shape of `PUSH-AUDIT.md`
and `docs/operator_guide/database.md` and
`ARCHITECTURE.md`, the test file
`shakenfist/tests/test_mariadb_find.py` that covers the
phase-1 primitives, and the current list of production
`mariadb.get_all_*(` call sites (needed for allowlist
decisions). Ground any claim in what the code does today.
Flag uncertainty explicitly.

## Goal

Close out the SQL-pushdown work:

1. Fill the one unit-test gap phases 1–5 left open
   (`find_network_interfaces` has no dedicated tests in
   `test_mariadb_find.py` — it was added in phase 5 but
   only exercised indirectly through the NetworkInterfaces
   iterator).
2. Document the pattern in
   `docs/operator_guide/database.md` and
   `ARCHITECTURE.md` so a future contributor knows when
   to use `find_*` vs `.filter()` vs the iterator.
3. Land the `PUSH-AUDIT.md` guardrail — a wave-1
   mechanical grep that catches new `mariadb.get_all_*(`
   additions outside the established call sites, and a
   wave-2a brief bullet that promotes SQL-pushdown
   discipline from advisory to blocking.
4. Mark the master plan complete (leave phase 7 as
   Planning for future work).

Non-goals for this phase:

* New functional `cluster_ci` tests. Phase 2 added
  `TestArtifactLookupByName`, phase 3 added
  `TestSameNameLookup` (instances + networks); those
  already exercise the name-collision and
  cross-namespace paths. No additional cluster_ci
  coverage is needed — the plan's original "add
  cluster_ci tests" item has already been delivered in
  phases 2 and 3.
* Any new iterator or primitive. Phase 5 closed out the
  iterator surface.
* Executing phase 7 (the denormalised child-UUID list
  removal) — that plan stays in Future-work status.

## Design

### 6a — Unit test gap fill

Phase 5 added `find_network_interfaces` to the phase-1
primitive trio (direct + gRPC + public) but did not
extend `shakenfist/tests/test_mariadb_find.py` with
dedicated test cases for it. The existing file has 40
tests covering `find_artifacts`, `find_instances`, and
`find_networks`; add a fourth class
`DirectFindNetworkInterfacesTestCase` plus
`FindNetworkInterfacesPublicTestCase` and
`GrpcFindNetworkInterfacesTestCase` mirroring the
existing structure.

One quirk to cover: `NetworkInterfaceData` has neither a
`namespace` nor a `name` column, so the direct helper
silently ignores those criteria fields. Add two tests
that specifically verify this:

* `test_namespace_in_criteria_is_silently_ignored` —
  construct `ObjectFilterCriteria(states=...,
  namespace='tenant-a')`, expect the returned rows are
  the same as if `namespace=None` were passed
  (because NetworkInterface has no namespace column).
* `test_name_in_criteria_is_silently_ignored` — same
  shape for `name`.

Otherwise the 10-test template from the other three
types applies verbatim: all-filters, each-filter-alone,
no-filters, states=[], zero-match, empty-table,
OperationalError logs criteria, and so on. Drop
namespace-filter-only and name-filter-only test cases
that don't apply to this type (since both are no-ops).

### 6b — Documentation

Two files get short additions.

**`docs/operator_guide/database.md`** — add a new
subsection under the existing "Access Pattern" section
that describes:

* The `ObjectFilterCriteria` Pydantic at
  `shakenfist/schema/object_filter.py`.
* The `find_*` primitive naming convention and what each
  one JOINs.
* When a caller should use `find_*` (state / namespace /
  name lookups where the column exists on the base
  table) vs `.filter()` (arbitrary Python predicate
  fallback) vs the iterator's typed `namespace=` kwarg
  (the common REST-endpoint case).
* A short note on how `NetworkInterface`'s primitive
  silently ignores `namespace` and `name` criteria
  (column doesn't exist); pointer to the Future-work
  entry.

Target: ~60-100 lines of prose.

**`ARCHITECTURE.md`** — add two or three paragraphs to
the "Database Layer" section that describes the
filter-pushdown discipline at the architectural level
(one-query-per-iteration, index-only plans via
`idx_object_states_type_state` and per-table name /
namespace indexes). Link out to
`docs/operator_guide/database.md` for the details.

Target: ~20-40 lines of prose.

No new diagrams.

### 6c — PUSH-AUDIT.md guardrail

The template's wave-1 style-check block at lines 46-48
today runs three greps (line length, stray `print()`,
new `etcd` references). Add a fourth:

```
git diff develop...HEAD -- '*.py' \
    | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' \
    | grep -v '# nopushdown:'
```

Rationale for the inline-comment allowlist rather than a
file-based list: a solo project doesn't need a
centralised allowlist file; a trailing `# nopushdown:
<reason>` comment on the offending line is immediately
self-documenting at review time and can't silently
drift. Legitimate exceptions today (`Artifact.filter()`
and friends, `_maintain_version_cache`, admin CLI code)
already exist in the tree and so don't trigger the
`git diff develop...HEAD` grep — only brand new
additions get caught.

The plan originally mentioned a file-based allowlist
(`shakenfist/mariadb.py`, `shakenfist/client/`, "explicit
sync-loop call sites"); use the inline-comment form
instead because it is simpler and locality-of-reasoning-
wins.

Also promote the wave-2a code-quality brief's SQL-
pushdown bullet (currently "does any new code
materialise a full object list with `get_all_*()` and
then filter in Python where a `WHERE` clause on an
indexed column could have done the work") from advisory
to **blocking**. One-word change, plus a line of
explanation.

### 6d — Close out the master plan

* Mark phase 6 complete in `docs/plans/index.md`.
* Add a "Bugs fixed during this work" paragraph in the
  master plan listing the real bugs the rollout found
  (the stability-branch KeyError regressions and the
  phase-4 `_resolve_prefilter_to_states` / mock_etcd
  state-filter regressions).
* Phase 7 stays in "Planning" status — it's Future work
  now rather than "imminent".

## Steps

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 6a   | medium | sonnet | none      | Extend `shakenfist/tests/test_mariadb_find.py` with Direct / Public / Grpc test classes for `find_network_interfaces` per the design above. Mirror the existing trio's structure; drop `namespace`/`name` filter-only tests (they are no-ops for this type) and add explicit "silently ignored" tests for those two criteria. Run `tox -e py3 -- shakenfist.tests.test_mariadb_find` to confirm. |
| 6b   | medium | sonnet | none      | Add the filter-pushdown subsection to `docs/operator_guide/database.md` under the "Access Pattern" section, and a short "SQL filter-pushdown discipline" paragraph or two to the Database Layer section of `ARCHITECTURE.md`. Match existing prose voice — terse, single-sentence paragraphs, no emoji. Reference files by relative path with markdown link syntax. |
| 6c   | medium | sonnet | none      | Edit `PUSH-AUDIT.md`: add the new `mariadb.get_all_*` grep as the fourth bullet in the wave-1 style-check block at lines 46-48, including the `# nopushdown:` allowlist filter and a one-line inline comment. Promote the wave-2a "SQL pushdown discipline" bullet at line 83 from advisory ("does any new code materialise...") to blocking ("Blocking: any new `mariadb.get_all_*(` call that is not on an existing line and not tagged `# nopushdown:` must be rewritten as a `find_*` call or as a bulk-scan helper with explicit justification."). |
| 6d   | low    | haiku  | none      | Mark phase 6 complete in `docs/plans/index.md`. Add a "Bugs fixed" paragraph to the master plan listing the four bugs caught during rollout (stability-branch KeyError, mock_etcd state-filter, phase-4 `_resolve_prefilter_to_states` regression, namespace alphabetical-ordering regression). Leave phase 7 at Planning. |
| 6e   | low    | haiku  | none      | Run `pre-commit run --all-files`. Fix anything flagged. Commit as needed. |

Commit grouping: one commit per step (6a, 6b, 6c, 6d),
plus any fixup commit from 6e. Tests in 6a, docs in 6b,
guardrail in 6c, administrivia in 6d.

## Back brief

Before executing any step, the sub-agent must back brief
with:

* Files it intends to change.
* For 6a: confirmation that the existing
  `test_mariadb_find.py` test-class layout matches the
  expected "one class per direct / public / gRPC" shape.
* For 6c: confirmation of the exact current lines of
  `PUSH-AUDIT.md` that need editing (line numbers
  have almost certainly shifted since the plan was
  written).
* Any design decision not explicit in the plan.

## Management session review checklist

After each step:

- [ ] Files changed match the brief; no unrelated edits.
- [ ] `pre-commit run --all-files` passes at least on
      the touched files.
- [ ] For 6a: all new tests pass under `tox -e py3`.
- [ ] Commit messages reference the phase 6 plan with
      the Co-Authored-By line including model / context
      / effort.

## Success criteria for phase 6

* `test_mariadb_find.py` has dedicated
  `find_network_interfaces` coverage equivalent to the
  other three primitives, with explicit assertions that
  `namespace` and `name` criteria are silently ignored.
* `docs/operator_guide/database.md` has a new section
  describing the pushdown pattern and when to use each
  entry point.
* `ARCHITECTURE.md` references the pattern briefly in
  its Database Layer section.
* `PUSH-AUDIT.md` wave-1 has the new grep with an
  `# nopushdown:` allowlist and wave-2a has the
  SQL-pushdown discipline bullet promoted to blocking.
* `docs/plans/index.md` shows phase 6 Complete and
  phase 7 Planning.
* Master plan has a Bugs-fixed section filled in.
* `pre-commit run --all-files` passes.

## Open questions for this phase

1. **Allowlist mechanism.** The master plan mentioned a
   file-based allowlist; I am proposing an inline
   `# nopushdown: <reason>` comment per line instead.
   Decision point: inline comment vs external
   allowlist file. **Current leaning:** inline comment
   — locality wins on a single-author codebase, and the
   grep runs against a diff so only new additions are
   ever tested. Confirm before 6c lands.

2. **Blocking-vs-advisory semantics on wave-2a.** The
   template's wave-2a brief is read by a sub-agent; it
   has no mechanical enforcement. Calling it "blocking"
   signals intent to the review agent (which then
   surfaces it as a blocking finding). If you prefer
   mechanical enforcement instead, we could bump the
   wave-1 grep to `set -e`-style (non-zero exit if any
   finding surfaces). **Current leaning:** soft —
   wave-1 grep reports findings but doesn't exit non-
   zero (matches the existing three greps' behaviour);
   wave-2a brief treats them as blocking in review.
