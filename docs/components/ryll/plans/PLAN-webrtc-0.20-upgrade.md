# webrtc-rs 0.20 upgrade

## Prompt

Port `shakenfist-spice-webrtc` from webrtc-rs 0.17.1 to 0.20.x.
This is a deliberate deferral, not a discovery: Renovate raised
the bump as PR #245 on 2026-08-03, CI failed with 25 compile
errors in the lib and 36 in the lib tests, and we chose to pin
`webrtc < 0.18` and write this plan rather than either rush the
port or leave a permanently-red PR open. PR #245 was repurposed
to carry the pin and this document.

Before executing any phase, read
`shakenfist-spice-webrtc/src/bridge.rs` end to end — it is the
only production file that touches webrtc-rs, and the port lives
almost entirely inside it. Read the 0.20 API docs at
<https://docs.rs/webrtc/0.20.0/webrtc/> rather than trusting the
API sketch in this plan; it was written from the docs index and
the CI error output, not from a compiling port.

Follow the project's plan conventions: per-phase plan files named
`PLAN-webrtc-0.20-upgrade-phase-NN-*.md`, one logical change per
commit, this master plan's phase table updated as work lands.

## Situation

### What we depend on today

Updated after phase 01 landed; the shape below is develop
`e07cfd4f`, not the tree this plan was first written against.
Phase 01 removed a manifest, a duplicated client peer connection,
and both of the API calls that had no direct replacement, so
several of the original numbers here were substantial
overstatements of the remaining work.

`webrtc = "0.17.1"` appears in exactly one manifest:
`shakenfist-spice-webrtc/Cargo.toml:27`. Step 1c moved ryll's
`--web` signalling test onto the shared `TestPeer` helper, after
which `ryll` named no webrtc type at all and its dev-dependency
was deleted.

`Cargo.lock` resolves it to 0.17.2, alongside `rtp` 0.17.2 and
`rtcp` 0.17.2. `rtp` is also a *direct* dependency of both
`shakenfist-spice-webrtc` (`Cargo.toml:36`, added by step 1b) and
`shakenfist-spice-renderer` (`Cargo.toml:137`), so the
`webrtc::rtp` re-export is already off the port's critical path.

The abstraction boundary is good. `WebrtcBridge` in
`shakenfist-spice-webrtc/src/bridge.rs` (1448 lines) is the single
production chokepoint. The complete webrtc-facing surface of the
workspace is three files and 37 `use webrtc::` lines:

| File | `use webrtc::` lines | Role |
|---|---|---|
| `shakenfist-spice-webrtc/src/bridge.rs` | 18 | The production bridge |
| `shakenfist-spice-webrtc/src/test_client.rs` | 13 | `TestPeer`, the shared client half (`test-support` feature) |
| `shakenfist-spice-webrtc/tests/loopback.rs` | 6 | `on_track` / `on_data_channel` wiring specific to that test |

`shakenfist-spice-webrtc/tests/lifecycle.rs`,
`ryll/src/web/*` and
`shakenfist-spice-renderer/tests/webrtc_h264_smoke.rs` name no
webrtc type: lifecycle and the reaper go through `WebrtcBridge`
and `StickySignal`, and the renderer's smoke test uses the
standalone `rtp` crate.

Within `bridge.rs`, the webrtc-facing code is concentrated:

| Region | Lines | What it does |
|---|---|---|
| `BridgeEvents` | 114–213 | The four callback bodies, shaped for 0.20's handler trait |
| `WebrtcBridge::new` | 295–455 | Media engine, interceptors, PC, tracks, control DC, callback registration |
| `accept_offer` | 492–517 | set-remote / create-answer / set-local / wait-for-gathering |
| `send_control` / `close` | 596–624 | DC send, PC teardown |
| RTP pumps | 659–910 | `track.write_rtp` against `rtp` crate types |

