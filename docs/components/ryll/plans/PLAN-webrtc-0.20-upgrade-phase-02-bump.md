# webrtc-rs 0.20 upgrade — phase 02: the atomic bump

Parent: [PLAN-webrtc-0.20-upgrade.md](/components/ryll/plans/PLAN-webrtc-0.20-upgrade/)

## Prompt

This is the phase the whole plan exists to make survivable: the
version in `shakenfist-spice-webrtc/Cargo.toml` changes and the
crate does not compile again until the port is finished. Phase 01
moved everything that *could* be done against 0.17 out of this
commit; what is left is the part that genuinely cannot build twice.

Before executing any step, read `shakenfist-spice-webrtc/src/bridge.rs`
end to end, then `src/test_client.rs`, then `tests/loopback.rs`.
Those three files are the entire webrtc-facing surface of the
workspace — 37 `use webrtc::` lines between them and nothing else.

Read the 0.20 API from the source, not from this document's
summaries and not from memory. The crate source is on crates.io;
`cargo doc --open -p webrtc` inside the devcontainer works too.
This plan's API claims were checked against webrtc 0.20.2's
source, and the file:line citations are given so the next reader
can re-check them rather than trust them.

Recommended planning effort for this phase: **high**. Recommended
effort per step is in the step table.

## Scope

**In scope**

- `webrtc = "0.20.2"` in `shakenfist-spice-webrtc/Cargo.toml`,
  with `rtc` added as a direct dependency and `rtp` and `rustls`
  removed from that crate (Decisions 1, 2 and the rustls finding
  below).
- The port of `bridge.rs`, `test_client.rs` and `tests/loopback.rs`
  to the 0.20 API.
- Choosing the UDP bind addresses, because the obvious placeholder
  is silently broken (Decision 4).
- Removing both webrtc rules from `renovate.json` (Decision 7).
- Keeping `make test`, `make lint`, `make check-windows` and
  `make web-smoke` green.

**Out of scope**

- Adopting any 0.20 capability that is not required to compile and
  behave as before: send back-pressure, GSO/GRO batching, the
  SCTP receive window, the shared reactor pool. Port first, tune
  later — a regression during a port is unattributable if the
  tuning knobs moved at the same time.
- Making the UDP bind address *configurable*. Phase 02 picks the
  addresses; exposing that choice as configuration stays phase 03.
  See Decision 4 for where the line falls and why it moved.
- Moving `shakenfist-spice-renderer` off the dead `rtp` 0.17
  crate. It is a dev-dependency there with no coupling to this
  crate's types (Decision 3).
- The soak, the browser matrix, and the RSS/CPU comparison against
  the phase-01 baseline. That is phase 04, and it is where a
  behavioural regression that the test suite cannot see would be
  caught.
- The `profile-level-id` discrepancy recorded in phase 01
  (`42001f` negotiated vs `42e01f` encoded). Pre-existing,
  unrelated to the port, and changing it during the bump would
  confound the phase-04 comparison.

## What the survey found

The master plan's phase-02 section was written in August 2026,
before phase 01 executed. Most of its factual claims about the
tree are now wrong — always in the direction of *less work than
described*, because phase 01 did that work. Every claim below was
checked against develop `e07cfd4f`.

**These claims are false and are corrected in the master plan in
the same commit as this file:**

| Master plan says | Actually |
|---|---|
| `webrtc = "0.17.1"` in two manifests, including a `ryll/Cargo.toml` dev-dependency | One manifest: `shakenfist-spice-webrtc/Cargo.toml:27`. Step 1c removed ryll's, and `ryll` now names no webrtc type anywhere |
| `ryll/src/web/signalling.rs:432-560` holds a `#[cfg(test)]` client peer connection | Gone. `signalling.rs` has no `use webrtc::` line at all; its test drives `TestPeer` |
| Four near-identical client-PC setups to rewrite | One (`TestPeer::build`), plus two deliberately-raw comparison peers inside `test_client.rs`'s own tests, which exist to prove `TestPeer` produces the same SDP |
| `gathering_complete_promise()` at `bridge.rs:429-430` | Gone; `accept_offer` awaits `wait_for_gathering()` on a `StickySignal` |
| `connection_state()` at `bridge.rs:861-865`, off the trait in 0.20 | Both `WebrtcBridge::connection_state` (`bridge.rs:949`) and `TestPeer::connection_state` (`test_client.rs:289`) read a `Mutex` shadow fed by the state-change callback. No raw call remains |
| `RTCRtpTransceiverInit` at `bridge.rs:924`, `:934`, `signalling.rs:439` | Only in `test_client.rs:171`, `:409`, `:564` |
| `bridge.rs` is 1233 lines; region table at `:166-360`, `:420-440`, `:513-545`, `:580-830`, `:861-865` | 1448 lines; `new` at `:295-455`, `accept_offer` at `:492-517`, `send_control`/`close` at `:596-624`, the RTP pumps at `:659-910` |

**What is left, precisely.** Three files, 37 import lines:
`bridge.rs` (18 `use webrtc::` lines), `test_client.rs` (13),
`tests/loopback.rs` (6). `tests/lifecycle.rs` has none —
it drives the bridge through `WebrtcBridge`'s own API and
`StickySignal`. `ryll/src/web/{server,cursor,inputs,lifecycle}.rs`
name no webrtc type, so the reaper and the HTTP layer are
untouched by this phase, exactly as the master plan hoped.

**Three things the master plan did not anticipate:**

1. **`tests/loopback.rs` registers callbacks after construction.**
   `loopback.rs:93` calls `client_pc.on_track(...)` and `:118`
   `client_pc.on_data_channel(...)` (which nests `dc.on_message`)
   on the raw peer connection it reaches through `TestPeer::pc()`.
   In 0.20 the handler is supplied to the builder *before* the peer
   connection exists, so these registrations have nowhere to go
   unless `TestPeer` accepts them at build time. Step 2a does that
   ahead of the bump, on 0.17, where the existing packet-count
   assertions still prove it behaviour-neutral.

2. **`ice_servers` is never plumbed from the CLI.** `ryll` passes
   `ice_servers: vec![]` unconditionally (`signalling.rs:299`), so
   `WebrtcBridgeConfig` has no existing configuration path from
   the command line for phase 03 to extend. Phase 03 is therefore
   slightly larger than the master plan implies — it has to build
   that path, not widen it. Recorded here rather than fixed here.

3. **webrtc 0.20.2 is `edition = "2024"`.** Not a problem — the
   devcontainer ships rustc 1.97.1 and every CI job uses
   `dtolnay/rust-toolchain@stable` with no pin — but it is the kind
   of thing that is much cheaper to know before the build fails.

**Documentation carrying version-specific claims:**
`docs/development.md:221` names `webrtc = "0.17.1"` explicitly, and
`docs/web-frontend.md:226-233` tells operators that "the ephemeral
RTP port range is chosen by the OS", which stops being true the
moment ryll passes a bind address. The second one is only fully
resolved by phase 03; phase 02 must not leave it saying something
false in the meantime.

## What 0.20 turned out to be

The master plan characterised 0.20 as "module reshuffling,
trait-scoped methods, an inverted callback model, and four things
with no direct replacement", and estimated the phase at 1.5–2.5
days on that basis. That characterisation was written from the
docs index and CI error output. Checked against the 0.20.2 source,
it understates the change in three places that matter, and the
estimate goes up accordingly (see Effort).

Citations below are into the published crate sources —
`webrtc-0.20.2/`, `rtc-0.20.2/` — which you can unpack from
crates.io. Re-check them rather than trusting this summary.

### The good news first

- **Gathering ordering is safe**, which was the plan's single
  riskiest unknown. `local_description()` is not a stored string:
  it re-renders from the live ICE agent on every call
  (`rtc-0.20.2/src/peer_connection/internal.rs:355-394`). Each
  candidate is pushed into the core *before* the completion
  sentinel (`webrtc-0.20.2/src/peer_connection/driver.rs:611-651`),
  and the sentinel is what sets gathering state to `Complete` and
  queues the event
  (`rtc-0.20.2/src/peer_connection/mod.rs:1734-1742`). So the
  phase-01 design — await the `Complete` event, then read
  `local_description()` — is correct on 0.20, and gets
  `a=end-of-candidates` into the SDP as a bonus. Note gathering
  does not begin until `set_local_description()`, so the existing
  create-answer → set-local → await → read order is required, not
  merely conventional.
