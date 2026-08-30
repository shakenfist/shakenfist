# `instar bitmap` — manage qcow2 persistent dirty bitmaps

`instar bitmap` creates, deletes, clears, enables, disables, and
merges the **persistent dirty bitmaps** stored inside a qcow2 image.
It is the sandboxed equivalent of `qemu-img bitmap` for the surface it
supports.

A persistent dirty bitmap tracks which regions of the disk have
changed since the bitmap was created, at a fixed *granularity* (bytes
per tracked bit). Bitmaps are the foundation of incremental backup:
a backup tool creates a bitmap, lets the guest run, and later reads
back only the regions the bitmap marks dirty. The bitmap lives in the
qcow2 file itself (in the *dirty bitmaps* header extension), so it
survives across restarts — hence "persistent".

Persistent dirty bitmaps are a **qcow2 v3** feature; a v2
(`compat=0.10`) image cannot store them and is rejected. Like
`qemu-img bitmap`, `instar bitmap` is **silent on success** — it
prints nothing and exits `0`; errors go to stderr with a non-zero
exit. Every mutation is applied by the `bitmap.bin` guest running in
the KVM sandbox, so the operation requires `/dev/kvm` (accessible to
root or members of the `kvm` group).

## Synopsis

```
instar bitmap (--add | --remove | --clear | --enable | --disable | --merge SOURCE)... [-g GRANULARITY] [-f FMT] [--output {human,json}] FILENAME BITMAP
```

Options:

```
      --add                 Create a new (enabled, empty) bitmap.
      --remove              Delete a bitmap and free its clusters.
      --clear               Reset a bitmap's bits to zero.
      --enable              Set the bitmap's auto flag (record).
      --disable             Clear the bitmap's auto flag (stop).
      --merge <SOURCE>      OR a source bitmap's set bits into the
                            target. SOURCE is a bitmap NAME in the
                            same image (same-file merge only).
  -g, --granularity <BYTES> Granularity for --add (accepts suffixes,
                            e.g. 64k). Only valid with --add.
  -f, --format <FMT>        Force format detection. qcow2 only.
      --output <FORMAT>     human (default, silent) | json
```

`FILENAME` is the qcow2 image; `BITMAP` is the target bitmap name.
The action flags are **repeatable** and are applied in **command-line
order**: `instar bitmap --add --disable disk.qcow2 backup0` creates the
bitmap and then disables it in a single invocation. The full flag
surface is reported by `instar bitmap --help`.

The `--object`, `--image-opts`, `-b`/`--source-file`, and
`-F`/`--source-format` flags are accepted by the parser but **rejected
at runtime** as not-yet-supported (see Refusals and errors).

## Actions

Actions are applied left-to-right in the order they appear on the
command line. A single invocation may combine several metadata
actions, but a `--merge` must be the **sole** action in its invocation.

| Action | What it does | Notes |
|---|---|---|
| `--add` | Creates a new, **enabled**, empty bitmap. | The bitmap's `auto` flag is set (enabled). An empty bitmap has a zero-filled table and allocates no data clusters. Use `-g` to choose the granularity. |
| `--remove` | Deletes the bitmap and frees its clusters. | Removing the last bitmap in the image also drops the dirty-bitmaps header extension. |
| `--clear` | Resets every bit to zero; the bitmap remains. | Data clusters are freed back to the empty (zero-table) representation. |
| `--enable` | Sets the `auto` flag (recording resumes). | |
| `--disable` | Clears the `auto` flag (recording stops). | `--add --disable` creates a bitmap that starts disabled. |
| `--merge SOURCE` | ORs `SOURCE`'s set bits into the target. | **Same-file only**: `SOURCE` is a bitmap *name* in the same image. Source and target must have **equal granularity**. Merging a bitmap into itself is allowed. Must be the only action in the invocation. |

Because actions run in order, `--add --disable` and
`--enable --clear` behave exactly as written. Repeating a flag repeats
the action.

## Output format

By default `instar bitmap` is **silent on success**, matching
`qemu-img bitmap`: on success it prints nothing and exits `0`. Errors
are written to stderr and the process exits non-zero.

