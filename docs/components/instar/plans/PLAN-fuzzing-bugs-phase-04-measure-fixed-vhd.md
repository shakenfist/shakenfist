# Phase 4 — measure fixed-VHD source detection (category B1)

Parent plan: [PLAN-fuzzing-bugs.md](/components/instar/plans/PLAN-fuzzing-bugs/)

## Goal

Make `instar measure` recognise fixed-VHD source images the way
`instar info` and `instar check` already do, so the differential
fuzz harness stops recording `exit_code_divergence` on VPC-source
inputs.

**Closes:** #335, #325, #324, #323, #319, #311, #310, #306, #293.

## Planning effort

High. The fix crosses the guest-side measure operation, the
shared format-detection layer, and the VMM's measure dispatch.
Need to:

* Confirm what `detect_format_from_header` does for fixed-VHD
  sector 0 (the comment at
  `src/shared/src/format_detection.rs:138` says "Fixed VHD has
  its signature only at the end, handled separately by caller").
* Read how `instar info` discovers fixed VHDs — there must be a
  trailing-footer read somewhere; reuse the same pattern.
* Decide whether the trailing-sector read happens in the guest
  measure op or in the host before calling the guest. The guest
  op currently only reads sector 0. The 384KB guest cap makes a
  small change in the guest op preferable to a host-side
  pre-classification round trip.

## Investigation

The divergence detail in (e.g.) issue #335:

```
"instar_stderr": "measure: source image is unsupported format\n..."
"qemu_stderr": ""
"instar_rc": 1, "qemu_rc": 0
```

…shows `instar measure` failing with `MEASURE_RESULT_ERROR_INVALID_SIZE`
(produced when `detect_and_scan` returns `None`).
`src/operations/measure/src/main.rs` line 337:

```rust
let format = detect_format_from_header(header, sector_size, false);
```

…only sees sector 0. For a fixed VHD that sector carries the OS
data (e.g. an MBR), not the `conectix` cookie. The match arm
falls through to the default `_ => None`. Result: measure bails
before reaching any of the actual calculators.

`detect_vhd_from_footer` already exists in
`src/shared/src/format_detection.rs` (read it; it's adjacent to
`detect_format_from_header`). `instar info` and `instar check`
call it as a second-pass detection after `detect_format_from_header`
returns `Raw`. `measure` does not — that is the bug.

There is one subtlety: the measure op does not know the input
device's capacity in sectors at the time `detect_and_scan` runs
without an explicit syscall. `input_capacity` is fetched at line
325. The footer lives at `(input_capacity - 1) * sector_size`.
Reading that sector is one extra `read_input_sector` call — well
within the guest binary size budget and within the guest's
runtime budget for a one-off classification.

### Source-format support gate

After this fix, `instar measure` accepts {raw, qcow2, vmdk-sparse,
vhd-dynamic, vhd-fixed, vhdx} as source formats. That matches
the documented support matrix from `PLAN-measure.md` phase 1.
No additional source formats are added — fixed VHD was always
supposed to be supported and was inadvertently dropped because
the detection path was insufficient.

## Implementation

In `src/operations/measure/src/main.rs:detect_and_scan` (line
337 onwards):

1. After `detect_format_from_header` returns `Raw`, perform a
   second-pass detection by reading the trailing sector of
   device 0 (`(input_capacity - 1)` for sector index) and
   passing it to `detect_vhd_from_footer`.
2. If that returns `Vhd`, dispatch into the existing
   `ImageFormat::Vhd` arm (the VHD scanner already handles
   both fixed and dynamic — confirm by reading
   `src/crates/vhd/src/lib.rs`).
3. Otherwise treat as `Raw` (the existing fall-through).
4. Account for the extra sector read in `*bytes_read`.

If the existing `Vhd` arm dispatches only into dynamic-VHD
scanning, extend it to call `vhd::scan_allocation_fixed` (or
equivalent — confirm name in the crate) when the footer reports
a fixed-VHD subformat. Fixed VHDs are fully allocated, so the
scanner can short-circuit by returning
`AllocationSummary { virtual_size, allocated_bytes: virtual_size,
target_units_with_data: virtual_size / target_unit_size }`.

## Verification

1. Reproduce each issue's seed locally:
   ```
   python3 scripts/differential-fuzz.py \
     --instar src/target/release/instar \
     --seed <seed> --iterations <iter+1> --fail-fast
   ```
   Use the seed and iteration count from each issue body. None
   should produce `exit_code_divergence` on the measure op for
   that seed/iter.
2. Confirm `instar measure` against a hand-crafted fixed-VHD
   image (you can generate one with `qemu-img convert -O vpc -o
   subformat=fixed`) produces the same `required size:` /
   `fully allocated size:` output as `qemu-img measure -O qcow2`.
3. Run a focused differential-fuzz campaign against the seeds
   in the closed issues plus a few new random seeds:
   `python3 scripts/differential-fuzz.py --iterations 5000`.
4. `make test-integration` with the measure subset.

## Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 4a | high | opus | worktree | In `src/operations/measure/src/main.rs:detect_and_scan`, add a fixed-VHD footer-detection second pass when `detect_format_from_header` returns `Raw`. Reuse `detect_vhd_from_footer` from `src/shared/src/format_detection.rs`. Read the trailing sector via the call table and account for it in `*bytes_read`. Dispatch into the existing `ImageFormat::Vhd` arm and confirm that arm handles fixed VHDs — extend the `vhd` crate's scan path if it does not. |
| 4b | medium | opus | worktree | Add an integration test using a hand-crafted fixed-VHD image (or a fixture under `tests/fixtures/`). Assert `instar measure -O qcow2 <image>` matches `qemu-img measure -O qcow2 <image>` byte-for-byte. |
| 4c | low | sonnet | none | Re-run the differential-fuzz seeds from the nine issues and confirm no divergence; also run a 5000-iteration fresh campaign. |
| 4d | low | sonnet | none | Close the nine issues with `gh issue close <n> -c "Fixed in <sha>. Root cause: measure did not detect fixed-VHD source images (only the first sector was inspected); see PLAN-fuzzing-bugs-phase-04-measure-fixed-vhd.md."`. |

## Commit shape

One commit for 4a + 4b ("measure: detect fixed VHD source via
trailing footer"). 4c is verification; 4d is housekeeping.
