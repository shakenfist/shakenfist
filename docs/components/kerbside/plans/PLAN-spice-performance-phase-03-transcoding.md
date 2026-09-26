# Phase 3: display transcoding in Kerbside, a feasibility spike

Master plan: [PLAN-spice-performance.md](/components/kerbside/plans/PLAN-spice-performance/).
Planning effort: high, per the master plan. The spike crosses
two repositories (ryll's crates and kerbside's proxy) and
depends on spice-server and spice-gtk behaviour that the
master plan left unverified.

The master plan's phase 3 section is the brief this plan
refines: its reasons, output modes, out-of-scope list and
risks stand unless this plan says otherwise. What follows
grounds it in the code as of 2026-09-26 and changes the
design where the code disagreed with the sketch.

## Situation

Sources read on 2026-09-26: kerbside develop 764183f, ryll
develop 8b70419 (kerbside pins 0f297a68), spice-server
91d42c4d (0.16.0 plus 19 commits), spice-gtk v0.43-2-g88ad5f1,
qemu 30e8a06b64. Line numbers are pointers; re-read before
building on them.

### The proxy today

- **Handshake order is fixed by the protocol.** `session.rs`
  reads the client's link message (161-171), sends the link
  reply (266-272), then reads and decrypts the ticket (273),
  authorises over gRPC (279-290), and only then dials the
  backend (`backend::run`, 355). The reply carries the RSA key
  the ticket is encrypted with, so it cannot wait for the
  backend. **The caps Kerbside offers the client therefore
  cannot depend on the backend's caps or on any decision made
  at authorisation**, including "transcode this session".
- **The client's caps are parsed and discarded.**
  `SpiceLinkMess` exposes `common_caps` and `channel_caps`, but
  nothing in the proxy reads them. The reply is
  `common_caps: [11]`, `channel_caps: [9]` for every channel
  type (`session.rs:262-271`). Common 11 is AUTH_SELECTION,
  AUTH_SPICE and MINI_HEADER. Channel 9 means SIZED_STREAM and
  A8_SURFACE on display, the two migration caps on main, and
  something else again on other channel types.
- **The backend leg always sends Ryll's defaults.**
  `perform_link` (ryll `link.rs:583-630`) hard-codes
  `DEFAULT_DISPLAY` for display, `DEFAULT_SPICEVMC` for
  usbredir and `DEFAULT_MAIN` for everything else, with no
  caps parameter at either the pinned rev or develop.
  `SpiceClient::connect_channel` also drops the backend's
  `SpiceLinkReply`, so the proxy cannot learn the server's
  caps. This is #477.
- **The relay assumes the mini header on both legs.** ryll's
  `MessageHeader` is the 6-byte mini header only
  (`messages.rs:7-40`) and the relay frames on it
  (`relay.rs:263-268`). A client that links without
  MINI_HEADER would break framing today; nothing else in the
  relay or firewall depends on caps (L1 is opcode-only, L0 is
  size and rate).
- **No channel-type seam exists.** `relay::run` is generic.
  The `Policy` trait can forward, drop or terminate a frame
  but cannot rewrite or inject, so a transcoder is not a
  policy. The natural seam is the `relay::run` call in
  `backend.rs:170-180`, but the transcode decision must be
  made before `connect_once` (`backend.rs:87`), because the
  backend caps differ in transcode mode.
- **Per-connection policy arrives in the authorisation
  reply.** `AuthorizeConnectionReply` carries a `Target` and a
  `FirewallPolicy` (`kerbside.proto:117-168`), filled by
  `servicer.py:build_firewall_policy()`. Any proto edit changes
  the contract hash and needs regenerated stubs. Proxy flags
  (`main.rs:72-148`) are process-wide.
- **Nothing tests the handshake.** There are no unit tests of
  the client link or the backend link in the proxy. Every
  harness (direct-qemu, the shaped-link rig) uses Ryll as the
  client, and Ryll advertises exactly the caps the backend leg
  sends today, so passthrough and today's behaviour are
  indistinguishable there.

