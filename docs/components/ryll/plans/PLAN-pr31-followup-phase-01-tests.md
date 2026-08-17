# Phase 1 — PR 31 follow-up: regression tests

Parent: [PLAN-pr31-followup.md](/components/ryll/plans/PLAN-pr31-followup/).

## Goal

Add two narrow regression tests so the bugs that were
fixed during the PR 31 merge cannot silently come back:

1. `VD_AGENT_ANNOUNCE_CAPABILITIES` is `6`, not `1` (the
   value that collided with `VD_AGENT_MOUSE_STATE`).
2. `MOUSE_MODE_REQUEST` body is exactly two
   little-endian bytes — the wire format `spice.proto`
   declares as `flags16` — not the four bytes the
   pre-merge code shipped.

Both bugs were one-line edits with one-line reverts, so
both deserve a one-line guard.

No production behaviour changes in this phase. Test-only
plus a tiny encoding helper extracted from
`maybe_request_client_mouse_mode` so the encoding can be
asserted without constructing a `MainChannel`.

## Scope

In scope:

- A new `VD_AGENT_*` regression test in the existing
  `tests` module of `ryll/src/channels/main_channel.rs`.
- A new `build_mouse_mode_request_payload` helper
  function (private to `main_channel.rs`) that owns the
  two-byte encoding.
- Refactor of `maybe_request_client_mouse_mode` to call
  the helper instead of inlining the `write_u16`.
- Two new `MOUSE_MODE_REQUEST` encoding tests in the
  same `tests` module.

Out of scope (other phases / future PRs):

