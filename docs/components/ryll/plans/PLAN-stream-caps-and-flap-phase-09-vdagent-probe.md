# Phase 9 — Vdagent responsiveness probe

Phase 9 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Goal

The spice in-guest agent has no diagnostic message types of
its own — clipboard, mouse state, monitors config, file
transfer, audio volume, GraphicsDeviceInfo, but nothing that
exposes "is the agent healthy?". The reference implementations
(spice-gtk, spice-html5) don't probe agent liveness either.

That said, two of the client → agent messages we already send
(`VD_AGENT_MONITORS_CONFIG`, Linux + Windows agents; and
`VD_AGENT_DISPLAY_CONFIG`, Windows only) are acknowledged by
`VD_AGENT_REPLY { uint32 type, uint32 error }` where `type`
echoes the request opcode. That's enough to derive a
"how fresh was the last agent response?" metric.

Surface that metric in `MainSnapshot` so a bug-report reader
can distinguish "the guest agent is wedged" from "the guest
agent never connected" from "the agent is responsive but the
guest application using it is wedged" — three failure modes
that look identical from the channel-state level today.

## Why this is non-zero value

The session-001 and session-005 bundles both contained cases
where `guest_agent_connected: true` but the operator's actual
guest-side application (Firefox, the test player) felt
unresponsive. With no liveness probe on the agent itself we
couldn't tell whether the agent thread was OK and the guest
application was the bottleneck, or whether the agent was wedged
and the guest application was waiting on it. One probe field
in the bug report makes the distinction visible.

## Scope

In scope:

- Add a `VD_AGENT_REPLY` arm in
  `MainChannel::handle_agent_message` (currently absent —
  REPLY messages silently fall through). Decode the
  `{ type, error }` pair.
- Track per-request send timestamps in a small map keyed by
  the request opcode (`VD_AGENT_MONITORS_CONFIG`,
  `VD_AGENT_DISPLAY_CONFIG`). Compute reply lag on match.
- Six new `MainSnapshot` fields (see *Snapshot additions*
  below) with serialisation tests.
- An idle probe: every N seconds when the agent is connected
  and no monitors-config send has happened, re-send the
  current monitors config. The guest treats an identical
  config as a no-op so this is safe; if it doesn't, that's
  itself a diagnostic signal.
- Optional notification: if `outstanding_agent_request_count`
  stays > 0 for more than 5 s after a probe send, push a
  `NotifySeverity::Warn` notification once per 60 s cool-down.

Out of scope:

- Extending the protocol. Vdagent has no diagnostic message
  type and we are not in a position to add one.
- Probing `VD_AGENT_DISPLAY_CONFIG` (Windows only). Could be
  added under the same plumbing if we ever need it; not
  measured value today.
