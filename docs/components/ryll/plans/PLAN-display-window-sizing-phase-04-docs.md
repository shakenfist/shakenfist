# Phase 4 — documentation

Parent: [PLAN-display-window-sizing.md](/components/ryll/plans/PLAN-display-window-sizing/).

## Goal

Update user-facing documentation to describe the
always-fit window behaviour (phase 1), the
`Obey guest size hints` toggle and `--no-obey-guest-size`
CLI flag (phase 2), and the fact that the toggle survives
reconnect. Keep AGENTS.md alone — it does not document
per-feature behaviour.

## Scope

In scope:

- A README `Features` bullet covering the always-fit
  behaviour and the toggle.
- The `--no-obey-guest-size` flag added to README's
  Options block alongside `--enable-paste-as-keystrokes`,
  `--monitors`, etc.
- A new ARCHITECTURE.md *Window sizing* subsection sitting
  next to (or absorbed into) the existing *Multi-Monitor
  Support* section. Documents the round-trip between
  `maybe_send_monitors_resize` (outgoing
  `VDAgentMonitorsConfig`) and the auto-fit on
  `SurfaceCreated`, why both exist, the dedup invariant,
  the maximised/fullscreen short-circuit, and the
  `obey_guest_size` opt-out.
- A line added to ARCHITECTURE.md's reconnect *What
  survives* list noting that `obey_guest_size` is a
  session-level preference and survives reconnect (it
  lives on `RyllApp` but is not reset in
  `RyllApp::reconnect`).
- Master plan housekeeping: flip phase 4's status to
  `Complete` in the Execution table when this lands.
- `docs/plans/index.md` housekeeping: leave the row at
  `In progress` until phase 5 also lands. No edit needed
  this phase.

Out of scope (later phases):

- Phase 5 — resolution-change notifications. Adds its
  own README bullet and ARCHITECTURE entry then.
- AGENTS.md — no changes; window sizing is not a
  build/test/conventions concern.
- Connection properties dialog
  (PLAN-connection-properties.md) — separate plan,
  separate docs.
- Doc rewrites beyond what these features added.
  Existing prose stays.

## Grounding — what's there today

`README.md` features list lives at lines 5-37 with one
bullet per feature in roughly chronological-of-landing
order. The closest neighbours for a window-sizing entry
are line 11 (multi-monitor) and line 17 (window
persistence). Adding adjacent to either reads naturally.

The Options block at lines 156-174 is a hand-maintained
mirror of the `--help` output. New flags get one line in
the same shape as `--monitors <N>` /
`--enable-paste-as-keystrokes`. The block does not list
`--pedantic` or every flag in `Args` — only the
operator-facing ones, so the new `--no-obey-guest-size`
fits.

`ARCHITECTURE.md` *Multi-Monitor Support* section lives
at lines 433-445. It mentions `VDAgentMonitorsConfig` and
the `(display_channel_id, surface_id)` surface-isolation
key. Window sizing reuses the same machinery but is
distinct enough in scope that it deserves its own
subsection rather than getting folded in.

The reconnect *What survives* list lives at lines
901-922. Each bullet is a category that the reconnect
path leaves intact. The existing entries already mention
the paste-as-keystrokes toggle and the egui Context
(window position / size). `obey_guest_size` joins them.

## Design

### README — Features bullet

Insert as a new bullet adjacent to the existing
multi-monitor entry. Suggested wording:

```markdown
- **Window auto-fit** - The ryll window tracks the guest's display
  surface size: every primary `SURFACE_CREATE` resizes the window
  to match (modulo an 8-pixel alignment that mirrors what we send
  the guest via `VDAgentMonitorsConfig`). Maximised or fullscreen
  windows are left alone, and the surface renders at native size
  inside them. Toggle `Obey guest size hints` in the hamburger menu
  (or launch with `--no-obey-guest-size`) to pin the window — useful
  for fixed-size capture or for guests that flap between resolutions
  on their own. The toggle is a session-level preference and
  survives reconnect.
```

### README — Options block

Add one line to the Options block, adjacent to the other
display-related flags:

```
  --no-obey-guest-size   Start with "Obey guest size hints" turned off
```

Place it after `--monitors <N>` so the display-related
flags cluster.

### ARCHITECTURE.md — Window sizing subsection

Insert a new `## Window sizing` section *immediately after*
the existing `## Multi-Monitor Support` section (between
lines 445 and 447). Suggested content:

