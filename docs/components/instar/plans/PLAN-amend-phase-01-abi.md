# PLAN-amend phase 01: shared ABI

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the `*Config` /
`*Result` family in `src/shared/src/lib.rs`, the `CallTable`
struct and its append-only convention, the `GuestMessage`
protobuf oneof in `crates/guest-protocol/proto/guest.proto`, the
core-side call-table population in `src/core/src/main.rs` and
result senders in `src/core/src/serial.rs`, the host-side
protobuf decode arms and `*RunResult` holders in
`src/vmm/src/main.rs`), and ground your answers in what the code
actually does today. Do not speculate about the codebase when you
could read it instead. Where a question touches on the qcow2
header model (v2 vs v3, the `compatible`/`incompatible`/
`autoclear` feature words, `refcount_order`, header extensions),
research the qcow2 spec / qemu source as needed. Flag any
uncertainty explicitly rather than guessing.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-amend-phase-NN-<descriptive>.md`. The master plan is
[PLAN-amend.md](/components/instar/plans/PLAN-amend/). This phase is the first of nine.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

This phase establishes the shared ABI surface that `amend` will
use. No planner crate, guest binary, or host CLI entry point is
added yet — phases 2–9 build on top of what phase 1 lands. The
phase is intentionally small and mostly mechanical so it can ship
in a single PR and unblock phase 2 (the qcow2 amend planner) and
phase 3 (the guest op) to be developed against a frozen ABI.

The pattern is well-established by every prior subcommand's ABI
phase — most recently `PLAN-rebase-commit-phase-01-abi.md` (which
added `RebaseConfig`/`RebaseResult`/`CommitConfig`/`CommitResult`,
the `write_input_sector` call-table pointer, and the two new proto
messages). amend is *simpler* than rebase/commit: it needs no
backing-path buffer, no input chain, and no new device-I/O
primitive. The mutation is a single in-place header-cluster
rewrite, applied through the existing `read_output_sector` /
`write_output_sector` primitives that resize already uses. The
only new call-table pointer is the result sender.

Grounding (verified against the current tree on the `amend`
branch):

- `*Config`/`*Result` structs live in `src/shared/src/lib.rs`,
  are `#[repr(C)] #[derive(Clone, Copy)]`, carry a 4-byte ASCII
  magic with a `MAGIC` associated const, expose `is_valid(&self)`
  (magic check), use an append-only `ERROR_*` enum, and end with
  a `_reserved: [u8; N]` tail. Reference templates:
  `RebaseConfig` (`src/shared/src/lib.rs:3259`), `RebaseResult`
  (`:3339`), `ResizeConfig` (`:3075`), `ResizeResult` (`:3194`).
- The per-op config is written by the host to
  `OPERATION_CONFIG_ADDR = 0x00081000`
  (`src/shared/src/lib.rs:328`-region) and read by the guest;
  amend reuses this slot — **no new address constant**.
- `CallTable` (`src/shared/src/lib.rs:693`) is append-only. Its
  last field today is `fsync_input` (`:963`); the struct closes at
  `:964`. `CallTable::VERSION` is currently **17**
  (`src/shared/src/lib.rs:1289`) and the guest's
  `validate_call_table!` macro rejects a mismatch
  (`src/shared/src/lib.rs:3780`). Adding a pointer bumps it to 18.
- Core populates every call-table slot in one struct literal in
  `src/core/src/main.rs` (the `send_rebase_result:
  ct_send_rebase_result` line at `:294` and neighbours). The
  `ct_send_*` thunks live near `src/core/src/main.rs:695`
  (`ct_send_rebase_result`). The actual senders are in
  `src/core/src/serial.rs` (`send_rebase_result` at `:569`,
  `send_commit_result` at `:587`), each building a `GuestMessage`
  via a `guest_protocol::*_result_message` helper
  (`rebase_result_message` at `crates/guest-protocol/src/lib.rs:670`).
  **Because core's struct literal names every field, core.bin will
  not compile until `ct_send_amend_result` exists and is
  registered — phase 1 must wire the core side, not just declare
  the pointer.**
