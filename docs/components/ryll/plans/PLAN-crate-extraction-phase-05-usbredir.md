# Phase 5: Extract shakenfist-spice-usbredir

## Prompt

Before executing any step of this plan, back brief the
operator. This is the simplest of the three crate extractions
— the usbredir module has zero ryll-internal coupling, a
clean API that needs no refactoring, and the fewest consumer
files. The work is a single extraction commit (unlike Phases 3
and 4 which each had prerequisite commits for LZ4 relocation
and marker-struct refactoring respectively).

I prefer one commit per logical change. Each commit must leave
the tree in a working state.

## Situation

After Phases 1-4 (most recently `9a409fe` and `2d3e5a8`), the
repository is a four-member Cargo workspace:

```
ryll-repo/
├── Cargo.toml                         # workspace manifest
├── ryll/
│   └── src/
│       ├── usbredir/                  # the target of Phase 5
│       │   ├── mod.rs                 # 9 lines
│       │   ├── constants.rs           # 150 lines
│       │   ├── messages.rs            # 804 lines
│       │   └── parser.rs              # 349 lines
│       ├── channels/usbredir.rs       # the *channel handler*
│       │                              #   (stays in ryll)
│       ├── usb/{mod,real,virtual_msc}.rs  # USB backends
│       │                              #   (consume usbredir types)
│       └── ...
├── shakenfist-spice-compression/      # real crate (Phase 3)
├── shakenfist-spice-protocol/         # real crate (Phase 4)
└── shakenfist-spice-usbredir/         # v0.0.0 placeholder
```