With `--output json` it prints a small numeric envelope:

```json
{
  "actions-applied": 1,
  "resulting-bitmaps": 1
}
```

| Field | Meaning |
|---|---|
| `actions-applied` | How many actions the guest applied in this invocation. |
| `resulting-bitmaps` | The number of persistent bitmaps in the image afterwards. |

`instar info` does **not** list bitmaps; to inspect them, use
`qemu-img info --output=json` and read the
`format-specific.data.bitmaps` array (see Known divergences).

## Granularity and names

The bitmap **granularity** is the number of disk bytes tracked by one
bit. For `--add` it defaults to the qcow2 default,
`min(65536, max(4096, cluster_size))` — i.e. the cluster size clamped
into the 4 KiB … 64 KiB range. Override it with `-g`.

A granularity must be a **power of two** in the range **512 B … 2 GiB**
(internally, `log2(granularity)` in `[9, 31]`). `-g` is only valid
together with `--add`; supplying it with any other action set is
rejected.

Bitmap names (both the target `BITMAP` and each `--merge` source) must
be **1 … 1023 bytes** (`BME_MAX_NAME_SIZE = 1023`).

## Refusals and errors

Host-side validation runs before any VM launch; the remaining checks
are enforced by the guest and mapped to a message. The user-facing
string is prefixed with `bitmap:` (guest errors) or `instar bitmap:`
in transcripts. Messages below are verbatim.

Host-side (rejected before the guest runs):

| Condition | Message | Cause |
|---|---|---|
| Not a qcow2 image | `not a qcow2 image` | The file's magic bytes are not qcow2 (host probe). |
| No actions given | `Need at least one of --add, --remove, --clear, --enable, --disable, or --merge` | No action flag was supplied. |
| `-g` without `--add` | `granularity only supported with --add` | `-g` given with an action set that does not include `--add`. |
| Bad granularity | `granularity must be a power of two between 512 and 2G` | `-g` value is zero, not a power of two, or outside `[512, 2G]`. |
| Merge mixed with other actions | `mixing --merge with other actions in one invocation is not supported` | `--merge` combined with any non-merge action. |
| Too many actions | `too many actions (max 8)` | More than `MAX_BITMAP_ACTIONS` actions in one invocation. |
| Too many merge sources | `too many --merge sources (max 8)` | More than `MAX_MERGE_SOURCES` `--merge` sources. |
| Bad target name length | `invalid bitmap name length` | `BITMAP` is empty or longer than 1023 bytes. |
| Bad source name length | `invalid merge source name length` | A `--merge` source name is empty or longer than 1023 bytes. |
| Merge names too large | `merge source names exceed the pool budget` | The combined `--merge` source names exceed the pool budget. |
| `--object` unsupported | `--object is not yet supported` | `--object` was given. |
| `--image-opts` unsupported | `--image-opts is not yet supported` | `--image-opts` was given. |
| Cross-file merge unsupported | `cross-file merge (-b/-F) is not yet supported` | `-b`/`--source-file` or `-F`/`--source-format` was given. |

Guest-side (mapped from the structured result code):

