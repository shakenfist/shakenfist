# PLAN-snapshot phase 03: list-mode guest binary scaffolding

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the most recent
streaming-emit operation `src/operations/map/{Cargo.toml,
linker.ld,src/main.rs}`, the qcow2 header parser
`qcow2::QcowHeader::parse` and the `nb_snapshots` /
`snapshots_offset` fields it surfaces, the streaming primitive
`qcow2::for_each_snapshot_entry` and converter
`qcow2::snapshot_entry_to_record` landed in phase 2, the
phase-1 wire ABI in `src/shared/src/lib.rs` for `SnapshotConfig`
/ `SnapshotEntryRecord` / `SnapshotResult` / `MODE_*` /
`FLAG_*` / `ERROR_*`, the `validate_call_table!` macro and the
shared scratch-memory constants `SCRATCH_MEM_BASE` /
`MAX_SECTOR_SIZE`, the `detect_format_from_header` helper, the
binary-size scaffolding in `src/build.sh` and
`scripts/check-binary-sizes.sh`, and the workspace registration
in `src/Cargo.toml`), and ground your answers in what the code
actually does today. Do not speculate about the codebase when
you could read it instead.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 3 of
fourteen.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Phase 1 landed the wire ABI. Phase 2 landed the qcow2 parser
extension and the streaming primitive. Phase 3 wires both
together inside a new guest binary at `src/operations/snapshot/`
that implements **only `MODE_LIST`** end-to-end and stubs the
mutating modes so the host CLI (phase 4) can dispatch all four
modes today even though three of them return an error until
phases 6–8 fill them in.

The phase is small in code but touches several build-system
files. Pattern is well-established by `src/operations/map/`
(phase 3 of `PLAN-map.md`) and `src/operations/measure/`
(phase 3 of `PLAN-measure.md`): a `no_std` `no_main` binary
that:

1. Validates the call table.
2. Reads `SnapshotConfig` from `OPERATION_CONFIG_ADDR`.
3. Validates the config (magic, `sector_size`, `mode`).
4. Reads the first sector to detect the source format.
5. Refuses non-qcow2 sources with `ERROR_UNSUPPORTED_FORMAT`.
6. Parses the qcow2 header (`QcowHeader::parse`) to extract
   `virtual_size`, `nb_snapshots`, `snapshots_offset`.
7. Dispatches on `config.mode`:
   - `MODE_LIST`: calls `qcow2::for_each_snapshot_entry` with a
     closure that converts each `SnapshotEntry` to a
     `SnapshotEntryRecord` via `qcow2::snapshot_entry_to_record`
     and emits it through `call_table.send_snapshot_entry`,
     incrementing a local counter.
   - `MODE_APPLY` / `MODE_CREATE` / `MODE_DELETE`: stub returns
     `ERROR_INVALID_CONFIG` immediately (phase 3 marker; phases
     6–8 replace these with the real planners).
8. Builds a `SnapshotResult` (mode echo, error, count, assigned
   id length 0 for list mode) and sends it via
   `send_snapshot_result`.
9. Calls `send_complete`.

The binary's footprint is small: format detection + qcow2
header parse + snapshot-table streaming + record conversion.
No L1/L2 walk, no refcount tables, no compression decoder.
Expected binary size at landing: ~80–110 KiB (well under the
384 KiB cap; `info`'s 123 KiB and `convert`'s 296 KiB bracket
where we expect to sit).

### What phase 3 builds on

- **Phase-1 ABI** (`src/shared/src/lib.rs`):
  - `SnapshotConfig` at `OPERATION_CONFIG_ADDR` with magic
    `"SNAP"`, `MODE_LIST = 0`, `MODE_APPLY = 1`,
    `MODE_CREATE = 2`, `MODE_DELETE = 3`, `FLAG_QUIET = 1`,
    `FLAG_FORCE_SHARE = 2`, `FLAG_VERBOSE = 1 << 31`. `arg`
    is a 256-byte UTF-8 buffer with `arg_len`.
  - `SnapshotEntryRecord` carries one snapshot on the wire.
  - `SnapshotResult` with `mode`, `error`, `snapshots_emitted`,
    `assigned_id_len`, `assigned_id`.
  - `CallTable` pointers `send_snapshot_entry`,
    `send_snapshot_result`, `fsync_input`.
