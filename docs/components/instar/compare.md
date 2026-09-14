# Compare

`instar compare` compares two images for identical content, as a
drop-in replacement for `qemu-img compare`.

```bash
# Compare two images for identical content (matches qemu-img compare output)
instar compare image1.raw image2.raw

# Strict mode: fail if images differ in size (even if content matches)
instar compare -s image1.raw image2.raw

# JSON output for programmatic consumption
instar compare --output json image1.raw image2.raw

# Compare LUKS-encrypted QCOW2 against decrypted raw
instar compare --luks-passphrase secret encrypted.qcow2 decrypted.raw
```

Exit codes: 0 = identical, 1 = content differs.

The compare operation reads the virtual content of both images and reports the
first byte offset where content diverges. For QCOW2 and VMDK images, this
includes cluster/grain/block table lookup and compressed cluster decompression
(zlib, ZSTD, and DEFLATE), so comparisons work across formats (e.g., QCOW2
vs raw, VMDK vs raw, VHD vs raw, compressed QCOW2 vs uncompressed QCOW2).
LUKS-encrypted QCOW2 images (crypt_method=2) can be compared at the
plaintext level using `--luks-passphrase`. Backing chains are automatically
discovered and flattened: unallocated clusters/grains are resolved by walking
the backing chain, so overlay images compare correctly against their flattened
equivalents.
When images differ in size, non-strict mode (default) treats extra zero-filled
regions as matching, while strict mode (`-s`) fails immediately on any size
difference.

Output is byte-for-byte identical with `qemu-img compare`.

## Differencing images

A differencing VHD (footer disk type 4) or VHDX (`HasParent` set) stores
only the sectors that differ from a parent image. instar cannot compose a
parent yet, so `compare` refuses such a source by name and exits 1 rather than
reporting a content difference it cannot actually account for:

```
compare: source is a differencing <VHD|VHDX> image whose parent instar cannot
yet compose; composition is deferred (see PLAN-differencing.md)
```

`instar info` is the exception — it reports the parent as a backing file
instead of refusing. See the "VHD/VHDX differencing" section of
[quirks.md](/components/instar/quirks/) for the per-operation record, and
[PLAN-differencing.md](/components/instar/plans/PLAN-differencing/) for the composition
work that will lift the refusal.
