# PLAN-bitmap phase 01: qcow2 bitmap-structure parsing

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the qcow2 header /
header-extension parsers in `src/crates/qcow2/src/lib.rs`, the
big-endian byte-reading helpers in `src/shared/src/lib.rs`, the
`no_std` streaming-enumeration idiom used for snapshots
(`for_each_snapshot_entry`), and the inline `#[cfg(test)]` fixture
style), and ground your answers in what the code actually does
today. Do not speculate about the codebase when you could read it
instead. Where a question touches on the qcow2 persistent-dirty-
bitmap on-disk format, research the qcow2 spec
(`docs/interop/qcow2.txt`) and qemu's `block/qcow2-bitmap.c`
rather than guessing. Flag any uncertainty explicitly.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-bitmap-phase-NN-<descriptive>.md`. The master plan is
[PLAN-bitmap.md](/components/instar/plans/PLAN-bitmap/). This is the first of ten phases.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

This phase lands the **read-side foundation** for `instar bitmap`:
the `no_std` qcow2 crate parsers, encoders, and geometry helpers
for the persistent-dirty-bitmap structures, plus a streaming
enumerator for the bitmap directory. It is a pure library phase —
no ABI, no guest op, no host CLI. Everything in phases 2–10 (the
planner, the guest op, info-equivalence testing) builds on the
primitives this phase freezes.

It is the *extra* phase relative to the amend plan: amend reused
the existing header parser, whereas the qcow2 bitmaps extension,
bitmap directory, bitmap table, and bitmap-data geometry are
**entirely net-new to instar** — nothing parses
`0x23852875` today. (The "bitmap" code that exists —
`SubclusterBitmapStatus` / `validate_subcluster_bitmap` at
`src/crates/qcow2/src/lib.rs:1282-1358`, and the overlap-detection
bitmap in `src/shared/src/bitmap.rs` — is the unrelated
*extended-L2 subcluster* and *check-overlap* machinery. **Do not
modify or confuse it with persistent dirty bitmaps.**)

Grounding (verified against the current tree on the `bitmap`
branch):

- **Header-extension walk.** `parse_header_extensions(header:
  &[u8], parsed: &QcowHeader) -> HeaderExtensionResults`
  (`src/crates/qcow2/src/lib.rs:471-521`) starts the walk at
  `header_length` (re-read via `be_u32(header,
  HEADER_LENGTH_OFFSET)` at `:487`), loops while `ext_offset + 8 <=
  header.len()`, reads `(type, len)`, breaks on `EXT_END`,
  bounds-checks `ext_offset + 8 + ext_len > header.len()`,
  dispatches with an `if / else if` chain over `EXT_BACKING_FORMAT`
  / `EXT_EXTERNAL_DATA_FILE` / `EXT_ENCRYPT_HEADER` (`:502-513`),
  and advances by `padded_len = (ext_len + 7) & !7`. **There is no
  final `else`: an unknown extension (including `0x23852875`) is
  silently skipped.** This is the hook point.
- **`HeaderExtensionResults`** (`src/crates/qcow2/src/lib.rs:445-461`)
  is `#[derive(Debug, PartialEq)]` and returned by value (no heap).
  It currently carries `backing_format`, `data_file_name_offset`,
  `data_file_name_len`, `luks_header_offset`, `luks_header_len`.
  New bitmap fields are added here.
- **Autoclear is not on `QcowHeader`.** `QcowHeader`
  (`src/crates/qcow2/src/lib.rs:292-323`) has no
  `autoclear_features` field; the only consumer that needs it reads
  it raw — `src/operations/snapshot/src/main.rs:451-460` does
  `be_u64(header, AUTOCLEAR_FEATURES_OFFSET) & 0x1`. The constant
  `AUTOCLEAR_FEATURES_OFFSET = 88` already exists
  (`src/crates/qcow2/src/lib.rs:146`). The bitmaps extension is
  consistency-guarded by **autoclear bit 0**, so the extension
  parse must read that bit from the header buffer.
- **Existing `EXT_*` constants** (`src/crates/qcow2/src/lib.rs:61-66`):
  `EXT_BACKING_FORMAT = 0xE2792ACA`, `EXT_EXTERNAL_DATA_FILE =
  0x44415441`, `EXT_ENCRYPT_HEADER = 0x0537BE77`, `EXT_END = 0`.
  `EXT_BITMAPS = 0x23852875` is added here.