| Condition | Message | Cause |
|---|---|---|
| Unsupported format | `not a qcow2 image` | The guest re-check rejects the format. |
| qcow2 v2 image | `cannot store dirty bitmaps in a qcow2 v2 image` | Persistent bitmaps require qcow2 v3 (`compat=1.1`). |
| Header parse failure | `the image header could not be parsed` | The header bytes are not a valid qcow2 header. |
| Header changed / retry | `the image's header changed between the host's pre-probe and the guest's read, or a guest write failed; retry, or run` `` `instar check` `` `if the image may be corrupt` | Transient consistency problem; retry. |
| Bitmap not found | `bitmap not found` | The named target bitmap does not exist (for `--remove`/`--clear`/`--enable`/`--disable`/`--merge`). |
| Bitmap already exists | `bitmap already exists` | `--add` of a name that already exists. |
| Bitmap in use | `bitmap is in use / inconsistent (only --remove is allowed)` | The bitmap is marked in-use/inconsistent; only `--remove` is permitted. |
| Name too long | `bitmap name is too long` | Guest-side name-length rejection. |
| Granularity out of range | `granularity is out of range (must be a power of two between 512 and 2G)` | Guest-side granularity rejection. |
| Too many bitmaps | `too many bitmaps in the image` | The image already holds the maximum number of bitmaps. |
| No space | `no free clusters to allocate the bitmap` | No free clusters to allocate the bitmap's table/data. |
| Write error | `I/O error writing the image` | A write to the image failed. |
| Read error | `I/O error reading the image` | A read from the image failed. |
| Scratch too small | `the image is too large for the bitmap scratch buffer` | The image geometry exceeds the guest scratch budget. |
| Internal overflow | `internal size or offset computation overflowed (host or guest bug)` | Arithmetic overflow; report as a bug. |
| Merge source not found | `merge source bitmap not found` | The `--merge` source name does not exist in the image. |
| Unsupported action | `unsupported bitmap action` | An unknown action opcode reached the guest. |
| Unsupported refcount width | `unsupported refcount width (only 16-bit refcounts are supported)` | The image uses `refcount_bits != 16`. |
| Incompatible merge | `bitmaps have incompatible granularity for merge` | The `--merge` source and target have different granularities. |

## Known divergences from `qemu-img bitmap`

In every case below instar is deliberately **more conservative** than
`qemu-img` — it refuses an operation qemu accepts. Each is recorded in
the `KNOWN_BITMAP_DIVERGENCES` registry in
[`tests/test_bitmap.py`](https://github.com/shakenfist/instar/blob/develop/tests/test_bitmap.py) (and the differential
allowlist `KNOWN_BITMAP_DIFFERENTIAL_DIVERGENCES` in
`scripts/differential-fuzz.py`), so a cross-validation mismatch that is
*not* registered there is treated as a real regression.

- **Mixed `--merge` + metadata actions in one invocation.** qemu
  applies `--merge` and metadata actions in command-line order within a
  single invocation; instar v1 requires a merge to be the **sole**
  action and refuses with `mixing --merge with other actions in one
  invocation is not supported` (registry key `merge_mixed`). Run the
  merge in its own invocation.
- **Cross-file merge (`--merge -b SOURCE_FILE`).** qemu can merge a
  bitmap from a *different* file; instar v1 is **same-file merge only**
  and refuses `-b`/`-F` with `cross-file merge (-b/-F) is not yet
  supported` (registry key `cross_file_merge`).
- **Non-16-bit refcount images.** qemu supports other refcount widths;
  instar refuses with `unsupported refcount width (only 16-bit
  refcounts are supported)` (registry key `refcount_bits`).
- **Cross-granularity merge.** qemu rescales a source bitmap to the
  target's granularity and accepts the merge; instar requires **equal
  granularity** and refuses with `bitmaps have incompatible granularity
  for merge` (registry key `merge_granularity`).
- **`instar info` does not list bitmaps.** The read side is not yet
  implemented: `instar info` reports no bitmaps array. To inspect an
  image's bitmaps, use `qemu-img info --output=json` and read
  `format-specific.data.bitmaps`.
- **Interleaved repeats of the *same* action flag may not preserve CLI
  order.** The metadata action flags (`--add`, `--remove`, `--clear`,
  `--enable`, `--disable`) use clap's `ArgAction::Count`, which collapses
  all repeats of a given flag to a single command-line position.
  Consequently a sequence that interleaves repeats of one flag around a
  differently-spelled flag — e.g. `--add --remove --add` — is
  reconstructed by relative flag position rather than exact
  per-occurrence order (it becomes `add, add, remove`). Because every
  action in an invocation targets the **same** bitmap name, such
  interleaving is degenerate and rarely meaningful; non-interleaved
  orderings (each flag appearing once, or repeats kept contiguous) are
  preserved and match `qemu-img`. This is a reconstruction limitation,
  not a refusal, so it is not in the refusal registry. Empirically
  verified against clap 4.6.1 (see the `clap_count_indices_tests`
  behaviour-pinning unit tests in `src/vmm/src/main.rs`).

