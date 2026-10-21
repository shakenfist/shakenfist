# RDP frontend phase 1: renderer prep

Master plan: [PLAN-rdp-frontend.md](/components/ryll/plans/PLAN-rdp-frontend/).

**Planning effort:** low. The master plan asks for high, and
the Execution notes record the choice to proceed anyway. It
asks for high because this phase changes renderer internals
that every mode depends on (the display surface, the playback
channel, the crate's feature set and public API) and has to
coordinate with Kerbside's transcoding spike. No review effort
is set by the master plan; review at high effort, because steps
5 and 6 change the published crate's API.

## Prompt

Read the master plan's *Prompt* first; everything there
applies. For this phase specifically, read before acting:

- `shakenfist-spice-renderer/src/display/surface.rs` and
  `src/surface_mirror.rs` (damage, steps 1 and 2).
- `shakenfist-spice-renderer/src/session.rs` (`run_connection`
  at :193, `run_headless` at :646, the fan-out and mirror tasks
  at :883-935, `plan_channel_links` at :1151) and
  `ryll/src/main.rs` `run_web` (:514-1010).
- `ryll/src/web/inputs.rs` `run_input_relay` (:132-314) and the
  pointer block in `ryll/src/app/mod.rs` (:4536-4630).
- `shakenfist-spice-renderer/src/channels/playback.rs` and
  `src/audio_sink.rs`.
- Every `Cargo.toml` in the workspace, the `Makefile` (`lint`
  at :277), and `AGENTS.md` *Cargo feature gating*.

Builds and tests run in Docker through the Makefile (`make
build`, `make test`, `make lint`), never with a host toolchain.
Line references below are against develop `7690e1e`; re-find
them rather than trusting them once earlier steps have landed.

## Scope

In scope, closing the master plan's renderer gaps 1 to 4:

- **Damage tracking** on `DisplaySurface`, with a bounded
  rectangle region a consumer takes in one call.
- **A shared pointer translator** in the renderer, used by
  `--web` and the GUI.
- **A shared event-bus helper** for the forwarder and
  mirror-apply tasks, used by `--web` and headless.
- **A slim renderer build**: features for usbredir, webdav,
  the H.264 encoder, H.264 decode and mozjpeg, so a consumer
  can link display decode alone.
- **Device-free playback**: a run-time playback mode that
  hands a sink Opus packets, raw PCM and decoded PCM without
  opening a cpal device, and `--web` switched to it.
- A `make lint` check that keeps the slim build compiling and
  slim.
- Documentation of the above.

Out of scope:

- Any RDP code or IronRDP dependency (phase 2).
- Mono and 4-bit cursor decode (gap 6, phase 3).
- Multi-monitor (gap 5, future work).
- Changing headless's audio behaviour: it keeps local output.
  See decision 9.
- Pruning `ryll/Cargo.toml`'s own unused direct dependencies
  (see *Future work*); this phase prunes only the renderer's.
- Fixing #398 (H.264 from spice-server's gstreamer never
  decodes); decision 6 relies on it only as evidence.
- Adding wheel support to `--web`, which has none (see
  *What the survey found*, item 9).

## What the survey found

Surveyed on 2026-10-20 against develop `7690e1e`. The master
plan's gap list is substantially right. These items correct or
sharpen it; the false claims are fixed at their source in the
master plan and the `index.md` row in this planning commit, so
later steps need not redo that.

1. **openh264 is pulled in by H.264 *decode*, not only by the
   encoder.** `openh264 = "=0.9.8"` is a non-optional
   dependency of `shakenfist-spice-compression`, used by
   `H264VideoDecoder` (`video.rs:266-477`, selected by
   `for_stream` at `video.rs:174`). Gating the renderer's
   encoder alone leaves `openh264-sys2` (a C++ build with cc
   and nasm) in the tree. Separating it is possible: a build
   without H.264 decode must also stop advertising
   `DISPLAY_CODEC_H264`, and `H264Offer::Off` already does
   exactly that (`link::display_channel_caps`, protocol
   `link.rs:744-749`; `preferred_video_codecs`,
   `display.rs:170-176`). MJPEG decode does not depend on it.
   The master plan said only that the encoder pulls openh264;
   corrected.
2. **The renderer turns on more C builds than the master plan
   listed.** It depends on the compression crate with default
   features plus `encode`, so it always builds `mozjpeg`
   (vendored libjpeg-turbo) as well as openh264. The pure-Rust
   `jpeg-decoder` fallback already exists, so mozjpeg can be a
   feature.
3. **Ten renderer dependencies are unused.** `rsa`, `sha1`,
   `rand`, `tokio-rustls`, `rustls-pemfile`, `webpki-roots`,
   `byteorder`, `async-channel`, `thiserror` and
   `http-body-util` have no references in the renderer's
   `src/` or `tests/` (checked by grep), and nor does `http`.
   `bytes` is used only by `tests/webrtc_h264_smoke.rs`. They
   can be deleted, no feature needed. `nusb` is used only under
   `cfg(target_os = "linux")` but is an unconditional
   dependency on every target.
4. **The audio gap is worse than "a tap, not a sink".** The
   tap fires before decode and whether or not a device opened
   (`playback.rs:1156-1221`), but the playback channel exists
   only with the `audio` feature (`channels/mod.rs:6`,
   `session.rs:456`, `:1158-1161`). So a `--no-default-features`
   ryll serves `--web` with no audio at all, and a default
   build serving `--web` also plays the guest's audio out of
   the server's own speakers, or, with no device, retries
   cpal every 30 s for nothing (`playback.rs:540-554`). The sink
   never sees decoded PCM, START/STOP or format changes
   (`audio_sink.rs:29-42`). Its only implementor is
   `WebOpusSink` (`ryll/src/web/audio.rs:51-117`).
5. **The playback channel offers Opus by accident.** Playback
   links with `DEFAULT_OTHER` (protocol `link.rs:706-712`),
   whose bits are the main channel's migrate caps; on the
   playback channel those same bits mean `CELT_0_5_1 | OPUS`
   (`constants.rs:130-135`, `:221-234`). Ryll cannot decode
   CELT (`playback.rs:1199-1219`). Not in the master plan;
   step 6 gives playback its own caps constant.
