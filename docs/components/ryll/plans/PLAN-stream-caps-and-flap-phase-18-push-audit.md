# Phase 18: Push audit

Parent plan:
[PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/)

## Goal

Run `PUSH-AUDIT.md` over the accumulated diff of every
stream-caps phase that landed, so the audit sees what the
capability, decoder, diagnostics and cache work did to *each
other* rather than what each phase did in isolation.  Findings
land as their own PR against `develop`, recorded in the master
plan under *Items deferred from the push audit*.  The plan is
not complete until every finding is fixed or declined in
writing.

## Planning effort

**Medium.**  The master plan's phase 18 section recommends
**low**, on the grounds that "the runbook does the work; the
phase plan is a wrapper".  That was written before anyone
measured the diff.  It is 64 files and 16 159 insertions —
8 829 of them Rust — which is roughly ten times the accumulated
diff the only previous closing audit in this repository
(`PLAN-idle-cpu-and-latency` phase 6) worked over.  A wrapper
that hands one agent a 19 000-line patch and asks for
"findings" gets a skim.  Splitting the patch, deriving the
range, and deciding what to do about the non-plan commits
inside it is the work below, and it has been done here so the
steps themselves stay light.  The master plan's effort
recommendation has been corrected at source.

## Scope

**In scope:**

- Deriving and recording the audit range for phases 1-12, 14
  and 15, and populating the master plan's `Merged` column so
  the derivation does not have to be repeated.
- Both mechanical waves of `PUSH-AUDIT.md` against that range.
- The wave 2 judgment reviews, split by area.
- Triaging every finding against current `develop`.
- Recording each finding as fixed or declined, in writing, in
  the master plan.

**Out of scope:**

- *Fixing* the findings.  Fixes land as their own PR against
  `develop`, per the master plan.  This phase's PR is the plan
  plus the master-plan corrections.
- Phases 13, 16 and 17 (parked, no code) and phase 15's
  awaiting-reproduction remainder.  Phase 15's landed
  instrumentation *is* in the range; its open investigation is
  not.
- The outstanding operator smoke tests (see *What the survey
  found*).  They gate the master plan going `Complete`; they do
  not gate this audit, which reads landed code.
- Re-auditing the crate extraction.  It predates this plan.

## What the survey found

The master plan's phase 18 section was written before any of
this was checked.  Its central premise — that the accumulated
diff "has to be assembled from their merge commits" and that
pre-convention phases must be "reconstruct[ed]" — turns out to
be true in principle and much cheaper in practice than it
sounds.  Two of its neighbouring claims were wrong and have
been corrected at source in the master plan as part of this
phase's commit; this section records what was found so a later
step does not redo it.

### The audit range is exact

Every phase of this plan landed on one long-lived branch,
`feedback-002`, in exactly **two** pull requests, and those two
merges are **adjacent on `develop`'s first-parent history** —
`cd4c7d9`'s first parent is `f22416a`.  Nothing unrelated
landed between them.

| PR | Merge | Date | Commits | Files | Insertions |
|----|-------|------|---------|-------|-----------|
| #102 | `f22416a` | 2026-05-31 | 92 | 62 | 15 348 |
| #105 | `cd4c7d9` | 2026-06-01 | 22 | 17 | 856 |

So the range is not merely recoverable, it is a plain
two-endpoint range with no filtering needed:

```
AUDIT_BASE=d416338      # f22416a^1, the develop commit before PR #102
AUDIT_HEAD=cd4c7d9      # the PR #105 merge
```

`audit-range.sh` builds `${AUDIT_BASE}...${AUDIT_HEAD}`
(three-dot).  Because `d416338` is an ancestor of `cd4c7d9`,
the merge base is `d416338` itself and the symmetric difference
equals the two-dot range.  Measured both ways: **64 files,
16 159 insertions, 623 deletions.**

Phase-to-PR mapping, for the `Merged` column:

