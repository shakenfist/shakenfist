# Backing Chain Discovery

The `instar info --chain` command discovers and displays the complete backing
file chain for disk images. This is an instar-specific feature that does not
exist in `qemu-img`.

## Overview

QCOW2 images can have backing files - a chain of images where each overlay
contains only the changed data relative to its parent. When processing such
images, it's critical to know the full chain for security validation and
correct data handling.

```
top.qcow2 (overlay)
    └── middle.qcow2 (intermediate)
            └── base.qcow2 (base image, no backing file)
```

## Usage

```bash
# Discover and display the backing chain
instar info --chain image.qcow2
```

### Example Output

```
Chain: 3 image(s)
  [0] /path/to/top.qcow2 (qcow2) -> middle.qcow2
      virtual size: 10 GiB (10737418240 bytes)
      disk size: 512 KiB (524288 bytes)
      cluster size: 65536 bytes
  [1] /path/to/middle.qcow2 (qcow2) -> base.qcow2
      virtual size: 10 GiB (10737418240 bytes)
      disk size: 1 MiB (1048576 bytes)
      cluster size: 65536 bytes
  [2] /path/to/base.qcow2 (qcow2)
      virtual size: 10 GiB (10737418240 bytes)
      disk size: 500 MiB (524288000 bytes)
      cluster size: 65536 bytes
```

For images without backing files, the chain has a single entry:

```
Chain: 1 image(s)
  [0] /path/to/standalone.qcow2 (qcow2)
      virtual size: 10 GiB (10737418240 bytes)
      disk size: 500 MiB (524288000 bytes)
      cluster size: 65536 bytes
```

## Security

Backing file paths are **untrusted data** embedded in image headers. A
malicious image could reference sensitive system files (e.g., `/etc/shadow`)
in an attempt to exfiltrate data during processing.

### Path Validation

The `--chain` command validates all backing file paths against a security
allowlist before following them. By default, only files in the same directory
as the input image are allowed.

```bash
# This would fail if base.qcow2 tries to reference /etc/passwd
instar info --chain malicious.qcow2
# Error: Backing file '/etc/passwd' is outside allowed paths: ["/path/to/images"]
```

### Configuration

The allowlist can be configured via the config file:

```toml
# ~/.config/instar/config
[security]
# Directories allowed for backing file resolution
# Special markers:
#   $IMAGE_DIR - directory containing the input image
#   $CWD       - current working directory
backing-path-allowlist = [
    "$IMAGE_DIR",                    # Default: same directory as image
    "/var/lib/libvirt/images",       # Add production image directories
]

# Maximum backing chain depth (default: 16)
max-chain-depth = 16
```

### Chain Depth Limit

To prevent infinite loops from circular references, the chain discovery
enforces a maximum depth (default: 16 levels). This is configurable via
`security.max-chain-depth`.

### Circular Reference Detection

If an image chain contains a circular reference (e.g., A → B → A), the
discovery will detect and report it:

```
Error discovering backing chain: Circular reference detected: /path/to/A.qcow2
```

## How It Works

Unlike host-side parsing approaches, instar's chain discovery maintains the
security model by using the **sandboxed info operation** for all format parsing:

1. Run `instar info` (inside KVM sandbox) on the top image
2. Extract the backing file path from the result
3. Validate the path against the security allowlist (on the host)
4. If valid, run `instar info` on the backing file
5. Repeat until reaching an image with no backing file

This ensures that all image header parsing happens inside the secure KVM
guest, while the host only performs path validation.

## Supported Formats

Chain discovery works with any format that supports backing files:

| Format | Backing File Support |
|--------|---------------------|
| QCOW2  | ✓ Yes |
| QCOW1  | ✓ Yes |
| Raw    | No |
| VMDK   | ✓ Yes (descriptor parentFileNameHint) |
| VHD    | No |
| VHDX   | No |

## Comparison with qemu-img

`qemu-img info --backing-chain` provides similar functionality but:

1. **Parses on the host** - Headers are parsed directly on the host system,
   exposing it to format parsing vulnerabilities
2. **No path validation** - Will follow any backing file path without
   security checks
3. **JSON output only for chain** - The `--backing-chain` flag requires
   `--output=json`

Instar's `--chain` flag maintains security by parsing in the sandbox and
validating paths before following them.

## Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| `Backing file not found` | Referenced file doesn't exist | Check path in image header |
| `Backing file outside allowed paths` | Path fails allowlist check | Add directory to `backing-path-allowlist` |
| `Chain depth exceeds maximum` | Chain has too many levels | Increase `max-chain-depth` or investigate chain |
| `Circular reference detected` | Chain loops back to itself | Fix the image chain |
| `Info operation failed` | Image parsing error | Check if image is corrupted |

## Operations Using Chain Discovery

The chain discovery infrastructure is used by the following operations:

- **`instar info --chain`** - Discover and display the full backing chain
- **`instar check --chain`** - Validate entire backing chains for consistency.
  Each backing image is loaded as a separate virtio-block device in the KVM
  guest and checked for format consistency, non-zero virtual size, and QCOW2
  header integrity. Chain errors are reported separately from primary image
  errors.
- **`instar compare`** - Automatically discovers backing chains for both images
  being compared. All chain images are loaded as separate virtio-block devices
  in the KVM guest. Unallocated QCOW2 clusters are resolved by walking the
  backing chain, so overlay images compare correctly against their flattened
  equivalents. Supports multi-level chains (e.g., top -> mid -> base) and
  chains on both sides of the comparison.
- **`instar convert`** - Discovers backing chains for the input image and loads
  all chain images as separate virtio-block devices for flattening. The guest
  walks the chain to resolve unallocated clusters, producing a standalone
  output image with no backing dependencies.