6. **The forwarder and mirror-apply tasks exist twice, not
   once:** in `run_web` (`main.rs:803-845`) and in
   `run_headless` (`session.rs:883-935`), with two separate
   `EVENT_BROADCAST_CAPACITY` constants (`session.rs:87`,
   `web/server.rs:34`), both 1024. RDP would be the third copy.
   The master plan named only `run_web`; corrected.
7. **The two pointer translators disagree.** The GUI
   (`app/mod.rs:4536-4555`) suppresses a repeated position in
   both mouse modes; the web relay (`web/inputs.rs:208-233`)
   suppresses only zero deltas in server mode. The GUI places
   button events at the last hover position; the web relay
   takes the button's own coordinates and records them as the
   next delta origin without telling the guest the pointer
   moved (`web/inputs.rs:237-264`). In server mode SPICE's
   `MOUSE_PRESS` carries no position, so that movement is lost.
   The inputs channel already sends `MOUSE_POSITION` before
   every press and release (`inputs.rs:750-830`), which covers
   client mode.
8. **Damage rectangles exist on six events, not four:**
   `ImageReadyChroma` and `ImageReadyAlpha` carry them too.
   The draw ops clamp to the surface and to clip lists inside
   `DisplaySurface` (`surface.rs:70-402`), so that is where the
   exact written rectangles are known.
9. **`--web` has no mouse wheel.** `BrowserMsg` has no wheel
   variant and `app.js` sends none; the GUI maps the wheel to
   button 4/5 press-release pairs (`app/mod.rs:4596-4618`).
   `docs/multi-mode-parity.md` has no wheel row, so the gap is
   unrecorded. Out of scope here (the translator in step 3 gives
   both frontends the same wheel mapping to call); recorded in
   *Future work*.
10. **`ChannelEvent::UsbDevicesChanged` is never emitted**
    (`channels/mod.rs:450-452`, `#[allow(dead_code)]`), and it
    is the only thing tying `usb::UsbDeviceInfo` into the
    always-present event enum.
11. **No CI lane builds the renderer slim.** `make lint` runs a
    slim clippy for the compression crate only
    (`Makefile:280`). cargo-deny runs with `--all-features`, so
    it never sees a slim tree.
12. **Kerbside's side of the hand-over is not merged.** The
    master plan says Kerbside's phase 3 plan "has been updated
    to match". That update is kerbside PR #581, still open on
    2026-10-20. Kerbside's spike step 3a has partly reached
    ryll already: the LZ4 and JPEG image encoders landed
    (`6895658`, `8dc590b`). Its slim-features part has not, so
    this phase does not collide with it.

## Decisions

1. **Damage lives on `DisplaySurface`, beside the dirty bool,
   and is independent of it.** Each draw op records the
   sub-rectangles it actually wrote, after clamping to the
   surface and the clip list, into a bounded `DamageRegion`. A
   new surface starts fully damaged, as it starts dirty.
   `take_damage()` returns the region and clears it;
   `consume_dirty()` keeps its meaning and does not touch the
   region, and vice versa. `RealFrameSource` and the GUI
   texture cache are unchanged. Independence costs nothing:
   the region is bounded, so a surface whose damage nobody
   takes holds at most `MAX_DAMAGE_RECTS` rectangles. Putting
   damage in `DrawOutcome` instead was rejected: it would hand
   the merging and bounding to every consumer, which is what
   the master plan's "goes in the renderer" rules out.
2. **The region merges and bounds deterministically.** A new
   rectangle contained in an existing one is dropped. Two
   rectangles merge when their bounding box's area is no more
   than the sum of their areas, so a merge never adds pixels
   that neither covered beyond their overlap; merging repeats
   until no pair qualifies. Past `MAX_DAMAGE_RECTS` (32), the
   whole region collapses to its bounding box. This is simple,
   testable and good enough for RDP, which re-diffs in 64x64
   tiles anyway (master plan, *The RDP side*).
3. **A lagged mirror marks everything damaged.** When the
   mirror's bus subscription reports `Lagged`, the shared
   helper (decision 4) calls a new
   `SurfaceMirror::mark_all_damaged()`, which sets every
   surface fully damaged and dirty. The pixels have already
   diverged; this makes every consumer resend the whole
   surface, rather than trusting a region that is missing the
   draws it never saw.
4. **One event-bus helper, in the renderer, used by `--web`
   and headless now.** `EventBus::new(capacity)` creates the
   broadcast sender and the mirror and takes the mirror's
   subscription at once. Callers take their own subscriptions
   with `subscribe()`, then call `start(event_rx)`, which spawns
   the forwarder and the mirror-apply task and returns their
   handles. Splitting construction from `start` preserves
   `run_web`'s rule that every long-lived consumer subscribes
   before events flow (`main.rs:724-735`). The two capacity
   constants become one, `session::EVENT_BROADCAST_CAPACITY`.
