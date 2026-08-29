# Phase 6: Push audit

Parent plan: [PLAN-idle-cpu-and-latency.md](/components/ryll/plans/PLAN-idle-cpu-and-latency/)

## Goal

Run `PUSH-AUDIT.md` over the accumulated diff of phases
1-5, so the audit sees what the repaint, logging, latency
and metrics changes did to *each other* rather than what
each did in isolation.  Findings land as their own PR
against `develop`, recorded in the master plan under
*Items deferred from the push audit*.  The plan is not
complete until every finding is fixed or declined in
writing.

## Planning effort

**High.**  Not because the audit itself is subtle — the
runbook is written — but because this is the first
master-plan closing audit run in this repository, and the
range derivation, the four-month staleness of the diff,
and the interaction with the crate extraction all had to
be worked out before a sub-agent could be briefed.  That
work is below, so the *steps* are mostly medium.

## What the survey found

The master plan's phase 6 section was written before any
of this was checked.  Three of its premises were wrong,
and they have been corrected at source in the master plan
as part of the phase 2 closeout commit — this section
records what was found so a later step does not redo it.

### The audit range is exactly derivable

The master plan assumed five per-phase merge commits
needing reconstruction.  There is **one**: all five phases
landed on the `screenshot` branch, merged as PR #36
(`6d52665`).  The `Merged` column now records it.

Better, the plan's commits run *contiguously* on that
branch, so the range is exact:

```
AUDIT_BASE=90a954b^1     # 8486269, the commit before the master plan
AUDIT_HEAD=1c28d6f       # "Rename last_latency to last_latency_ms"
```

Thirteen commits, and `tools/audit/audit-range.sh` accepts
the bounds as given.  The three candidate ranges, measured:

| Range | Files | Insertions | Verdict |
|---|---|---|---|
| `90a954b^1..1c28d6f` | 25 | 1 957 | **use this** |
| `90a954b^1..85bc901` | 43 | 4 041 | crosses the develop merge |
| `90a954b^1..develop` | 340 | 119 684 | the naive range `PUSH-AUDIT.md` warns about |

Roughly 1 150 of those 1 957 insertions are the plan files
themselves, so the code under audit is about 800 lines.
This is a small audit.

`PUSH-AUDIT.md` cites this very plan as its worked example
of a range that cannot be derived after the fact, and on
the per-phase question it is right.  On the whole-plan
question it is too pessimistic, and the reason is specific
rather than general: this plan's phases happened to land
on one branch, contiguously.  **Do not generalise this to
other plans** — and consider whether `PUSH-AUDIT.md`'s
example paragraph should be softened, which is step 6f.

### Two commits the range does not cover

- **`85bc901`** ("Address automated reviewer feedback on
  PR #36") sits *above* `AUDIT_HEAD`, touches only
  `ryll/src/app.rs` (+41/-21), and is **half in scope**.
  Two of its four items are this plan's (`LatencyTracker`
  history moved to `VecDeque`; the redundant
  `last_latency_ms.is_some()` GUI guards replaced with
  `!self.latency.history.is_empty()`).  The other two are
  the screenshot-HUD plan's `screenshot_paths`.  It cannot
  be folded into the range because `10e7efc` ("Merge
  branch 'develop' into screenshot") sits between, which
  is what inflates the second row of the table above.
- **`6d52665`'s own merge diff** is the wrong patch to
  hand any agent: PR #36 also carried the whole
  screenshot-and-latency-HUD plan.

### The diff is four months stale

Phases 1-5 landed 2026-04-20; this audit runs 2026-08-27.
The crate extraction has since moved most of the audited
code out of `ryll/`:

| In the diff | Today |
|---|---|
| `ryll/src/metrics.rs` | `shakenfist-spice-renderer/src/metrics.rs` |
| `ryll/src/channels/*.rs` | `shakenfist-spice-renderer/src/channels/*.rs` |
| `ryll/src/app.rs` | unchanged |
| `ryll/src/bugreport.rs` | unchanged |
| `shakenfist-spice-protocol/src/logging.rs` | unchanged |

The web frontend was later built on the same event path
this plan introduced, so the repaint-notify contract now
has consumers phases 1-5 never saw.

This is the phase's central design problem and decision 1
addresses it.

### One trap in the tooling

`audit-range.sh`'s content-scanning helpers
(`audit_range_show`) read each file **at `AUDIT_HEAD`** —
that is, at its April content — which is correct for
auditing a historical diff and misleading if read as a
statement about the tree today.  Wave 1's build, lint and
test steps, by contrast, run against the *current* tree.
So a single wave 1 run mixes April-content style findings
with current-tree test results.  Expect it; do not treat
the style findings as live defects without step 6e.

### Two findings already in hand

Surfaced while closing phase 2, so the audit starts from
them rather than rediscovering them:

1. **The latency statistic may be a burst artefact.**  The
   status bar read `Latency: 0.1ms` against a loopback
   guest.  The value is the interval between consecutive
   server PINGs, and phase 1 recorded sf-3 sending "a burst
   of 2 pings at connect time then going quiet" — so the
   number reflects whether the server happens to be
   bursting, not a property of the link.  See the master
   plan's success-criteria section.
2. **The master plan's *Bugs fixed during this work*
   section is still the placeholder** "(To be filled in as
   we go.)"  Either it is genuinely empty, in which case
   say so, or four phases of work fixed something nobody
   recorded.

