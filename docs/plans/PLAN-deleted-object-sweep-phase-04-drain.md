# Phase 4: drain sfcbr and keep it drained

Parent plan:
[PLAN-deleted-object-sweep.md](PLAN-deleted-object-sweep.md).

**Planning effort:** medium, as the master plan specifies. This phase
changes no daemon code. It raises one configuration value on one
cluster, watches what the database tier pays, adds the regression
guard the mission asks for, and writes down how to do the same on
another cluster. The judgement in it is the drain rate and the "clean
enough to raise it again" test, and both are decided below rather
than left to the implementer.

Decisions continue the plan's single sequence: phase 3 used D14-D23, so
this phase starts at **D24**.

## Scope

In:

- The 24 hour reading of phase 3 on sfcbr, which brief 3g asked this
  phase's planning commit to record (recorded in the master plan
  under "What phase 3 measured"; see the survey for why it is a 16
  hour reading today and what completes it).
- Raising `DELETED_OBJECT_SWEEP_MAX_OBJECTS` on sfcbr, in steps, with
  a written test for whether the tier absorbed each step (D24, D25).
- A regression guard: a nightly fact in the 33fl infra report that
  turns the backlog gauge and the hard-delete counter into "days
  behind" per object type and flags a type that is behind or growing
  (D26).
- The answer to phase 3's D23: whether the sweep's RPC pairs need
  load-budget entries, decided from the rate they settle at once the
  backlog is gone (D27).
- The closing condition for #4473, the soak that proves it, and the
  #4399 reading the master plan calls a natural experiment (D28,
  D29).
- Operator documentation of all of the above.

Out:

- **Changing the shipped default of 100 objects a pass.** It is
  nearly four times sfcbr's arrival rate and sfcbr is the busiest
  cluster we know of (D25).
- **A Grafana alert on the backlog.** No Shaken Fist metric is alerted
  on today and the signal is day-scale (D26).
- **#3922** (errored objects are never hard deleted). It adds
  arrivals. It becomes safe to pick up once D28's soak has passed, and
  the master plan's Future work says so.
- **Anything about the sweep's code.** One observation about it is
  recorded under "What the survey found" and left where it is.

## What the survey found

All references are to `develop` at 7d013a5d2a and to 33fl at 2f77f500
unless stated. The master plan's phase 4 section was written before
phases 2 and 3 ran, and the survey corrected four of its claims at the
source (the master plan's phase 4 section now carries a *Revised while
planning phase 4* note, and the `docs/plans/index.md` row was
rewritten); this section records what was found so no later step
repeats the check.

1. **Phase 3 went live on sfcbr at 2026-10-28T09:09Z**, when the
   nightly deploy restarted sf-cluster and sf-4 (pid 1577153) was
   elected. At planning time it had run for 16.7 hours, not the 24
   that brief 3g asks for. The numbers under "What phase 3 measured"
   in the master plan are therefore a 16 hour window ending
   2026-10-29T01:55Z, cross-checked against the per-pass log lines in
   Loki for the same period, and the first step of this phase re-runs
   them over the full first 24 hours (window ending
   2026-10-29T09:10Z) and replaces them in the same pull request.
   The reading will not change in kind: every number below is a rate.

2. **The cap, not the time budget, sets sfcbr's drain rate.** Of 913
   passes since the deploy, 891 took exactly 100 entries (the cap) and
   22 fewer, those being the passes that ran into the 20 second budget
   with a turn of per-object namespaces or a 3.4 second blob batch
   inside them. Mean processing time per pass is 4.6 seconds, median
   3.2. The sweep ran 54.7 passes an hour rather than 60: the pass is
   registered every 30 seconds (phase 3's D19) but the maintenance
   loop also runs the hourly backlog count and the other jobs, and
   every pass that overran the budget pushed the next one out. That is
   about 9% of capacity and is not worth a change; D24's arithmetic
   uses 55 passes an hour.

3. **Throughput per type is the cap divided by the types with work.**
   Thirteen types held a backlog and each was hard deleted about 8,000
   times in 16 hours, within 2% of each other. The round robin takes
   one turn of 25 from each type with work, so at a cap of 100 a pass
   serves four types and the rotation completes every 3.25 passes.
   That is the fairness phase 2 asked for, and it is why raising the
   cap raises every type's share equally until a type drains, when its
   share passes to the rest. The total drain rate is what the cap
   sets: 86,075 hard deleted in 16 hours against about 43,000 arrivals
   a day, so the backlog fell by 61,074 in the 17 hours from 08:00Z to
   01:00Z (1,655,145 to 1,594,071), about 86,000 a day. At that rate
   the 1.59M backlog takes about 18.5 days.

4. **The master plan said the throttle "defaults to about phase 2's
   rate".** It defaults to about phase 2's *intended* rate (D18's 100 a
   pass), which phase 3's cadence fix turned into 1.7 times phase 2's
   measured rate: 129,000 a day at the cap against phase 2's 76,840.
   Corrected in the master plan's phase 4 section.