### Ryll's crates

- **Server-role support stops at the link.** The protocol
  crate can parse a client link, send a reply, read a ticket
  and send an auth result; `make_message` and
  `MessageHeader::write` work in either direction. Every
  display-channel server message (SURFACE_CREATE, MARK,
  DRAW_COPY, the STREAM_* family, INVAL_ALL_*,
  MONITORS_CONFIG, SET_ACK) has a reader and no writer, and
  there is no mock server that emits display messages.
- **The renderer is a client that owns its socket.**
  `DisplayChannel::new` (renderer `channels/display.rs:765`)
  takes a `SpiceStream`, sends DISPLAY_INIT itself, answers
  SET_ACK and PING, and unconditionally sends
  PREFERRED_COMPRESSION(AUTO_GLZ) and
  PREFERRED_VIDEO_CODEC_TYPE([H264, MJPEG]) (950-957). There
  is no feed-bytes API, but kerbside's backend connect already
  returns a `SpiceStream`, so the stream can be handed over
  as-is.
- **Damage exists only as events.** Decoded output arrives as
  `ChannelEvent`s (`ImageReady`, `FillRect`, `CopyBits` and
  so on, each with a rect), applied to a `SurfaceMirror` whose
  `DisplaySurface` keeps one dirty bool. A transcoder has to
  accumulate damage from the event stream.
- **Renderer coverage.** It decodes QUIC, LZ_RGB, GLZ_RGB,
  ZLIB_GLZ, LZ4, JPEG, 32-bit bitmaps and FROM_CACHE, and
  MJPEG and H.264 streams. LZ_PLT, SURFACE, FROM_CACHE_LOSSLESS
  and JPEG_ALPHA are unimplemented, as are ROP3, STROKE, TEXT
  and COMPOSITE draws, and off-screen surfaces are never
  composited. qemu's own display path (`ui/spice-display.c`,
  used by virtio-gpu and std-vga) only produces bitmap draws on
  surface 0, so the spike is sound for those guests. A guest
  running the QXL driver can emit the unimplemented operations.
- **The renderer's dependencies are heavy.** It pulls tokio
  "full", nusb, dav-server, hyper, openh264 and the `image`
  crate, plus cpal and opus behind the default `audio`
  feature. openh264 built from source is exactly the licensing
  problem in the master plan's H.264 risk, so **the renderer
  must not reach the kerbside-proxy wheels on PyPI** until
  that is decided.
- **Decode runs inline on the async task.** GLZ, QUIC, JPEG
  and H.264 decode are not on `spawn_blocking`. Fine for a
  spike; a productised design runs a worker per session anyway
  (master plan, Security risk).
- **No JPEG encoder in production code.** `mozjpeg`
  (`=0.10.13`, SIMD) and `image` with `jpeg` are already in the
  dependency tree, used by tests and the swatch generator.
- **The webrtc bridge is the nearest precedent.** `ryll --web`
  fans events out to a `SurfaceMirror` and feeds a
  `FrameSource` into an `EncoderTask`. Its fan-out and encoder
  task shapes carry over; its bridge does not, and it is
  full-frame with no damage regions.

### spice-server and spice-gtk

- **A lossless-only backend is achievable, but needs three
  things.**
  - MULTI_CODEC with no CODEC_* bits: `dcc_create_video_encoder`
    returns no encoder (`server/video-stream.cpp:801-835`), no
    STREAM_CREATE is sent (`dcc-send.cpp:2216-2219`), and
    frames go out as ordinary draws. Without MULTI_CODEC the
    server still falls back to MJPEG.
  - No STREAM_REPORT cap, and tolerance of STREAM_CLIP and
    STREAM_DESTROY for stream ids that were never created: the
    server still marshals those for its internal streams
    (`dcc-send.cpp:2251-2279`).
  - PREFERRED_COMPRESSION set to LZ4 (or LZ). Draws otherwise
    still become lossy JPEG whenever `enable_jpeg` is on and the
    compression resolves to QUIC (`dcc.cpp:660-667`), and
    `enable_jpeg` turns on under qemu's default
    `jpeg-wan-compression=auto` when the main-channel net test
    reports under 10 Mbit. In relay mode that net test measures
    the client's real link, so a slow client already gets JPEG
    draws today.
