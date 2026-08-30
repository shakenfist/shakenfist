# `instar dd` — windowed block copy

`instar dd` copies a byte window from a source disk image into a new
output image, behaviourally compatible with upstream `qemu-img dd`.
It reads the input through instar's KVM sandbox (the same backing-chain
reader used by `instar convert`) and writes the selected window to the
output. The copy is **dense** — every byte in the window is read and
written regardless of whether it is zero, unlike `instar convert` which
skips zero blocks by default.

Because the operation runs in a KVM guest it requires `/dev/kvm`
(accessible to root or members of the `kvm` group). All input formats
supported by `instar convert` (raw, qcow2, vmdk, vpc/VHD, vhdx, and
qcow2 backing chains) are also supported as `dd` inputs.

`instar dd` targets **upstream `qemu-img dd`** compatibility only.
PVE/downstream extensions are not implemented; see
[Out of scope](#out-of-scope) below.

## Synopsis

```
instar dd [-f FMT] [-O OUTPUT_FMT] if=INPUT of=OUTPUT [bs=N] [count=N] [skip=N]
```

All positional arguments after the flags are `name=value` operands.
Both `if=` and `of=` are mandatory; omitting either is an error and
instar exits non-zero.

Flags:

```
  -f FMT          Input format hint. Accepted and validated but
                  format forcing is deferred — auto-detection from
                  the file's magic bytes is authoritative. See
                  Known divergences.
  -O OUTPUT_FMT   Output format. When absent, defaults to raw —
                  NOT the input format.
```

Operands:

| Operand | Default | Meaning |
|---------|---------|---------|
| `if=INPUT` | (required) | Input image path. |
| `of=OUTPUT` | (required) | Output image path. |
| `bs=N` | `512` | Block size in bytes. Accepts 1024-based size suffixes (see below). Range: `1..=2147483647` (INT\_MAX). `bs=0` is rejected. |
| `count=N` | (whole image) | Maximum number of blocks to copy. Clamps the copy DOWN only; if `count*bs` exceeds the input virtual size the copy ends at the virtual size. `count=0` produces an empty output. |
| `skip=N` | `0` | Number of blocks to skip at the front of the input window. The input read starts at byte `skip*bs`. Skipping past end-of-file produces an empty output and exits 0 (not an error). |

### Size suffixes for `bs`, `count`, and `skip`

`bs`, `count`, and `skip` are parsed with the same 1024-based suffix
parser used by `qemu-img`:

| Suffix | Multiplier |
|--------|-----------|
| `b` | 512 |
| `k`, `K` | 1024 (KiB) |
| `M`, `m` | 1 048 576 (MiB) |
| `G`, `g` | 1 073 741 824 (GiB) |
| `T`, `t` | 1 099 511 627 776 (TiB) |
| `P`, `p` | 1 125 899 906 842 624 (PiB) |
| `E`, `e` | 1 152 921 504 606 846 976 (EiB) |

No suffix means the value is in bytes.

Tokens without an `=` separator or with an unrecognised key are
rejected:

```
dd: unrecognized operand '<TOKEN>'
```

## Window semantics

The `dd` window is computed from `bs`, `count`, `skip`, and the input's
virtual size using the following rules (parity-verified against
`qemu-img dd`):

```
copy_len = count * bs, clamped DOWN to virtual_size
           (or virtual_size when count is absent)
start    = skip * bs
end      = copy_len
out_vsize = max(0, end - start)
```

Key properties:

- **`count` clamps down only.** If `count*bs` exceeds the input's
  virtual size, `end` is clamped to `virtual_size`. Supplying a huge
  `count` is the standard way to copy "up to the end" from a `skip`
  offset.
- **`count=0` yields empty output.** `end=0`, so `out_vsize=0`.
  Output file is created and exits 0.
- **Skip past EOF yields empty output, not an error.** When
  `skip*bs >= end`, `out_vsize` saturates to 0; instar exits 0
  (same as `qemu-img dd`).
- **Output starts at byte 0.** `skip` removes bytes from the front of
  the input window; the output is always written from the beginning of
  the output file.
- **Overflow saturates.** Arithmetic on huge `count` or `skip` values
  saturates (`u64::MAX`) then clamps; it never wraps or panics.
- **Dense copy.** Every byte in the window is read from the input and
  written to the output. There is no zero-skipping (unlike
  `instar convert`, which uses `SKIP_ZEROS` by default).

The window math lives in `src/crates/dd/src/lib.rs`
(`compute_dd_window`), a `no_std` crate that is unit-tested and fuzz-
tested independently of the VMM binary.

## Output formats and virtual-size rounding

`-O` selects the output format. When `-O` is absent the output is
always **raw**, regardless of the input format. This matches
`qemu-img dd` behaviour.

Supported output formats:

| `-O` flag value | Format | Notes |
|-----------------|--------|-------|
| `raw` (default) | Raw binary | Output file size = `round_up(out_vsize, 512)`. The last partial block is zero-padded to a sector boundary, matching `qemu-img dd`. |
| `qcow2` | QCOW2 | Default driver options (64 KiB cluster size, v3 compat). Virtual size = `round_up(out_vsize, 512)`. |
| `vmdk` | VMware VMDK | Default driver options (64 KiB grain size, monolithicSparse). Virtual size = `round_up(out_vsize, 512)`. |
| `vpc` | VHD/VPC | Virtual size is set via CHS geometry rounding (see below) — always ≥ `round_up(out_vsize, 512)` and potentially much larger. |
| `vhdx` | VHDX | Default driver options (32 MiB block size). Virtual size = `round_up(out_vsize, 512)`. See Known divergences for the block-size difference from `qemu-img`. |

### VHD CHS geometry rounding

VHD (vpc) encodes virtual size as cylinder × head × sector geometry.
`instar dd` uses the same CHS rounding algorithm as `qemu-img dd`
(`chs_rounded_size` in `src/crates/vhd/src/lib.rs`, an exact mirror
of qemu vpc.c's `calculate_rounded_image_size`): the byte window is
rounded up to whole sectors, then qemu's upward search finds the
first sector count whose floor CHS geometry covers the request; that
geometry's product becomes the declared size and the geometry itself
the footer CHS. The resulting virtual size is always ≥ the window
size and is byte- and size-identical to `qemu-img dd`. (An earlier
one-pass ceil approximation diverged from qemu on schedules like a
69632-sector window — differential-fuzz issue #382. And the footer
CHS must be the search geometry itself, not the floor geometry of the
declared size: the two differ when the search's final candidate sits
above a head-count boundary while its product sits below one, e.g. a
104349-sector window declares 104363 sectors = 877×7×17 but floors to
1023×6×17 — coverage-fuzz issue #413.)

Example (verified against `qemu-img dd 10.0.8`):

| `out_vsize` (bytes) | Declared virtual size (bytes) | CHS geometry |
|---------------------|-------------------------------|--------------|
| 512 | 34 816 | C=1 H=4 S=17 |
| 1 000 | 34 816 | C=1 H=4 S=17 |
| 3 000 | 34 816 | C=1 H=4 S=17 |
| 34 817 | 69 632 | C=2 H=4 S=17 |
| 1 048 576 | 1 079 296 | C=31 H=4 S=17 |
| 35 651 584 | 35 686 400 | C=820 H=5 S=17 |

For a 3 000-byte window (`bs=1000 count=3`) the declared virtual size
is **34 816 bytes** — significantly larger than the 3 000-byte payload.
The round-trip data (converted back to raw) is still byte-identical to
`qemu-img dd`.

### Output format parity guarantee

For non-empty windows with supported formats, `instar dd` produces
output that is byte- and size-identical to `qemu-img dd` for raw output
and virtual-size- and data-identical (round-trip-to-raw byte-identical)
for structured formats. This is verified by the integration test suite
(`tests/test_dd.py`, `tests/test_dd_baselines.py`) across qemu-img
versions 6.0.0 through 10.2.0.

## Known divergences from `qemu-img dd`

### vhdx default block size

`instar dd -O vhdx` emits 32 MiB data blocks for all virtual sizes.
`qemu-img dd` uses 8 MiB blocks for small images (below approximately
1 GiB). This is a pre-existing instar vhdx-writer default, the same
divergence documented for `instar create -O vhdx`. The data and declared
virtual size are identical between the two tools; only the `cluster-size`
field in `qemu-img info` output differs (32 MiB vs 8 MiB). This
divergence is recorded in `KNOWN_DD_DIVERGENCES` in
`tests/test_dd_baselines.py` and does not cause a test failure.

### `count=0 -O vmdk`

`qemu-img dd` itself exits non-zero (exit 1) when `count=0 -O vmdk`
because the monolithicSparse VMDK format cannot represent a zero-
capacity disk. `instar dd` exits 0 and produces a file, but that file
is unreadable by `qemu-img info`. Because `qemu-img dd` itself cannot
produce a readable baseline for this case, no parity comparison is
possible; only `instar dd` exit 0 is asserted.

### `count=0 -O vhdx`

`qemu-img dd` exits 0 and produces a readable zero-virtual-size vhdx.
`instar dd` also exits 0 but its empty vhdx is rejected by `qemu-img
info`. No data is lost (both have zero payload); this is a limitation
of the vhdx writer when the virtual size is zero. It is tracked as
future work; see [Future work](#future-work).

### `-f` input format flag

`-f FMT` is accepted by the CLI and validated, but format forcing is
deferred to a future release. Auto-detection from the file's magic bytes
is authoritative. Passing `-f raw` on a raw input does not cause an
error and the output matches `qemu-img dd` (which uses auto-detection),
but the hint itself is currently ignored.

### Error message text

Error message text is instar-native (e.g.
`dd: 'if=' is required`, `dd: unrecognized operand 'foo=1'`,
`dd: invalid bs: 0 (must be 1..=2147483647)`). The messages are
different from `qemu-img dd`'s messages, but exit codes and observable
output sizes are parity-identical.

## Out of scope

`instar dd` targets upstream (QEMU) `qemu-img dd` only. The following
operands and flags from PVE/downstream `qemu-img` forks and other
extended variants are **not implemented** and not planned for v1:

- `osize=` / `isize=` (Proxmox extensions)
- `seek=` (output offset)
- stdin/stdout (`-` as `if=` or `of=`)
- `-n` (no-overwrite)
- `-l` (list)
- `--target-image-opts` / `-o` (output driver create options)
- `--image-opts` / `--object` / `-U` (QEMU object and unlock flags)

These are listed here so the boundary is unambiguous. If you need one
of these please open a GitHub issue.

## Examples

Copy a whole qcow2 image to raw (the default output format):

```
instar dd if=disk.qcow2 of=disk.raw
```

Copy a whole raw image to qcow2:

```
instar dd -O qcow2 if=disk.raw of=disk.qcow2
```

Copy the first 512 MiB of a large image to a new raw file:

```
instar dd if=big.qcow2 of=first512m.raw bs=1M count=512
```

Skip the first 128 MiB and copy the next 256 MiB (bs=64K, skip=2048,
count=4096):

```
instar dd if=disk.raw of=window.raw bs=65536 skip=2048 count=4096
```

Copy a windowed slice to qcow2 (note: VHD CHS rounding will expand
the declared virtual size for very small windows):

```
instar dd -O qcow2 if=disk.raw of=slice.qcow2 bs=65536 skip=2 count=4
```

Read from a non-raw format (auto-detected), write raw:

```
instar dd if=disk.vmdk of=disk.raw
```

Read from a qcow2 backing chain (overlay resolves against base
transparently), write raw:

```
instar dd if=overlay.qcow2 of=flat.raw
```

Whole-image conversion to VHD (note: virtual size is CHS-rounded):

```
instar dd -O vpc if=disk.raw of=disk.vpc
```

Produce an empty output (zero-byte payload, exits 0):

```
instar dd if=disk.raw of=empty.raw bs=512 count=0
```

## Future work

- **`-f` input format forcing.** The `-f` flag is accepted but the
  hint is currently ignored; auto-detection is authoritative.
  Wiring the hint through `discover_backing_chain` is a small
  follow-up.
- **`--image-opts` / `--object` / `-U`.** The QEMU driver-option,
  object, and unlock flags are not implemented. These depend on
  QEMU's `--image-opts` URL syntax and are low-priority for instar's
  sandboxed use case.
- **PVE/downstream extensions.** `osize=`, `isize=`, `seek=`,
  stdin/stdout, `-n`, `-l`, `--target-image-opts`, and `-o` (driver
  create options) are out of scope for upstream parity.
- **Readable empty vhdx (`count=0 -O vhdx`).** instar's zero-virtual-
  size vhdx is currently rejected by `qemu-img info`. The vhdx writer
  needs to produce a header/metadata structure that qemu-img accepts
  even when there is no payload data.
- **`seek=` output offset.** Allows writing into the middle of an
  existing output file. Not in upstream `qemu-img dd`; add only if a
  concrete downstream need appears.

For the window math crate, see
[`src/crates/dd/src/lib.rs`](https://github.com/shakenfist/instar/blob/develop/src/crates/dd/src/lib.rs).
For the VHD CHS rounding function, see
[`src/crates/vhd/src/lib.rs`](https://github.com/shakenfist/instar/blob/develop/src/crates/vhd/src/lib.rs)
(`chs_rounded_size`).
For the divergence allowlist, see `KNOWN_DD_DIVERGENCES` at the top of
[`tests/test_dd_baselines.py`](https://github.com/shakenfist/instar/blob/develop/tests/test_dd_baselines.py).
