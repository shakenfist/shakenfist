# PLAN-check-repair phase 04: guest wiring — safe `leaks` tier (end-to-end)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly and ground your
answers in what the code actually does today; do not speculate
when you could read. The code this phase touches or mirrors:

- `src/operations/check/src/main.rs` — `_start` (config read +
  dispatch, ~line 47) and `check_qcow2` (line 2105). The
  detection walk builds a boolean reference bitmap `bmp` and, in
  the leak sweep (lines ~2761–2887), counts `result.leaks` for
  refcount entries with `rc > 0` whose cluster `!bmp.test(cidx)`.
  The repair pass added here reuses that `bmp`.
- `src/crates/check/src/qcow2.rs` — the phase-2 planner
  `reclaim_leaks_in_refblock(refblock, entries_in_block,
  refcount_bits, is_referenced)` this phase drives, and the
  phase-1 `RepairError`/`RepairCounters` surface.
- `src/operations/snapshot/src/main.rs` — the **mutating-guest
  write-back pattern** to mirror: `write_input_byte_range`
  (line ~293, a sector RMW helper over `write_input_sector` with
  a bounce buffer) and `fsync_input(0)` for durability; the
  scratch-region staging-buffer convention (lines ~119–187).
- `src/shared/src/lib.rs` — `CheckConfig::{FLAG_REPAIR,
  FLAG_REPAIR_ALL, should_repair, should_repair_all}` and the
  `CheckResult::{repaired_leaks, FLAG_REPAIR_INCOMPLETE}` fields
  from phase 1; the call-table `write_input_sector` /
  `fsync_input` / `read_input_sector` signatures; the
  `SCRATCH_MEM_*` layout constants.
- `src/vmm/src/main.rs` — `run_check` (line ~6597): the
  read-only device open (`BackingStore::open(path, true, None,
  false)` + `VirtioBlockDevice::new(.., true /*RO*/)`, ~6764)
  and the `CheckConfig` flag assembly (~6718). Contrast with
  `run_snapshot_mutating_guest` (~10830), which opens the input
  **read-write** (`BackingStore::open_rw_existing(path,
  Some(capacity_hint))` + `VirtioBlockDevice::new(.., false
  /*RW*/)`). `CheckArgs` is at ~line 2915; `CHECK_CONFIG_FLAG_*`
  mirror constants at ~line 83.

The parent master plan is
[PLAN-check-repair.md](/components/instar/plans/PLAN-check-repair/). Read its "Design
overview", **especially safety-model point 4** (the `corrupt`-bit
crash-safe ordering applies to the *lossy* tier — phase 5 — not
this one). This is phase 4 of eleven. It was split from the
original single "guest wiring" phase: **this phase ships only the
safe `leaks` tier, end-to-end and independently testable**; the
lossy `all` tier counting-walk is phase 5.

This is the **first phase that wires the `check` crate into a
binary** and the first that changes `check.bin`.

I prefer one commit per logical change, and at minimum one commit
per phase. The commit must build, pass tests, and have a clear
message.

## Situation

Phases 1–3 landed the ABI and the pure planners; nothing imports
the `check` crate yet, so `check.bin` is still byte-identical to
its pre-project self. This phase makes `instar check --repair`
actually reclaim leaked clusters in a qcow2 image, end to end.

### Why the leaks tier is the right first end-to-end slice

The safe (`leaks`) tier reuses the detection walk's existing
boolean `bmp` and only **frees clusters the completed whole-image
walk proved unreferenced** (`reclaim_leaks_in_refblock` zeroes
`rc > 0 && !is_referenced` entries). Those frees are monotonic
and individually crash-safe: a partially-applied leaks repair
leaves a consistent (if still-leaky) image. So this tier needs
**no `corrupt`-bit dance** — and setting the `corrupt` bit here
would actively *regress* an image with unrelated, unfixed
corruptions (re-validation would not come back clean, leaving the
bit set on a previously-openable image). Durability `fsync`
ordering still applies; the `corrupt`-bit guard is the lossy
tier's concern (phase 5).

