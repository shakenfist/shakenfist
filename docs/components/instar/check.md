# Check

`instar check` validates image structural integrity, as a drop-in
replacement for `qemu-img check`.

```bash
# Validate image structural integrity (matches qemu-img check output)
instar check image.qcow2

# JSON output for programmatic consumption
instar check --output json image.qcow2

# Validate the entire backing chain
instar check --chain image.qcow2

# Repair a qcow2 image in place: safe leak reclamation (the default tier)
instar check --repair image.qcow2

# Lossy repair: also rebuild refcounts and reconcile COPIED flags
instar check --repair=all image.qcow2
```

## QCOW2 validation

For QCOW2 images, check validates:

- Header integrity (version, cluster_bits, virtual_size)
- Incompatible feature bit validation (rejects unknown bits per spec)
- Full L1/L2 table consistency (all sectors, including extended L2)
- Extended L2 subcluster bitmap validation (alloc/zero overlap,
  alloc-without-host, host-without-ref, compressed non-zero bitmap)
- Overlap detection (two L2 entries referencing same host cluster)
- Refcount validation for all widths (1/2/4/8/16/32/64-bit refcounts)
- Leak detection (clusters with refcount > 0 but no L2 reference),
  including correct handling of compressed cluster host ranges
- Cluster sizes from 512B to 2MB (cluster_bits 9-21)
- Dirty/corrupt incompatible feature flags

## QCOW2 repair

For QCOW2 images, `instar check --repair[=leaks|all]` additionally repairs the
image in place: the safe `leaks` tier reclaims unreferenced clusters
(crash-safe, lossless), and the lossy `all` tier rebuilds refcounts and
reconciles COPIED flags under a crash-safe `corrupt`-bit write ordering —
mirroring `qemu-img check -r leaks`/`-r all`. It refuses rather than guessing:
snapshotted images are repaired by neither tier, and the lossy `all` tier
declines its rebuild on compressed, external-data, or already-corrupt images
(the safe leak reclamation still runs, and the result is reported incomplete).
See
[qcow2/qcow2-refcount.md](/components/instar/qcow2/qcow2-refcount/#repairing-refcount-inconsistencies).

## VMDK validation

For VMDK images (monolithicSparse, streamOptimized, and
monolithicFlat — including multi-extent twoGbMaxExtentFlat
and flat-in-backing-chain via parentFileNameHint), check validates:

- Full header parsing (version, capacity, grain size, flags, compression)
- Descriptor bounds and multi-extent detection (graceful rejection)
- Grain directory offset within file bounds
- Full grain directory and grain table walk
- Grain data offsets within file bounds
- Compressed grain marker validation (LBA consistency, compressed size
  bounds, marker-plus-data within file)
- Redundant Grain Directory (RGD) cross-check when FLAG_USE_RGD is set
- Overlap detection via 1-bit-per-grain bitmap (same pattern as QCOW2)
- streamOptimized footer validation (magic, GD offset)
- Fragmentation measurement

## VHD validation

For VHD images (dynamic and fixed), check validates:

- Footer cookie and checksum (from first or last sector)
- Format version (must be 1.0) and features (reserved bit required)
- Disk type validity (fixed, dynamic, differencing)
- Fixed VHD: data_offset check, file size vs virtual size validation
- Dynamic header cookie, checksum, and version
- BAT offset within file bounds
- BAT entry validation: allocated block offsets within file
- Overlap detection (no two BAT entries reference same block)
- Fragmentation tracking (non-sequential block allocation)
- Footer copy consistency (start vs end of file)

## VHDX validation

For VHDX images, check validates:

- File identifier signature at offset 0 ("vhdxfile")
- Header 1 and Header 2: signature, CRC-32C checksum, active header
  selection by sequence number
- Dirty log detection (non-zero log GUID)
- Region table 1: signature, CRC-32C, BAT and metadata region presence
- Region table 2: cross-validation against region table 1
- Metadata: required items (FileParameters, VirtualDiskSize,
  LogicalSectorSize, PhysicalSectorSize)
- Differencing disk detection (unsupported)
- BAT entries: block offsets within file bounds, 1MB alignment,
  overlap detection, state validation
- Fragmentation tracking (non-sequential block allocation)

## Chain validation

The `--chain` flag discovers the full backing chain (using the same chain
discovery infrastructure as `instar info --chain`), sets up each image as a
separate virtio-block device in the KVM guest, and validates:

- Format consistency: each backing image's format matches what chain
  discovery found
- Virtual size validity: backing images must have non-zero virtual size
- QCOW2 header validation: backing images that are QCOW2 get basic header
  checks (magic, version, cluster_bits, L1/refcount table bounds, corrupt
  feature flag)

Chain errors are reported separately as `chain-errors` in JSON output and
in human-readable output. Without `--chain`, `chain-errors` is always 0.

**Note:** The `chain-errors` field is always present in JSON output,
even when `--chain` is not used. This is a schema addition relative to
previous versions.
