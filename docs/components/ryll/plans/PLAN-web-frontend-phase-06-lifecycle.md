# Phase 6: Reconnect and lifecycle

## Prompt

Before responding to questions or making changes, explore the
codebase. Read the master plan at
`docs/plans/PLAN-web-frontend.md` (especially the Phase 6
section in the Execution table) and the Phases 1–5 plans plus
their execution histories. Key files for this phase:

- `shakenfist-spice-webrtc/src/bridge.rs` — `WebrtcBridge`
  with `on_peer_connection_state_change` already wired to fire
  `EncoderControl::RequestKeyframe` on `Connected`. Phase 6
  extends this to also signal "bridge dead" on the terminal
  states (`Failed` / `Disconnected` / `Closed`).
- `ryll/src/web/server.rs` — `WebState` carries
  `bridge_slot: Arc<Mutex<Option<WebrtcBridge>>>`,
  `encoder: Arc<Mutex<EncoderInfra>>`,
  `opus_active_tx: Arc<Mutex<Option<mpsc::Sender<...>>>>`.
- `ryll/src/web/signalling.rs` — `EncoderInfra::restart` is
  the canonical "stop existing pipeline and rebuild" entry
  point. Phase 6 reuses this from a server-side reaper task.
- `ryll/src/main.rs::run_web` — the run-loop that owns the
  HTTP server, the SPICE session, the surface mirror, the
  cursor/audio relays, and the SHUTDOWN_REQUESTED → cancel
  bridge.
- `ryll/src/web/assets/app.js` — current state from Phases
  4–5. Detects ICE-failed and disconnected via
  `pc.oniceconnectionstatechange` but only updates the status
  text. Phase 6b adds auto-reconnect with backoff.
- The existing graceful-shutdown wiring: `ctrlc::set_handler`
  in `main.rs` raises `SHUTDOWN_REQUESTED`; `axum::serve`
  drains via `with_graceful_shutdown(shutdown_signal())`
  (commit `10c09b11`); `run_connection` polls its
  `cancel: Arc<AtomicBool>` and exits cleanly.

External: WebRTC `RTCPeerConnectionState` enum (`new`,
`connecting`, `connected`, `disconnected`, `failed`,
`closed`). Browser's `RTCPeerConnection.iceConnectionState`
is a separate but correlated state machine.

Flag any uncertainty rather than guessing.

## Goal

Make `--web` mode survive browser disconnects gracefully,
reap dead bridges proactively (so the encoder stops burning
CPU when no viewer is watching), and shut down cleanly on
SIGTERM / Ctrl-C with no dangling state.

After Phase 6:

- Closing the browser tab causes the server to reap the
  bridge + encoder + audio pump within ~1 second; CPU usage
  drops to idle. The SPICE session stays alive (master plan
  Resolution: PeerConnection-layer reconnect, not
  SPICE-channel reconnect).
- Re-opening the same URL within any time window completes a
  new offer/answer round-trip; the new bridge sees the
  current SPICE state (a forced keyframe arrives within the
  first frame).
- The browser-side JS auto-reconnects on transient ICE /
  connection-state failures with exponential backoff (1 s,
  2 s, 4 s, 8 s, capped at 16 s, max 5 attempts then a
  manual "Reconnect" button).
- SIGTERM and Ctrl-C trigger the existing axum graceful
  shutdown plus an explicit bridge close, so the WebRTC
  stack tears down cleanly (no dangling DTLS/SRTP state on
  the host).
- An integration test in the webrtc crate exercises the
  PC-drop path: client closes its PC, server's bridge
  reaper observes the terminal state, the bridge slot
  empties; a subsequent offer succeeds.

Out of scope:

- Multi-viewer support (one viewer at a time stays the MVP).
- Session resumption across `ryll` process restarts
  (operator restarts ryll = new SPICE session; that's by
  design).
- Idle-timeout-tear-down-of-SPICE-session (a Jupyter-style
  "no viewer for 30 minutes, exit" knob — useful for
  multi-tenant deployments but out of MVP).
- Token rotation across reconnects (the per-launch token
  remains stable for the lifetime of the ryll process).

## Scope

In:

