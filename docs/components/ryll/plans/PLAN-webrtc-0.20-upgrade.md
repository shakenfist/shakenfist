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

`webrtc = "0.17.1"` appears in two manifests:

- `shakenfist-spice-webrtc/Cargo.toml:18` — the production
  dependency.
- `ryll/Cargo.toml:213` — a dev-dependency, used by the
  `--web` signalling tests to drive a real client peer
  connection through the in-process axum router.

`Cargo.lock` currently resolves both to 0.17.2, alongside
`rtp` 0.17.2 and `rtcp` 0.17.2.

The abstraction boundary is good. `WebrtcBridge` in
`shakenfist-spice-webrtc/src/bridge.rs` (1233 lines) is the
single chokepoint. Outside that crate, the only direct webrtc
usage is test code:

- `ryll/src/web/signalling.rs:432-560` — `#[cfg(test)]` client PC.
- `shakenfist-spice-renderer/tests/webrtc_h264_smoke.rs` —
  already depends on the standalone `rtp` crate rather than the
  `webrtc::rtp` re-export, so it is barely affected.

`ryll/src/web/{server,cursor,inputs}.rs` hold `WebrtcBridge`
values but never name a webrtc type. They should not need to
change at all.

Within `bridge.rs`, the webrtc-facing code is concentrated:

| Region | Lines | What it does |
|---|---|---|
| `WebrtcBridge::new` | 166–360 | Media engine, interceptors, PC, tracks, control DC, three callbacks |
| `accept_offer` | 420–440 | set-remote / create-answer / set-local / wait-for-gathering |
| `send_control` / `close` | 513–545 | DC send, PC teardown |
| `connection_state` | ~864 | State accessor used by the reaper |
| RTP pumps | 580–830 | `track.write_rtp` against `rtp` crate types |

### What 0.20 changes

webrtc-rs 0.20 re-homes the crate on the sans-io `rtc` protocol
core, wrapped in a thin *async* layer. Note that the async model
survives — `create_answer`, `set_remote_description`, `add_track`
and friends are all still `async`. The CI errors reading
"`Option<RTCSessionDescription>` is not a future" are a
consequence of those methods moving onto a trait that is not in
scope, not of the API going synchronous.

Four distinct kinds of breakage, in rough order of effort:

**1. Module reshuffling.** The bulk of the 25 errors. `api`,
`interceptor`, `track` and `rtp` are gone as top-level modules;
`peer_connection::{configuration, sdp, peer_connection_state}`
and `rtp_transceiver::{rtp_codec, rtp_transceiver_direction}`
flatten into their parents. Local tracks move to
`media_stream::track_local::static_rtp`. `APIBuilder` becomes
`PeerConnectionBuilder`; `RTCConfiguration` gains an
`RTCConfigurationBuilder`. `webrtc::rtp` is no longer re-exported,
so `rtp` becomes a direct dependency of
`shakenfist-spice-webrtc` — as it already is for the renderer's
H.264 smoke test.

**2. Trait-scoped methods.** `RTCPeerConnection`'s operations
moved onto an object-safe `PeerConnection` trait, so
`use webrtc::peer_connection::PeerConnection` is required before
any of `create_answer`, `set_local_description`, `add_track`,
`create_data_channel` or `close` resolve.

**3. The callback model inverted.** Today `bridge.rs` registers
three callbacks *after* construction:
`pc.on_peer_connection_state_change` (`:258`),
`control_dc.on_message` (`:312`), and `pc.on_data_channel`
(`:328`, which nests a further `remote_dc.on_message`). In 0.20
these collapse into a single `PeerConnectionEventHandler` impl —
nine async methods, all defaulted no-op — handed to the builder
via `.with_handler()` *at build time*. Every piece of state those
closures capture (`dead`, `dead_flag`, `incoming_tx`,
`encoder_control`) is already created before the PC exists today,
so the data flow should thread cleanly into a handler struct, but
`new()` gets restructured.

**4. Things with no direct replacement.** These are the risk:

- `gathering_complete_promise()` is not on the `PeerConnection`
  trait. `accept_offer` (`:429-430`) uses it for the non-trickle
  "gather every candidate, then return the complete SDP" dance
  that our signalling protocol depends on. The replacement is
  presumably `on_ice_gathering_state_change` on the handler,
  which means `accept_offer` must await a signal the handler
  raises.
