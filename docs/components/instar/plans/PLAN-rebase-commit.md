# `instar rebase` and `instar commit` subcommands

## Status: Complete

Phases 1-12.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, LUKS, KVM, virtio,
disk image formats, `qemu-img` semantics), research as needed to
give a confident answer. Flag any uncertainty explicitly rather
than guessing.

All planning documents go in `docs/plans/`. Phase plans for this
master plan are named `PLAN-rebase-commit-phase-NN-<descriptive>.md`
alongside this file and linked from the Execution table below.
They are *not* added to `docs/plans/order.yml` — only the master
plan is.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

`PLAN-convert-followups.md` enumerates seven `qemu-img`
subcommands deferred from the convert effort. `measure`,
`create`, and `resize` have shipped (see their respective master
plans). `rebase` and `commit` are scheduled fourth — ahead of
`map` and `snapshot` — because:

- They share a single piece of new infrastructure (the two-device
  read+write model: read one image, mutate another) and a single
  set of new format-write paths (backing-file pointer rewrite in
  qcow2 headers and in vmdk monolithicSparse descriptors). Doing
  them as a pair amortises the design and review work.
- They reuse the `read_output_sector` call-table primitive that
  landed in resize phase 7 (`src/shared/src/lib.rs:729`). No
  new ABI extension is required to read from the image being
  mutated, which removes the biggest correctness risk that
  resize had to address.
- They reuse the existing backing-chain discovery path in
  `src/vmm/src/main.rs:1919` (`discover_backing_chain`), which
  already handles per-format backing references and validates
  paths against the security allowlist. Both subcommands need to
  walk a chain on the host side before launching the guest.
- They are the highest-impact gap remaining in the day-to-day
  qcow2 workflow. Overlay management (a `--snapshot` style
  `qcow2` overlay backed by a base image, periodically committed
  forward or rebased onto a new base) is one of the most common
  qemu-img use cases in practice — far more so than internal
  qcow2 snapshots (`qemu-img snapshot`) which most modern
  workflows have moved away from.
- They are bounded in scope: qcow2 and vmdk monolithicSparse are
  the only two image formats instar parses today that support
  backing chains. VHD differencing and VHDX differencing have no
  parser support yet (vhdx explicitly rejects them at
  `src/crates/vhdx/src/lib.rs:797`), so they fall naturally out
  of v1 scope.

The relevant existing infrastructure this plan builds on:

- VMM subcommand scaffolding in `src/vmm/src/main.rs`
  (`enum Commands` at lines 2332–2348, per-op `*Args` struct,
  `run_*` function), call-table boundary in
  `src/shared/src/lib.rs` (`OPERATION_CONFIG_ADDR`, per-op
  `*Config` and `*Result` structs near the existing
  `ResizeConfig` / `ResizeResult` definitions), and the
  protobuf wrapper in `crates/guest-protocol/proto/guest.proto`
  (`GuestMessage` oneof payload — add `rebase_result` and
  `commit_result` arms).
- `discover_backing_chain` (`src/vmm/src/main.rs:1919–2092`).
  Already walks qcow2, vmdk-flat (text descriptor with
  `parentFileNameHint=`), vmdk-binary, vhd, and vhdx images and
  follows their backing references via a sandboxed `info`
  operation. Validates every resolved path against the security
  allowlist. Both rebase (which needs to read the old backing
  chain in safe mode) and commit (which needs the immediate
  backing) consume this directly.
- `read_output_sector` call-table function pointer
  (`src/shared/src/lib.rs:729`). Lets the guest both read and
  write the file mounted on the output device. Required by
  rebase (the overlay being rebased is the output) and by commit
  (the backing being merged into is the output, plus we may
  need to write `0`-refcount entries back to the overlay if we
  also clear it — see open question 5).
- Format parsers (`crates/qcow2/`, `crates/vmdk/`) — both
  already extract backing references during `info` (see
  `src/crates/qcow2/src/lib.rs:524` `read_backing_file` and
  `src/crates/vmdk/src/lib.rs:1081` `read_and_parse_descriptor`).
- Format writers from `create` and `resize`:
  - qcow2 header rewrite path (`build_header` in
    `src/crates/create/src/lib.rs`) understands the
    `backing_file_offset` / `backing_file_size` fields at
    qcow2 header bytes 8–19 and writes the backing-file path
    string at the indicated offset.
  - vmdk descriptor emitters in `src/crates/create/src/lib.rs`
    around lines 593–607 already emit `parentFileNameHint=`
    and `parentCID=` fields.
  - qcow2 refcount / L1 / L2 mutation patterns from the resize
    planners in `src/crates/resize/`.
- The cross-version baseline harness in
  `instar-testdata/scripts/generate-baselines.py` and its
  `expected-outputs/{info,create,measure,resize}-*/` layouts.
  This plan adds `expected-outputs/rebase-*/` and
  `expected-outputs/commit-*/`.
- The coverage-guided fuzz harnesses in `src/fuzz/` and the
  differential fuzzer (`scripts/differential-fuzz.py`).

## Mission and problem statement

