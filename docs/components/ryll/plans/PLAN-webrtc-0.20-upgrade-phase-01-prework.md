# webrtc-rs 0.20 upgrade — phase 01: pre-work on 0.17

Parent: [PLAN-webrtc-0.20-upgrade.md](/components/ryll/plans/PLAN-webrtc-0.20-upgrade/)

## Prompt

Every change in this phase compiles, tests, and ships against
webrtc-rs **0.17.1**. Nothing here bumps the dependency. The
purpose is to shrink phase 02 — which is an unavoidably large
atomic commit, because the crate does not build between the
version change and the end of the port — by moving as much work
as possible into commits that can be reviewed and landed
normally.

Two rules for anyone executing this phase:

1. **No behaviour changes.** Every step is a refactor. The answer
   SDP, the RTP output, and the lifecycle signals must be
   identical before and after. Where that is hard to eyeball,
   the step says how to prove it.
2. **If a step cannot be done without touching 0.20 API surface,
   stop and move it to phase 02.** The value of this phase is
   entirely in it being landable today.

Recommended planning effort for this phase: **medium** — the
research is front-loaded into this document. Recommended effort
per step is in the step table.

## Situation

### A correction to the master plan

The master plan states that `connection_state()` at
`bridge.rs:861` is a "state accessor used by the reaper". That is
wrong, and the error matters for how this phase is scoped.

`connection_state()` lives inside a `#[cfg(test)]` impl block
(`bridge.rs:832-866`) and is `pub(crate)`. Production code never
calls it. The reaper in `ryll/src/web/lifecycle.rs` uses
`dead_handle()` / `dead_flag_handle()` instead, which are backed
by the `Arc<Notify>` + `Arc<AtomicBool>` pair and do not touch
the peer connection at all.

So the production bridge does **not** need a connection-state
shadow. What needs one is the *test clients*, which call
`connection_state()` on a raw `RTCPeerConnection`
(`loopback.rs:238`, `:248`, `lifecycle.rs:165`, `:175`,
`bridge.rs:1166-1167`) — and that is exactly the call that
disappears from the trait in 0.20.

The master plan is corrected in the same commit as this file.

### The duplication nobody has had to care about until now

There are **four** near-identical client-side peer connection
setups in the tree:

| Site | Lines | Registers H.264 | Polls state | Has DC handler |
|---|---|---|---|---|
| `bridge.rs` in-file test | 904–948 | yes | yes (`:1166`) | no |
| `tests/loopback.rs` | 100–216 | yes (inline) | yes (`:238`) | yes (`:154`) |
| `tests/lifecycle.rs` | 88–140 | — | yes (`:165`) | no |
| `ryll/src/web/signalling.rs` | 434–492 | — | no | no |

Each one does some subset of: `MediaEngine::default()`,
`register_default_codecs()`, H.264 registration, `Registry::new()`,
`register_default_interceptors()`, `APIBuilder`,
`new_peer_connection()`, two `add_transceiver_from_kind()` calls,
`create_offer()`, `set_local_description()`,
`gathering_complete_promise()`, `local_description()`, then a
poll loop on `connection_state()`.

Every one of those calls changes in 0.20. Left as-is, phase 02
rewrites this boilerplate four times, in two crates, with four
chances to get it subtly different — and the differences would be
invisible until an integration test flakes. Collapsing it to one
implementation first is the single highest-leverage thing this
phase does.

Note that `ryll/src/web/signalling.rs` is in a *different crate*,
so the shared helper has to be reachable across the workspace.

### What phase 02 still has to do afterwards

For calibration, after this phase lands, phase 02's remaining
work in `bridge.rs` is roughly: rewrite ~25 `use` statements,
swap `APIBuilder` for `PeerConnectionBuilder`, add
`.with_udp_addrs()`, add the `streams` field in three places, and
change one `impl` block from inherent methods to
`PeerConnectionEventHandler`. Plus the same treatment once in the
shared test helper instead of four times.

## Steps

