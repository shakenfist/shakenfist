# PLAN-snapshot phase 12: coverage-guided fuzz harnesses

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (`src/fuzz/` — the
`instar-fuzz` crate, its shared mock-CallTable harness
(`set_fuzz_input` / `build_call_table` / `input_capacity` /
`extract_fuzz_offset` in `src/fuzz/src/lib.rs`), the existing
targets — `fuzz_qcow2_l1l2` for the mock-device parser pattern
and `fuzz_resize_planners` / `fuzz_commit_planners` for the
structured-header pure-planner pattern with invariant asserts;
`src/fuzz/Cargo.toml`'s `[[bin]]` registry; the Makefile
`fuzz-build` / `fuzz-run` targets (devcontainer-wrapped pinned
nightly + cargo-fuzz); `.github/workflows/coverage-fuzz.yml`
(nightly 1h/target, PR smoke, post-merge smoke, the explicit
target list around line 212, corpus seed/push steps);
`scripts/extract-fuzz-corpus.py` (format → target seed mapping
+ `create_minimal_seeds`); and the snapshot crate surface this
phase fuzzes: `src/crates/snapshot/src/qcow2.rs`,
`src/crates/snapshot/src/table.rs`, plus the qcow2 crate's
streaming snapshot parser). Ground every invariant you assert
in the crate's documented contracts — do not speculate when
you could read the doc comments and the phase 5–8 plans.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 12 of
fourteen.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Phases 1–11 delivered the snapshot subcommand, harnesses,
baselines, and the integration suite. Phase 12 adds the two
coverage-guided fuzz targets the master plan's success
criteria name — `fuzz_snapshot_parse` and
`fuzz_snapshot_refcount` — registered in the nightly CI lane
alongside the existing nineteen targets.

### Established infrastructure facts

1. **Two target archetypes exist.** Parser targets
   (`fuzz_qcow2_l1l2` etc.) feed the fuzz input through the
   shared mock CallTable (`instar_fuzz::set_fuzz_input(data)`
   + `build_call_table()`) so call-table-driven code reads
   the fuzzer's bytes as the device. Planner targets
   (`fuzz_resize_planners`, `fuzz_commit_planners`) decode a
   small structured header from the input, synthesize staged
   slices, dispatch pure functions, and **assert semantic
   invariants on success** — errors are silently ignored;
   panic (and ASAN) is the base oracle, the asserts are the
   semantic oracle.
2. **Registration surface**: a `[[bin]]` entry in
   `src/fuzz/Cargo.toml`, the target file, the explicit
   target list in `.github/workflows/coverage-fuzz.yml`
   (~line 212), and a seed mapping in
   `scripts/extract-fuzz-corpus.py` (format → targets for
   natural seeds; `create_minimal_seeds` for synthetic
   targets). Corpus persistence to instar-testdata
   (`custom/fuzz-corpus/<target>/`) happens automatically on
   nightly runs — no testdata-repo work in this phase.
3. **The workflow's PR lane triggers on `src/fuzz/**`**, so
   the phase 12 PR itself will build + smoke the new targets
   in CI when pushed.
4. **Local bounded runs** go through
   `make fuzz-build FUZZ_TARGET=...` and
   `make fuzz-run FUZZ_TARGET=... FUZZ_DURATION=...`
   (devcontainer; no host toolchain).

### Target 1: `fuzz_snapshot_parse`

Mock-CallTable archetype, aimed at the qcow2 crate's
streaming snapshot parser and the snapshot crate's pure
table readers — the code that faces untrusted snapshot-table
bytes in every list/create/delete/apply invocation.

Drive, per input:
- Read sector 0 through the mock device; `QcowHeader::parse`;
  when it parses, run `for_each_snapshot_entry` with the
  header's `nb_snapshots` / `snapshots_offset`.
- A second, header-independent variant with fuzz-derived
  `nb_snapshots` / `snapshots_offset` (bypasses header
  validation to hit the entry parser harder). **Clamp the
  iteration count** (`nb.min(4096)`) so a claimed 65536-entry
  table cannot blow the per-exec time budget.
