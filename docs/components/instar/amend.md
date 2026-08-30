# `instar amend` — change qcow2 image options in place

`instar amend` changes a qcow2 image's compatibility version
(`compat=0.10` ↔ qcow2 v2, `compat=1.1` ↔ qcow2 v3) and/or the
`lazy_refcounts` flag, by rewriting only the header cluster in
place. It is the sandboxed equivalent of `qemu-img amend` for
the options it supports, and produces byte-equivalent
`qemu-img info` output for every supported transition across
every qemu-img version 6.0.0 through 10.2.0.

The `amend.bin` guest runs in the KVM sandbox, reading the
existing header cluster, computing the minimal patch set, and
applying the patches via virtio-block. Cluster data and the
refcount tree are never touched. Because the operation runs
in a KVM guest, it requires `/dev/kvm` (accessible to root or
members of the `kvm` group). **qcow2-only** — raw, vmdk, vpc,
and vhdx are refused.

## Synopsis

```
instar amend [OPTIONS] -o KEY=VALUE,... FILENAME
```

Options:

```
  -f, --format <FMT>            Force format detection.
                                qcow2 only; default: auto-detect
                                from the file's magic bytes.
  -o, --options <KEY=VALUE,...> qemu-img-style options.
                                Repeatable; comma-separated.
                                Required: at least one supported
                                key must be given.
  -q, --quiet                   Suppress "Image amended." or
                                "No change." on success. Errors
                                still go to stderr.
      --output <FORMAT>         human (default) | json
```

The full flag surface is reported by `instar amend --help`.

## Supported transitions

`instar amend` v1 recognises exactly two `-o` keys:

| Transition | `-o` key | Allowed when | Gate |
|---|---|---|---|
| v2 → v3 upgrade | `compat=1.1` | image is v2 | Upgrade is always allowed (no blocking features to check). |
| v3 → v2 downgrade | `compat=0.10` | image is v3 | Refused if any v3-only incompatible feature is set (compression, extended L2, external data, dirty, or corrupt) or if `refcount_bits != 16`. |
| Enable lazy refcounts | `lazy_refcounts=on` | image is v3 (or upgrading to v3 in the same invocation) | Refused against a v2 image unless `compat=1.1` is also given. |
| Disable lazy refcounts | `lazy_refcounts=off` | image is v3 | Always allowed on a non-dirty v3 image. Silently applied as part of a v3→v2 downgrade. |
| No-op | either key already matches | any | Returns `No change.` / `"action": "noop"`; the file is not written. |

A dirty or corrupt image (the `DIRTY` or `CORRUPT` incompatible
feature bit is set) is always refused regardless of the
requested transition. Run `instar check` to repair the image
first.

## Output format

Human (default):

```
Image amended.
```

On a no-op (the requested options already match the current
header):

```
No change.
```

`-q` suppresses both lines; errors still go to stderr.

JSON (`--output json`):

```json
{
  "filename": "/tmp/doc2.qcow2",
  "format": "qcow2",
  "action": "amended",
  "compat": "1.1",
  "lazy_refcounts": "on"
}
```

Field meanings:

| Field | Values | Meaning |
|---|---|---|
| `filename` | string | Absolute or relative path as given on the command line. |
| `format` | `"qcow2"` | Image format (always `qcow2` in v1). |
| `action` | `"amended"` or `"noop"` | Whether the header was rewritten. |
| `compat` | `"1.1"` or `"0.10"` | The **resulting** compatibility version. |
| `lazy_refcounts` | `"on"` or `"off"` | The **resulting** lazy-refcounts state. |

## `-o` option semantics

`-o` is the only way to specify changes, matching `qemu-img amend`'s
interface. Options are comma-separated within a single `-o` argument
and the flag may be repeated; both forms are equivalent.

### `compat=VALUE`

| Value | Effect |
|---|---|
| `0.10` | Set qcow2 compatibility version to v2. |
| `1.1` | Set qcow2 compatibility version to v3. |