- `connection_state()` is likewise off the trait, and `:864`
  uses it.
- `RTCRtpTransceiverInit` gained a required `streams` field
  (`bridge.rs:924`, `:934`, `signalling.rs:439`).
- `PeerConnectionBuilder` requires `.with_udp_addrs(...)`. 0.17
  bound sockets internally; 0.20 makes the caller choose. That
  is a configuration and deployment question, not just a code
  one.

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
- Shadow the connection state in that struct — the state-change
  callback already sees every transition — and reimplement
  `connection_state()` (`:864`) to read the shadow rather than
  ask the PC. This removes one of the no-direct-replacement
  items entirely, and works identically on 0.17.
- Give `accept_offer` an explicit "gathering complete" signal
  (a `Notify` or oneshot raised from the state-change path)
  rather than calling `gathering_complete_promise()` inline, and
  verify the SDP it returns is byte-identical to today's.

Each of these is its own commit. All of them are testable now,
which is the point.

### Phase 02 — The atomic bump

One commit, necessarily large:

- `webrtc = "0.20"` in both manifests; `cargo update` for the
  lock.
- Import rewrites across `bridge.rs`, the two integration tests,
  and the `signalling.rs` test module.
- `use webrtc::peer_connection::PeerConnection` wherever trait
  methods are called.
- `impl PeerConnectionEventHandler` for the phase-01 struct,
  handed to `PeerConnectionBuilder::with_handler()`.
- `APIBuilder` → `PeerConnectionBuilder`, `RTCConfiguration` →
  `RTCConfigurationBuilder`.
- `streams` field on the three `RTCRtpTransceiverInit` sites.
- `.with_udp_addrs(vec!["0.0.0.0:0"])` as a hardcoded
  placeholder — phase 03 makes it configurable. Confirm that
  ephemeral binding still yields the same host and server-
  reflexive candidates that 0.17 produced.
- Remove the `< 0.18` pin from `renovate.json`, keeping the
  patch-disable rule unless phase 02 establishes that 0.20's
  consolidation onto a single `rtc` core has made the
  sibling-crate skew of #215 impossible.

Green `tests/loopback.rs` (two bridges exchanging offer/answer
plus DC traffic) and `tests/lifecycle.rs` (terminal-state
detection) are the bar for this phase. They are not sufficient —
see phase 04 — but nothing proceeds without them.

### Phase 03 — Socket binding configuration

`with_udp_addrs` is a real behavioural change: the bind address
is now ryll's decision. Add it to `WebrtcBridgeConfig`, plumb it
through `--web` configuration, and document it. This matters for
anyone running `--web` behind a firewall or in a container, where
an ephemeral port is exactly the wrong default — being able to
pin the media port is arguably an improvement over 0.17, but only
if it is exposed.

Touches `docs/configuration.md` and `docs/web-frontend.md`.

### Phase 04 — Soak validation and docs

The 0.20 release notes headline UDP batching via GSO/GRO,
elimination of tokio scheduler overhead in datachannel
operations, and opt-in send back-pressure. All three land on the
`run_video_pump` write path (`bridge.rs:644`). Integration tests
exercise that path for seconds; a regression there shows up over
minutes.

- A real browser session against a real SPICE guest, held long
  enough to see steady-state behaviour, with the latency HUD and
  runtime metrics captured.
- Chrome and Firefox at minimum; Safari if a Mac is available.
- Compare RSS and CPU against a 0.17 baseline captured before
  the bump — take that baseline during phase 01 while we are
  still on the old version.
- Update `ARCHITECTURE.md` and `AGENTS.md` if the bridge's task
  and callback structure changed shape, which phase 02 makes
  likely.

## Phase order

| Phase | Plan | Status |
|-------|------|--------|
| 1. Pre-work on 0.17 | PLAN-webrtc-0.20-upgrade-phase-01-prework.md | Not started |
| 2. Atomic bump to 0.20 | PLAN-webrtc-0.20-upgrade-phase-02-bump.md | Not started |
| 3. Socket binding configuration | PLAN-webrtc-0.20-upgrade-phase-03-udp-addrs.md | Not started |
| 4. Soak validation and docs | PLAN-webrtc-0.20-upgrade-phase-04-soak.md | Not started |

Phase 01 is a hard prerequisite for 02 only in the sense that it
makes 02 tractable; 02 could be done standalone at higher risk.
Phases 03 and 04 both depend on 02.

