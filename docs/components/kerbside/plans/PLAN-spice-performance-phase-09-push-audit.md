# SPICE performance phase 9: push audit of phases 6 and 7

Master plan: [PLAN-spice-performance.md](/components/kerbside/plans/PLAN-spice-performance/).

## Planning effort

This plan was written in a high-effort session. The master plan
gives the phase no effort level, only the push-audit shared block.
Review effort: medium. The judgment is almost entirely in the
range -- the survey found landed, unaudited work the master plan's
phase 9 section does not mention -- and in weighting the review by
blast radius rather than by line count. The steps themselves
follow the runbook.

## Scope

In scope:

- Running kerbside's `PUSH-AUDIT.md`, waves 1 and 2, over the two
  landings under *Audit range*: phase 6's follow-up (#701) and
  phase 7 (#716).
- The shared block's floor checklist, items 1 to 5, for kerbside.
- Deciding, for each `Future work` entry in this plan's phase
  files and the master plan, whether it has a live home; and
  listing those that do not (decision 5).
- Triaging every finding as blocking or advisory, with the reason,
  in this plan's *Outcome*.
- Filing problems that predate the plan as issues.

Out of scope:

- Fixing findings. They land in a follow-up pull request after
  this one merges (decision 4), which also marks the plan
  `Complete` if phase 2 has landed by then.
- Phases 1 to 5, and the kerbside-patches landings #1748 and
  #2024: audited by phase 6, which this phase cites rather than
  repeats (decision 1).
- ryll #406, audited by phase 6 step 6g under ryll's own runbook.
  Phase 7 landed nothing in ryll (survey finding 5), so no second
  repository is in range.
- Phase 8, which was abandoned and landed nothing.
- Wiring `tools/quic-bench/` into CI. Whether that should happen
  is a finding for triage; an audit phase does not change code
  (decision 2).
- Phase 2's dri-devel submission, which is still unsent and whose
  audit is the upstream review.

## Audit range

### kerbside

| Landing | Read as | Size | What it is |
|---------|---------|------|------------|
| 17a31ebf75 (#701) | `17a31ebf75^1..17a31ebf75` | 26 files, 736+/114- | Phase 6's follow-up: the fixes its own audit called for |
| e0f53fb04a (#716) | `e0f53fb04a^1..e0f53fb04a` | 20 files, 5084+/26- | Phase 7: the rig's loss term and the transport bench |

Both are merge commits, so each is read against its first parent,
which is the whole of what landed.

### Cited, not repeated

- Phases 1 to 5, kerbside: phase 6's audit, recorded in
  [its plan](/components/kerbside/plans/PLAN-spice-performance-phase-06-push-audit/)
  (2a34fe7ea9, #668).
- kerbside-patches #1748 and #2024: phase 6 step 6h, floor
  checklist.
- ryll fdb5ead5b4 (#406): phase 6 step 6g, under ryll's runbook.
- andris #56: audited in its own pull request body.

## What the survey found

**1. Phase 6's follow-up landed unaudited, and it is shipped
code.** This is the finding that reshapes the phase. The master
plan's phase 9 section says the phase "audits phase 7's
accumulated diff ... and cites phase 6 for phases 1-5". But phase
6's audit merged on 2026-10-31 as #668, and the follow-up pull
request it called for merged seven days later, on 2026-11-07, as
17a31ebf75 (#701). Nothing audited that follow-up: a grep for
`17a31eb` and `#701` across `docs/plans/` finds them only in the
master plan's `Merged` cell for phase 6. It is 736 insertions over
26 files and, unlike phase 7's, most of it ships --
`rust/kerbside-proxy/src/caps.rs` (+135),
`main.rs` (+95), `ackwindow.rs` (+61), `relay.rs` (+60),
`backend.rs` (+20), `kerbside/config.py` (+30) and
`tools/build-proxy-wheel.sh` (+49). The shared block is explicit
that the final phase audits the accumulated diff of every phase
rather than the last phase alone, precisely so that work landing
between audits is not missed. Corrected at source in the master
plan's phase 9 section and the `index.md` row as part of this
planning commit.

**2. Nothing automated compiles or tests the phase's Rust.**
`tools/quic-bench/` is 1,162 lines of Rust outside
`Cargo.lock`, and no repository gate touches it.
`.github/workflows/rust.yml` triggers on `rust/**` plus four named
`tools/` scripts, none of them this one, and runs with
`working-directory: rust/kerbside-proxy`; nothing anywhere in
`.github/` mentions `quic-bench`; there is no Rust hook in
`.pre-commit-config.yaml`; and wave 1's own gates are tox, meaning
flake8 and py3. The crate is a standalone cargo project -- there
is no root `Cargo.toml` and it is not a workspace member -- whose
only Rust gate is a hand-run `make lint`, `make test` and
`make mutants` in Docker. Its shell and Python are gated:
shellcheck matches `^(tools|demo)/` and flake8 matches `^tools/`,
so `run-bench.sh`, `mutation-check.sh` and `summarise.py` are
covered. Wave 1 would therefore pass vacuously on the Rust, which
is why decision 2 names a substitute gate.
- *Corrected 2026-11-12 during execution:* "no repository gate
  touches it" was too strong. CodeQL does. `codeql-analysis.yml`
  pins `languages: actions, javascript-typescript, python, rust`
  with no path configuration, and its run on the phase 7 merge
  found both Cargo manifests and extracted 30 Rust files without
  error -- exactly the 24 under `rust/kerbside-proxy/src/`, plus
  `build.rs`, plus the crate's 5. Its Python extractor took
  `tools/quic-bench/summarise.py` as well. The narrower claim is
  the one that matters and still holds: nothing builds, tests or
  lints the crate. CodeQL neither compiles it -- it logged
  "unable to determine project Rust toolchain" -- nor runs its
  tests, so a broken build or a failing test still lands unseen.

**3. A phase-1 page states one thing phase 7 disproved, and the
finding as first written overstated which.** This entry was
rewritten on 2026-11-12 after step 9d refuted most of it; the
corrected version is below, and what it replaced is recorded in
*Execution notes*.

What is wrong is one sentence.
`docs/performance/proxy-backpressure.md:253` says BBR "was not
measured" because "This host's kernel has only `reno` and `cubic`
loaded". That is false in the present tense -- the host now
reports `reno cubic bbr`, which
`docs/performance/quic-transport-bench.md` records -- and, more
to the point, it reads as the reason BBR *cannot* be measured
here, where phase 7 found the module shipped with the kernel and
simply not loaded. The rest of the bullet survives: loading it
needs root, and an unprivileged namespace cannot autoload it,
which the master plan's correction already says.

What is not wrong is the prediction. "On these profiles BBR could
only trim the bottleneck queue (about 20 ms at 20 ms RTT, one
BDP)" is a statement about queue depth, and phase 7's 171 to
104 ms no-loss cut on 80 ms / 10 Mbit is roughly one BDP at that
RTT, so it illustrates the mechanism rather than refuting it.
Phase 7's throughput result is a loss effect: at 0% loss the two
controllers tie, and phase 1's rig had no loss term at all, since
phase 7 added it. Nor can phase 7 touch "small next to the
ACK-window backlog", because its bench has no spice-server and no
ACK window.

The first draft of this finding also said phase 7's numbers were
"not phase 1's profiles". That is wrong: `PROFILES='20:50 80:10
80:50'` at `docs/performance/proxy-backpressure.md:87` includes
80 ms / 10 Mbit. Only 150 ms / 2 Mbit is new.

**4. A discoverability gap, not a stale page.** Also rewritten on
2026-11-12 after step 9d refuted half of it. The loss-term half
was wrong: `docs/testing.md` says in terms that "The header of
`run-matrix.sh` documents every variable", and phase 7 documented
the `:loss_pct` field in that header at length, so the page
delegates by design and is not stale. Its one `PROFILES=0:0` is
an incidental example of an unshaped case, not a field reference.
What survives is advisory: `tools/quic-bench/` appears nowhere
outside `docs/plans/` and the two measurement pages, and
`docs/testing.md`'s shaped-link material is a subsection of its
Ryll section -- which the bench does not belong in, having no
Ryll, no guest and no SPICE. The measurement page owns the
bench's invocation; a one-line cross-reference would help
discovery, and that is the whole of the finding.

**5. The premise that holds.** Phase 7 landed nothing in ryll:
its diff touches neither `tools/shaped-link/ryll-surface-drawn-rect.patch`
nor `build-ryll.sh`'s `RYLL_REF` pin, both of which its execution
notes record as deliberately untouched once steps 5a, 5b and 6
became void. Phase 8 landed nothing at all. So the master plan's
"all of it in Kerbside" is correct, even though its scope is not.

**6. Two `Future work` entries phase 7 asked to have tracked have
no home.** As first written this said "two entries have no home",
which step 9d showed is false: about fifteen have none, nearly
all of them the master plan's own long tail. The defensible
version is narrower. Two entries are ones phase 7 itself flagged
as deserving a home -- the kernel-BBR finding, "worth its own
plan", and the missing `ticket-valid-until` in the `.vv`
template, "worth an issue" -- and `gh issue list --state all
--search` finds no issue for either. Phase 9 is the plan's last
phase, so an entry with no home dies with the plan when it
retires.

**7. Floor item 3 looks clean mechanically.** The addresses in the
files phase 7 added are RFC1918 rig addresses inside unprivileged
namespaces -- 10.77.0.x for `shaped-link`, 10.78.0.x for
`quic-bench`, deliberately distinct -- plus `0.0.0.0` and
loopback. No home or scratchpad paths, no hostnames. The audit
confirms rather than discovers this, and extends it to #701.

## Decisions

1. **The range is #701 and #716, not phase 7 alone.** Survey
   finding 1. Auditing phase 7 by itself would leave shipped
   proxy code permanently unaudited, which is the failure the
   shared block's accumulated-diff rule exists to prevent. Phases
   1 to 5 are cited rather than re-read: they were audited, the
   audit's findings were fixed, and re-reading 5,000 lines of
   already-audited diff would buy less than reading the 736 lines
   that fixed it.

2. **The crate's own `make` targets stand in for wave 1's Rust
   gate.** Survey finding 2 means wave 1 cannot speak for
   `tools/quic-bench/`. Step 9a runs `make lint`, `make test` and
   `make mutants` in Docker and reports them as the substitute,
   naming them as hand-run rather than enforced. The back brief
   settled what the finding then recommends: wire the crate into
   `rust.yml`, with the wiring itself follow-up work rather than
   part of this pull request. The argument against is the one
   review round one on #716 made when it declined CI wiring for
   `summarise.py` -- the crate is a measurement tool for a closed
   experiment. The argument that won is phase 7's own *Future
   work*, which names `tools/quic-bench/` as the bench to re-run
   the kernel-BBR question with: a tool with a future needs a
   gate.

3. **Weight the review by blast radius, not line count.** #716 is
   5,084 insertions of standalone tool that never enters the wheel,
   never runs unattended and has no untrusted input. #701 is 736
   insertions, most of them in the shipped proxy, including 135
   lines in `caps.rs`, which parses a client's link message before
   the backend sees it. The security step reads #701 at high
   effort on opus; `quic-bench`'s security question is what the
   tool does to the host that runs it.

   - *Corrected 2026-11-12 by steps 9c and 9e, independently:*
     `caps.rs` does not parse the link message. #701's code there
     is `backend_channel_caps`, a one-line `.to_vec()`
     pass-through, plus tests and about 83 lines of module doc;
     every bound on the wire -- the 4096-byte link message, 16
     capability words, the checked capability offset, the
     handshake timeout -- is in ryll's protocol crate, which
     #701 relies on and does not change. The weighting still
     stands, because what #701 changed governs which
     capabilities reach the backend, but the reason given for it
     was wrong.

4. **Findings are not fixed here.** The shared block puts them in
   their own pull request against the default branch. That
   includes survey findings 3 and 4, which are findings this phase
   records rather than edits it makes.

5. **Every `Future work` entry gets checked for a live home.**
   Phase 6 step 6e set the precedent. Because phase 9 is the last
   phase, the check covers all six phase files and the master
   plan, and anything without a home is listed in *Outcome* for
   Michael to file. The audit does not file issues without
   showing him the wording first.

6. **Phase 9 marks itself `Complete` and the plan stays
   `In progress`.** A push-audit phase closes itself out in its
   own pull request and records no `Merged` cell. The plan cannot
   reach `Complete` while phase 2's kernel fix is unsubmitted, so
   the plan's own status stays `In progress` with phase 2 named as
   the reason.

7. **The record goes in this plan's *Outcome*.** One place, as
   phase 6 did, so a reader finds the verdicts next to the range
   they were reached against.

## Step plan

Step 9a is a gate: wave 2 is only worth spending on if wave 1
passes, as the runbook says. Steps 9b to 9e are read-only readers
and run in parallel once 9a has passed. 9c works on a copy of the
tree in its own scratch directory, so it does not collide with
the others.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | low | sonnet | none | Wave 1, kerbside, over both landings in *Audit range*, oldest first: run `AUDIT_RANGE="$M^1..$M" tools/audit/wave1.sh` with `AUDIT_PATHS` unset for `M` of `17a31ebf75` then `e0f53fb04a`, and report each exit code with its advisory output (codes are in `PUSH-AUDIT.md` under Wave 1). Do not use `plan-range.sh`, and never change a range to make a run pass; if either run exits 6, stop and report. Then the substitute Rust gate decision 2 names, because wave 1's gates are tox and cannot see `tools/quic-bench/` (survey finding 2): in `tools/quic-bench/`, run `make lint`, `make test` and `make mutants` under `timeout 1800` each, in Docker, and report each one's result with the test count and the killed/survived mutant counts. A surviving mutant is a finding. Confirm independently, with `grep` over `.github/` and `.pre-commit-config.yaml`, that no automated gate covers that crate's Rust, and say what you checked. Finally, for `rust/kerbside-proxy`, find `rust.yml`'s push run on `17a31ebf75` with `$HOME/bin/ci-status shakenfist/kerbside runs --branch develop --limit 200` and report its conclusion and run ID. Report in under 350 words. |
| 9b | medium | sonnet | none | Wave 2 mechanical, plus the 2a code-quality brief in `PUSH-AUDIT.md`, over both landings. Run `AUDIT_RANGE="$M^1..$M" tools/audit/wave2-mechanical.sh` once per landing and report its output; it never exits non-zero on findings. The shared blocks named in 2a bind: `python-version-discipline`, `comment-proportion`, `source-file-size` and `plan-references-in-code`. Material for the size block: `tools/quic-bench/src/main.rs` (474), `run-bench.sh` (417), `summarise.py` (339), `src/workload.rs` (293), `src/quic.rs` (276), and in #701 `caps.rs` and `main.rs` as they now stand. The cross-phase question, which is the one worth your time: `tools/shaped-link/run-matrix.sh` and `tools/shaped-link/summarise.py` were changed by both #701 and #716, and `run-matrix.sh` is 700-odd lines across five phases now -- does it carry modes, profile fields or code paths that no caller and no page uses any more, and did #716's loss term leave a path that only an old invocation reaches? Second: `tools/quic-bench/summarise.py` and `tools/shaped-link/summarise.py` are sibling scripts in the same repository; do they duplicate logic that should be shared, or is the duplication deliberate? Answer that one on the evidence, not on principle. Report blocking or advisory per finding, with file and line, in under 450 words. |
| 9c | medium | opus | none | 2b test review over both landings, under the `functional-test-coverage` shared block. Work on a copy of the tree in your scratch directory; never mutate the worktree, and restore from the copy rather than with `git checkout <dir>`. Each mutant runs under `timeout`, and a timeout counts as killed. #716 added nine unit tests in `tools/quic-bench/src/main.rs` and a mutation script, `tools/quic-bench/mutation-check.sh`, which claims seven mutations and seven kills. Re-run that script and report its result; then answer, per test, whether it fails when the property it names is broken, and name the mutation you used. Then the question the script does not answer: `summarise.py` is the code that produced every published table, and it has no tests at all -- list the paths in it that a published number passed through and say which could change a published figure without any test noticing. Note that review round one on #716 declined to add fixture tests for it, on the grounds that the independent recomputation in the phase's execution notes is the real check; say whether you agree, on the evidence. For #701, check the guards it added: `caps.rs`'s capability rewriting including a malformed or oversized link message, `ackwindow.rs` holding the backlog at 2W+1, and `config.py`'s validation. Run `make test` in a copy's `rust/kerbside-proxy/` under `timeout 900`, at most eight Rust mutants in all. Keep the mutations in a script and give its path. Report in under 450 words. |
| 9d | high | opus | none | 2c documentation review over both landings, under its four shared blocks (`readme-discipline`, `llm-doc-discipline`, `diagram-discipline`, `plan-phase-references`). Three questions first, each of which this plan records as a survey finding you must confirm or refute independently rather than accept. One: `docs/performance/proxy-backpressure.md` around line 253 says this host's kernel has only `reno` and `cubic` loaded, and predicts BBR could only trim about 20 ms of queue; phase 7 measured 9.27 Mbit against 1.64 at 1% loss and p95 104 ms against 171 ms at no loss, on 80 ms / 10 Mbit and 150 ms / 2 Mbit. Read both pages and the master plan's dated correction, and say which statements are now false, which are merely about different profiles, and which page owns each claim. Two: `docs/testing.md` teaches `tools/shaped-link/` but was not touched by #716 -- does it need the loss term and a pointer to `tools/quic-bench/`, or does the measurement page own that? Three: check every `Future work` entry in all six `PLAN-spice-performance-phase-*.md` files and the master plan for an issue or somewhere live that still tracks it, reading GitHub only with `gh issue view`, `gh issue list` and `gh pr view`; the kernel-BBR and `ticket-valid-until` entries are expected to have none. Then hold the pages together: the eleven pages under `docs/performance/`, `docs/index.md`, `docs/testing.md`, `ARCHITECTURE.md` and `AGENTS.md`. Does any page still state something a later phase overturned, beyond question one? Do `docs/index.md`'s two new entries describe what the pages say? Grep `README.md`, `AGENTS.md`, `ARCHITECTURE.md` and `docs/` outside `docs/plans/` for "phase <number>" and for named people. Report blocking or advisory per finding, in under 550 words. |
| 9e | high | opus | none | 2d security review, weighted by decision 3. The material that ships is #701: `caps.rs` (+135), which parses and rewrites an untrusted client's link message before the backend sees it; `ackwindow.rs` (+61) and `relay.rs` (+60), which count client ACKs and server messages; `main.rs` (+95); `backend.rs` (+20); `kerbside/config.py` (+30); and `tools/build-proxy-wheel.sh` (+49). Check every length, count and offset taken from the wire for bounds and overflow, what a truncated or oversized link message does, unbounded per-connection state, whether a client-supplied value reaches a log line, and whether an out-of-range config value reaches `setsockopt` unchecked. The `path-traversal-review` shared block binds. For #716 the question is different, because nothing in it ships, enters the wheel or takes untrusted input: it is what the tool does to the host that runs it. Read `tools/quic-bench/run-bench.sh` and #716's changes to `tools/shaped-link/run-matrix.sh` for commands run as root, `tc` or namespace state left behind on failure, temporary paths another user on the host could pre-create before the script writes them, and the TLS key material the bench generates -- the script does `chmod 700` on its TLS directory and checks certificate validity, so say whether that is sufficient and whether the key can be read by another user at any point. Then the floor checklist, items 2 to 5, over both landings, each with a verdict or a sentence saying why it does not apply: 2, does anything added run unattended, and does it report its own failure where a human sees it? 3, did either landing put a secret, token, private hostname, address other than loopback or documentation ranges, username or home or scratchpad path into this public repository -- including the figures and command output quoted in the two new pages? 4, did either remove or rename anything with a consumer left, checked by grep across this repository and `../ryll`, with the greps stated? 5, this repository publishes a wheel and images, so does a broken change in #701's `build-proxy-wheel.sh` fail closed? Report severity, file, line, class and fix per finding, in under 550 words. |
| 9f | high | (management session) | n/a | Triage and record. Take 9a to 9e's reports and reproduce every finding against the tree before accepting it; an agent's report is model output, not evidence. Classify each as blocking or advisory and decide: fix in the follow-up pull request, decline with the reason in writing, or file as an issue if it predates the plan (`git log -S` or blame against the two landings). Show Michael the wording of any issue before filing it. Give a verdict on each floor item 1 to 5 for kerbside. Record the back brief's recommendation that `tools/quic-bench/` be wired into `rust.yml`, and say whose follow-up that is. Write it all into this plan's *Outcome*, including the `Future work` entries with no home, and run the private-data probe set over the text this phase adds before committing. Mark phase 9 `Complete` in the master plan and `docs/plans/index.md` with no `Merged` cell, and leave the plan `In progress`, naming phase 2 as the reason (decision 6). Commit subject: "Record the phase 6 and 7 push audit." |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The survey's three doc findings are mine, and an agent told they are findings will confirm them rather than check them. | 9d's brief states each as a claim to confirm or refute independently, names the pages that own each claim, and asks which statements are merely about different profiles. 9f reproduces before accepting. |
| #701's range was recorded in a `Merged` cell but never audited, so the same could be true of a landing I have not noticed. | 9a's floor item 1 verdict covers every recorded landing in the plan, not just the two in range, and reports any whose form or audit it cannot account for. |
| A surviving mutant in `mutation-check.sh` would mean #716's tests do not hold, after a review round that cited them. | 9a reports killed and survived counts explicitly and a survivor is a finding; 9c re-derives the kills independently rather than trusting the script. |
| The crate has no automated gate, so a later change to it breaks nothing visibly. | Named as survey finding 2, and the back brief settled it: the audit recommends a CI gate, so the gap leaves the plan as a recommendation rather than an observation. |
| An audit phase quietly turns into a fixing phase, as the material is small and the fixes look cheap. | Decision 4. Findings land in a follow-up pull request; 9f records, and does not edit. |
| Phase 2 is still open, so the plan cannot complete and the audit could be read as closing it. | Decision 6: phase 9 `Complete`, plan `In progress` with phase 2 named. |

## Definition of done

- `AUDIT_RANGE` for each of `17a31ebf75^1..17a31ebf75` and
  `e0f53fb04a^1..e0f53fb04a` was run through `tools/audit/wave1.sh`
  and `tools/audit/wave2-mechanical.sh`, with every exit code
  recorded in *Execution notes*, and no range was altered to make
  a run pass.
- `make lint`, `make test` and `make mutants` in
  `tools/quic-bench/` are recorded with their test count and
  their killed and survived mutant counts. Any survivor appears
  in *Outcome* as a finding.
- Each of the nine unit tests #716 added is listed with the
  mutation that kills it, or named as unguarded.
- *Outcome* gives a verdict, or a sentence saying why it does not
  apply, for each of floor items 1 to 5, for kerbside.
- *Outcome* states, for `docs/performance/proxy-backpressure.md`'s
  BBR bullet, which of its statements are false, which are about
  other profiles, and which page owns the claim -- with file and
  line.
- Every `Future work` entry in the six phase files and the master
  plan is listed in *Outcome* with its live home, or named as
  having none. The kernel-BBR and `ticket-valid-until` entries
  carry wording Michael can file as issues.
- *Outcome* recommends wiring `tools/quic-bench/` into `rust.yml`
  and names whose follow-up that is.
- Every finding is classified blocking or advisory and dispositioned
  fix, decline or file, with the reason, in *Outcome*.
- `grep -rn 'phase [0-9]'` over `README.md`, `AGENTS.md`,
  `ARCHITECTURE.md` and `docs/` outside `docs/plans/` finds no
  plan citation either landing introduced.
- The private-data probe set was run over the text this phase
  adds, and *Execution notes* says so.
- Phase 9 reads `Complete` in both the master plan's Execution
  table and `docs/plans/index.md`, with no `Merged` cell, and the
  plan's own status reads `In progress` with phase 2 named as the
  reason.

## Future work

- Nothing yet. Findings the audit declines or defers are recorded
  in *Outcome* with the reason, and anything worth tracking
  beyond this plan becomes an issue at step 9f.

## Execution notes

Entries are added as the steps run, per `PLAN-TEMPLATE.md`: the
date with its timezone, any step run out of order, any decision
that changed, and the wave exit codes.

- **2026-11-12, AEDT.** All six steps ran on 2026-11-12. 9a ran
  first as the gate and passed; 9b, 9c, 9d and 9e then ran in
  parallel as read-only readers; 9f is this record. No step ran
  out of order and no decision changed during execution, beyond
  the corrections below.
- **2026-11-12, AEDT.** Wave exit codes: `wave1.sh` 0 for
  `17a31ebf75^1..17a31ebf75` and 0 for `e0f53fb04a^1..e0f53fb04a`;
  `wave2-mechanical.sh` 0 for both, which it returns regardless of
  findings. No range was altered. `plan-range.sh` was not used.
- **2026-11-12, AEDT.** Step 9a's brief omitted the floor item 1
  verdict that the plan's *Risks* table assigns to 9a, so the
  management session did it instead: all twelve kerbside landings
  resolve as two-parent merge commits, and the four out-of-repo
  landings take the cited-audit form. That is a defect in the
  brief, not in the step.
- **2026-11-12, AEDT.** Step 9a reported no `rust.yml` push run on
  `17a31ebf75`. That was its tooling's window rather than an
  absence: `ci-status` returned the newest 100 runs, which begin
  after the merge. The run exists, 42355942233, push event,
  conclusion success, and the management session confirmed it by
  querying the workflow runs for that head directly.
- **2026-11-12, AEDT.** Four of the survey's findings and one
  decision were corrected during execution, each recorded at its
  source and summarised in *Outcome*. What they said before:
  finding 2, "no repository gate touches it"; finding 3, that the
  page's queue prediction was disproved and that phase 7's
  numbers were "not phase 1's profiles"; finding 4, that
  `docs/testing.md` is stale on the loss term; finding 6, "two
  `Future work` entries have no home"; decision 3, that
  `caps.rs` "parses a client's link message". Steps 9c, 9d and 9e
  refuted them against the tree. The briefs asked for exactly
  that, and told the steps not to soften a refutation to match
  the plan. Rows 9c and 9e in *Step plan* are left as they were
  sent, so row 9e still describes `caps.rs` as parsing the link
  message: the briefs are a record of what was asked, and the
  correction belongs in decision 3 and *Outcome*, which carry
  it.
- **2026-11-12, AEDT.** The private-data probe set was run over
  the text this phase adds: no address beyond loopback and the
  rig's documentation-range namespace addresses, no hostname, no
  username, no home or scratchpad path, no credential.
- **2026-11-12, AEDT.** The survey's count of the crate's Rust was
  wrong in the first draft of this plan: `tools/quic-bench/src/`
  is 1,162 lines, not about 1,500, which the planning commit also
  said. Corrected here and in `index.md` before any step ran.
- **2026-11-12, AEDT.** Back brief questions 2 to 4 answered: the
  audit recommends a `rust.yml` gate for the crate; 9f lists the
  two homeless `Future work` entries with filing wording rather
  than filing them; and this session may push and open the pull
  request.
- **2026-11-12, AEDT.** Back brief question 1 is answered ahead of
  implementation: Michael confirmed the widened range, phase 6's
  follow-up #701 together with phase 7's #716, in preference to
  auditing phase 7 alone or re-auditing phases 1 to 7. Decision 1
  and the corrected master plan section stand as written.
  Questions 2 to 4 are still open.

## Outcome

**Audited 2026-11-12, AEDT. Nothing blocking was found in either
landing, and no vulnerability.** Thirteen advisory or
informational findings go to a follow-up pull request, listed
below with their disposition. The two landings are 17a31ebf75
(#701) and e0f53fb04a (#716), each read as a merge against its
first parent.

### Wave results

| Check | 17a31ebf75 (#701) | e0f53fb04a (#716) |
|-------|-------------------|-------------------|
| `wave1.sh` | exit 0 | exit 0 |
| `wave2-mechanical.sh` | exit 0, nothing to triage | exit 0, nothing to triage |
| `rust.yml` | run 42355942233, push, success | not triggered: path-scoped to `rust/**` |

Wave 1's one advisory was a suppressed `print()` in
`tools/shaped-link/summarise.py`, which carries an
`audit-allow-print` marker and is scored as a pass.

The substitute Rust gate decision 2 names, run in Docker:
`make lint` exit 0, `make test` 9 passed, `make mutants` killed 7
and survived 0. Step 9c then re-ran the crate's mutation script
with the failing test printed, and confirmed each mutant fails
*exactly* its named test rather than being credited by a generic
panic. It added two mutants for the two header tests the script
did not defend, six Rust mutants against #701's guards and one
Python mutant against the narrowed config bound: all nine killed,
each by the test that names the property.

### Each test, and the mutation that kills it

Every mutation was run on a copy, under `timeout`, with the files
touched after restoring so that no stale binary could be tested,
and each was confirmed to fail *exactly* the test named rather
than being credited by a generic panic. None survived.

| Test (`tools/quic-bench/src/main.rs`) | Mutation that kills it |
|---|---|
| `percentile_of_nothing_is_none` | `None` returned as `Some(0)` |
| `percentile_is_nearest_rank` | `ceil` for the rank becomes `floor` |
| `percentile_never_runs_off_the_bottom` | the clamp removed |
| `throughput_without_a_steady_window_is_zero_not_a_division_by_zero` | the guard becomes `if false` |
| `throughput_is_megabits_of_the_steady_bytes_only` | `bytes_steady` becomes `bytes_total` |
| `the_first_two_channels_are_the_measured_ones` | bulk and interactive swapped |
| `a_header_claiming_an_implausible_size_is_refused` | the size check becomes `if false` |
| `a_header_round_trips_its_role_and_size` | the role returned as `ROLE_IDLE` |
| `a_truncated_header_is_an_error_not_a_default` | `read_exact(..)?` becomes `let _ =` |

The last two were added by step 9c: the crate's own script
defended seven of the nine, which is finding 12. The guards #701
added were mutated too, seven mutants in all, each killed and
correctly attributed: masking the forwarded capabilities to the
reply set (killed by the unit test and by a functional handshake
test), inlining the relayed capabilities in one backend arm,
clamping an over-ack at zero, the stall threshold from twice to
three times the window, crediting a zero window as one, the
socket-option bound disabled, and in Python the narrowed ceiling
widened back to the unsigned maximum.

### The BBR bullet, statement by statement

The definition of done asks for this one explicitly, since it is
the finding the survey got wrong.

| Statement | File and line | Verdict |
|---|---|---|
| "This host's kernel has only `reno` and `cubic` loaded" | `docs/performance/proxy-backpressure.md:253` | **False.** The host reports `reno cubic bbr`. Worse, it reads as the reason BBR cannot be measured here, where the module ships with the kernel and was merely unloaded. This page owns the claim and is the only line needing an edit. |
| "Loading `tcp_bbr` needs root, and an unprivileged network namespace cannot autoload it" | `docs/performance/proxy-backpressure.md:254-256` | **True**, and the master plan's correction says so. |
| "On these profiles BBR could only trim the bottleneck queue (about 20 ms at 20 ms RTT, one BDP)" | `docs/performance/proxy-backpressure.md:258-260` | **Not falsified.** It is a statement about queue depth, and phase 7's 171 to 104 ms no-loss cut on 80 ms / 10 Mbit is about one BDP at that RTT, so it illustrates the mechanism. This page owns it and nothing overturns it. |
| "which is small next to the ACK-window backlog" | `docs/performance/proxy-backpressure.md:260` | **Untouched by phase 7**, whose bench has no spice-server and no ACK window. |
| The throughput gap, 9.27 against 1.64 Mbit at 1% loss | `docs/performance/quic-transport-bench.md` owns the data | **A loss effect.** At 0% loss the controllers tie, and phase 1's rig had no loss term, which phase 7 added. |
| "not phase 1's profiles" | this plan, as first written | **False.** `PROFILES='20:50 80:10 80:50'` at `docs/performance/proxy-backpressure.md:87` includes 80 ms / 10 Mbit. Only 150 ms / 2 Mbit is new. |

### Floor checklist: kerbside

1. **Pass.** Every recorded landing in the plan takes one of the
   block's forms. All twelve kerbside landings resolve and have
   two parents, so each is read against its first parent; the
   four landings in other repositories -- kerbside-patches
   #1748 and #2024, ryll #406, andris #56 -- take the
   cited-audit form, and phase 6 cites each.
2. **Pass.** #701's wheel feature check runs in workflows that
   publish unattended, and a failure fails the job before
   anything is published, visibly as an Actions failure. #716
   adds nothing that runs unattended.
3. **Pass.** Neither landing put a secret, token, private
   hostname, username or home path into this public repository.
   #701: no hits. #716: only the rig's RFC1918 namespace
   addresses, `0.0.0.0` and loopback. Neither landing added an
   image or any binary. The command output quoted in the two new
   pages is sysctl values plus a kernel and an iproute2 version,
   which the results need in order to be interpretable.
4. **Pass, near-vacuously.** Neither landing removed or renamed
   anything: `git diff --name-status --find-renames` shows only
   `M` for #701 and `A` or `M` for #716. The one interface
   narrowing -- the socket-option ceiling from 2^32-1 to 2^31-1
   -- moved in `config.py`, `main.rs`, `transcode/mod.rs` and
   `docs/configuration.md` together, with an upgrade note, and
   the only configured sites in `etc/kerbside.conf.example` are
   commented out. Greps run: the renamed log constant's prefix
   repository-wide, and `backend_channel_caps`, `notsent_lowat`
   and the old ceiling across this repository and `../ryll` at
   `origin/develop`, which consumes none of it.
5. **Pass, with one advisory.** This repository publishes a wheel
   and images. `build-proxy-wheel.sh` runs under
   `set -euo pipefail` and aborts before maturin on a missing
   interpreter, a parse error or an unexpected key, so a broken
   change publishes nothing. The advisory is finding 11.

### Findings

All advisory or informational; none blocks the landing. Each goes
to the follow-up pull request unless marked otherwise.

1. **`tools/quic-bench/summarise.py`: a lost repeat narrows the
   gate's own threshold, silently.** `collect` appends a repeat
   only when `read_run` returns a value, and `read_run` returns
   `None` when a repeat's `{arm}.json` is missing, so such a
   repeat never enters `runs` -- while `aggregate`'s
   `unmeasured = len(runs) - len(measured)` can only see repeats
   that are present with a null percentile. A repeat lost to a
   crash, a kill or a full disk therefore shrinks
   `p95_spread_ms`, which is the figure decision 2 of phase 7
   gates its verdict against, and nothing warns. This is the
   same class as the null-percentile path that #716's own review
   round fixed, one layer further up: the fix generalised to the
   wrong boundary. It cannot have moved a published number --
   the independent recomputation covered those, and phase 7's
   margin was about seventy times its spread -- but the next run
   of this bench is unprotected. Fix: count repeats attempted
   against repeats read, and warn on the difference.
2. **`detect_headline_cc` falls back to `ccs[0]`.** `bbr` sorts
   before `cubic`, so the fallback would label every row's scope
   backwards, making the sensitivity arm read as the gate.
3. **The throughput medians were never independently
   recomputed.** Phase 7's recomputation covered the interactive
   percentiles and bytes per queue slot, not the megabit tables,
   which pass through the same `aggregate`.
4. **`tools/quic-bench/run-bench.sh`: a pre-created TLS
   directory.** The script does `mkdir -p "${WORKDIR}/tls"` and
   then `chmod 700`, and its comment correctly reasons that
   narrowing must precede writing. The residual case is a
   `WORKDIR` under a world-writable directory, where another
   local user can pre-create `tls` as a symlink: `mkdir -p`
   succeeds on it, `chmod` follows it, and the unencrypted keys
   land somewhere readable. Not reachable with the default
   `WORKDIR`. Fix: port the ownership refusal already in
   `tools/shaped-link/quality.sh`, which rejects a path this
   user does not own.
5. **`run-bench.sh` leaks the namespace holder on SIGKILL.** The
   client namespace is held by `unshare --net sleep infinity`; a
   kill that skips the EXIT trap leaves that process and its
   namespace indefinitely. Everything else dies with the script,
   and no `tc` or namespace state reaches the host, since all of
   it runs under `unshare --user --map-root-user --net`. Fix:
   bound the sleeper.
6. **`docs/performance/quic-transport-bench.md` writes "the phase
   should stop here".** A plan reference in documentation, which
   `plan-phase-references` reserves for plan files. Introduced by
   #716, and the definition of done's own grep misses it because
   the word carries no number.
7. **`docs/spice/capabilities.md` does not document the proxy's
   reply mask.** The page is 176 lines and says nothing about the
   mask, what is forwarded verbatim, or that the mask is not a
   security boundary. That reasoning is phase 6's audit
   resolution, and it now lives in exactly two places:
   `caps.rs`'s module doc, and plan files that retirement
   deletes. Fix: give the page the behaviour and leave the code
   comment a pointer. This supersedes the comment-proportion
   finding below.
8. **`tools/shaped-link/run-matrix.sh`'s loss field has no
   caller.** No committed script or page invokes `PROFILES` with
   a third field; the only reference outside the rig's own header
   is phase 7's definition of done. The runs under loss went
   through `tools/quic-bench`'s own matrix instead, because steps
   3 to 6 never ran. Keep it with one documented example, or
   remove it.
9. **`docs/testing.md` has no cross-reference to the bench.**
   Advisory only: the measurement page owns the bench's
   invocation, and `docs/testing.md`'s shaped-link material is a
   subsection of its Ryll section, which the bench does not
   belong in, having no Ryll, no guest and no SPICE.
10. **Informational.** `caps.rs`'s module doc covers enforcement
    and client compatibility but does not name the resource-cost
    axis: forwarding a capability the proxy never offered lets a
    client turn on backend work, such as an encoder, that the
    proxy did not advertise. One sentence there.
11. **Advisory.** `build-proxy-wheel.sh`'s feature check reads
    the two manifests rather than the built wheel, so a feature
    arriving by another route -- a dependency's own features, a
    cargo config, a caller's maturin invocation -- would pass.
    An assertion over the built binary would close it.
12. **The mutation script covered seven of nine tests.** The two
    header tests were undefended until step 9c added mutants for
    them; both hold. Nothing tests `quic.rs`, `tcp.rs`,
    `nogso.rs`, or the warmup-to-steady sample boundary that
    decides every published percentile.
13. **Trivial.** A loss field of `0.0` rather than `0` is not
    caught by the `!= '0'` test, so it shapes `loss 0.0%` and
    names a `-0.0pct` directory.

### Declined, and why

- **Moving `caps.rs`'s module doc to `docs/`, as the
  comment-proportion check suggests.** #701 added 128 lines
  there, 83 of them comment against 45 of code, which the rule
  makes a candidate. Declined as stated: the rule exempts a
  hard-won explanation, and this is one -- it is the resolution
  phase 6's audit accepted when it declined capability masking,
  on the ground that masking would drop guests to a slower render
  path for no enforcement the opcode allowlist does not already
  give. Cutting it would discard that. Finding 7 is the right
  shape of the same observation: the gap is in `docs/`, not an
  excess in the code.
- **Sharing the namespace and shaping setup between
  `run-bench.sh` and `run-matrix.sh`.** They do duplicate it.
  Declined: the 0% control depends on the two scripts building
  the same qdisc line independently, which phase 7 verified by
  diffing them. Factoring it out would replace a cross-check with
  a single point of failure.
- **`tools/quic-bench/src/main.rs`'s `.unwrap()` on a header
  read.** Not a finding: it is inside a `#[tokio::test]`, where
  the unwrap failing is the assertion.
- **Clamping the forwarded capabilities.** Declined by phase 6
  with reasons; this audit does not reopen it, and step 9e agrees
  the landing argues it down convincingly.

### `Future work` with no home

Phases 1, 3, 4 and 5 carry no *Future work* section of their own,
feeding the master plan's instead; phase 6's reads "None yet". So
the live set is phase 7's entries and the master plan's.

With a home, every issue verified open:

| Entry | Plan | Home |
|---|---|---|
| ryll `surface_drawn` rect patch; SET_ACK restart; display-socket stall; codec preference after DISPLAY_INIT | master | ryll#673 |
| andris heat counting; socket pacing and the JPEG fringe; lag watchdog; libvirt attach; own D-Bus bindings | master | andris#62 |
| kerbside-patches sysroot `build.sh` | master | kerbside-patches#2033 |
| RustCrypto unpin | master | ryll#93 |
| RDP | master | ryll's own RDP frontend plan |
| Phase 2's dri-devel submission; phase 5's spice-server series | phase 7 | the master plan's phase 2 and 5 sections; Michael's to send |

With no home:

| Entry | Plan |
|---|---|
| **The kernel's BBR on the proxy host** | phase 7 |
| **The `.vv` file lacks `ticket-valid-until`** | phase 7 |
| Items 2, 6, 7 and 8, which were to "become the next plan" | master |
| Lossless refinement for `gl=on`; `FB_DAMAGE_CLIPS` for qxl | master |
| AF_UNIX in `reds_security_check`; backend-leg TLS session resumption | master |
| ACK pacing; the `streaming-video` recommendation | master |
| H.265 in spice-server; a faster bitrate ramp | master |
| Re-measuring the helper's failing cells; the `transcode` feature's CI steps | master |

Two of those are different, because phase 7 itself asked for them
to be tracked, and both are Michael's to file:

- **The kernel's BBR on the proxy host** (phase 7, "worth its own
  plan"). On the shaped links the kernel's BBR carried 9.27 Mbit
  against Cubic's 1.64 at 1% loss on 80 ms / 10 Mbit, and cut the
  no-loss interactive p95 from 171 ms to 104 ms. It is a
  `net.ipv4.tcp_congestion_control` change and it applies to
  today's per-channel TCP client leg. Two caveats any follow-up
  must carry: `tcp_bbr` was loaded by hand on this host and the
  load is not persisted, so a recommendation has to say how the
  module gets loaded as well as which sysctl to set; and the
  trade is not uniform, since on 150 ms / 2 Mbit BBR's p95 under
  loss sits above Cubic's. `tools/quic-bench/` is the bench to
  re-run it with, which is also the argument for finding 2 of the
  survey.
- **The `.vv` file does not emit `ticket-valid-until`** (phase 7,
  "worth an issue"), although `docs/spice/console-vv-extensions.md`
  defines it and recommends it for Kerbside specifically, and the
  API knows the lifetime. Note that kerbside#411 -- the `.vv`
  construction being duplicated three times -- is a dependency
  rather than a home: adding a key means touching every copy.

### Corrections this audit made to its own plan

The survey's documentation findings were the management session's
own unverified claims, and the steps were briefed to refute them
rather than confirm them. Four did not survive intact, and the
corrections are in *What the survey found* with the originals
recorded there and in *Execution notes*:

- Finding 2 said no repository gate touches the crate. CodeQL
  does; what holds is that nothing builds, tests or lints it.
- Finding 3 said a page states what phase 7 disproved. One
  sentence of it, the availability claim, is wrong; the queue
  prediction is not falsified, and 80 ms / 10 Mbit *is* one of
  phase 1's profiles, which the finding denied.
- Finding 4 said `docs/testing.md` is stale on the loss term. It
  delegates to the rig's header by design; only the
  discoverability half survives.
- Finding 6 said two entries have no home. About fifteen have
  none; two are the ones phase 7 asked to have tracked.
- Decision 3 said `caps.rs` parses the client's link message. It
  does not; ryll's crate does, unchanged by #701.

## Back brief

My understanding: this is the plan's last phase, and it audits
what phases 6 and 7 landed in kerbside -- #701 and #716, each
read as a merge against its first parent -- citing phase 6 for
phases 1 to 5 and ryll #406. Step 9a is a gate; 9b to 9e then run
in parallel as read-only readers; 9f triages, records and closes
the phase out. Findings land in a follow-up pull request, not
here. Points needing your answer before 9a runs:

1. **The range.** The survey found phase 6's follow-up (#701,
   736 insertions, mostly shipped proxy code) landed seven days
   after the audit that called for it and was never audited. I
   have put it in range and corrected the master plan's phase 9
   section, which said phase 7 alone. Do you agree, or would you
   rather phase 9 audit phase 7 only and #701 be handled
   separately?
2. **The Rust gate.** Answered: the audit recommends wiring
   `tools/quic-bench/` into `rust.yml`, and the wiring is
   follow-up work (decision 2).
3. **The two unfiled `Future work` entries** -- the kernel-BBR
   sysctl finding and the missing `ticket-valid-until`. Answered:
   step 9f lists both with wording Michael can file, and files
   nothing itself (decision 5).
4. **The pull request.** Answered: this session may push and open
   it when the phase is done, as it did for phase 7. Landing
   stays Michael's.