5. **One pointer translator, synchronous, mode passed per
   call.** `PointerTranslator` in the renderer
   (`src/pointer.rs`) holds only the last position. It takes
   the mouse mode as an argument on each call, so the GUI
   (synchronous, mode in `self.mouse_mode`) and the web relay
   (async, mode in a `watch`) both use it without sharing a
   runtime type. The unified behaviour:
   - A move to the last position sends nothing, in either
     mode (the GUI's behaviour). For `--web` this drops
     duplicate `MOUSE_POSITION` messages in client mode, which
     the guest ignored anyway.
   - In server mode the first move after a reset is a zero
     delta and sends nothing; later moves send `MouseMotion`.
   - A button event that carries a position different from
     the last one, in server mode, is preceded by the
     `MouseMotion` delta to it, then recorded as the new
     origin. **This changes `--web` behaviour**, deliberately:
     today the movement is silently dropped (survey item 7).
     The existing test
     `a_button_position_becomes_the_next_delta_reference` is
     updated to expect the delta. Client mode needs no extra
     event, because the inputs channel already sends a
     position with every press.
   - A button event with no position (the GUI) uses the last
     position.
   - `wheel(clicks)` turns signed wheel clicks into button 4
     or 5 press-release pairs at the last position, as the GUI
     does today; the egui-specific click accumulator stays in
     the GUI.
   - `reset()` forgets the position (the GUI calls it on
     reconnect, `app/mod.rs:1160`).
6. **openh264 decode becomes a feature; a build without it
   offers no H.264.** The compression crate gains
   `h264-decode = ["dep:openh264"]`, default on, and `encode`
   gains `dep:openh264` explicitly. In a renderer build without
   `h264-decode`, `run_connection` and `DisplayChannel::new`
   force `H264Offer::Off`, which already drops both the cap bit
   and the codec preference; the protocol crate's
   `DEFAULT_DISPLAY` and its logging test do not change. MJPEG
   stream decode is untouched. The cost of dropping H.264
   decode in a slim build is nil in practice: H.264 from
   spice-server's gstreamer encoder does not decode in ryll at
   all today (#398), and the default preference already asks
   for MJPEG first.
7. **The renderer's feature set.** All default-on, so a plain
   dependency on the crate builds what it builds today:

   | Feature | Pulls in | Gates |
   |---------|----------|-------|
   | `audio` | cpal, rtrb | local audio output only (narrowed, decision 8) |
   | `usbredir` | nusb (Linux), shakenfist-spice-usbredir | `usb`, `channels::usbredir` |
   | `webdav` | dav-server, hyper, hyper-util | `webdav`, `channels::webdav` |
   | `encoder` | compression `encode` (openh264-sys2, jpeg-encoder) | `encoder::h264`, `encoder::task` |
   | `h264-decode` | compression `h264-decode` (openh264) | H.264 stream decode |
   | `mozjpeg` | compression `mozjpeg` (libjpeg-turbo) | the SIMD JPEG decoder |
   | `digest-decode` | unchanged, default off | unchanged |

   The renderer depends on the compression crate with
   `default-features = false` and the decoder features it
   always needs (`quic`, `glz`, `lz`, `lz4`, `jpeg`). Gating
   follows the `audio` precedent (`session.rs:226`, `:456`,
   `:1158-1161`): plain-data types stay ungated so
   `run_connection` and `run_headless` keep one signature in
   every build (`UsbCommand`, `WebdavCommand`,
   `ShareDirConfig`, `VirtualDiskConfig`, every snapshot, every
   other `ChannelEvent` variant), and `plan_channel_links`
   skips a compiled-out channel with a logged reason.
   `FrameSource`, `FrameRef`, `RealFrameSource`,
   `SyntheticFrameSource`, `EncodedFrame` and `EncoderControl`
   stay ungated (the last two move out of the files that need
   openh264), because `shakenfist-spice-webrtc`'s production
   code needs only those. `even_dimensions` moves out of the
   compression crate's `encode` module so it is available
   without the encoder (`web/inputs.rs` uses it for viewport
   rounding). The dead `UsbDevicesChanged` variant is deleted
   rather than having its types moved. `ryll` names the
   features it needs explicitly (it already uses
   `default-features = false`), and `shakenfist-spice-webrtc`
   takes `encoder` only as a dev-dependency. These names
   become public API on crates.io, which is why they are
   settled here rather than by the implementer.
8. **Playback is always compiled; only local output is a
   feature.** `opus-decoder` is pure Rust and small, while cpal
   pulls ALSA, so the split that matters is decode versus
   device. `PlaybackChannel` and opus decode become
   unconditional, and `audio` gates only the cpal audio thread
   and its ring. `run_connection`'s
   `opus_sink: Option<Arc<dyn OpusPacketSink>>` parameter
   becomes `playback: PlaybackOutput`, with two variants:
   - `Local`: today's behaviour. In a build without `audio`,
     the channel is not linked, as today.
   - `Sink(Arc<dyn PlaybackSink>)`: never spawns the audio
     thread and is linked in every build. Notification
     suppression keys off this variant instead of
     `opus_sink.is_some()` (`playback.rs:1383`).

   The trait is renamed `PlaybackSink`, since it is no longer
   only about Opus, and gains `on_start(PlaybackFormat)`,
   `on_stop()` and `on_decoded_pcm(&[i16], rate, channels)`,
   all with default no-op bodies beside the existing
   `on_opus_packet` and `on_pcm_samples`. Decode keeps running
   in both variants, as it does today, so the control socket's
   `stats.playback.packets_decoded` keeps its meaning
   (`docs/control-socket-protocol.md`). Decoding only on demand
   was rejected for that reason; Opus decode of one stereo
   stream is cheap. The rename is a breaking API change, which
   is acceptable at 0.x and needed anyway by the signature
   change.
9. **`--web` switches to `Sink`; headless and the GUI stay on
   `Local`.** That `--web` should not open a local audio device
   is the master plan's requirement. Headless today really does
   play audio locally when a device exists, and whether that is
   a feature or an accident is not this phase's call. It is
   recorded in *Future work* rather than changed silently.
10. **Playback gets its own caps constant.** A
    `DEFAULT_PLAYBACK` in the protocol crate, `OPUS` only, and
    `default_channel_caps` uses it for the playback channel.
    The only wire change is dropping the CELT bit, which ryll
    cannot decode. Phase 4 will want to choose Opus or PCM per
    client, and that starts from a named constant rather than
    a borrowed one.
11. **The slim build is checked in `make lint`, which CI
    already runs.** A new `tools/check-slim-renderer.sh`, run in
    the devcontainer by `make lint`, does three things. It
    clippies the renderer with `--no-default-features
    --all-targets`. It checks the renderer with each feature
    enabled on its own. It runs `cargo tree -p
    shakenfist-spice-renderer --no-default-features -e
    normal,build` and fails if any of `openh264`,
    `openh264-sys2`, `mozjpeg-sys`, `nusb`, `dav-server`,
    `hyper`, `cpal` or `alsa-sys` appears. A separate CI job
    was rejected: the lint job exists and this costs it a
    renderer build.

## Step plan

Steps run in order; each depends on the one before only where
stated. Each step is one commit, with the subject given.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | none | Damage accumulator on `DisplaySurface`. Commit: "Track damage rectangles on display surfaces." See brief 1. |
| 2 | medium | sonnet | none | Shared event-bus helper; switch `run_web` and `run_headless`. Needs step 1. Commit: "Share the event bus and mirror tasks." See brief 2. |
| 3 | high | opus | none | Shared pointer translator; switch `--web` and the GUI. Commit: "Share pointer translation between frontends." See brief 3. |
| 4 | medium | sonnet | none | Dependency hygiene with no behaviour change. Commit: "Prune the renderer's unused dependencies." See brief 4. |
| 5 | high | opus | worktree | Slim feature set. Needs step 4. Commit: "Make the renderer's heavy dependencies optional." See brief 5. |
| 6 | high | opus | worktree | Device-free playback; `--web` uses it. Needs step 5. Commit: "Add a device-free playback mode." See brief 6. |
| 7 | medium | sonnet | none | Slim-build check in `make lint`. Needs steps 5 and 6. Commit: "Check the slim renderer build in lint." See brief 7. |
| 8 | medium | sonnet | none | Documentation. Needs all of the above. Commit: "Document the renderer prep for RDP." See brief 8. |

### Brief 1: damage accumulator

- New module `shakenfist-spice-renderer/src/display/damage.rs`,
  declared in `display/mod.rs`. Types:
  `pub struct DamageRect { pub left: u32, pub top: u32, pub
  right: u32, pub bottom: u32 }` (right and bottom exclusive,
  matching the `(l, t, r, b)` tuples `surface.rs` already
  uses), and `pub struct DamageRegion` holding a
  `Vec<DamageRect>`. Methods: `add(rect)`, `is_empty()`,
  `rects() -> &[DamageRect]`, `bounding_box() ->
  Option<DamageRect>`, `full(width, height) -> Self`. Constant
  `pub const MAX_DAMAGE_RECTS: usize = 32`.
- Merge and bound rules exactly as decision 2. Ignore empty
  rectangles. Keep it allocation-light: no new dependencies.
- `DisplaySurface` (`display/surface.rs`) gains a `damage:
  DamageRegion` field, initialised to the full surface in
  `new()`. Add `take_damage(&mut self) -> DamageRegion` (returns
  and clears) and `damage(&self) -> &DamageRegion` (peek). Do
  not change `consume_dirty`/`is_dirty`.
- In each draw op, add the rectangle(s) actually written,
  where the op already sets `dirty = true`:
  - `blit` (:70): the clamped rectangle, left to
    `left + copy_width/4`, top to `min(top + height,
    self.height)`.
  - `fill_rect` and `invert_rect`: each clipped sub-rectangle
    passed to `fill_subrect`/`invert_subrect`.
  - `copy_bits`: each **destination** sub-rectangle passed to
    `copy_subrect`. The source is not damage.
  - `blit_chroma` and `blit_alpha`: the clamped
    `clip_w x clip_h` rectangle, only when the op wrote.
- `SurfaceMirror` (`surface_mirror.rs`): add
  `mark_all_damaged(&mut self)`, which sets each surface's
  region to full and its dirty bit. Add a crate-internal
  setter on `DisplaySurface` for that. `apply_event` and
  `DrawOutcome` do not change.
- Re-export `DamageRect`, `DamageRegion` and
  `MAX_DAMAGE_RECTS` from `lib.rs`.
- Unit tests in `damage.rs` and `surface.rs`:
  - a new surface is fully damaged;
  - `take_damage` clears;
  - each op's damage, including `copy_bits` damaging only
    the destination, and clip lists producing per-clip
    rectangles;
  - an off-surface draw adds nothing;
  - containment dropping;
  - the merge rule (two adjacent rectangles merge, two
    distant ones do not);
  - 33 disjoint rectangles collapse to the bounding box;
  - `consume_dirty` and `take_damage` do not affect each
    other;
  - `mark_all_damaged`.
- `make test` and `make lint` pass.

### Brief 2: event-bus helper

- New module `shakenfist-spice-renderer/src/event_bus.rs`,
  exported from `lib.rs`. `pub struct EventBus` owning a
  `broadcast::Sender<ChannelEvent>`, an
  `Arc<tokio::sync::Mutex<SurfaceMirror>>`, and the mirror's
  pre-taken `broadcast::Receiver`.
  - `EventBus::new(capacity)` creates all three.
  - `sender()` clones the sender.
  - `subscribe()` returns a receiver.
  - `mirror()` clones the mirror handle.
  - `start(self, event_rx: mpsc::Receiver<ChannelEvent>,
    label: &'static str) -> EventBusHandles` spawns the
    forwarder and the mirror-apply task and returns their
    `JoinHandle`s. `label` is for logs ("web", "headless").
- Behaviour must match the two existing copies:
  `session.rs:883-935` and `ryll/src/main.rs:803-845`.
  - The forwarder drains the mpsc into the broadcast and
    ignores send errors.
  - The mirror task applies each event under the lock.
  - On `Lagged(n)` the mirror task warns and then calls
    `mark_all_damaged()` (decision 3, from step 1).
  - On `Closed` it logs and exits.
- `start` takes `self` by value. If `EventBus` itself still
  holds a sender after `start`, the broadcast never closes, so
  make sure it does not. `run_headless` relies on the bus
  closing when the forwarder exits (`session.rs:937-940`):
  read that comment.
- Switch `run_headless` and `run_web` to it. Keep `run_web`'s
  ordering: construct the bus, take the cursor subscription
  (and any other long-lived ones) before spawning
  `run_connection`, then `start`. Use the bus's
  `sender()` where `event_broadcast_tx` was cloned. Delete
  `ryll/src/web/server.rs`'s `EVENT_BROADCAST_CAPACITY` and use
  `shakenfist_spice_renderer::session::EVENT_BROADCAST_CAPACITY`.
- Test: a tokio test that starts a bus, sends a
  `SurfaceCreated` and an `ImageReady` through the mpsc, and
  sees them applied to the mirror. A second test: a bus
  with capacity 1, a flood of events and a slow mirror, ends
  with every surface fully damaged. If forcing `Lagged`
  deterministically is awkward, test `mark_all_damaged`
  through a smaller seam and say so.
- `make test` and `make lint` pass. No behaviour change in
  either mode beyond decision 3.

### Brief 3: pointer translator

- New module `shakenfist-spice-renderer/src/pointer.rs`,
  exporting `PointerTranslator` from `lib.rs`. API, all
  synchronous:
  - `new()` and `reset()`;
  - `last_pos() -> Option<(u32, u32)>`;
  - `motion(mouse_mode: u32, x: u32, y: u32) ->
    Option<InputEvent>`;
  - `button(mouse_mode: u32, button: u32, down: bool, pos:
    Option<(u32, u32)>) -> Vec<InputEvent>`, at most two
    events;
  - `wheel(clicks: i32) -> Vec<InputEvent>`.
  Coordinates are surface pixels. Compare the mode against
  `shakenfist_spice_protocol::MOUSE_MODE_SERVER`; anything else
  (client, unknown) is treated as client, as both callers do
  today.
- Semantics exactly as decision 5. Port the comments that
  explain *why* (the ack-window cost of zero deltas,
  `web/inputs.rs:216-224`; the first move as a zero delta;
  the inputs channel sending a position before each press,
  `inputs.rs:750-760`) into the new module rather than losing
  them.
- `ryll/src/web/inputs.rs` `run_input_relay`: replace
  `last_pos` with a `PointerTranslator`. `PointerMove` calls
  `motion`. `PointerButton` calls `button(..., Some((x, y)))`
  and sends each returned event in order. Keep the
  denormalisation and the no-primary-surface skip.
- `ryll/src/app/mod.rs`: replace the `last_mouse_pos` field
  (:374, initialised :982, reset :1160) with a
  `PointerTranslator`.
  - The hover block at :4536-4555 calls `motion`.
  - The button loop at :4568-4594 calls `button(..., None)`.
    Keep `forwarded_buttons` and the `pointer_on_surface` gate
    exactly as they are.
  - The wheel block at :4596-4618 calls `wheel(clicks)`.
  - The input-suppressed release path at :4623 and the cursor
    overlay at :4956 read `last_pos()`.
  - The GUI sends with `try_send`; keep that.
- Tests:
  - Unit tests in `pointer.rs` for both modes: duplicate
    suppression, the first server-mode move, deltas, the
    server-mode button catch-up delta, no catch-up in client
    mode, a button with no position, wheel up and down,
    `reset`.
  - Update `a_button_position_becomes_the_next_delta_reference`
    in `web/inputs.rs` to expect the catch-up `MouseMotion`
    before the press, and add a client-mode test that a
    repeated position sends nothing. The other existing web
    input tests must pass unchanged.
- `make test` and `make lint` pass.

### Brief 4: dependency hygiene

No behaviour change, and every build stays identical apart
from fewer crates.

- `shakenfist-spice-renderer/Cargo.toml`:
  - Delete `rsa`, `sha1`, `rand`, `tokio-rustls`,
    `rustls-pemfile`, `webpki-roots`, `byteorder`,
    `async-channel`, `thiserror`, `http` and
    `http-body-util`. Grep `src/` and `tests/` for each
    before deleting.
  - Move `bytes` to `[dev-dependencies]`.
  - Move `nusb` under
    `[target.'cfg(target_os = "linux")'.dependencies]`.
  - Fix the stale comments: `socket2` is used only by the
    control socket's bind (`control/server.rs:158-160`), and
    `image` only by the screenshot verb and display tests.
- In `shakenfist-spice-compression`, move `even_dimensions`
  out of `encode/h264.rs` (:83-85) to an ungated module, and
  re-export it from the crate root unconditionally. Keep
  the renderer's re-export path (`encoder/h264.rs:12`,
  `lib.rs:46-49`) working.
- In the renderer, move `EncodedFrame` (`encoder/h264.rs:16-26`)
  and `EncoderControl` (`encoder/task.rs:25-32`) into a new
  `encoder/types.rs` and re-export them from the same paths, so
  step 5 can gate `h264.rs` and `task.rs` without taking them
  along.
- Delete `ChannelEvent::UsbDevicesChanged`
  (`channels/mod.rs:450-452`) and its `kind()` arm (:601).
  Grep the workspace, including `docs/control-socket-protocol.md`,
  for the event name, and remove it from any list of
  subscribable kinds.
- `make build`, `make test` and `make lint` pass, plus `make
  check-windows`, since `nusb` moved.

### Brief 5: slim feature set

Read decisions 6 and 7 first. They are settled; the work is
mechanical but wide, which is why this runs in a worktree.

- **`shakenfist-spice-compression/Cargo.toml`.** Make
  `openh264` optional and add `h264-decode =
  ["dep:openh264"]` to `default`. Add `dep:openh264` to
  `encode`, since `encode/h264.rs:14-20` uses
  `openh264::encoder`.
- **`video.rs`.** Gate `H264VideoDecoder` and its impl
  (:266-477) and the `for_stream` H.264 arm (:174) on
  `h264-decode`. Without it, codec 3 falls through to
  `UnsupportedCodec`, which the display channel already logs
  and skips (`display.rs:1775-1792`). Leave
  `SPICE_VIDEO_CODEC_TYPE_H264` ungated.
- **Compression tests.** Gate the H.264 tests (`video.rs`
  :574, :718, :768, the `h264_round_trip_tests` module at
  :1022, :1174), and `encode/h264.rs:1201-1220`, which needs
  both features. Confirm `make lint`'s existing slim
  compression clippy (`--no-default-features --features
  encode`) still passes.
- **`shakenfist-spice-renderer/Cargo.toml`.** Add the features
  in decision 7's table, all in `default` except
  `digest-decode`. Depend on compression with
  `default-features = false, features = ["quic", "glz", "lz",
  "lz4", "jpeg"]`, with `encoder`, `h264-decode` and
  `mozjpeg` forwarding to it. Make `nusb`,
  `shakenfist-spice-usbredir`, `dav-server`, `hyper` and
  `hyper-util` optional. `lz4_flex` stays (the display tests
  use it, and compression pulls it anyway).
- **Gating, following the `audio` precedent** (list every
  `cfg(feature = "audio")` site first and copy the shape):
  - `lib.rs`: `pub mod usb` under `usbredir`, `pub mod webdav`
    under `webdav`.
  - `channels/mod.rs`: `usbredir` and `webdav` and their
    re-exports.
  - `encoder/mod.rs`: `h264` and `task` under `encoder`.
  - `session.rs`: the usbredir arm (:410-431) and the webdav
    arm (:433-454). In `plan_channel_links` (:1151-1175),
    `cfg!` skips with reasons like "usbredir feature disabled
    at compile time". Silence the now-unused parameters as
    `session.rs:226` does.
  - In a build without `h264-decode`, force `H264Offer::Off`
    before `display_channel_caps` is called (around
    `session.rs:340`) and in `DisplayChannel::new`
    (`display.rs:1040`), with a comment pointing at decision 6.
- **Renderer tests.** Gate the display H.264 tests
  (`display.rs:4822-4839` helper, :4866, :4888, :4915) on
  `all(feature = "h264-decode", feature = "encoder")`. The
  preference tests at :5876-5925 must pass in the default
  build; gate the H.264-specific ones. Add `[[test]]
  required-features = ["encoder"]` for `tests/encoder_smoke.rs`
  and `tests/webrtc_h264_smoke.rs`. Add a
  `plan_channel_links` test per new feature, mirroring
  `playback_follows_the_audio_feature` (`session.rs:1314`).
- **`ryll/Cargo.toml`.** List the renderer features ryll
  needs explicitly: `usbredir`, `webdav`, `encoder`,
  `h264-decode`, `mozjpeg`. `audio` keeps forwarding through
  ryll's own `audio` feature. Every existing ryll build (`make
  build`, `make check-windows`, the slim
  `--no-default-features -p ryll`) must link the same
  functionality as before.
- **`shakenfist-spice-webrtc/Cargo.toml`.** No production
  features. Add the renderer as a dev-dependency with
  `encoder`, since `bridge.rs:2141` and `tests/loopback.rs`,
  `tests/lifecycle.rs` use `H264Encoder`/`EncoderTask`.
- **Verify, and paste the output into the step's report:**
  - `cargo clippy -p shakenfist-spice-renderer
    --no-default-features --all-targets -- -D warnings` is
    clean.
  - `cargo tree -p shakenfist-spice-renderer
    --no-default-features -e normal,build` contains none of
    `openh264`, `openh264-sys2`, `mozjpeg-sys`, `nusb`,
    `dav-server`, `hyper`. `cpal` will still be absent,
    because `audio` is off.
  - `cargo check -p shakenfist-spice-renderer
    --no-default-features --features X` passes for each new
    feature X.

  Run them inside the devcontainer (copy the
  `DOCKER_RUN_OFFLINE` invocation from the Makefile). Then
  `make build`, `make test`, `make lint` and
  `make check-windows`.
- Report the full `cargo tree` output for the slim build; the
  management session records it in this plan.

### Brief 6: device-free playback

Read decisions 8 to 10 and the survey's items 4 and 5 first.

- **`shakenfist-spice-renderer/Cargo.toml`.**
  `opus-decoder` becomes non-optional, and `audio = ["dep:cpal",
  "dep:rtrb"]`.
- **`channels/mod.rs`.** Compile `playback` unconditionally.
- **`playback.rs`.** Gate only `AudioThread`, `CallbackState`,
  the ring and their tests on `audio`. `Resampler`,
  `pcm_bytes_to_i16`, the TOC parser, `OutputStatus` and the
  mode helpers stay ungated, with their tests. Expect to
  restructure `start_audio_output` (:1329-1352) and the
  `audio_producer` handling so a build without `audio`, or a
  `Sink` session, never constructs the thread.
- **`audio_sink.rs`.** Rename `OpusPacketSink` to
  `PlaybackSink` (rename the file to `playback_sink.rs`). Add:
  - `pub struct PlaybackFormat { pub mode: u16, pub
    sample_rate_hz: u32, pub channels: u8 }`;
  - `fn on_start(&self, format: PlaybackFormat) {}` and
    `fn on_stop(&self) {}`;
  - `fn on_decoded_pcm(&self, samples: &[i16],
    sample_rate_hz: u32, channels: u8) {}`.
  Call `on_start` after START (and again after a MODE change,
  with the new mode) and `on_stop` on STOP. Call
  `on_decoded_pcm` with the decode buffer slice after each
  successful Opus decode (`push_samples_opus`, :1281-1314).
  Saturate rather than truncate the channel count into `u8`
  (today `as u8`, :1170-1176).
- **`run_connection`.** Replace `opus_sink:
  Option<Arc<dyn OpusPacketSink>>` with `playback:
  PlaybackOutput`: `pub enum PlaybackOutput { Local, Sink(Arc<dyn
  PlaybackSink>) }`. Update `plan_channel_links` (:1158-1161):
  `Sink` links in every build; `Local` links only with
  `audio`, with today's skip reason. Replace the
  `opus_sink.is_some()` notification suppression (:1383) with
  the `Sink` variant. Delete the `cfg(not(feature = "audio"))`
  silencer at :226 if it is no longer needed.
- **Callers.** There are four: `session.rs:771` (headless,
  `Local`), `ryll/src/main.rs:764` (`--web`,
  `Sink(WebOpusSink)`), and `ryll/src/app/mod.rs:915` and
  `:1248` (GUI, `Local`). Rename `WebOpusSink` to
  `WebPlaybackSink` and update `ryll/src/web/audio.rs`'s module
  doc, which describes local playback happening alongside.
- **Caps (protocol crate).** Add `DEFAULT_PLAYBACK`
  (`PLAYBACK_OPUS` only) in `constants.rs` next to the playback
  cap constants (:221-234), and a `ChannelType::Playback` arm
  in `default_channel_caps` (`link.rs:706-712`). Update the test
  at `link.rs:2000-2009`, which pins today's accidental value.
- **Tests.** None today drive `PlaybackChannel`'s DATA
  dispatch (survey). Using `channels/test_support.rs`, add
  tests that feed START, MODE(OPUS), DATA and STOP to a
  `Sink`-mode channel with a recording sink, and assert:
  - `on_start` arrives before the first packet;
  - the Opus packet is tapped;
  - `on_decoded_pcm` follows the tap with 48 kHz samples;
  - `on_stop` arrives;
  - no audio thread was spawned (expose a test-only
    observation, such as a count of thread starts).
  Add a RAW-mode case for `on_pcm_samples`. Update
  `playback_follows_the_audio_feature` to cover both variants.
- **Verify.**
  - `make test` and `make lint` pass.
  - The slim clippy from brief 5 still passes.
  - `cargo tree -p shakenfist-spice-renderer
    --no-default-features -e normal,build` has no `cpal` or
    `alsa-sys`.
  - Run `ryll --web` against the local QEMU test server
    (`docs/development.md`) and confirm in the log that no
    audio thread starts. Say in the report if no guest with
    audio was available to hear it end to end.

### Brief 7: slim-build check

- New `tools/check-slim-renderer.sh` (shellcheck-clean, `set
  -euo pipefail`), run inside the devcontainer. It performs
  decision 11's three checks with `--frozen`, and on failure
  prints which crate appeared and the `cargo tree -i` path that
  pulled it in.
- Call it from the `lint` target in the `Makefile` (:277-280),
  after the existing clippies, via the same
  `$(DOCKER_RUN_OFFLINE) $(RYLL_IMAGE)` wrapper. Update the
  comment above `lint`.
- Prove it bites. Temporarily make `nusb` non-optional, run
  it, and confirm it fails naming `nusb`. Do not commit that.
- `make lint` passes.

### Brief 8: documentation

- `docs/development.md` *Cargo features*: a renderer
  subsection with decision 7's table (in prose or a table),
  how to build the slim renderer, and that `make lint` checks
  it. Correct the `audio` bullet: without `audio` the playback
  channel is now still linked for a `Sink` session.
- `AGENTS.md` *Cargo feature gating*: one or two sentences that
  the renderer now has its own feature set, the same
  gate-at-the-use-site rule applies, and `make lint` runs the
  slim check. Nothing else in `AGENTS.md`.
- `ARCHITECTURE.md`:
  - The trait table row (:114): `OpusPacketSink` becomes
    `PlaybackSink`, now carrying decoded PCM and format
    events.
  - The compression crate row (:30): H.264 decode is a
    feature.
  - Add the event bus, pointer translator and damage region
    to the renderer's file tree.
  The shape of the system does not otherwise change.
- `docs/multi-mode-parity.md`:
  - Audio playback row (:109), web cell: no host cpal thread
    any more.
  - Row :207: correct the PCM fallback wording to match what
    the code does.
  - Add a mouse wheel row: GUI available, headless n/a,
    web missing (survey item 9).
- `docs/web-mode-internals.md`: the event bus helper, the
  playback sink mode, and the pointer translator, replacing
  any description of the hand-written tasks.
- `docs/rendering-pipeline.md`: a short section on damage
  tracking. What is recorded per op (`copy_bits` damages its
  destination), the merge and bound rules, the
  independence from the dirty bit, and lag marking everything
  damaged.
- `docs/control-socket-protocol.md`: remove
  `UsbDevicesChanged` if step 4 has not already.
- `shakenfist-spice-renderer/README.md`: the feature list, if
  it has one; add one if it does not, since crates.io
  consumers read it.
- Check with `grep -rn OpusPacketSink docs/ ARCHITECTURE.md
  AGENTS.md` that no stale name remains outside `docs/plans/`.

## Risks and mitigations

- **A missed `cfg` breaks a feature combination nobody
  builds.** Mitigation: step 7's per-feature checks run in
  every `make lint`. The management session reviews step 5
  by running the slim clippy itself, not by reading the
  report.
- **Feature unification hides a missing feature in ryll.** A
  workspace build unifies features, so ryll forgetting to
  request `webdav` would still build under `make build`.
  Mitigation: `make check-windows` builds `-p ryll` alone. The
  management session also runs `cargo check -p ryll` and
  `cargo check -p ryll --no-default-features` alone after
  step 5.
- **The pointer change regresses the GUI.** The GUI's pointer
  block is egui-coupled and has no unit tests. Mitigation:
  the translator's behaviour for the GUI's calls (`button`
  with no position, `motion`) is identical to today's by
  construction. The management session runs the GUI against
  the local QEMU test server after step 3, and checks
  hover, click, drag and wheel in both mouse modes (a guest
  with and without vdagent).
- **The server-mode catch-up delta surprises a `--web`
  user.** Mitigation: it only fires when a button arrives at
  a position no move reported, which the browser rarely does.
  Decision 5 records it as deliberate, and the PR
  description calls it out.
- **Damage tracking slows the draw path.** One `add` per
  written sub-rectangle, with at most 32 rectangles to scan.
  Mitigation: none needed beyond keeping `add` linear; the
  management session rejects a quadratic-per-pixel design in
  review.
- **The playback refactor breaks local audio in the GUI.** The
  cpal thread code moves but should not change. Mitigation:
  step 6 runs in a worktree, the existing `playback.rs` tests
  move with their code, and the management session plays guest
  audio through the GUI after step 6.
- **Kerbside's spike step 3b wants a different damage API.**
  Mitigation: the API (decision 1) is a plain region taken
  under the mirror lock, which is what spike 3b's brief
  describes. After this phase merges, the management session
  points kerbside PR #581 (or its successor) at the
  merged API.
- **Publishing.** Feature names and the `PlaybackSink`
  rename are breaking for crates.io consumers of 0.3.0.
  Mitigation: acceptable at 0.x; the next release notes must
  list them. `tools/package-check-verify.sh` (`cargo package
  --workspace`) in CI catches a manifest that does not
  package.

## Definition of done

- [x] `tools/check-slim-renderer.sh` exists, `make lint` runs
      it, and it passes. It fails when `nusb` is made
      non-optional (verified in step 7, not committed).
- [x] `cargo tree -p shakenfist-spice-renderer
      --no-default-features -e normal,build` lists none of
      `openh264`, `openh264-sys2`, `mozjpeg-sys`, `nusb`,
      `dav-server`, `hyper`, `cpal`, `alsa-sys`. Its output is
      recorded in *Results* below.
- [x] `grep -rn "fn take_damage" shakenfist-spice-renderer/src`
      and `grep -rn "pub struct PointerTranslator"` and
      `grep -rn "pub struct EventBus"` each find one
      definition. `grep -n "last_mouse_pos\|last_pos"
      ryll/src/app/mod.rs ryll/src/web/inputs.rs` finds no
      hand-rolled position state.
- [x] `grep -rn "broadcast::channel::<" ryll/src/main.rs
      shakenfist-spice-renderer/src/session.rs` finds nothing;
      both modes use `EventBus`.
- [x] `grep -rn "OpusPacketSink" --include=*.rs --include=*.md
      . | grep -v docs/plans/` finds nothing.
- [x] `run_web` passes `PlaybackOutput::Sink`; a `--web`
      session against the local test server logs no audio
      thread start.
- [x] Damage tests cover `copy_bits` destination-only damage,
      the 32-rectangle bound and the lag path; pointer tests
      cover both modes and the catch-up delta.
- [x] `make build`, `make test`, `make lint`, `make
      check-windows` and `pre-commit run --all-files` pass.
- [x] The GUI is checked by hand (pointer in both modes,
      wheel, local audio) and the result is recorded in
      *Results*.
- [x] The docs listed in brief 8 are updated, and no doc
      outside `docs/plans/` describes `--web` as opening a
      local audio device.
- [x] The master plan's acceptance for phase 1 is met. Where
      it differs from this plan (decision 5's `--web`
      behaviour change), the difference is recorded here.

## Results

Filled in by the management session as steps land.

- Slim dependency tree after step 5 (`cargo tree -p
  shakenfist-spice-renderer --no-default-features -e
  normal,build`, 266 lines):
  - Direct dependencies: anyhow, base64, flate2, image, libc,
    lz4_flex, serde, serde_json, shakenfist-spice-compression,
    shakenfist-spice-protocol, socket2, tokio, tokio-util, tracing.
  - None of `openh264`, `openh264-sys2`, `mozjpeg-sys`, `nusb`,
    `dav-server`, `hyper`, `cpal` or `alsa-sys` appear (checked by
    grep in the management session).
  - The only `-sys` crate left is `aws-lc-sys` (a C build needing
    cmake). It does not come from the renderer: the protocol
    crate's `tokio-rustls` takes rustls's default crypto provider,
    aws-lc-rs. The master plan's acceptance names openh264, nusb,
    dav-server and cpal, so the criterion is met, but a slim
    consumer still needs a C toolchain. Recorded in *Future work*.
- GUI pointer check after step 3, 2026-10-20 (AEDT), against the
  `debian-xfce:13` desktop guest on SPICE port 5910 (5900 was in
  use by the local andris service):
  - Client mode: Mikal checked hover, click, drag, right-click and
    wheel; all fine.
  - Server mode: buttons, wheel and relative motion work. Getting
    the guest into server mode took more than stopping vdagent.
    q35's default `vmmouse` is an absolute device, so spice-server
    keeps offering client mode through it. The guest was restarted
    with `-machine q35,vmport=off`, `-vga virtio` and
    `agent-mouse=off`, after which the server offered server mode
    only.
  - In server mode the host and guest cursors drift apart until
    the host cursor leaves the window before reaching the target.
    This predates the phase. A scripted closed pointer path,
    replayed with python-xlib over each window, moved the guest
    cursor about 50 px left per run in both the develop `7690e1e`
    build and the step-3 build (runs: −53, −51, −49 px; matching
    cursor-move counts). The cause is the guest's pointer
    acceleration on relative motion, with no pointer grab or warp
    in the GUI to correct it (spice-gtk grabs in server mode).
    Recorded in *Future work*.
- Audio checks after step 6, same guest, with `alsa-utils`
  installed in it and `speaker-test` playing (it played pink noise,
  its default, rather than the intended sine):
  - GUI (`Local`): Mikal heard the audio. The log showed `MODE: 3`
    (Opus), START, and the local audio thread starting. The develop
    build sounded the same.
  - `--web` (`Sink`): the log showed `MODE: 3`, START and the Opus
    decoder starting, and no line about a local audio thread or
    output device (0 matches for "audio output started", "audio
    output unavailable" or "audio thread"). No browser was attached,
    so audio arriving in a browser was not checked.
- Final tree: `make build`, `make test` (1505 passed, 0 failed),
  `make lint` (including the slim check), `make check-windows`,
  `make package-check` and `pre-commit run --all-files` all pass.

## Execution notes

- Run on 2026-10-20 (AEDT), steps in plan order.
- The plan was written at low planning effort, not the high effort
  the master plan asks for; Mikal chose to proceed with it as
  written.
- Steps 5 and 6 ran in worktrees made by hand from the phase
  branch, then fast-forwarded into it, because the Agent tool's own
  worktree isolation branches from the default branch.
- Step 4 ran while the desktop guest was booting for the step 3 GUI
  check, which used a binary copied out of the tree first.
- The desktop guest ran on SPICE port 5910 via
  `tools/start-desktop-qemu.sh`, because the local andris service
  holds 5900, so `make test-qemu-desktop` could not be used as is.
- Review changes beyond the briefs:
  - Step 4: gated run_headless's mirror handle, which step 2 had
    left unused on Windows.
  - Steps 5 and 8: corrected two comment and doc claims.
  - Step 8: added the GUI's server-mode drift to the parity
    matrix.
  Each commit message says what review changed.
- The PR's first automated review changed decision 11: the
  per-feature builds in `tools/check-slim-renderer.sh` now run
  clippy with warnings denied rather than `cargo check`, so a
  gating mistake that only warns in one feature's build fails
  lint. It also made a sink's `on_stop` follow an `on_start`
  only, and made raw PCM conversion reuse one buffer.

## Future work

- **Headless audio.** Headless still plays audio locally when
  a device exists, and retries cpal when none does. With
  `PlaybackOutput` in place, a headless discard or sink mode
  is a one-line choice; whether headless *should* play audio
  is a product decision (decision 9).
- **`ryll/Cargo.toml` declares crates it never uses
  directly**: nusb, dav-server, hyper, hyper-util, http,
  http-body-util, lz4_flex, image, jpeg-decoder, flate2,
  socket2, rsa, sha1, byteorder, tokio-rustls, rustls-pemfile,
  webpki-roots and async-channel (per the survey; `http::`
  hits in `ryll/src` are `axum::http`). Pruning them does not
  affect a renderer consumer, but tidies ryll's own tree.
- **Web mouse wheel** (#594, survey item 9): add a wheel message to
  `app.js` and the relay, through `PointerTranslator::wheel`.
- **GUI server-mode pointer grab** (#592). In server mouse mode the GUI
  sends relative deltas from host hover positions without grabbing
  or warping the pointer, so guest acceleration makes the two
  cursors drift until parts of the guest display cannot be reached
  (see *Results*). spice-gtk grabs the pointer in server mode.
  This predates the phase; the RDP frontend will meet the same
  problem in server mode.
- **The protocol crate's TLS provider** (#593).
  `shakenfist-spice-protocol` depends on `tokio-rustls` with
  default features, which pulls in aws-lc-rs and `aws-lc-sys` (a
  cmake C build), while ryll itself installs `ring` as its rustls
  provider. Choosing one provider in the protocol crate (probably
  `ring`, with `default-features = false`) would take the last C
  build out of the slim renderer and stop ryll building two crypto
  libraries. Check what Kerbside's proxy expects before changing
  it.
- **cargo-deny on the slim tree**: it runs with
  `--all-features`, so it audits a superset. Probably fine;
  noted in case a licence ever differs between the two trees.

## Back brief

Before executing any step of this plan, back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with it. Step 5 is the step that is
cheap to propose and expensive to redo: before its sub-agent
starts, the management session confirms that decision 7's
feature table still matches the tree after step 4.