- Tracking lag per-request-id (vdagent's REPLY doesn't carry
  a request id, only a request type — so two MONITORS_CONFIG
  sends back-to-back can't be correlated individually).
  Workaround: outstanding_agent_request_count is a count
  rather than a tracking map; lag is measured against the
  most recent send.
- New UI beyond the optional notification — operators read
  the fields from a bug report, not a live status widget.

## Background — vdagent reply contract

From `/srv/src-reference/spice/spice-protocol/spice/vd_agent.h`:

```c
typedef struct SPICE_ATTR_PACKED VDAgentReply {
    uint32_t type;   // echoes the request opcode
    uint32_t error;  // VD_AGENT_SUCCESS = 0, anything else = failure
} VDAgentReply;
```

Senders that receive a REPLY:

- `VD_AGENT_MONITORS_CONFIG` (Linux + Windows agents) — the
  one we send today on resize and at session start.
- `VD_AGENT_DISPLAY_CONFIG` (Windows only) — we don't send
  this today.

No request id — REPLYs correlate by type only. If the operator
resizes twice in quick succession, the two reply lags can't be
distinguished. Acceptable: the failure mode we care about
(agent wedged) shows as no REPLY at all, not as ambiguous
attribution.

## Snapshot additions

On `MainSnapshot`:

```rust
/// Number of vdagent-mediated requests sent that expect a
/// REPLY (today: VD_AGENT_MONITORS_CONFIG). Cumulative.
pub agent_request_count: u32,

/// Number of VD_AGENT_REPLY messages received. Cumulative.
pub agent_reply_count: u32,

/// Number of REPLY messages with non-zero `error` field
/// (anything other than VD_AGENT_SUCCESS).
pub agent_reply_error_count: u32,

/// Session-relative seconds at the most recent REPLY receipt.
pub last_agent_reply_ts_secs: Option<f64>,

/// Microseconds between the most recent request send and its
/// matching REPLY (matched by request type, see Background).
/// None until the first REPLY arrives.
pub last_agent_reply_lag_us: Option<u32>,

/// Bounded ring of recent reply lags for min/max/mean
/// computation. Cap = 16 (same shape as
/// `mjpeg_decode_recent_*`).
pub recent_agent_reply_lag_us: VecDeque<u32>,

/// Number of REPLY-eligible requests we have sent without
/// seeing a matching REPLY yet. Increments on send,
/// decrements on REPLY receipt (down to zero, not below).
/// Persistently > 0 means the agent is wedged.
pub outstanding_agent_request_count: u32,
```

All fields default to zero / None / empty; only populated when
the agent is connected and we have actually sent a probe.

## Step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9A | medium | sonnet | none | **Reply parser + per-request bookkeeping.** Add a `VD_AGENT_REPLY` arm in `MainChannel::handle_agent_message` (`shakenfist-spice-renderer/src/channels/main_channel.rs:1192+`) that decodes the 8-byte payload as `{ type: u32, error: u32 }` (LE). Add a small in-channel field — `agent_request_send_ts: HashMap<u32, Instant>` keyed by request type — that records the send time in `send_agent_data_message` whenever the type is one of the REPLY-eligible request opcodes (`VD_AGENT_MONITORS_CONFIG` for now; document the list with a comment so adding more is a one-line change). On REPLY receipt, look up the send time by the echoed `type`, compute `Instant::now() - sent` as microseconds, push into the recent-ring (cap 16, drop oldest), update `last_agent_reply_lag_us` / `last_agent_reply_ts_secs`, increment `agent_reply_count` (and `agent_reply_error_count` if `error != 0`), decrement `outstanding_agent_request_count` (saturating; clamp at zero). Increment `agent_request_count` and `outstanding_agent_request_count` in `send_agent_data_message` on the send side, gated on the request type being REPLY-eligible. Add the six new `MainSnapshot` fields; populate in the existing `update_snapshot` path. Extend `test_main_snapshot_serialises` (or equivalent) to set non-zero values and assert the new fields appear in the JSON output. Verify `make build && make test && make lint && pre-commit run --all-files`. **Why medium:** the bookkeeping is mechanical but spans send-side and recv-side state in the same channel; getting the saturating-decrement edge cases right matters (REPLY arriving with no matching send, REPLY arriving after a counter wraparound, etc.). Reference: see how phase 1 wired STREAM_REPORT counters in `display.rs` for the per-channel-state-plus-snapshot-write pattern. |
| 9B | low | haiku | none | **Idle probe re-send.** In `MainChannel`, add a tokio interval that fires every `VDAGENT_PROBE_INTERVAL` (default 30 s) when the agent is connected (`self.guest_caps_received == true`) and no monitors-config send has happened in the same interval. Re-send the most recent monitors config payload via `send_monitors_config` (which already exists at line ~1155; if it requires arguments, cache the last-sent config locally on the channel so the probe doesn't have to recompute it). The guest treats an identical config as a no-op so this is safe. Document the constant near where it's defined: "Probe-cadence: 30 s is chosen so the snapshot ring (cap 16) covers 8 minutes of agent history in a bug report — long enough to characterise an intermittent stall without burning bandwidth on a working agent." Verify `make build && make test && make lint && pre-commit run --all-files`. **No new test needed** — the probe is exercised end-to-end by 9D's smoke. |
| 9C | low | haiku | none | **Stuck-agent notification.** Add a tokio interval inside `MainChannel` that polls `outstanding_agent_request_count` every 5 s; if it has been > 0 continuously for more than 5 s since the last probe send, push a `NotifySeverity::Warn` notification via the existing notification path with `NotificationSource::Internal` and message "Guest agent is not replying — last probe sent {Ms} ago, no REPLY received". Cool-down 60 s — store `last_stuck_notification_ts: Option<Instant>` on the channel and only push if the previous notify is None or older than 60 s. Reuse the cool-down pattern from phase 8's flap notification (`streaming_state.rs::NotificationToFire`). Verify `make build && make test && make lint && pre-commit run --all-files`. |
| 9D | low | haiku | none | **Docs touch-up.** Update `docs/troubleshooting.md` with a "Guest agent diagnostics" subsection that explains each new MainSnapshot field, the probe cadence (30 s), the stuck-agent notification (5 s window, 60 s cool-down), and how to read the values from a `channel-state.json`. Add a one-sentence note in `ARCHITECTURE.md`'s main-channel section noting agent reply-lag tracking lives on `MainSnapshot`. Verify `pre-commit run --all-files`. |
| 9E | — | — | — | **Operator smoke.** One ryll session against any test instance. After session bring-up, wait one probe interval (~30 s), file a bug report, confirm `main.agent_request_count > 0`, `main.agent_reply_count > 0`, `main.last_agent_reply_lag_us` is small (typically <10 ms). Then deliberately freeze the guest (e.g. `kill -STOP <gnome-shell-pid>`) and wait 30 s; check that the Warn notification fires once. Unfreeze and confirm the next REPLY drops `outstanding_agent_request_count` back to zero. Operator-driven, not a sub-agent task. |

## Open questions

- **Q1: probe cadence.** 30 s is the proposed default — long
  enough to not burn bandwidth, short enough that 16 samples
  cover 8 minutes (matching the bug-report capture window).
  Reconsider if early operator data shows agent replies are
  variable on the order of tens of ms vs hundreds — the
  cadence should be at least 10x the typical reply lag to
  avoid the ring being dominated by transient stalls. Decide
  in 9B; default 30 s.
- **Q2: notification window threshold.** 5 s "agent
  outstanding" before warning is conservative — most healthy
  replies arrive in well under 100 ms. If the operator finds
  it noisy in real use we can raise to 10 s. Decide post-9E.

## Success criteria

- `make build && make test && make lint && pre-commit run --all-files` clean.
- `MainSnapshot` has the six new agent-probe fields,
  populated in `update_snapshot`, visible in a bug-report
  `channel-state.json`.
- A session against a healthy agent shows
  `agent_request_count == agent_reply_count` (or off by at
  most one in-flight) and `last_agent_reply_lag_us` < 100 ms.
- A deliberately-frozen agent (via `kill -STOP`) reproduces
  the stuck-agent Warn notification once within 35 s of the
  freeze, and once unfrozen the notification does not
  re-fire within the 60 s cool-down even though the reply
  arrives.
- `docs/troubleshooting.md` describes how to interpret the
  new fields.

## Cross-references

- Master plan section "Phase 9 — Vdagent responsiveness probe"
  for the original intent.
- `/srv/src-reference/spice/spice-protocol/spice/vd_agent.h`
  for `VDAgentReply` layout and the list of REPLY-eligible
  request types.
- Phase 1 (`completed/PLAN-stream-caps-and-flap-phase-01-stream-report.md`)
  for the "per-channel state + snapshot write" pattern.
- Phase 8 (`PLAN-stream-caps-and-flap-phase-08-streaming-indicator.md`)
  for the cool-down notification pattern (the streaming flap
  notification uses an equivalent 60 s cool-down).