## Interaction with other subcommands

`instar resize` **refuses** any qcow2 image that carries persistent
dirty bitmaps, because resizing would otherwise discard them. The
refusal message is:

```
refusing to resize an image with persistent dirty bitmaps (would
discard them); remove the bitmaps first with `instar bitmap
--remove` or qemu-img
```

Remove the bitmaps with `instar bitmap --remove` first (or use
`qemu-img`), then resize. See [Resize](/components/instar/resize/) for details.

## Examples

Add a bitmap (enabled, empty) with the default granularity:

```
instar bitmap --add disk.qcow2 backup0
```

Add a bitmap with an explicit 64 KiB granularity:

```
instar bitmap --add -g 64k disk.qcow2 backup0
```

Create a bitmap that starts disabled (ordered actions in one call):

```
instar bitmap --add --disable disk.qcow2 backup0
```

Remove a bitmap (and free its clusters):

```
instar bitmap --remove disk.qcow2 backup0
```

Clear a bitmap's bits without deleting it:

```
instar bitmap --clear disk.qcow2 backup0
```

Disable, then later re-enable recording:

```
instar bitmap --disable disk.qcow2 backup0
instar bitmap --enable  disk.qcow2 backup0
```

Merge one bitmap into another in the same image (equal granularity):

```
instar bitmap --merge incremental0 disk.qcow2 full0
```

JSON output for scripting:

```
$ instar bitmap --output json --add disk.qcow2 backup0
{
  "actions-applied": 1,
  "resulting-bitmaps": 1
}
```

Inspect the resulting bitmaps with qemu-img (instar info lists none):

```
qemu-img info --output=json disk.qcow2 | jq '."format-specific".data.bitmaps'
```

Refused — the bitmap already exists:

```
$ instar bitmap --add disk.qcow2 backup0
instar bitmap: bitmap already exists
```

Refused — a v2 image cannot store bitmaps:

```
$ instar bitmap --add old-v2.qcow2 backup0
instar bitmap: cannot store dirty bitmaps in a qcow2 v2 image
```

Refused — mixing a merge with a metadata action:

```
$ instar bitmap --merge src0 --disable disk.qcow2 dst0
instar bitmap: mixing --merge with other actions in one invocation is not supported
```

## Future work

- **Cross-file merge (`-b` / `-F`).** Merge a bitmap from a separate
  source file, matching `qemu-img bitmap --merge -b SOURCE_FILE`. The
  guest ABI has no source-file channel yet.
- **Cross-granularity merge.** Rescale a source bitmap to the target's
  granularity during a merge, as qemu does, instead of refusing
  `bitmaps have incompatible granularity for merge`.
- **Prune the empty EXT_BITMAPS record on last removal.** When the
  final bitmap is removed, the guest overwrites the bitmaps extension
  body in place to `(nb=0, size=0, offset=0)` rather than deleting the
  32-byte record and restoring `EXT_END`. This is inert — `write_back`
  leaves the autoclear bitmaps bit clear on last removal, so qemu
  treats the present-but-autoclear-clear extension as absent per the
  qcow2 spec, and the body is rewritten in place so nothing accumulates
  across add/remove cycles. Deleting the record cleanly would require
  relocating any following header-extension records, unlike the
  append-only create path, so it is left as accepted behaviour.
- **`instar info` bitmap listing.** Report the image's persistent
  bitmaps (name, granularity, flags, count) in `instar info` output so
  `qemu-img info` is no longer required to inspect them.

For the bitmap planner crate, see
[`src/crates/bitmap/`](https://github.com/shakenfist/instar/tree/develop/src/crates/bitmap). For the guest
operation, see [`src/operations/bitmap/`](https://github.com/shakenfist/instar/tree/develop/src/operations/bitmap).
For the divergence whitelist, see `KNOWN_BITMAP_DIVERGENCES` at the top
of [`tests/test_bitmap.py`](https://github.com/shakenfist/instar/blob/develop/tests/test_bitmap.py).
