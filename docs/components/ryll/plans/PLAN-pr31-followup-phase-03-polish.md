# Phase 3 — PR 31 follow-up: polish items

Parent: [PLAN-pr31-followup.md](/components/ryll/plans/PLAN-pr31-followup/).

## Goal

Land the three independent polish items from the
"Should consider" section of the master plan that
remain real after the second-rebase merge:

1. Cancel the previous reconnect attempt before
   spawning the next, so spamming Reconnect against an
   unresponsive server cannot accumulate threads and
   sockets (item 6).
2. Reveal the host cursor when an overlay (popup,
   tooltip, side panel, dialog) is consuming pointer
   input over the SPICE surface (item 7).
3. Stop the host clipboard from ping-ponging when
   line endings or trailing whitespace are mangled by
   round-tripping through Windows or some Wayland
   compositors (item 8).

Item 9 (the MOUSE_MODE log-line three-arm match) was
flagged "verify still relevant" by the master plan and
turns out to be moot — the second-rebase merge took
develop's INIT and MOUSE_MODE handlers, both of which
already use three-arm matches. This phase records it as
closed without code changes.

Each live item is independently shippable. Land them as
three separate commits — possibly three separate PRs —
in any order. The plan groups them because they share
the same context (PR 31 follow-up) and the same
acceptance harness (`make build && make test &&
make lint && pre-commit run --all-files`).

## Scope

In scope:

- A per-connection cancel signal threaded through
  `RyllApp::reconnect`, `RyllApp::new`, and
  `run_connection`. Implementation uses
  `Arc<AtomicBool>` to match the existing
  `SHUTDOWN_REQUESTED` cooperative-cancel pattern; no
  new dependencies.
- A one-line predicate addition to the cursor-hide
  block in `app.rs` so an active overlay reveals the
  host cursor.
- A normalisation pass and hash-based dedup for the
  host-clipboard echo guard, replacing the raw-text
  comparison.
- A formal close-out note for item 9 in the master
  plan, with a one-line pointer to develop's
  `10f19477` (which absorbed the wholesale-replaced
  log lines).

Out of scope (future PRs / not part of this phase):

- Any further work on the master plan beyond items
  6-9. After this phase, the master plan goes to
  Complete.
- The pure regression-test gates from Phase 1 already
  cover the wire format and the announce-capabilities
  collision; this phase does not add further tests
  beyond the small unit additions each fix wants for
  itself (see per-item Acceptance criteria below).
- Replacing `std::thread::spawn` + per-call
  `tokio::Runtime` with a single shared runtime owned
  by `RyllApp`. That is a larger architectural move
  and not what the master plan called for; the
  cancel-signal fix solves the practical problem
  (thread accumulation) without the refactor.

## Grounding — what's there today

### Item 6: reconnect cancellation

`RyllApp::reconnect` at
[`ryll/src/app.rs:634-751`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L634-L751)
spawns a fresh `std::thread::spawn` containing a fresh
`tokio::runtime::Runtime`, calls
`run_connection(...).await` inside `block_on`, and
returns. The previous attempt's runtime is not signalled
in any way. It collapses only when:

- The previous `run_connection` returns (because the
  main channel finally disconnects, the connection
  task completes, or `SHUTDOWN_REQUESTED` fires).
- Or the runtime is dropped by the thread exiting.

`run_connection`'s select loop is at
[`ryll/src/app.rs:3443-3525`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L3443-L3525).
Three branches today: event channel, connection task
join, and a 100 ms `tokio::time::sleep` whose body polls
`SHUTDOWN_REQUESTED`. The 100 ms branch is the natural
home for a per-connection cancel poll — same
cooperative-cancel pattern, scoped to a single attempt
instead of process-wide.

### Item 7: cursor-hide overlay handling

The cursor-hide predicate is at
[`ryll/src/app.rs:2984-2994`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2984-L2994):

```rust
if self.cursor_image.is_some()
    && !self.region_select_active
    && self.surface_rect != egui::Rect::NOTHING
    && ctx.input(|i| {
        i.pointer
            .hover_pos()
            .is_some_and(|p| self.surface_rect.contains(p))
    })
{
    ctx.output_mut(|o| o.cursor_icon = egui::CursorIcon::None);
}
```