Neither of the two API calls with no 0.20 replacement survives.
`gathering_complete_promise()` was replaced in step 1f by a sticky
`gathered` signal raised from `on_ice_gathering_state_change`, and
`RTCPeerConnection::connection_state()` was replaced in steps 1d
and 1e by a `Mutex` shadow fed by the state-change callback —
`WebrtcBridge::connection_state` (`bridge.rs:949`) and
`TestPeer::connection_state` (`test_client.rs:289`) both read it.

The four near-identical client-side peer connection setups the
first draft of this plan found are now one: `TestPeer::build`
(`test_client.rs:94`). Collapsing them was phase 01's largest
step.

### What 0.20 changes

webrtc-rs 0.20 re-homes the crate on the sans-io `rtc` protocol
core, wrapped in a thin *async* layer. Note that the async model
survives — `create_answer`, `set_remote_description`, `add_track`
and friends are all still `async`. The CI errors reading
"`Option<RTCSessionDescription>` is not a future" are a
consequence of those methods moving onto a trait that is not in
scope, not of the API going synchronous.

The account below was written from the 0.20 docs index and PR
#245's CI output, before anyone read the source. Phase-02 planning
checked it against webrtc 0.20.2 and found it right in outline and
wrong in emphasis; the corrections are marked, and
[PLAN-webrtc-0.20-upgrade-phase-02-bump.md](/components/ryll/plans/PLAN-webrtc-0.20-upgrade-phase-02-bump/)
carries the full findings with citations.

**1. Module reshuffling.** `api`, `interceptor`, `track` and `rtp`
are gone as top-level modules; `peer_connection::{configuration,
sdp, peer_connection_state}` and `rtp_transceiver::{rtp_codec,
rtp_transceiver_direction}` flatten into their parents. Local
tracks move to `media_stream::track_local::static_rtp`.
`APIBuilder` becomes `PeerConnectionBuilder`; `RTCConfiguration`
gains an `RTCConfigurationBuilder`.

*Correction:* this is bigger than a reshuffle. `webrtc` 0.20 is a
thin shim that does not re-export its sans-io core, so `rtc`
becomes a direct dependency carrying `MediaEngine`, `Registry`,
the MIME constants and `rtp::Packet`. And the standalone `rtp`
crate phase 01 adopted is a dead line ending at 0.17.2 — RTP now
lives in `rtc-rtp`, reached as `rtc::rtp`. The API is
near-identical (`codecs` becomes `codec`), but it is a different
crate, so an `rtp` 0.17 `Packet` is a different *type*, not an
older one.

**2. Trait-scoped methods.** `RTCPeerConnection`'s operations
moved onto an object-safe `PeerConnection` trait, so
`use webrtc::peer_connection::PeerConnection` is required before
any of `create_answer`, `set_local_description`, `add_track`,
`create_data_channel` or `close` resolve. *Addition:* the builder
returns an unnameable `impl PeerConnection`, so the bridge stores
`Arc<dyn PeerConnection>`.

**3. The callback model inverted.** Today `bridge.rs` registers
four callbacks after construction (`:406`, `:412`, `:418`,
`:426`). In 0.20 the peer-connection ones collapse into a single
`PeerConnectionEventHandler` impl — nine async methods, all
defaulted no-op — handed to the builder via `.with_handler()` *at
build time*, which is the one mandatory builder call. Phase 01
shaped `BridgeEvents` for exactly this.

*Correction, and the biggest single miss in this section:*
datachannel messages and remote tracks did not move onto the
handler, they stopped being callbacks altogether. `on_message` does
not exist; a channel is an `Arc<dyn DataChannel>` you `poll()` in
a loop, and remote tracks are the same. So the callback *bodies*
do move after all, from registrations into spawned poll loops —
the one thing phase 01's `BridgeEvents` comment promised would not
happen.

**4. Things with no direct replacement.**

- `gathering_complete_promise()` — retired by phase 01 step 1f.
  The 0.20 ordering was checked and is safe: every candidate is in
  the ICE agent before the `Complete` event is dispatched, and
  `local_description()` re-renders from that agent on each call.
