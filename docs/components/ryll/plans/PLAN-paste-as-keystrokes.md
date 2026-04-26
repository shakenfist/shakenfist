# Paste-as-keystrokes fallback

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

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, and data flow. Consult `AGENTS.md`
for build commands, project conventions, code organisation,
and a table of protocol reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), and `/srv/src-reference/qemu/qemu/`
(server-side SPICE in `ui/spice-*`).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table in this master plan under the *Execution*
section.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Ryll already has a complete vdagent clipboard path in
`ryll/src/channels/main_channel.rs:792` (`handle_agent_message`,
including `VD_AGENT_CLIPBOARD_GRAB` / `VD_AGENT_CLIPBOARD` /
`VD_AGENT_CLIPBOARD_REQUEST`) and announces
`VD_AGENT_CAP_CLIPBOARD_BY_DEMAND` /
`VD_AGENT_CAP_CLIPBOARD_SELECTION` capabilities at
`ryll/src/channels/main_channel.rs:670`. That whole
pathway depends on a vdagent process running inside the
guest to handle the agent-side of the clipboard protocol.

The sibling project uncalibrated-sextant is a `no_std` UEFI
Rust binary that boots inside QEMU and exercises SPICE
channels diegetically. It does not — and realistically will
never — run vdagent: vdagent is a Linux userspace daemon
that needs a kernel, X/Wayland, glib, and a filesystem; UEFI
provides none of those. Its locked-bootloader scene needs
the test driver to feed a bootloader password into the
guest. With no vdagent, the SPICE clipboard channel is
unavailable to that guest. The first phase of that scene's
plan also confirmed that `remote-viewer` (virt-viewer 11.0)
has no "paste as keystrokes" fallback of its own — its
clipboard menu item relies on vdagent end-to-end. We
therefore need ryll itself to grow that fallback if any
uncalibrated-sextant scene that exercises a "paste a string"
gesture is to work at all.

The rough shape of the feature is well understood:
synthesise a sequence of `KEY_DOWN` / `KEY_UP` events on the
existing inputs channel, one per character, with shift held
where required for shifted glyphs. Ryll already has all the
load-bearing pieces: an `InputEvent::KeyDown` /
`InputEvent::KeyUp` enum at `ryll/src/channels/mod.rs:198`,
a `key_to_scancode` map at `ryll/src/channels/inputs.rs:596`
covering ASCII letters/digits/punctuation/Tab/Enter/Space, a
`make_scancode` helper at line 579 that knows the
press/release encoding, and the cadence-mode precedent at
`ryll/src/app.rs:1213` for synthesising key events on a
timer. The existing host-clipboard plumbing
(`arboard::Clipboard` cached at
`ryll/src/channels/main_channel.rs:80`) gives us a free
"read what's currently on the host clipboard" primitive when
we want to plumb the GUI gesture.

## Mission and problem statement

Add a paste-as-keystrokes path to ryll: take a string from
the host (clipboard contents, or a string supplied via CLI),
translate each character to the SPICE scancode sequence that
produces it on a US-QWERTY guest layout, and emit those
events on the existing inputs channel with a configurable
inter-character delay.

This is **a fallback for guests without vdagent**, not a
replacement for the vdagent clipboard path. The vdagent path
remains the default for guests that have it (it is faster,
preserves Unicode, and round-trips correctly). The fallback
exists for guests that fundamentally cannot run vdagent —
UEFI binaries, BIOS dialogs, very early boot screens — and
for test scenarios where bypassing vdagent is the point
(e.g. exercising the SPICE inputs channel under realistic
typing load without leaning on the agent).

We want the feature usable from both modes:

* **Headless / scripted**: a CLI flag carrying the string to
  type. This is the surface the uncalibrated-sextant test
  driver uses.
* **GUI / interactive**: a button or keyboard shortcut that
  reads the host clipboard and types it. This is what an
  operator uses while iterating on a scene.

