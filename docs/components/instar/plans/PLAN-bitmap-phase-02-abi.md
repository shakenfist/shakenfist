# PLAN-bitmap phase 02: shared ABI

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
persistent-dirty-bitmap format or `qemu-img bitmap` semantics,
consult Phase 1's `src/crates/qcow2/src/bitmap.rs` and the qcow2
spec. Flag any uncertainty explicitly rather than guessing.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-bitmap-phase-NN-<descriptive>.md`. The master plan is
[PLAN-bitmap.md](/components/instar/plans/PLAN-bitmap/); Phase 1 is
[PLAN-bitmap-phase-01-parse.md](/components/instar/plans/PLAN-bitmap-phase-01-parse/).
This is the second of ten phases. The direct template for this
phase is [PLAN-amend-phase-01-abi.md](/components/instar/plans/PLAN-amend-phase-01-abi/).

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

This phase establishes the shared ABI surface that `bitmap` will
use, and **freezes it** so Phase 3 (the planner crate), Phase 4
(the guest op), and Phase 5 (the host CLI) can be developed
against a stable boundary. No planner crate, guest binary, or host
CLI entry point is added yet. Following the amend/snapshot
precedent, the `Commands` enum is **not** extended in this phase —
`instar bitmap` still prints "unrecognized subcommand"; the
decode path is wired but unreachable from the CLI until Phase 5.

`bitmap` is closest to `snapshot` on the ABI axis: both carry a
**name** and a **mode/action**. It differs in one material way —
`qemu-img bitmap` applies an **ordered, repeatable list of
actions** in one invocation (e.g. `--add --disable`), so the
config must carry an action *list*, not a single `mode` (this is
master-plan Open question 1, resolved below). It is *simpler* than
snapshot in another way: `qemu-img bitmap` produces **no list
output and is silent on success**, so there is no per-entry
streaming message (no `send_bitmap_entry`) — just one terminal
`BitmapResult`. The mutation reuses the existing
`read_output_sector` / `write_output_sector` device primitives
(same single-device read+write idiom resize/amend use); the only
new call-table pointer is the result sender.

Grounding (verified against the current tree at HEAD `7104be5`):

- **`*Config`/`*Result` structs** live in `src/shared/src/lib.rs`,
  are `#[repr(C)] #[derive(Clone, Copy)]`, carry a 4-byte ASCII
  magic with a `MAGIC` associated const, expose `is_valid(&self)`
  (magic check), use an append-only `ERROR_*` enum, and end with a
  `_reserved: [u8; N]` tail. Reference templates: `AmendConfig`
  (`:3489-3519`, 128 bytes, MAGIC `0x414D4E44` "AMND"),
  `AmendResult` (`:3557-3575`, 64 bytes, MAGIC `0x414D5253`
  "AMRS"), and — the name-carrying model — `SnapshotConfig`
  (`:2712-2754`, 312 bytes: `magic, mode, flags, sector_size,
  arg_len, _pad, arg:[u8;256], …`; name in `arg`, length in
  `arg_len`) and `SnapshotResult` (`:2863-2889`, 184 bytes).
- **Config region ceiling.** `OPERATION_CONFIG_ADDR = 0x00081000`
  (`:331`), `OPERATION_CONFIG_MAX_SIZE = 4096` (`:334`); the next
  region `CHAIN_CONFIG_ADDR = 0x00082000` (`:338`) sits immediately
  after. **A `BitmapConfig` must be ≤ 4096 bytes.** There is *no*
  generic `size_of::<*Config>() <= OPERATION_CONFIG_MAX_SIZE`
  assert today (only ad-hoc per-struct bounds); this phase adds one
  for `BitmapConfig`.
- **`CallTable`** (`:705-983`) is append-only; its last field today
  is `send_amend_result` (`:982`). `CallTable::VERSION` is **18**
  (`:1309`); `validate_call_table!` checks it (`:3986`); the
  version test `call_table_version_is_eighteen` asserts it
  (`:5211`). Appending `send_bitmap_result` bumps VERSION 18 → 19
  and requires updating the doc comment (`:1302-1308`), the assert,
  and the test name.