### 1a — Capture the 0.17.2 performance baseline

Before touching anything. Phase 04 needs something to compare
against, and it cannot be captured after the bump.

Run a real `--web` session against a real SPICE guest for long
enough to reach steady state (20 minutes is enough), and record:
process RSS, per-thread CPU from the runtime metrics, the latency
HUD's distribution, and the video pump's dropped-packet debug
count. Record the commit SHA, the guest, the resolution, and the
browser alongside the numbers, because a baseline without its
conditions is not a baseline.

Write the numbers into this file under a new "Baseline" heading
rather than a scratch file, so they are still there when phase 04
runs.

### 1b — Promote `rtp` to a direct dependency

`bridge.rs:43-47` imports `H264Payloader`, `OpusPayloader`,
`Header`, `Packet` and `Payloader` through the `webrtc::rtp`
re-export, which does not exist in 0.20.

`shakenfist-spice-renderer` already depends on the standalone
crate directly (`rtp = "0.17"`, with a comment at
`shakenfist-spice-renderer/Cargo.toml:131-136` explaining why),
so this step just brings the webrtc crate into line with an
existing project decision.

Add `rtp = "0.17"` to `shakenfist-spice-webrtc/Cargo.toml`,
change the five imports from `webrtc::rtp::*` to `rtp::*`, and
rewrite the comment at `Cargo.toml:15-17` — it currently explains
that webrtc 0.17.1 is pinned because it re-exports a matching
`rtp`, which stops being the reason once we depend on `rtp`
directly.

Behaviour proof: the types are literally the same types; this is
a path change. `cargo tree -p shakenfist-spice-webrtc -i rtp`
should show one `rtp` version, not two.

### 1c — Extract the shared test-client helper

The big one. Create `shakenfist-spice-webrtc/src/test_client.rs`
behind an optional `test-support` feature, exposing a
`TestPeer` type that covers the union of what the four call sites
need:

- `TestPeer::builder()` with an opt-in seed datachannel. The
  sites also differ in whether they register H.264 — but that
  turned out to be unobservable (see "What landed" below), so
  the builder does not expose it.
- `offer_and_gather() -> Result<String>` — create offer, set
  local, wait for gathering, return the resolved SDP.
- `set_remote_answer(sdp)`.
- `wait_until_connected(timeout)`.
- Accessors for the raw `Arc<RTCPeerConnection>` so a site that
  needs something the helper does not cover (loopback's
  `on_track` counters) can still reach through.

`shakenfist-spice-webrtc` gets `test-support = []` in
`[features]`; `ryll` enables it on its dev-dependency.

Migrate all four call sites. `loopback.rs` keeps its `on_track`
counter wiring and DC echo handler locally — those are genuinely
test-specific — but gets its PC and its SDP dance from `TestPeer`.

Behaviour proof: `make test` passes, and the SDP each site
produces is unchanged. Capture one offer SDP per site before and
after and diff them; they should differ only in ICE ufrag/pwd,
SSRCs, and fingerprints, which are random per PC.

This step is worth doing carefully. If the helper ends up so
general that every call site passes a different combination of
flags, it has failed — prefer two small helpers over one
parameterised one.

#### What landed, and how it differed from this plan

Two things were wrong in the sketch above.

**The `#[cfg(test)]` impl on `WebrtcBridge` does not fold away.**
It serves a different purpose than client-PC emulation:
`control_datachannel_roundtrips_messages` drives a *bridge-to-
bridge* exchange, so `create_offer_and_gather`, `set_remote_answer`
and `connection_state` are needed on the bridge itself. It stays.
That means `WebrtcBridge::connection_state` still calls
`RTCPeerConnection::connection_state`, and step 1d cannot retire
that call on its own — step 1e picks it up instead, reading the
`BridgeEvents` shadow.

