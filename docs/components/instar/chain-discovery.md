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
| VHD    | ✓ Yes | ✓ Yes (`convert`, `dd`, `compare`, `bench`, `rebase`) |
| VHDX   | ✓ Yes | ✓ Yes (`convert`, `dd`, `compare`, `bench`, `rebase`) |

Chain discovery resolves a differencing VHD or VHDX parent for `instar
info --chain` and for the five operations listed, each of which composes
the parent's sectors into its own read. `commit` and `check` discover
the same chain but never resolve a differencing parent in it — they
record the reference without following it, because neither composes
sector data from one. `map` and `measure` do not use chain discovery at
all. See "Known limitations" below for the detail.

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

- **`instar info --chain`** - Discover and display the full backing chain,
  resolving a differencing VHD or VHDX parent as well as a qcow2 or vmdk one.
- **`instar check --chain`** - Validate entire backing chains for consistency.
  Each backing image is loaded as a separate virtio-block device in the KVM
  guest and checked for format consistency, non-zero virtual size, and QCOW2
  header integrity. Chain errors are reported separately from primary image
  errors. A differencing VHD or VHDX parent in the chain is recorded but not
  resolved — `check` refuses a differencing source outright, so it has
  nothing to validate through one.
- **`instar commit`** - Discovers the backing chain to merge an overlay into
  its parent. A differencing VHD or VHDX parent is recorded but not
  resolved, for the same reason as `check`.
- **`instar compare`** - Automatically discovers backing chains for both images
  being compared, resolving a differencing VHD or VHDX parent in either
  chain as well as a qcow2 or vmdk one. All chain images are loaded as
  separate virtio-block devices in the KVM guest. Unallocated clusters are
  resolved by walking the backing chain, so overlay images compare correctly
  against their flattened equivalents. Supports multi-level chains (e.g.,
  top -> mid -> base) and chains on both sides of the comparison.
- **`instar convert`** / **`instar dd`** - Discover the backing chain for the
  input image, resolving a differencing VHD or VHDX parent as well as a
  qcow2 or vmdk one, and load all chain images as separate virtio-block
  devices for flattening. The guest walks the chain to resolve unallocated
  or parent-owned sectors, producing a standalone output image with no
  backing dependencies.
- **`instar bench`** / **`instar rebase`** - Also resolve a differencing VHD
  or VHDX parent in the chain they read, so the sectors the top image
  leaves to its parent are served from the parent rather than silently
  skipped. A rebase detach covers the whole virtual size; a benchmark
  covers only the offsets it was asked to read.

## Known limitations

### A differencing VHD or VHDX parent is resolved only by the operations that read it

`discover_backing_chain` takes two things that together decide what it
does with a VHD (`disk_type == 4`) or VHDX (`HasParent` set) parent: what
the caller will do with the chain (report it, or compose sector data from
it), and, for a composing caller, whether that specific operation can
read a differencing parent at all. An operation that will read the
parent's sectors — `convert`, `dd`, `compare`, `bench` and `rebase` —
resolves it through the same allowlist and depth-limit checks described
above for a qcow2 backing file. An operation that discovers the chain but
will not read a differencing parent — `check` and `commit`, each of which
refuses or does not reach such a source — records the reference (so
`instar info` can still report it) without resolving or opening it. This
split exists so that a refusal is never contingent on whether the parent
file happens to exist: an operation that cannot compose a differencing
source refuses it before the parent's presence or absence could change
the answer, rather than resolving it just to throw the result away. The
*reporting* walk behind `instar info --chain` resolves the parent
unconditionally, whatever the checked-out image's own operation could do
with one, because it has no refusal to make contingent on the parent's
presence — and continues the listing into it:

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
the very next `convert` of the same image does. VHD and VHDX now carry
the same disagreement to protect for five operations, and `info --chain`
resolves their parent unconditionally regardless.

This is no longer "discovery, not composition" for every caller: `convert`,
`dd`, `compare`, `bench` and `rebase` read a resolved VHD or VHDX
parent's sector data, exactly like any other chain member. The invariant
that survives is the one the differencing work established at the
outset: an operation must never resolve a parent it will not go on to
read. An
operation that cannot compose a differencing source refuses it before the
parent's presence or absence could change the answer — resolving the
parent only to discard the result would make the same differencing image
give a typed refusal when its parent happened to sit beside it and a path
error when it did not, and a refusal that depends on a file the operation
is not going to read is not a refusal. That is why `check` and `commit`
still record the reference without resolving it, and why `instar info
--chain`, which has no refusal to protect, resolves unconditionally.

Three lists worth keeping distinct, because they are not the same set.
The operations that *resolve* a differencing parent — composing sector
data from it — are `convert`, `dd`, `compare`, `bench` and `rebase`, plus
`info --chain`'s reporting walk, which resolves one without composing
anything. The operations that discover a chain but never resolve a
differencing parent in it are `check` and `commit`. The operations that
refuse *every* differencing source outright are `measure` and `map`,
neither of which uses chain discovery at all; `check` also refuses one,
through its own guest-side check rather than through chain discovery, so
it refuses regardless of whether `--chain` walked anything. `commit` and
`rebase` separately reject a VHD or VHDX source before any parent is
considered, for an unrelated reason: neither operation supports those
formats as the file being acted on at all (`commit` and `rebase` are
qcow2 and VMDK only) — so that refusal is not a differencing refusal and
would stand even for a VHD with no parent. See the "VHD/VHDX
differencing" section of [quirks.md](/components/instar/quirks/) for the full per-op
record.
