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

Exit codes, as qemu-img compare's:

| Exit | Meaning |
|------|---------|
| 0 | The images are identical. |
| 1 | The images differ. Only a comparison that found a difference exits 1. |
| 2 | An image could not be opened or set up for comparison, or any other error: nothing was compared. |
| 4 | A read failed part-way through the comparison. The error names the image and the virtual offset of the failed read. |

A failure is never reported as a difference. An error goes to stderr as
`Error: "..."` and nothing is printed on stdout, so a script can rely on
exit 1 meaning the images really differ.

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
only the sectors that differ from a parent image. `compare` composes
either or both sides against their own parent chain: each image descends
its own chain independently, so a differencing image on one side is never
read against the other side's chain by mistake.

If an image's own chain has no parent to read, `compare` still refuses
it, rather than reporting a content difference it cannot actually
account for:

```
compare: a differencing <VHD|VHDX> image in the chain compare was given
has no parent behind it, so the sectors it leaves to its parent could
not be composed
```

and exits 2. A parent reference that cannot be resolved — the file is
missing, or outside the backing file allowlist — is refused the same
way, during chain discovery.

See the "VHD/VHDX differencing" section of [quirks.md](/components/instar/quirks/) for the
per-operation record.