- **Byte helpers** live in the shared crate
  (`src/shared/src/lib.rs:74-99`): `be_u16` (`:76`), `be_u32`
  (`:82`), `be_u64` (`:88`); `write_be_u16/u32/u64` (`:130-142`).
  They **do not bounds-check** — callers length-guard first. The
  qcow2 crate imports only `be_u32, be_u64` today
  (`src/crates/qcow2/src/lib.rs:27`); this phase adds `be_u16`
  (and the `write_be_*` it needs for the encoders).
- **`no_std` variable-length idiom.** The crate is `#![no_std]`
  (`:10`) with no heap on the parse path. The directory is a
  variable count of variable-length entries — the established
  pattern for exactly this shape is the snapshot **streaming
  enumerator** `for_each_snapshot_entry(...) -> bool`
  (`src/crates/qcow2/src/lib.rs:957-968`): it reads each record
  into fixed stack scratch via a cached byte reader, decodes a
  fixed head + variable-length strings into an entry with **inline
  fixed-capacity buffers** (`SnapshotEntry { id: [u8;64], name:
  [u8;256], ... }`, `:663-712`), invokes a `FnMut(&Entry) -> bool`
  callback, and returns `false` on read error / oversized record.
  The bitmap enumerator mirrors this exactly.
- **Test + fixture style.** Inline `#[cfg(test)] mod tests`
  (`src/crates/qcow2/src/lib.rs:3026+`). Fixtures are hand-built
  in-memory byte buffers: `make_qcow2_header() -> [u8; 512]`
  (`:3069-3094`) zeroes a buffer and pokes fields via
  `copy_from_slice(&val.to_be_bytes())`; the extension test
  `header_extensions_with_backing_format` (`:3269-3288`) appends an
  extension body after `header_length` and an `EXT_END`
  terminator. New parser tests follow this verbatim.
- **Feature gate.** Bitmap *parsing* is always-on (mirror
  `parse_header_extensions`), **not** behind `feature = "create"`
  (which gates only `src/crates/qcow2/src/create.rs` image
  writing). So new tests in the always-on path run under the plain
  workspace `cargo test`; `cargo test -p qcow2 --features create`
  (`Makefile:511`) must still pass.

## Mission

Add a self-contained `no_std` bitmap module to the qcow2 crate
exposing the parse/encode/geometry primitives and the directory
enumerator that the rest of the plan consumes. Concretely:

1. **A new module `src/crates/qcow2/src/bitmap.rs`** (`pub mod
   bitmap;` in `lib.rs`, always-on, not feature-gated), holding the
   bitmap constants, structs, pure parsers, encoders, geometry
   helpers, and the streaming enumerator, with its own inline
   `#[cfg(test)] mod tests`. Rationale: it is large enough to
   warrant a module, and keeping it self-contained avoids bloating
   the 6300-line `lib.rs`. `EXT_BITMAPS` is the one constant that
   also lives in `lib.rs`'s `EXT_*` block (next to `EXT_END`) for
   discoverability and is referenced by `parse_header_extensions`.

2. **Bitmaps header-extension parse** wired into
   `parse_header_extensions`: a new `else if ext_type ==
   EXT_BITMAPS` arm that parses the 24-byte body
   (`bitmap::parse_bitmaps_extension`) and records, into new
   `HeaderExtensionResults` fields, `nb_bitmaps` (u32),
   `bitmap_directory_size` (u64), `bitmap_directory_offset` (u64,
   cluster-aligned), and a **consistency flag**: the extension is
   usable only if autoclear bit 0 is *set*. Per the spec, an
   extension present with the autoclear bit *clear* must be treated
   as inconsistent/absent — surface this as a distinct field
   (e.g. `bitmaps_present: bool` + `bitmaps_inconsistent: bool`,
   final names a phase decision) so callers can both detect "has
   bitmaps" and "header lies about bitmaps". `parse_header_extensions`
   reads `be_u64(header, AUTOCLEAR_FEATURES_OFFSET) & 0x1` itself.