Implement `instar rebase` and `instar commit` such that:

1. `instar rebase` accepts the same surface area as
   `qemu-img rebase`:
   - `[-f FMT]` to force the input image format.
   - `-b BACKING` to set the new backing file path (required).
     An empty string detaches the image from its backing chain.
   - `[-F BACKING_FMT]` to declare the new backing file's
     format. If omitted, instar probes via the standard
     format-detection path.
   - `[-u]` unsafe / metadata-only mode. See open question 3:
     the default decision is to support both `-u` and the
     default (safe) mode, with safe as the default.
   - `[-q]` quiet mode.
   - `FILENAME` positional argument naming the overlay (i.e. the
     image whose backing reference is being changed).

2. `instar commit` accepts the same surface area as
   `qemu-img commit`:
   - `[-f FMT]` to force the overlay image format.
   - `[-b BASE]` to name the target backing into which overlay
     data is merged. If omitted, the immediate parent in the
     overlay's backing chain is used.
   - `[-q]` quiet mode.
   - `FILENAME` positional argument naming the overlay (top
     image) being committed.
   - **Deferred to future work** (see "Future work" below):
     `[-d]` (drop the overlay after commit), `[-p]` (progress
     bar), `[-r RATE_LIMIT]`, `[-t CACHE]`, intermediate-image
     commits (committing through an intermediate layer of a
     deep chain).

3. All metadata mutation runs entirely inside the KVM guest,
   exactly like every other instar operation. For both
   subcommands the host opens both the overlay and the relevant
   backing files with appropriate access modes, attaches them
   to the guest as virtio-block devices, and lets the guest
   read the existing metadata, compute the patch list, and
   emit the writes. The host performs only: pre-launch
   existence + permission checks, backing-chain discovery and
   validation, post-launch result rendering, and (for commit
   without `-d`) leaving the overlay file in place.

4. For `rebase` the mutated file is the overlay
   (`FILENAME`). It is attached as the output device. The old
   backing chain and the new backing image are both attached
   as input devices for safe-mode comparison; in `-u` mode the
   guest only needs read access to the new backing's header
   to validate format compatibility.

5. For `commit` the mutated file is the backing
   (`-b BASE` or the discovered immediate parent). It is
   attached as the output device. The overlay being committed
   is attached as input device 0. The guest iterates the
   overlay's allocation map, reads each allocated cluster from
   the overlay, and writes it through to the backing at the
   same guest-virtual offset. The overlay itself is also
   modified at the end of the operation (its allocation
   entries for the committed clusters are cleared, matching
   `qemu-img commit` behaviour with no `-d`). See open
   question 5 — this requires the overlay to also be opened
   read-write.

6. The post-rebase / post-commit bytes are *equivalent* to
   what `qemu-img rebase` / `qemu-img commit` produce — not
   byte-identical (qemu-img bumps random GUIDs and timestamps;
   the diff in those fields is expected) but `instar info`,
   `qemu-img info`, and `instar check` all report identical
   metadata on the two files, and the resulting backing chain
   reads back identical guest-virtual data.

7. `instar check` on every post-rebase / post-commit image
   reports clean (no orphaned clusters, no refcount
   inconsistencies, no dangling backing references, no
   cluster-size mismatch with the new backing).

8. Coverage-guided fuzzing exercises the rebase and commit
   planners directly with `(starting_header_bytes,
   backing_metadata, options)` triples and asserts no panics,
   no integer overflow, every emitted write fits within the
   declared output bounds, and the re-parsed image is
   well-formed.

9. The existing differential fuzzer is extended so that for
   each randomly generated `(format, options, starting_chain,
   target_backing)` it runs `instar rebase` / `instar commit`
   and the equivalent `qemu-img` invocation against
   identically-seeded fixtures, then `qemu-img info
   --output=json` on the result, and asserts info-equivalence
   plus data-content equivalence.

## Open questions

The questions below need operator decisions before phase plans
are written. Each carries a working default that lets the plan
proceed if the operator wants to defer.

### 1. Combined plan, or split into PLAN-rebase + PLAN-commit?

This plan covers both subcommands in one master plan because the
shared infrastructure (host two-device wiring, the
`RebaseConfig` / `CommitConfig` ABI pattern, the chain-discovery
reuse) is substantial relative to the per-subcommand work.

**Default: keep combined.** PLAN-resize and PLAN-create both
shipped as single ~13-phase plans of similar size. Splitting
would mean either duplicating the shared-infra phase across two
plans (bad) or making one plan depend on the other (bad: blocks
parallel sub-agents from picking up commit while rebase phase
plans are being written).

### 2. Format coverage

Working assumption: **qcow2 (v2/v3) and vmdk monolithicSparse
only** for v1.

- VHD differencing disks (`disk_type=4`) and VHDX differencing
  disks have no parser support today. Adding parent-locator
  parsing is its own substantial work item (see PLAN-create's
  Future work section).
- Other vmdk subformats (`twoGbMaxExtentSparse`, `flat-only`,
  `streamOptimized`) either lack backing-chain support entirely
  or are unusual enough that they can be deferred to a vmdk-
  specific follow-up.