**The H.264 registration knob is unobservable, so it does not
exist.** Three sites registered H.264 explicitly and ryll's
signalling test did not, which looked like a genuine difference
worth a builder flag. It is not: `register_default_codecs()`
already registers H.264 at PT 102, and webrtc-rs drops
`register_h264`'s explicit re-registration as a duplicate payload
type. The two offers are byte-identical, asserted by
`register_h264_is_redundant_with_default_codecs`. The builder
therefore has exactly one knob — the seed datachannel — and
`TestPeer` always mirrors the bridge's registration.

That test also records a related discrepancy found on the way,
deliberately left alone: the default PT 102 entry carries
`profile-level-id=42001f` while `register_h264` asks for
`42e01f`, so the bridge negotiates one profile-level and its
encoder emits another. Browsers tolerate the constraint-set
difference. It predates this work and is not the port's problem,
but it is worth someone's attention eventually.

One further simplification fell out: with `signalling.rs` off
its hand-rolled peer, `ryll` has no direct `webrtc::` reference
left at all, so its `webrtc = "0.17.1"` dev-dependency is gone.
Phase 02 now has one manifest to bump instead of two.

### 1d — Shadow connection state in the helper

With 1c landed there is one place to change. Register
`on_peer_connection_state_change` inside `TestPeer::builder()`,
keep the latest state in an `Arc<Mutex<RTCPeerConnectionState>>`
(or an `AtomicU8` with a conversion, if clippy prefers it), and
have `wait_until_connected` read the shadow.

Behaviour proof: tests still pass, and — important — still
actually *wait*, rather than passing because the shadow defaults
to `Connected`. Assert the shadow starts at `New`.

Note that this does **not** by itself retire
`RTCPeerConnection::connection_state()` from the workspace, as
this plan originally claimed. `WebrtcBridge::connection_state`
(the `#[cfg(test)]` one, used by the bridge-to-bridge roundtrip
test) still calls it. Step 1e retires that one by reading the
`BridgeEvents` shadow, so the "no call sites remain" check
belongs at the end of 1e rather than here.

### 1e — Collapse the bridge's three callbacks into one struct

`bridge.rs` registers three callbacks at `:258`, `:312` and
`:328`, the last nesting a fourth at `:335`. Introduce a
`BridgeEvents` struct holding what they capture today —
`encoder_control: mpsc::Sender<EncoderControl>`,
`dead: Arc<Notify>`, `dead_flag: Arc<AtomicBool>`,
`incoming_tx: mpsc::Sender<Vec<u8>>` — with three async methods:

```
async fn on_state_change(&self, state: RTCPeerConnectionState)
async fn on_control_message(&self, data: Vec<u8>)
async fn on_remote_data_channel(&self, dc: Arc<RTCDataChannel>)
```

Keep registering them through the 0.17 closure API; each closure
becomes a two-line delegation to an `Arc<BridgeEvents>`. Phase 02
then adds `impl PeerConnectionEventHandler for BridgeEvents` and
deletes the closures — the bodies never move again.

Everything those closures capture is already constructed before
the PC exists (`dead` and `dead_flag` at `:247-248`, `incoming_tx`
at `:309`), so this does not reorder construction. It does mean
`control_dc.on_message` and the nested `remote_dc.on_message`
both delegate to the same `on_control_message`, which is already
the intent — the comment at `:295-308` says so explicitly.

Behaviour proof: the sticky-flag semantics are the subtle part.
`dead_flag.swap(true, ...)` at `:282` guards `notify_waiters()`
so only the first terminal transition fires. Keep that inside
`on_state_change` and keep `tests/lifecycle.rs` green — it
asserts both the first `wait_for_dead` resolving and the second
returning immediately via the fast path.

### 1f — Handler-driven ICE-gathering completion

The riskiest item in phase 02, de-risked here.

`accept_offer` (`:429-430`) calls `gathering_complete_promise()`,
which is gone from the 0.20 trait. 0.17 already has
`on_ice_gathering_state_change`, so the replacement design can be
built and validated *today*:

- Add `gathered: Arc<Notify>` + `gathered_flag: Arc<AtomicBool>`
  to `BridgeEvents`, following the same sticky pattern as
  `dead` / `dead_flag` — the reasoning at `:239-248` applies
  identically, and a late subscriber here would hang `accept_offer`
  forever.
