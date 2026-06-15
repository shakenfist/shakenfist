# PLAN-check-repair phase 01: ABI extension + planner-crate scaffold

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the
relevant source files and ground your answers in what the code
actually does today; do not speculate when you could read. The
specific files this phase touches are:

- `src/shared/src/lib.rs` — the `CheckConfig` (line 1714) and
  `CheckResult` (line 1781) wire structs and their `impl`
  blocks (1722, 1828). This is the source of truth for the
  check operation's ABI.
- `src/vmm/src/main.rs` — the VMM-side mirror constants
  `CHECK_CONFIG_FLAG_*` (lines 83–93) and `run_check`
  (line 6597). Note `CHECK_CONFIG_FLAG_VERBOSE = 1 << 31`.
- `src/crates/snapshot/Cargo.toml` and
  `src/crates/snapshot/src/{lib.rs,qcow2.rs}` — the template
  for the new planner crate (one mutating-operation crate per
  operation; `#![no_std]`; pure functions over staged slices).
- `src/operations/snapshot/Cargo.toml` — the precedent for the
  package-rename + `[[bin]]` pattern (package `snapshot-op`
  produces the `snapshot` binary via `[[bin]] name = "snapshot"`).
- `src/operations/check/Cargo.toml` — the operation being
  renamed.
- `Makefile` line 499 — the `--exclude check` entry in the
  `cargo test --workspace` invocation.

The parent master plan is
[PLAN-check-repair.md](/components/instar/plans/PLAN-check-repair/); read its "Design
overview: the repair safety model" section, which this phase
encodes into the ABI. This is phase 1 of ten. It is a **pure
plumbing/scaffold phase**: no repair logic, no behavioural
change, no guest or host CLI surface yet. Phases 2–3 add the
planners, phase 4 wires the guest, phase 5 adds the host CLI.

I prefer one commit per logical change, and at minimum one
commit per phase. The commit must build, pass tests, and have a
clear message.

## Situation