- In the visitor: assert every `SnapshotEntry` field respects
  its buffer (`id_len as usize <= entry.id.len()`,
  `name_len as usize <= entry.name.len()`), then run
  `snapshot_entry_to_record` and assert the wire record's
  lens respect its 32/256 buffers. Exercise visitor
  early-stop (return `false` after a fuzz-chosen count).
- Over a fuzz-selected window of the raw input, drive the
  pure table readers: `snapshot_table_byte_len`,
  `snapshot_table_entry_bounds` (for every index: bounds lie
  within the claimed length; index ≥ nb errors), and
  `find_snapshot_in_table` in both `MatchMode`s with a
  fuzz-derived needle — asserting any `FoundSnapshot.index`
  is in range and its `l1_table_offset` / `l1_size` equal
  the values decoded from the bounds-located raw entry, and
  the coherence rule `byte_len == end of last entry's
  bounds` whenever both succeed.

### Target 2: `fuzz_snapshot_refcount`

Structured-header planner archetype over the snapshot
crate's mutators — the "no corruption regardless of input"
target the master plan flags as the part needing care.

Header decode (resize-target style): `cluster_bits` clamped
to 9..=14 (stride math exercised, per-exec buffers small),
`extended_l2` flag, `refcount_bits` selector (all seven
widths for scalar paths; the allocator only accepts 16 and
the error path is itself coverage), refblock count 1..=4,
L1 entry count 0..=64, an op selector, an L2-presence
bitmask (driving `l2_for_index` to return `None` for some
allocated entries — the `MisalignedAccess` path), id/name
length fields, and buffer contents cycled from the
remaining input. Reusable `thread_local!` scratch like the
resize target.

**The invariant set.** Each is justified below; the
implementing agent must re-derive each justification from
the crate's documented contracts before asserting it, and
must drop (with a note in the back-brief) any it cannot
justify — a false assert burns the nightly lane with
phantom crashes:

1. **Precheck never mutates**: `precheck_snapshot_refcount`
   leaves `refblocks` byte-identical, success or failure.
   (Contract since phase 7: it delegates to the read-only
   dry-run pass; `&[u8]` makes it structural, the assert
   guards against interior-mutability regressions and
   documents the contract in the fuzzer.)
2. **Precheck-Ok implies apply-Ok**: if the precheck accepts
   `(op, staged state)`, `update_snapshot_refcount` on the
   identical state succeeds. (The apply's internal dry-run
   is the same computation; divergence means the two passes
   read different cluster sets — exactly the phase 6b class
   of bug.)
3. **Inc/dec round-trip identity**: after a successful
   `IncrementForCreate`, a `DecrementForDelete` over the same
   L1/L2 state returns the refblocks to byte-identical
   start state. (Both walks visit the same cluster set —
   data + L2-table clusters; +1 then −1 per cluster; the
   dec cannot underflow because every visited cluster is
   ≥ 1 post-inc.)
4. **Flag-walker idempotence and containment**:
   `update_copied_flags_for_l1` run twice — the second run
   reports 0 rewrites and changes no byte; across the first
   run, for every L1/L2 entry only bit 63 of the
   type-and-offset word may differ (mask-compare), the
   extended-L2 bitmap halves are untouched, and afterwards
   each allocated entry's COPIED bit equals
   `refcount_for_cluster(host_offset) == 1` while
   offset-zero entries are scrubbed clear (the phase 8b
   contract).
5. **Allocator claims exactly what it says**:
   on success of `alloc_contiguous_clusters_in_refblocks`,
   the `count` claimed entries read 0 in a pre-call snapshot
   and 1 after, no other refblock byte changed, and
   `(offset / cluster_size)` indexes the first claimed
   entry; `count == 0` errors `InvalidConfig`;
   on `RefcountExhausted`, a bounded verification scan from
   the cursor's start position confirms no `count`-run of
   zeros exists. Cursor never moves backwards.
6. **Flag-helper containment**:
   `rewrite_l1_entry_copied_flag` /
   `rewrite_l2_entry_copied_flag` change at most bit 63 of
   the targeted entry's type-and-offset word and nothing
   else (byte-compare the full buffer minus that bit).