3. **Directory-entry parse + encode.** A `BitmapDirEntry` with
   inline name buffer and the 24-byte fixed-head fields
   (`bitmap_table_offset` u64, `bitmap_table_size` u32, `flags`
   u32, `bitmap_type` u8, `granularity_bits` u8, `name_size` u16,
   `extra_data_size` u32), plus helpers `is_enabled()` (auto bit),
   `is_in_use()`, `granularity()` = `1 << granularity_bits`,
   `name_bytes()`. `parse_bitmap_dir_entry(buf) -> Option<(entry,
   entry_size)>` validates exactly as qemu's `check_dir_entry`:
   reject `flags & BME_RESERVED_FLAGS` (i.e. any bit outside 0/1 —
   **qemu rejects bit 2 `extra_data_compatible` too**), reject
   `bitmap_type != BT_DIRTY_TRACKING_BITMAP (1)`, reject
   `extra_data_size != 0`, reject `name_size == 0` or `> 1023`;
   compute `entry_size = round_up(24 + extra_data_size + name_size,
   8)` and bounds-check it against `buf`; the name field sits at
   `entry + 24 + extra_data_size` and is **not NUL-terminated**.
   `serialize_bitmap_dir_entry(entry, out) -> Option<usize>` is the
   inverse (24-byte head + name + zero-pad to 8), needed by the
   phase-3 planner but landed and tested here as a round-trip
   partner.

4. **Table-entry decode + encode + validate.** A
   `BitmapTableEntry` enum `{ AllZeroes, AllOnes, Allocated(u64) }`
   with `decode_bitmap_table_entry(raw: u64)`,
   `encode_bitmap_table_entry(entry) -> u64`, and
   `validate_bitmap_table_entry(raw) -> bool` enforcing the masks:
   `BME_TABLE_ENTRY_OFFSET_MASK = 0x00ff_ffff_ffff_fe00`,
   `BME_TABLE_ENTRY_RESERVED_MASK = 0xff00_0000_0000_01fe` (must be
   zero), `BME_TABLE_ENTRY_FLAG_ALL_ONES = 1 << 0`. Semantics:
   offset (bits 9-55) non-zero ⇒ `Allocated`; offset zero ⇒
   `AllOnes` if bit 0 set else `AllZeroes`; any reserved bit set ⇒
   invalid.

5. **Geometry + validation helpers** matching qemu exactly:
   - `default_granularity(cluster_size: u64) -> u64 =
     min(65536, max(4096, cluster_size))`
     (qemu `bdrv_get_default_bitmap_granularity`).
   - `granularity_bits_valid(bits: u8) -> bool` = `9..=31`
     (`BME_MIN/MAX_GRANULARITY_BITS`).
   - `bitmap_bytes_needed(virtual_size, granularity) -> u64` =
     `div_round_up(div_round_up(virtual_size, granularity), 8)`
     (qemu `get_bitmap_bytes_needed`).
   - `bitmap_table_size_entries(serialized_bytes, cluster_size) ->
     u64` = `div_round_up(serialized_bytes, cluster_size)`
     (one table entry per data cluster).
   Use `checked_*` / saturating arithmetic — these run on
   attacker-controlled header values and must not panic (the crate
   is fuzzed; see phase 9).