- **Core populates the call table** in one struct literal
  (`src/core/src/main.rs:285-304`, `send_amend_result:
  ct_send_amend_result` at `:303`); the thunk `ct_send_amend_result`
  is at `:710-715`; the sender `send_amend_result` is in
  `src/core/src/serial.rs:611-621`, building the message via
  `guest_protocol::amend_result_message(...)`. **Because the
  literal names every field, `core.bin` will not compile until
  `ct_send_bitmap_result` + `serial::send_bitmap_result` exist and
  are registered — phase 2 must wire the core side.**
- **core.bin budget (Open question 12).** Budget = 72 KiB
  (`0x10000..0x22000`, `.bss`-inclusive extent). Current extent end
  ≈ `0x203d0` ⇒ **~64.9 KiB used, ~7 KiB headroom (90.2%)**. The
  existing senders are deliberately **numeric-passthrough** (string
  rendering pushed host-side, `serial.rs:604-610`) to stay under
  this ceiling. `send_bitmap_result` must follow suit — no large
  string/lookup tables in core.
- **Proto** (`crates/guest-protocol/proto/guest.proto`): the
  `GuestMessage` payload `oneof` runs through `amend_result = 19`
  (`:427`). Builder helpers are in
  `crates/guest-protocol/src/lib.rs` (`amend_result_message`
  `:724-743`). `MAX_MESSAGE_SIZE = 2200` (`:48`). The next free tag
  is **20**.
- **Host decode + holder** (`src/vmm/src/main.rs`): the
  `Payload::AmendResult(a)` decode arm is at `:5630-5642`; the
  holder `struct AmendRunResult` at `:2682-2693`; the
  `format_message` debug arm at `:928-931`.
- **`ImageFormat`** (`:1419`, `Qcow2 = 2`, `from_u32` `:1447`,
  `name()` `:1466`) maps the format code to `"qcow2"`.

## Mission and problem statement

After this phase lands:

1. `src/shared/src/lib.rs` defines two new `#[repr(C)]` structs,
   `BitmapConfig` and `BitmapResult`, each with a unique magic, an
   `is_valid()`, append-only `ACTION_*` / `ERROR_*` / `FLAG_*`
   constant sets, the fields the guest needs at runtime (including
   the **ordered action list** and the **target bitmap name** +
   **merge-source name pool**), and a `_reserved` tail. Layouts in
   Open questions 3–5. A `const _: () = assert!(size_of::<Bitmap
   Config>() <= OPERATION_CONFIG_MAX_SIZE)` guards the page bound.

2. `CallTable` gains exactly one new field at the very end:
   `send_bitmap_result: unsafe extern "C" fn(*const BitmapResult)`.
   `CallTable::VERSION` bumps 18 → 19. No other pointer is added —
   bitmap reuses `read_output_sector` / `write_output_sector`.

3. `crates/guest-protocol/proto/guest.proto` gains one message
   `BitmapResultMessage` and one oneof arm `BitmapResultMessage
   bitmap_result = 20;`. Fields are numeric where possible to
   protect the core budget (Open question 6).

4. Core-side wiring makes `core.bin` build and send the message:
   `guest_protocol::bitmap_result_message(...)`
   (`crates/guest-protocol/src/lib.rs`), `serial::send_bitmap_result`
   (`src/core/src/serial.rs`), `ct_send_bitmap_result`
   (`src/core/src/main.rs`), and its registration in the call-table
   struct literal. **core.bin is re-measured** (Open question 12).

5. Host-side wiring makes `instar` build and decode the message: a
   `BitmapRunResult` holder in `src/vmm/src/main.rs` (mirroring
   `AmendRunResult`) and a `Payload::BitmapResult(r)` decode arm
   plus a `format_message` arm. The `Commands` enum is **not**
   extended (Phase 5).

6. Unit tests in `src/shared/src/lib.rs`'s `#[cfg(test)] mod tests`
   verify size, magic, `is_valid()`, alignment, and the
   `<= OPERATION_CONFIG_MAX_SIZE` bound of the two new structs
   (mirroring `amend_config_*` / `snapshot_config_*` tests), and
   the bumped `CallTable::VERSION`.

