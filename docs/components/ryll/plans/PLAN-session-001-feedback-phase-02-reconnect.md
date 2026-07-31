# Phase 02: Main-channel reconnect / keepalive (originally framed as the K1 fix)

## Resolution update (2026-05-11)

K1 itself is now **resolved at the root** in commits `370d8ce5`
(fix) and `cf3d31f5` (regression test). Root cause was not a
keepalive issue, a server rcc timeout, or any tokio / rustls /
kernel bug — it was an abandoned-receiver deadlock in our own
session orchestrator (`shakenfist-spice-renderer/src/
session.rs`'s intermediate `mpsc::channel(64)`). See the resolution note in
`PLAN-session-001-feedback.md` for the full chronology (the
standalone `docs/TOKIO-WEDGING.md` write-up has been removed
and remains available in git history).

This means the rest of this phase plan applies *as written* but
with a different framing:

- The keepalive mitigations already landed (mirror keepalive,
  KEY_MODIFIERS idle keepalive on inputs, spurious-PONG idle
  keepalive on main, channel-exit logging, app-focus gating
  of clipboard polling, etc.) are no longer load-bearing
  *for K1 specifically*. They stay landed as defense-in-depth
  against unrelated mid-session liveness failures.
- The remaining Phase 02 steps (channel-error attribution,
  reconnect state machine + auto-reconnect UX, console.vv
  extensions and modal variants, docs wrap-up) are still
  worth doing — they handle disconnects in general, not just
  K1. **Reframe** any work below from "fix K1" to "make ryll
  recover gracefully from arbitrary mid-session
  disconnects."
- The "gated on Phase 01 data" prerequisite below is now
  historical only. Phase 01 produced rich `disconnect-cause`
  zips that were valuable during the K1 investigation but are
  not a hard input for the remaining UX work.

Pending steps as of resolution date (see active task list):

- Step 4: `ChannelEvent::Error` channel attribution — see
  **Block E** in the Approach section for the design and
  the Block E Tasks subsection for the concrete change list.
- Step 5: ReconnectState state machine + auto-reconnect UX —
  Block A in the Approach section.
- Step 6: console.vv extensions + modal variants — Block A
  sections A.4 / A.5 / A.6 in the Approach section.
- Step 7: wrap-up docs and master-plan status — the "Wrap-up"
  Tasks subsection.

Three K1-investigation side-quests were captured as
deferred work in the master plan's "Deferred side-quests
from the K1 investigation" section
(`PLAN-session-001-feedback.md`) rather than re-listed here:

- Step 2d: screen-lock detection on macOS.
- Step 22: macOS app icon.
- Step 23: stack-trace capture in disconnect bug reports.

The rest of this document is preserved unmodified for context
on why each Phase 02 step was originally proposed and how it
relates to the (now-defunct) K1 hypotheses.

## Prompt

Before answering questions or making design decisions in this
document, read the relevant ryll source. Key files:
`shakenfist-spice-renderer/src/channels/main_channel.rs` (the
client-side 30 s keepalive timeout and the PING / PONG path),
`shakenfist-spice-protocol/src/client.rs` (TCP keepalive socket
options applied at connect time), `ryll/src/app.rs` (the
existing manual `reconnect()` method and the `ChannelEvent`
handlers extended in Phase 01), and `ryll/src/bugreport.rs`
(the `DisconnectCause` record produced at the moment of
failure). Consult `ARCHITECTURE.md` for channel and event flow,
`AGENTS.md` for build and test conventions, and the SPICE
reference at `/srv/src-reference/spice/` for the server's rcc
liveness check (`spice/server/red-channel-client.cpp:656` and
`main-channel-client.cpp:38` for the 30 s constant) and
spice-gtk's keepalive strategy (`spice-gtk/src/spice-session.c:2286`
TCP keepalive setup, `spice-gtk/src/channel-base.c:43` reactive
PONG).

This phase lands the user-visible fix for bug **K1** — "main
channel rcc 30 s unresponsive timeout tears down session" —
identified during dogfooding session 001. It is gated on
**Phase 01 data**: at least one disconnect-cause.json zip
captured from a real reproduction. Without that, the diagnostic
branches under "Approach" cannot be selected, and we would be
designing the fix from speculation. See "Prerequisite" below.

One commit per logical step (no-regret pieces independent of
the diagnostic outcome can land before the data arrives, but
the conditional branches must wait). Each commit must build,
lint, and pass tests on its own.

## Situation

### What we already established

**Server-side timeout is 30 s, not 300 s** (Q2 from the master
plan, resolved). At
`/srv/src-reference/spice/spice/server/main-channel-client.cpp:38`:

```cpp
#define CLIENT_CONNECTIVITY_TIMEOUT (MSEC_PER_SEC * 30)
```

The check itself lives in
`/srv/src-reference/spice/spice/server/red-channel-client.cpp:656`
(`connectivity_timer`), measures **any inbound byte** from the
client, and resets on receive. If 30 s pass with no byte
received, the server logs `"rcc has been unresponsive for more
than %u ms"` and tears down the session. The user perceives
this as an inputs-channel disconnect because the entire SPICE
session drops when main is torn down.

**TCP keepalive is already configured on the SPICE socket** at
`shakenfist-spice-protocol/src/client.rs:189-202`. Values match
spice-gtk exactly: `TCP_KEEPIDLE = 30 s`, `TCP_KEEPINTVL = 15 s`,
`TCP_KEEPCNT = 3`. This rules out "we forgot the obvious thing".

**ryll responds to server PINGs sub-millisecond** in every
session-001 pcap (verified during triage). The PING handler at
`main_channel.rs:522-563` is purely synchronous on the channel
read loop — `Ping::read()` parses, the PONG payload is built,
and `make_message()` queues it on the send loop, all without
awaiting anything that could block.

**Client-side mirror timeout** at
`shakenfist-spice-renderer/src/channels/main_channel.rs:297-311`
fires after 30 s of no inbound data on main:

```rust
_ = tokio::time::sleep_until(last_data_received + keepalive_timeout) => {
    info!("main: no data received for {}s, assuming disconnected", ...);
    if let Ok(mut snap) = self.snapshot.lock() {
        snap.keepalive_timeout_fired = true;
    }
    self.event_tx
        .send(ChannelEvent::Disconnected(ChannelType::Main))
        .await
        .ok();
    self.repaint_notify.notify_one();
    break;
}
```

This is the **same 30 s window as the server's rcc check**, so
either side firing first triggers a teardown. The Phase 01
disconnect-cause.json record now distinguishes "we timed
ourselves out" (`keepalive_timeout_fired = true`) from a real
EOF / RST.

**Reconnect today is manual.** `RyllApp::reconnect()` at
`app.rs:701` is wired only to the "Reconnect" button on the
disconnect modal at `app.rs:3127`. There is no auto-retry, no
backoff, no surface for non-modal reconnect attempts. The
`connection_cancel: Option<Arc<AtomicBool>>` plumbing
(`app.rs:419`, `app.rs:706`, `app.rs:787`) is reusable for an
auto-retry path — we cancel the previous attempt and spawn a
new one, exactly the same way the manual button does today.

**spice-gtk and virt-viewer have no application-layer
keepalive.** They rely on TCP keepalive (`spice-session.c:2286`,
identical values) plus reactive PONG. If TCP keepalive +
reactive PONG were sufficient, ryll would not see this
disconnect — yet it does, on macOS, with the user actively
using their computer. So either ryll is doing something
spice-gtk doesn't, or the platform is doing something to ryll
that it doesn't do to virt-viewer. Both are testable from the
disconnect-cause.json + pcap once Phase 01 data is in hand.

### What we don't yet know

The session-001 pcaps captured only post-reconnect activity —
they do not contain the moment of failure. We cannot tell:

1. Whether the server stopped sending PINGs in the seconds
   before disconnect (server-side starvation).