- **Phase-2 qcow2 surface**:
  - `qcow2::QcowHeader::parse(header_bytes) -> Option<QcowHeader>`
    returns the parsed header with `virtual_size`,
    `nb_snapshots`, `snapshots_offset`, `incompatible_features`,
    `version`, etc.
  - `qcow2::for_each_snapshot_entry(call_table, device_idx,
    nb_snapshots, snapshots_offset, sector_size, input_capacity,
    cache_buf, &mut bytes_read, |entry| {...}) -> bool` streams
    entries one at a time.
  - `qcow2::snapshot_entry_to_record(entry: &SnapshotEntry,
    header_virtual_size: u64) -> shared::SnapshotEntryRecord`
    converts to the wire form, handling the v2 disk_size
    fallback.
- **Existing operation scaffolding** (`src/operations/map/`):
  - `Cargo.toml` shape: `[[bin]]`, `panic = "abort"`,
    `opt-level = "z"`, `lto = true`.
  - `linker.ld`: identical for every operation; copy verbatim.
  - `main.rs` skeleton: `#![no_std] #![no_main]`, `extern "C"
    _start`, `panic_handler`, `validate_call_table!`,
    `SCRATCH_MEM_BASE` slot layout.
- **Build scaffolding**:
  - `src/Cargo.toml` lists `operations/map` in the
    `[workspace] members`.
  - `src/build.sh` has a per-binary block (build, objcopy,
    size-check, copy-to-target/release).
  - `scripts/check-binary-sizes.sh` has a `for op in ...` loop
    listing every binary by name.
  - `Makefile`'s `CARGO_TOML_FILES` and `--exclude foo-op` test
    list.

### What phase 3 does not change

- The wire ABI (frozen).
- The qcow2 crate (frozen for this phase; later phases extend
  it for mutators).
- The host VMM (phase 4 wires the host CLI; this phase only
  adds the guest binary).
- The fuzz harness (phase 12 extends fuzz coverage).
- Documentation prose (no `docs/snapshot.md` yet; that lands
  in phase 14). The phase 3 commit message and inline doc
  comments in `src/operations/snapshot/src/main.rs` are the
  only doc.

### Why phase 3 lands `MODE_LIST` and stubs the others

The master plan front-loads list mode so it can ship to users
once phase 4 (host CLI for list mode) lands, independently of
the mutating modes. Stubbing `MODE_APPLY` / `MODE_CREATE` /
`MODE_DELETE` lets phase 4's host CLI dispatch all four flags
without a "subcommand not recognised" failure path: the user
sees a clear `qcow2: snapshot create not yet implemented`
error rather than `instar` aborting with a stack trace.

The stub error code is `ERROR_INVALID_CONFIG` so it is
distinguishable in the host-side renderer (phase 9) from the
genuine "config is invalid" path that real planners hit on
malformed input. An alternative would be to add an
`ERROR_NOT_IMPLEMENTED` code — open question 3 below.

## Mission and problem statement

After phase 3 lands:

1. `src/operations/snapshot/` exists with:
   - `Cargo.toml` declaring `name = "snapshot-op"` and
     `[[bin]] name = "snapshot"`, dependencies `shared` and
     `qcow2` only, `[profile.release]` matching `map-op`.
   - `linker.ld` identical to `src/operations/map/linker.ld`.
   - `src/main.rs` implementing the flow described in the
     Situation section above.

2. `src/Cargo.toml` adds `operations/snapshot` to `members`.

3. `src/build.sh` has a `Building snapshot operation` block
   that:
   - `cd operations/snapshot && cargo +nightly build --release && cd ../..`
   - Converts `target/x86_64-unknown-none/release/snapshot` to
     `snapshot.bin` via `rust-objcopy`.
   - Copies `snapshot.bin` to `target/release/` alongside the
     other binaries.
   - Adds `check_size "snapshot.bin" "target/release/$SNAPSHOT_BIN" "$OP_MAX"`.