- Raise them from a new `on_ice_gathering_state_change` handler
  when the state reaches `Complete`.
- Rewrite `accept_offer` to await that signal instead of the
  promise.
- Do the same for `TestPeer::offer_and_gather` (which is why 1c
  comes first).

Behaviour proof — and this one deserves real rigour, because a
subtly-early signal produces an SDP that is missing candidates
and fails only on some networks:

Run `accept_offer` 20 times before and after, and assert the
answer SDP contains the same number of `a=candidate:` lines each
time. Additionally assert that the gathering signal fires *after*
`local_description()` returns a description containing at least
one candidate — if `on_ice_gathering_state_change` can fire
before the local description is updated, that ordering bug exists
in 0.17 too and we want to find it now, not in phase 02.

### 1g — Re-measure and confirm no regression

Repeat 1a's measurement on the phase-01 tip. Steps 1b–1f are all
refactors, so the numbers should be within noise of the baseline.
If they are not, something in this phase changed behaviour and
the phase is not done.

## Step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | low | haiku | none | Run `ryll --web` against a real SPICE guest for 20 min, record RSS, per-thread CPU, latency HUD distribution, and video-pump drop count into a "Baseline" section of `docs/plans/PLAN-webrtc-0.20-upgrade-phase-01-prework.md`, along with commit SHA, guest, resolution and browser. Do not change any code. |
| 1b | low | sonnet | none | Add `rtp = "0.17"` to `shakenfist-spice-webrtc/Cargo.toml`; change the five imports at `bridge.rs:43-47` from `webrtc::rtp::*` to `rtp::*`; rewrite the now-stale comment at `Cargo.toml:15-17`. Mirror the existing declaration in `shakenfist-spice-renderer/Cargo.toml:131-136`. Verify `cargo tree -p shakenfist-spice-webrtc -i rtp` shows a single version. |
| 1c | high | opus | worktree | Create `shakenfist-spice-webrtc/src/test_client.rs` behind a `test-support` feature exposing a `TestPeer` builder, and migrate all four client-PC setups to it: `bridge.rs:904-948`, `tests/loopback.rs:100-216`, `tests/lifecycle.rs:88-140`, `ryll/src/web/signalling.rs:434-492`. Fold away the `#[cfg(test)]` impl at `bridge.rs:832-866`. `ryll` enables the feature on its dev-dependency. Sites keep their own `on_track` / DC-echo wiring. Prove offer SDPs are unchanged modulo per-PC randomness. Read the "Extract the shared test-client helper" section of this plan first — it explains what must stay configurable and warns against over-generalising. |
| 1d | medium | sonnet | none | Inside `TestPeer`, register `on_peer_connection_state_change` and shadow the latest state; make `wait_until_connected` read the shadow. Afterwards `grep -rn "\.connection_state()" --include="*.rs"` must show no calls on a raw `RTCPeerConnection`. Assert the shadow starts at `New` so the tests still genuinely wait. |
| 1e | high | opus | none | Introduce `BridgeEvents` in `bridge.rs` holding `encoder_control`, `dead`, `dead_flag`, `incoming_tx`, with async methods `on_state_change`, `on_control_message`, `on_remote_data_channel`. Reduce the closures at `:258`, `:312`, `:328` (and the nested one at `:335`) to delegations. Preserve the `dead_flag.swap` guard at `:282` exactly — `tests/lifecycle.rs` asserts both the first `wait_for_dead` resolving and the second taking the sticky fast path. Do not reorder construction. |
| 1f | high | opus | worktree | Add `gathered: Arc<Notify>` + `gathered_flag: Arc<AtomicBool>` to `BridgeEvents` using the same sticky pattern as `dead`/`dead_flag`; raise from a new `on_ice_gathering_state_change` handler on `Complete`; rewrite `accept_offer` (`bridge.rs:429-430`) and `TestPeer::offer_and_gather` to await it instead of `gathering_complete_promise()`. Validate per the "Behaviour proof" in this plan's 1f section — 20 runs, identical `a=candidate:` counts, and confirm the signal cannot fire before the local description carries candidates. |
| 1g | low | haiku | none | Repeat 1a's measurement on the phase-01 tip and append it beside the baseline. Flag any difference outside noise as a regression — every step in this phase is meant to be behaviour-preserving. |