- raw, qed: no backing chains by format definition.

Confirm: ship v1 with qcow2 + vmdk monolithicSparse, document
the rest as "unsupported, will fail with a clear error", file
a follow-up for VHD/VHDX differencing.

### 3. Rebase: support `-u` (unsafe metadata-only) mode?

`qemu-img rebase` has two modes:

- **Unsafe (`-u`):** rewrites only the backing-file pointer in
  the overlay's header. Trusts the user that the new backing
  contains the same logical content as the old one (or that
  any divergence is intentional).
- **Safe (default):** walks the old and new backing chains
  cluster-by-cluster, identifies ranges where the old and new
  backings differ, and copies the *old* backing's data into
  the overlay for those ranges before swapping the header.

Working assumption: **support both, default to safe.**

- `-u` is small (~100 LoC of qcow2 header rewrite plus the
  equivalent vmdk descriptor rewrite) and matches qemu-img's
  surface exactly. Omitting it would be the only divergence in
  the CLI.
- Safe mode is the heavier lift (chain walking, cluster-level
  data comparison, copy-on-difference into the overlay). It is
  also where the correctness contract lives.
- The instar security model is about parser sandboxing, not
  about preventing the operator from making knowingly-unsafe
  metadata changes. Removing `-u` would just push users to
  drop down to `qemu-img` for that one operation.

Confirm: ship both, default safe, document `-u`'s risk in the
help text.

### 4. LUKS-encrypted backings / overlays

Working assumption: **defer LUKS to follow-up work.**

instar already supports decrypting LUKS-in-qcow2 on read paths
(convert with `--luks-passphrase`). Rebase/commit with LUKS in
the chain adds:

- A re-encryption pass on commit (data read out of an encrypted
  overlay, then re-encrypted before being written into an
  encrypted backing). Requires a host-side key for the
  destination.
- Backing-chain walk in safe-mode rebase against LUKS-encrypted
  parents.

This is enough novel work to belong in its own plan. v1 of
rebase/commit refuses LUKS-encrypted overlays or backings with
a clear `not yet supported` error.

### 5. Overlay treatment on `instar commit`

`qemu-img commit` always rewrites the overlay's allocation
tables so the committed clusters become unallocated (refcount
→ 0, L2 entries cleared). Without `-d`, the overlay file then
exists but is effectively empty and re-reads from the backing.
With `-d`, qemu-img also unlinks the overlay file.

Working assumption: **mirror qemu-img's default behaviour
(clear allocation, do not unlink). Defer `-d`.**

This means the overlay must be opened `O_RDWR` on the host for
commit — same as resize-without-shrink. The guest writes data
into the backing first, then clears the overlay's allocation
last (matching the resize pattern: data first, metadata last).
Atomicity is not guaranteed across both files; partial failure
is detectable by `instar check --chain` after the fact (see
open question 8).

### 6. Cluster-size mismatch between overlay and new backing

qcow2 overlays can have a different `cluster_bits` than their
backing. Rebase semantics in qemu are: the overlay's clusters
stay at the overlay's cluster size, and the safe-mode copy
operates at the overlay's cluster granularity, reading whatever
fragment of the backing covers each overlay cluster.

Working assumption: **match qemu-img exactly.** Document this
in the safe-mode design so the planner doesn't get clever and
try to align reads to the backing's cluster size.

### 7. External data files

qcow2 with `incompatible_features` external-data-file bit set
stores data outside the qcow2 metadata file. Rebase semantics
when the external data file changes: out of scope, because
qemu-img rebase explicitly refuses external-data-file images.

Working assumption: **refuse rebase/commit on qcow2 images with
external data files, matching qemu-img.** Confirm in the error
message that this is by design.

### 8. Failure handling

Working assumption: **resize-style ordering, no rollback.**

- For rebase safe-mode: write copied clusters into the overlay
  first (refcount entries, then cluster contents, then L2
  entries pointing at them), then rewrite the backing pointer
  in the overlay's header last.
- For commit: write cluster data into the backing first, then
  update the backing's allocation metadata, then clear the
  overlay's allocation last.
- On partial failure the user can run `instar check --chain`
  to diagnose. Document that mid-operation interruption leaves
  the overlay in a state that may need either re-running the
  command or running `qemu-img check -r` (we cannot offer
  `instar check --repair` yet — see PLAN-convert-followups
  phase 2).

### 9. Concurrent access

`qemu-img` uses OFD locks (`fcntl(F_OFD_SETLKW)`) to detect
in-use images. instar does not currently take advisory locks
during resize. Working assumption: **match resize — no
locking in v1.** File a follow-up to add OFD locks across all
mutating subcommands at once.

## Design overview

### Architectural shape

The work decomposes into the same four layers as resize:

1. **Per-format planners** (`src/crates/rebase/` and
   `src/crates/commit/`, both `no_std`). Given the existing
   parsed overlay header and (for rebase) the new backing
   metadata or (for commit) the backing's parsed header, the
   planner returns a typed plan: a bounded list of byte-level
   patches plus the I/O the guest must perform against the
   input device(s) before each patch becomes safe to commit.