- `connection_state()` — retired by phase 01 steps 1d and 1e.
  *Correction:* it has no replacement anywhere in 0.20, not merely
  no trait method, so shadowing was the only option rather than
  the tidy one.
- `RTCRtpTransceiverInit` gained a `streams` field. *Correction:*
  the struct derives `Default`, so this is not breakage at all.
- `.with_udp_addrs(...)` — 0.17 bound sockets internally; 0.20
  makes the caller choose. *Correction, and this one enlarges
  phase 02:* the natural placeholder `0.0.0.0:0` binds fine and
  emits a literal `0.0.0.0` host candidate that browsers discard,
  while every Rust-to-Rust test still passes. Phase 02 therefore
  has to enumerate interface addresses rather than defer the
  question to phase 03.
- *New:* `TrackLocalStaticRTP::new` now takes a whole
  `MediaStreamTrack` and the caller supplies the SSRC and codec
  parameters that webrtc-rs previously chose.

### Why this cannot be staged the usual way

The project convention is that every commit builds and passes
tests. A dependency major bump cannot honour that incrementally —
`shakenfist-spice-webrtc` is broken from the moment the version
changes until the port is complete, and the test crates break
with it. Phase 02 is therefore an unavoidably large atomic
commit.

Phase 01 exists to shrink it. A surprising amount of the work can
be done *against 0.17*, where it compiles and tests today, so
that the atomic step is mostly import rewriting.

## Mission and problem statement

Get `shakenfist-spice-webrtc` onto webrtc-rs 0.20.x with `--web`
mode behaving identically — same SDP exchange, same H.264 and
Opus tracks, same control datachannel, same terminal-state
reaping — and remove the `< 0.18` Renovate pin.

Out of scope: adopting 0.20's new capabilities. The release adds
opt-in send back-pressure, GSO/GRO UDP batching, a bounded shared
reactor pool, and a configurable SCTP receive window. Those are
interesting for a video-streaming workload and may well justify
their own plan, but tuning them during a port makes it impossible
to attribute a regression. Port first, tune later.

## Approach

### Phase 01 — Pre-work on 0.17

Version-neutral refactoring that compiles and passes tests
against 0.17.1 today, and shrinks the atomic step:

- Promote `rtp` (and `rtcp` if used) to direct dependencies of
  `shakenfist-spice-webrtc` at the version the lockfile already
  resolves, and switch `bridge.rs` off the `webrtc::rtp`
  re-export. Mirrors what `shakenfist-spice-renderer` already
  does, and the comment at `shakenfist-spice-webrtc/Cargo.toml:15`
  explaining the re-export pin goes away with it.
- Collapse the three callback registrations into a single struct
  with three methods, still registered through the 0.17 API. The
  struct is then trivially re-targeted at
  `PeerConnectionEventHandler` in phase 02.
- Collapse the four duplicated client-side peer connection setups
  into one shared test helper behind a `test-support` feature, so
  phase 02 rewrites that boilerplate once instead of four times
  across two crates. This is the largest step in the phase and
  was not visible when this master plan was first written.
- Shadow the connection state inside that helper — the
  state-change callback already sees every transition — so
  `wait_until_connected` reads the shadow rather than calling
  `RTCPeerConnection::connection_state()`. This removes one of
  the no-direct-replacement
  items entirely, and works identically on 0.17.
- Give `accept_offer` an explicit "gathering complete" signal —
  a sticky `Notify` + `AtomicBool` pair raised from a new
  `on_ice_gathering_state_change` handler, which 0.17 already
  provides — rather than calling `gathering_complete_promise()`
  inline, and prove the answer SDP carries the same candidate
  set. This validates the phase 02 design for the riskiest
  no-direct-replacement item while we can still fall back.

