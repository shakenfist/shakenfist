# PLAN-snapshot phase 06: create mode (MODE_CREATE)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the phase 5 mutator
primitives in `src/crates/snapshot/src/qcow2.rs`; the commit
guest binary `src/operations/commit/src/main.rs`, which is the
canonical staging / writeback / RMW-bounce-buffer reference for
a mutating qcow2 guest; the phase 3 snapshot guest binary
`src/operations/snapshot/src/main.rs`; the phase 4 host CLI
`run_snapshot` / `run_snapshot_list` in `src/vmm/src/main.rs`;
the streaming snapshot-table parser `for_each_snapshot_entry`
in `src/crates/qcow2/src/lib.rs`), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question
touches on qcow2 spec details or qemu behaviour, the
authoritative references are `block/qcow2-snapshot.c`
(`qcow2_snapshot_create`, `qcow2_write_snapshots`,
`find_new_snapshot_id`) and `block/qcow2-refcount.c`
(`qcow2_update_snapshot_refcount`, `qcow2_alloc_clusters`) in
qemu 10.0.x — fetch from
`https://gitlab.com/qemu-project/qemu/-/raw/v10.0.0/block/...`
if no local checkout is available — plus the locally installed
`qemu-img` 10.0.8 binary for empirical verification.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 6 of
fourteen — the first phase that mutates a real image.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Phases 1–5 landed: wire ABI (1), streaming snapshot-table
parser (2), list-mode guest binary (3), list-mode host CLI (4),
and the pure refcount / COPIED-flag mutator primitives in the
new `src/crates/snapshot/` crate (5). Phase 6 composes the
phase 5 primitives into the first mutating mode: `instar
snapshot -c NAME` (MODE_CREATE).

### Empirically established qemu-img behaviour

These facts were verified against the locally installed
`qemu-img` 10.0.8 during phase 6 planning (creating snapshots
on a fresh 64 MiB qcow2 and decoding the resulting file with a
Python struct walker). They are requirements, not guesses:

1. **Success is silent.** `qemu-img snapshot -c NAME img`
   prints nothing and exits 0.
2. **Duplicate names are allowed.** Creating `foo` twice
   succeeds and yields two snapshots both named `foo`, with
   IDs `1` and `2`. There is no "already exists" error for
   `qemu-img snapshot -c` (that message belongs to HMP
   `savevm`). The master plan's create step 2 ("refuse on
   duplicate name") is **wrong** and is corrected by this
   phase. `ERROR_DUPLICATE_NAME` stays reserved in the ABI.
3. **ID assignment** is `max(numeric value of existing IDs) +
   1`, rendered as a decimal string. First snapshot gets `"1"`.
4. **On-disk entry layout** written by qemu-img 10.0.8:
   40-byte header, then **24 bytes of extra data**
   (`vm_state_size_large = 0`, `disk_size` = the image's
   virtual size in bytes, **`icount = 0`** — not the
   `u64::MAX` "absent" sentinel; `qemu-img.c::img_snapshot`
   memsets the whole `QEMUSnapshotInfo`), then the id string,
   then the name string. Each entry *starts* at an 8-byte
   aligned offset within the table; the gap bytes are zero.
   The file ends immediately after the last entry's name —
   **no trailing pad** (observed file size `0x7008c`, not
   rounded to 8).
5. **`vm_clock_nsec = 0`, `vm_state_size = 0`** for qemu-img
   created snapshots. `date_sec`/`date_nsec` come from
   gettimeofday; `date_nsec` is `tv_usec * 1000` so it is
   always a multiple of 1000.
6. **The snapshot table is fully re-written to a new
   allocation on every create** (`qcow2_write_snapshots`):
   compute the new table's byte size, allocate contiguous
   clusters, write all entries (old + new), flush, write the
   header's `nb_snapshots` + `snapshots_offset` as a single
   12-byte write at header offset 60, flush, then free the
   old table's clusters. There is no "append in place even if
   there's room" path. Observed: after the second create the
   table had moved from `0x50000` to `0x70000` and `0x50000`
   was free.
7. **Allocation is first-fit from the start of the refcount
   table.** Each `qemu-img` invocation starts its
   `free_cluster_index` at 0, so single-shot invocations
   allocate the lowest-numbered free cluster. New allocations
   land past EOF (the file grows) when the image is densely
   packed, which is the normal case.

### The create algorithm (qemu-faithful)

Mirrors `qcow2_snapshot_create` + `qcow2_write_snapshots`:

1. Refuse unsupported images (see "Feature gates" below).
2. Stream existing snapshot entries: compute `max_id` and
   the old table's exact byte length. Refuse if
   `nb_snapshots >= 16` (instar v1 cap; qemu allows 65536 —
   documented quirk).
