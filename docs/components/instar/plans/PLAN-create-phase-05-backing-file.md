# PLAN-create phase 5: backing-file polish

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, KVM, virtio,
disk image formats, qemu-img semantics), research as needed to
give a confident answer. Flag any uncertainty explicitly rather
than guessing.

This is a phase plan under `PLAN-create.md`. Phases 1–4 shipped
the metadata emitters, guest binary, host CLI subcommand, and
`-o` option parser. Phase 5 closes the backing-file gaps phase 3
deferred and adds the chain-related edge-case tests the master
plan asked for.

## Mission

Most of the backing-file surface already shipped in phase 3
(clap flags `-b` / `-F` / `-u`, path resolution relative to the
new image's directory, attach as input device 0, embed the
user-typed path verbatim, guest-side header parsing for raw /
qcow2 / vmdk / vhd). Phase 5 closes the remaining gaps and adds
the tests:

1. **VHDX-as-backing virtual_size extraction.** Phase 2e's
   `read_backing_virtual_size` returns
   `ERROR_BACKING_PARSE_FAILED` for vhdx backings because vhdx
   stores `virtual_size` in a metadata-region item that
   requires walking three regions. Wire it up via the vhdx
   crate's `VhdxState::init`, which already does the walk.

2. **VMDK parentCID computation.** Phase 1d's
   `build_vmdk_descriptor_with_backing` hard-codes
   `parentCID=deadbeef` because it has no way to know the
   parent's real CID. When the target is vmdk and the backing
   is vmdk, the guest reads the parent's descriptor (via the
   vmdk crate's `read_and_parse_descriptor`) and plumbs the
   real CID up through `Qcow2/Vmdk/...CreateOpts` into
   `build_vmdk_descriptor_with_backing`.

3. **Clearer backing-related errors.** Today every backing
   failure (unknown format, parse failed, too-large)
   collapses to one of a few error codes with terse messages.
   Add a couple of new codes and improve the host-side message
   table so users see what specifically went wrong.

4. **Integration tests** for the corners the master plan called
   out: backing-chain non-recursion, vhdx-as-backing,
   vmdk-from-vmdk with real parentCID, missing-file (host
   path-resolution error), backing-format mismatch
   (auto-detect catches it), backing-file-too-large-for-target.

5. **Internal docs** (CHANGELOG / AGENTS / ARCHITECTURE) noting
   the closures.

Out of scope, deferred to future work or other phases:

- **Differencing VHD / VHDX as the *target* of create.** Phases
  1e and 1f explicitly return `BackingFileUnsupported` when
  asked to emit a differencing VHD (`DISK_TYPE_DIFFERENCING`)
  or a parent-locator-bearing VHDX. The encodings are complex
  (UTF-16 path encoding, platform-code plumbing, parent locator
  hash) and the use case is rare; phase 5 keeps them deferred.
- **Backing-chain composition** beyond the immediate parent.
  qemu-img also stops at the immediate parent — the chain is
  resolved at *open* time by traversing the file. instar's
  behaviour already matches: `-b` writes one backing reference;
  the resulting image's child of a child references its
  immediate parent, not the grandparent. Phase 5 only adds a
  test that asserts this.
- **`--sector-size > 512`.** Still deferred behind the same
  "phase 5 may relax this" note phase 3 left, but doing it
  requires the planner to emit coalesced sector-sized writes
  (a `crates/create` change, not a host-side change). Defer to
  a dedicated follow-up phase.
- **Multi-file VMDK subformats** (`monolithicFlat`,
  `twoGbMaxExtent*`). Tracked separately in PLAN-create.md's
  Future work; requires multi-output-device call-table support.
- **Encrypted backing.** Tracked separately under encryption
  future work.

## What the survey turned up

### Phase 3 backing-file surface (already shipped)

`run_create_nonraw` in `src/vmm/src/main.rs` (lines ~6440–6540):

- Resolves `args.backing` relative to the output file's parent
  directory.
- Verifies the backing exists and is a regular file (unless
  `-u`).
- Opens read-only via `BackingStore::open`.
- Embeds the user-typed bytes in `CreateConfig.backing_file`.
- Maps `args.backing_format` → numeric `ImageFormat` via
  `create_backing_format_code`.
- Attaches as input device 0.

The validator (`validate_create_args`) rejects `-b` without
`-F` or `-u`, and rejects `-f raw -b BACKING`.

### Phase 2e guest-side backing lookup

`src/operations/create/src/main.rs::read_backing_virtual_size`
handles raw / qcow2 / vmdk / vhd:

```rust
ImageFormat::Raw => capacity * sector_size,
ImageFormat::Qcow2 => qcow2::QcowHeader::parse(header).virtual_size,
ImageFormat::Vmdk4 => vmdk::Vmdk4Header::parse(header).virtual_size,
ImageFormat::Vhd  => read last sector + vhd::VhdFooter::parse,
_ => None,  // vhdx + everything else falls into ERROR_BACKING_PARSE_FAILED
```

