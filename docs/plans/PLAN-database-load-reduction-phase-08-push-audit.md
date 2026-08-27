# PLAN: Database load reduction phase 8 -- push audit

Planning effort: medium. Review effort: high.

## Why this phase exists

[PLAN-database-load-reduction.md](PLAN-database-load-reduction.md) has
run for five weeks across nine merged pull requests and one still open.
Every one of those was reviewed on its own. Nobody has looked at the
result as a single body of work, and this plan more than most needs
that: its phases do not sit side by side, they sit *on top of* each
other. Phase 1 removed polls, phase 2 served what was left from a
cache, phase 5 removed more, phase 6 discovered that a third of the
apparent regression it was chasing was the counter learning to see two
more nodes. Each step was measured against the step before it. None was
measured against the shape of the whole.

`PUSH-AUDIT.md` is the repository's audit template, normally a pre-push
gate run against `develop...HEAD`. Here it runs retrospectively over
nine merges, which changes the baseline but not the questions.

There is one direct precedent,
[PLAN-queue-performance-phase-08-push-audit.md](PLAN-queue-performance-phase-08-push-audit.md)
(PR #3880), and its retrospective is the most useful single input to
this plan. That audit found real defects and still missed one, for a
reason it wrote down afterwards: it asked whether a change was
*correct*, and never asked what the corrected code would then *do*. Its
words -- "a dead code path has no behaviour to audit; the moment you
revive one, its behaviour is new work". Decision 4 below is that lesson
applied to this plan.

## Scope

**In scope.** The code this plan added or changed, across the nine
merges and the open phase 7 branch listed in decision 1, audited under
the `PUSH-AUDIT.md` headings: wave 1 mechanical checks, and wave 2's
code quality, test coverage, documentation and security reviews, plus
the fifth lens in decision 4.

**Out of scope.** Re-litigating phase 7's two automated review rounds.
Those findings are recorded in
[the phase 7 plan](PLAN-database-load-reduction-phase-07-regression-detection.md)
under *What review found* and *What the second review found*, and were
fixed. The audit may disagree with a disposition, but it starts by
reading what was already decided rather than rediscovering it.

**Out of scope.** Reconciling the private operations report in `33fl`
with the public load model. Phase 7 recorded this as a known
consequence -- two sources of truth for the same numbers -- and placed
the change outside this repository. It stays there.

**Out of scope.** Fixing anything the audit finds, unless it is
blocking or trivial. This plan's convention, like the precedent's, is
that a review phase records and files rather than expanding into the
work it discovers.

## What the survey found

The master plan's phase 8 section is short and, as far as it goes,
accurate: `PUSH-AUDIT.md` exists at the repository root, the
instruction to audit the accumulated diff rather than the last phase's
diff is the right instruction, and findings can land as their own pull
request. Four things it did not anticipate:

1. **Phases do not map one-to-one onto merges.** The section says "every
   phase in this plan", which reads as one range per phase. It is not:
   phase 1 is one PR, phases 2 through 4 share a single PR, phase 5 is
   four, and phase 6 is three. An audit keyed on one merge per phase
   would silently miss six of the nine ranges -- including
   `#3506` (queue backoff, 361 insertions) and `#3877` (phase 6
   closeout, 348 insertions), neither of which is small.

2. **Phase 7 is not merged.** #3893 is open, green, and awaiting the
   merge queue at `4995eb2cc`. The section assumes the whole plan is on
   `develop` before the audit runs. Decision 2 handles this.

3. **The footprint is smaller and cleaner than the precedent's.** 71
   files, of which 9 are plan documents; 62 files of code, tests,
   tooling and operator documentation. Unlike queue-performance -- whose
   changes were a minority of a 105-file refactor and needed an explicit
   file list to carve out -- every file in these ranges attributes to a
   branch of this plan. No scope-restriction decision of the
   precedent's decision-2 kind is needed, which removes the precedent's
   largest source of judgement error.

4. **`docs/plans/index.md` and the master plan agree.** Both say phases
   1-6 complete, phase 7 in progress, "6 of 8". No closeout drift to
   correct at source, which the `next-phase` skill asks be reported as
   a result in its own right.

5. **`CLAUDE.md`'s directory listing is stale, and not because of this
   plan.** It lists `shakenfist/cache.py` as the "In-memory caching
   layer" at `CLAUDE.md:163`. That module was deleted in PR #2870 on
   2025-12-21, seven months before this plan started, and phase 2's
   object cache lives in `shakenfist/mariadb.py` instead. The line is
   therefore **out of scope as a finding against this plan** -- but
   `CLAUDE.md` *was* edited by range #3466, so step 8e should say
   whether an editor touching that file should have caught it, and the
   line is worth fixing here regardless because it is one line and the
   audit already knows about it.

Verified as part of the survey: all nine merge commits are ancestors of
`develop`; no leftover worktree or branch exists for phases 1-6; and
phase 6's deferred items were filed rather than dropped (#3876 for
`GetReferencesFrom`/api, and the phase 7 budget marks that pair
provisional against it so the regression detector cannot canonise it).