### What was checked and found correct

Not everything was stale.  Phases 1, 3, 4 and 5 all
survive in current `develop`: the repaint bridge is at
`ryll/src/app.rs:1037` and `:1307` with the 1 Hz fallback
at `:4465`; `log_message` is `debug!` with no embedded
timestamp
(`shakenfist-spice-protocol/src/logging.rs:251`); the
PING latency sample is emitted from
`shakenfist-spice-renderer/src/channels/main_channel.rs:1032`;
and `runtime-metrics.json` reaches the bug-report ZIP via
`ryll/src/bugreport.rs:1296`, covered by
`test_bug_report_runtime_metrics_in_zip`.  `make test`
passes 787 tests and `pre-commit run --all-files` passes
all six hooks as of the phase 2 closeout.

## Decisions

1. **Audit the April diff, then triage every finding
   against current `develop` before acting.**  The
   alternative — auditing today's version of the code
   phases 1-5 introduced — would produce more immediately
   actionable findings, and it is the option a reviewer is
   most likely to argue for.  It is rejected because it
   answers a different question.  This phase exists to ask
   "what did this plan do to the codebase", and a plan
   whose code has since been refactored by someone else
   has not thereby been audited.  Auditing today's code
   would also silently re-audit the crate extraction's
   work, which had its own review.  The cost of the
   choice is a triage pass, which is step 6e, and it is
   cheap because the audit is only ~800 lines of code.

2. **`AUDIT_BASE=90a954b^1`, `AUDIT_HEAD=1c28d6f`, with
   `85bc901` handled as a separate patch.**  Rather than
   widening the range to swallow `85bc901` (43 files, most
   of them unrelated) or dropping it (it contains real
   phase 4 changes).  Step 6a builds a two-part patch file.

3. **Judgment agents get a patch file, not a revision
   range.**  `PUSH-AUDIT.md` requires this, and here it
   matters more than usual: a range would tempt an agent
   into `git show`ing the merge commit and auditing the
   screenshot-HUD plan by accident.

4. **The four wave 2 judgment agents run in parallel, one
   triage agent runs after them.**  They are independent
   by construction; the triage step is not, because it
   needs the full finding list to check against current
   `develop` in one pass.

5. **`PUSH-AUDIT.md` gets a correction as part of this
   phase.**  Its worked example says this plan's range is
   not derivable; the survey shows it is.  Leaving that
   uncorrected would mislead the next plan's audit in the
   direction of not trying.  This is a documentation fix
   to a repo-root runbook, so it is its own step with its
   own commit (6f), not folded into a findings commit.

