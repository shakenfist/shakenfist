# qcow2 write infrastructure — phase 02: interim gates

Parent:
[PLAN-qcow2-write-infrastructure.md](/components/instar/plans/PLAN-qcow2-write-infrastructure/).
Planned at high effort. Covers GitHub issues
[#420](https://github.com/shakenfist/instar/issues/420) (commit
snapshot corruption),
[#421](https://github.com/shakenfist/instar/issues/421) (rebase
snapshot-shared refcount corruption / data loss) and
[#422](https://github.com/shakenfist/instar/issues/422) (rebase
512-byte-cluster livelock). The phase-1 findings section of the
master plan holds the evidence and repro recipes; this phase
stops the bleeding. The real fixes (COW) land in phase 7; #420
and #421 stay open until then, with a comment when the gates
land.

## Scope

Three work items. The gates are deliberately minimal: refuse
before any mutation, matching bench's posture and message style,
with byte-idempotence proven by test. No fuzzer changes are
needed — `op_commit` / `op_rebase` never generate snapshot-
bearing fixtures (phase-1 Q3 finding), so the new refusals
cannot introduce fuzz divergence.

### Item 1 — commit gate (#420)

Refuse `instar commit` when the **backing** file has internal
snapshots, before any image mutation.

* **ABI**: append `CommitResult::ERROR_BACKING_HAS_SNAPSHOTS =
  14` in `src/shared/src/lib.rs` (the `impl CommitResult` block;
  codes are append-only — 13 is currently the last). Update any
  exhaustive error-code arrays/tests (grep for
  `ERROR_INTERNAL_OVERFLOW` uses; there is a completeness list
  around src/shared/src/lib.rs:4978).
* **Guest**: in `run_qcow2`
  (src/operations/commit/src/main.rs:444+), immediately after
  the backing header is staged and parsed (the backing
  `stage_side` block, :549+), check `nb_snapshots > 0` on the
  parsed backing `QcowHeader` and return
  `err_result(..., ERROR_BACKING_HAS_SNAPSHOTS)`. This point is
  before the plan step and the per-cluster loop; staging is
  read-only, so the refusal is byte-idempotent (unlike the
  late `ERROR_REFCOUNT_EXHAUSTED` refusal — see the phase-1
  finding about scaffolding writes). The vmdk path
  (`run_vmdk`) has no internal-snapshot concept and is
  untouched. The overlay side is deliberately NOT gated —
  phase 1's second-order probe showed instar matching qemu
  there (see Item 1 verification below for the residual
  variant).
* **Guest error mapping**: add the arm to `map_commit_error`
  (src/operations/commit/src/main.rs:149) if the gate is
  expressed as a `CommitError` variant; a direct `err_result`
  with the new code is also acceptable — follow whichever the
  existing early-refusal gates use (see the cluster-size
  mismatch gate for the pattern).
