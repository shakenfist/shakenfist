# PLAN-amend phase 02: qcow2 amend planner crate

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the planner-crate
conventions in `src/crates/{rebase,resize}/`, the qcow2 header
parser/writer in `src/crates/qcow2/`, the `no_std` + scratch-buffer
discipline, the patch/plan/error type families), and ground your
answers in what the code actually does today. Do not speculate
about the codebase when you could read it instead. Where a question
touches the qcow2 on-disk format (v2 vs v3 fixed-header layout,
header extensions, `refcount_order`, the `compatible`/
`incompatible`/`autoclear` feature words, lazy refcounts, the
backing-file string), research the qcow2 spec and qemu's
`qcow2_update_header` / `qcow2_amend_options` as needed. Flag any
uncertainty explicitly rather than guessing.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-amend-phase-NN-<descriptive>.md`. The master plan is
[PLAN-amend.md](/components/instar/plans/PLAN-amend/); phase 1 (the shared ABI) is
[PLAN-amend-phase-01-abi.md](/components/instar/plans/PLAN-amend-phase-01-abi/) and is
landed. This is the second of nine.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what changed
and why.

## Situation

Phase 1 froze the ABI: `AmendConfig` (128 B) and `AmendResult`
(64 B) in `src/shared/src/lib.rs`, with the flag set
(`FLAG_SET_COMPAT`/`FLAG_COMPAT_V3`, `FLAG_SET_LAZY`/`FLAG_LAZY_ON`,
`FLAG_QUIET`), the action set (`ACTION_NOOP`/`ACTION_AMENDED`), and
the full `ERROR_*` enum (`ERROR_OK` .. `ERROR_INTERNAL_OVERFLOW`).
No planner, guest, or CLI exists yet.

This phase builds **`src/crates/amend/`** — a pure `no_std` planner
that takes the qcow2 image's first header cluster plus the
requested option changes and emits a byte-level patch list (no
I/O). Phase 3 (the guest op) reads the cluster, calls this planner,
applies the patches, and reports the result; phase 4 (host CLI)
populates `AmendConfig`. The planner is the **correctness core** of
amend: it owns every refusal decision and the byte-exact header
rewrite, so it is planned at high effort and gets exhaustive inline
unit tests.

**What changes on the wire, per operation** (grounding the design):

- **`lazy_refcounts` toggle (no version change).** Only
  `compatible_features` at offset 80 changes (set/clear
  `COMPAT_LAZY_REFCOUNTS = 1 << 0`). The fixed-header length is
  unchanged, so nothing after it moves. → a single 8-byte
  selective patch.
- **`compat` upgrade v2 → v3.** The fixed header grows from 72 to
  104 bytes. Fields set/written: `version` (offset 4) = 3,
  `incompatible_features` (72) = 0, `compatible_features` (80) =
  lazy bit, `autoclear_features` (88) = 0, `refcount_order` (96) =
  `log2(refcount_bits)` (4 for the v2-mandatory 16-bit),
  `header_length` (100) = 104. **Any header extensions and the
  backing-file string that sat at offset 72 in the v2 image must
  move up to offset 104**, and `backing_file_offset` (8) must be
  bumped by the +32 shift.
- **`compat` downgrade v3 → v2.** The fixed header shrinks from 104
  (or more) to 72 bytes. `version` (4) = 2; the v3-only words at
  72..104 cease to exist (they become the start of the extension
  area). Extensions and the backing string that sat at
  `header_length` must move **down** to offset 72, and
  `backing_file_offset` bumped by the negative shift. Refused
  unless the image is downgrade-safe (see Open question 2).

The grounding the implementer must build on (all line numbers
verified on the `amend` branch):

- **qcow2 header parser + constants** in
  `src/crates/qcow2/src/lib.rs`: offset constants at lines 44–146
  (`VERSION_OFFSET=4`, `BACKING_FILE_OFFSET_OFFSET=8`,
  `BACKING_FILE_SIZE_OFFSET=16`, `INCOMPATIBLE_FEATURES_OFFSET=72`,
  `COMPATIBLE_FEATURES_OFFSET=80`, `AUTOCLEAR_FEATURES_OFFSET=88`,
  `REFCOUNT_ORDER_OFFSET=96`, `HEADER_LENGTH_OFFSET=100`,
  `COMPRESSION_TYPE_OFFSET=104`, `V2_HEADER_EXTENSION_OFFSET=72`,
  `QCOW2_HEADER_LENGTH_V3=104`); feature bits at 68–76
  (`INCOMPAT_DIRTY/CORRUPT/EXTERNAL_DATA/COMPRESSION/EXTENDED_L2`,
  `COMPAT_LAZY_REFCOUNTS`); extension type constants at 60–66
  (`EXT_BACKING_FORMAT=0xE2792ACA`, `EXT_EXTERNAL_DATA_FILE=
  0x44415441`, `EXT_ENCRYPT_HEADER=0x0537BE77`, `EXT_END=0`).
  `QcowHeader` struct at 292–323 (carries `version`,
  `refcount_bits`, `incompatible_features`, `compatible_features`,
  `dirty`, `corrupt`, `has_external_data`, `extended_l2`,
  `lazy_refcounts`, `backing_file_offset`, `backing_file_size`,
  `cluster_size`); `QcowHeader::parse` at 335–411 (offsets 0..72
  are parsed identically for v2 and v3; v3-only fields read only
  when `version >= 3`). `parse_header_extensions` at 471–521
  **only walks extensions for `version >= 3`** (returns empty for
  v2) — the planner needs a *version-agnostic* extension-area
  length walk (see step 2c).
- **The header writer `build_header()`** at
  `src/crates/qcow2/src/create.rs:329` **always emits a v3 header**
  (hardcodes version 3, `header_length=104`, the v3 feature words,
  extensions at offset 104) and rebuilds the extension set from
  `BuildHeaderOptions` (backing-file/backing-format/luks only). It
  **cannot emit v2** and **does not preserve arbitrary existing
  extensions** (e.g. a feature-name-table extension). It is
  therefore **not suitable** for amend, which must preserve the
  image's existing extension bytes verbatim and target either
  version. amend needs its own copy-and-adjust header serializer
  (step 2c). `write_be_u32`/`write_be_u64`
  (`src/shared/src/lib.rs:136/142`) are the `no_std` byte writers
  to use.
- **Planner-crate conventions** (mirror these exactly):
  - `src/crates/rebase/` is the closest analog. `Cargo.toml` deps:
    `shared = { path = "../../shared" }` and `qcow2 = { path =
    "../qcow2", features = ["create"], default-features = false }`.
    `#![no_std]` at `src/lib.rs:36`.
  - Patch enum `RebasePatch<'a> { Write { byte_offset: u64, bytes:
    &'a [u8] }, Append { .. } }` (`rebase/src/lib.rs:140`), a
    `const EMPTY`, `MAX_REBASE_PATCHES = 16` (`:203`), and a `Copy`
    `RebasePlan<'a>` holding a fixed `[Patch; MAX]` with
    `patch_count`, `patches()`, and `push()` (`:210–250`).
  - Error enum `RebaseError` (`:42–111`) with one variant per
    `shared::RebaseResult::ERROR_*` wire code.
  - Planner signature `pub fn plan_rebase_qcow2<'a>(opts: &...,
    scratch: &'a mut [u8]) -> Result<.., RebaseError>`
    (`rebase/src/qcow2.rs:175`); it `QcowHeader::parse`s the
    header itself, validates, then slices patch bytes out of
    `scratch` (all returned borrows bound to the scratch lifetime).
  - The "full header rebuild" pattern is `resize`'s
    `plan_header_only` (`resize/src/qcow2.rs:496–531`): allocate a
    cluster from scratch, fill it, emit one `Write { byte_offset:
    0, bytes }`.
  - Guest consumption (informs the API phase 3 needs): the rebase
    guest reads the header, calls the planner with
    `PLANNER_SCRATCH`, and applies patches in order via a
    `write_byte_range` loop (`src/operations/rebase/src/main.rs:246`).
  - Inline tests build a header as a byte array and assert on the
    emitted patches; the helper `make_header(..) -> [u8; 4096]` at
    `rebase/src/qcow2.rs:533`. Run with `cargo test -p amend`.
  - Workspace members in `src/Cargo.toml` list `crates/rebase`,
    `crates/resize`, …; add `crates/amend` (the guest binary
    `operations/amend` is added in phase 3, not here).

