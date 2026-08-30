# `instar commit` — merge an overlay's data into its backing

`instar commit` merges every allocated cluster from an overlay
into its backing image, then zeroes the overlay's metadata so
the overlay reads as empty against the (now-updated) backing.
It is the safe, sandboxed equivalent of `qemu-img commit`, with
byte-equivalent post-commit `qemu-img info --output=json` output
for qcow2 across every qemu-img version 6.0.0 through 10.2.0.

The `commit.bin` guest runs in the KVM sandbox. The host opens
the **backing** as the output device (RW) and the **overlay**
as input slot 0 (RW, so the guest can run the overlay-clear
pass via `write_input_sector(0, ...)`). The backing's own
ancestor chain occupies input slots [1..N) read-only (v1
doesn't consult them, but the slots are populated for
forward compatibility).

## Synopsis

```
instar commit [OPTIONS] FILENAME
```

Common options:

```
  -f, --format <FMT>   qcow2 | vmdk
                       Default: auto-detect from the overlay's
                       magic bytes
  -b, --base <BASE>    Optional. Backing file to commit into.
                       When omitted, the host resolves the
                       overlay's recorded immediate parent.
                       v1 supports only the overlay's
                       immediate parent.
  -q, --quiet          Suppress the success line. Errors still
                       go to stderr.
      --output <FORMAT> human (default) | json
```

The full flag surface is reported by `instar commit --help`.

## `-b` resolution

When `-b BASE` is omitted (the common case), the host reads
the overlay's recorded backing-file pointer and uses that.
Relative paths in the recorded pointer resolve against the
overlay's parent directory.

When `-b BASE` is supplied, the host resolves it relative to
the overlay's parent directory and compares the resolved path
to the overlay's recorded backing-file pointer. If they don't
match, instar refuses with `commit through an intermediate
layer is not yet supported (the overlay's immediate parent is
X)` — intermediate-image commit is deferred to a future
release (see Future work).

This matches `qemu-img commit`'s implicit `-b` behaviour and
makes the explicit-`-b` form a self-documenting safety
assertion.

## Target formats

| Format                  | Implicit `-b` | Explicit `-b`       | Notes                                                       |
|-------------------------|---------------|---------------------|-------------------------------------------------------------|
| qcow2 v2 / v3           | ✓             | ✓                   | refcount_bits=16 only; cluster sizes must match between overlay and backing |
| vmdk monolithicSparse   | reject (gap)  | ✓                   | Implicit `-b` blocked by info-vmdk-backing-file follow-up   |
| Other formats           | reject        | reject              | qemu-img also rejects                                       |

For qcow2, the post-commit `qemu-img info --output=json` for
both the overlay and the backing matches `qemu-img commit`
byte-for-byte across every shipped qemu-img version (verified
by the cross-version baseline matrix at
`instar-testdata/expected-outputs/commit-overlay-info-json/`
and `commit-backing-info-json/`, exercised by
`tests/test_commit.py:TestCommitBaselineMatrix`).

For vmdk, the implicit-`-b` resolution path is gated by a
pre-existing gap in the host info operation — it doesn't yet
expose vmdk monolithicSparse's `parentFileNameHint` via the
`backing_file` field. The host's `-b`-against-recorded-parent
check therefore refuses every vmdk commit without an
explicit `-b`. The smoke + matrix + round-trip vmdk
cases all pass an explicit `-b base.vmdk`; once the info-side
gap lifts, the implicit form will work too.

## Output format

Human (default):

```
Image committed.
```

(matches `qemu-img commit` byte-for-byte.) JSON
(`--output=json`):

```json
{
  "overlay": "/path/to/overlay.qcow2",
  "overlay_format": "qcow2",
  "backing": "/path/to/base.qcow2",
  "backing_format": "qcow2",
  "clusters_committed": 16,
  "bytes_committed": 1048576,
  "overlay_clusters_cleared": 16
}
```

`-q` suppresses both forms on success and prints only errors.

## Atomicity guarantees

The commit guest applies its mutations in a strict order so
that crash recovery is straightforward:

1. **Cluster data into the backing.** For every overlay
   cluster, the guest reads its bytes from the overlay
   (input slot 0), allocates a cluster on the backing if
   the backing doesn't already cover that range, and
   writes the data to the backing's allocated cluster.
2. **Backing metadata.** Once every cluster is written,
   the guest flushes the backing's dirty L2 tables, then
   the L1 table, then the refcount blocks (refcounts
   last, so a crash never leaves a referenced cluster
   with a stale refcount). After this point the backing's
   metadata fully reflects the merged content.
3. **Overlay-clear pass.** Last, the guest issues a batch
   of `write_input_sector(0, ...)` calls that zero the
   overlay's L2 entries and refcount-block entries for
   every committed cluster. This is observable as
   "the overlay reads as empty against the new backing".
   This pass is **skipped when the overlay itself carries
   internal snapshots** (zeroing shared active metadata in
   place would corrupt them — see Known divergences): the
   overlay is left byte-unchanged, its snapshots preserved,
   and the committed clusters read identically through the
   new backing.