2. Whether ryll's tokio runtime stopped processing reads in
   time (client-side starvation — most plausibly macOS App Nap
   when ryll is not the foreground window).
3. Whether the TCP path itself silently dropped traffic between
   the OS keepalive probe interval (rare but possible on flaky
   wifi / VPN).

Phase 01's `disconnect-cause.json` plus the disconnect-moment
pcap will resolve this. The diagnostic decision tree under
"Approach" branches accordingly.

## Mission and problem statement

Make ryll survive the K1 disconnect class without the user
having to click "Reconnect", and where possible **prevent** the
disconnect from happening at all. The phase has two halves:

1. **No-regret UX**: introduce automatic reconnect with backoff
   on transport failure, so a momentary disconnect (network
   blip, server restart, ticket reuse on the same gateway) is
   recovered transparently. This applies regardless of the K1
   root cause.

2. **Root-cause fix for K1**: diagnose against Phase 01 data,
   then apply the matching one of three pre-designed fixes.

The phase succeeds when:

- A user who leaves a SPICE session running on macOS overnight
  (or while doing other work for >30 minutes) returns to a
  still-functional session, OR returns to a session that
  reconnected automatically without manual intervention.
- The next dogfooding session does not reproduce K1, OR if it
  does, the produced disconnect-cause.json + pcap show a path
  we know how to address (rather than the speculative state we
  are in today).
- No regression for the ticket-bounded deployments (Kerbside,
  oVirt) where reconnect against a one-time ticket is doomed —
  ryll detects these via the standard `delete-this-file=1`
  console.vv key and shows an explanatory modal instead of
  retrying. See §A.4.
- A new console.vv extension `ticket-valid-until=<unix-ts>`
  is parsed and surfaced (countdown UI, expiry-aware modal,
  pre-expiry warning notification). Documented in the
  companion `console-vv-extensions.md` doc — see "Companion
  docs" below. Producers (Kerbside, oVirt) are not yet
  emitting this key on day one; the absence is a no-op.

## Prerequisite

**Phase 02 implementation is gated on at least one
`disconnect-cause.json` zip from a real K1 reproduction.** That
zip must:

- Have `keepalive_timeout_fired` set on the `main` snapshot (or
  explicitly *not* set, in which case the cause is server-side
  RST and the diagnostic branch is different).
- Carry a `traffic.pcap` whose end shows the run-up to the
  failure — last 60 s of main-channel traffic before the
  timeout.
- Be reproducible (the user has been able to trigger K1 just
  by leaving the session idle while using the host for other
  work; a ~30 minute idle window has been sufficient on
  session-001).

The no-regret UX work (auto-reconnect with backoff, sections
"Auto-reconnect" below) **may proceed in parallel** with data
collection — it does not depend on the diagnostic outcome.

## Approach

The work breaks into three blocks. Block A is no-regret and can
land first. Block B is the diagnostic step (no code, just
analysis). Block C is the conditional fix selected by Block B.

### Block A — Auto-reconnect with backoff (no-regret)

Today, every disconnect terminates in the modal at
`app.rs:3119`. The user clicks "Reconnect", which calls
`RyllApp::reconnect()` (`app.rs:701`). Block A inserts an
automatic retry layer between the disconnect signal and the
modal.

#### A.1 Retry policy

Three attempts with exponential backoff: **1 s, 4 s, 16 s**
(matching the spice-gtk `SPICE_SESSION_PROPS_PROTOCOL` retry
shape — short first attempt for blip recovery, longer windows
for server restarts). Total worst-case wait ~21 s before the
modal pops.

Caps:

- Maximum 3 attempts per disconnect cluster; subsequent
  disconnects within a 5 minute window do not extend the
  budget. (Otherwise a flapping server would have ryll banging
  away forever.)
- Auto-reconnect **does not** trigger when the .vv said
  `delete-this-file=1` (single-use ticket — see §A.4) or when
  `ticket-valid-until` has elapsed (§A.5) — both are known-
  doomed retries.

#### A.2 Wiring

A new state machine on `RyllApp`:

```rust
enum ReconnectState {
    Idle,                        // connected normally
    Pending { attempt: u8, next_at: Instant },
    Modal,                       // budget exhausted, user takes over
}
```

Replaces the bool-ish `show_disconnect_dialog`. Driven from
the existing GUI tick loop (`update()` in `app.rs`). When
`ChannelEvent::Disconnected` / `Error` fires, transition Idle →
Pending{1, now+1s}. The tick loop checks if `next_at` has
passed and triggers a `reconnect()` if so. On success, back to
Idle. On failure, increment attempt; if attempt > 3 or budget
exhausted, transition to Modal (current behaviour).

The disconnect-snapshot logic from Phase 01 still runs at the
event handler — auto-reconnect does not suppress it. Each
attempt that fails *also* writes a snapshot, subject to the
existing 60 s cooldown (which was designed for exactly this
case).

#### A.3 UI surface

Two visible changes:

1. **Status-bar indicator** — when in `Pending`, show
   "Reconnecting… (attempt 2/3)" in the bottom status panel
   beside the existing FPS/connected widgets. Dismiss on
   success or on Modal transition.
2. **Notification** — push a `NotifySeverity::Warn` entry per
   attempt failure with source `NotificationSource::BugReport`
   ("Reconnect attempt 2 failed: <error>"). Same notification
   plumbing Phase 01 already uses for "Disconnect snapshot
   saved to …".

Modal copy varies by exit cause — see A.6 below.

#### A.4 Detecting one-shot tickets via `delete-this-file`

In Kerbside / oVirt deployments, the SPICE ticket is a
one-time-use token: once any channel has linked with it, the
server invalidates it. A reconnect attempt with the same
ticket fails at `reds.cpp:2098-2110`'s ticket-validation step.

We **must not auto-reconnect in that case** — it produces a
ratchet of failed attempts, each writing a snapshot (despite
cooldown bounding it), confusing the user and the reviewer of
the bug-report directory.

**The standard virt-viewer `delete-this-file=1` key is a
reliable proxy for one-shot ticket semantics.** Empirically
every producer that emits one-shot tickets (Kerbside, oVirt)
also sets `delete-this-file=1`, because the file becomes
useless after the first link establishment. Reusable-ticket-
with-`delete-this-file=1` is a deployment contradiction (what
would you reuse from after deletion?). The spec does not
formally require this interpretation, but the empirical
contract is strong enough to lean on.

Implementation: extend the .vv parser at
`ryll/src/config.rs:266` to read `delete-this-file` and
surface it on `Config` as a new `bool` field
(`ticket_is_single_use`). When `true`, the auto-reconnect
state machine refuses to enter `Pending` — disconnects go
straight to `Modal { variant: OneShotConsumed }`.

Does **not** add a new CLI flag or a new console.vv key —
piggybacks on a key that exists, so day-one behaviour against
existing producers (Kerbside, oVirt) is correct without
producer-side changes. If a future producer ever wants
file-deletion-without-single-use semantics, an explicit
override key can be added then; speculatively defining one now
just invents a contradiction nobody asked for.

