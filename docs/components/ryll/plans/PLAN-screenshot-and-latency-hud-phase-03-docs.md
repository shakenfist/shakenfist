# Phase 3: Documentation

Parent plan: [PLAN-screenshot-and-latency-hud.md](/components/ryll/plans/PLAN-screenshot-and-latency-hud/)

## Goal

Update user-facing documentation to mention the two new
features. No `ARCHITECTURE.md` changes are needed —
neither feature adds a module or alters data flow.

## Background

Existing patterns to match:

- The README features list at
  [README.md:6-28](https://github.com/shakenfist/ryll/blob/develop/README.md) uses bold-prefixed bullets
  describing each feature. F11 (traffic viewer) and F12
  (bug reports) are described in this style at lines 24-25.
- There is no separate "Keyboard shortcuts" section. F8
  should follow the same pattern as F11/F12 — described
  inline with the feature it triggers.
- There are no docs in `docs/` that describe individual
  hotkeys. The CLI options table at
  [README.md:149-162](https://github.com/shakenfist/ryll/blob/develop/README.md) is for command-line flags
  only; the screenshot has no CLI flag (per master plan
  open question 1) so it does not appear there.
- `AGENTS.md` documents project conventions and code
  organisation — no change needed; we're not adding a
  module or new pattern.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a   | low | haiku | none | In [README.md](https://github.com/shakenfist/ryll/blob/develop/README.md) features list (lines 6-28), add two new bullets in the style of the existing ones: (1) "**Screenshot capture** - Press F8 or click 'Screenshot' in the status bar to save the current display as a PNG via a native file dialog. With multiple monitors, one PNG per surface is saved with `-1`, `-2` suffixes." (2) "**Latency sparkline** - Bottom stats panel shows a rolling history of latency samples next to the `Latency` label, populated by `--cadence` mode keystroke timings." Place them near the bandwidth sparkline bullet (line 20) since they're related stats-bar features. |
| 3b   | low | haiku | none | Verify `ARCHITECTURE.md` and `AGENTS.md` need no changes by reading them and confirming no section describes hotkeys, the stats panel, or the bug-report PNG path in a way that would now be inaccurate. If you find a section that does need updating, do the update; if not, leave them and note that in the commit message. |

## Success criteria for this phase

- `README.md` mentions F8 and the latency sparkline in the
  features list, in a style matching the existing F11/F12
  bullets.
- `ARCHITECTURE.md` and `AGENTS.md` are either unchanged
  (with a verification note in the commit message) or
  updated to reflect new behaviour.
- `pre-commit run --all-files` passes (catches markdown
  trailing whitespace and similar).
- One commit for this phase, containing both step outputs.

## Closing the plan

After phase 3 lands:

- Update [docs/plans/index.md](/components/ryll/plans/index/) status column for
  this plan to *Complete*.
- Push the `screenshot` branch (operator does this; do not
  open a PR per the user's standing instruction).
