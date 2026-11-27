# Untrusted value and text sweep

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
overview, channel types, data flow, and code organisation.
Consult `docs/development.md` for build commands, and
`AGENTS.md` for project conventions and a table of protocol
reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), and `/srv/src-reference/qemu/qemu/`
(server-side SPICE in `ui/spice-*`).

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

Reading the RDP frontend in November 2026 raised a question: ryll
and kerbside have a *protocol* firewall for SPICE, so is there a
*content* firewall missing as well? Nothing, for instance, checks
that the numbers in the audio channel are reasonable.

The protocol firewall is real and it is thorough. `BoundedReader`
makes every parser panic-free and overflow-checked, `ReceiveBuffer`
and `MAX_MESSAGE_BODY` refuse an oversized body before buffering
it, `limits::rgba_len` bounds every decoded image, `MAX_CLIP_RECTS`
bounds a clip, and the fuzz targets in
`shakenfist-spice-protocol/fuzz/` keep the parsers honest against
arbitrary bytes. [Image decoder
hardening](/components/ryll/plans/PLAN-decoder-hardening/) gave image geometry the
shared-limit treatment across eight issues (#171 to #177, #182),
and its root-cause finding was that the bug was the *convention*,
not the call sites.

What none of that covers is a field whose every bit pattern is in
range. A `u32` channel count parses successfully whatever the
server put in it; so does a `u32` sample rate, and so does a string
of ASCII escape sequences. Those values are not inert — ryll sizes
buffers from them, divides by them, logs them, and hands them to
threads a long way from the message they arrived in — and nothing
in the protocol firewall has anything to say about them. The fuzz
targets cannot find this class by construction: the parse
*succeeds*, and the damage happens downstream of it, often on
another thread.

Three instances were found in one afternoon's reading. Two are
fixed by this plan's first phase and the third is issue #692:

| Finding | Where | Consequence |
|---------|-------|-------------|
| `PLAYBACK_START` geometry unvalidated | `channels/playback.rs` START arm, into `AudioThread::spawn` and `Resampler::new` | `channels` reaches `vec![0i16; ch]` in the cpal callback: `0xffffffff` channels is an 8 GiB allocation on a real-time thread. `frequency = 0` is the numerator of a resampling ratio that never advances |
| `SPICE_MSG_NOTIFY` text unsanitised | `Notify::message_text()`, used by seven channels | Up to 64 KiB of server bytes reach `warn!` and a notification entry unfiltered: a newline forges log lines, `ESC[` or the 8-bit CSI drives the terminal of whoever reads the log, a bidi override reorders what a human sees |
| Guest Opus forwarded before validation | `channels/playback.rs` DATA arm, `shakenfist-spice-rdp/src/audio.rs` (#692) | A packet ryll's own decoder rejects has already been sent to the RDP client's decoder, on a machine that trusts ryll and has never heard of the guest |

### The RDP frontend is what makes this worth doing now

In GUI, headless and web-client terms ryll is a *client*:
guest-origin bytes terminate at ryll's own decoders, and the
protocol firewall's job is to stop those decoders panicking or
allocating the host to death. The blast radius is ryll's own
process.

With `--rdp` (and over WebRTC) ryll is a *gateway*: it takes
guest-origin bytes and re-emits them to a third party, under its
own authority, onto a machine that trusts ryll and has no
relationship with the guest at all. That is the classic firewall
position, and ryll has only recently been in it. Framed as
defending a user's ears, a content firewall is a weak idea. Framed
as *ryll does not launder unvalidated guest bytes across a trust
boundary it is the only guard for*, it is a straightforward
posture goal.

### Two places the boundary is already handled correctly

Both implement content filtering by **narrowness** rather than by
inspection, and both are the pattern the rest of this plan extends:

- `fits_pcm` (`shakenfist-spice-rdp/src/audio.rs`) drops any
  decoded stream that is not exactly 48 kHz stereo. Not
  transcoded, not best-effort: dropped.
- The RDP clipboard bridge offers `CF_UNICODETEXT` only,
  size-capped, with line endings normalised at the boundary. No
  files, no HTML, no images, no `FileContents`.

### What is deliberately not in scope

Content a human asked for. A guest is entitled to play loud audio
and to draw a bright flashing screen, and nothing in a sample or a
pixel distinguishes a hostile one from a game:

- **Audio level.** There is no metadata lever to close:
  `PLAYBACK_VOLUME` is recorded for bug reports and never applied,
  and ryll's own `VolumeControl` clamps to 0-100, so a server has
  no influence on output level at all. The only remaining lever is
  sample amplitude, which is the content. A limiter would change
  every sound a user hears in order to defend against a thing they
  asked for; if it is ever wanted it ships as a "normalise audio"
  toggle in the UI, an audio-quality feature, and is not called
  security.
- **Display brightness and flashing.** The same argument. Note
  that photosensitivity is the one place where such a control has a
  real safety case, and that is an opt-in accessibility feature,
  not a firewall.

## Mission and problem statement

Make three rules true of ryll, and enforce each by something other
than a reviewer remembering it:

1. Every server-chosen wire value that escapes into client state is
   validated **where it is stored**, not where it is eventually
   used, and a message that fails is refused whole rather than
   clamped.
2. Every server-supplied string is sanitised **in its accessor**,
   so a channel cannot forget to.
3. Everything ryll re-emits to a third party across a trust
   boundary is validated **before** it is re-emitted.

Patching the three known instances would leave the next message
type to make the same mistake, exactly as the image decoders did
before [PLAN-decoder-hardening.md](/components/ryll/plans/PLAN-decoder-hardening/). So
the deliverable is a convention in `STYLEGUIDE.md`, a complete
inventory of where it applies, and a regression guard that fails CI
rather than waiting for a reviewer.

## Open questions

1. **Where does the regression guard live?** The class of bug is
   invisible to the existing parser fuzz targets because the parse
   succeeds. A handler-level target — arbitrary bytes through a
   channel's `process_messages` over the loopback harness — would
   find it directly. Options: a new target in the detached
   `fuzz/` workspace (nightly `cargo fuzz`, so it runs on a
   schedule rather than per-PR), or an `arbitrary`/proptest test
   in-tree that runs in normal CI on every push. Probably both,
   with the in-tree one as the gate.
2. **Can CI assert a memory ceiling?** An alloc bomb is only
   caught by a reviewer noticing the multiplication today. Running
   the handler tests under an `RLIMIT_AS` would turn "allocates
   gigabytes" into a test failure. Is that portable enough across
   the CI platform matrix to be worth it, or does it only run on
   Linux?
3. **How should #692 gate the Opus forward?** A full decode gate
   costs ordering latency and risks dropping audio a client would
   have played, because ryll's decoder may be stricter than the
   client's. A structural check — the TOC byte and frame count
   that `opus_packet_samples_48k` already parses — may be the
   better trade. Settle this in the gateway phase, not here.
4. **Clipboard paste injection: police it or not?** ryll puts
   guest-chosen bytes into the host clipboard
   (`main_channel.rs`, `GuestClipboard::Data`). A guest that
   copies text carrying newlines or terminal escapes is staging a
   paste-injection attack on whoever pastes into a shell. But
   stripping newlines breaks legitimate multi-line copy, which is
   most of what a clipboard is for. The current answer is to do
   nothing, log byte counts only, and record the reasoning; revisit
   only with a concrete opt-in design.
5. **Does kerbside want the same sweep?** It is in the same
   gateway position for the same messages, and its protocol
   firewall is the sibling of ryll's. The cross-repo single-home
   rule says the convention is documented once; which repository
   owns it is unsettled.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Audio geometry and notify text | This file, steps below | In progress | |
| 2. Inventory | - | Not started | |
| 3. Close the gaps | - | Not started | |
| 4. Gateway validation | - | Not started | |
| 5. Regression guards | - | Not started | |
| 6. Push audit | - | Not started | |

### Phase 1: Audio geometry and notify text

The two findings that are cheap, objectively correct and have no
design question left in them, plus the convention the later phases
are measured against. One pull request, one commit per step.

| Step | Effort | Model | Isolation | Status | Brief for sub-agent |
|------|--------|-------|-----------|--------|---------------------|
| 1a | medium | opus | none | Complete | `PlaybackStart::playable()` in `shakenfist-spice-protocol/src/messages/playback.rs` with `MIN_`/`MAX_CHANNELS` (1 to 8) and `MIN_`/`MAX_FREQUENCY` (4 kHz to 384 kHz) constants; refuse an unplayable START in the renderer's START arm with a `playback:start:unplayable` `warn_once!` and a `starts_refused` snapshot counter, leaving any running stream untouched. `format` is not validated here |
| 1b | medium | opus | none | Complete | `shakenfist-spice-protocol/src/text.rs` with `sanitise_untrusted`, replacing C0, C1, `DEL` and bidi formatting characters with `U+FFFD` and borrowing when there is nothing to replace; applied inside `Notify::message_text()` so all seven channels inherit it |
| 1c | low | sonnet | none | Complete | `STYLEGUIDE.md` "Server-chosen values" and "Server-supplied text" sections, a one-line pointer from `AGENTS.md`'s protocol-handling list, a `docs/spice-protocol.md` "Values a server chooses" section, and the `starts_refused` counter in `docs/channel-diagnostics-audit.md` and `docs/troubleshooting.md` |
| 1d | medium | opus | none | Complete | Mutation-check both fixes: break `playable()`'s bounds and `is_refused`'s character classes in turn and confirm a test fails each time (see the `mutation-check` skill). Result: 15 mutants, 15 killed by the expected tests — the two range checks and both `MIN_` constants, the renderer's refusal and its `continue`, both arms of the sanitising accessor, the borrow fast path, and each of the four refused character classes |

### Phase 2: Inventory

The point of the inventory is to find out whether three findings
in one afternoon means three or thirty. It produces a table, in
the phase plan, of every server-chosen value that escapes into
state and every server-supplied string, with a verdict each:
already bounded, bounded by narrowness, or open.

It must cover, channel by channel: `main` (agent tokens, display
sizes, mouse mode, multimedia time), `display` (stream create and
clip geometry, already largely bounded — confirm rather than
assume), `cursor`, `inputs`, `playback` (the rest of it: `MODE`,
`LATENCY`, `VOLUME`, `MUTE`), `vd_agent` (monitor config, clipboard
types and sizes), `usbredir`, `webdav`, and the generic
`port`/`spicevmc` paths. For each, the question is not "is it
parsed safely" — `BoundedReader` answers that — but "what does the
value do after it is stored, and how far from here does that
happen".

Two specific things to check, because they were noticed and not
chased: `PlaybackStart::format` is ignored entirely (the data path
assumes S16), and a 64 KiB `NOTIFY` per message is a log-volume
lever even once sanitised.

For server-supplied text, Phase 1's review swept every
`from_utf8_lossy` outside test code. Two more reached a log or an
error unsanitised and were closed in Phase 1 itself: the HTTP
proxy's status line (`quote_status_line` in
`shakenfist-spice-protocol/src/proxy.rs`, which is not a SPICE
channel and so is not in the list above) and the usbredir server
hello's version string. The rest are test code, a `dlsym` symbol
name ryll chose itself, or clipboard content the user asked for. That
grep does not find text a parser stores as a `String` by other
means, so the inventory still asks the question of every string field
in each channel above.

Also for the inventory: `sanitise_untrusted` deliberately passes
invisible characters that cannot forge a record, drive a terminal or
reorder the rendering (zero-width characters, the U+E0000 tag block,
the U+FFF9 to U+FFFB annotation marks). Decide whether hiding text
from a reader of a log or bug report is in the threat model, and if
so which of them to refuse without mangling localised text.

### Phase 3: Close the gaps

Apply the Phase 1 convention to whatever Phase 2 found, one commit
per message type, each with its refusal test and its `warn_once!`
key. If Phase 2 finds nothing open, this phase says so in one
sentence and closes — a clean inventory is a real result.

### Phase 4: Gateway validation

Issue #692 and the general rule behind it, across every path where
ryll re-emits guest-origin bytes to a third party: the RDP audio
relay, the RDP clipboard bridge (confirm it is already narrow
enough), the WebRTC media path, and the web frontend's signalling
and media. Settle open question 3 first.

### Phase 5: Regression guards

The part that makes the convention survive. Settles open questions
1 and 2 and implements the answer: a handler-level fuzz or
property target that drives arbitrary message bodies through each
channel's dispatch rather than through a parser alone, under a
memory ceiling where the platform allows one, plus whatever
`shakenfist/development` consistency audit can check the convention
across the ecosystem's repositories.

### Phase 6: Push audit

`PUSH-AUDIT.md` over the accumulated diff of every phase, per the
shared block above.

<!-- shared-block: plan-push-audit-phase v4 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did. What is done instead is at
  least the checklist below, applied to the recorded ranges: it is
  a floor, not a substitute for a runbook, and a plan adds the
  questions its own repository's blast radius raises. Each item
  gets a verdict, or a sentence saying why it does not apply.
  Do not borrow another repository's runbook instead: a runbook is
  written for one repository's blast radius.
  1. Every recorded landing takes one of the forms above: a merge
     commit with two parents, whose diff is read against its first
     parent; every commit of a direct landing, or its `first..last`
     range; a landing in another repository, whose own audit is
     cited; or a statement that no range is recoverable, naming the
     paths the audit read.
  2. Anything the diff added that runs unattended -- cron, a timer,
     a scheduled workflow, a self-update -- reports its own failure
     somewhere a human sees, or is named as not doing so.
  3. No secret or token entered any repository, and no private
     hostname or IP address entered a repository more public than
     the one it came from.
  4. Anything the diff removed or renamed has no consumer left,
     checked by grep across every repository that could consume it
     rather than by assumption, with the greps stated.
  5. Where the repository publishes something -- an image, a
     release, a package -- a broken change fails closed: one that
     publishes nothing is recoverable, one that publishes something
     wrong is not.
<!-- shared-block-end -->

In this repository the Execution phases are the *Phase order*
table each master plan carries, and `Merged` is its last column,
after `Status` — last because a row that omits it must still
reach `Status`. Every phase here lands as its own pull request,
so the cell holds one merge commit; the block's other shapes are
for repositories where a phase lands directly. See *Two ways this
runbook is invoked* in `PUSH-AUDIT.md` for what the push-audit
phase then does with those commits.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

!!! note "In this project"

    Phases involving deep protocol research, algorithm
    understanding, or architectural decisions should be planned
    at high effort. Phases that are mechanical or follow
    well-established patterns can be planned at medium effort.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

!!! note "In this project"

    A worked brief for this codebase: instead of "add tests for
    the QUIC decoder", write "add tests for `quic_decode()` in
    `shakenfist-spice-compression/src/quic.rs`. Test vectors: a
    2x2 RGBA image encoded with the reference C encoder at
    `/srv/src-reference/spice/spice-common/...`. The function
    takes `(data, width, height)` and returns
    `Option<Vec<u8>>` of RGBA pixels."

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] The code builds (`pre-commit run --all-files` or
          equivalent).
    - [ ] Tests pass (`cargo test --workspace` or equivalent).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck).