- The host (vmm) does not populate the call table; it decodes the
  protobuf result off the serial channel. The decode arm for
  rebase is `Some(guest_::GuestMessage_::Payload::RebaseResult(r))`
  at `src/vmm/src/main.rs:917`, and the host-side holder
  `RebaseRunResult` is at `src/vmm/src/main.rs:2657`.
- `crates/guest-protocol/proto/guest.proto`: the `oneof payload`
  arms run through `snapshot_result = 18`;
  `RebaseResultMessage` is the message template. The next free
  field number is **19**.
- `ImageFormat` (`src/shared/src/lib.rs:1419`, `Qcow2 = 2`) with
  `from_u32` (`:1447`) and `name()` (`:1466`) maps the format code
  to `"qcow2"` for the result message.
- qcow2 feature/version facts the ABI must carry (all already
  parsed by `src/crates/qcow2/src/lib.rs`): `version` (2 or 3),
  `compatible_features` (bit 0 = `COMPAT_LAZY_REFCOUNTS`),
  `incompatible_features` (`DIRTY`/`CORRUPT`/`EXTERNAL_DATA`/
  `COMPRESSION`/`EXTENDED_L2`), `refcount_bits`, `cluster_size`,
  `virtual_size`.

## Mission and problem statement

After phase 1 lands:

1. `src/shared/src/lib.rs` defines two new structs, `AmendConfig`
   and `AmendResult`, each with a unique magic, an `is_valid()`
   helper, an append-only `ERROR_*`/`ACTION_*` constant set, the
   per-format fields amend needs at runtime, and a `_reserved`
   tail. Field layouts in Open questions 3–4.

2. `CallTable` gains exactly one new function-pointer field at the
   very end of the struct: `send_amend_result: unsafe extern "C"
   fn(*const AmendResult)`. `CallTable::VERSION` bumps 17 → 18. No
   other call-table pointer is added — amend reuses
   `read_output_sector` / `write_output_sector` (the same
   single-device read+write idiom resize uses).

3. `crates/guest-protocol/proto/guest.proto` gains one new message
   type `AmendResultMessage` and one new `oneof payload` arm
   `AmendResultMessage amend_result = 19;`.

4. Core-side wiring makes `core.bin` build and actually send the
   message: `guest_protocol::amend_result_message(...)`
   (`crates/guest-protocol/src/lib.rs`), `serial::send_amend_result`
   (`src/core/src/serial.rs`), `ct_send_amend_result`
   (`src/core/src/main.rs`), and its registration in the
   call-table struct literal.

5. Host-side wiring makes `instar` build and decode the message:
   an `AmendRunResult` holder struct in `src/vmm/src/main.rs`
   (mirroring `RebaseRunResult`) and a new
   `Payload::AmendResult(r)` decode arm that stashes the harvested
   fields. The arm need not be reachable from any subcommand yet —
   the `Commands` enum is **not** extended in this phase (that is
   phase 4). `instar amend` still prints "unrecognized
   subcommand".

