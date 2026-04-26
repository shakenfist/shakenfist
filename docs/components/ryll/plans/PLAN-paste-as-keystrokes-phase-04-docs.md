# Phase 4: Documentation and cross-repo updates

## Overview

Finalise documentation in the ryll worktree and update
cross-repo references in `shakenfist/uncalibrated-sextant`
now that the paste-as-keystrokes feature has landed
(phases 1-3 complete).

## Parent plan

[PLAN-paste-as-keystrokes.md](/components/ryll/plans/PLAN-paste-as-keystrokes/)

## Current state

### ryll-side documentation (already largely done)

Phases 2 and 3 sub-agents incrementally updated the three
documentation files. Current coverage:

- **README.md**: Feature bullet at line 19 covers the
  feature description, CLI flags, Ctrl+Alt+V shortcut,
  Menu → Paste, and the vdagent-disabled behaviour.
  CLI flags listed in the Options section (lines 166-169).
  **Gap**: no explicit mention of the US-QWERTY layout
  caveat or the 4096-character cap.

- **ARCHITECTURE.md**: Full section (lines 948-998)
  covering translator, state machine, per-character event
  sequence, CLI flags, GUI surface, clipboard reading,
  and scancode details. **Gap**: none identified.

- **AGENTS.md**: Design decision #15 (lines 139-152)
  covering the state machine, modifier tracking, and
  public API. Code organisation entry for `inputs.rs`
  (line 262) mentions the paste state machine. **Gap**:
  none identified.

### Plan tracking files

- **`docs/plans/index.md`** line 30: status is "In
  planning", phases column says "(phases not yet written)".
  Needs updating to "Complete" with links to all four
  phase files.

- **`docs/plans/order.yml`**: already has the master plan
  entry. No change needed.

- **`PLAN-paste-as-keystrokes.md`** execution table
  (lines 276-281): all four phases show "Not started".
  Needs updating to "Complete" for phases 1-4.

### Cross-repo: uncalibrated-sextant

Located at `/home/mikal/src/shakenfist/uncalibrated-sextant/`.

- **`PLAN-locked-bootloader.md`** lines 81-128:
  Prerequisites block explains the ryll paste-as-keystrokes
  dependency. Now satisfied — should be struck through or
  annotated as resolved.

- **`PLAN-locked-bootloader.md`** lines 267-268:
  Phase 2 status: "Blocked on ryll paste-as-keystrokes"
  Phase 3 status: "Blocked on Phase 2"
  Both should change to "Not started".

- **`PLAN-locked-bootloader-phase-01-spice-infra.md`**
  line 471: paste success criterion is `[~]` with a note
  explaining that remote-viewer has no fallback. Should
  change to `[x]` with a note that ryll now has
  paste-as-keystrokes.

## Implementation steps

### Step 1: Update README.md layout caveat

Add a brief note about the US-QWERTY layout constraint
and 4096-character cap to the existing paste-as-keystrokes
feature bullet. Something like:

> Characters are mapped to US-QWERTY scancodes; guests
> with a different keyboard layout will see different
> characters. Maximum paste length is 4096 characters.

### Step 2: Update master plan execution table