Any other value is rejected:

```
amend: bad value '<v>' for -o key 'compat' (expected 0.10 or 1.1)
```

### `lazy_refcounts=VALUE`

| Value | Aliases | Effect |
|---|---|---|
| `on` | `true`, `yes` | Set the `lazy_refcounts` compatible-feature bit. |
| `off` | `false`, `no` | Clear the `lazy_refcounts` compatible-feature bit. |

Any other value is rejected:

```
amend: bad value '<v>' for -o key 'lazy_refcounts' (expected on/off)
```

### Constraints

- At least one of `compat` or `lazy_refcounts` must be given. If
  neither is present:

  ```
  amend: no supported -o options given (expected compat= and/or lazy_refcounts=)
  ```

- Unknown keys (including qemu-img amend keys that instar does not
  yet support, such as `refcount_bits`, `data_file`, `backing_file`,
  and `cluster_size`) are rejected:

  ```
  amend: -o key '<key>' is not supported (amend changes compat and lazy_refcounts only)
  ```

- A missing `=` in a key-value piece is rejected:

  ```
  amend: -o option '<piece>' is missing a value (expected KEY=VALUE)
  ```

## Refusals and errors

The table below lists every distinct refusal and error that
`instar amend` can emit, with the verbatim message and its cause.

| Error | Message | Cause |
|---|---|---|
| Unsupported format | `only qcow2 images can be amended` | The file is not a qcow2 image (raw, vmdk, vpc, or vhdx). |
| Downgrade blocked by feature | `cannot downgrade to compat=0.10: image uses a v3-only incompatible feature (compression, extended L2, external data, or is dirty/corrupt)` | A `compat=0.10` downgrade was requested but the image carries one or more v3-only incompatible features (`COMPRESSION`, `EXTENDED_L2`, `EXTERNAL_DATA`, `DIRTY`, or `CORRUPT`). |
| Downgrade blocked by refcount width | `cannot downgrade to compat=0.10: image uses refcount_bits != 16 (v2 supports 16-bit refcounts only; rewriting the refcount tree is out of scope)` | A `compat=0.10` downgrade was requested but the image uses a non-16-bit refcount width. Rewriting the refcount tree is out of scope for v1. |
| Lazy requires v3 | `lazy_refcounts=on requires compat=1.1 (lazy refcounts are a v3-only feature); upgrade with -o compat=1.1 first or in the same invocation` | `lazy_refcounts=on` was requested against a v2 image (or a downgrade to v2) without a simultaneous `compat=1.1`. |
| Dirty image | `the image is marked dirty (another writer may hold it open); run \`instar check\` first` | The image's `DIRTY` incompatible feature bit is set; another process may still hold it open. |
| Extension relocation unsupported | `this compat change would have to relocate a header extension, which is not yet supported; use \`qemu-img amend\`` | The requested version change (v2↔v3) would require moving a header extension in the cluster, which is not yet implemented. See Future work. |
| Header could not be parsed | `the image header could not be parsed` | The header bytes could not be interpreted as a valid qcow2 header; the file may be corrupt. |
| Header mismatch / retry | `the image's header changed between the host's pre-probe and the guest's read, or a guest write failed; retry, or run \`instar check\` if the image may be corrupt` | A transient consistency problem; retry the operation. |
| Scratch too small | `the image is too large for the amend scratch buffer` | The image's cluster size exceeds the guest's scratch budget. |
| I/O write error | `I/O error writing the image header` | A write to the image file failed. |
| Internal overflow | `internal size or offset computation overflowed (host or guest bug)` | An arithmetic overflow in the host or guest; report as a bug. |

## Known divergences from `qemu-img amend`