6. **Streaming directory enumerator.**
   `for_each_bitmap_entry(...) -> bool` mirroring
   `for_each_snapshot_entry`'s signature and I/O approach
   (`src/crates/qcow2/src/lib.rs:957-968`): given the cached byte
   reader, `bitmap_directory_offset`, `bitmap_directory_size`, and
   `nb_bitmaps`, read each directory entry's fixed head into stack
   scratch, decode via `parse_bitmap_dir_entry`, copy the name into
   the entry's inline buffer, invoke `FnMut(&BitmapDirEntry) ->
   bool` (return `false` to stop), and advance by `entry_size`.
   Return `false` on read error, malformed entry, oversized name,
   or running past `bitmap_directory_size` / `nb_bitmaps`. This is
   what the guest op (phase 4) and any info listing use to
   enumerate existing bitmaps without heap.

### Constants to define (in `bitmap.rs`, with `EXT_BITMAPS` also in `lib.rs`)

```
EXT_BITMAPS: u32                    = 0x23852875
AUTOCLEAR_BITMAPS_BIT: u64          = 1 << 0   // autoclear_features bit 0
BME_FLAG_IN_USE: u32                = 1 << 0
BME_FLAG_AUTO: u32                  = 1 << 1   // "enabled"
BME_FLAG_EXTRA_DATA_COMPATIBLE: u32 = 1 << 2   // spec only; qemu rejects
BME_RESERVED_FLAGS: u32             = 0xFFFF_FFFC  // bits 2..=31
BT_DIRTY_TRACKING_BITMAP: u8        = 1
BME_MIN_GRANULARITY_BITS: u8        = 9
BME_MAX_GRANULARITY_BITS: u8        = 31
BME_MAX_NAME_SIZE: usize            = 1023
QCOW2_MAX_BITMAPS: u32              = 65535
QCOW2_MAX_BITMAP_DIRECTORY_SIZE: u64 = 1024 * 65535
BME_MAX_TABLE_SIZE: u64             = 0x8000000
BME_MAX_PHYS_SIZE: u64              = 0x20000000  // 512 MiB
BME_TABLE_ENTRY_OFFSET_MASK: u64    = 0x00ff_ffff_ffff_fe00
BME_TABLE_ENTRY_RESERVED_MASK: u64  = 0xff00_0000_0000_01fe
BME_TABLE_ENTRY_FLAG_ALL_ONES: u64  = 1 << 0
```

The inline name buffer is `[u8; 1024]` (1023 max + headroom);
`BitmapDirEntry` is ~1064 bytes — fine for one stack-resident
in-flight entry (snapshot's is similar).

## Out of scope for this phase

- The bitmap *planner* (alloc/free, refcounts, directory rewrite,
  autoclear dance) — phase 3 (`src/crates/bitmap/`). This phase
  only **reads** and provides the encode partners for round-trip
  testing.
- Any ABI / guest op / host CLI — phases 2, 4, 5.
- **`instar info` bitmap listing** — see step 1e: included as an
  *optional, recommended-deferred* step. The pure parsing
  primitives land here regardless; wiring them through the info
  ABI/proto/host-render is a separate surface (the `#[repr(C)]`
  `shared::Qcow2Info` has no list field, the wire `Qcow2Info`
  caps at `MAX_MESSAGE_SIZE = 2200`, and the host JSON renderer
  hand-emits conditional commas). The qemu oracle is sufficient
  for phase-7 correctness, so info listing is not on the critical
  path. **Recommend deferring 1e** unless the operator wants the
  `qemu-img info` parity win now.

## Resolved open questions (from the master plan)

- **OQ3 (info listing):** parsing lands here; the info-output
  surface is the optional step 1e, recommended deferred. The qemu
  oracle covers phase-7 testing without it.
- **OQ4 (bitmap preservation by other ops):** **investigated and
  largely answered during planning** — see "Findings: pre-existing
  bitmap preservation" below. Step 1d confirms the one remaining
  unknown (`snapshot`) and records the verdict; the `resize`
  data-loss fix is logically separable and is *not* folded into
  this phase (recommended as a dedicated follow-up, operator
  decision).
- **OQ7 (granularity/geometry formulas):** resolved — the four
  helpers in Mission §5 reproduce qemu's
  `bdrv_get_default_bitmap_granularity`,
  `get_bitmap_bytes_needed`, and `size_to_clusters` exactly, with
  panic-free arithmetic.

## Findings: pre-existing bitmap preservation (OQ4)

Planning-time investigation of every in-place qcow2 mutation op's
header-write path. The load-bearing helper is `qcow2::build_header`
(`src/crates/qcow2/src/create.rs:329-410`): it `fill(0)`s a fresh
v3 header, writes only `EXT_BACKING_FORMAT`/`EXT_ENCRYPT_HEADER`/
`EXT_END`, and leaves `autoclear_features` = 0 — it **cannot carry
forward an unknown `0x23852875` extension**.