## Decisions

1. **The audit baseline is the plan's merge ranges plus the open phase 7
   branch, not `develop...HEAD`.** Most of this work is merged, so
   `git diff develop...HEAD` on this branch is empty and every command
   in the template would report success against nothing.

   | Phase | PR | Merge | Range |
   |-------|----|-------|-------|
   | 1 | #3466 | `dcd3b32b1` | `dcd3b32b1^1...dcd3b32b1` |
   | 2-4 | #3473 | `926060406` | `926060406^1...926060406` |
   | 5 | #3504 | `706f8db81` | `706f8db81^1...706f8db81` |
   | 5 | #3509 | `6e2948ee9` | `6e2948ee9^1...6e2948ee9` |
   | 5 | #3508 | `c64ef3afe` | `c64ef3afe^1...c64ef3afe` |
   | 5 | #3506 | `ff259930d` | `ff259930d^1...ff259930d` |
   | 6 | #3818 | `89d4ec294` | `89d4ec294^1...89d4ec294` |
   | 6 | #3825 | `19f6783d4` | `19f6783d4^1...19f6783d4` |
   | 6 | #3877 | `accea7f20` | `accea7f20^1...accea7f20` |
   | 7 | #3893 | *(open)* | `develop...database-load-reduction-phase-07-regression-detection` |

   The per-range diffs are what each agent reads. Where a later phase
   rewrote an earlier phase's code, the *net* state is what matters for
   a correctness finding, so an agent that finds something in an early
   range must check the file as it stands on the phase 7 branch before
   reporting it -- the range shows what changed, the working tree shows
   what shipped.

2. **This phase branches from `develop`, and phase 7 findings are fixed
   in #3893 rather than here.** A defect in code that has not merged yet
   should be fixed before it merges. #3893 is open and green; a fixup
   there costs one push, whereas landing phase 7 and then repairing it
   from this branch puts a known defect on `develop` for no reason.
   Findings against the nine merged ranges are fixed in this phase's
   PR. A finding that spans both is fixed in #3893 if the phase 7 code
   is where the defect lives, and named here either way.

   Consequence to watch: if #3893 merges while this phase is running,
   rebase this branch onto `develop` and the distinction disappears.
   Nothing else changes.

3. **The audit runs with sub-agents, as `PUSH-AUDIT.md` specifies.**
   The precedent ran its four judgment briefs inline because the
   operator had asked in that session that sub-agents not be spawned.
   The operator has asked for the opposite here. The briefs are
   independent and read-only, so they run in parallel; the management
   session grades and disposes.

4. **A fifth wave-2 lens: what this plan stopped doing.**
   `PUSH-AUDIT.md`'s four headings ask whether the code that exists is
   correct. This plan's entire method was *deleting* reads and serving
   what remained from cache. Neither of those leaves code behind to
   audit -- a poll that no longer runs has no line for a reviewer to
   look at, and that is exactly the shape of defect the precedent's
   retrospective says an audit misses.

   Every removed poll and every added cache is a freshness trade. The
   fifth agent walks the trades and asks, for each: what now reads
   state that may be stale, how stale can it be, who notices, and what
   used to observe this that no longer does. This is the lens most
   likely to find something, and it is the one no template heading
   covers.