In `PLAN-paste-as-keystrokes.md`, update the execution
table (lines 276-281) to mark all four phases complete
with links to their phase files:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. Translator | [Phase 1](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-01-translator/) | Complete |
| 2. Channel + CLI | [Phase 2](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-02-channel-cli/) | Complete |
| 3. GUI gesture | [Phase 3](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-03-gui/) | Complete |
| 4. Docs and cross-repo | [Phase 4](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-04-docs/) | Complete |
```

### Step 3: Update docs/plans/index.md

Change line 30 from:

```
| 2026-04-25 | [Paste-as-keystrokes fallback](/components/ryll/plans/PLAN-paste-as-keystrokes/) | ... | In planning | (phases not yet written) |
```

to:

```
| 2026-04-25 | [Paste-as-keystrokes fallback](/components/ryll/plans/PLAN-paste-as-keystrokes/) | ... | Complete | [1. Translator](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-01-translator/), [2. Channel + CLI](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-02-channel-cli/), [3. GUI gesture](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-03-gui/), [4. Docs](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-04-docs/) |
```

### Step 4: Cross-repo — update PLAN-locked-bootloader.md

In `/home/mikal/src/shakenfist/uncalibrated-sextant/docs/plans/PLAN-locked-bootloader.md`:

**4a**: Add a resolution note at the top of the
Prerequisites section (line 81), before the existing text:

> **Resolved.** ryll has landed paste-as-keystrokes on the
> `fallback-paste` branch (phases 1-4 of
> `PLAN-paste-as-keystrokes.md` in `shakenfist/ryll`).
> The prerequisite is satisfied; Phases 2 and 3 of this
> milestone are unblocked.

Keep the existing explanation text intact as historical
context — don't delete it.

**4b**: Update the execution table (lines 267-268):

Change Phase 2 from:
```
Blocked on ryll paste-as-keystrokes (see *Prerequisites* below)
```
to:
```
Not started
```

Change Phase 3 from:
```
Blocked on Phase 2
```
to:
```
Not started
```

### Step 5: Cross-repo — update phase 1 success criterion

In `/home/mikal/src/shakenfist/uncalibrated-sextant/docs/plans/PLAN-locked-bootloader-phase-01-spice-infra.md`:

Change line 471 from `[~]` to `[x]` and update the
annotation:

```
- [x] A single character pasted from the host clipboard
      reaches the binary's `read_key` loop and appears in
      `dist/serial.log` as a keypress event with the matching
      `unicode=` field. **Resolved: ryll now has
      paste-as-keystrokes (`--enable-paste-as-keystrokes`,
      `Ctrl+Alt+V`) which synthesises Inputs-channel
      keystrokes for clipboard contents without requiring
      guest-side vdagent.**
```

### Step 6: Run validation (ryll worktree only)

```bash
pre-commit run --all-files
make test
```

The cross-repo changes are documentation-only and have no
build artefacts to validate.

## Files to modify

| File | Repo | Changes |
|------|------|---------|
| `README.md` | ryll | Add layout caveat and character cap |
| `docs/plans/PLAN-paste-as-keystrokes.md` | ryll | Update execution table — all phases Complete |
| `docs/plans/index.md` | ryll | Update status to Complete, add phase links |
| `docs/plans/PLAN-locked-bootloader.md` | uncalibrated-sextant | Add resolution note to Prerequisites, unblock Phases 2-3 |
| `docs/plans/PLAN-locked-bootloader-phase-01-spice-infra.md` | uncalibrated-sextant | Flip paste criterion `[~]` → `[x]` |

## Step-level guidance

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 1-6 | low | haiku | none | Mechanical documentation edits. The plan provides exact before/after text for every change. Two repos are involved: ryll worktree at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/ryll-wt-fallback-paste/` and uncalibrated-sextant at `/home/mikal/src/shakenfist/uncalibrated-sextant/`. |

**Recommended execution**: Single sub-agent invocation
(haiku, low effort). All changes are mechanical text
edits with exact before/after content specified.

## Commit note

This phase produces **two commits in two different repos**.
The ryll changes (steps 1-3, 6) land in the ryll worktree
on the `fallback-paste` branch. The cross-repo changes
(steps 4-5) land in uncalibrated-sextant — check which
branch is appropriate before committing there.

Do NOT commit without operator approval. Present the
changes for review.

## Success criteria

- README.md mentions the US-QWERTY layout caveat and
  4096-character cap.
- The master plan execution table shows all four phases
  as Complete with links.
- `docs/plans/index.md` shows "Complete" with phase links.
- The uncalibrated-sextant Prerequisites section has a
  resolution note.
- The uncalibrated-sextant execution table shows Phases 2
  and 3 as "Not started" (no longer blocked).
- The phase 1 paste criterion is `[x]` with an updated
  annotation.
- `pre-commit run --all-files` passes in the ryll
  worktree.
