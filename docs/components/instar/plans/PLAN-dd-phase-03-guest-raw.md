# PLAN-dd phase 03: Guest windowing for raw output

Master plan: [PLAN-dd.md](/components/instar/plans/PLAN-dd/)
Previous phase: [PLAN-dd-phase-02-host-operands.md](/components/instar/plans/PLAN-dd-phase-02-host-operands/)

## Status: Complete (4375324 impl, 814197e tests)

> **Implementation note (correcting the Design below).** The Design
> section assumed `read_chain_virtual_cluster` reads arbitrary
> unaligned offsets. That was **wrong**: `read_raw_sectors` (and the
> multi-sector branch of `read_cluster_sectors`) floored the start to
> a 512-byte sector and dropped sub-sector tails, so a window whose
> start/end was not 512-aligned (only possible when `bs` is not a
> multiple of 512) read the wrong bytes. Per the operator's choice of
> full arbitrary-`bs` parity, this was fixed at the **root cause**:
> both primitives now read an arbitrary sub-sector byte range
> (covering boundary sectors via a scratch, exact-byte copy), with the
> sector-aligned fast path byte-identical so other readers are
> unaffected. `convert_to_raw` uses a carry scheme (write whole output
> sectors per flush, carry the sub-sector remainder) and the host sizes
> dd raw output to `round_up(out_vsize, 512)` (qemu-img dd's rule,
> verified empirically). Result: windowed `dd -O raw` is byte- AND
> size-identical to `qemu-img dd` for any `bs`. **For phase 4:** the
> read primitives are already byte-accurate, so the structured writers
> only need the read-loop windowing + output-metadata/size handling,
> not another reader fix.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Flag any uncertainty
explicitly rather than guessing.

## Mission

Make the guest honour the dd input window for **raw output**. After
this phase, `instar dd` with `skip=`/`count=`/non-default `bs=` is
byte-identical to `qemu-img dd` for `-O raw` (the default), across
the full window matrix — including sub-sector-aligned windows,
short final blocks, and the empty-output (`count=0` / skip-past-EOF)
cases.

Today the guest's `convert_to_raw` copies `[0, virtual_size)` and
ignores `FLAG_DD_WINDOW` and the `window_start`/`window_end` fields
the host already writes. This phase teaches it to copy
`[window_start, window_end)` to output offset 0, and fixes the
host so dense raw output is truncated to the exact `out_vsize`.

Non-raw formats (`-O qcow2|vmdk|vpc|vhdx`) still ignore the window
after this phase — they are phase 4. So windowed dd to a non-raw
format remains incorrect until then; this is the documented
intermediate state (see Caveat).

## Design

### The change is small because the hard parts are already handled

Grounding established two facts that make this phase a focused edit
rather than a rewrite:

1. **`read_chain_virtual_cluster`** (`src/crates/qcow2/src/lib.rs`
   ≈ 5044) reads an **arbitrary byte count from an arbitrary byte
   offset** — it computes `intra_offset = virtual_offset %
   cluster_size` and `read_cluster_sectors` copies sub-sector
   ranges. So the guest may read directly from an unaligned
   `window_start` (e.g. `skip=1 bs=512` ⇒ 512); no floor-alignment
   or head-shift gymnastics are needed.
2. **Output addressing stays sector-aligned automatically.**
   `convert_to_raw` accumulates a fixed `output_sector_size` per
   flush. If the loop starts at `window_start` and the output
   sector index is computed as `(accum_start - window_start) /
   output_sector_size`, then because each flush advances
   `accum_start` by exactly `output_sector_size` (small-cluster
   branch) or by `chunk_size` which is a power-of-two multiple of
   `output_sector_size` (large-cluster branch), `(accum_start -
   window_start)` is always a whole multiple of
   `output_sector_size`. The output sectors come out 0, 1, 2, …
   with no sub-sector misalignment, regardless of how unaligned
   `window_start` is.

### Guest edit: `convert_to_raw` (`src/operations/convert/src/main.rs` ≈ 1465)

Introduce a read window `(read_start, read_end)`:

- The caller (the convert `main` dispatch, where the config is
  read) computes `let (read_start, read_end) = if
  config.has_dd_window() { (config.window_start, config.window_end) }
  else { (0, virtual_size) };` and passes both into
  `convert_to_raw`. For normal convert this is `(0, virtual_size)`
  — identical to today.