- **The ACK window is counted in messages.** spice-server
  blocks a channel once more than twice the client window is
  unacknowledged (`red-channel-client.cpp:636-642`); the window
  is 20, or 40 if the net test found low bandwidth, and is set
  when the channel connects. That caps a display channel at
  roughly 41 or 81 messages per round trip, however small they
  are. The mechanism has not been observed on the rig. Kerbside
  sees every SET_ACK, server message and client ACK in relay
  mode, so it can compute the in-flight count directly.
- **A server that never sends SET_ACK is never ACKed.**
  spice-gtk's ACK counter only starts on SET_ACK
  (`channel-base.c:27-39`, `spice-channel.c:2141-2149`). A
  terminating Kerbside can skip ACK flow control on the client
  leg entirely and pace from the socket. On the backend leg the
  renderer already does ACKs at LAN speed.
- **The minimum display-server sequence** that spice-gtk
  accepts: optional SET_ACK, read DISPLAY_INIT, optional
  INVAL_ALL_PALETTES, SURFACE_CREATE for surface 0 with the
  PRIMARY flag, a full-surface draw, MONITORS_CONFIG (only if
  the MONITORS_CONFIG cap was advertised, otherwise the widget
  shows the whole surface), then MARK, without which the widget
  never becomes ready (`spice-widget.c:278-280`).
- **JPEG draws are synchronous; MJPEG streams are not.** A
  DRAW_COPY with a JPEG image is decoded in order on the
  display coroutine (`canvas_base.c:469-497`; one data chunk,
  sizes must match; set no cache flags). Stream frames are
  scheduled against the client's multimedia clock, dropped
  when their margin is negative, and painted later from the
  main loop (`channel-display-mjpeg.c:172-245`). A queued frame
  can paint over a later draw. spice-server 0.16.0 stopped
  sending the client a clock 400 ms behind (7e56fc9f), so
  margins are now around zero. A transcoder emitting streams
  would have to track the client's clock from the relayed main
  channel.
- **No message resets the client's GLZ window.** A GLZ image
  referring to an id the client lacks blocks the display
  coroutine indefinitely (`decode-glz.c:149-166`). INVAL_ALL_PIXMAPS,
  INVAL_ALL_PALETTES, STREAM_DESTROY_ALL and a surface destroy
  and create reset everything else. Both the pixmap cache and
  the GLZ window belong to the client session, not the channel.
- **The backend display channel can probably reconnect on its
  own.** `reds_handle_other_links` accepts any channel whose
  connection id matches a live main channel, rejecting only a
  duplicate that has not yet been torn down, and a reconnect
  replays the whole `dcc_start` sequence with fresh caches.
- **The 0.15.2 connect crash is unfixed at HEAD by reading.**
  `~VideoStreamClipItem` dereferences `agent->dcc`, which is
  null until `dcc_create_all_streams` runs, and new clients
  join the channel's client list before that. A lossless
  backend does not avoid it, because the server still creates
  streams internally. Not reproduced against HEAD yet.

## Mission

Answer, with measurements, whether Kerbside should terminate
the display channel and re-encode it for clients on slow
links. Along the way, fix #477, which the spike needs and
which is a product bug in its own right.

Specifically:

1. Make link capabilities per connection (#477): in ryll's
   crate, then in the proxy.
2. Confirm or refute that spice-server's ACK window is what
   caps display throughput on a long link, by observing it
   from the proxy.
3. Build a transcoding display channel behind a build feature
   and a proxy flag, against a lossless-only backend.
4. Measure it against the re-baseline, including the case the
   re-baseline left open: links slower than 10 Mbit and image
   quality.
5. Find out whether a session can move between relay and
   transcoding while it runs.
6. Recommend go or no-go, with phase plans for productising if
   go.

## Design decisions

These refine the master plan's sketch. Each is the default the
steps assume; the open questions below say which need a
decision from Michael.

- **Client-leg caps are a fixed set per channel type.** They
  cannot be negotiated with the backend (Situation). The reply
  carries, for each channel type, the caps spice-server itself
  advertises for that type, masked to what Kerbside relays.
  That replaces the single `9`, which means different things on
  different channels. If a backend lacks a cap Kerbside offered,
  the client may send a message that server ignores or refuses;
  the ryll change below returns the backend's reply so the proxy
  can at least log the mismatch.
- **The client's caps are forwarded to the backend, with
  MINI_HEADER required.** A client that links without it gets a
  link error rather than broken framing. Forwarding makes
  spice-server encode for the real client, which is the point of
  #477.
- **The transcode decision is made at connect and is
  process-wide during the spike.** A proxy flag
  (`--transcode-display`, off by default), not a proto field:
  it avoids contract-hash churn for an experiment. A per-source
  or per-session carrier in `AuthorizeConnectionReply` is a
  productising decision.
- **The renderer stays out of release builds.** The transcoder
  lives behind a cargo feature in kerbside-proxy, off by
  default and not enabled by `tools/build-proxy-wheel.sh`. The
  ryll side feature-gates what the transcoder does not need
  (usbredir, webdav, audio, openh264) so the dependency is only
  the display decode path.
- **Server-role display serialisers go in ryll's protocol
  crate,** next to the parsers they mirror, as the rust-proxy
  plan's server primitives did. Each is tested by round-tripping
  through the existing reader.
- **The first output is JPEG and lossless draws, not a
  stream.** JPEG DRAW_COPY is ordered and needs no clock, and
  carries the same bytes as an MJPEG frame. A damaged region is
  sent as JPEG while it keeps changing and re-sent lossless
  (LZ4) once it has been quiet for a short interval, which is
  the image-quality case transcoding is now argued on. Streams
  are a later step, taken only if the measurements show per-draw
  overhead matters, because they bring the multimedia clock
  problem.
- **The client leg has no ACK window.** The transcoder sends no
  SET_ACK, and paces by writing only when the client socket's
  unsent bytes are below `TCP_NOTSENT_LOWAT`. Between writes,
  damage accumulates and superseded content is never sent.
- **The backend leg is lossless and non-GLZ.** MULTI_CODEC with
  no CODEC_* bits, no STREAM_REPORT, and PREFERRED_COMPRESSION
  set to LZ4, which also keeps a later switch back to relay
  from depending on GLZ state.

## Open questions

1. **The flag and the feature.** Decided 2026-09-26: a
   process-wide flag behind a non-default build feature. A
   proto field would cost a contract change for something that
   may be a no-go; the per-session carrier is a productising
   decision.
2. **Pin bump side effects.** #477 needs a ryll pin bump from
   0f297a68 to develop, which also narrows the backend TLS trust
   set: with a `ca_cert` configured, develop trusts only that CA
   rather than that CA plus the public web roots (ryll 3050082).
   Decided 2026-09-26: accept it, since hypervisor
   certificates come from the configured CA, and document it in
   `docs/` and the release notes.
3. **Client-leg caps for channels spice-server does not own.**
   The "caps spice-server advertises" rule covers display,
   main, cursor, inputs, playback, record and usbredir. Port and
   webdav channels are relayed; step 1b checks what caps, if
   any, they carry.

## Steps

Ryll steps land as ryll pull requests first, following ryll's
`AGENTS.md`; kerbside steps then bump the pin. Kerbside's Rust
builds run through `make -C rust/kerbside-proxy` in Docker, and
ryll's through its own Makefile, never with a host toolchain.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | ryll: per-connection link caps in `shakenfist-spice-protocol`. See brief notes. |
| 1b | high | opus | none | kerbside: bump the ryll pin, forward client caps, per-channel-type reply caps, handshake tests. Fixes #477. See brief notes. |
| 2 | high | opus | none | kerbside: observe the ACK window from the relay, then measure it on the shaped-link rig. See brief notes. |
| 3a | high | opus | none | ryll: server-role display serialisers, a configurable display channel, and a slim renderer feature set. See brief notes. |
| 3b | high | opus | worktree | kerbside: the transcoding display channel behind a feature and a flag. See brief notes. |
| 4 | high | opus | none | Measure transcoding against the re-baseline, including sub-10 Mbit links, CPU and image quality. See brief notes. |
| 5 | high | opus | worktree | Test moving a live session between relay and transcoding. See brief notes. |
| 6 | high | opus | none | Write up the spike with a go or no-go recommendation. See brief notes. |

Steps 1a and 1b come first. Step 2 depends only on the relay
and can run beside 1a. Step 3a can start once 1a has merged,
and 3b needs 1b and 3a. Steps 4 and 5 need 3b. The management
session reviews and commits each step, and records each merge
in this plan.

Brief notes for 1a:

- Add `perform_link_with_caps(stream, connection_id,
  channel_type, channel_id, common_caps: &[u32],
  channel_caps: &[u32])`, with `perform_link` becoming a wrapper
  that passes today's defaults, so existing callers are
  unchanged.
- Add a way for `SpiceClient` to use it: either
  `connect_channel_with_caps`, or an optional caps override on
  `ConnectionConfig` (which is built per connection in kerbside,
  so either works). Pick one and say why.
- Return the backend's `SpiceLinkReply` (or at least its caps)
  from the new connect path, and drop the `#[allow(dead_code)]`
  on its caps fields.
- `perform_auth` assumes AUTH_SELECTION was granted. Make the
  new path check the reply's common caps and fail cleanly if
  AUTH_SELECTION or MINI_HEADER is missing.
- Tests: extend the duplex round-trip tests in `link.rs` to
  cover multi-word caps and the returned reply.
- Keep the change additive; say in the pull request that
  kerbside will consume it.

Brief notes for 1b:

- **Pin bump.** Bump `rust/kerbside-proxy/Cargo.toml`'s ryll rev
  to a develop commit that contains 1a. `build_config`
  (`backend.rs:197-219`) is a full struct literal and needs the
  new `proxy: None` field. Document the trust-set narrowing
  (open question 2) in `docs/` and in the release notes.
- **Client leg.** Keep the parsed client `SpiceLinkMess` and
  pass its caps through `serve()` to `backend::run`. Refuse a
  client whose common caps lack MINI_HEADER, with a link error.
  (As built: logged, not audited. The refusal precedes
  authentication, and `kerbside/api.py` deliberately writes no
  audit rows for unauthenticated rejections.)
- **Reply caps.** Replace the fixed `[11]`/`[9]` with a table
  per channel type. Derive each row from what spice-server
  advertises for that channel type (read
  `/srv/src-reference/spice/spice/server/`, e.g.
  `display-channel.cpp:2229` and each channel's
  `set_cap` calls), masked to what Kerbside relays. Check that
  the L1 allowlist admits every opcode those caps enable, and
  cite the source line for each row in a comment.
- **Backend leg.** Dial with the client's caps (MINI_HEADER
  forced on) through the 1a API, and log the backend's reply
  caps at debug level, with a warning when the backend lacks a
  cap Kerbside offered the client.
