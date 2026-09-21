# `instar create` — create a new empty disk image

`instar create` produces a new empty disk image in a target format. It is
the safe, sandboxed equivalent of `qemu-img create`: for every supported
format, `qemu-img info --output=json` on an `instar create`-produced
image matches `qemu-img info` on the corresponding `qemu-img create`
image, modulo a documented divergence whitelist (filename, actual-size,
vmdk per-invocation CIDs, vhdx log-size).

Raw output is host-only — `open(O_CREAT|O_TRUNC) + ftruncate` plus
optional preallocation — so it does not require KVM. Every other target
format runs the `create.bin` guest in the KVM sandbox and writes the
metadata via virtio-block.

## Synopsis

```
instar create [-f FMT] [OPTIONS] [-b BACKING [-F FMT] [-u]] \
              FILENAME [SIZE]
```

Common options:

```
  -f, --format <FMT>            raw | qcow2 | vmdk | vpc | vhdx
                                Default: raw
  -b, --backing <BACKING>       Backing file path (embedded verbatim)
  -F, --backing-format <FMT>    Backing file format hint
  -u, --backing-unsafe          Don't fail if backing isn't accessible
  -q, --quiet                   Suppress "Created: ..." line on success
      --output <FORMAT>         human (default) | json
  -o, --options <KEY=VALUE,...> qemu-img-style options (repeatable)
      --preallocation <MODE>    off | metadata | falloc | full
```

Per-target option flags (alternatives to `-o`):

```
  qcow2: --cluster-size, --refcount-bits, --extended-l2,
         --lazy-refcounts, --compat
  vmdk:  --subformat, --grain-size
  vpc:   --subformat, --block-size
  vhdx:  --block-size
```

`SIZE` accepts a suffix from `{b,K,M,G,T}` (e.g. `1G`, `512M`). It is
required unless `-b BACKING` is given, in which case it defaults to the
backing file's virtual size.

The full flag surface is reported by `instar create --help`.

## Target formats

| Target | Subformats             | Backing? | qemu-img info equivalence |
|--------|------------------------|----------|---------------------------|
| raw    | (host-only)            | No       | byte-equivalent (file size + zero-fill) |
| qcow2  | n/a                    | Yes      | info-equivalent (modulo refcount_bits, compat, zstd) |
| vmdk   | monolithicSparse, streamOptimized | Yes | info-equivalent |
| vpc    | dynamic, fixed         | Yes (dynamic only) | info-equivalent (modulo CHS virtual_size rounding) |
| vhdx   | dynamic                | Yes      | info-equivalent (modulo default block_size when unspecified) |

A "No" in the *Backing?* column means the format cannot be created
*as a child* at all: `-b` / `-o backing_file` is rejected. Raw is the
only such target — it has no metadata to record a backing reference
in. vpc and vhdx may be used as the **parent** of a qcow2 or vmdk
child regardless of format, which is what `tests/test_create.py`'s
`test_vhdx_as_backing` exercises; they may now also be used as a
**child** of a parent of their own format — see the per-format
sections below. vpc's "Yes" is qualified because a differencing VHD
is necessarily `subformat=dynamic`: only a dynamic disk has the
header and BAT a parent reference lives in, so `-o subformat=fixed`
together with `-b` is refused. A fixed VHD is fine as the **parent**
of a differencing child.

The "info-equivalence" contract is verified by the cross-version baseline
matrix in `instar-testdata/expected-outputs/create-info-json/` across 80
qemu-img versions (6.0.0 through 10.2.0), exercised by
`tests/test_create.py`'s `TestCreateBaselineMatrix`.

## Output format

Human (default):

```
Created: foo.qcow2 (format=qcow2, virtual_size=1073741824, unit_size=65536)
```

JSON (`--output=json`):

```json
{
    "format": "qcow2",
    "filename": "foo.qcow2",
    "virtual_size": 1073741824,
    "resolved_unit_size": 65536,
    "metadata_bytes_written": 196608,
    "file_size_after": 196608
}
```

`-q` (quiet) suppresses both forms on success and only prints errors.

## `-o key=value,...` reference