7. `make instar` builds, `make lint` is clean, `make test-rust`
   passes, `pre-commit run --all-files` passes, and
   `make check-binary-sizes` shows `core.bin` still within its
   72 KiB budget after the new sender (re-measure and record the
   new headroom).

Nothing in this phase changes user-visible behaviour.

## Open questions

To confirm during planning review before step 2a runs. Each is a
real choice grounded in the code and the qemu 10.0 `bitmap`
behaviour established in the master-plan research.

### 1. Magic values

Follow the existing convention (4 ASCII chars as a big-endian-style
hex constant, first char = high byte, e.g. "AMND" =
`0x414D4E44`). Neither collides with the taken set (AMND/AMRS,
SNAP/SNRS/SNER, RESI/RRES, …):

- `BitmapConfig::MAGIC = 0x424D5043` (`"BMPC"`)
- `BitmapResult::MAGIC = 0x424D5253` (`"BMRS"`)

(Action before 2a: grep the shared crate for `0x424D` to confirm no
collision.)

### 2. Action, flag, and error constant sets

**Action opcodes** (bytes in `BitmapConfig.actions`, applied in
order; `0` = none/unused):

- `ACTION_ADD = 1`
- `ACTION_REMOVE = 2`
- `ACTION_CLEAR = 3`
- `ACTION_ENABLE = 4`
- `ACTION_DISABLE = 5`
- `ACTION_MERGE = 6`

**`BitmapConfig` flags:**

- `FLAG_QUIET = 1 << 0` — host-only (guest ignores; success is
  silent regardless, matching qemu).
- `FLAG_VERBOSE = 1 << 31` — mirrors the other configs' verbose bit.

No per-action "present/value" flag pairs are needed (unlike amend):
each action carries its own semantics via the opcode, granularity
is a dedicated field, and merge sources are a dedicated pool.

**`BitmapResult` result codes.** `action` echoes the *last applied*
opcode (or `0`); `ACTION`-style noop is not needed (silent success).

**`BitmapResult` error codes** (append-only; the set the qemu error
conditions from the research map onto — trim/extend later by
appending):

- `ERROR_OK = 0`
- `ERROR_UNSUPPORTED_FORMAT = 1` — input is not qcow2 (qcow2-only).
- `ERROR_UNSUPPORTED_VERSION = 2` — qcow2 v2 cannot store bitmaps
  (qemu: "Cannot store dirty bitmaps in qcow2 v2 files").
- `ERROR_PARSE_FAILED = 3` — `QcowHeader::parse` failed.
- `ERROR_HEADER_MISMATCH = 4` — host cross-check disagreed with the
  guest re-read (defensive, mirrors rebase/amend).
- `ERROR_BITMAP_NOT_FOUND = 5` — remove/clear/enable/disable/merge
  named a bitmap that does not exist.
- `ERROR_BITMAP_EXISTS = 6` — `--add` with an existing name.
- `ERROR_BITMAP_IN_USE = 7` — an `in_use`/inconsistent bitmap was
  targeted by an action other than `--remove` (qemu refuses; only
  remove is allowed, and there is no `--force`).
- `ERROR_NAME_TOO_LONG = 8` — name > 1023 bytes.
- `ERROR_GRANULARITY_RANGE = 9` — granularity_bits outside 9..=31.
- `ERROR_TOO_MANY_BITMAPS = 10` — would exceed 65535 bitmaps.
- `ERROR_NO_SPACE = 11` — cluster allocation failed / bitmap too
  large for the granularity.
- `ERROR_WRITE_FAILED = 12`
- `ERROR_READ_FAILED = 13`
- `ERROR_SCRATCH_TOO_SMALL = 14`
- `ERROR_INTERNAL_OVERFLOW = 15`
- `ERROR_MERGE_SOURCE_NOT_FOUND = 16` — a `--merge` source bitmap
  does not exist.
- `ERROR_UNSUPPORTED_ACTION = 17` — reserved for a deferred action
  path (e.g. cross-file `--merge -b` if Phase 5 defers it, or an
  action the planner does not yet implement). Keep reserved even if
  unused at freeze time.

### 3. Field layout of `BitmapConfig` (Open questions 1 + 9 resolved here)