The translation layer is the fiddly bit: AT keyboard
scancodes are physical, not semantic. Ryll's existing map is
implicitly US-QWERTY. "Paste 'Z'" means "press shift, press
the key in the QWERTY 'Z' position, release, release shift",
not "deliver Unicode codepoint U+005A". Guests with a
different kernel keymap will see different characters. This
constraint mirrors how `xdotool --key` works on X11 and is
an acceptable starting point for our use case (test guests
under our control), but it must be documented.

## Open questions

These need answers before we start implementation. I am
flagging them for operator review — I have a recommended
default for each but want to surface the trade-off.

1. **GUI surface** *(resolved; refined by Q7)*: a dedicated
   *Paste as keystrokes* button in the menu/status area,
   rendered only when the feature is enabled at startup
   via `--enable-paste-as-keystrokes` (see Q7), and then
   further disabled while an active vdagent channel is
   present (i.e. `MainChannel::agent_connected` is true).
   This makes the fallback's purpose discoverable — when a
   guest can't run an agent the button lights up — without
   tempting the operator to bypass the faster, Unicode-clean
   vdagent path on guests that have it.

   The keyboard shortcut is `Ctrl+Alt+V`, not
   `Ctrl+Shift+V`: the latter clashes with VSCode's
   integrated terminal and most other terminal emulators
   (xterm, gnome-terminal, alacritty all bind it to "paste
   from clipboard"). `Ctrl+Alt+V` is unbound in major
   editors and terminals.

   Implementation note: ryll captures keys at the egui layer
   (using `i.modifiers.ctrl` and `i.modifiers.alt`
   independently — see `ryll/src/app.rs:1181`) before doing
   its own scancode translation, so the Windows "Ctrl+Alt
   == AltGr" quirk that affects raw-keyboard consumers does
   not apply: egui surfaces the modifier flags directly. On
   macOS, `Ctrl+Alt+V` is unidiomatic (`Cmd+Shift+V` would
   be more native), but ryll's primary platform is Linux
   and the cross-platform precedent is to use the same
   shortcut everywhere unless there's a strong reason not
   to. Defer macOS-native shortcut binding to Future work.

2. **Default inter-character delay**: xdotool defaults to
   12 ms between keys. spice-gtk has no precedent because it
   doesn't have this feature. I recommend **16 ms** as the
   default (≈ 60 chars/s, well within any reasonable
   keyboard buffer), exposed via `--paste-char-delay-ms`.

3. **Keyboard layout** *(resolved)*: hard-code US-QWERTY,
   re-using the existing scancode map at
   `ryll/src/channels/inputs.rs:596`. The translator builds
   on that same table — there is no second layout-specific
   mapping to maintain. Document the constraint in `README.md`
   and `AGENTS.md` (the CLI flags' help text and the
   translator's module doc-comment). A `--paste-layout` flag
   and per-layout scancode tables remain in *Future work* if
   we ever land non-US guests in the test fleet.

4. **Modifier-state restoration** *(resolved)*: yes — the
   paste sequence releases any held Shift / Ctrl / Alt at
   the start, types the string, and re-presses whichever
   modifiers were down at the moment of trigger. Without
   this, `Ctrl+Alt+V` would type `Ctrl+Alt+<every-char>`
   into the guest, which is useless. The state to track
   lives in `RyllApp::last_modifiers` at `app.rs:230`; the
   inputs channel must emit the matching KeyUp / KeyDown
   scancodes (`0x1D` Ctrl, `0x2A` Shift, `0x38` Alt — see
   `app.rs:1184–1208` for the existing modifier-tracking
   loop) so the guest's view of modifier state stays
   consistent with the host's.

5. **Maximum paste length** *(resolved)*: hard cap at 4096
   characters with no override flag. At the chosen 16 ms
   delay (Q2), 4096 characters is roughly a minute of
   typing — well above any realistic password or BIOS
   command line, well below the point where the operator
   would benefit from anything other than "hit Ctrl-C and
   try again". This is a fallback for pasting a password
   into a bootloader prompt, not a text-input method.
   Beyond the cap, the translator truncates and emits a
   `warn!` line citing the requested vs typed character
   counts. No CLI flag.

6. **Non-ASCII characters** *(resolved)*: pre-validate the
   whole string before typing any of it. If every codepoint
   maps to a scancode sequence, type. If any codepoint
   doesn't, abort: emit nothing on the inputs channel and
   surface the failure to the operator. This is safer than
   skip-and-continue for the load-bearing use case — a
   password silently missing one character would lock the
   operator out of a bootloader prompt after a few retries,
   with no obvious diagnostic.

   Translator API: return `Result<Vec<KeyTriple>,
   PasteError>` where `PasteError::Unrepresentable { count,
   sample }` carries the count of bad codepoints and a small
   sample (e.g. the first three) for the error message.

   Surface treatment:

   * **GUI**: an informational `egui::Window` dialog
     ("Cannot paste as keystrokes. The clipboard contains
     <N> characters that have no US-QWERTY scancode
     mapping: <samples>.") with an OK button. No bytes
     sent. Pattern after the bug-report dialog at
     `app.rs:show_bug_dialog`.
   * **Headless / CLI**: log an `error!` line with the same
     content and exit non-zero. Headless is scripted, so the
     non-zero exit is the right signal that the test harness
     should fail loudly.

7. **Auto-engage when vdagent is absent** *(resolved)*: no.
   The whole feature is opt-in: the GUI button is only
   rendered, and `Ctrl+Alt+V` is only honoured, when the
   operator passes a `--enable-paste-as-keystrokes` flag at
   startup. With the flag absent, ryll behaves exactly as it
   does today (vdagent path only, no fallback surface). With
   the flag present, the button is rendered and the shortcut
   is bound; the button is still disabled while
   `MainChannel::agent_connected` is true (per Q1) so the
   operator doesn't bypass the faster path by mistake.

   This supersedes Q1's "always visible" framing —
   "always" now means "always when the feature is enabled
   at startup." The headless `--paste-text` flag is its own
   opt-in: passing it implies the feature is wanted.

   No auto-engage of any kind. The detection hysteresis
   (an agent that drops mid-session, agent capabilities
   that announce late, etc.) is not something we need to
   solve to unblock uncalibrated-sextant, and a quietly
   auto-routed `Ctrl+V` would be a footgun for guests
   where `Ctrl+V` already means something. Both
   auto-engage and a more-discoverable surface (e.g.
   "vdagent is absent — enable paste-as-keystrokes
   fallback?" prompt) are deferred to Future work.

8. **Telemetry** *(resolved)*: a dedicated
   `ChannelEvent::PasteCompleted { chars, elapsed_ms }`
   event emitted by the inputs channel when the sequence
   finishes (added next to the existing `Statistics`
   variant in `ryll/src/channels/mod.rs:24`). Surfaced as
   a transient status-bar message in the GUI and as a
   single `info!` line in headless. Pre-validation from Q6
   means a successful paste types every requested
   character, so `skipped` is unnecessary; `elapsed_ms`
   is the more useful field — it tells the operator
   whether the sequence took roughly what they expected
   given the configured char-delay.

## Execution

Four phases. Each is small enough to land as a single PR
with a single integration test.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Translator | [Phase 1](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-01-translator/) | Complete |
| 2. Channel + CLI | [Phase 2](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-02-channel-cli/) | Complete |
| 3. GUI gesture | [Phase 3](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-03-gui/) | Complete |
| 4. Docs and cross-repo | [Phase 4](/components/ryll/plans/PLAN-paste-as-keystrokes-phase-04-docs/) | Complete |

**Phase 1 — Translator (medium effort, sonnet model)**.
Pure-function translator from `&str` to a sequence of
`(scancode_press, scancode_release, needs_shift)` triples.
Lives in `ryll/src/channels/inputs.rs` next to
`key_to_scancode`. Handles ASCII printable, Tab, Enter (LF
or CRLF), Space; classifies the rest as "skipped" with the
codepoint preserved so the caller can `warn_once!`. Pure
unit tests against the function: round-trip a curated
fixture of every printable ASCII character plus the obvious
edge cases (mixed case, all the shifted punctuation, an
embedded newline, an embedded tab, a non-ASCII codepoint, an
empty string). No integration with the channel yet.

**Phase 2 — Channel and CLI (high effort, opus model)**.
Add `InputEvent::PasteText { text: String, char_delay_ms:
u32 }` to `ryll/src/channels/mod.rs:198` (the 4096-character
cap from Q5 is a constant in `inputs.rs`, not a parameter).
Handle it in `inputs.rs::handle_input_event` by running the
translator, then for each triple: send a shift KeyDown if
`needs_shift`, send the key KeyDown, sleep half the delay,
send the key KeyUp, send the shift KeyUp if needed, sleep
the rest of the delay. Save and restore modifier state
around the whole sequence. Drain the input channel
backpressure cooperatively (the loop in `inputs.rs:155`
needs to keep servicing real-time input while the paste
runs — we should not block the channel for the duration of
the paste). Add `--enable-paste-as-keystrokes`, `--paste-text TEXT`,
and `--paste-char-delay-ms N` to `ryll/src/config.rs:12`.
The first is the master gate (per Q7) — without it, the
inputs channel ignores any `InputEvent::PasteText` it
receives and the GUI never renders a button (see Phase 3).
Passing `--paste-text` implies the feature is wanted, so
that flag should also satisfy the gate. Wire them through `main.rs` /
`app.rs` so headless mode triggers a paste once after the
inputs channel is up. Emit the dedicated
`ChannelEvent::PasteCompleted` when the sequence finishes.
Integration test: headless ryll + the existing
`make test-qemu` UEFI latency guest, paste a known string,
assert the screen colour change matches.

**Phase 3 — GUI gesture (medium effort, sonnet model)**.
Gate everything in this phase on the
`--enable-paste-as-keystrokes` flag from Phase 2 — when
absent, the GUI surface is identical to today. When
present: add a *Paste as keystrokes* button in the
status-bar cluster at `ryll/src/app.rs:1518` (alongside
the existing Traffic / USB / Folders / Screenshot / Gaps
/ Report buttons) that reads
`arboard::Clipboard::get_text()` (reusing the cached
clipboard at `main_channel.rs:80` if accessible, or a
separate one in `app.rs`) and submits an
`InputEvent::PasteText`. The button is disabled when
the main channel reports an active vdagent (the existing
clipboard path is preferred when available). Add a
`Ctrl+Alt+V` shortcut in `handle_input` at `app.rs:1144`,
taking care that the shortcut itself does not get
translated into ordinary KeyDowns sent to the guest. The
shortcut deliberately avoids `Ctrl+Shift+V` because it
clashes with terminal emulators' paste binding. Pre-
validate the clipboard contents per Q6 — on
`PasteError::Unrepresentable` open an informational
`egui::Window` instead of submitting the
`InputEvent::PasteText`, mirroring the bug-report dialog
pattern. Display the `ChannelEvent::PasteCompleted`
outcome as a transient status-bar message (5 s timeout,
same pattern as `bug_status_message` at `app.rs:1610`).
Manual test: run `make test-qemu`, copy a string on the
host, hit `Ctrl+Alt+V` in the ryll GUI, observe the
keystrokes arrive in the guest.

**Phase 4 — Docs and cross-repo (low effort, sonnet model)**.
Update `README.md` (CLI flags, GUI shortcut), `AGENTS.md`
(new InputEvent variant, layout caveat, where the
translator lives), and `ARCHITECTURE.md` (the inputs-channel
"can run a paced sequence" pattern). Update
`docs/plans/index.md` and `docs/plans/order.yml` per the
template's *Documentation index maintenance* section. Then
cross over into `shakenfist/uncalibrated-sextant/` and:
strike the *Prerequisites* block from
`docs/plans/PLAN-locked-bootloader.md`, mark
`PLAN-locked-bootloader-phase-01-spice-infra.md` "paste"
success criterion `[x]` (replacing the `[~]`), flip the
execution-table rows for Phases 2 and 3 from *Blocked* to
*Not started*. Confirm the ryll release / version that
shipped the feature so the cross-repo reference is
unambiguous.

Phase 1 is mechanical enough to run sonnet on a thorough
brief. Phase 2 is the load-bearing one: it touches the
channel handler's `select!` invariants, has timing-related
correctness concerns, and needs careful attention to the
modifier state machine in `app.rs:1181` so the GUI's
existing modifier tracking doesn't get out of sync with
the synthetic events the inputs channel emits. Phase 3 has
clear precedent (the bug-report dialog in `app.rs`). Phase
4 is mechanical edits.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never
in the management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean
and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step
   with the brief from the plan, at the recommended
   effort level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it) or
   the model was too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied
   with the result.

This applies to all steps, including high-effort ones.
If a sub-agent can't succeed even with a detailed brief
and the right model, that's a signal the brief needs
improving, not that the management session should do
the implementation itself.

Use `isolation: "worktree"` for sub-agents when the
change is risky or experimental. The worktree is
discarded if the output is unsatisfactory. For safe,
well-understood changes, sub-agents can work directly
in the main tree (this worktree at
`shakenfist/ryll-wt-fallback-paste`).

### Planning effort

The master plan itself was created at **high effort** —
it required reading the inputs/main channel handlers,
the egui input loop, the cadence precedent, the existing
clipboard plumbing, and the relevant uncalibrated-sextant
plans to confirm the cross-repo dependency.

Phase 1 plan: medium effort. The translator surface is
narrow and well-bounded; the planner needs to enumerate
the shifted-punctuation table from the existing scancode
map.

Phase 2 plan: high effort. Requires getting the
modifier-state machine right and reasoning about the
inputs channel's `select!` invariants while it is paced
through a synthetic sequence.

Phase 3 plan: medium effort. Mostly egui plumbing with
clear precedent (bug-report dialog).

Phase 4 plan: low effort. Mechanical doc edits.

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**
- **high** — Requires reading multiple files, making
  judgment calls, understanding non-obvious invariants,
  or researching external references. The sub-agent
  needs to think carefully about edge cases.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief. May need to read
  a few files but the approach is well-defined.
- **low** — Purely mechanical changes (rename, reformat,
  add a log line). The brief is a complete instruction.

**Model choice:** The planner should recommend which
model is best suited for each step. This is a judgment
call, not a rigid rule — the right model depends on what
the step requires, not on whether it's "planning" or
"implementation".

- **opus** — Best for steps that require deep reasoning,
  cross-file architectural understanding, subtle
  correctness judgment, or complex protocol research.
  Also appropriate for intricate implementation where
  getting it wrong would be costly to debug.
- **sonnet** — Good default for well-briefed
  implementation work. Faster and cheaper than opus.
  Works well when the plan front-loads the research
  and the brief is detailed enough that the agent
  doesn't need to make broad judgment calls.
- **haiku** — Suitable for purely mechanical tasks:
  search-and-replace, adding log lines, running
  commands. The brief must be a near-complete
  instruction.

The model choice interacts with effort level and brief
quality. A detailed brief compensates for a lighter
model — sonnet at medium effort with a thorough brief
often matches opus at medium effort with a vague brief.
The planner's job is to write briefs good enough that
the recommended model can succeed.

Note: the model also determines the context window
(opus has 1M tokens, sonnet and haiku have 200K). Steps
that require holding many files in context simultaneously
may need opus for that reason alone, even if the
reasoning itself is straightforward.

**When in doubt, skew to the more capable model.**
Saving money only matters if the outcome is still
acceptable. A failed or low-quality implementation
wastes more time (and therefore more money) than using
a heavier model would have cost. Only recommend a
lighter model when you are confident the brief is
detailed enough for it to succeed.

**Brief for sub-agent:** This is the key field. Write it
as if briefing a colleague who has never seen the
codebase. Include: what to change, which files to touch,
what patterns to follow, and any non-obvious constraints.
The better the brief, the lower the effort level needed
and the lighter the model that can succeed.

A good brief front-loads the research the planner already
did, so the implementing agent doesn't repeat it. For
example, instead of "add tests for the QUIC decoder",
write "add tests for `quic_decode()` in
`shakenfist-spice-compression/src/quic.rs`. Test vectors:
a 2x2 RGBA image encoded with the reference C encoder at
`/srv/src-reference/spice/spice-common/...`. The function
takes `(data, width, height)` and returns
`Option<Vec<u8>>` of RGBA pixels."

### Management session review checklist

After a sub-agent completes, the management session
should verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code builds (`pre-commit run --all-files` or
      equivalent).
- [ ] Tests pass (`cargo test --workspace` or
      equivalent).
- [ ] The changes match the intent of the brief — not
      just syntactically correct but semantically right.
- [ ] Commit message follows project conventions
      (including the Co-Authored-By line with model,
      context window, effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck, gitleaks, bidi).
* New code follows existing patterns: channel handler
  structure, `InputEvent` variant style, async tasks via
  tokio, event communication via mpsc channels, `warn_once!`
  for protocol gaps as defined in
  `STYLEGUIDE.md` §"warn_once for protocol gaps".
* There are unit tests for the translator (every printable
  ASCII codepoint, mixed case, shifted punctuation, Tab,
  Enter, Space, non-ASCII rejection, empty input). The
  existing test suite still passes (`cargo test
  --workspace`).
* Lines are wrapped at 120 characters; clippy `-D warnings`
  remains clean.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` describe
  the new CLI flags, the GUI gesture, the layout caveat,
  and the `InputEvent::PasteText` variant.
* `docs/plans/index.md` and `docs/plans/order.yml` list this
  master plan.
* A headless smoke test against `make test-qemu` types a
  known string and the guest reflects the keystrokes
  (each key flips the screen colour as the latency probe
  expects).
* The cross-repo reference in
  `shakenfist/uncalibrated-sextant/docs/plans/PLAN-locked-bootloader.md`
  has been updated: *Prerequisites* satisfied, Phase 2 and
  Phase 3 unblocked, Phase 1 success-criterion checkbox for
  the paste step flipped from `[~]` to `[x]`.

### Future work

* **Multiple keyboard layouts.** The first cut hard-codes
  US-QWERTY. A `--paste-layout` flag taking a layout name
  (us, gb, de, etc.) plus per-layout scancode tables would
  cover non-US guests. Out of scope here because every
  guest in the current test fleet is US-QWERTY.
* **Unicode-via-compose.** xdotool supports Unicode by
  synthesising Ctrl+Shift+U-prefixed compose sequences on
  Linux. SPICE guests can in principle be driven the same
  way, but the receiving side must support it. Out of
  scope; the `warn_once!`-and-skip behaviour is the agreed
  fallback.
* **Auto-engage when vdagent absent.** Watch
  `agent_connected` in `MainChannel` and route a `Ctrl+V`
  gesture through paste-as-keystrokes when no agent is
  present, with the existing clipboard path otherwise.
  Needs hysteresis around mid-session agent
  drops/reconnects. Deliberately deferred — see Open
  question 7.
* **Paste-from-file CLI flag.** `--paste-file PATH` for
  scripted use cases that don't fit on a CLI. Trivial
  extension once Phase 2 lands; deferred to keep the first
  cut small.
* **Test-driver story.** Once
  `shakenfist/uncalibrated-sextant`'s gRPC-over-serial
  transport ships, paste-as-keystrokes becomes one of the
  test directives the driver issues over that channel
  instead of a CLI flag. That's the longer-term shape;
  this milestone gets us the surface, the driver work
  reuses it.
* **Per-character timing histogram in bug reports.** The
  bug-report `InputsSnapshot` could record a small
  histogram of inter-key timings during a paste, useful
  for diagnosing guest-side keyboard buffer overruns.
  Out of scope.

### Bugs fixed during this work

This section will list any bugs we encounter during
development that we fixed.

### Documentation index maintenance

When this master plan is created, update:

* **`docs/plans/index.md`** — add a row to the *Master
  plans* table with today's date, a link to this plan, the
  one-line intent "Synthesise SPICE keystrokes for the
  contents of a string when no vdagent is available", an
  initial status of *In planning*, and links to each phase
  plan file as they are written.
* **`docs/plans/order.yml`** — add an entry for this master
  plan so it appears in the documentation navigation bar.
  Phase files are not added.

When all phases are complete, update the status column in
`index.md` to *Complete*.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