2. **Guest operation binaries**
   (`src/operations/rebase/` and `src/operations/commit/`).
   Each reads its config from `OPERATION_CONFIG_ADDR`, reads
   the existing image header via `read_output_sector`, parses
   it, calls the relevant planner, iterates the resulting
   plan, and emits the writes via `write_output_sector` (and,
   for commit's overlay-clear pass, against the overlay device
   too). The 384 KB binary cap (`AGENTS.md` line 330) is the
   sizing constraint; resize's binary is 73 KB so we have
   plenty of headroom.

3. **Host VMM subcommands**. `run_rebase()` and `run_commit()`
   in `src/vmm/src/main.rs`. Each parses its clap surface,
   opens the relevant files with the right access modes,
   discovers the relevant backing chains via
   `discover_backing_chain`, attaches the files as virtio-block
   devices, populates the per-op `*Config`, launches the
   guest, and renders the result.

4. **Tests and fuzzers**. Integration tests covering the
   `(format × mode × chain shape × qemu-img version)` matrix;
   round-trip tests via `instar info` and `instar check`;
   coverage-guided fuzzers per planner; differential fuzzer
   comparing instar to qemu-img on identical seed fixtures.

This split keeps planners unit-testable in plain `cargo test`
and keeps the fuzz harnesses trivial. It also leaves the door
open for `snapshot` to reuse the same idioms.

### Device-attachment model

For **rebase**:

| Device slot | Image | Mode | Purpose |
|-------------|-------|------|---------|
| Output | Overlay | `O_RDWR` | Read existing header; write copied clusters (safe mode); rewrite backing pointer. |
| Input 0 | Old immediate backing | `O_RDONLY` | Safe-mode compare source. Skipped in `-u` mode. |
| Input 1..N | Older backing chain ancestors | `O_RDONLY` | Safe-mode compare: needed for clusters where the old immediate backing defers to its own backing. Skipped in `-u`. |
| Input N+1 | New immediate backing | `O_RDONLY` | Safe-mode compare target plus (both modes) header read for format-compat validation. |
| Input N+2..M | New backing chain ancestors | `O_RDONLY` | Safe-mode compare. Skipped in `-u`. |

For **commit**:

| Device slot | Image | Mode | Purpose |
|-------------|-------|------|---------|
| Output | Backing (commit target) | `O_RDWR` | Receive committed cluster data; refcount updates. |
| Input 0 | Overlay | `O_RDWR` | Read source data; clear allocation at the end. Attached via `open_chain_devices_rw` with `rw_slots=&[0]`; the guest uses `write_input_sector(0, ...)` to zero the overlay's L2 / refcount entries. |
| Input 1..N | Backing's own ancestors | `O_RDONLY` | Needed only if commit chooses to skip clusters whose overlay value equals what the backing chain already provides (open question — see below). |

Open subquestion for the commit phase plan: should commit be
"copy every allocated overlay cluster to the backing
unconditionally" or "copy only clusters where the overlay
differs from what the backing chain already returns"? The
former is simpler and matches qemu-img. The latter saves I/O
but adds chain-walking on the read path. Default: match
qemu-img (copy every allocated overlay cluster).

Commit's overlay-also-RW model is the only structural change
compared to resize. The two-device pattern (output + read-only
inputs) already exists for convert.

### Call-table extension

Three new function pointers are appended at the end of
`CallTable` in `src/shared/src/lib.rs`, with `VERSION` bumped
from 14 to 15 (see [PLAN-rebase-commit phase 1](/components/instar/plans/PLAN-rebase-commit-phase-01-abi/)
for the wire details):

- `send_rebase_result: unsafe extern "C" fn(*const RebaseResult)`
- `send_commit_result: unsafe extern "C" fn(*const CommitResult)`
- `write_input_sector: unsafe extern "C" fn(u32, u64, *const u8, usize) -> bool`

The two `send_*_result` pointers follow the exact pattern of
`send_resize_result` (resize phase 7) and `send_create_result`
(create). `write_input_sector` is the symmetric counterpart of
`read_input_sector` and is required for commit's overlay-clear
pass: commit attaches the overlay as an input device (input
slot 0) because the backing it writes the merged cluster data
into is the output, and the guest needs to clear the overlay's
L2 / refcount tables once the merge is done. Phase 1 research
surfaced this gap; the master plan originally claimed no ABI
extension was needed, which was incorrect.

For the existing I/O primitives, rebase and commit reuse
`read_output_sector` (added in resize phase 7),
`write_output_sector`, `read_input_sector`,
`get_input_device_count`, `get_input_capacity`, and
`get_input_sector_size`. The `OPERATION_CONFIG_ADDR` and
`CHAIN_CONFIG_ADDR` slots already exist; phase 1 adds
`RebaseConfig` / `RebaseResult` and `CommitConfig` /
`CommitResult` structs to `src/shared/src/lib.rs` alongside
the existing per-op config and result families.

