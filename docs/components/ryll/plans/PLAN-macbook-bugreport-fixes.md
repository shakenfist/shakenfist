# Macbook bug-report fixes (2026-04-23)

## Situation

Four artefacts were captured on macOS (ryll 0.1.4, aarch64) on
2026-04-23 against a `sf-3:46133` SPICE session in which the
guest rebooted several times (each display pcap shows 5–9
`SURFACE_DESTROY`/`SURFACE_CREATE` pairs):

- `ryll-bugreport-2026-04-23T08-21-29Z.zip` — Display / "Static"
- `ryll-bugreport-2026-04-23T08-22-24Z.zip` — Input / "Clicks are not working"
- `ryll-bugreport-2026-04-23T08-22-47Z.zip` — Display / "Static"
- `ryll-pedantic-main-hexdump-106-2026-04-23T08-22-37Z.zip` — pedantic gap

This plan covers the two concrete bugs the artefacts pin down
(clicks-not-working-after-VM-reboot, and the pedantic gap). The
"static" bug needs a separate capture flow before we can
meaningfully debug it — see `PLAN-bugreport-trigger-snapshot.md`.

## Findings

### 1. `main:hexdump:106` — unhandled MULTI_MEDIA_TIME

Decoding the main-channel pcap inside the pedantic zip:

    rel=153.664s  type=106  size=4  payload=fd4994ce

This is `SPICE_MSG_MAIN_MULTI_MEDIA_TIME` — a 4-byte u32 the
server sends so the client can align audio/video timestamps.
Ryll has no handler for it, so `log_unknown_once` fires and
`--pedantic` mode materialises a bug report. The payload
itself is well-formed; this is purely a missing handler.

### 2. `MOUSE_MODE` misparse → clicks dead after VM reboot

The Input bug report's `channel-state.json` shows MouseMove,
MouseDown, and MouseUp events all being emitted
(`bytes_out=25169`). The problem is upstream in the main
channel.

Main-channel pcap for the pedantic report shows three
`MOUSE_MODE` messages across the session:

    rel=0.003s    MOUSE_MODE (4 bytes): 03 00 02 00
    rel=62.728s   MOUSE_MODE (4 bytes): 01 00 01 00
    rel=67.587s   MOUSE_MODE (4 bytes): 03 00 01 00

The SPICE wire format for `SpiceMsgMainMouseMode` is two
`uint16`s (`supported_modes`, `current_mode`) — see
`/srv/src-reference/spice/spice-protocol/spice/messages.proto`.
Correctly decoded:

- `rel=0.003s` — supported=3, current=2 (CLIENT). Initial negotiation.
- `rel=62.728s` — supported=1, current=1 (SERVER). Guest just rebooted; agent gone.
- `rel=67.587s` — supported=3, current=1 (SERVER). Agent back but server still in SERVER mode.

`ryll/src/channels/main_channel.rs:343-357` reads the whole
4-byte payload as a single little-endian `u32`, producing
`0x00020003 = 131075`, `0x00010001 = 65537`, and
`0x00010003 = 65539` respectively. The display bug reports'
`session.json` confirms this — `"mouse_mode": 131075`. None of
those match `MOUSE_MODE_SERVER (1)` or `MOUSE_MODE_CLIENT (2)`,
so the GUI check at `ryll/src/app.rs:1977` always falls
through to the "client / absolute" branch.

Two things then go wrong after a guest reboot:

1. The server sits in SERVER/relative mode, but ryll keeps
   sending `MOUSE_POSITION` (absolute) because the mode check
   decides wrong. The server ignores the coordinates.
2. Even if the parse were fixed, the `MOUSE_MODE` handler
   (`main_channel.rs:343-357`) never re-sends
   `MOUSE_MODE_REQUEST(CLIENT)` — that logic only runs in the
   `INIT` handler. So the server can't be nudged back to
   CLIENT mode after the guest reboot.

Result: the client sends absolute coords + `MOUSE_PRESS` into
the void, and clicks land wherever the guest's internal
cursor happens to be.

### 3. Static (out of scope here)

Two display reports, 78s apart, same session:

- `08-21-29Z` — `screenshot.png` is mostly black with a tiny
  white mark top-left. The pcap shows 6 `SURFACE_CREATE` / 5
  `SURFACE_DESTROY`; last create rel=73.595s followed by
  ~5800 `DRAW_COPY`s up to rel=82.774s. Bug captured at
  uptime 86.4s.
- `08-22-47Z` — `screenshot.png` shows clean kernel-boot
  console text ("Hostname set to (ryll)"). The surface
  buffer is correct; whatever "static" the user saw is
  either short-lived and gone by submit time, or happening
  downstream of the surface buffer (texture upload, wgpu
  present, macOS compositor).