| Op | Verdict | Evidence |
|----|---------|----------|
| **resize** | **UNSAFE — drops bitmaps + clears autoclear on every path** | `src/crates/resize/src/qcow2.rs` calls `build_header` and emits a full-cluster `Write { byte_offset: 0 }` in header-only grow (`:513-527`), grow-with-L1 (`:652-693`), table-relocate (`:1108-1255`), and shrink (`:1238-1255`). `Qcow2ResizeOpts` has no bitmaps field. |
| **amend** | **Effectively safe for bitmaps** | Same-version v3 change is a selective 8-byte write of `compatible_features` (`src/crates/amend/src/qcow2.rs:127-143`) — extension area untouched. Cross-version rebuild *relocates* the whole extension chain verbatim (`:268-270`) but zeros autoclear (`:255`); since bitmaps require v3 and a v2→v3 upgrade starts with no bitmaps, there is nothing to lose. (Document the autoclear-zeroing as a latent hazard if amend ever runs v3→v3 through that path.) |
| **rebase** | **Safe** | Writes only backing-path bytes + a 12-byte field at offset 8 (`src/crates/rebase/src/qcow2.rs:300-323`); never touches extensions/autoclear. |
| **commit** | **Safe** | Only `Write` patches to backing-image refcount/L1/L2; no `build_header`, no header-extension rewrite (`src/crates/commit/src/qcow2.rs`, `lib.rs:119-149`). (Like qemu, commit does not migrate bitmaps — acceptable.) |
| **check --repair** | **Safe** | RMW of the 8-byte `incompatible_features` word only, to set/clear `INCOMPAT_CORRUPT` (`src/operations/check/src/main.rs:3800-3826`, `:4132-4165`); no extension/autoclear touch. |
| **snapshot** | **SAFE (and gated)** — *resolved in step 1d* | Never calls `build_header` (grep-confirmed). All header mutation is a targeted 12-byte RMW of `nb_snapshots`/`snapshots_offset` at offset 60 (`src/operations/snapshot/src/main.rs:1168-1171` create, `:1527-1530` delete; apply writes no header bytes), preserving offset-88 autoclear and any offset-≥104 extension. Belt-and-suspenders: all three modes call `mutating_feature_gates` (`main.rs:454-460`), which *refuses* any image with the autoclear bitmaps bit set (`ERROR_UNSUPPORTED_FEATURE`). |

