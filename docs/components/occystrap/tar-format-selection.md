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

Format is chosen per member, not per layer: each file is written with a plain
USTAR header when it fits, and with a PAX extended header only when it needs
one. A tar archive may mix the two freely, since a PAX archive is just a USTAR
archive in which some members are preceded by an extended header. One file
which needs PAX therefore costs one extended header, not one for every
long-named file in the layer.

## USTAR Format Limits

The following conditions trigger automatic fallback to PAX format:

| Limit | USTAR Maximum | Notes |
|-------|---------------|-------|
| Path length | 256 characters | prefix (155) + '/' + name (100), counting the '/' added to directory names |
| Basename | 100 characters | Filename portion after last '/' |
| Symlink target | 100 characters | The path the symlink points to |
| File size | 8 GiB - 1 byte | Octal representation limit |
| UID/GID | 2,097,151 | Octal value 7777777 |
| Modification time range | 0 to 8,589,934,591 | Octal; negative mtimes require PAX |
| Owner names | 32 characters | uname and gname |
| Modification time | Whole seconds | Sub-second mtimes require PAX |
| Character encoding | ASCII only | Non-ASCII names or owners require PAX |
| Extended records | None | Any extended record requires PAX |

## Preserving Extended Records

PAX extended headers carry metadata which has no USTAR field at all. The
important ones for container images are the `SCHILY.xattr.*` records, which
hold extended attributes:

- `security.capability`: file capabilities set with `setcap`, for example
  `cap_net_raw` on `ping` or a Prometheus blackbox exporter.
- `security.selinux`: SELinux labels.
- `user.*`: arbitrary user xattrs.

Python's USTAR writer silently discards a member's extended records, so a
member which has any is always written as PAX. Until issue
[#151](https://github.com/shakenfist/occystrap/issues/151) was fixed the
format was chosen per layer without considering these records, and the
`normalize-timestamps` and `exclude` filters stripped file capabilities from
almost every layer they rewrote.

Not every record is copied forward. When a member is rewritten, occystrap
drops:

- Records which duplicate a header field (`path`, `linkpath`, `size`, `uid`,
  `gid`, `uname`, `gname`, `mtime`). Python applies these to the member when
  reading, but they would take priority over the member's fields when
  writing, undoing changes such as a normalized mtime. Python regenerates them
  from the fields when a member needs them.
- Records which describe how the source archive was encoded (`hdrcharset` and
  `GNU.sparse.*`). The data written is the decoded data, so these would no
  longer describe it and would corrupt the output. For the same reason, old
  GNU sparse members (type `S`) are written as regular files.

The `normalize-timestamps` filter additionally drops `atime`, `ctime` and
`LIBARCHIVE.creationtime` records, so that they cannot vary between builds.

## Implementation

The format selection is implemented in `occystrap/tarformat.py`:

```python
from occystrap import tarformat

with tarfile.open(fileobj=dest, mode='w') as out:
    with tarfile.open(fileobj=src, mode='r') as tar:
        for member in tar:
            fileobj = tar.extractfile(member) if member.isfile() else None
            tarformat.add_member(out, member, fileobj)
```

`add_member()`:

1. Drops extended records which must not be copied forward
   (`prepare_member_for_rewrite()`)
2. Checks the member for remaining extended records and for information
   USTAR would silently lose, then asks Python's tarfile to encode a USTAR
   header for it, so that the hard limits are exactly the ones the writer
   enforces (`needs_pax_format()`)
3. Writes the member as USTAR or PAX accordingly

No separate scan of the layer is needed, so each layer is read only once.

## Affected Components

### Filters (Smart Format Selection)

The following filters rewrite layers using per-member format selection:

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

The layer content itself (which may have long paths and extended records) is
pre-built and added as a binary blob, so the outer tar format doesn't affect
file paths or extended attributes within layers.

## Compatibility

USTAR format is universally supported:

- Docker accepts both USTAR and PAX format layers
- OCI specification allows both formats
- All POSIX-compliant tar implementations support USTAR
- The format is automatically detected when reading

Rewritten layers which contain extended records, or which previously needed
PAX for some other reason, produce different bytes (and so different digests)
than older occystrap releases did, because records are now kept and long
names in those layers now use USTAR headers. Layers which never needed PAX are
written exactly as before.

Layer cache entries made with filters by older releases are not reused,
since they may hold layers with their file capabilities stripped. The cache
key includes a version of the rewriting rules
(`tarformat.LAYER_REWRITE_VERSION`), so the first push with filters after
upgrading rewrites and uploads those layers again.
