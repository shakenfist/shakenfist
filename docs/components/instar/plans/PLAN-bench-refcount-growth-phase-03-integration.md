# bench refcount growth — phase 03: integration tests

Parent: [PLAN-bench-refcount-growth.md](/components/instar/plans/PLAN-bench-refcount-growth/).
Planned at medium effort. All changes in `tests/test_bench.py`.

## Scope

1. **Flip the divergence regression test to a parity test.**
   `TestBenchDivergenceRegression::test_qcow2_write_refblock_coverage_still_diverges`
   currently asserts instar refuses while qemu succeeds (issue
   #397's exact vector). Replace it with a parity test in the
   write-path test family: run the same argv on twin images
   (instar vs qemu-img), assert both exit 0, `qemu-img compare`
   identical, `qemu-img check` clean on the instar copy.
   Remove the `qcow2-write-refblock-coverage` entry from
   `KNOWN_BENCH_DIVERGENCES` and fix the registry-count
   docstrings ("entries 2-7, 9-12" reverts to 2-7, 9-11).
2. **Growth matrix.** New test class covering, at minimum:
   * 4M / cluster 512, empty, sequential allocating schedule
     (refblock growth, no RT relocation);
   * 16M / cluster 512, empty, schedule spanning > 8 MiB of
     allocation (RT relocation: needed slots > 64);
   * 64M / cluster 512, prepopulated 2M, large allocating
     schedule (RT relocation + mixed overwrite/allocate);
   * 64M / cluster 4096, empty, > 8 MiB allocation (single
     refblock outrun at 4 KiB clusters);
   * 16M / cluster 65536, allocating schedule (no-growth fast
     path — assert success and check-clean; this guards the v1
     path).
   Every case: instar rc 0; `qemu-img check --output=json`
   clean (corruptions/leaks/check-errors all 0); `qemu-img
   compare` equal against a twin image run through `qemu-img
   bench` with identical argv; and a follow-up
   `qemu-io -c 'write ...'`/`qemu-img check` round-trip on the
   instar copy proving qemu can keep using the grown image
   (mirrors the planning probe).
   Use the existing fixture helpers (`make_qcow2` with
   cluster_size, `_qemu_io` for prepopulation) and the
   established `run_instar_bench` / `run_qemu_bench` pattern;
   KVM guard via `_require_kvm`.
3. **Flush-interval interaction.** One growth case with
   `--flush-interval` set (interval < count) to exercise the
   deferred old-RT decrement riding an interval flush rather
   than only the run-end flush.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | default (Fable) | none | Implement the Scope in tests/test_bench.py following the file's existing families and style (single quotes, 120-col, docstring conventions). Compute each schedule's expected growth by hand in comments (slots before/after, RT relocation or not) so the matrix rows are auditable. Run the suite via `tests/.venv/bin/python3 -m unittest test_bench` from tests/ and paste the summary. |

## Review checklist deltas

Standard checklist, plus: run the full bench suite and the
registry-linkage assertions (each KNOWN_BENCH_DIVERGENCES entry
still has a matching test and vice versa).