- In `convert_to_raw`, replace the loop's use of `0` and
  `virtual_size`:
  - `let mut virtual_offset: u64 = read_start;`
  - `while virtual_offset < read_end {`
  - `let remaining = read_end - virtual_offset;` (chunk clamp)
  - `let should_flush = accum_bytes >= output_sector_size as u64 ||
    virtual_offset >= read_end;`
  - `let total_chunks = (read_end - read_start + chunk_size - 1) /
    chunk_size;` (progress denominator; guard `read_end >
    read_start` to avoid div-by-zero — see empty case below)
  - **Output addressing:** `let output_first_sector = (accum_start
    - read_start) / output_sector_size as u64;`
- Everything else (zero-skip check — inactive for dense dd — the
  partial-final-sector padding, the `output_capacity` break, the
  per-sector write loop) is unchanged. `accum_start` is still set
  to `virtual_offset` on the first chunk of an accumulation, so for
  the first flush `accum_start == read_start` ⇒ output sector 0.

`bytes_read` / `send_complete` need no change: nothing depends on
bytes-copied equalling `virtual_size` (grounding confirmed it is
informational), and for a window it will naturally equal
`read_end - read_start`.

**Empty window** (`read_start >= read_end`, i.e. `count=0` or skip
past the count-clamped end): the `while virtual_offset < read_end`
loop body never runs, nothing is written, `send_complete(.., true)`
fires. The host created/sizes the output to `out_vsize == 0`.
Result: empty output, success — matching `qemu-img dd`. Make sure
the `total_chunks` computation does not divide by zero when
`read_end == read_start` (e.g. compute it as
`(read_end.saturating_sub(read_start) + chunk_size - 1) /
chunk_size` and only use it when non-zero, or skip progress when
the window is empty).

### Shared edit: `has_dd_window()` helper (`src/shared/src/lib.rs`)

Add to the `impl ConvertConfig` accessors (next to
`should_skip_zeros` etc.):

```
pub fn has_dd_window(&self) -> bool {
    (self.flags & Self::FLAG_DD_WINDOW) != 0
}
```

`window_start` / `window_end` are already public fields readable as
`config.window_start` / `config.window_end`.

### Host edit: truncate dense raw output to `out_vsize` (`src/vmm/src/main.rs`, in `execute_convert`)

This is essential and easy to miss. The existing raw-output
truncation (≈ 9596) is gated on `skip_zeros`:

```
if skip_zeros && !is_structured_output && flat_extent_path.is_none() {
    ... f.set_len(out_vsize)? ...
}
```

dd is **dense** (`skip_zeros == false`), so this branch is skipped
today. For a windowed copy whose `out_vsize` is not a multiple of
`output_sector_size`, the guest writes whole padded sectors and the
output file ends up **larger than `out_vsize`**, which fails the
byte-for-byte comparison against `qemu-img dd`. Fix: also truncate
raw output to `out_vsize` for dd. Broaden the condition so the
`set_len(out_vsize)` fires when `exec.window.is_some()` (dd) for the
non-structured / non-flat raw path, in addition to the existing
`skip_zeros` case. Verify (via the phase tests) that the output
device `output_capacity` rounding (`compute_output_capacity(raw,
out_vsize)`) still permits writing the full final (padded) sector
before truncation — i.e. capacity is rounded **up** to a sector so
the last partial block is not clamped away.

### Intermediate-state caveat