- **`compat=0.10` downgrade of a zstd-compressed image is
  refused.** `instar amend -o compat=0.10` refuses a v3 image
  that carries the `COMPRESSION` (zstd) incompatible feature
  with:

  ```
  cannot downgrade to compat=0.10: image uses a v3-only
  incompatible feature (compression, extended L2, external
  data, or is dirty/corrupt)
  ```

  `qemu-img` 10.0.8 *accepts* the same downgrade, rewriting
  the `compression_type` field. instar refuses because v2
  cannot represent `compression_type` and v1 does not
  recompress cluster data (zstd→zlib would be required);
  the "refuse rather than guess" posture avoids silently
  producing an image with stale cluster encoding. The
  divergence is recorded in `tests/test_amend.py` under
  `KNOWN_AMEND_DIVERGENCES` and does not cause a test failure.
  Lifting it is deferred to Future work (see below).

## Examples

Upgrade a v2 image to v3:

```
instar amend -o compat=1.1 disk.qcow2
```

Downgrade a v3 image to v2 (refused if blocking features are
present):

```
instar amend -o compat=0.10 disk.qcow2
```

Enable lazy refcounts on a v3 image:

```
instar amend -o lazy_refcounts=on disk.qcow2
```

Disable lazy refcounts:

```
instar amend -o lazy_refcounts=off disk.qcow2
```

Upgrade to v3 and enable lazy refcounts in one invocation:

```
instar amend -o compat=1.1,lazy_refcounts=on disk.qcow2
```

JSON output for scripting:

```
instar amend --output json -o compat=1.1 disk.qcow2
```

Force the format (bypasses auto-detection from magic bytes):

```
instar amend -f qcow2 -o lazy_refcounts=off disk.qcow2
```

Refused downgrade — image carries the `COMPRESSION` incompatible
feature:

```
$ instar amend -o compat=0.10 zstd-disk.qcow2
instar amend: cannot downgrade to compat=0.10: image uses a
v3-only incompatible feature (compression, extended L2,
external data, or is dirty/corrupt)
```

Quiet mode (suppresses the success line; exit code still
signals success or failure):

```
instar amend -q -o compat=1.1 disk.qcow2
```

## Future work

- **`refcount_bits` amend.** Changing the refcount width
  requires rewriting the entire refcount tree and possibly
  growing or shrinking the refcount table. This is a
  substantial structural rewrite, best approached by reusing
  the `src/crates/snapshot/` refcount mutators and the resize
  refcount-table growth helpers.
- **External data file amend** (`data_file`, `data_file_raw`).
  Attaching or detaching an external data file requires
  managing the `INCOMPAT_EXTERNAL_DATA` bit and the `DATA`
  header extension. Out of scope for v1.
- **LUKS / encryption amend.** Changing keyslots or encryption
  parameters. Depends on LUKS plumbing on the convert side.
- **`backing_file` / `backing_fmt` via amend.** qemu-img amend
  can change the backing pointer; instar already owns this
  path via `rebase -u`. Whether to alias or leave it to
  `rebase` is an open question.
- **Non-qcow2 formats.** qemu-img amend is qcow2-centric;
  revisit only if a concrete need appears for vmdk, vpc, or
  vhdx.
- **Header-extension relocation hardening.** v1 refuses any
  v2↔v3 version change that would have to move a header
  extension (`this compat change would have to relocate a
  header extension, which is not yet supported; use
  \`qemu-img amend\``). Lifting this restriction requires
  implementing the extension-relocate path in the amend
  planner and guest.
- **Compressed-image downgrade (zstd→zlib recompression).**
  The known divergence above (refusal of a `compat=0.10`
  downgrade on a zstd-compressed image) could be lifted by
  decompressing and recompressing every cluster from zstd to
  zlib during the downgrade. This is a data-touching operation
  well outside the header-cluster-only amend model and is out
  of scope for v1.

For the amend planner, see
[`src/crates/amend/src/qcow2.rs`](https://github.com/shakenfist/instar/blob/develop/src/crates/amend/src/qcow2.rs).
For the divergence whitelist, see `KNOWN_AMEND_DIVERGENCES` at
the top of
[`tests/test_amend.py`](https://github.com/shakenfist/instar/blob/develop/tests/test_amend.py).