5. **Wave 1's exit condition is relaxed in one specific way.** The
   template says stop if `pre-commit` or `tox` fails. Those run against
   the working tree, which here is `develop` plus this plan document --
   so a failure is a pre-existing failure on `develop`, not something
   this plan introduced. If wave 1 fails, record it, check whether the
   plan's own diff is implicated, and continue to wave 2 rather than
   stopping. Stopping would only be correct if this branch were about to
   be pushed as code. Same reasoning as the precedent's decision 4.

6. **Findings are graded blocking or advisory. Blocking findings are
   fixed in this phase (or in #3893, per decision 2); advisory findings
   are filed as issues and listed here.** A finding that is real but
   outside this plan's scope is filed and named as such rather than
   quietly downgraded.

7. **A clean heading is a result.** If a heading finds nothing, it says
   so in one sentence, alongside the list of what it actually examined.
   An audit reporting nothing under every heading is recorded as such
   rather than padded. The precedent's first risk -- that a
   retrospective audit rubber-stamps code which already shipped -- is
   guarded by requiring the "what I examined" list, not by requiring
   findings.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | medium | opus | none | *(Management session, this document.)* Add phase 8 to the master plan's Execution table, write this phase plan, register it in `docs/plans/index.md`, and confirm the master plan's status stays In progress. Run `tools/check-plan-status.py` and `pre-commit run --all-files`. Commit. |
| 8b | medium | sonnet | none | **Wave 1.** Run `pre-commit run --all-files` and `tox` and record the result; per decision 5 a failure here is reported, not a stop. Then run the template's four style greps against **each** of the ten ranges in decision 1 rather than `develop...HEAD` -- lines over 120 characters, stray `print(`, new `etcd` references, and new `mariadb.get_all_*(` without a `# nopushdown:` tag. `protos/database.proto` is **not** in any range, so the proto-freshness check does not apply; confirm that rather than assuming it. Then the style-conformance judgment brief from `PUSH-AUDIT.md`: import ordering, the `shakenfist_utilities.logs` pattern, single quotes for strings and double for docstrings, 120-character lines, event logging with the right `EVENT_TYPE_*` constant, and the three-layer direct/gRPC/public pattern for every new `mariadb.py` function. Note that `shakenfist/util/metrics_scrape.py`, `shakenfist/util/caller_identity.py` and `shakenfist/util/grpc_channel.py` are new modules in these ranges and get the closest read. Report the greps' actual output, not a summary of it. |
| 8c | high | opus | none | **2a, code quality.** Take 8b's mechanical output as input. Review the ten ranges for duplicated logic, missed abstractions, and the two blocking rules: SQL pushdown (any new `mariadb.get_all_*(` that should be a `find_*`, including callers that reach it through a helper that scans) and the cached-FK-list pattern (any new `list[str]`/`list[UUID4]` on a `shakenfist/schema/*_attributes.py` model that a `WHERE <fk> = ?` could serve live). Both rules matter unusually much here: this is a plan about database load, so a change that reduced polling while introducing a full scan would be a self-defeating defect, and phase 2's cache is precisely a denormalisation. Also apply the comment-proportion shared block -- phases 6 and 7 added long explanatory comments and some are load-bearing while others restate the code. Check `shakenfist/mariadb.py` (which is where the object cache lives -- `_OBJECT_CACHE` and the `_object_cache_*` helpers around `:167-236`, not a `cache.py` module; see survey finding 5), `shakenfist/baseobject.py` and `shakenfist/daemons/daemon.py` as they stand on the phase 7 branch, not only as the ranges changed them. Triage every TODO / `# noqa` / `# type: ignore` the sweep flagged as blocking or advisory. |
| 8d | medium | sonnet | none | **2b, test coverage.** Review the ten ranges for coverage. The plan added roughly twenty test modules; the question is not quantity but whether the *risky* changes are covered. Specifically: does the object cache have invalidation tests (a write during a read, a delete of a cached object, TTL expiry) and not merely hit/miss tests? Does the idle-poll backoff have a test that a daemon still shuts down promptly, given phase 1 changed the shutdown-signalling path of every daemon? Do the phase 5 backoff changes cover the wake-on-work path as well as the sleep path? Shaken Fist prefers functional to unit coverage, so name which `shakenfist/deploy/shakenfist_ci` tests exercise each behaviour and which have unit coverage only. Flag assertions that test implementation details rather than behaviour. Note explicitly that `shakenfist/deploy/shakenfist_ci/database_tier.py`'s positive control has no unit coverage by construction -- it needs a live cluster -- and say whether that is acceptable or a gap. |
| 8e | medium | sonnet | none | **2c, documentation.** Check documentation against code across the ten ranges, applying the README, LLM-doc and plan-phase-reference shared blocks from `PUSH-AUDIT.md`. The specific risks here: `AGENTS.md`, `ARCHITECTURE.md` and `CLAUDE.md` were all edited by these ranges and the shared blocks say growth in those files is itself a finding; `docs/operator_guide/database.md` gained a large section in phase 7 and should be checked for content that belongs in `docs/developer_guide/`; survey finding 5 gives you one known-stale claim in `CLAUDE.md:163` already attributed to a pre-plan PR, so treat it as a worked example of what to look for rather than as a finding to rediscover; and the plan-phase-reference block forbids "phase N" references in `docs/` outside plans directories, which a plan this size is likely to have leaked. Confirm the nine plan documents' statuses agree with `docs/plans/index.md` and with `tools/check-plan-status.py`. The plan changed no database schema, so migration guidance does not apply -- confirm that rather than assuming it. |
| 8f | high | opus | none | **2d, security.** Security review of the ten ranges. The live areas, in order: **(1) the metrics surface.** Phase 4 added caller attribution to counters and phase 7 added `shakenfist/util/metrics_scrape.py` and an `sf-ctl` subcommand that scrapes gateway metrics ports. Does any counter label or scraped value carry a namespace name, object UUID, or anything else that turns an unauthenticated metrics port into an information leak? Is the metrics port authenticated at all, and if not, is that written down? **(2) Resource exhaustion.** The `sf-ctl database-load` scrape is unbounded in the number of series it parses from a remote endpoint; check for a cap and a timeout. **(3) Concurrency.** Phase 2's cache is shared mutable state reached from every daemon; check for lock ordering against `ClusterLock` and for anything that can deadlock or spin. **(4) SQL.** Any f-string or `text()` interpolation in the new `mariadb.py` functions. **(5) Input validation.** The budget YAML is read through `importlib.resources` from the installed wheel -- confirm it is parsed with `yaml.safe_load` and that a malformed or hostile file produces an error rather than code execution. Report findings with severity; critical and high must be fixed before this phase closes. |
| 8g | high | opus | none | **The freshness and observability lens (decision 4).** This is the plan-specific brief and it has no template heading. Build the list of what this plan stopped doing: every fixed-rate read it deleted, every value it moved behind a cache, and every backoff it lengthened. The phase 1, 2 and 5 plan documents name these directly, and `shakenfist/data/database_load_budget.yaml` is a second index -- a pair with a near-zero coefficient in that file is a loop that used to run. For each, answer four questions: what now reads state that may be stale; what is the worst-case staleness in wall-clock terms; who observes a stale read and what do they do about it; and what used to be observable that no longer is. Concrete cases to start from and *not* to stop at: daemon state transitions now poll at `DAEMON_STATE_POLL_INTERVAL` (does anything need to see a daemon stop faster than that?); the elected cluster loop's `ELECTED_LOOP_POLL_SECONDS` sets both its liveness and its `GetNodeDaemonState` rate, so the two are now coupled -- is the coupling documented and is either value load-bearing for the watchdog windows in `docs/`?; the object cache serves static values, so anything that mutates a "static" value is now a correctness bug rather than a slow read -- is there anything that does?; and the IPAM cache from #3508 sits next to the in-memory-only IPAM trap recorded in `CLAUDE.md` as issue 3532. Report each trade as sound, undocumented, or a defect. "Sound" requires naming the mechanism that makes it sound, not the absence of a bug report. |
| 8h | high | opus | none | *(Management session.)* Grade every finding blocking or advisory, fix the blocking ones (here or in #3893 per decision 2), file the advisory ones as issues, and write the results into this plan's Findings section and the master plan. Check each heading names what it examined, per decision 7. Set both statuses, run `tools/check-plan-status.py` and `pre-commit run --all-files`. |

## Risks and mitigations

* **The audit rubber-stamps merged code.** Reviewing what already
  shipped and passed CI invites confirming it. Mitigation: decision 7 --
  every heading names what it examined, and "nothing found" is only
  acceptable alongside that list. Step 8h checks this before writing
  results.
* **Ten ranges is enough surface to skim.** Roughly 4,200 insertions
  across the merged ranges plus 7,000 in phase 7. An agent that reads
  the diffstat and reasons from file names will produce plausible
  findings that are not about this code. Mitigation: each brief names
  specific files and specific questions, and 8h spot-checks two findings
  per agent against the tree before accepting the report.
* **The net state differs from the ranges.** Phase 5 rewrote phase 1's
  backoff; phase 6 rewrote phase 4's counters. An agent reading only the
  ranges can report a defect that a later phase already fixed.
  Mitigation: decision 1's second paragraph, restated in 8c's brief --
  check the file as it stands before reporting.
* **The fifth lens finds something expensive.** A freshness defect in
  phase 2's cache would be a correctness bug on `develop` today, and the
  fix would not be small. Mitigation: that is what the phase is for. It
  lands in this phase's PR; if it is too large for that, this plan says
  so and files it at high priority rather than downgrading it to
  advisory to keep the phase small.
* **#3893 merges mid-audit.** Decision 2's consequence. Mitigation:
  rebase onto `develop`; no finding changes, only where its fix lands.

## Definition of done

* Every one of the five wave 2 headings (2a, 2b, 2c, 2d, and the
  decision-4 lens) has a written result naming what was examined.
* Wave 1's two commands and four style greps have been run against the
  decision-1 ranges, with output recorded -- not asserted.
* The proto-freshness check is explicitly recorded as not applicable,
  with the evidence that no range touches `protos/`.
* Every finding carries a grade (blocking or advisory) and a
  disposition (fixed here, fixed in #3893, filed as #NNNN, or declined
  with a reason in writing).
* No blocking finding is left unresolved. A finding is resolved when the
  defect it names is fixed; a *related* gap the fix reveals but does not
  cause may be filed, provided the disposition table says so explicitly
  and grades the filed remainder advisory in its own right.
* The decision-4 lens has produced an explicit list of the reads this
  plan removed and the caches it added, and each entry is graded sound,
  undocumented, or a defect -- with "sound" naming a mechanism.
* Two findings per agent have been spot-checked against the tree by the
  management session, and the spot-check is recorded.
* `tools/check-plan-status.py` passes, and the master plan's Execution
  table and `docs/plans/index.md` agree with each other.
* The master plan's status becomes Complete only if no blocking finding
  remains open, under the definition above, and phase 7 has merged.
* If the audit finds nothing, that is recorded in one sentence, per the
  master plan's own instruction for this phase.

## Findings

*(Written by steps 8b through 8h.)*

## Back brief

Before executing any step of this plan, back brief the operator on your
understanding of it and how the work you intend to do aligns with it.

Gate: after step 8b, report whether wave 1 passed before spending on
wave 2. If wave 1 fails for a reason unrelated to this plan -- decision
5 -- say so explicitly rather than presenting it as an audit finding.

Gate: before 8h fixes any blocking finding whose repair is larger than a
few lines, report the finding and the intended fix to the operator. A
blocking finding on merged code means something is wrong on `develop`
right now, and how much of that repair belongs in an audit phase is the
operator's call, not the auditor's.