6. Unit tests in `src/shared/src/lib.rs`'s existing `#[cfg(test)]
   mod tests` block verify the size, magic, `is_valid()`, and
   alignment of the two new structs, mirroring the existing
   `resize_config_*` / `rebase_*` tests.

7. `make instar` builds, `make lint` is clean, `make test-rust`
   passes, `pre-commit run --all-files` passes, and `make
   check-binary-sizes` is unchanged (no guest binary code lands
   in this phase; verify `core.bin` still fits after the
   `CallTable` grows by one pointer — it is a pointer table in
   RAM, not in the 64 KiB core text, so this should be a no-op,
   but check).

Nothing in phase 1 changes user-visible behaviour.

## Open questions

These should be confirmed during planning review before step 1a
runs. Each is a real choice, not a placeholder.

### 1. Magic values

Working choices (4-byte ASCII, as displayed big-endian when read
off the wire). Neither collides with the existing inventory
(`RESI`/`RRES`, `REBA`/`RBRS`, `COMM`/`CORS`, `CREA`/`CRES`,
`MEAS`/…, etc. — all start `0x43`/`0x4D`/`0x52`/`0x53`; amend's
start `0x41`):

- `AmendConfig::MAGIC = 0x414D4E44` (`"AMND"`)
- `AmendResult::MAGIC = 0x414D5253` (`"AMRS"`)

Confirm or pick alternatives before step 1a. (Action: grep the
file for `0x41` magics to be certain there is no collision.)

### 2. Flag, action, and error constant sets

**`AmendConfig` flags** — amend changes *only* the options the user
named, so the config must distinguish "option present" from "value
of option". Use a present-bit + value-bit pair per option:

- `FLAG_QUIET = 1 << 0` — host-only; guest ignores.
- `FLAG_SET_COMPAT = 1 << 1` — the `compat=` option was given.
- `FLAG_COMPAT_V3 = 1 << 2` — target version: set ⇒ v3 (`1.1`),
  clear ⇒ v2 (`0.10`). Only meaningful when `FLAG_SET_COMPAT`.
- `FLAG_SET_LAZY = 1 << 3` — the `lazy_refcounts=` option was given.
- `FLAG_LAZY_ON = 1 << 4` — target lazy state: set ⇒ on, clear ⇒
  off. Only meaningful when `FLAG_SET_LAZY`.

Add `is_valid()` plus small helper predicates if convenient
(`wants_compat_change()`, etc.) — but those can also wait for the
planner crate in phase 2. Keep phase 1 to the constants.

**`AmendResult` actions** (cf. resize's noop/grow/shrink):

- `ACTION_NOOP = 0` — requested options already matched the
  header; nothing rewritten.
- `ACTION_AMENDED = 1` — header rewritten.

**`AmendResult` error codes** (append-only; the minimum phases
2–4 are likely to need — trim or extend later by appending):

- `ERROR_OK = 0`
- `ERROR_UNSUPPORTED_FORMAT = 1` — input is not qcow2 (v1 is
  qcow2-only).
- `ERROR_INVALID_OPTION = 2` — an unrecognised/unsupported `-o`
  key reached the guest (defence in depth; mostly rejected
  host-side in phase 4).
- `ERROR_DOWNGRADE_BLOCKED_FEATURE = 3` — `compat=0.10` refused
  because a v3 incompatible feature is set (`DIRTY`, `CORRUPT`,
  `EXTERNAL_DATA`, `COMPRESSION`, `EXTENDED_L2`).
- `ERROR_DOWNGRADE_REFCOUNT_WIDTH = 4` — `compat=0.10` refused
  because `refcount_bits != 16` (v2 supports 16-bit only;
  rewriting the refcount tree is out of v1 scope).
- `ERROR_LAZY_REQUIRES_V3 = 5` — `lazy_refcounts=on` requested
  against a v2 image, or while simultaneously downgrading to v2.
- `ERROR_HEADER_MISMATCH = 6` — the host-probed cross-check
  (version/features/refcount_bits/cluster_size) disagreed with the
  guest's re-read of the header (defensive, mirrors rebase).
- `ERROR_PARSE_FAILED = 7` — `QcowHeader::parse` failed on the
  staged header.
- `ERROR_DIRTY = 8` — `INCOMPAT_DIRTY` is set; refuse to amend an
  image another writer may hold open.
- `ERROR_EXTENSION_RELOCATION_UNSUPPORTED = 9` — the image carries
  header extension(s) that a v2⇔v3 transition would have to
  relocate, and v1 punts (see master-plan Open question 2). If
  phase 2 decides to *implement* relocation, this code may go
  unused — keep it reserved.
- `ERROR_WRITE_FAILED = 10`
- `ERROR_SCRATCH_TOO_SMALL = 11`
- `ERROR_INTERNAL_OVERFLOW = 12`

### 3. Field layout of `AmendConfig`

Working draft (the host pre-probes the current header and passes a
summary as a cross-check, exactly as resize passes
`current_virtual_size`; the guest re-parses and validates against
it):

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct AmendConfig {
    pub magic: u32,                       // 0x414D4E44 "AMND"
    pub target_format: u32,               // ImageFormat as u32 (Qcow2 in v1)
    pub flags: u32,                       // FLAG_* above
    pub sector_size: u32,

    pub cluster_size: u32,                // qcow2 cluster size (header-cluster span)
    pub current_version: u32,             // 2 or 3 (host-probed cross-check)
    pub current_refcount_bits: u32,       // host-probed cross-check
    pub _pad: u32,                        // align the u64s

    pub current_incompatible_features: u64, // host-probed cross-check
    pub current_compatible_features: u64,   // host-probed cross-check
    pub virtual_size: u64,                  // host-probed cross-check

    pub _reserved: [u8; 72],              // -> total 128 bytes
}
```

