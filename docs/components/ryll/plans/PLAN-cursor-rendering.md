# Render SPICE cursor on screen

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, and data flow. Consult `AGENTS.md`
for build commands, project conventions, and code
organisation. The `shakenfist/kerbside` repository contains
a working SPICE proxy implementation in Python, including
SPICE protocol documentation in `docs/` and a reference
SPICE client in `testclient/ryll/` -- refer to these when
working on protocol questions. In particular,
`kerbside/spiceprotocol/packets/cursor.py` shows how to
decode SpiceCursor structures.

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table like this in this master plan under the
Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. Parse cursor data | PLAN-cursor-rendering-phase-01-parse.md | Not started |
| ...   | ...  | ...    |
```

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Ryll currently handles cursor channel messages for position
and visibility (INIT, SET, MOVE, HIDE) but does not parse
or render cursor image data. The cursor is never drawn on
screen. In server mouse mode (mode 1), the server controls
the cursor position and shape -- the client must render the
cursor image at the position the server specifies.

The cursor channel receives `SpiceCursor` structures that
contain the cursor image. These have:

- **Flags** (u32): CACHE_ME (bit 1), FROM_CACHE (bit 2)
- **Header** (21 bytes): unique_id (u64), type (u16),
  width (u16), height (u16), hot_spot_x (u16),
  hot_spot_y (u16)
- **Pixel data**: format depends on type field

Cursor types:

| Type | Name    | Format                          |
|-----:|---------|---------------------------------|
|    0 | Alpha   | 32-bit ARGB, 4 bytes/pixel      |
|    1 | Mono    | 1-bit packed, byte-aligned rows |
|    5 | Color24 | 24-bit BGR, 3 bytes/pixel       |
|    6 | Color32 | 32-bit xRGB, 4 bytes/pixel      |

Types 2-4 (Color4, Color8, Color16) are rare in practice.
Alpha (type 0) is by far the most common from QEMU/KVM.

The cursor cache allows the server to send a cursor once
and reuse it by unique_id via the FROM_CACHE flag.

## Mission and problem statement

Render the server-provided cursor image on screen so the
user can see where they are pointing. This requires:

1. Parsing SpiceCursor data from INIT and SET messages
2. Converting cursor pixel data to RGBA
3. Caching cursors by unique_id
4. Drawing the cursor as an egui overlay at the server-
   reported position (accounting for hot_spot offset)
5. Handling cache invalidation (INVALIDATE_ONE,
   INVALIDATE_ALL)

## Open questions

- Should we render cursors in both server mode and client
  mode, or only server mode? In client mode the OS cursor
  is visible, but the server may still send a custom shape.
- Should we hide the native OS cursor when over the SPICE
  surface? This is what virt-viewer does.
- How should we handle cursor types 2-4 (palette-based)?
  These are very rare. We could log a warning and skip
  them initially.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Parse SpiceCursor | [PLAN-cursor-rendering-phase-01-parse.md](/components/ryll/plans/PLAN-cursor-rendering-phase-01-parse/) | Complete |
| 2. Render overlay | [PLAN-cursor-rendering-phase-02-render.md](/components/ryll/plans/PLAN-cursor-rendering-phase-02-render/) | Complete |

**Phase 1: Parse SpiceCursor data**

- Add `SpiceCursorHeader` struct to `protocol/messages.rs`
  with fields: flags, unique_id, cursor_type, width, height,
  hot_spot_x, hot_spot_y
- Parse SpiceCursor data from CURSOR_INIT (after the 9-byte
  position/trail/visible header) and CURSOR_SET (after the
  5-byte position/visible header)
- Handle the FROM_CACHE flag (no pixel data, look up by id)
- Convert Alpha (type 0) pixel data from ARGB to RGBA
- Convert Color32 (type 6) from xRGB to RGBA
- Add a cursor image cache (HashMap<u64, CursorImage>)
- Handle INVALIDATE_ONE and INVALIDATE_ALL for the cache
- Add a new `ChannelEvent::CursorShape` variant to pass the
  cursor image and hot_spot to the app
- Unit test the ARGB-to-RGBA conversion

**Phase 2: Render cursor overlay**

- Store the current cursor image in `RyllApp`
- In the egui `update()` method, after rendering surfaces,
  draw the cursor image as a small texture at the server-
  reported position minus the hot_spot offset
- Hide the native OS cursor when hovering over the SPICE
  surface (egui `CursorIcon::None`)
- Handle cursor visibility (HIDE message, visible flag)
- Account for window scaling if the surface doesn't match
  the viewport pixel-for-pixel

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck).
* A visible cursor appears on the SPICE surface when
  connected to a real server in server mouse mode.
* The cursor moves when the server sends MOVE messages.
* Cursor shape changes when the server sends SET messages.
* Cursor caching works (FROM_CACHE flag uses cached image).
* The native OS cursor is hidden over the SPICE surface.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` have been
  updated if needed.
* If the changes affect SPICE protocol behaviour, the
  relevant documentation in `shakenfist/kerbside/docs/` has
  been reviewed.

### Future work

- Support palette-based cursor types (Color4, Color8,
  Color16) if they are encountered in practice.
- Animated cursor trails (TRAIL message).
- Client-mode cursor shape override (server may send a
  custom cursor shape even in client mode).

### Bugs fixed during this work

(none yet)

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