- A "bridge dead" signal channel on `WebrtcBridge`: a
  `tokio::sync::Notify` (or a `tokio::sync::watch::Sender<bool>`)
  that fires when the PC's connection state reaches a
  terminal value. Subscribed to by a server-side reaper.
- A reaper task in `run_web` that watches the active
  bridge's "dead" signal; when raised, takes the bridge out
  of `bridge_slot`, closes it cleanly, stops the encoder via
  `EncoderInfra::restart`-equivalent (or a new
  `EncoderInfra::stop` helper), and clears
  `opus_active_tx`. The SPICE session is left untouched.
- Explicit bridge close in the shutdown path. After
  `axum::serve` returns (graceful shutdown completed), close
  any bridge still in the slot before letting `run_web`'s
  tokio runtime drop.
- Browser-side auto-reconnect with exponential backoff. On
  ICE-failed or connection-state-failed, reset the
  `RTCPeerConnection`, wait the backoff, retry the
  `connect()` flow. Status overlay updates so the operator
  sees what's happening.
- Integration test in `shakenfist-spice-webrtc/tests/loopback.rs`
  (or a new `lifecycle.rs` test): drive the offer/answer
  flow, then close the client PC, then assert the server
  bridge reaches the terminal state and observable side
  effects (e.g., `bridge_slot` is empty).
- Documentation: parity matrix update if any user-visible
  reconnect behaviour changed; ARCHITECTURE.md note on the
  bridge lifecycle; README mention of auto-reconnect.

Out:

- All items listed in "Out of scope" above.
- Multi-viewer / broadcast-channel encoder model (master
  plan Future work).
- The PCM → Opus encoder fallback (Phase 5e deferred this).

## Approach

### Bridge "dead" signal

Add to `shakenfist-spice-webrtc/src/bridge.rs`:

```rust
pub struct WebrtcBridge {
    // ... existing fields ...
    dead: Arc<Notify>,
    dead_flag: Arc<AtomicBool>,
}

impl WebrtcBridge {
    pub async fn new(config: WebrtcBridgeConfig) -> Result<Self> {
        // ... existing setup ...

        let dead = Arc::new(Notify::new());
        let dead_flag = Arc::new(AtomicBool::new(false));

        // Extend the existing connection-state callback.
        let encoder_control_for_cb = config.encoder_control.clone();
        let dead_cb = dead.clone();
        let dead_flag_cb = dead_flag.clone();
        pc.on_peer_connection_state_change(Box::new(move |state| {
            let encoder_control = encoder_control_for_cb.clone();
            let dead = dead_cb.clone();
            let dead_flag = dead_flag_cb.clone();
            Box::pin(async move {
                use webrtc::peer_connection::peer_connection_state::RTCPeerConnectionState::*;
                match state {
                    Connected => {
                        let _ = encoder_control.send(EncoderControl::RequestKeyframe).await;
                    }
                    Failed | Disconnected | Closed => {
                        if !dead_flag.swap(true, Ordering::SeqCst) {
                            tracing::info!("WebrtcBridge: PC reached {:?}, signalling dead", state);
                            dead.notify_waiters();
                        }
                    }
                    _ => {}
                }
            })
        }));

        Ok(Self { /* ... */ dead, dead_flag })
    }

    /// A future that resolves when the PC reaches a terminal
    /// state (Failed, Disconnected, or Closed). Used by the
    /// server-side reaper to proactively tear down the bridge
    /// + encoder when the browser disconnects.
    ///
    /// The future resolves at most once per bridge (the dead
    /// flag is sticky). Calling this after the bridge is
    /// already dead returns immediately.
    pub async fn wait_for_dead(&self) {
        if self.dead_flag.load(Ordering::SeqCst) {
            return;
        }
        self.dead.notified().await;
    }
}
```

`Notify` semantics: `notify_waiters()` wakes all currently-
waiting futures but does NOT queue notifications for
late subscribers. The `dead_flag` AtomicBool covers the
late-subscriber case (callers that check the flag first).

The reason `Disconnected` is treated as terminal here (vs.
the WebRTC convention that `disconnected` can recover):
for our single-viewer MVP, treating it as terminal means we
reap aggressively and let the browser-side auto-reconnect
build a fresh PC. That's simpler than juggling a "wait and
see" timer. If a future operator complains that brief
network hiccups force a full reconnect, we revisit.

