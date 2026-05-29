# Phase 2 — qcow2 `scan_allocation` invariant break (category A2)

Parent plan: [PLAN-fuzzing-bugs.md](/components/instar/plans/PLAN-fuzzing-bugs/)

## Goal

Make `qcow2::Qcow2State::scan_allocation` honour the invariant
`allocated_bytes <= virtual_size` for all parseable inputs, so
the ten queued reproducers stop tripping the harness assert.

**Closes:** #338, #330, #321, #317, #313, #308, #304, #297, #295, #292
(of which #297, #295, #292 are `autofix-failed`).

## Planning effort

High. The fix touches the qcow2 allocation scanner — a
cross-cutting parser path that is exercised by `measure`,
`info`, and `check`. Need to confirm none of the other call
sites rely on the current (buggy) behaviour, and need to decide
between cap and reject. Read `docs/qcow2/` and the qcow2 spec
sections on L1/L2 entries before settling on a fix shape.

## Investigation

The harness assert (`fuzz_measure_scan.rs:74`):

```rust
assert!(
    s.allocated_bytes <= s.virtual_size,
    "qcow2 allocated {} > virtual {}",
    s.allocated_bytes,
    s.virtual_size
);
```

is fired when `scan_allocation` walks L1/L2 tables that point at
data clusters past the image's declared `virtual_size`. The
qcow2 spec allows this — L2 entries beyond `virtual_size /
cluster_size` are valid on disk but semantically out of bounds.
qemu-img ignores them; instar's scanner counts every allocated
cluster regardless of whether the corresponding guest LBA is
inside `virtual_size`.

The 1119-byte reproducer in #338 is a hand-crafted qcow2 with
extreme cluster sizes and L1/L2 entries that overflow the
declared virtual size; the same shape recurs across the ten
inputs (different byte-level mutations of the same family).

### Per `~/.claude/CLAUDE.md`: prefer correct fix over simple fix

The simple fix is to cap `allocated_bytes` at `virtual_size` at
the end of the scan. The correct fix is to stop counting
clusters whose guest LBA is `>= virtual_size` during the scan
itself, because:

* The `target_units_with_data` field downstream also depends on
  per-cluster guest LBAs.
* Capping at the end masks the underlying bookkeeping bug and
  leaves `target_units_with_data` potentially inconsistent with
  `allocated_bytes`.

So: gate the per-cluster `allocated_bytes += cluster_size`
update on `lba + cluster_size <= virtual_size`, and do the same
for `target_units_with_data`. Keep the harness assert — it
should never fire after this fix.

## Implementation

In `src/crates/qcow2/src/lib.rs` (`scan_allocation` and its
helpers — locate by grepping for `allocated_bytes +=`):

1. Inside the inner L2-entry loop, compute the guest LBA of the
   cluster currently being inspected. (The loop index already
   gives the L2 slot index; combine with L1 index and
   `cluster_bits` to derive the LBA.)
2. Skip allocation accounting for any cluster whose LBA is
   `>= virtual_size`.
3. Apply the same skip to the target-unit accounting (the code
   that increments `target_units_with_data` and operates on
   `target_unit_size`).
4. Leave the parser-level cluster traversal as-is — we want
   coverage to still drive into the out-of-range L2 entries
   so other invariants (e.g. cluster bounds, refcount overlap)
   keep being checked.

Add a unit test that:

* Builds a small qcow2 image (in-memory `Vec<u8>` is fine —
  `src/crates/qcow2/tests/` has examples).
* Populates an L2 entry that points to a cluster past
  `virtual_size`.
* Asserts `scan_allocation(...)` returns
  `allocated_bytes <= virtual_size` and that
  `target_units_with_data * target_unit_size <= virtual_size`
  (the bug-286 invariant the harness also checks on lines
  84-89 of `fuzz_measure_scan.rs`).

Reference the reproducer corpus when shaping the unit test —
the smallest of the ten inputs gives the clearest minimal
case.

## Documentation and quirk status

Skipping out-of-bounds L2 entries is an internal accounting
choice, not an observable divergence from qemu-img: differential
fuzz exercises the same surface qemu-img exposes and has not
flagged a mismatch, which is evidence qemu-img makes the same
choice. So this is **not** a `docs/quirks.md` entry — that file
tracks operator-toggleable divergences via `--ignore-quirks` /
`--unsafe-quirks`, and there is nothing for the operator to
toggle here.

It does warrant two lighter forms of tracking:

1. **Code comment at the skip site.** At the point in
   `scan_allocation` where the LBA-bounds check rejects an L2
   entry, add a short comment explaining that the qcow2 spec
   allows L2 entries past `virtual_size` on disk but they have
   no guest-visible meaning, so they do not contribute to
   allocation accounting. Without this comment a future reader
   will assume it is a bug and "fix" it back.
2. **A paragraph in the qcow2 docs.** Add a short section to
   the appropriate file under `docs/qcow2/` (check the index
   for the parser-behaviour file — likely
   `docs/qcow2/parsing.md` or similar) describing the
   invariant: *"`allocated_bytes` never exceeds `virtual_size`;
   L2 entries past `virtual_size` are parsed but not counted."*
   Cross-link from `fuzz_measure_scan.rs:74` (or from the unit
   test) so the contract is discoverable.

**Contingency — promote to a real quirk if needed.** If
implementation surfaces a number qemu-img counts that instar
does not (i.e. a differential-fuzz `output_divergence` on the
measure op after this phase lands), the choice becomes
operator-visible. In that case, add an entry to
`docs/quirks.md` with a classification (safe vs unsafe — likely
safe, since over-counting allocation has no security impact),
decide whether it falls under `--ignore-quirks` or is always-on,
and update this plan's *Bugs fixed* section in the master plan
to reference the quirk entry. Do not pre-emptively add the
quirk entry — the differential evidence currently points the
other way.

## Verification

1. Re-run each filed reproducer:
   ```
   cd src/fuzz
   cargo fuzz run fuzz_measure_scan artifacts/fuzz_measure_scan/crash-<hash>
   ```
   for each of the ten hashes listed in the issues. None should
   crash.
2. Run a 10-minute campaign:
   `cargo fuzz run fuzz_measure_scan -- -max_total_time=600`.
3. `make test-rust` — in particular the `qcow2` crate tests and
   any `measure` integration tests. Confirm no measure output
   has shifted for known-good images (the cross-version
   baselines in `tests/baselines/measure/` should be unchanged).

## Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 2a | high | opus | worktree | In `src/crates/qcow2/src/lib.rs`, modify `scan_allocation` so that L2 entries pointing to clusters outside `[0, virtual_size)` do not contribute to `allocated_bytes` or `target_units_with_data`. Derive the guest LBA from the L1 / L2 indices + `cluster_bits`. Do not change cluster traversal — only the accounting. Keep the function `no_std`-compatible (the crate is used by guest code under the 384KB cap). |
| 2b | medium | opus | worktree | Add unit tests in the qcow2 crate covering an L2 entry past `virtual_size` and the bug-286 target-unit invariant. Use the smallest of the ten reproducer artefacts as the basis. |
| 2c | low | sonnet | none | Add a short comment at the LBA-bounds skip site in `scan_allocation` explaining that the qcow2 spec allows on-disk L2 entries past `virtual_size` but they carry no guest-visible meaning. Add a paragraph to the qcow2 parsing docs (locate the right file under `docs/qcow2/`) stating the `allocated_bytes <= virtual_size` invariant. |
| 2d | low | sonnet | none | Run differential fuzz against the seeds used by the queued reproducers (and a 5000-iteration fresh campaign) to confirm no `output_divergence` on measure. If a divergence does appear, stop and revisit: the contingency in *Documentation and quirk status* applies — add a `docs/quirks.md` entry and update the master plan. |
| 2e | low | sonnet | none | Verify the ten reproducers pass and run a 10-minute coverage-fuzz campaign. |
| 2f | low | sonnet | none | Close the ten issues with `gh issue close <n> -c "Fixed in <sha>. Root cause: qcow2 scan_allocation counted L2 entries past virtual_size; see PLAN-fuzzing-bugs-phase-02-measure-scan.md."`. |

## Commit shape

One commit for steps 2a + 2b + 2c ("qcow2: skip out-of-bounds
L2 entries in scan_allocation"). The code comment and the docs
paragraph belong with the behaviour change so reviewers see the
contract alongside its enforcement. Steps 2d, 2e, 2f are
verification and housekeeping.