**Conclusion (step 1d resolved):** there is a real, pre-existing
**bitmap-data-loss bug in `resize`** (full header rebuild via
`build_header` drops the bitmaps extension and clears autoclear on
every path) and a latent autoclear-zeroing in cross-version
`amend`; `rebase`/`commit`/`check --repair` are safe, and
`snapshot` is safe *and* explicitly gates bitmap images out.
`snapshot`'s autoclear-bit gate is the model for the `resize` fix.
The `resize` defect was **fixed on this branch** (commit `5892e1c`):
the resize planner now refuses images with the bitmaps autoclear
bit set, matching `snapshot`. See the master plan's "Defects found
during this work" section for details.

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Create `src/crates/qcow2/src/bitmap.rs` (`pub mod bitmap;` in `lib.rs`, always-on). Define all bitmap constants from the master plan / this plan's constants block. Add `EXT_BITMAPS = 0x23852875` to the `EXT_*` group in `lib.rs:61-66` as well. Add the pure `parse_bitmaps_extension(data: &[u8]) -> Option<BitmapsExtension>` (24-byte body: `nb_bitmaps` u32 @0, reserved u32 @4, `bitmap_directory_size` u64 @8, `bitmap_directory_offset` u64 @16, all big-endian; reject `nb_bitmaps == 0` or `> QCOW2_MAX_BITMAPS`, dir size `> QCOW2_MAX_BITMAP_DIRECTORY_SIZE`, dir offset not cluster-aligned). Extend `HeaderExtensionResults` (`lib.rs:445-461`) with `bitmaps_present: bool`, `bitmaps_inconsistent: bool`, `bitmap_nb_bitmaps: u32`, `bitmap_directory_size: u64`, `bitmap_directory_offset: u64`. In `parse_header_extensions` (`lib.rs:471-521`) add an `else if ext_type == EXT_BITMAPS` arm that calls `parse_bitmaps_extension` on the body slice; set the consistency flags by reading `be_u64(header, AUTOCLEAR_FEATURES_OFFSET) & AUTOCLEAR_BITMAPS_BIT` (extension valid only if the bit is set — present-but-bit-clear ⇒ `bitmaps_inconsistent = true`, `bitmaps_present = false`). Add `be_u16` to the `use shared::{...}` import. Inline tests: extension present+autoclear-set, present+autoclear-clear (inconsistent), absent, malformed/oversized body, v2 image (no extensions walked). Mirror the fixture style of `header_extensions_with_backing_format` (`lib.rs:3269`). Constraint: `no_std`, panic-free (use length guards before every `be_*`). |
| 1b | high | opus | none | In `bitmap.rs`, implement the directory-entry and table-entry codecs + geometry helpers. `BitmapDirEntry { bitmap_table_offset: u64, bitmap_table_size: u32, flags: u32, bitmap_type: u8, granularity_bits: u8, name_size: u16, extra_data_size: u32, name: [u8;1024] }` with `is_enabled()` (`flags & BME_FLAG_AUTO`), `is_in_use()`, `granularity()`, `name_bytes()`. `parse_bitmap_dir_entry(buf) -> Option<(BitmapDirEntry, usize)>` validating exactly like qemu `check_dir_entry`: `flags & BME_RESERVED_FLAGS == 0`, `bitmap_type == 1`, `extra_data_size == 0`, `1 <= name_size <= 1023`, `entry_size = round_up(24 + extra_data_size + name_size, 8)` bounds-checked, name at `24 + extra_data_size`. `serialize_bitmap_dir_entry(&entry, out) -> Option<usize>` (inverse, zero-padded to 8). `BitmapTableEntry { AllZeroes, AllOnes, Allocated(u64) }` with `decode_bitmap_table_entry`/`encode_bitmap_table_entry`/`validate_bitmap_table_entry` per the masks in this plan. Geometry: `default_granularity`, `granularity_bits_valid`, `bitmap_bytes_needed`, `bitmap_table_size_entries` (formulas in Mission §5), all using `checked_*`/saturating. Inline tests: dir-entry parse↔serialize round-trip; reject reserved flag bit / type≠1 / extra_data≠0 / name_size 0 and 1024; padding correctness for several name lengths; table-entry all-zeroes/all-ones/allocated/ reserved-bit-set; geometry helpers against hand-computed values incl. the qemu default (64 KiB cluster ⇒ 65536 ⇒ granularity_bits 16) and overflow inputs (u64::MAX virtual size must not panic). `no_std`, panic-free. |
| 1c | high | opus | none | In `bitmap.rs`, implement `for_each_bitmap_entry(...) -> bool`, the streaming directory enumerator, mirroring `for_each_snapshot_entry` (`lib.rs:957-968`) — same cached-reader I/O style, same `FnMut(&BitmapDirEntry) -> bool` callback returning `false` to stop, same fixed stack scratch + inline-name approach. Inputs: the byte reader, `bitmap_directory_offset`, `bitmap_directory_size`, `nb_bitmaps`. Read each entry's 24-byte head into scratch, decode via `parse_bitmap_dir_entry`, copy the name in, dispatch the callback, advance by `entry_size`; stop after `nb_bitmaps` entries or `bitmap_directory_size` bytes. Return `false` on any read error / malformed entry / overrun. Tests: drive it with an in-memory `read` closure over a hand-built directory buffer containing 0, 1, and several entries (incl. a disabled one and varied name lengths); assert names/flags/granularity; assert early-stop on callback `false`; assert clean failure on a truncated directory and on `nb_bitmaps` exceeding the bytes available. `no_std`, panic-free. |
| 1d | medium | sonnet | none | Resolve the one open OQ4 item: read `src/crates/snapshot/src/qcow2.rs` (and its guest op) and determine whether snapshot create/delete/apply rewrites the qcow2 header via `build_header` (would drop the bitmaps extension + autoclear bit) or via selective field writes to `nb_snapshots`/`snapshots_offset` (offsets 60/64) that preserve extensions. Quote the relevant code. Append the verdict to the master plan's "Findings: pre-existing bitmap preservation" table and its "Bugs fixed during this work" section, and (if snapshot or the confirmed `resize` bug warrants it) draft a one-paragraph tracker-issue description. No code changes — investigation + doc update only. |
| 1e | high | opus | worktree | **OPTIONAL — recommend deferring; only run if the operator opts in.** Surface bitmaps in `instar info` to match `qemu-img info`. Add a guest-side directory read (using 1c's `for_each_bitmap_entry` via the info op's input-device reader, analogous to `read_backing_file` at `src/operations/info/src/main.rs:806`) in `parse_qcow2_header` (`:769-856`); carry a bounded bitmap list to the host — a new `repeated Qcow2BitmapInfo { string name; uint32 granularity; repeated string flags; }` in `crates/guest-protocol/proto/guest.proto` (the `#[repr(C)] shared::Qcow2Info` cannot hold a list; watch `MAX_MESSAGE_SIZE = 2200`); render it in the host human block (`src/vmm/src/main.rs:1212-1259`, between `lazy refcounts` and `refcount bits`) and JSON (`:1485-1543`, `"bitmaps"` array after `"lazy-refcounts"`, before `"refcount-bits"`), exactly matching qemu's nesting (enabled ⇒ `flags: [auto]`, disabled ⇒ empty `flags`). Update `tests/helpers/info_json.py` normalisation to allow the `bitmaps` array. This is an ABI+proto+render change — isolate in a worktree; if it pressures `MAX_MESSAGE_SIZE` or the binary-size caps, bail and defer per the master plan. |