### Server-side reaper

In `ryll/src/web/server.rs` or a new `lifecycle.rs`:

```rust
//! Bridge reaper: watches the active bridge for terminal PC
//! state and tears down the bridge + encoder when observed.
//! The SPICE session (run_connection) is left untouched.

pub async fn run_bridge_reaper(state: Arc<WebState>) {
    loop {
        // Acquire a clone of the active bridge's "dead" signal
        // without holding the slot lock for long.
        let dead_handle: Option<Arc<tokio::sync::Notify>> = {
            let slot = state.bridge_slot.lock().await;
            slot.as_ref().map(|b| b.dead_handle())
            // dead_handle() is a new method that returns
            // Arc<Notify> for external waiters; cheaper than
            // exposing &Notify under the lock.
        };

        let Some(dead) = dead_handle else {
            // No active bridge; sleep and re-check.
            tokio::time::sleep(Duration::from_millis(500)).await;
            continue;
        };

        dead.notified().await;
        tracing::info!("bridge reaper: bridge died, reaping");

        // Take the bridge out of the slot, close it, and stop
        // the encoder. Use EncoderInfra::stop() (a new helper)
        // rather than restart() because we want to release
        // resources, not rebuild.
        let bridge = {
            let mut slot = state.bridge_slot.lock().await;
            slot.take()
        };
        if let Some(b) = bridge {
            let _ = b.close().await;
        }
        {
            let mut enc = state.encoder.lock().await;
            enc.stop().await;  // new helper; sends Stop, awaits handle
        }
        {
            let mut tx = state.opus_active_tx.lock().await;
            *tx = None;
        }

        tracing::info!("bridge reaper: reaped; awaiting next viewer");
    }
}
```

Race conditions to watch:

- A new `/offer` arrives between the reaper noticing the
  dead signal and the reaper acquiring the bridge_slot
  lock. The new offer's `post_offer` handler also tries to
  take the bridge and replace it. Both serialise on the
  bridge_slot mutex. Whichever fires first takes the bridge;
  the other observes `slot.take()` returning `None` and
  proceeds with no-op.
- The reaper fires on a Disconnected state that recovers
  (rare under our aggressive policy but possible). The next
  /offer rebuilds. No data loss; minor cost (encoder
  restart) is acceptable.

### `EncoderInfra::stop` helper

Add a stop variant alongside the existing restart:

```rust
impl EncoderInfra {
    /// Stop the active encoder task without restarting. Used
    /// by the bridge reaper when no immediate replacement is
    /// expected.
    pub async fn stop(&mut self) {
        if let Some(tx) = self.control_tx.take() {
            let _ = tx.send(EncoderControl::Stop).await;
        }
        if let Some(h) = self.handle.take() {
            let _ = tokio::time::timeout(Duration::from_secs(2), h).await;
        }
    }
}
```

`restart()` is essentially `stop()` followed by a fresh
spawn. Refactor if cleanest, otherwise leave them parallel.

### Shutdown sequence in `run_web`

The existing shutdown path is:

1. Ctrl-C → `SHUTDOWN_REQUESTED.store(true)`
2. The bridge between `SHUTDOWN_REQUESTED` and the SPICE
   `cancel: Arc<AtomicBool>` flips the cancel.
3. axum's `with_graceful_shutdown` drains.
4. `axum::serve(...).await` returns.
5. `run_web` exits, runtime drops, all tasks abort.

Phase 6 inserts an explicit bridge close between (4) and (5):

```rust
// existing: axum::serve(...).with_graceful_shutdown(...).await?;
tracing::info!("web: HTTP server drained");

// 6 addition: close any active bridge cleanly so DTLS/SRTP
// tears down before the runtime drops.
let bridge = {
    let mut slot = state.bridge_slot.lock().await;
    slot.take()
};
if let Some(b) = bridge {
    tracing::info!("web: closing active bridge before exit");
    let _ = b.close().await;
}
{
    let mut enc = state.encoder.lock().await;
    enc.stop().await;
}

// then the SPICE-side cancel/runtime shutdown takes over.
```

### Browser-side auto-reconnect

In `app.js`, add:

```javascript
const RECONNECT_BACKOFFS_MS = [1000, 2000, 4000, 8000, 16000];
let reconnectAttempt = 0;

function scheduleReconnect() {
    if (reconnectAttempt >= RECONNECT_BACKOFFS_MS.length) {
        setStatus("Disconnected. Click to reconnect.");
        showReconnectButton();
        return;
    }
    const delay = RECONNECT_BACKOFFS_MS[reconnectAttempt++];
    setStatus(`Reconnecting in ${delay/1000}s (attempt ${reconnectAttempt})…`);
    setTimeout(() => {
        resetPeerConnection();
        connect().catch(err => {
            console.warn("[ryll] reconnect attempt failed:", err);
            scheduleReconnect();
        });
    }, delay);
}

function resetPeerConnection() {
    if (pc) {
        try { pc.close(); } catch (e) {}
    }
    pc = new RTCPeerConnection();
    // Re-create the seed DC and re-wire ontrack / oniceconnection / etc.
    // Refactor the existing setup into an init function so this can call
    // it cleanly.
}

pc.oniceconnectionstatechange = () => {
    console.log("[ryll] ICE state:", pc.iceConnectionState);
    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {
        scheduleReconnect();
    }
};

pc.onconnectionstatechange = () => {
    if (pc.connectionState === "connected") {
        // Reset the backoff counter on successful connect.
        reconnectAttempt = 0;
    } else if (pc.connectionState === "failed") {
        scheduleReconnect();
    }
};
```

The "Click to reconnect" button is a small UI addition: a
hidden button revealed on max-attempts. Clicking it resets
`reconnectAttempt = 0` and calls `scheduleReconnect()`.