## Mission and problem statement

Create `src/crates/amend/` exposing a pure `no_std`
`plan_amend_qcow2` that, given the first header cluster and the
requested changes, returns an `AmendPlan` describing the action
(`NoOp`/`Amended`), the resulting version and lazy state, and the
header patch(es) to apply — or an `AmendError` that maps to a
`shared::AmendResult::ERROR_*` code. After this phase:

1. The crate compiles, is a workspace member, is `no_std`, and
   depends on `shared` and `qcow2` (with `features=["create"]` for
   the `QcowHeader`/offset constants; the planner does **not** call
   `build_header`).

2. `plan_amend_qcow2` implements the full decision matrix:
   - Parse the header cluster (`QcowHeader::parse`); `ParseFailed`
     on failure.
   - Reject non-qcow2 / nonsensical input with `UnsupportedFormat`.
   - **Refuse any amend of a `DIRTY` or `CORRUPT` image** (`Dirty`)
     — instar never holds an image open RW outside an op, so a set
     dirty bit means another writer may be mid-flight; refuse
     rather than race. (qemu likewise refuses.)
   - Compute the target version (from `FLAG_SET_COMPAT`/
     `FLAG_COMPAT_V3`, else unchanged) and target lazy state (from
     `FLAG_SET_LAZY`/`FLAG_LAZY_ON`, else unchanged), with the
     constraint that **v2 cannot carry lazy refcounts**: requesting
     `lazy_refcounts=on` against a target-v2 image (or a v2 image
     not being upgraded) is `LazyRequiresV3`; a v3→v2 downgrade
     silently clears lazy (it is a compatible feature that simply
     ceases to exist).
   - **Downgrade gate (target v2, source v3):** refuse with
     `DowngradeBlockedFeature` if any incompatible feature bit is
     set (`DIRTY`/`CORRUPT`/`EXTERNAL_DATA`/`COMPRESSION`/
     `EXTENDED_L2`); refuse with `DowngradeRefcountWidth` if
     `refcount_bits != 16` (v2 supports 16-bit only; rewriting the
     refcount tree is out of v1 scope).
   - **No-op detection:** if the target version and lazy state
     already match the image, return `action = NoOp` with no
     patches (resolves master-plan Open question 4 — instar reports
     a no-op rather than rewriting; phase 4 renders it and phase 6
     reconciles against qemu's observable behaviour).

3. It emits the correct patch(es):
   - **Same-version lazy toggle:** one `Write { byte_offset: 80,
     bytes: <8-byte new compatible_features> }`.
   - **Version change (up or down):** one `Write { byte_offset: 0,
     bytes: <rebuilt header cluster> }` produced by the
     copy-and-adjust serializer in step 2c, which preserves the
     fixed-header fields 0..72, sets the version/feature/
     refcount_order/header_length fields appropriate to the target,
     relocates the existing extension area + backing-file string to
     the new fixed-header boundary, and bumps `backing_file_offset`
     by the shift. If the relocation would push meaningful bytes
     past the cluster end, refuse with
     `ExtensionRelocationUnsupported` (resolves master-plan Open
     question 2; see Open question 2 below for the recommended
     scope).

4. Inline `#[cfg(test)] mod tests` cover every refusal path, the
   no-op path, the lazy-toggle patch, and the version-change
   rebuild (with and without extensions / a backing file, both
   directions, and the overflow-refusal). The exhaustive
   apply-then-reparse round-trip matrix is phase 5's `tests/`
   suite; phase 2 asserts on the emitted patch bytes directly.

Out of scope for this phase: any I/O or guest wiring (phase 3);
crash-safety write ordering / the corrupt-bit dance (a phase-3
guest concern — the planner only produces the final target bytes,
see Open question 5); `refcount_bits` changes, external-data-file,
encryption, and non-qcow2 formats (deferred in the master plan).

## Open questions

### 1. Crate API shape (confirm before 2a)

Working draft, mirroring `rebase`/`resize`:

```rust
#![no_std]

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AmendError {
    UnsupportedFormat,              // -> ERROR_UNSUPPORTED_FORMAT (1)
    InvalidOption,                  // -> ERROR_INVALID_OPTION (2)
    DowngradeBlockedFeature,        // -> ERROR_DOWNGRADE_BLOCKED_FEATURE (3)
    DowngradeRefcountWidth,         // -> ERROR_DOWNGRADE_REFCOUNT_WIDTH (4)
    LazyRequiresV3,                 // -> ERROR_LAZY_REQUIRES_V3 (5)
    ParseFailed,                    // -> ERROR_PARSE_FAILED (7)
    Dirty,                          // -> ERROR_DIRTY (8)
    ExtensionRelocationUnsupported, // -> ERROR_EXTENSION_RELOCATION_UNSUPPORTED (9)
    ScratchTooSmall,                // -> ERROR_SCRATCH_TOO_SMALL (11)
    Overflow,                       // -> ERROR_INTERNAL_OVERFLOW (12)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AmendPatch<'a> {
    Write { byte_offset: u64, bytes: &'a [u8] },
}
pub const MAX_AMEND_PATCHES: usize = 2;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AmendAction { NoOp, Amended }

#[derive(Clone, Copy)]
pub struct AmendPlan<'a> {
    pub action: AmendAction,
    pub resulting_version: u32,          // 2 or 3
    pub resulting_lazy_refcounts: bool,
    patch_count: u8,
    patches_storage: [AmendPatch<'a>; MAX_AMEND_PATCHES],
}
// new()/patches()/push() exactly like RebasePlan.

pub struct Qcow2AmendOpts<'a> {
    pub header_cluster: &'a [u8],  // the whole first cluster
    pub cluster_size: u32,
    pub set_compat: bool,
    pub target_v3: bool,           // meaningful iff set_compat
    pub set_lazy: bool,
    pub lazy_on: bool,             // meaningful iff set_lazy
}

pub fn plan_amend_qcow2<'a>(
    opts: &Qcow2AmendOpts<'_>,
    scratch: &'a mut [u8],
) -> Result<AmendPlan<'a>, AmendError>;
```

Notes: `ERROR_HEADER_MISMATCH (6)` and `ERROR_WRITE_FAILED (10)`
have **no** `AmendError` variant — they are guest-side concerns
(the host/guest cross-check and the device write), surfaced in
phase 3, not by the pure planner. `error_code()` on `AmendError`
returns the wire `u32`. The planner derives the current
version/lazy/features entirely from `header_cluster`; the
host-probed cross-check fields in `AmendConfig` are validated by
the guest (phase 3), keeping the planner pure and unit-testable
(matches how `plan_rebase_qcow2` re-parses the overlay header
itself). Confirm field names and the `MAX_AMEND_PATCHES` value.

### 2. Extension/backing-file relocation: full rebuild vs refuse — RECOMMENDATION

This is the central design fork. A version change moves the
fixed-header boundary (72↔104), so anything after it (header
extensions, then the backing-file string) shifts. Two options:

- **(A) Full copy-and-adjust rebuild (RECOMMENDED).** Relocate the
  existing extension area + backing string verbatim to the new
  boundary and bump `backing_file_offset`. Handles every realistic
  image — including v2 images *with* a backing file (whose backing
  string sits at offset 72 and would otherwise block the upgrade).
  Header extensions are opaque `(type, len, data)` blobs that move
  as a byte block; the only pointer fixup is `backing_file_offset`
  (the external-data and encrypt extensions store offsets into
  *other* clusters, which don't move — and we refuse external-data
  images anyway). This matches what qemu's `qcow2_update_header`
  effectively does (it rewrites the whole header cluster). Refuse
  with `ExtensionRelocationUnsupported` only in the genuinely
  unsupportable case where an upgrade's +32 shift would push
  meaningful bytes past the cluster end (needs a larger header
  cluster — out of v1 scope).

- **(B) Selective-patch + refuse-if-relocation-needed.** Only write
  the changed fixed-header fields in place; refuse (with
  `ExtensionRelocationUnsupported`) any image whose bytes in the
  contested 72..104 region are non-zero. Simpler, but it refuses
  **every v2-with-backing-file upgrade** — a surprising limitation,
  since backing-file images are common (the convert/rebase/commit
  test corpus is full of them).

**Recommendation: (A).** It is the correct fix (per the project's
"prefer the correct fix" principle), the relocation logic is not
materially more code than (B)'s "detect and refuse" logic, and it
avoids a limitation users would hit immediately. The implementer
must, before coding, **inspect real qemu-created fixtures** to
confirm the byte layout assumptions: create v2 images with and
without a backing file (and with a backing-format extension) via
`qemu-img create -f qcow2 -o compat=0.10[,backing_file=...]`, dump
the first cluster, and verify (a) where the extension chain and
backing string actually sit in v2 (expected: extensions at 72,
backing string after `EXT_END`), (b) that a plain standalone v2
image has zeros from 72 to the cluster end, and (c) the v3 layout
(`header_length`, extensions at 104). If (A) proves materially
riskier than expected, fall back to (B) for v1 and file
relocation as a follow-up — but default to (A).

(Operator: if you would rather ship a bounded v1 quickly, say so
and the plan switches to (B). Absent that, the steps below assume
(A).)

**Fixture findings (verified 2026-06-15 against qemu-img 10.0.8).**
Real qemu layouts the implementer must handle:

- **v2 plain** (`compat=0.10`, no backing): version=2,
  `backing_file_offset=0`, bytes 72..cluster-end all zero. Upgrade
  writes the v3 fixed fields into 72..104; nothing to relocate.
- **v2 + backing** (`compat=0.10,backing_file=…,backing_fmt=…`):
  extension chain starts at **offset 72** — a backing-format ext
  (`0xE2792ACA`, len 5 = "qcow2"), then `EXT_END` at 0x58, then the
  backing string at offset 96 (`backing_file_offset=0x60`,
  size 10). On upgrade this whole region (72..106) collides with
  the v3 fixed header and **must relocate to 104** (+32), with
  `backing_file_offset` bumped 96→128. This is the case option (B)
  would refuse — confirming (A).
- **v3 plain** (`compat=1.1`): **`header_length` = 112, not 104**
  (`refcount_order=4` at 96, header_length `0x70` at 100), and a
  **feature-name-table extension `0x6803F857`** (len 384, the
  "dirty bit"/"corrupt bit"/… strings) sits at offset 112. So
  v3 sources have `src_ext_start = header_length = 112` (read the
  field — do NOT hardcode 104) and carry an extension `build_header`
  cannot emit, reconfirming amend must relocate extensions
  verbatim.
- **v3 + backing**: ext chain at 112 = backing-format ext, then
  feature-name-table ext, with the backing string far out at
  `backing_file_offset=0x210` (528). Downgrade relocates the whole
  112..538 region down to 72 (−40) and bumps `backing_file_offset`
  528→488.
- **Backing file without `backing_fmt`**: qemu **refuses to create
  it** ("Backing file specified without backing format"), so the
  "bare backing string at offset 72 with no extension chain" case
  does not arise from qemu — every backing file is preceded by the
  backing-format extension. The planner should still bound-check
  defensively, but need not special-case it.

Design consequences folded into step 2c:
- **Read the source `header_length` field for v3** (`src_ext_start`);
  it is 112 in modern qemu, sometimes 104 elsewhere.
- **On upgrade, write `header_length = 104`** (the minimum valid v3
  header: no `compression_type` field, extensions immediately
  after) and **do not synthesize a feature-name-table extension**.
  The relocated v2 extensions (if any) are placed at 104. This is
  byte-different from qemu's upgrade (112 + feature-name-table +
  `compression_type=0`) but `qemu-img info`/`check`/`compare` do
  not observe `header_length` or the feature-name-table, so it
  should stay info-equivalent — phase 6 records any residual
  divergence in `KNOWN_AMEND_DIVERGENCES`.