A crash between steps 1 and 2 leaves the backing in a
state qemu-img check can repair (clusters allocated but
no L2 references — leaks, not corruption). A crash between
steps 2 and 3 leaves both files internally consistent and
correctly merged — the overlay still owns the committed
clusters in its L2 tables but reading through it returns
the same bytes the backing now has. Subsequent `instar
commit` invocations are idempotent: on the second
invocation every committed cluster has matching data on
both sides, so step 1 is a no-op, and the overlay-clear
pass completes.

This matches `qemu-img commit`'s all-or-nothing observable
behaviour.

## Known divergences from `qemu-img commit`

- **`-d` (drop overlay after commit) not yet supported.**
 qemu-img deletes the overlay file after a successful
 commit when `-d` is set. instar leaves the overlay in
 place (now zero-allocated and reading as empty against
 the new backing). The user can `rm` it manually.
- **`-p` (progress bar) not yet supported.** instar
 reports cluster counts in the JSON envelope but does
 not emit a live progress bar.
- **`-r` (rate limit) not yet supported.** Commits run
 at full I/O speed.
- **`-t` (cache mode) not yet supported.** instar's
 backing-store layer doesn't expose cache-mode knobs.
- **Intermediate-image commit not yet supported.** When
 `-b BASE` names a backing that's two or more layers
 below the overlay (e.g. `overlay → intermediate → base`,
 `-b base.qcow2`), instar refuses with a clear "commit
 through an intermediate layer is not yet supported"
 message. qemu-img walks the chain and merges every
 layer between the overlay and BASE into BASE. Lifting
 the gap requires the chain-walking helper from
 the PLAN-rebase-commit work to be promoted to a shared
 crate.
- **vmdk implicit-`-b` blocked.** The host pre-check
 refuses every vmdk commit without explicit `-b`
 because the host info operation doesn't currently
 expose vmdk monolithicSparse's `parentFileNameHint`
 via `backing_file`. Tracked separately under
 PLAN-info's vmdk follow-ups.