Each of these is its own commit. All of them are testable now,
which is the point. Detailed in
[PLAN-webrtc-0.20-upgrade-phase-01-prework.md](/components/ryll/plans/PLAN-webrtc-0.20-upgrade-phase-01-prework/),
which corrects two things this master plan got wrong on first
writing: `connection_state` is test-only, and the client-PC
setup is duplicated four times.

### Phase 02 — The atomic bump

Detailed in
[PLAN-webrtc-0.20-upgrade-phase-02-bump.md](/components/ryll/plans/PLAN-webrtc-0.20-upgrade-phase-02-bump/),
which corrects most of the factual claims this master plan made
about the tree — all in the direction of less work, because phase
01 did it — and revises the API account above from the 0.20.2
source.

Three preparatory commits that still build against 0.17, then one
atomic commit, then cleanup:

- Move `tests/loopback.rs`'s post-construction `on_track` and
  `on_data_channel` registrations into `TestPeerBuilder`, since
  0.20 has no post-construction registration to move them to.
- Make the `BridgeEvents` bodies non-blocking, because 0.20 awaits
  handler methods inline in the connection's driver loop.
- Write and unit-test the UDP bind-address selection.
- The bump itself: `webrtc = "0.20.2"`, `rtc` in, `rtp` and
  `rustls` out, `PeerConnectionBuilder` with a build-time handler,
  datachannel and remote-track poll loops in place of `on_message`
  and `read_rtp`, tracks rebuilt with explicit SSRCs and codings.
- Both webrtc rules out of `renovate.json`, and the docs that name
  the version or describe the UDP port behaviour.

Green `tests/loopback.rs` (two bridges exchanging offer/answer
plus DC traffic) and `tests/lifecycle.rs` (terminal-state
detection) are necessary but explicitly *not* sufficient here: a
wrong bind address leaves both green and every browser broken, so
the phase also requires a real browser session before it closes.

### Phase 03 — Socket binding configuration

Phase 02 answers the hard half of this — *what* to bind, which it
has to, because the placeholder the original plan proposed
(`0.0.0.0:0`) silently produces unroutable candidates. Phase 03 is
what is left: exposing that choice as configuration so an operator
can pin the media port or restrict the interface, which matters
behind a firewall or in a container.

Note that `WebrtcBridgeConfig` currently has *no* path from the
command line at all — `ice_servers` exists on the struct but ryll
passes an empty vector unconditionally
(`ryll/src/web/signalling.rs:300-303`, the only production
construction site; the other twenty are tests). So phase 03 builds
that plumbing rather than extending it, and should carry
`ice_servers` along with the bind address while it is there.

Phase 03's planning survey corrected two things this section said
or assumed. The `signalling.rs` line number above drifted by one
during phase 02. And "touches `docs/configuration.md`" turns out
to mean *writing* its web section rather than extending it:
`docs/configuration.md` documents no `--web` flag at all today,
not even the `--web-host` and `--web-port` that have shipped since
the web frontend landed. Phase 02's review also deferred two items
into this phase — an interface allowlist rather than only a port
pin, and an opt-in for loopback-only hosts — both of which
[the phase plan](/components/ryll/plans/PLAN-webrtc-0.20-upgrade-phase-03-udp-addrs/)
now carries.

Touches `docs/configuration.md`, `docs/web-frontend.md` and
`docs/web-mode-internals.md`.

### Phase 04 — Soak validation and docs

The 0.20 release notes headline UDP batching via GSO/GRO,
elimination of tokio scheduler overhead in datachannel
operations, and opt-in send back-pressure. All three land on the
`run_video_pump` write path (`bridge.rs:1576`). Integration tests
exercise that path for seconds; a regression there shows up over
minutes.

Phase 04's planning survey corrected four claims this section
made; they are fixed in place below, and
[the phase plan](/components/ryll/plans/PLAN-webrtc-0.20-upgrade-phase-04-soak/)
records what was wrong with each.