**Important naming distinction**: `ryll/src/usbredir/` is the
*protocol parser and types module* (what we're extracting).
`ryll/src/channels/usbredir.rs` is the *channel handler* that
uses the protocol module (stays in ryll). Both exist because
SPICE's usbredir channel uses the usbredir wire protocol. The
`mod usbredir;` at [ryll/src/main.rs:26](https://github.com/shakenfist/ryll/blob/develop/ryll/src/main.rs#L26)
declares the protocol module (the extraction target). The
`pub mod usbredir;` at
[ryll/src/channels/mod.rs:5](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/mod.rs#L5)
declares the channel handler (not touched by Phase 5).

**Current state of the usbredir code** (from the Phase 5
research pass):

* [ryll/src/usbredir/mod.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/usbredir/mod.rs)
  (9 lines): declares `pub mod constants`, `pub mod messages`,
  `pub mod parser`. No re-exports, no type definitions.

* [ryll/src/usbredir/constants.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/usbredir/constants.rs)
  (150 lines): `pub mod msg_type` (35 message-type constants),
  `pub mod cap` (8 capability constants), `pub const RYLL_CAPS`,
  `pub enum Status`, `pub enum UsbSpeed`, `pub enum EpType`,
  `pub fn msg_type_name()`. Pure constants and enums, no
  external crate imports (only stdlib). Has
  `#![allow(dead_code)]` at the top.

* [ryll/src/usbredir/messages.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/usbredir/messages.rs)
  (804 lines): 15 message struct types (each with
  `pub const SIZE`, `pub fn read()`, `pub fn write()`),
  `pub fn make_usbredir_message()`, `pub struct UsbredirMessage`,
  `pub enum UsbredirPayload` with 21 variants. Uses
  `byteorder::{LittleEndian, ReadBytesExt, WriteBytesExt}`.
  Internal `use super::constants::msg_type` for dispatch.

* [ryll/src/usbredir/parser.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/usbredir/parser.rs)
  (349 lines): `pub struct UsbredirParser` with `pub fn new()`,
  `pub fn feed()`, `pub fn next_message()`. Uses
  `anyhow::Result`. Internal `use super::constants::msg_type`
  and `use super::messages::*`.

**Coupling**: zero `use crate::*` imports in any file. The
module's only dependencies are `byteorder`, `anyhow`, and
`std::io`.

**Tests**: messages.rs and parser.rs contain unit tests — the
research found ~17 test functions total, all pure input/output
with no ryll-specific mocking. Exact counts to be verified
during pre-flight (the research report was inconsistent on the
number). These tests migrate with their files to the new crate.

**Consumer files** that import from `crate::usbredir::*`:

| File | Imports |
|---|---|
| [ryll/src/channels/usbredir.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/usbredir.rs) | `crate::usbredir::constants::{self, msg_type, msg_type_name, Status, RYLL_CAPS}`, `crate::usbredir::messages::{...}`, `crate::usbredir::parser::UsbredirParser` |
| [ryll/src/usb/mod.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/usb/mod.rs) | `crate::usbredir::constants::Status`, `crate::usbredir::messages::{DeviceConnect, EpInfo, InterfaceInfo}` |
| [ryll/src/usb/real.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/usb/real.rs) | `crate::usbredir::constants::Status`, `crate::usbredir::messages::{DeviceConnect, EpInfo, InterfaceInfo}` |
| [ryll/src/usb/virtual_msc.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/usb/virtual_msc.rs) | (likely same pattern as real.rs — verify during execution) |

**Inline qualified paths** (lesson from Phase 4): the research
found two inline `crate::usbredir::messages::InterruptReceivingStatus { ... }`
struct instantiations at `channels/usbredir.rs:595` and `:611`.
These must be caught alongside the `use` statement updates.

## Mission and problem statement

Replace the Phase 2 `shakenfist-spice-usbredir v0.0.0`
placeholder with a real crate containing the usbredir protocol
parser, message types, and constants. Switch ryll's 3-4
consumer files to import from the new crate.

After this phase:

- The `shakenfist-spice-usbredir` crate exists at
  `shakenfist-spice-usbredir/` as a real workspace member.
- `ryll/src/usbredir/` is deleted.
- The `mod usbredir;` declaration in `ryll/src/main.rs` is
  removed.
- All consumer files (`channels/usbredir.rs`, `usb/mod.rs`,
  `usb/real.rs`, and `usb/virtual_msc.rs` if applicable)
  import from `shakenfist_spice_usbredir::*`.
- All existing ryll tests continue to pass. The usbredir
  unit tests migrate to the new crate. Total test count stays
  at 99.
- The crate is at version `0.0.0`. No publish happens.
- Per Decision #1's caveat, ryll's path-dep has no `version =`
  qualifier.

## Approach

This phase is a single extraction commit plus bookkeeping. No
prerequisite commit is needed because the usbredir module's API
is already clean (confirmed by the research pass — no marker
structs, no positional-arg issues, no file relocations needed
before extraction).

1. **Extraction commit**: `git mv` the three files
   (`constants.rs`, `messages.rs`, `parser.rs`) from
   `ryll/src/usbredir/` into
   `shakenfist-spice-usbredir/src/`. Delete `mod.rs`. Rewrite
   the placeholder `Cargo.toml`/`lib.rs`/`README.md`. Update
   the 3-4 consumer files. Remove `mod usbredir;` from
   `main.rs`. Add the path-dep to `ryll/Cargo.toml`.

2. **Status update commit**: mark Phase 5 complete, record
   discoveries.

## Pre-flight verification

1. **Working tree is clean** on `display-iteration`.
2. **CI is green** on the most recent push (or Phase 4's
   commits are pushed and verified).
3. **All 99 tests pass** via `make test`.
4. **`cargo clippy --workspace --all-targets -- -D warnings`
   passes**.
5. **The usbredir module structure matches the research**:
   `ls ryll/src/usbredir/` shows exactly `constants.rs`,
   `messages.rs`, `mod.rs`, `parser.rs`.
6. **Count usbredir tests**: `grep -c '#\[test\]'
   ryll/src/usbredir/*.rs` — record the exact counts for
   post-extraction verification.
7. **Grep for ALL consumer references**: both
   `grep -rn 'use crate::usbredir' ryll/src/` AND
   `grep -rn 'crate::usbredir::' ryll/src/ | grep -v '^.*:use '`
   to catch inline qualified paths (Phase 4 lesson).

## Crate layout

### `shakenfist-spice-usbredir/Cargo.toml`

```toml
[package]
name = "shakenfist-spice-usbredir"
version = "0.0.0"
edition.workspace = true
license.workspace = true
authors.workspace = true
repository.workspace = true
description = "Pure-Rust parser and message types for the SPICE USB redirection (usbredir) protocol, extracted from the ryll SPICE client."
readme = "README.md"

[dependencies]
anyhow = "1"
byteorder = "1"
```

No `tokio`, no `tracing`, no feature flags. The simplest
`Cargo.toml` of the three extracted crates.

### `shakenfist-spice-usbredir/src/lib.rs`

```rust
//! Pure-Rust parser and message types for the SPICE USB
//! redirection (usbredir) protocol, suitable for clients,
//! proxies, and protocol analysis tools.
//!
//! - [`constants`] — message types, capabilities, status
//!   codes, USB speed and endpoint-type enums.
//! - [`messages`] — wire-format struct definitions with
//!   `read` and `write` methods for every usbredir message
//!   type, plus `UsbredirMessage` / `UsbredirPayload` for
//!   parsed message dispatch.
//! - [`parser`] — `UsbredirParser`, a byte-stream parser
//!   that accumulates data and yields complete
//!   `UsbredirMessage` values.
//!
//! Extracted from the
//! [ryll](https://github.com/shakenfist/ryll) SPICE client.

pub mod constants;
pub mod messages;
pub mod parser;
```

No crate-root re-exports needed — the three sub-modules are
the natural API surface, and consumers already access them via
`usbredir::constants::*`, `usbredir::messages::*`,
`usbredir::parser::*`.

### `ryll/Cargo.toml` change

```toml
# Extracted SPICE USB redirection protocol types (same
# dual-spec caveat: no `version` qualifier until 0.1.0).
shakenfist-spice-usbredir = { path = "../shakenfist-spice-usbredir" }
```

### `ryll/src/main.rs` change

Remove `mod usbredir;` at line 26. The `mod usb;` declaration
(which is the USB device backend, not the usbredir protocol)
stays.

### Consumer file import updates

Each `use crate::usbredir::*` becomes
`use shakenfist_spice_usbredir::*`. The two inline qualified
paths at `channels/usbredir.rs:595` and `:611` become
`shakenfist_spice_usbredir::messages::InterruptReceivingStatus { ... }`.

### Internal `super::*` references in the moved files

After the move, `messages.rs` and `parser.rs` use
`use super::constants::*` and `use super::messages::*`
respectively. Since `super` from a lib.rs-declared module
resolves to the crate root, these still compile correctly in
the new crate (same as Phase 4's `link.rs` experience). No
changes needed.

## Execution

### Step 1: Extract shakenfist-spice-usbredir

1. `git mv` the three files:
   ```sh
   git mv ryll/src/usbredir/constants.rs shakenfist-spice-usbredir/src/constants.rs
   git mv ryll/src/usbredir/messages.rs  shakenfist-spice-usbredir/src/messages.rs
   git mv ryll/src/usbredir/parser.rs    shakenfist-spice-usbredir/src/parser.rs
   ```
2. Delete `ryll/src/usbredir/mod.rs` (`git rm`).
3. Write the new `Cargo.toml`, `lib.rs`, `README.md`.
4. Add the path-dep to `ryll/Cargo.toml`.
5. Remove `mod usbredir;` from `ryll/src/main.rs`.
6. Update the 3-4 consumer files: change all
   `use crate::usbredir::*` imports to
   `use shakenfist_spice_usbredir::*`, and fix the 2 inline
   qualified paths.
7. Verify locally:
   - `cargo fmt --all --check`
   - `cargo clippy --workspace --all-targets -- -D warnings`
   - `cargo build --workspace`
   - `cargo test --workspace` — total must still be 99.
     The per-crate split will shift (ryll loses the usbredir
     tests; the new crate gains them).
   - `cargo build -p shakenfist-spice-usbredir` succeeds.
   - `cargo build --release -p ryll`, `cargo deb`, `cargo
     generate-rpm` all still work.
   - `pre-commit run --all-files`
8. Inspect `git status` — expected shape: 3 file renames,
   1 deleted (`mod.rs`), `Cargo.lock` updated, `ryll/Cargo.toml`
   modified, `ryll/src/main.rs` -1 line, 3-4 consumer files
   modified, plus the three rewritten placeholder files.
9. Commit.

### Step 2: Mark Phase 5 complete

Update the master plan's execution table and record execution
discoveries. Commit.

## Open questions

1. **Does `usb/virtual_msc.rs` import from
   `crate::usbredir`?** The research showed `usb/mod.rs` and
   `usb/real.rs` do; `virtual_msc.rs` likely follows the same
   pattern but wasn't explicitly confirmed. Verify during
   execution with the grep in pre-flight step 7.

2. **Exact test count.** The research report was inconsistent
   (said "14" at the summary but listed 17 test names). The
   exact count doesn't matter for the plan — just verify
   during pre-flight that the post-extraction total is 99.

## Administration and logistics

### Success criteria

* `ryll/src/usbredir/` no longer exists.
* `shakenfist-spice-usbredir/` is a real workspace member.
* The crate's `Cargo.toml` still has `version = "0.0.0"`.
* All 99 ryll tests pass. The usbredir unit tests now appear
  under `shakenfist_spice_usbredir` in the test output.
* `cargo build -p shakenfist-spice-usbredir` succeeds.
* `git log --follow` on each file traces back to its original
  location.
* CI is green.

### Future work

* **Publish `shakenfist-spice-usbredir v0.1.0`** — same
  future-work item as the other two crates.
* **Clean up `#![allow(dead_code)]` in constants.rs** —
  the allow may be hiding unused constants that should be
  removed or documented. API polish for `0.1.0`.
* **Add a `README.md` usage example** showing
  `UsbredirParser::feed()` + `next_message()` loop.

### Bugs fixed during this work

(None expected.)

### Discoveries during execution

* **Open Question #1 answered: yes, `usb/virtual_msc.rs`
  imports from `crate::usbredir`.** Same pattern as
  `usb/mod.rs` and `usb/real.rs`: `Status`, `DeviceConnect`,
  `EpInfo`, `InterfaceInfo`. Four consumer files total, as the
  pre-flight grep confirmed.

* **Open Question #2 answered: 17 tests.** 10 in `messages.rs`,
  7 in `parser.rs`. The research report's summary said "14"
  but listed 17 names — the test function count was correct at
  17. Post-extraction: 80 ryll + 2 compression + 17 usbredir +
  0 protocol = 99 total.

* **Clippy `new_without_default` lint surfaced on
  `UsbredirParser`.** When the usbredir code was part of ryll,
  this lint was either suppressed or not triggered (possibly
  because the crate-level `#![allow(dead_code)]` in
  `constants.rs` masked it, or because the lint only fires on
  public items in a standalone crate). Once the code became its
  own crate, clippy flagged `UsbredirParser::new()` as needing
  a `Default` impl. Fixed by adding `impl Default for
  UsbredirParser` that delegates to `Self::new()`.

* **No inline qualified paths beyond the two already known.**
  The pre-flight grep for `crate::usbredir::` (without `use`)
  found exactly the two `InterruptReceivingStatus` struct
  instantiations at `channels/usbredir.rs:595` and `:611`.
  No surprises beyond what the research pass predicted.

* **All three moved files registered as proper git renames.**
  `constants.rs` and `messages.rs` at 100%; `parser.rs` at
  99% because of the added `Default` impl (6 lines). `git log
  --follow` traces all three files back to their original
  `ryll/src/usbredir/` locations.

### Back brief

Before executing, confirm:

1. Single extraction commit (no prerequisite).
2. `mod usbredir;` in `main.rs` is removed; `pub mod usbredir;`
   in `channels/mod.rs` is NOT touched (different module).
3. Both `use crate::usbredir` import lines AND inline
   `crate::usbredir::` qualified paths will be caught.
4. No `version =` qualifier on the path-dep.
5. Test count stays at 99 after the per-crate split.
