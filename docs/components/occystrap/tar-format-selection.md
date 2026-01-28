# Tar Format Selection

Occystrap uses smart tar format selection to minimize output size when
re-creating layer tarballs. This document explains the problem, solution,
and implementation details.

## The Problem

When occystrap filters modify container image layers (e.g., normalizing
timestamps or excluding files), the layers must be re-tarred. Python's
`tarfile` module defaults to PAX format (POSIX.1-2001), which adds extended
header blocks for certain metadata.

The issue arises with **long filenames**. Container images often contain
deeply nested paths, especially those with Rust toolchains, Node.js modules,
or similar dependencies. For example:

```
home/vscode/.rustup/toolchains/nightly-x86_64-unknown-linux-gnu/lib/rustlib/...
```

When a filename exceeds 100 characters, PAX format adds an extended header
block (~1KB) for that file. In a typical Rust development container with
~50,000 files where 98% have paths longer than 100 characters, this adds
approximately **50MB of overhead per layer**.

### Real-World Example

Analysis of a `virtio-block-dev` container image:

| Metric | Value |
|--------|-------|
| Total files | 51,008 |
| Files with paths > 100 chars | 50,379 (98.8%) |
| Original layer size | 1,315 MB |
| PAX format output | 1,367 MB (+52 MB) |
| USTAR format output | 1,315 MB (+6 KB) |

The difference is entirely due to PAX extended headers for long filenames.

## The Solution

Occystrap now uses **USTAR format** (POSIX.1-1988) by default, which handles
long paths more efficiently using a prefix+name split:

- **name field**: 100 bytes for the filename portion
- **prefix field**: 155 bytes for the directory path
- **Total**: Up to 256 characters without extra headers

USTAR format falls back to PAX only when content requires features that
USTAR cannot represent.

## USTAR Format Limits

The following conditions trigger automatic fallback to PAX format:

| Limit | USTAR Maximum | Notes |
|-------|---------------|-------|
| Path length | 256 characters | prefix (155) + '/' + name (100) |
| Basename | 100 characters | Filename portion after last '/' |
| Symlink target | 100 characters | The path the symlink points to |
| File size | 8 GiB - 1 byte | Octal representation limit |
| UID/GID | 2,097,151 | Octal value 7777777 |
| Character encoding | ASCII only | Non-ASCII requires PAX |

## Implementation

The format selection is implemented in `occystrap/tarformat.py`:

```python
from occystrap.tarformat import select_tar_format_for_layer

# Scan layer and select optimal format
tar_format = select_tar_format_for_layer(layer_fileobj, transform_fn)

# Use selected format when writing
with tarfile.open(fileobj=dest, mode='w', format=tar_format) as tar:
    ...
```

The `select_tar_format_for_layer()` function:

1. Scans all members in the source tar
2. Applies any transformation function (e.g., timestamp normalization)
3. Checks each member against USTAR limits
4. Returns `USTAR_FORMAT` if all members fit, `PAX_FORMAT` otherwise
5. Short-circuits on first PAX-requiring member for efficiency

## Affected Components

### Filters (Smart Format Selection)

The following filters use smart format selection, scanning layer contents to
determine the optimal format:

- **TimestampNormalizer**: Normalizes file timestamps for reproducible builds
- **ExcludeFilter**: Removes files matching glob patterns

Both filters now produce significantly smaller output when processing layers
with many long-named files.

### Output Writers (Direct USTAR)

The following output writers create outer tarballs (containing layer blobs,
config, and manifest). These always use USTAR format directly without scanning,
since the outer tar only contains short paths (SHA256 hashes ~75 characters):

- **TarWriter**: Creates docker-loadable tarballs
- **DockerWriter**: Loads images into the Docker daemon

The layer content itself (which may have long paths) is pre-built and added as
a binary blob, so the outer tar format doesn't affect file paths within layers.

## Compatibility

USTAR format is universally supported:

- Docker accepts both USTAR and PAX format layers
- OCI specification allows both formats
- All POSIX-compliant tar implementations support USTAR
- The format is automatically detected when reading

There is no compatibility impact from this change.

## Verification

To verify the format selection is working, enable debug logging:

```python
import logging
logging.getLogger('occystrap.tarformat').setLevel(logging.DEBUG)
```

You'll see messages like:
```
DEBUG:occystrap.tarformat:Layer compatible with USTAR format
```

Or when PAX is required:
```
DEBUG:occystrap.tarformat:Layer requires PAX format
```