- A real browser session against a real SPICE guest, held long
  enough to see steady-state behaviour. **Listen to the audio**
  while it is open: phase 02's browser session confirmed the
  playback channel negotiated Opus but nobody confirmed sound by
  ear, so that Definition-of-done clause is inherited here.
  *Correction:* this section originally asked for "the latency
  HUD and runtime metrics captured". Neither exists under
  `--web` — both are GUI-mode-only — which phase 01 discovered
  during 1a and worked around with external `/proc` sampling.
  Phase 04 must sample the same way for the comparison to hold.
- Chrome and Firefox at minimum; Safari if a Mac is available.
  **Firefox is a known blocker inherited from phase 02**: a Firefox
  that does not offer H.264 gets no video at all, because ryll
  encodes H.264 only. Land #289 (tell the viewer) before soaking,
  and settle whether a Firefox with a working OpenH264 plugin is
  enough for this criterion or whether ryll needs a second codec.
  *Correction:* phase 01's Baseline conditions block concluded
  Firefox "cannot be the phase-04 viewer on this host" after it
  failed to establish ICE under 0.17. Phase 02 contradicted that
  on 0.20 — ICE was fully healthy and everything but video
  worked — so the blocker is codec-specific, not transport-specific.
- Compare RSS and CPU against a 0.17 baseline captured before
  the bump — take that baseline during phase 01 while we are
  still on the old version. *Correction:* the baseline exists,
  but the harness that produced it was never committed, so
  reproducing its conditions is a phase 04 step
  (`tools/web-soak.sh`) rather than a given.
- Run `RYLL_GATHERING_SOAK=1 make test` on a quiet host: the
  20-iteration invariant-candidate-count check on the gathering
  signal is off by default (host interface churn makes it flaky
  in CI) and this soak is exactly the deliberate occasion it is
  gated for.
- Check `ARCHITECTURE.md` and `AGENTS.md` against the bridge's
  shipped task and callback structure. *Correction:* this asked
  phase 04 to update them "if the bridge's task and callback
  structure changed shape, which phase 02 makes likely". It did,
  and phase 02 already wrote it up — `AGENTS.md` carries a
  "WebRTC conventions" section and `ARCHITECTURE.md`'s file tree
  was corrected by phase 03. Phase 04 verifies rather than
  writes.

## Phase order

| Phase | Plan | Status |
|-------|------|--------|
| 1. Pre-work on 0.17 | [PLAN-webrtc-0.20-upgrade-phase-01-prework.md](/components/ryll/plans/PLAN-webrtc-0.20-upgrade-phase-01-prework/) | Complete — baseline captured, 1g agrees within noise |
| 2. Atomic bump to 0.20 | [PLAN-webrtc-0.20-upgrade-phase-02-bump.md](/components/ryll/plans/PLAN-webrtc-0.20-upgrade-phase-02-bump/) | Complete — Chromium session on `7e2fb58e` confirms the bind address. Its two deferrals were discharged in phase 04: the audio check was performed by ear, and #289/#290 are fixed |
| 3. Socket binding configuration | [PLAN-webrtc-0.20-upgrade-phase-03-udp-addrs.md](/components/ryll/plans/PLAN-webrtc-0.20-upgrade-phase-03-udp-addrs/) | Complete — `--web-media-addr` (address or interface name), `--web-media-port` and `--web-ice-server`, carried through `WebState` into a `UdpBindPolicy` the bridge resolves per offer. Explicit addresses override the loopback default; `0.0.0.0` is refused at startup |
| 4. Soak validation and docs | [PLAN-webrtc-0.20-upgrade-phase-04-soak.md](/components/ryll/plans/PLAN-webrtc-0.20-upgrade-phase-04-soak/) | Complete — audio confirmed by ear at last; the bump costs no CPU and slightly less memory, bisected either side of the phase-02 merge; #289/#290 fixed, plus four input bugs the browser check found. Safari unexercised (#310), Firefox still has no video (#311; #289 makes it legible) |

