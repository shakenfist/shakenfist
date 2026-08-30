# `instar rebase` — change an overlay's backing-file reference

`instar rebase` changes the backing-file pointer recorded in an
overlay image and, in safe mode, copies divergent clusters from
the old backing chain into the overlay so the post-rebase result
is content-equivalent to the pre-rebase result. It is the safe,
sandboxed equivalent of `qemu-img rebase`, with byte-equivalent
post-rebase `qemu-img info --output=json` output for qcow2
across every qemu-img version 6.0.0 through 10.2.0.

Both modes run the `rebase.bin` guest in the KVM sandbox. The
host opens the overlay as the output device (RW), the old
backing chain as input slots [0..N), and (in safe mode) the
new backing chain as input slots [N..M). The guest reads the
overlay's header, plans the metadata mutation, and applies the
patches via virtio-block.

## Synopsis

```
instar rebase [OPTIONS] -b BACKING FILENAME
```

Common options:

```
  -f, --format <FMT>           qcow2 | vmdk
                               Default: auto-detect from the
                               existing file's magic bytes
  -b, --backing <BACKING>      Required. New backing file path,
                               or empty string ("") to detach.
  -F, --backing-format <FMT>   qcow2 | vmdk | raw. Optional
                               format hint for the new backing;
                               the guest probes it either way.
  -u, --backing-unsafe         Unsafe (metadata-only) rebase.
                               Trusts the caller that the new
                               backing has the same content as
                               the old; no chain comparison, no
                               data copy.
  -q, --quiet                  Suppress the success line.
                               Errors still go to stderr.
      --output <FORMAT>        human (default) | json
```

The full flag surface is reported by `instar rebase --help`.

## Mode grammar

The rebase planner runs in one of two modes:

| Mode             | Trigger             | What it does                                                                                                  |
|------------------|---------------------|---------------------------------------------------------------------------------------------------------------|
| Unsafe (`-u`)    | `-u` flag           | Rewrites only the backing-file pointer in the overlay's header. No chain comparison, no cluster copying.       |
| Safe (default)   | absence of `-u`     | Walks the old and new backing chains, copies clusters whose content differs into the overlay, then rewrites the backing pointer. |

Detach is encoded as `-b ""` — the overlay's backing pointer
is zeroed, and the overlay becomes standalone. Detach is valid
in either mode and is essentially equivalent to "rebase to no
backing"; unsafe-mode detach is instant, safe-mode detach
flattens every cluster the old chain provided.

## Target formats

| Format                  | Unsafe (`-u`) | Safe (default) | Notes                                            |
|-------------------------|---------------|----------------|--------------------------------------------------|
| qcow2                   | ✓             | ✓              | v2 and v3; refcount_bits=16 only                 |
| vmdk monolithicSparse   | ✓             | reject         | Safe mode is a planner gap (Future work)         |
| Other formats           | reject        | reject         | qemu-img also rejects                            |

For qcow2, the post-rebase `qemu-img info --output=json` matches
`qemu-img rebase` byte-for-byte across every shipped qemu-img
version (verified by the cross-version baseline matrix in
`instar-testdata/expected-outputs/rebase-info-json/` and
exercised by
`tests/test_rebase.py:TestRebaseBaselineMatrix`). For vmdk —
which `qemu-img rebase` rejects with `Image format driver does
not support rebase` on every shipped version — the post-rebase
state is verified by `tests/test_rebase.py`'s vmdk smoke and
the qemu-img info readback (`TestRebaseSuccessPaths`).

## Output format

Human (default):

```
Image rebased.
```

(matches `qemu-img rebase` byte-for-byte.) When `-b ""` is
supplied:

```
Image detached.
```

JSON (`--output=json`, non-detach):

```json
{
  "overlay": "/path/to/disk.qcow2",
  "overlay_format": "qcow2",
  "mode": "unsafe",
  "clusters_copied": 0,
  "bytes_copied": 0,
  "new_backing": "next.qcow2"
}
```

Detach JSON drops `new_backing` and adds `"detached": true`.
`mode` is one of `"unsafe"` or `"safe"`. `-q` suppresses both
forms on success and prints only errors.

## `-u` (unsafe) semantics

Unsafe-mode rebase rewrites only the qcow2 `backing_file_offset`
and `backing_file_size` header fields (or the vmdk descriptor's
`parentFileNameHint=` line). No backing-chain content is read;
no overlay cluster data is touched. The caller is asserting
that the new backing is content-equivalent to the old backing,
so existing overlay clusters remain coherent against the new
chain without copying.

Use `-u` when:

- You renamed or moved the backing file but haven't changed
  its bytes.
- You're swapping in a backing image you know is bit-identical
  to the old one (e.g. a copy, a hardlink, a snapshot you took
  immediately after the original was finalised).

