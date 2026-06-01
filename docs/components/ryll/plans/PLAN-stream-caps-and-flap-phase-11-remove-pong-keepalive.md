# Phase 11 — Remove spurious-PONG keepalive

Phase 11 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Goal

Remove the `send_idle_keepalive()` path in `main_channel.rs`
(added in commit `cfd4a20c` 2026-05-09 as a band-aid for the
K1 main-channel wedge) and its counters / select-arm /
snapshot fields. The K1 root cause was fully fixed in
`370d8ce5` (2026-05-11) by dropping the abandoned temp event
channel, so the band-aid is now redundant.

The keepalive currently fires once every 15 s of idle and
leaves visible `Spice: main:0 (...): invalid net test stage,
ping id 0 test id 0 stage 0` warnings in the qemu log on
every session (visible in every 003-005 bundle).

## Scope

In scope:

- Delete `send_idle_keepalive` (entire function).
- Delete the `KEEPALIVE_IDLE` constant.
- Delete the select-arm in main_channel's task loop that
  fires the keepalive.
- Delete the `client_keepalive_send_count` and
  `last_client_keepalive_send_ts_secs` fields on
  `MainState`/`MainChannel` (whichever owns them).
- Delete the matching fields on `MainSnapshot` and from the
  bug-report serialisation tests.
- Update `MainSnapshot::test_main_snapshot_serialises`
  (or the equivalent) to drop the assertions.

Out of scope:

- Any other main-channel changes. This is a pure deletion.
- Adding a new keepalive on a different mechanism. The K1
  fix means we don't need one.

## Step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 11A | low | sonnet | none | **Delete the keepalive surface.** Find `send_idle_keepalive` and `KEEPALIVE_IDLE` in `shakenfist-spice-renderer/src/channels/main.rs` (the file is grep-able; if the symbol moved, follow). Delete the function, the constant, the select-arm in the channel task that fires it on tick, the two counter fields (`client_keepalive_send_count`, `last_client_keepalive_send_ts_secs`), and the corresponding `MainSnapshot` fields plus their `update_snapshot` writes. Update `test_main_snapshot_serialises` (or equivalent) to drop the assertions on the deleted fields. Verify `make build && make test && make lint && pre-commit run --all-files`. |
| 11B | low | haiku | none | **Doc + cross-reference cleanup.** Grep for any docs that reference `client_keepalive_send_count` or `KEEPALIVE_IDLE` (`docs/troubleshooting.md` if it mentions keepalive interpretation, `ARCHITECTURE.md` if the snapshot schema is documented there). Remove or update. Note in `docs/troubleshooting.md` that `invalid net test stage` warnings in qemu logs from older ryll runs were caused by this and shouldn't appear post-phase-11. Verify `pre-commit run --all-files`. |
| 11C | — | — | — | **Long-idle soak.** Operator runs a ryll session against any test target, leaves it sitting idle for ≥10 minutes (no input, no display activity), confirms main channel does NOT disconnect. The `main: heartbeat T+...` log line should keep firing every second on its own without any keepalive sends. If main disconnects in the soak window, K1 has reproduced and 11A should be reverted; otherwise we're done. |

## Open questions

None. This is straight removal; the only risk is K1 reproducing,
which 11C's soak rules out.

## Inputs-channel `send_idle_keepalive` (discovered during 11A)

Sub-agent execution of 11A surfaced that
`shakenfist-spice-renderer/src/channels/inputs.rs` has its own
`send_idle_keepalive` from commit `f39f2e3b` (session-001 phase
02). It uses a different mechanism (KEY_MODIFIERS restatement,
not a spurious PONG with stage 0) and was added under a
different hypothesis (cross-channel idleness: keep inputs busy
to also keep main alive).

Decision: **keep it.** Rationale:

- Unlike main's PONG-with-stage-0, the inputs restatement does
  NOT leak any visible warning to the server log — KEY_MODIFIERS
  is a legitimate idempotent client→server message.
- The cross-channel-idleness hypothesis from the original
  session-001 phase 02 work was never empirically disproven,
  just deferred. With K1 fixed via the main-channel path, the
  inputs path is now low-cost defense-in-depth (one byte every
  10 s when idle).
- The "fix the root cause, drop the band-aid" principle that
  drove 11A applies less cleanly here because (a) the inputs
  keepalive is non-spurious from a protocol standpoint (it's
  a real idempotent message, not a forged PONG to a non-
  existent PING), and (b) cost is genuinely negligible.

If a future per-channel-timeout investigation shows the inputs
keepalive is also redundant, it can be removed at that point
under its own ticket. No action in this phase.

## 11A: stale doc reference fixed

11A's sub-agent missed `docs/channel-diagnostics-audit.md`'s
description of main-channel diagnostics, which still listed
`client_keepalive_send_count` as a current field. Fixed
post-hoc alongside the inputs-keepalive decision write-up
above; the audit doc now points readers at this plan for the
inputs / main split.

## Success criteria

- `make build && make test && make lint && pre-commit run --all-files` clean.
- `git grep KEEPALIVE_IDLE` and `git grep send_idle_keepalive`
  return zero results after 11A.
- 11C: ≥10 minutes idle, no main-channel disconnect, no
  `invalid net test stage` warnings in the qemu log post-11A.
- Future sessions' qemu logs do not contain
  `invalid net test stage` warnings from `main:0`.
