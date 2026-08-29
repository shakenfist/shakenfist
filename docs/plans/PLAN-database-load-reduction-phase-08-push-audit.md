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

Six agents ran: wave 1 (8b), the four `PUSH-AUDIT.md` judgment headings
(8c-8f), and the decision-4 freshness lens (8g). Every heading below
names what it examined, per decision 7.

### Wave 1 (8b) — passed

`pre-commit run --all-files` green (ten hooks); `tox` green in 265s,
3714 tests, no failures. Style greps run against each of the ten ranges
individually: no line over 120 characters, no stray `print(`, one
`etcd` hit which is a historical comment in `mariadb.py:144` explaining
the cache's design lineage. **Proto freshness is not applicable**, with
evidence: `git diff --name-only` over all ten ranges matches nothing
under `protos/` or `shakenfist/protos/`, so `tox -e genprotos` was
correctly not run.

The pushdown grep hit twice, and decision 1's net-state rule changed
the answer for both. `baseobject.py` `_maintain_version_cache()` was
untagged when phase 2 landed it and carries `# nopushdown: every node
wanted` today, added later by unrelated commit `2feb509bb` -- an audit
reading ranges alone would have filed a fixed defect. `ctl.py:585` is
still untagged on the phase 7 branch (F-R1 below).

### The three defects

