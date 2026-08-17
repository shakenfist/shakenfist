# Phase 4: Real latency from PING/PONG

Parent plan: [PLAN-idle-cpu-and-latency.md](/components/ryll/plans/PLAN-idle-cpu-and-latency/)

## Goal

Replace the broken keystroke-based latency measurement
with a real signal derived from SPICE PING/PONG.

The current `Latency: Xms` label and sparkline always read
zero because [channels/inputs.rs:347-356](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs#L347-L356)
sets `last_key_time = Instant::now()` then immediately
calls `last_key_time.unwrap().elapsed()` on the very next
line — measuring only the few microseconds between the two
statements.

## Background

Phase 4 is informed by a research memo (in the parent plan,
section "Latency measurement is broken"):

- `SPICE_MSG_PING = 4` (server→client) and
  `SPICE_MSGC_PONG = 3` (client→server) are base-channel
  messages, mandatory since SPICE v1, present on every
  channel, requiring no capability negotiation.
- Payload: `uint32 id` + `uint64 timestamp` (sender's
  monotonic ns).  PONG echoes both fields verbatim.
- The wire direction is asymmetric: there is **no**
  `SPICE_MSGC_PING` and no `SPICE_MSG_PONG`.  Ryll cannot
  originate probes; it can only respond.  spice-gtk uses
  the same pattern.
- Spice-server originates PINGs on a per-channel timer
  (default ~1 s during warmup) using `tcp_nodelay` to
  bypass Nagle and get an accurate first sample.  We
  inherit that signal for free.
- All PING handlers in ryll already exist and parse the
  message + emit a PONG response: [main_channel.rs:389](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/main_channel.rs#L389),
  [display.rs:442](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L442),
  [cursor.rs:292](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/cursor.rs#L292),
  [inputs.rs:310](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs#L310),
  [playback.rs:456](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/playback.rs#L456),
  [usbredir.rs:232](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/usbredir.rs#L232),
  [webdav.rs:278](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/webdav.rs#L278).
- Each handler currently parses the PING but does nothing
  with the timing — it's free signal we throw away.

## What "latency" we can actually compute

Two options:

1. **Inter-PING interval at the client.**  Record the
   wall-clock arrival times of consecutive PINGs.  The
   variance is jitter; the mean is meaningless (driven by
   server's timer, not network RTT).  **Reject** — it
   measures the server's send cadence, not network
   latency.

2. **Server-side RTT inferred from the PING `timestamp`
   field.**  The server stamps the PING with its own
   monotonic time (`spice_get_monotonic_time` on the
   server).  When we PONG it back, the server computes
   `now() - pong.timestamp` to get its measured RTT.  The
   client cannot directly read the same value because the
   server's clock and our clock are different.

   **However**: if we record `Instant::now()` when we
   receive a PING and then again when we *send the next
   message of any kind* on that channel, the gap is the
   client-side processing delay between two server-driven
   events.  That's not RTT either.

   The correct client-side trick: stamp our own monotonic
   time when a PING arrives, and stamp it again when the
   *next* PING arrives.  The difference between those two
   client times, minus the difference between the two
   PINGs' embedded server timestamps, equals the
   round-trip jitter introduced by the network and our
   processing.  Still not absolute RTT — that requires a
   protocol where we send a probe and time the response.

3. **Approximate one-way: client receive cadence vs server
   send cadence.**  The PING's embedded `timestamp` is the
   server's send time (in server-monotonic-ns).  Our
   `Instant::now()` at receive is our wall time.  The
   *delta* between successive `(server_ts, client_ts)`
   pairs gives us network + server-scheduling jitter.
   Useful for the sparkline, comparable across sessions,
   but not a "ping time" number anyone would recognise.

### Pragmatic recommendation

We accept that **without bidirectional probing we cannot
measure absolute RTT**.  Best signal we can produce:

- Display a **PING cadence** (time between consecutive
  PINGs received from the server, on the channel we choose).
  This is what spice-server itself uses internally to size
  its ack window; if the network path slows down or the
  server's send loop blocks, this number grows.
- Plus a **client PONG turnaround**: wall time from
  PING-received to PONG-sent.  This is dominated by the
  ryll receive loop's latency (queueing + scheduler).
- Sum them and report as "Latency: X ms" for the status
  bar.  Document in `--help` and the README that this is
  *not* a true network RTT, it's a SPICE protocol
  responsiveness number.

Per-channel granularity: server pings every channel.  Use
the **main channel** for the headline number.  If main is
too sparse on a given session, fall back to display.

## Constraints and edge cases

- **First PING after connect**: there's no "previous"
  timestamp to diff against.  Skip the first sample.
- **Channel comes and goes**: usbredir/webdav can
  disconnect mid-session.  Handlers run in independent
  tasks; if a channel's task ends, its sampler simply
  stops emitting.  No cleanup needed.
- **Cadence mode**: this plan removes the keystroke-based
  latency emission.  `--cadence` itself stays — it injects
  test keystrokes for unrelated reasons (functional
  smoke).  Just delete the broken `ChannelEvent::Latency`
  emission from `inputs.rs`.
- **Sparkline source**: the `LatencyTracker` already
  exists from the screenshot/HUD plan.  It just receives
  the wrong signal today.  Phase 4 changes the source to
  PING-driven samples without touching the tracker or its
  tests.
- **Event field name**: the `ChannelEvent::Latency`
  variant currently has a single `key_timestamp: f64`
  field, milliseconds-or-seconds depending on who looks
  at it.  Rename to something honest — `sample_ms: f32` —
  and update the consumer in `app.rs`.

## Approach

1. Pick the **main channel** as the headline source.  Do
   not measure on display/cursor/inputs/etc. for v1; that
   keeps the sample stream clean and avoids per-channel
   weighting questions.

2. In `main_channel.rs`'s PING handler:
   - Record `Instant::now()` as `ping_received_at`.
   - If `last_ping_received_at` is `Some`, compute the
     elapsed wall time (`ping_received_at -
     last_ping_received_at`) — this is the inter-PING
     interval at the client.
   - Subtract the server's send-interval from the embedded
     timestamps: `(ping.timestamp -
     last_ping.timestamp).into_seconds()`.  spice-server
     uses nanoseconds.
   - The signed difference is the "extra delay this
     interval introduced".  Add the absolute value to a
     baseline (the server's ping period, typically ~1s) to
     get a "responsiveness ms" number.

   In practice the simplest *useful* and *honest* signal
   is just: **the client-observed inter-PING interval, in
   ms**.  When the network is healthy and the server isn't
   blocked, this hovers near 1000 ms with low variance.
   Spikes mean either the server stalled or the network
   buffered.  That's a latency-shaped metric without
   pretending to be RTT.

   **Decision**: emit `inter-PING client interval, ms` as
   the sample for v1.  Document clearly.  Leave room to
   subtract the server-stamped interval in a follow-up
   once we have a real session to validate the sign and
   units.

3. After computing a sample, the PING handler emits
   `ChannelEvent::Latency { sample_ms: f32 }`.

4. In `app.rs`, the `ChannelEvent::Latency` arm already
   feeds `self.latency.record(...)` and updates
   `self.stats.last_latency`.  Just rename the field.

5. Delete the broken emission in `inputs.rs:347-356`.
   `last_key_time` may also be unused after this — check
   and remove if so (it's referenced in the bug-report
   snapshot at [inputs.rs:551](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs#L551),
   so probably keep the field, just not the latency
   emission).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a   | medium | sonnet | none     | In [ryll/src/channels/mod.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/mod.rs#L88), rename `ChannelEvent::Latency { key_timestamp: f64 }` to `ChannelEvent::Latency { sample_ms: f32 }`.  Update the consumer in [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L643) accordingly: change the destructuring and replace `(key_timestamp * 1000.0) as f32` with the new `sample_ms` directly.  Drop the broken emission in [ryll/src/channels/inputs.rs:347-356](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs#L347-L356) — keep `last_key_time = Some(Instant::now())` (still used by the bug-report snapshot at line ~551) but remove the `event_tx.send(ChannelEvent::Latency { ... })` and the matching `repaint_notify.notify_one()`.  Add a brief comment at the keep-the-field-but-drop-the-event site: "// last_key_time is recorded for the bug-report snapshot only; latency is now measured from server PINGs in main_channel.rs". |
| 4b   | medium | sonnet | none     | In [ryll/src/channels/main_channel.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/main_channel.rs#L389), extend the `PING` handler to track the previous PING's wall-clock arrival (a new `last_ping_at: Option<Instant>` field on `MainChannel`).  When a PING arrives and `last_ping_at.is_some()`, compute `(now - last).as_secs_f64() * 1000.0` as the sample, send `ChannelEvent::Latency { sample_ms }`, and call `self.repaint_notify.notify_one()` (mirroring the pattern from phase 2).  Always update `last_ping_at = Some(now)` regardless.  Skip the first PING (no prior sample to diff).  No need to read the embedded server timestamp — keep v1 simple. |
| 4c   | low    | sonnet | none     | Update [README.md](https://github.com/shakenfist/ryll/blob/develop/README.md) feature bullet for "Latency sparkline" (introduced in the screenshot HUD plan).  Replace the `--cadence` reference with: "Latency sparkline - Bottom stats panel shows client-observed inter-PING interval from the main channel (lower variance is better; spikes indicate network or server stalls)."  Update the docstring on `LatencyTracker` in [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs) too, if it still references keystroke timing. |

## Success criteria for this phase

- Connected ryll session shows real, non-zero ms numbers
  in the `Latency: Xms` label and the sparkline.
- Numbers hover near the server's ping period (~1000 ms
  default) with low variance on a healthy connection.
- `--cadence` mode no longer emits latency events; the
  flag retains its keystroke-injection behaviour.
- `pre-commit run --all-files` and `make test` pass.
- Single commit for steps 4a + 4b combined; separate
  commit for step 4c (docs).

## Future work

- Subtract server-stamped inter-PING interval (from the
  embedded `timestamp` field) to isolate
  network-introduced jitter from server send cadence.
  Requires a real session to validate units and sign.
- Per-channel latency in the bug-report snapshot for
  diagnostic value (display channel latency under load is
  a different signal from main-channel latency).
- True keystroke-to-display RTT measurement, which would
  need an instrumented guest or known-glyph detection.
  Out of scope.
