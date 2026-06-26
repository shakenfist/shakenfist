# PLAN-dd phase 04: Guest windowing for structured output formats

Master plan: [PLAN-dd.md](/components/instar/plans/PLAN-dd/)
Previous phase: [PLAN-dd-phase-03-guest-raw.md](/components/instar/plans/PLAN-dd-phase-03-guest-raw/)

## Status: Complete (d10e5b1 impl, c3cd396 tests)

> **Outcome.** All six structured writers honour the window;
> `instar dd -O qcow2|vmdk|vpc|vhdx` matches `qemu-img dd` on data
> and declared virtual size (round-trip-to-raw verified for aligned,
> non-512-end, and sub-sector windows on raw + qcow2 inputs).
> Per-format declared sizes: qcow2/vmdk/vhdx `round_up(out_vsize,
> 512)`; VHD CHS-rounded via the new `vhd::chs_rounded_size` (the
> Design's assumption that instar's existing geometry helper matched
> qemu was **wrong** — it floors; fixed at root). dd uses a 64 KB
> device sector size for structured output (the VHD/VHDX block path
> assumes `oss == MAX_SECTOR_SIZE`) and 512 for raw.
> **Known limitation:** `count=0 -O vhdx` (empty window) produces a
> VHDX that `qemu-img info` rejects (no data, exits 0); qemu's empty
> VHDX is readable. The empty vmdk case matches qemu (qemu's own
> `count=0 -O vmdk` exits 1). Tracked in the master plan's Future
> work and for phase-9 differential fuzzing.

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

Make the guest honour the dd input window for the **structured
output formats** — qcow2, vmdk, vhd (vpc), vhdx, and the qcow2 /
vmdk compressed variants — so `instar dd -O <fmt>` with
`skip=`/`count=`/any `bs=` produces a windowed output image whose
**data content and declared virtual size match `qemu-img dd
-O <fmt>`**. After this phase, all of dd's output formats honour
the window; raw was done in phase 3.

Structured output is **not** byte-comparable to qemu's file (cluster/
grain/block allocation differs even for convert), so parity here
means: (a) the output's declared **virtual size** equals qemu's,
and (b) reading the output **back to raw** yields the same bytes as
reading qemu's output back to raw — exactly how the existing
convert tests validate structured output (`assert_size_roundtrip`
in `tests/base.py`).

## Design

### The read primitives are already byte-accurate

Phase 3 fixed `read_raw_sectors` / `read_cluster_sectors` to read
arbitrary sub-sector byte ranges. So this phase needs **no reader
changes** — only the per-writer windowing and virtual-size sizing.

### Windowing pattern (mirror phase 3, identical across all six writers)

The six writers live in `src/operations/convert/src/main.rs`:
`convert_to_qcow2`, `convert_to_qcow2_compressed`,
`convert_to_vmdk`, `convert_to_vmdk_compressed`, `convert_to_vhd`,
`convert_to_vhdx`. Each currently takes a `virtual_size` parameter,
iterates output clusters/grains/blocks over `[0, virtual_size)`,
reads input at `virtual_offset = index * unit`, and sizes its output
metadata from `virtual_size`. Exploration confirmed all six share
this structure; the compressed variants differ only in per-unit
encoding, so windowing them is identical to their base.