Avoid `-u` when:

- The new backing's data could legitimately differ from the
  old. The overlay's existing-data assumptions would be
  silently broken; reads through the rebased overlay would
  return stale or incorrect content.

## `-b` (new backing) semantics

`-b BACKING` is required (no implicit "current parent" — that's
commit's job). Relative paths resolve against the overlay's
parent directory at parse time, matching qemu-img's convention.
The path is stored in the qcow2 header (or the vmdk descriptor)
verbatim — instar does not canonicalise to absolute paths.

Detach is signalled by `-b ""`. The backing pointer is zeroed
and the overlay becomes standalone.

## Known divergences from `qemu-img rebase`

- **Long-path relocation is rejected.** Long new-backing paths
 that don't fit the overlay's existing header slot are
 refused with `ERROR_BACKING_PATH_TOO_LONG` (the planner's
 in-place rewrite path is bounded by the existing slot size).
 qemu-img silently relocates the path string to a fresh
 cluster and updates the header offset. Lifting the gap is a
 master-plan TODO; for now the picker in the differential
 fuzzer matches the backing-name length to the overlay's
 existing slot.
- **Safe-mode rebase for vmdk is not yet supported.** instar
 rejects with `ERROR_UNSUPPORTED_FORMAT`. qemu-img also
 rejects vmdk rebase entirely (there is no upstream vmdk
 rebase at all). The vmdk smoke tests use unsafe mode.
- **Safe-mode rebase copy-on-writes snapshot-bearing
 overlays.** Since the PLAN-q workcow2-write-infrastructure (issue #421 resolved),
 safe mode (including safe-mode detach) no longer refuses
 an overlay with internal snapshots — it succeeds by
 copying. Where the safe-mode allocator would previously
 have mutated a snapshot-shared active L2 in place and
 under-counted refcounts (`refcount=1 reference=2`,
 enabling data loss via a later `snapshot -d`, issue
 #421), it now COWs the shared L2 (copy `T → T'`, repoint
 the L1, `rc(T')=1`, `rc(T)`−1; the shared write path is
 documented in
 [qcow2/qcow2-write-planner.md](/components/instar/qcow2/qcow2-write-planner/)).
 The load-bearing
 subtlety is the **snapshot-view semantic**: matching
 qemu's contract, safe-mode rebase covers the active view
 only, so after the rebase a snapshot's unallocated ranges
 **read through the new backing** rather than staying at
 their pre-rebase content — instar reproduces this
 read-through-new-backing semantic exactly. The
 correctness bar is qemu-parity (`qemu-img check` clean +
 active-view `qemu-img compare` + a snapshot read-back
 oracle whose expected value is the snapshot resolved
 against the new backing), not image-byte identity. `-u`
 is unchanged (it only rewrites the header
 backing-pointer region, which is never snapshot-shared)
 and stays parity-tested against qemu-img. **Known
 limitation:** COW growth is coarsely sized at
 `2 × overlay_cluster_count` (rebase writes into unowned
 clusters); the over-provisioned refblocks are
 check-clean, and a tighter bound is recorded follow-up
 work.
- **Overlays with extended L2 entries or unknown/compression
 feature bits are refused.** Since the phase-5 migration of
 safe mode onto `crates/qcow2-write`, safe-mode rebase
 (including safe detach) refuses with error 15: ``the
 overlay uses features instar rebase does not support
 (extended L2 entries, or unknown/compression feature
 bits). Use -u for a metadata-only rebase or fall back to
 `qemu-img rebase` ``. The extended-L2 refusal replaces a
 silent-corruption defect (the walk misread 16-byte
 extended-L2 entries as 8-byte); the zstd/unknown-bit
 refusal is spec-mandated and narrows shapes that
 previously rebased correctly (the bit is inert without
 compressed clusters). `-u` stays allowed. qemu-img builds
 with the matching feature support proceed.
- **Overlays with inconsistent metadata are refused.**
 Safe-mode rebase refuses with error 16: ``the overlay's
 metadata is inconsistent (refcounts, table flags or
 layout); refusing to write into it. Run `qemu-img check`
 on the overlay, or fall back to `qemu-img rebase` ``.
 This covers a sparse (holed) refcount table — a
 stock-producible, check-clean shape that rebase silently
 misallocated previously (the rebase sibling of
 commit's issue #428) — plus reserved bits and
 classification inconsistencies. The sparse-table refusal
 fires before any mutation and is byte-idempotent;
 qemu-img rebases the same shape check-clean.
- **Deep-allocation safe rebases refuse on refcount
 exhaustion.** v1 never appends refblocks, so a safe
 rebase that needs more cluster allocations than the
 overlay's existing refcount blocks can hold refuses with
 `ERROR_REFCOUNT_EXHAUSTED` where qemu-img completes.
 (Before the phase-2 fix for issue #422 these shapes
 could hang instead of refusing — a staged-L2 lookup went
 stale after arena growth and the guest spun in its panic
 handler; fixed, with staging reordered so the growable
 L2 arena can no longer clobber refblock staging. The
 phase-5 migration then retired the growable arena and its
 staging-count caps outright: existing L2 tables are
 windowed with `min(256, 2 MiB / cluster_size)` slots and
 safe eviction, and refblocks stage at
 `min(2048, 3 MiB / cluster_size)`, so overlays that
 previously refused `ERROR_SCRATCH_TOO_SMALL` at staging
 time on populated-L2 count alone now rebase.)
 Retiring the exhaustion ceiling is the master plan's
 refcount-growth generalization.
- **Beyond-EOV tail bytes of copied clusters are zeros.**
 When the old backing chain is larger than the overlay's
 virtual size, a copied tail cluster's bytes beyond
 end-of-virtual-size are zero-filled where `qemu-img
 rebase` carries the old chain's bytes into the raw file.
 Virtual content is identical — bytes past EOV are not
 virtual content — and this is the only raw-level
 divergence sanctioned by the phase-5 migration proof.
- **Cross-cluster-size rebase is rejected.** If the new
 backing's qcow2 cluster size differs from the overlay's,
 safe-mode rebase refuses with
 `ERROR_NEW_BACKING_INCOMPATIBLE`. qemu-img silently
 succeeds but the resulting overlay has inconsistent
 metadata; the master plan tracks this as a future
 hardening item.
- **External-data-file qcow2 overlays are refused.** Rebase
 refuses any overlay with the `INCOMPAT_EXTERNAL_DATA`
 feature bit set; that's the qcow2 v3 external data file
 feature, which is incompatible with backing chains.
 qemu-img refuses for the same reason.
- **LUKS-encrypted overlays / backings are refused.**
 Lifting the gap depends on the matching LUKS plumbing
 on the convert side (master-plan Future work).
- **`--object OBJDEF` and `--image-opts` are not
 implemented.** Rejected at the host CLI surface; the
 qemu-img features they unlock require complex
 plumbing not in scope for v1.
- **Cross-version baseline matrix scope.** The recorded
 baselines cover qcow2 only — qemu-img rebase rejects
 vmdk / vhd / vhdx with "Operation not supported" on
 every shipped version, so there is no cross-tool diff
 to record for those targets.

## Future work

For the per-format rebase planners, see
[`src/crates/rebase/src/lib.rs`](https://github.com/shakenfist/instar/blob/develop/src/crates/rebase/src/lib.rs)
(`plan_rebase_qcow2`, `plan_rebase_vmdk`). For the divergence
whitelist applied during cross-version baseline comparison, see
[`tests/helpers/info_json.py`](https://github.com/shakenfist/instar/blob/develop/tests/helpers/info_json.py).

Tracked under the [PLAN-rebase-commit
master plan](/components/instar/plans/PLAN-rebase-commit/):

- Long-path relocation in qcow2 unsafe-mode rebase (planner +
  guest scratch budget for the appended path cluster).
- Vmdk safe-mode rebase (planner gap; cluster comparison loop
  + descriptor rewrite atomicity).
- Cross-cluster-size rebase (planner gap; would need
  cluster-size adapters in the safe-mode allocator).
- `--object` / `--image-opts` for LUKS-wrapped overlays
  (depends on convert-side LUKS plumbing).
- Targeted seed corpus for `fuzz_rebase_planners`. The
  `scripts/generate-fuzz-seeds.py` infrastructure can
  walk the existing testdata.
- Lifting the 1 MiB cluster-size cap on safe mode (the
  per-side compare buffers are 1 MiB; larger cluster sizes
  surface `ERROR_SCRATCH_TOO_SMALL`). The phase-5 migration
  onto `crates/qcow2-write` retired the old allocator's
  staging-count refusals, so this cap is the remaining
  scratch bound.

## Examples

Unsafe rebase to a new backing in the same directory:

```
instar rebase -u -b new-backing.qcow2 disk.qcow2
```

Safe rebase to a new backing of the same virtual size:

```
instar rebase -b new-backing.qcow2 -F qcow2 disk.qcow2
```

Detach the overlay (zero its backing pointer):

```
instar rebase -u -b "" disk.qcow2
```

JSON output for scripting:

```
instar rebase -u -b new-backing.qcow2 --output=json disk.qcow2
```

Auto-detect the overlay format from the file's magic bytes:

```
instar rebase -u -b new-backing.qcow2 disk.qcow2
```
