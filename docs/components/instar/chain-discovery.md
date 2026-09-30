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

### JSON output

`instar info --chain --output json` renders the same chain as a JSON
array, one object per member, top image first. Key names match the
non-chain `--output json` form: `filename`, `format`, `virtual-size`,
`actual-size`, `cluster-size` (omitted when the format has none, as
for raw) and `backing-filename` (the unresolved reference from that
image's own header, present on every member whose header names a
parent). The resolved path of a followed backing file is the next
element's `filename`, not repeated on the referencing element.

`backing-filename` on the **last** element therefore means the listing
is truncated: the walk stopped before resolving that reference, and
the reason is on stderr. A complete chain ends on a member whose
header names no parent, and that member has no `backing-filename`
key. A script that treats the last element as necessarily resolved
will read a truncated chain as a complete one — check for the key, or
check the exit path's stderr, rather than assuming.

```
$ instar info --chain --output json top.qcow2
[
    {
        "filename": "/path/to/top.qcow2",
        "format": "qcow2",
        "virtual-size": 10737418240,
        "actual-size": 524288,
        "cluster-size": 65536,
        "backing-filename": "middle.qcow2"
    },
    {
        "filename": "/path/to/middle.qcow2",
        "format": "qcow2",
        "virtual-size": 10737418240,
        "actual-size": 1048576,
        "cluster-size": 65536,
        "backing-filename": "base.qcow2"
    },
    {
        "filename": "/path/to/base.qcow2",
        "format": "qcow2",
        "virtual-size": 10737418240,
        "actual-size": 524288000,
        "cluster-size": 65536
    }
]
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

| Format | Chain Discovery | Chain Composition |
|--------|------------------|--------------------|
| QCOW2  | ✓ Yes | ✓ Yes |
| QCOW1  | ✓ Yes | ✓ Yes |
| Raw    | No | No |
| VMDK   | ✓ Yes (descriptor parentFileNameHint) | ✓ Yes |
| VHD    | ✓ Yes, via `info --chain`'s reporting walk only | Not yet |
| VHDX   | ✓ Yes, via `info --chain`'s reporting walk only | Not yet |

VHD and VHDX are split across two columns because, uniquely among these
formats, their two columns now disagree: `instar info --chain` resolves
and lists a differencing VHD or VHDX parent, but no operation can yet
compose one into sector data. See "Known limitations" below for what
that means for every other caller.

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

## Known limitations

### A differencing VHD or VHDX parent is walked only by `info --chain`

`discover_backing_chain` takes a policy. A *composing* caller —
`convert`, `dd`, `compare`, `bench`, `check`, `commit` and `rebase` —
stops at a VHD (`disk_type == 4`) or VHDX (`HasParent` set) parent
exactly as before, recording the parent reference (so `instar info` can
still report it) without resolving or opening it. Only the *reporting*
walk behind `instar info --chain` resolves the parent — through the same
allowlist and depth-limit checks described above for a qcow2 backing
file — and continues the listing into it:

```
$ instar info --chain vhd-diff-child-aligned.vhd
Chain: 2 image(s)
  [0] /abs/path/vhd-diff-child-aligned.vhd (vpc) -> vhd-diff-parent.vhd
      virtual size: 16 MiB (16777216 bytes)
      disk size: 4 MiB (4198912 bytes)
      cluster size: 2097152 bytes
  [1] /abs/path/vhd-diff-parent.vhd (vpc)
      ...