This interpretation is documented prominently in the README
and in the new `console-vv-extensions.md` doc (see "Companion
docs" below) so producers know what we infer from the standard
key.

#### A.5 Ticket validity window via `ticket-valid-until`

A new console.vv extension key:

```ini
[virt-viewer]
ticket-valid-until=1730500000  ; unix timestamp
```

Optional. When set, ryll knows when the server will reject
the ticket regardless of one-shot status. Three uses:

1. **Auto-reconnect bound.** `ReconnectState::Pending` checks
   `now() >= ticket_valid_until` before each attempt; if past
   expiry, transitions to `Modal { variant: TicketExpired }`
   instead of retrying.
2. **Pre-disconnect warning.** A `NotifySeverity::Warn`
   notification fires once at T-30 s relative to expiry:
   "Session ticket expires in 30 seconds." Driven from the
   GUI tick loop, not a dedicated timer.
3. **Modal context.** `Modal { variant: TicketExpired }`
   includes the expiry timestamp in the body text.

This is a genuinely new extension — no existing console.vv
key carries this information. Document under "extensions" in
the new doc; raise as an RFE against Kerbside (in
`/home/mikal/src/shakenfist/kerbside`) and against oVirt
issue tracker once the doc lands.

Day-one behaviour with no producers populating the key:
identical to today (key absent → no expiry tracking → no
behaviour change beyond A.4's `delete-this-file` reading).

#### A.6 Disconnect modal variants

`ReconnectState::Modal` carries a variant discriminant:

```rust
enum ModalVariant {
    Generic { latest_error: String },     // generic disconnect, retry possible
    OneShotConsumed,                      // delete-this-file=1 was set
    TicketExpired { expired_at: SystemTime }, // ticket-valid-until elapsed
}
```

UI rendered at `app.rs:3119`:

| Variant | Title | Body | Buttons |
|---|---|---|---|
| Generic | "Connection lost" | "Three automatic reconnect attempts failed: \<latest_error\>." | Reconnect, Quit |
| OneShotConsumed | "Session ended — cannot reconnect" | "This connection used a single-use ticket. Request a new connection from the system that issued the original link." | Quit only |
| TicketExpired | "Session ended — ticket expired" | "The ticket for this session expired at \<HH:MM:SS\>. Request a new connection." | Quit only |

Both `OneShotConsumed` and `TicketExpired` omit the Reconnect
button — there is no useful action for the user inside ryll;
the doomed-retry ratchet is exactly what the variant exists to
prevent.

Edge case: `ticket-valid-until` set but in the future at
disconnect time. The server told us the ticket expired but our
clock thinks it's still valid — almost certainly clock skew.
Render the `TicketExpired` modal anyway (server's view is
authoritative) but log a `warn!` "ticket-valid-until in the
future at disconnect time, possible clock skew" so future
debugging has a hook.

#### A.7 Reset path

#### A.7 Reset path

`reconnect()` at `app.rs:701` already does the right teardown
(cancel previous, clear surfaces, respawn). One adjustment:
also clear the `keepalive_timeout_fired` flag on the
`MainSnapshot` so a subsequent disconnect cleanly reports its
own cause. Phase 01's open-question 3 listed this as the
right fix; do it now in `reconnect()` rather than scattering
clearing logic. If the `MainSnapshot` already exists at the
point `reconnect()` runs (it does, via
`self.channel_snapshots.main`), this is a one-liner.

### Block B — Diagnostic step (no code)

Once a Phase 01 disconnect-cause.json zip is in hand:

**Decision tree**:

| `keepalive_timeout_fired` | Last `last_recv_ts_secs` on main vs. session uptime | pcap tail | Diagnosis | Branch |
|---|---|---|---|---|
| true | gap of ≥30 s before disconnect | no FIN / RST from server in window | **Server stopped sending PINGs**, or PINGs lost on path. The server's own connectivity timer fires concurrently. | C.1 (proactive client-side PING) |
| true | gap of ≥30 s before disconnect | server PINGs visible in window, ryll PONGs delayed > 30 s | **Client-side starvation.** Most likely macOS App Nap throttling the tokio runtime when ryll is not foreground. | C.2 (disable App Nap on macOS) |
| false | normal traffic up to ~T-1 s | server FIN / RST at T | **Server-side close** — this row should not occur unless something other than the rcc timeout is killing us (e.g. agent disconnect, ticket re-validation on a partial reconnect). | C.3 (investigate the specific server log line) |
| true | last recv was server PING ≤500 ms before disconnect | ryll PONG was queued but never went out | tokio send-side starvation; same as C.2 substantively. | C.2 |

Sub-cases:

- If the disconnect-cause.json's `per_channel.main.ping_recv_count`
  is zero or near-zero across the whole session (not just the
  failure window), the server has not been PINGing at all —
  unusual for QEMU but possible. Confirms C.1.
- If display channel was active (`per_channel.display.bytes_in`
  rising) right up to the disconnect moment but main was idle,
  that's evidence main is being singled out — plausibly App
  Nap doesn't single out one channel, but tokio task scheduling
  can if main's task happens to be suspended on the wrong
  resource. Lean toward C.2.

**Output of Block B**: a one-paragraph summary of the chosen
branch, committed to this plan as a "Diagnosis" section
appended below "Approach" before any C-block code lands.

### Block C — Root-cause fix (one of)

#### C.1 Proactive client-side PING on every channel

(Scope expanded from the original draft. The original specified
main-channel only at 10 s. The session-001b data — see
"Diagnosis" — shows the failing channels are inputs / cursor /
playback / usbredir, all of which sit completely silent in
both directions for the last hundreds of seconds before
disconnect. Main and display were both still active. So the
PING needs to land on whichever channel has gone idle, not
just main.)

Introduce a client-driven `SPICE_MSGC_PING` on **every channel
ryll runs** (main, display, inputs, cursor, playback, usbredir,
webdav). On each channel, if no inbound bytes have been
received for **15 s**, send a PING. The server responds with
PONG, the byte-flow on the channel is restored, and the
server's per-channel idle timer (whatever its actual constant
is — see Diagnosis) resets. 15 s is conservative against the
observed 300 s server-side window with a wide safety margin.

The `Ping` opcode is a symmetric protocol message — the SPICE
spec defines it for both directions
(`/srv/src-reference/spice/spice-gtk/src/channel-base.c:43`
treats inbound PING uniformly, and the universal PONG handler
is added to *every* channel via `spice_channel_add_base_handlers`
at `channel-base.c:210-234`). The server side at
`/srv/src-reference/spice/spice/server/red-channel-client.cpp`
handles client-sent PINGs in the same connectivity-timer reset
path as any other inbound byte. spice-gtk does not itself emit
proactive client PINGs as far as we can tell — but the
universality of the handler means the server is required to
accept them on any channel, so doing so is protocol-legal.

Site: every channel handler's `tokio::select!` read loop gains
a new branch:

```rust
_ = tokio::time::sleep_until(last_recv_or_send + Duration::from_secs(15)) => {
    let ping = build_client_ping();  // SPICE_MSGC_PING
    self.send(ping).await?;
    last_recv_or_send = tokio::time::Instant::now();
}
```

`last_recv_or_send` is a new local (not added to the snapshot —
transient) tracking the more recent of the channel's last
inbound byte and last outbound byte. This ensures:

- A channel actively receiving server traffic (display under
  load, main while clipboard sync is running) does not emit
  redundant client PINGs on top.
- A channel actively *sending* (e.g. cursor position updates
  while the user is using the session) does not emit client
  PINGs either — the user-driven traffic is doing the job.
- Only fully-idle channels emit the proactive PING, at most
  once per 15 s.

Snapshot fields to add on **every channel snapshot** (not just
main):

```rust
pub client_ping_send_count: u32,
pub last_client_ping_send_ts_secs: Option<f64>,
```

A future disconnect-cause.json then shows whether the
proactive PING was firing on the affected channel — critical
diagnostic if a session-002b reproduction shows the disconnect
returning despite the fix.

Cost: in the worst case (full idle on all 7 channels) one
~11-byte message every 15 s × 7 channels = ~5 byte/s.
Indistinguishable from noise. The expected case is 1–2
channels needing PINGs at any given moment (display and main
are virtually always active during use).

Caveats:

- The webdav channel is only present when shared-folder
  redirection is active. Its handler should still gain the
  proactive-PING branch but only run when the channel is
  established.
- The PONG handler on every channel already increments
  `pong_send_count` (Phase 01 work). The reverse — counting
  PONGs we *receive* from the server in response to our PING —
  is new. Add `client_pong_recv_count: u32` to the snapshot
  alongside the send-side counter so we can confirm round-trip.
- Cancel any in-flight client-PING send if the channel goes
  through `Disconnected` — don't write to a closed socket.

#### C.2 Disable App Nap on macOS (opportunistic, not selected by Block B)

The session-001b data did not strongly support the
client-side starvation hypothesis: timing was indistinguishable
across foreground / background / different-virtual-desktop
cases. App Nap typically activates only when backgrounded, so
if it were the dominant cause the foreground capture should
have looked different. It didn't.

That said, App Nap could be a contributing factor on the
*idle channels' tokio tasks* even when ryll's main thread is
foregrounded — and disabling it is a small, defensible
hardening that any interactive remote-display app should
probably do. Therefore: keep the design here, but treat C.2
as **a follow-on if a session-002b reproduction shows the
disconnect persisting after C.1 + Block A**, not as a
required part of this phase.

If implemented:

macOS App Nap activates when an app is not the active window
and not playing audio, suspending its runloop / GCD queues.
tokio sleeps and socket reads are subject to it. ryll's audio
playback is on a separate channel and may not always be active
(no audio in the guest = no playback channel data = nothing
keeping us awake).

Fix: call `NSProcessInfo.beginActivityWithOptions:reason:` on
startup with `NSActivityUserInitiated | NSActivityIdleSystemSleepDisabled`
(or at least `NSActivityUserInitiated | NSActivityLatencyCritical`),
holding the resulting `NSObjectProtocol` for the lifetime of
the SPICE session. This is the documented opt-out from App Nap
and is what apps like Zoom and SSH clients use.

Implementation:

- New crate dep: `objc2` (already in the workspace via egui's
  macOS path) or a small `extern "C"` block. Probably the
  cleanest: a `#[cfg(target_os = "macos")]` module
  `ryll/src/macos.rs` exposing `begin_user_activity()` →
  returns an opaque guard struct that calls
  `endActivity:` on drop.
- Call from `RyllApp::new` after the connection thread has
  spawned.
- Drop the guard when the session ends (Drop on `RyllApp` or
  on the connection-thread cleanup).

Treat as an opportunistic follow-on. If C.1 + Block A close
out the K1 reproduction successfully, C.2 may still be worth
landing as macOS hardening but does not block this phase.

Cost: zero additional traffic. Slight increase in idle CPU
when ryll is not the active app (macOS will not throttle
us). This is the tradeoff every interactive remote-display app
makes.

Sub-task: also call `IOPMAssertionCreateWithName` with
`kIOPMAssertionTypePreventUserIdleSystemSleep` if the user has
explicitly requested "don't let the host sleep while connected"
— defer this to a later phase, mention here so we don't tangle
the App Nap fix with a different assertion.

#### C.3 Server-side close investigation

If diagnosis is "server-side close, not rcc timeout": this is
unexpected and invalidates the hypothesis baseline. Stop
implementing and return to triage — likely we have a different
bug than K1. Re-open the master plan.

### Block D — ryll's own 30 s timeout

Independent of the C-block selection, the ryll-side mirror
timeout at `main_channel.rs:297` is currently a footgun: it
fires at the same 30 s as the server, sometimes racing the
server, and we can't tell which closed first from the modal
path. With Block A (auto-reconnect) and Block C (root cause
addressed), the mirror timeout has three options:

(D.a) **Keep at 30 s.** Defensive: if the server somehow
disappears without RST (host hard-killed, network partition),
we still notice in 30 s. With auto-reconnect, the user sees a
brief "reconnecting" flash. This is the conservative option.

(D.b) **Extend to 90 s.** Lets the server's 30 s window fire
unambiguously first when the server is still alive — the pcap
will then show server FIN/RST instead of our local timer
firing, which is more informative for future debugging. Still
catches truly-dead-server cases within 90 s.

(D.c) **Remove entirely.** Rely on TCP keepalive (75 s to
detect a dead peer) plus the channel read returning Err on
RST. Simplifies the code path; downside is in the unlikely
case the kernel TCP keepalive fails to detect death, we hang
forever.

Pick **(D.b)**: keep the timeout but extend to 90 s. Cost is
negligible, debuggability improves materially. Add a one-line
comment at the timeout site explaining why 90 s and not 30 s
("server's own check is 30 s; this is a backstop for when the
server itself is dead or unreachable, not a primary
mechanism").

### Block E — `ChannelEvent::Error` channel attribution

Originally raised as a "minor Phase 01 plumbing improvement"
under the Diagnosis section. Promoted to a first-class Phase 02
step because the auto-reconnect UX in Block A wants per-channel
attribution on every disconnect path — `Disconnected` already
carries it, `Error` does not, and the resulting asymmetry leaks
into modal copy, snapshot filenames, and any future
per-channel reconnect telemetry.

**Variant change.** `ChannelEvent::Error(String)` becomes
`ChannelEvent::Error { channel: ChannelType, message: String }`
in `shakenfist-spice-renderer/src/channels/mod.rs:174`. Mirrors
`Disconnected(ChannelType)`.

**Three emit sites:**

- `channels/inputs.rs:239` — straightforward; pass
  `ChannelType::Inputs`. The `"inputs: "` prefix is dropped from
  the message string since the structured field carries the same
  information.
- `session.rs:333` — currently inside a flat `for handle in
  handles` loop where channel attribution has already been lost.
  Fix at construction: pair each `JoinHandle` with its
  `ChannelType` so the wait loop can pass it through. Specifically:
  - `session.rs:143` becomes
    `vec![(ChannelType::Main, main_handle)]`.
  - Every `handles.push(tokio::spawn(...))` at lines 174, 191,
    210, 232, 258, 283 becomes
    `handles.push((channel_type, tokio::spawn(...)))`.
  - The `abort_handles` collection at line 303 iterates
    `.map(|(_, h)| h.abort_handle())`.
  - The wait loop at line 322 destructures
    `(channel_type, handle)` and forwards the type into the
    event.
- (No third emit site today, but the variant must remain
  composable for future channels that surface
  application-level errors — webdav and usbredir are the
  likely future emitters.)

**Two consume sites:**

- `session.rs:517` (headless `error!` log) — include channel
  name in the log line so headless-mode operators see the
  attribution.
- `ryll/src/app.rs:1146` — destructure `{ channel, message }`
  and pass `channel.name()` to
  `maybe_write_disconnect_snapshot` in place of the hard-coded
  `"error"`. Also include the channel name in
  `disconnect_reason` so the existing modal text reads
  ("inputs channel error: ...") rather than just
  ("Connection error: ...").

**Doc fixups:** the two doc comments in `bugreport.rs` at
lines 638 and 716 currently say `"error" for ChannelEvent::Error
paths without a specific channel attribution` — both become
unconditional, since every `Error` now names its channel. The
`_ =>` fallback arm in `BugReportType::channel_name()` at
line 671 stays as a defensive default but should never fire
after this change.

No new tests required; the change is mechanical and the
existing unit / integration suite exercises the affected
paths. Verified by `make build`, `make lint`, `make test`. The
filename change (`ryll-disconnect-inputs-…` instead of
`ryll-disconnect-error-…`) is the user-visible signal.

Block E is independent of Blocks A/B/C/D and may land before
Block A. It does not require Phase 01 data.

## Diagnosis

(This section is the "Output of Block B" promised under
"Approach". It captures the conclusions from session-001b
data — three disconnect-cause.json zips at
`~/ryll-test-sessions/test-session-001b/` — and pins down the
C-block branch to follow.)

### Reproduction

Three captures by the user, all on macOS, all reproducing K1:

| Zip | App position | Disconnect timing | Failing channel |
|---|---|---|---|
| `…05-16-29Z.zip` | foreground, user wandered off | T+510 s | inputs |
| `…05-34-16Z.zip` | backgrounded, host actively used | T+510 s | inputs |
| `…05-44-21Z.zip` | backgrounded on different virtual desktop | T+540 s | inputs |

All three: error message identical
("`inputs: read error: peer closed connection without sending
TLS close_notify`"), `keepalive_timeout_fired: false`, channel
filename literally `error` because `ChannelEvent::Error`
doesn't carry channel attribution (a Phase 01 plumbing
limitation worth fixing later).

### Per-channel state at the moment of failure

`disconnect-cause.json[*].per_channel`:

| Channel | Last recv (median across 3 runs) | PINGs received |
|---|---|---|
| main | T+465 s | 66–67 |
| display | T+496–527 s | 68–72 |
| **inputs** | **T+300.3 s** | **4** |
| cursor | T+300 s | 4 |
| playback | T+300 s | 4 |
| usbredir | T+300 s | 4 |
| webdav | never connected | 0 |

The 300-second mark is sharp and reproducible across all
three runs. Cursor / inputs / playback / usbredir all stop
receiving server traffic at almost the same instant; main
and display keep going.

### Reconciling three different time constants

Three numbers come up in this failure mode and they do not
trivially line up:

| Number | Source | What it represents |
|---|---|---|
| **30 s (30 000 ms)** | QEMU/libvirt log line: `kvm: warning: Spice: main:0 (...): rcc 0x558a785cd310 has been unresponsive for more than 30000 ms, disconnecting` | The server's `CLIENT_CONNECTIVITY_TIMEOUT` at `main-channel-client.cpp:38`. Definitively 30 s; the user has confirmed the log line is unambiguous. |
| **300 s** | T+300 mark in disconnect-cause.json `last_recv_ts_secs` | When the four idle channels stop receiving any server traffic at all. |
| **75 s** | T+465 (main's last byte) → T+540 (disconnect detection in zip 3) | Gap between main going silent and ryll observing the read error. Matches `TCP_KEEPIDLE 30 + 3 × TCP_KEEPINTVL 15 = 75 s` exactly. |

A coherent story that fits all three:

1. At **T+300** the server stops sending traffic on the four
   idle channels. *Why* this happens at 300 s is the
   unresolved part — the SPICE server's `connectivity_timer`
   does not have a 300 s constant. Possibilities:
   - Server's per-channel ping_timer logic gates on channel
     activity in some way that produces a ~300 s tail.
   - Some interaction with `PING_TEST_IDLE_NET_TIMEOUT_MS`
     (100 ms) and the `CONNECTIVITY_STATE_BLOCKED` state
     transitions yields this number.
   - Something else (caps negotiation, agent state) gates
     server behaviour around the 5-minute mark.
   - **Or it's a coincidence with user behaviour** despite
     the user's belief otherwise — the channels going silent
     may simply reflect a 5-minute baseline of "stuff the
     user does at session start" tapering off uniformly. This
     is testable by reproducing while continuously moving the
     mouse: if the inputs channel still goes silent at T+300
     under continuous mouse movement, it's server-side; if
     not, it's a user-activity artefact.
2. From T+300 to T+465 the inputs/cursor/playback/usbredir
   sockets are silent in both directions. Main is still
   active (SET_ACKs every 15 s, server PINGs every ~7 s).
   The server's `CLIENT_CONNECTIVITY_TIMEOUT` for those
   channels' rcc is presumably resetting because the
   *server-side* `received_bytes` flag is set when ryll
   replies to PINGs on main — but that's per-channel
   monitoring, so this should not be the explanation.
   Another unresolved question.
3. At T+465 main itself goes silent (no more SET_ACKs from
   server, no client traffic to drive new ones). The
   server's main-channel rcc check now has nothing to reset
   on. 30 s later (T+495) the server's check fires and logs
   `unresponsive for more than 30000 ms`. Server tears down.
   The kernel TCP stack on macOS surfaces the FIN on the
   inputs socket at ~T+540; ryll's read on inputs returns
   the rustls "peer closed without TLS close_notify" error
   first because the inputs task happens to be polling at
   that moment. The ~45 s delta between server-side log
   (T+495) and client-side detection (T+540) is consistent
   with the 75 s TCP keepalive backstop on the inputs socket
   firing slightly before the server's actual FIN propagates.

This story explains the 30 s log line truthfully (no QEMU
typo — the rcc check really is 30 s, and it really fires at
T+495 once main is genuinely silent for 30 s). It does **not**
explain the 300 s mark or why the four idle channels go
silent simultaneously. That is left as an open question; C.1
(below) sidesteps the need to resolve it because making ryll
send proactive bytes on every channel renders the server's
exact PING-gating logic irrelevant.

### What kills the session

After the channels go silent at T+300, ryll's read on the
inputs TCP socket returns EOF only at T+510–540 — 210–240 s
later. This is consistent with the 75 s TCP keepalive
detection cycle (`TCP_KEEPIDLE 30 + 3 × TCP_KEEPINTVL 15 = 75 s`)
running on the *main* channel, not the inputs one. Main's
last bytes are at T+465 s; T+465 + 75 = T+540 s, which lines
up with zip 3's disconnect detection. The inputs channel's
own TCP keepalive should fire faster (channels are independent
TCP sockets), so either the inputs socket's keepalive is
quiescent on macOS until something else wakes the runtime, or
the kernel buffers the inputs FIN until main's death wakes
ryll's tokio runtime to drain pending reads.

### What spice-gtk does that ryll doesn't (probably)

Re-checked: spice-gtk's PONG handler is universal (added to
*every* channel via `spice_channel_add_base_handlers` at
`spice-gtk/src/channel-base.c:210-234`), same as ryll's. So
"spice-gtk PONGs and ryll doesn't" is not the answer. We did
not find evidence of spice-gtk emitting proactive client PINGs
or any other periodic per-channel send. Three remaining
hypotheses for "why doesn't remote-viewer hit this":

1. **It does, but is not as systematically dogfood-tested in
   long-idle scenarios.** virt-viewer users typically aren't
   leaving sessions running for 5–10 minutes idle and then
   coming back; or when they do, the disconnect dialog is
   easy to dismiss and the failure mode isn't reported.
2. **Capability negotiation differences alter server
   behaviour.** spice-gtk negotiates a wider set of
   capabilities. The server may be selectively gating PING
   send on certain caps; ryll, with fewer caps, may be in a
   server code path that stops PINGing inputs/cursor/etc. once
   the channel is "set up" but no traffic flows.
3. **A session-property or initial handshake message** that
   spice-gtk sends and ryll doesn't, indirectly nudging the
   server to keep the channel "active".

This open question is worth chasing, but the fix proposed
below (proactive client PING on every idle channel) does not
depend on resolving it. C.1 makes ryll *send bytes*
client-side, which trivially keeps the server's per-channel
timer happy regardless of what the server's exact PING-send
gating logic is. Whatever spice-gtk relies on, our PING
sidesteps it.

### Selected branch: C.1, with scope expanded

Original C.1 in the plan said "main channel only, every 10 s".
The data invalidates the scope: main is fine; the failing
channels are inputs / cursor / playback / usbredir.

Revised C.1: send `SPICE_MSGC_PING` on **every channel** when
that channel has been silent (in both directions) for ≥ 15 s.
15 s is conservative against the observed 300 s window, with
ample margin for clock skew, scheduling jitter, and any
shorter timeout we don't yet know about. Cost is negligible
(see C.1 for the math).

C.2 (App Nap opt-out) is **demoted from "selected branch" to
"opportunistic follow-on"** — the foreground/background timing
parity argues against it being load-bearing. Keep the design
in the plan; revisit only if a session-002b reproduction after
C.1 + Block A still shows disconnects.

C.3 (server-side close investigation) is no longer on the
critical path — the data fits within the K1 hypothesis;
nothing here invalidates the master-plan triage.

Block A (auto-reconnect with backoff) is unchanged. It's a
UX win regardless of root cause, and once C.1 prevents the
disconnect class entirely, A becomes a backstop for the
remaining "real network died" cases (laptop sleep, server
restart, etc.).

Block D (extend ryll's mirror keepalive to 90 s) is unchanged
and correctly motivated by `keepalive_timeout_fired: false`
across all three captures — our local timer is harmless in
this failure mode but extending it means the server's check
fires unambiguously first whenever it does fire.

### Two minor improvements for Phase 01 plumbing, surfaced by this data

(Not strictly Phase 02 work, but worth landing alongside.)

- `ChannelEvent::Error(String)` carries no channel
  attribution. The disconnect-cause filename ends up as
  `ryll-disconnect-error-…` rather than
  `ryll-disconnect-inputs-…`, which is mildly confusing. Phase
  01's `BugReportType::Disconnect { channel }` already supports
  the per-channel form; the gap is in the event itself.
  **Promoted to Block E (Approach section) and tracked as
  Step 4 of this phase** — a small mechanical refactor to
  `ChannelEvent::Error { channel: ChannelType, message: String }`
  so the snapshot pipeline picks up the channel name.
- `RuntimeMetrics::unavailable("not sampled on the GUI thread")`
  in the auto-disconnect zip is a known limitation but the
  error message is opaque to a maintainer reading the zip
  cold. Tighten the wording or link to the explanation in
  ARCHITECTURE.md.

## Open questions

1. **Should auto-reconnect retry against a fresh ticket?** If
   the deployment supports it, the conductor / gateway
   (Kerbside, oVirt manager) can issue a new ticket on demand.
   ryll has no current path to request one. Phase 02 does not
   add this; the .vv-file ticket is what we have. If the
   .vv-file flow grows a "refresh" hook (e.g. browser
   integration in conductor), revisit.

2. **Should the auto-reconnect attempts share the disconnect
   modal's reason text?** Today the modal shows the original
   error. After auto-reconnect failure, we should show the
   *latest* attempt's error (most informative — the original
   may have been a transient blip while the latest is the real
   failure mode). Yes — track latest error in the
   `ReconnectState::Modal { latest_error }` variant.

3. **Macros / build-time gating for the App Nap fix.** Cargo
   features vs. `#[cfg(target_os = "macos")]`? Use cfg — App
   Nap is platform-conditional behaviour, not a feature
   flag. The non-macOS path returns a no-op guard, keeping the
   call site identical.

4. **Auto-reconnect during initial connect.** Today the link
   establishment at `session.rs` can fail (host unreachable,
   bad cert, bad ticket). Should auto-reconnect cover initial
   failures too? Defer: initial-connect failures are
   user-visible immediately and the user is already
   interactive at that moment. Auto-reconnect adds value when
   the user is *not* in front of the screen.

5. **Telemetry / counters.** Should we expose
   `auto_reconnect_count` somewhere visible (status bar, bug
   report)? Add to the existing channel-state JSON so a future
   bug report shows whether the user's session was rocky. Cheap
   and informative.

## Tasks

### Block A (no-regret, lands without Phase 01 data)

- [x] Add `ReconnectState` enum on `RyllApp` (`app.rs`),
      replacing the implicit boolean `show_disconnect_dialog`.
      State transitions only via the central event handler and
      the GUI tick. Pure `on_disconnect()` transition with
      `awaiting_outcome` flag distinguishes retry-failure from
      channel-storm events.
- [x] In the GUI tick (`update()` in `app.rs`), poll
      `ReconnectState::Pending` deadlines and trigger
      `reconnect()` when reached. Gated on
      `awaiting_reconnect_outcome` so a deadline-past frame
      doesn't re-fire `reconnect()` on every paint.
- [x] Wire `ChannelEvent::Disconnected` / `Error` handlers to
      transition Idle → Pending(1) — preserving the existing
      Phase 01 disconnect-snapshot call. Do not bypass the 60 s
      cooldown; auto-reconnect attempts that fail will mostly
      hit cooldown after the first.
- [x] Add status-bar "Reconnecting… (n/3)" widget in the
      bottom panel. Match the existing FPS/connected widget
      style.
- [x] Push a `NotifySeverity::Warn` notification on each
      attempt failure (source `NotificationSource::BugReport`
      to keep the producer set tidy). Fires for failures of
      attempts 1, 2, and 3 within a cluster.
- [x] Render the three modal variants from §A.6 — Step 5
      landed `Generic`; Step 6 added `OneShotConsumed`
      (Close only, "single-use ticket" body) and
      `TicketExpired { expired_at }` (Close only, "ticket
      expired at HH:MM:SS UTC" body). Dispatched on
      `ModalVariant` inside `ReconnectState::Modal(_)`.
- [x] Extend the .vv parser at `ryll/src/config.rs` to read
      `delete-this-file` (existing standard key) into a new
      `Config::ticket_is_single_use: bool` field. Plumbs
      through `Config::from_args` automatically — Config flows
      by value into `RyllApp` and is read via
      `RyllApp::reconnect_policy()`.
- [x] Extend the .vv parser to read the new
      `ticket-valid-until=<unix-ts>` extension key into
      `Config::ticket_valid_until: Option<SystemTime>`.
      Malformed values log a `warn!` and yield `None`; absent
      keys yield `None`. Parsing failure does not fail the
      connect.
- [x] When `ticket_is_single_use` is true, the auto-reconnect
      state machine refuses to enter `Pending`; first
      disconnect goes straight to `Modal(OneShotConsumed)` via
      `ReconnectPolicy::forbid_retry()`.
- [x] When `ticket_valid_until` is set and `SystemTime::now()
      >= expiry`, transition to `Modal(TicketExpired { expired_at })`
      both at disconnect time and at every Pending tick fire
      (so a long Pending window outliving the ticket
      short-circuits to Modal rather than firing a doomed
      reconnect).
- [x] Pre-disconnect warning: in the GUI tick, when
      `ticket_valid_until` is set and within 30 s of expiry
      (and notification not yet pushed for this session), push
      `NotifySeverity::Warn` "Session ticket expires in 30
      seconds." Latched via `RyllApp::ticket_expiry_warned`
      so the warning fires exactly once per session.
- [~] Edge case: `ticket_valid_until` set but in the future at
      disconnect time. Deviated from the plan's exact wording.
      Instead of rendering `TicketExpired` regardless, ryll
      lands in `Modal(Generic)` (since `forbid_retry()` returns
      `None` while the ticket is still valid by our clock) and
      logs a `warn!` "3 reconnect attempts failed but
      ticket-valid-until is still in the future ... possible
      clock skew or server-side issue independent of ticket
      expiry" when we land in Generic with a future expiry.
      Reason: ryll cannot detect "ticket expired" specifically
      from a disconnect — only the wall-clock comparison is
      available. Rendering `TicketExpired` for every disconnect
      on a ticketed session would mislabel real network
      failures.
- [x] In `RyllApp::reconnect()`, clear
      `MainSnapshot::keepalive_timeout_fired` (Phase 01 OQ #3
      done here, not in Phase 01).
- [x] Add `auto_reconnect_count: u32` for the bug-report
      pipeline (open question 5). Bump it on every transition
      into Pending. **Lives on `AppSnapshot` (session.json)**
      rather than the per-channel state JSON the plan originally
      named — auto-reconnect is session-level, not
      channel-level, so the session-summary file is the natural
      home.
- [x] State-machine unit tests in `app.rs`:
  - Idle → Pending(1) on first disconnect with correct
    backoff.
  - Idle → Pending(1) → Pending(2) → Pending(3) → Modal on
    three awaiting-outcome failures, latest_error tracked.
  - Storm-event idempotency: a non-awaiting second event while
    Pending returns None (state unchanged).
  - Cluster-reset window blocks retry within 5 min of Modal.
  - Cluster-reset window expires after 5 min — fresh Pending.
  - Modal ignores extra non-awaiting events.
  - Backoff array and MAX_ATTEMPTS pinned at [1, 4, 16] / 3.
- [ ] Cooldown and auto-reconnect interact correctly: each
      failed attempt within 60 s skips snapshot but continues
      attempting. (Snapshot cooldown is exercised by existing
      bugreport.rs tests; integration verification deferred to
      the manual check below.)
- [x] `delete-this-file=1` path: disconnect →
      `Modal(OneShotConsumed)` without entering Pending. Test:
      `ticket_single_use_skips_pending_and_lands_in_oneshot_modal`.
- [x] `ticket-valid-until` past: disconnect →
      `Modal(TicketExpired)` at disconnect time. Test:
      `ticket_expired_in_past_lands_in_ticket_expired_modal`.
      Tick-time mid-Pending expiry transition exercised in
      app code path (not a pure state-machine path; manual
      check in Step 7).
- [~] `ticket-valid-until` future: warning fires once at
      T-30 s. App-level latch via `ticket_expiry_warned`;
      pure-state test not feasible (it's a `update()` tick
      side effect, not a state-machine transition). Manual
      verification deferred to Step 7.
- [x] .vv parser: round-trips both keys; malformed
      `ticket-valid-until` logs warn and yields `None`. Tests:
      `vv_delete_this_file_1_sets_single_use`,
      `vv_delete_this_file_0_leaves_single_use_off`,
      `vv_ticket_valid_until_parses_unix_ts`,
      `vv_ticket_valid_until_malformed_logs_warn_and_yields_none`,
      `vv_ticket_valid_until_absent_yields_none`,
      `vv_defaults_have_ticket_fields_unset`.
- [x] Update README's ".vv configuration file" section with a
      "console.vv keys ryll honours" subsection covering
      ryll's interpretation of `delete-this-file=1` (skip
      auto-reconnect) and the new `ticket-valid-until`
      extension key, linking to the kerbside-wt-docs
      `console-vv-extensions.md` doc.
- [ ] Manual integration check (deferred to Step 7): kill
      SPICE server while connected with a regular .vv,
      observe three attempts then Generic modal. Repeat with
      `delete-this-file=1`, observe immediate OneShotConsumed
      modal. Repeat with a `ticket-valid-until` in the past,
      observe TicketExpired modal. Manual checks of the
      pre-expiry T-30s warning and clock-skew log line.

### Block B (analysis, no code)

- [ ] Reproduce K1 with Phase 01 build and capture at least
      one disconnect-cause.json zip. Document: idle scenario,
      time to disconnect, contents of `disconnect-cause.json`.
- [ ] Walk the decision tree above. Append a "Diagnosis"
      section to this plan with the chosen branch and
      evidence.

### Block C (selected: C.1; C.2 opportunistic, C.3 not applicable)

#### Block C.1 — Proactive client PING on every channel (selected)

(Scope expanded from the original "main only, every 10 s" to
"every channel, every 15 s when idle" per the Diagnosis.)

- [ ] Add `SPICE_MSGC_PING` builder in
      `shakenfist-spice-protocol/src/messages` (verify name —
      symmetric to the existing server `SPICE_MSG_PING`; if
      the client→server form is not present yet, add it).
- [ ] In **every channel handler**'s `tokio::select!` read
      loop, add a new branch driven by `last_recv_or_send +
      Duration::from_secs(15)`. On fire: send a client PING
      and reset the local timestamp. Channels:
  - `main_channel.rs` (lines around 212-313)
  - `display.rs`
  - `inputs.rs`
  - `cursor.rs`
  - `playback.rs`
  - `usbredir.rs`
  - `webdav.rs` (only when the channel is established —
    skip the PING branch otherwise)
- [ ] Add `client_ping_send_count: u32`,
      `last_client_ping_send_ts_secs: Option<f64>`, and
      `client_pong_recv_count: u32` to **every channel
      snapshot** in
      `shakenfist-spice-renderer/src/snapshots.rs`
      (MainSnapshot, DisplaySnapshot, InputsSnapshot,
      CursorSnapshot, PlaybackSnapshot, UsbredirSnapshot,
      WebdavSnapshot).
- [ ] Maintain the new counters: bump send-side in the new
      select branch; bump recv-side in the existing PONG
      handler (which today only counts server-PING / our-PONG —
      add the symmetric path for our-PING / server-PONG).
- [ ] Extend `PerChannelDiagnostics` and `DisconnectCause` in
      `ryll/src/bugreport.rs` to surface the three new fields,
      so a session-002b disconnect-cause.json shows whether
      proactive PING was firing on the channel that died.
- [ ] Cancel-safety: ensure the new select branch interacts
      cleanly with `Disconnected` — don't write to a closed
      socket. The existing send-error paths already handle
      this for user-driven traffic; the same shape applies
      to the proactive PING path.
- [ ] Unit tests:
  - The new select branch fires after 15 s of channel silence
    in either direction and updates the timestamp.
  - The branch does not fire when bytes are flowing (active
    receive resets the timer; active send resets the timer).
  - Round-trip: incoming server PONG bumps
    `client_pong_recv_count`.

#### Block C.2 — Disable App Nap on macOS (opportunistic only)

(Demoted from "selected" per the Diagnosis. Implement only if a
session-002b reproduction after C.1 + Block A still shows
disconnects, or as standalone macOS hardening once Phase 02 is
otherwise complete.)

- [ ] If implemented: per the design in the Approach section
      above. Tasks unchanged from earlier draft (objc2-based
      `begin_user_activity()` guard module under
      `#[cfg(target_os = "macos")]`, README macOS section,
      manual overnight integration check).

#### Block C.3 — Server-side close investigation (not selected)

The session-001b data fits the K1 hypothesis. C.3 would only
be invoked if a future reproduction *invalidates* the
hypothesis. No tasks here.

### Block D (independent, lands with C-block)

- [ ] Extend the client-side keepalive timeout at
      `main_channel.rs:219` from 30 s to 90 s. Add a comment
      explaining the change ("backstop for dead/unreachable
      server, not a primary mechanism — the server's own check
      is at 30 s and the rcc disconnect message is more
      informative than our local timer").
- [ ] Update Phase 01's
      `test_collect_per_channel_round_trips_keepalive_and_traffic`
      assertion if it referenced 30 s anywhere (grep — it
      shouldn't, but verify).

### Block E (`ChannelEvent::Error` attribution, independent of A/B/C/D)

- [ ] Change `ChannelEvent::Error(String)` to
      `ChannelEvent::Error { channel: ChannelType, message: String }`
      in `shakenfist-spice-renderer/src/channels/mod.rs:174`.
- [ ] Update `channels/inputs.rs:239` to construct the new
      variant with `ChannelType::Inputs`; drop the `"inputs: "`
      message prefix.
- [ ] Pair each channel `JoinHandle` with its `ChannelType` in
      `session.rs`:
  - `let mut handles = vec![(ChannelType::Main, main_handle)];`
    at line 143.
  - Adjust every `handles.push(tokio::spawn(...))` site (lines
    174, 191, 210, 232, 258, 283) to push the tuple.
  - `abort_handles` at line 303 maps `(_, h) => h.abort_handle()`.
  - Wait loop at line 322 destructures and forwards the channel
    type into `ChannelEvent::Error { channel, message }`.
- [ ] Update the headless consumer at `session.rs:517` to log
      the channel name.
- [ ] Update `ryll/src/app.rs:1146` to destructure
      `{ channel, message }`; pass `channel.name()` to
      `maybe_write_disconnect_snapshot` and embed it in
      `disconnect_reason`.
- [ ] Update the two doc comments in `ryll/src/bugreport.rs`
      (lines 638, 716) that describe the now-impossible
      "no channel attribution" case.
- [ ] Verify with `make build`, `make lint`, `make test`.

### Wrap-up

- [x] Update `ARCHITECTURE.md`: added "Auto-reconnect with
      backoff" and "Modal variants and console.vv ticket
      keys" sections following the "Auto-snapshot on channel
      disconnect" section. Describes the state machine, the
      three modal variants, `ReconnectPolicy`, the pre-expiry
      warning, and links to the companion
      `console-vv-extensions.md` doc. The C.1 proactive PING
      and C.2 App Nap opt-out sections noted in the original
      plan are not applicable — both were demoted to "not
      pursued" once K1 was resolved at the root in commit
      `370d8ce5`.
- [x] Update `AGENTS.md` with the new `ReconnectState`
      pattern (§22, the slot after the §21 notifications
      entry). Covers the pure-transition / side-effects-at-
      call-site split, the `awaiting_outcome` flag, the three
      modal variants, and the `ReconnectPolicy` short-circuit
      path.
- [x] Update `PLAN-session-001-feedback.md` Execution table
      status for Phase 02 → Done.
- [ ] Manual integration check (deferred operator action,
      not a code task): with a real SPICE server, exercise
      all three modal paths and verify the T-30s
      pre-expiry warning and the clock-skew log line fire as
      documented in `console-vv-extensions.md`. Bundled here
      for visibility — see "Phase 02 manual verification
      notes" at the bottom of this document for a checklist.

## Companion docs

This phase adds the first ryll-defined console.vv extension
key (`ticket-valid-until`) and ascribes a non-spec interpretation
to a standard key (`delete-this-file=1` → skip auto-reconnect).
Both must be discoverable to producers who want their .vv
files to drive ryll's behaviour correctly.

A new doc lives in the **kerbside-wt-docs** worktree at
`/home/mikal/src/shakenfist/kerbside-wt-docs/docs/spice/console-vv-extensions.md`
(committed alongside the existing protocol docs `channel-protocols.md`
and `spice-link-protocol.md`). The doc covers:

- A short preamble explaining what console.vv is and why ryll
  documents extensions separately (the standard format has no
  registry, and ryll consumes some standard keys with stronger
  semantics than the spec requires).
- A "ryll's interpretation of standard keys" section documenting
  `delete-this-file=1` as a one-shot ticket signal (rationale
  + implication: ryll skips auto-reconnect).
- An "Extensions" section documenting `ticket-valid-until=<unix-ts>`
  with format, semantics, and ryll's behaviour when set / unset.
- A "How to support these in your producer" section with sample
  console.vv content that Kerbside / oVirt operators can paste.
- A "Future extensions under consideration" section so this
  doc is the obvious place to discuss new keys.

Filing RFEs against producers (Kerbside, oVirt) once the doc
exists is part of this phase's wrap-up but not blocking —
ryll's day-one behaviour without producer changes is correct
because absent keys are no-ops.

## Out of scope

- Reconnect with a fresh ticket. Requires conductor /
  gateway-side support not currently available; see open
  question 1.
- Surfacing non-critical channel disconnects (cursor /
  playback / usbredir / webdav) to the user beyond the existing
  Phase 01 snapshot. That is **Phase 09** (F1 — connection
  events in the notifications pane).
- Per-channel auto-reconnect — once a channel drops mid-session
  under one-shot tickets, it cannot be re-linked, so per-
  channel retry is wasted effort. Whole-session reconnect (this
  phase) is the only meaningful retry granularity.
- Implementing the wider standard-virt-viewer-keys parity gap
  (`title`, `fullscreen`, `disable-channels`, `secure-channels`,
  `enable-usbredir`, `proxy`, etc. — see `config.rs:266`).
  ryll's .vv parser today reads only host/port/tls-port/password/
  ca/host-subject. That gap deserves its own master plan with
  the standard-key compat as the framing; tangling it into K1
  conflates two unrelated motivations (reconnect correctness
  vs. .vv compat). This phase adds only the two keys it needs.
- Producer-side changes (Kerbside / oVirt emitting
  `ticket-valid-until`). Tracked as RFEs after the
  console-vv-extensions.md doc lands, not implemented here.
- Changes to the channel teardown semantics (Disconnected
  event → loop break). The signal flow is fine; only the
  disconnect *response* changes.
- Telemetry beyond the channel-state JSON's
  `auto_reconnect_count`. A persistent metrics store is its
  own master plan if we ever need it.
- macOS Idle Sleep prevention (`IOPMAssertion…`). Different
  problem, different opt-in, different lifecycle. Mentioned in
  C.2 only to clarify it is *not* what App Nap opt-out covers.
- Linux / Windows equivalents to App Nap. Linux has no
  equivalent; Windows has connected-standby restrictions but
  ryll has not been observed to hit them. Revisit only if
  reproduced.

## Phase 02 manual verification notes

The state-machine paths are unit-tested (see
`app.rs::tests::reconnect_*` and `ticket_*`), but the
end-to-end UX needs a real SPICE server to verify the modal
copy, button layout, and notification timing. This checklist
is intentionally low-ceremony — tick boxes against a real
session, not a CI run.

1. **Generic modal — auto-retry exhaustion.**
   - Connect with a reusable .vv (no `delete-this-file`, no
     `ticket-valid-until`).
   - Once session is live, kill the SPICE server (e.g. `virsh
     destroy <domain>`).
   - Expected: status bar shows "Reconnecting… (1/3)" within
     ~1 s; updates to (2/3) at ~5 s; (3/3) at ~21 s. A `Warn`
     notification fires per attempt failure (visible in the
     notifications side panel via the bell).
   - At ~21 s the modal opens with title "Connection lost"
     and body "Three automatic reconnect attempts failed:
     …". Buttons: **Reconnect**, **Close**.
   - Click Reconnect: the modal closes, status bar shows
     "Reconnecting… (1/3)" again (cluster reset because of
     manual intervention).

2. **OneShotConsumed modal — single-use ticket.**
   - Connect with `delete-this-file=1` in the .vv.
   - Once session is live, drop the connection (server side
     or `iptables` on the host).
   - Expected: status bar does **not** show "Reconnecting…"
     at all. The modal opens immediately, title "Session
     ended — cannot reconnect", body "This connection used
     a single-use ticket. …". Buttons: **Close** only (no
     Reconnect button).

3. **TicketExpired modal — `ticket-valid-until` elapsed.**
   - Connect with `ticket-valid-until=<unix-ts in past>` in
     the .vv. (The server has to accept the link, since the
     server's own ticket validation is independent. For a
     test fixture, set `ticket-valid-until` to a few seconds
     after `now` so the link succeeds but the deadline passes
     during the session.)
   - Wait for the deadline to pass while connected; nothing
     visible should change yet (`ticket-valid-until` is only
     consulted at disconnect / Pending tick).
   - Drop the connection.
   - Expected: the modal opens immediately, title "Session
     ended — ticket expired", body "The ticket for this
     session expired at HH:MM:SS UTC. …". Buttons: **Close**
     only.

4. **Pre-expiry T-30s warning.**
   - Connect with `ticket-valid-until=<unix-ts at now+90s>`.
   - Wait ~60 s.
   - Expected: at T-30s, exactly **one** `Warn` notification
     pushes "Session ticket expires in 30 seconds." Confirm
     by opening the notifications panel — only one entry,
     not a stream of duplicates as the deadline approaches.

5. **Clock-skew log line.**
   - Connect with `ticket-valid-until=<unix-ts in distant
     future>` (a day from now is fine).
   - Kill the SPICE server and let auto-reconnect exhaust
     its three attempts.
   - Expected: the modal that opens is `Generic` (not
     `TicketExpired`, since our clock says the ticket is
     still good). Inspect logs for the `warn!` line "3
     reconnect attempts failed but ticket-valid-until is
     still in the future …". This is the diagnostic hook
     for the scenario where the server invalidates a ticket
     before its declared expiry (server-side revocation,
     clock skew, etc.).

The expected outputs above match what the Step 5 and Step 6
unit tests assert at the state-machine level; this checklist
just confirms the GUI surfaces match.
