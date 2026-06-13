# Phase 3 — Category C: qcow2 shrink sub-byte refcount corruption

Master plan: [PLAN-bug-fixes.md](/components/instar/plans/PLAN-bug-fixes/)

Closes: #365.

Planning effort: **high**. Requires guest shrink-path
root-causing, qcow2 refcount-format interpretation against qemu's
`block/qcow2-refcount.c`, and a fix-vs-gate design decision.

## Background

Two separate defects were involved in #365:

1. **Already fixed** (commit `f3d2a49`): the shared sub-byte
   refcount accessors (`snapshot::qcow2::read/set_refcount_in_block`,
   lifted from `resize::qcow2::set_refcount`) packed 1/2/4-bit
   entries MSB-first; qemu's `get/set_refcount_ro0/ro1/ro2` are
   LSB-first. A byte-exact layout test now pins qemu's ordering.

2. **Remaining (this phase)**: the corruption reproduces
   identically after `f3d2a49`, so the resize shrink path has a
   **second, independent width assumption**. `plan_shrink`
   (`src/crates/resize/src/qcow2.rs`) computes `entries_per_refblock`
   correctly from `refcount_bits`, so the suspect is elsewhere in
   the shrink refcount staging/rebuild — possibly a path that
   writes refblock entries at a hardcoded 16-bit stride, or
   refcount-table regeneration math. The garbage values
   `qemu-img check` reports (e.g. `0x3F00`, `0x1111`) look like
   multi-bit writes landing in sub-byte refblocks.

## Reproduction (from the issue)

```
for rb in 1 2 4 16; do
  qemu-img create -f qcow2 -o refcount_bits=$rb t$rb.qcow2 16M
  qemu-io -f qcow2 -c 'write -P 7 0 64k' -c 'write -P 9 12M 128k' t$rb.qcow2
  instar resize --shrink t$rb.qcow2 8M; echo "rb=$rb rc=$?"
  qemu-img check t$rb.qcow2
done
```

Expected after fix: `rb=1/2/4` either pass `qemu-img check` cleanly
(if root-caused-and-fixed) or exit non-zero with a clear "sub-byte
refcount widths unsupported for shrink" message (if gated). `rb=16`
must remain clean and exit 0.

## Approach — bounded investigation, then decide

This phase is exploratory. Run it as a **sub-agent in a worktree**
(`isolation: "worktree"`) so an unsatisfactory attempt is cheap to
discard.

### Investigation (root-cause attempt)

Trace the shrink refcount path end to end for a `refcount_bits != 16`
image. Read the guest shrink op and the planner's refcount
staging/rebuild in `src/crates/resize/src/qcow2.rs`. Specifically
look for:

* any refblock-entry read or write that assumes a 16-bit (2-byte)
  entry stride — e.g. indexing `refblock[i * 2]`, `u16`-typed
  refcount reads/writes, `* 2` or `>> 1` byte-offset math, or a
  `REFCOUNT_BYTES = 2` style constant — rather than dispatching on
  `refcount_bits` through the (now-correct) `read/set_refcount_in_block`
  accessors that `f3d2a49` fixed;
* refcount-table regeneration that computes refblock **count** or
  **size** from a fixed 16-bit assumption;
* whether the shrink path even routes its refcount writes through
  the shared sub-byte accessors at all, or has its own inlined
  copy that was never updated.

A useful differential: the snapshot crate and the grow path may
handle sub-byte widths correctly (snapshot mutating modes refuse
them; grow may stage them correctly). Diffing how grow vs. shrink
stage refblock entries is likely to isolate the divergence quickly.

### Decision gate

* **If the second assumption is cleanly isolable** (a localised
  stride/width bug), fix it so the shrink routes all refblock
  access through the width-aware accessors, and make the shell
  reproduction `qemu-img check`-clean for all four widths.
* **If it is not cleanly isolable** within the investigation
  budget, fall back to **gating**: refuse `refcount_bits != 16` for
  `resize --shrink` with a clear error and a non-zero exit,
  matching the posture the snapshot mutating modes already take.
  A loud refusal is strictly better than silent exit-0 corruption.
  Record the deferral in the master plan's *Future work*.

Surface the decision (and the evidence behind it) to the management
session before committing — do not silently pick the gate to save
effort.

### Fuzz coverage (lands with this phase either way)

The differential resize fuzzer's `op_resize` picker never overrides
`refcount_order`, which is why this escaped. Add a `refcount_bits`
dimension to its image generation so this class is covered going
forward. If the decision is "gate", the fuzzer must treat the
refusal as a known/expected divergence (instar errors where qemu
succeeds) rather than filing it.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | worktree | Root-cause the second width assumption in the qcow2 shrink refcount path. Reproduce #365 with the shell loop above. Trace the shrink refcount staging/rebuild in `src/crates/resize/src/qcow2.rs` and the guest shrink op; find where a sub-byte (`refcount_bits` 1/2/4) image gets refblock entries written at a hardcoded 16-bit stride or sized from a 16-bit assumption, instead of dispatching on `refcount_bits` via the `read/set_refcount_in_block` accessors fixed in `f3d2a49`. Cross-reference qemu `block/qcow2-refcount.c`. Report the exact site(s) and a recommended fix-vs-gate decision with evidence back to the management session before changing code. |
| 3b | high | opus | worktree | Apply the chosen remedy. If fixing: route all shrink refblock access through the width-aware accessors so the shell reproduction is `qemu-img check`-clean for `refcount_bits` 1/2/4/16. If gating: reject `refcount_bits != 16` in the `resize --shrink` planner with a clear error and non-zero exit, mirroring the snapshot mutating modes' refusal. Add a regression test (the shell reproduction as an integration test, or a unit test on the planner). |
| 3c | medium | sonnet | none | Add a `refcount_bits` dimension to the differential resize fuzzer's image generation (`op_resize` picker / image setup in the differential-fuzz harness) so sub-byte widths are exercised. If Phase 3b chose to gate, register the gated refusal as a known/expected divergence so the harness does not file it. |

## Verification

- [x] The #365 shell reproduction: `create` and `resize --shrink`
      across `refcount_bits` 1/2/4/16 are all `qemu-img check`-clean
      (the root-cause fix resolved sub-byte create as well).
- [x] `make instar` builds, `make lint` clean.
- [x] `make check-binary-sizes` passes.
- [x] `make test-rust` and the full `make test-integration` suite pass
      (the previously-skipped rb-1/rb-8/rb-64 create+resize cases now
      run live against qemu).
- [x] Differential fuzzer with the new `refcount_bits` dimension ran
      250 iterations with 0 divergences.
- [x] `pre-commit run --all-files` passes.

## Commit

One commit (or two if the fuzzer dimension is logically separate
from the fix/gate). Body should state which remedy was chosen and
why, reference `f3d2a49` as the first half of the fix, and:

```
Closes #365
```