```

When the reporting walk cannot resolve a parent, it ends the listing at
the last image it did resolve — that image's own unresolved reference
stays in the printed line (`-> vhd-diff-parent.vhd`) exactly as it
always has — and `instar info --chain` still exits 0. A single line
names the reason on stderr:

```
instar: backing chain stops at <child absolute path>: parent '<reference>' <reason>
```

`<reason>` is one of: the reference is a Windows absolute path and
cannot be resolved on this host; it is outside the backing file
allowlist; it was not found; it would exceed the maximum backing chain
depth; it is already in the chain (circular reference); or it could not
be resolved, for any other reason. `absolute_win32_path` and
`volume_path` VHDX locator values (`C:\images\parent.vhdx`,
`\\?\Volume{GUID}\...`) are reported verbatim, by design, and are
deliberately never rewritten into POSIX convention before that check
runs — inventing a host path out of a drive letter and then testing the
invention against the allowlist would be a path-traversal primitive, not
a convenience, so a locator of this kind always ends the walk with the
Windows-absolute-path reason, never a "not found" one. A relative VHDX
locator does not have this problem: it arrives already rendered into
POSIX convention (`vhdx-diff-parent.vhdx`, not `.\vhdx-diff-parent.vhdx`)
before the host ever sees it, and resolves like any other relative name.

One caveat about the reasons themselves. For an *absolute* reference,
resolution probes the filesystem before the allowlist is consulted, so
"was not found" and "is outside the backing file allowlist" distinguish
whether an attacker-chosen absolute host path exists. Only existence
leaks, never content, and the allowlist still rejects — no path outside
it is ever opened. A qcow2 chain naming an absolute backing file has
always drawn the same distinction; what is new is that `info --chain`
now prints it while exiting 0, so it no longer takes a failing command
to carry the answer. If you run `instar info --chain` over untrusted
images in a service and return its stderr to whoever supplied them,
that is the line to withhold. Tracked as
[issue #611](https://github.com/shakenfist/instar/issues/611).

The fail-soft covers *resolving* the parent reference, and nothing
beyond it. Once a parent resolves, it is read like any other chain
member and the ordinary rules apply, so a malformed cross-format chain
— a VHD or VHDX child naming a parent that is not itself a VHD or VHDX
— can still end in a non-zero exit:

| Resolved parent | `info --chain` |
|---|---|
| a VHD or VHDX, as the format requires | lists it, exits 0 |
| an unidentifiable file | lists it as `unknown`, exits 0 |
| a self-contained qcow2 | lists it as `qcow2`, exits 0 |
| a qcow2 whose own backing file is missing | **exits non-zero** |
| a detected-but-unsupported format such as qed | **exits non-zero** |

The last two are the pre-existing rules for those formats reached at any
chain position, not a new behaviour: a qcow2 chain is not fail-soft, and
a detected-but-unsupported format is refused wherever it appears. Note
also that instar does not check that a resolved parent has the same
format as its child, though the VHD and VHDX specifications require it —
so the third row lists a qcow2 as a VHD's parent without complaint.
Tracked as
[issue #608](https://github.com/shakenfist/instar/issues/608).

`instar info` reports and refuses nothing (see [info.md](/components/instar/info/)), so
`info --chain` exiting non-zero because a parent happened to be absent
would be a worse regression than the one-image listing it replaces.
Contrast qcow2 and VMDK: `info --chain` on either with a missing backing
file still exits non-zero, deliberately, because those formats compose
today — a listing that quietly stopped short would disagree with what
the very next `convert` of the same image does. VHD and VHDX have no
such disagreement to protect, because nothing composes them yet.

This is discovery, not composition: nothing in instar can read the
*data* of a VHD or VHDX parent, only the header fields that name it, and
no composing caller resolves one. A composing operation must never
resolve a parent it will not go on to read — if it did, the same
differencing image would give a typed refusal when its parent happened
to sit beside it and a path error when it did not, and a refusal that
depends on a file instar is not going to read is not a refusal. That is
exactly why `info --chain` walks and nothing else does.

Two different lists appear in this document and in the changelog, and
they are not the same set. The *composing callers* — the operations that
call `discover_backing_chain` with the composing policy — are `convert`,
`dd`, `compare`, `bench`, `check`, `commit` and `rebase`. The operations
that *refuse a differencing source* are `convert`, `dd`, `compare`,
`bench`, `check`, `measure` and `map`. The overlap is not total in either
direction: `measure` and `map` refuse without walking a chain, and
`commit` and `rebase` reject a VHD or VHDX source before any parent is
considered, because neither operation supports those formats at all
(`commit` and `rebase` are qcow2 and VMDK only) — so their refusal is not
a differencing refusal and would stand even for a VHD with no parent. See
the "VHD/VHDX differencing" section of [quirks.md](/components/instar/quirks/) for the
full per-op record. The composition work
in [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/) lifts each
operation's restriction as real chain composition lands, one operation
at a time.