The lossy tier (refcount recount, COPIED reconciliation,
`corrupt`-bit ordering, bounded-memory recount map) is
phase-3-sized on its own and lands in phase 5.

### What this phase builds on

- Phase 2's `reclaim_leaks_in_refblock` (pure, tested).
- Phase 1's `FLAG_REPAIR` / `should_repair()` and the
  `repaired_leaks` / `FLAG_REPAIR_INCOMPLETE` result fields.
- The snapshot mutating-guest write-back pattern
  (`write_input_byte_range` + `fsync_input`).
- The phase-1 device-rename (`check-op` package, `check` binary).

### What this phase produces

1. **Guest** (`src/operations/check/`):
   - `check-op`'s `Cargo.toml` gains `check = { path =
     "../../crates/check", default-features = false }`. `check.bin`
     grows (it imports the crate for the first time) — must stay
     under the 384 KiB cap (it is 41 KiB today, with vast
     headroom).
   - A new `repair_leaks_qcow2(...)` helper in
     `src/operations/check/src/main.rs`, called from
     `check_qcow2` **after** the leak sweep (so the complete
     `bmp` is in scope) when repair is requested. For each
     refcount-table entry → refblock, it stages the whole
     refblock cluster into a scratch buffer, calls
     `reclaim_leaks_in_refblock` with
     `is_referenced = |local_idx| bmp.test(rt_idx *
     entries_per_block + local_idx)`, and — if any entries were
     reclaimed — writes the refblock back with the snapshot
     `write_input_byte_range` pattern. One `fsync_input(0)` after
     all refblocks (no inter-block ordering constraint — every
     write is an independent safe free).
   - The detection path stays **byte-identical** — repair is a
     *separate pass* after detection, not woven into the leak
     sweep, so the read-only `check` output and walk are
     unchanged. (The minor cost is re-reading the refblocks; not
     the hot path.)
   - Post-repair result update: `result.repaired_leaks =
     reclaimed`; `result.leaks -= reclaimed`;
     `result.total_errors -= reclaimed`; clear `FLAG_HAS_LEAKS` if
     `result.leaks` reaches 0. So the `CheckResult` the guest
     sends reflects the **post-repair** state (matching qemu's
     re-check-after-repair), and an otherwise-clean image reports
     clean (exit 0 once phase 6 refines codes).
   - `check_qcow2` gains a `repair: bool` parameter (threaded
     from `_start` via `config.should_repair()`); the repair pass
     is guarded by it. Repair is **qcow2-only** (it lives inside
     `check_qcow2`); `--repair` on other formats is a reported
     no-op.
   - If `config.should_repair_all()` is set (not expected from
     this phase's host, which only sets `FLAG_REPAIR` — defensive):
     do the leaks tier and set `FLAG_REPAIR_INCOMPLETE` (the
     all-tier corrections are phase 5).

2. **Host** (`src/vmm/src/main.rs`) — minimal enablement pulled
   forward so the guest write path is exercisable:
   - `CheckArgs` gains `--repair` (bool for this phase; phase 6
     replaces it with `--repair[=leaks|all]`).
   - `run_check` opens the input **read-write when `--repair`**
     (`BackingStore::open_rw_existing` + `VirtioBlockDevice::new(..,
     false)`), else read-only exactly as today. The conditional
     mirrors `run_snapshot_mutating_guest`.
   - `check_flags |= CHECK_CONFIG_FLAG_REPAIR` when `--repair`.
     (Do **not** set `FLAG_REPAIR_ALL` — phase 6.)
   - Exit-code behaviour is **unchanged** (the 0/2/3 refinement is
     phase 6); a clean post-repair result still passes.

### What this phase does NOT do (deferred)

- The lossy `all` tier: refcount recount, both-directions
  correction, COPIED reconciliation, the `corrupt`-bit crash-safe
  ordering, the bounded computed-refcount map — **all phase 5**.
- `--repair=all` CLI surface, `--help` warning text, and the
  0/2/3 exit-code mapping — **phase 6**.
- Corrupt-fixture baselines and the round-trip integration test
  (corrupt image → `instar check --repair` → `qemu-img check`
  clean) — **phases 7–8**. This phase's verification is the
  build/test gates plus a **healthy-image no-op smoke** (`instar
  check --repair` on a clean qcow2 stays clean, `repaired_leaks ==
  0`, `qemu-img check` clean afterward), which proves the path
  does not corrupt a good image.

## Open questions

### 1. Separate repair pass, or fold reclamation into the leak sweep?

**Resolved: separate pass.** Folding would force the detection
leak sweep (which streams refblocks sector-by-sector) to stage
whole refblocks, changing the read-only walk and risking its
byte-identical output. A separate pass keeps detection untouched
and uses `reclaim_leaks_in_refblock` cleanly on whole-refblock
buffers. The extra refblock reads are acceptable (repair is not
the hot path).

### 2. `fsync` per refblock or once at the end?

**Resolved: once after all refblocks.** Every reclaimed entry is
an independent free of a proven-unreferenced cluster; there is no
ordering dependency between refblocks, so a single durability
barrier at the end suffices. (The lossy tier's ordered
write/fsync/`corrupt`-bit sequence is phase 5.)

### 3. Does the guest set the `corrupt` bit?

**Resolved: no** (see Situation / master-plan safety point 4).
Leak reclamation is crash-safe and `leaks` are not "corruption";
setting the bit would regress images with unrelated unfixed
errors. Phase 5's structural repairs set/clear it.

### 4. What does the post-repair `CheckResult` report?

**Resolved: the post-repair state.** Subtract reclaimed leaks
from `leaks` / `total_errors`, populate `repaired_leaks`, clear
`FLAG_HAS_LEAKS` if none remain. qemu re-checks after repair and
reports the result; instar mirrors that without a second full
walk (the reclamation count is authoritative — it equals the
detected-leak count for the leaks tier).

### 5. How is the input opened read-write?

**Resolved: conditionally, only under `--repair`.** Mirror
`run_snapshot_mutating_guest`: `BackingStore::open_rw_existing(path,
Some(capacity_hint))` + `VirtioBlockDevice::new(.., false)`.
Without `--repair` the open is byte-identical to today's
read-only path, so the reporting `check` is unaffected.

### 6. Memory for staging?

**Resolved: one refblock cluster at a time** (≤ 2 MiB, the qcow2
max cluster) plus an RMW bounce sector — trivially within the
~12.9 MiB scratch region. No whole-image map (that is phase 5's
bounded concern). Add a dedicated scratch-region buffer following
the snapshot layout convention with a compile-time
no-overlap assert.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | worktree | Host enablement in `src/vmm/src/main.rs`. (i) Add `#[arg(long)] repair: bool` to `CheckArgs` (~line 2915) with a doc comment noting it reclaims leaked clusters (qcow2 only) and that the full `--repair[=leaks|all]` surface lands in a later phase. (ii) In `run_check` (~6597), make the input device open conditional on `args.repair`: when true, open read-write mirroring `run_snapshot_mutating_guest` (~10830) — `BackingStore::open_rw_existing(Path::new(&args.input), Some(capacity_hint))` and `VirtioBlockDevice::new(.., false /*RW*/, ..)`; when false, the existing read-only open is byte-for-byte unchanged. Determine `capacity_hint` the same way the read-only path computes `input_size`. (iii) In the `check_flags` assembly (~6718), add `if args.repair { check_flags |= CHECK_CONFIG_FLAG_REPAIR; }` using the existing mirror constant (line 86). Do NOT set `FLAG_REPAIR_ALL`. (iv) Leave the exit-code mapping unchanged. Build `cargo build -p vmm`. Opus: the conditional device-open must not perturb the read-only path (the reporting `check` must stay identical) and the RW-open/ownership/capacity details mirror snapshot exactly. |
| 4b | high | opus | worktree | Guest leaks-tier repair in `src/operations/check/`. (i) `Cargo.toml`: add `check = { path = "../../crates/check", default-features = false }`. (ii) In `src/main.rs`, add a scratch buffer for one refblock cluster following the snapshot layout convention (a `const REPAIR_REFBLOCK_BUF: usize = ...` in the `SCRATCH_MEM` region sized to the max cluster, plus reuse/define an RMW bounce), with a `const _: () = assert!(.. <= shared::ALLOC_HEAP_BASE, ..)` no-overlap guard. (iii) Add a `write_input_byte_range`-style helper if one is not already shareable (mirror `snapshot/src/main.rs:293`), or factor minimally. (iv) Add `unsafe fn repair_leaks_qcow2(call_table, sector_size, bmp: &Bitmap, refcount_table_offset, refcount_table_clusters, cluster_size, refcount_bits, entries_per_block, input_capacity, actual_size) -> u64` (returns reclaimed count): iterate the refcount table exactly as the leak sweep does (mirror lines ~2792–2820 for reading each `refblock_off`), and for each non-zero `refblock_off` stage the whole refblock cluster into the scratch buffer, call `check::qcow2::reclaim_leaks_in_refblock(refblock, entries_per_block, refcount_bits, |local_idx| bmp.test(rt_idx * entries_per_block + local_idx))`, and if it returns > 0 write the cluster back with the byte-range helper; accumulate the count; after the loop `fsync_input(0)` once if anything was written. Map any `RepairError` to a debug print + abort the repair (leave the image as-is) — do NOT panic. (v) Thread a `repair: bool` param into `check_qcow2` from `_start` (`config.should_repair()`); after the leak sweep, when `repair`, call `repair_leaks_qcow2`, then update `result`: `repaired_leaks = reclaimed`, `leaks -= reclaimed`, `total_errors -= reclaimed`, clear `FLAG_HAS_LEAKS` if `leaks == 0`. If `config.should_repair_all()`, also set `FLAG_REPAIR_INCOMPLETE` (all-tier deferred). Repair stays inside the qcow2 path (qcow2-only). Confirm the detection path is otherwise untouched. Opus: the cluster-index ↔ refblock-entry mapping for the `is_referenced` closure must exactly match the detector's `cidx` math (lines ~2868–2875), refblock self-marks must keep refblocks from being freed, and the write-back must use the correct `refblock_off` byte offset and `cluster_size` length. |
| 4c | medium | sonnet | worktree | Verify and commit. From the worktree `src/` with the cargo target dir redirected to an owned path: `cargo build -p vmm -p check-op`, `make test-rust` (no new unit tests expected — the planner is already tested; confirm nothing regressed), `make instar` (now `check.bin` **changes** — it imports the crate), `make check-binary-sizes` (**`check.bin` must stay < 384 KiB**; report its new size), `make lint`, `pre-commit run --all-files` (watch the rustfmt comment-alignment trap). Then a **healthy-image no-op smoke**: create a clean qcow2 with `qemu-img create -f qcow2 /tmp/clean.qcow2 64M`, write a little data via `qemu-io` if available, run the freshly-built `instar check --repair /tmp/clean.qcow2`, and assert it exits success, reports `repaired_leaks == 0` (or no repair output), and that `qemu-img check /tmp/clean.qcow2` is clean afterward (the image must not be corrupted by a no-op repair). If `qemu-img`/`qemu-io` are unavailable in the worktree env, say so and fall back to running `instar check --repair` on an existing clean fixture under `tests/`/`testdata` and `instar check` (no repair) to confirm identical reporting. Stage and present ONE commit (4a+4b) with the `~/.claude/CLAUDE.md` convention. The message explains: this wires the safe `leaks` tier end-to-end — guest `repair_leaks_qcow2` driving the phase-2 planner with the detector's bitmap, post-repair `CheckResult` update, minimal host `--repair` flag + read-write open; no `corrupt`-bit dance (leak reclamation is crash-safe); the lossy `all` tier and CLI/exit-code polish are phases 5–6; `check.bin` grows as it first imports the `check` crate but stays within budget. |

