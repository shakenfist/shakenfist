# RDP frontend phase 3: cursor and resize

Master plan: [PLAN-rdp-frontend.md](/components/ryll/plans/PLAN-rdp-frontend/).

**Planning effort:** high, as the master plan's guidance asks
for a phase that turns on protocol details (RDP pointer wire
formats, IronRDP's capability exchange and reactivation) and a
correctness question (a desktop size negotiated before the
display handler can serve it). Review at high effort: step 3
changes when the client is told which desktop size, and a
mismatch there drops the connection.

## Prompt

Read the master plan's *Prompt* first; everything there
applies. For this phase in particular:

- The RDP side is still IronRDP **0.13.0** (`ironrdp-server`
  0.13.0, `ironrdp-pdu` 0.9.0, `ironrdp-acceptor` 0.10.0,
  `ironrdp-displaycontrol` 0.8.0, `ironrdp-graphics` 0.9.0).
  `make fetch` puts their source under
  `.cargo-cache/registry/src/index.crates.io-*/` in the
  worktree, and every IronRDP file and line cited below is
  relative to the crate directory there. Read it; never build
  or run anything from inside it.
- The SPICE reference is spice-gtk at
  `/srv/src-reference/spice/spice-gtk` (commit `112f480`,
  2026-10-20). `src/channel-cursor.c` `set_cursor` (`:340-480`)
  and `src/spice-util.c` `spice_mono_edge_highlight` are the
  reference for every cursor format this phase decodes.
- Phase 5 (lifecycle and the `rdp-smoke` lane) is planned and
  run in parallel by another session. Both phases touch
  `shakenfist-spice-rdp/src/{lib,display,server}.rs` and the
  master plan's Execution table; whichever merges second
  rebases, and neither closes out the other.
- Builds run in Docker through the Makefile, never with a host
  toolchain.

## Scope

In scope:

- **Renderer:** decode SPICE `MONO`, `COLOR4` and `COLOR16`
  cursors, and honour the AND mask that follows `COLOR32`
  pixels, as spice-gtk does. Every mode benefits.
- **RDP cursor shapes:** the session's current cursor shape
  reaches the RDP client as `DisplayUpdate::RGBAPointer`,
  scaled down to fit 96x96 when larger; SPICE hide becomes
  `HidePointer`, and show resends the shape.
- **RDP resize:** the client's desktop size from its Client
  Core Data, and later display-control monitor layouts, become
  `DisplaySize::Requested` on the session's resize channel,
  aligned as the GUI aligns them. The guest's answer already
  becomes `DisplayUpdate::Resize` (phase 2).
- The loopback test extended to see a pointer update decoded by
  IronRDP's own client, and a Core Data size reach the resize
  channel.
- A manual FreeRDP test against the desktop guest, recorded in
  *Results*.
- Documentation of the user-visible changes.

Out of scope:

- `PointerPosition` (the master plan leaves it out on
  qemu-rdp's experience; the manual test records server mouse
  mode's behaviour so a later phase can decide).
- `LargePointer` (IronRDP 0.13.0 never sends it; see the
  survey) and the pointer cache (`CachedPointer`): every shape
  goes out with cache index 0.
- `COLOR8` cursors, which spice-gtk does not decode either.
- Multi-monitor layouts: only the primary monitor of a layout
  is used (master plan *Future work*).
- Headless cursor tracking (`docs/multi-mode-parity.md` marks
  it missing; unchanged).
- The two input items phase 2 deferred (*Future work* there).

## What the survey found

The master plan's phase 3 section was written before phase 2
ran. Checked against the tree and IronRDP 0.13.0 on
2026-10-22; corrections are made at the source (the master
plan's gap 6 and phase 3 section, and the `index.md` row) in
the planning commit, so later steps need not redo them.

1. **The renderer refuses more than gap 6 says.**
   `decode_cursor_pixels` (`shakenfist-spice-renderer/src/channels/cursor.rs:537-548`)
   decodes only `ALPHA`, `COLOR24` and `COLOR32`; `MONO`,
   `COLOR4`, `COLOR8` and `COLOR16` log
   `cursor: unsupported cursor type` and emit nothing, so every
   mode keeps whatever shape it had. Gap 6 names only mono and
   4-bit. The test `test_unsupported_cursor_types_return_none`
   asserts the refusal of all four and must change.
2. **`COLOR32` ignores its AND mask.** spice-gtk reads a 1-bit
   mask after the pixels for `COLOR32`, `COLOR16` and `COLOR4`
   (`channel-cursor.c:360-460`): a set bit makes the pixel
   transparent, or, where the colour is white, an inverting
   pixel that RGBA cannot express and spice-gtk approximates
   with a checker (`get_pix_hack`, `:329-332`). Ryll treats
   `COLOR32`'s fourth byte as padding and makes every pixel
   opaque (`cursor.rs:541`, `:572-578`), so a `COLOR32` cursor
   shows its transparent area as an opaque block. Not in the
   master plan; fixed here because it is the same mask code.
   spice-gtk indexes the colour formats' mask by pixel number
   with no per-row padding (`get_pix_mask`, `:324-327`), while
   the mono masks have a row stride of `(width + 7) / 8`
   (`mono_cursor`, `:308-322`); the two agree only for widths
   that are multiples of 8.
3. **No shared cursor state exists.** `SurfaceMirror` ignores
   cursor events (`surface_mirror.rs:269-272`); the GUI reads
   its own channel (`ryll/src/app/mod.rs:1770-1783`) and
   `--web` subscribes a relay to the event bus
   (`ryll/src/web/cursor.rs:70`, subscribed at
   `ryll/src/main.rs:754`). `ryll/src/rdp.rs` creates the bus
   (`:213`) and subscribes nothing for cursors, and
   `SessionHandles` (`shakenfist-spice-rdp/src/lib.rs:64-78`)
   has no cursor field. A decoded shape is
   `ChannelEvent::CursorShape(CursorImage)` with top-down,
   straight-alpha RGBA pixels (`channels/mod.rs:566-574`);
   hide and show are `ChannelEvent::CursorPosition { visible }`,
   sent on every move as well (`cursor.rs:253-278`).
4. **IronRDP's `RGBAPointer` is a 32 bpp TS_POINTERATTRIBUTE,
   not RGBA.** `rgba_pointer` (`ironrdp-server-0.13.0/src/encoder/mod.rs:185-205`)
   sends `data` unchanged as the XOR mask with `xor_bpp: 32`
   and an empty AND mask. IronRDP's own client decoder reads
   it as bottom-up rows (`ironrdp-graphics-0.9.0/src/pointer.rs:194`,
   `:220-224`) of B, G, R, A with straight alpha (`:401-404`).
   So the RDP adapter must flip rows and swap red and blue.
5. **An oversized pointer drops the client.** The only size
   check is the wire format's: each mask at most `u16::MAX`
   bytes with an even scanline length
   (`ironrdp-pdu-0.9.0/src/basic_output/pointer/mod.rs:70-109`),
   so 32 bpp tops out near 127x127. A larger one fails to
   encode, and the error aborts the client loop
   (`ironrdp-server-0.13.0/src/server.rs:1060`). The server
   does not advertise the Large Pointer capability set
   (`capabilities.rs:5-17`) and never sends `LargePointer`,
   so by MS-RDPBCGR the client expects at most 96x96. The
   master plan's "larger than 96x96 needs `LargePointer` or
   scaling" leaves only scaling on 0.13.0, and it is
   mandatory.
6. **`request_initial_size` does not see the client's size by
   default.** It is called from the capability loop only when
   the client's Bitmap capability set has `desktop_resize_flag`
   (`server.rs:1407-1417`), with the size from the client's
   Confirm Active, which echoes the server's own Demand Active
   size (MS-RDPBCGR 2.2.1.13.2; `builder.rs:239-243`). The
   size the client wants is only in its Client Core Data, which
   the acceptor reads when the builder sets
   `with_honor_client_desktop_size(true)` (`builder.rs:236-263`;
   `ironrdp-acceptor-0.10.0/src/connection.rs:516-541`, range
   200..=8192). The master plan's "the client's initial
   desktop size via `request_initial_size`" needs that flag.
   The encoder is still built from `size()` (`server.rs:1482`),
   so the display handler must reconcile the two.
7. **`request_initial_size` runs again on every
   reactivation** (`server.rs:1739-1749` rebuilds the acceptor
   and reruns `client_accepted`), with the client's echo of the
   size it was last told. Forwarding that to the guest would
   replace the size the client asked for with the guest's old
   size.
8. **Display control needs no opt-in.** The DVC is attached in
   `attach_channels` (`server.rs:683-688`) with caps of one
   monitor; `request_layout` receives a
   `DisplayControlMonitorLayout` (`ironrdp-displaycontrol-0.8.0/src/pdu/mod.rs:213`)
   whose entries have `is_primary()` and `dimensions()`. A
   layout outside 200..=8192 or with an odd width fails to
   decode and never arrives (`:367-379`). It is called on
   `spawn_blocking` with the display mutex held
   (`server.rs:352-356`), so it must not await.
9. **Phase 2's adapter already reconciles one mismatch.**
   `ClientSize` (`shakenfist-spice-rdp/src/display.rs:44-91`)
   records the size the client was told; `MirrorUpdates`
   starts from it and makes its first update a resize when the
   surface differs (`display.rs:225-234`). Honouring the Core
   Data size is the same situation, provided `ClientSize`
   records the negotiated size rather than the one `size()`
   reported before the handshake.
10. **Resize alignment differs by mode.** The GUI and the
    control socket round down to a multiple of 8, minimum 8
    (`align_monitor_dimension`,
    `shakenfist-spice-renderer/src/channels/main_channel.rs:135-137`);
    `--web` rounds to even (`ryll/src/web/inputs.rs:238-258`).
    The main channel debounces requests for 200 ms and holds one
    made before the agent connects (`main_channel.rs:1360-1460`).
11. **The loopback test turns pointers off**
    (`enable_server_pointer: false`, `tests/loopback.rs:206-209`)
    and sets a client desktop size (`:173`) that the server
    currently ignores.

The master plan's other claims for this phase hold: hide maps
to `HidePointer`, layouts arrive through `request_layout`, and a
guest-driven size change is already `DisplayUpdate::Resize`.

## Decisions

1. **Decode as spice-gtk does, inverting pixels included.**
   `MONO` through `spice_mono_edge_highlight`; `COLOR4` with
   its 16-entry palette; `COLOR16` as 15-bit RGB; `COLOR32` with
   its mask. Where the mask marks a white pixel as inverting,
   use spice-gtk's checker approximation, since `CursorImage`
   is RGBA and no frontend can draw XOR. Matching spice-gtk
   pixel for pixel means a cursor looks the same in ryll as in
   remote-viewer, which is the comparison users make. `COLOR8`
   stays refused. The mask row stride follows spice-gtk
   exactly (no padding for colour formats); the step's tests
   use widths that are and are not multiples of 8 so the
   choice is pinned.
2. **Cursor state reaches the RDP crate as a watch channel on
   `SessionHandles`.** A new `cursor: watch::Receiver<CursorState>`
   field, where `CursorState` holds the latest shape (if any)
   and whether it is visible. A relay in `shakenfist-spice-rdp`
   (`pub fn spawn_cursor_relay(broadcast::Receiver<ChannelEvent>)`)
   turns the event bus into it, changing the watch only when
   the shape or visibility changes, not on every move. It lives
   in the RDP crate rather than in `ryll` so that Kerbside,
   which builds its own `SessionHandles`, gets it too. A watch
   coalesces: a client that is behind gets the latest shape,
   never a backlog. A lagged broadcast receiver logs once and
   carries on; the next shape corrects it.
3. **Pointer updates ride the display update stream.** IronRDP
   has no other path. `MirrorUpdates` checks the watch on each
   poll and queues the pointer update ahead of that poll's
   bitmaps. A new stream (every connection and every
   reactivation) sends the current shape first, or
   `HidePointer` if hidden, because a reactivated client starts
   from its default pointer.
4. **Scale large cursors down to fit 96x96, keeping the aspect
   ratio**, nearest-neighbour, with the hotspot scaled the same
   way and clamped inside the image. 96x96 rather than the
   wire format's ~127x127 because the server does not
   advertise Large Pointer support, so a conforming client may
   refuse anything bigger. Rows are flipped to bottom-up and
   red and blue swapped; straight alpha is kept; the scanline
   (`width * 4`) is always even. Cache index 0 every time.
5. **Honour the client's Core Data size** with
   `with_honor_client_desktop_size(true)`, and make
   `MirrorDisplay::request_initial_size` do three things: record
   the negotiated size in `ClientSize` (so the stream's first
   update is a resize to the surface's size whenever they
   differ, which is phase 2's existing path), send
   `DisplaySize::Requested` for it, aligned, on the session's
   resize channel, and return it. **This is the decision most
   likely to be argued with.** Without an agent in the guest it
   costs one extra deactivation-reactivation per connection
   (client size, then the guest's size) compared with leaving
   the flag off. The alternative, never learning the client's
   size until it sends a display-control layout, means a
   fullscreen client gets the guest's size and keeps it unless
   the client happens to send a layout, which mstsc and FreeRDP
   do not promise to do at connect. A reactivation at connect
   is cheap; a session stuck at the wrong size is the visible
   failure resize exists to fix.
6. **Only the first `request_initial_size` of a connection asks
   the guest.** Later calls are reactivation echoes (survey item
   7) and only update `ClientSize`. The first call is told apart
   by a per-connection flag reset where `ClientSize` is
   forgotten (on accept, `server.rs:510`).
7. **Layouts use the primary monitor**, or the first entry when
   none is flagged primary (decoding does not insist on one),
   aligned the same way, sent with `try_send` because the
   callback cannot await. A full resize channel drops the
   request and logs once; the next layout supersedes it anyway.
8. **Align as the GUI does**: `align_monitor_dimension`, a
   multiple of 8, which also satisfies RDP's even-width rule.
   The guest may then be up to 7 pixels narrower or shorter
   than the client window; the client shows the server's size,
   which is what it does today.
9. **A client without `desktop_resize_flag` is not handled
   specially.** IronRDP's acceptor adopts the Core Data size
   whatever the flag says, but then never calls
   `request_initial_size`, so `ClientSize` keeps the guest's
   size and the stream sends nothing to correct the client.
   Such a client cannot follow a resize anyway; every client in
   the master plan's list sets the flag. Recorded under *Risks*.
10. **Hold a connection's first resize for two seconds** (added
    after step 5; Mikal's choice). A reactivation during the
    dynamic-channel handshake strands those channels for the
    whole connection, and IronRDP gives no signal that the
    handshake has finished. So for `FIRST_RESIZE_GRACE` after
    accept the update stream holds a resize for a size mismatch,
    and the bitmaps that would not fit, while still sending
    pointer updates; if the guest adopts the size the client was
    told meanwhile, no resize is needed at all. The rejected
    alternative was serving the guest clipped into the client's
    size with no initial resize: more invasive, and a client
    without an agent would keep a wrong-size desktop.

## Step plan

Steps run in order, in the phase worktree, because each runs
`make test` in the same tree. Each step is one commit, with the
subject given.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | none | Renderer: decode `MONO`, `COLOR4`, `COLOR16` and the `COLOR32` mask. Commit: "Decode mono, 4-bit and 16-bit SPICE cursors." See brief 1. |
| 2 | high | opus | none | RDP cursor shapes: `CursorState`, the relay, `SessionHandles.cursor`, pointer updates in `MirrorUpdates`, wiring in `ryll/src/rdp.rs`. Commit: "Send the guest's cursor shape to RDP clients." See brief 2. |
| 3 | high | opus | none | RDP resize: honour the Core Data size, `request_initial_size`, `request_layout`. Commit: "Ask the guest for the RDP client's desktop size." See brief 3. |
| 4 | high | opus | none | Loopback test: a pointer update decoded by IronRDP's client, and a Core Data size reaching the resize channel. Needs steps 2 and 3. Commit: "Prove RDP pointers and resize with the loopback client." See brief 4. |
| 5 | - | - | - | Manual FreeRDP test against the desktop guest, run by the management session with Mikal, recorded in *Results*. Needs steps 1-3. No commit of its own; the results go in with step 6. |
| 6 | medium | sonnet | none | Documentation and the master plan. Needs step 5's results. Commit: "Document RDP cursors and resize." See brief 6. |

### Brief 1: renderer cursor formats

Files: `shakenfist-spice-renderer/src/channels/cursor.rs`
(`decode_cursor_pixels`, `:527-588`, and its tests, from
`:589`).

- Port spice-gtk's `set_cursor` (`/srv/src-reference/spice/spice-gtk/src/channel-cursor.c:340-480`)
  for `MONO`, `COLOR4`, `COLOR16`, and the mask part of
  `COLOR32`, plus `mono_cursor` (`:308-322`), `get_pix_mask`
  (`:324-327`), `get_pix_hack` (`:329-332`) and
  `spice_mono_edge_highlight` (`src/spice-util.c`). Read them
  in full; do not reconstruct them from this summary. Note
  spice-gtk works in native-endian `guint32` (`0xAARRGGBB`
  read as BGRA bytes on little-endian, then swapped to RGBA at
  `:472-477`); ryll's output is RGBA bytes, so get the channel
  order right and test it with asymmetric colours.
- Keep ryll's safety rules: size buffers with
  `limits::rgba_len`, and refuse a message shorter than the
  format needs (pixels plus mask, plus the palette for
  `COLOR4`) before allocating, as `:554-568` does for the
  current formats. Compute each format's needed length with
  checked arithmetic.
- `COLOR24` is not in spice-gtk; leave its decoding as it is.
  `COLOR8` stays refused with the existing warning.
- Tests: one per new format with a small hand-built image
  (asymmetric colours, a transparent pixel, an inverting
  pixel), for a width that is a multiple of 8 and one that is
  not; a short message per format; `COLOR32` with its mask
  making a pixel transparent. Replace
  `test_unsupported_cursor_types_return_none` with a test that
  `COLOR8` and an unknown type are refused.
- Mutation-check in a copy (restore from a copy, never with
  `git checkout`), under `timeout`: dropping the mask, the
  palette lookup's nibble order, the edge highlight, the
  `COLOR16` channel shifts. Report the script path.
- `make test`, `make lint`, `pre-commit run --all-files`.

### Brief 2: RDP cursor shapes

Files: `shakenfist-spice-rdp/src/lib.rs` (`SessionHandles`,
`:64-78`), a new `shakenfist-spice-rdp/src/cursor.rs`,
`shakenfist-spice-rdp/src/display.rs` (`MirrorUpdates`,
`:171-300`), `ryll/src/rdp.rs` (the event bus at `:213`, the
`SessionHandles` built at `:237`), and every test that builds a
`SessionHandles` (`auth.rs`, `input.rs`, `server.rs` tests,
`tests/loopback.rs`).

- `CursorState { shape: Option<Arc<CursorImage>>, visible: bool }`
  (public, `Clone`, `Default` = no shape, visible).
  `SessionHandles` gains `pub cursor: watch::Receiver<CursorState>`.
- `pub fn spawn_cursor_relay(events: broadcast::Receiver<ChannelEvent>) -> (watch::Receiver<CursorState>, JoinHandle<()>)`
  in `cursor.rs`: `CursorShape` replaces the shape;
  `CursorPosition { visible, .. }` changes only visibility, via
  `send_if_modified` so a move that changes nothing wakes no
  one. `RecvError::Lagged` logs once with `warn_once!` and
  continues; `Closed` ends the task. In `ryll/src/rdp.rs`
  subscribe before the session is spawned, as the comment at
  `:210-212` requires, and abort the task on shutdown as the
  other bus tasks are.
- In `MirrorUpdates`, keep a clone of the session's watch
  receiver (taken from the slot's session, re-taken if the
  session changes). A new stream marks the watch as changed so
  its first poll sends the current state. On a change, queue
  before the poll's bitmaps: `HidePointer` when not visible or
  no shape has arrived (send `DefaultPointer` for "no shape,
  visible" instead), otherwise `RGBAPointer`. Keep
  `next_update`'s cancel-safety rule (the comment at
  `display.rs:256-262`): read the watch where nothing awaited
  can lose it.
- The conversion is a pure function, tested on its own:
  RGBA top-down to BGRA bottom-up, straight alpha; images
  larger than 96 in either dimension scaled down to fit 96x96
  keeping the aspect ratio (nearest-neighbour), hotspot scaled
  and clamped to `< width`, `< height`; a zero-sized image
  yields no update. Survey items 4 and 5 give the wire facts;
  re-read `encoder/mod.rs:185-205` and
  `ironrdp-graphics-0.9.0/src/pointer.rs:190-260` before
  relying on them.
- Tests: the conversion (a 2x2 with four distinct colours
  checks both the flip and the swap; a 200x100 scales to 96x48
  with its hotspot scaled; 96x96 passes unscaled); the relay
  (shape, hide, show, a move with no change sends nothing);
  the stream (first poll sends the current shape before the
  whole-surface bitmap; a change mid-stream queues one pointer
  update; hidden sends `HidePointer`).
- Mutation-check: no row flip, no channel swap, no scaling,
  the relay waking on every move. Same rules as brief 1.
- `make test`, `make lint`, `pre-commit run --all-files`.

### Brief 3: RDP resize

Files: `shakenfist-spice-rdp/src/display.rs` (`MirrorDisplay`,
`ClientSize`), `shakenfist-spice-rdp/src/server.rs` (the
builder at `:435-470`, `ConnectionLog::on_accept` at `:500-511`).

- `with_honor_client_desktop_size(true)` on the builder, in
  both security modes. Read its precondition
  (`ironrdp-server-0.13.0/src/builder.rs:236-263`) and survey
  items 6, 7 and 9 before starting.
- `MirrorDisplay::request_initial_size(client_size)`: always
  `ClientSize::set(client_size)` (it is the size the client was
  just told, overriding what `size()` recorded before the
  handshake). On the first call of a connection only, send
  `DisplaySize::Requested(w, h)` with both sides passed through
  `shakenfist_spice_renderer::channels::main_channel::align_monitor_dimension`
  (public, `main_channel.rs:135-137`) on the slot's session's
  `resize_tx` with `try_send`. Return `client_size`. "First
  call of a connection" is a flag beside `ClientSize`, reset
  by the same `forget()` the connection handler already calls
  on accept, so the two cannot drift apart.
- `request_layout(layout)`: the primary monitor's
  `dimensions()`, or the first entry's when none is primary;
  aligned; `try_send` on the slot's session's `resize_tx`; a
  full or closed channel or an empty slot logs once
  (`warn_once!`) and drops it. It runs on a blocking thread
  with the display mutex held: no `.await`, no
  `blocking_lock` on the surface mirror.
- Do not change `MirrorUpdates`' resize logic: with
  `ClientSize` holding the negotiated size, its existing
  first-poll comparison sends the resize to the surface's size.
  Add a comment where `request_initial_size` sets it saying so.
- Tests (unit, in `display.rs`): the first call sends one
  aligned `Requested` and records the size; a second call of
  the same connection records but sends nothing; after
  `forget()` the next call sends again; a stream built after
  `request_initial_size(1280x720)` with a 64x48 surface starts
  with `Resize(64x48)` and no bitmap before it; a layout with a
  non-primary first entry uses the primary; a layout with no
  primary uses the first entry; an empty slot drops a layout
  without panicking.
- Mutation-check: sending on every call, not recording the
  size, ignoring the primary flag, skipping alignment.
- `make test`, `make lint`, `pre-commit run --all-files`.

### Brief 4: loopback test

File: `shakenfist-spice-rdp/tests/loopback.rs`. Read the whole
file first; the test at `:445` is the pattern.

- Turn on `enable_server_pointer` in the client config
  (`:206-209`) and update its comment. Before connecting, put a
  known shape in the session's cursor watch (an asymmetric
  4x4); assert the client receives a pointer update and that
  IronRDP's client decodes it to the same pixels (the client's
  pointer decoding is in `ironrdp-session` /
  `ironrdp-graphics::pointer`; find which update the connector
  exposes and assert on decoded RGBA, so the test proves the
  flip and swap end to end rather than restating the encoder).
- Set the client's desktop size (`:173`) to one that differs
  from the surface (e.g. 800x600 against the 64x48 test
  surface) and assert that `DisplaySize::Requested(800, 600)`
  arrives on the session's resize receiver, and that the
  session is still served at the surface's size after the
  reactivation (the existing reactivation assertion).
- If the connector cannot expose pointer updates without a
  large harness change, stop and report rather than building
  one; the management session decides.
- Mutation-check: the stream sending no pointer, the honour
  flag off. Same rules as brief 1.
- `make test`, `make lint`, `pre-commit run --all-files`.

### Brief 6: documentation and the master plan

Files: `docs/multi-mode-parity.md` (the RDP row of the mode
table, `:26`), `docs/web-frontend.md` and
`docs/spice-protocol.md` only where they state which cursor
formats are decoded or how resize requests are made,
`docs/configuration.md` only if a flag changed (none is
planned), `docs/plans/PLAN-rdp-frontend.md` (the phase 3
section's acceptance and any open question this phase
answers), and this plan's *Results*.

- The mode table's RDP row says display and input only; make
  it name cursor shapes and resize too. The per-feature RDP
  column is phase 6's (it lands with the operator guide), so
  do not add it here; record the cells it will need (cursor
  shape scaled to fit 96x96, position not sent, hide and show,
  resize from the client's initial size and display-control
  layouts with vdagent in the guest) in this plan's *Results*
  for phase 6 to copy.
- The mono, 4-bit, 16-bit and masked 32-bit cursor decoding
  is user-visible in every mode; say so where cursor support is
  described.
- No phase or decision citations in code or docs outside
  `docs/plans`; no dependency versions in `docs/`.
- `pre-commit run --all-files`.

## Risks and mitigations

- **The negotiated size and the encoder disagree and the
  client drops.** Honouring Core Data makes the client's
  desktop the client's size while the encoder is built from
  the surface's. Mitigation: `ClientSize` records the
  negotiated size so the first update is the resize (brief 3's
  "no bitmap before the resize" test), the loopback test runs
  the whole sequence with IronRDP's client (brief 4), and the
  management session runs FreeRDP at a size different from the
  guest's (step 5).
- **A client without `desktop_resize_flag`** is told its own
  size and sent the surface's bitmaps (decision 9). Mitigation:
  none in code; the manual test notes the flag FreeRDP sends,
  and if a tested client lacks it, step 3 is revisited before
  the PR.
- **Pointer bytes in the wrong order** show a garbled or
  upside-down cursor that unit tests restating the encoder
  would not catch. Mitigation: brief 4 asserts on IronRDP's
  client-side decode, and step 5 checks the cursor visually.
- **Mono cursors are untested against a real guest.** The
  desktop guest (XFCE) sends alpha cursors. Mitigation: unit
  tests against spice-gtk's algorithm; step 5 records which
  formats the guest sent (the renderer's debug log), and a
  Windows guest test is recorded as not done if none is
  available.
- **Reactivation drops the display-control channel** and later
  layouts never arrive. Mitigation: step 5 resizes the FreeRDP
  window after the first reactivation and checks the guest
  follows.
- **Conflicts with phase 5**, which edits the same files.
  Mitigation: whichever merges second rebases; the conflict is
  in code both sessions understand, and neither phase closes
  out the other.

## Definition of done

- [x] `grep -n 'COLOR8\|unsupported cursor type' shakenfist-spice-renderer/src/channels/cursor.rs`
      shows `COLOR8` as the only named refused format, and
      cursor tests for `MONO`, `COLOR4`, `COLOR16` and masked
      `COLOR32` pass under `make test`.
- [x] `SessionHandles` has a `cursor` field, `ryll/src/rdp.rs`
      subscribes a cursor relay before spawning the session,
      and `grep -n 'RGBAPointer\|HidePointer' shakenfist-spice-rdp/src/display.rs`
      finds both.
- [x] `grep -n 'with_honor_client_desktop_size(true)' shakenfist-spice-rdp/src/server.rs`
      finds it, and `MirrorDisplay` implements
      `request_initial_size` and `request_layout`.
- [x] The loopback test enables server pointers, asserts a
      decoded pointer and a `Requested` size, and fails when
      the stream's pointer update or the honour flag is
      removed (mutation, management session).
- [x] Every mutation listed in briefs 1-4 is killed; the
      scripts' paths and counts are in *Execution notes*.
- [x] `make build`, `make test`, `make lint`, `make
      check-windows`, `make package-check` and `pre-commit run
      --all-files` pass.
- [ ] *Results* records, with FreeRDP's version and the date:
      the cursor changing shape over a text field and a window
      edge, hiding where the guest hides it, FreeRDP started at
      a size other than the guest's and the guest adopting it,
      a window resize after that adopting too, the
      `desktop_resize_flag` FreeRDP sent, the cursor formats the
      guest sent, and server mouse mode's cursor behaviour.
      *Closed out unmet on 2026-10-25, by Mikal's decision: the
      guest adopting a requested size and server mouse mode's
      cursor were not observed (see Execution notes); both are
      under Future work.*
- [x] `docs/multi-mode-parity.md`'s RDP row (`:26`) names
      cursor shapes and resize, and *Results* lists the RDP
      cursor and resize cells phase 6's column will need.
- [x] `git grep -n -i 'phase 3\|decision [0-9]\|survey item' -- shakenfist-spice-rdp shakenfist-spice-renderer/src ryll/src docs ':!docs/plans'`
      finds nothing.

## Results

### Manual test: FreeRDP against the desktop guest

2026-10-23, 07:40 to 12:52 AEDT, on the Kasm host, with Mikal
watching and driving the pointer. Guest: `debian-xfce:13` under
`tools/start-desktop-qemu.sh` (QXL, vdagent) on SPICE port
5910, in client mouse mode, at 1024x768. Server: a debug build of
`ryll --rdp --rdp-port 0 --direct 127.0.0.1:5910`, with
`--verbose` for the later runs. Client: `xfreerdp3` (FreeRDP
3.15.0) on the same host, `/cert:tofu /dynamic-resolution`,
started at 1600x900, 1440x900, 1280x960 and 1152x864 on
successive connections.

- **Cursor shapes:** correct. Over the desktop, a terminal and a
  window edge the pointer took the guest's arrow, I-beam and
  resize shapes, the right way up and in the right colours. The
  guest sent only `ALPHA` cursors (type 0), all 64x64, so none
  was scaled; the mono, 4-bit and 16-bit decoders and the
  scaling are covered by unit tests only, and no Windows guest
  was available.
- **Initial size:** FreeRDP sets `desktop_resize_flag`. Each
  client was told the size it asked for, the guest was asked for
  it (aligned to 8, so 1600x900 became 1600x896), and the client
  was then resized to the guest's 1024x768: FreeRDP's window
  shrank to match.
- **The guest never changed resolution**, for ryll's requests or
  for remote-viewer's "resize guest to window": this guest image
  does not apply client-driven monitors configs, so the guest
  adopting the requested size could not be observed end to end.
  The loopback test covers the RDP side.
- **Defect found and fixed:** the first runs showed every
  dynamic virtual channel (FreeRDP's Advanced Input,
  DisplayControl, ECHO) left pending for the whole connection,
  so window drags sent nothing. The server's log showed the
  client's dynamic-channel replies "received and lost" during
  the reactivation that the immediate first resize caused.
  IronRDP starts those channels only on the first activation and
  drops client PDUs while reactivating. Fixed by decision 10;
  after it the channels open, the resize follows two seconds
  after accept, and no PDU is lost.
- **Layout echo found and fixed:** FreeRDP reports its window's
  new size as a layout after every server resize, which passed
  the guest's own old size back as a request. Layouts of the
  size the client already has are now ignored (`139dc98`). After
  both fixes, Mikal's window drag reached the guest as a
  1488x968 monitors config.
- **Middle click** worked through FreeRDP's Advanced Input
  channel once that channel opened.
- **Server mouse mode: not tested** this phase.
- **mstsc and a Windows guest: not tested** (none available).

### Cells for phase 6's RDP column

For `docs/multi-mode-parity.md`'s per-feature RDP column, which
lands with the operator guide:

| Feature | RDP |
|---|---|
| Server cursor rendering (SET/INIT) | available (the shape is sent as an RDP pointer; larger than 96x96 is scaled down to fit) |
| Cursor position tracking | n/a (the client draws its own pointer where its mouse is; the guest's position is not sent) |
| Cursor hide/show | available |
| Default arrow fallback cursor | available (the client's own default pointer until the guest sends a shape) |
| Guest resolution follows viewport | available (the client's initial size and its display-control layouts, with vdagent in the guest; a connection's first resize waits up to 2 s) |

## Execution notes

- **Back brief, 2026-10-23 AEDT.** Mikal confirmed decision 5
  (honour the client's Core Data size); the management session
  may start the desktop guest with `make test-qemu-desktop` for
  step 5 and stop it afterwards; neither a Windows guest nor
  mstsc is available, so those *Results* cells read "not
  tested"; this session may push and open the pull request once
  the definition of done passes.
- **Steps 1-3** ran in order as planned, each an Opus sub-agent
  at high effort, reviewed and committed by the management
  session. Mutation scripts, run on copies under `timeout`:
  step 1, 23 mutants, all killed; step 2, 12, all killed; step
  3, 9, all killed (scripts under the session scratchpad's
  `p03/step1-3/mutate.py`). The cargo helper the scripts used
  ran cargo as the container's PID 1, which ignores `timeout`'s
  TERM, so one step 2 run hung until stopped by hand; the helper
  now passes `--init`.
- **Step 3 departure:** `ironrdp-server` does not re-export
  `DisplayControlMonitorLayout`, so `shakenfist-spice-rdp` now
  depends on `ironrdp-displaycontrol` 0.8 directly; it was
  already in `Cargo.lock` through `ironrdp-server`, so the lock
  gains an edge, not a crate.
- **Step 3 edge case, accepted:** a client whose Core Data size
  is outside RDP's 200..=8192 range is told the size reported
  before the handshake (in TLS mode the fallback, since the slot
  is empty then), and that echo is taken for the size it asked
  for, so the guest is asked for it. Rare, and the client's next
  display-control layout supersedes it.
- **Step 4** ran as planned (Opus, high); five mutants, all
  killed. The loopback client decodes pointers itself, so no
  harness change was needed.
- **Step 5, 2026-10-23 AEDT**, found two defects, fixed before
  step 6 as step 3b (`fca07c7`, Opus, high: decision 10, seven
  mutants, all killed) and a management-session fix for
  FreeRDP's layout echo (`139dc98`, one mutant, killed). See
  *Results*. Mikal chose the hold over serving the guest at the
  client's size or deferring the fix. An issue for IronRDP
  describing the dropped dynamic-channel replies was drafted for
  Mikal to file.
- **Step 5 interruption:** the desktop guest was stopped part
  way through by another session, most likely through the
  `/tmp/ryll-test-qemu.pid` file every checkout's
  `make test-qemu-*` targets share. It was restarted with
  `QEMU_PID_FILE=/tmp/ryll-p03-qemu.pid` and
  `QEMU_QMP_SOCKET=/tmp/ryll-p03-qmp.sock`, and stopped by its
  own PID at the end, since `make test-qemu-stop` also removes
  a shared OVMF vars copy.
- **Rebased** onto `origin/develop` (12 commits ahead, including
  #608-#610) before pushing; the one conflict, both sides adding
  a row to `docs/plans/index.md`, was resolved by keeping both.
- **Definition of done, checked 2026-10-23 13:04 AEDT** on the
  rebased tree: `make build`, `make check-windows` and `make
  package-check` exit 0 (the Windows check's four warnings are in
  `session.rs` and `bugreport.rs`, which this phase does not
  touch); `make test` 1630 passed, 0 failed; `make lint` and
  `pre-commit run --all-files` pass. `COLOR8` is the only named
  refused cursor format; `SessionHandles.cursor` exists and
  `ryll/src/rdp.rs` subscribes the relay (`:219`) before spawning
  the session (`:295`); `display.rs` sends `RGBAPointer` and
  `HidePointer`; the server sets
  `with_honor_client_desktop_size(true)` and `MirrorDisplay`
  implements both resize hooks; the loopback test enables server
  pointers and its mutants are killed; every step's mutants are
  killed (steps 1, 2, 3, 3b and 4: 23, 12, 9, 7 and 5, plus the
  layout echo's one); the parity row names cursor and resize and
  *Results* carries phase 6's cells; the citation grep finds
  nothing. **Not met:** the *Results* item, which asks for the
  guest adopting the requested size and for server mouse mode's
  cursor behaviour. The guest image ignores client resizes from
  any client, and server mouse mode was not run; both are under
  *Future work*.

## Future work

- `PointerPosition` in server mouse mode, if step 5 shows the
  client's pointer drifting from the guest's.
- The pointer cache (`CachedPointer`) for guests that switch
  between a few shapes often.
- `LargePointer` once an IronRDP release sends it, so HiDPI
  cursors need not be scaled.
- Drop the first-resize hold once IronRDP keeps the client's
  dynamic-channel replies across a reactivation (or restarts
  unfinished channels); the hold costs a client of another size
  up to two seconds of blank desktop at connect.
- A desktop guest image that applies client-driven monitors
  configs, so resize can be tested end to end: `debian-xfce:13`
  ignores them from ryll and from remote-viewer alike.
- Per-checkout PID and QMP paths for the Makefile's
  `test-qemu-*` targets, so concurrent sessions cannot stop each
  other's guests.
- Server mouse mode's cursor behaviour, untested this phase.

## Back brief

Before executing any step of this plan, back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with it. Gates that need Mikal's answer
before the steps that depend on them:

- Decision 5 (honouring the client's Core Data size, at the
  cost of one reactivation per connection without an agent);
  confirm before step 3.
- The desktop guest on SPICE port 5910, with vdagent running,
  for step 5; FreeRDP 3 (`xfreerdp3`, `sdl-freerdp3`) is
  installed on this host.
- Whether a Windows guest (for mono cursors) or a Windows
  machine with mstsc is available for step 5; if not, those
  cells in *Results* say "not tested".
- Whether this session may push and open the pull request at
  the end.