Dependencies: 1a first. 1c before 1d and before 1f. 1e before 1f.
1b is independent and can go any time. 1g last.

## Status

| Step | State |
|------|-------|
| 1a — 0.17.2 baseline | Done — see "Baseline" below |
| 1b — `rtp` direct dependency | Done |
| 1c — shared `TestPeer` | Done |
| 1d — state shadow in `TestPeer` | Done |
| 1e — `BridgeEvents` | Done |
| 1f — handler-driven gathering | Done |
| 1g — re-measure | Done — agrees with 1a within noise |

Plus one unplanned commit: the `wait_for_dead` lost-wakeup fix
described under "Found during execution".

## Baseline

Captured 2026-08-13. Two 20-minute soaks under identical
conditions: 1a on the 0.17 tip (develop `ce740e26`, which
resolves webrtc 0.17.2 via lockfile maintenance; the manifest
still says 0.17.1), 1g on the phase-01 tip (`9ea7cfb0`). Phase 04
must reproduce these conditions to compare against these numbers.

| Metric | 1a (`ce740e26`) | 1g (`9ea7cfb0`) |
|--------|-----------------|-----------------|
| RSS start → end | 154 → 215 MB | 161 → 197 MB |
| RSS max | 226 MB | 197 MB |
| CPU, all threads, whole run | ~1.4% of one core | ~0.9% of one core |
| Video pump drops | 0 | 0 |
| Audio pump drops | 0 | 0 |
| Reaper events | 0 | 0 |
| ryll alive at end | yes | yes |
| Host CPU busy%, mean (max) | 8.3 (9) | 10.4 (29) |

> **Correction, made during phase 04 (2026-08-22).** The three
> "drops"/"events" rows above are not measurements. The conditions
> below record running with
> `RUST_LOG=info,shakenfist_spice_webrtc=debug,ryll=debug` because
> "the drop counters only log at debug" — but ryll does not read
> `RUST_LOG` at all. It selects `INFO` or `DEBUG` from `--verbose`
> and builds a plain `LevelFilter` (`ryll/src/main.rs:161-169`), and
> `RUST_LOG` appears nowhere in its history except inside a comment.
> Both runs were therefore at `INFO`, where those counters are not
> emitted, so the zeros mean "nothing logged" rather than "nothing
> dropped". The RSS and CPU rows are unaffected — they were sampled
> externally from `/proc`.

**Verdict: no regression.** The phase-01 tip is at-or-below the
baseline on both memory and CPU, and the differences are within
run-to-run noise at this load. Both runs show RSS growing through
the run (plausibly ring buffers and caches filling to their
caps); the growth is common to both and is itself part of the
baseline shape phase 04 should expect.

### Conditions

- Guest: `testdata/uefi-latency-guest.qcow2` under
  `qemu-system-x86_64` (q35, 128 MB, OVMF, QXL, `-display
  none`), SPICE on `localhost:5900` with ticketing disabled,
  fixed at 1280x800. Booted once and reused for both runs.
- Guest behaviour (discovered during 1a): *any* keypress
  advances a fixed 8-colour cycle — teal, red, magenta, yellow,
  grey, black, blue, green — as a full-screen repaint,
  sometimes via a full video-mode reset (SPICE surface destroy →
  640x480 → recreate 1280x800). One step in eight is black, so
  the viewer legitimately shows ~30 s of black every 4 minutes.
- Driver: one QMP `sendkey` every 30 s; the guest was stepped to
  teal before each run so both runs start at the same cycle
  position. A 5 s cadence does not work: the mode-set churn
  outruns the stream's few-second recovery and the viewer stays
  black permanently (see "Found during execution").
