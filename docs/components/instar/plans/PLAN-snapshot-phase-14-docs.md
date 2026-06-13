# PLAN-snapshot phase 14: documentation and close-out

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the per-subcommand
doc shape — `docs/commit.md` is the closest sibling and the
template for `docs/snapshot.md`; `docs/index.md`'s subcommand
table; `CHANGELOG.md`'s Keep-a-Changelog style and the `op_map`
entry as the voice model; `docs/plans/index.md` row conventions;
`docs/quirks.md`'s snapshot sections, which the new doc links
rather than duplicates; the `snapshot` crate's public surface in
`src/crates/snapshot/src/lib.rs`; `qcow2::find_snapshot` and its
single call site in `src/operations/convert/src/main.rs`; and
`.github/workflows/functional-tests.yml`'s job structure before
wiring anything into it). Ground every parity claim in the
quirks catalogue and the probe transcripts below.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 14 of
fourteen — the close-out.

I prefer one commit per logical change, and at minimum one
commit per phase. This phase makes FOUR commits (see Execution).

## Situation

Phases 1–13 delivered the complete `instar snapshot` subcommand:
ABI, list mode (human + JSON, cross-version baselines), the
three mutating modes with byte-parity against qemu-img under
`file.discard=ignore`, 94 integration tests, two coverage-guided
fuzz targets in nightly CI, and the differential fuzzer's
`op_snapshot` chain. Along the way the work fixed real bugs in
list-name truncation, multibyte column padding, and delete's
surviving-L2 COPIED refresh. Phase 14 writes the documentation,
closes the master plan's bookkeeping, and resolves the four
deferred dispositions — three of which turn out to involve small
code changes, pinned by the probes below.

### Empirically established behaviour (probes, qemu-img 10.0.8)

1. **`qcow2::find_snapshot` is NOT unused — and it has a real
   parity bug.** The master plan's future-work entry ("confirmed
   wrong for both mutating modes' semantics and is unused —
   phase 14 removes or re-documents it") is stale on both
   counts. `src/operations/convert/src/main.rs:426` calls it to
   resolve `instar convert --snapshot ARG`, and its per-entry
   id-or-name matching diverges from qemu. Probe: image with
   snapshots `id=1 name="2"` (content 0xAA) and `id=2 name="x"`
   (content 0xBB); `qemu-img convert -l 2` extracts snapshot
   **ID 2** (0xBB — qemu resolves via a full ID pass, then a
   full name pass, exactly like `snapshot -a`'s matcher);
   `instar convert --snapshot 2` extracts the snapshot **named
   "2"** (0xAA — first per-entry hit). The companion
   `find_snapshot_streaming` has the same semantics and **no
   callers outside the qcow2 crate's own tests** — dead code.
2. **The zero-date `-l` divergence is a 3-line special case.**
   `format_qemu_snapshot_date_local` (`src/vmm/src/main.rs:11279`)
   early-returns an empty string for `date_sec == 0`; qemu
   feeds 0 through `localtime` and renders the epoch in local
   time. Removing the early return restores parity (the
   `localtime_r` path handles 0 fine). The JSON output path
   carries raw numeric date fields and is unaffected.
3. **`SnapshotPlan` / `SnapshotPatch` / `MAX_SNAPSHOT_PATCHES`
   are dead production code.** Grep confirms the only users are
   `src/crates/snapshot/src/lib.rs`'s own unit tests and
   `fuzz_snapshot_refcount`'s op 7 / invariant 8 (phase 12). The
   guest binary went with direct write-groups in phase 6 and
   never adopted the planner API.
4. **The CI container can run the shell harnesses.** They need a
   built `instar`, qemu-utils, and `/dev/kvm` — the same
   prerequisites the functional-tests and differential-fuzz
   workflows already provision.

## Mission and problem statement

After phase 14 lands: `docs/snapshot.md` exists and is indexed;
every doc listed in the master plan's success criteria is
current; `CHANGELOG.md` records the feature and its fixes;
`PLAN-convert-followups.md` strikes `snapshot` (the subcommand
roster is complete); `docs/plans/index.md` marks PLAN-snapshot
**Complete**; the four deferred dispositions are resolved in
code; and the snapshot harnesses run in CI.

## Open questions

### 1. `find_snapshot` disposition?

**Fix it, don't remove it.** Probe 1 shows a real user-visible
bug: `convert --snapshot` picks the wrong snapshot on
ID/name-collision images. Rework `qcow2::find_snapshot` to two
full passes (ID pass over all entries, then name pass —
qemu's `find_snapshot_by_id_or_name` shape, the same semantics
`snapshot -a` implements), update its doc comment, and add unit
tests for the collision permutations (later-ID beats
earlier-name; name-only fallback; not-found). Delete the dead
`find_snapshot_streaming` and its tests outright. Add an
integration regression test for `convert --snapshot` on a
collision image (home it with the existing convert snapshot
tests; assert extracted content matches `qemu-img convert -l`).
Residual to document in `docs/quirks.md` (convert section): the
lookup walks the bounded 16-entry table (`MAX_SNAPSHOTS`), so a
snapshot beyond the first 16 is not-found under instar where
qemu finds it — same v1 cap family as the snapshot subcommand's.

### 2. Zero-date rendering decision?

**Match qemu: render the epoch.** The project's standing
principle is byte-parity with qemu-img; a blank column is a
deliberate divergence we would carry forever for a degenerate
input. Remove the `date_sec == 0` early return (probe 2), add a
unit test pinning the epoch rendering (use `TZ=UTC` via the
existing test pattern for date-dependent tests — check how the
phase 4 date tests pin TZ), and REWRITE the
`docs/quirks.md` "Zero `date_sec` renders a blank DATE column"
entry into a fixed-behaviour note (the entry currently says
phase 14 owns the decision — record the decision and the fix).
The differential fuzzer's nonzero date sentinel stays as-is
(nothing depends on the divergence; no churn).

### 3. `SnapshotPlan` disposition?

**Remove it.** Dead production code (probe 3); the project
convention is no speculative API. Removal ripples: delete the
type, `SnapshotPatch`, `MAX_SNAPSHOT_PATCHES`, and their lib.rs
unit tests; in `fuzz_snapshot_refcount` drop op 7 / invariant 8
(op selector `% 8` → `% 7`, module doc updated, the
invariant-8 doc block removed); in `scripts/extract-fuzz-corpus.py`
drop the `minimal_op7` seed (and renumber nothing — ops 0..=6
keep their meanings). Rebuild the fuzz target and run a
60-second spin smoke to confirm the harness still executes
(`make fuzz-run FUZZ_TARGET=fuzz_snapshot_refcount
FUZZ_DURATION=60`). The phase 12 plan document is history — do
not edit it; the commit message records the ripple.

### 4. Harnesses in CI?

**Yes — a Makefile target plus a functional-tests job.** Add
`make snapshot-harnesses` running all seven
`tools/snapshot-*.sh` scripts (fail on first failing harness;
they need `$INSTAR` pointing at the built binary — read how the
scripts resolve it). Wire a job into
`.github/workflows/functional-tests.yml` following that
workflow's existing job conventions (build instar, qemu-utils,
/dev/kvm — read the whole file first; if its structure makes a
new job awkward, a step appended to an existing
integration-test job is acceptable; say which you chose and
why). The seven harnesses currently total 241 assertions and
run in well under five minutes.