- **Tests.** Add a Rust test of the whole handshake: a fake
  client sending distinctive caps, a fake backend using ryll's
  `read_link_mess`, and assertions on the caps the backend
  received and the reply the client received. `handle_connection`
  takes a `SpiceStream`, which cannot wrap a duplex, so a small
  refactor may be needed; keep it minimal and say what it was.
- **End to end.** In the direct-qemu harness, connect with
  remote-viewer (whose caps differ from Ryll's) and show that
  the backend now receives its caps, for example by checking
  that no H.264 STREAM_CREATE reaches a client without
  CODEC_H264. Report how you checked.
- **Docs.** Update `docs/` wherever the handshake or caps are
  described, and close #477 from the commit.
- **Checks.** `tox -e flake8`, `tox -e py3`, the crate's tests
  and clippy.

Brief notes for 2:

- **Observation, in the relay.** Track, per display and cursor
  channel, the SET_ACK generation and window, the count of
  server-to-client messages since SET_ACK, and client ACKs. The
  in-flight count is messages sent minus ACKs times the window.
  spice-server blocks once in-flight exceeds twice the window.
  Expose it as a Prometheus metric or at debug level (choose,
  and justify against the metrics conventions in `docs/`), and
  count how often in-flight reaches the block threshold.
- **Keep the relay byte-exact.** This is observation only; it
  must not change what is forwarded.
- **Measurement.** Run the shaped-link rig at 80 ms / 50 Mbit
  and 80 ms / 10 Mbit, heavy activity, `streaming-video=all`
  and off. Report the window size in use (20 or 40, which also
  says what the net test concluded), the fraction of time the
  channel sat at the threshold, and whether throughput tracks
  roughly 2W+1 messages per round trip.
- **Output.** A section in
  `docs/performance/streaming-rebaseline.md`, or a sibling page,
  saying whether the mechanism is confirmed. A refutation is a
  result, and it weakens transcoding's case: say so.

Brief notes for 3a:

- **Serialisers.** Add writers, in the protocol crate, for the
  display server messages the transcoder emits: SURFACE_CREATE,
  SURFACE_DESTROY, MARK, RESET, INVAL_ALL_PIXMAPS,
  INVAL_ALL_PALETTES, DRAW_COPY with a JPEG or LZ4 image
  descriptor, MONITORS_CONFIG and STREAM_DESTROY_ALL. Add
  readers for DISPLAY_INIT and the PREFERRED_* messages from the
  server's side. Round-trip every writer through the existing
  reader in tests. Streams are not needed yet.
- **LZ4 image encoding** in the compression crate, matching the
  decoder the renderer already has; test by round trip.
- **JPEG encoding.** A small encoder wrapper over `mozjpeg`,
  already a dependency, producing the single-chunk baseline JPEG
  spice-gtk's `canvas_get_jpeg` expects.
- **Configurable display channel.** Let a caller of
  `DisplayChannel` choose the PREFERRED_COMPRESSION value (or
  none) and whether PREFERRED_VIDEO_CODEC_TYPE is sent, instead
  of the unconditional AUTO_GLZ and [H264, MJPEG]. Defaults stay
  as today for Ryll.
- **Slim features.** Make it possible to depend on
  `shakenfist-spice-renderer` for display decode without
  usbredir, webdav, audio or openh264. Report the resulting
  dependency tree. If openh264 cannot be separated cleanly from
  stream decode, say so; MJPEG decode must remain.

Brief notes for 3b:

- **Feature and flag.** A `transcode` cargo feature, off by
  default, gates the renderer dependency and the new module.
  `--transcode-display` (process-wide, off by default) enables
  it at run time. Confirm the release wheel build does not
  enable the feature.
- **Seam.** In `backend::run`, before dialling: a display
  channel in transcode mode dials with MULTI_CODEC and no
  CODEC_* bits, no STREAM_REPORT, and otherwise the client's
  caps. Everything else goes to `relay::run` unchanged.
- **Backend half.** Hand the backend `SpiceStream` to ryll's
  `DisplayChannel`, configured to send PREFERRED_COMPRESSION LZ4
  and no codec preference. Apply its events to a
  `SurfaceMirror`, and accumulate damage rectangles from the
  events. Ignore STREAM_CLIP and STREAM_DESTROY for unknown
  stream ids.
- **Client half.** Answer as a display server with the sequence
  in Situation: read DISPLAY_INIT, SURFACE_CREATE for the
  primary, a full lossless draw, MARK, and MONITORS_CONFIG only
  if the reply advertised that cap. No SET_ACK. Firewall the
  client's messages with the existing L1 policy for the display
  channel.
- **Pacing.** Write only when the client socket's unsent bytes
  are under the low-water mark; otherwise keep accumulating
  damage. When writable, send the damaged region: JPEG if it
  changed within the last interval, LZ4 once it has been quiet
  for that interval (make both the interval and the JPEG
  quality flags). Coalesce overlapping rectangles, and bound the
  number of rectangles per flush.
- **Surface changes.** Resize and surface destroy from the
  backend are forwarded as a new SURFACE_CREATE and a full draw.
- **Lifecycle.** Honour the session's `CancellationToken`, emit
  the same audit event and byte metrics as the relay, and tear
  down both legs together.
- **Tests.** Unit-test damage accumulation and the pacing
  decision. An integration test drives the terminator with a
  fake backend emitting canned display messages and checks the
  client-side output parses with ryll's readers.
- **Scope.** One display channel, primary surface only, no
  streams on either leg, virtio-gpu or std-vga guests. Refuse
  (fall back to relay, and log) on anything else: a second
  display channel, or an unimplemented draw or image type.

Brief notes for 4:

- **Matrix.** Reuse the shaped-link rig and the re-baseline's
  cells, and add sub-10 Mbit profiles (for example 80 ms / 5
  Mbit and 150 ms / 2 Mbit). Compare relay with
  `streaming-video=off`, relay with `all`, and transcode.
- **Per session, record:**
  - keypress-to-draw latency, p50 and p95;
  - CPU on the Kerbside host, including an unshaped LAN run
    (the always-transcode cost);
  - image quality: SSIM and PSNR of the client's final frame
    against a lossless capture of the same workload, taken at
    the same instants. Say how the capture was made and aligned;
  - whether text is legible during animation, with a
    screenshot;
  - the guest's damage path (atomic damage clips or DIRTYFB).
- **Clients.** Ryll headless for timing. Remote-viewer for
  correctness only, since it reports no timings.
- **Output.** A page under `docs/performance/` with the
  procedure and results, in the style of the re-baseline.

Brief notes for 5:

- **Relay to transcode.** With a session in relay whose backend
  was dialled lossless and non-GLZ (as in 3b), close and
  reconnect the backend display channel in transcode mode, then
  reset the client with STREAM_DESTROY_ALL, INVAL_ALL_PIXMAPS,
  INVAL_ALL_PALETTES and a full lossless draw. Watch for the
  "duplicate channel" race on reconnect.
- **Transcode to relay.** Reconnect the backend display channel
  in relay mode and forward its fresh init. Check whether the
  client's stale GLZ window can matter when the backend never
  used GLZ.
- **Clients.** Test both remote-viewer and Ryll, and record
  whether either misbehaves.
- **Output.** Whether switching works, what it costs (the time
  the display is blank or stale), and whether the mode can
  therefore follow the link, or must be chosen once at connect.

Brief notes for 6:

- Write `docs/performance/transcoding-spike.md`: the verdict,
  the evidence from steps 2, 4 and 5, CPU per session, and the
  answer to the master plan's Open question 4.
- If go: draft phase plans for productising SPICE-out (the
  per-session carrier, the sandboxed worker, capacity, codec
  licensing, the out-of-scope list) and for WebRTC out.
- If no-go: say what would change the answer.
- Update the master plan: the phase 3 row, Open question 4, and
  Future work items this changes.

## Definition of done

- #477 is fixed and closed: client caps reach the backend, the
  client-leg reply is per channel type, and a handshake test
  covers both.
- The ACK-window mechanism is confirmed or refuted from
  observation, and the result is recorded.
- A transcoding display channel exists behind a non-default
  feature and flag, with tests, and is not in the release
  wheels.
- The measurement page covers latency, CPU and image quality,
  including links slower than 10 Mbit.
- The mode-switching question has an answer.
- The spike's write-up gives a go or no-go, and the master
  plan's Execution row records every merge (ryll and kerbside)
  in `Merged`.
