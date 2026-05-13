# Phase 03: Preserve audio volume across reconnect

## Prompt

Before answering questions or making design decisions in this
document, read the relevant ryll source. Key files:
`shakenfist-spice-renderer/src/channels/playback.rs:30-66`
(the `VolumeControl` definition, two `AtomicU8` / `AtomicBool`
fields, defaults volume=80 and muted=false), `ryll/src/app.rs`
around line 749 (`RyllApp::new` constructs the initial
`VolumeControl`), around line 995 (`RyllApp::reconnect`
**creates a fresh** `VolumeControl` and overwrites
`self.volume_control` with it — this is the bug), and the
slider widget around line 2548 (`set_volume` / `set_muted`
calls driven by the bottom-stats-bar UI). Consult
`ARCHITECTURE.md`'s "Code Organisation" / playback sections
for how the volume reaches the cpal audio thread and
`AGENTS.md` §14 ("Dedicated audio thread with lock-free ring
buffer") for the audio-thread / VolumeControl interaction.

This phase fixes bug **K3** — "Reconnect resets client audio
volume" — identified from session B6 (10:40:51Z) during
dogfooding session 001. The master plan flags this as small
("Phase 03 — own plan (small)").

## Situation

### What we already established

**The audio volume is host-side state, not server-side.** The
SPICE protocol's playback channel carries audio data; the
guest does not know or care about the client's playback
volume. ryll's volume slider drives the cpal output gain on
the client. So preserving volume across reconnect is purely
a client-side bookkeeping issue — no protocol changes
involved.

**`VolumeControl` is shared via `Arc<VolumeControl>` between
the app (which writes via the slider) and the playback
channel (which reads each output frame).** Two `Atomic*`
fields hold volume (u8, 0–100) and muted (bool). The audio
thread reads `effective_volume()` per frame.

**`RyllApp::reconnect` creates a brand-new `VolumeControl`
and overwrites `self.volume_control` with it.** `app.rs:995`:

```rust
let volume_control =
    shakenfist_spice_renderer::channels::playback::VolumeControl::new();
…
self.volume_control = volume_control.clone();
…
let vol_for_conn = volume_control;
```

`VolumeControl::new()` always returns volume=80, muted=false.
The user's prior slider position and mute toggle are
discarded. The slider widget now binds to the new
`VolumeControl` and shows 80% / unmuted.

### What we don't yet know

Nothing material. The bug is mechanical and the fix follows
directly from the existing data flow. A separate question —
**should volume persist across ryll processes** (relaunch,
not just reconnect)? — is **out of scope** for K3 per the
master plan's "small" framing; addressing it would require a
new settings-persistence story that does not exist today
(`ryll/src/settings.rs` carries only logging config, not
user preferences). Tracked as a possible future expansion
under "Open questions" below.

## Mission and problem statement

Make ryll preserve the user's volume slider position and mute
state across an auto-reconnect or manual-Reconnect cycle.
Within a single ryll process, the user's last audio choices
must survive any number of reconnects without their
intervention.

The phase succeeds when:

- A user setting volume to 25% (or muting) sees those values
  persist through a disconnect → auto-reconnect → fresh
  audio playback cycle, with no slider jump back to 80% / no
  surprise un-mute.
- The same is true for a manual click of the disconnect
  modal's Reconnect button.
- No regression for the headless / web modes (single-session
  paths that never call `reconnect()`).
- No new tokio-runtime / channel plumbing — the fix should
  be subtractive, not additive.

## Approach

`RyllApp::reconnect` does the right thing for almost
everything in the connection lifecycle — recreate the
event/input/usb/webdav/resize channels, reset the GUI
surfaces, etc. It is wrong about exactly one item: it
recreates `VolumeControl`, which is host-side state that
should survive the connection swap.

**The fix:** stop creating a new `VolumeControl` in
`reconnect()`. Reuse `self.volume_control` for the new
connection. The Arc semantics already cover the shared-read
case — the old playback channel reads its dropped `Arc` and
exits; the new one reads from the same `Arc` the slider has
been writing to all along.

Concretely, in `RyllApp::reconnect()`:

```rust
// remove:
let volume_control =
    shakenfist_spice_renderer::channels::playback::VolumeControl::new();
…
self.volume_control = volume_control.clone();
…
let vol_for_conn = volume_control;

// becomes:
// VolumeControl persists across reconnects — the user's
// slider position and mute state are host-side state, not
// session state, so we hand the *existing* Arc to the new
// connection task rather than recreating it.
let vol_for_conn = self.volume_control.clone();
```

No changes to `RyllApp::new`, the renderer crate, or any
downstream consumer. `VolumeControl::new()` itself stays as
it is (the initial-launch default of 80% / unmuted is still
correct; only the reconnect path was wrong).

### Why not also persist across processes?

Tempting to add a tiny per-user settings file ("ryll
remembers your last volume between launches") at the same
time. **Don't** in this phase, for three reasons:

1. The K3 entry in the master plan is sized as "small". Cross-
   process persistence pulls in path conventions (XDG vs
   `~/Library/Application Support` vs `%APPDATA%`), file
   format, schema-versioning, migration — none of which
   exist in the project today.
2. ryll has other user-facing toggles (obey-guest-size,
   cadence, paste-as-keystrokes) that would deserve the same
   persistence treatment; doing volume only would be
   asymmetric.
3. A persistence story for *all* user prefs is the right
   shape, deserves its own plan, and should land after the
   in-process bug is fixed — fixing K3 first cleans the
   slate.

See "Open questions" for the future-expansion note.

### Testing

No unit-test infrastructure exists for `RyllApp::reconnect()`
— the function spawns a tokio runtime and a renderer thread,
which are not unit-testable without scaffolding far heavier
than the bug warrants. The state-machine tests landed in
Steps 5–6 of Phase 02 use pure functions; this fix does not.

Instead:

- **Direct behavioural test:** add a tiny test in
  `app.rs::tests` that exercises `VolumeControl`'s round-trip
  semantics (set_volume / volume, set_muted / muted). Locks
  in the contract the fix relies on. Cheap.
- **Manual integration check (notes only):** with a real
  SPICE session, set volume to 25%, mute, then kill the SPICE
  server and let auto-reconnect run. Verify the slider
  position and mute icon survive. Check matches the existing
  Phase 02 manual checklist style.

The renderer's `playback::tests` module already covers the
`VolumeControl` behaviours; the new test is a guard that
catches accidental re-introduction of the bug via a future
`VolumeControl::new()` call in `reconnect()`.

## Tasks

- [x] In `RyllApp::reconnect()` (`ryll/src/app.rs` ~line
      995), remove the `let volume_control =
      VolumeControl::new();` allocation and the
      `self.volume_control = volume_control.clone();`
      reassignment. Change `let vol_for_conn = volume_control;`
      to `let vol_for_conn = self.volume_control.clone();`.
      Comment above the `vol_for_conn` binding explains that
      volume is host-side state that survives reconnects.
- [x] Add a unit test in `ryll/src/app.rs::tests`
      `volume_control_round_trip` that constructs a
      `VolumeControl`, calls `set_volume(25)` and
      `set_muted(true)`, and asserts `volume() == 25` and
      `muted() == true`. Also exercises the Arc-shared-read
      path (one clone sees writes from another) since that
      is the property the fix's `self.volume_control.clone()`
      hand-off relies on.
- [x] Update `PLAN-session-001-feedback.md` Execution table
      row for Phase 03 → Done.
- [x] Update `PLAN-session-001-feedback.md` K3 row in the
      "Confirmed bugs" table with the resolution pointer
      (this phase's plan file is named in the row).
- [ ] Manual integration check (deferred operator action,
      bundled with the Phase 02 manual checklist if you run
      them together): connect, set volume to 25%, mute, kill
      SPICE server, watch auto-reconnect, verify slider and
      mute icon both retain the pre-disconnect state. Repeat
      with the manual Reconnect-button path from the
      disconnect modal.

## Out of scope

- **Cross-process persistence.** ryll relaunch resets volume
  to 80% / unmuted, same as today. See "Open questions" for
  a future-expansion sketch.
- **Server-side volume.** Some SPICE deployments may want
  the guest to know the client's volume (audio normalization,
  per-app mixers). ryll does not surface this and the SPICE
  spec has no client→server volume message; deferred
  indefinitely.
- **Other reconnect-resets-state bugs.** A separate audit
  could check whether other user preferences (e.g.
  obey-guest-size, cadence) survive reconnect. They are
  initialized once in `RyllApp::new` and not touched in
  `reconnect()`, so should be fine — but this phase does not
  add a regression test for them. Open as a follow-up if
  another instance turns up.
- **Web-mode volume.** `--web` mode has no client-side
  volume slider (audio is decoded in the browser via WebRTC).
  Out of scope for this phase; revisit if the web frontend
  ever grows a volume control.

## Open questions

1. **Should we add a settings-persistence file as a separate
   master-plan phase?** A `~/.config/ryll/settings.toml` (or
   platform-specific equivalent) tracking volume, mute,
   obey-guest-size, cadence, paste-as-keystrokes, and any
   future toggles would unify the user-preferences story.
   Worth scoping if a second user-pref-reset bug surfaces;
   premature to do now for K3 alone.

2. **Should the bottom-bar volume slider show a visual
   indicator when volume has been set but no audio is
   playing?** Independent UX question that the K3 fix
   surfaces but does not address. Defer.

## Companion docs

None new. ARCHITECTURE.md's playback section already
describes the `Arc<VolumeControl>` ownership; the fix in this
phase reinforces what the doc already says ("host-side
authority on volume") so no doc update is needed once K3
lands. AGENTS.md §14 ("Dedicated audio thread with lock-free
ring buffer") likewise stays correct.