### 5. What does `docs/snapshot.md` contain?

Mirror `docs/commit.md`'s shape: what the subcommand does, the
guest/host split, synopsis, the four modes with their matcher
semantics (the `-d` name-only vs `-a` ID-then-name asymmetry
table from quirks), options (`-q`, `-U`, `-f`, `--output`,
rejected `--image-opts`), feature gates and refusals
(refcount_bits, compressed, encrypted, external data, bitmaps,
dirty), v1 limits (16-snapshot cap, no refcount-structure
growth, 255-byte names, refused empty names, apply-after-resize
refusal), crash-safety write-group ordering (condensed from the
guest's module docs), the parity statement (byte-identical
mutations under `file.discard=ignore`, sector-granular tail,
table-padding note) with links into `docs/quirks.md` rather than
duplicated text, and a verification section (harnesses, 94
integration tests, fuzz targets, differential fuzzer).

### 6. One CHANGELOG entry or several?

The feature gets one **Added** entry (the whole subcommand,
phases 1–13 condensed, voice-matched to the existing `op_map`
entry); the user-visible fixes get **Fixed** entries: multibyte
list padding (5f6a1b9), delete surviving-L2 COPIED refresh
(a5d0767), the convert `--snapshot` collision fix and the
zero-date rendering fix (this phase). The name-truncation fix
(c2e1cc6) predates any release of the feature it fixes — fold
it into the Added entry's prose rather than a Fixed line.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 14a | medium | fable | in-worktree | **Commit 1 — convert matcher fix.** Open question 1: two-pass `find_snapshot`, delete `find_snapshot_streaming` + tests, collision unit tests, convert integration regression test, quirks note for the 16-entry lookup cap. Gates: `make instar`, `make test-rust`, the new integration test, `pre-commit run --all-files`. Re-run the probe-1 scenario and confirm instar now extracts 0xBB. |
| 14b | low | fable | in-worktree | **Commit 2 — zero-date rendering.** Open question 2: remove the early return, unit test, quirks entry rewrite. Verify with a date-zeroed image that `instar snapshot -l` matches `qemu-img snapshot -l` byte-for-byte (the phase 13 normdates probe shape). |
| 14c | medium | fable | in-worktree | **Commit 3 — SnapshotPlan removal.** Open question 3 in full, including the fuzz-target and corpus-seeder ripple and the 60s spin smoke. `make fuzz-build` both snapshot targets; binary sizes unchanged (the crate change is dead-code-only — state it). |
| 14d | medium | fable | in-worktree | **Commit 4 — docs + CI + close-out.** Open questions 4–6: `docs/snapshot.md`; `docs/index.md` row; README sweep (subcommand coverage section + tests section already mention snapshot — verify and fill gaps, including confirming the README mentions `.claude/skills/`); `ARCHITECTURE.md` snapshot-op section sweep; `AGENTS.md` sweep; `docs/usage.md` sweep (Proxmox rows already list snapshot); `docs/testing.md` (harnesses, fuzz targets, differential op); `docs/qcow2/qcow2-snapshots.md` currency sweep (must reflect delete's surviving-L2 refresh from a5d0767); `CHANGELOG.md`; `PLAN-convert-followups.md` strike (roster complete — note phase 1 of that plan needs only the check-repair phase 2 work); master plan phase 14 row → Landed + success-criteria final sweep (each criterion explicitly checked or annotated); `docs/plans/index.md` PLAN-snapshot row → Complete; `make snapshot-harnesses` + the functional-tests job. Run the full harness battery via the new make target as its own verification. `pre-commit run --all-files` (the workflow edit must pass actionlint). |

## Agent guidance

### Execution model

**Single Fable agent, working directly in the operator's
`snapshot`-branch worktree** (the phase 12/13 pattern; isolated
worktrees spawn on stale bases). The agent must not run
`git reset` / `git restore` / `git checkout --` or anything else
that discards state; its only git writes are `git add` of files
it touched and the four commits, in the step order above, each
gated green before committing.

The master plan rated this phase "low effort, haiku or sonnet"
when it was docs-only; the probes above moved three code
dispositions into it, so it gets the same single-fable treatment
as phases 12/13. The mechanical doc sweeps ride along.

**Permission caveat (operator action before dispatch).** The
sub-agent Bash allowlist blocks `python3`, `qemu-img` (except
`--version`), `qemu-io`, and `bash tools/*.sh`. The agent
implements everything and runs the allowlisted gates
(`make instar` / `test-rust` / `lint` / `fuzz-build`,
`pre-commit`); the probe re-runs, harness battery, date-zeroed
list comparison, and fuzz spin smoke fall to the management
session unless the allowlist is extended first. The agent must
list precisely which verifications it could not run, per commit,
and must NOT commit a step whose load-bearing verification is
blocked — stop at that boundary instead (the phase 13 pattern:
implementation complete, honest handoff).

### Management session review checklist

- [ ] Probe-1 re-run: collision image extracts 0xBB under both
      tools; the regression test fails on the pre-fix binary
      (check out the parent commit's binary or revert the
      matcher locally to prove it can fire).
- [ ] Date-zeroed `-l` byte-equality vs qemu.
- [ ] Fuzz spin smoke after the op-7 removal; corpus seeder
      still runs end-to-end against testdata.
- [ ] `make snapshot-harnesses` green locally (241 assertions);
      the workflow job passes actionlint.
- [ ] Doc sweep spot-checks: snapshot.md claims trace to quirks
      entries or probe transcripts; no invented numbers; the
      success-criteria sweep leaves nothing silently unchecked.
- [ ] Four commits, each self-contained and green.

## Administration and logistics

### Success criteria

Phase 14 — and with it PLAN-snapshot — is complete when:

* The four dispositions are resolved as decided above, each in
  its own green commit.
* `docs/snapshot.md` exists, is indexed, and every master-plan
  success-criteria doc is current; CHANGELOG records the feature
  and fixes; convert-followups strikes snapshot;
  `docs/plans/index.md` says Complete.
* `make snapshot-harnesses` exists, passes locally, and runs in
  CI.
* All gates green; the master plan's phase 14 row is Landed.

### Future work created by this phase

- **Convert's 16-entry snapshot lookup cap** (quirks note from
  step 14a) joins the existing >16-snapshots future-work family.
- The master plan's Future work section already records the
  remaining deferrals (bitmaps, compressed, encrypted, >256
  listing, disk_size-mismatch apply, resize-on-snapshot-images,
  fsync rollout to commit); step 14d's sweep must leave that
  list accurate rather than re-litigate it.

### Administration

The instar-testdata `snapshot-baselines` branch (phases 10–13
baselines, tip f01b48b44) is still local-only and awaits
operator review and push; nothing in this phase depends on it,
but PLAN-snapshot is not operationally complete until it lands.
This is an operator task, not agent work.

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`docs/plans/order.yml`. The master plan's Execution table links
this file; step 14d flips the row to Landed and the
`docs/plans/index.md` master row to Complete.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with it — including your reading of the
four dispositions against the probes, the commit boundaries, and
which verifications you expect to hand back to the management
session.
