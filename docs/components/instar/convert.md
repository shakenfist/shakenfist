# Convert

`instar convert` converts between disk image formats, as a drop-in
replacement for `qemu-img convert`.

```bash
# Convert QCOW2 to raw (flattens backing chains)
instar convert input.qcow2 output.raw

# Convert any input to QCOW2 v3 output
instar convert -O qcow2 input.raw output.qcow2

# Convert QCOW2 with backing chain to standalone QCOW2
instar convert -O qcow2 overlay.qcow2 standalone.qcow2

# Convert with compressed QCOW2 output (zlib/deflate compression)
instar convert -c -O qcow2 input.raw output.qcow2

# Convert with dense output (write all clusters including zeros)
instar convert --no-skip-zeros input.qcow2 output.raw

# Specify output cluster size for QCOW2 (512 to 2097152, default: 65536)
instar convert -O qcow2 --cluster-size 4096 input.raw output.qcow2
instar convert -O qcow2 --cluster-size 2097152 input.raw output.qcow2

# Write QCOW2 output with extended L2 entries (16-byte entries with subcluster bitmaps)
instar convert -O qcow2 --extended-l2 input.raw output.qcow2

# Write LUKS-encrypted QCOW2 output (AES-256-XTS, crypt_method=2)
instar convert -O qcow2 --luks-encrypt-passphrase 'secret' input.raw encrypted.qcow2

# Decrypt LUKS-encrypted QCOW2 back to raw
instar convert --luks-passphrase 'secret' encrypted.qcow2 output.raw

# Convert to VHD dynamic format
instar convert -O vpc input.qcow2 output.vhd

# Convert to VHDX dynamic format
instar convert -O vhdx input.qcow2 output.vhdx

# Decrypt native LUKS v2 container (Argon2id KDF)
instar convert --luks-passphrase 'secret' --max-guest-memory 1G encrypted.luks output.raw

# Decrypt LUKS container wrapping a QCOW2 image
instar convert --luks-passphrase 'secret' luks-wrapped.img output.raw

# Specify VMDK grain size (4096 to 65536, default: 65536)
instar convert -O vmdk --grain-size 4096 input.raw output.vmdk

# Specify VHD block size (524288+, default: 2097152)
instar convert -O vpc --block-size 1048576 input.raw output.vhd

# Specify VHDX block size (1048576 to 268435456, default: 33554432)
instar convert -O vhdx --block-size 4194304 input.raw output.vhdx

# Progress reporting
instar convert -p 5 input.qcow2 output.raw
```

The convert operation reads the virtual content of an input image (including
backing chain flattening) and writes it in the requested output format.
Compressed clusters (zlib/deflate and ZSTD) are decompressed transparently,
including clusters up to 2MB. QCOW2 v3 images with extended L2 entries
(subclusters) are also supported. Legacy AES-128-CBC encrypted QCOW2 images
(`crypt_method=1`) can be decrypted with `--qcow2-password`. LUKS-in-QCOW2
images (`crypt_method=2`) and native LUKS containers can be decrypted with
`--luks-passphrase`. LUKS v2 containers using Argon2id KDF require
`--max-guest-memory` (e.g., `--max-guest-memory 1G`). Native LUKS
containers wrapping QCOW2 images are transparently detected and
decrypted, with the inner QCOW2 processed as the conversion source.
Individual snapshots can be extracted with `--snapshot <name-or-id>`.

By default, convert produces sparse output by skipping zero-filled clusters
(matching `qemu-img convert` behavior). Use `--no-skip-zeros` for dense output.
The default can also be set via `convert.sparse` in the config file.

## Supported output formats

- **raw** (default) - Flat raw output
- **qcow2** - QCOW2 v3 output with 16-bit refcounts, configurable cluster
  size (512 bytes to 64KB, default 64KB), optional zlib compression (`-c`)
- **vmdk** - VMDK monolithicSparse output (default), streamOptimized
  with `-c`, or monolithicFlat with `--subformat monolithicFlat`.
  Configurable grain size (4KB-64KB, default 64KB via `--grain-size`)
  for sparse/streamOptimized output
- **vpc** - VHD dynamic output, configurable block size (512KB+,
  default 2MB via `--block-size`)
- **vhdx** - VHDX dynamic output, configurable block size (1MB-256MB,
  default 32MB via `--block-size`)