VHDX hits the `_` arm and returns `None` → the host renders
`ERROR_BACKING_PARSE_FAILED` with the user-facing message that
explicitly mentions the phase-5 deferral.

### VHDX walk primitive

`vhdx::VhdxState::init` (`src/crates/vhdx/src/lib.rs:662`) already
walks header → region table → metadata region and exposes
`virtual_disk_size: u64` directly. Signature:

```rust
pub unsafe fn init(
    call_table: &CallTable,
    device_idx: u32,
    sector_size: usize,
    input_capacity: u64,
    bat_cache_buf: *mut u8,
    data_cache_buf: *mut u8,
    bytes_read: &mut u64,
) -> Option<Self>;
```

The two cache buffers are each `MAX_SECTOR_SIZE` bytes. The
create guest's scratch layout reserves
`GUEST_CREATE_SCRATCH_LIMIT = 8 MiB` starting at
`SCRATCH_MEM_BASE + MAX_SECTOR_SIZE`; the first two sector-
sized slots of that region can serve as the vhdx caches during
the backing-header lookup phase (the planner doesn't touch
scratch until after the lookup returns).

### VMDK CID / parentCID

`vmdk::read_and_parse_descriptor` (`src/crates/vmdk/src/lib.rs:1077`)
parses a descriptor and populates a `VmdkInfo { cid, parent_cid,
... }` struct. The descriptor lives at byte offset
`desc_offset_sectors * 512` for `desc_size_sectors * 512` bytes;
the guest reads them via `read_input_sector` after parsing the
binary header.

### Phase 1d's parentCID hardcode

`src/crates/create/src/lib.rs::build_vmdk_descriptor_with_backing`
writes `parentCID=deadbeef` regardless of the actual parent. The
function is called from `plan_vmdk` when `opts.backing` is
`Some`. Phase 5 needs:

- An optional `parent_cid: Option<u32>` field on
  `VmdkCreateOpts` (or on a sub-struct `BackingRef`).