## Effort estimate

Roughly a week, with a realistic band of three days to two weeks:

| Phase | Estimate |
|---|---|
| 01 — pre-work on 0.17 | 1 day |
| 02 — atomic bump | 2–3 days |
| 03 — socket binding config | ½ day |
| 04 — soak and docs | 1 day |

The variance is almost entirely in phase 02, and almost entirely
in the two items with no direct replacement: ICE-gathering
completion and whatever the datachannel event surface turns out
to be. If both map cleanly onto the handler trait, phase 02 is
two days. If either requires restructuring the signalling
protocol, add a week.

## Open questions

These need answering from the 0.20 source or docs before phase 02
is planned in detail. They are the reason phase 02's estimate has
the range it does.

1. **What replaces `gathering_complete_promise()`?** Presumably
   waiting on `on_ice_gathering_state_change` reaching
   `Complete`. Confirm, and confirm it is raised before
   `local_description()` returns the full SDP — our signalling
   is non-trickle and depends on that ordering.

2. **Does `RTCDataChannel` keep `on_message`, or is there a
   datachannel-level event handler analogous to
   `PeerConnectionEventHandler`?** This determines whether the
   nested `on_data_channel` → `on_message` wiring at `:328-348`
   survives as-is or needs its own handler type. Directly
   affects the phase 02 estimate.

3. **Does `TrackLocalStaticRTP::write_rtp` keep its signature?**
   The type survives in `media_stream::track_local::static_rtp`,
   but the new opt-in back-pressure (`writable` / `try_send`)
   suggests the write path may have grown a fallible variant we
   should be using rather than the blocking one.

4. **Does `with_udp_addrs` accept `0.0.0.0:0`,** and does
   ephemeral binding still produce the same candidate set 0.17
   generated internally? If it forces an explicit port, phase 03
   becomes a prerequisite of phase 02 rather than a follow-up.

5. **Which `rtp` major does 0.20 pair with?** Phase 01 promotes
   `rtp` to a direct dependency at the 0.17-era version; phase 02
   has to move it in lockstep. The renderer's H.264 smoke test
   (`shakenfist-spice-renderer/tests/webrtc_h264_smoke.rs`) uses
   `H264Payloader` from the same crate and moves with it.

6. **Does 0.20 make the #215 sibling-skew problem obsolete?**
   The patch-disable rule in `renovate.json` exists because
   webrtc-rs shipped sibling crates in lockstep while declaring
   loose ranges on them. If 0.20's consolidation onto one `rtc`
   core removes that failure mode, the rule can go when the pin
   does.

7. **Does rustls stay pinned the same way?**
   `shakenfist-spice-webrtc/Cargo.toml:49-54` pins rustls to
   whatever webrtc 0.17.1 pulls transitively, so the DTLS
   `CryptoProvider` matches. Re-derive that pin against 0.20 —
   and note that this coupling is the most likely thing to force
   this plan onto the schedule, since a rustls advisory would
   leave us no room to defer.

## Why we are deferring rather than doing it now

- `cargo audit` and `cargo deny` both pass on 0.17.2 today, so
  there is no security pressure.
- 0.17.x is nonetheless the end of the old line; fixes land on
  0.20+ only.
- The forcing function is most likely rustls, not webrtc. The
  pin at `shakenfist-spice-webrtc/Cargo.toml:49-54` couples our
  rustls version to webrtc's, so a rustls advisory would demand
  this port with no notice and no schedule. That is the
  scenario this plan exists to make survivable.
- Secondarily, 0.20's performance work is aimed squarely at our
  workload, so this may become something we want before it is
  something we must do.

## Administration and logistics

### Success criteria

* `webrtc = "0.20"` (or later) in both manifests, with the
  `< 0.18` pin removed from `renovate.json`.
* `make test` passes, including `tests/loopback.rs` and
  `tests/lifecycle.rs`.
* `pre-commit run --all-files` passes.
* A real browser reaches a real SPICE guest through `--web`,
  with video, audio, input, and cursor all working, and survives
  a soak long enough to compare RSS and CPU against the 0.17
  baseline.
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
  real option for the first time.

### Bugs fixed during this work

None yet. Related existing issues: #215 (webrtc sibling-crate
lockfile skew, the reason for the patch-disable rule) and PR #245
(the Renovate bump this plan defers).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