3. Assign the new ID = `max_id + 1` as a decimal string.
4. Allocate `ceil(l1_size * 8 / cluster_size)` **contiguous**
   clusters for the snapshot's L1 copy.
5. Write the active L1's bytes verbatim to the copy
   (retaining the current COPIED bits — qemu copies before
   the refcount pass, so the stored copy keeps stale COPIED
   flags; `qemu-img check` does not validate snapshot L1
   flags).
6. Refcount pass over the active L1 (addend +1): every
   allocated data cluster **and every L2 table cluster**.
   Two-pass: dry-run first (abort on overflow without
   mutating), then apply. This is phase 5's
   `update_snapshot_refcount` — *extended by this phase to
   cover L2 table clusters* (see open question 2).
7. COPIED-flag rewrite over the active L1/L2 (phase 5's
   `update_copied_flags_for_l1`): every entry whose cluster
   refcount is now > 1 gets COPIED cleared.
8. Serialise the new snapshot table: old entries byte-for-
   byte verbatim (preserving unknown extra data), new entry
   appended per fact 4 above. Allocate contiguous clusters
   for it.
9. Writeback group A: L1 copy, dirty L2 clusters, rewritten
   active L1, dirty refblocks (covering the data/L2
   increments and the new allocations). `fsync_input(0)`.
10. Writeback group B: the new snapshot table.
    `fsync_input(0)`.
11. Writeback group C (the commit point): 12 bytes at header
    offset 60 — `nb_snapshots` (u32 BE) + `snapshots_offset`
    (u64 BE). `fsync_input(0)`.
12. Writeback group D: decrement the old table's clusters to
    0 in the staged refblocks and write those refblocks back.
    Skipped when there was no old table (`snapshots_offset ==
    0` / `nb_snapshots == 0`).

The barrier ordering gives the same crash-safety contract as
qemu: a crash before group C leaves the old table authoritative
(new clusters are orphaned garbage, `qemu-img check` reports
leaks, not corruption); a crash after group C leaves the new
table authoritative (the old table's clusters leak until group
D). Leaks are repairable; dangling references are not.

### Feature gates (checked guest-side before any mutation)

- Not qcow2 → `ERROR_UNSUPPORTED_FORMAT` (existing phase 3
  check).
- `crypt_method != 0` → `ERROR_UNSUPPORTED_FEATURE`.
- External data file (`has_external_data`) →
  `ERROR_UNSUPPORTED_FEATURE`.
- zstd compression (`compression_type != 0`) →
  `ERROR_UNSUPPORTED_FEATURE`.
- zlib compressed clusters (no header bit — detected during
  the L2 staging walk when any entry classifies
  `Compressed`) → `ERROR_UNSUPPORTED_FEATURE` (master plan
  source-format scope; refcounting a compressed extent needs
  the multi-cluster walk deferred to future work).
- Dirty (`incompatible_features` bit 0) or corrupt (bit 1)
  → `ERROR_UNSUPPORTED_FEATURE`. qemu auto-repairs dirty
  lazy-refcount images on RW open; instar v1 refuses instead
  — refcounts in a dirty image are not trustworthy and we
  must not mutate on top of them. Documented quirk.
- Bitmaps extension (autoclear feature bit 0, raw header
  bytes 96..104 — `QcowHeader` does not currently surface
  autoclear bits, read them from the staged header sector)
  → `ERROR_UNSUPPORTED_FEATURE`.
- `refcount_bits != 16` → `ERROR_UNSUPPORTED_FEATURE` for
  MODE_CREATE only. The phase 5 allocator is 16-bit-only (the
  qemu-img default since v3 was introduced; v2 is always 16).
  Documented quirk.
- `nb_snapshots >= 16` → `ERROR_SNAPSHOT_TABLE_FULL`
  (instar v1 cap; documented quirk).
- Staging bounds exceeded (L1 > 64 KiB, more than 256 L2
  tables, more than 32 refblocks, old snapshot table >
  64 KiB) → `ERROR_UNSUPPORTED_FEATURE`. Same bounds and
  posture as the commit binary.

Extended L2 (subcluster) images are **supported**: refcount
semantics are identical at L2-entry granularity, the phase 5
helpers take an `extended_l2` flag throughout, and the L1/L2
bytes are copied verbatim (bitmaps included).