Open subquestion: is passing the full feature words necessary, or
is `current_version` + `current_refcount_bits` enough for the
cross-check? Working answer: pass the feature words too — the
guest needs them anyway to evaluate downgrade blockers, and the
cross-check is free once they're present. Confirm the `_reserved`
size yields a clean 128-byte total (recompute from the field list
in the unit test).

### 4. Field layout of `AmendResult`

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct AmendResult {
    pub magic: u32,                       // 0x414D5253 "AMRS"
    pub target_format: u32,               // echoed
    pub action: u32,                      // ACTION_NOOP / ACTION_AMENDED
    pub error: u32,                       // ERROR_*

    pub resulting_version: u32,           // 2 or 3 after amend (for render/baseline)
    pub resulting_lazy_refcounts: u32,    // 0 / 1 after amend

    pub _reserved: [u8; 40],              // -> total 64 bytes
}
```

`resulting_version` and `resulting_lazy_refcounts` let the host
render the success line and let phase 7 baselines assert the
post-amend state without a second probe. Confirm the `_reserved`
size yields 64 bytes total.

### 5. Proto `AmendResultMessage` shape

**Resolved as IMPLEMENTED (diverged from the string-based draft —
forced by the core binary size cap).** The string draft below
mirrored `RebaseResultMessage`:

```protobuf
// REJECTED draft:
message AmendResultMessage {
  string target_format = 1;
  string action = 2;          // "noop" | "amended"
  string compat = 3;          // "0.10" | "1.1"
  bool lazy_refcounts = 4;
  uint32 error = 5;
}
```

This was rejected during implementation: `core.bin` was already at
**63 680 B / 65 536 B (97 %)** on `develop`, and wiring the
string-mapping sender (`"noop"/"amended"`, `"0.10"/"1.1"`) into
`core` pushed it to **65 600 B — 64 B over the hard 64 KiB cap**
(`make instar` / `check-binary-sizes` FAIL). The fix moves the
presentation strings to the host (which has no size budget) and
keeps `action` / `resulting_version` numeric on the wire. The
**shipped** message:

```protobuf
message AmendResultMessage {
  string target_format = 1;     // "qcow2" (reuses ImageFormat::name())
  uint32 action = 2;            // AmendResult::ACTION_* (0=noop,1=amended)
  uint32 resulting_version = 3; // qcow2 version 2 or 3; host -> "0.10"/"1.1"
  bool lazy_refcounts = 4;      // resulting state
  uint32 error = 5;             // mirrors AmendResult::ERROR_*
}
```

`serial::send_amend_result` now passes `action` and
`resulting_version` through numerically (only `target_format`
keeps the shared `ImageFormat::name()` mapping every sender
already links); the host renders `"noop"/"amended"` and
`"0.10"/"1.1"` in phase 4. This brought `core.bin` to **65 304 B
(99 %)** — it fits, but with only ~232 B of headroom.

**Finding for the operator (raised separately): `core.bin` is
effectively at its 64 KiB ceiling.** Every future result-sender
addition will overflow it. The next subcommand/phase that touches
`core` will force a decision: keep squeezing per-feature, or lift
the core memory budget (move the operations load address in
`src/shared/src/lib.rs`'s memory map — a loader/layout change). Not
resolved here. Oneof arm: `AmendResultMessage amend_result = 19;`.

### 6. Does amend need any new device-I/O primitive?

Working answer: **no.** The header is a single cluster at offset 0
on the output device. The guest reads it with
`read_output_sector` and writes it back with
`write_output_sector` — the exact pair resize uses for its
single-device read+write. No `write_input_sector`-style addition.
Confirm by checking that resize's guest only uses those two
primitives for its header patch. If phase 2/3 later discovers a
need (it should not), that is a separate appendable change.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Add `AmendConfig` and `AmendResult` to `src/shared/src/lib.rs`, immediately after the `RebaseResult` block (ends at `:3424`) and before `CommitConfig` (`:3440`) — or anywhere in the `*Config`/`*Result` family, your call, but keep them adjacent. Copy `RebaseConfig` (`:3259`-`:3331`) and `RebaseResult` (`:3339`-`:3424`) as templates. Use the exact field layouts in Open questions 3–4, the magic values in OQ1, and the flag/action/error constant sets in OQ2. Add `is_valid(&self) -> bool` (magic check) on each. Do **not** add any address constant — amend reuses `OPERATION_CONFIG_ADDR`. Run `cargo build -p shared`; iterate until clean. |
| 1b | medium | sonnet | none | Append unit tests for the two new structs to the existing `#[cfg(test)] mod tests` block in `src/shared/src/lib.rs`. Mirror the `rebase_config_*` / `rebase_result_*` tests: assert each `MAGIC`, assert `is_valid()` is true with magic set and false with magic zeroed, assert `core::mem::size_of::<AmendConfig>() == 128` and `size_of::<AmendResult>() == 64` (recompute from the field list and fix the `_reserved` length if the assertion fails — the size assertion is the source of truth), and assert `align_of` is 8 (config) / 4 (result). Run `cargo test -p shared`. |
| 1c | medium | opus | none | Append one field to `CallTable` in `src/shared/src/lib.rs` at the very end, after `fsync_input` (`:963`): `pub send_amend_result: unsafe extern "C" fn(*const AmendResult),` with a doc block modelled on `send_rebase_result` (`:888`-`:893`) explaining it carries the action/version/lazy/error and is appended for back-compat. Bump `CallTable::VERSION` from 17 to 18 (`:1289`). Opus because this is the ABI-version-gating change that every guest binary validates against. Run `cargo build -p shared`. |
| 1d | medium | sonnet | none | Extend `crates/guest-protocol/proto/guest.proto`. Add `AmendResultMessage` mirroring `RebaseResultMessage` with the field list in OQ5. Append the oneof arm `AmendResultMessage amend_result = 19;` after `SnapshotResultMessage snapshot_result = 18;`. Run `cargo build -p guest-protocol` and confirm the generated Rust compiles. |
| 1e | medium | sonnet | none | Core-side wiring so `core.bin` builds and sends the message. Mirror the rebase path exactly across three files: (1) `crates/guest-protocol/src/lib.rs` — add `amend_result_message(target_format, action, compat, lazy_refcounts, error)` modelled on `rebase_result_message` (`:670`), setting the `Payload::AmendResult` variant; (2) `src/core/src/serial.rs` — add `send_amend_result(result: &shared::AmendResult)` modelled on `send_rebase_result` (`:569`) that maps `action` (`ACTION_NOOP`→"noop", else "amended"), `resulting_version` (2→"0.10", else "1.1"), and `resulting_lazy_refcounts` (!=0) into the helper; (3) `src/core/src/main.rs` — add `ct_send_amend_result` near `ct_send_rebase_result` (`:695`) and register `send_amend_result: ct_send_amend_result` in the call-table struct literal (`:294`-region). Add `send_amend_result` to the `serial::` import list at `src/core/src/main.rs:34`. Run `cargo build` for core. |
| 1f | high | opus | none | Host-side decode in `src/vmm/src/main.rs`. Add an `AmendRunResult` holder struct mirroring `RebaseRunResult` (`:2657`), carrying `target_format: u32`, `action`/`resulting_version`/`resulting_lazy_refcounts`, and `error: u32`. Add a `Some(guest_::GuestMessage_::Payload::AmendResult(r)) => { ... }` arm next to the `RebaseResult` arm (`:917`) that logs a debug line and stashes the fields into the holder, following whatever mechanism the rebase arm uses to surface its `RebaseRunResult` (read the rebase arm and its second decode site at `:5290` to match the pattern). The arm does not need to be reachable from a subcommand yet — only the build must pass. Opus: this crosses the protobuf/host boundary and must match the existing harvest mechanism precisely. Run `cargo build --workspace`. |
| 1g | low | sonnet | none | Update `docs/plans/PLAN-amend.md`: in the phase-1 row of the Execution table, leave the link as-is; add a one-line note under the table (or in the relevant Open question) recording that phase 1 adds the single `send_amend_result` pointer and bumps `CallTable::VERSION` to 18, and that no new device-I/O primitive was required (resolves master-plan Open question 6/ABI footprint). Do **not** add this phase file to `docs/plans/order.yml`. |
| 1h | low | sonnet | none | From the worktree root: `pre-commit run --all-files`, resolve any rustfmt/clippy findings; `make instar`; `make test-rust`; `make check-binary-sizes` (expect unchanged). Then stage everything and present a single commit covering steps 1a–1g, using the CLAUDE.md message convention (≤50-char first line ending in a period, 75-char body wrap, `Prompt:` paragraph, `Signed-off-by: Michael Still <mikal@stillhq.com>`, and `Co-Authored-By`/`Assisted-By` lines naming model, 1M context, effort level). Do not push. |