- Promoting the file-private `VD_AGENT_*` constants to
  the `shakenfist-spice-protocol` crate (the master plan
  originally suggested this; deferred — see "Why not
  promote the constants" below).
- README and ARCHITECTURE updates (Phase 2).
- The polish items (cancellation token for `reconnect()`,
  cursor-hide overlap, clipboard CRLF, log-line three-arm
  match) (Phase 3+).

## Grounding — what's there today

### VD_AGENT_* constants

The constants are file-private in
[`ryll/src/channels/main_channel.rs:50-74`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/main_channel.rs#L50-L74),
with a comment that they "must match
`spice-protocol/spice/vd_agent.h`":

```rust
const VD_AGENT_PROTOCOL: u32 = 1;

// VDAgentMessage type values — must match spice-protocol/spice/vd_agent.h
#[allow(dead_code)]
const VD_AGENT_MOUSE_STATE: u32 = 1;
const VD_AGENT_MONITORS_CONFIG: u32 = 2;
#[allow(dead_code)]
const VD_AGENT_REPLY: u32 = 3;
const VD_AGENT_CLIPBOARD: u32 = 4;
#[allow(dead_code)]
const VD_AGENT_DISPLAY_CONFIG: u32 = 5;
const VD_AGENT_ANNOUNCE_CAPABILITIES: u32 = 6;
const VD_AGENT_CLIPBOARD_GRAB: u32 = 7;
const VD_AGENT_CLIPBOARD_REQUEST: u32 = 8;
const VD_AGENT_CLIPBOARD_RELEASE: u32 = 9;
```

The original PR 31 bug was `VD_AGENT_ANNOUNCE_CAPABILITIES = 1`,
which made the announcement message indistinguishable
from `VD_AGENT_MOUSE_STATE` on the wire — the server
would dispatch our capabilities packet to its mouse-state
handler.

Note that `VD_AGENT_PROTOCOL = 1` and
`VD_AGENT_MOUSE_STATE = 1` are *not* a conflict; they
are in different namespaces (protocol-version constant
versus message-type discriminant). The collision the
test must guard is between the message-type discriminants
only.

### MOUSE_MODE_REQUEST encoding

Today, encoding is inline in
[`maybe_request_client_mouse_mode`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/main_channel.rs#L658-L693)
at lines ~679-686:

```rust
info!("main: requesting client mouse mode");
// SpiceMsgcMainMouseModeRequest: spice.proto declares
// mouse_mode as flags16, so the body is one little-endian
// u16. Writing u32 here ships two extra zero bytes, which
// some servers tolerate and others reject as a malformed
// request — matching MOUSE_MODE on the read side
// (parse_mouse_mode_payload) which is already u16-aware.
let mut mode_payload = Vec::new();
mode_payload.write_u16::<LittleEndian>(MOUSE_MODE_CLIENT as u16)?;
let msg = make_message(main_client::MOUSE_MODE_REQUEST, &mode_payload);
```

The existing tests module
([`ryll/src/channels/main_channel.rs:990-1047`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/main_channel.rs#L990-L1047))
already covers `parse_mouse_mode_payload` and
`should_request_client_mouse_mode` — those landed with
develop's `10f19477`. The write side has no coverage;
this phase fills that gap.

### Why not promote the constants

The master plan suggested moving the regression test
into `shakenfist-spice-protocol/src/constants.rs`. That
implies promoting the `VD_AGENT_*` constants to the
shared crate. We're not doing that here for two reasons:

- The constants are still under active churn (PR 31 just
  added one); promoting them widens the change surface
  for a phase that should be low-risk.
- The test only needs to guarantee internal consistency,
  not a public API contract. A test next to the
  constants reads naturally and gives the same regression
  guard.

If a future phase needs them in the shared crate (for
example if a second client wants to reuse them), the
promotion can land then with this regression test moving
alongside.

## Design

### Test 1 — `vd_agent_constants_match_spice_protocol`

A single `#[test]` in the existing `mod tests` block
that asserts each `VD_AGENT_*` message-type discriminant
holds the spec value and, separately, that
`VD_AGENT_ANNOUNCE_CAPABILITIES != VD_AGENT_MOUSE_STATE`.

The `!=` assertion is redundant given the per-constant
asserts — if both equal their spec values, they cannot
collide — but it documents the original bug at the assert
site and gives a clear message if a future edit tries
the broken value again.

```rust
#[test]
fn vd_agent_constants_match_spice_protocol() {
    // Values from spice-protocol/spice/vd_agent.h
    // (VDAgentMessage type discriminants).
    assert_eq!(VD_AGENT_MOUSE_STATE, 1);
    assert_eq!(VD_AGENT_MONITORS_CONFIG, 2);
    assert_eq!(VD_AGENT_REPLY, 3);
    assert_eq!(VD_AGENT_CLIPBOARD, 4);
    assert_eq!(VD_AGENT_DISPLAY_CONFIG, 5);
    assert_eq!(VD_AGENT_ANNOUNCE_CAPABILITIES, 6);
    assert_eq!(VD_AGENT_CLIPBOARD_GRAB, 7);
    assert_eq!(VD_AGENT_CLIPBOARD_REQUEST, 8);
    assert_eq!(VD_AGENT_CLIPBOARD_RELEASE, 9);

    // Regression for PR 31: ANNOUNCE_CAPABILITIES used
    // to be 1, which collided with VD_AGENT_MOUSE_STATE.
    // Server would dispatch our capabilities announcement
    // to its mouse-state handler.
    assert_ne!(
        VD_AGENT_ANNOUNCE_CAPABILITIES, VD_AGENT_MOUSE_STATE,
        "ANNOUNCE_CAPABILITIES (6) must not collide with MOUSE_STATE (1)"
    );
}
```

The constants are module-private but the `tests` module
is `mod tests { use super::...; }` so visibility is
already correct.

### Test 2 — `MOUSE_MODE_REQUEST` body shape

Direct testing requires either constructing a
`MainChannel` (which owns sockets, channels, runtimes —
heavy) or a fake transport. Neither is justified for a
single-byte regression. Instead, extract a small pure
helper:

```rust
/// Build the body of a SPICE_MSGC_MAIN_MOUSE_MODE_REQUEST.
///
/// `spice.proto` declares `mouse_mode` as `flags16`, so
/// the body is one little-endian `u16`. See the read
/// side at `parse_mouse_mode_payload`.
fn build_mouse_mode_request_payload(mode: u32) -> Vec<u8> {
    let mut payload = Vec::with_capacity(2);
    // Vec writes never fail; unwrap is safe.
    payload
        .write_u16::<LittleEndian>(mode as u16)
        .expect("Vec write should not fail");
    payload
}
```

Then `maybe_request_client_mouse_mode` becomes:

```rust
let mode_payload = build_mouse_mode_request_payload(MOUSE_MODE_CLIENT);
let msg = make_message(main_client::MOUSE_MODE_REQUEST, &mode_payload);
```

The existing inline comment about flags16 moves onto
the helper's doc-comment so it lives next to the
encoding decision rather than the call site.

Tests:

```rust
#[test]
fn mouse_mode_request_payload_is_two_bytes_for_client() {
    // Regression for PR 31 blocking #3: the body is
    // flags16 (one little-endian u16), not u32. Writing
    // u32 here shipped two extra zero bytes that some
    // servers reject as malformed.
    assert_eq!(
        build_mouse_mode_request_payload(MOUSE_MODE_CLIENT),
        vec![0x02, 0x00],
    );
}

#[test]
fn mouse_mode_request_payload_is_two_bytes_for_server() {
    // Same shape regardless of which mode we ask for —
    // belt-and-braces against a future "let's encode
    // the supported_modes mask too" temptation that
    // would re-widen the body.
    assert_eq!(
        build_mouse_mode_request_payload(MOUSE_MODE_SERVER),
        vec![0x01, 0x00],
    );
}
```

The asserted byte vectors are deliberate: they catch
both a width regression (4 bytes vs 2) and an endianness
regression (would fail if someone swapped `LittleEndian`
for `BigEndian`).

The helper takes `u32` to match the `MOUSE_MODE_*`
constants in the protocol crate, even though the body is
narrower. Using `u32` at the API boundary and casting
inside the helper keeps the `as u16` cast in one place
where the comment can explain it.

## Implementation steps

1. **Add the `build_mouse_mode_request_payload` helper.**
   Place it in `ryll/src/channels/main_channel.rs`
   immediately above
   `maybe_request_client_mouse_mode` so the call site is
   adjacent to the definition.

2. **Update `maybe_request_client_mouse_mode` to use the
   helper.** Replace the three lines that build
   `mode_payload` with one call to the helper. Move the
   flags16 explanation comment off the call site and
   onto the helper's doc-comment.

3. **Add the `tests` module imports for new symbols.**
   The existing `use super::{...}` line needs
   `build_mouse_mode_request_payload` added; the
   `VD_AGENT_*` constants are already in scope via the
   module-level `use super::*`-equivalent (current
   module uses an explicit `use super::{...}` import —
   add the constants).

4. **Add `vd_agent_constants_match_spice_protocol`.**
   Append to the existing `mod tests` block.

5. **Add the two `mouse_mode_request_payload_*` tests.**
   Append to the existing `mod tests` block.

6. **Run the full test suite.** `make test` must show
   all 304 tests passing (current 301 plus three
   new tests).

7. **Run `make lint` and `pre-commit run --all-files`.**
   Both must pass with zero warnings or errors.

## Acceptance criteria

- `make build` clean.
- `make test` reports `301 + 3 = 304` tests passing,
  zero failures.
- `make lint` reports zero warnings or errors with
  `-D warnings`.
- `pre-commit run --all-files` reports all hooks
  passed.
- The diff is small: one new helper (~6 lines including
  doc-comment), one call-site change (3 lines → 1
  line + comment removal), three new test functions
  (~25 lines including comments), and the new `use`
  imports for the test module. No file outside
  `ryll/src/channels/main_channel.rs` should change.

## Verification of regressions caught

To convince ourselves the tests would actually fail on
the bugs they guard, before committing the final state
(but after writing the tests):

1. Temporarily revert `MOUSE_MODE_CLIENT as u16` back to
   `MOUSE_MODE_CLIENT` with `write_u32`, run `make test`,
   confirm `mouse_mode_request_payload_is_two_bytes_for_client`
   fails with the expected `[0x02, 0x00, 0x00, 0x00]
   != [0x02, 0x00]` message. Restore the fix.
2. Temporarily change `VD_AGENT_ANNOUNCE_CAPABILITIES`
   from `6` to `1`, run `make test`, confirm
   `vd_agent_constants_match_spice_protocol` fails on
   the `assert_eq!(VD_AGENT_ANNOUNCE_CAPABILITIES, 6)`
   line. Restore the value.

Both checks happen during development only; the
committed tree has the correct values and no temporary
edits.

## Out of scope — explicitly

The master plan called this work "Should fix items 1-2".
Other items in that section (README updates, ARCHITECTURE
updates) are Phase 2 and do not appear here. If a Phase 2
implementation later wants to land docs alongside these
tests, that is a Phase 2 decision; this phase intentionally
ships tests only so the diff stays trivially reviewable.

## Update of master plan on completion

When this phase merges to develop, the corresponding two
items at the top of the "Should fix" section in
[PLAN-pr31-followup.md](/components/ryll/plans/PLAN-pr31-followup/) are struck
through (`~~text~~`) with a one-line note pointing to the
landed PR or commit, matching the convention in
PLAN-pr23-followup.md and PLAN-supply-chain-followups.md.
The "Administration > Tracking" suggestion to bundle items
1-2 into one PR has now been honoured; the same paragraph
should remain to guide the Phase 2 docs PR.