```markdown
## Window sizing

The ryll window auto-fits to the guest display surface.
On every primary `SURFACE_CREATE` (and on the
`ImageReady` auto-create fallback for surface 0), the
event-handling path queues a viewport resize via
`pending_resize`. `RyllApp::update` consumes the pending
value, runs the pure `compute_auto_resize` decision
helper, and — if the helper returns Some — issues a
`ViewportCommand::InnerSize` to ask egui to make the
window match the surface. The aligned target is also
seeded into `last_sent_resize` so the next frame's
`maybe_send_monitors_resize` dedupes and we do not echo
our own resize back to the guest as a fresh
`VDAgentMonitorsConfig`.

The reverse direction — user drags the ryll window —
runs each frame in `maybe_send_monitors_resize`. The
viewport's inner-rect size is reduced by
`STATS_BAR_HEIGHT` (zero when maximised or fullscreen),
8-pixel aligned, and clamped to a floor of 8 on each
axis via `compute_outgoing_resize`. The result is sent
to the guest as a `VDAgentMonitorsConfig` if it differs
from `last_sent_resize`. The guest may honour the hint
exactly, pick the closest supported mode, or decline —
whatever resolution the guest actually chooses comes back
as a fresh `SURFACE_CREATE`, and the auto-fit pipeline
above re-syncs the window.

Three short-circuits keep the loop stable:

* `compute_auto_resize` returns None when the viewport is
  maximised or fullscreen; the surface renders at native
  size inside the available area rather than fighting
  egui for the inner size.
* `compute_auto_resize` dedupes against `last_auto_resize`
  so a no-op resize event does not refire the
  `ViewportCommand` every frame.
* `compute_outgoing_resize` plus `last_sent_resize`
  dedupes the outgoing side, so an auto-fit's seeded
  target does not bounce back to the guest as if the
  user had just dragged the window.

Both decision helpers are pure functions and are
unit-tested in `ryll/src/app.rs`'s `tests` module.

The auto-fit can be turned off with the
`Obey guest size hints` checkbox in the hamburger menu
(or the `--no-obey-guest-size` CLI flag at launch).
With the toggle off, the window stays where the user put
it and the surface renders at native pixel size inside
it — overflowing or letterboxing as the dimensions
require. The toggle is a session-level preference and
is **not** reset across a reconnect.

`pending_resize` is only set when the affected surface
key is `(display_channel_id == 0, surface_id == 0)`, so
a secondary monitor's surface event cannot resize the
primary window.
```

### ARCHITECTURE.md — Reconnect "What survives"

Add a bullet between the paste-as-keystrokes entry and
the notification-store entry (i.e. inside the existing
list at lines 901-922):

```markdown
- The "Obey guest size hints" toggle. It is a
  session-level preference (set via the hamburger menu
  or `--no-obey-guest-size`) and is not touched by the
  reconnect path, so a reconnect inherits whatever
  value the user last left.
```

### Master plan status

Flip phase 4 in the Execution table from `Not started`
to `Complete`. Phase 5 stays `Not started`.

## Step-level guidance

Single step, single commit.

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 4a   | low    | sonnet | none      | Apply the doc edits described in *Design*. Files: `README.md` (one Features bullet, one Options line), `ARCHITECTURE.md` (one new subsection after Multi-Monitor Support, one bullet inside the existing reconnect *What survives* list), and `docs/plans/PLAN-display-window-sizing.md` (status table). Use the wording given in *Design* verbatim — adjust only formatting (line wrapping, heading levels) to match the surrounding style of each file. Do not edit AGENTS.md. Do not edit `docs/plans/index.md`. Run `pre-commit run --all-files`. Single commit titled `Document window auto-fit, toggle, and CLI flag.`. Do not commit until the management session has reviewed. |

Low effort and sonnet because the brief gives every diff
verbatim. No tests need to run because no code changes;
`pre-commit` covers formatting checks.

## Success criteria

* `pre-commit run --all-files` passes.
* README.md features list mentions window auto-fit,
  the toggle, and the CLI flag.
* README.md Options block lists `--no-obey-guest-size`.
* ARCHITECTURE.md has a `## Window sizing` section
  immediately after `## Multi-Monitor Support`.
* The reconnect *What survives* list mentions the
  `Obey guest size hints` toggle.
* The master-plan Execution table shows phase 4 as
  Complete.

## Review checklist

- [ ] README features bullet uses the wording from
      *Design*, fitted to surrounding line-wrap style.
- [ ] README Options block line for
      `--no-obey-guest-size`.
- [ ] `## Window sizing` subsection inserted
      immediately after `## Multi-Monitor Support`.
- [ ] Reconnect *What survives* gains the
      Obey-guest-size toggle entry.
- [ ] AGENTS.md untouched.
- [ ] `docs/plans/index.md` untouched (it stays
      `In progress` until phase 5 lands).
- [ ] Master plan status table shows phase 4 Complete.

## Follow-up

Phase 5 adds resolution-change notifications and gets its
own README bullet and ARCHITECTURE entry. When phase 5
lands, also flip the master-plan top-level status from
`In progress` to `Complete` in `docs/plans/index.md`.