qcow2 v2 and v3 are both supported. Images with a backing file
are supported (refcounts only touch this image's clusters).

### File growth

New clusters (L1 copy, new snapshot table) normally land past
the current EOF. The rebase / commit host paths already solve
this: `BackingStore::open_rw_existing(path,
Some(capacity_hint))` with a generous hint
(`file_size * 2`, min 1 GiB) lets the guest write past EOF
through the virtio boundary and the file grows on demand
(`src/vmm/src/main.rs:3558-3563`). The phase 6 host dispatch
(open question 1) opens the image the same way. The guest
itself never needs to know the file size: allocation is driven
purely by zero refcount entries within the staged refblocks,
which is exactly qemu's definition of "free".

### What phase 6 produces

1. **ABI**: `SnapshotConfig` gains `date_sec: u32` and
   `date_nsec: u32`, carved from the front of `_reserved`
   (`[u8; 32]` → `[u8; 24]`; total size, alignment, and all
   existing field offsets unchanged — verified by the existing
   layout unit tests, extended). The host populates them from
   `SystemTime::now()`; `date_nsec` is truncated to
   microsecond precision × 1000 to match qemu-img's
   `tv_usec * 1000` exactly.
2. **Snapshot crate additions** (`src/crates/snapshot/`):
   - `alloc_contiguous_clusters_in_refblocks(...)` — n
     consecutive zero-refcount clusters, first-fit, sets each
     to 1. The single-cluster `alloc_cluster_in_refblocks`
     becomes a thin wrapper (count = 1).
   - `update_snapshot_refcount` / dry-run / apply passes
     extended to also adjust **each L2 table cluster's**
     refcount (open question 2 — a phase 5 correctness gap
     vs qemu).
   - `serialize_snapshot_entry(&NewSnapshotEntry, out: &mut
     [u8]) -> Result<usize, SnapshotError>` — 40-byte header
     + 24-byte extra data + id + name; returns the unpadded
     byte length.
   - `snapshot_table_byte_len(table: &[u8], nb_snapshots:
     u32) -> Result<usize, SnapshotError>` — walks raw table
     bytes (8-aligned entry starts) and returns the table's
     total byte length, so the guest can stage / copy / free
     it. Walks raw headers directly — independent of the
     bounded parser's 63-char id/name truncation.
   - `build_snapshot_table(old_table: &[u8], old_len: usize,
     new_entry: &[u8], new_entry_len: usize, out: &mut [u8])
     -> Result<usize, SnapshotError>` — verbatim copy of the
     old entries + 8-aligned append of the new entry; returns
     the new table's byte length (unpadded after the last
     entry, matching qemu).
   - `parse_decimal_id(&[u8]) -> Option<u64>` and
     `format_decimal_u64(u64, &mut [u8]) -> usize` — ID
     assignment helpers.
   - ~40 new unit tests (see step briefs).