7. **Table round-trip coherence**: a serialized
   `NewSnapshotEntry` (fuzz-derived id/name lengths within
   the wire bounds) appended via `build_snapshot_table` to a
   fuzz-prefix old table (only when `snapshot_table_byte_len`
   accepts the prefix) yields a table whose `byte_len`
   equals the returned length, whose last entry's bounds
   recover the serialized bytes verbatim, and from which
   `build_snapshot_table_without(remove = k)` produces a
   table whose surviving entries are byte-identical (via
   bounds extraction) to the originals and whose `byte_len`
   re-parses at `nb − 1`.
8. **`SnapshotPlan::push`** accepts exactly
   `MAX_SNAPSHOT_PATCHES` entries then errors.

### What phase 12 does not change

- The snapshot / qcow2 crates: **nothing**. If a 300-second
  local spin (or invariant design) surfaces a genuine crash
  or invariant violation, STOP and report — that is a real
  phase 5–8 bug finding, not something to absorb by
  weakening the assert.
- Existing fuzz targets, corpus, harnesses, tests.
- instar-testdata (nightly runs will create the corpus dirs
  themselves).

## Mission and problem statement

After phase 12 lands: `make fuzz-build` builds both new
targets; `make fuzz-run FUZZ_TARGET=fuzz_snapshot_parse`
and `...=fuzz_snapshot_refcount` each complete a 300-second
local spin with zero crashes and healthy exec rates; both
targets appear in the nightly workflow's list and the corpus
seeder; and the master plan's fuzz success-criterion row is
satisfiable by the next nightly run.

## Open questions

### 1. One refcount target or several?

The invariant set spans nine public functions. **Working
answer: one target with an op selector** (the
`fuzz_resize_planners` precedent dispatches five planners) —
the ops share the staged-state synthesis, and libFuzzer's
coverage feedback handles the dispatch dimension. Splitting
would multiply Cargo/workflow/corpus boilerplate for no
coverage gain.

### 2. Should `fuzz_snapshot_parse` also drive the guest-side find path?

No — the guest binary's find is a thin loop over
`snapshot_table_entry_bounds` + byte compares, already
covered via the pure functions; the guest binary itself is
`no_main`/bare-metal and not linkable into a fuzz target.
The differential fuzzer (phase 13) exercises the full guest
path end-to-end.

### 3. Seeds for the synthetic target?