For the JS, the existing IIFE needs to be refactored: the
PC setup, transceiver registration, DC creation, and offer
flow become a `connect()` function that can be re-invoked.
The viewport-on-connect message also needs to retrigger; the
input listeners stay registered (they target document /
videoEl, which don't change).

### Integration test

Add `shakenfist-spice-webrtc/tests/lifecycle.rs`:

```rust
//! Phase 6 integration test: close the client PC, observe
//! the server bridge's wait_for_dead future resolve, verify
//! the bridge reaches a terminal state.

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn pc_close_signals_dead() {
    let _ = rustls::crypto::ring::default_provider().install_default();

    // Build server bridge.
    let (server_enc_tx, _) = mpsc::channel::<EncoderControl>(4);
    let server = WebrtcBridge::new(WebrtcBridgeConfig {
        ice_servers: vec![],
        encoder_control: server_enc_tx,
    }).await.expect("server bridge");

    // Build client PC and complete the SDP exchange (mirror
    // the loopback test pattern).
    // ... ICE handshake, server.accept_offer, client.set_remote_description ...

    // Wait for both sides to reach Connected.
    // ... existing loopback timeout pattern ...

    // Now close the client PC; verify the server's
    // wait_for_dead resolves within ~5 seconds.
    client_pc.close().await.expect("client close");
    let dead = tokio::time::timeout(
        Duration::from_secs(5),
        server.wait_for_dead(),
    ).await;
    assert!(dead.is_ok(), "server bridge did not observe terminal state");

    server.close().await.ok();
}
```

The reaper itself doesn't get an integration test in the
webrtc crate (the reaper lives in ryll and depends on
`WebState`); add a unit test in `ryll/src/web/lifecycle.rs`
or skip — the loopback-style test for `wait_for_dead` is the
key insurance.

## Prerequisites

- Phase 5 complete on `thought-bubble`. (It is — last
  commit `21205a50`.)

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a   | high   | opus  | worktree | Add the "bridge dead" signal to `WebrtcBridge`. New fields `dead: Arc<Notify>`, `dead_flag: Arc<AtomicBool>`. Extend the existing `on_peer_connection_state_change` callback to fire `notify_waiters()` on `Failed`/`Disconnected`/`Closed` (using the swap-and-check pattern so we only fire once). Add `pub async fn wait_for_dead(&self)` and `pub fn dead_handle(&self) -> Arc<Notify>`. Add the unit test from the plan's "Integration test" section as a `tests/lifecycle.rs` integration test. Single commit. |
| 6b   | high   | opus  | worktree | Add `EncoderInfra::stop` (parallels `restart` but doesn't respawn). Add the bridge reaper task in `ryll/src/web/lifecycle.rs` (new module). Spawn it from `run_web` after `web::run` is set up. Wire the explicit-bridge-close into the shutdown path in `run_web`: after `axum::serve` returns, take the bridge from the slot, close it, and call `EncoderInfra::stop`. Add a unit test for `EncoderInfra::stop` (analogous to the existing restart tests). Single commit. |
| 6c   | medium | sonnet | none     | Browser-side auto-reconnect. Refactor `app.js`'s existing IIFE so the PC setup is a callable `connect()` function. Add `scheduleReconnect()` with the backoff schedule (1s/2s/4s/8s/16s, max 5 attempts). Add a "Click to reconnect" button revealed on max-attempts. Update the status overlay's text on each transition. Bump the JS file size — verify no test asserts an exact size. Single commit. |
| 6d   | medium | sonnet | none     | Documentation. Update `docs/web-frontend.md` with the auto-reconnect behaviour and the "browser tab close → seamless reopen" experience. Update `ARCHITECTURE.md` with a paragraph on the bridge lifecycle (dead-signal → reaper → bridge close + encoder stop, SPICE session unaffected). Flip Phase 6 in the master plan execution table from "Not started" to "Complete". Update the index.md status line. Single commit. |

After 6d, Phase 6 is done. The web frontend gracefully
handles browser disconnects, auto-reconnects on transient
failures, reaps bridges proactively to release CPU, and
shuts down cleanly.

## Step details

### Step 6a expanded brief

The `Notify` + `AtomicBool` pair handles three subtleties:

1. **Late subscribers.** `Notify::notify_waiters()` only
   wakes currently-waiting futures. A consumer that calls
   `wait_for_dead()` AFTER the PC has already died would
   wait forever. The flag check at the top of
   `wait_for_dead()` returns immediately in that case.
2. **Multiple terminal-state transitions.** A PC could go
   through `Disconnected` → `Closed` (some implementations).
   The `swap(true, ...)` pattern fires the notify only on
   the first transition.
3. **Cancellation safety.** `notify.notified().await` is
   cancellation-safe; if the awaiting future is dropped, no
   leak.

The unit test for 6a should:
- Build a server bridge and a client PC.
- Drive SDP to Connected (mirror loopback.rs).
- Close the client PC.
- Assert `server.wait_for_dead()` resolves within 5 s.
- Verify a second call returns immediately (test
  late-subscriber path explicitly).

### Step 6b expanded brief

The reaper's lock dance is the trickiest piece. Verify
through a small unit test:

```rust
#[tokio::test]
async fn reaper_clears_slot_when_bridge_dies() {
    let state = build_test_state();  // helper
    let bridge = build_test_bridge();  // helper
    state.bridge_slot.lock().await.replace(bridge);

    // Start the reaper.
    let reaper = tokio::spawn(run_bridge_reaper(state.clone()));

    // Manually fire the bridge's dead signal.
    state.bridge_slot.lock().await.as_ref().unwrap()
        .dead_handle().notify_waiters();
    // ...wait briefly...
    tokio::time::sleep(Duration::from_millis(200)).await;

    // Slot should be empty.
    assert!(state.bridge_slot.lock().await.is_none());
    reaper.abort();
}
```

The shutdown sequence in `run_web`: after `axum::serve`
returns (which happens after Ctrl-C), take the bridge and
close it. Be careful: the runtime is in shutdown mode at
this point; `tokio::time::timeout` and other primitives still
work but we don't want to block forever. Use a 2-second
ceiling on `bridge.close().await` and `enc.stop().await`.

### Step 6c expanded brief

The JS refactor is non-trivial because the existing IIFE
captures `pc`, `dc`, `videoEl`, `cursorEl`, etc. in closure.
After 6c the structure is:

```javascript
let pc;
let dc;

function init() {
    pc = new RTCPeerConnection();
    dc = pc.createDataChannel("control-seed", { ordered: true });
    // Wire dc.onopen, dc.onmessage, pc.ontrack, pc.oniceconnectionstatechange,
    // pc.onconnectionstatechange. The latter two trigger scheduleReconnect()
    // on terminal states. Document-level keydown/keyup and videoEl mouse
    // listeners stay registered across reconnects (they don't reference pc).
}

async function connect() {
    init();
    // ... existing offer flow ...
}

connect().catch(err => {
    console.error("[ryll] initial connect failed:", err);
    scheduleReconnect();
});
```

Test by reading the rendered JS body in a unit test
(equivalent to the existing `app_js_reads_token_from_url`
test): assert it contains `scheduleReconnect`, `connect`,
some backoff numeric like `1000`, etc.

### Step 6d expanded brief

The docs flips are mechanical. Pay attention to:

- `docs/multi-mode-parity.md`: the "Reconnect/Lifecycle"
  section's web column. Currently those rows say "missing"
  or "out of MVP". Phase 6 makes "Reconnect-on-disconnect
  (PC drop preserves SPICE session)" available.
- `ARCHITECTURE.md`: extend the Phase 5 section with a
  Phase 6 lifecycle paragraph.
- `README.md`: the multi-modal table — flip "In progress
  (Phases 0–5 of 8 complete)" to "0–6 of 8".

## Acceptance criteria

- `make lint` passes after each step.
- `make test` passes after each step.
- After 6a: `wait_for_dead()` integration test passes.
- After 6b: `EncoderInfra::stop` unit test passes; reaper
  unit test passes.
- After 6c: app.js auto-reconnect verified manually (close
  the browser network, observe the page status changes
  through the backoff schedule, restore network, observe
  reconnection).
- After 6d: parity matrix and master plan reflect Phase 6
  complete.
- `pre-commit run --all-files` passes.
- Each of 6a–6d is a single commit.

## Risks

- **`Disconnected` state recovery.** WebRTC PCs can recover
  from `Disconnected` back to `Connected`. Phase 6 treats
  `Disconnected` as terminal (reap aggressively). If a
  future operator hits a flaky-network use case where this
  causes spurious reconnects, the policy can be relaxed
  (require the state to stay terminal for N seconds before
  reaping). Document the trade-off.
- **Notify late-subscriber pitfalls.** The flag check is
  the safety net. Verify the unit test exercises both the
  "fires before subscribe" and "fires after subscribe"
  paths.
- **Race between reaper and `/offer` replacement.** Both
  serialise on `bridge_slot.lock()`; whichever takes the
  slot first wins. Verify the second one no-ops cleanly.
- **Browser auto-reconnect with stale SDP cache.** Some
  browsers cache failed PCs for a brief window. The JS
  refactor must construct a brand-new `RTCPeerConnection`
  each attempt, not reuse the old one.
- **Encoder stop timing.** The 2-second timeout on
  `EncoderInfra::stop()` can be hit if the encoder is
  mid-frame on a contended `try_lock`. The orphaned task
  exits naturally on the next send error. Acceptable.
- **Worktree base reset.** As ever, the first thing in
  worktree-isolated steps is
  `git fetch origin && git reset --hard thought-bubble`.

## Documentation updates

After 6d:

- `docs/web-frontend.md` — auto-reconnect behaviour,
  "browser tab close + reopen" experience.
- `ARCHITECTURE.md` — bridge-lifecycle paragraph (Phase 6).
- `AGENTS.md` — note the new `WebrtcBridge::wait_for_dead`
  / `EncoderInfra::stop` / web-mode reaper if it shows up
  in the Code Organisation tree.
- `docs/multi-mode-parity.md` — flip the relevant
  reconnect-related rows.
- `README.md` — multi-modal table progress marker.
- `docs/plans/PLAN-web-frontend.md` — Phase 6 row Complete.
- `docs/plans/index.md` — Phase 6 marker.

## Estimated total scope

Roughly 800–1100 lines across four commits. Heaviest in 6b
(reaper task + shutdown sequence + tests, ~400 LoC) and 6c
(JS refactor + reconnect logic, ~250). 6a is ~150 LoC of
trait extension + test. 6d is ~200 LoC of doc edits.

## Back brief

Before executing 6a, the implementing agent should
back-brief: which terminal states fire the dead signal
(`Failed` only, or `Failed` + `Disconnected` + `Closed`),
how the late-subscriber case is tested, and whether
`dead_handle()` returns `Arc<Notify>` or some other shape.

Subsequent steps follow the same pattern: back-brief first,
edit second.