`surface_rect.contains(p)` is true whenever the pointer
is geometrically over the surface. It is also true when
a popup window or floating dialog is layered over that
part of the surface — egui's overlay does not reshape
the underlying surface_rect. So the host cursor stays
hidden even when the user is supposed to be interacting
with the overlay.

egui exposes `Context::wants_pointer_input()`, which is
true on any frame where an egui widget is consuming
pointer events (drag, click, hover into an interactive
widget). Adding `!ctx.wants_pointer_input()` to the
AND chain reveals the host cursor on those frames and
keeps it hidden when nothing else wants the pointer.

### Item 8: clipboard CRLF / LF normalisation

`MainChannel::poll_host_clipboard` at
[`ryll/src/channels/main_channel.rs:923-944`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/main_channel.rs#L923-L944)
compares the freshly read host clipboard against
`self.last_clipboard_text` (Option<String>):

```rust
let changed = match &self.last_clipboard_text {
    Some(prev) => prev != &text,
    None => true,
};
```

The matching write path at line 879 stores the raw
text we just sent to the host clipboard:

```rust
self.last_clipboard_text = Some(text);
```

On round-trip through Windows or some Wayland
compositors:

- LF (`\n`) gets converted to CRLF (`\r\n`).
- Trailing whitespace gets trimmed or appended
  inconsistently.

The next poll sees a "different" string and re-grabs,
which fires `send_clipboard_grab` to the guest, the
guest re-sends, the round trip mangles again, and the
guest's clipboard ping-pongs.

Storing a hash of normalised text fixes both problems:
the comparison is invariant under line-ending and
trailing-whitespace munging, and the in-memory copy of
the clipboard contents goes away (small privacy win
since clipboard content can include passwords).

### Item 9: MOUSE_MODE log-line three-arm match

The master plan called this out at the MOUSE_MODE
handler, with a "verify still relevant" caveat because
the second-rebase merge had replaced parts of that
handler wholesale. Verification:
[`ryll/src/channels/main_channel.rs:371-378`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/main_channel.rs#L371-L378)
(INIT) and
[`ryll/src/channels/main_channel.rs:411-415`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/main_channel.rs#L411-L415)
(MOUSE_MODE) both use three-arm matches:

```rust
let mode_name = match init.current_mouse_mode {
    1 => "server (relative)",
    2 => "client (absolute)",
    other => {
        warn!("main: unknown mouse mode {}", other);
        "unknown"
    }
};
```

The original `if current == 1 { "server" } else { "client" }`
that PR 31 shipped was removed during the merge. The
item is closed; the master plan strike-through cites
develop's `10f19477` and our second-rebase merge.

## Design

### Item 6 — per-connection cancel signal

Approach: cooperative cancellation via
`Arc<AtomicBool>`, mirroring the existing
`SHUTDOWN_REQUESTED` pattern at module scope. No new
dependencies; no architectural refactor.

Concrete shape:

1. Add a field to `RyllApp`:
   ```rust
   /// Per-connection cancel flag. `reconnect()` sets
   /// the previous attempt's flag before spawning the
   /// next, so a stale `run_connection` sees the flag
   /// in its 100 ms poll branch and exits cleanly,
   /// dropping its tokio runtime when the thread
   /// returns.
   connection_cancel: Option<Arc<AtomicBool>>,
   ```

2. In `RyllApp::new` (the initial-connection path),
   create a fresh `Arc::new(AtomicBool::new(false))`,
   store it in `self.connection_cancel`, and pass a
   clone into `run_connection`.

3. In `RyllApp::reconnect` (line 634), at the top of
   the function:
   ```rust
   if let Some(prev) = self.connection_cancel.take() {
       prev.store(true, Ordering::Relaxed);
   }
   ```
   Then create a fresh flag for the new attempt and
   pass it through the same plumbing already in place
   for `enable_paste` / `notifications_clone`.

4. `run_connection` gains a parameter
   `cancel: Arc<AtomicBool>`. The 100 ms select branch
   polls it alongside `SHUTDOWN_REQUESTED`:
   ```rust
   _ = tokio::time::sleep(Duration::from_millis(100)) => {
       if crate::SHUTDOWN_REQUESTED.load(Ordering::Relaxed) {
           info!("app: shutdown requested (SIGINT)");
           break;
       }
       if cancel.load(Ordering::Relaxed) {
           info!("app: connection cancelled (superseded)");
           break;
       }
   }
   ```

5. The connection task spawned earlier in
   `run_connection` (the one referenced by
   `connection_handle`) is what actually owns the
   sockets. When the select loop breaks and
   `run_connection` returns, `block_on` returns, the
   runtime drops, and the runtime drop aborts the
   connection task (which closes its sockets). This
   already happens for `SHUTDOWN_REQUESTED`; the new
   path just adds another reason to exit.

The 100 ms poll latency is acceptable: the user
pressing Reconnect twice in a row already accepts
some lag while channels rebuild, and 100 ms before
the previous attempt notices the supersede is well
inside human reaction time.

#### Why not `tokio::sync::Notify` or `CancellationToken`

`CancellationToken` (from `tokio-util`) gives
nicer ergonomics — `cancel_token.cancelled().await`
becomes its own select branch, no polling. But adding
`tokio-util` is a new dependency, and the existing
`SHUTDOWN_REQUESTED` pattern already exercises the
`AtomicBool` + 100 ms-poll shape, so following it
keeps the code locally consistent. If we later want
to standardise on `CancellationToken` for both, that
becomes a single follow-up across both signals, not
this phase.

`Notify` would also work but has the same
new-dependency-or-pattern-divergence trade-off.

### Item 7 — overlay-aware cursor hide

Add `!ctx.wants_pointer_input()` to the AND chain:

```rust
if self.cursor_image.is_some()
    && !self.region_select_active
    && self.surface_rect != egui::Rect::NOTHING
    && !ctx.wants_pointer_input()
    && ctx.input(|i| {
        i.pointer
            .hover_pos()
            .is_some_and(|p| self.surface_rect.contains(p))
    })
{
    ctx.output_mut(|o| o.cursor_icon = egui::CursorIcon::None);
}
```

Order matters slightly: putting the cheap
`!ctx.wants_pointer_input()` before the more expensive
`ctx.input(...)` short-circuits faster on overlay
frames.

`wants_pointer_input` is true on the frame in which
egui has decided to capture pointer events — for
example when a popup is open and the pointer is over
its interactive surface. When the user moves the
pointer back over the SPICE surface (with no popup
under it), the predicate returns to false and the
host cursor hides again. This matches what readers
expect from "the host cursor reveals while I'm
interacting with the GUI."

### Item 8 — clipboard hash-based dedup

Replace `last_clipboard_text: Option<String>` with
`last_clipboard_hash: Option<u64>`. Add two private
helpers in `main_channel.rs`:

```rust
/// Normalise text so the dedup is invariant under
/// line-ending munging during host-clipboard round
/// trips (Windows and some Wayland compositors flip
/// LF ↔ CRLF and trim/append trailing whitespace).
fn normalize_clipboard(text: &str) -> String {
    text.replace("\r\n", "\n")
        .replace('\r', "\n")
        .trim_end()
        .to_string()
}

/// Hash the normalised text. Storing the hash instead
/// of the text keeps clipboard contents (which may
/// include passwords) out of memory.
fn hash_clipboard(text: &str) -> u64 {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut h = DefaultHasher::new();
    normalize_clipboard(text).hash(&mut h);
    h.finish()
}
```

Update both call sites (the guest→host and host→guest
paths) to store `hash_clipboard(&text)` instead of
`Some(text)`. Update the comparison in
`poll_host_clipboard`:

```rust
let new_hash = hash_clipboard(&text);
let changed = match self.last_clipboard_hash {
    Some(prev) => prev != new_hash,
    None => true,
};
if changed {
    info!("main: host clipboard changed ({} bytes)", text.len());
    self.last_clipboard_hash = Some(new_hash);
    self.send_clipboard_grab().await?;
}
```

`DefaultHasher`'s collision resistance is more than
adequate for a "did the user copy something different"
check. We are not authenticating; we are deduplicating
against accidental round-trip drift.

The byte-count log line is preserved verbatim — the
log records `text.len()` not the hash.

### Item 9 — record as closed

No code change. In the master plan, mark item 9 with a
strike-through and a one-line note pointing at
develop's `10f19477` (which absorbed the rebuilt
handlers) and at our second-rebase merge commit
(`9aa12b9e`) as the moment it materially landed in this
PR's history.

## Implementation steps

Each item is its own commit (and own PR if you want).
Within a commit, follow these steps in order. Across
commits, the order is:

1. Item 6 (cancel signal) — touches the most surfaces;
   land it first so subsequent items don't conflict.
2. Item 7 (cursor-hide) — single-line addition.
3. Item 8 (clipboard hash) — small, isolated to
   `main_channel.rs`.

If you split into separate PRs, do them in this order
or rebase after each.

### Item 6 commit steps

1. Add the `connection_cancel: Option<Arc<AtomicBool>>`
   field to `RyllApp`.
2. Initialise it in `RyllApp::new` and pass a clone to
   the first `run_connection` call.
3. Update `RyllApp::reconnect` to cancel the previous
   flag (if any), allocate a fresh flag, and pass it
   to the new `run_connection`.
4. Update `run_connection`'s signature to take
   `cancel: Arc<AtomicBool>`.
5. Add the cancel poll inside the 100 ms select branch.
6. Add an info-level log line on cancellation so a
   developer reading logs sees "connection cancelled
   (superseded)" when Reconnect spam happens. The line
   should be unambiguous; do not reuse the
   SHUTDOWN_REQUESTED text.
7. Run `make build`. Cycle through `make test`,
   `make lint`, `pre-commit run --all-files`. None of
   these should report new warnings.

### Item 7 commit steps

1. Add `!ctx.wants_pointer_input()` to the AND chain
   at `ryll/src/app.rs:2984-2994`.
2. Run `make build && make lint`.
3. Hand-test only if you have a SPICE server handy:
   open the bug-report dialog (F12) over the surface,
   confirm the host cursor reveals, close the dialog,
   confirm it hides again. If no server is handy,
   document in the commit message that the change is
   small enough that the lint/build gates plus the
   plan-described mechanism cover it.

### Item 8 commit steps

1. Rename the field
   `last_clipboard_text: Option<String>` to
   `last_clipboard_hash: Option<u64>` in `MainChannel`.
2. Add `normalize_clipboard` and `hash_clipboard`
   helpers private to `main_channel.rs`.
3. Replace both write sites and the read-side
   comparison.
4. Add unit tests in the existing `mod tests` block:
   - `clipboard_hash_invariant_under_crlf_lf`:
     `hash_clipboard("foo\nbar")` ==
     `hash_clipboard("foo\r\nbar")`.
   - `clipboard_hash_invariant_under_trailing_whitespace`:
     `hash_clipboard("foo")` ==
     `hash_clipboard("foo\n")` ==
     `hash_clipboard("foo  ")`.
   - `clipboard_hash_distinguishes_different_content`:
     `hash_clipboard("foo")` !=
     `hash_clipboard("bar")`.
5. Run `make build && make test && make lint`.

### Item 9 commit steps

No code change. Strike through item 9 in the master
plan with the closing note in the same commit that
strikes through items 6, 7, and 8 (i.e., the master-plan
update commit at the end of this phase, or the last
of the per-item commits if you prefer to bundle the
strike-through with the final fix).

## Acceptance criteria

Per item:

- **Item 6**: After landing, spamming the Reconnect
  button five times in quick succession against a
  hung server (manual test) should produce one new
  active thread per press at the moment of the press,
  with previous attempts logging "connection cancelled
  (superseded)" within ~100 ms and exiting. The OS
  thread count, observed via `/proc/<pid>/status`,
  should not climb monotonically with each press.
  Automated coverage: a unit test for the cancel-flag
  predicate is not warranted (it is an `AtomicBool`
  load); the integration-level behaviour is the test.
- **Item 7**: With a side panel open over the surface,
  the host cursor is visible while the pointer is
  inside the panel and hidden when the pointer
  re-enters the surface area not covered by the
  panel. With no panel open, behaviour is unchanged.
- **Item 8**: The three new clipboard hash unit tests
  pass. The `make test` count grows by exactly 3 from
  Phase 1's 304 to 307. No other tests change.

Across all items:

- `make build` clean.
- `make lint` reports zero warnings or errors with
  `-D warnings`.
- `pre-commit run --all-files` reports all hooks
  passed.
- The diff for each commit is bounded to the files
  named in its commit steps.

## Verification — does the fix actually fix

### Item 6

To convince ourselves the cancellation works:

1. Build a small temporary unreachable target
   (a `socat -d -d TCP-LISTEN:5901,reuseaddr,fork
   /dev/null &` will do — listens but never speaks
   SPICE), connect ryll to it, and once the
   "Disconnected" dialog appears, press Reconnect
   five times in two seconds.
2. Observe `/proc/$(pgrep -f ryll)/status | grep ^Threads:`
   before, during, and 30 seconds after the spam.
   With the fix, the thread count climbs briefly then
   returns to baseline as superseded attempts log
   "connection cancelled (superseded)" and exit.
   Without the fix (revert the cancel poll branch),
   the thread count stays elevated until each
   underlying TCP connection times out (~75 s with our
   keepalive settings).
3. Restore the cancel poll if you reverted it.

### Item 7

Manual check: open the bug-report dialog (F12) over
the surface, move the pointer over the dialog. The
host cursor should be visible while over the dialog
and hidden once the pointer leaves the dialog rect for
surface area not covered by the dialog. Repeat for at
least one side panel (Notifications bell or Folders)
and one floating window (Protocol gaps via the
"Gaps: N" button).

### Item 8

Beyond the three unit tests — which lock the
invariants — there is no automated end-to-end check
that exercises an actual host clipboard round trip.
Manual verification on Windows or Wayland where the
mangling reproduces is the long way round. The unit
tests are sufficient for the regression guard.

## Out of scope — explicitly

- Replacing per-call `tokio::Runtime` with a single
  shared runtime owned by `RyllApp`. The cancel-signal
  fix solves the practical thread-leak; the larger
  refactor stays out for now.
- Migrating both `SHUTDOWN_REQUESTED` and
  `connection_cancel` to a single
  `tokio_util::sync::CancellationToken`. New
  dependency, larger blast radius; tracked separately
  if we ever want to standardise.
- Sanitising clipboard content beyond line-ending and
  trailing-whitespace normalisation. The dedup
  invariant is what we are guarding; encoding
  conversions, BOM stripping, etc. are the
  responsibility of `arboard` / the OS clipboard
  provider.
- Hand-testing the cursor-hide fix on every overlay
  type ryll exposes. The egui contract for
  `wants_pointer_input` is the source of truth; a
  spot-check on bug-report dialog + side panel +
  Protocol gaps window covers the three layout
  classes (modal, side-anchored, free-floating).

## Update of master plan on completion

When this phase merges to develop:

- Strike through items 6, 7, 8, 9 in the
  "Should consider" section of
  [PLAN-pr31-followup.md](/components/ryll/plans/PLAN-pr31-followup/) with
  one-line notes pointing to this phase plan and the
  landed commits / PRs (one note per item).
- Update the index entry for the master plan to add
  this phase to the Phases column and flip the status
  from "In progress" to "Complete".
- Update the Administration > Tracking subsection's
  remaining "One PR per item in Should consider"
  bullet to reflect what actually shipped (one PR
  bundling 6+7+8 if that's how you ship them, or
  three PRs each named).

After this phase, the PR 31 follow-up master plan is
complete. Item 9 closes without code, items 1-3 are
covered by Phase 1, items 4-5 are covered by Phase 2,
items 6-8 are covered by this phase.