After this phase, only `-O raw` honours the window. Windowed dd to
`qcow2`/`vmdk`/`vpc`/`vhdx` still copies the whole image (those
writers are phase 4). Do not add non-raw windowed integration tests
here; whole-image non-raw dd is not meaningfully exercised until
phase 4 either. Add a brief code comment near the dispatch noting
that only `convert_to_raw` honours `read_start`/`read_end` as of
phase 3.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Implement the raw-window feature end-to-end across three files. (1) `src/shared/src/lib.rs`: add `has_dd_window()` to `impl ConvertConfig`. (2) `src/operations/convert/src/main.rs`: in the dispatch where the config is read, compute `(read_start, read_end) = if config.has_dd_window() { (config.window_start, config.window_end) } else { (0, virtual_size) }` and thread both into `convert_to_raw`; change `convert_to_raw` to loop over `[read_start, read_end)` and address output sectors as `(accum_start - read_start) / output_sector_size` (full details in the Design section, including the empty-window / div-by-zero guard). Leave the other `convert_to_*` writers untouched (phase 4) and add a one-line comment that only raw honours the window. (3) `src/vmm/src/main.rs` `execute_convert`: also truncate raw output to `out_vsize` when `exec.window.is_some()` (dense dd), not only when `skip_zeros`. GUEST CODE NOTE: convert.bin is `no_std`, near the 384KB cap, and codegen-sensitive — keep the change minimal (no new heap, no large stack arrays); run `make check-binary-sizes`. Verify: `make instar`, `make lint`, `make test-rust`; then MANUAL windowed cross-checks against `qemu-img dd` for (a) `skip=1 bs=512` (unaligned window), (b) `count=N` smaller than the image, (c) `count` beyond EOF, (d) `count=0` (empty), (e) `skip` past EOF (empty, exit 0), (f) a size where the window end is not sector-aligned (short final block) — each `cmp`-identical to `qemu-img dd if=.. of=.. bs=..`. Report all results. Do NOT touch convert's existing whole-image behaviour (the convert integration suite must stay green — run it). |
| 3b | medium | sonnet | none | Extend `tests/test_dd.py` (reuse the phase-2 `run_instar_dd`/`run_qemu_img_dd` helpers) with the raw window matrix, each asserting instar output byte-identical to `qemu-img dd` (pass the SAME `bs`/`count`/`skip` to both): `skip` aligned (e.g. `bs=65536 skip=1`); `skip` UNALIGNED (`bs=512 skip=1`); `count` smaller than image (`bs=65536 count=8`); `count` beyond EOF; `count=0` ⇒ empty output (assert size 0, exit 0); `skip` past EOF ⇒ empty output, exit 0; `skip`+`count` together; a short-final-block case (window end not a multiple of 65536); size suffixes (`bs=1M count=2`). Inputs: small qcow2 and raw, with written patterns. Confirm via `make test-integration` (run `^test_dd\.`); also run `^test_convert\.` as a regression check. |

Per the master plan / `PLAN-TEMPLATE.md`, sub-agents implement and
the management session reviews the actual files (especially the
`convert_to_raw` loop edit and the host truncation condition)
before committing. Suggested commits: 3a the raw-window feature
(shared + guest + host, one logical change); 3b the integration
matrix.

## Verification

- [ ] `convert` whole-image behaviour unchanged: convert
      integration suite green; a convert round-trip byte-identical
      to before.
- [ ] Windowed `instar dd -O raw` byte-identical to `qemu-img dd`
      for: unaligned skip, aligned skip, count<image, count>EOF,
      count=0 (empty), skip past EOF (empty, exit 0), skip+count,
      short final block, size suffixes.
- [ ] Empty-output cases produce a 0-byte file and exit 0.
- [ ] `make instar` builds; `make lint` clean; `make test-rust`
      passes; `make check-binary-sizes` passes (convert.bin still
      under cap after the edit).
- [ ] `pre-commit run --all-files` passes.
- [ ] Only `src/shared/src/lib.rs`,
      `src/operations/convert/src/main.rs`,
      `src/vmm/src/main.rs`, and `tests/` changed.
- [ ] Commit messages follow conventions (model/context/effort in
      `Co-Authored-By`).

## Hand-off to phase 4

Phase 4 ([PLAN-dd-phase-04-guest-formats.md](/components/instar/plans/PLAN-dd-phase-04-guest-formats/),
to be written) applies the same `[read_start, read_end)` windowing
to the structured writers — `convert_to_qcow2`(`_compressed`),
`convert_to_vmdk`(`_compressed`), `convert_to_vhd`,
`convert_to_vhdx` (`src/operations/convert/src/main.rs` ≈ 2073,
2520, 2967, 3258, 3677, 4105). They share the same
`read_chain_virtual_cluster` read loop, so the read-side windowing
mirrors phase 3; the extra work is ensuring their output metadata
(L1/L2, grain tables, BATs) and output virtual size derive from
`out_vsize`, not the input virtual size, and that the host output
sizing already done for raw extends correctly to each format. Phase
4 then adds the format-output integration tests.