* **Host**: add the message arm to `map_commit_error` in
  src/vmm/src/main.rs:6985, matching the existing style
  (error 11 reads "the backing's refcount blocks are full; v1
  doesn't append new ones. Fall back to `qemu-img commit`"):
  `"the backing file has internal snapshots; committing would
  corrupt them. Fall back to `qemu-img commit`"`.
* **Tests** (tests/test_commit.py):
  1. Refusal test mirroring bench's
     `test_refuse_internal_snapshot`
     (tests/test_bench.py:1041-1053): build the phase-1 Q1
     fixture (base + `snapshot -c` + overlay), run
     `instar commit`, assert rc != 0, the exact message, and
     sha256-unchanged of BOTH base and overlay
     (byte-idempotence).
  2. Live-parity test for the un-gated overlay-snapshot shape,
     including the variant phase 1 did NOT probe (post-snapshot
     overlay write before commit): run instar and qemu-img on
     twin fixtures, assert exit codes, info-equivalence,
     check-clean, AND osnap content read-back (apply-on-copy +
     convert + sha256) unchanged across the commit on both
     sides. If this variant diverges, STOP and report to the
     management session — do not silently widen the gate; the
     verdict changes this plan.

### Item 2 — rebase safe-mode gate (#421)

Refuse **safe-mode** `instar rebase` (including safe detach)
when the overlay has internal snapshots. `-u` metadata-only
rebase only rewrites the header/backing-pointer region, which is
never snapshot-shared, and qemu permits it — it stays allowed
and gets a parity test instead.

* **ABI**: append `RebaseResult::ERROR_OVERLAY_HAS_SNAPSHOTS =
  14` (append-only after 13) in `src/shared/src/lib.rs`; update
  the exhaustive code list around :4908 and any related tests.
* **Guest**: in the qcow2 path of
  src/operations/rebase/src/main.rs, after the overlay header
  is staged/parsed and the mode is known, refuse when
  `!config.is_unsafe() && hdr.nb_snapshots > 0` (covers safe
  detach too: `is_detach()` without `FLAG_UNSAFE` copies data).
  Placement must precede all writes — safe mode's first
  mutation happens well after header staging, but verify
  against the actual code path and put the check adjacent to
  the existing early gates. vmdk rebase is untouched (no
  internal snapshots).
* **Host**: message arm in `map_rebase_error`
  (src/vmm/src/main.rs:6890): `"the overlay has internal
  snapshots; a safe-mode rebase would corrupt them. Use -u for
  a metadata-only rebase or fall back to `qemu-img rebase`"`.
* **Tests** (tests/test_rebase.py):
  1. Refusal test on the phase-1 Q2 fixture (64K clusters,
     psw=no — the corrupting shape): rc != 0, exact message,
     overlay sha256 unchanged.
  2. Refusal also fires on the psw=yes shape (the gate is on
     `nb_snapshots`, not on sharedness — document in the test
     docstring that instar refuses shapes qemu handles; that
     asymmetry is the documented interim divergence).
  3. `-u` parity test on the same snapshot-bearing fixture:
     instar `-u` and qemu-img `-u` both succeed; assert exit
     codes, info-equivalence, check-clean, and that snap1's
     virtual view changes IDENTICALLY on both sides (read-back
     hash equality instar-vs-qemu, not before/after — the view
     legitimately changes because unallocated ranges now
     resolve through the new backing).

### Item 3 — livelock root-cause and fix-or-gate (#422)

Two sequential steps: investigate, then act on the verdict.

* **Investigation (2c, no production code changes).** Repro is
  the issue #422 recipe (64M, cs=512, safe mode). First
  distinguish true livelock from pathological slowness: the
  phase-1 observation ("mtime frozen") does not rule out a long
  read phase, since reads don't touch mtime. Run a scaling
  curve — same fixture at 4M / 8M / 16M / 32M virtual size,
  cs=512, wall-clock each (generous timeouts) — and, in
  parallel, reason from the code: candidate mechanisms include
  the per-cluster old/new chain comparison loop
  (src/operations/rebase/src/main.rs:729-819) amplifying 64 KiB
  sector-cache reads per 512-byte cluster through
  `CHAIN_CACHES`, the linear `find_staged_l2` scan per cluster,
  and the staged-L2 arena growth path (:742-857) — or a genuine
  non-advancing loop. Use `instar -v` verbose output, host
  strace (is the guest still issuing virtio I/O, or spinning
  CPU-only?), and targeted `verbose_print` instrumentation in a
  throwaway build if needed. Deliverable: the mechanism with
  file:line, the scaling curve, whether it terminates, and a
  fix-vs-gate recommendation.
* **Fix or gate (2d).** Decision criteria: FIX in this phase if
  the root cause is contained (wrong bound, non-advancing
  cursor, cache thrash fixable by a batching/short-circuit
  change) and lands in ≲150 lines including tests, proven by
  the scaling curve flattening and the existing rebase suite
  passing. Otherwise GATE: refuse safe-mode qcow2 rebase below
  a cluster-size threshold measured from the curve (message in
  the house "not yet supported... fall back" style, new
  appended error code), document in quirks.md, and leave #422
  open annotated with the mechanism. Either way, add a
  regression test: the 64M cs=512 safe rebase either completes
  within a CI-safe bound (fix) or refuses cleanly with
  sha256-unchanged (gate).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | default (Fable) | none | Implement Item 1 exactly as scoped (ABI code 14, guest gate after backing staging, host message, both tests). Read the master plan's phase-1 findings Q1 first for the fixture recipes and the byte-idempotence requirement. Build with `make instar`, run `make lint`, the commit integration suite, and `pre-commit run --all-files`. If the Item-1 test 2 parity variant diverges, stop and report rather than widening the gate. One commit, repo conventions. |
| 2b | high | default (Fable) | none | Implement Item 2 exactly as scoped (ABI code 14, safe-mode-only guest gate incl. safe detach, host message, three tests incl. the `-u` parity test). Read the phase-1 findings Q2 first. Sequential after 2a — both steps touch `src/shared/src/lib.rs` and `src/vmm/src/main.rs`. Same build/lint/test/pre-commit bar. One commit. |
| 2c | high | default (Fable) | none | Investigate Item 3 per its Investigation paragraph: scaling curve (4M/8M/16M/32M at cs=512), strace/verbose characterisation (CPU-spin vs I/O-bound), code-level mechanism with file:line, termination verdict, fix-vs-gate recommendation against the stated criteria. Instrumented builds are throwaway — leave the tree clean (`git status` clean, `git diff` empty) apart from nothing; findings return as text. No commit. |
| 2d | high | default (Fable) | worktree if fix | Act on 2c's verdict per Item 3's decision criteria: either the contained fix + scaling regression test, or the threshold gate + refusal regression test + quirks.md entry. Use worktree isolation if fixing (the write path is live-defect territory); direct tree if gating. Same build/lint/test/pre-commit bar. One commit. |
| 2e | medium | default (Fable) | none | Documentation close-out: quirks.md commit/rebase sections gain the two new refusal contracts and the explicit interim divergence note (qemu proceeds where instar now refuses; real fix phase 7); docs/commit.md and docs/rebase.md refusal tables updated; CHANGELOG.md entries referencing #420/#421/#422; master plan execution table (phase 2 → Complete, with 2c's mechanism recorded in the findings section) and plans index status updated; comments posted on #420/#421 (gate landed, corruption no longer reachable, real fix phase 7) and #422 (fixed and closed, or mechanism + gate noted). One commit. |

2a and 2b are sequential (shared files). 2c can run concurrently
with 2a/2b (investigation-only, no tree writes). 2d follows 2c;
2e follows everything.

## Review checklist deltas

Standard checklist from PLAN-TEMPLATE.md, plus:

* Byte-idempotence of every new refusal is asserted by test
  (sha256 of all fixture images unchanged), not assumed.
* The new error codes are appended (14), never reordered, and
  every exhaustive code list / completeness test in
  `src/shared/src/lib.rs` and the VMM knows them.
* The `-u` parity test compares snap1 read-back hashes
  instar-vs-qemu (both change; they must change identically) —
  not before-vs-after.
* Cross-check 2d's outcome against 2c's evidence before
  accepting (curve must flatten for a fix; threshold must match
  the measured knee for a gate).
* No new divergence appears in a differential-fuzz smoke run
  (the pickers build no snapshot fixtures, so any new divergence
  means an unintended behaviour change).
* Master-plan defect register and issues #420-#422 end the phase
  mutually consistent (status, links, mechanism).