- Viewer: Debian Chromium on the same host, fresh profile,
  `--disable-features=WebRtcHideLocalIpsWithMdns` and
  `--autoplay-policy=no-user-gesture-required`, signalling over
  loopback. Firefox 140 ESR could not establish ICE in this
  environment at all (its offers carried no usable candidates,
  with or without the mDNS-obfuscation and loopback prefs) and
  cannot be the phase-04 viewer on this host.
- ryll: `--web --direct localhost:5900`, dev-profile build via
  `make build`,
  `RUST_LOG=info,shakenfist_spice_webrtc=debug,ryll=debug`
  (the drop counters only log at debug). Encoder negotiated
  1280x800@30fps in both runs.
- Sampling: RSS (`/proc/<pid>/status`) and per-thread CPU
  (`/proc/<pid>/task/*/stat`) every 30 s, with whole-host CPU
  busy% and load average recorded per sample so contamination
  from other workloads on the shared machine is visible in the
  record. The host stayed quiet through 1a; 1g saw one brief
  external spike (max 29% busy on 16 cores), not enough to move
  the numbers.

### Deviations from the step brief

The brief asked for "per-thread CPU from the runtime metrics"
and "the latency HUD's distribution". Both are GUI-mode-only:
the auto-snapshot loop is spawned from `app.rs` and the web
shell has no latency HUD, so neither exists under `--web`.
Substituted: external `/proc` sampling as above, and no latency
distribution (RTT over loopback is ~0 and browser-side WebRTC
stats were not scraped). Phase 04 must measure the same way for
the comparison to hold.

This is also a light workload — a solid-colour full-screen
repaint every 30 s is nearer idle-with-bursts than a busy
desktop (compare the 0.2–50 Mbit/s range seen in real test
sessions). It is what is reproducible unattended; treat the
numbers as a floor-shape baseline, not a stress result.

## Effort

Two days, up from the one day the master plan estimated. The
increase is step 1c, which the master plan did not account for —
the four-way duplication only became visible on a close read of
the test files.

This is a good trade rather than a slip: 1c moves work *out* of
phase 02's atomic commit, where it would have been four parallel
rewrites reviewed as one diff, into a normal reviewable refactor
that runs against a test suite which still works. Phase 02 should
come down by at least as much, and its risk comes down more.

## Acceptance

- `make test` and `pre-commit run --all-files` pass at every
  commit in the phase, not just the tip.
- `webrtc = "0.17.1"` is unchanged in both manifests. If this
  phase bumped the dependency, it did the wrong thing.
- No call to `RTCPeerConnection::connection_state()` or
  `gathering_complete_promise()` remains anywhere in the
  workspace.
- `bridge.rs` registers its callbacks through `BridgeEvents`
  rather than inline closures, and every closure body is a
  delegation.
- One client-PC construction path exists, not four.
- The 1a and 1g measurements agree within noise.

## Open questions

1. ~~**Does `on_ice_gathering_state_change` fire `Complete` before
   `local_description()` carries the candidates in 0.17?**~~
   **Answered: no, the ordering is safe.** Step 1f landed and
   `gathering_signal_fires_after_local_description_is_populated`
   asserts the local description carries `a=candidate:` lines the
   instant `wait_for_gathering` returns.
   `accept_offer_answer_carries_all_candidates` additionally runs
   the full exchange repeatedly and asserts a non-zero candidate
   count every time (5 iterations in the smoke tier; the 20-run
   invariant-count soak is gated behind `RYLL_GATHERING_SOAK=1`
   after PR #267 review flagged the exact-equality check as
   coupled to host interface churn). The sticky-signal design
   needs no second condition, and this open question no longer
   inflates phase 02's estimate.