## Agent guidance

### Execution model

All implementation work for this phase is done by sub-agents,
never in the management session. After each step the management
session: (1) reads the actual changed files — does not trust the
summary; (2) confirms no unrelated files were modified; (3) runs
the lint/test commands the step names; (4) commits, asks for a
retry with a sharper brief, or upgrades the model.

### Model and effort notes

- Steps 1a, 1b, 1d, 1e, 1g, 1h are mechanical extensions of
  well-established patterns; sonnet at low/medium effort suffices
  given the exact line references and templates in each brief.
- Steps 1c and 1f use opus: 1c is the version-gating ABI change
  every guest validates against, and 1f must match the existing
  protobuf-harvest mechanism across the host boundary (and the
  opus context window helps hold the two decode sites at `:917`
  and `:5290` together with the holder definition).
- When in doubt, skew to the more capable model.

### Management session review checklist

After each step:

- [ ] Read the changed files — don't trust the agent's summary.
- [ ] No unrelated files modified; all ABI changes are
      append-only (no existing struct field or call-table pointer
      moved or reordered).
- [ ] `cargo build -p shared` / `cargo test -p shared` (1a–1c).
- [ ] `cargo build -p guest-protocol` (1d).
- [ ] `cargo build` for core (1e) and `cargo build --workspace` (1f, 1h).
- [ ] `make instar`, `make lint`, `make test-rust`,
      `make check-binary-sizes`, `pre-commit run --all-files` (1h).
