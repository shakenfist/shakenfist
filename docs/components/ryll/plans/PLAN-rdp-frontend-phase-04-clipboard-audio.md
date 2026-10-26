# RDP frontend phase 4: clipboard and audio

Master plan: [PLAN-rdp-frontend.md](/components/ryll/plans/PLAN-rdp-frontend/).

**Planning effort:** medium, as the master plan's guidance asks
for phases 3 to 6: both features follow patterns phases 1 to 3
set (a renderer-side relay, a session handle, an IronRDP factory
registered in `build_server`). Review steps 1 and 2 at high
effort all the same: the survey found that IronRDP drops the
whole RDP connection when either channel is sent a message in
the wrong state, so a sequencing mistake there disconnects the
user rather than merely losing a paste.

## Prompt

Read the master plan's *Prompt* first; everything there
applies. For this phase in particular:

- The RDP side is still IronRDP **0.13.0**: `ironrdp-server`
  0.13.0, `ironrdp-cliprdr` 0.7.0, `ironrdp-rdpsnd` 0.9.0,
  `ironrdp-acceptor` 0.10.0, all already in `Cargo.lock`.
  `make fetch` puts their source under
  `.cargo-cache/registry/src/index.crates.io-*/` in the
  worktree, and every IronRDP file and line cited below is
  relative to the crate directory there. Read it; never build
  or run anything from inside it.
- The SPICE clipboard reference is spice-gtk at
  `/srv/src-reference/spice/spice-gtk` (`src/spice-gtk-session.c`
  for line endings). No FreeRDP or qemu-rdp source is available
  locally; claims about FreeRDP are checked in the manual test,
  not assumed.
- The host's FreeRDP is 3.15.0 (`xfreerdp3`), built with
  `WITH_OPUS=ON` and `WITH_DSP_FFMPEG=ON` (`xfreerdp3
  /buildconfig`), so it can decode Opus audio.
- Builds run in Docker through the Makefile, never with a host
  toolchain.

## Scope

In scope:

- **Text clipboard both ways** between the RDP client's
  `CLIPRDR` channel (`CF_UNICODETEXT`) and the session's
  `MemoryClipboard`, which the renderer's main channel already
  ferries to and from the guest's vdagent.
- **Audio playback** through the RDP client's `RDPSND` channel,
  from the renderer's device-free `PlaybackOutput::Sink`: Opus
  passed through unchanged when the client accepts Opus,
  decoded 48 kHz PCM when it does not.
- `--rdp` always creating the session's `MemoryClipboard`
  (today only with `--control-socket`), shared by the RDP client
  and a control socket.
- The loopback test extended to see clipboard text cross both
  ways and an audio wave arrive, decoded by IronRDP's own
  client-side channels.
- A manual FreeRDP test against the desktop guest, recorded in
  *Results*.
- Documentation of the user-visible changes.

Out of scope:

- Rich clipboard formats (images, HTML) and file copy (master
  plan *Future work*).
- Microphone, audio volume and `RDPSND` `Close`/`SetVolume`
  (decision 10).
- Resampling or re-encoding audio: a SPICE stream that is not
  48 kHz stereo is not played (decision 7).