Working draft. The host pre-probes the header + bitmaps extension
and passes a cross-check (like resize/amend); the guest re-parses
and validates against it. The **ordered action list** is
`num_actions` + `actions:[u8; MAX_BITMAP_ACTIONS]`; the **target
name** is the positional `BITMAP` (one name for all actions);
**merge sources** are consumed in order from a length-delimited
pool as each `ACTION_MERGE` is encountered.

```rust
pub const MAX_BITMAP_ACTIONS: usize = 8;
pub const BITMAP_NAME_BUF: usize = 1024;         // holds a <=1023-byte name
pub const MAX_MERGE_SOURCES: usize = 8;
pub const MERGE_SOURCE_POOL: usize = 2048;       // concatenated source names

#[repr(C)]
#[derive(Clone, Copy)]
pub struct BitmapConfig {
    pub magic: u32,                        // 0x424D5043 "BMPC"
    pub target_format: u32,                // ImageFormat (Qcow2 in v1)
    pub flags: u32,                        // FLAG_*
    pub sector_size: u32,

    pub num_actions: u32,                  // 1..=MAX_BITMAP_ACTIONS
    pub name_len: u32,                     // target name length (1..=1023)
    pub num_merge_sources: u32,            // 0..=MAX_MERGE_SOURCES
    pub _pad0: u32,

    pub granularity: u64,                  // --add granularity in BYTES; 0 = default
    // host-probed cross-check (guest re-parses + validates):
    pub current_autoclear_features: u64,
    pub current_incompatible_features: u64,
    pub virtual_size: u64,
    pub bitmap_directory_offset: u64,      // 0 if no bitmaps extension yet
    pub bitmap_directory_size: u64,

    pub current_version: u32,              // 2 or 3
    pub current_refcount_bits: u32,
    pub cluster_size: u32,
    pub nb_bitmaps: u32,                   // existing bitmap count (0 if none)

    pub actions: [u8; MAX_BITMAP_ACTIONS], // opcodes, in CLI order
    pub _pad1: [u8; 8],                    // align pool region

    pub name: [u8; BITMAP_NAME_BUF],       // target bitmap name (UTF-8, no NUL)
    pub merge_source_lens: [u16; MAX_MERGE_SOURCES],
    pub merge_source_pool: [u8; MERGE_SOURCE_POOL], // source names, concatenated

    pub _reserved: [u8; N],                // pad total to a round <= 4096
}
```

Approx size: ~112 (scalars+actions+pad) + 1024 + 16 + 2048 ≈ 3200 B
before `_reserved`; round the total to a clean number (suggest
**3584** or **4096**) and pin it with a `size_of` assert **and**
the `<= OPERATION_CONFIG_MAX_SIZE` assert. Fits the 4096 page.

**Subquestions to confirm:**
- **MAX_BITMAP_ACTIONS = 8** — generous; real `qemu-img bitmap`
  invocations use 1–3 actions. Host rejects >8 with a clear error.
- **Merge pool sizing (MAX_MERGE_SOURCES = 8, MERGE_SOURCE_POOL =
  2048).** Holds e.g. 8 short names or ~2 max-length names.
  Real incremental-backup merges use 1–2 sources. The host rejects
  more sources than `MAX_MERGE_SOURCES` or a total source-name
  byte-count over the pool with a clear "too many/large merge
  sources" error. **This is a documented v1 bound** — note it in
  Phase 5/Phase 10 docs.
- **Merge in v1 (Open question 2).** The ABI reserves the
  merge-source slots regardless, so no second ABI bump is needed if
  same-image merge lands in v1 or slips to a follow-up. If Phase 5
  defers cross-file `--merge -b`, that is rejected host-side (there
  is no source-file field in this config — `-b` is *not* in the v1
  ABI; add it only when cross-file merge is implemented).
- **Cross-check fields.** The guest re-reads the header + bitmaps
  extension itself, so the cross-check is defensive; keep the
  listed fields (they are cheap and let the guest assert
  host/guest agreement, emitting `ERROR_HEADER_MISMATCH` on
  disagreement).