Repeatable; later `-o` invocations override earlier ones. Individual
flag values are overridden by matching `-o` keys when both are
specified.

### qcow2

Honoured:
- `size` (alternative to the positional `SIZE`)
- `cluster_size` (512..2 MiB power of two)
- `refcount_bits` — see Known divergences
- `extended_l2` (on|off; requires `cluster_size >= 16k`)
- `lazy_refcounts` (on|off)
- `compat` (0.10|1.1) — see Known divergences
- `compression_type` (zlib|zstd) — see Known divergences
- `preallocation` (off|metadata|falloc|full)
- `backing_file`, `backing_fmt` (alternative to `-b` / `-F`)

Rejected (future work):
- `data_file`, `data_file_raw`, `encrypt.*`

### vmdk

Honoured:
- `size`, `subformat` (monolithicSparse|streamOptimized)
- `backing_file`, `backing_fmt`

Rejected (instar doesn't emit these subformats):
- `subformat=monolithicFlat`, `subformat=twoGbMaxExtent*`

Note: qemu-img doesn't accept an `-o grain_size=` key — grain is set by
the subformat. instar exposes `--grain-size` as an independent flag.

### vpc (VHD)

Honoured:
- `size`, `subformat` (dynamic|fixed)
- `backing_file`, `backing_fmt` (alternative to `-b` / `-F`) — a VHD
  child with a parent is a *differencing* disk (`disk_type=4` plus a
  populated parent locator entry in the dynamic header). The guest
  reads the parent's identity — footer `uuid` and `timestamp` — off
  the parent itself and writes it into the child. The parent must
  itself be a VHD: format detection decides, not the `-F` hint, and a
  parent that detects as anything else (or a hint that contradicts
  detection) is refused with `ERROR_PARENT_FORMAT_MISMATCH`. Either
  subformat of VHD is acceptable as the parent — a fixed VHD carries
  its `conectix` cookie only in the trailing footer, and the parent
  probe reads the footer rather than trusting the first sector. The
  *child*, though, is necessarily dynamic: `subformat=fixed` with a
  backing file is refused ("invalid option for target format"),
  because a fixed VHD has no dynamic header to record a parent in.
  See [Backing-file semantics](#backing-file-semantics) below and the
  "VHD/VHDX differencing" section of [quirks.md](/components/instar/quirks/).

Accepted but no size effect:
- `force_size`

### vhdx

Honoured:
- `size`, `block_size` (1 MiB..256 MiB power of two)
- `backing_file`, `backing_fmt` — a VHDX child with a parent sets the
  `HasParent` file-parameter bit and writes a populated parent
  locator metadata item carrying the parent's active-header
  `DataWriteGuid`. The parent must itself be a VHDX, checked the same
  way as vpc above: detection decides, and a mismatch (or a
  contradicting `-F`) is refused with `ERROR_PARENT_FORMAT_MISMATCH`.
  See [Backing-file semantics](#backing-file-semantics) below and the
  "VHD/VHDX differencing" section of [quirks.md](/components/instar/quirks/).

Accepted but no size effect:
- `log_size`

### raw

Honoured:
- `size`, `preallocation` (off|falloc|full)

Rejected:
- `preallocation=metadata` (raw has no metadata to preallocate)
- Any backing-file key (raw doesn't support backing)

## Backing-file semantics

This section describes `create` for the target formats that accept a
backing file: qcow2, vmdk, vpc and vhdx. Raw has no way to record one.

The user-typed path is embedded verbatim into the new image's metadata,
matching qemu-img: a relative path stays relative; an absolute path
stays absolute. This holds for qcow2, vmdk, and for the VHD parent
unicode name field, which is the field qemu and libvhdi resolve a VHD
parent through. It does **not** hold for the vpc/vhdx *locator* path
that records where a Hyper-V tool would look for the parent: a
relative path is normalised into the Hyper-V convention before being
written there (`/` becomes `\`, prefixed `.\`), while a POSIX-absolute
path is kept verbatim under the Windows-defined locator key, because
no honest Windows-style rendering of an absolute POSIX path exists.
See the "VHD/VHDX differencing" section of [quirks.md](/components/instar/quirks/) for
the rationale and the resulting path-length limits. The host resolves
the path **relative to the new image's directory** when opening the
backing file, so the resulting reference is portable across moves of
the parent.

A backing file requires either `-F FMT` (explicit format hint) or `-u`
(unsafe; skip the accessibility check). `-u` is **not** a shorthand for
`-F raw` — it asserts nothing about the format at all. See
[`-u` is not `-F raw`](#-u-is-not-f-raw) below, which is the difference
that decides how a fixed VHD parent is sized. The hint is used as the
initial format guess; if
the backing file's first sector contradicts the hint via its magic
bytes, auto-detection wins and the metadata records the detected
format. vpc and vhdx additionally require the parent to be their own
format — a vpc child needs a VHD parent, a vhdx child a VHDX one — and
refuse with `ERROR_PARENT_FORMAT_MISMATCH` otherwise, whether the
mismatch comes from detection or from an explicit `-F` that
contradicts it; qcow2 and vmdk are unaffected and continue to accept a
parent of any format they can detect. Three-level chains record only
the immediate parent — instar does not recurse to grandparents
(matches qemu-img). When the backing size would exceed the target
format's addressable range, `ERROR_BACKING_SIZE_TOO_LARGE` fires with
an actionable "try a larger cluster size" hint.

Every check reached through the backing probe — the differencing
refusal below, the parse check and format detection — now runs
whenever `-b` is given, including when an explicit `SIZE` is also
given. Previously `create -b <parent> child 64M` skipped all three
checks because the probe only ran to infer a missing size (#579).

Running the probe always does **not** mean every backing image must
be one the probe can size. It parses raw, qcow2, sparse VMDK, VHD
(either subformat) and VHDX; for anything else — vdi, qcow1, qed,
iso, luks, parallels, bochs, cloop, the VMDK v3 header, a VMDK text
descriptor as `monolithicFlat` produces, or a block device that
stats as zero-length — it reports the format it detected and no
size. That is only fatal where a size is actually needed: omit
`SIZE` and the create is refused, supply one and a qcow2 or vmdk
child is written as before, since those record a parent by path and
never ask it how big it is. A vpc or vhdx child is refused either
way — but with a diagnosis that distinguishes the two cases: a parent
of the wrong format is `ERROR_PARENT_FORMAT_MISMATCH`, while a parent
of the *right* format whose deeper structures will not parse (a VHD
missing its trailing footer, a VHDX with an unreadable region table)
is `ERROR_BACKING_PARSE_FAILED`, since telling the user their VHD is
not a VHD would be both false and unactionable.

### `-u` is not `-F raw`

`-u` is not a synonym for `-F raw` here. A fixed-subformat VHD keeps
no copy of its footer at offset 0, so header detection alone calls one
`raw`; instar falls back to reading the file's last sector for a VHD
footer, as `info`, `check` and `resize` all do, and sizes the parent
from `current_size` if it finds one. Any `-F` that positively asserts
a format suppresses that fallback — `-F raw` included, and `-F vpc`
excepted because it agrees with it — because that is the user
asserting what the file is, and it is also what the child records as
its backing format, so sizing from a footer while writing `raw` would
have the two disagree by the footer's 512 bytes. Detection reports
`raw` for anything it does not recognise as well as for a genuinely
raw file, so without this rule a raw parent whose last 512 bytes
happen to begin with `conectix` would be sized from those bytes even
under `-F qcow2`. `-u` asserts nothing about the
format (it only says "do not fail if the backing file is
inaccessible"), so the fallback still applies: `create -f qcow2 -b
fixed.vhd -u child.qcow2` sizes the parent from its VHD footer, while
`-F raw` on the same file sizes it from the file length. `-u` also
lets a fixed VHD be accepted as a vpc differencing parent without the
user naming a format.

A relative parent path is rewritten into a POSIX-*equivalent* form
rather than kept byte-for-byte: redundant leading `./` components and
repeated separators are collapsed before `/` is mapped to `\\`, so
`-b ./sub//parent.vhdx` emits the locator `.\\sub\\parent.vhdx`, and a
path that collapses to nothing (`./`) is refused. For vhdx this is
user-visible on the way back out, because the locator is the only
record of the path and so is what `info` reports: the child above
reports `sub/parent.vhdx`. A VHD child reports the path exactly as
typed, since `info` reads its parent unicode name field instead.

A differencing child **inherits its parent's virtual size**, and a
`SIZE` that disagrees with the parent is refused with
`ERROR_PARENT_SIZE_MISMATCH` rather than written. The child stores only
the blocks that differ and reads every other block from the parent at
the same offset, so a chain whose two images describe different disks
cannot be composed. Omitting `SIZE` is the right way to ask for a
differencing child. This bites most easily with a qemu-img-created
parent: qemu-img rounds a VHD's virtual size up to CHS geometry and
instar does not, so a parent qemu-img made as `64M` declares
67,125,248 bytes and `-b parent.vhd ... 64M` is a mismatch.

Two rules apply to the **parent path** a vpc or vhdx child records,
both covered in full in the "VHD/VHDX differencing" section of
[quirks.md](/components/instar/quirks/):

- A relative path containing a literal backslash is refused with
  `ERROR_PARENT_PATH_NOT_REPRESENTABLE`. The locator holds a Windows
  path, so instar writes `/` as `\` when filling it, and a backslash
  already in the filename cannot be told apart from one instar
  produced. An absolute path keeps its POSIX bytes and is not subject
  to the rule.
- A relative path caps two code units shorter than an absolute one,
  because the `.\` prefix comes out of the same budget: 254 versus 255
  for VHD, 258 versus 260 for VHDX. Over the cap is
  `ERROR_PARENT_NAME_TOO_LONG`.

A **differencing VHD or VHDX is refused as a backing file**:

```
create failed: backing file is a differencing VHD or VHDX whose parent
instar cannot yet compose; an overlay on it could not be read back
(see PLAN-differencing.md)
```

This has its own error code (`ERROR_BACKING_DIFFERENCING`) rather than
the generic `ERROR_BACKING_PARSE_FAILED`, because the backing header
parses perfectly well — the image is valid, just not one instar can read
through yet. Every read path in instar refuses a
differencing image because the parent cannot be composed yet, so an
overlay stacked on one would be a chain that can never be read back.
Refusing at create time is the only outcome that does not hand you a
dead image. Their plain dynamic parents are accepted normally. See the
"VHD/VHDX differencing" section of [quirks.md](/components/instar/quirks/).

## Preallocation modes

| Mode      | raw                   | qcow2                                                | vmdk / vpc / vhdx |
|-----------|-----------------------|------------------------------------------------------|-------------------|
| `off`     | `ftruncate` only      | header + L1 + refcount only                          | header + BAT only |
| `metadata`| rejected              | L1 + L2 + refcount populated; data region as a hole  | rejected (future work) |
| `falloc`  | `posix_fallocate`     | metadata mode + `posix_fallocate` on data region     | rejected (future work) |
| `full`    | `fallocate(ZERO_RANGE)` with `pwrite` fallback | metadata mode + zero-fill data region   | rejected (future work) |

For qcow2 metadata mode the L2 tables can total well over the guest's
8 MiB scratch limit at large virtual sizes, so the guest streams them
through a reusable single-cluster slot rather than packaging them into
the `MetadataPlan`. The host's `apply_preallocation` helper handles
`falloc` and `full` post-emission.

## Known divergences from `qemu-img create`

The cross-version baseline matrix passes for the bulk of supported
options, but four categories of writer divergence are documented and
tracked as future work. The canonical list (with per-case rationale)
is `KNOWN_WRITER_DIVERGENCES` in `tests/test_create.py`.

- **VHD `virtual_size`**: qemu-img rounds up to the next CHS-aligned
  multiple (legacy VHD geometry); instar emits exact bytes. The
  divergence is typically < 256 KiB. Both files are valid VHDs; the
  difference surfaces only in `qemu-img info`'s `virtual-size` field.
- **qcow2 `compat=0.10`**: the writer always emits `compat=1.1`.
  qemu-img honours both.
- **qcow2 `compression_type=zstd`**: the writer records `zlib` in the
  header regardless. qemu-img switches over to the zstd cluster
  encoder when zstd is requested. The cluster data itself is empty in
  a fresh image so the field-only divergence has no functional
  impact, but `qemu-img info` reports the discrepancy.
- **vhdx default `block_size`**: at virtual sizes ≤ 1 GiB instar
  defaults to 8 MiB; qemu-img always defaults to 32 MiB. Pass
  `-o block_size=...` (or `--block-size`) explicitly to match.
  Explicit block sizes round-trip cleanly.
- **vpc / vhdx differencing children**: instar can create a
  differencing VHD or VHDX (`-b` with `-f vpc` / `-f vhdx`); qemu-img
  refuses to create either ("Backing file not supported for file
  format 'vpc'" / `'vhdx'`), so this is an instar-only capability with
  no qemu-img oracle to compare against. See the "VHD/VHDX
  differencing" section of [quirks.md](/components/instar/quirks/).

All `-o refcount_bits=` widths (1/2/4/8/16/32/64) are now written
correctly: `build_header` derives `refcount_order` from `refcount_bits`
and sub-byte widths pack LSB-first, so the produced files round-trip
through `instar check` and match `qemu-img` (instar #365).

## Future work

- VHD CHS-geometry round-trip matching (close the `virtual_size`
  divergence).
- qcow2 `compat=0.10` honouring.
- zstd-aware qcow2 create (drop the accept-ignore; emit the zstd
  header bit).
- vhdx default `block_size` matching qemu's 32 MiB at all virtual
  sizes.
- Preallocation for vmdk / vpc / vhdx (each format needs its own
  BAT-population pattern plus the same host `apply_preallocation`
  post-pass that qcow2 already uses).
- Multi-file VMDK subformats (`monolithicFlat`, `twoGbMaxExtentSparse`,
  `twoGbMaxExtentFlat`) — needs multi-output-device support in the
  call table.
- VHDX `subformat=fixed` (not emitted by qemu-img either).
- `--object OBJDEF` and `encrypt.*` for LUKS-encrypted create —
  pair with the existing convert-side passphrase plumbing.
- Atomic-rename safety: write to `FILENAME.tmp` and `rename()` on
  guest success, so a guest crash mid-emission doesn't leave a
  partial file.
- Refactor convert to call into `crates/create/` for the metadata
  emission step (master-plan future-work item).

For the per-format metadata layouts implemented today, see
`src/crates/create/src/lib.rs` (the `plan_qcow2` / `plan_vmdk` /
`plan_vhd` / `plan_vhdx` functions). For the divergence-whitelist
applied during JSON comparison, see `tests/helpers/info_json.py`.

## Examples

Create a 1 GiB qcow2 with default options:

```
instar create -f qcow2 disk.qcow2 1G
```

Same but with a 4 KiB cluster size and extended L2:

```
instar create -f qcow2 -o cluster_size=4k,extended_l2=on disk.qcow2 1G
```

Create a vmdk with the stream-optimised subformat:

```
instar create -f vmdk -o subformat=streamOptimized disk.vmdk 1G
```

Create a 16 MiB fixed VHD (footer-only metadata at end of file):

```
instar create -f vpc -o subformat=fixed disk.vhd 16M
```

Create a vhdx with an explicit 32 MiB block size (matches qemu-img's
default and avoids the `vhdx` default-block-size divergence):

```
instar create -f vhdx -o block_size=32M disk.vhdx 1G
```

Create a qcow2 child image with a backing reference (virtual_size
defaults from the parent):

```
instar create -f qcow2 -b parent.qcow2 -F qcow2 child.qcow2
```

Create a differencing VHD child (the parent's identity is read off the
parent and the virtual size defaults from it):

```
instar create -f vpc -b parent.vhd -F vpc child.vhd
```

Create a raw image with `falloc` preallocation (reserves blocks
without writing them):

```
instar create -f raw --preallocation falloc disk.raw 1G
```

JSON output for scripting:

```
instar create -f qcow2 --output json disk.qcow2 1G
```