## Agent guidance

### Execution model

All implementation is by sub-agents in the `check-repair`
worktree (itself the isolation — do not nest a throwaway
worktree). The management session reads the actual diff, runs the
gates, and commits. 4a and 4b touch disjoint files (vmm vs the
check op) and could be reviewed independently, but land as one
commit (the phase).

### Model and effort notes

- **4a and 4b are high-effort opus.** 4a must not perturb the
  read-only `check` path while adding the RW open; 4b is the
  first real write-to-disk repair path — the cluster-index
  mapping, refblock self-mark handling, and write-back offsets
  are the traps, and a wrong free corrupts a live image.
- **4c is medium sonnet**: scripted verify + a careful no-op
  smoke that must confirm a good image survives `--repair`
  untouched.

### Management session review checklist

- [ ] Read the diff; the **read-only `check` path is byte-for-byte
      unchanged** (device open and walk) when `--repair` is absent.
- [ ] `repair_leaks_qcow2` runs only when `repair` is set, only on
      qcow2, only after the full `bmp` is built.
- [ ] The `is_referenced` closure's `local_idx → cidx` mapping
      matches the detector (lines ~2868–2875); refblock clusters
      are never freed (self-marked referenced).
- [ ] Write-back uses the correct `refblock_off` and `cluster_size`
      length; one `fsync_input(0)` after the loop; nothing written
      when nothing reclaimed.