- **`MediaEngine`, `Registry` and `register_default_interceptors`
  all survive**, so codec registration keeps its shape.
- **The `streams` field on `RTCRtpTransceiverInit` is not a
  problem.** The struct derives `Default`
  (`rtc-0.20.2/src/rtp_transceiver/mod.rs:200-208`), so
  `RTCRtpTransceiverInit { direction, ..Default::default() }`
  compiles. The master plan listed this as breakage; it is not.
- **`PeerConnectionEventHandler` is exactly the shape phase 01
  built `BridgeEvents` for**
  (`webrtc-0.20.2/src/peer_connection/mod.rs:141-169`): nine async
  `&self` methods, all defaulted to no-ops, passed as
  `Arc<dyn PeerConnectionEventHandler>` to `.with_handler()`,
  which is the one mandatory builder call
  (`:428-429`). Two renames: our `on_state_change` maps to
  `on_connection_state_change`, and `RTCIceGathererState` becomes
  `RTCIceGatheringState`.

### The three things that are bigger than described

**1. `rtc` becomes a direct dependency, and the `rtp` crate is
dead.** `webrtc` 0.20.2 is a ~9k-line async shim over a new
sans-io core crate, `rtc`, which it does *not* re-export
(`webrtc-0.20.2/src/lib.rs:112-125`). Everything protocol-level
now lives there: `rtp::Packet`, `MediaEngine`, `MIME_TYPE_H264`,
`MIME_TYPE_OPUS`, `Registry`, `register_default_interceptors`,
`RTCRtpCodecCapability`. Meanwhile the standalone `rtp` crate that
phase 01 promoted to a direct dependency ends at 0.17.2 — it is an
abandoned line, replaced by `rtc-rtp`, reached as `rtc::rtp`.
`write_rtp` takes an `rtc::rtp::Packet`, so an `rtp` 0.17 `Packet`
is not merely a different version, it is a different type.

The API our pumps use is essentially unchanged — the `Header` and
`Packet` structs are field-identical, `H264Payloader` and
`OpusPayloader` are byte-identical, and the only source change is
that the module is `codec` (singular), not `codecs`. But the
manifest change is an addition, not a version bump: `rtp = "0.17"`
comes out, `rtc = "0.20.2"` goes in.

**2. Datachannel messages and remote tracks became poll-based.**
This is the item that invalidates phase 01's promise that "the
bodies below do not move again". There is no `on_message` on the
handler trait and no `RTCDataChannel` user type at all. A data
channel is an `Arc<dyn DataChannel>` with
`async fn poll(&self) -> Option<DataChannelEvent>`
(`webrtc-0.20.2/src/data_channel/mod.rs:200`), yielding
`OnOpen | OnMessage(..) | OnClose | ...`. The idiomatic shape,
used by every shipped example, is to spawn a task per channel that
loops on `poll()`. The same is true of remote tracks:
`on_track` hands you an `Arc<dyn TrackRemote>` and `read_rtp()` is
replaced by `TrackRemote::poll()`.

So `BridgeEvents::on_control_message` survives as a function, but
its *callers* change from two callback registrations into two
spawned poll loops — one for the datachannel we create, one per
datachannel the remote opens. `tests/loopback.rs`'s per-track
reader loops change the same way.

**3. Track construction is a rewrite, not an import change.**
`TrackLocalStaticRTP::new` no longer takes
`(capability, id, stream_id)`. It takes a whole `MediaStreamTrack`
(`webrtc-0.20.2/src/media_stream/track_local/static_rtp.rs:70`),
built from `(stream_id, track_id, label, kind, codings)`
(`rtc-0.20.2/src/media_stream/track.rs:165-171`), where `codings`
is a `Vec<RTCRtpEncodingParameters>` carrying the **SSRC and the
codec** — both of which webrtc-rs chose for us in 0.17. An empty
codings vector yields no codec preference and no SSRC, and a codec
that is not registered in the `MediaEngine` fails the `add_track`
with `ErrRTPTransceiverCodecUnsupported`. `write_rtp` also now
takes the packet by value and is a `TrackLocal` **trait** method,
so the trait must be in scope.

### Two facts that change decisions elsewhere

**`with_udp_addrs(vec!["0.0.0.0:0"])` — the master plan's
placeholder — produces a broken deployment.** Binding is a plain
`UdpSocket::bind` over the addresses given
(`webrtc-0.20.2/src/peer_connection/mod.rs:691-697`), and the bound
addresses are the *only* input to host-candidate generation. There
is no unspecified-address filtering anywhere in the stack, so a
`0.0.0.0` bind emits a literal
`a=candidate:... 0.0.0.0 <port> typ host`, which browsers discard.
Every shipped media example binds an explicit interface address.
This is the finding that most changes phase 02's scope — see
Decision 4 — and it is dangerous because **our test suite cannot
catch it**: two Rust peers on one host both see `0.0.0.0`
consistently and connect happily, so `tests/loopback.rs` would go
green on a build no browser could reach.

**Our rustls pin, and eleven `install_default()` calls, delete.**
`rtc-dtls` 0.20.2 selects its crypto provider from its own cargo
features and passes it explicitly, specifically so that it never
consults the process-wide default
(`rtc-dtls-0.20.2/src/config.rs:38-78` — the comment there
describes our exact bug). So
`shakenfist-spice-webrtc/Cargo.toml:67-72` and every
`rustls::crypto::ring::default_provider().install_default()` in
that crate go away. `ryll`'s own rustls dependency and its two
`install_default()` calls **stay** — they serve SPICE TLS and
`axum-server`, and `aws-lc-rs` still enters ryll's graph via
`reqwest → quinn`, independently of webrtc.

This also retires the master plan's stated forcing function. The
plan says a rustls advisory would demand this port with no notice,
because our rustls version is coupled to webrtc's. After 0.20 the
version is governed by `rtc`'s ordinary `^0.23.35` floor, and a
rustls advisory becomes a lockfile bump.

### Other porting hazards, recorded so nobody rediscovers them

- **Handlers are awaited inline in the driver event loop**
  (`webrtc-0.20.2/src/peer_connection/driver.rs:653-681`). A slow
  handler stalls the connection.
  `BridgeEvents::on_state_change` currently does
  `encoder_control.send(...).await` on a bounded channel — that is
  a latent stall on 0.20. See step 2b.
- **`close()` must be called explicitly.** Dropping the peer
  connection on the default runtime detaches the driver task
  rather than stopping it
  (`webrtc-0.20.2/src/peer_connection/mod.rs:838-861`).
- **`connection_state()` has no replacement at all** — not on the
  trait, not on the core. Phase 01's shadow is not a convenience,
  it is the only option.
- **The default SDP changes.** 0.20's `register_default_codecs`
  adds an RFC 4588 RTX codec per video codec, plus HEVC and AV1,
  and `rtc` 0.20.1 stopped advertising ULPFEC (PT 116 disappears).
  The answer SDP will be materially different from 0.17's. That is
  expected, not a regression — but it must be looked at rather
  than assumed, because advertising RTX we never send is a
  behaviour change a browser can act on.
- `RTPCodecType` is now `RtpCodecKind`; mDNS is disabled by
  default in the async builder, so no `.local` candidates; the
  `SettingEngine` setters `set_nat_1to1_ips`, `set_ip_filter`,
  `set_interface_filter` and `set_include_loopback_candidate`
  still compile but are dead code in 0.20 — nothing reads them.

## Decisions