## Verification (management-session review checklist)

After each step, verify in the management session:

- [ ] The intended files changed and no others (read them).
- [ ] `cargo test -p qcow2` passes (covers the new always-on
      bitmap tests).
- [ ] `cargo test -p qcow2 --features create` still passes
      (`Makefile:511` — the create-gated tests must not regress).
- [ ] `make test-rust` passes.
- [ ] `make lint` is clean.
- [ ] `make instar` builds and `make check-binary-sizes` passes —
      adding parsing code to the qcow2 crate could grow any guest
      binary that links it; dead-code elimination should strip the
      as-yet-uncalled functions, but confirm `info.bin` /
      `snapshot.bin` and `core.bin` stay within budget (especially
      if step 1e runs and `info.bin` actually calls the new code).
- [ ] `pre-commit run --all-files` passes.
- [ ] Parsers are panic-free on adversarial input (length guards
      before every `be_*`; `checked_*`/saturating arithmetic) —
      this code is fuzzed in phase 9 and must not panic on a
      malformed header/directory.
- [ ] Commit message follows project conventions (Co-Authored-By
      with model / context window / effort level / settings;
      `Signed-off-by`; `Prompt:` paragraph).

## Success criteria

This phase is complete when:

- `src/crates/qcow2/src/bitmap.rs` exists and exports the
  constants, `BitmapsExtension`/`parse_bitmaps_extension`,
  `BitmapDirEntry` with `parse_bitmap_dir_entry` /
  `serialize_bitmap_dir_entry`, `BitmapTableEntry` with its
  decode/encode/validate, the four geometry helpers, and
  `for_each_bitmap_entry` — all `no_std` and panic-free.
- `parse_header_extensions` recognises `EXT_BITMAPS`, populates the
  new `HeaderExtensionResults` bitmap fields, and correctly marks
  an autoclear-bit-clear extension as inconsistent.
- The bitmap codecs round-trip (parse → serialize → parse) for a
  representative set of entries, and reject every malformed case
  qemu's `check_dir_entry` rejects.
- The geometry helpers reproduce qemu's
  default-granularity / bytes-needed / table-size formulas and do
  not panic on `u64::MAX`-scale inputs.
- All Rust tests pass (`make test-rust`, plus `cargo test -p qcow2
  --features create`); `make lint`, `make instar`,
  `make check-binary-sizes`, and `pre-commit run --all-files` are
  clean.
- The OQ4 findings table is complete (snapshot resolved) in the
  master plan, with the `resize` bitmap-data-loss bug recorded for
  a follow-up.
- (If step 1e ran) `instar info --output=json` on a
  bitmap-bearing qcow2 matches `qemu-img info --output=json`'s
  `bitmaps` array, and the human output matches qemu's nesting.

## Back brief

Before executing any step, the executing agent should back brief
the operator on its understanding of this phase and how the work
aligns with it — in particular, that this is a **read-side
library phase** (no planner, no guest op, no host CLI), that the
`SubclusterBitmapStatus` / overlap-bitmap code is a different
concept that must not be touched, and that step 1e is optional and
recommended deferred.