Phase 01 is a hard prerequisite for 02 only in the sense that it
makes 02 tractable; 02 could be done standalone at higher risk.
Phases 03 and 04 both depend on 02.

Phase 02 is now `Complete`: phase 04's browser sessions supplied the
verification it was waiting for. What follows is the reasoning it
was left open with, kept because it explains why.

The port
itself has landed on `webrtc = "0.20.2"` and both Renovate rules
are gone, `rtc` is now a direct dependency, the standalone `rtp`
and `rustls` dependencies are gone, datachannels and remote
tracks became poll loops, and UDP sockets bind enumerated
interface addresses because `0.0.0.0` passes every test and
reaches no browser.  That last point is why the phase stays
open: only a real browser session can catch a wrong bind
address, and the test suite cannot stand in for it.

## Effort estimate

Roughly a week and a half, with a realistic band of five days to
two weeks:

| Phase | Estimate |
|---|---|
| 01 — pre-work on 0.17 | 2 days (actual) |
| 02 — atomic bump | 3–5 days |
| 03 — socket binding config | 1 day (revised from ½ by the phase plan; actual) |
| 04 — soak and docs | 1 day |

Phase 01 grew by a day after detailed planning surfaced the
four-way client-PC duplication. Phase 02 was expected to come down
by the same amount, and in one sense it did — the four-way rewrite
is gone — but detailed planning then found three things this plan
had not: the datachannel and remote-track surfaces became
poll-based rather than moving onto the handler, track construction
now requires caller-supplied SSRCs and codec parameters, and the
UDP bind address has to be solved in 02 rather than deferred to
03. Net, 02 roughly doubled.

Two things went the other way and are already priced in.
ICE-gathering completion — the item this plan called its riskiest
— was retired by phase 01 and independently confirmed safe in
0.20. And the rustls coupling *subtracts* work: the pin and eleven
`install_default()` calls delete outright.

The remaining variance is in phase 02's atomic commit, and it is
now concentrated in the media path rather than the signalling
path: whether explicit codings reproduce 0.17's negotiated result
first time, and whether 0.20's new RTX advertisement changes
browser behaviour. Neither can restructure the signalling
protocol, which is why the upper bound came in from "add a week"
to five days.

## Open questions

All seven were answered during phase-02 planning, against the
webrtc 0.20.2 and `rtc` 0.20.2 sources rather than the docs index.
They are kept here with their answers because the answers are what
sized phase 02, and two of them moved work between phases.

1. ~~**What replaces `gathering_complete_promise()`?**~~
   **Answered: `on_ice_gathering_state_change`, and the ordering
   is safe.** `local_description()` re-renders from the live ICE
   agent on every call, candidates are pushed into the core before
   the completion sentinel, and the sentinel is what queues the
   event. Non-trickle signalling is correct on 0.20, and gets
   `a=end-of-candidates` for free. Gathering does not start until
   `set_local_description()`, so our existing call order is
   required rather than incidental.

2. ~~**Does `RTCDataChannel` keep `on_message`?**~~
   **Answered: no, and neither does anything else.** Datachannel
   messages and remote-track RTP both became poll-based —
   `DataChannel::poll()` and `TrackRemote::poll()` — so the wiring
   becomes a spawned loop per channel and per track. This is the
   answer that most enlarged phase 02, because it means the
   callback *bodies* move, which phase 01 had been told they would
   not.

3. ~~**Does `TrackLocalStaticRTP::write_rtp` keep its
   signature?**~~ **Answered: nearly — it takes the packet by
   value and is a `TrackLocal` trait method.** There is no
   fallible variant on tracks; `writable`/`try_send` exist only on
   `DataChannel`. `write_rtp` already applies back-pressure by
   awaiting on the driver's bounded event channel. Separately,
   `TrackLocalStaticRTP::new` changed materially and now wants the
   SSRC and codec from us.