- [ ] No `corrupt`-bit write anywhere in this phase.
- [ ] Post-repair `CheckResult` reflects reclaimed leaks
      (`repaired_leaks`, decremented `leaks`/`total_errors`,
      `FLAG_HAS_LEAKS` cleared when 0).
- [ ] `RepairError` is handled (debug print + abort repair), never
      `unwrap`/panic.
- [ ] `make check-binary-sizes`: `check.bin` < 384 KiB (report the
      delta from 41 KiB).
- [ ] Healthy-image `--repair` is a no-op: image stays `qemu-img
      check`-clean, `repaired_leaks == 0`.
- [ ] `make test-rust`, `make lint`, `pre-commit` clean.

## Administration and logistics

### Success criteria

* `instar check --repair <leaky.qcow2>` reclaims leaked clusters
  in-guest and reports the post-repair state; on a healthy image
  it is a no-op that leaves the image `qemu-img check`-clean.
* The read-only `instar check` path (no `--repair`) is unchanged
  in output, exit code, and device handling.
* `make instar`, `make test-rust`, `make check-binary-sizes`
  (`check.bin` < 384 KiB), `make lint`, `pre-commit run
  --all-files` all pass.
* Lands in one commit on the `check-repair` branch.

### Future work created by this phase

- The lossy `all` tier (recount + correction + COPIED + the
  `corrupt`-bit ordering) — phase 5.