On the host side, phase 1 also adds an `open_chain_devices_rw`
helper alongside the existing `open_chain_devices`. The new
variant takes a `rw_slots: &[usize]` parameter naming which
chain slots should be opened `O_RDWR` (and constructed with
`read_only=false` on the virtio side). For commit, slot 0 (the
overlay) is RW; every other slot stays RO. For rebase, the
helper is not used — the overlay being rebased is the output
device.

### Per-format plans

#### qcow2 rebase

- Header field rewrite (`backing_file_offset`,
  `backing_file_size`) at qcow2 header bytes 8–19. The
  backing-file path string lives at `backing_file_offset`
  in the file (typically in the first cluster after the
  header, depending on whether the original had a backing
  reference).
- Header `backing_file_format` extension (qcow2 v3 only;
  optional header extension at `header_length`).
- Safe-mode planner: walks both chains' L1/L2 allocation
  trees, identifies clusters where the old and new chains'
  guest-virtual content differs, emits a copy plan (allocate
  a new cluster in the overlay, populate it with the
  old-chain's content, update the L2 entry, bump the
  refcount).

#### qcow2 commit

- Overlay walker: iterates allocated clusters via the
  existing `walk_l2_standard` in
  `src/crates/qcow2/src/lib.rs:1110`.
- Backing writer: for each allocated overlay cluster, either
  reuses an existing backing cluster (if already allocated)
  or allocates a new one in the backing. Updates the
  backing's L2 entry and bumps the backing's refcount.
- Overlay clear-pass: zeros the overlay's L2 entries for the
  committed clusters and decrements the overlay's refcounts
  to zero. This is the only step that touches the overlay's
  metadata.

#### vmdk monolithicSparse rebase

- Descriptor rewrite at sector 1: update
  `parentFileNameHint=` to the new path, update `parentCID=`
  to the new backing's CID (read from the new backing's own
  descriptor).
- No allocation-table changes are needed — vmdk monolithic
  sparse descriptors don't carry the sort of header-internal
  pointers qcow2 does.
- Safe-mode planner: same chain-walk shape as qcow2's, but
  operating at vmdk grain granularity instead of qcow2
  cluster granularity.

#### vmdk monolithicSparse commit

- Overlay walker: existing GD/GT iteration in
  `src/crates/vmdk/src/lib.rs`.
- Backing writer: for each allocated overlay grain, write
  into the backing's grain table. Bump the backing's
  GTE-to-grain references.
- Overlay clear-pass: zero the overlay's GTE entries.

### Atomicity and ordering

Borrowing the resize idiom directly:

For rebase safe mode:

1. Allocate any new clusters needed in the overlay (refcount
   entries written first).
2. Write cluster contents.
3. Write L2 entries pointing at the new clusters.
4. Rewrite header to point at new backing (last).

For commit:

1. Write cluster contents into the backing.
2. Write backing's refcount entries.
3. Write backing's L2 entries pointing at the new clusters.
4. Clear overlay's L2 entries.
5. Zero overlay's refcounts.

In both cases, partial failure leaves the file in a state that
either (a) still references the old configuration (steps 1–3
failed) or (b) is a transient inconsistency detectable by
`instar check --chain`. We document the recovery posture
explicitly in `docs/usage.md`.

## Execution

