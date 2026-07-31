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