4. `scripts/check-binary-sizes.sh`'s `for op in ...` loop adds
   `snapshot`.

5. `Makefile`'s `CARGO_TOML_FILES` includes
   `src/operations/snapshot/Cargo.toml`. The `make test-rust`
   target's `--exclude` list includes `snapshot-op` to match
   the convention used for every other operation crate.

6. The build step in `src/build.sh` and the trailing summary
   block list `snapshot.bin` with a one-line description.

7. `make instar` builds clean; `snapshot.bin` lands in
   `src/target/release/`. `make check-binary-sizes` reports
   `snapshot.bin` under 384 KiB (expected ~80–110 KiB at
   landing).

8. `make lint` clean. `pre-commit run --all-files` clean.

9. `make test-rust` passes — `snapshot-op` has no inline
   tests (the binary is `no_std` no_main and per-step testing
   happens via the streaming primitive's unit tests in qcow2
   from phase 2). The crate's `--exclude` from the workspace
   `cargo test` matches what every other operation does.

10. A manual smoke test confirms `MODE_LIST` works end-to-end
    via the existing VMM debug formatter — phase 1 added
    verbose-trace formatter arms for `Payload::SnapshotEntry`
    and `Payload::SnapshotResult`. With a one-line shell
    sequence the smoke test (open question 5) drives the
    guest from a test harness that calls
    `verify_call_table!`, writes a `SnapshotConfig`, and
    asserts on the `--verbose` output. If a clean harness
    is too much yoke for phase 3, defer the smoke test to
    phase 4 (host CLI) — open question 5 prefers this.

Nothing in phase 3 changes user-visible behaviour. The host
CLI dispatcher in `src/vmm/src/main.rs` does not learn the
`Snapshot` variant until phase 4. `instar snapshot` continues
to print "unrecognized subcommand".

## Open questions

### 1. Should phase 3 share scratch slots with future phases (5–8)?

Working answer: **lay out the scratch slots now so phases 5–8
extend without renumbering**. The mutating modes need at least
five logical slots: HEADER, CACHE_A (snapshot-table reads),
CACHE_B (L1 cache), CACHE_C (L2 cache), CACHE_D (refcount
cache). Phase 3 only touches HEADER and CACHE_A, but the
constants for B/C/D can be declared with a phase-3 `_` prefix
or just left unused (`#[allow(dead_code)]`) so phase 5 doesn't
have to renumber addresses.

Concretely, phase 3 declares:

```rust
const HEADER_BUF: usize = SCRATCH_MEM_BASE;
const CACHE_BUF_A: usize = HEADER_BUF + MAX_SECTOR_SIZE;
// Phase 5+ will use CACHE_BUF_B/C/D for L1/L2/refcount caches.
const CACHE_BUF_B: usize = CACHE_BUF_A + MAX_SECTOR_SIZE;
const CACHE_BUF_C: usize = CACHE_BUF_B + MAX_SECTOR_SIZE;
const CACHE_BUF_D: usize = CACHE_BUF_C + MAX_SECTOR_SIZE;
```

Total = 5 × MAX_SECTOR_SIZE = 320 KiB. SCRATCH_MEM_SIZE is
~12.9 MiB so there's no pressure. The unused B/C/D constants
have `#[allow(dead_code)]` and the compile won't warn.

### 2. Should phase 3 instantiate `Qcow2State::init` or use `QcowHeader::parse` directly?

Working answer: **`QcowHeader::parse` directly**. `Qcow2State`
bundles L1/L2 caches and is required for cluster lookup, which
list mode never needs. The header parser is pure
(`Option<QcowHeader>` from `&[u8]`) and surfaces
`virtual_size`, `nb_snapshots`, `snapshots_offset`,
`incompatible_features`, `version` — everything list mode
needs.

