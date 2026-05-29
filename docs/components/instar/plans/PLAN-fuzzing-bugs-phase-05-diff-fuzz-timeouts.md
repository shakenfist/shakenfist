# Phase 5 — differential-fuzz timeout classification (category B2)

Parent plan: [PLAN-fuzzing-bugs.md](/components/instar/plans/PLAN-fuzzing-bugs/)

## Goal

Stop the differential-fuzz harness from filing
`exit_code_divergence` issues when `qemu-img` times out — those
divergences reflect a known upstream pathology, not an instar
defect.

**Closes:** #336, #334, #315.

## Planning effort

Low. The change is in `scripts/differential-fuzz.py` and is
mechanical once the harness's qemu-runner code is located.

## Investigation

Issue bodies look like:

```
"instar_rc": 0, "qemu_rc": -1,
"qemu_stderr": "TIMEOUT after 30s",
"operation": "resize", "apply_shrink": true,
"options": ["cluster_size=512", "lazy_refcounts=on"]
```

The harness wraps `qemu-img` calls with a 30-second timeout
(grep `scripts/differential-fuzz.py` for `TIMEOUT` /
`subprocess` / `timeout=`). When the timeout fires the harness
sets `qemu_rc = -1` and `qemu_stderr = "TIMEOUT after 30s"`,
then compares exit codes against instar's. Since instar
succeeded (`rc = 0`) and qemu reported `-1`, the divergence
check trips.

The correct classification is **inconclusive**: external tool
unavailable. The harness already has a notion of skipped
operations; it should treat qemu timeouts the same way.

## Implementation

In `scripts/differential-fuzz.py`:

1. Locate the function that runs `qemu-img` and translates a
   `subprocess.TimeoutExpired` into `qemu_rc = -1` /
   `qemu_stderr = "TIMEOUT after 30s"`.
2. After capturing the timeout, instead of recording an
   `exit_code_divergence`, increment an `inconclusive_qemu_timeout`
   counter and skip the divergence check for this iteration.
3. Surface the counter in the run summary so we don't lose
   visibility on how often qemu hangs.
4. Adjust any tests under `scripts/tests/` (if they exist —
   check) that depended on the old behaviour.

The harness already handles `qemu-img not installed` and
similar by skipping — model the timeout case the same way.

## Verification

1. Re-run each seed:
   ```
   python3 scripts/differential-fuzz.py \
     --instar src/target/release/instar \
     --seed 59095591 --iterations 752 --fail-fast
   python3 scripts/differential-fuzz.py \
     --instar src/target/release/instar \
     --seed 804298385 --iterations 196 --fail-fast
   python3 scripts/differential-fuzz.py \
     --instar src/target/release/instar \
     --seed 829673908 --iterations 405 --fail-fast
   ```
   None should report `exit_code_divergence` on the iteration
   listed in the corresponding issue (#336, #334, #315). All
   three should now produce an `inconclusive` record.
2. Confirm the run summary mentions the
   `inconclusive_qemu_timeout` counter and that it is non-zero
   for the runs above.
3. Make sure real exit-code divergences (e.g. the ones from
   category B1, until phase 4 lands) are still reported.

## Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 5a | low | sonnet | none | In `scripts/differential-fuzz.py`, change qemu-img timeout handling: instead of recording the timeout as an `exit_code_divergence`, classify it as `inconclusive_qemu_timeout` (matching the existing skip-style classifications). Surface the counter in the per-run summary. Don't change the timeout value (30s). |
| 5b | low | sonnet | none | Run the three seeds in the Verification section and confirm no divergence is reported. |
| 5c | low | sonnet | none | Close the three issues with `gh issue close <n> -c "Not a bug: qemu-img upstream hangs on this adversarial qcow2 shrink input. Harness updated in <sha> to classify qemu-img timeouts as inconclusive; see PLAN-fuzzing-bugs-phase-05-diff-fuzz-timeouts.md."`. |

## Commit shape

One commit for step 5a ("differential-fuzz: classify qemu-img
timeouts as inconclusive"). 5b is verification, 5c is
housekeeping.