4. ~~**Does `with_udp_addrs` accept `0.0.0.0:0`?**~~
   **Answered: yes, and that is the trap.** It binds happily and
   emits a literal `0.0.0.0` host candidate, which browsers
   discard — while two Rust peers on one host agree about it and
   connect, so no test we have would fail. Phase 03 does not
   become a prerequisite of phase 02, but *choosing* the addresses
   does move into phase 02, leaving phase 03 to expose the choice
   as configuration.

5. ~~**Which `rtp` major does 0.20 pair with?**~~
   **Answered: none — the `rtp` crate is dead at 0.17.2.** RTP
   moved to `rtc-rtp`, reached as `rtc::rtp`, so we depend on
   `rtc` and the type identity comes for free from webrtc's exact
   pin. The only source change is `codecs` → `codec`. The
   renderer's smoke test does *not* move in lockstep: its `rtp`
   is a dev-dependency with no shared types. It should move
   eventually, on its own schedule — see Future work.

6. ~~**Does 0.20 make the #215 sibling-skew problem obsolete?**~~
   **Answered: yes, arithmetically.** `webrtc` 0.20.x requires
   `rtc` at an exact patch, and `rtc` requires each of its 16
   siblings at that same exact patch, so cargo cannot resolve the
   inconsistent set that broke #215. Both Renovate rules go when
   the port lands, not just the pin.

7. ~~**Does rustls stay pinned the same way?**~~
   **Answered: it is not pinned at all any more.** `rtc-dtls`
   0.20.2 selects its crypto provider from its own cargo features
   and passes it explicitly rather than reading the process
   default — upstream hit our exact bug and fixed it properly. Our
   direct `rustls` dependency and every `install_default()` call
   in `shakenfist-spice-webrtc` delete. ryll's own rustls
   dependency stays: it serves SPICE TLS and `axum-server`, and
   `aws-lc-rs` still reaches ryll's graph via `reqwest → quinn`
   regardless of webrtc. This also retires the forcing function
   described below — after 0.20 our rustls version is an ordinary
   `^0.23.35` floor, so an advisory is a lockfile bump rather than
   an emergency port.

## Why we are deferring rather than doing it now

- `cargo audit` and `cargo deny` both pass on 0.17.2 today, so
  there is no security pressure.
- 0.17.x is nonetheless the end of the old line; fixes land on
  0.20+ only.
- The forcing function is most likely rustls, not webrtc. The
  pin at `shakenfist-spice-webrtc/Cargo.toml:67-72` couples our
  rustls version to webrtc's, so a rustls advisory would demand
  this port with no notice and no schedule. That is the
  scenario this plan exists to make survivable. (Phase-02
  planning established that the port *removes* this coupling —
  see open question 7 — which makes the argument for doing it
  stronger, not weaker: the exposure persists for exactly as long
  as we stay on 0.17.)
- Secondarily, 0.20's performance work is aimed squarely at our
  workload, so this may become something we want before it is
  something we must do.

## Administration and logistics

### Success criteria

* `webrtc = "0.20.2"` (or later) in the one manifest that names
  it, with both webrtc rules removed from `renovate.json`.
* `make test` passes, including `tests/loopback.rs` and
  `tests/lifecycle.rs`.
* `pre-commit run --all-files` passes.
* A real browser reaches a real SPICE guest through `--web`,
  with video, audio, input, and cursor all working, and survives
  a soak long enough to compare RSS and CPU against the 0.17
  baseline. Note this is the *only* check that can catch a wrong
  UDP bind address, which is why phase 02 requires a browser
  session of its own rather than waiting for phase 04's soak.
* The answer SDP advertises no candidate with an unspecified
  address, and at least one candidate.
* The reaper still tears the bridge down when the browser goes
  away — `wait_for_dead` fires on `Failed`, `Disconnected` and
  `Closed`.
* `docs/configuration.md` and `docs/web-frontend.md` cover the
  UDP bind address; `ARCHITECTURE.md` and `AGENTS.md` reflect any
  change to the bridge's task and callback structure.