- **On downgrade, relocate the feature-name-table extension
  verbatim** to offset 72 (v2 readers ignore unknown extensions;
  harmless). Do not attempt to strip it.

### 3. Version-agnostic extension-area length helper

The rebuild (and the overflow check) needs the length of the
"meaningful tail" = the extension chain plus the backing-file
string, for **either** source version. The existing
`parse_header_extensions` (`qcow2/src/lib.rs:471`) is v3-only.
Working answer: add a small `pub fn header_extension_area_end(
cluster: &[u8], start: usize) -> Option<usize>` to the qcow2 crate
(beside `parse_header_extensions`) that walks `(type:u32, len:u32)`
records from `start`, advancing by `8 + align8(len)`, stopping at
`EXT_END`, and returns the offset just past `EXT_END`; the planner
then takes `meaningful_end = max(ext_end, backing_file_offset +
backing_file_size)`. Confirm placement (qcow2 crate vs a private
helper inside the amend crate). Putting it in the qcow2 crate lets
phase 5/8 reuse it and keeps the parsing logic with the format.

### 4. Confirm the no-op and lazy-clear-safety semantics

- **No-op:** target == current for both version and lazy ⇒
  `NoOp`, zero patches. Does qemu rewrite the header anyway (mtime
  change) on a no-op amend? Phase 6 will reconcile; for the planner
  the contract is "no patches when nothing changes". Confirm this
  is acceptable (it means `instar amend` is idempotent and does not
  touch the file on a no-op).