2. ~~**Should `test-support` be a feature or a separate crate?**~~
   **Answered during 1c: a feature.** It is lighter, matches the
   workspace, and the reasoning is recorded in the crate's
   `Cargo.toml` comment. The accepted consequence is that
   `src/test_client.rs` (~500 lines) ships in the published
   crates.io tarball for `shakenfist-spice-webrtc`; it has no
   production-surface impact because the module is gated behind
   `#[cfg(any(test, feature = "test-support"))]` and the feature
   is off by default. If the tarball weight ever grates, the
   `-testkit` crate split remains available.

3. **Does `loopback.rs`'s `on_track` wiring belong in the
   helper?** It is currently the only site with it, so the plan
   leaves it local. If phase 02 or a later plan adds a second
   consumer, revisit rather than generalising speculatively.

## Found during execution

### `wait_for_dead` has a lost-wakeup race

Not introduced by this phase, but directly adjacent to it and
worth fixing before someone copies the pattern again.

`WebrtcBridge::wait_for_dead` (and the equivalent inline logic in
`ryll/src/web/lifecycle.rs`) does:

```rust
if self.dead_flag.load(Ordering::SeqCst) { return; }
self.dead.notified().await;
```

`Notify::notified()` does not register interest until the future
is first polled, so there is a window between the flag load and
the first poll. If the peer connection reaches a terminal state
inside that window, `notify_waiters()` fires with nobody
registered, and the subsequent await blocks forever — the reaper
never tears the bridge down.

`wait_for_gathering`, added in step 1f, avoids this by calling
`Notified::enable()` before the flag check, which registers up
front so a notification landing in the window is still delivered.

**Fixed**, in its own commit after 1f rather than folded into it:
`WebrtcBridge::wait_for_dead` and the reaper's inline equivalent
in `ryll/src/web/lifecycle.rs` both now `enable()` before checking
the flag. The reaper case is the one with teeth — a lost wakeup
there leaves a dead bridge and its encoder pipeline running until
the process exits.

This is a genuine production bug fix that happens to have been
found by porting work, not a refactor. It is called out here so
it is not mistaken for one.

The PR #267 review then pointed out that the fix left the
correct-but-subtle pattern copy-pasted at four sites — the exact
duplication that produced the original bug. The wait and raise
halves are now consolidated into
`shakenfist_spice_webrtc::StickySignal` (`src/sticky.rs`), used
by `wait_for_dead`, `wait_for_gathering`,
`TestPeer::offer_and_gather`, and the ryll bridge reaper (which
takes a single `dead_signal()` handle in place of the old
`dead_handle()`/`dead_flag_handle()` pair). The type carries unit
tests for the pre-raised fast path, the lost-wakeup schedule
itself (raise after the waiter registered but before it was
woken), and multiple/repeat waiters — coverage the inline copies
could not have without a running ICE stack.

### The notify path versus the fast path

Whether `wait_for_gathering`'s first call blocks on the
notification or finds the flag already set is timing-dependent,
so the acceptance tests cannot control which path they exercise.
`gathering_signal_fires_after_local_description_is_populated`
therefore calls it a second time, after gathering has certainly
finished, to guarantee the sticky fast path is covered at all.
Without that, a regression making late callers hang could pass
the suite.

### Sustained surface churn wedges the web stream (pre-existing)

Found while running the baseline soaks, not introduced by this
phase. The uefi-latency-guest performs a full video-mode reset
(SPICE surface destroy → 640x480 → recreate 1280x800) when its
colour changes, and the browser stream takes a few seconds to
recover from each one. Driving a colour change every 5 s churns
faster than recovery, and the viewer then shows black
*permanently* — it never resurfaces even though ryll's logs show
the surfaces cycling and the encoder restarting. At a 30 s
cadence recovery completes every time. Real desktops rarely
mode-set repeatedly, so this is a robustness gap rather than a
dogfooding blocker. Tracked as
[Q5 in OPEN-QUESTIONS.md](/components/ryll/plans/OPEN-QUESTIONS/): the encoder/stream
should survive surface churn at any cadence, and the right time
to diagnose is after the 0.20 port rewrites the adjacent paths.