### Future work

* Adopt 0.20's send back-pressure (`writable` / `try_send`) in
  `run_video_pump` to bound peak RSS under a slow consumer.
  Deliberately out of scope for the port itself.
* Evaluate GSO/GRO batching and the configurable SCTP receive
  window against our own latency measurements.
* Reconsider whether `shakenfist-spice-webrtc` should depend on
  the sans-io `rtc` core directly rather than the async wrapper.
  Probably not — we are happy with tokio — but 0.20 makes it a
  real option for the first time. (Note the port makes `rtc` a
  direct dependency regardless, because `webrtc` does not
  re-export the types its own API takes.)
* Move `shakenfist-spice-renderer/tests/webrtc_h264_smoke.rs` off
  the abandoned `rtp` 0.17 crate onto `rtc::rtp`. It is a
  dev-dependency with no type coupling to the webrtc crate, so it
  does not have to move with the port — but once it has, the test
  is exercising a payloader we no longer ship.
* Ask upstream to re-export `rtc::rtp` (or at least `rtp::Packet`)
  from `webrtc`. The crate already hand-re-exports two DTLS enums
  with a comment explaining that forcing callers to add a
  version-locked second dependency is bad — and then does exactly
  that on the primary media write path.
* Reconsider `register_default_codecs()`. On 0.20 it advertises
  RTX, HEVC and AV1 that we never send. Deliberately left alone
  during the port so that negotiation differences stay
  attributable; worth revisiting once the phase-04 soak has a
  clean baseline.
* Let `--web-ice-server` carry TURN credentials. `RTCIceServer`
  has `username` and `credential` fields and the bridge already
  maps a URL string into one, but the flag takes a bare URL, so
  an authenticated TURN server cannot be configured — only STUN
  and open TURN work today. Associating a credential pair with a
  specific URL is a flag-syntax question phase 03 deliberately
  did not answer in half an hour. Phase 03's plan says twice that
  this was "recorded in Future work"; phase 04's planning survey
  found it had not been recorded anywhere at all, which is why it
  appears here rather than in phase 03's commit.

* Give phase 03's configuration surface an interface allowlist,
  not just a port pin. Since 0.20 made socket binding the
  caller's job, `host_udp_bind_addrs` binds and advertises every
  non-loopback address the host has — including RFC 1918,
  169.254/16 and container/veth addresses — so a browser on the
  public interface learns the host's internal addressing. This is
  what 0.17 did internally too, so the port did not regress it,
  but 0.20's `SettingEngine::set_ip_filter` /
  `set_interface_filter` still compile while doing nothing, so
  there is no way to narrow it today. Raised by the automated
  review of PR #278. **Carried into phase 03's plan** as
  `--web-media-addr`, which takes an interface name as well as an
  address; phase 02's other deferral, an opt-in for loopback-only
  hosts, falls out of the same flag.

### Bugs fixed during this work

Two, both found by the automated review of PR #278 and both
introduced by the 0.20 port itself:

* The RTP pumps stamped a hardcoded payload type. 0.20 validates
  the payload type against the negotiated codec list rather than
  rewriting it, and which type is negotiated depends on what the
  browser offered — so Chrome worked and Firefox would have shown
  a black screen with nothing above `trace` in the log. The pumps
  now read the resolved value out of the senders' parameters.
* The bridge reaper parked forever on a bridge replaced by
  `POST /offer`, because `close()` on 0.20 does not reliably raise
  the dead signal the reaper waits on. A viewer reloading the page
  would strand it for the life of the process. It now also wakes on
  a bridge-replacement notification.

Both are covered by regression tests that fail on the pre-fix code;
see the phase-02 plan's review follow-up.

Related existing issues: #215 (webrtc sibling-crate
lockfile skew, the reason for the patch-disable rule) and PR #245
(the Renovate bump this plan defers).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