- `--repair[=leaks|all]`, `--help` warning, 0/2/3 exit codes —
  phase 6.
- Corrupt-fixture round-trip integration — phase 8. (This phase
  only smoke-tests the healthy no-op case, because constructing a
  controlled leaked-cluster fixture belongs with the baselines/
  integration tooling.)

### Bugs fixed during this work

- **Snapshot-blind-spot corruption guard (follow-up commit).**
  Found while planning phase 5: `check_qcow2` does not walk
  snapshot L1/L2 tables (it only reports `nb_snapshots`), so the
  detection `bmp` omits clusters referenced only by an internal
  snapshot. On a diverged-snapshot image those clusters have
  `refcount > 0` but test `!bmp`, so the leaks-tier reclamation
  would free them and corrupt the snapshot. (Read-only `check`
  already over-reports them as leaks — a pre-existing detection
  false-positive; repair weaponised it.) Fix: the guest refuses
  leak repair when `hdr.nb_snapshots > 0` (reclaim nothing, set
  `FLAG_REPAIR_INCOMPLETE`). Verified on a diverged-snapshot
  fixture (instar reports 67 false leaks; `--repair` leaves the
  image byte-identical and `qemu-img check` clean with the
  snapshot intact). Snapshot-aware recount is future work (it
  needs the snapshot-table walk phase 5 also defers).

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`order.yml`. The master plan's phase-4 row is updated to "Landed"
with a pointer here once the commit is in. (The master plan's
Execution table and `index.md` phase list were already renumbered
to 11 phases when this plan was written.)

### Back brief

Before executing any step, back brief the operator on your
understanding of the phase and how it aligns with the master
plan's safety model — especially that this tier frees only
proven-unreferenced clusters, takes no `corrupt`-bit action, and
must leave the read-only `check` path and a healthy image
untouched.