- New command-line flags (decision 1).
- Line-ending conversion inside the renderer for guests that
  announce CRLF, which is a gap in every mode, filed as
  [#624](https://github.com/shakenfist/ryll/issues/624)
  (decision 5).
- mstsc: no Windows machine is available, as in phases 2 and 3.

## What the survey found

Checked on 2026-10-25 against the tree at `2edb002` and the
pinned IronRDP crates. The master plan's phase 4 section is
short and broadly right; what follows is what it did not say,
plus four corrections, which this planning commit makes at
source (the master plan's *Prompt*, *What ryll already gives
us*, gap 4, the IronRDP *Channels* bullet, open question 6 and
the phase 4 section, and the `index.md` row).

### Ryll side

1. **The device-free audio path exists.** Phase 1 replaced the
   tap with `PlaybackOutput::Sink(Arc<dyn PlaybackSink>)`
   (`shakenfist-spice-renderer/src/playback_sink.rs:52-106`).
   A sink gets `on_start(PlaybackFormat)`, `on_opus_packet`
   before decode, `on_decoded_pcm` after it (always 48 kHz),
   `on_pcm_samples` for a raw-PCM server, and `on_stop`. It
   opens no device in any build. *Correction:* the master plan
   still names `OpusPacketSink`, which phase 1 renamed, and
   lists gap 4 as open.
2. **`--rdp` discards audio today.** `ryll/src/rdp.rs:42-48`
   defines `DiscardPlayback`, a sink that drops every packet,
   and `:224` hands it to the session.
3. **`--rdp` makes a `MemoryClipboard` only with a control
   socket** (`ryll/src/rdp.rs:189-206`), and gives the same one
   to the control socket's handles (`:352-356`). Without a
   socket the main channel gets no clipboard at all.
4. **`MemoryClipboard`** (`shakenfist-spice-renderer/src/clipboard.rs:53-160`)
   is what an RDP backend needs: `set_host_text` for what the
   RDP client copied, `text()` to answer a paste request, and
   `subscribe_guest_copies()`, a watch that changes on every
   guest copy (with its length, not its text). The main channel
   polls it every 500 ms and grabs the guest's clipboard when
   the host text changes.
5. **The main channel passes clipboard text through
   unconverted.** spice-gtk converts line endings when the
   guest announces `VD_AGENT_CAP_GUEST_LINEEND_CRLF`
   (`spice-gtk-session.c:812`, `:1002`); ryll defines the
   constants (`shakenfist-spice-protocol/src/constants.rs:758-759`)
   and reads neither. Filed as #624. The renderer's host-side
   text is therefore LF text, as on a Unix host.
6. **`SessionHandles`** (`shakenfist-spice-rdp/src/lib.rs:68-87`)
   is built at eight sites: `ryll/src/rdp.rs:241`,
   `tests/loopback.rs:194`, and tests in `input.rs` (`:524`,
   `:903`), `display.rs` (`:789`, `:1479`), `auth.rs` (`:177`)
   and `server.rs` (`:620`, `:775`, `:816`). A new field
   touches every one.

### IronRDP side

7. **Both channels plug in as factories** on the builder:
   `with_cliprdr_factory(Option<Box<dyn CliprdrServerFactory>>)`
   and `with_sound_factory(Option<Box<dyn SoundServerFactory>>)`
   (`ironrdp-server` `src/builder.rs:175-183`). Each factory
   gets the server's event sender once, from `RdpServer::new`
   (`server.rs:522-527`), and builds a fresh backend for every
   connection (`attach_channels`, `server.rs:668-681`). The
   backend talks back by sending `ServerEvent::Clipboard(..)` or
   `ServerEvent::Rdpsnd(..)` on that sender, which is the same
   unbounded sender `RdpServerHandle::shutdown` sends `Quit` on.
8. **`ironrdp-server` re-exports too little.** It re-exports
   `CliprdrServerFactory`, `RdpsndServerHandler`,
   `RdpsndServerMessage` and `SoundServerFactory`
   (`src/lib.rs:24`, `:40`), but not `CliprdrBackend`,
   `CliprdrBackendFactory`, `ClipboardMessage`, the clipboard
   PDU types, `AudioFormat`, `WaveFormat` or
   `NegotiatedFormat`. The crate must depend on
   `ironrdp-cliprdr` 0.7 and `ironrdp-rdpsnd` 0.9 directly; both
   are already in `Cargo.lock` through `ironrdp-server`, so this
   adds edges, not crates (as phase 3 did for
   `ironrdp-displaycontrol`).
9. **A message sent in the wrong state drops the connection.**
   `dispatch_server_events` (`server.rs:1081-1165`) applies each
   event with `?`. `RdpsndServer::wave` fails until the client's
   formats have arrived and a format has been chosen
   (`ironrdp-rdpsnd` `src/server.rs:184-189`);
   `CliprdrServer::initiate_paste` and `submit_format_data` fail
   until the channel is Ready (`ironrdp-cliprdr` `src/lib.rs:573-583`,
   `:773-776`, `:857-858`). Any of those failures ends the RDP
   connection. `initiate_copy` never fails on the server side
   (`:816-830`).
10. **When each channel becomes usable.** The server starts its
    static channels once, on the first activation (`server.rs:1384-1394`):
    rdpsnd sends its formats, cliprdr its capabilities and
    Monitor Ready. cliprdr turns Ready, and calls the backend's
    `on_ready`, when the client's first Format List arrives
    (`lib.rs:665-670`), which a client sends straight after its
    capabilities. rdpsnd calls the handler's `choose_format`
    and then `start` when the client's Training Confirm arrives
    (`src/server.rs:319-363`), and `stop` when the channel is
    dropped at the end of the connection (`:386-390`).
11. **The server chooses the audio format, not the client.**
    `choose_format` is given the formats both sides support, in
    the server's preference order (`src/server.rs:79-115`,
    `negotiate_formats` `:240-259`); a client expresses
    preference only by leaving formats out of its reply. Formats
    match on tag, channels, rate, bit depth and the extra-data
    blob, ignoring the two derived fields (`:271-277`). Opus is
    defined as `WaveFormat::OPUS` (`0x704F`, `src/pdu/mod.rs:198`)
    with no encoder, as the master plan says. *Correction:* the
    master plan's open question 6 says "let the client pick".
12. **IronRDP keeps only the last four waves of a batch.**
    `dispatch_server_events` drops the oldest waves when more
    than four are queued at once (`server.rs:1083-1097`). A
    burst of short waves after any stall (a large RemoteFX
    frame being written, a reactivation) loses audio.
13. **Events are not drained during a handshake.** Between
    connections the accept loop reads the event channel and
    discards clipboard and sound events (`server.rs:936-955`);
    inside a connection the client loop dispatches them
    (`:1300-1322`). During a handshake nothing reads the
    channel, and it is unbounded. A backend that sends only
    after its channel has started sends nothing then.
14. **Reactivation keeps the channels but loses client PDUs.**
    A deactivation-reactivation (every resize since phase 3)
    moves the static channels into the acceptor and back
    (`server.rs:1739-1753`), so channel state survives and
    `start` is not called again. While it runs, the acceptor
    drops any client PDU it does not expect
    (`ironrdp-acceptor` `src/connection.rs:812-839`), static
    channel PDUs included. Phase 3's two-second hold on the
    first resize (`FIRST_RESIZE_GRACE`) keeps the first
    reactivation clear of both channels' initial handshakes; a
    client copy made during a later reactivation would be lost.