- **Snapshot-bearing images copy-on-write (backing
 snapshots preserved).** Since the PLAN-q workcow2-write-infrastructure (issues #420 and #423
 resolved), commit no longer refuses images with internal
 snapshots — it copies the shared clusters instead (the
 shared allocate-on-write and copy-on-write machinery is
 documented in
 [qcow2/qcow2-write-planner.md](/components/instar/qcow2/qcow2-write-planner/)).
 Where
 the per-cluster loop previously blind-overwrote a
 snapshot-shared backing cluster, commit now COWs it (copy
 `D → D'`, repoint the L2, `rc(D')=1`, `rc(D)`−1; the old
 cluster is never freed), so every pre-existing backing
 snapshot is preserved bit-identically — matching qemu,
 which COWs and preserves. When the **overlay** has
 internal snapshots the post-commit overlay-clear pass is
 skipped (zeroing shared active metadata in place was the
 #423 corruption): the overlay is left byte-unchanged with
 its snapshots preserved, and its active view stays
 `qemu-img compare`-identical to qemu. The correctness bar
 is qemu-parity (`qemu-img check` clean + active-view
 compare + a snapshot read-back oracle asserting each
 backing snapshot equals its pre-commit content), not
 image-byte identity. **Known limitation:** because the
 clear pass is skipped, a snapshot-bearing overlay is not
 byte-emptied the way a snapshot-free overlay is — the
 committed clusters stay mapped in the overlay's active L2
 (reading identically through the new backing) rather than
 zeroed. Full byte-emptying parity would need an
 overlay-side COW-clear primitive (recorded follow-up).
- **Backings with unknown or compression feature bits
 refused.** Since the phase-4 migration onto
 `crates/qcow2-write`, a backing whose header carries the
 zstd compression-type bit or any unknown
 incompatible-features bit refuses (error 16): ``the
 backing file uses features instar commit does not
 support (unknown or compression feature bits). Fall
 back to `qemu-img commit` ``. Spec-mandated (commit
 previously proceeded illegally); fires before any
 staging or mutation. qemu-img builds with zstd support
 proceed on the zstd shape.
- **Backings with inconsistent metadata refused.** A
 sparse (holed) refcount table, reserved bits in
 refcount-table/L1/L2 entries, or snapshot-shared /
 refcount-inconsistent clusters on a header that claims
 no snapshots refuse (error 17): ``the backing file's
 metadata is inconsistent (refcounts, table flags or
 layout); refusing to write into it. Run `qemu-img
 check` on the backing, or fall back to `qemu-img
 commit` ``. The sparse-table refusal fires at staging
 time, before any mutation; previously that shape —
 stock-producible and check-clean — was silently
 corrupted (see docs/quirks.md). qemu-img proceeds
 check-clean on it.
- **Compressed backing clusters refused.** A committed
 extent landing on a compressed backing L2 entry refuses
 with the existing `ERROR_UNSUPPORTED_FORMAT` (the code
 the overlay side has always used for compressed
 entries). qemu-img allocates a fresh uncompressed
 cluster instead; previously instar silently
 destroyed every stream packed in the shared host
 cluster (see docs/quirks.md).
- **Refcount exhaustion refused instead of grown.**
 Commits that need more free clusters than the backing's
 existing refcount blocks provide refuse (error 11);
 qemu-img grows the refcount table and completes. v1
 never appends refblocks; retiring the ceiling is the
 refcount-growth generalization (the PLAN-q workcow2-write-infrastructure). Backing staging
 capacity itself is byte-driven now:
 `min(2048, 3 MiB / cluster_size)` staged refblocks
 (formerly a flat 32) and a windowed backing-L2 model
 with no total-count refusal, so strictly more images
 reach a successful commit. Overlay-side staging caps
 are unchanged.
- **Cluster-size mismatch refused.** If the overlay and
 backing have different qcow2 cluster sizes, the host
 pre-check refuses with `commit between mismatched
 cluster sizes is not yet supported`. qemu-img silently
 succeeds but with limited efficiency. Lifting the gap
 needs the planner to support cluster-size adapters.
- **Cross-format commit refused.** qcow2 → qcow2 and
 vmdk → vmdk only. Cross-format commit (e.g. qcow2
 overlay onto a vmdk backing) is refused with
 `ERROR_UNSUPPORTED_FORMAT`. Lifting needs planner
 extensions plus a cluster-size translation layer.
- **`-f raw` overlays refused.** Raw files don't have
 a backing chain — there's nothing to commit. qemu-img
 refuses for the same reason; the error message is
 slightly different.
- **LUKS-encrypted overlays / backings refused.** Same
 reason as rebase; deferred to convert-side LUKS
 plumbing.
- **Large cluster sizes refused.** The commit guest
 binary's `OVERLAY_RT_LIMIT` and `BACKING_RT_LIMIT`
 scratch regions are sized at `MAX_SECTOR_SIZE`
 (64 KiB), so a single-cluster refcount table for any
 `cluster_size > 64 KiB` overflows the budget and
 returns `ERROR_SCRATCH_TOO_SMALL`. Lifting the bound
 is a master-plan TODO; the differential fuzzer picker
 caps `cluster_size` at 64 KiB to match.

## Future work

For the per-format commit planners, see
[`src/crates/commit/src/lib.rs`](https://github.com/shakenfist/instar/blob/develop/src/crates/commit/src/lib.rs)
(`plan_commit_qcow2`, `plan_commit_vmdk`). For the divergence
whitelist applied during cross-version baseline comparison, see
[`tests/helpers/info_json.py`](https://github.com/shakenfist/instar/blob/develop/tests/helpers/info_json.py).

Tracked under the [PLAN-rebase-commit
master plan](/components/instar/plans/PLAN-rebase-commit/):

- `-d` (drop overlay after commit). A post-success
 `std::fs::remove_file(overlay_path)` call in
 `run_commit`. Trivial when the command-line surface
 is extended.
- `-p` (progress reporting). Reuse the existing
 `send_progress` call-table function pointer; the guest
 binary calls it every N committed clusters; the host
 renders a progress bar.
- `-r` (rate limit). Wire a delay-between-writes knob
 into the guest's per-cluster loop.
- `-t` (cache mode). Backing-store layer needs to
 expose cache-mode knobs first.
- Intermediate-image commit. Needs the chain-walking
 helper from the PLAN-rebase-commit work step 3f
 promoted to a shared crate plus planner extensions to
 merge through intermediates.
- Cross-format commit (qcow2 ↔ vmdk). Needs planner
 extensions plus the cluster-size mismatch resolution.
- Cluster-size-mismatch commit. Needs cluster-size
 adapters in the planner's per-cluster loop.
- Implicit-`-b` for vmdk. Needs the host info operation
 to surface vmdk monolithicSparse's
 `parentFileNameHint` via the `backing_file` field
 (tracked under PLAN-info's vmdk follow-ups).
- Tighter `OVERLAY_RT_LIMIT` / `BACKING_RT_LIMIT` so
 large-cluster-size overlays don't surface
 `ERROR_SCRATCH_TOO_SMALL`.

## Examples

Implicit `-b` (commit into the overlay's recorded parent):

```
instar commit overlay.qcow2
```

Explicit `-b` (must match the overlay's recorded parent):

```
instar commit -b base.qcow2 overlay.qcow2
```

JSON output for scripting:

```
instar commit --output=json overlay.qcow2
```

Quiet mode (suppresses the success line):

```
instar commit -q overlay.qcow2
```

Vmdk commit (must use explicit `-b` until the implicit-`-b`
gap lifts):

```
instar commit -b base.vmdk overlay.vmdk
```

Auto-detect the overlay format from the file's magic bytes
(works for qcow2 and vmdk monolithicSparse):

```
instar commit overlay.qcow2
```