Per writer, mirror what phase 3 did to `convert_to_raw`:
1. **Thread the window in.** Replace the `virtual_size` parameter
   with `read_start: u64` (input byte offset of output offset 0) and
   `out_vsize: u64` (the output's virtual size). The dispatch
   computes `read_start` and `out_vsize = read_end - read_start`
   (from phase 3's `(read_start, read_end)`); for whole-image
   convert it passes `(0, virtual_size)`, so behaviour is unchanged.
2. **Offset reads by `read_start`.** Where the loop computes
   `virtual_offset = index * unit`, use `read_start + index * unit`.
   Reads are byte-accurate, so an unaligned `read_start` is fine.
3. **Size geometry from `out_vsize`** (the declared-virtual-size
   rule below), not the input `virtual_size`.
4. **Zero-pad the final partial unit** (already done by each writer
   for convert's last cluster/grain/block) — matches qemu, which
   zero-fills past the window end.

The `convert_luks_wrapped_qcow2` path (phase 3 passed it
`(0, inner_virtual_size)`) is convert-only; keep it whole-image.

### Declared virtual size — match qemu per format (the careful part)

`out_vsize = window_end - window_start` may not be 512-aligned (only
when `bs` isn't a multiple of 512). Empirically verified against
qemu-img dd 10.0.8 — the **declared** virtual size of qemu's output
per format (for `out_vsize = 3000`):

| Format | Rule | 3000 → | Does instar already do this? |
|--------|------|--------|------------------------------|
| qcow2  | `round_up(out_vsize, 512)` | 3072 | **No** — writes the size exactly (`write_qcow2_header` ≈ writes `virtual_size` verbatim). Must round. |
| vmdk   | `round_up(out_vsize, 512)` | 3072 | **Likely yes** — `capacity_sectors = (vsize+511)/512` already rounds. Verify. |
| vhdx   | `round_up(out_vsize, 512)` | 3072 | **Verify** — metadata writes the size verbatim; check `calculate_bat_layout` / metadata rounding. |
| vhd (vpc) | **CHS geometry rounding** (its own algorithm; substantially larger) | **34816** | **Verify** — instar convert-to-VHD already matches qemu for aligned sizes (convert suite green), so its CHS rounding presumably matches; confirm it produces qemu's size for a windowed `out_vsize`. |

So: each writer must declare the **same** virtual size as qemu for
the windowed `out_vsize`. For qcow2 (and vhdx if it writes verbatim)
this means rounding `out_vsize` up to 512 before it becomes the
header/metadata size and the geometry input. For vmdk the existing
`capacity_sectors` rounding already does it. For vhd, the existing
CHS rounding must produce qemu's CHS size from `out_vsize` — pass
the raw `out_vsize` (NOT pre-rounded to 512) so the CHS algorithm
sees the same input qemu's does. **The implementer must verify each
format's declared virtual size against qemu-img dd directly** (don't
assume — phase 3's grounding was wrong about an analogous detail).

Note the data beyond `out_vsize`: when the declared size exceeds
`out_vsize` (e.g. 3072 vs 3000, or VHD's 34816), the bytes past the
window end must read back as **zero** — the writer reads input only
up to `window_end` and zero-pads the rest, which matches qemu.

### Empty window (out_vsize == 0)

`count=0` / skip-past-end give `out_vsize == 0`. Each writer must
produce a valid 0-virtual-size image of that format (or whatever
qemu-img dd produces for `count=0 -O <fmt>` — verify per format;
qemu may produce a minimal/empty image). Confirm exit 0.

### Host output sizing

`execute_convert` already opens structured output with
`compute_output_capacity(target_format, out_vsize)` (phase 2) and
the structured-output path is `always sparse`. Confirm the capacity
headroom is sufficient when the declared size rounds up; if a writer
declares `round_up(out_vsize, 512)` or VHD's larger CHS size, pass
that rounded size (not raw `out_vsize`) to `compute_output_capacity`
so the backing file is large enough. (The raw-path truncation from
phase 3 does not apply to structured output.)

### Validation method (drives the tests)

Mirror `assert_size_roundtrip` (`tests/base.py` ≈ 818): for each
format, `instar dd <window> -O <fmt>` → assert `qemu-img info`
reports the expected format and the **same virtual size as qemu-img
dd**; convert the instar output back to raw and convert qemu's
output back to raw; assert the two raws are byte-identical. Do NOT
byte-compare the structured files.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | Window all six structured writers in `src/operations/convert/src/main.rs` (`convert_to_qcow2`, `_qcow2_compressed`, `convert_to_vmdk`, `_vmdk_compressed`, `convert_to_vhd`, `convert_to_vhdx`), mirroring phase 3's `convert_to_raw` change. Replace each writer's `virtual_size` param with `read_start`/`out_vsize`; offset reads by `read_start`; size geometry/metadata from `out_vsize`. The dispatch already computes `(read_start, read_end)` (phase 3) — pass `read_start` and `out_vsize = read_end - read_start` to each writer (and `(0, virtual_size)` stays the convert path, so convert is unchanged). CRITICAL: make each writer's DECLARED virtual size match `qemu-img dd -O <fmt>` for a non-512-aligned `out_vsize` — qcow2/vmdk/vhdx use `round_up(out_vsize, 512)`, vhd uses its existing CHS rounding (pass raw `out_vsize`). Add rounding where the writer currently writes the size verbatim (at least qcow2; verify vhdx). Ensure `compute_output_capacity` in `execute_convert` is fed the rounded size so the backing file fits. Keep `no_std`, mind the 384KB cap (`make check-binary-sizes`). VERIFY each format against qemu-img dd by round-trip-to-raw: for `bs=65536 skip=2 count=4`, `bs=1000 count=3` (non-512), `bs=513 skip=1 count=3` (sub-sector), and `count=0` (empty), confirm (a) `qemu-img info` virtual size equals qemu-img dd's and (b) instar-output→raw equals qemu-output→raw byte-for-byte, on a patterned input. Also run the convert integration suite (`^test_convert\.` at concurrency 4) to prove convert is unaffected (must be 0 failed), plus `make test-rust`, `make lint`. Report per-format virtual sizes and round-trip results. |
| 4b | medium | sonnet | none | Add a structured-output window matrix to `tests/test_dd.py`, mirroring `assert_size_roundtrip` (`tests/base.py`). For each of `-O qcow2`, `-O vmdk`, `-O vpc`, `-O vhdx`: run `instar dd <window>` and `qemu-img dd <window>` to that format, assert `qemu-img info` reports the same virtual size for both, and assert instar-output→raw == qemu-output→raw byte-for-byte (use `qemu-img convert -O raw` and a direct byte compare). Windows to cover: `bs=65536 skip=2 count=4`, `bs=1000 count=3` (non-512 end), `bs=513 skip=1 count=3` (sub-sector), `count=0` (empty). Inputs: patterned raw and qcow2. Run `^test_dd\.` (all pass) and `^test_convert\.` at concurrency 4 (0 failed). NOTE: never run convert at the default concurrency 16 — large manifest images exhaust memory/KVM and fail spuriously. |

Per the master plan / `PLAN-TEMPLATE.md`, sub-agents implement and
the management session reviews the actual files (especially the
per-format virtual-size rounding) before committing. Suggested
commits: 4a the six-writer windowing; 4b the structured test matrix.
Because the per-format rounding is subtle, review the declared
virtual sizes against qemu carefully — a passing round-trip on
512-aligned windows does not prove the non-aligned rounding is right.

## Verification

- [ ] `instar dd -O qcow2|vmdk|vpc|vhdx <window>` matches
      `qemu-img dd -O <same>` on: declared virtual size AND
      output→raw byte content, for aligned, non-512-end, sub-sector,
      and empty windows, on raw and qcow2 inputs.
- [ ] Compressed variants (qcow2 `-c`, vmdk `-c`) honour the window
      (round-trip parity).
- [ ] `convert` unchanged: `^test_convert\.` 0 failed (concurrency
      4); a convert round-trip to each format byte-identical to
      before.
- [ ] `make instar`, `make lint`, `make test-rust`,
      `make check-binary-sizes` (convert.bin under cap) all pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] Only `src/operations/convert/src/main.rs`, `src/vmm/src/main.rs`
      (capacity, if needed), and `tests/test_dd.py` changed.
- [ ] Commit messages follow conventions (model/context/effort).

## Hand-off

With phases 3 and 4 done, all `instar dd` output formats honour the
window and match `qemu-img dd`. Remaining master-plan phases: 5 Rust
unit tests (parser/window-math already have some — extend as
needed), 6 integration (consolidate the 14-row matrix), 7
cross-version baselines, 8 coverage fuzz, 9 differential fuzz
(random `bs`/`count`/`skip`/`-O` vs qemu-img dd — the memory
[[dd-qemu-img-parity-contract]] records the verified rules), 10
docs. The phase-9 differential harness is the strongest guard
against any remaining per-format rounding edge cases.
