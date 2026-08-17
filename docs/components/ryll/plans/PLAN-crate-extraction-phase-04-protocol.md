# Phase 4: Extract shakenfist-spice-protocol

## Prompt

Before executing any step of this plan, back brief the operator
as to your understanding of the plan and how the work you intend
to do aligns with the master plan
([PLAN-crate-extraction.md](/components/ryll/plans/PLAN-crate-extraction/)) and
Decisions #1, #4, #5, #6, and #7 it records.

This phase is the second piece of cross-crate extraction work,
following the same pattern as Phase 3 ([compression](/components/ryll/plans/PLAN-crate-extraction-phase-03-compression/))
but at roughly twice the scale: four files instead of four
files, ~1585 lines instead of ~2240, more `use crate::protocol`
import sites in ryll (9 files instead of 1), and one in-flight
API cleanup (the marker-struct → real-struct refactor of four
input message types per Decision #5).

The invariant to preserve is that all 99 existing ryll tests
continue to pass, and ryll's end-to-end SPICE client behaviour
is bit-for-bit unchanged. **The wire format must not change.**
The marker-struct refactor changes only the public Rust API
shape, not the bytes that go on the wire.

`SpiceClient` ([ryll/src/protocol/client.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/client.rs))
**stays in ryll** during Phase 4 because it currently imports
`crate::config::Config`. It is moved into the new crate in
Phase 6 ([ConnectionConfig refactor](/components/ryll/plans/PLAN-crate-extraction/))
once the dependency on ryll's `Config` is replaced with a narrow
`ConnectionConfig` struct per Decision #4. Phase 4 keeps
`client.rs` exactly where it is and updates its imports so it
consumes its sibling protocol items from the new crate instead
of from `super::*`.

I prefer one commit per logical change. Each commit must leave
the tree in a working state: `cargo build`, `cargo test
--workspace`, `pre-commit run --all-files`, and the CI workflow
must all pass after every commit.

## Situation

After Phases 1-3 (commits `7191d9e` through `24c0913`), the
repository is a four-member Cargo workspace:

```
ryll-repo/
├── Cargo.toml                         # workspace manifest
├── ryll/                              # the application
│   ├── Cargo.toml
│   └── src/
│       ├── channels/                  # 6 channel handlers, all
│       │                              # consume crate::protocol::*
│       ├── protocol/                  # the target of Phase 4
│       │   ├── mod.rs                 # 8 lines
│       │   ├── client.rs              # 250 lines, stays here
│       │   ├── constants.rs           # 357 lines
│       │   ├── link.rs                # 355 lines
│       │   ├── logging.rs             # 272 lines
│       │   └── messages.rs            # 601 lines
│       ├── app.rs                     # imports SpiceClient
│       └── ...
├── shakenfist-spice-compression/      # real crate (Phase 3)
├── shakenfist-spice-protocol/         # v0.0.0 placeholder
└── shakenfist-spice-usbredir/         # v0.0.0 placeholder
```

**Current state of the protocol code** (from the Phase 4
research pass):

* [ryll/src/protocol/mod.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/mod.rs)
  is 8 lines: declares the five sub-modules, plus
  `pub use client::SpiceClient;` and `pub use constants::*;`.
  These two re-exports are the API surface that the rest of
  ryll consumes (`crate::protocol::ChannelType`,
  `crate::protocol::SpiceClient`, etc.).

* [ryll/src/protocol/constants.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/constants.rs)
  (357 lines): SPICE magic, version, capability flags,
  `ChannelType`, `SpiceError`, `ImageType`, `NotifySeverity`
  enums; the `main_server`/`main_client`/`display_server`/
  `display_client`/`inputs_client`/`inputs_server`/
  `cursor_server`/`cursor_client`/`spicevmc_server`/
  `spicevmc_client` modules of message-type opcode constants;
  `keyboard_modifiers` and `mouse_buttons` const modules.
  Pure constants and enum impls. No `use crate::*` imports.

* [ryll/src/protocol/messages.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/messages.rs)
  (601 lines): 14 real wire-format structs (`MessageHeader`,
  `MainInit`, `Ping`, `ChannelEntry`, `ChannelsList`, `SetAck`,
  `Notify`, `SurfaceCreate`, `DrawCopyBase`, `ImageDescriptor`,
  `DisplayInit`, `CursorInit`, `CursorSet`, `SpiceCursorHeader`)
  plus the `make_message()` helper. Also contains four
  **marker structs** that are zero-sized namespaces with
  associated `write` functions (the subject of Decision #5):
  - `InputsKeyModifiers` ([line 531](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/messages.rs#L531)):
    `pub fn write(modifiers: u16, buf: &mut Vec<u8>)`
  - `KeyEvent` ([line 541](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/messages.rs#L541)):
    `pub fn write(scancode: u32, buf: &mut Vec<u8>)`
  - `MousePosition` ([line 551](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/messages.rs#L551)):
    `pub fn write(x: u32, y: u32, buttons: u32, display_id: u8, buf: &mut Vec<u8>)`
    — **the worst case**: four positional args, three of
    them the same `u32` type, easily mis-ordered at call
    sites.
  - `MouseButton` ([line 570](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/messages.rs#L570)):
    `pub fn write(button: u32, buttons_state: u32, buf: &mut Vec<u8>)`
    — has a private `mask_to_id` helper.

* [ryll/src/protocol/logging.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/logging.rs)
  (272 lines): pure formatting and tracing helpers
  (`timestamp`, `format_timestamp`, `log_message`, `log_detail`,
  `log_unknown`, `log_incomplete`, `hex_dump`) plus a nested
  `pub mod message_names` with 10 message-name lookup
  functions (one per channel direction). All call into
  `tracing::*` macros directly.

* [ryll/src/protocol/link.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/link.rs)
  (355 lines): the SPICE link handshake. Public items:
  `SpiceStream` enum (Plain/Tls wrapper), `SpiceLinkMess`
  struct + `::new` constructor + `::serialize`, `SpiceLinkReply`
  struct + `::parse`, `pub fn encrypt_password` (RSA-OAEP +
  SHA1), `pub async fn perform_link`, `pub async fn perform_auth`.
  `SpiceLinkMess::new` has 5 positional args of distinct types
  (`u32`, `ChannelType`, `u8`, `u32`, `u32`) — flagged by the
  master plan as "minor", and the research pass confirms it is
  ergonomically fine as-is because the types are all distinct.
  No internal cleanup needed during Phase 4.

* [ryll/src/protocol/client.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/client.rs)
  (250 lines, **stays in ryll**): `SpiceClient` struct that
  wraps a connection. Imports
  [`use crate::config::Config;`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/client.rs#L17)
  which is the dependency that forces it to stay in ryll until
  Phase 6. Also imports
  `use super::constants::{ChannelType, SpiceError};` and
  `use super::link::{perform_auth, perform_link, SpiceStream};` —
  these become `shakenfist_spice_protocol::*` imports during
  Phase 4. Imports nothing from `messages.rs` or `logging.rs`.

**Decompression coupling check**: zero. None of the four
target files import anything from `crate::*`. They only use
`std::*`, `byteorder`, `tokio`, `tokio_rustls`, `anyhow`,
`rsa`, `sha1`, `rand`, and `tracing`. All already in ryll's
`Cargo.toml`. No tests to migrate (no `#[test]` functions in
any of the four target files).

**ryll consumers of `crate::protocol::*`** (9 files total,
from the research pass):

| File | Imports |
|---|---|
| [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs) | `crate::protocol::{ChannelType, SpiceClient}` |
| [ryll/src/channels/mod.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/mod.rs) | `crate::protocol::ChannelType` |
| [ryll/src/channels/display.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs) | `link::SpiceStream`, `logging::{self, message_names}`, `messages::{make_message, DisplayInit, DrawCopyBase, ImageDescriptor, MessageHeader, Ping, SetAck, SurfaceCreate}`, `{display_client, display_server, ChannelType, ImageType}` |
| [ryll/src/channels/inputs.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs) | `link::SpiceStream`, `logging::{self, message_names}`, `messages::{make_message, InputsKeyModifiers, KeyEvent, MessageHeader, MouseButton, MousePosition, Ping, SetAck}`, `{inputs_client, inputs_server, keyboard_modifiers, ChannelType}` |
| [ryll/src/channels/main_channel.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/main_channel.rs) | `link::SpiceStream`, `logging::{self, message_names}`, `messages::*`, `{main_client, main_server, ChannelType, NotifySeverity}` |
| [ryll/src/channels/cursor.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/cursor.rs) | `link::SpiceStream`, `logging::{self, message_names}`, `messages::*`, `{cursor_client, cursor_server, ChannelType}` |
| [ryll/src/channels/usbredir.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/usbredir.rs) | `link::SpiceStream`, `logging::{self, message_names}`, `messages::{make_message, MessageHeader, Ping, SetAck}`, `{spicevmc_client, spicevmc_server, ChannelType}` |
| [ryll/src/channels/webdav.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/webdav.rs) | `link::SpiceStream`, `logging::{self, message_names}`, `messages::{make_message, MessageHeader, Ping, SetAck}`, `{spicevmc_client, spicevmc_server, ChannelType}` |
| [ryll/src/protocol/client.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/client.rs) | `super::constants::{ChannelType, SpiceError}`, `super::link::{perform_auth, perform_link, SpiceStream}` |

`app.rs` is the only file that imports `SpiceClient`. Every
other file imports non-client items only.

**Marker-struct call sites** (the API cleanup scope, all in
[ryll/src/channels/inputs.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs)):

| Line | Call |
|---|---|
| 341 | `KeyEvent::write(scancode, &mut payload)?;` (KeyDown) |
| 359 | `KeyEvent::write(scancode, &mut payload)?;` (KeyUp) |
| 379 | `MousePosition::write(x, y, self.button_state, 0, &mut payload)?;` (MouseMove) |
| 390 | `MousePosition::write(x, y, self.button_state, 0, &mut pos_payload)?;` (MouseDown position) |
| 423 | `MousePosition::write(x, y, self.button_state, 0, &mut pos_payload)?;` (MouseUp position) |
| 407 | `MouseButton::write(button, self.button_state, &mut payload)?;` (MouseDown) |
| 438 | `MouseButton::write(button, self.button_state, &mut payload)?;` (MouseUp) |
| 470 | `InputsKeyModifiers::write(modifiers, &mut payload)?;` (initial modifiers send) |

**8 call sites, all in one file.** This contains the blast
radius nicely.

## Mission and problem statement

Replace the Phase 2 `shakenfist-spice-protocol v0.0.0`
placeholder with a real crate containing the SPICE protocol
constants, wire-format messages, logging helpers, and link
handshake — everything in `ryll/src/protocol/` *except*
`client.rs`. Switch ryll's 8 consumer files to import from
the new crate. Refactor the four marker-struct message types
into real structs with `pub` fields and `write(&self, buf)`
methods, per Decision #5, and update the 8 call sites in
`inputs.rs`.

After this phase completes:

- The `shakenfist-spice-protocol` crate exists at
  `shakenfist-spice-protocol/` as a real workspace member
  containing `constants.rs`, `link.rs`, `logging.rs`, and
  `messages.rs`.
- The four marker-struct message types (`InputsKeyModifiers`,
  `KeyEvent`, `MousePosition`, `MouseButton`) are real
  data-bearing structs with `#[derive(Debug, Clone, Copy)]`,
  `pub` fields, and `pub fn write(&self, buf: &mut Vec<u8>)
  -> io::Result<()>` methods.
- `ryll/src/protocol/` contains only `client.rs` and a thin
  `mod.rs` that re-exports `SpiceClient` and re-exports the
  protocol items the rest of ryll references via the
  `crate::protocol::*` paths it currently uses (so the
  per-channel-handler imports can keep working with minimal
  churn).
- Or alternatively (and this is the chosen approach — see
  Approach below), every ryll file is updated to import
  directly from `shakenfist_spice_protocol::*`, and
  `ryll/src/protocol/` is reduced to just `client.rs` with
  no `mod.rs` shim.
- All 99 existing ryll tests continue to pass.
- `cargo test --workspace` exercises ryll, the new protocol
  crate (which has no tests today), and the existing
  compression crate's 2 tests.
- The new crate is at version `0.0.0` on its local
  `Cargo.toml` — **no `0.1.0` publish happens in this phase**.
  Same as Phase 3, publishing to crates.io is future work.
- Per Decision #1 (and Phase 3's hard-learned dual-spec
  caveat), ryll's path-dependency on
  `shakenfist-spice-protocol` is added **without** the
  `version =` qualifier; cargo refuses to resolve `^0.1`
  against a local `0.0.0`.

There must be no behavioural changes to ryll. Every SPICE
message must be encoded and decoded exactly as before; every
byte on the wire must be bit-identical.

## Approach

Phase 4 splits into three commits, each independently
buildable, testable, and CI-green:

1. **Marker-struct refactor commit** (no cross-crate
   movement). Refactor the four marker-struct message types
   in `ryll/src/protocol/messages.rs` into real
   data-bearing structs with `pub` fields and
   `write(&self, buf)` methods, then update the 8 call sites
   in `ryll/src/channels/inputs.rs` to use struct literal
   construction followed by `.write(&mut buf)`. Wire format
   unchanged. This commit lands first because:
   - It's a self-contained API change inside ryll, so it
     doesn't get tangled with the cross-crate move.
   - If the refactor accidentally changes wire bytes, the
     test suite will catch it before the much larger
     extraction commit obscures the cause.
   - It establishes the *final* shape of the message types
     before they become public crate API, so the extraction
     commit moves them into the crate in their final form,
     not in their interim marker-struct form.

2. **Extraction commit** (the real work). Replace the
   `shakenfist-spice-protocol/` placeholder contents with the
   real crate implementation:
   - Rewrite `shakenfist-spice-protocol/Cargo.toml` with
     real dependencies.
   - Rewrite `shakenfist-spice-protocol/src/lib.rs` to
     declare the four sub-modules (`constants`, `link`,
     `logging`, `messages`) and re-export the most commonly
     used items.
   - `git mv` the four files (`constants.rs`, `link.rs`,
     `logging.rs`, `messages.rs`) from
     `ryll/src/protocol/` into
     `shakenfist-spice-protocol/src/`.
   - Update each moved file's internal imports if any
     reference cross-file paths via `super::*` (e.g.
     `link.rs` uses `super::constants::*`).
   - Add `shakenfist-spice-protocol = { path = "..." }` to
     ryll's `[dependencies]`.
   - Update all 9 ryll consumer files to import from
     `shakenfist_spice_protocol::*` instead of
     `crate::protocol::*`. The 9 files include `client.rs`
     itself, which gets `use shakenfist_spice_protocol::{...}`
     instead of `use super::*`.
   - Slim down `ryll/src/protocol/mod.rs` to just declare
     `pub mod client;` and re-export `pub use client::SpiceClient;`.
     The `pub use constants::*;` re-export is removed; ryll's
     consumers now go through the new crate directly.
   - The `mod protocol;` declaration in `ryll/src/main.rs`
     stays — it's a module that contains only `client.rs`
     after this phase, but it's not removed.
   - Update the placeholder README to describe the real
     crate.

3. **Status update commit**: mark Phase 4 complete in the
   master plan, record execution discoveries.

The chosen design — **no thin shim in `ryll/src/protocol/mod.rs`**,
every consumer updated to import from `shakenfist_spice_protocol::*`
directly — is the cleaner end state. The alternative (keep
`ryll/src/protocol/mod.rs` as a `pub use shakenfist_spice_protocol::*;`
shim) would minimise diff churn in the channel handlers but
leaves ryll with a confusing two-level indirection where
`crate::protocol::ChannelType` and
`shakenfist_spice_protocol::ChannelType` are the same thing.
That's the kind of legacy crud that hangs around forever once
shipped. Better to bite the bullet and update all 9 import
sites in one commit.

## Pre-flight verification

Before starting Commit 1, Claude verifies:

1. **Working tree is clean** on `display-iteration`. The
   most recent commit should be `24c0913` (Phase 3 status
   update) or similar.
2. **CI is green** on the most recent pushed commit. Phase 3
   verified green at the end of the user's most recent push.
   If new commits have landed since, re-check.
3. **All 99 ryll tests pass currently** via `make test`. The
   pre-Phase-4 baseline should be 97 ryll + 2
   shakenfist-spice-compression = 99 total, exactly matching
   the post-Phase-3 state.
4. **`cargo clippy --workspace --all-targets -- -D warnings`
   passes**. If new lints have surfaced for any reason, fix
   them in a separate commit before starting Phase 4.
5. **The protocol module structure matches the research
   findings**: `ls ryll/src/protocol/` shows exactly
   `client.rs`, `constants.rs`, `link.rs`, `logging.rs`,
   `messages.rs`, `mod.rs` (six files). If anything has been
   added or removed since the research pass, stop and re-plan.
6. **The four marker structs still exist with the expected
   signatures** in `messages.rs`. A quick `grep -n
   '^impl InputsKeyModifiers\|^impl KeyEvent\|^impl MousePosition\|^impl MouseButton'
   ryll/src/protocol/messages.rs` should return four hits.
7. **The 8 call sites in `inputs.rs` still exist**. A
   `grep -n 'KeyEvent::write\|MousePosition::write\|MouseButton::write\|InputsKeyModifiers::write'
   ryll/src/channels/inputs.rs` should return exactly 8 hits.

If any of these checks fail, stop and re-plan rather than
improvising.

## Marker-struct refactor design

Each of the four marker structs becomes a real data-bearing
struct following a consistent pattern. Wire format is
unchanged.

### `InputsKeyModifiers`

**Before:**
```rust
pub struct InputsKeyModifiers;

impl InputsKeyModifiers {
    pub fn write(modifiers: u16, buf: &mut Vec<u8>) -> io::Result<()> {
        buf.write_u16::<LittleEndian>(modifiers)?;
        Ok(())
    }
}
```

**After:**
```rust
#[derive(Debug, Clone, Copy)]
pub struct InputsKeyModifiers {
    pub modifiers: u16,
}

impl InputsKeyModifiers {
    pub fn write(&self, buf: &mut Vec<u8>) -> io::Result<()> {
        buf.write_u16::<LittleEndian>(self.modifiers)?;
        Ok(())
    }
}
```

**Call site update** in `inputs.rs:470`:
```rust
// before:
InputsKeyModifiers::write(modifiers, &mut payload)?;

// after:
InputsKeyModifiers { modifiers }.write(&mut payload)?;
```

### `KeyEvent`

**Before:**
```rust
pub struct KeyEvent;

impl KeyEvent {
    pub fn write(scancode: u32, buf: &mut Vec<u8>) -> io::Result<()> {
        buf.write_u32::<LittleEndian>(scancode)?;
        Ok(())
    }
}
```

**After:**
```rust
#[derive(Debug, Clone, Copy)]
pub struct KeyEvent {
    pub scancode: u32,
}

impl KeyEvent {
    pub fn write(&self, buf: &mut Vec<u8>) -> io::Result<()> {
        buf.write_u32::<LittleEndian>(self.scancode)?;
        Ok(())
    }
}
```

**Call site update** in `inputs.rs:341` and `:359`:
```rust
// before:
KeyEvent::write(scancode, &mut payload)?;

// after:
KeyEvent { scancode }.write(&mut payload)?;
```

### `MousePosition`

This is the highest-value refactor: 4 positional args, 3 of
them `u32`, 1 `u8`. The current call sites all pass `0` for
`display_id`, which is the kind of "magic number" that
becomes immediately self-documenting after the refactor.

**Before:**
```rust
pub struct MousePosition;

impl MousePosition {
    pub fn write(
        x: u32,
        y: u32,
        buttons: u32,
        display_id: u8,
        buf: &mut Vec<u8>,
    ) -> io::Result<()> {
        buf.write_u32::<LittleEndian>(x)?;
        buf.write_u32::<LittleEndian>(y)?;
        buf.write_u32::<LittleEndian>(buttons)?;
        buf.write_u8(display_id)?;
        Ok(())
    }
}
```

**After:**
```rust
#[derive(Debug, Clone, Copy)]
pub struct MousePosition {
    pub x: u32,
    pub y: u32,
    pub buttons: u32,
    pub display_id: u8,
}

impl MousePosition {
    pub fn write(&self, buf: &mut Vec<u8>) -> io::Result<()> {
        buf.write_u32::<LittleEndian>(self.x)?;
        buf.write_u32::<LittleEndian>(self.y)?;
        buf.write_u32::<LittleEndian>(self.buttons)?;
        buf.write_u8(self.display_id)?;
        Ok(())
    }
}
```

**Call site update** in `inputs.rs:379`, `:390`, `:423`:
```rust
// before:
MousePosition::write(x, y, self.button_state, 0, &mut payload)?;

// after:
MousePosition {
    x,
    y,
    buttons: self.button_state,
    display_id: 0,
}
.write(&mut payload)?;
```

### `MouseButton`

This one has a private `mask_to_id` helper. The helper stays
private and unchanged; the public API is what gets refactored.

**Before:**
```rust
pub struct MouseButton;

impl MouseButton {
    fn mask_to_id(mask: u32) -> u32 {
        match mask {
            0x01 => 1, // LEFT
            0x02 => 2, // MIDDLE
            // ...
        }
    }

    pub fn write(button: u32, buttons_state: u32, buf: &mut Vec<u8>) -> io::Result<()> {
        buf.write_u32::<LittleEndian>(Self::mask_to_id(button))?;
        buf.write_u32::<LittleEndian>(buttons_state)?;
        Ok(())
    }
}
```

**After:**
```rust
#[derive(Debug, Clone, Copy)]
pub struct MouseButton {
    /// Bitmask for the button being pressed/released. Encoded
    /// to a button id on write via `mask_to_id`.
    pub button: u32,
    /// Current state of all buttons.
    pub buttons_state: u32,
}

impl MouseButton {
    fn mask_to_id(mask: u32) -> u32 {
        match mask {
            0x01 => 1, // LEFT
            0x02 => 2, // MIDDLE
            // ...
        }
    }

    pub fn write(&self, buf: &mut Vec<u8>) -> io::Result<()> {
        buf.write_u32::<LittleEndian>(Self::mask_to_id(self.button))?;
        buf.write_u32::<LittleEndian>(self.buttons_state)?;
        Ok(())
    }
}
```

**Call site update** in `inputs.rs:407` and `:438`:
```rust
// before:
MouseButton::write(button, self.button_state, &mut payload)?;

// after:
MouseButton {
    button,
    buttons_state: self.button_state,
}
.write(&mut payload)?;
```

**Note on the `button` field name**: ryll's call sites have a
local `button: u32` variable that shadows whatever the
encoder thinks of it. The struct field is also called
`button` to match. The doc comment on the field documents
that it's a *bitmask* (e.g. `0x01` for LEFT) that gets
encoded to a button id by `mask_to_id` at write time. This
is mildly confusing but matches the existing code; cleaning
up the naming is an API polish concern for the eventual
`0.1.0` release, not for this phase.

## Crate layout

### `shakenfist-spice-protocol/Cargo.toml`

```toml
[package]
name = "shakenfist-spice-protocol"
version = "0.0.0"
edition.workspace = true
license.workspace = true
authors.workspace = true
repository.workspace = true
description = "Pure-Rust SPICE protocol primitives: constants, wire-format messages, link handshake, and logging helpers. Extracted from the ryll SPICE client. Reserved crate name; the first published release will follow as 0.1.0 once the API has stabilised."
readme = "README.md"

[dependencies]
anyhow = "1"
byteorder = "1"
rand = "0.8"
rsa = "0.9"
sha1 = "0.10"
tokio = { version = "1", features = ["io-util", "net"] }
tokio-rustls = "0.26"
tracing = "0.1"
```

Notes:

- The `version` stays at `0.0.0`. **This phase does not
  publish.** Bumping to `0.1.0` is the future-work polish
  task.
- No feature flags. Unlike `shakenfist-spice-compression`,
  the protocol crate doesn't have natural feature
  boundaries — all consumers will want all of the constants,
  messages, link handshake, and logging together. Adding
  features here would be over-engineering. (Compression had
  feature flags because the four codecs are independent and
  GLZ uniquely needs `tokio`.)
- `tokio` is required because `link.rs` defines async
  functions and uses `tokio::net::TcpStream`. The features
  list `io-util` (for `AsyncReadExt`/`AsyncWriteExt`) and
  `net` (for `TcpStream`). This is narrower than ryll's
  `features = ["full"]` which is correct for a library.
- `tokio-rustls` is required because `SpiceStream` wraps
  either a plain TCP stream or a TLS stream.
- `rsa`, `sha1`, `rand` are required for the SPICE auth
  RSA-OAEP password encryption in `link.rs::encrypt_password`.
- `anyhow` is used for `Result` types in `link.rs`. Long
  term it would be cleaner to use a per-crate error enum
  (`thiserror`-style), but converting away from `anyhow` is
  an API polish concern for `0.1.0`, not extraction work.

### `shakenfist-spice-protocol/src/lib.rs`

```rust
//! Pure-Rust SPICE protocol primitives extracted from the
//! [ryll](https://github.com/shakenfist/ryll) SPICE client.
//!
//! This crate provides the wire-format types and helpers
//! needed to implement a SPICE client, server, or proxy in
//! Rust:
//!
//! - [`constants`] — protocol magic, version, capability
//!   flags, and message-type opcode constants for every
//!   channel direction.
//! - [`messages`] — wire-format struct definitions with
//!   `read` and `write` methods, including the input message
//!   types (`KeyEvent`, `MousePosition`, etc.).
//! - [`link`] — SPICE link handshake (`SpiceLinkMess`,
//!   `SpiceLinkReply`, `perform_link`, `perform_auth`),
//!   `SpiceStream` (a Plain/TLS wrapper), and the
//!   `encrypt_password` helper for SPICE auth.
//! - [`logging`] — protocol-traffic logging helpers and
//!   message-name lookup tables for every channel direction.
//!
//! A high-level `SpiceClient` for actually connecting to a
//! SPICE server is intentionally not part of this crate; it
//! lives in ryll for now and will move into a separate
//! `shakenfist-spice-client` (or similar) crate once it has
//! been refactored to take a narrow `ConnectionConfig`
//! struct instead of ryll's broader application config.

pub mod constants;
pub mod link;
pub mod logging;
pub mod messages;

// Re-export the most commonly used items at the crate root
// for convenience. Consumers can also reach into the
// sub-modules directly.
pub use constants::{
    capabilities, cursor_client, cursor_server, display_client, display_server, inputs_client,
    inputs_server, keyboard_modifiers, main_client, main_server, mouse_buttons, spicevmc_client,
    spicevmc_server, ChannelType, ImageType, NotifySeverity, SpiceError, SPICE_MAGIC,
    SPICE_VERSION_MAJOR, SPICE_VERSION_MINOR,
};
```

The re-export list at the bottom matches the items that ryll
currently accesses through `crate::protocol::*` (which today
goes through `pub use constants::*` in
`ryll/src/protocol/mod.rs`). This means ryll's `use
shakenfist_spice_protocol::{ChannelType, ImageType, ...};`
imports are short and don't need `constants::` prefixes.

### `shakenfist-spice-protocol/README.md`

Same general structure as the compression crate's README (a
description, a status section explaining the unpublished
state, a brief usage example, a source link). Drafted in the
execution section below.

### `ryll/Cargo.toml` change

Add the new path-dependency to `[dependencies]`:

```toml
# Extracted SPICE protocol primitives. Same dual-spec caveat
# as shakenfist-spice-compression: the local crate is at
# 0.0.0 to match the published placeholder, so the path-dep
# does not yet carry a `version =` qualifier. The dual-spec
# idiom only becomes usable once the local version reaches
# 0.1.0.
shakenfist-spice-protocol = { path = "../shakenfist-spice-protocol" }
```

Place near the existing `shakenfist-spice-compression` line.

### Updates to ryll's protocol module

After the extraction commit, [ryll/src/protocol/](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol)
contains only `client.rs` and a slimmed-down `mod.rs`:

```rust
// ryll/src/protocol/mod.rs (after Phase 4)
pub mod client;

pub use client::SpiceClient;
```

The `pub use constants::*;` re-export is gone because every
ryll consumer now imports from `shakenfist_spice_protocol::*`
directly.

The `mod protocol;` declaration in
[ryll/src/main.rs:23](https://github.com/shakenfist/ryll/blob/develop/ryll/src/main.rs#L23) stays
unchanged. It now declares a 2-file directory (`mod.rs` +
`client.rs`) instead of a 6-file directory.

### Updates to `ryll/src/protocol/client.rs`

`client.rs` currently has these imports:

```rust
use crate::config::Config;
use super::constants::{ChannelType, SpiceError};
use super::link::{perform_auth, perform_link, SpiceStream};
```

After Phase 4 (but still in ryll, awaiting Phase 6):

```rust
use crate::config::Config;
use shakenfist_spice_protocol::{ChannelType, SpiceError};
use shakenfist_spice_protocol::link::{perform_auth, perform_link, SpiceStream};
```

The `use crate::config::Config;` line stays — that's exactly
the dependency that forces `client.rs` to remain in ryll
until Phase 6. The other two imports switch from `super::*`
to `shakenfist_spice_protocol::*`.

### Updates to ryll's other 8 consumer files

Each of `app.rs`, `channels/mod.rs`, `channels/display.rs`,
`channels/inputs.rs`, `channels/main_channel.rs`,
`channels/cursor.rs`, `channels/usbredir.rs`, and
`channels/webdav.rs` has one or more `use crate::protocol::*`
import lines. Each becomes
`use shakenfist_spice_protocol::*` (preserving the same
items inside the braces). Specifics vary per file — e.g.
`app.rs` keeps `crate::protocol::SpiceClient` because
`SpiceClient` lives in ryll, not the new crate.

`app.rs` is the only special case:

```rust
// before:
use crate::protocol::{ChannelType, SpiceClient};

// after:
use crate::protocol::SpiceClient;
use shakenfist_spice_protocol::ChannelType;
```

`SpiceClient` continues to be reachable via
`crate::protocol::SpiceClient` because of the `pub use
client::SpiceClient;` re-export in the slimmed-down
`ryll/src/protocol/mod.rs`.

## Execution

Each step below is one commit.

### Step 1: Marker-struct refactor

Refactor the four marker-struct message types in
[ryll/src/protocol/messages.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/messages.rs)
into real data-bearing structs per the "Marker-struct
refactor design" section above. Then update the 8 call sites
in
[ryll/src/channels/inputs.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs)
to use struct-literal construction followed by
`.write(&mut buf)`.

Concrete steps within the commit:

1. In
   [messages.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/messages.rs),
   replace the four marker-struct definitions with the
   data-bearing versions. The order matters only for diff
   readability — going top-to-bottom (`InputsKeyModifiers`
   first, then `KeyEvent`, `MousePosition`, `MouseButton`)
   matches the file ordering.
2. For `MouseButton`, the private `mask_to_id` helper stays
   in the impl block unchanged.
3. In
   [inputs.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs),
   update each of the 8 call sites per the "After" examples
   above. The struct literal lives on its own line followed
   by `.write(&mut buf)?;` rather than being an expression
   inside another statement; rustfmt may rewrite the
   formatting on commit but the semantics are unambiguous.
4. Verify locally inside the devcontainer:
   - `cargo fmt --all --check`
   - `cargo clippy --workspace --all-targets -- -D warnings`
   - `cargo build --workspace`
   - `cargo test --workspace` — all 99 tests still pass.
   - `pre-commit run --all-files`
5. Commit.

The diff should be ~80 lines: ~40 for the four struct
refactors in `messages.rs`, ~40 for the 8 call site updates
in `inputs.rs` (each updated call goes from 1 line to ~5
lines because of the multi-line struct literal).

**Commit message (subject ≤ 50 chars):**
> Make input message types data-bearing structs.

### Step 2: Extract shakenfist-spice-protocol

The real work. Larger than Phase 3's extraction commit
because there are 9 import-update sites in ryll instead of 1.

Concrete steps within the commit:

1. **Delete the Phase 2 placeholder contents** of
   `shakenfist-spice-protocol/src/lib.rs` (the 4-line
   placeholder doc-comment) and `README.md` (the placeholder
   README). Keep `shakenfist-spice-protocol/Cargo.toml` —
   will be rewritten in-place.

2. **Move the four protocol files** via `git mv` to preserve
   rename history:
   ```sh
   git mv ryll/src/protocol/constants.rs shakenfist-spice-protocol/src/constants.rs
   git mv ryll/src/protocol/link.rs      shakenfist-spice-protocol/src/link.rs
   git mv ryll/src/protocol/logging.rs   shakenfist-spice-protocol/src/logging.rs
   git mv ryll/src/protocol/messages.rs  shakenfist-spice-protocol/src/messages.rs
   ```

3. **Update each moved file's internal cross-file imports**.
   The research found that `link.rs` uses
   `super::constants::*` for protocol constants (likely
   `SPICE_MAGIC`, `SPICE_VERSION_*`, `ChannelType`,
   `SpiceError`, capability flags, etc.). After the move,
   `link.rs` lives at the crate root, and so do its
   siblings. The `super::*` paths still work
   semantically (`super` from a `lib.rs`-declared module
   resolves to the crate root, where `constants` is
   reachable as a sibling module), but a quick `grep -n
   'super::' shakenfist-spice-protocol/src/*.rs` will
   confirm whether any need adjusting. Anything that says
   `super::constants::*` can either stay as-is (working) or
   be changed to `crate::constants::*` (slightly more
   explicit). **Decision: leave as `super::*` if it
   compiles**, to minimise diff churn.

4. **Write the new `shakenfist-spice-protocol/Cargo.toml`**
   from the template above.

5. **Write the new `shakenfist-spice-protocol/src/lib.rs`**
   from the template above.

6. **Write the new `shakenfist-spice-protocol/README.md`**
   following the structure below:

   ```markdown
   # shakenfist-spice-protocol

   Pure-Rust SPICE protocol primitives:

   - **`constants`** — SPICE magic, version, capability flags,
     `ChannelType`, `SpiceError`, `ImageType`,
     `NotifySeverity`, and the message-type opcode constants
     for every channel direction (main / display / inputs /
     cursor / spicevmc).
   - **`messages`** — wire-format structs with `read`/`write`
     methods for every SPICE message type ryll knows about,
     including the input event types (`KeyEvent`,
     `MousePosition`, `MouseButton`, `InputsKeyModifiers`).
   - **`link`** — SPICE link handshake (`SpiceLinkMess`,
     `SpiceLinkReply`, `perform_link`, `perform_auth`),
     `SpiceStream` (a Plain/TLS wrapper), and the
     `encrypt_password` helper for SPICE password auth
     (RSA-OAEP + SHA1).
   - **`logging`** — protocol-traffic logging helpers and a
     `message_names` lookup module for every channel
     direction.

   A high-level `SpiceClient` for actually connecting to a
   SPICE server is intentionally not part of this crate; it
   lives in
   [ryll](https://github.com/shakenfist/ryll) for now and
   will move into a separate crate once it has been
   refactored to take a narrow `ConnectionConfig` struct
   instead of ryll's broader application config (see the
   [extraction plan](https://github.com/shakenfist/ryll/blob/develop/docs/plans/PLAN-crate-extraction.md)
   for context).

   ## Status

   This crate is **not yet published to crates.io**. The
   `0.0.0` entry there is a Phase 2 name reservation; the
   real `0.1.0` release will follow once API polish is
   complete.

   Internal consumers (ryll itself and the planned Rust
   rewrite of the shakenfist kerbside SPICE proxy) should
   depend on this crate via a workspace path or a git
   dependency until `0.1.0` ships.

   ## Source

   Extracted from the
   [ryll](https://github.com/shakenfist/ryll) SPICE client.
   ```

7. **Update `ryll/Cargo.toml`** to add the new dependency
   line (no `version =` qualifier per Decision #1's caveat).
   Place near the existing `shakenfist-spice-compression`
   line.

8. **Update `ryll/src/protocol/mod.rs`** down to:
   ```rust
   pub mod client;

   pub use client::SpiceClient;
   ```
   This is a 6-line file. The `pub mod constants/link/logging/messages;`
   declarations and `pub use constants::*;` re-export are
   removed.

9. **Update `ryll/src/protocol/client.rs`** to import from
   `shakenfist_spice_protocol::*`:
   ```rust
   // before:
   use super::constants::{ChannelType, SpiceError};
   use super::link::{perform_auth, perform_link, SpiceStream};

   // after:
   use shakenfist_spice_protocol::link::{perform_auth, perform_link, SpiceStream};
   use shakenfist_spice_protocol::{ChannelType, SpiceError};
   ```
   The `use crate::config::Config;` line is **unchanged**
   — it's the dependency that keeps client.rs in ryll.

10. **Update the 8 other ryll consumer files** to import
    from `shakenfist_spice_protocol::*` instead of
    `crate::protocol::*`. The import lines per file are
    listed in the Situation section. Pattern:
    ```rust
    // every line that says
    use crate::protocol::link::SpiceStream;
    use crate::protocol::logging::{self, message_names};
    use crate::protocol::messages::{...};
    use crate::protocol::{display_client, display_server, ChannelType, ImageType};

    // becomes:
    use shakenfist_spice_protocol::link::SpiceStream;
    use shakenfist_spice_protocol::logging::{self, message_names};
    use shakenfist_spice_protocol::messages::{...};
    use shakenfist_spice_protocol::{display_client, display_server, ChannelType, ImageType};
    ```

    The one exception is `app.rs`, which currently has:
    ```rust
    use crate::protocol::{ChannelType, SpiceClient};
    ```
    This must be split because `ChannelType` moves to the
    new crate and `SpiceClient` stays in ryll:
    ```rust
    use crate::protocol::SpiceClient;
    use shakenfist_spice_protocol::ChannelType;
    ```

11. **Verify locally** (the same suite as Phase 3's Step 2):
    - `cargo fmt --all --check`
    - `cargo clippy --workspace --all-targets -- -D warnings`
    - `cargo build --workspace`
    - `cargo test --workspace` — all 99 tests must still
      pass. The pre-Phase-4 baseline was 97 ryll + 2
      compression. Post-Phase-4 is 97 ryll + 2 compression +
      0 protocol = 99 still. The new crate adds zero tests
      because the old protocol files had none.
    - `cargo build --release -p ryll` produces a working
      binary.
    - `cargo build -p shakenfist-spice-protocol` succeeds.
    - `cargo deb --no-build -p ryll` and
      `cargo generate-rpm -p ryll` still produce the same
      artefacts.
    - `pre-commit run --all-files`

12. **Inspect git diff stats** before committing. Expected
    shape:
    - 4 file renames from `ryll/src/protocol/*.rs` to
      `shakenfist-spice-protocol/src/*.rs` (probably 100%
      similarity for `constants.rs`, `logging.rs`, and
      `messages.rs`, since they don't reference siblings;
      possibly 99% for `link.rs` if the `super::*` imports
      need any tweaks).
    - `shakenfist-spice-protocol/src/lib.rs` rewritten (was
      a 4-line placeholder, now ~30 lines).
    - `shakenfist-spice-protocol/Cargo.toml` rewritten (was
      minimal placeholder, now has deps).
    - `shakenfist-spice-protocol/README.md` rewritten.
    - `ryll/Cargo.toml` modified (+1 dep group).
    - `ryll/src/protocol/mod.rs` modified (slimmed from 8
      lines to 4).
    - `ryll/src/protocol/client.rs` modified (~3 import
      lines).
    - 8 ryll consumer files modified (each: 1-4 import
      lines updated).
    - `Cargo.lock` updated with the new dependency edge.
    If the diff includes anything outside that list, stop
    and investigate before committing.

13. Commit.

**Commit message (subject ≤ 50 chars):**
> Extract shakenfist-spice-protocol crate.

### Step 3: Mark Phase 4 complete

After the operator confirms CI is green on Steps 1 and 2 (or
chooses to defer the CI run):

1. Update the master plan's execution table:
   ```
   | 4. Extract shakenfist-spice-protocol crate | PLAN-crate-extraction-phase-04-protocol.md | Complete |
   ```
2. Add the execution discoveries to this phase plan's
   "Discoveries during execution" section.
3. Pre-commit and commit.

**Commit message (subject ≤ 50 chars):**
> Mark Phase 4 (protocol extraction) complete.

## Open questions

1. **Should the marker-struct refactor add `read` methods
   too?** Each new data-bearing struct is currently
   write-only (matching the existing API). If a future
   protocol-analysis tool wants to *parse* an
   `InputsKeyModifiers` event off the wire, it would need a
   matching `read` method. This is symmetric with the
   `MessageHeader::read` / `Ping::read` etc. pattern that the
   non-marker structs already use.

   **Decision for this phase: no, do not add `read` methods.**
   None of ryll's call sites need them, the wire format is
   trivial enough that adding them would be a 4-line copy
   per struct, and adding API surface that has no consumer
   yet is exactly the kind of speculative work the project
   guidelines warn against. Flag as future work for the
   `0.1.0` polish phase if a real consumer ever appears.

2. **Should `ryll/src/protocol/mod.rs` go away entirely?**
   With only `client.rs` left in the protocol module after
   Phase 4, it would arguably be cleaner to flatten:
   `ryll/src/protocol_client.rs` or just `ryll/src/client.rs`.
   That would let us delete `ryll/src/protocol/mod.rs`
   entirely and remove `mod protocol;` from `main.rs`.

   **Decision for this phase: no, keep the
   `protocol/{mod.rs, client.rs}` structure.** Two reasons:
   - Phase 6 will move `client.rs` into the new crate
     anyway, at which point `ryll/src/protocol/` goes away
     entirely. Doing the rename in Phase 4 just to undo it
     in Phase 6 is churn for no benefit.
   - The `crate::protocol::SpiceClient` import path that
     `app.rs` uses today keeps working with no churn if the
     module structure stays. Renaming would force a small
     update to `app.rs` for no real gain.

3. **Should the `shakenfist-spice-protocol` crate use
   feature flags?** Compression has them per-algorithm. The
   protocol crate has no obvious feature boundary — every
   consumer wants all of constants, messages, link, and
   logging.

   **Decision for this phase: no feature flags.** The
   Cargo.toml in the "Crate layout" section reflects this.
   If a future consumer wants only the constants without
   the link/handshake (e.g. a static analyser), feature
   flags can be added then.

4. **Should `link.rs` keep using `anyhow::Result` for its
   public API?** `anyhow` is convenient inside an
   application but is generally considered an
   anti-pattern in library APIs (consumers usually want
   typed errors they can match on). A `thiserror`-based
   error enum would be the textbook fix.

   **Decision for this phase: keep `anyhow::Result`.**
   Refactoring the error type is API polish that belongs in
   the `0.1.0` release work, not in the extraction. Add to
   the future-work list.

5. **Should `client.rs`'s imports use the
   `shakenfist_spice_protocol::link::*` path or pull the
   functions to the crate root via re-export?** The plan
   uses the explicit path. Alternative is to add
   `pub use link::{perform_auth, perform_link,
   SpiceStream};` to the new crate's `lib.rs`.

   **Decision for this phase: use the explicit path.** It
   makes the dependency on the link sub-module visible to
   the reader, and `link::` is short enough that the
   verbosity cost is minimal. Adding `link::*` re-exports
   would be inconsistent with the choice to expose
   `messages::*` and `logging::*` via their sub-modules
   only.

6. **What about pre-existing `pub use constants::*;` in
   `ryll/src/protocol/mod.rs`?** This is the current API
   shape that lets `app.rs` write
   `crate::protocol::ChannelType` instead of the fuller
   `crate::protocol::constants::ChannelType`. The new
   crate's `lib.rs` uses the same trick (re-exporting
   constants at the crate root) so consumers can write
   `shakenfist_spice_protocol::ChannelType` instead of
   `shakenfist_spice_protocol::constants::ChannelType`. The
   import-line churn in the 8 ryll consumer files is
   minimised by this — most existing imports just need a
   text-substitution from `crate::protocol::` to
   `shakenfist_spice_protocol::`, with no path-restructuring.

7. **Decision #1 dual-spec caveat applies again.** Phase 3
   discovered that the `{ path = "...", version = "0.1" }`
   dual-spec idiom doesn't work when the local crate is at
   `0.0.0`. Phase 4 follows the same rule: ryll's
   path-dependency on `shakenfist-spice-protocol` does
   **not** carry a `version =` qualifier. The qualifier is
   added back when the eventual `0.1.0` release lands. This
   is captured in Decision #1's "Caveat (discovered during
   Phase 3)" section already; nothing new to plan, just
   apply.

## Administration and logistics

### Success criteria

We will know Phase 4 has been successfully implemented when:

* `ryll/src/protocol/` contains only `client.rs` and a
  4-line `mod.rs`.
* `shakenfist-spice-protocol/` is a real workspace member
  with `constants.rs`, `link.rs`, `logging.rs`,
  `messages.rs`, plus a real `lib.rs`/`Cargo.toml`/`README.md`.
* The crate's `Cargo.toml` still has `version = "0.0.0"`.
  No publish has happened.
* The crate's `README.md` describes the real crate, not a
  placeholder.
* The four marker-struct message types
  (`InputsKeyModifiers`, `KeyEvent`, `MousePosition`,
  `MouseButton`) are real data-bearing structs with `pub`
  fields and `write(&self, buf)` methods.
* All 8 call sites in
  [ryll/src/channels/inputs.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs)
  use struct literal construction followed by
  `.write(&mut buf)`.
* `cargo build --workspace`, `cargo test --workspace`, and
  `cargo clippy --workspace --all-targets -- -D warnings`
  all pass.
* All 99 ryll tests pass unchanged. (97 in ryll, 2 in
  shakenfist-spice-compression, 0 in shakenfist-spice-protocol.)
* `cargo build -p shakenfist-spice-protocol` succeeds.
* `cargo build --release -p ryll`,
  `cargo deb --no-build -p ryll`, and
  `cargo generate-rpm -p ryll` still produce working
  artefacts.
* All 9 ryll consumer files (including `client.rs`)
  import from `shakenfist_spice_protocol::*` instead of
  `crate::protocol::*` for the items that moved. `app.rs`
  is the only file that still has a
  `crate::protocol::SpiceClient` import (because
  `SpiceClient` stays in ryll until Phase 6).
* CI is green on the branch after Step 2 is pushed.
* `git log --follow` on each of the four moved files shows
  unbroken history back to their original locations under
  `ryll/src/protocol/`.
* Wire format is unchanged. (No way to assert this directly
  — the test suite is the safety net.)

### Future work

* **Publish `shakenfist-spice-protocol v0.1.0`** — listed
  in the master plan's Future Work, alongside the
  compression crate's eventual publish. The polish list is
  largely shared (categories, keywords, dependency review,
  README expansion), with two protocol-specific items:
  - Replace `anyhow::Result` with a typed error enum
    (`thiserror`-based) in `link.rs`'s public API.
  - Consider adding `read` methods to the input message
    types (`InputsKeyModifiers`, `KeyEvent`,
    `MousePosition`, `MouseButton`) for symmetry with the
    other message types and for future protocol-analysis
    tooling.
* **Move `SpiceClient` into the protocol crate (Phase 6)**
  — already in the master plan execution table. Requires
  the `ConnectionConfig` refactor first.
* **Fuzz-test the wire-format parsers** — once the
  protocol crate has stable input/output boundaries,
  every `MessageHeader::read`, `MainInit::read`,
  `Ping::read`, etc. is an ideal `cargo-fuzz` target.
* **Audit `ChannelType::name()` and similar enum helpers**
  for whether they should be `Display` impls instead of
  `name() -> &'static str` methods. API polish for
  `0.1.0`.

### Bugs fixed during this work

(None expected. Phase 4 is purely a refactor + a small API
cleanup that preserves wire format. If a bug is discovered
during execution, record it here and fix it in a separate
commit before Step 2 lands.)

### Discoveries during execution

* **Inline fully qualified paths missed by the initial grep.**
  The import-line grep for `use crate::protocol` found 26
  import lines across 8 files, but missed 5 inline fully
  qualified paths in `inputs.rs` (`crate::protocol::mouse_buttons::LEFT`
  etc.) inside a `match` expression. These surfaced as compile
  errors after the first build attempt. Fixed by switching
  them to `shakenfist_spice_protocol::mouse_buttons::*`.
  Lesson for Phase 5: grep for both `use crate::protocol`
  *and* `crate::protocol::` (without `use`) to catch inline
  qualified paths.

* **Rustfmt re-ordered imports in multiple channel files.**
  After switching `crate::protocol::*` to
  `shakenfist_spice_protocol::*`, rustfmt moved the new
  external-crate imports after the `crate::*` imports in the
  import block (consistent with its group-ordering rule:
  `std::` first, then external crates, then `crate::*`).
  This was expected from the Phase 3 experience but affected
  more files this time (6 channel handlers).

* **All four moved files registered as 100% renames.** Unlike
  Phase 3 (where `glz.rs`, `lz.rs`, `lz4.rs` were 99% due
  to the `super::DecompressedImage` → `crate::DecompressedImage`
  change), the protocol files had no internal import changes
  needed — `link.rs`'s `use super::constants::*` still
  resolves correctly because `super` from a lib.rs-declared
  module is the crate root. This means `git log --follow`
  works perfectly for all four files.

* **The `mouse_buttons` and `keyboard_modifiers` const
  modules needed to be in the crate root re-exports.** The
  lib.rs re-export list had to include these alongside
  `ChannelType`, `ImageType`, etc. so that ryll consumers
  could write `shakenfist_spice_protocol::mouse_buttons::LEFT`
  without going through `constants::`. This matched the
  existing `pub use constants::*;` pattern that ryll's old
  `protocol/mod.rs` had.

* **`SpiceLinkMess::new` confirmed fine as-is.** The 5-arg
  constructor was flagged as "minor" in the master plan's
  open questions. During execution, all five args have
  distinct types (`u32`, `ChannelType`, `u8`, `u32`, `u32`)
  and the callers are all clear about what they're passing.
  No refactor needed.

* **No tests to migrate.** Confirmed: none of the four
  extracted files had `#[test]` functions. Test count stayed
  at 97 ryll + 2 compression + 0 protocol = 99 total.

### Back brief

Before executing any step of this plan, back brief the
operator as to your understanding of the plan. Specifically
confirm:

1. The three-commit sequence: (Step 1) marker-struct
   refactor inside ryll, (Step 2) cross-crate extraction of
   the four protocol files, (Step 3) status update.
2. That `client.rs` **stays in ryll** and is only updated
   with new import paths, not moved into the new crate.
3. That the wire format must not change — the four
   refactored marker structs must produce the same bytes as
   before.
4. That ryll's path-dependency on the new crate does
   **not** carry a `version =` qualifier (per the Phase 3
   dual-spec caveat in Decision #1).
5. That `ryll/src/protocol/mod.rs` is slimmed but not
   deleted, and that `mod protocol;` in `main.rs` stays.
6. The pre-flight checks will be run, and any failure will
   stop execution rather than being worked around.