### 4. Field layout of `BitmapResult`

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct BitmapResult {
    pub magic: u32,                 // 0x424D5253 "BMRS"
    pub target_format: u32,         // echoed
    pub error: u32,                 // ERROR_*
    pub action: u32,                // last applied opcode (0 if none)

    pub actions_applied: u32,       // how many actions succeeded before error/end
    pub resulting_nb_bitmaps: u32,  // bitmap count after the op (render/baseline)

    pub _reserved: [u8; 40],        // -> total 64 bytes
}
```

`actions_applied` + `resulting_nb_bitmaps` let the host render a
useful (still-silent-by-default) summary and let Phase 8 baselines
assert post-op state without a second probe. Confirm `_reserved`
yields 64 bytes.

### 5. Proto `BitmapResultMessage` shape (Open question 6)

Numeric-only to protect the core budget (the amend string-draft was
rejected for exactly this reason — `serial.rs:604-610`). Pass the
format as a small string only, mirroring `amend_result_message`
(or drop it and let the host supply "qcow2" — decide in 2c):

```protobuf
message BitmapResultMessage {
  string target_format = 1;     // "qcow2"; or omit and infer host-side
  uint32 error = 2;
  uint32 action = 3;
  uint32 actions_applied = 4;
  uint32 resulting_nb_bitmaps = 5;
}
```

`oneof payload { … BitmapResultMessage bitmap_result = 20; }`.
No per-bitmap entry message (bitmap has no list output).

### 6. core.bin budget (Open question 12)

~7 KiB headroom today. Adding one call-table pointer (RAM table,
not core text) plus one thin numeric sender should be well within
budget, but **2c must re-run `make check-binary-sizes` and record
the new core extent**. If it regresses toward the ceiling: keep the
sender numeric (drop the `target_format` string), and if still
tight, note it for the operator (the next lever is lifting
`OPERATION_LOAD_ADDR`, a layout change out of this phase's scope).

## Execution / step-level guidance

Small, mostly mechanical phase following a well-worn path; plan at
**medium** effort except 2a (the struct/layout freeze) which is
**high** because the ordered-action + name + merge-pool layout is
the load-bearing design decision the rest of the plan builds on.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | In `src/shared/src/lib.rs`, add `BitmapConfig` and `BitmapResult` per Open questions 2–4: the two `#[repr(C)] #[derive(Clone, Copy)]` structs, their `MAGIC` consts (0x424D5043 / 0x424D5253), `ACTION_*` / `FLAG_*` / `ERROR_*` const sets, `is_valid()`, and the `MAX_BITMAP_ACTIONS`/`BITMAP_NAME_BUF`/`MAX_MERGE_SOURCES`/`MERGE_SOURCE_POOL` consts. Place them near `AmendConfig`/`AmendResult` (`:3489+`). Add `#[cfg(test)]` tests mirroring `amend_config_*`: pin `size_of`/`align_of` for both, assert magic round-trips and `is_valid`, and add `const _: () = assert!(size_of::<BitmapConfig>() <= OPERATION_CONFIG_MAX_SIZE);` plus a runtime test asserting the same. Compute `_reserved` so `BitmapConfig` is a round total (3584 or 4096) and `BitmapResult` is 64. No CallTable change yet. |
| 2b | medium | sonnet | none | Append `pub send_bitmap_result: unsafe extern "C" fn(*const BitmapResult),` as the last field of `CallTable` (after `send_amend_result`, `:982`). Bump `CallTable::VERSION` 18 → 19 (`:1309`), update the append-log doc comment (`:1302-1308`), and update the version test `call_table_version_is_eighteen` (`:5204-5211`) — rename to `..._nineteen` and assert 19. Do NOT touch core/host yet (this step will not build core until 2c; that is expected — commit 2b+2c together if needed to keep the tree building, or sequence so the first building commit is after 2c). |
| 2c | high | opus | none | Wire the core side so `core.bin` builds and sends the message. Add `BitmapResultMessage` + `bitmap_result = 20` to `crates/guest-protocol/proto/guest.proto`; add the `bitmap_result_message(...)` builder to `crates/guest-protocol/src/lib.rs` (mirror `amend_result_message` `:724-743`, numeric fields per Open question 5); add `serial::send_bitmap_result` (`src/core/src/serial.rs`, mirror `:611-621`, numeric-passthrough — no string tables); add `ct_send_bitmap_result` thunk (`src/core/src/main.rs`, mirror `:710-715`) and register `send_bitmap_result: ct_send_bitmap_result` in the call-table literal (`:285-304`) and the `serial` import (`:31`). Then run `make instar` and `make check-binary-sizes`; RECORD core.bin's new `.bss`-inclusive extent and headroom in the commit message / this plan. If core regresses toward 72 KiB, drop the `target_format` string from the message. |
| 2d | medium | sonnet | none | Host-side decode. In `src/vmm/src/main.rs`: add `struct BitmapRunResult` (mirror `AmendRunResult` `:2682-2693`: `target_format, error, action, actions_applied, resulting_nb_bitmaps`), a `Payload::BitmapResult(b)` decode arm in the appropriate run loop scaffold (mirror `:5630-5642`) — it may be wired into a placeholder harvester since no subcommand reaches it yet — and a `format_message` debug arm (mirror `:928-931`). Do NOT extend the `Commands` enum. Ensure `instar` builds. |
| 2e | medium | sonnet | none | Full verification + tests pass: `make instar`, `make lint`, `make test-rust` (incl. the new shared-crate tests and the bumped VERSION test), `make check-binary-sizes`, `pre-commit run --all-files`. Fix any fallout. Confirm `instar bitmap` still prints "unrecognized subcommand" (Commands not extended). |