**1. Target `webrtc = "0.20.2"`, not `"0.20"` and not 0.21.**
0.20.0 has a peer-connection driver hot-loop on a non-advancing
timeout, fixed in 0.20.1 — benchmarking phase 04 against a version
with a known CPU-burn bug would produce a meaningless comparison.
0.20.2 additionally stops scheduling ICE checks in terminal
states, which is exactly the path `tests/lifecycle.rs` and the
reaper exercise. 0.21.0-alpha.1 exists and carries a fix we would
like (inbound datachannel messages dropped when the consumer is
slow), but its headline is a deliberate reshaping of the public
API before a 1.0 freeze — porting onto that during a port is the
wrong trade. Declare `0.20.2` so the manifest records the floor we
validated; do not use `=0.20.2`.

**2. Depend on `rtc`, not on `rtc-rtp` directly.** We need
`rtc::rtp::Packet` to call `write_rtp`, and `rtc::…::MediaEngine`
and friends besides. Depending on `rtc = "0.20.2"` guarantees by
construction that our `Packet` is the same type `webrtc` expects,
because `webrtc` pins `rtc` to an exact patch. Aliasing
`rtp = { package = "rtc-rtp" }` would keep our `rtp::` prefixes but
leaves a second version to keep aligned by hand, which is the
class of mistake that produced issue #215.

**3. `shakenfist-spice-renderer` does not move in this phase.**
Its `rtp = "0.17"` is a *dev*-dependency used only by
`tests/webrtc_h264_smoke.rs`, so it shares no type identity with
the webrtc crate and imposes no unification constraint. It should
move eventually — testing a payloader we no longer ship is
pointless, and `rtp` 0.17 is a dead line — but it is independent
work that would enlarge this phase's diff for no coupling reason.
Recorded in Future work.

**4. Bind enumerated interface addresses, not `0.0.0.0`, and do it
in this phase.** This is the decision most likely to be argued
with, because it pulls work toward phase 02 that the master plan
put in phase 03. The argument for deferring is that phase 03 owns
socket configuration. The argument for doing it now — which wins —
is that `0.0.0.0:0` is not a placeholder that works pending
configuration, it is a placeholder that silently produces
unroutable SDP while every automated test passes. Shipping phase
02 on `0.0.0.0` would mean the port is "done", green, merged, and
broken for every real browser until phase 03 lands.

So phase 02 reproduces what 0.17 did internally: enumerate the
host's interface addresses, skip loopback and unspecified, and
bind one ephemeral socket per address. What phase 02 does *not* do
is make any of that configurable — no CLI flag, no config field,
no port pinning. That stays phase 03, which is now "expose the
policy", not "invent it".

**5. Keep `register_default_codecs()` plus the explicit H.264
registration, and diff the SDP.** The alternative — a bare
`MediaEngine` registering only H.264 and Opus — is tempting
because 0.20's defaults now advertise RTX, HEVC and AV1 that we
have no intention of sending. But changing our codec registration
strategy in the same commit as the version bump makes any
negotiation difference unattributable, which is the exact failure
mode this whole plan is structured to avoid. Keep the same calls,
capture the answer SDP before and after, and record the delta in
this file. If the RTX advertisement turns out to matter, it gets
its own change with its own reasoning.

**6. Split the phase into landable pre-work, then one atomic
commit, then follow-ups.** Three of the changes 0.20 forces can be
made against 0.17, where they compile and are covered by the
existing suite: moving `tests/loopback.rs`'s post-construction
callbacks to build time, making the handler bodies non-blocking,
and writing the interface-enumeration helper. Each becomes its own
reviewable commit before the crate stops building. This is the
same trade phase 01 made, and for the same reason: the atomic
commit is unavoidable, but it does not have to carry anything that
could have been reviewed separately.

**7. Delete both Renovate rules, not just the pin.** The master
plan says to keep the patch-disable rule unless phase 02
establishes that 0.20 made the sibling-skew problem impossible. It
is established, from the index metadata, before the phase starts:
`webrtc` 0.20.x requires `rtc` at an exact patch (`^0.20.2`, not
`^0.20.0`), and `rtc` in turn requires every one of its 16 sibling
crates at that same exact patch. The inconsistent set that broke
issue #215 is arithmetically unreachable — cargo cannot resolve
it. Both rules go.

## Step plan