`instar check` is a complete *reporting* tool today. The repair
capability is a reserved-but-dead placeholder:
`CheckConfig::FLAG_REPAIR = 1 << 0` exists (commented "future
feature") with a `should_repair()` accessor, the VMM mirrors it
as `CHECK_CONFIG_FLAG_REPAIR` (`#[allow(dead_code)]`, never
read), there is no `--repair` CLI flag, and no repair logic
anywhere. The `CheckResult` wire struct already tiers findings
into `corruptions` / `leaks` / `refcount_errors` /
`chain_errors` / `subcluster_errors` counters — exactly the
buckets repair acts on.

The master plan settled the design: QCOW2-only v1, a safe
`leaks` tier and a lossy `all` tier mirroring `qemu-img check
-r`, dry-run-by-default (plain `check` is the preview),
in-place mutation, crash-safe write ordering guarded by the
`corrupt` header bit, and refuse-rather-than-guess. The repair
mutators reuse `src/crates/snapshot/`'s hardened refcount/L1/L2
primitives.

This phase lays the foundation those later phases build on: the
ABI bits and counters that carry repair intent and results
across the wire, and the new `src/crates/check/` planner crate
(empty scaffold + stable type surface). It also resolves the
crate-name collision the operator chose to handle by convention.

### The crate-name collision and its resolution (decided)

Operation packages follow the `<x>-op` convention paired with an
`<x>` planner crate (`snapshot-op`/`snapshot`,
`commit-op`/`commit`, `rebase-op`/`rebase`, `resize-op`/`resize`).
The `check` operation is the lone exception — package `check`,
no paired crate — because no `check` crate ever existed to
collide with. Creating `src/crates/check/` forces the issue.

**Decision (operator, this session): adopt the convention.**
Rename the operation package `check` → `check-op` and add a
`[[bin]] name = "check"` section so the produced binary stays
`check` (hence `check.bin` is byte-name-identical). This mirrors
`snapshot-op` exactly. The new planner crate takes the clean
name `check`.

The rename's blast radius was mapped and is contained:
- `src/vmm/src/main.rs:6609` `get_binary_path("check.bin")` —
  **unaffected** (binary still named `check`).
- `tools/test-package-install.sh:92` `/usr/lib/instar/check.bin`
  — **unaffected**.
- `src/vmm/src/chain.rs:1237` (comment naming `check.bin`) —
  **unaffected**.
- `Makefile:499` `--exclude check` — **must become**
  `--exclude check-op` (the workspace-test exclude list names
  operation *packages*; the renamed package is `check-op`). The
  new `check` *crate* is a host-testable `no_std` lib like
  `snapshot` and is intentionally **not** excluded, so its unit
  tests run under `--workspace`.
- `scripts/check-rust.sh` `--exclude check` (TWO sites: the
  clippy `--fix` invocation ~line 55 and the clippy check
  invocation ~line 71) — **must become** `--exclude check-op`.
  *(Found during execution, not in the original blast-radius
  sweep — see "Bugs fixed during this work". Without it, clippy
  tries to build the `no_std`/`panic=abort` operation binary and
  fails with "unwinding panics are not supported without std".)*
- `src/Cargo.toml` `members` references operations by path
  (`operations/check`), not package name — **unaffected** by the
  rename; this phase only *adds* `crates/check` to it.

### What this phase builds on

- **`CheckConfig` / `CheckResult`** (`src/shared/src/lib.rs`):
  extended here. Both are `#[repr(C)]`; the VMM writes
  `CheckConfig` to `OPERATION_CONFIG_ADDR` as `magic` then
  `flags` (`run_check`, lines 6733–6734), and the guest returns
  `CheckResult` via the `send_check_result` call-table pointer.
- **`src/crates/snapshot/`**: the new crate depends on it for
  the refcount/L1/L2 primitives phases 2–3 will call
  (`set_refcount_in_block`, `for_each_cluster_in_l1`,
  `update_copied_flags_for_l1`, `SnapshotError`). Phase 1 only
  adds the dependency and a `From<SnapshotError>` impl; it calls
  nothing yet.
- **The `[[bin]] name` pattern** from `snapshot-op`.

### What this phase produces

1. `CheckConfig` gains `FLAG_REPAIR_ALL: u32 = 1 << 4`
   (bit 4 is free: bits 0–3 are REPAIR/QUIET/UNSAFE_QUIRKS/CHAIN
   in shared, and the VMM mirror's only other bit is VERBOSE at
   1 << 31) and a `should_repair_all()` accessor. Tiering
   semantics, documented on the flags: `FLAG_REPAIR` alone =
   **leaks** tier; `FLAG_REPAIR | FLAG_REPAIR_ALL` = **all**
   tier. `FLAG_REPAIR_ALL` without `FLAG_REPAIR` is meaningless
   and treated as no-repair.
2. `CheckResult` gains three repair-outcome counters appended
   after `flags` (preserving `#[repr(C)]` field order):
   `repaired_leaks: u32`, `repaired_refcounts: u32`,
   `repaired_corruptions: u32`; and a status flag
   `FLAG_REPAIR_INCOMPLETE: u32 = 1 << 8` (bits 0–7 are taken by
   the existing FLAG_VALID..FLAG_CHAIN_ERRORS) set when repair
   ran but could not fully clean the image (refuse-don't-guess
   hit, or the snapshot allocator's `RefcountExhausted`). `new()`
   initialises the three counters to 0.
3. New crate `src/crates/check/` (package `check`):
   - `Cargo.toml` from the `snapshot` template — deps `shared`,
     `qcow2`, `snapshot`, all `default-features = false`; dev-deps
     mirror.
   - `src/lib.rs`: `#![no_std]`; `pub mod qcow2;`; the stable
     public type surface for phases 2–3:
     - `RepairTier` enum `{ Leaks, All }`.
     - `RepairError` enum (`RefcountExhausted`,
       `AmbiguousCorruption`, `Unsupported`, `MisalignedAccess`,
       `ParseFailed`) with `From<snapshot::qcow2::SnapshotError>`
       so the planners can `?`-propagate snapshot-primitive
       errors.
     - `RepairCounters` struct `{ leaks: u32, refcounts: u32,
       corruptions: u32, incomplete: bool }` — the planner return
       shape the guest will fold into `CheckResult`.
   - `src/qcow2.rs`: empty stub with the `use` lines it will need
     (`use snapshot::qcow2::{...};`, `use qcow2::{...};`) and a
     `#[cfg(test)] mod tests` skeleton. No planner functions yet.
   - `"crates/check"` added to `src/Cargo.toml` `members`.
4. Operation package `check` renamed to `check-op` with
   `[[bin]] name = "check"`; `Makefile:499` exclude updated to
   `check-op`.

### What this phase does NOT change

- No `--repair` CLI flag (phase 5).
- No change to `run_check` device opening (still read-only;
  the writable-open lands with the host CLI — see open
  question 3).
- No repair logic; the `check` crate compiles and tests but
  contains no planners.
- No change to the `check` guest binary's behaviour. The binary
  is rebuilt under the new package name but its code is
  untouched, so `check.bin` should be byte-identical (verify in
  step 1d). Every other operation binary is byte-identical.
- The reporting path of `instar check` is byte-identical in
  output and exit code.

## Open questions

### 1. Append `CheckResult` counters, or pack into existing fields?

**Resolved: append three `u32` counters after `flags`.** The
struct is `#[repr(C)]` and both ends compile from `shared`, so
appending fields keeps guest and host in lockstep. Packing
repair counts into the existing error counters would conflate
"found" with "fixed" and break the reporting path's invariants.

### 2. One repair flag with a tier value, or two flag bits?

**Resolved: two bits (`FLAG_REPAIR` + `FLAG_REPAIR_ALL`).** The
wire `CheckConfig` carries only `magic` + `flags`; there is no
room for an enum value without a layout change, and two bits map
cleanly onto `qemu-img -r leaks` / `-r all`. `should_repair()`
stays "any repair requested"; `should_repair_all()` adds the
lossy tier.

### 3. Does phase 1 make the input device writable?

**Resolved: no — deferred, with a documented cross-phase
dependency.** `run_check` opens the input read-only
(`BackingStore::open(.., true, ..)`, `VirtioBlockDevice::new(..,
true /*read-only*/)`). Repair needs it writable, but only when
`--repair` is set — and the flag is phase 5. Phase 1 keeps the
read-only default untouched (preserving the no-behavioural-change
property). **Dependency to flag for the master plan:** phase 4
(guest wiring) cannot be exercised end-to-end until the
writable-open exists, so the writable-open (host-side, gated on
`FLAG_REPAIR`) should be pulled into phase 4 or done as the
first step of phase 5 *before* phase 4's integration testing —
the snapshot family used the same "pull the minimal host
dispatch forward" technique. Recorded here so the phase 4/5
planner accounts for it.

### 4. Does `RepairError` need to map 1:1 to wire codes?

**Resolved: no.** Unlike `SnapshotResult`, `CheckResult` reports
via counters + flags, not `ERROR_*` codes. `RepairError` is an
internal planner type; the guest (phase 4) translates a hard
failure into `send_error` and a partial repair into
`FLAG_REPAIR_INCOMPLETE`. The only mapping phase 1 ships is
`From<SnapshotError>` so the planners can propagate primitive
errors ergonomically.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | opus | worktree | Extend the check ABI in `src/shared/src/lib.rs`. (i) In `impl CheckConfig` (line 1722): add `pub const FLAG_REPAIR_ALL: u32 = 1 << 4;` with a doc comment explaining the tiering (`FLAG_REPAIR` alone = leaks tier; `FLAG_REPAIR | FLAG_REPAIR_ALL` = all tier; `FLAG_REPAIR_ALL` without `FLAG_REPAIR` = no repair). Add `pub fn should_repair_all(&self) -> bool { (self.flags & Self::FLAG_REPAIR_ALL) != 0 && self.should_repair() }`. Do NOT change the `CheckConfig` struct fields (still `magic` + `flags`). (ii) In `CheckResult` (struct at line 1781): append three fields after `flags`: `pub repaired_leaks: u32,` `pub repaired_refcounts: u32,` `pub repaired_corruptions: u32,`. Keep `#[repr(C)]` field order; these go last. (iii) In `impl CheckResult` (line 1828): add `pub const FLAG_REPAIR_INCOMPLETE: u32 = 1 << 8;` (bits 0–7 are taken). Update `new()` (line 1857) to init the three new counters to 0. Add a `pub fn repair_incomplete(&self) -> bool` accessor mirroring the existing flag accessors. (iv) Add ~6 unit tests in the shared crate's existing test module: `FLAG_REPAIR_ALL` bit value; `should_repair_all` true only when both bits set; `should_repair_all` false when only ALL set; `repair_incomplete` accessor; `new()` zeroes the counters; the three counters round-trip through a struct copy. Build `cargo build -p shared` and `cargo test -p shared`. **Do not touch the VMM mirror constants** (that's phase 5 when the flag is wired). Opus because the bit-allocation and repr(C)-append reasoning must not collide with existing bits and must keep the wire layout consistent across guest/host. |
| 1b | medium | sonnet | worktree | Rename the check operation package, preserving the binary name. (i) In `src/operations/check/Cargo.toml`: change `name = "check"` to `name = "check-op"`. Add a `[[bin]]` section immediately after `[package]`: `[[bin]]\nname = "check"\npath = "src/main.rs"` — exactly mirroring `src/operations/snapshot/Cargo.toml`. (ii) In `Makefile`, the `cargo test --release --workspace` invocation (around line 499): change `--exclude check` to `--exclude check-op`. (iii) Do NOT add a dependency on the new `check` crate yet (phases 2–4). Verify nothing else references the operation by package name: `grep -rn '\bcheck\b' src/Cargo.toml` (members are path-based, should be untouched) and confirm `src/vmm/src/main.rs:6609`, `tools/test-package-install.sh`, `src/vmm/src/chain.rs` reference `check.bin` (the artifact, unchanged). Build `cargo build -p check-op --release` and confirm a `check` binary is produced. |
| 1c | medium | opus | worktree | Create the `src/crates/check/` planner crate scaffold. (i) `Cargo.toml`: copy `src/crates/snapshot/Cargo.toml`, set `name = "check"`, `description = "Pure planner crate for qcow2 check --repair (leak reclamation + refcount/COPIED rebuild, no I/O)"`. Dependencies: `shared`, `qcow2 (default-features = false)`, `snapshot (default-features = false)` — all path deps; dev-deps mirror. (ii) `src/lib.rs`: `#![no_std]`; `pub mod qcow2;`; define the stable type surface: `RepairTier { Leaks, All }`; `RepairError { RefcountExhausted, AmbiguousCorruption, Unsupported, MisalignedAccess, ParseFailed }` with `impl From<snapshot::qcow2::SnapshotError> for RepairError` (map each SnapshotError variant to the closest RepairError — read `snapshot::qcow2::SnapshotError`'s variants first; `RefcountExhausted`→`RefcountExhausted`, `RefcountOverflow`/`MisalignedAccess`→`MisalignedAccess`, `Unsupported`→`Unsupported`, parse-ish→`ParseFailed`); `RepairCounters { leaks: u32, refcounts: u32, corruptions: u32, incomplete: bool }` deriving `Default`, `Clone`, `Copy`. Document each type with the role it plays for phases 2–3. (iii) `src/qcow2.rs`: stub with the `use` lines the planners will need (`use snapshot::qcow2::{set_refcount_in_block, read_refcount_in_block, for_each_cluster_in_l1, update_copied_flags_for_l1};` — adjust to the actual exported names after reading the snapshot crate; gate behind `#[allow(unused_imports)]` if needed to keep the stub clean) and an empty `#[cfg(test)] mod tests {}`. (iv) Add `"crates/check"` to the `members` array in `src/Cargo.toml`. Build `cargo build -p check` and `cargo test -p check` (zero tests, must compile clean). Opus because this fixes the type surface every later phase consumes; getting `RepairError`/`RepairCounters` and the `From<SnapshotError>` mapping right now avoids churn in phases 2–3. |
| 1d | low | sonnet | worktree | Full verification + single commit. Run, from the worktree `src/` (redirect cargo target-dir to an owned path to avoid the known worktree target-ownership issue): `cargo build -p shared -p check -p check-op`, `make test-rust` (the new shared tests + the zero-test `check` crate run under `--workspace`; confirm `check-op` is now excluded by name and `check` crate is included), `make instar`, `make check-binary-sizes`, `make lint`, `pre-commit run --all-files`. **Critical assertion:** every operation binary, *including* `check.bin`, is byte-identical to its pre-phase size/content (this phase changes no operation code — only the package name and a `[[bin]]` stanza, which must not alter the emitted binary). If `check.bin` differs, investigate before committing. Stage and present ONE commit covering 1a–1c with the `~/.claude/CLAUDE.md` message convention (≤50-char first line ending in `.`, 75-char body wrap, Prompt paragraph summarising the phase, Signed-off-by, Assisted-By + Co-Authored-By with model/context/effort). The message explains: this lands the check-repair ABI (two repair flag bits + three result counters + an incomplete flag), scaffolds the `src/crates/check/` planner crate with the stable `RepairTier`/`RepairError`/`RepairCounters` surface, renames the op package to `check-op` (binary still `check`) to free the `check` crate name per convention, and changes no behaviour — no operation binary imports the crate and the reporting path is byte-identical. |

## Agent guidance

### Execution model

All implementation is by sub-agents in worktrees; the management
session reviews the actual changed files (not summaries),
confirms no unrelated files moved, runs the named build/test
commands, and then commits. All four steps use
`isolation: "worktree"` for consistency with the rest of the
plan family and because the package rename touches build glue.

### Model and effort notes

- Steps **1a and 1c are opus**: 1a allocates wire bits that must
  not collide and appends to a `#[repr(C)]` struct shared across
  the guest/host boundary; 1c fixes the planner type surface and
  the `From<SnapshotError>` mapping that phases 2–3 inherit.
  Both are cheap to get wrong and expensive to churn later.
- Steps **1b and 1d are sonnet**: a mechanical rename following
  the `snapshot-op` precedent, and a scripted verify-and-commit.

### Management session review checklist

- [ ] Read every changed file; no unrelated files modified.
- [ ] `FLAG_REPAIR_ALL = 1 << 4` collides with nothing in
      shared *or* the VMM mirror (VERBOSE is 1 << 31).
- [ ] `CheckResult` new fields are appended last; `new()`
      initialises them; `#[repr(C)]` intact.
- [ ] `check-op` package produces a `check` binary via
      `[[bin]] name = "check"`; `check.bin` references elsewhere
      still resolve.
- [ ] `Makefile` excludes `check-op` (not `check`); the `check`
      crate's tests run under `--workspace`.
- [ ] `src/crates/check/` compiles and tests clean (zero tests
      is acceptable for the scaffold); `From<SnapshotError>` is
      exhaustive over the actual `SnapshotError` variants.
- [ ] `make instar` + `make check-binary-sizes`: **`check.bin`
      and every operation binary byte-identical** to pre-phase.
- [ ] `make lint` and `pre-commit run --all-files` clean.
- [ ] The `check` crate is safe Rust top-to-bottom (no
      `unsafe`).

## Administration and logistics

### Success criteria

Phase 1 is complete when:

* `src/shared/src/lib.rs` carries `FLAG_REPAIR_ALL`,
  `should_repair_all`, the three `CheckResult` repair counters,
  and `FLAG_REPAIR_INCOMPLETE`, with passing unit tests.
* `src/crates/check/` exists, compiles, tests clean, and exposes
  the `RepairTier` / `RepairError` / `RepairCounters` surface
  with `From<SnapshotError>`.
* The operation package is `check-op` producing a `check`
  binary; `Makefile` excludes `check-op`.
* `make instar`, `make test-rust`, `make check-binary-sizes`,
  `make lint`, `pre-commit run --all-files` all pass.
* Every operation binary, including `check.bin`, is
  byte-identical to its pre-phase build; the reporting path is
  unchanged.
* All of the above lands in one commit on the `check-repair`
  branch.

### Future work created by this phase

- **Writable input device.** The host must open the input
  read-write when `FLAG_REPAIR` is set; deferred to phase 4/5
  per open question 3 and flagged as a cross-phase dependency.
- **VMM mirror constant.** `CHECK_CONFIG_FLAG_REPAIR_ALL` will
  be added VMM-side in phase 5 when the `--repair` flag is
  wired; phase 1 deliberately leaves the VMM untouched.

### Bugs fixed during this work

- **`scripts/check-rust.sh` clippy exclude lists.** The original
  blast-radius sweep found only the `Makefile` `cargo test`
  exclude, but `check-rust.sh` carries the operation-binary
  exclude list in two more places (the clippy `--fix` and clippy
  check invocations). Leaving them as `--exclude check` made
  clippy attempt to build the renamed `check-op` `no_std` /
  `panic = "abort"` binary in the dev profile (where the
  per-package `panic = "abort"` is ignored — "profiles for the
  non root package will be ignored"), failing with "unwinding
  panics are not supported without std". Caught by
  `pre-commit run --all-files`; fixed by renaming both to
  `--exclude check-op`. Lesson for future renames: grep the
  whole repo for the package name in *every* exclude list, not
  just the Makefile.

### Documentation index maintenance

This is a phase plan, not a master plan: it is **not** added to
`order.yml`. The master plan's Execution table row for phase 1
should be updated to "Landed" with a pointer to this file once
the commit is in.

### Back brief

Before executing any step, back brief the operator on your
understanding of the phase and how the work aligns with this
plan and the master plan's safety model.