(2b and 2c may be squashed into a single commit if keeping every
commit buildable requires it — a `CallTable` field without its core
registration does not compile. Prefer: land 2a as its own commit,
then 2b+2c as one "wire the call-table + core sender" commit, then
2d, then 2e as the verification/cleanup.)

## Management session review checklist

- [ ] The intended files changed and no others (read them).
- [ ] `BitmapConfig` ≤ 4096 (both the `const _` assert and a
      runtime test), `BitmapResult` == 64, magics correct and
      collision-free.
- [ ] `CallTable::VERSION` == 19; `validate_call_table!` and the
      version test updated.
- [ ] Core builds and actually sends the message; the sender is
      numeric-passthrough (no string tables in core).
- [ ] `make check-binary-sizes` passes and `core.bin` is still
      within 72 KiB — the new extent and headroom are recorded.
- [ ] Host decodes `Payload::BitmapResult`; `Commands` NOT
      extended; `instar bitmap` still unrecognized.
- [ ] `make instar`, `make lint`, `make test-rust`,
      `pre-commit run --all-files` all green.
- [ ] Commit messages follow conventions (Co-Authored-By with
      model/context/effort/settings; `Signed-off-by`; `Prompt:`).

## Success criteria

This phase is complete when:

- `BitmapConfig` / `BitmapResult` exist in `src/shared/src/lib.rs`
  with the frozen layout (ordered action list, target name,
  merge-source pool, host cross-check), unique magics, append-only
  constant sets, `is_valid()`, size/align/`<= OPERATION_CONFIG_MAX_
  SIZE` asserts and tests.
- `CallTable` has `send_bitmap_result` as its last field and
  `VERSION == 19`; the validate macro and version test agree.
- `crates/guest-protocol` defines `BitmapResultMessage`
  (`bitmap_result = 20`) and its builder; `core` registers and
  sends it; `core.bin` still fits its 72 KiB budget (new headroom
  recorded).
- `src/vmm/src/main.rs` decodes `Payload::BitmapResult` into a
  `BitmapRunResult`; the `Commands` enum is unchanged.
- `make instar`, `make lint`, `make test-rust`,
  `make check-binary-sizes`, `pre-commit run --all-files` are all
  green; `instar bitmap` still prints "unrecognized subcommand".

## Back brief

Before executing any step, the executing agent should back brief
the operator on its understanding of this phase and how the work
aligns with it — in particular that this is an **ABI-freeze**
phase (no planner, guest op, or CLI), that the config carries an
**ordered action list** (not a single mode) because `qemu-img
bitmap` applies repeatable actions in order, that the new core
sender must stay **numeric-only** to respect the ~7 KiB core
budget, and that the `Commands` enum is deliberately **not**
extended here.