Steps 2a–2c compile and pass tests against 0.17.1 and land as
ordinary reviewable commits. 2d is the atomic commit. 2e cleans up
behind it.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Move `tests/loopback.rs`'s post-construction callback registrations into `TestPeerBuilder`, still on the 0.17 API. Today `loopback.rs:93` calls `client_pc.on_track(..)` and `:118` `client_pc.on_data_channel(..)` on the raw PC from `TestPeer::pc()`; in 0.20 the handler is supplied to the builder before the PC exists, so these have nowhere to go. Add two builder hooks — something like `.on_track(f)` and `.on_data_channel(f)` taking boxed closures that `TestPeerBuilder::build` registers before returning — and migrate `loopback.rs` onto them. Keep the spawned-task shape of the existing `on_track` body and the comment at `loopback.rs:83-89` explaining why it spawns rather than looping inline: that reasoning survives the port, because 0.20 awaits handler methods inline in the driver loop too. `test_client.rs:225-233`'s `pc()` doc warns that the two callback slots are claimed and re-registration is last-writer-wins; update it once nothing re-registers. `make test` must stay green — `loopback.rs` asserts video and audio RTP packet counts, which is the real proof this refactor is behaviour-neutral. |
| 2b | medium | sonnet | none | Make every `BridgeEvents` method non-blocking, on 0.17. In 0.20 handler methods are awaited inline in the peer-connection driver event loop (`webrtc-0.20.2/src/peer_connection/driver.rs:653-681`), so an await inside one stalls the connection. `BridgeEvents::on_state_change` (`bridge.rs:156-160`) currently does `encoder_control.send(EncoderControl::RequestKeyframe).await` on a bounded `mpsc`, and `on_control_message` (`bridge.rs:205-212`) does `incoming_tx.send(data).await` on a 64-slot channel. Switch both to `try_send`, and handle the two error cases distinctly: a closed channel is the existing "receiver dropped" debug case, but a *full* channel is new and must be logged at `warn` with a count or rate limit, because silently dropping a keyframe request or an input event is a real symptom someone will have to debug. Keep the sticky-`dead` logic and the exactly-once logging in the match guard at `bridge.rs:169-182` exactly as-is. |
| 2c | medium | sonnet | none | Write the UDP bind-address helper that step 2d will need, against 0.17 where it is unused but unit-testable. Add `if-addrs = "0.15"` to `shakenfist-spice-webrtc/Cargo.toml` (MIT OR BSD-3-Clause, both on `deny.toml`'s allowlist; `libc` on unix and `windows-sys` 0.61 on Windows, and 0.61.2 is already in `Cargo.lock`, so no new duplicate). Add a function that returns the socket addresses to bind: every interface address, ephemeral port, skipping loopback, unspecified, and IPv6 link-local. Unit-test the filtering with a synthetic address list rather than the live host — a test that enumerates the real interfaces of a CI runner is exactly the host-coupled flake phase 01 had to gate behind `RYLL_GATHERING_SOAK`. Do not wire it into `WebrtcBridge` and do not add a config field; 2d consumes it and phase 03 makes it configurable. Read Decision 4 in this plan first — it explains why this is not premature. |
| 2d | high | opus | worktree | The atomic bump. Read this whole plan first, then `bridge.rs`, `test_client.rs` and `tests/loopback.rs` end to end, then the 0.20.2 sources for anything you are unsure of. Manifest: `webrtc = "0.20.2"`, add `rtc = "0.20.2"`, delete `rtp = "0.17"`, delete the `rustls` dependency and its comment (`Cargo.toml:67-72`). Delete every `rustls::crypto::ring::default_provider().install_default()` in this crate (`bridge.rs:301`, `:1172`, `:1294`, `:1375`; `test_client.rs:374`, `:440`, `:470`, `:537`; `tests/loopback.rs:45`; `tests/lifecycle.rs:39`) with their comments — `rtc-dtls` 0.20.2 passes its provider explicitly and never reads the process default. Do NOT touch `ryll`'s rustls dependency or its two `install_default()` calls; they serve SPICE TLS and axum-server and are still required. In `bridge.rs`: build via `PeerConnectionBuilder` with `.with_configuration(RTCConfigurationBuilder…build())`, `.with_media_engine`, `.with_interceptor_registry`, `.with_handler(Arc::new(events))` (mandatory — `build()` errors without it), and `.with_udp_addrs(step 2c's addresses)`; store the result as `Arc<dyn PeerConnection>` since the builder returns an unnameable `impl PeerConnection`. Implement `PeerConnectionEventHandler` for `BridgeEvents`, mapping `on_state_change`→`on_connection_state_change` and taking `RTCIceGatheringState` in place of `RTCIceGathererState`. Replace both `on_message` registrations with spawned `while let Some(ev) = dc.poll().await` loops that forward `DataChannelEvent::OnMessage` to `on_control_message`, one for the control DC we create and one inside `on_data_channel` for each remote channel. Rebuild both tracks: `TrackLocalStaticRTP::new(MediaStreamTrack::new(stream_id, track_id, label, kind, codings))` with explicit codings carrying the SSRC and the codec — an empty codings vec silently yields no SSRC and no codec preference. `write_rtp` now takes the packet by value and is a `TrackLocal` trait method, so bring the trait into scope. `rtp::codecs::` becomes `rtc::rtp::codec::` (singular). `RTPCodecType`→`RtpCodecKind`. Apply the same treatment to `test_client.rs` (its hooks from 2a become handler methods) and `tests/loopback.rs` (`read_rtp()` loops become `TrackRemote::poll()` loops). Capture the answer SDP from `bridge_accept_offer_returns_answer_with_h264_and_opus` before and after and paste the diff into this plan's "What landed" section. |
| 2e | medium | sonnet | none | Clean up behind the bump. `renovate.json`: delete both webrtc rules — the `allowedVersions: "<0.18"` rule and the patch-disable rule (Decision 7 explains why the second one goes too). `docs/development.md:221`: update the webrtc version and its one-line description. `docs/web-frontend.md:226-233`: the claim that "the ephemeral RTP port range is chosen by the OS" is now wrong in a way that matters to operators reading it for firewall rules — say that ryll binds one ephemeral UDP socket per non-loopback interface address, and note that pinning the port is not yet configurable (phase 03). `AGENTS.md`: the webrtc dependency row, the `rtc` row it now needs, and anything in the `shakenfist-spice-webrtc/src/` tree annotation that describes the callback structure. `ARCHITECTURE.md`: the bridge construction and SDP-flow sections describe three callbacks registered post-construction; that becomes one handler supplied at build time plus per-channel poll loops. |

Dependencies: 2a, 2b and 2c are independent of each other and all
precede 2d. 2e follows 2d.

**Back-brief gate before 2d.** 2a–2c are ordinary refactors and
need no gate. 2d is the commit that cannot be partially reviewed
and cannot be bisected, and it makes two choices this plan can
only specify in prose: the exact shape of the `codings` vectors
that carry our SSRCs and codecs, and the ownership of the spawned
datachannel poll loops (who holds the `JoinHandle`, and what
cancels them on `close()`). Before writing code, the implementing
agent should back-brief those two shapes and the bind-address
wiring, and get agreement. Getting them wrong is cheap to propose
and expensive to redo inside a diff nobody can split.

## Risks and mitigations

**A green test suite on an unroutable build.** The highest-
consequence risk in the phase. If the bind addresses are wrong —
`0.0.0.0`, or loopback only — `tests/loopback.rs` and
`tests/lifecycle.rs` still pass, because both peers are Rust
processes on one host that agree about the bogus address. No
automated check we have today would fail. Mitigations, all three
required: step 2c makes the address selection a separately tested
unit; 2d adds an assertion that no `a=candidate:` line in the
answer SDP carries an unspecified address; and the phase does not
close until a real browser has reached a real SPICE guest
(Definition of done). The reviewer should treat "loopback.rs is
green" as evidence of nothing on this point.

**Advertising RTX we never send.** 0.20's default codec
registration adds an RTX codec per video codec, so the answer SDP
will offer retransmission our pumps do not implement. A browser
that takes us up on it and NACKs will get silence. Mitigation: 2d
records the SDP diff, and the reviewer decides whether it needs
addressing before phase 04's soak rather than after. The fallback
— registering only H.264 and Opus on a bare `MediaEngine` — is
deliberately *not* taken pre-emptively, per Decision 5.

**Leaked driver tasks and leaked poll loops.** 0.20 detaches the
driver task on drop rather than stopping it, and the new
datachannel poll loops are tasks we spawn ourselves. A bridge that
is dropped rather than closed now leaks two kinds of task.
Mitigation: `WebrtcBridge::close` already exists and is called by
the reaper; 2d must make it cancel the poll loops, and the
back-brief gate asks for that design explicitly.
`tests/lifecycle.rs` is the regression test.

**A diff too large to review.** Unavoidable in kind, reducible in
size: steps 2a–2c remove three separable changes from it, and the
plan front-loads the API research so the reviewer is checking
decisions rather than discovering them. If 2d's diff still comes
back with unrelated cleanups folded in, send it back — this is the
one commit in the project where "while I was in there" is
genuinely costly.

**Windows cross-check.** `make check-windows` cross-compiles to
`x86_64-pc-windows-gnu` and is a required CI job. Both new
dependencies claim Windows support (`if-addrs` via `windows-sys`,
which is already in our lockfile at the version it wants; `rtc`
declares no target-specific dependencies at all), but neither has
been built for that target here. Run `make check-windows` as part
of 2d rather than discovering it in CI.

**0.20.2 is very new** — released four days before this plan was
written. It clears our own `minimumReleaseAge: "3 days"` bar, and
the two patch releases before it fix things we care about, but it
has not had long in the wild. Accepted rather than mitigated: the
alternative is 0.20.0 with a known driver hot-loop, which is worse
for a phase whose successor measures CPU.

## Definition of done

Falsifiable, in the order a reviewer would check them:

- `shakenfist-spice-webrtc/Cargo.toml` declares `webrtc = "0.20.2"`
  and `rtc = "0.20.2"`, and declares neither `rtp` nor `rustls`.
- `grep -rn "install_default" shakenfist-spice-webrtc/` returns
  nothing; the same grep against `ryll/src/` still returns its two
  call sites.
- `grep -rn "webrtc = " --include="Cargo.toml" .` matches exactly
  one line in the whole workspace.
- `renovate.json` contains no `allowedVersions` entry for webrtc
  and no rule disabling its patch updates.
- `make test`, `make lint`, `make check-windows`, `make web-smoke`
  and `make web-smoke-tls` all pass, and `pre-commit run
  --all-files` is clean.
- A test asserts that every `a=candidate:` line in an answer SDP
  carries a routable address — no `0.0.0.0`, no `::`, and at least
  one candidate present.
- `tests/lifecycle.rs` passes unchanged in intent: `wait_for_dead`
  fires when the *peer* goes away — the test closes the client
  peer, which is the case that still raises the signal — and the
  second wait takes the sticky fast path. It does not, and must
  not be read to, claim that a locally-initiated `close()` raises
  `dead`; finding 3 below records that it usually does not.
- A real browser has reached a real SPICE guest through `--web`
  with video, audio, keyboard, mouse and cursor all working, and
  the result is recorded in this file with the browser, guest and
  commit SHA. This is the only check that can catch the
  bind-address failure mode.
- The before/after answer SDP diff is recorded in this file, with
  a one-line judgement on each difference — expected, or needs
  follow-up.
- No fact about the webrtc version or the UDP port behaviour is
  stated differently in `docs/development.md`,
  `docs/web-frontend.md`, `AGENTS.md` and `ARCHITECTURE.md`.

## Effort

Three to five days, up from the master plan's 1.5–2.5. The
master plan's estimate assumed the port was import rewriting plus
a handler restructure. Three things it did not know add real work:
the datachannel and remote-track surfaces became poll-based, so
the callback bodies do move after all; track construction now
requires us to supply SSRCs and codec parameters that webrtc-rs
previously chose; and the bind address has to be solved here
rather than deferred to phase 03, because the obvious placeholder
is silently broken.

Against that, two things the master plan feared cost nothing.
Gathering-completion ordering is safe, and the rustls coupling
*subtracts* work — a dependency and eleven call sites delete. The
master plan's own variance statement said the estimate hinged on
the two no-direct-replacement items; phase 01 retired both, and
what replaced them as the driver of the estimate is the poll-based
event surface.

Phase 03 comes down correspondingly: its hard part — deciding what
to bind — is answered here, leaving it to expose the policy as
configuration.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan. Before executing step 2d
specifically, back-brief the three shapes named in the step table:
the `codings` vectors, the ownership and cancellation of the
datachannel poll loops, and the bind-address wiring.

## Status

| Step | State |
|------|-------|
| 2a — `TestPeer` callbacks at build time | Done (`df4d15b7`) |
| 2b — non-blocking `BridgeEvents` | Done (`526d8d67`) |
| 2c — UDP bind-address selection | Done (`0397a656`) |
| 2d — the atomic bump | Done (`41c1e7cf`) |
| 2e — Renovate and docs | Done (`959b14a8`) |

Every Definition-of-done item is met, the browser session
included — with one clause evidenced only indirectly: nobody
listened to the audio. See *Browser verification* under What
landed.

- `make test` (16 suites), `make lint`, `make check-windows`,
  `make web-smoke`, `make web-smoke-tls` and
  `pre-commit run --all-files` all pass.
- The manifest declares `webrtc = "0.20.2"` and `rtc = "0.20.2"`
  and neither `rtp` nor `rustls`; `grep -rn install_default
  shakenfist-spice-webrtc/` is empty while ryll keeps its two;
  `webrtc = ` appears once in the workspace; `renovate.json`
  mentions webrtc nowhere.
- `accept_offer_answer_carries_all_candidates` asserts every
  candidate carries a routable address.
- `tests/lifecycle.rs` is unchanged in intent and passes.

**The browser session was not a formality.** Decision 4 exists
because a wrong bind address leaves the entire automated suite
green — two Rust peers on one host agree about an unroutable
address and connect — so nothing above can distinguish a working
deployment from a broken one. Two findings from 2d sharpen the
same point: the datachannel stream-1 collision means the browser
direction is now exercised only by a test that emulates the
browser, and the new RTX advertisement is something only a real
browser can react to. It earned its keep: it found a defect no
test in the suite can see (see *Browser verification*).

## Two decisions this phase deliberately left open

Both belong to whoever reviews this before phase 04, not to the
port:

1. **We advertise RTX we never send.** `a=ssrc-group:FID` plus a
   second video SSRC. A browser that loses a packet and NACKs gets
   nothing back — no worse than 0.17 in outcome, but it is now
   entitled to expect otherwise, and it costs an SSRC. Decision 5
   deliberately did not pre-empt this; suppressing it is a change
   with its own reasoning, not a port detail. The alternative is a
   bare `MediaEngine` registering only H.264 and Opus.
2. **The `remote-dc` pump is dead code in the common path** now
   that `on_data_channel` does not fire for pre-negotiated
   channels. It is retained because a peer that creates its
   channel *after* negotiation would still land there. If the
   browser session confirms nothing ever arrives that way, it
   could go — but confirm before deleting.

## What landed

### Four things this plan got wrong, found while executing 2d

All four were found by the test suite, none by reading the API.

**1. `write_rtp` validates the RTP header, it does not rewrite it.**
0.17's `TrackLocalStaticRTP::write_rtp` overwrote the packet's SSRC
and payload type from the bind context, so the pumps could invent
their own SSRC and it did not matter. 0.20 instead *rejects* a packet
whose SSRC is not one the track claims
(`rtc-0.20.2/src/rtp_transceiver/rtp_sender/mod.rs:368-374`) and whose
payload type is not negotiated on that sender leg (`:390-410`). This
invalidates Decision-adjacent guidance to leave `ssrc: None` and let
the core fill it randomly: nothing would then know the value, and
every packet would be dropped at the sender with only a debug log —
a connected viewer watching a blank screen. `WebrtcBridge::new` now
picks both SSRCs, puts them in the codings, and hands them to the
pumps, which is what every shipped example does
(`examples/rtp-to-webrtc/rtp-to-webrtc.rs:243`).

**2. Datachannels created before negotiation collide on SCTP stream
1, so `on_data_channel` does not fire.** The stream id is assigned at
creation time from the DTLS role
(`rtc-0.20.2/src/peer_connection/internal.rs:936-954`), and before the
handshake there is no role, so both peers' pre-negotiation channels
get id 1. The peer's DCEP open then arrives for an id already in the
local map and the driver does not announce it
(`webrtc-0.20.2/src/peer_connection/driver.rs:84-101`); its messages
surface on our *own* control channel instead. Production is unaffected
in behaviour — `ryll/src/web/assets/app.js` has a single
`control-seed` channel with an `onmessage` and no `ondatachannel`, so
both directions still work — but the `remote-dc` pump is dead in the
common case, and `tests/loopback.rs` had to move its echo onto the
client's own seed channel to keep testing the real path. Phase 04's
browser check is what confirms this end to end.

**3. A self-initiated `close()` does not reliably deliver `Closed`.**
`close()` sets the driver's shutdown flag and the driver checks it at
the top of every loop iteration, so it usually exits before
dispatching the transition the core queued. `WebrtcBridge::close` no
longer raises `dead` (harmless — the reaper waits on `dead` to decide
whether to close), and `test_client`'s terminal-state test was a
coin-flip race until it was rewritten to set the shadow directly.

**4. Smaller API deltas the import map missed.**
`RTCDataChannelInit::ordered` is a plain `bool`, not `Option<bool>`,
and its `Default` is `true`. `MediaEngine::register_codec` takes an
`RTCRtpCodecParameters` whose codec field is `rtp_codec`, not
`capability`, and it has no `..Default::default()` tail.

### Answer SDP diff, 0.17.1 → 0.20.2

From `bridge_accept_offer_returns_answer_with_h264_and_opus`, against
the same `TestPeer` offer. Judgement on each difference:

| Difference | Verdict |
|---|---|
| Ten `rtx/90000` payload types added (103, 106, 99, 97, 101, 107, 104, 105, 124, 109), one per video codec, each with `a=fmtp:N apt=<primary>` | **Expected** (0.20's `register_default_codecs`), but see the follow-up below |
| `a=ssrc-group:FID <media> <rtx>` and a second video SSRC | **Needs a follow-up decision.** This is the RTX advertisement made concrete: we now name an RTX stream we never send on. A browser that NACKs gets nothing back, same as before, but it is now entitled to expect otherwise |
| `a=rtpmap:116 ulpfec/90000` gone | **Expected** — `rtc` 0.20.1 stopped advertising ULPFEC |
| `a=extmap-allow-mixed` gone | **Expected**; one- and two-byte extension headers are no longer mixed |
| `a=extmap` set grows: `sdes:mid` (1), `sdes:rtp-stream-id` (2), `sdes:repaired-rtp-stream-id` (3), and transport-cc moves from id 1 to id 4 | **Expected** — the simulcast/RTX extensions `register_default_interceptors` now configures. Note our pumps stamp no extensions, and `write_rtp` rejects unnegotiated ones, so this is advertisement only |
| `a=msid-semantic:WMS *` added at session level | **Expected**, and more correct than before |
| m-line payload-type order reversed: video now leads with 126 (H265) rather than 96 (VP8); audio leads with 8 (PCMA) rather than 111 (opus) | **Expected but worth watching.** The payload *numbers* against this particular offer are unchanged — H.264 102, opus 111 — but the order in a `sendonly` answer nominally states our preference. Nothing reads it here, since we send exactly one codec per m-line. Note the numbers are not fixed in general; see the review follow-up below |
| `a=msid:` now precedes the `a=ssrc:` block | Cosmetic |
| Fingerprint, ufrag, pwd, SSRCs, ports differ | Per-run values |

Everything else — the H.264 profile list and payload types, the
`rtcp-fb` sets, the BUNDLE group, `setup:active`, `rtcp-mux`,
`rtcp-rsize`, `a=sendonly`, the msid/mslabel/label names, the
candidate lines — is byte-identical modulo per-run values.

The `profile-level-id=42001f` vs `42e01f` discrepancy phase 01
recorded is unchanged: PT 102 still carries `42001f`. 0.20's codec
matching would not have made it an error either way — it degrades to
a mime-type-only match
(`rtc-0.20.2/src/rtp_transceiver/rtp_sender/rtp_codec.rs:139-163`).

### Browser verification

The Definition-of-done gate, run 2026-08-17.

| | |
|---|---|
| Commit | `7e2fb58e` (`ryll v0.1.7 (7e2fb58e)` in the session log) |
| Guest | shakenfist `debian-xfce:13` via `make test-qemu-desktop`, 1280x1024 surface, SPICE on 5900 |
| Browser | Chromium 151.0.7922.137 (Debian 13) — **pass** |
| Browser | Firefox 140.13.0esr — **video fails**, see below |
| Locked dependency | webrtc/rtc **0.20.3**, not the 0.20.2 the manifest declares and this plan analyses. A Renovate patch bump landed between the port and this session |

Chromium carried a working session: video, keyboard, mouse, cursor
and viewport resize all behaved, and the three log lines
`docs/development.md` names were correct — `main: mouse mode=2
(client (absolute))`, `playback: MODE: 3`, and `web: encoder
restarted at 1280x1024@30fps`.

**Audio was not verified by ear.** `playback: MODE: 3` says the
channel negotiated Opus, and `loopback.rs` asserts audio RTP
reaches the far side, but nobody listened. That leaves one
Definition-of-done clause evidenced only indirectly. It does not
bear on the bind-address question this phase exists to answer, so
it is carried into phase 04 — which holds a browser session open
for minutes anyway — rather than held against the port.

**The bind address is confirmed good.** This was the gate's whole
reason for existing. ICE nominated a host pair on a real interface
address and held it, with consent refreshing on schedule — the
failure mode Decision 4 anticipated (a literal `0.0.0.0` candidate
that every browser discards) is not present.

**Firefox connects but shows no video, and that is a real defect.**
Everything except the picture worked — audio, datachannel, input,
cursor, resize — while the server logged `no H.264 payload type
negotiated` followed by an unbounded `Failed to send RTP:
unsupported codec type by this transceiver`.

The cause is not the codec intersection. Firefox 140 on this host
*advertises* four H.264 entries via
`RTCRtpReceiver.getCapabilities('video')` but emits an offer whose
video section carries only VP8, VP9, AV1, rtx, ulpfec and red — no
`a=rtpmap` H.264 line at any payload type. Its OpenH264 GMP is
present on disk (2.6.0) but never loads. ryll encodes H.264 only,
so there is no common video codec and nothing to negotiate;
`resolve_negotiated_payload_types` reported exactly that.

Chromium by contrast offers H.264 at PT 102 with
`profile-level-id=42001f`, matching the MediaEngine default entry —
which is precisely the divergence the `resolve_negotiated_payload_types`
doc comment predicted, now observed on both sides.

Two defects follow, filed rather than fixed here because neither is
a port regression: a browser with no common video codec gets a black
screen and no explanation (#289), and the video pump keeps encoding
and writing packets that cannot be sent (#290). The session also
motivated an in-browser diagnostics panel (#291) — establishing the
above took `about:webrtc`, a hand-written offer-generating page and
a read through the intersection logic, to recover a fact the browser
knew from the start.

Phase 04 requires Chrome and Firefox at minimum. Firefox is
therefore that phase's gate, not this one's, and #289 should land
before it so the next person to meet this sees a sentence rather
than a black rectangle.

**Not established:** whether anything ever arrives on the
`remote-dc` pump in a real browser session — the open decision
below asks for that confirmation, and this session did not capture
it. Do not delete the pump on the strength of this run.

## Review follow-up

The automated reviewer raised ten items on PR #278 in a first round,
three in a second, seven in a third and ten in a fourth. Seven were
real bugs with no test covering them. Each is fixed here; most carry
a regression test that fails on the pre-fix code, and the two that
do not are recorded as such — under items 15 and 20 — rather than
papered over with a test that cannot fail.

Round 1's items are recorded first, then rounds 2, 3 and 4 under
their own headings below. Round 4 is the last: see "Why the review
loop stops here" at the end.

**1. The pumps stamped a hardcoded payload type (fixed).** The same
0.20 change that made the SSRC load-bearing — `write_rtp` validates
the header rather than rewriting it — applies to the payload type,
and only the SSRC half was handled. Which payload type is negotiated
depends on what the browser offered:
`set_codec_preferences_from_remote_description`
(`rtc-0.20.2/src/rtp_transceiver/internal.rs:299-383`) intersects the
offer with our MediaEngine and remaps each match onto *our* number,
and `register_default_codecs` registers five H.264 entries at
different profile-level-ids. Chrome offers `42001f` and lands on
PT 102, so the constant happened to be right. Firefox offers only
`42e01f`, which matches PT 125 — every video packet would have been
rejected at the sender, logged at `trace` inside the library, leaving
a connected viewer on a black screen.

Fixed by resolving the payload types from the senders' negotiated
parameters after `set_remote_description` and publishing them to the
pumps through an `Arc<AtomicU8>` (the pumps are spawned before
`accept_offer`, so the value does not exist when they start). H.264
selection prefers a `packetization-mode=1` entry, because the
payloader emits FU-A fragments.

Opus was safe by luck rather than design — one Opus entry in the
MediaEngine means any Opus offer remaps onto 111 — and is now
resolved the same way rather than trusted.

Covered by `loopback_media_flows_when_client_offers_a_narrow_codec_set`,
which offers one H.264 fmtp at Firefox's payload numbers. It reports
0 video packets against the old code while every other test stays
green.

**2. The reaper parked forever on a replaced bridge (fixed).**
`run_bridge_reaper` is one long-lived task that watches one bridge at
a time and only advances when its wait returns. This phase documented
that `close()` no longer reliably raises `dead` and judged it
harmless; that was right for the bridge being closed and wrong for
the reaper's loop. A viewer reloading the page would leave the reaper
parked on the old bridge's signal for the life of the process: the
encoder keeps running for nobody, the audio tap keeps feeding a dead
pump, and no later viewer is ever reaped.

Fixed by adding a `WebState::bridge_replaced` notification, raised by
`post_offer` after the generation bump, and selecting on it alongside
the dead signal. `notify_one` rather than `notify_waiters` so a
replacement landing between the reaper's slot read and its park is
not lost. Covered by `reaper_follows_a_replaced_bridge`, which fails
by timeout on the old code — and whose `!first_dead.is_raised()`
assertion independently confirms that `close()` really does leave the
signal unraised.

**Also addressed:** finished datachannel pumps are now pruned on push
rather than accumulating for the life of the bridge; the
empty-bind-address errors no longer assert a cause they cannot know
(enumeration failure and a loopback-only host are indistinguishable
by design, so they point at the `warn` that does distinguish them);
the `raw_peer` test helper reuses the same guard as the real
builders; and four stale comments were corrected — two describing the
0.20 port in the future tense, one rustls rationale in a `ryll` test
that still blamed webrtc 0.17.1's dependency tree, and
`ARCHITECTURE.md` plus `AGENTS.md` still naming the retired
`RTCPeerConnection` Rust type.

**One review item did not survive checking.** The reviewer read the
`Cargo.lock` churn — `getrandom 0.4.3`, `nix` gaining `memoffset`, a
second `quinn-udp` major — as unrelated to the port. Every one of
them traces back to the new dependency tree: `quinn-udp 0.6.1` is a
direct dependency of `webrtc 0.20.2`, `rand 0.10.2` (which pulls
`getrandom 0.4.3`) comes from the whole `rtc` family, and `nix
0.31.3` (which pulls `memoffset`) comes from `rtc-shared 0.20.2`.
Nothing was swept in. `deny.toml` sets `multiple-versions = "warn"`,
so the duplicate `quinn-udp` majors are reported and not fatal.

**Deferred deliberately.** The reviewer's observation that we now
bind and advertise every non-loopback address, with `SettingEngine`'s
filters dead on 0.20, stands as written and belongs with phase 03's
configuration surface — an interface allowlist, not just a port pin.
It is now recorded under Future work in the master plan; it was
not there before this review.

### Second review round

Round 2 raised three items on the round-1 fixes. One was a real bug
introduced by the round-1 reaper fix; it is the reason this section
exists rather than the phase being closed after one round.

**11. The replacement notification could reap a live bridge (fixed).**
The round-1 fix gave the reaper a second wake source but left it
treating any wake as proof that its bridge had died. `notify_one`
stores a permit when no task is parked, and `post_offer` empties
`bridge_slot` at the *start* of the request — so for the whole of the
encoder restart, bridge construction and ICE gathering the reaper is
on its no-bridge sleep path, not in the `select!`. The wake was
therefore stored, and the reaper's next iteration snapshotted the
already-bumped generation, picked up the *new* bridge, consumed the
permit and reaped it. The generation check cannot discriminate this:
it detects a replacement that landed *during* the wait, and this one
landed *before* the snapshot.

This was not a narrow race. It is the ordinary first-connection path
— the reaper is spawned before any offer with the slot empty — so the
round-1 fix turned "no viewer after the first is ever reaped" into
"the first viewer is torn down moments after it connects". That is a
worse failure than the one it fixed, and it survived a full green test
run, `make web-smoke` and `make web-smoke-tls`.

The fix is to gate the reap on `StickySignal::is_raised` — the
condition itself — rather than on the wait having returned. The
generation check stays: it covers the opposite direction, an old
bridge's signal raised late while a new bridge sits in the slot.
`a_replacement_wake_does_not_reap_a_live_bridge` reproduces the
sequence and fails without the gate, while
`reaper_follows_a_replaced_bridge` passes either way — the round-1
test could not see this.

The general lesson, recorded because it is the kind of thing that
recurs: adding a wake source to a loop silently invalidates any
"I woke, therefore X" reasoning the loop was resting on. Waking is
scheduling, not evidence.

**12. Three statements of the dead-signal contract (fixed).**
`ARCHITECTURE.md`'s Phase 6 section, `AGENTS.md`'s `bridge.rs`
annotation and this plan's Definition of done all still said the
signal is raised on `Failed`, `Disconnected` or `Closed` — which
finding 3 above had already contradicted. All three now say the
signal means "the peer went away", and that a locally-initiated
`close()` usually does not raise it. `ARCHITECTURE.md`'s reaper loop
gained the replacement arm and the liveness gate, since a reader who
trusted the old text would conclude both were redundant.
`tests/lifecycle.rs` was correct throughout: it closes the *client*
peer, which is the remote-side case that does still raise the signal.

**13. H.264 selection ignored profile-level-id (fixed).** Taken
rather than deferred, because the premise checked out from source.
`negotiated_h264_payload_type` preferred a packetization-mode 1 entry
but broke ties by intersection order, so it could name a main- or
high-profile payload type. The renderer pins no profile
(`H264Encoder::new` calls `Encoder::new()`), which leaves openh264 at
`iEntropyCodingModeFlag = 0` — CAVLC — per `param_svc.h:164`, and
`encoder_ext.cpp:662` then resolves `uiProfileIdc` to `PRO_BASELINE`.
So the encoder emits profile_idc `0x42` and the SDP could advertise
something else. Mode 1 still outranks profile: mode 0 cannot carry a
fragmented NAL at all, whereas a profile mismatch is decoded from the
SPS anyway. This changes nothing for Chrome or Firefox today; it only
bites a browser that orders a high-profile entry first.

### Third review round

Seven items: two `fix`, two `document`, three `consider`. The two
fixes are both task and socket leaks created by the same 0.20
change — dropping a peer connection detaches its driver rather than
stopping it — which the port documented and then did not fully act
on. Two of the round's items had already been resolved by the
`llm-doc-structure` restructure that landed on `develop` while this
branch was in review, and are recorded as such.

**14. A failed `/offer` leaked the whole bridge (fixed).**
`post_offer` builds the bridge at step 4 and installs it in
`bridge_slot` at step 7. The `?` on step 6's `accept_offer` dropped
the bridge on the floor in between, and on 0.20 a dropped bridge is
not a stopped bridge: the driver task, the UDP sockets bound for ICE
(one per non-loopback interface), the control-DC pump, and the input
relay spawned at step 5c all survive the failed request. Nothing
else could ever reap it, because reaping is driven by the slot it
never reached. The offer cooldown bounds the rate, not the total, so
a client posting malformed SDP in a loop accumulates them for the
life of the process. This is a genuine regression: on 0.17 the drop
stopped the machinery.

Fixed at two levels, deliberately. The call site now closes
explicitly and awaits it, so teardown is finished before the 400 is
returned — that is the deterministic path and the one that matters.
`WebrtcBridge` also gained a `Drop` impl that aborts the pumps and
spawns a best-effort close, because "forgot to close" is silent
everywhere on 0.20, not only here, and a rule nobody can see being
broken is not a safeguard. `close()` sets a flag the destructor
checks, so the two do not both run.

The crate's own `accept_offer_rejects_malformed_sdp` test proves the
SDP is rejected and calls `close()` itself, which is exactly why it
never noticed that production did not. A test that cleans up after
the code under test cannot see the code under test failing to clean
up.

**15. `close()` skipped the pump aborts when `pc.close()` errored
(fixed, and not covered by a test).** `close()` was
`self.pc.close().await?;` followed by draining and aborting the
pumps, so a close error returned before aborting anything — leaking
precisely the tasks the method exists to reap. Reachable in
production: `post_offer` and `run_bridge_reaper` both anticipate that
error and warn-and-continue, so every caller handled it except
`close()` itself. The aborts are now unconditional and the result is
propagated afterwards; the ordering argument in the doc comment
(close first so pumps exit naturally rather than being cancelled
mid-message) is unaffected, because ordering and unconditionality are
separable.

No regression test, and this is a real gap rather than an oversight.
The error branch needs `pc.close()` to fail, which is not inducible
without a fake `dyn PeerConnection` — a test double for the whole
trait, to exercise two lines. `close_drains_the_datachannel_pump_handles`
was checked against the pre-fix code and *passes*, because the happy
path drains the list either way. Recorded here so the next reader
knows the guard is inspection, not CI.

**16. Doc items, mostly overtaken by the docs restructure.** The
reviewer asked for `bind_addrs.rs` in ARCHITECTURE.md's second crate
tree and for AGENTS.md's `lifecycle.rs` annotation to mention the
replacement arm and the liveness gate. Both referred to text that
`llm-doc-structure` (PR #277) deleted: AGENTS.md no longer carries a
source tree at all, and ARCHITECTURE.md's duplicate tree is now the
only one, corrected while re-homing this branch's doc changes over
that restructure.

What did survive is the contradiction the reviewer found underneath
those: AGENTS.md's `StickySignal` convention says one-shot lifecycle
events must never use a bare `Notify` and never `notify_one()`,
"which would leak a permit" — while `bridge_replaced`, added by this
PR, is a bare `Notify` using `notify_one()` *because* the leaked
permit is the point. Both statements are right and they read as
contradictory. The convention now distinguishes the two cases: sticky
for a one-shot fact, bare `Notify` plus an explicit re-check for a
recurring nudge, with the round-2 bug named as what happens when a
loop treats a wake as evidence.

**17. SSRC collision and zero (taken).** Video and audio SSRCs were
drawn independently with no check that they differ or are non-zero.
Both tracks are BUNDLE-ed onto one transport and RFC 8843 §9.2
requires SSRCs to be unique across a BUNDLE group, so a collision
would have a receiver demultiplexing by SSRC misroute or drop one
stream. Before the port this was invisible because the core rewrote
the header; now the value is what the SDP advertises and what
`write_rtp` validates. Probability is ~2^-32 per bridge and it will
realistically never fire — taken anyway because the guard is three
lines and the failure mode (one media stream silently missing, for
one viewer, unreproducibly) is the worst kind to debug.

**18. Loopback-only hosts (documented, not changed).** `new` errors
when `host_udp_bind_addrs()` is empty, so `--web` fails every
`/offer` on a host with no non-loopback interface — including when
browsing from that same host. That is Decision 4 working as intended,
and the failure is loud rather than a browser that mysteriously never
connects. The gap was that the error talks about ICE candidates and
interface enumeration, which does not obviously translate to "bring
up an interface". `docs/web-frontend.md`'s troubleshooting section
now says so plainly. Whether to add an opt-in for loopback-only
operation is left to phase 03's configuration surface.

**19. The `rtc` 0.20.x stack is a fresh security-critical dependency
(acknowledged).** The port swapped a mature DTLS/SRTP/SCTP/STUN tree
for a sans-io reimplementation released days earlier, parsing
untrusted input from every address the host binds. Not a defect in
this PR, and the alternative is strictly worse — 0.17.x is abandoned
and will never be fixed. Recorded as an accepted risk in
`docs/plans/PLAN-supply-chain-followups.md`, where the scanning
policy lives, along with the transitive `winapi` / `bitflags 1.3.2` /
duplicate `quinn-udp` warnings and the intent to treat `rtc-*` as a
watch item on the weekly `cargo audit`. Confirmed against CI rather
than by inspection: the `cargo audit` and `cargo deny` lanes both
pass on the ported tree.

**Convergence.** Round 3 is 2 fix / 2 doc / 3 consider, against
round 2's 1 / 1 / 1 and round 1's 3 / 3 / 4. The fix count went *up*,
which is the signal worth taking seriously rather than explaining
away — but both fixes are the same defect class (0.20's detach
semantics) in code the port wrote, not in machinery added by
review rounds, and the `Drop` backstop closes the class rather than
the two instances. That is the distinction between a converging loop
and a generator feeding on its own output. The rule stands: land when
no `fix` items remain, and the browser check is still the gate that
matters more than any of this.

### Fourth review round

Ten items: two `fix`, five `consider`, three `info`. Items 1, 2, 3
and 5 were taken; 4, 6 and 7 were declined with reasons; the three
`info` items needed no action, and one of them (10) was the reviewer
confirming that round 3's `StickySignal` convention change was the
right way to handle a genuine exception to a documented rule.

Both `fix` items land in code this PR wrote, and the first lands in
machinery *added by round 3* — which is the signal that stopped the
loop. See the end of this section.

**20. `close()` disarmed the `Drop` backstop before doing the work
(fixed, and not covered by a test).** Round 3 added a `Drop` impl so
a bridge that is dropped without `close()` still gets cleaned up,
and keyed it on a `closed` flag that `close()` set as its *first*
statement. Cancel that future between the store and the aborts and
`Drop` takes its early-return path and does nothing: the pumps are
never aborted, and the driver task and its UDP sockets leak. That is
reachable — `post_offer` is an axum handler whose future is dropped
when the client disconnects, and `run_bridge_reaper` is aborted by
the shutdown path while it may be inside `close()`. The backstop
added to catch silent leaks was guaranteed not to fire on exactly
the path where it was needed, which is worse than not having one,
because it looks like there is one.

Fixed by moving the store after the aborts. Both cleanup steps are
idempotent, so a cancelled `close()` now falls through to `Drop`
correctly and a completed one still skips it.

No regression test, and a test was written and then deleted rather
than kept. Reaching the window needs `pc.close()` to return
`Pending` at least once; measured in-process, both connected to a
`TestPeer` and not, it completes on the first poll, so the test
passed with the statements in either order. A test that cannot fail
reads as coverage without being any — the same objection the round
raised about the renderer's H.264 smoke test, and it applies here
too. The ordering is guarded by the comment at the store.

**21. `on_control_message` dropped browser input events for a reason
that had stopped being true (fixed).** It used `try_send` on the
64-slot `incoming_tx`, justified by "this method is awaited inline
in the driver loop". After step 2d that is false: its only caller is
`run_dc_pump`, which both `new` and `on_data_channel` `tokio::spawn`.
The same file said so eleven lines above the justification, and the
two were never reconciled.

The cost was real rather than theoretical. That channel carries the
browser's keyboard, mouse and resize events over an ordered,
reliable datachannel. If the consumer stalls — the SPICE inputs
channel on a slow network is the ordinary case — the slots fill and
events are discarded mid-stream. A dropped key-up leaves a modifier
stuck down in the guest, which a user can only report as "my
keyboard went weird". Awaiting instead parks that one datachannel's
poll loop and lets SCTP flow control push back on the browser, which
is what an ordered reliable channel is for.

`on_state_change` keeps `try_send`: it really is dispatched inline.
The distinction is now stated on the type, on both methods, and in
AGENTS.md, because the over-broad version of the rule — "anything
that needs to hand off must use `try_send`" — had already been
copied into the conventions file where the next author would inherit
it. The rule is about the dispatch path, not the type.
`a_full_control_channel_applies_back_pressure` replaces the test
that asserted the old dropping behaviour, and fails on the pre-fix
code.

**22. `new` leaked the peer connection on a post-`build()` failure
(taken).** Between `build()` and `Ok(Self { .. })`, `new` used `?`
on two `add_track` calls and one `create_data_channel`. On any of
them the `Arc<dyn PeerConnection>` was dropped with the driver
running and the sockets bound, and the `Drop` backstop could not
help because `WebrtcBridge` had never been constructed. Same leak
class as item 14, one layer earlier. The fallible part is now
`attach_tracks_and_control_dc`, so there is one error path, and it
closes the peer connection before returning.

**23. Payload type re-read per packet (taken).** `run_video_pump`
loaded the negotiated payload type inside the per-packet loop, so a
store landing mid-frame could split one access unit across two
payload types — which a receiver reads as two streams and
reassembles as neither. The load is now hoisted to once per access
unit, and once per input packet in both audio pumps. The window
closes before DTLS is up, so this is defensive rather than a live
defect.

**Declined, with reasons:**

- **Item 4 — the failed `/offer` leaves the encoder and audio pump
  running.** Correct, and bounded: the next successful offer
  restarts the encoder and replaces `active_opus_tx`, retiring both
  pumps. The residue is a CPU cost until the next offer, not
  unbounded growth. Factoring the reaper's teardown into a shared
  helper is the right fix and is worth doing on its own, not
  bolted onto the end of a port that has already had four review
  rounds. Rides to the auto-filed issue.
- **Item 6 — `accept_offer` waits for ICE gathering with no
  timeout.** Pre-existing, inherited from phase 01's `gathered`
  signal, and unchanged by this port. Adding a timeout changes
  behaviour on a path the port did not touch, which is exactly what
  makes a regression unattributable in phase 04's comparison
  against the phase-01 baseline.
- **Item 7 — the renderer's H.264 smoke test exercises the
  abandoned `rtp` crate.** The sharpest of the three, and the
  argument is accepted: a green test against a payloader we no
  longer ship reads as coverage it does not provide. It is already
  recorded under the master plan's Future work, and moving it means
  touching a second crate's dev-dependencies during the bump.
  Deferred, not dismissed.

**Why the review loop stops here.** Four rounds, thirty items, seven
real bugs. Rounds 1 and 2 found defects in the port; rounds 3 and 4
each found a defect in the machinery the *previous round* added —
round 4's item 20 is a bug in round 3's `Drop` impl, and item 22 is
the same leak class one layer up. That is the documented signature
of a generator loop rather than a converging one, and the fix count
rising from 1 to 2 while the diff grows is the second signature.

The counter-argument, which is why round 4 was worth running: the
bugs are real, and item 21 would have cost users dropped keystrokes.
But the remaining risk in this PR is no longer the kind static
review finds. The reviewer's own item 8 says so — the browser check
is "the highest-value check remaining and the automated suite is
structurally unable to substitute for it". Three of the four rounds
have found defects that a fully green suite could not see, and none
of them would have been caught by a fifth round either; they were
caught by reading. So: no fifth round. The remaining `consider`
items ride to their auto-filed issues, and the gate is the browser.