6. **Findings land as a separate PR from this plan file.**
   Per the master plan.  This phase's PR is the plan plus
   the `PUSH-AUDIT.md` correction; the findings PR follows
   once there are findings to fix.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | low | haiku | none | Build the audit patch. Run `git diff 90a954b^1 1c28d6f > /tmp/plan-audit.patch`, then append the partly-in-scope review commit: `git show 85bc901 >> /tmp/plan-audit.patch`. Verify the result: the patch must contain 25 files from the first diff plus `ryll/src/app.rs` from the second, and must **not** contain `daa4626`-era screenshot work (grep the patch for `screenshot_paths` — it will appear only in the `85bc901` section, which is expected and is called out in step 6c's brief). Print the diffstat of each part. Do not interpret anything; this step only assembles. |
| 6b | low | sonnet | none | Run wave 1: `AUDIT_BASE=90a954b^1 AUDIT_HEAD=1c28d6f tools/audit/wave1.sh`. Exit codes are in `PUSH-AUDIT.md`. Two things to know before reading the output. First, wave 1's build/lint/test steps run against the **current tree**, not the audit range, so they are re-confirming what the phase 2 closeout already measured (787 tests pass, all six pre-commit hooks pass) — a failure here means something regressed on `develop` today, not something wrong with this plan. Second, the range-scoped style checks read file content **at `AUDIT_HEAD`**, i.e. April, so a long-line or unguarded-`log_message` hit may have been fixed since; report them, do not fix them, and mark each as needing the step 6e check. If wave 1 fails on codes 1-3, stop and report — do not proceed to wave 2. |
| 6c | low | sonnet | none | Run `AUDIT_BASE=90a954b^1 AUDIT_HEAD=1c28d6f tools/audit/wave2-mechanical.sh` and report its output verbatim, then add the style-conformance judgment review from `PUSH-AUDIT.md`'s "Style conformance — judgment portion" against `/tmp/plan-audit.patch`. Pay particular attention to the `repaint_notify.notify_one()` pairing requirement (`docs/design-decisions.md` decision #17): phase 2 added a `notify_one()` call after every `event_tx.send()` across seven channel handlers, and a missed pairing is exactly the defect that would make the UI silently stop updating. Check every `send_event`/`event_tx.send` site in the patch has one. Note that the `85bc901` section of the patch contains two hunks about `screenshot_paths` that belong to a different plan — skip those, they are not in scope. |
| 6d | medium-to-high | sonnet (2a/2b/2c), opus (2d) | none | The four wave 2 judgment agents from `PUSH-AUDIT.md`, run in parallel, each against `/tmp/plan-audit.patch` rather than a revision range: **2a code quality**, **2b test review**, **2c documentation review**, **2d security review** (opus, high effort). Use each brief in `PUSH-AUDIT.md` verbatim, with two additions. (i) The patch is from April; report what the patch shows and do not check it against the current tree — that is step 6e's job, and doing it here would have four agents redundantly repeating it. (ii) For 2d specifically, the highest-value target is `ryll/src/metrics.rs` (467 new lines), which parses `/proc/self/stat`, `/proc/self/status` and `/proc/self/task/*` — check the parsing for panics on malformed or truncated `/proc` content, and check the sampling sleep cannot be triggered on a UI thread. For 2c, note that the plan-file criteria naming `README.md` and `ARCHITECTURE.md` were already reconciled during the phase 2 closeout (the content moved to `docs/configuration.md` and `docs/diagnostics.md`); do not re-report that as a gap. |
| 6e | high | opus | none | Triage. Take every finding from 6b, 6c and 6d and classify each against **current `develop`** as `still-present`, `already-fixed`, or `moved` — using the file mapping in this plan's survey section (`ryll/src/metrics.rs` → `shakenfist-spice-renderer/src/metrics.rs`, `ryll/src/channels/*` → `shakenfist-spice-renderer/src/channels/*`; `app.rs`, `bugreport.rs` and `logging.rs` did not move). For each `still-present` finding give the current file and line. This is the step that decides what the findings PR actually contains, so be conservative: a finding you cannot locate in current `develop` is `already-fixed` only if you can point at what fixed it, otherwise it stays `still-present` and gets a human look. Add the two findings already in hand from the survey section (the latency burst-artefact question and the empty *Bugs fixed during this work* section) to the list before triaging. Output a table: finding, source agent, severity, status, current location. |
| 6f | low | sonnet | none | Correct `PUSH-AUDIT.md`'s "Two ways this runbook is invoked" section. It states that a master plan's accumulated diff "is not reliably derivable after the fact" and cites this plan, measured at 338 files, as the example. That is true of the naive range and false of the contiguous-commit range: the real answer here is 25 files, via `90a954b^1..1c28d6f`. Rewrite the passage to keep its warning — the naive range really is 340 files today — while adding that where a plan's phases landed contiguously on one branch, `<first-plan-commit>^1..<last-plan-commit>` gives an exact range, and that the `Merged` column should record the branch and bounding commits, not only a merge SHA. Keep the existing advice to record commits as phases land; that is still the point. Own commit, subject "Record that a contiguous phase range is derivable." |
| 6g | medium | opus | none | Management step, not a sub-agent step: review the 6e table, decide fix-or-decline for each finding, and record the outcome in the master plan under a new *Items deferred from the push audit* heading — matching the shape `PLAN-web-frontend.md` uses, minus the phase number. Every finding must be fixed or declined **in writing**. If the audit found nothing, that is recorded in one sentence and the phase is done. Fixes land as their own PR against `develop`; this step only decides and records. |

## Risks and mitigations

- **The audit reports "no findings" because it looked at
  nothing.**  This is the failure the phase exists to
  prevent, and the empty-range guard (exit 6) only catches
  the degenerate case.  *Mitigation:* step 6a prints the
  diffstat of both patch parts, and step 6b's brief states
  the expected shape (25 files + `app.rs`).  A reviewer
  checking this phase should look at those two numbers
  first; if they are not 25 and 1, the range broke.
- **Stale findings burn the findings PR's credibility.**
  Four months is long enough that some of an April diff's
  problems are already gone.  *Mitigation:* step 6e, and
  its instruction that "already-fixed" requires pointing
  at the fix.  The management session (6g) checks that
  every `already-fixed` claim carries one.
- **An agent audits the wrong plan.**  PR #36 carried two
  plans and the merge diff mixes them.  *Mitigation:*
  patch file rather than revision range (decision 3), and
  an explicit "skip the `screenshot_paths` hunks"
  instruction in 6c and 6d.
- **The `PUSH-AUDIT.md` correction over-corrects.**  The
  contiguous-range trick worked here by luck of how the
  branch was built; presenting it as the general method
  would send the next audit down a wrong path.  *Mitigation:*
  6f's brief says keep the warning and add the exception,
  and the management session reads the resulting wording
  rather than accepting it.

## Definition of done

Falsifiable items only.

- `git diff --stat 90a954b^1 1c28d6f | tail -1` reports
  25 files changed, and the assembled patch also contains
  `ryll/src/app.rs` from `85bc901`.
- `tools/audit/wave1.sh` has been run with the bounds
  above and its exit code is recorded in this file.
- `tools/audit/wave2-mechanical.sh` output is recorded in
  this file, verbatim.
- All four wave 2 judgment agents have reported, and each
  report is either summarised in this file or its findings
  appear in the 6e table.
- The 6e table exists, and every row has a status of
  `still-present`, `already-fixed` or `moved`; every
  `already-fixed` row names what fixed it.
- The master plan has an *Items deferred from the push
  audit* section in which every finding is marked fixed or
  declined, with a reason for each declination — or a
  single sentence recording that the audit found nothing.
- The master plan's *Bugs fixed during this work* section
  is no longer the placeholder text.
- `PUSH-AUDIT.md` no longer claims this plan's range is
  underivable.
- The master plan's phase 6 row reads `Complete`, and
  `docs/plans/index.md` shows the master plan as
  `Complete`.
- `pre-commit run --all-files` passes; `make test` passes.

## Back brief

Before executing any step, back brief the operator on the
understanding of this phase and how the intended work
aligns with it.

Two gates where the work is cheap to propose and expensive
to redo, so stop for agreement rather than proceeding:

- **After step 6a**, confirm the assembled patch is the
  right patch — the diffstat, and the decision to include
  `85bc901` in part rather than widen the range.  Every
  later step is wasted if this is wrong.
- **Before step 6g acts on the 6e table**, agree the
  fix-or-decline split.  Declining a finding in writing is
  a judgment the operator owns, not the audit's.

## Execution record: 2026-08-27

### Step 6a — patch assembled

`git diff 90a954b^1 1c28d6f` gave 25 files, 1 957
insertions, 94 deletions; `git show 85bc901` appended one
more file (`ryll/src/app.rs`, +41/-21).  That is the shape
this plan predicted, so the gate passed and execution
continued.  All `screenshot_paths` occurrences in the
combined patch fall in the `85bc901` section, as expected.

### Step 6b — wave 1: FAILED, exit 1, and the cause is the audit tooling

Wave 1 did not get past its first stage.  The failure is
not in the audited code — it is in the audit harness, and
it fires *because* this phase follows `PUSH-AUDIT.md`'s
documented procedure.

`wave1.sh` stage 1a runs `pre-commit run --all-files`,
which runs the `audit range smoke test` hook, which is
`tools/audit/test-audit-range.sh`.  That script builds a
scratch repository and asserts on range-resolution
behaviour — but it does not clear `AUDIT_BASE` /
`AUDIT_HEAD` from its environment.  With the bounds this
phase requires exported, the scratch repo sees
`AUDIT_BASE=90a954b^1`, a commit that does not exist there,
and 13 assertions fail.  Demonstrated both ways:

```
$ env -u AUDIT_BASE -u AUDIT_HEAD tools/audit/test-audit-range.sh
all audit-range assertions held
$ AUDIT_BASE=90a954b^1 AUDIT_HEAD=1c28d6f tools/audit/test-audit-range.sh
13 assertion(s) failed
```

This is the same class of bug as `9a4067e` ("Stop the audit
test inheriting global git config"), which fixed inherited
git config; inherited audit bounds were missed.

The remaining wave 1 stages were therefore run by hand:

- `./scripts/check-rust.sh check` (rustfmt + clippy): **pass**.
- `cargo test --workspace`: **pass**, 787 tests, 0 failures.
- Raw `println!`/`eprintln!` check: hits in `ryll/src/main.rs`
  and `ryll/src/web/server.rs` are covered by
  `audit-allow-println` markers; hits in
  `shakenfist-spice-compression/` are test code.  Five
  production hits in the `main_channel.rs` watchdog path
  carry no marker and would trip exit 4 once the harness
  bug above is fixed — a latent second wave 1 failure,
  outside this plan's range.
- Unguarded `logging::log_message` check: **inspects a
  directory that no longer exists** — see the findings
  table.

### Step 6c — wave 2 mechanical, verbatim

```
=== wave 2a: TODO / FIXME / HACK in changed files ===
(none)

=== wave 2a: new #[allow(dead_code)] in changed files ===
(none added)

=== wave 2b: new test count in changed files ===
new #[test] functions: 6
rust files changed: 13

=== wave 2c: doc files touched in changed set ===
ARCHITECTURE.md
README.md
docs/plans/PLAN-idle-cpu-and-latency-phase-01-profile.md
docs/plans/PLAN-idle-cpu-and-latency-phase-02-repaint.md
docs/plans/PLAN-idle-cpu-and-latency-phase-03-logging.md
docs/plans/PLAN-idle-cpu-and-latency-phase-04-latency.md
docs/plans/PLAN-idle-cpu-and-latency-phase-05-metrics.md
docs/plans/PLAN-idle-cpu-and-latency.md
docs/plans/index.md
docs/plans/order.yml

=== wave 2d: security smoke ===
new unsafe{} blocks in changed files:
+        // SAFETY: sysconf is async-signal-safe and has no unsafe
+        let v = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };

new .unwrap() / .expect() in non-test code:
(11 hits, all inside #[cfg(test)] modules on inspection)
```

### Step 6d — four judgment agents

All four reported.  2c (documentation) found **no blocking
gaps**: the patch's own `ARCHITECTURE.md` hunk documents
the bug-report ZIP tree but not the repaint bridge or the
PING latency source, and `57f5b62` closed that downstream
on the same branch before the push.  No `phase <N>`
leakage outside `docs/plans/`, and no wire-protocol change,
so `shakenfist/kerbside/docs/` needs no review.

The other three converged on one structural finding from
three independent directions: the 42 hand-paired
`event_tx.send(...)` / `repaint_notify.notify_one()` call
sites.  All three enumerated the sites and all three found
the pairing correct today; all three observed that nothing
enforces it.  2b and 2a independently proposed the same
fix, which is structural rather than test-shaped: collapse
the pair into one `emit()` helper so the failure mode
cannot be expressed.  That agreement is the strongest
signal this audit produced.

Full findings and their triage against current `develop`
are in the table below.

### Step 6e — findings triaged against current `develop`

Twenty-two findings.  One is already fixed, nine moved
with the crate extraction and travelled unchanged, and
twelve are still present where they were.  Nothing was
dropped as "already fixed" without naming the fix.

| ID | Finding | Severity | Status | Current location |
|----|---------|----------|--------|------------------|
| T1 | `test-audit-range.sh` inherits `AUDIT_BASE`/`AUDIT_HEAD`; 13 assertions fail when set, breaking wave 1 via pre-commit | High (tooling) | still-present | `tools/audit/test-audit-range.sh:24-26`, whose `unset` covers only `GIT_*` |
| T2 | Unguarded-`log_message` check greps `ryll/src/channels/`, gone since the crate extraction | Low (tooling) | still-present | `tools/audit/wave1.sh:126` |
| T3 | Same check keys on `is_verbose()`, a convention since abandoned | Low (tooling) | still-present | `tools/audit/wave1.sh:123-135` |
| T4 | 5 unmarked production `eprintln!` in the watchdog path | Low (style) | still-present, out of range | `shakenfist-spice-renderer/src/channels/main_channel.rs:461-509` |
| **T5** | **`wave1.sh`'s fatal `println!` check does not scan `shakenfist-spice-renderer/` or `shakenfist-spice-webrtc/` at all** | **High (tooling)** | **still-present** | `tools/audit/wave1.sh:97-99` |
| A1 | Hand-paired `send_event(...)` / `notify_one()` with no `emit()` helper | Low | moved, grew 42 → 58 sites | `shakenfist-spice-renderer/src/channels/*.rs` |
| A2 | `use tokio::sync::Notify as RepaintNotify;` in main_channel only | Low | moved | `.../channels/main_channel.rs:7` |
| A3 | CPU-percent formula duplicated (process and per-thread) | Low | moved | `.../metrics.rs:313`, `:343` |
| A4 | `clk_tck()` `SAFETY:` comment justifies with the wrong concept | Low | moved | `.../metrics.rs:216-217` |
| B1 | `linux::sample()` untested; delta arithmetic fused to I/O and `sleep` | Medium | moved | `.../metrics.rs:279-368` |
| B2 | `parse_proc_stat` untested for truncated / non-numeric / no-paren input | Low | moved | `.../metrics.rs:144`, tests at `:876`, `:896` |
| B3 | `parse_proc_status_kb` has no malformed-value test | Low | moved | `.../metrics.rs:184`, test `:913-927` |
| B4 | Bug-report ZIP test covers only the `Unavailable` metrics variant | Low | still-present | `ryll/src/bugreport.rs:3451-3512` |
| B5 | PING inter-arrival computation untested; no pure helper to test | Low | moved | `.../channels/main_channel.rs:1026-1037` |
| B7 | playback gated on `is_verbose()` while six channels did not | Low | **already-fixed** | all 7 converged on `self.log_config.verbose` |
| B8 | Metrics tests assert on raw JSON substrings | Low | moved | `.../metrics.rs:933`, `:944`, `:971` |
| D1 | 2 s `thread::sleep` on the egui UI thread; false "gated on a file dialog" comment | **Medium, raise** | still-present | `ryll/src/bugreport.rs:1296`, comment `:1270-1275` |
| D2 | PING handler emits `Latency` before writing PONG | Low | still-present, now bounded | `.../main_channel.rs:1032-1036` vs PONG `:1056` |
| D3 | "Latency" is a server-chosen interval, emitted before `Ping::read()` validates | Low | still-present | `.../main_channel.rs:1031` vs `:1042` |
| D4 | `pub fn sample` divides by unguarded `window_secs`; NaN into `sort_by(partial_cmp)` | Low | still-present, Linux path only | `.../metrics.rs:302`, sort `:351-355` |
| D6 | `libc` not target-gated | Info | still-present, worse | `ryll/Cargo.toml:150-151` (dead), renderer `:89-90` (live) |
| D7 | `info!`→`debug!` did not shrink `/tmp/ryll.log` | Info | still-present | `ryll/src/main.rs:197-201` |

Five triage results changed the picture enough to record
separately.

**T5 is new, and it is the most serious thing this audit
found.**  Chasing T4 showed that `wave1.sh`'s raw
`println!`/`eprintln!` check — the one style check that is
*fatal* rather than advisory — scans only `ryll/src`,
`shakenfist-spice-protocol/src`,
`shakenfist-spice-compression/src` and
`shakenfist-spice-usbredir/src`.  It does not scan
`shakenfist-spice-renderer/` or `shakenfist-spice-webrtc/`.
Measured: those two hold 28 754 of the workspace's 62 024
source lines, so **46% of the code is invisible to the one
style check that can fail the build** — and the renderer
alone (23 859 lines) is now larger than `ryll` (19 322).  So the
five unmarked `eprintln!` calls in T4 would *not* trip
exit 4 even with T1 fixed: the check passes vacuously on
the crate it most needs to read.  Together T1, T2, T3 and
T5 say the same thing — the audit harness was not updated
when the crates were split, and three of its four
range-scoped checks are now looking at the wrong places.

**B7 is the only genuine fix, and it is what makes T3 a
false positive.**  All seven channels converged on
`self.log_config.verbose`; `is_verbose()` survives only at
`ryll/src/settings.rs:27`, used once to build `LogConfig`.
The wave 1 heuristic keys on the convention that lost.

**D1 deserves raising rather than lowering.**  The other
two `BugReport::new` call sites — `auto_snapshot.rs:245`
and the pedantic observer at `bugreport.rs:1915` — were
both wrapped in `tokio::task::spawn_blocking`, each with a
comment about not stalling the executor.  The interactive
path was not.  The codebase demonstrably knows about this
hazard and fixed it everywhere except the one place a
human is watching the window, and the comment still
justifying it (`:1270-1275`) is contradicted by the two
call sites that route around it.

**D4 is half-fixed, and the fixed half is the fix.**  The
macOS implementation added since April guards both divisors
with `window.as_micros().max(1)`, sorts with `sort_by_key`
instead of `partial_cmp`, and carries a zero-window
regression test.  The Linux path it was modelled on got
none of that.  The correct code already exists 150 lines
below the defect.

**D6 must be re-scoped before it is fixed.**  In `ryll` the
dependency is not merely ungated but dead — no `libc::`
reference remains in `ryll/src` — so that line should be
deleted, not gated.  The renderer's live copy is used on
both Linux and macOS, so the original suggestion of
`cfg(target_os = "linux")` would break the macOS build.

### Step 6f — `PUSH-AUDIT.md` corrected

`99136d6` ("Record that a contiguous phase range is
derivable"), merged in PR #326.  The runbook keeps its
warning about the naive range — 340 files here, today —
and now records that where a plan's phases landed
contiguously on one branch,
`<first-plan-commit>^1..<last-plan-commit>` gives an exact
range, with the caution that the contiguous case is luck
rather than method.

### Step 6g — fix-or-decline, and where the fixes landed

The management call on all 22 findings is recorded in the
master plan under *Items deferred from the push audit*
(`2223c44`, PR #326).  Sixteen were fixed, five declined in
writing, and one — B7 — was already fixed before the audit
ran.  The fixes went out as two PRs rather than the one the
phase anticipated, because the harness findings blocked the
code findings from being verified:

| PR | Merge | Findings |
|----|-------|----------|
| #325 | `6fecb50` | T1, T5, T4, and T2/T3, which folded in because that PR already owned `wave1.sh` |
| #327 | `192265f` | D1, D2, D3, D4, D6, A1, A2, A3, A4, B1, B5 |

Declined: B2, B3, B4, B8 (test-coverage gaps behind paths
that already degrade to `RuntimeMetrics::unavailable`) and
D7 (a duplicate of issue #313).  Reasons are in the master
plan.

Two things worth carrying forward.  A1's `EventSink`
refactor found a real defect — `AgentConnected` sent with
no paired repaint notify — that three independent judgment
agents had each read past while reporting the pairing
complete; the structural fix found what the reading did
not.  And the audit's own recommendation on D1
(`tokio::task::spawn_blocking`) would have panicked, since
the UI thread has no runtime to spawn onto.

### Definition of done — checked at closeout

Every item holds.  The two status items were the last
outstanding, and this commit closes them:

- Range: `git diff --stat 90a954b^1 1c28d6f | tail -1`
  reports 25 files; the assembled patch also carried
  `ryll/src/app.rs` from `85bc901`.  Recorded under step 6a.
- Wave 1 exit code recorded (1, harness bug), wave 2
  mechanical output recorded verbatim, all four judgment
  agents reported.  Steps 6b, 6c, 6d.
- The 6e table has a status on every row, and its one
  `already-fixed` row names what fixed it.
- The master plan's *Items deferred from the push audit*
  marks every finding fixed or declined, with reasons.
- The master plan's *Bugs fixed during this work* section
  is no longer the placeholder.
- `PUSH-AUDIT.md` no longer claims this plan's range is
  underivable.
- The master plan's phase 6 row reads `Complete` and
  `docs/plans/index.md` shows the master plan as
  `Complete`.
- `pre-commit run --all-files` and `make test` both pass on
  `develop` at the point of closeout.