- [ ] The two structs and the new pointer match the layouts in
      Open questions 2–5; `CallTable::VERSION` is 18.

## Administration and logistics

### Success criteria

Phase 1 is complete when:

* All eight steps are merged in one commit on the `amend` branch.
* `make instar` builds and `make lint` is clean.
* `make test-rust` passes, including the new struct-layout tests
  from step 1b.
* `make check-binary-sizes` is unchanged.
* `pre-commit run --all-files` is clean.
* `AmendConfig`/`AmendResult`, the `send_amend_result` pointer
  (VERSION 18), the `AmendResultMessage` proto + oneof arm, the
  core sender path, and the host decode arm all compile with no
  `dead_code` warnings (the `pub` items and the registered
  call-table pointer suppress the warning even though no
  subcommand reaches them yet).
* `instar amend` still prints "unrecognized subcommand" (the
  `Commands` enum is untouched until phase 4).

### Future work created by this phase

None directly. Subsequent phases consume the ABI:

- Phase 2 (qcow2 amend planner) defines pure `no_std` functions
  that take a parsed `QcowHeader` + the amend request and emit a
  header-patch list; they mirror the `AmendConfig` flag semantics
  but do not depend on the struct directly.
- Phase 3 (guest op) reads `AmendConfig` from
  `OPERATION_CONFIG_ADDR`, re-parses the header, validates against
  the cross-check fields, calls the planner, applies patches via
  `write_output_sector`, and calls `send_amend_result`.
- Phase 4 (host CLI) populates `AmendConfig` from the parsed `-o`
  options + a host-side header probe, and consumes the
  `AmendRunResult` the step-1f decode arm produces.

If phase 2 or 3 discovers it needs additional config fields, append
them inside the `_reserved` tail and shrink the padding — no other
phase changes, because the layout is `#[repr(C)]` and the
non-reserved field offsets are fixed.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added to
`docs/plans/order.yml`. The master plan links to it from its
Execution table (already present).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