Phase plans are written one at a time, each at the effort level
called out below. Each phase produces at least one commit.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Shared ABI: `RebaseConfig`, `CommitConfig`, `*Result` structs, `send_*_result` + `write_input_sector` call-table pointers, `GuestMessage` arms, host two-device chain plumbing | [PLAN-rebase-commit-phase-01-abi.md](/components/instar/plans/PLAN-rebase-commit-phase-01-abi/) | Complete (58f15a6) |
| 2. Rebase planners (qcow2 + vmdk, both `-u` and safe modes) | [PLAN-rebase-commit-phase-02-rebase-planners.md](/components/instar/plans/PLAN-rebase-commit-phase-02-rebase-planners/) | Complete: qcow2 unsafe + safe (6395d97, 0e4c4b9), vmdk unsafe (54caf37), vmdk safe-mode + grain allocator (step 2e), cross-format integration tests (step 2f). |
| 3. Rebase guest binary | [PLAN-rebase-commit-phase-03-rebase-guest.md](/components/instar/plans/PLAN-rebase-commit-phase-03-rebase-guest/) | Complete: error codes (f96833a), scaffold (9dd1fa3), qcow2 unsafe (fd3e338), vmdk unsafe (a47f48d), read_chain_cluster helper (74fac82), qcow2 safe-mode runner (90deff9). |
| 4. Rebase host CLI (`run_rebase`, clap args, chain wiring) | [PLAN-rebase-commit-phase-04-rebase-host.md](/components/instar/plans/PLAN-rebase-commit-phase-04-rebase-host/) | Partial: clap args + dispatch (913ce15), render + error mapping (3a39c33), pre-checks + chain discovery (dc39783), KVM lifecycle / vCPU loop (4d) + smoke tests (4e) shipped together. |
| 5. Rebase integration tests + cross-version baselines | [PLAN-rebase-commit-phase-05-rebase-tests.md](/components/instar/plans/PLAN-rebase-commit-phase-05-rebase-tests/) | Complete: base.py helpers (546d8fd), error + success-path scaffolding (837006a), qcow2 success paths run end-to-end, cross-version baselines in instar-testdata (c10c499d9/3e9c11f3b), TestRebaseRoundTrip + vmdk success path (db85b9e), TestRebaseBaselineMatrix (3f3a0bf). Enablement fix for safe-mode end-to-end landed alongside as 6fe3d56. |
| 6. Commit planners (qcow2 + vmdk) | [PLAN-rebase-commit-phase-06-commit-planners.md](/components/instar/plans/PLAN-rebase-commit-phase-06-commit-planners/) | Complete: scaffold (34333e9), qcow2 commit planner + backing allocator (da0b974), vmdk commit planner + grain allocator (db4d389), cross-format integration tests (26ca765). |
| 7. Commit guest binary | [PLAN-rebase-commit-phase-07-commit-guest.md](/components/instar/plans/PLAN-rebase-commit-phase-07-commit-guest/) | Complete: error codes (0de7e2c), scaffold (b9821bb), qcow2 commit runner (b02d143), vmdk commit runner (db3ed0d). |
| 8. Commit host CLI (`run_commit`, clap args, overlay-RW wiring) | [PLAN-rebase-commit-phase-08-commit-host.md](/components/instar/plans/PLAN-rebase-commit-phase-08-commit-host/) | Complete: plan + master link (6af197d), clap args + dispatch stub (9a3561a), render + error mapping (c24c22f), pre-checks + chain discovery (9cbe75c), KVM lifecycle + vCPU loop (a72f351), test_commit.py smoke tests (e425618). Phase 7 commit-op output bounce fix landed alongside as b7dc9c7. |
| 9. Commit integration tests + cross-version baselines | [PLAN-rebase-commit-phase-09-commit-tests.md](/components/instar/plans/PLAN-rebase-commit-phase-09-commit-tests/) | Complete: plan (9be9e66), instar-testdata generator extension (1f2cc83b1) + 640 cross-version baselines (ccaf1af3d), TestCommitBaselineMatrix (8baa56d), TestCommitRoundTrip (ba16ca9). |
| 10. Coverage-guided fuzzing (rebase + commit planners) | [PLAN-rebase-commit-phase-10-fuzz.md](/components/instar/plans/PLAN-rebase-commit-phase-10-fuzz/) | Complete: plan (af25ec6), rebase vmdk planner bug fix (1611841) + fuzz_rebase_planners (a2935bb), fuzz_commit_planners (95f632c), CI wiring + wrap-up (this step). |
| 11. Differential fuzzing vs qemu-img | [PLAN-rebase-commit-phase-11-diff-fuzz.md](/components/instar/plans/PLAN-rebase-commit-phase-11-diff-fuzz/) | Complete: plan (1518b30), op_rebase (47db188), op_commit (0649c4c), wrap-up (this step). |
| 12. Documentation, CHANGELOG, ARCHITECTURE updates | [PLAN-rebase-commit-phase-12-docs.md](/components/instar/plans/PLAN-rebase-commit-phase-12-docs/) | Complete: plan (6fe5888), per-subcommand guides (944788f), cross-cutting updates (e29b3a5), wrap-up (this step). **All twelve phases of PLAN-rebase-commit are now complete.** |

Recommended planning effort by phase:

- Phase 1 (ABI): **medium**. The pattern is identical to
  `CreateConfig` / `ResizeConfig` — mostly mechanical, but
  the two-device chain plumbing is novel enough to warrant
  reading the existing chain-config code carefully.
- Phase 2 (rebase planners): **high**. Safe-mode chain
  comparison is the trickiest correctness work in this plan.
- Phase 3 (rebase guest): medium. Mostly orchestration over
  the phase 2 planners.
- Phase 4 (rebase host): medium.
- Phase 5 (rebase tests): medium. Baseline harness pattern is
  well-established.
- Phase 6 (commit planners): **high**. Refcount management
  against two different files (overlay + backing) is the
  novel risk.
- Phase 7 (commit guest): medium.
- Phase 8 (commit host): medium. Two-device RW is a slight
  variation on convert's chain handling.
- Phase 9 (commit tests): medium.
- Phase 10 (fuzz): medium.
- Phase 11 (diff fuzz): **high**. Designing fixtures that
  exercise edge cases (cluster-size mismatch, deep chains,
  empty overlays) without flooding the fuzzer with trivial
  inputs needs thought.
- Phase 12 (docs): low.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this conversation)
is reserved for planning, review, and decision-making. This
keeps the management context lean and avoids drowning it in
implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the phase plan, at the recommended effort level
   and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files — the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether
   the brief was insufficient (improve it) or the model was
   too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied with the
   result.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental. For safe, well-understood changes,
sub-agents can work directly in the main tree.

### Planning effort

The master plan itself was created at high effort. Each phase
plan should be created at the effort level called out in the
Execution table above. When in doubt, skew higher.

