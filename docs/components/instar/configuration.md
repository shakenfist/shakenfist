# Configuration Guide

This document describes instar's configuration options, including command-line
flags and configuration files.

## Command-Line Flags

### Output Control

| Flag | Description |
|------|-------------|
| `--output=human` | Human-readable output (default) |
| `--output=json` | Machine-parseable JSON output |
| `--extra-detail` | Include format-specific details not provided by qemu-img |

#### `--extra-detail`

Includes additional format-specific information that qemu-img does not output.
This provides more comprehensive details about the disk image while maintaining
compatibility with standard output.

Currently supported extra details:

- **VDI format**: image-type, block-size, blocks-in-image, blocks-allocated, uuid
- **LUKS format detection**: Detects LUKS encrypted volumes (qemu-img reports these as "raw")

```bash
# Default: matches qemu-img output
instar info image.vdi

# With --extra-detail: includes VDI-specific fields
instar info --extra-detail image.vdi
# format specific information:
#     image-type: dynamic
#     block-size: 1048576
#     blocks-in-image: 1024
#     blocks-allocated: 0
#     uuid: 12345678-1234-1234-1234-123456789abc
```

**LUKS Detection**

LUKS (Linux Unified Key Setup) encrypted volumes are detected by their magic
bytes but qemu-img does not recognize them, reporting them as "raw" format.
With `--extra-detail`, instar correctly identifies LUKS volumes:

```bash
# Default: matches qemu-img (reports as unknown due to no partition table)
instar info encrypted.luks
# file format: unknown

# With --extra-detail: detects LUKS format
instar info --extra-detail encrypted.luks
# file format: luks
```

### Quirk Control

instar categorizes qemu-img behaviors as "safe quirks" (formatting differences)
and "unsafe quirks" (security-affecting behaviors). See [quirks.md](/components/instar/quirks/)
for the full classification.

| Flag | Safe Quirks | Unsafe Quirks | Use Case |
|------|-------------|---------------|----------|
| (default) | Enabled | Disabled | Production use |
| `--ignore-quirks` | Disabled | Disabled | Intuitive output |
| `--unsafe-quirks` | Enabled | Enabled | Compatibility testing |

#### `--ignore-quirks`

Disables safe quirks for more intuitive output:

- Reports actual file sizes instead of block-rounded values
- Uses standard rounding instead of banker's rounding
- Shows precise values instead of 3-significant-figure formatting

This flag is useful when you want accurate filesystem information rather
than qemu-img-compatible output.

```bash
# Default: matches qemu-img output
instar info image.qcow2
# disk size: 196 KiB (rounded to 4K blocks)

# With --ignore-quirks: actual values
instar info --ignore-quirks image.qcow2
# disk size: 191.2 KiB (actual file size)
```

#### `--unsafe-quirks`

Enables unsafe quirks that match qemu-img's behavior but introduce security
vulnerabilities. **Use only for compatibility testing, never in production.**

The primary unsafe quirk is "RAW as fallback format" - treating any file
without a recognized format header as a valid raw disk image. This behavior
enables backing file disclosure attacks (CVE-2015-5163, CVE-2024-32498).

```bash
# Default: rejects files without valid format or partition table
instar info /etc/passwd
# Error: Unknown format (no valid disk image header or partition table)

# With --unsafe-quirks: matches qemu-img (insecure)
instar info --unsafe-quirks /etc/passwd
# file format: raw
# virtual size: 2.5 KiB
```

**Warning**: Never use `--unsafe-quirks` when processing untrusted images.
It exists solely for verifying qemu-img output compatibility in test suites.

### Format Specification

| Flag | Description |
|------|-------------|
| `-f FORMAT` / `--format=FORMAT` | Explicitly specify input format |

When format is specified explicitly, instar skips format auto-detection and
parses the file using the specified format's parser directly.

```bash
instar info --format=qcow2 image.qcow2
```

### Convert Options

| Flag | Description |
|------|-------------|
| `-O FORMAT` / `--output-format=FORMAT` | Output format (raw, qcow2) |
| `-c` / `--compress` | Enable zlib compression for QCOW2 output |
| `-S` / `--skip-zeros` | Skip writing zero-filled clusters (default, sparse output) |
| `--no-skip-zeros` | Write all clusters including zeros (dense output) |
| `--cluster-size=N` | Output cluster size for QCOW2 (512 to 65536, default 65536) |
| `-p N` / `--progress=N` | Report progress every N seconds |