`fuzz_snapshot_parse` gets natural seeds: add it to the
qcow2 format list in `extract-fuzz-corpus.py` — the eleven
snapshot-bearing fixtures are ideal (verify how the script
truncates qcow2 seeds; if header-only truncation would chop
the snapshot table, add a snapshot-aware extraction that
captures `snapshots_offset .. +table_len` — read the
script's existing per-target special-casing first).
`fuzz_snapshot_refcount` is synthetic: add a
`create_minimal_seeds` entry (a handful of hand-built
structured-header inputs hitting each op selector).

### 4. Per-exec cost budget

The refcount target's worst case is bounded by
`cluster_bits ≤ 14` × 4 refblocks × 64 L1 entries × 64 KiB
L2 staging — comfortably sub-millisecond walks. The parse
target's bound is the `nb.min(4096)` clamp and the mock
device being the input itself (≤ libFuzzer's default
max_len). Report observed exec/sec for both in the
back-brief; under ~500/s warrants investigation before
commit (the existing targets run in the thousands).

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 12a | medium | fable | worktree | `fuzz_snapshot_parse`. Add the target file per the Situation section's drive list, modelled on `fuzz_qcow2_l1l2` (mock CallTable) with the pure-table-reader section alongside; the entry/record buffer-respect asserts and the bounds/byte_len/find coherence asserts per the plan. Add the `[[bin]]` to `src/fuzz/Cargo.toml` (alphabetical/positional convention per the existing file) and any `instar-fuzz` crate dependency additions (`snapshot = { path = ... }`). Clamp iteration per open question 4. Build via `make fuzz-build FUZZ_TARGET=fuzz_snapshot_parse`. |
| 12b | high | fable | worktree | `fuzz_snapshot_refcount`. The structured-header target per the Situation section, modelled on `fuzz_resize_planners` (thread_local scratch, header decode, silent-error/assert-on-success discipline). Implement the eight-invariant set EXACTLY as specified, re-deriving each justification from the crate docs / phase 5–8 plans before asserting; drop-with-note anything you cannot justify. The inc/dec round-trip (invariant 3) and the flag-walker containment (invariant 4) are the load-bearing ones — get the staged-state cloning right so byte-identity comparisons compare like with like. Build it. This step is the phase's risk centre: a wrong assert produces phantom nightly crashes, a too-weak one fuzzes nothing. |
| 12c | medium | fable | worktree | Wiring + local spins. (i) Add both targets to the explicit list in `.github/workflows/coverage-fuzz.yml` (and any other per-lane list in that file — read it fully; the PR-smoke default at ~line 167 may or may not enumerate). (ii) `scripts/extract-fuzz-corpus.py`: qcow2-format mapping for the parse target with snapshot-aware seed extraction per open question 3; `create_minimal_seeds` entries for the refcount target (one seed per op selector). (iii) Local verification: `make fuzz-build` for both; `make fuzz-run FUZZ_TARGET=<each> FUZZ_DURATION=300` — zero crashes, record execs/sec and final coverage counters; seed-corpus smoke (run each target once over its seeds via cargo fuzz run <target> <seed-dir> -- -runs=0 equivalent through the make wrapper, or document why not wirable). If a spin crashes: STOP, triage whether it is a harness bug or a real crate bug, and report before any further work. |
| 12d | low | sonnet | worktree | Gates + docs + commit. `make instar`, `make test-rust`, `make check-binary-sizes` (no production code changed — binaries byte-identical, assert it), `make lint`, `pre-commit run --all-files` (the workflow file edit must pass the Actions linter). Docs: master plan phase 12 row → Landed; the success-criteria fuzz row annotated (targets registered; nightly picks them up). Single commit per `~/.claude/CLAUDE.md` conventions covering: the two targets and their archetypes, the eight-invariant semantic oracle and why each invariant is true, the seed strategy, and the local spin results. |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. **Single Fable agent** — the invariant
correctness is judgement work of exactly the kind Fable
handled well in phases 7/8 (and the master plan's own model
note rated the harness invariants opus-grade, which maps to
Fable post-update). Worktree isolation; verify the base is
the `snapshot` branch head first (`git reset --hard
snapshot` if not — every prior phase needed it).

### Management session review checklist

- [ ] Read both targets; every assert traces to a documented
      contract; no assert was weakened to make a spin pass.
- [ ] The Cargo/workflow/extract-script registrations are
      complete (grep for one existing target's name across
      the repo and confirm the new ones appear in the same
      set of places).
- [ ] Local 300s spins: zero crashes, exec rates reported
      and sane.
- [ ] Binaries byte-identical; all gates green.
- [ ] Independent spot-check: re-run one bounded spin per
      target; deliberately corrupt one staged byte in a unit
      test of the harness's decode (or equivalent) to prove
      the invariants can actually fire.

## Administration and logistics

### Success criteria

Phase 12 is complete when:

* Both targets build in the devcontainer and complete clean
  300-second local spins at healthy exec rates.
* All registration surfaces are updated (Cargo, workflow,
  corpus seeder) — verified by the same-places grep.
* No production code changed; binaries byte-identical; all
  gates green.
* The master plan's phase 12 row is Landed.

### Future work created by this phase

- **Nightly corpus growth** happens automatically; the first
  nightly run after push creates
  `custom/fuzz-corpus/{fuzz_snapshot_parse,fuzz_snapshot_refcount}/`
  in instar-testdata.
- **Phase 13** builds the differential fuzzer on top —
  end-to-end `-c/-d/-a` chains against qemu-img, where the
  freed-cluster and discard divergences documented in phases
  7/8 shape the comparison.

### Bugs fixed during this work

None expected in production code (the phase changes none).
A spin-surfaced crash is a stop-and-report finding against
phases 5–8.

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`docs/plans/order.yml`. The master plan's Execution table
already links this file; step 12d flips the row to Landed.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan — including
your re-derivation (or rejection) of each of the eight
invariants and the observed exec rates.