### Step-level guidance

Each phase plan must include a step table of the shape defined
in `PLAN-TEMPLATE.md`:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

For this plan in particular, model choice should default to
opus for any step that crosses the host/guest boundary (it
needs to hold both the call-table ABI and the relevant format
spec in context simultaneously) and for any step that touches
refcount or allocation-table mutation. Sonnet is fine for
purely host-side clap / file-handling work and for test
authoring once the baseline harness is established.

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes` (384KB
      limit per operation).
- [ ] `make test-rust` and the relevant
      `make test-integration` targets pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] For any phase that touches the host/guest boundary:
      the `instar info` output of a freshly-rebased or
      freshly-committed image matches what `qemu-img info`
      reports on the equivalent qemu-img output, modulo the
      whitelisted non-deterministic fields.
- [ ] Commit message follows project conventions (including
      the Co-Authored-By line with model, context window,
      effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes` (384KB limit).
* All Rust unit tests pass (`make test-rust`).
* All Python integration tests pass (`make test-integration`),
  including new `test_rebase.py` and `test_commit.py`.
* `pre-commit run --all-files` passes.
* `instar rebase` and `instar commit` accept the documented
  CLI surface and refuse anything outside it with a clear
  error.
* For every supported `(format, mode, chain shape)`
  combination, `qemu-img info` on `instar`-produced output
  matches `qemu-img info` on `qemu-img`-produced output,
  modulo the whitelisted non-deterministic fields.
* `instar check --chain` on every post-rebase / post-commit
  image is clean.
* Coverage-guided fuzz targets exist for the rebase and
  commit planners and have been run for at least 24 hours
  without finding new defects.
* The differential fuzzer covers
  `(format × options × chain × operation)` combinations and
  passes a baseline corpus.
* `docs/usage.md` documents both subcommands with examples,
  flag reference, and the failure-mode guidance from open
  question 8.