3. **Guest binary**: `src/operations/snapshot/src/main.rs`
   MODE_CREATE replaces the phase 3 stub. Commit-style scratch
   staging (header, raw old snapshot table, active L1, L2
   staging with index array, refcount table, refblock offsets
   array, refblocks, new-table build buffer, RMW bounce
   buffer), feature gates, the 12-step algorithm above, RMW
   byte-range read/write helpers on input slot 0 (modelled on
   commit's `read_output_byte_range` / `write_output_byte_
   range`), `SnapshotResult` with `assigned_id` populated.
4. **Host CLI** (open question 1): minimal `-c` dispatch —
   `run_snapshot_create` modelled on `run_snapshot_list` but
   opening the image RW with a capacity hint, populating
   `mode = MODE_CREATE`, `arg` = name, and the new date
   fields. Silent on success (matching qemu-img), error
   mapping through the existing `snapshot_error_message`.
   `-a` / `-d` keep their "arrives in phase 9" message.
5. **Docs**: quirks (duplicate names allowed; 16-snapshot
   cap; `refcount_bits != 16` refusal; dirty-image refusal;
   compressed-cluster refusal), `docs/qcow2/
   qcow2-snapshots.md` mutator-surface additions, master-plan
   execution-table updates (including fixing the stale
   "Not started" status on the already-landed phases 3 and 4).

### What phase 6 does not change

- The wire protobuf (`SnapshotEntryMessage` /
  `SnapshotResultMessage`) — frozen since phase 1. The
  `SnapshotResult.assigned_id` field has existed since
  phase 1.
- The qcow2 crate.
- List mode (guest or host) — byte-identical behaviour.
- MODE_DELETE / MODE_APPLY stubs (phases 7 / 8).
- `SnapshotPatch` / `SnapshotPlan` (see open question 5).

## Mission and problem statement

After phase 6 lands, on any supported qcow2 image:

```
instar snapshot -c snap1 image.qcow2   # silent, exit 0
qemu-img check image.qcow2             # clean, no leaks
qemu-img snapshot -l image.qcow2       # shows ID 1, TAG snap1
instar snapshot -l image.qcow2         # byte-identical to qemu-img
```

and the image is structurally equivalent to one produced by
`qemu-img snapshot -c snap1` on a copy of the same input:
identical `qemu-img info` output (snapshot list included)
modulo the `date` fields, identical `qemu-img check` summary,
identical ID assignment, and a second create assigns ID 2 even
with a duplicate name.

## Open questions

### 1. Pull the minimal `-c` host dispatch into phase 6?

The master plan schedules all mutating-mode host CLI work in
phase 9. Taken literally, phases 6–8 would accumulate three
guest-side mutating modes that cannot be executed end-to-end
until phase 9 — the riskiest code in the plan family would sit
unvalidated against real images for three phases, and any
ordering or staging bug would surface as a phase 9 big-bang.

**Working answer: yes, deviate.** Phase 6 lands a minimal
`run_snapshot_create` (RW open with capacity hint + config
population + silent success + error mapping — ~80 lines
modelled on `run_snapshot_list`), so the phase is validated
end-to-end by `qemu-img check` on real post-op images before
it ships. Phases 7 and 8 do the same for `-d` / `-a`. Phase 9
shrinks to consolidation (shared open-path helper, success/
quiet semantics review, error-message polish, master-plan
state). This mirrors the phase 5 precedent of overturning a
master-plan call when the evidence is in front of us; the
master plan's phase 9 row is annotated accordingly by step 6g.

### 2. Extend `update_snapshot_refcount` to cover L2 table clusters

Phase 5's `update_snapshot_refcount` walks only **data**
clusters. qemu's `qcow2_update_snapshot_refcount` also
adjusts the refcount of **each L2 table cluster** reachable
from the L1 — and must, because after a create the active L1
and the snapshot's L1 copy share the same physical L2 tables.
Without the L2 bump, a post-create guest write through the
active L1 would see refcount 1 on the L2 cluster, skip the
COW, and modify the snapshot's L2 in place — silent snapshot
corruption. This is a latent phase 5 gap (the master plan's
pseudocode includes the L2-table update; the phase 5 briefs
dropped it).

**Working answer: fix it in the existing function, always-on
(no flag).** All three op variants need it (delete decrements
L2 tables, apply swaps them). The dry-run pass checks L2
table clusters for overflow exactly like data clusters. The
L1 table's own clusters are *not* covered (qemu's function
doesn't either — the caller owns L1-cluster refcounts: create
allocates the copy at refcount 1; delete frees the snapshot's
L1 explicitly). New unit tests pin all of this, including
"dry-run detects overflow on an L2 table cluster without
mutating".

### 3. Always reallocate the snapshot table (vs append in place)

The master plan's create step 8 suggested appending in place
when the existing table has room. qemu never does this — 
`qcow2_write_snapshots` always writes a fresh table and frees
the old one (empirical fact 6).

**Working answer: always reallocate, qemu-faithful.** It is
also the better crash-safety story: the old table stays
intact until the header pointer flips. The "in place" path
would save one cluster of churn at the cost of a divergent
layout and a second code path.

### 4. Duplicate-name behaviour

**Resolved empirically: allow duplicates, matching qemu-img
10.0.8** (fact 2). `ERROR_DUPLICATE_NAME` remains reserved in
the ABI (savevm-style semantics could use it later). The
master plan's contrary claim is corrected by step 6g. The
phase 13 differential fuzzer depends on this match — refusing
where qemu succeeds would diverge on every dup-name chain.

### 5. Does phase 6 use `SnapshotPatch` / `SnapshotPlan`?

**Working answer: no.** The create writeback needs fsync
barriers *between* write groups, which a flat patch list
cannot express, and the guest writes each staged region
directly (commit-binary style). The types stay as landed; if
phases 7/8 also end up not using them, phase 14 decides
whether to remove them or document them as reserved.

### 6. Snapshot L1 copy keeps stale COPIED bits

qemu writes the L1 copy *before* the refcount/flag pass and
never revisits it, so the stored copy's entries keep COPIED
set even though refcounts are now 2 (fact: `qemu-img check`
only validates the *active* L1/L2 flags; apply refreshes
flags when the snapshot is gone back to).

**Working answer: copy the active L1 verbatim from the
pre-rewrite staged bytes, exactly like qemu.** The guest must
therefore serialise the L1 copy (or snapshot the staged L1
buffer) *before* running `update_copied_flags_for_l1` on the
active L1.

### 7. Where does the timestamp come from?

The guest has no wall clock. **Working answer: host-supplied
via two new `SnapshotConfig` fields** (`date_sec: u32`,
`date_nsec: u32`) carved from `_reserved`. u32 seconds matches
the on-disk field width (qemu wraps in 2106 too). The host
truncates nanoseconds to `microseconds * 1000` to match
qemu-img byte-for-byte. List mode leaves them zero. No magic
or version bump: offsets of all existing fields are unchanged
and the struct is host→guest-in-lockstep (both sides ship in
one binary release).

### 8. Zero-length L1 (`l1_size == 0`)

A 0-byte virtual disk has `l1_size = 0`; the L1 copy would be
a zero-byte allocation. qemu allocates 0 bytes (returns a
valid offset without consuming clusters). **Working answer:
allocate zero clusters, write `l1_table_offset` as the next
free-cluster offset the allocator *would* have returned, and
let the smoke matrix include a 0-byte image to confirm parity
with qemu-img.** If qemu-img errors on this case, match its
error instead — the implementing agent verifies empirically
and documents the result in the back-brief.

### 9. New-entry name length bound

`SnapshotConfig.arg` is 256 bytes; qemu's `QEMUSnapshotInfo
.name` is `char[256]` (255 usable). The host CLI already
rejects nothing today; the guest serialiser takes the name
from `arg[..arg_len]` as-is. **Working answer: cap at 255
bytes host-side with a clear error** (matching qemu's
truncation boundary, but failing loudly instead of silently
truncating — divergence noted in quirks if qemu truncates).
The implementing agent verifies what `qemu-img snapshot -c`
does with a ≥256-char name and matches the observable
behaviour where reasonable.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | worktree | ABI: add the timestamp fields to `SnapshotConfig` in `src/shared/src/lib.rs`. (i) Replace `_reserved: [u8; 32]` with `date_sec: u32`, `date_nsec: u32`, `_reserved: [u8; 24]` (in that order, immediately after `arg`). Document that they carry the host's wall-clock at invocation for `MODE_CREATE` (qcow2 on-disk `date_sec`/`date_nsec`), are zero for other modes, and that `date_nsec` is microsecond-truncated (`usec * 1000`) to match `qemu-img`. (ii) Extend the existing `SnapshotConfig` layout/size unit tests in `src/shared/src/lib.rs` to pin: total size unchanged (312), `arg` offset unchanged, `date_sec` at offset 280, `date_nsec` at 284. (iii) Update the host's per-field config writer in `run_snapshot_list` (`src/vmm/src/main.rs`, the "Write SnapshotConfig (per-field at known offsets)" block) to write zeros for the new fields and adjust its offset-map comment. (iv) Update the guest binary's config consumption only if it materialises the struct (it reads in place — likely a no-op). `cargo test -p shared`, `make instar` clean. |
| 6b | high | opus | worktree | Extend `update_snapshot_refcount` (and `dry_run_refcount_pass` / `apply_refcount_pass`) in `src/crates/snapshot/src/qcow2.rs` to also adjust each **L2 table cluster's** refcount, per open question 2. (i) In both passes, after iterating an L1 entry's L2 entries, apply the same read → check → (set) sequence to the L2 table's own host offset (`raw & L1_OFFSET_MASK`), using the same `refblock_byte_offset_for_cluster` mapping. Mirror qemu's `qcow2_update_snapshot_refcount` in `block/qcow2-refcount.c` — read it first; note that qemu updates the L2 refcount once per L1 entry, and does NOT touch the L1 table's own clusters (callers own those). (ii) Update the function's doc comments to state the L2-table coverage and the L1-cluster exclusion explicitly. (iii) Add ~8 unit tests: increment bumps the L2 table cluster's refcount by exactly 1 per create; decrement reverses it; SwapForApply nets the L2 clusters of from/to correctly including the shared-L2 case; dry-run detects overflow on an L2 table cluster (refcount at max) and leaves `refblocks` byte-identical (reuse the existing byte-identity snapshot-compare pattern at the existing `refcount_dry_run_aborts_on_overflow_without_mutation` test); an L1 with two entries pointing at two different L2s bumps both; an unallocated L1 entry contributes no L2 bump. (iv) Audit the existing ~60 tests: any test that asserts post-apply refblock bytes must be updated for the new L2-table bumps — update them deliberately, never by blind snapshotting; each updated expectation gets a one-line comment saying the L2-table cluster is now counted. Use opus: this corrects a phase 5 correctness gap; getting the per-L1-entry-exactly-once semantics wrong corrupts snapshots invisibly. |
| 6c | high | opus | worktree | New pure helpers in `src/crates/snapshot/src/qcow2.rs` (or a new `src/crates/snapshot/src/table.rs` module if `qcow2.rs` gets unwieldy — agent's choice, declared in lib.rs either way). (i) `pub fn alloc_contiguous_clusters_in_refblocks(blocks: &mut [u8], cluster_size: u64, refcount_bits: u32, refblock_count: u64, host_refblocks_start: u64, count: u64, cursor: &mut AllocCursor) -> Result<u64, SnapshotError>` — first-fit scan for `count` *consecutive* zero-refcount entries (consecutive cluster indices, allowed to span refblock boundaries since coverage is contiguous), set each to 1, return the host offset of the first. `count == 0` is `InvalidConfig`. Rework `alloc_cluster_in_refblocks` as a `count = 1` wrapper. 16-bit width only, as before. (ii) `pub struct NewSnapshotEntry<'a> { pub l1_table_offset: u64, pub l1_size: u32, pub id: &'a [u8], pub name: &'a [u8], pub date_sec: u32, pub date_nsec: u32, pub vm_clock_nsec: u64, pub vm_state_size: u32, pub vm_state_size_large: u64, pub disk_size: u64, pub icount: u64 }` plus `pub fn serialize_snapshot_entry(e: &NewSnapshotEntry, out: &mut [u8]) -> Result<usize, SnapshotError>` — emits the 40-byte BE header (`extra_data_size = 24`), the 24-byte extra data, id bytes, name bytes; returns the unpadded length; `MisalignedAccess` if `out` is too small. Field order per `parse_snapshot_header_bytes` in `src/crates/qcow2/src/lib.rs:770` (the read side is the layout oracle — round-trip it in tests). (iii) `pub fn snapshot_table_byte_len(table: &[u8], nb_snapshots: u32) -> Result<usize, SnapshotError>` — walk raw entries (40-byte header at an 8-aligned start, advance by `40 + extra_data_size + id_str_size + name_size` rounded up to 8 *between* entries), return the unpadded end of the last entry; `ParseFailed` if a walk escapes the buffer. (iv) `pub fn build_snapshot_table(old_table: &[u8], old_len: usize, new_entry: &[u8], out: &mut [u8]) -> Result<usize, SnapshotError>` — copy `old_table[..old_len]` verbatim, zero-pad to the next 8-byte boundary, append `new_entry`, return total length. (v) `pub fn parse_decimal_id(id: &[u8]) -> Option<u64>` (strtoul-style: parse leading decimal digits, `None` if empty or non-digit lead — match qemu's `find_new_snapshot_id` which uses `strtoul` and treats non-numeric IDs as 0) and `pub fn format_decimal_u64(v: u64, out: &mut [u8]) -> usize`. Verify the strtoul edge semantics against qemu source and encode them in tests (e.g. id "abc" parses as 0, id "3x" parses as 3). (vi) ~25 unit tests: contiguous alloc happy path / spans-a-refblock-boundary / skips a one-cluster hole smaller than count / exhausted / cursor reuse across calls / sets every claimed entry to 1; serialize round-trips through `parse_snapshot_header_bytes` + the streaming parser's extra-data rules; table byte-len on a hand-built two-entry table with 24-byte extra data and on a table with oversized unknown extra data; build preserves old bytes verbatim including unknown extra data and 8-aligns the append; decimal helpers cover 0, max, non-numeric, mixed. |
| 6d | high | opus | worktree | Implement MODE_CREATE in `src/operations/snapshot/src/main.rs`, replacing the phase 3 stub. Read `src/operations/commit/src/main.rs` first — it is the staging / RMW / writeback template. (i) Extend the scratch layout (commit-style consts; keep `HEADER_BUF` / `CACHE_BUF_A` for list mode): raw old-snapshot-table buf (64 KiB cap), active L1 buf (64 KiB cap), L1-copy buf (64 KiB), L2 staging (2 MiB, `MAX_STAGED_L2 = 256` index array), refcount-table buf (64 KiB), refblock-offsets array, refblocks buf (2 MiB, `MAX_REFBLOCKS = 32`), new-table build buf (66 KiB), RMW bounce buf (MAX_SECTOR_SIZE). Add the compile-time assert that the layout stays below `ALLOC_HEAP_BASE`. (ii) Local helpers `read_input_byte_range` / `write_input_byte_range` on input slot 0 with bounce-buffer RMW for non-sector-aligned accesses, modelled on commit's output-side equivalents. (iii) Feature gates per the plan's "Feature gates" section, each mapping to the listed error; bitmaps via raw autoclear bytes 96..104 of the staged header sector. (iv) The 12-step algorithm from the Situation section: stream entries via `qcow2::for_each_snapshot_entry` for `max_id` (using `parse_decimal_id`); stage the raw old table and validate with `snapshot_table_byte_len` (cross-check against the streamed walk); stage L1 / L2s (gate on `Compressed` classification during this walk) / refcount table / refblocks (build the `rb_offsets` host-offset array and the `refblock_byte_offset_for_cluster` closure exactly like commit); copy the staged L1 bytes into the L1-copy buf **before** any flag rewrite (open question 6); allocate L1-copy clusters then build + serialise the new entry (id from `format_decimal_u64`, name from `config.arg`, dates from `config.date_sec/date_nsec`, `disk_size` = header virtual_size, `vm_clock_nsec = 0`, `vm_state_size = 0`, `vm_state_size_large = 0`, `icount = 0`) then `build_snapshot_table` then allocate the new table's contiguous clusters; `update_snapshot_refcount(IncrementForCreate)`; `update_copied_flags_for_l1`; writeback groups A → fsync → B → fsync → C (12 bytes at header offset 60: `nb_snapshots + 1` BE u32, new `snapshots_offset` BE u64) → fsync → D (old-table free, skipped when `nb_snapshots == 0`) → fsync, every write through the RMW helpers, every fsync via `call_table.fsync_input(0)`. Track dirty L2s / dirty refblocks with bitsets or staged-index dirty flags so group A writes only touched clusters. (v) Populate `SnapshotResult.assigned_id` / `assigned_id_len` and `snapshots_emitted = 0`; every error path goes through the existing `finish`. (vi) `make instar` + `make check-binary-sizes` (`snapshot.bin` must stay within 384 KiB). Use opus: the writeback ordering and the staged-buffer bookkeeping are the highest-risk code in the phase. |
| 6e | medium | sonnet | worktree | Minimal host `-c` dispatch in `src/vmm/src/main.rs` per open question 1. (i) In `run_snapshot`, route `args.create` to a new `run_snapshot_create(&args, name, verbose)`; `-a` / `-d` keep the phase 9 message (update its text to say phases 7–9). (ii) `run_snapshot_create` is modelled on `run_snapshot_list` with these deltas: validate the name (non-empty, ≤ 255 bytes per open question 9 — verify qemu-img's >255 behaviour empirically and match where reasonable, documenting any divergence); open the image with `BackingStore::open_rw_existing(path, Some(capacity_hint))` using the rebase-style hint (`file_size * 2`, min 1 GiB; see `src/vmm/src/main.rs:3558`); write `mode = MODE_CREATE`, `arg` = name bytes, `date_sec`/`date_nsec` from `SystemTime::now()` (UNIX epoch, nsec truncated to `usec * 1000`); run the guest with the same message-pump as list mode; on `ERROR_OK` print nothing (qemu parity — `-q` therefore has no visible effect on create; leave the flag plumbed); on error, map via the existing `snapshot_error_message` and exit non-zero. (iii) Extend `snapshot_error_message` with friendly texts for the codes create can newly return (`UNSUPPORTED_FEATURE`, `SNAPSHOT_TABLE_FULL`, `ALLOCATION_FAILED`, `REFCOUNT_OVERFLOW`), including the "16-snapshot v1 cap" and "refcount-bits" hints. |
| 6f | high | opus | worktree | End-to-end verification matrix against `qemu-img` 10.0.8 (not committed as tests — phase 11 owns that; this step is validation and bug-fixing). Build fixtures with `qemu-img create` (+ `qemu-img io`-style writes via `qemu-io` to get real data clusters and L2 tables) covering: v3 64 KiB clusters with data; v3 512-byte clusters; v2; with backing file; extended L2 (`-o extended_l2=on`); empty 0-byte virtual disk (open question 8); an image that already has one qemu-created snapshot. For each: run `instar snapshot -c NAME` on copy A and `qemu-img snapshot -c NAME` on copy B, then assert (1) `qemu-img check` clean on A — zero errors *and zero leaks*; (2) `qemu-img snapshot -l` (TZ=UTC) on A and B identical modulo the DATE column; (3) `qemu-img info` on A and B identical modulo date fields; (4) `instar snapshot -l` on A byte-identical to `qemu-img snapshot -l` on A; (5) second create assigns ID 2; duplicate name accepted (two entries, fact 2); (6) write data to A post-snapshot via `qemu-io`, then `qemu-img check` still clean and the snapshot's data still readable via `qemu-img convert -l snapshot.id=1` or `qemu-img snapshot -a` + compare — this is the test that catches the L2-table refcount gap (open question 2): without the L2 bump, qemu's post-snapshot write skips L2 COW and corrupts the snapshot. Also verify the refusal paths: 16-snapshot cap, zstd image, LUKS image, external-data-file image, dirty image (create with `lazy_refcounts=on` and kill mid-write, or hand-flip the dirty bit), >255-char name, non-qcow2 input. Fix any bugs found (in the same commit), and record the matrix results in the back-brief. |
| 6g | low | sonnet | worktree | Documentation. (i) `docs/quirks.md` snapshot section: duplicate names allowed (qemu parity, with the empirical note); 16-snapshot v1 cap (qemu: 65536); `refcount_bits != 16` refused for mutating modes; dirty/corrupt images refused (qemu auto-repairs on RW open); compressed clusters refused for mutating modes; `-q` has no visible effect on create (success is silent anyway). (ii) `docs/qcow2/qcow2-snapshots.md`: extend the "Mutator surface" section with the new functions and a short "create write ordering" subsection listing the A/B/C/D barrier groups and the crash-safety contract. (iii) `docs/plans/PLAN-snapshot.md`: phase 6 row → Landed with a pointer to this plan; fix the stale "Not started" status on the phases 3 and 4 rows; phase 9 row annotated "create dispatch landed early in phase 6 (see open question 1 there); delete/apply dispatch follow in phases 7/8; phase 9 is consolidation"; correct the create-mode step 2 duplicate-name claim and step 8 in-place-append claim (point at this plan's facts 2 and 6); mark open question 5 resolved (vm_state always 0 — confirmed empirically, icount written as 0 not absent). (iv) If `docs/usage.md` documents the snapshot subcommand, add `-c`. Keep total edits tight. |
| 6h | low | sonnet | worktree | Full verification + commit. `make instar`, `make test-rust` (snapshot crate gains ~40 tests; shared gains the layout tests; resize must still pass), `make check-binary-sizes`, `make lint`, `pre-commit run --all-files`. Confirm list-mode behaviour is unchanged (run `instar snapshot -l` against a fixture and diff against `qemu-img snapshot -l`). Stage and present a single commit for steps 6a–6g following `~/.claude/CLAUDE.md` conventions (50-char first line ending in `.`, 75-char wrap, Prompt paragraph, Signed-off-by, Co-Authored-By with model + context window + effort). The message should cover: MODE_CREATE end-to-end (planner helpers + guest + minimal host dispatch pulled forward from phase 9), the L2-table refcount-coverage fix to the phase 5 mutator, the qemu-faithful create ordering with fsync barriers, the empirically-corrected duplicate-name behaviour, and the new SnapshotConfig date fields. |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session reviews each step's
output against this plan and the qemu references — read the
actual files, don't trust summaries. Phase 6 is a mutating
phase: **worktree isolation is mandatory for every step** (per
the master plan's agent guidance).

Steps 6b, 6c, 6d, 6f are high-effort opus; 6a, 6e are sonnet;
6g, 6h are low-effort sonnet. When a step's brief and the code
disagree, stop and report rather than improvising — except for
obvious factual corrections (wrong line number, renamed
helper), which should be noted in the back-brief.

### Management session review checklist

- [ ] Read the changed files.
- [ ] No unrelated files modified.
- [ ] `cargo test -p snapshot -p shared -p resize` green.
- [ ] The L2-table refcount extension matches
      `qcow2_update_snapshot_refcount` semantics (once per L1
      entry; L1 clusters excluded) — verify against the qemu
      source, not just the tests.
- [ ] The writeback order in the guest is A → fsync → B →
      fsync → C → fsync → D, with C being exactly the 12-byte
      header write.
- [ ] The L1 copy is serialised from pre-rewrite bytes
      (open question 6).
- [ ] The new-entry serialisation matches empirical fact 4
      (extra 24, icount 0, no trailing pad).
- [ ] Step 6f's matrix ran on every listed fixture; the
      post-snapshot-write corruption probe (matrix item 6)
      passed.
- [ ] `make instar`, `make test-rust`,
      `make check-binary-sizes`, `make lint`,
      `pre-commit run --all-files` all clean.
- [ ] Manually walk one small example end-to-end (per the
      master plan: 2-cluster image, one create; predict the
      refcount and COPIED deltas, then hexdump-diff the
      actual image against the prediction).

## Administration and logistics

### Success criteria

Phase 6 is complete when:

* All steps land in one commit on the `snapshot` branch.
* `instar snapshot -c` satisfies the Mission section's
  parity contract on the step 6f fixture matrix.
* The post-snapshot-write probe (qemu writing to an
  instar-snapshotted image, then both check and snapshot
  content verification passing) is clean — proving the
  L2-table refcount fix.
* All refusal paths return the documented errors with
  friendly host-side messages.
* List mode is byte-identical to phase 4 behaviour.
* `make instar`, `make test-rust`, `make check-binary-sizes`,
  `make lint`, `pre-commit run --all-files` all pass;
  `snapshot.bin` within the 384 KiB cap.
* Docs and master-plan corrections from step 6g are in.

### Future work created by this phase

- **Compressed-cluster mutating support** — already tracked
  in the master plan; the gate lands here.
- **`refcount_bits != 16` mutating support** — the scalar
  accessors handle all widths; only the allocator is
  16-bit-bound. A follow-up widens
  `alloc_contiguous_clusters_in_refblocks`.
- **Refcount-table growth** — `RefcountExhausted` when every
  staged refblock is full (phase 5 open question 7); becomes
  more visible now that create actually allocates.
- **`SnapshotPatch`/`SnapshotPlan` disposition** — unused by
  phase 6 (open question 5); phase 14 decides.

### Bugs fixed during this work

- Phase 5's `update_snapshot_refcount` did not adjust L2
  table cluster refcounts (open question 2). Fixed by step
  6b. Any further phase 5 gaps surfaced by the step 6f matrix
  are fixed in the same commit and listed here.
- The master plan's create-mode description had two
  empirically-wrong claims (duplicate-name refusal; in-place
  table append) — corrected by step 6g.

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`docs/plans/order.yml`. The master plan's Execution table
already links this file; step 6g flips the row to Landed.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan. Include the empirical
verification results from open questions 8 and 9 (zero-length
L1 and >255-char names) once gathered.