#### `-c` / `--compress`

Enables zlib (raw deflate) compression for QCOW2 output, equivalent to
`qemu-img convert -c`. Each non-zero data cluster is compressed and packed
at sector-aligned (512-byte) positions. Clusters that don't compress smaller
than the cluster size are written uncompressed as a fallback.

```bash
# Compressed QCOW2 output
instar convert -c -O qcow2 input.raw output.qcow2

# Requires -O qcow2 (compression only applies to QCOW2 output)
instar convert -c input.raw output.raw  # Error: -c requires -O qcow2
```

#### `-S` / `--skip-zeros` (default)

Skips writing zero-filled clusters to the output, producing a sparse file.
This is enabled by default, matching `qemu-img convert` behavior. The
default can be overridden via the config file:

```toml
[convert]
sparse = false  # Disable sparse output by default
```

Resolution order (first match wins):
1. `--no-skip-zeros` on the command line (forces dense output)
2. `--skip-zeros` / `-S` on the command line (forces sparse output)
3. `convert.sparse` in the config file
4. Default: `true` (sparse output)

For raw output, the output file is truncated to the image's virtual size
after conversion so the apparent file size matches the source image.

### Compare Options

| Flag | Description |
|------|-------------|
| `-s` / `--strict` | Fail immediately if images differ in size |
| `-q` / `--quiet` | Suppress output (exit code only) |

In non-strict mode (default), images of different sizes are considered
identical if the extra sectors in the larger image are all zeros. Strict
mode fails immediately on any size difference, matching `qemu-img compare -s`.

### Backing Chain

| Flag | Description |
|------|-------------|
| `--chain` | Discover and report backing file chain |

See [chain-discovery.md](/components/instar/chain-discovery/) for details on secure backing
chain discovery.

---

## Configuration File

instar can read configuration from a TOML file at:
- `~/.config/instar/config.toml` (user configuration)
- `/etc/instar/config.toml` (system configuration)

### Example Configuration

```toml
# ~/.config/instar/config.toml

[output]
# Default output format: "human" or "json"
format = "human"

[quirks]
# Disable safe quirks for more intuitive output
ignore_safe = false

# Enable unsafe quirks (NOT RECOMMENDED for production)
# This matches qemu-img's insecure behavior
enable_unsafe = false
```

### Configuration Precedence

Command-line flags override configuration file settings:

1. Command-line flags (highest priority)
2. User configuration (`~/.config/instar/config.toml`)
3. System configuration (`/etc/instar/config.toml`)
4. Built-in defaults (lowest priority)

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `INSTAR_CONFIG` | Override configuration file path |
| `INSTAR_TESTDATA_PATH` | Test data directory (for development) |
| `INSTAR_BINARY_PATH` | Override instar binary path (for testing) |

---

## Security Considerations

### Default Secure Configuration

instar's defaults are chosen for security:

1. **Format validation**: Files must have recognized format headers or valid
   partition tables to be accepted as disk images

2. **Backing file reporting**: Backing file paths are reported but never
   followed (KVM sandbox prevents filesystem access)

3. **Resource limits**: Operations are bounded by guest memory (32MB) and
   timeout mechanisms

### When to Use `--unsafe-quirks`

The only legitimate use case for `--unsafe-quirks` is compatibility testing
against qemu-img output. For example:

```bash
# In test suite: verify instar matches qemu-img for arbitrary files
instar info --unsafe-quirks test-file.bin > instar.out
qemu-img info test-file.bin > qemu.out
diff instar.out qemu.out
```

Never use `--unsafe-quirks` when:
- Processing user-uploaded images
- Running in production environments
- Handling images from untrusted sources

---

## Related Documentation

- [quirks.md](/components/instar/quirks/) - Detailed quirk documentation and classification
- [format-detection-safety.md](/components/instar/format-detection-safety/) - Security model
- [chain-discovery.md](/components/instar/chain-discovery/) - Backing chain discovery
- [output-formats.md](/components/instar/output-formats/) - Output format details