* `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, and
  `CHANGELOG.md` have been updated as needed.
* `docs/plans/PLAN-convert-followups.md` is updated to mark
  rebase and commit as shipped (matching the existing
  `~~create~~ / ~~measure~~ / ~~resize~~` pattern).

### Future work

Items beyond the twelve phases above:

- **Phase 5 deferrals** (steps 5d, 5e, 5f shipped together;
  remaining items below are scope reductions inside the
  shipped surface):
  - Cross-format rebase baselines (qcow2 ↔ vmdk). qemu-img
    rebase only supports qcow2, so the matrix is qcow2-only
    today. When the planner relaxes the format-match check
    and instar grows its own vmdk-aware reference path,
    cross-format baselines can be regenerated independently.
  - VMDK round-trip vs qemu-img. qemu-img rebase returns
    "Operation not supported" for vmdk on every shipped
    version, so the vmdk round-trip test skips. Phase 5's
    `test_vmdk_unsafe_rebase_records_new_backing` instead
    asserts `qemu-img info` reports the new
    backing-filename, which is the closest meaningful
    contract available.
- **Phase 4 deferrals** (none — phase 4 is fully shipped):
  - Steps 4d (`run_rebase_guest` KVM lifecycle) and 4e
    (smoke tests for qcow2 unsafe rebase and detach)
    landed together. The qcow2 in-place success paths run
    end-to-end; the vmdk and qemu-img round-trip cases
    are tracked under phase 5 step 5f.
- **Phase 3 deferrals** (steps 3e and 3f shipped together
  alongside the rest of phase 3; remaining items below are
  scope reductions inside the shipped surface):
  - vmdk safe-mode rebase guest path. Phase 2 step 2e
    shipped the planner-side grain allocator, but the
    guest runner still only dispatches qcow2 in safe mode
    and returns `ERROR_UNSUPPORTED_FORMAT` for vmdk-safe.
  - Promote `read_chain_cluster` to a shared crate once
    commit (phase 7) needs the same primitive. v1 keeps it
    local to the rebase binary; the second consumer is the
    trigger to refactor.
  - Larger images. The safe-mode runner currently caps at
    `cluster_size ≤ 1 MiB`, `staged_l2_count ≤ 256`,
    `refblock_count ≤ 2048`. Realistic ~16 GiB qcow2s with
    default geometry fit comfortably; the caps reject
    larger images with `ERROR_SCRATCH_TOO_SMALL`. A future
    follow-up can grow the scratch carve or switch L2 to
    read-on-demand (open question 2 option A).
- **Phase 2 deferrals** (steps 2e and 2f shipped together
  alongside the rest of phase 2; the remaining items below
  are scope reductions inside the shipped surface):
  - Long-path relocation in both qcow2 modes. When the new
    backing path doesn't fit the existing slot, both modes
    reject with `BackingPathTooLong`. Wiring the allocator
    into the metadata-patch construction is mechanical but
    bundled together with phase 3 review.
  - qcow2 refcount widths other than 16 in the allocator.
    1/2/4/8/32/64 bit are rare; the bit-packing reference
    is `qcow2::lookup_refcount`.
  - qcow2 backing-format header extension rewrite. When the
    user passes `-F BACKING_FMT`, rebase should emit a
    patch for the extension block.
  - vmdk safe-mode GD extension. The step 2e allocator only
    fills GTEs in GTs that already exist; allocating a new
    GT (and bumping the GD entry) when the covering GDE is
    zero is a follow-up. Until that lands, the safe-mode
    guest must fall back to `-u` for overlays whose
    pre-existing GD coverage is incomplete.
- VHD differencing and VHDX differencing backing-chain
  support. Requires parent-locator parsing in the respective
  format crates first. Track under PLAN-convert-followups
  alongside the differencing-output work from PLAN-create's
  Future work.
- LUKS-encrypted overlay / backing support for both
  subcommands. Needs a re-encryption pipeline that goes
  beyond what convert does today.
- `commit -d` (drop the overlay after a successful commit).
  Trivial to add as a host-side `unlink` after a successful
  guest run, but skipped from v1 to keep the failure-mode
  semantics simple.
- `commit -p` progress reporting. Reuse the existing
  `send_progress` call-table function pointer.
- Intermediate-image commit (`qemu-img commit -b BASE TOP`
  where BASE is two or more layers below TOP). The chain-
  discovery path already walks deep chains; the planner
  needs to handle merging across multiple intermediates.
- OFD advisory locking across all mutating subcommands
  (resize, rebase, commit). Worth doing once, across all
  three, in a single PLAN-locking effort.
- `instar rebase --check-only` (read-only mode that prints
  what safe-mode rebase would do without writing anything).
  Useful for operator confidence in production.
- An aggregate `instar repack` operation that combines
  commit + rebase + sparsify into one pass over a chain.
  Out of scope but easy once the primitives exist.
- **Commit scratch budget for large clusters.** The commit
  guest binary's `OVERLAY_RT_LIMIT` and `BACKING_RT_LIMIT`
  scratch regions are sized at `MAX_SECTOR_SIZE` (64 KiB),
  so a single-cluster refcount table for any
  `cluster_size > 64 KiB` overflows the budget and returns
  `ERROR_SCRATCH_TOO_SMALL`. The differential fuzzer picker
  caps `cluster_size` at 64 KiB to match. Lifting needs the
  scratch carve to size against the largest supported
  cluster (~2 MiB), matching what the rebase planner
  already does.
- **Vmdk implicit-`-b` for commit.** The host info
  operation doesn't currently expose vmdk monolithicSparse's
  `parentFileNameHint` via the `backing_file` field, so the
  host-side `-b`-against-recorded-parent check refuses
  every vmdk commit without an explicit `-b`. Phase 9's
  matrix + round-trip vmdk cases all pass an explicit
  `-b base.vmdk`; the implicit form will start working
  unchanged once the info-side gap is closed. Tracked
  separately under PLAN-info's vmdk follow-ups; mirrored
  here so the rebase/commit Future-work surface is
  complete.
- **Cross-cluster-size rebase.** Safe-mode rebase
  currently requires the old and new backing's qcow2
  cluster sizes to match. If they differ the planner
  refuses with `ERROR_NEW_BACKING_INCOMPATIBLE`. Lifting
  needs cluster-size adapters in the safe-mode allocator
  and the per-cluster comparison loop.
- **Targeted seed corpora for `fuzz_rebase_planners` and
  `fuzz_commit_planners`.** Both targets shipped without
  seed corpora (matching the resize target's shipping
  shape). The `scripts/generate-fuzz-seeds.py`
  infrastructure can walk the existing testdata and emit
  `(starting_header_bytes, backing_metadata, options)`
  tuples for both planners; out of scope for v1, queued
  alongside the resize-side equivalent.
- **Widen fuzz harness clamps.** Both rebase and commit
  planner fuzz targets currently mask `refblock_count`
  and `allocated_gt_count` at 4 bits (cap 15), so the
  `> MAX_REFBLOCKS` and `refblock_host_offsets.len() !=
  refblock_count` mismatch shapes are never explored.
  Widening the masks or adding a separate boundary-shape
  arm would surface the same class of bug as the
  `RebasePlan::new(0)` finding from phase 10.

### Bugs fixed during this work

This section will list any bugs encountered during development
that were fixed. To be filled in as work progresses.

### Documentation index maintenance

When this plan was created:

* `docs/plans/index.md` — added a row to the *Master plans*
  table with date 2026-05-30, intent summary, status
  `Not started`, and a placeholder phase list.
* `docs/plans/order.yml` — added a line for
  `PLAN-rebase-commit.md` after `PLAN-resize.md`.
* `docs/plans/PLAN-convert-followups.md` — updated the
  subcommand-reference and execution sections to mark
  `rebase` and `commit` as scheduled under this new plan
  (analogous to the existing `~~create~~ / ~~measure~~ /
  ~~resize~~` notations).

When all phases of this plan are complete:

* Update the status column in `docs/plans/index.md` to
  *Complete*.
* Update `docs/plans/PLAN-convert-followups.md` to strike
  through `rebase` and `commit` matching the existing
  shipped-subcommand pattern.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