Phases 5–8 will need `Qcow2State::init` for the L1/L2 walks.
They can add it; phase 3 keeps the binary lean.

### 3. Should the stub modes return `ERROR_INVALID_CONFIG` or a new `ERROR_NOT_IMPLEMENTED` code?

Working answer: **`ERROR_INVALID_CONFIG` for phase 3**. The
error-code set in `SnapshotResult::ERROR_*` is append-only
(landed in phase 1). Adding a new code requires a phase-1
amendment, which we avoid because it's not load-bearing — the
host renderer (phase 9) will already need to distinguish
"create not implemented" from "create with bad arg" based on
the mode echo and the build version, not the error code
alone.

The stub also calls `(call_table.verbose_print)` with a
one-line `"snapshot: mode N not implemented in v1\n\0"`
message so the verbose log makes the stub obvious.

Alternative considered: add `ERROR_NOT_IMPLEMENTED = 13`
appended to phase 1's set. Rejected: appending to a frozen
phase-1 struct requires either modifying the phase-1 commit
(no, it's pushed) or making the new code phase 3's
responsibility (yes, but then phases 6/7/8 have to flip the
stub to a real planner *and* remove the error code). Cleaner
to keep `ERROR_INVALID_CONFIG` as the stub marker; phases
6/7/8 replace the stub with the real planner and the error
code stays available for genuinely-invalid configs.

### 4. Should phase 3 refuse incompatible feature flags (compressed clusters, encryption, external data file, bitmaps)?

Working answer: **no, for list mode**. The master plan
explicitly says list mode works regardless of incompatible
features. The mutating modes refuse — but phase 3 stubs
those, so the refusal happens for the wrong reason
(`ERROR_INVALID_CONFIG` "not implemented"). Phases 6/7/8 add
the real `ERROR_UNSUPPORTED_FEATURE` refusal alongside the
planner.

For list mode specifically: a qcow2 with compressed clusters
still has a parseable snapshot table; we can list its
snapshots without ever touching the compressed clusters.
Same for encryption and external data file. Refusing in
phase 3 would be an unjustified divergence from
`qemu-img snapshot -l` (which doesn't refuse either).

### 5. Should phase 3 add a smoke test, or defer to phase 4 (host CLI)?

Working answer: **defer to phase 4**. The guest binary cannot
be exercised without a host launcher, and phase 4 adds the
host CLI plus an integration test that drives the guest. A
phase-3 smoke test would either (a) duplicate phase 4's
harness or (b) introduce a one-shot Python test that talks
to the VMM directly, which is awkward and short-lived.

Phase 3's verification is the build (binary lands, size
under cap) and the verbose-trace formatter arms from phase 1
(if a SnapshotEntry / SnapshotResult message arrives on the
serial channel, the VMM prints it correctly — the formatter
itself was tested by phase 1's manual review).

### 6. Should phase 3 add a Rust unit test for `_start` somehow?

Working answer: **no**. The binary is `no_std no_main` and
unit-testing `_start` would require building a mock
`CallTable`, mocking `OPERATION_CONFIG_ADDR`, and rebuilding
the entire binary harness. The mocking infrastructure lives
in `src/fuzz/src/lib.rs` and is intended for the fuzz
harnesses; reusing it for unit tests is bigger than the
value.

The streaming primitive (`for_each_snapshot_entry`) and the
converter (`snapshot_entry_to_record`) already have unit
tests from phase 2. The integration tests in phase 11 will
exercise the full guest binary against fixtures.

If a regression in the binary surfaces later, phase 12 fuzz
harnesses run the binary under fuzz with adversarial
inputs.

### 7. Binary name: `snapshot` or `snap`?

Working answer: **`snapshot`**. Every other operation binary
uses the full subcommand name (`info`, `convert`, `measure`,
`create`, `resize`, `rebase`, `commit`, `map`). The host CLI
matches on `Snapshot(SnapshotArgs)` (phase 4). Truncating to
`snap` would be inconsistent with no benefit.

### 8. Should the build-script per-binary block be DRYed up?

Working answer: **no, copy-paste it**. The existing
`src/build.sh` has 12 near-identical per-binary blocks. The
phase-3 commit adds a 13th. A future refactor that loops
over a list of binary names is a separate scope; phase 3 is
not the place.

(Alternative considered: factor the per-binary block into a
shell function. Rejected as out-of-scope.)

### 9. Should `SnapshotConfig.input_device_count` exist?

Working answer: **no**. List mode operates on a single image,
identical to map's `input_device_count == 1` invariant. The
phase-1 `SnapshotConfig` does not have an
`input_device_count` field (open question 3 in phase 1's
plan declared it implicit; the host always attaches exactly
one input device for snapshot list mode). Phase 3 trusts
this invariant — it calls `(call_table.read_input_sector)(0,
...)` for device 0 and does not call
`(call_table.get_input_device_count)()`.

Phases 5–8 (mutate) will revisit if any mutating mode needs
multiple devices, but the master plan says snapshot is
single-image: the overlay being mutated is attached as
input slot 0 (RW), no chain.

### 10. Should phase 3 also wire the host CLI?

Working answer: **no**. Phase 4 of the master plan owns the
host CLI for list mode. Phase 3 is guest-binary-only. The
phase boundary is intentional — splitting host and guest into
separate commits makes review easier and lets phase 4 be
gated independently on the host-side tests.

### 11. Should phase 3 enforce a sector_size invariant?

Working answer: **yes, mirror the map check**. Map enforces
`config.sector_size >= 512 && config.sector_size as usize <=
MAX_SECTOR_SIZE && config.sector_size.is_power_of_two()`.
Snapshot inherits the same. Failure returns
`ERROR_INVALID_CONFIG`.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | low | sonnet | worktree | Create the new operation crate at `src/operations/snapshot/`. (i) `Cargo.toml`: copy `src/operations/map/Cargo.toml` as a template, change `name = "snapshot-op"`, change `description = "Snapshot operation: list / apply / create / delete qcow2 internal snapshots"`, change the `[[bin]] name = "snapshot"` and `path = "src/main.rs"`. Dependencies are `shared = { path = "../../shared" }` and `qcow2 = { path = "../../crates/qcow2" }` — drop the `raw`, `vmdk`, `vhd`, `vhdx` deps that map needs (snapshot is qcow2-only). Keep the `[profile.release]` block identical (`panic = "abort"`, `opt-level = "z"`, `lto = true`). (ii) `linker.ld`: copy `src/operations/map/linker.ld` verbatim — every operation uses the same script. (iii) `src/main.rs`: a stub that compiles — `#![no_std] #![no_main]`, `use core::panic::PanicInfo;`, `use shared::{...minimal imports...};`, `#[panic_handler] fn panic(_: &PanicInfo) -> ! { loop {} }`, `#[no_mangle] pub unsafe extern "C" fn _start() -> u64 { 0 }`. Just enough to build. (iv) Add `"operations/snapshot"` to the `members` list in `src/Cargo.toml`. Run `cargo build -p snapshot-op --release --target x86_64-unknown-none` (or whatever target the existing operations use; check the `src/build.sh` invocation `cargo +nightly build --release` — the target is configured in `src/.cargo/config.toml`). Iterate until clean. |
| 3b | medium | sonnet | worktree | Replace the stub `_start` in `src/operations/snapshot/src/main.rs` with the full prologue mirroring `src/operations/map/src/main.rs` lines 1–195. Specifically: doc module comment summarising what the binary does, imports (`shared::{detect_format_from_header, validate_call_table, CallTable, ImageFormat, MapConfig, ...}` → adapt for snapshot: `SnapshotConfig`, `SnapshotEntryRecord`, `SnapshotResult`, `CALL_TABLE_ADDR`, `MAX_SECTOR_SIZE`, `OPERATION_CONFIG_ADDR`, `SCRATCH_MEM_BASE`), the scratch-slot constants from open question 1 (`HEADER_BUF`, `CACHE_BUF_A`, plus `CACHE_BUF_B`, `CACHE_BUF_C`, `CACHE_BUF_D` with `#[allow(dead_code)]` and a `// reserved for phases 5+` comment), the `get_call_table()` helper, the `panic_handler`, and the entry-point skeleton `pub unsafe extern "C" fn _start() -> u64 { let call_table = get_call_table(); validate_call_table!(call_table, "snapshot"); ... }`. Inside `_start`, validate the `SnapshotConfig` per open question 11: `config.is_valid()` and the sector-size invariant. On failure return a `finish` helper (modelled on `map`'s `finish` at lines 116–138) that builds a `SnapshotResult { mode: 0, error: SnapshotResult::ERROR_INVALID_CONFIG, snapshots_emitted: 0, assigned_id_len: 0, assigned_id: [0; 64], magic: SnapshotResult::MAGIC, _pad: 0, _reserved: [0; 104] }` and sends it via `call_table.send_snapshot_result` + `send_complete(b"snapshot\0", bytes_read, false)`. Run `cargo build -p snapshot-op` until clean. |
| 3c | medium | sonnet | worktree | Add the format-detection + qcow2 header-parse pass to `_start` in `src/operations/snapshot/src/main.rs`, immediately after the config validation from step 3b. (i) Read the first sector via `(call_table.read_input_sector)(0, 0, HEADER_BUF as *mut u8, sector_size)`; on `false` return `finish(... ERROR_IO ...)`. (ii) Build a slice `header_bytes = core::slice::from_raw_parts(HEADER_BUF as *const u8, sector_size)`. (iii) Call `detect_format_from_header(header_bytes, sector_size, false)`; if the result is not `ImageFormat::Qcow2` return `finish(... ERROR_UNSUPPORTED_FORMAT ...)` after a `verbose_print(b"snapshot: non-qcow2 source rejected\n\0")`. (iv) Parse the header: `let hdr = match qcow2::QcowHeader::parse(header_bytes) { Some(h) => h, None => return finish(... ERROR_PARSE_FAILED ...) };`. (v) Bind `virtual_size = hdr.virtual_size`, `nb_snapshots = hdr.nb_snapshots`, `snapshots_offset = hdr.snapshots_offset` locally. Run `cargo build -p snapshot-op` until clean. |
| 3d | high | opus | worktree | Add the `MODE_LIST` emit loop to `_start` in `src/operations/snapshot/src/main.rs`, immediately after the qcow2 header parse from step 3c. Dispatch on `config.mode`: for `SnapshotConfig::MODE_LIST` (and only that mode in this step), implement: (i) Early-exit if `nb_snapshots == 0`: build a `SnapshotResult { mode: MODE_LIST, error: OK, snapshots_emitted: 0, ... }` and send via `send_snapshot_result` + `send_complete(b"snapshot\0", bytes_read, true)`. (ii) Otherwise call `qcow2::for_each_snapshot_entry(call_table, 0, nb_snapshots, snapshots_offset, sector_size, input_capacity, CACHE_BUF_A as *mut u8, &mut bytes_read, |entry| -> bool { let record = qcow2::snapshot_entry_to_record(entry, virtual_size); (call_table.send_snapshot_entry)(&record); snapshots_emitted += 1; true })`. The closure captures `&mut snapshots_emitted` and `call_table`. (iii) Check the return value: `true` → all entries visited; `false` → callback stopped early or read error. The closure never returns false in v1, so `false` is always a read error → return `finish(... ERROR_IO ...)`. (iv) On success build the success `SnapshotResult` with `snapshots_emitted` populated and send via `send_snapshot_result` + `send_complete(... true)`. Use opus: the closure captures + lifetime requirements interact with `for_each_snapshot_entry`'s `impl FnMut(&SnapshotEntry) -> bool` and the call-table function pointer; getting that right without a `Cell` or `RefCell` (no_std, no alloc) needs care. Run `cargo build -p snapshot-op` and `make instar` until clean. After the build, check `make check-binary-sizes` reports `snapshot.bin` under 384 KiB. |
| 3e | low | sonnet | worktree | Add stub dispatch arms for `MODE_APPLY`, `MODE_CREATE`, `MODE_DELETE` to `_start` in `src/operations/snapshot/src/main.rs`, immediately after the `MODE_LIST` arm from step 3d. Each stub: (i) calls `(call_table.verbose_print)(b"snapshot: mode N not implemented in v1\n\0")` with the right mode digit, (ii) returns `finish(... ERROR_INVALID_CONFIG ...)` with the requested mode echoed in `SnapshotResult.mode`. Default arm (mode > 3) also returns `ERROR_INVALID_CONFIG`. Confirm the build is clean with `cargo build -p snapshot-op` and `make instar`. |
| 3f | medium | sonnet | worktree | Add `snapshot.bin` to the build scaffolding. (i) `src/build.sh`: add a "Building snapshot operation" block after the "Building map operation" block (lines ~218-235), mirroring the map block exactly — `cd operations/snapshot && cargo +nightly build --release && cd ../..` then the objcopy to `snapshot.bin` then the conditional file check. Add `SNAPSHOT_BIN="snapshot.bin"` to the variable section. Add `cp "$SNAPSHOT_BIN" target/release/` to the copy block at line 264. Add `check_size "snapshot.bin" "target/release/$SNAPSHOT_BIN" "$OP_MAX" || FAILED=1` to the size-check block at the end. Update the trailing summary lines (the multi-line `echo "Binaries..."` block at the end of build.sh) to include snapshot.bin and update the help lines. (ii) `scripts/check-binary-sizes.sh`: add `snapshot` to the `for op in ...` list at line 65. (iii) `Makefile`: add `src/operations/snapshot/Cargo.toml` to `CARGO_TOML_FILES` (at line 732, after `src/operations/commit/Cargo.toml`, *before* `crates/guest-protocol/Cargo.toml`), and add `--exclude snapshot-op` to the `test-rust` target's `cargo test --workspace` exclusion list at line 506 (after `--exclude map-op`). Run `make instar` and `make check-binary-sizes` until clean. The first build will rebuild every operation binary; verify each lands under cap and `snapshot.bin` is in `src/target/release/`. |
| 3g | low | sonnet | worktree | Run `make instar`, `make test-rust`, `make check-binary-sizes`, `make lint`, and `pre-commit run --all-files` from the worktree root. Confirm `snapshot.bin` is under 384 KiB (target: ~80–110 KiB at landing). Confirm no existing operation binary regressed (the build script changes touch only snapshot's block; `info`/`convert`/etc should be byte-identical or close). Stage and present a single commit covering all of steps 3a–3f with the commit-message convention from `~/.claude/CLAUDE.md` (50-char first line ending in `.`, 75-char body wrap, Prompt paragraph, Signed-off-by, Co-Authored-By line that includes model + context window + effort + any other active settings). The commit message should explain that this lands the guest binary for snapshot list mode (qcow2-only) with stubs for the mutating modes that phases 6–8 will replace, and note that the host CLI for list mode arrives in phase 4. |

## Agent guidance

### Execution model

All implementation work for this phase is done by sub-agents,
never in the management session. The management session (this
conversation) is reserved for review and decision-making.
After each step the management session:

1. Reads the actual files that were supposed to change.
2. Confirms no unrelated files were modified.
3. Runs the lint / test commands that the step's brief names.
4. Either commits, asks for a retry with a sharper brief, or
   upgrades the model.

All steps in this phase use `isolation: "worktree"`. The
phase adds a new operation binary; intermediate states between
step 3a (crate exists but does nothing) and step 3d (binary is
functional) are not shippable. A worktree keeps the main
checkout clean if any step has to be retried.

### Model and effort notes

- Steps 3a, 3b, 3c, 3e, 3f, 3g are mechanical extensions of
  well-established patterns (the map binary is the most
  recent and most closely-shaped reference). Sonnet at
  low/medium effort with the briefs above is enough.
- Step 3d is the load-bearing step: the closure captures
  inside `for_each_snapshot_entry` interact with the call-
  table function pointer and the converter's `header_virtual_size`
  parameter in a way that has to be right first try (no_std,
  no alloc means no fallback patterns). Use opus.

### Management session review checklist

After each step:

- [ ] Read the changed files — don't trust the agent's
      summary.
- [ ] No unrelated files modified.
- [ ] `cargo build -p snapshot-op` (every step).
- [ ] `make instar` (steps 3d, 3f, 3g).
- [ ] `make check-binary-sizes` (steps 3f, 3g); confirm
      `snapshot.bin` lands under 384 KiB and no other
      operation binary regressed.
- [ ] `make lint` (step 3g).
- [ ] `pre-commit run --all-files` (step 3g).
- [ ] The `_start` flow dispatches correctly on
      `config.mode` and only `MODE_LIST` has a real
      implementation (other modes return stub errors with a
      verbose_print marker).
- [ ] The scratch-slot constants are laid out for phase 5+
      (HEADER + 4 cache buffers, dead_code allowed for the
      unused ones).
- [ ] The qcow2 header parse uses `QcowHeader::parse`
      directly, not `Qcow2State::init` (open question 2).
- [ ] No incompatible-feature refusal (open question 4) —
      list mode works on any qcow2.

### Pre-commit verification ritual (step 3g)

The single commit at the end of step 3g must build cleanly
through the entire stack:

1. `make instar` — full host VMM + core + all guest
   operation binaries including the new `snapshot.bin`.
2. `make test-rust` — workspace unit tests; `snapshot-op` is
   excluded (no inline tests) following the existing
   convention for operation crates.
3. `make check-binary-sizes` — confirm `snapshot.bin` lands
   under 384 KiB and no other operation binary regressed.
4. `make lint` / `pre-commit run --all-files`.

If any of these fail, fix the failure in the *same* commit
(we are not amending a published commit; this is the
original commit's pre-push verification). Do not split into
a follow-up.

## Administration and logistics

### Success criteria

Phase 3 is complete when:

* All seven steps above land in one commit on the `snapshot`
  branch.
* `src/operations/snapshot/{Cargo.toml,linker.ld,src/main.rs}`
  exist and build into `snapshot.bin` under 384 KiB.
* `src/Cargo.toml` lists `operations/snapshot` as a workspace
  member.
* `src/build.sh` builds, objcopies, copies, and size-checks
  `snapshot.bin`.
* `scripts/check-binary-sizes.sh` includes `snapshot` in its
  binary list.
* `Makefile` `CARGO_TOML_FILES` includes the new Cargo.toml
  and `test-rust` excludes `snapshot-op`.
* `make instar`, `make test-rust`, `make check-binary-sizes`,
  `make lint`, `pre-commit run --all-files` all pass.
* The verbose-trace formatter arms from phase 1 print
  `Payload::SnapshotEntry` and `Payload::SnapshotResult`
  correctly (already tested by phase 1; no action needed
  here).

### Future work created by this phase

- **Smoke test against a real qcow2 fixture.** Deferred to
  phase 4 (host CLI), which provides the natural surface to
  drive the guest end-to-end.
- **Stub mode error code.** Phase 3 reuses
  `ERROR_INVALID_CONFIG` for the stubbed mutating modes
  (open question 3). Phases 6/7/8 replace each stub with a
  real planner; the error code stays available for genuine
  config validation failures.
- **Compressed-cluster / encryption / external-data-file
  handling in mutating modes.** Phase 3 (list mode) does
  not refuse on these. Mutating phases 6/7/8 will add the
  refusal alongside the real planners.

### Bugs fixed during this work

This section will list any bugs encountered during
development that we fix in passing. The phase-2 streaming
primitive and the phase-1 ABI have unit-test coverage;
phase 3's binary is thin glue. Any surprising behaviour
would surface during `make instar` or the binary-size check.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not**
added to `docs/plans/order.yml` per the convention. The
master plan links to it from the Execution table at
`docs/plans/PLAN-snapshot.md:866-882`; step 3g updates that
row to point at this file.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