* New code follows existing patterns: channel handler
  structure, message parsing via `byteorder`, async tasks
  via tokio, event communication via mpsc channels.
* There are unit tests for new logic, and the existing tests
  still pass (`make test`).
* Lines are wrapped at 120 characters, single quotes for
  Rust strings where applicable.
* Documentation in `docs/` has been updated to describe any
  new features or configuration options — including
  `docs/spice-protocol.md` if the change adds or modifies
  channels, message types, or compression algorithms.
* `ARCHITECTURE.md` has been updated only if the shape of
  the system changed, and `AGENTS.md` only if a convention
  changed. Both are a summary and an index into `docs/`.
* If the changes affect SPICE protocol behaviour, the
  relevant documentation in `shakenfist/kerbside/docs/` has
  also been reviewed and updated if needed.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add a row to the *Master plans* table
  with the creation date, a link to the plan, a one-line
  intent summary, the initial status, and links to each
  phase plan file. Keep the table in chronological order.
* **`order.yml`** — add an entry for the new master plan
  so it appears in the documentation navigation bar. Phase
  files should *not* be added to `order.yml`.

When all phases of a plan are complete — including the
push-audit phase, and every finding it raised — update the
status column in `index.md` to *Complete*.

### Future work

* Issue #692, if the gateway phase is descoped: the finding stands
  on its own and does not need the rest of this plan.
* Open question 4 (clipboard paste injection) if a design appears
  that does not break multi-line copy.
* Open question 5: the same sweep in kerbside, which is in the
  same gateway position for the same messages.
* Per-channel message size caps (#436) are the volume half of the
  same story: this plan bounds what a value *means*, not how often
  it arrives.

### Bugs fixed during this work

The unvalidated `PLAYBACK_START` geometry and the unsanitised
`SPICE_MSG_NOTIFY` text, both in Phase 1 and neither previously
filed as an issue. Issue #692 was filed by the same review and is
owned by Phase 4.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