5. **The master plan said to add load-budget entries "once the drain
   rate is settled".** At the rates measured, every one of the sweep's
   RPC pairs is already under the unbudgeted ceiling of
   `max(0.25, 0.05 x nodes)` = 0.30/s on sfcbr's six nodes
   (`shakenfist/data/database_load_budget.yaml:110-111`):
   `HardDeleteObjects` 0.088/s from sf-cluster and 0.023/s from
   sf-api, the three base deletes (`DeleteObjectState`,
   `DeleteObjectEvents`, `DeleteObjectMetadata`) 0.143/s each from
   the per-object types, `DeleteNamespace` 0.131/s, `GetObjectsByState`
   0.26/s against its budgeted 0.222/s base (tolerance is twice
   that), and the hourly `CountObjectsByState` 0.008/s. The drain will
   push the per-object namespace pairs over the ceiling for its
   duration, and once the backlog is gone every pair falls to its
   arrival-driven rate, which is lower than today's. So the honest
   expectation is that no entry is needed. The question is answered
   from the settled rate rather than assumed (D27), and the master
   plan's phase 4 section now says so. Nothing on sfcbr enforces the
   ceiling during the drain: the generated rules in
   `examples/prometheus-database-load-rules.yaml` are not deployed on
   the 33fl Prometheus (phase 2's execution notes checked), the 33fl
   nightly ratchet warns about an unbaselined pair only above 2.0/s
   (`claude_infra_reports/facts/2026-10-28.json`, `thresholds`), and
   CI's `test_no_unbudgeted_fixed_rate_database_polling` sees CI's
   own cluster. That is acceptable for a bounded drain and is written
   down here so the ratchet's reader is not surprised by a namespace
   delete pair at 0.4/s for three days.

6. **How comparable signals are reported.** The master plan offered
   "an entry in the 33fl infra report ... or an alert, whichever
   matches how comparable signals are already reported". The
   comparable signal is sf-database load, which is a nightly report
   ratchet (`shakenfist_db_load`, `tools/infra-report-precompute.py:7012`
   in 33fl) and not an alert; `config/maui/grafana/alerting/rules.yml`
   in 33fl alerts on no Shaken Fist metric at all, and the report's own
   convention for a day-scale signal with no 3am action is a ratchet
   (`compute_timer_overhead`'s note, 33fl `:2643`). So the guard is a
   report fact (D26). The operator guide's suggestion of an
   `absent()` alert on the gauge stays as operator advice.

7. **Where the knob is set on sfcbr.** sfcbr's cluster configuration
   is `extra_config` in 33fl's `group_vars/allsf.yml:47`, applied with
   `sf-ctl set-config` by the collection's cluster configuration play
   and read back and asserted by `sfcbr.yml:284-300` on every deploy.
   `shakenfist/config.py:1260` loads `cluster_config` once at import,
   so a changed value reaches sf-cluster only on restart. The nightly
   deploy restarts the daemons when code, rendered config or units
   changed (`roles/node/tasks/register.yml:33-38`, `:197-210`), which
   on sfcbr is most nights because `develop` moves; a night it does
   not is detectable from the pass log (the `total` field stays at or
   under 100) and is fixed with a restart of sf-cluster. A hand
   `sf-ctl set-config` would also work but would not be in 33fl's
   history, and 33fl is where every other sfcbr setting lives.

8. **The documentation the master plan names is in the right place
   already.** Phase 3 documented the three options under "The
   deleted-object sweep" in `docs/operator_guide/database.md:970-1004`,
   not under "Watching for sweeps that silently do nothing" (`:1032`),
   which is where the master plan said this phase's tuning notes would
   go. The tuning goes with the options, and the regression check with
   the metrics, so this phase adds to both sections rather than one.

9. **`sf-db-load sweep` exists** (the `sf-db-load` helper's `sweep`
   subcommand) and prints per-day passes, items processed and hard
   deletes per type from the sfcbr log. It is the quick view for 4d
   and 4f, and the plan names it rather than restating its queries.

10. **One observation on the sweep's code, left alone.** The 20 second
    budget is checked between turns and between objects of a
    per-object turn, so a pass can overrun it by one batch call:
    the longest pass since the deploy processed for 33.8 seconds. It
    is bounded by `DELETED_OBJECT_BATCH_SIZE` and does not matter at
    these rates, but a cluster whose blob batches took tens of seconds
    would see it. Not this phase's problem; recorded so the number in
    the log is not mistaken for a stall.

11. **The two counters disagree by extrapolation, not by data.**
    Dividing `increase()` of the entry counter by `increase()` of the
    batch counter over a window that straddles the deploy gives 30
    entries a batch, more than the batch size of 25, because the batch
    series starts inside the window and Prometheus extrapolates the
    two series differently. Over a window entirely after the deploy
    the ratio is 25. Step 4a uses post-deploy windows only.

12. **#4399 is unchanged so far.** sf-cleaner's `GetObjectState` was
    3.04/s over the 16 hours against 2.77/s in the day before, with
    10% of the backlog gone. The master plan's natural experiment
    needs the drain to finish before it can be read (D29).

## Decisions

### D24 -- Raise sfcbr's cap to 400 a pass, then 800, each held for a day

The drain is set by `DELETED_OBJECT_SWEEP_MAX_OBJECTS`, applied
through 33fl's `extra_config` (finding 7). The first step is **400**,
four times today's value:

- Capacity is 400 x 55 passes x 24 = 528,000 a day; net of 43,000
  arrivals, the 1.59M backlog drains in about 3.5 days.
- A pass at 400 is sixteen turns of 25. Fifteen are batches at about
  0.25 seconds (finding 3's latencies) and one is a turn of 25
  namespaces at 0.25 seconds each, so a pass processes for about 10
  seconds of its 20. The budget is not binding, and if the tier slows
  it becomes binding before anything else does, which is the
  self-limit phase 3's D18 kept.
- The tier pays about 15 more commits a minute for the batches and
  about 125 for the namespace turn, so about 2.5 commits a second
  more than today, and about 5 requests a second more. Phase 2 paid
  3.3 commits a second more than phase 3 does for less than half the
  deletes, and the tier absorbed it. This step is inside what has
  already been seen to work.

After 24 hours at 400, if every clean test in D25 holds, the cap goes
to **800** (about 1.0M a day; the remainder drains in a day and a
half), and is left there until the backlog is gone, at which point
the sweep is arrival-bound whatever the cap says. If a test fails the
cap is held, or dropped back to 100, as D25 says.

This is the decision most likely to be argued with, from either side.
Flat out would be a cap of 1,000 or more with the time budget as the
only limit: the master plan ruled that out as an unmeasured write
load on labqnap, and the two-step version costs one extra day. The
cautious alternative is to leave 100 and wait 18 days, which is a
fortnight of elevated `GetInstance` costs for every lister on sfcbr
and a fortnight before #4399 can be read.

### D25 -- What "the tier absorbed it" means, decided before the drain

A step is clean when, over its first 24 hours and against the phase 3
baseline (the 16 hour window in "What phase 3 measured", refreshed by
4a):

1. MariaDB commits rose by no more than 5 a second
   (`sum(rate(mysql_global_status_commands_total{command="commit"}[24h]))`,
   baseline 14.8/s).
2. Batch latency for the operation types stayed under 1.5 times the
   baseline (`cluster_deleted_object_batch_seconds` per type, baseline
   0.21-0.25 s; so under 0.38 s), and the namespace per-object cost
   under 0.4 s (baseline 0.25).
3. InnoDB row lock waits did not double
   (`sum(rate(mysql_global_status_innodb_row_lock_waits[24h]))`).
4. No sf-database process restarted outside a deploy
   (`changes(process_start_time_seconds{job="shakenfist-database"}[1d])`
   per instance), and Loki shows no watchdog SIGABRT of a gateway
   (the signature in the sf-database watchdog memory: `{job="shakenfist"}
   |= "sf-database" |= "SIGABRT"`, tenant sfcbr).
5. The sweep's own median processing time per pass stayed under 15
   seconds (the `elapsed_seconds` field of the pass log line), so the
   budget was not what bounded the drain.
6. The CI runner flake-rate alert did not fire for a cause that
   traces to sfcbr's database tier.

Holding on a failed test means leaving the cap where it is for
another day and re-reading. Dropping back means editing
`extra_config` to 100 and, to take effect before the next nightly
deploy, `sf-ctl set-config DELETED_OBJECT_SWEEP_MAX_OBJECTS 100` on a
database node followed by a restart of sf-cluster on every node.
Both are recorded in the execution notes with the numbers that
decided them.

The shipped default stays at 100. It is four times sfcbr's arrivals,
sfcbr is the busiest cluster we know of, and a cluster that has
accumulated a backlog is better served by an operator reading the
new "Draining a backlog" section than by a default sized for a
one-off drain.

### D26 -- The regression guard is a nightly report fact, `shakenfist_sweep_backlog`

Added to 33fl's `tools/infra-report-precompute.py` beside
`compute_sf_db_load`, registered as a section, with a note, a prompt
subsection under section 19, a `docs/infra-report-facts.md` bullet,
`FLAG_CHANGE_SPECS` entries and unit tests. From four instant queries
at the report time:

```
max by (object_type) (cluster_deleted_object_backlog)
max by (object_type) (max_over_time(cluster_deleted_object_backlog[2h] offset 24h))
sum by (object_type) (increase(cluster_deleted_object_seconds_count{outcome="hard_deleted"}[24h]))
sum by (object_type) (increase(cluster_deleted_object_seconds_count{outcome="failed"}[24h]))
```

it computes, per type: `backlog`, `backlog_prior` (null when the
prior sample is missing), `hard_deleted_24h`, `arrivals_24h` =
`hard_deleted_24h + (backlog - backlog_prior)` clamped at zero,
`days_behind` = `backlog / arrivals_24h` (null when arrivals are
under 100 a day, where the ratio means nothing), and two flags:
`behind` when `days_behind` is over 1.0 and `backlog` is at least
1,000, and `growing` when `backlog - backlog_prior` is at least the
larger of 1,000 and a tenth of `backlog_prior`. Cluster-wide it
carries `total_backlog`, `total_prior`, `hard_deleted_24h`,
`failed_24h`, `net_per_day`, and `days_to_drain` (`total_backlog /
-net_per_day` when the net is negative and the total is over 50,000)
so the report can say "draining, about N days left" during this
phase and "behind" after it.

Three things about the gauge that the function must respect, and the
tests pin: it is leader-only, so a leadership change in the last hour
leaves it absent, and absence is reported as `unavailable` for the
whole section rather than read as zero; two leaders can briefly both
publish it, so it is read with `max by`, never `sum by`; and the
24-hour-ago sample is read over a two hour window because the count
is hourly and the series may have a gap.

The mission's bound is 24 hours (master plan, mission 1), which is
what `days_behind > 1` encodes. The 1,000 floor keeps a type with
forty objects inside its one hour grace period from flagging; an
object type that matters has more arrivals than that in a day.

Why not an alert: finding 6. Why not a streak counter like
`shakenfist_db_load`'s `days_flagged`: the closing condition (D28)
reads seven consecutive nightly facts files, which is a management
session's `jq` rather than code, and the report's "Resolved since the
previous night" section already comes from `FLAG_CHANGE_SPECS`.

### D27 -- Load-budget entries only for a pair still over the ceiling once settled

Finding 5. Once the backlog is gone and D28's soak has run, the
sweep's pairs are read over the soak week
(`sum by (operation) (rate(database_requests_total{caller_daemon="cluster",
operation=~"HardDeleteObjects|Delete.*|GetObjectsByState|CountObjectsByState"}[7d]))`).
A pair under the unbudgeted ceiling needs no entry, and the expected
answer is that none is over it. A pair that is over it gets an entry
derived with `tools/derive-database-load-budget.py` over the soak
window (which also regenerates
`examples/prometheus-database-load-rules.yaml`; a test asserts the
two agree), marked `activity_coupled` with a note saying the rate
follows arrivals, in its own pull request after this phase's.
Whichever way it goes is recorded in the master plan's phase 4
section, so D23's deferred question has a written answer.

### D28 -- #4473 closes after seven clean nights, recorded as a soak

The master plan's definition of done for this phase is a week with
the per-type gauge near the arrival rate times the delay. In D26's
terms: seven consecutive nightly facts files with `behind` and
`growing` both empty and `total_backlog` under 50,000 (about a day
of arrivals; the steady state is about one hour's worth, 2,000 or
so, plus whatever is inside the 30 second operation delay). The soak
is recorded with `plan-blockers soak <33fl cap PR> --for 10d
--after-deploy sfcbr --plan deleted-object-sweep`, ten days being
three to drain and seven to prove it, and phase 5 (the push audit) is
not planned until it has passed. #4473 is closed with the before and
after numbers, and the master plan's "Bugs fixed during this work"
records it. The close-out of this phase, including these numbers,
is phase 5's planning commit, as every phase's has been.

### D29 -- The #4399 reading is taken at the end of the soak, whatever it says

sf-cleaner's `GetObjectState` rate over the soak week against the
week before the drain. If it fell with the backlog, the backlog was
the remaining cause and #4399 can close; if it did not, the issue
gets that reading and stays open with one fewer candidate. Either is
a result. The reading goes on the issue and in the execution notes.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | low | management session | none | After 2026-10-29T09:10Z, re-run brief 3g's queries over the 24 hour window ending then and replace the 16 hour numbers in the master plan's "What phase 3 measured". See brief 4a. |
| 4b | medium | sonnet | worktree (33fl) | Add the `shakenfist_sweep_backlog` fact to 33fl's infra report pre-compute, with tests, note, prompt subsection, docs bullet and flag-change entries (D26). See brief 4b. |
| 4c | low | haiku | worktree (33fl) | Raise sfcbr's `DELETED_OBJECT_SWEEP_MAX_OBJECTS` to 400 through `extra_config` in `group_vars/allsf.yml` (D24). See brief 4c. |
| 4d | medium | management session | none | After the nightly deploy ships 4c: confirm the running cap, watch 24 hours, apply D25, raise to 800 or hold, repeat. See brief 4d. |
| 4e | medium | sonnet | none | Document draining a backlog, the watch queries and the days-behind check in the operator guide, and add the release note. See brief 4e. |
| 4f | medium | management session | none | Once the backlog is gone: answer D27 from the settled rates, and take D29's reading at the end of the soak. See brief 4f. |
| 4g | low | management session | none | After seven clean nights: close #4473 with the numbers; record the result for phase 5's planning commit. See brief 4g. |

Commit subjects, one per step that changes a repository:

- 4a: `Record phase 3's first 24 hours on sfcbr.` (shakenfist, in this
  phase's pull request)
- 4b: `Report the deleted-object sweep's backlog nightly.` (33fl)
- 4c: `Drain sfcbr's deleted-object backlog at 400 a pass.` (33fl);
  the raise to 800 is a second commit, `Raise the deleted-object drain
  to 800 a pass.`, and any retreat its own commit saying why
- 4e: `Document draining a deleted-object backlog.` (shakenfist, in
  this phase's pull request)

4b and 4c are independent pull requests against 33fl and can be
opened together; 4c lands first if only one does, because the drain
is watched by hand (4d) whether or not the fact exists yet. This
phase's shakenfist pull request carries the plan, 4a and 4e, and can
merge as soon as both are done; 4d, 4f and 4g happen on the live
cluster afterwards and their outcomes are recorded by phase 5's
planning commit, as phases 2 and 3 were.

### Brief 4a: the full first 24 hours (management session)

Run after 2026-10-29T09:10Z. The queries are brief 3g's, evaluated at
`time=2026-10-29T09:10:00Z` with `[24h]` windows, against
`http://maui.home.stillhq.com:9099/api/v1/query`, plus the backlog
gauge at `2026-10-28T08:00:00Z` and `2026-10-29T09:00:00Z` (the gauge
is absent in the hour after the 09:09Z leadership change, so the
"before" sample is the 08:00Z one). Cross-check the hard-delete count
against Loki: the sum of the `total` field of `Processed N items from
per-deleted-object queue` lines in `{job="shakenfist"}` (tenant
`sfcbr`) between 2026-10-28T09:09:59Z and 2026-10-29T09:10Z must be
within 10% of it. Replace the numbers in the master plan's "What phase
3 measured" and change its window sentence; keep the structure. Do
not change the 16 hour reading in this plan's survey, which is a
record of what planning saw.

### Brief 4b: the nightly backlog fact (33fl)

Worktree: `git worktree add -b sweep-backlog-ratchet ../33fl-wt-sweep-backlog master`
from `/srv/kasm_profiles/mikal/vscode/src/mach33labs/33fl`. 33fl's
branches are named for their topic (`sfcbr-error-mining-2026-10-28`,
`irc-summary-2026-10-28`); this one has no date because it is not a
report.

Files, all in 33fl:

1. `tools/infra-report-precompute.py`. Add `compute_sweep_backlog(time_s)`
   directly after `compute_sf_db_load` (`:7012-7036`), following its
   shape: `prom_query(expr, time_s)` for each of D26's four
   expressions, a pure `summarize_sweep_backlog(now_rows, prior_rows,
   deleted_rows, failed_rows)` that does the arithmetic (so the tests
   call it with literal rows, as `summarize_sf_db_load` is tested),
   and a `note` string in the returned dict that defines every field
   and both flags with their thresholds, because
   `docs/infra-report-facts.md` says the note is where field
   semantics live. Constants beside the function:
   `SWEEP_BACKLOG_BEHIND_DAYS = 1.0`, `SWEEP_BACKLOG_FLOOR = 1000`,
   `SWEEP_BACKLOG_GROWING_FRACTION = 0.1`,
   `SWEEP_BACKLOG_DRAIN_THRESHOLD = 50000`,
   `SWEEP_BACKLOG_MIN_ARRIVALS = 100`. If the first query returns no
   rows, return `{'unavailable': 'cluster_deleted_object_backlog
   absent (leadership changed within the hour?)', 'note': ...}` and
   nothing else; the report treats `unavailable` as "not checked",
   never as clean. A type present now but absent 24 hours ago gets
   `backlog_prior: null`, `arrivals_24h: null`, `days_behind: null`
   and neither flag. Register it in the sections dict next to
   `'shakenfist_db_load'` (`:10926`) as
   `'shakenfist_sweep_backlog': section('shakenfist_sweep_backlog',
   lambda: compute_sweep_backlog(time_s))`. Add
   `('shakenfist_sweep_backlog.behind', None)` and
   `('shakenfist_sweep_backlog.growing', None)` to `FLAG_CHANGE_SPECS`
   (`:10195`), matching the entries around them; read the comment
   above the tuple for what the second element means before choosing
   it. Output per type is a list sorted by `backlog` descending under
   `by_type`, with `behind` and `growing` as lists of type names, so
   the flag-change comparison has a path to compare.
2. `tools/test_infra_report_precompute.py`. A `SweepBacklogTest`
   class modelled on `DiskLatencyTest` (`:36-60`), patching
   `prom_query` with a side effect keyed on the expression. Cases:
   (a) a healthy cluster, nothing flagged, `days_to_drain` null;
   (b) one type behind (backlog 20,000, arrivals 5,000) and one
   growing (prior 100,000, now 112,000), both flagged, and a type with
   backlog 390 and arrivals 300 not flagged despite `days_behind` 1.3
   (the floor, not the arrivals minimum, is what spares it; a separate
   case covers arrivals under 100 giving a null ratio);
   (c) a draining cluster: total 1,500,000, net -400,000, so
   `days_to_drain` is 3.75; (d) the gauge absent: the section is
   `unavailable` and has no `by_type`; (e) the prior sample missing
   for one type: that type has null `arrivals_24h` and no flag;
   (f) two leaders publishing the same type: the `now` rows carry
   two instances and the result is the larger, not the sum -- this
   test must call `summarize_sweep_backlog` with rows whose `metric`
   carries an `instance` label, and the compute function's expression
   must use `max by (object_type)`; assert on the expression string
   the fake `prom_query` receives as well as on the result. Run the
   file the way its header's `importlib.util` loader expects
   (`python3 tools/test_infra_report_precompute.py`, or `python3 -m
   unittest tools/test_infra_report_precompute.py`; whichever the
   existing classes run under) and keep every existing test passing.
3. `claude_infra_reports/prompt.txt`. A subsection `### Deleted-object
   sweep backlog (\`shakenfist_sweep_backlog\`)` after the sf-database
   load ratchet subsection (`:942-968`), in that subsection's voice:
   report from the facts, do not recompute; `unavailable` is "not
   checked tonight", say so in one line; `behind` and `growing` each
   get a finding naming the type, its `backlog`, `arrivals_24h` and
   `days_behind`, and the finding cites shakenfist#4473 while that is
   open; while `days_to_drain` is non-null say "draining, about N
   days left" in one line and raise nothing; an empty `behind` and
   `growing` with a null `days_to_drain` needs no mention.
4. `docs/infra-report-facts.md`. One bullet after the sf-database
   load ratchet bullet (`:196-209`): what the fact is, the four
   source expressions in words, the two flags and their thresholds,
   the leader-only caveat, and that it is phase 4 of the
   deleted-object sweep plan in the shakenfist repo.

Constraints. The precompute runs unattended every night, so the
function must never raise on an empty or partial query result: every
branch of (c) to (f) is a shape a real night can produce. Do not touch
`sf-database-load-baseline.json` or anything under
`claude_infra_reports/` other than `prompt.txt`. Run 33fl's
`pre-commit run --all-files` before proposing the commit.

### Brief 4c: raise the cap on sfcbr (33fl)

Worktree: `git worktree add -b sfcbr-sweep-drain ../33fl-wt-sweep-drain master`.
In `group_vars/allsf.yml:47`, `extra_config` is a JSON document in a
single-quoted string: `'[{"name": "RAM_OVERCOMMIT_RATIO", "value":
"1.0"}]'`. Add `{"name": "DELETED_OBJECT_SWEEP_MAX_OBJECTS", "value":
"400"}` as a second element, keeping the string single-quoted and the
JSON valid (check with `python3 -c 'import json,yaml; ...'` over the
file, or `yq`). Add a comment above the line, in the file's style,
saying it is the deleted-object drain of shakenfist#4473 at phase 4
of `docs/plans/PLAN-deleted-object-sweep.md` in the shakenfist repo,
that the shipped default is 100, and that the value goes back to the
default (or the line goes) once the backlog is drained and phase 4's
soak has passed. `sfcbr.yml:284-300` reads the pair back after every
deploy and compares as lower-cased strings, so `"400"` is right and
`400` would also be. Nothing else changes. The raise to 800, and any
retreat, is a one-line edit of the same value in its own commit with
the D25 numbers in the message.

### Brief 4d: watch the drain (management session)

After the nightly `sfcbr.yml` run that follows 4c's merge (the
`{job="ansible-deploy", playbook="sfcbr.yml"} |= "deploy completed"`
marker in Loki):

1. Confirm the cap took effect: the pass log line's `total` reaches
   400 (`loki-query '{job="shakenfist"} |= "per-deleted-object queue"'
   --tenant sfcbr --since 1h`). If it is still at or under 100, the
   deploy restarted nothing; restart sf-cluster on every sfcbr node
   (`ansible -i <33fl inventory> allsf -b -m service -a "name=sf-cluster
   state=restarted"`, one node at a time) and confirm again. Record
   the time the cap took effect; it is the start of the 24 hours.
2. After 24 hours, evaluate D25's six tests with the queries it
   names, over `[24h]` ending now, against the 4a baseline. Also
   record: `sum(cluster_deleted_object_backlog)` now and the per-type
   top five, the hard-delete count, and `sf-db-load sweep`'s row for
   the day.
3. Decide per D25: raise to 800 (a 33fl commit on 4c's branch or a
   new one), hold, or drop. Write the decision and the six numbers in
   this plan's execution notes (they are recorded by phase 5's
   planning commit if this phase's pull request has merged).
4. Repeat at 800 until `sum(cluster_deleted_object_backlog)` is under
   50,000, then record the time and start the soak (D28).

### Brief 4e: operator documentation (shakenfist)

In `docs/operator_guide/database.md`:

1. Under "The deleted-object sweep" (`:970-1004`), after the three
   option bullets and the paragraph on batching, a subsection
   `#### Draining a backlog`. It says how to tell there is one (the
   backlog gauge against the hard-delete rate, with the two queries),
   how to size the cap (capacity is the cap times passes a day, about
   1,400 at one pass a minute; net drain is that minus arrivals, where
   arrivals are the hard deletes plus the change in the backlog over a
   day; so days to drain is backlog over net), that the time budget is
   the safety limit and not the knob, what to watch while it runs
   (D25's six signals, as the generic PromQL without sfcbr's numbers),
   and that the cap goes back once the backlog is gone because a cap
   above arrivals changes nothing at steady state. Apply the new value
   with `sf-ctl set-config DELETED_OBJECT_SWEEP_MAX_OBJECTS N` and a
   restart of sf-cluster, since cluster configuration is read at
   start. Worked numbers from sfcbr are fine as an example, labelled
   as one.
2. Under "Watching for sweeps that silently do nothing", after the
   backlog trend query (`:1143`), the days-behind check as PromQL:
   backlog over arrivals, with arrivals built from the hard-delete
   increase plus the backlog delta, and the sentence that a type over
   1.0 is behind the mission's bound of a day. This is D26's
   arithmetic for an operator without the 33fl report.
3. `docs/release_notes/v07-v08.md`, after phase 3's bullet
   (`:871-887`): one bullet saying the operator guide now describes
   draining a backlog and checking whether the sweep is keeping up.

Constraints: no mention of phase numbers or decision numbers outside
`docs/plans`; wrap as the surrounding text does; the queries must
parse (paste each into
`http://maui.home.stillhq.com:9099/api/v1/query` once). Run
`pre-commit run --all-files`.

### Brief 4f: settle the budget question and read #4399 (management session)

When the backlog is under 50,000 and the soak week has run:

1. D27's query over `[7d]`. For each pair, compare with the ceiling
   (0.30/s on six nodes). Record the table in the execution notes and
   the one-sentence answer in the master plan's phase 4 section. If a
   pair is over, open the follow-on pull request D27 describes.
2. D29: `sum(rate(database_requests_total{caller_daemon="cleaner",
   operation="GetObjectState"}[7d]))` now, and the same with `offset`
   to the week before 4c took effect. Comment on #4399 with both and
   what they mean; close it only if the rate fell with the backlog.

### Brief 4g: close #4473 (management session)

Seven consecutive nightly facts files under
`claude_infra_reports/facts/` in 33fl with `shakenfist_sweep_backlog`
present, `behind` and `growing` empty and `total_backlog` under
50,000. Then close #4473 with: the backlog at the start of the plan
(1.32M on 2026-10-06, 1.74M at phase 2's deploy), the rate each phase
reached, the drain's dates and cap, and the steady-state backlog.
Clear the soak with `plan-blockers` and note that phase 5 can be
planned. If a night is not clean, say why in the execution notes and
the count starts again.

## Risks and mitigations

- **The drain is a write load labqnap has not carried.** Phase 1 tied
  the per-commit cost to `innodb_flush_log_at_trx_commit=1` on
  labqnap's storage, and the sf-database watchdog SIGABRTs of earlier
  this year were labqnap's buffer pool. Mitigation: D24 steps rather
  than jumps, D25 is written before the first step and names the
  queries, the time budget bounds a pass whatever the cap says, and
  the way back is one line in 33fl plus a restart. The management
  session checks, at 24 hours per step.
- **The cap change does not take effect.** `cluster_config` is read at
  start, and the nightly deploy restarts sf-cluster only on change.
  Mitigation: brief 4d's first step confirms from the pass log before
  the clock starts.
- **The regression guard reads absence as zero.** A leadership change
  drops the gauge for up to an hour. Mitigation: D26's `unavailable`
  branch and test (d); the prompt says `unavailable` means not
  checked.
- **The guard double-counts during a leadership handover.**
  Mitigation: `max by`, and test (f) asserts on the expression.
- **33fl's nightly report edits the same files.** `prompt.txt` and
  the pre-compute are touched by the report workflow and by hand.
  Mitigation: 4b is small and lands the day it is opened; a conflict
  is a rebase.
- **The soak is interrupted by a deploy that resets the sweep.** A
  restart loses the in-memory work lists but not the backlog or the
  counters, and the gauge returns within an hour. Mitigation: D28
  counts nights, not hours, and an `unavailable` night is not a dirty
  night; it is a night that does not count.
- **#4399 reads as unchanged.** That is a result (D29), not a failure
  of this phase.
- **The fact flags sfcbr's small types forever.** A type with a few
  hundred objects inside their grace period and few arrivals would
  have a high `days_behind`. Mitigation: the 1,000 floor and the
  100-a-day arrivals minimum, both chosen from the per-type numbers
  in finding 3; test (b)'s third type pins the floor.

## Definition of done

1. `pre-commit run --all-files` passes in the shakenfist worktree, and
   33fl's `pre-commit run --all-files` and
   `tools/test_infra_report_precompute.py` pass in each 33fl worktree.
2. The master plan's "What phase 3 measured" carries a 24 hour window
   ending 2026-10-29T09:10Z, and its hard-delete count is within 10%
   of the sum of the Loki pass lines' `total` over the same window
   (brief 4a records both numbers).
3. 4b's tests fail for each of three mutations and pass unmutated:
   `max by` changed to `sum by` in the expression (test f), the
   absent-gauge branch returning an empty `by_type` instead of
   `unavailable` (test d), and `SWEEP_BACKLOG_FLOOR` set to 0 (test
   b's third type). The first nightly facts file after 4b merges has
   a `shakenfist_sweep_backlog` key with `by_type` entries for every
   type that publishes the gauge, and the next morning's report
   mentions the drain in one line.
4. Within 24 hours of 4c's merge, the sfcbr pass log's `total` field
   reaches 400 (brief 4d step 1 records the time).
5. The operator guide's new subsection and check exist, each PromQL
   in them evaluates without error against the 33fl Prometheus, and
   `git diff <merge-base> -- docs | grep '^+' | grep -n "phase [0-9]\|D[0-9][0-9]"`
   finds no line outside `docs/plans`.
6. After the drain: `sum(cluster_deleted_object_backlog)` on sfcbr
   under 50,000, then seven consecutive nightly facts files with
   `behind` and `growing` empty, then #4473 closed with the numbers
   (brief 4g). Recorded by phase 5's planning commit.
7. D27's table of the sweep's pairs against the ceiling, and D29's
   two numbers on #4399, both recorded (brief 4f).

Items 1 to 5 gate this phase's pull request. Items 6 and 7 happen on
the live cluster afterwards and gate phase 5.

## Execution notes

- **Back brief, 2026-10-29 (UTC).** Michael kept D24 (400 then 800),
  D26 (a report fact, not an alert) and D28 (seven clean nights under
  50,000), and allowed this session to open the two 33fl pull requests
  and to push and open this phase's shakenfist pull request once the
  definition of done passes.
- **4b, 4c and 4e ran in parallel**, each in its own worktree, because
  none writes to another's tree. 4a waits for 2026-10-29T09:10Z.
- **4c, 2026-10-29 (UTC).** Mach33Labs/33fl#1094. D28's soak was first
  recorded with `--after-deploy sfcbr`, but an sfcbr deploy reports
  only `shakenfist/shakenfist` and `shakenfist/client-python` as
  shipped (33fl `sfcbr.yml`, `plan_blockers_shipped`), so a soak keyed
  on a 33fl pull request would never start. It is recorded as ten
  days after merge instead; the clock the plan cares about is the one
  brief 4d starts from the pass log.
- **4b, 2026-10-29 (UTC).** Mach33Labs/33fl#1095, also recorded as a
  blocker because D28 reads the fact it adds. Brief 4b's test case (b)
  as first written asked for a type with backlog 40 and arrivals 30 to
  show the floor, but arrivals under 100 give a null ratio before the
  floor is reached; the test uses 390 and 300, and the brief is
  corrected above. The management session corrected the fact's
  description of what the gauge counts (objects in a final state,
  including those inside their grace period, not soft-deleted rows).
  794 tests pass; the three mutants in the definition of done are each
  killed (`scratchpad/p04/4b/mutate.sh` in this session's scratchpad).
- **4e, 2026-10-29 (UTC).** The watch queries were written with one
  hour windows and changed to one day by the management session, to
  match D25 and the text's own "hold each step for a day". The sixth
  signal is worded generically, since the CI flake alert is 33fl's.
  Every query was evaluated once against the 33fl Prometheus.
- **4a, 2026-10-29T09:30Z.** Brief 3g re-run over the window ending
  09:10Z and recorded in the master plan. Definition of done item 2:
  Prometheus 130,663 hard deletes, Loki 131,026 over 1,325 passes,
  within 0.3%. Arrivals read lower than the 16 hour estimate (37,000
  a day against 43,000), and the drain time correspondingly longer at
  the old cap (17 days); D24's arithmetic at 400 is unchanged in kind.
- **4d step 1, 2026-10-29T09:35Z.** Mach33Labs/33fl#1094 merged at
  08:45Z. sf-cluster restarted at 09:03Z (a new leader pid from
  09:03:32Z) but every pass since still takes exactly 100 entries, so
  the cap did not reach it. `rundaily.sh` is operator-run from a local
  33fl checkout, so the likeliest cause is a checkout that predated
  the merge; `cluster_config` on sfcbr could not be read from this
  session (the production read was declined), so the clock for the
  first D25 reading has not started. Next: confirm with `sf-ctl
  show-config` on a database node and either wait for the next
  nightly run or apply by hand with `sf-ctl set-config` and a restart
  of sf-cluster.

## Back brief

Before starting, confirm with Michael:

- **D24's steps of 400 then 800**, and D25's six tests. The numbers
  are the judgement in this phase; if he would rather a single step
  or a different ceiling on commits, that is a one-line change to
  D24 and the 4c brief before anything runs.
- **D26's choice of a report fact over an alert**, since it is the
  guard the mission asks for and lives in 33fl rather than here.
- **D28's 50,000 threshold and seven nights**, which decide when
  #4473 closes.
- That 4b and 4c are opened as 33fl pull requests from this session,
  and that 4a waits for 09:10Z.