The operator has pointed out that the screenshot is taken at
submit time, not at the moment the user hit F12, so a
transient artefact can be gone before the capture happens.
Fixing that is a prerequisite to debugging the static
properly and is covered by
`PLAN-bugreport-trigger-snapshot.md`.

## Fixes

### Fix 1 — Parse `MOUSE_MODE` as two u16s

`ryll/src/channels/main_channel.rs`, `MOUSE_MODE` arm (≈
lines 343-357):

- Read `supported_modes: u16` followed by `current_mode: u16`
  from the 4-byte payload (require `payload.len() >= 4`).
- Dispatch `ChannelEvent::MouseMode(current_mode as u32)` so
  the downstream GUI check against `MOUSE_MODE_SERVER` /
  `MOUSE_MODE_CLIENT` works unchanged.
- Log with the same `mode_name` match that the `INIT` handler
  uses.

### Fix 2 — Re-request CLIENT mode on MOUSE_MODE transitions

In the same arm, after dispatching the event: if
`supported_modes & MOUSE_MODE_CLIENT as u16 != 0` and
`current_mode as u32 != MOUSE_MODE_CLIENT`, send a fresh
`MOUSE_MODE_REQUEST(CLIENT)`. This mirrors the INIT-time
logic at `main_channel.rs:329-339` so a guest reboot can
recover absolute mouse mode.

Extract the "if server supports client and we're not in it,
request client" block into a helper method called from both
the INIT and MOUSE_MODE arms.

### Fix 3 — Handle `MULTI_MEDIA_TIME`

- Add `pub const MULTI_MEDIA_TIME: u16 = 106;` to
  `shakenfist-spice-protocol::constants::main_server`.
- Add `main_server::MULTI_MEDIA_TIME => "multi_media_time",`
  to the name table in `shakenfist-spice-protocol/src/logging.rs`.
- Handle type 106 in `main_channel.rs` by reading a `u32` and
  logging at debug. No state needs to be stored yet — the
  playback channel will wire this in when audio sync is
  implemented. Note this in "Future work" below.

### Tests

Add to `ryll/src/channels/main_channel.rs` (or wherever
similar handler tests already live — check before placing):

- A test that feeds a `MOUSE_MODE` payload of `03 00 01 00`
  through the parse logic and asserts the emitted
  `ChannelEvent::MouseMode` carries `current_mode == 1`, and
  that a follow-up `MOUSE_MODE_REQUEST` with CLIENT is sent.
- A test that feeds a `MULTI_MEDIA_TIME` payload (4-byte u32)
  and asserts no `log_unknown_once` key is registered for
  `main:hexdump:106`.
- Existing main-channel tests must still pass.

## Execution

One commit per fix, in this order. Each commit must build and
pass `pre-commit run --all-files` on its own.

| Step | Effort | Model  | Isolation | Brief |
|------|--------|--------|-----------|-------|
| 1    | low    | sonnet | none      | Add `MULTI_MEDIA_TIME = 106` const + name-table entry + debug-logging handler in `main_channel.rs`. Add a test that feeds the 4-byte payload and confirms no `main:hexdump:106` key gets registered. Commit as "Handle MAIN MULTI_MEDIA_TIME message.". |
| 2    | medium | sonnet | none      | Parse `MOUSE_MODE` as u16+u16 and extract the "request CLIENT mode" helper. Call the helper from both INIT and MOUSE_MODE arms. Add a test with payload `03 00 01 00` asserting emitted event + follow-up request. Commit as "Parse MOUSE_MODE wire format correctly and recover after reboot.". |

Management session runs `pre-commit run --all-files` and
`make test` after each commit before handing off the branch
for a PR.

## Success criteria

* `pre-commit run --all-files` passes.
* `make test` passes.
* New unit tests cover both fixes.
* A replay of the pedantic report's main.pcap no longer
  fires `main:hexdump:106`.
* The commits mention the bug-report artefacts in their
  bodies so the trail is traceable.

## Future work

* Bug-report trigger-time snapshot (own plan —
  `PLAN-bugreport-trigger-snapshot.md`). Required before we
  can usefully debug the static bug.
* Store `multi_media_time` on the main-channel state and
  publish it to the playback channel when audio sync is
  implemented. Currently we just consume the message.

## Bugs fixed during this work

To be filled in if we encounter anything else during the
implementation.

## Documentation index maintenance

This is a standalone plan, so add it under *Standalone plans*
in `docs/plans/index.md` with the 2026-04-23 date. It does
not need an `order.yml` entry (only master plans do).