- **Lazy clear safety (master-plan Open question 5):** clearing
  `lazy_refcounts` on a non-dirty image is safe at rest (refcounts
  are accurate when the lazy bit is not actively dirty), and we
  already refuse `DIRTY`. Setting it just sets the bit. Confirm no
  refcount flush is required (it is not — instar never left the
  image lazy-dirty, and a foreign dirty image is refused).

### 5. Crash-safety is deferred to phase 3

The planner emits only the *final* target header bytes. Whether the
guest writes them atomically, or guards the rewrite with the
`corrupt` incompatible bit (set-corrupt → write → clear-corrupt,
the `check --repair` pattern), is a phase-3 write-ordering
decision (master-plan Open question 6). Phase 2 does not address
it. Confirm we are comfortable leaving the single-cluster header
write unguarded at the planner layer.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | low | sonnet | none | Scaffold `src/crates/amend/`. Create `Cargo.toml` mirroring `src/crates/rebase/Cargo.toml` (name `amend`, `publish = false`, deps `shared = { path = "../../shared" }` and `qcow2 = { path = "../qcow2", features = ["create"], default-features = false }`). Create `src/lib.rs` (`#![no_std]`) defining the types in Open question 1 — `AmendError` (with an `error_code(&self) -> u32` returning the `shared::AmendResult::ERROR_*` values noted), `AmendPatch`, `MAX_AMEND_PATCHES`, `AmendAction`, `AmendPlan` (copy `RebasePlan`'s `new`/`patches`/`push` shape from `rebase/src/lib.rs:210–250`), and `Qcow2AmendOpts`. Add a `src/qcow2.rs` module with `pub fn plan_amend_qcow2<'a>(opts, scratch) -> Result<AmendPlan<'a>, AmendError>` that for now just `QcowHeader::parse`s and returns `Err(UnsupportedFormat)` (stub). Add `"crates/amend"` to `members` in `src/Cargo.toml`. Verify with `pre-commit run --all-files` (it compiles the workspace as the host user; do NOT invoke cargo directly — the sandbox denies it). |
| 2b | high | opus | none | Implement the decision/validation matrix and the same-version lazy-toggle patch in `src/crates/amend/src/qcow2.rs`, leaving the cross-version rebuild as an `Err(ExtensionRelocationUnsupported)`/TODO for 2c. Logic: parse header; refuse `Dirty` if `dirty || corrupt`; compute target version (set_compat ? (target_v3?3:2) : current) and target lazy (set_lazy ? lazy_on : current_lazy); enforce `LazyRequiresV3` when lazy-on targets v2; for a v3→v2 target run the downgrade gate (`DowngradeBlockedFeature` if any incompat bit set, `DowngradeRefcountWidth` if `refcount_bits != 16`) and force target lazy false; detect no-op (target==current for version and lazy) → `AmendAction::NoOp`, no patches; for a **same-version** lazy change emit one `Write { byte_offset: COMPATIBLE_FEATURES_OFFSET as u64, bytes }` where `bytes` is the new 8-byte big-endian `compatible_features` written into `scratch` via `write_be_u64`. Set `resulting_version`/`resulting_lazy_refcounts` on the plan. Write exhaustive inline `#[cfg(test)]` tests for: every refusal (dirty, corrupt, lazy-on-v2, downgrade-blocked per feature bit, downgrade-refcount-width), the no-op (v3→v3 same lazy; v2→v2), and the lazy on/off toggle patch (assert offset 80 and exact bytes). Use a `make_header` helper like `rebase/src/qcow2.rs:533`, extended to set features/refcount_order. opus: the refusal matrix has subtle interactions (lazy + downgrade, no-op vs change) and is the safety boundary. Validate via `pre-commit run --all-files`. |
| 2c | high | opus | worktree | Implement the cross-version copy-and-adjust header rebuild (Open question 2 option A). First add `pub fn header_extension_area_end(cluster: &[u8], start: usize) -> Option<usize>` to `src/crates/qcow2/src/lib.rs` (beside `parse_header_extensions:471`), version-agnostic, walking `(type,len)` records by `8 + ((len+7)&!7)` until `EXT_END`, with full bounds checking; add unit tests for it (empty chain, one backing-format ext, truncated chain → None). Then in `amend/src/qcow2.rs`, for a version change: compute `src_ext_start` (v2⇒72, v3⇒`header_length` field), `dst_fixed_len` (target v3⇒104, v2⇒72), `delta = dst_fixed_len - src_ext_start`, and `meaningful_end = max(header_extension_area_end(...), backing_file_offset + backing_file_size)`; refuse `ExtensionRelocationUnsupported` if `meaningful_end + delta > cluster_size`; build the target cluster in `scratch` (zeroed): copy source bytes `0..72`, set `version`(4), and for target v3 write `incompatible_features`(72), `compatible_features`(80)=lazy?, `autoclear_features`(88)=0, `refcount_order`(96), `header_length`(100)=104 (for target v2 leave 72.. as relocated extension area); copy the tail `src[src_ext_start..meaningful_end]` to `dst[dst_fixed_len..]`; if `backing_file_size > 0` set `backing_file_offset`(8) = `src_backing_offset + delta`; emit one `Write { byte_offset: 0, bytes: &scratch[..cluster_size] }`. Inline tests: upgrade no-ext (assert version/features/refcount_order/header_length and that 72..104 written, tail preserved), upgrade with backing-format ext + backing file (assert ext bytes relocated to 104 and `backing_file_offset` bumped +32), downgrade no-ext, downgrade with ext (assert relocated to 72, freed tail zeroed, `backing_file_offset` bumped −delta), and overflow refusal. Worktree isolation because this is the highest-risk byte-twiddling step. Validate via `pre-commit run --all-files` and `make test-rust` (confirm `cargo test --workspace` picks up `-p amend`; if not, add an explicit `cargo test -p amend` line to the Makefile `test-rust` target beside the `-p create` line). opus: byte-exact relocation correctness. |
| 2d | low | sonnet | none | Update the master plan `docs/plans/PLAN-amend.md`: in the phase-2 row mark status appropriately; in the Open questions, append "Resolved in phase 2" notes to OQ1 (downgrade writer: a copy-and-adjust serializer in the amend crate, not `build_header`), OQ2 (relocation: option A implemented; `ExtensionRelocationUnsupported` only on cluster overflow), OQ3 (refcount-width refusal wired as `DowngradeRefcountWidth`), OQ4 (no-op = zero patches), OQ5 (lazy clear safe given the dirty refusal), and note OQ6 (crash-safety) remains for phase 3. Keep it to a few sentences each, matching the phase-1 resolution style. Do NOT add this phase file to `order.yml`. |
| 2e | low | sonnet | none | From the worktree root run `pre-commit run --all-files`, `make instar` (expect core.bin unchanged — no guest code lands here, so it must stay at the phase-1 size; if `make instar` even rebuilds core, confirm the size did not regress), and `make test-rust` (all suites incl. the new `amend` tests). Then stage and present a single commit for steps 2a–2d with the CLAUDE.md message convention (≤50-char first line ending in a period, 75-char body wrap, `Prompt:` paragraph, `Signed-off-by`, and `Co-Authored-By`/`Assisted-By` naming model + 1M context + effort). Do not push. |

## Agent guidance

### Execution model

All implementation work for this phase is done by sub-agents, never
in the management session. After each step the management session
reads the actual changed files (does not trust the summary),
confirms no unrelated files changed, runs the named gates, and then
commits, retries with a sharper brief, or upgrades the model. Note
the sandbox **denies direct `cargo`**; validate via
`pre-commit run --all-files` (compiles + lints the workspace as the
host user), `make test-rust`, and `make instar`.

### Model and effort notes

- 2a, 2d, 2e are mechanical; sonnet at low effort with the exact
  templates suffices.
- 2b and 2c are the correctness core and use opus: 2b owns the
  refusal/no-op matrix (the safety boundary), and 2c is byte-exact
  header relocation. 2c is worktree-isolated because a wrong byte
  offset corrupts every amended image; the worktree is discarded if
  the output is unsatisfactory.
- When in doubt, skew to the more capable model.

### Management session review checklist

After each step:

- [ ] Read the changed files — don't trust the agent's summary.
- [ ] No unrelated files modified; the qcow2-crate addition (2c) is
      a new `pub fn` only, no existing parser behaviour changed.
- [ ] `pre-commit run --all-files` clean (2a–2c).
- [ ] `make test-rust` passes incl. the new `amend` and
      `header_extension_area_end` tests (2c, 2e).
- [ ] `make instar` builds and `core.bin` size is unchanged from
      phase 1 (no guest code in this phase) (2e).
- [ ] The refusal matrix matches Open question 2's gates and maps
      to the right `ERROR_*` codes; the rebuilt-cluster bytes match
      the offsets in step 2c.

## Administration and logistics

### Success criteria

Phase 2 is complete when:

* `src/crates/amend/` exists, is a `no_std` workspace member, and
  `plan_amend_qcow2` implements the full decision matrix and patch
  emission for lazy toggle, upgrade, and downgrade.
* Every refusal path, the no-op path, the lazy-toggle patch, and
  the version-change rebuild (both directions, with and without
  extensions/backing file, plus overflow refusal) are covered by
  inline unit tests.
* `header_extension_area_end` is added to the qcow2 crate with
  tests and changes no existing behaviour.
* `make instar` builds, `core.bin` is unchanged from phase 1,
  `make lint` is clean, `make test-rust` passes, and
  `pre-commit run --all-files` is clean.
* The master plan's resolved Open questions are updated.

### Future work created by this phase

- Phase 3 (guest op) reads the full first cluster, validates the
  `AmendConfig` host-probed cross-check against its re-parse
  (`ERROR_HEADER_MISMATCH`), calls `plan_amend_qcow2`, applies the
  patches (`ERROR_WRITE_FAILED` on device failure), and decides the
  crash-safety write ordering (master-plan Open question 6).
- Phase 5 (`src/crates/amend/tests/`) does the exhaustive
  apply-then-`QcowHeader::parse` round-trip matrix and can reuse
  `header_extension_area_end`.
- If Open question 2 lands as option (B) instead of (A),
  relocation support becomes a tracked follow-up.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added to
`docs/plans/order.yml`. The master plan links to it from its
Execution table (already present).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