15. **IronRDP's client side can drive both channels** for the
    loopback test: `ClientConnector::with_static_channel`
    (`ironrdp-connector` `src/connection.rs:155`), the client
    `Cliprdr` in `ironrdp-cliprdr`, and `Rdpsnd` with an
    `RdpsndClientHandler` (`ironrdp-rdpsnd` `src/client.rs:13-28`,
    `:58-75`).

## Decisions

1. **No new flags; the library's opt-out is a `None`.** Both
   factories are always registered, so every client is offered
   both channels. `SessionHandles` gains
   `clipboard: Option<Arc<MemoryClipboard>>` and
   `audio: Option<Arc<AudioRelay>>`; a session with `None`
   answers nothing on that channel (the clipboard backend
   ignores the client, the sound handler declines every format).
   `ryll --rdp` always fills both. Reasoning: in ryll the
   operator and the user are the same person, and every RDP
   client already has a switch for each (FreeRDP's `-clipboard`
   and `/sound`, mstsc's local resources tab), so a server flag
   would duplicate one the user already has. The `None` is the
   knob a Kerbside gateway needs to apply its firewall policy's
   `permitted_channels` per session. If an operator asks for a
   server-side switch, it is a two-line follow-up.
2. **One `MemoryClipboard` per session, shared.** `--rdp` makes
   it unconditionally, gives it to the main channel, to
   `SessionHandles.clipboard` and, when there is one, to the
   control socket. Its two writers already keep host and guest
   text apart.
3. **The client's copies are fetched eagerly.** When the client
   announces a Format List containing `CF_UNICODETEXT`, the
   backend asks for the text at once and stores it with
   `set_host_text`. The SPICE side of ryll has no delayed
   rendering for host text (`ClipboardBackend::get_text` is a
   synchronous read), so a lazy fetch would need renderer work
   for little gain. The cost is one transfer per client copy,
   which the GUI also pays by polling the host clipboard.
4. **On connect, the client's clipboard wins.** A client sends
   its current clipboard as its first Format List, and decision
   3 pushes it to the guest. Guest copies made before the client
   connected are not offered to it; guest copies made afterwards
   are, each as a Format List with `CF_UNICODETEXT`, and the text
   is read from `MemoryClipboard::text()` only when the client
   asks for it. This is what an RDP session to Windows does, and
   the reverse would overwrite the user's local clipboard every
   time they connect.
5. **Line endings are converted at the RDP boundary.**
   `CF_UNICODETEXT` is Windows text (CRLF, NUL-terminated
   UTF-16LE); the renderer's host-side text is LF (survey 5).
   Text from the client has CRLF turned into LF; text to the
   client has every LF not already preceded by CR turned into
   CRLF. This matches what the GUI sends a guest from a Unix
   host. Converting for CRLF guests is the renderer's job, for
   every mode (#624).
6. **Never send a message the channel can refuse.** From survey
   9: the clipboard backend sends `SendInitiatePaste` and
   `SendFormatData` only after `on_ready`, and the sound handler
   sends a wave only between `start` and `stop`. Anything that
   would break the rule is dropped with a `debug!` rather than
   sent. This is the rule the review checks first, and the unit
   tests pin it.
7. **Audio formats: Opus first, then PCM, both 48 kHz stereo.**
   The handler advertises Opus (`0x704F`, 2 channels, 48000 Hz)
   and then PCM (`WAVE_FORMAT_PCM`, 2 channels, 48000 Hz, 16
   bit), and `choose_format` takes the first common one. Opus
   costs nothing to pass through and is a few tens of kbit/s;
   PCM is 1.5 Mbit/s, which matters on the constrained networks
   this mode exists for. Only 48 kHz stereo is offered because
   decoded Opus is 48 kHz and nothing in ryll resamples. A SPICE
   stream that does not fit the chosen format (a raw-PCM server
   at another rate or channel count, or raw PCM when the client
   chose Opus) is not played, with a `warn_once!`. Spice-server
   and QEMU send 48 kHz stereo Opus, so this is the rare case.
8. **Audio reaches the RDP thread through a relay on the
   session.** A new `AudioRelay` in `shakenfist-spice-rdp`
   implements `PlaybackSink` and publishes on a small
   `tokio::sync::broadcast` channel; it also keeps the stream's
   current `PlaybackFormat`, so a client that connects mid-stream
   knows it. `ryll` passes the same `Arc<AudioRelay>` as the
   session's `PlaybackOutput::Sink` and as
   `SessionHandles.audio`. The sound handler, on `start`, takes
   the session from the slot, subscribes, and spawns a forwarder
   task on the server thread that turns relay items into waves;
   `stop` aborts it. Subscribing at `start` means no stale
   audio is ever queued and nothing is sent before the channel
   can take it (decision 6); the broadcast's lag drops old audio
   rather than growing. The relay lives in the RDP crate, beside
   `spawn_cursor_relay`, so a gateway gets it with the crate.
   The rejected alternative, a sink holding the connection's
   sender directly, is simpler but ties the session to a
   connection and bypasses the session seam.
9. **One Opus packet per wave; PCM in 20 ms waves.** FreeRDP
   decodes one Opus packet per wave, so packets are not merged.
   Decoded PCM is gathered into waves of at least 960 frames
   (20 ms) before sending, and flushed on `on_stop`, because
   spice-server also sends 256-sample packets, and survey 12
   means four 5 ms waves are all the slack a stall gets.
10. **No `Close` and no `SetVolume`.** A SPICE stop sends
    nothing; the client stops hearing waves. `SetVolume` fails
    unless the client set the volume flag, and that failure would
    drop the connection (survey 9), for a feature SPICE does not
    give ryll anyway (`PLAYBACK_VOLUME` is not advertised).
11. **Wave timestamps count milliseconds of audio sent** since
    the forwarder started, wrapping at `u32::MAX`, derived from
    sample counts rather than the wall clock.

## Step plan

Steps run in order, in the phase worktree, because steps 1 and
2 both edit `lib.rs`, `server.rs`, `ryll/src/rdp.rs` and
`Cargo.toml`, and every step runs `make test` in the same tree.
Each step is one commit, with the subject given.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | none | Clipboard: `SessionHandles.clipboard`, a cliprdr factory and backend over `MemoryClipboard`, registration in `build_server`, and `--rdp` always making the clipboard. Commit: "Carry clipboard text between RDP clients and the guest." See brief 1. |
| 2 | high | opus | none | Audio: `AudioRelay`, `SessionHandles.audio`, a sound factory and handler with the forwarder, registration, and `--rdp` using the relay instead of `DiscardPlayback`. Needs step 1. Commit: "Play the guest's audio to RDP clients." See brief 2. |
| 3 | high | opus | none | Loopback test: clipboard both ways and a wave in the negotiated format, through IronRDP's client-side channels. Needs steps 1 and 2. Commit: "Prove RDP clipboard and audio with the loopback client." See brief 3. |
| 4 | - | - | - | Manual FreeRDP test against the desktop guest, run by the management session with Mikal listening, recorded in *Results*. Needs steps 1-3. No commit of its own; the results go in with step 5. |
| 5 | medium | sonnet | none | Documentation and the master plan. Needs step 4's results. Commit: "Document RDP clipboard and audio." See brief 5. |

### Brief 1: clipboard

Files: `shakenfist-spice-rdp/Cargo.toml`, a new
`shakenfist-spice-rdp/src/clipboard.rs`, `src/lib.rs`
(`SessionHandles`, `:68-87`, and the module list and crate
docs), `src/server.rs` (`build_server`, `:427-486`), every
`SessionHandles` construction site (survey 6), and
`ryll/src/rdp.rs` (`:185-206`, `:241-248`, `:316`, `:349-356`).

Read first, in full: survey items 4, 5, 7-10 and 14 above;
`ironrdp-cliprdr-0.7.0/src/backend.rs`; `lib.rs:485-930` and
`:1596-1700` of the same crate; `ironrdp-server-0.13.0/src/server.rs:505-545`,
`:655-690` and `:1080-1170`; and how `spawn_cursor_relay` and
`MirrorDisplay` read the session from the `SessionSlot`.

- Add `ironrdp-cliprdr = "0.7"` with a comment in the style of
  the existing entries (an edge, not a crate; why it is needed:
  `ironrdp-server` does not re-export the backend trait or PDU
  types). Check `Cargo.lock` gains no package; refresh it only
  with the Makefile target that may write it.
- `SessionHandles` gains
  `pub clipboard: Option<Arc<MemoryClipboard>>`, documented
  (decision 1). Every construction site sets it; tests that do
  not exercise the clipboard use `None`.
- `clipboard.rs`: a factory implementing `CliprdrBackendFactory`
  and `ServerEventSender` (hence `CliprdrServerFactory`), holding
  the `SessionSlot` and the event sender once `set_sender` gives
  it; and a per-connection backend implementing `CliprdrBackend`:
  - `client_capabilities`: empty (no file stream, no locking);
    `temporary_directory`: an empty string.
  - `on_ready`: record readiness; take the session from the
    slot; if it has a clipboard, subscribe to
    `subscribe_guest_copies()` and spawn (`tokio::spawn`; the
    server thread's runtime is current; check) a task that sends
    `ClipboardMessage::SendInitiateCopy(vec![CF_UNICODETEXT])`
    on each change. The subscription starts marked seen, which
    gives decision 4. Abort the task when the backend is
    dropped.
  - `on_remote_copy(formats)`: if ready, the session has a
    clipboard and `CF_UNICODETEXT` is offered, send
    `SendInitiatePaste(CF_UNICODETEXT)` (decision 3).
  - `on_format_data_response`: unless `is_error`, decode with
    `to_unicode_string`, convert CRLF to LF (decision 5) and
    `set_host_text`. Log byte counts only, never the text
    (as the main channel does).
  - `on_format_data_request`: if ready, answer
    `SendFormatData(OwnedFormatDataResponse::new_unicode_string(..))`
    with the clipboard's text converted LF to CRLF, or
    `new_error()` when there is no text, no clipboard or another
    format was asked for.
  - Every other callback is a no-op with a `debug!`.
  - Nothing that needs Ready is ever sent before `on_ready`
    (decision 6). Write the backend's send path so this is
    structural, not a check repeated at each call site.
- Register the factory with `with_cliprdr_factory` in
  `build_server`.
- `ryll/src/rdp.rs`: make the `MemoryClipboard` unconditionally
  (decision 2), give it to `run_connection`, to
  `SessionHandles.clipboard`, and to the control socket when
  there is one. Replace the comment at `:189` ("not carried
  yet").
- Unit tests in `clipboard.rs`, driving the backend directly
  with a real `MemoryClipboard` and an event sender whose
  receiver the test reads: a guest copy after `on_ready` sends
  one `SendInitiateCopy` with `CF_UNICODETEXT`; a guest copy
  before `on_ready` sends nothing then and nothing on
  `on_ready`; `on_remote_copy` with and without
  `CF_UNICODETEXT`, before and after ready; a format data
  response lands in `MemoryClipboard::text()` with CRLF turned
  into LF and is not published as a guest copy; a format data
  request answers with NUL-terminated UTF-16LE CRLF text (a
  multi-line, non-ASCII string), with an error response when
  empty, and with nothing before ready; a session with
  `clipboard: None` sends nothing at all; the conversion helpers
  do not double an existing CRLF.
- Mutation-check in a copy (restore from a copy, never with
  `git checkout`), one mutant per run under `timeout`: dropping
  the ready gate, either line-ending conversion, the
  `CF_UNICODETEXT` filter, and the eager paste. Report the
  script path and counts.
- `make build`, `make test`, `make lint`, `pre-commit run
  --all-files`.

### Brief 2: audio

Files: `shakenfist-spice-rdp/Cargo.toml`, a new
`shakenfist-spice-rdp/src/audio.rs`, `src/lib.rs`, `src/server.rs`,
every `SessionHandles` construction site, and `ryll/src/rdp.rs`
(`DiscardPlayback`, `:42-48`; `:224`; `:241-248`).

Read first, in full: survey items 1, 7-14;
`shakenfist-spice-renderer/src/playback_sink.rs`;
`ironrdp-rdpsnd-0.9.0/src/server.rs`;
`ironrdp-server-0.13.0/src/sound.rs` and `server.rs:1080-1140`;
`ryll/src/web/audio.rs` (the existing `PlaybackSink`, for the
non-blocking rule); and `src/cursor.rs` (the relay pattern this
follows).

- Add `ironrdp-rdpsnd = "0.9"`, commented as in brief 1.
- `audio.rs`:
  - `AudioRelay`: `PlaybackSink` over a
    `broadcast::Sender<AudioItem>` with a small capacity (about
    16 items, a third of a second of 20 ms packets; say why in
    a comment), keeping the latest `PlaybackFormat` (or none
    after `on_stop`) behind a mutex for late subscribers.
    `AudioItem` carries an Opus packet with its sample count,
    decoded PCM (48 kHz, from `on_decoded_pcm`), raw PCM with
    its rate and channels (from `on_pcm_samples`), start and
    stop. Every callback is non-blocking; sending with no
    subscriber is not an error.
  - A sound factory (`SoundServerFactory`) holding the slot and
    the event sender, building a handler per connection.
  - The handler (`RdpsndServerHandler`): `get_formats` returns
    decision 7's two formats (fill `n_avg_bytes_per_sec` and
    `n_block_align` sensibly; IronRDP's match ignores them,
    survey 11); `choose_format` returns the first common format,
    or `None` when the session has no relay; `start` subscribes
    to the session's relay and spawns the forwarder; `stop`
    aborts it. Log the chosen format at `info!` once per
    connection: the manual test reads it.
  - The forwarder: Opus packets become one wave each when Opus
    was chosen; decoded PCM, when PCM was chosen, is gathered
    into waves of at least 960 frames as little-endian `i16`
    bytes (decision 9), flushed on stop; raw PCM is used only if
    it is 48 kHz stereo and PCM was chosen; everything else is
    dropped with a `warn_once!` naming why (decision 7). A
    `Lagged` receive is a `warn_once!` and carries on. Each wave
    goes out as `ServerEvent::Rdpsnd(RdpsndServerMessage::Wave(bytes, ts))`
    with decision 11's timestamp. No `Close`, no `SetVolume`
    (decision 10).
- `SessionHandles` gains `pub audio: Option<Arc<AudioRelay>>`;
  register the factory with `with_sound_factory`; re-export
  `AudioRelay` from `lib.rs` and describe audio in the crate
  docs.
- `ryll/src/rdp.rs`: delete `DiscardPlayback`; make one
  `Arc<AudioRelay>`, pass it as `PlaybackOutput::Sink` and as
  `SessionHandles.audio`.
- Unit tests: the relay delivers each kind of item and keeps
  the format for a late subscriber; `choose_format` prefers Opus
  and falls back to PCM, and declines with no relay; the
  forwarder sends nothing before `start` and nothing after
  `stop`; Opus passes through byte for byte; PCM is gathered to
  960 frames, in order, little-endian, and flushed on stop;
  raw PCM at 44.1 kHz and Opus-when-PCM-was-chosen are dropped;
  timestamps advance by the audio's duration. Use paused tokio
  time where a test waits.
- Mutation-check as in brief 1: the start gate, the format
  preference order, the PCM gathering threshold, the byte
  order, the timestamp step.
- `make build`, `make test`, `make lint`, `make check-windows`,
  `pre-commit run --all-files`.

### Brief 3: loopback test

File: `shakenfist-spice-rdp/tests/loopback.rs`, and
`shakenfist-spice-rdp/Cargo.toml` if a dev-dependency is
needed (the two channel crates are already normal dependencies
after steps 1 and 2). Read the whole test file first; the test
at `:559` and its `Client` harness (`:300-540`) are the
pattern.

- Add a second `#[tokio::test]` rather than growing the first,
  sharing the harness. Attach a client `Cliprdr` with a
  recording backend and an `Rdpsnd` with a recording handler to
  the connector (`ClientConnector::with_static_channel`, survey
  15), with a session that has a `MemoryClipboard` and an
  `AudioRelay`.
- Assert, in order: the client's initial Format List with
  `CF_UNICODETEXT` makes the server ask for the data, and the
  client's answer lands in the session's `MemoryClipboard` with
  LF line endings; a guest copy (`set_text` on the clipboard, as
  the main channel would) reaches the client as a Format List,
  and the client's request for it is answered with the text in
  CRLF form; with the client offering PCM only, a decoded-PCM
  push on the relay reaches the client's handler as a PCM wave
  of the right bytes. If time allows, a second connection that
  offers Opus as well receives an Opus packet unchanged.
- Find how the client's `ActiveStage` hands static-channel
  output back to the harness for sending. If driving the
  client's channels needs a large harness change, stop and
  report rather than building one; the management session
  decides.
- Mutation-check: the clipboard factory not registered, the
  sound factory not registered, the forwarder sending nothing.
  Same rules as brief 1.
- `make test`, `make lint`, `pre-commit run --all-files`.

### Brief 5: documentation and the master plan

Files: `docs/configuration.md` (the RDP Mode prose, `:76-95`),
`docs/multi-mode-parity.md` (the RDP row of the mode table,
`:26`), `docs/plans/PLAN-rdp-frontend.md` (open question 6's
answer and the phase 4 section), `shakenfist-spice-rdp/README.md`
if it lists what the crate serves, and this plan's *Results*.

- The configuration prose: the RDP client's text clipboard
  works both ways (the guest needs vdagent), and the guest's
  audio plays on the client, as Opus when the client accepts it
  and PCM otherwise, with the PCM bandwidth stated. How to turn
  either off in FreeRDP and mstsc, since ryll has no flag
  (decision 1).
- The mode table's RDP row: add clipboard and audio. The
  per-feature RDP column stays phase 6's; record the cells it
  will need (clipboard sync, audio playback, anything step 4
  found) in this plan's *Results*.
- Open question 6 gets an evidence-based answer from step 4:
  what FreeRDP negotiated, with and without Opus available,
  and that mstsc was not tested.
- No phase or decision citations in code or docs outside
  `docs/plans`; no dependency versions in `docs/`.
- `pre-commit run --all-files`.

## Step 4: the manual test

Run by the management session with Mikal, who must listen:
nobody else can hear the audio. Start the desktop guest with
its own PID and QMP paths, as phase 3 learnt to
(`QEMU_PID_FILE=/tmp/ryll-p04-qemu.pid
QEMU_QMP_SOCKET=/tmp/ryll-p04-qmp.sock make test-qemu-desktop`),
and stop it by its own PID afterwards. Serve it with a debug
build of `ryll --rdp --rdp-port 0 --direct 127.0.0.1:5910
--verbose`, and connect `xfreerdp3 /cert:tofu
/dynamic-resolution /sound`. Record in *Results*, with
FreeRDP's version and the date:

- **Clipboard, client to guest:** a single line, several lines
  and non-ASCII text copied on the host and pasted in a guest
  terminal and a guest text editor; whether line endings
  arrive as LF.
- **Clipboard, guest to client:** the same three copied in the
  guest and pasted on the host.
- **Clipboard on connect:** the host's clipboard at connect
  reaches the guest (decision 4).
- **Audio:** the format ryll logged as chosen; that Mikal hears
  the guest's audio (a test tone or a video); whether it
  stutters; then again with `/sound:format:1` or whatever
  FreeRDP's option is for leaving Opus out, so the PCM path is
  heard too.
- **Audio under load:** audio during `tools/rdp-measure.sh`'s
  video workload, whether it drops out (survey 12), and the
  bytes sent with audio playing compared with phase 2's video
  row.
- **After a resize:** clipboard both ways and audio still work
  after a FreeRDP window resize (survey 14).
- **Reconnect:** disconnect and reconnect FreeRDP; clipboard
  and audio work on the second connection.
- mstsc: not tested (none available).

## Risks and mitigations

- **A channel message in the wrong state drops the client**
  (survey 9). Mitigation: decision 6 makes the gate structural;
  briefs 1 and 2 test each "nothing before ready/start" case
  and mutation-check the gates; the loopback test keeps the
  connection alive across both channels; the management
  session reviews every send site in steps 1 and 2 against
  survey 9 before committing.
- **Static-channel PDUs lost in a reactivation** leave a
  connection's clipboard stuck before Ready, or its audio never
  negotiated (survey 14). Mitigation: phase 3's first-resize
  hold covers the initial handshakes; step 4 checks both
  channels after a resize; a later loss is recoverable for the
  clipboard (the next client copy turns the channel Ready) and
  is recorded rather than fixed for audio.
- **Audio drops under display load**, because IronRDP keeps
  only four waves of a batch (survey 12). Mitigation: 20 ms PCM
  waves (decision 9); step 4 listens during the video workload
  and records what happens. If Opus's short packets drop
  audibly, the follow-up is to prefer PCM or to merge packets,
  and it is recorded in *Future work*, not fixed in this phase.
- **FreeRDP does not echo the Opus format byte for byte**, so
  Opus is never common and PCM is used. Mitigation: step 4
  records the chosen format; PCM still plays; a mismatch is
  recorded with what FreeRDP sent.
- **Large clipboard text.** Neither direction is capped beyond
  what `MemoryClipboard` and IronRDP already allow. Mitigation:
  none in this phase; step 4 notes any problem with a large
  paste, and a cap goes to *Future work* if needed.
- **Clipboard content in logs.** Mitigation: brief 1 logs
  byte counts only, as the main channel does; the management
  session greps the diff for logged text.
- **Conflicts with phase 5**, which has not started and edits
  the same files. Mitigation: whichever merges second rebases.

## Definition of done

- [x] `grep -n 'with_cliprdr_factory\|with_sound_factory' shakenfist-spice-rdp/src/server.rs`
      finds both, and `grep -n 'DiscardPlayback' ryll/src/rdp.rs`
      finds nothing.
- [x] `SessionHandles` has `clipboard` and `audio` fields, and
      `ryll/src/rdp.rs` makes the `MemoryClipboard` outside any
      `control_server` branch.
- [x] Unit tests prove nothing is sent on either channel before
      it can take it (decision 6), and the line-ending and PCM
      byte-order conversions, under `make test`.
- [x] The loopback test's new case passes, and fails when
      either factory is unregistered (mutation, management
      session).
- [x] Every mutation listed in briefs 1-3 is killed; the
      scripts' paths and counts are in *Execution notes*.
- [x] `make build`, `make test`, `make lint`, `make
      check-windows`, `make package-check` and `pre-commit run
      --all-files` pass.
- [x] *Results* records every item listed under *Step 4*, with
      FreeRDP's version and the date, and says plainly which
      were not tested.
- [x] The master plan's open question 6 has an answer citing
      step 4, and `docs/multi-mode-parity.md`'s RDP row (`:26`)
      names clipboard and audio.
- [x] `git grep -n -i 'phase 4\|decision [0-9]\|survey item' -- shakenfist-spice-rdp shakenfist-spice-renderer/src ryll/src docs ':!docs/plans'`
      finds nothing.
- [x] `git grep -n -i 'clipboard\|text' -- shakenfist-spice-rdp/src/clipboard.rs | grep -i 'info!\|debug!\|warn!'`
      shows no log line that formats the clipboard text itself.

## Results

### Manual test: FreeRDP against the desktop guest

2026-10-25, about 11:00 to 13:35 AEDT, on the Kasm host, with
Mikal driving and listening. Guest: `debian-xfce:13` under
`tools/start-desktop-qemu.sh` (QXL, vdagent, Intel HDA through
SPICE audio) on SPICE port 5910, with its own PID, QMP and OVMF
variables paths. Server: a debug build of `ryll --rdp
--rdp-port 0 --direct 127.0.0.1:5910 --verbose` at `245b961`.
Client: `xfreerdp3` (FreeRDP 3.15.0, built with Opus) on the
same host, `/dynamic-resolution /size:1280x800`, first with
`/sound` and then, on a second connection, `/sound:format:1`.

- **Audio format:** with `/sound`, ryll logged "streaming Opus at
  48000 Hz, 2 channels": FreeRDP accepted the Opus format as
  offered. With `/sound:format:1` it logged PCM. The guest's
  stream was SPICE Opus (data mode 3), 48 kHz stereo.
- **Audio heard:** Mikal heard the guest's audio clean over Opus,
  from a test tone and a video in the guest's browser, and clean
  over PCM. On the PCM connection it got choppy while Mousepad
  was in the foreground over the video. IronRDP's log shows no
  stale wave dropped and the relay never lagged on either
  connection, so the gaps arose before the relay or after the
  wire; not diagnosed (*Future work*).
- **Audio under load:** 30 s of `tools/rdp-measure.sh` with the
  video playing in a window: Opus 173.5% average CPU (255% peak),
  1.36 Mbit/s; PCM 171.7% (262% peak), 2.78 Mbit/s. The
  difference, about 1.4 Mbit/s, is PCM's 1.5 Mbit/s against
  Opus's few tens of kbit/s. (Phase 2's 109 Mbit/s video row was
  a full-screen 853x480 region; this window was smaller.)
- **Clipboard, client to guest:** a single line, several lines
  and non-ASCII text all pasted correctly in a guest terminal and
  in Mousepad, line breaks intact.
- **Clipboard, guest to client:** a copy from Mousepad reached
  the host (16 bytes, offered and fetched within a millisecond).
  The first attempts did not: for those copies the guest's agent
  answered ryll's request with empty data (`size=8`, no text),
  so there was nothing to offer. That is between the guest's
  agent and the main channel, not RDP; whether the GUI sees the
  same is unchecked (*Future work*). After a host-to-guest paste
  the guest re-took the clipboard with the same text, which ryll
  offered back to the client; harmless, since the text is the
  client's own.
- **Clipboard on connect: not observable with FreeRDP.** With
  text on the host clipboard, FreeRDP's first Format List was
  empty, so there was nothing to fetch; it announces only later
  copies. The server side is covered by the loopback test.
- **After a resize:** clipboard both ways and audio still worked
  after a FreeRDP window resize.
- **Reconnect:** after FreeRDP was closed and started again, the
  second connection negotiated its audio and clipboard afresh,
  and both clipboard directions and audio worked.
- **mstsc: not tested** (none available).

### Cells for phase 6's RDP column

| Feature | RDP |
|---|---|
| Audio playback (PCM and Opus decoding) | available (Opus passed through when the client accepts it, decoded 48 kHz stereo PCM otherwise, about 1.5 Mbit/s; a SPICE stream of another shape is not played) |
| Audio volume/mute persisted across reconnect | n/a (the client's own volume control) |
| Clipboard sync with the guest (vdagent, CLIPBOARD selection, UTF-8 text) | available (text both ways through the RDP clipboard channel, with vdagent in the guest; line endings converted to CRLF for the client and LF for the guest) |

## Execution notes

- **Back brief, 2026-10-25 AEDT.** Mikal confirmed decisions 1
  and 7; the management session may start the desktop guest for
  step 4 and will ping Mikal to listen; this session may push
  and open the pull request once the definition of done passes.
- **Step 1** ran as planned (Opus, high). Eight mutants, all
  killed by the expected tests (script in the session
  scratchpad's `p04/step1/run-tests.sh`). Departures, kept: the
  crate also depends on `ironrdp-core` 0.2 (an edge, not a
  crate), because `CliprdrBackend` requires `AsAny` and nothing
  re-exports it; a session with no clipboard answers a data
  request with nothing rather than an error, since it never
  offers a Format List; the guest-copy task is spawned through
  `Handle::try_current` and logs rather than panicking without
  a runtime.
- **Step 2** ran as planned (Opus, high). Fourteen mutants, all
  killed (`p04/step2/run-tests.sh`). Departures, kept: the relay
  holds 32 items rather than 16, because each Opus packet is
  published twice (the packet and its decode), so it is still
  about a third of a second; an `ironrdp-svc` dev-dependency
  (an edge) lets the handler's tests drive IronRDP's real
  `RdpsndServer` through the client handshake; Opus packets
  from a mono stream are passed through, as a stereo Opus
  decoder takes them; items that do not belong to the chosen
  codec (Opus packets when PCM was chosen, and the reverse) are
  dropped silently, being normal operation.
- **Found in step 2's review: a stale wave can drop the next
  client.** IronRDP's server events carry no connection
  identity. A wave the forwarder queued just before a
  connection ended is normally discarded by the accept loop,
  but that loop `select!`s between draining events and
  accepting (`ironrdp-server` `server.rs:936-955`), so with a
  second client already waiting in the listen backlog it can
  accept first. The new connection then dispatches the stale
  wave before its audio channel has a format, which drops it;
  the client's retry succeeds. Not fixable from this crate (the
  receiver is IronRDP's), so recorded under *Future work*; the
  clipboard has the same exposure only for a paste request or
  data response sent in the last instant of a connection.
- **Step 3** ran as planned (Opus, high), including the optional
  second connection that accepts Opus. Three mutants, all killed
  (`p04/step3/run-tests.sh`); the new test passed 20 runs alone
  and 24 alongside the first test, eight at a time. It does not
  check the wave's format with the IronRDP client's
  `Rdpsnd::get_format`, which looks `format_no` up in the
  server's list while the server (correctly, per MS-RDPEA)
  numbers it by the client's; the test asserts the number and
  the bytes instead.
- **Step 4** ran as planned; see *Results*. The verbose server
  log reached 1 GB over the two and a half hours, which is worth
  knowing before running `--verbose` that long again.
- **Step 5** ran as planned (Sonnet, medium); the management
  session checked its FreeRDP option claims against `xfreerdp3
  /help` and the test run.
- **Definition of done, checked 2026-10-25 AEDT** at `4ef222b`:
  `make build`, `make test` (1665 passed, 0 failed), `make
  lint`, `make check-windows` (its warnings are in `session.rs`
  and `bugreport.rs`, which this phase does not touch) and
  `make package-check` exit 0, and `pre-commit run --all-files`
  passes. Both factories are registered in `server.rs` and
  `DiscardPlayback` is gone; `SessionHandles` has `clipboard`
  and `audio`, and `ryll/src/rdp.rs` makes the clipboard outside
  the control-socket branch (`:185`). Steps 1-3's 25 mutants are
  all killed by their expected tests; the management session
  re-ran "sound factory not registered" on a copy and the new
  loopback test failed as expected. The citation grep finds
  nothing, and no clipboard log line formats the text.
  *Results* covers every step 4 item, saying which could not be
  observed (clipboard on connect, with FreeRDP) or tested
  (mstsc).

## Future work

- Server-side switches for the clipboard and audio, if an
  operator asks for one (decision 1).
- Line-ending conversion for CRLF guests, in the renderer (#624).
- Guest copies the guest's agent answers with empty data, seen
  with `debian-xfce:13` for the first copies of the manual test;
  check whether the GUI sees the same, and file it if so.
- PCM audio choppy while another guest window is in front of the
  video, with no wave dropped by IronRDP or the relay; not
  diagnosed.
- Rich clipboard formats and file copy, and the microphone
  (master plan *Future work*).
- A wave or clipboard message queued as one connection ends can
  drop a client that was waiting in the backlog (see *Execution
  notes*). Worth raising with IronRDP: a channel message the
  channel is not ready for could be dropped rather than ending
  the connection, or server events could carry a connection
  identity. Phase 5's takeover rule makes a waiting client more
  common, so it should check this.

## Back brief

Before executing any step of this plan, back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with it. Gates that need Mikal's answer
before the steps that depend on them:

- Decision 1 (no server-side flags; the `None` is the
  library's opt-out) and decision 7 (Opus preferred over PCM);
  confirm before step 1.
- Mikal available to listen for step 4, and the desktop guest
  on SPICE port 5910 with vdagent and its audio device.
- Whether this session may push and open the pull request once
  the definition of done passes.