- `build_vmdk_descriptor_with_backing` uses the supplied CID
  when present, falls back to the sentinel when not (e.g. for
  raw-backing-of-vmdk, where there's no CID concept).

### Error code surface today

`CreateResult::ERROR_*` (in `src/shared/src/lib.rs`):

```
ERROR_OK = 0
ERROR_INVALID_OPTION = 1
ERROR_INVALID_SIZE = 2
ERROR_SCRATCH_TOO_SMALL = 3
ERROR_BACKING_READ_FAILED = 4
ERROR_BACKING_PARSE_FAILED = 5
ERROR_BACKING_TOO_LONG = 6
ERROR_WRITE_FAILED = 7
ERROR_UNSUPPORTED_FORMAT = 8
```

Phase 5 may add:

- `ERROR_BACKING_FORMAT_UNSUPPORTED = 9` — the guest recognised
  the format but can't extract size from it (vhdx-as-backing
  on a future regression, or a format like VDI that we
  detect but don't yet handle).
- `ERROR_BACKING_SIZE_TOO_LARGE = 10` — backing's virtual_size
  exceeds the target format's addressable range. Currently
  surfaces as `ERROR_INVALID_SIZE` from `plan_qcow2`; a
  dedicated code lets the host say "the backing image is too
  large for cluster_size=512 qcow2 — try cluster_size=64k".

Both are optional polish; the plan recommends adding them in
5c.

## Open questions

1. **VHDX cache buffer placement.** Two `MAX_SECTOR_SIZE`
   buffers are needed for `VhdxState::init`. Reusing the first
   two sector-sized chunks of `CREATE_SCRATCH` (which is unused
   during the backing-header lookup) is the simplest path; the
   alternative is reserving a dedicated VHDX scratch region in
   `shared/lib.rs`. Recommend: reuse `CREATE_SCRATCH` and
   document the lifetime constraint (lookup must complete
   before plan_*).

2. **What if the user passes `-F vhdx` but the file isn't
   actually vhdx?** Phase 2e currently uses `detect_format_from_header`
   on the first sector, ignoring the user-supplied `-F` hint
   entirely. That's actually a defensible policy — auto-detect
   is more reliable than user input. Recommend: keep
   auto-detect; treat `-F` purely as a hint for the metadata
   embed and as the "without -F we refuse to open" gate.
   Document this asymmetry in `docs/quirks.md` when phase 11
   ships the docs.

3. **VMDK parentCID for non-vmdk backings.** If a user does
   `instar create -f vmdk -b parent.qcow2 -F qcow2 child.vmdk`,
   parentCID has no source. Recommend: use the existing
   sentinel (deadbeef) and document the limitation. Most
   vmdk-with-qcow2-backing setups don't exist in practice.

4. **Error-code additions vs. better messages on existing
   codes.** Adding new codes means a CallTable ABI bump
   (`ERROR_*` constants are exposed through the create guest's
   `CreateResult`). Better messages on the existing codes
   require no ABI change. Recommend: the two new codes
   proposed (FORMAT_UNSUPPORTED, SIZE_TOO_LARGE) are worth
   the ABI churn because they let the host render distinct
   actionable messages. The shared crate's `CreateResult`
   struct doesn't change shape — only the constant set
   expands.

5. **Backing-chain non-recursion test setup.** Building a
   three-level chain (`grandparent -> parent -> child`)
   requires either pre-built test fixtures or the test
   creating all three at runtime via repeated `instar create`
   calls. Recommend: the test creates all three at runtime in
   a `tempfile.TemporaryDirectory()`. Adds a few hundred ms
   to the smoke set but stays hermetic.

6. **Backing-too-large detection layer.** The error currently
   surfaces from `qcow2::create::compute_layout` when the L1
   table would exceed `QCOW2_MAX_L1_SIZE_ENTRIES`. That's
   target-specific. For phase 5 a simpler-and-more-general
   approach: the guest, after reading the backing's
   virtual_size, sanity-checks it against a per-target
   ceiling table before calling `plan_*`. Recommend: add the
   ceiling check; map the failure to the new
   `ERROR_BACKING_SIZE_TOO_LARGE` for a clearer message
   regardless of which `plan_*` would have caught it.

7. **Should the host probe a sector of the backing before
   attaching it?** No — that would re-introduce host-side
   parsing of untrusted format bytes, violating the security
   model. Keep the guest as the only parser.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high   | opus   | none | Extend `read_backing_virtual_size` in `src/operations/create/src/main.rs` to handle vhdx. The current implementation matches on `ImageFormat::Vhdx` and falls through to the `_ => None` arm. Replace that with a `vhdx::VhdxState::init` call. Two `MAX_SECTOR_SIZE` cache buffers are required — reuse the first two sector-sized slots of `CREATE_SCRATCH` (the planner doesn't run until after the lookup returns). Pattern: define `const VHDX_CACHE_A: usize = CREATE_SCRATCH;` and `const VHDX_CACHE_B: usize = CREATE_SCRATCH + MAX_SECTOR_SIZE;` near the other scratch consts. Inside the vhdx arm, build the state and read `state.virtual_disk_size`. Differencing VHDX (parent locators) still fails — `VhdxState::init` returns `None` for differencing images; that's correct, the deferred-target reject in `crates/create` still rules out emitting one. Run `make instar`, smoke-test by hand against a vhdx parent file (use `instar create -f vhdx /tmp/parent.vhdx 32M` to build one, then `instar create -f qcow2 -b parent.vhdx -F vhdx /tmp/child.qcow2`), and confirm `instar info` reports the right virtual_size on the child. |
| 5b | high   | opus   | none | Plumb the real parentCID through the `vmdk` create path. Three coordinated changes: (1) Add `parent_cid: Option<u32>` to `crates/create::VmdkCreateOpts` (with a backwards-compatible default of `None`). (2) Update `build_vmdk_descriptor_with_backing` to use the supplied CID when `Some`, fall back to the existing sentinel `deadbeef` when `None`. Update the unit tests in `crates/create` that touch the vmdk-with-backing path. (3) In the create guest (`src/operations/create/src/main.rs`), when target=vmdk + backing is present + backing format detection returns Vmdk4, read the parent's descriptor via `vmdk::read_and_parse_descriptor`, extract `info.cid`, and set `opts.parent_cid = Some(cid)` before calling `plan_vmdk`. The descriptor read needs sector-aligned access — convert sector offset to byte offset using `desc_offset_sectors * 512`. Smoke-test: `instar create -f vmdk /tmp/p.vmdk 32M; instar create -f vmdk -b p.vmdk -F vmdk /tmp/c.vmdk` produces a child whose `parentCID` line in the embedded descriptor matches the parent's `CID`. Read the descriptor by `head -c 10240 /tmp/c.vmdk | tail -c 9728` to verify. |
| 5c | medium | sonnet | none | Refine the error surface. Add two new `CreateResult::ERROR_*` constants in `src/shared/src/lib.rs`: `ERROR_BACKING_FORMAT_UNSUPPORTED = 9` (backing format recognised but size extraction not implemented — currently only applies if a future regression breaks the vhdx path) and `ERROR_BACKING_SIZE_TOO_LARGE = 10` (backing's virtual_size exceeds the target's addressable range). In the create guest, add a per-target ceiling check after the backing's virtual_size is resolved (`qcow2 with cluster_size=512` ≤ ~128 GiB, etc.) — return ERROR_BACKING_SIZE_TOO_LARGE on overflow rather than letting `plan_qcow2`'s `InvalidVirtualSize` surface as the less-clear `ERROR_INVALID_SIZE`. In the host (`src/vmm/src/main.rs`), update `create_error_detail` to map the two new codes to actionable messages: ERROR_BACKING_SIZE_TOO_LARGE includes the suggestion "try a larger cluster size or a target format with greater virtual-size headroom". Add a brief comment in `CreateResult`'s ERROR_* docstring noting that codes are stable (only appended, never reordered) so existing operation binaries keep working. |
| 5d | medium | sonnet | none | Integration tests in `tests/test_create.py`. Add a new class `TestCreateBackingChain` with these cases: (1) vhdx-as-backing — build a vhdx parent, create a qcow2 child with `-b parent.vhdx -F vhdx`, info reports the parent's virtual_size on the child; (2) vmdk-from-vmdk — build a vmdk parent, create a vmdk child with backing, the child's descriptor's `parentCID` matches the parent's `CID` (parse the descriptor text out of the child); (3) backing-chain non-recursion — three-level chain (grandparent → parent → child) built via three `instar create` calls; `instar info child` reports `backing-filename=parent` (not grandparent), confirming we record the immediate backing only; (4) backing format mismatch — create a qcow2 file, then `instar create -f qcow2 -b foo.qcow2 -F raw child.qcow2` — the guest's auto-detect picks qcow2 from the magic, ignoring the wrong `-F` hint; child's virtual_size matches the parent's; (5) backing-too-large for target — create a 4 TiB backing, then try `instar create -f qcow2 -b parent -F raw --cluster-size 512 child` — expect ERROR_BACKING_SIZE_TOO_LARGE with the cluster-size hint in stderr. Five new tests; expect total `tests/test_create.py` count to grow from 24 to 29. |
| 5e | low    | sonnet | none | Internal docs: (1) `CHANGELOG.md` — extend the Unreleased "instar create" entry to mention vhdx-as-backing now works, vmdk parentCID is correctly computed, and the two new error codes. Remove "vhdx-as-backing" from the deferred list. (2) `ARCHITECTURE.md` — update the operations/create paragraph noting vhdx backings now walk via VhdxState::init and the new error-code surface. (3) `AGENTS.md` — no change needed (the create entry doesn't enumerate format-by-format backing support). (4) Mark phase 5 complete in PLAN-create.md's execution table. |

## Out of scope for phase 5

Reminders so a sub-agent doesn't drift:

- No multi-file VMDK subformats (tracked separately in
  Future work).
- No `--sector-size > 512` relaxation (needs planner changes
  first).
- No encrypted backing.
- No differencing VHD or VHDX as the *output* target (phases
  1e and 1f explicitly defer).
- No backing-chain composition / flattening — instar records
  only the immediate parent reference, same as qemu-img.
- No JSON or human renderer changes — phase 3e's output
  surface is unchanged.

## Success criteria

- `make instar` builds cleanly. `create.bin` size still
  under the 384 KiB cap (expect a small increase for the
  VHDX walk and descriptor parse — order of a few KiB).
- `make lint` clean.
- `make test-rust` passes — any new rust unit tests
  (parent_cid handling, ceiling checks) raise the totals.
- `make test-integration` includes the new
  `TestCreateBackingChain` cases (5 added; total ~29 in
  `tests/test_create.py`) and they all pass.
- `pre-commit run --all-files` clean.
- End-to-end manual smoke checks all pass:
  - `instar create -f vhdx /tmp/p.vhdx 32M; instar create -f
    qcow2 -b p.vhdx -F vhdx /tmp/c.qcow2; instar info
    /tmp/c.qcow2` reports virtual_size=32M and
    backing-filename=p.vhdx (no ERROR_BACKING_PARSE_FAILED).
  - `instar create -f vmdk /tmp/p.vmdk 32M; instar create -f
    vmdk -b p.vmdk -F vmdk /tmp/c.vmdk` produces a child whose
    descriptor's parentCID line matches the parent's CID
    (not the deadbeef sentinel).
  - Grandparent → parent → child three-level chain: `instar
    info child` reports the immediate parent only.
- `git diff --stat phase-5-base..HEAD -- src/operations/`
  shows changes only to `src/operations/create/` (phase 5's
  guest-side work doesn't touch any other operation).

## Bugs fixed during this work

(To be filled in.)

## Back brief

Before executing each step of this phase, please back brief the
operator as to your understanding of the step and how the work
you intend to do aligns with the brief. In particular, flag if
the brief refers to file/line locations that don't match what
you find when you read them (the survey was a snapshot; the
codebase may have moved).