| Phases | Landed in |
|--------|-----------|
| 1-8, 11A, 12, 14, 15 (and the 13/16/17 plan stubs) | `f22416a` (PR #102) |
| 9, 10, 11B | `cd4c7d9` (PR #105) |

This is the third distinct shape `PUSH-AUDIT.md`'s *Two ways
this runbook is invoked* section has now met — one PR
(`idle-cpu-and-latency`), and now two adjacent PRs.  The
runbook already covers it ("look at how the phases landed
before concluding the diff is unrecoverable"), so it needs no
edit this time; step 18f only fills in the table.

### The diff is large, and mostly not code

| Slice | Insertions |
|---|---|
| Rust (`*.rs`) | 8 829 |
| `docs/plans/**` | 5 683 |
| Everything else (docs, CI, Cargo, fixtures) | 1 647 |
| **Total** | **16 159** |

The five largest Rust files account for over half the code:

| File | +/- |
|---|---|
| `shakenfist-spice-compression/src/jpeg.rs` | +1 880 / -0 |
| `shakenfist-spice-compression/src/video.rs` | +979 / -0 |
| `shakenfist-spice-renderer/src/channels/display.rs` | +866 / -383 |
| `ryll/src/bugreport.rs` | +583 / -23 |
| `shakenfist-spice-renderer/src/snapshots.rs` | +530 / -17 |

This is what drives decision 3.

### Five commits in the range are not this plan's

The range is exact for *the branch*; the branch carried a
little else.  In `f22416a`:

| Commit | What | Verdict |
|---|---|---|
| `7115df8` | `ci: workflow_dispatch build for arbitrary branches` (+158, `.github/workflows/manual-build.yml`) | not this plan |
| `d723074` | `Include git sha in --version output` | not this plan |
| `6650b86`, `098bb0a` | `docs/plans/PLAN-streaming-test-automation.md` (+184) | a *different* master plan, spun out of this one |
| `d2dadb7` | `ci: fix cargo-deny failures on PR #102` | caused by this plan's new dependencies — in scope |
| `41f984a` | `docs/libvirt-spice-recommendations.md` (+468) | landed with the phase 9 plan commit — in scope |

Total genuinely-foreign content is under 400 lines across two
files plus one plan document.  Decision 2 keeps them in the
range rather than assembling a hand-filtered patch.

### The audit harness's own defects are fixed

The `idle-cpu-and-latency` audit found that three of the four
range-scoped wave 1 checks were looking at the wrong places
after the crate extraction, and that its fatal `println!` check
did not scan `shakenfist-spice-renderer/` at all — 46% of the
workspace.  Both are fixed on current `develop`:
`tools/audit/wave1-checks.sh` now derives the scan set from the
workspace members in `Cargo.toml`, and
`tools/audit/test-audit-range.sh:36` unsets `AUDIT_BASE` /
`AUDIT_HEAD` before building its scratch repository, so wave 1
no longer fails through pre-commit when the bounds are
exported.

This matters more here than it sounds.  **Most of this plan's
code lives in the two crates that check could not see** —
`shakenfist-spice-renderer` and `shakenfist-spice-compression`
hold roughly 6 700 of the 8 829 Rust insertions.  Had this
audit run before those fixes, wave 1 would have passed
vacuously over the bulk of the diff.

### The code has not moved, but it has drifted

Unlike the `idle-cpu-and-latency` audit — where the crate
extraction had relocated most of the audited code between the
diff and the audit — **all 64 files in this range still exist
at the same paths today**, checked file by file.  There is no
mapping table to maintain.

There is, however, three months of drift *within* those files,
and it is uneven.  Measured `cd4c7d9..develop` on the audited
Rust files:

| File | Since the plan landed |
|---|---|
| `ryll/src/app.rs` | +517 / -332 |
| `shakenfist-spice-renderer/src/channels/main_channel.rs` | +111 / -212 |
| `shakenfist-spice-renderer/src/channels/display.rs` | +152 / -190 |
| `shakenfist-spice-renderer/src/channels/playback.rs` | +32 / -114 |
| `shakenfist-spice-compression/src/jpeg.rs` | +20 / -17 |
| `shakenfist-spice-compression/src/video.rs` | +16 / -19 |

So the compression crate — where the largest and most
security-relevant part of the diff is — is essentially
untouched since it landed, and its findings will be live.  The
renderer channels have moved enough that a triage pass is
mandatory.  That is step 18e.

### One thing found and deliberately not fixed

Fifteen of the Execution table's eighteen `Status` cells carry
prose ("Code landed (5A-5B); 5C operator smoke test pending")
where the `plan-status-vocabulary` shared block
(`PLAN-TEMPLATE.md:144`) asks for exactly one term and nothing
else.  The table predates the block.  Rewriting fifteen rows
would discard status detail that has no other home, so it is
recorded here and handed to step 18g as a decision rather than
folded into this phase.

### One stale claim in the master plan, corrected

The phase 10 row claims the documentation catch-all landed an
"ARCHITECTURE.md capability table + README CLI flag docs".
Neither is where it says today: `ARCHITECTURE.md` mentions no
capability by name (`grep -c 'STREAM_REPORT\|LZ4_COMPRESSION'`
returns 0) and `README.md` mentions none of the CLI flags.  The
content survives — the capability table is in
`docs/spice-protocol.md`, and `--auto-snapshot-interval` is
documented in `docs/features.md:145` and
`docs/diagnostics.md:401` — it was relocated by the later
`llm-doc-structure` (PR #277) and `readme-pitch` (PR #222)
work, which is exactly what those changes were for.  The row
has been corrected to name the current locations, so step 18d's
documentation agent does not report a gap that does not exist.

### What is outstanding, and why it does not block this phase

Five operator smoke tests remain open across the plan: 2C/3H
(per-platform JPEG decode matrix), 5C (auto-snapshot), 6F
(H.264 wire smoke, blocked on an H.264-capable spice-server
build), 9E (deliberate vdagent freeze), and 11C (long-idle
soak).  Phases 13, 16 and 17 are parked and have no code;
phase 15 awaits a reproduction.

None of them changes the diff this phase audits, and none can
be run from a session — they need the operator and a guest.
The master plan's own closeout section directs that phase 18
"closes the plan out once the phases that are going to land
have landed", and all code has landed.  This phase therefore
proceeds, and the master plan stays `In progress` until the
smokes close or are declined.  **Marking the master plan
`Complete` is not part of this phase's definition of done**,
which is the one place this phase deliberately differs from the
`idle-cpu-and-latency` audit it is modelled on.

## Decisions

1. **Audit the May diff as it landed, then triage every
   finding against current `develop` before acting.**  Same
   call the `idle-cpu-and-latency` audit made, and the one a
   reviewer is most likely to argue with: auditing today's
   version of this code would produce more immediately
   actionable findings.  It is rejected because it answers a
   different question — this phase exists to ask what *this
   plan* did to the codebase, and code that someone else has
   since refactored has not thereby been audited.  The cost is
   step 18e, and the survey shows that cost is concentrated in
   the renderer channels; the compression crate barely moved.

2. **Use the range as it stands, foreign commits included,
   rather than assembling a filtered patch.**  Under 400 lines
   of genuinely foreign content across `manual-build.yml` and
   the `--version` change, plus one unrelated plan document.
   Filtering them out would cost a hand-assembled patch and buy
   very little; the alternative failure — an agent auditing the
   wrong plan, which is why the `idle-cpu-and-latency` audit
   needed a patch file — does not arise here, because both
   merges are this plan's.  Every agent brief names the five
   commits and says to skip them.

3. **Split the patch by area for the judgment agents; do not
   hand any one agent the whole 19 000 lines.**  The wave 2
   briefs in `PUSH-AUDIT.md` assume a diff an agent can hold in
   mind.  Code quality and security are split across two area
   halves each — the compression crate (decoders, caches; the
   attacker-facing surface) and the renderer/client
   (diagnostics, snapshots, channel handlers, GUI).  Test and
   documentation review stay whole, because both are
   cross-cutting questions that a split would make harder
   rather than easier.  Six judgment agents, not four and not
   sixteen.

4. **Security review gets the larger share of the budget, and
   it goes to the decoders.**  `jpeg.rs` (+1 880) and `video.rs`
   (+979) parse attacker-controlled bytes from the wire across
   four platform backends, two of which are FFI
   (`ImageIO`, `WIC`) and one of which is `dlopen`-probed
   (VA-API).  `lz4.rs` and `byte_bounded_lru.rs` decompress and
   cache on server-supplied sizes.  That is where a real
   vulnerability would be, and it is the part of the diff that
   has not been touched since it landed.

5. **Populate the `Merged` column as part of this phase, not
   the findings PR.**  It is the artefact that makes a future
   re-audit cheap, it is derived work this phase already did,
   and leaving it for a PR that may find nothing to fix would
   risk losing it.

6. **Findings land as a separate PR from this plan file**, per
   the master plan.  This phase's PR is the plan plus the
   master-plan corrections (the `Merged` column, the phase 10
   documentation locations, and the phase 18 effort level).

7. **This phase does not mark the master plan `Complete`.**
   The outstanding operator smokes are real work, and a closing
   audit that quietly flips the status would erase them.  Step
   18g records the audit outcome; the status stays `In
   progress` with the reason stated in the plan, which is what
   `docs/plans/index.md`'s own vocabulary section asks for.

## Steps

Each step is its own commit where it changes files; 18a-18e
produce findings recorded in this file rather than code.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 18a | low | haiku | none | Assemble the audit patches. `git diff d416338 cd4c7d9 > /tmp/plan-audit.patch`, then four area sub-patches from the same range, using `git diff d416338 cd4c7d9 -- <paths>`: (i) `compression.patch` — `shakenfist-spice-compression/`; (ii) `renderer.patch` — `shakenfist-spice-renderer/`; (iii) `client.patch` — `ryll/`; (iv) `docs.patch` — `docs/ ARCHITECTURE.md README.md AGENTS.md` and any other `*.md`; (v) `protocol.patch` — `shakenfist-spice-protocol/ tools/ Cargo.lock deny.toml`. The five sub-patches must together cover every file in the whole patch except `.github/workflows/manual-build.yml`, which is the one foreign file this phase excludes; print the set difference to prove it. Print the diffstat of each and of the whole. The fifth sub-patch exists because the obvious four-way split silently drops 360 insertions of this plan's own work — the SPICE capability and opcode constants in `shakenfist-spice-protocol/src/constants.rs` (+119), the name lookups in `logging.rs` (+89), the `gen-swatches-jpeg` fixture generator (+64), and the `Cargo.lock` / `deny.toml` entries for the three new decoder dependencies (+88). Expected shape, and the gate for step 18b: the whole patch is **64 files, 16 159 insertions, 623 deletions**; if it is not, the range broke and everything after this is wasted. Do not interpret anything; this step only assembles. |
| 18b | low | sonnet | none | Run wave 1: `AUDIT_BASE=d416338 AUDIT_HEAD=cd4c7d9 tools/audit/wave1.sh`. Exit codes are tabulated in `PUSH-AUDIT.md`. Two things to know before reading the output. First, wave 1's build, lint and test stages run against the **current tree**, not the audit range, so a failure there means something regressed on `develop` today rather than something wrong with this plan — say which it is. Second, the range-scoped style checks read file content **at `AUDIT_HEAD`**, i.e. at its 2026-06-01 state, so a long-line or unguarded-`log_message` hit may already have been fixed; report them, do not fix them, and mark each as needing the step 18e check. Note in the report whether the `println!`/`eprintln!` check actually scanned `shakenfist-spice-renderer/` and `shakenfist-spice-compression/` — it could not before PR #325, and those two crates hold most of this diff. If wave 1 fails on codes 1-3, stop and report; do not proceed to wave 2. |
| 18c | low | sonnet | none | Run `AUDIT_BASE=d416338 AUDIT_HEAD=cd4c7d9 tools/audit/wave2-mechanical.sh` and report its output verbatim, then add the style-conformance judgment review from `PUSH-AUDIT.md`'s *Style conformance — judgment portion* against `/tmp/plan-audit.patch`. Two areas deserve particular attention. (i) The `repaint_notify.notify_one()` pairing requirement (`docs/design-decisions.md` decision #17): this plan added event sends across the display, playback, usbredir, webdav and main channels — check every `send_event` / `event_tx.send` site in the patch has its pairing. (ii) Channel-prefix log conventions on the new diagnostic logging, which this plan added a lot of. Skip the five non-plan commits listed in this plan's survey section (`7115df8`, `d723074`, `6650b86`, `098bb0a`, and the `PLAN-streaming-test-automation.md` hunks). |
| 18d | medium-to-high | sonnet (2a, 2b, 2c), opus (2d) | none | Six judgment agents from `PUSH-AUDIT.md`, run in parallel, each given a **patch file path** rather than a revision range. Use the runbook's briefs verbatim, with the additions below. All six: the patch is from 2026-06-01; report what the patch shows and do **not** check it against the current tree — that is step 18e's job, and six agents redundantly repeating it is the waste this split exists to avoid. All six: skip the five non-plan commits named in the survey. **2a-1** (code quality, `compression.patch`) — the four JPEG backends in `jpeg.rs` and the MJPEG/H.264 dispatch in `video.rs` are the place a missed abstraction would show; check the backends against the `JpegDecoder` trait for logic that should have been shared. **2a-2** (code quality, `renderer.patch` + `client.patch`) — phase 4 expanded four channel snapshots from the same template; look for the copy-paste that implies. **2b** (test review, whole patch) — `jpeg.rs`, `video.rs`, `lz4.rs` and `byte_bounded_lru.rs` are the new-module cases; note explicitly which of the four platform JPEG backends can be tested on Linux CI and which cannot, since "untested" and "untestable here" are different findings. **2c** (documentation review, `docs.patch` plus the whole patch for context) — note that the capability table now lives in `docs/spice-protocol.md` and the CLI flags in `docs/features.md` / `docs/diagnostics.md`, having been relocated by PRs #222 and #277; do not report their absence from `ARCHITECTURE.md` and `README.md` as a gap. **2d-1** (security, opus, high effort, `compression.patch`) — the highest-value target in this audit. `jpeg.rs` parses attacker-controlled JPEG across ImageIO (macOS FFI), WIC (Windows COM), VA-API (`dlopen`-probed, with hand-rolled JPEG header parsing) and mozjpeg; `video.rs` feeds openh264 wire data; `lz4.rs` decompresses on server-supplied sizes; `byte_bounded_lru.rs` and the GLZ dictionary cap bound memory a malicious server controls. Check unchecked indexing, unbounded or attacker-sized allocation, integer overflow in size arithmetic, `unsafe` invariants and `Send`/`Sync` claims on FFI handles, and COM threading. **2d-2** (security, opus, high effort, `renderer.patch` + `client.patch`) — concurrency and resource exhaustion: the auto-snapshot tokio task and its file rotation cap, the shared `MmClock`, the vdagent probe's reply bookkeeping on the main channel, and whether any new bug-report path can be driven to unbounded disk or memory growth by the server. |
| 18e | high | opus | none | Triage. Take every finding from 18b, 18c and 18d and classify each against **current `develop`** as `still-present`, `already-fixed` or `moved`. No file mapping is needed — all 64 audited files are still at the same paths — but the drift is uneven and the survey table in this plan says where: `app.rs`, `main_channel.rs`, `display.rs` and `playback.rs` have moved substantially since `cd4c7d9`, while `jpeg.rs` and `video.rs` have barely changed, so compression findings should be assumed live until shown otherwise and channel findings should be checked line by line. For each `still-present` finding give the current file and line. Be conservative: a finding you cannot locate is `already-fixed` **only** if you can point at what fixed it; otherwise it stays `still-present` and gets a human look. Output a table: finding, source agent, severity, status, current location. |
| 18f | low | sonnet | none | Record the derivation in the master plan so it never has to be repeated. Fill the `Merged` column of `PLAN-stream-caps-and-flap.md`'s Execution table using the phase-to-PR mapping in this plan's survey section: `f22416a` (PR #102) for phases 1-8, 11, 12, 14 and 15; `cd4c7d9` (PR #105) for phases 9, 10 and 11B; `—` for the parked 13, 16 and 17 and for this phase until it merges. Put commits in that column and nothing else. While in that table, note — do not fix — that fifteen of its `Status` cells carry prose where the `plan-status-vocabulary` shared block (`PLAN-TEMPLATE.md:144`) asks for a single term; that predates the block, it is a consistency finding rather than this phase's work, and step 18g decides whether it becomes one. `PUSH-AUDIT.md` needs no edit: its *Two ways this runbook is invoked* section already tells the reader to look at how the phases landed, which is what worked here. Own commit, subject "Record where each stream-caps phase landed." |
| 18g | medium | opus | none | Management step, not a sub-agent step: review the 18e table, decide fix-or-decline for each finding, and record the outcome in the master plan under a new *Items deferred from the push audit* heading, matching the shape `PLAN-web-frontend.md` uses minus the phase number. Every finding must be fixed or declined **in writing**, with a reason for each declination. If the audit found nothing, that is one sentence and the phase is done. Also fill in the master plan's *Bugs fixed during this work* section if it is still placeholder text — eighteen phases either fixed something or did not, and both are answers. Leave the master plan's status at `In progress` with the outstanding operator smokes named as the reason (decision 7). Fixes land as their own PR against `develop`; this step only decides and records. |

## Risks and mitigations

- **The audit reports "no findings" because it looked at
  nothing.**  The failure this phase exists to prevent, and the
  empty-range guard (exit 6) only catches the degenerate case.
  *Mitigation:* step 18a prints the diffstat and this plan
  states the expected numbers (64 files / 16 159 / 623).  A
  reviewer should check those three numbers first; if they do
  not match, the range broke.
- **An area agent reports on the wrong slice.**  Six agents
  with four patch files is more bookkeeping than the runbook's
  four-with-one.  *Mitigation:* step 18a names each sub-patch
  and prints its diffstat, and each brief in 18d names the
  patch file it gets by the same name.  Step 18e will notice a
  slice nobody reported on, because it enumerates by source
  agent.
- **Stale findings burn the findings PR's credibility.**  Three
  months is long enough for some of this to be gone, and
  `app.rs` alone has turned over 849 lines since.  *Mitigation:*
  step 18e, and its rule that `already-fixed` requires pointing
  at the fix; step 18g checks that every `already-fixed` claim
  carries one.
- **The security review skims the biggest file in the diff.**
  `jpeg.rs` is 1 880 lines of four-backend FFI, which is a lot
  to ask of one agent even at high effort.  *Mitigation:*
  decision 4 gives it a dedicated opus agent with only the
  compression patch, and the brief enumerates the specific
  hazard classes per backend rather than asking for "security
  issues".  If 2d-1 comes back thin relative to that surface,
  re-run it per-backend rather than accepting the result.
- **Phase 18 quietly closes a plan that is not closed.**  Five
  operator smokes are outstanding and a closing audit is
  exactly the moment they would get lost.  *Mitigation:*
  decision 7, and a definition-of-done item that asserts the
  master plan still reads `In progress` with the smokes named.

## Definition of done

Falsifiable items only.

- `git diff --shortstat d416338 cd4c7d9` reports **64 files
  changed, 16 159 insertions(+), 623 deletions(-)**, and step
  18a's assembled patch matches.
- The four area sub-patches exist and their file counts sum to
  64.
- `tools/audit/wave1.sh` has been run with the bounds above and
  its exit code is recorded in this file.
- The wave 1 report states whether the `println!` check scanned
  `shakenfist-spice-renderer/` and `shakenfist-spice-compression/`.
- `tools/audit/wave2-mechanical.sh` output is recorded in this
  file, verbatim.
- All six wave 2 judgment agents have reported, and each report
  is either summarised in this file or its findings appear in
  the 18e table.
- The 18e table exists; every row has a status of
  `still-present`, `already-fixed` or `moved`, and every
  `already-fixed` row names what fixed it.
- The master plan's Execution table has a non-`—` `Merged`
  entry for every phase that landed (1-12, 14, 15), and `—` for
  13, 16 and 17.
- The master plan has an *Items deferred from the push audit*
  section in which every finding is marked fixed or declined
  with a reason — or a single sentence recording that the audit
  found nothing.
- The master plan's *Bugs fixed during this work* section is no
  longer placeholder text.
- The master plan's phase 18 row reads `Complete`; the master
  plan's own status in `docs/plans/index.md` still reads `In
  progress`, and the master plan names the five outstanding
  operator smokes as the reason.
- `pre-commit run --all-files` passes; `make test` passes.

## Back brief

Before executing any step, back brief the operator on the
understanding of this phase and how the intended work aligns
with it.

Three gates where the work is cheap to propose and expensive to
redo, so stop for agreement rather than proceeding:

- **After step 18a**, confirm the assembled patch is the right
  patch — the three headline numbers, and the decision to leave
  the five foreign commits inside the range rather than filter
  them out.  Every later step is wasted if this is wrong.
- **Before step 18d spawns**, confirm the six-agent split and
  which patch each gets.  Respawning six agents over a bad
  split is the most expensive mistake available here.
- **Before step 18g acts on the 18e table**, agree the
  fix-or-decline split.  Declining a finding in writing is a
  judgment the operator owns, not the audit's.

## Execution record: 2026-08-29

### Step 18a — patches assembled, and the split was wrong

`git diff d416338 cd4c7d9` gave exactly the predicted shape —
**64 files, 16 159 insertions, 623 deletions** — so the gate
passed.

The four-way split in this plan's original 18a brief did not.
It covered 57 of 64 files; seven fell through, five of them
this plan's own work: `shakenfist-spice-protocol/src/constants.rs`
(+119, the capability and opcode constants the whole plan is
about), `logging.rs` (+89), `tools/gen-swatches-jpeg/` (+64),
and `Cargo.lock` / `deny.toml` (+88, the three new decoder
dependencies). A fifth sub-patch, `protocol.patch`, was added
and the brief corrected. Coverage is now 63 of 64, the
remainder being `.github/workflows/manual-build.yml`, which is
the one foreign file this phase excludes by design.

Lesson for the next closing audit: assert that the sub-patches
sum to the whole *before* spending on judgment agents. A
missing slice is silent — the agents report on what they were
given and nobody notices what they were not.

### Step 18b — wave 1: exit 0

`pre-commit run --all-files`, `./scripts/check-rust.sh check`
and `cargo test --workspace` all pass on the current tree.

The question this phase most wanted answered came back yes:
the fatal raw `println!`/`eprintln!` check **did** scan
`shakenfist-spice-renderer/` and `shakenfist-spice-compression/`
this run. `tools/audit/wave1-checks.sh` derives its scan set
from the `Cargo.toml` workspace members and produced six
directories. The 46%-of-the-workspace blind spot the
`idle-cpu-and-latency` audit found is genuinely fixed, which
matters here because those two crates hold roughly 6 700 of
the 8 829 Rust insertions under audit.

One style finding: a 123-character line at
`ryll/src/config.rs:19`. It is the `RYLL_GIT_SHA` `--version`
line from foreign commit `d723074`, so it is out of scope. Not
carried forward.

### Step 18c — wave 2 mechanical, verbatim

```
=== wave 2a: TODO / FIXME / HACK in changed files ===
ryll/src/bugreport.rs:1352:        // TODO: Connection reports (BugReportType::Connection) today only
ryll/src/capture.rs:366:        // pre-existing behaviour. TODO: repack when source dims are odd.
shakenfist-spice-renderer/src/channels/usbredir.rs:883:            // TODO: track per-device byte counts.
shakenfist-spice-renderer/src/snapshots.rs:695:    // TODO: track per-device byte counts.
shakenfist-spice-renderer/src/snapshots.rs:700:    // TODO: track per-device byte counts.

=== wave 2a: new #[allow(dead_code)] in changed files ===
+    /// `vaTerminate`. `#[allow(dead_code)]` because we never
+    #[allow(dead_code)]
+    /// `#[allow(dead_code)]` because we never call methods on
+    #[allow(dead_code)]
+    #[allow(dead_code)]
+    #[allow(dead_code)]
(if any of the above were added in this branch, consider whether the dead code can be deleted instead)

=== wave 2b: new test count in changed files ===
new #[test] functions: 106
rust files changed: 28

=== wave 2d: security smoke ===
new unsafe{} blocks in changed files:
+        // Safety: with_data is unsafe because `options` generics
+        let source = unsafe { CGImageSource::with_data(&cf_data, None) }?;
+        // Safety: count and image_at_index are unsafe for the same
+        if unsafe { source.count() } == 0 {
+        let cg_image = unsafe { source.image_at_index(0, None) }?;
+        // Safety: CGBitmapContextCreate is unsafe because `data`
+        let context = unsafe {
+        let hr = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
+            match unsafe { CoCreateInstance(&CLSID_WICImagingFactory, None, CLSCTX_INPROC_SERVER) }
+        let stream = match unsafe { factory.CreateStream() } {

new .unwrap() / .expect() in non-test code:
+                                .expect("auto-snapshot: failed to build tokio runtime");
+            let snap = self.channel_snapshots.display.lock().unwrap();
+        let mut f = std::fs::File::create(path).unwrap();
+        f.write_all(b"fake").unwrap();
+        let tmp = tempfile::tempdir().unwrap();
+        let tmp = tempfile::tempdir().unwrap();
+        let tmp = tempfile::tempdir().unwrap();
+            .expect("wait_for_cancel must return promptly when flag already set");
+            .expect("waiter task must complete within one poll after cancel set")
+            .expect("waiter task must not panic");
+                    let guard = buf.lock().unwrap();
+        let json = serde_json::to_string_pretty(&snap).unwrap();
+        let parsed: serde_json::Value = serde_json::from_str(&json).unwrap();
+        let json = serde_json::to_string_pretty(&snap).unwrap();
+        let raw = serde_json::to_string(&PlaybackCodec::Raw).unwrap();
+        let opus = serde_json::to_string(&PlaybackCodec::Opus).unwrap();
+        let other = serde_json::to_string(&PlaybackCodec::Other(42)).unwrap();
+        let json = serde_json::to_string_pretty(&snap).unwrap();
+        let json = serde_json::to_string_pretty(&snap).unwrap();
+        let outcome = self.images.lock().unwrap().insert(image_id, pixels);
(review each: are they panic-safe given the inputs?)
```

Style conformance passed. The `repaint_notify.notify_one()`
pairing (`docs/design-decisions.md` decision #17) holds at
every site the patch adds or touches — only two are genuine
`ChannelEvent` sends; the rest of the diff's "send" hits are
the new per-opcode wire counters. Channel log prefixes are
correct and no field silently changed units.

### Step 18d — six judgment agents

All six reported. Two structural observations are worth more
than any individual finding.

**The agents corrected this plan's own premises, three times.**
The VA-API "hand-rolled JPEG header parsing" that this plan
called "the single most suspicious thing in the diff" does not
exist: `VaapiDecoder::decode` (`jpeg.rs:1253`) is a probe-only
stub delegating to an embedded mozjpeg fallback, verified
directly. The GLZ dictionary does not reimplement byte
accounting; it correctly reuses the new `ByteBoundedLru`. And
`snapshots.rs` gained zero tests — the serialisation tests this
plan sent an agent looking for live in `bugreport.rs`.

**Wave 1's green result is narrower than it reads.** The macOS
`imageio_tests` and Windows `wic_tests` do not compile on
Linux, and `.github/workflows/ci.yml:465-479` runs only
`cargo build --release -p ryll` plus a web smoke on those two
platforms — never `cargo test`. So of the "106 new tests", a
platform-gated subset has never run anywhere in CI.

### Step 18e — findings triaged against current `develop`

Fifty-one findings. Both triage agents were told that
`already-fixed` requires naming the fix, and both complied.
The headline numbers: **one HIGH survives**, four findings were
refuted outright, three were ruled out of this plan's scope,
and six are already fixed on `develop`.

The compression crate barely moved since this landed (the only
commits touching `jpeg.rs` / `video.rs` / `lz4.rs` in the
interval are comment-only), so its findings are live. The
renderer and client drifted substantially, and that is where
the already-fixed rows are.

| ID | Finding | Severity | Verified | Status | Current location |
|----|---------|----------|----------|--------|------------------|
| **S1-1** | **`ImageIoDecoder` passes no format hint or magic-byte check, so ImageIO sniffs the container and server bytes can reach TIFF/HEIF/WebP/JP2/RAW sub-decoders. The WIC sibling pins `GUID_ContainerFormatJpeg`; macOS always selects this backend.** | **HIGH** | CONFIRMED | **still-present** | `jpeg.rs:320`; contrast `:628`; selector `:1315` |
| S1-2 | `JpegDecoderRsDecoder` applies no size bound and its rustdoc falsely claims it matches `jpeg-decoder`'s internal cap (that cap is `usize::MAX`) | Medium | CONFIRMED | still-present | `jpeg.rs:91-135`, doc `:34-44` |
| S1-3 | 16384 cap still permits a 1 GiB frame; `MozJpegDecoder` double-buffers to ~2 GiB; decoded dims never cross-checked against the STREAM_CREATE rect | Medium | CONFIRMED, corrected | still-present | `jpeg.rs:44`, `:209`, `:222-225` |
| S1-4 | GLZ byte-cap eviction makes a pre-existing 100 ms per-reference stall newly reachable, and the wait is provably futile for an evicted id | Medium | CONFIRMED, split | still-present (cap half in scope) | eviction `glz.rs:65-88`; stall `:373-393` |
| S1-5 | `WicDecoder` fabricates `&mut [u8]` from `&[u8]`; UB regardless of writes, and the SAFETY comment addresses the wrong hazard | Medium | CONFIRMED | still-present | `jpeg.rs:610-613` |
| S1-6 | H.264 path has no decoded-dimension cap | Medium | OVERSTATED | still-present | `video.rs:326-334` |
| S1-7 | `JpegDecoderRsDecoder` never validates `rgba.len() == w*h*4` where `MozJpegDecoder` does | Low | OVERSTATED | still-present | `jpeg.rs:113-121` vs `:211-222` |
| S1-8 | VA-API probe allocates `vec![0; max_profiles]` from an unvalidated driver `c_int` | Low | CONFIRMED, corrected | still-present | `jpeg.rs:1131`, `:1159` |
| S1-9 | lz4 `row_bytes` unchecked and uncapped allocation | Low | CONFIRMED pre-existing | **out-of-scope** | byte-identical at `d416338` |
| S1-9b | New test codifies "truncated payload returns partial zero-filled image" as intended | Advisory | CONFIRMED | still-present | `lz4.rs:343` |
| S1-10 | DHT cached before any decode attempt and never invalidated on failure | Low | CONFIRMED | still-present | `video.rs:183-193` |
| S2-1 | Repeated `MAIN_INIT` re-emits `SessionInitialized` with no once-guard; each spawns a fresh OS thread and tokio runtime | Medium | CONFIRMED premise, severity down | still-present | `main_channel.rs:796`, `app.rs:1562`, `:1627` |
| S2-2 | Per-frame UI-thread deep clone of uncapped `streams_active` under the snapshot mutex | Medium | CONFIRMED | still-present | `app.rs:3210`, `display.rs:568`, `:1364` |
| S2-3 | Opcode maps keyed on a server-controlled `u16` (65 536 keys) cloned under the mutex on every batch and every send | Medium | CONFIRMED | still-present | six channel modules; `main_channel.rs:1126` |
| S2-4 | `STREAM_ACTIVATE_REPORT` `max_window_size` / `timeout_ms` unvalidated; the `as i32` cast sign-flips | Medium | CONFIRMED | still-present | `display.rs:113-114`, `:1689`, `:2804` |
| S2-5 | Auto-snapshot cap is a file count, not a byte budget (~1 GB at defaults); `interval` uses `Burst` | Low | CONFIRMED | still-present | `auto_snapshot.rs:130`, `:201` |
| S2-6 | Every `STREAM_CREATE` allocates a `Box<dyn VideoDecoder>`; re-CREATE overwrites without `retire_stream` | Medium | CONFIRMED | still-present | `display.rs:1343`, `:1364`, `:853` |
| S2-7 | `ByteBoundedLru` counts only `value.len()`; `image_cache_ids` collected and sorted on every snapshot | Medium | CONFIRMED | still-present | `byte_bounded_lru.rs:107`, `image_cache.rs:100` |
| S2-8 | `drain_all_pcap_bytes` holds six ring mutexes across full iterations, then materialises ~50 MB | Low | CONFIRMED | still-present | `bugreport.rs:559-577` |
| S2-9 | Auto-snapshot cancel set only at the next `SessionInitialized`; a non-reconnecting session writes forever | Low | CONFIRMED | still-present | `app.rs:1589` |
| S2-10 | `prune_to_cap` blocks the executor | Info | OVERSTATED | still-present | `auto_snapshot.rs:292` |
| S2-11 | Zero-valued CLI caps panic at startup | Low | CONFIRMED | still-present | `main.rs:974`, `auto_snapshot.rs:201` |
| S2-12 | `outstanding_agent_request_count` drifts upward | Info | CONFIRMED | still-present | `main_channel.rs:1338` |
| S2-13 | `unreachable!()` on the STREAM_DATA decode dispatch | Info | CONFIRMED | moved | `display.rs:1614` |
| Q1-1 | Dimension-validation block triplicated (structurally, not verbatim) | Advisory | OVERSTATED | still-present | `jpeg.rs:194`, `:334`, `:657` |
| Q1-2 | RGBA buffer allocation duplicated | Advisory | CONFIRMED | still-present | `jpeg.rs:345`, `:709` |
| Q1-3 | Empty-input short-circuit duplicated | Minor | CONFIRMED | still-present | `jpeg.rs:308`, `:563` |
| Q1-4 | `DecodedJpeg` and `DecodedFrame` structurally identical; one rebuilt from the other | Advisory | CONFIRMED | still-present | `jpeg.rs:29`, `video.rs:49`, `:195` |
| Q1-5 | `FnVaCreateConfig` unused typedef | Advisory | CONFIRMED | still-present | `jpeg.rs:944` |
| Q1-6 | `unsafe impl Send/Sync for VaapiDecoder` sound only by an unenforced invariant | Advisory | CONFIRMED | still-present | `jpeg.rs:1219` |
| Q1-7 | `VaapiDecoder` docstring roadmap paragraph | Advisory | OVERSTATED | still-present, narrowed | `jpeg.rs:802-812` |
| Q2-1 | Six-way copy-paste of the opcode-counter block | Advisory | CONFIRMED | still-present | six channel modules |
| Q2-2 | Baseline-assert boilerplate repeated across four snapshot tests | Advisory | CONFIRMED | still-present | `bugreport.rs:2341-2600` |
| Q2-3 | `mjpeg_duration_stats` "comment concedes the name is misleading" | — | **REFUTED** | out-of-scope | `display.rs:2777` |
| Q2-4 | 22-line doc comment attached to `wait_for_cancel` instead of `run_auto_snapshot_loop` | Low | CONFIRMED | still-present | `auto_snapshot.rs:172-197` |
| Q2-5 | `DisconnectCause` main-channel keepalive fields hardcoded `0`/`None` with no comment | Medium | CONFIRMED | still-present | `bugreport.rs:1086-1087` |
| Q2-6 | `display_cap_name()` omits bits 6 and 12, both in `DEFAULT_DISPLAY`; `_ => None` hides it | Low | CONFIRMED | still-present | `logging.rs:552-565` |
| Q2-7 | `capture.rs` odd-dimension TODO | — | CONFIRMED pre-existing | out-of-scope, since removed | gone; `b3bbf72` |
| Q2-8 | Review-process metadata in shipped comments | Advisory | CONFIRMED, partly fixed | still-present, reduced | `main_channel.rs:1463`; one removed by `f1b307c` |
| Q2-9 | Three duplicate TODOs; `bytes_to_guest`/`bytes_from_guest` always zero in the payload | Low | CONFIRMED | moved | `snapshots.rs:690`, `:695` |
| T-1 | 137 new `assert!(json.contains(…))` substring assertions; exactly one converted to a typed assertion | Low | CONFIRMED | still-present | `bugreport.rs`, 190 now vs 53 at base |
| T-2 | No test exercises `MAX_DECODED_JPEG_DIMENSION` | Advisory | CONFIRMED | still-present | no test references the constant |
| T-3 | The dimension cap is inline in `cfg`-gated bodies, duplicated per platform | Low | **PARTLY REFUTED** — the "zero CI coverage" half is false | still-present (duplication only) | `jpeg.rs:333`, `:657` |
| T-4 | "The macOS/Windows CI matrix never runs `cargo test`" | — | **REFUTED** | not a defect | `ci.yml:532-533` |
| T-5 | No zero-length-packet test through the `VideoDecoder` wrapper | Advisory | CONFIRMED | still-present | `video.rs:953` |
| T-6 | No cascading-eviction or churn test for `byte_bounded_lru` | Advisory | CONFIRMED | still-present | `byte_bounded_lru.rs:290` |
| T-7 | lz4 overflow guards untested | Advisory | CONFIRMED | still-present | `lz4.rs:388` |
| T-8 | `swatches.jpg` fixture used only by macOS/Windows tests | Advisory | CONFIRMED | still-present | `jpeg.rs:1535`, `:1653` |
| T-9 | `make test-qemu` recommended; no unit test stands up a real SPICE session | Advisory | CONFIRMED | still-present | process |
| D-1 | "phase \<number\>" references in non-plan docs | — | **REFUTED** on current `develop` | **already-fixed** | 0 hits; `d1b2f60`, `7332cb7`, `f1b307c` |
| D-2 | Video-decode build dependencies undocumented | Low | OVERSTATED | mostly fixed; narrow gap live | see below |
| D-3 | README auto-snapshot subsection | Advisory | CONFIRMED at patch time | already-fixed | `docs/features.md:145` |
| D-4 | ARCHITECTURE.md auto-snapshot subsection | Advisory | CONFIRMED at patch time | already-fixed | `docs/diagnostics.md:165` |
| D-5 | New opcodes may warrant a kerbside doc review | Info | not checked | out-of-scope | — |
| W-2 | `wave1.sh`'s two range-scoped checks disagree about what "the audit range" means: one greps the live tree, the other reads `AUDIT_HEAD` | Low (tooling) | CONFIRMED | still-present | `wave1.sh:143` vs `:176` |

Four results changed the picture enough to record separately.

**S1-1 is the only HIGH, and the asymmetry is the argument.**
The Windows backend pins `GUID_ContainerFormatJpeg`; the macOS
one passes `None` and lets ImageIO sniff. Two backends written
days apart for the same job disagree about whether to constrain
the container, and the permissive one is the only backend
macOS ever selects. That is a one-line fix with a clear
precedent inside the same file.

**D-1 was the loudest finding and it is entirely gone.** The
documentation agent reported "phase \<number\>" references
across five non-plan documents and rated it blocking; the
triage agent re-ran the check against current `develop` and
found **zero** hits, cleaned by `d1b2f60`, `7332cb7` and
`f1b307c`. This is what the triage step is for, and it is the
strongest argument in this plan's favour for auditing the
historical diff and triaging afterwards rather than skipping
straight to today's tree.

**D-2 inverted on inspection.** Most of the removed AGENTS.md
build-dependency content *was* re-homed. What survives is
narrower and worse than a gap: `docs/development-macos.md:214`
still says the openh264 crate "downloads a pre-built library at
build time", which was true of older releases and is false of
the pinned `openh264-sys2 0.9.8`, which compiles vendored
source. So the document offers a remedy for a failure mode that
no longer exists and hides the one that does — a missing C
toolchain, which `docs/development.md:62-69`'s `apt-get` list
also omits. The 2c agent's blanket "all accuracy checks passed"
did not catch this.

**T-3 and T-4 were wrong, and the management session
repeated them before checking.** Recorded here rather than
quietly deleted, because the failure mode is the one this
whole phase exists to guard against — a confident finding
that nobody verified.

The claim was that `.github/workflows/ci.yml` runs only
`cargo build` plus a web smoke on macOS and Windows, so the
platform-gated decoder tests never execute anywhere. In fact
`ci.yml:532-533` carries an unguarded
`cargo test --workspace ${{ matrix.features }}` step that
predates this plan (`a488b39`) and runs on every matrix
entry. `imageio_tests` and `wic_tests` are gated only on
`target_os`, not on any feature, so both compile and run on
their legs. The triage agent read the matrix *definition* at
lines 465-479 and inferred the steps list from it without
reading down to line 532; the management session relayed
that twice without checking.

What survives is narrower and real: those legs run in the
merge tier (`merge_group` / `workflow_dispatch`), not per
pull request, so platform coverage arrives at merge time;
and **T-2** is true — no test anywhere fed the guard an
oversized image. The platform-independent helper is still
worth having, for the duplication and for T-2, but not for
the reason the audit gave.

**T-3's severity as originally recorded.** `MAX_DECODED_JPEG_DIMENSION` is an
allocation bound against hostile input, and on two of three
platforms it is enforced by code that no CI job compiles, let
alone runs. Factoring the check into a platform-independent
helper fixes Q1-1 and S1-2 in one change, and gives T-2
somewhere to live. It is still the highest-leverage item in
this audit; the CI justification for it was not real.

### Step 18f — the derivation recorded

The `Merged` column now names `f22416a` (PR #102) for phases
1-8, 12, 14 and 15, `cd4c7d9` (PR #105) for phases 9 and 10,
both for phase 11 (11A and 11B landed separately), and keeps
the em dash for the parked 13, 16 and 17. A paragraph below the
table records the range derivation and the five foreign commits
inside it.

### Step 18g — the review round on PR #333

The fixes branch was opened as
[PR #333](https://github.com/shakenfist/ryll/pull/333). The
automated reviewer raised six fix items and four suggestions;
all ten were addressed. The plan commits, originally on a
separate `stream-caps-and-flap-phase-18-push-audit` branch with
no PR, were cherry-picked onto the fixes branch afterwards so
the audit's record and the code it describes review together —
splitting them was a mistake, and `CLAUDE.md` says as much.

Four of the review's findings were defects this audit missed,
which is worth recording plainly: the audit bounded what a
server could make a channel hold, and then left a re-`STREAM_CREATE`
able to destroy a live stream (item 4), a duplicate-session guard
defeated by alternating server-chosen ids (item 2), a `join()` on
the egui UI thread (item 1), and a diagnostics regression where
common server opcodes stopped getting their own map entries
(item 3). Bounding a structure and bounding the *path* that
mutates it are different jobs, and wave 2's per-file ownership
split made the second harder to see.

Two of the review's suggestions did not survive checking, and
the accurate fix was taken instead:

- **`as_chunks` → `chunks_exact`** was proposed to avoid an
  undeclared MSRV bump. Clippy's `chunks_exact_to_as_chunks`
  lint forbids it and the workspace builds with `-D warnings`,
  so the reviewer's own alternative was taken: `rust-version
  = "1.88"` is now declared at the workspace root and inherited
  by all six crates. The floor was real and undeclared; it now
  fails with cargo's MSRV error rather than in a packaging lane.
- **Hoisting `video::for_stream` above the teardown** would
  have fixed item 4 while letting a flood of `STREAM_CREATE`s
  past the cap drive an openh264 allocation per message. The
  order is instead cap check → decoder → teardown → insert,
  with the cap exempting ids already held.

The `bytes_from_guest` asymmetry (item 10) was also narrower
than reported — both directions count control traffic once a
device is attached — so the docstring was corrected rather than
the counting.

Two QEMU runs (`make test-qemu`, `make test-qemu-desktop`)
exercised the changed auto-snapshot machinery under a saturated
display channel (2123 decodes, 13.8 MB in 90 s, six auto-snapshot
zips on exact cadence, no UI stall). Neither guest promotes a
video region, so `streams_created_total` stayed 0 and the
STREAM_CREATE reordering is **not** verified against a real
server; its evidence is the five unit tests, one of which was
confirmed to fail against the previous ordering. Giving a test
guest a video workload is the follow-up that would close this.

### Closeout: 2026-08-30

PR #333 merged as `00219c0`. That was the last unmet item in
the Definition of done — the master plan's phase 18 row now
reads **Complete** with a `Merged` entry, and every other
done-criterion was already satisfied when the PR landed.

The master plan's own status in `docs/plans/index.md` stays
**In progress**, which is correct: five operator smokes (3H,
5C, 6F, 9E, 11C) and three parked video phases (13, 16, 17)
outlive this audit. None of them is agent-actionable, so the
plan's remaining work is operator-side from here.