**F-D1. The floating IP reaper still reads one full table per address,
and the budget now records the residue as expected load.** BLOCKING.
Code merged (#3818); budget on the open branch (#3893).

`floating_ip_reaper.py:55,70` calls `ipam.is_free(addr)` once per
floating gateway and once per floating interface. `IPAM.is_free()` is
`address not in self.in_use` (`ipam.py:207`) and `in_use` is a property
issuing a fresh `mariadb.get_addresses_in_use()` RPC on every access
(`ipam.py:186`). That is one whole-table read per address -- the exact
shape #3655 and phase 6 existed to remove, surviving in a second helper.
`:45` spends another RPC building a `LOG.debug` argument evaluated
regardless of log level.

Phase 6's Definition of done claims "#3655 is fixed and closed, with a
functional-CI assertion that the reservation sweep issues one bulk read
per pass rather than one per address". The assertion is
`test_reaper_read_count_does_not_grow_with_address_count`
(`test_reservation_sweeps.py:111`), whose comment says it "actually
holds the fix in place". It cannot: its fake sets `self.in_use` as a
plain set attribute (`:21`) and overrides `is_free()` as a local dict
lookup (`:42`), so neither touches the `per_address_reads` counter the
test asserts is zero. The fake makes free precisely the call that costs
a round trip in production.

The budget then canonised it. `GetAddressesInUse`/`net` carries
`per_instance_qps: 0.114` at a measured mean of 5.1/s, with the note
"the loop #3655 reduced from one read per address to one bulk read per
pass" -- false as written. Its replacement `GetReservationsForIPAM`/`net`
sits beside it at 0.6/s with the note "It is meant to be flat in
instance count; a slope here is a regression". The plan wrote down the
test for this regression and applied it to the wrong call. Step 7a's
brief said "**Use post-phase-6 numbers, not today's** -- this file
defends a floor, and encoding a regression as the budget is the exact
failure the phase 5 plan warned about"; this is that failure.

Honesty caveat: the call path is proven, but attributing the 0.114
coefficient specifically to these sites is inference, not measurement
(its r-squared is 0.457 and it has not been re-measured on sfcbr).

**F-D2. The queues dispatcher backs off when its worker pool is full,
not only when the queue is empty.** BLOCKING, medium. Merged (#3506).

`dequeue_job()` returns `False` for two unrelated conditions and its
own docstring says so at `daemon.py:665-666`: "Returns True if at least
one job was started, False if the pool is full or there was nothing
eligible to claim." The pool-full return is at `:671-672`, *before* the
`mariadb.dequeue_work_items` call at `:714`. `queues/main.py:170-173`
treats both identically and sleeps `poll_backoff.next_empty_interval()`.

So a saturated node climbs 0.2 -> 2.0s over three seconds of continuous
fullness and then notices a freed worker slot up to 2.0s late, mean
~1.0s, against 0.2s before #3506. The backoff exists to remove database
load, but the pool-full branch issues *no* database call, so the sleep
buys nothing and costs dispatch throughput. `IDLE_POLL_MAX_SECONDS`'
own comment at `daemon.py:73-77` claims "a burst is still drained at
full speed and only the idle->work transition pays the extra latency",
which is false exactly when a burst is large enough to fill the pool.

It is invisible to everything this plan built: the pool-full branch
makes no database call, so `Dequeue`/`queues` does not move and the
budget's `per_node_base_qps: 0.512` is the idle rate with no busy
counterpart. A saturated dispatcher and an idle one are
indistinguishable in the metrics. This is the "a poll that no longer
runs has no line to review" shape decision 4 exists to catch.

The other two `IdlePollBackoff` call sites are the control and got it
right: `network/workitem.py:232` backs off only on `if not items:`,
with saturation handled by bounded worker queues; `transfers/main.py:136`
keys on an empty reply rather than on whether a worker started.

Coverage gap that hid it: `test_daemon_dequeue_job.py` sets
`pool.workers = {}` in every case, so the pool-full return is never
exercised, and `test_daemon_idle_poll_backoff.py` tests the backoff
class in isolation, never against a loop.

**F-D3. `_OBJECT_CACHE` is unbounded, never swept, and unmeasured.**
BLOCKING, medium. Merged (#3473, extended by #3508). Found
independently by 8f (as F2) and 8g (as D2), which is why it is graded
here rather than filed.

The complete symbol grep is nine lines (`mariadb.py:163,164,204,205,
211,227,228,234,235`). Entries leave by exactly two paths: a read of
the same key after expiry (`:211`), or an explicit evict from an
`update_*`/`delete_*` **in the evicting process only** (`:234-238`).
There is no sweeper, no LRU, no maximum size and no config knob for
one. An object read once and never read again stays resident for the
life of the process, and a delete evicts only in the process that
performed it -- every other process keeps the entry regardless. The
cache is therefore every object uuid a process has ever read, not a
working set.

Phase 2's plan anticipated this and deferred it: "Memory growth (blobs
number in the thousands). Bounded by TTL expiry; if needed, add a size
cap in a follow-up (noted, not built)." The premise is wrong in one
word -- expiry is lazy and access-triggered, so it bounds *staleness*,
not *residency*. The follow-up was never filed.

No privileged position is needed to grow it: an authenticated tenant
doing ordinary create/read/delete cycles grows the resident set of
every sf-api worker, sf-queues, sf-net and sf-cluster process. Nothing
observes it -- the hit/miss/eviction counters report rates, not
occupancy, and per F-U5 they are scraped from three daemons of about
twelve.

### Rule violations and smaller findings

**F-R1.** `ctl.py:585` calls `mariadb.get_all_node_metrics()` with no
`# nopushdown:` tag. The scan is substantively correct -- `_cluster_shape()`
genuinely wants every node and `instances_active` is not in
`NODE_METRICS_EXTRACTION_SPEC` -- but the rule is mechanical, its
sibling at `baseobject.py:79` carries the tag, and nothing enforces it,
so this fails every future audit until tagged. Phase 7 branch, fix in
#3893.

**F-R2.** Six late uncommented imports of `set_caller_identity`
(`ctl.py:152`, `nodelock/main.py:51`, `privexec/main.py:711`,
`sentinel_first/main.py:38`, `sentinel_last/main.py:35`,
`gunicorn_config.py:100`). `daemon.py:33` and `database/main.py:70`
import the same symbol at module top, which proves there is no cycle to
justify lateness. `gunicorn_config.py` may have a real reason (gunicorn
loads it before the app), in which case the fix is the comment, not the
move. Merged, advisory.

**F-R3.** `# noqa: E501` at `daemons/database/main.py:6170` is dead --
the line is 101 characters and flake8 runs at `--max-line-length=120`.
Merged, advisory.

**F-R4.** Triple-single-quoted strings at
`tools/generate-database-load-rules.py:43,209`, against CLAUDE.md's
unconditional rule. Both are templates embedding `"`, so the choice is
defensible; flake8 does not enforce it. Phase 7 branch, advisory.

**F-R5.** Copyright headers are internally inconsistent within the same
PRs: `metrics_scrape.py`, `caller_identity.py`, `load_budget.py` and
both `tools/` scripts say 2026; `schema/database_load_budget.py` and
`database_tier.py` say 2019, which is the repo convention. Advisory.

**F-R6.** `IPAM.get_allocation_age()` (`ipam.py:406`) is dead -- phase 6
removed its only production caller and left the method, which two test
fakes still simulate. It is also misnamed: it returns `reserved_at`, a
timestamp, not an age. Merged, advisory.

### Undocumented constraints (the decision-4 lens)

**F-U1. The immutable tier's documented membership and its documented
*criterion* are both wrong.** `ipam` joined the 300s tier at
`mariadb.py:17366` (#3508) and appears in none of the three places that
enumerate it (`config.py:362-364`, `docs/operator_guide/database.md:283`,
`docs/developer_guide/database_internals.md:104-105`). Worse, all three
justify the tier as "types with no post-creation writer", which is
false for two of its five members: `update_ipam` (`mariadb.py:17357`)
and `update_network_interface` (`:16955`) both exist. Both evict, so
the code is correct -- but an editor who adds an updater to an
immutable-tier type and believes the documented criterion will conclude
no eviction hook is needed. Merged, fix here.

**F-U2. A hard-deleted object hydrates as a live, database-backed
object for up to 300s in every process that had it cached, and this
interacts with the `in_memory_only` guard.** `Network.__init__`
(`network/network.py:73-79`) chooses `in_memory_only=True` *only when*
`IPAM.from_db` returns `None`. A stale cache entry makes it return a
hit, so a network whose IPAM was hard-deleted on the elected node can
hydrate elsewhere with a fully database-backed IPAM for the TTL -- the
object shape whose write path issue 3532's guard exists to close.
Graded undocumented rather than defect: no concrete caller was traced
that writes through such an IPAM inside the window (`find_*` iterators
are uncached and would not enumerate the deleted network). But
CLAUDE.md pitfall 5's invariant now has a second way to be violated
that has nothing to do with adding a persistence path. Wants a sentence
at the wiring site and in `coding_rules.md`. Merged.

**F-U3. Pulling the documented cache kill switch fires the phase 7
alerts, and neither section says so.** `docs/operator_guide/database.md:286-288`
presents `OBJECT_CACHE_TTL_*=0` as "a fast rollback to pure read-through".
The budget was derived with the cache *on*, so the pairs the cache
suppresses now sit under the inclusion cut and are governed by
`unbudgeted_fixed_rate_per_node_qps: 0.05`. `GetIPAM`/`cluster` alone
ran at 5.5/s pre-cache. Disabling the cache therefore puts a large set
of pairs an order of magnitude over the unbudgeted ceiling and fires
`ShakenFistUnbudgetedDatabasePolling` cluster-wide for as long as the
rollback is in effect -- arguably correct alerting, but an operator
pulling an emergency lever should be told in advance, and the budget's
own "do not edit the budget to make an alert stop" instruction leaves
them nowhere to go. Phase 7 branch, fix in #3893.

**F-U4. The real worst-case daemon-state staleness is 60s, not the 2s
the budget notes cite.** `DAEMON_STATE_POLL_MAX_INTERVAL = 60`
(`daemon.py:67`) doubles the interval on every `DatabaseUnavailable`,
reaching the cap after six consecutive failures. Six budget notes say
"rate-limited to `DAEMON_STATE_POLL_INTERVAL` (2s)" with no mention of
the backoff, so a cluster whose database is struggling reads *below*
its own budget for this pair -- the one direction `base_term_caveat`
does not cover. The constant arrived out of range (#3715) but it is
phase 1's net state. Note text on the phase 7 branch, fix in #3893.

**F-U5. The object-cache counters are scraped from three daemons of
about twelve.** `start_http_server` is called only in
`daemons/cluster/main.py:75`, `daemons/resources/main.py:211` and
`daemons/database/main.py:6424`. The counters are module-scope so they
exist in every process that imports `mariadb`, but the client-side
caches in sf-api, sf-net, sf-queues, sf-cleaner, sf-transfers and
sf-sidechannel -- where most of the hit rate and all of F-D3's memory
lives -- are unobservable. Phase 2's plan knew and accepted this;
`docs/operator_guide/database.md:288-292` does not carry the caveat and
reads as though the counters describe the cluster. Merged, fix here.

**F-U6. The stray-lock escalation threshold now equals its own scan
interval.** `STRAY_LOCK_CHECK_INTERVAL = 30` (`queues/main.py:25`)
moved the scan from every ~0.2s to every 30s; the warning-to-error
escalation at `:160` still uses a 30s threshold, so escalation lands on
the second or third scan depending on jitter. Nothing acts on a stray
lock (the sweep only logs), so the consequence is detection latency.
Two constants that must not be equal now are, and nothing says so.
Merged, advisory.

**F-U7. `subsystem_internals.md:315-316` still restates the elected-loop
poll as a literal** ("sleeps on `lock.lost_event.wait(5)`"). Phase 7
named that constant `ELECTED_LOOP_POLL_SECONDS` precisely so it would
be greppable, and this restates the number where an editor changing the
constant would not find it. Phase 7 branch, fixed in #3893.

  The finding as first written said this was "the only place in the tree"
  and that was wrong; fixing it turned up three more live sites --
  `tools/derive-database-load-budget.py:211`, the
  `GetNodeDaemonState`/`cluster` note in the budget, and `CLAUDE.md:317`.
  All four now name the constant. The plan files also restate it and were
  left alone: they record what was true when they were written.

### Security (8f)

Nothing critical or high. Six low/medium and four informational; F2 is
recorded above as F-D3. The rest:

* **Unvalidated `caller_daemon` label** (`database/main.py:6162-6167`):
  whatever arrives in gRPC metadata becomes a permanently-retained
  prometheus child, with no allowlist, length cap or charset check.
  Low, because anyone who can reach the insecure port at `:6652` can
  already read and write the whole database -- the marginal loss is the
  monitoring path. Merged, fix here (three lines).
* **Metrics parsers ignore Prometheus escaping**
  (`metrics_scrape.py:37-46` and the deliberate copy at
  `load_budget.py:128-136`): both split label blocks on `,` and `=`
  without honouring `\"`, so a crafted label forges an
  `(operation, caller)` pair. Chained with the previous item this is an
  attack on the regression detector itself -- false positives, or
  diluting a real pair below its ceiling. Low; phase 7 branch.
* **`ci-install-promtool.sh` skips verification on the cache-hit path**
  (`:32-46`): the download checks SHA256, but `if [ ! -x
  "${DEST}/promtool" ]` skips everything when a file is already at the
  predictable `/tmp/promtool-3.14.0`, on static runners whose own
  comment says `/tmp` persists between jobs. Low, riding on the larger
  pre-existing exposure that those runners execute untrusted PR code
  with a shared `/tmp`. Phase 7 branch.
* **The scrape has no response-size cap and no total-time bound**
  (`metrics_scrape.py:60-63`): `requests`' `timeout` is a
  read-inactivity timeout, not a transfer deadline. Client-side denial
  of service on an admin CLI. Low; phase 7 branch.
* **`check_daemon_state()` crashes on a malformed `NODE_UUID`**
  (`daemon.py`): `uuid.UUID(node_uuid)` sits inside a `try` catching
  only `DatabaseUnavailable`, so a bad `SHAKENFIST_NODE_UUID` raises an
  uncaught `ValueError` every 2s in every daemon. `config.NODE_UUID` has
  no validator and `_resolve_node_uuid()` returns early when it is
  truthy, so the value never reaches `Node._load_persisted_uuid()`'s
  guard. Operator-misconfiguration availability only. Low, one line,
  merged.
* **The cache extends tenant-secret residency** (advisory): `ssh_key`
  and `user_data` are plain `str` on `InstanceData` and instances sit
  in the 300s tier, so cloud-init credentials stay resident past the
  last read (and per F-D3 are never actually freed). No cross-tenant
  read path exists -- the cache is keyed by object identity and callers
  still authorise -- so this is secret-lifetime defence-in-depth, not
  an access-control break. File rather than fix.
* **Informational:** the sf-database gRPC and metrics ports are
  unauthenticated and documented nowhere (both predate this plan, but
  phase 4 put an attribution story and phase 7 a monitoring story on
  top of that port, so this is where it became worth writing down); the
  docs ask operators to attach `sf-ctl database-load --json` to a
  public issue tracker, and that JSON carries internal mesh IPs; agent
  get-file paths are now logged at INFO with tenant-controlled,
  unsanitised content.

Clean with evidence: zero new SQL of any kind across the ten ranges (no
`text()`, no f-string SQL, no concatenation); no range adds a REST
endpoint or gRPC method; `yaml.safe_load` throughout and no `pickle`,
`eval` or `exec`; no `shell=True` or `os.system`; no credential logged,
evented or returned. Lock ordering: `_OBJECT_CACHE_LOCK` guards three
pure dict operations with no I/O and no nested acquisition (the
prometheus increments are deliberately outside it), so it can be taken
while a `ClusterLock` is held but never the reverse and no inversion is
constructible.

### Test coverage (8d)

No blocking findings; the gaps are missing regression tests for code
that is correct today.

* **The wake path is untested everywhere.** `transfers` has tests for
  all-idle and all-busy but not the transition, which is the case that
  matters; `queues/main.py`'s dequeue loop and `network/workitem.py`'s
  dispatcher have no coverage of either path. "Sleeping longer is only
  safe if waking still works" is asserted nowhere in this plan. This is
  also the gap that hid F-D2.
* **`test_object_cache.py:5-7` claims coverage that does not exist:**
  "the per-type wiring... is covered in the object-type test modules".
  `grep -rl '_object_cache\|OBJECT_CACHE' shakenfist/tests/` returns
  that file alone. The cache is wired into eleven types; the
  read-evict-delete cycle is proven for `blob` and `ipam` only.
  Upgraded from the agent's advisory grade because a false claim in the
  tree is what stops the next reviewer looking -- it conceals the gap
  rather than merely leaving it. `instance` and `namespace` are the
  ones worth covering.
* `idle()` is never tested for breaking early when `abort_path` appears
  mid-loop -- the existing test mocks `os.path.exists` to `False`
  throughout, so it proves the interval and nothing about promptness.
  `exit_gracefully()` has no coverage at all (pre-existing).
* The functional-only positive control in `database_tier.py` was
  assessed rather than discovered, and is **acceptable, not a gap**:
  the untestable half genuinely needs a live cluster, and every piece
  of arithmetic beneath it is unit tested, including the constant-parity
  tests pinning the harness's duplicated constants to the daemon code.

### Documentation (8e)

No blocking findings. Three gaps against merged code, all fixable here:
the `ipam` omission from the immutable-tier enumeration (recorded above
as F-U1); caller attribution has no `docs/developer_guide/` coverage,
so a developer adding a daemon entry point is not told that skipping
`set_caller_identity()` reports their load as `caller_daemon="unknown"`;
and the idle-poll backoff has no developer-guide coverage either, only
code comments pointing at plan documents, which is the wrong home for
"how does this behave today".

Clean with evidence: `README.md` untouched by all ten ranges;
`AGENTS.md` untouched by the nine merged ones, and its single phase 7
bullet links out rather than restating; no `state_targets` change
anywhere, so the state-machine check is not applicable; no schema
change, so migration guidance is not applicable; every `phase N` leak
in `docs/` outside plans directories traces to other plans.
`tools/check-plan-status.py` passes.

One vindication of decision 1's net-state rule: #3473 really did add a
25-line cache deep-dive to `ARCHITECTURE.md`, a genuine
llm-doc-discipline violation at landing -- and unrelated commit
`f9117ca3d` later moved it into `docs/developer_guide/`. An audit
reading ranges alone would have filed a fixed defect.

Survey finding 5 (`CLAUDE.md:163` listing a `cache.py` deleted by
#2870) was assessed as instructed rather than rediscovered: the #3466
edit was a narrowly-scoped correction to a *different* module's row in
the Core Components table, 43 lines from the stale directory listing,
so missing it was reasonable. The underlying hazard -- two independent
enumerations of the same module inventory with no single source of
truth -- is the finding worth keeping.

### Management spot-checks (decision 7, DoD)

Eleven claims verified directly against the tree rather than accepted
from a report: the `is_free` -> `in_use` -> RPC path and both reaper
call sites; the reaper test's fake attributes; the two budget entries
and their contradictory notes; `dequeue_job`'s docstring and the
pool-full return preceding `dequeue_work_items`; the complete
`_OBJECT_CACHE` symbol grep; `uuid.UUID()` inside the
`DatabaseUnavailable`-only `try`; `_object_cache_put('ipam', ...)` at
the immutable TTL against all three doc enumerations; the
`test_object_cache.py` docstring against
`grep -rl '_object_cache' shakenfist/tests/`; `exit_gracefully` absent
from the test tree; `check-plan-status.py` passing; and the absence of
`shakenfist/cache.py` with its deletion attributed to #2870.

Two agent claims did not survive contact and are recorded as such
above: the `baseobject.py` pushdown tag (already fixed by an unrelated
commit) and the `ARCHITECTURE.md` growth (already moved). Both would
have been filed by an audit that read ranges without checking the tree,
which is what decision 1's second paragraph exists to prevent.

### What the audit missed (8h)

Fixing the metrics parser finding turned up a third copy of that parser
which the audit did not name: `scrape_operation_requests()` in
`database_tier.py`. The security finding listed `metrics_scrape.py` and
"the deliberate copy" in `load_budget.py` and stopped counting at two,
because that is how the parity test and both docstrings describe the
arrangement -- the finding inherited the tree's own account of itself
instead of grepping for the sample name, which returns three files.

The third copy was worse than the two that were found. It matched label
substrings against the raw line, so the escaping weakness was there too;
it read the *last* whitespace field as the value, which is the trailing
timestamp bug the other two copies had already been fixed for and which
their tests and comments describe as fixed; and splitting the line on
whitespace truncated the label block at the first space inside a quoted
value. `scrape_database_counters()` beside it read the last field as
well. Both are corrected, and `scrape_operation_requests()` now shares
the parser rather than carrying a third one.

The general lesson is the one this plan already applies to code and did
not apply to itself: a docstring saying "there are two copies and a test
asserts they agree" is a claim about the tree, and claims about the tree
get checked against the tree. Decision 7 required "nothing found" to
list what was examined; it should equally require a count to say how it
was arrived at.

### Disposition of every finding (8h)

Landed on this branch, against `develop`, in `c56d687c7`: F-D1 with its
blind test fake, the cache residency bound and its per-type eviction
coverage, the immutable-tier description in three places, the
`NODE_UUID` `ValueError`, and the `caller_daemon` label allowlist.

Landed on this branch afterwards: F-U3, the cache kill switch. Routed to
#3893 when it was written, because the alerts it fires are phase 7's, and
moved here because phase 8 rewrites the very paragraph it annotates and
merges after phase 7 -- so the alert exists by the time the warning
does, and neither branch conflicts with the other.

Landed in #3893: the metrics parser escaping and the two trailing
timestamp reads described above, the `nopushdown` tag on
`ctl.py`, cache-hit verification in `ci-install-promtool.sh`, F-U4's
backoff caveat on six budget notes, F-U7 at all four sites, and F-R4.
The `GetAddressesInUse`/`net` note is there too rather than here,
because `database_load_budget.yaml` does not exist on `develop` at all
-- it arrives with phase 7 -- so the note correcting a claim about the
reaper had to go on the branch that carries the file, even though the
reaper fix itself is here. The note says so, and says which direction
the un-refitted coefficient now errs in.

Filed rather than fixed: #3942 (dispatcher pool-full backoff) and #3944
(secret residency), both labelled `automated-fix-attempted` because each
needs a design decision rather than a same-day patch; #3943 (wake-path
and per-writer cache eviction coverage), left unlabelled because an
independent automated fix is welcome there.

Left as advisory and not acted on: F-R5's copyright header years and
F-R6's dead `IPAM.get_allocation_age()`, both recorded above.

### What the automated reviewer caught that this audit did not (#3950)

The audit fixed three defects and then introduced two of its own, both
of which the automated PR review found and this audit's own reading of
its own diff had not. That is worth recording in the same spirit as
"what the audit missed" above.

**The occupancy gauge did not count the read path.** The residency bound
added `database_object_cache_entries`, set in `_object_cache_put()` and
`_object_cache_evict()` but not in `_object_cache_get()`, which is where
a lazily-expired entry is actually dropped. Since reads outnumber writes
— the entire premise of the cache — the gauge over-reported occupancy
indefinitely, and the operator guide written in the same change points
operators at it as authoritative. The mechanism is the one this phase
already documented for a different defect: the finding that a metric was
unasserted (test-coverage gap 4, "OBJECT_CACHE_SIZE is never asserted")
was written down and then not acted on, and the bug it predicted was
sitting in the diff at the time. `test_the_occupancy_gauge_tracks_the_
lazy_expiry_read_path` now closes it, and fails against the pre-fix code
with `0 != 10.0`.

**The trim amortisation was defeated in a band.** `OBJECT_CACHE_TRIM_
TARGET` exists so the O(n) pass runs once per `cap - target` inserts
rather than once per insert, but only the sort branch trimmed to target;
the expired sweep trimmed to `cap`. Where roughly one entry expires per
insert, freeing that one entry left the cache at exactly `cap`, so the
next insert was over again and rescanned every entry under the lock —
a full scan on every put, which is the amortisation inverted rather than
applied. Both branches now trim to target. `test_a_full_scan_is_
amortised_when_one_entry_expires_per_insert` counts `items()` calls
because both O(n) passes walk the dict that way: 120 scans over 200
inserts before the fix, under 60 after.

**The NODE_UUID guard traded a crash for silence.** The audit's own fix
for the malformed-`SHAKENFIST_NODE_UUID` crash routed through
`_log_stability()`, which logs at debug and dedupes within 10s. That is
right for transient cluster-version churn and wrong here: the condition
is a permanent operator error, so at default log level nothing was
emitted at all, the offending value was not named, and the early return
means the daemon can never reach `set_abort_path()` and will not observe
a stopping or stopped transition for the life of the process. Now logged
once at error with the value. `test_daemon_node_uuid_guard.py` covers
all three properties, including a vacuity guard that a valid uuid still
reaches `get_node_daemon_state()`.

Also corrected: the master plan's Execution table had phase 7 as "Not
started" while phase 8 was "In progress", contradicting `index.md` and
this plan's own survey finding 4, which asserted the two agreed. Phase 7
is #3893.

The reviewer's remaining findings were left as recorded advice. Its
merge-ordering finding (the operator guide naming phase 7 artifacts
absent from `develop`) proposes gating this PR behind #3893, which is
already decision 2's disposition. Its observation that the capacity trim
sheds the whole 30s mutable tier before touching a 300s immutable entry
is a real and undocumented consequence of sorting mixed TTLs by absolute
expiry. The behaviour is now stated in the operator guide, and changing
the policy — sorting by remaining fraction of TTL so the tiers compete on
equal terms — is filed as #3953, because that is a design decision this
phase should not make on its way out.

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
