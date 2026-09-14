# Phase 3: publish metrics when the running-domain set changes

Parent plan:
[PLAN-transient-capacity-refusals.md](PLAN-transient-capacity-refusals.md).

**Planning effort:** medium, as the master plan specifies -- "the
pattern is the existing loop", and that is true of the mechanism. What
takes the planning above mechanical is not the loop but the bill: the
publish this phase makes conditional is not one database write, it is
a fifteen-round-trip sweep, and the master plan's section costed it
as
though the cheap half (the libvirt poll) were the whole of it. The
decisions below are mostly about that.

This phase continues the plan's decision sequence at **D18**; phases 1
and 2 used D1-D17.

## Context

The master plan's open question 4 asks why a node keeps refusing for
about a minute after its instances are deleted, and answers it: the
CPU pre-filter charges `max(measured, committed)`, and while the
committed side is released inside the delete transaction, the measured
side is a 60 s poll of libvirt.

The survey confirms that answer at the line. `_has_sufficient_cpu()`
(`shakenfist/scheduler.py:321`) computes

```python
current_cpu = max(measured_cpus, committed_cpus)
```

where `measured_cpus` is `cpu_total_instance_vcpus` out of the node's
`node_metrics` row. The docstring above it is explicit that the
`max()` is deliberate and load-bearing in the *other* direction -- it
is what stops a node whose ledger is full but whose instances are
still fetching images from winning the load ordering and then being
refused by the guard. So the rule is right and this phase does not
touch it. Only the cadence of the measurement is wrong.

The master plan's own evidence is run 34119030297: `measured 6 /
committed 0 / limit 3`, thirty-two seconds after six instances were
deleted and their ledger correctly released. The node was empty and
refused anyway, for as long as it took the next 60 s tick to arrive.

## Scope

In scope:

- A cheap poll of the active-domain set in `sf-resources`, and an
  immediate metrics publish when that set changes.
- Whatever is required in `shakenfist/util/libvirt.py` to make that
  poll one libvirt call rather than N+1.
- Restructuring `_run_inner()`'s cadence decision enough that it can
  be unit tested without driving the daemon loop.
- Declaring the changed load in `shakenfist/data/database_load_budget.yaml`,
  including the two `(operation, caller_daemon)` pairs the survey
  found are absent from it entirely.
- One functional test that the published measurement follows a delete
  within seconds rather than within a minute.
- Documentation of the new cadence where the old one is currently
  stated as a flat 60 s.

Out of scope, explicitly:

- **The `max(measured, committed)` rule itself.** It is correct. This
  phase makes one of its two inputs fresher and changes nothing about
  how they are combined.
- **Releasing the ledger earlier in `Instance.delete()`.** The master
  plan already considered and rejected it, for the reason that the
  measured side is what binds after a teardown; nothing in the survey
  disturbs that.
- **A partial or capacity-only metrics upsert.** See D20 -- it is the
  rejected alternative there, and the reason is recorded so that a
  later measurement can reopen it on evidence.
- **Re-deriving the load budget from a post-change measurement
  window.** That needs the change running on `sfcbr` for days. D23
  records it as follow-up rather than pretending this phase can do it.
- **Anything in `client-python` or `shakenfist/actions`.** Unlike
  phase 2 this phase is single-repository.

## What the survey found

The master plan's phase 3 section is short and mostly right. Four of
its claims needed correcting, and all four have been corrected at
source in the planning commit -- in the master plan's phase 3 section,
its open question 4, and the `index.md` row -- so a later step should
not redo it.

### The premise holds, and is now located

`scheduler.py:321` `_has_sufficient_cpu()` charges
`max(measured_cpus, committed_cpus)` against `limit_cpus`, and
`measured_cpus` is `cpu_total_instance_vcpus` from the metrics row.
`summarize_resources()` (`scheduler.py:1083-1089`) publishes both
inputs separately as `cpu_measured` and `cpu_committed` over
`/admin/resources`, which is what makes the functional test in step 3c
possible without reading the database.

### The publish is not cheap, and the master plan costed only the poll

The section says the poll touches no database, which is true, and
leaves the impression that a publish is therefore nearly free. It is
not. A publish is `update_metrics()` -> `_get_stats()`
(`shakenfist/daemons/resources/main.py:215`), which makes, per call:

| Call | Site | RPC | Count |
|------|------|-----|-------|
| `Node.from_db(config.NODE_NAME)` | `main.py:221` | `GetNodeByFqdn` | 1 |
| `mariadb.get_node_metrics()` | `main.py:226` | `GetNodeMetrics` | 1 |
| `mariadb.get_work_queue_length()` | `main.py:429`, driven from `:447`, `:455`, `:485`, `:508` | `GetQueueLength` | 12, or 17 on the network node |
| `mariadb.upsert_node_metrics()` | `main.py:658` | `UpsertNodeMetrics` | 1 |

Fifteen round trips on an ordinary hypervisor, twenty on the elected
network node. Twelve of the fifteen are queue-depth reads, from
`shakenfist/operations/baseoperation.py:68-120`. They have nothing to
do with capacity and they are the overwhelming majority of the bill.
D20 decides what to do about that, and it is the decision in this plan
most likely to be argued with.

*Corrected after the D23 gate ran, which is the point of having had
one. The first draft of this section said seven queue reads and ten
round trips, having found two of the four loops that drive them. The
decisions below use the corrected figures.*

Three further calls are reachable from `_get_stats()` and are
deliberately **not** counted here: `GetNodeAttributes` once and
`UpdateNodeAttributes` up to four times, from the version and
process-metrics block at `main.py:527-574`. That block is gated by its
own 300 s wall clock rather than by the publish, so publishing more
often does not raise its rate -- it only changes which publish it
lands on. D23 leaves them alone.

### Three other pre-filter stages read the same stale row, and two have no ledger to fall back on

The master plan frames phase 3 as a CPU fix. The same metrics row
feeds:

- `_has_sufficient_ram()` `scheduler.py:399` -- `memory_available`,
  a raw measurement with **no** `max()` against the ledger.
- `_has_sufficient_ram()` `scheduler.py:412` -- `memory_total_instance_actual`
  for the KSM overcommit ratio, likewise unbacked.
- `_has_sufficient_disk()` `scheduler.py:481` -- `disk_free_instances`.
- `_has_idle_disk_bandwidth()` `scheduler.py:495` -- the disk-busy rate.

Because a publish rewrites the whole row, a domain-set-change trigger
refreshes all of these at once. This is a larger win than the section
claims and it settles a scope question before it is asked: the
*trigger* only needs to watch the domain set, even though the *effect*
is a full refresh. A memory-valued trigger would be wrong anyway --
`memory_total_instance_actual` moves continuously as guests balloon,
so it would fire on every poll forever.

### `UpsertNodeMetrics` is not in the load budget at all

The section says to "declare the new publish rate in
`database_load_budget.yaml` as `activity_coupled`". There is no entry
to amend. `grep -n 'caller_daemon: resources'` yields exactly five
pairs -- `GetNodeDaemonState`, `GetQueueLength`, `GetInstanceAttributes`,
`GetNodeByFqdn`, `GetObjectState` -- and neither `UpsertNodeMetrics`
nor `GetNodeMetrics` is among them, presumably because neither cleared
the derivation's significance threshold over the fit window. So D23 is
about adding entries, not editing a rate.

Two further facts the section does not carry, both of which change
what "declare it as `activity_coupled`" means:

- `activity_coupled` **disables enforcement**. `enforced()` in
  `shakenfist/deploy/shakenfist_ci/load_budget.py:443-450` returns
  false for any entry carrying it, and
  `test_database_load_budget.py:106` holds that behaviour up. Marking
  the resources pairs is therefore not a modelling refinement, it is
  surrendering CI's ability to catch a regression in them.
- The file is generated (`tools/derive-database-load-budget.py`) and
  its header says in terms: "DO NOT hand-edit levels to make a check
  pass." The mark itself does survive re-derivation --
  `derive-database-load-budget.py:476` carries `activity_coupled`
  forward from the previous file -- so a mark added here is not lost
  the next time the budget is derived.

### `listAllDomains` is not what the code calls

Open question 4 says "a `listAllDomains` every few seconds costs
nothing". `LibvirtConnection.get_all_domains()`
(`shakenfist/util/libvirt.py:192`) is `listDomainsID()` followed by a
`lookupByID()` and a `name()` per domain -- N+1 libvirt round trips,
not one. D22 decides what the poll uses instead. Three callers exist
(`daemons/cleaner/scheduled_tasks.py:222`,
`daemons/resources/main.py:401`, and `util/libvirt.py:183`), so
reworking the helper itself is not free either.

### Smaller corrections

- The section says this is "the only change in this plan that touches
  a daemon other than the cluster daemon". Phase 4 changes
  `shakenfist/external_api/instance.py`, which runs under `sf-api`.
  Reworded at source to say what was meant: it is the only change
  outside the scheduler, the API and the test suite.
- Master plan line 205 cites `resources/main.py:645, 704`.
  `update_metrics()` is at `:648` and the 60 s gate at `:704`; the
  first has drifted by three lines. Corrected.
- No CPU hotplug path exists (`setVcpus` appears nowhere under
  `shakenfist/`), which is what makes D18 sound.
- Nothing of this phase has been built already: no
  `domain_set`/`last_domains`/`force_publish` symbol exists anywhere
  in the tree.
- No test drives `_run_inner()`. The eleven test cases in
  `shakenfist/tests/test_daemon_resources.py` cover pure helpers and
  `_get_stats()`'s node-deletion race. D24 is about that gap.

## Decisions

### D18 -- The trigger is the active-domain *set*, not a count and not a vCPU total

The master plan says "the set of running domains or their vCPU total".
The disjunction is unnecessary in one direction and insufficient in
the other.

Unnecessary: there is no CPU hotplug in Shaken Fist, so a running
domain's vCPU count never changes. The total can only move when the
set does, and watching it as well buys nothing.

Insufficient: a count would miss a same-count change. During a CI
teardown-and-recreate a poll interval can easily contain one delete
and one create, leaving the count identical and the capacity picture
completely different -- and a create is exactly the case where a stale
*low* measurement lets the scheduler over-admit, which is the failure
the `max()` exists to prevent. Compare set membership.

### D19 -- Poll from the existing loop at 5 s, not from a new thread

`_run_inner()`'s `while` loop already ticks at 1 s via `self.idle(1)`
(`shakenfist/daemons/daemon.py:534`, which also pets the watchdog and
checks the abort path), and already contains two
`time.time() - last_x > interval` gates. A third is the pattern, not a
departure from it.

The alternative in this file is a thread, and `_run_health_checks` is
one for a stated reason: a resource probe can block for the whole
timeout when a hard NFS mount hangs, and the loop holds the nodelock.
A libvirt list against the local socket is not that. The poll runs
inline.

Five seconds, not "a few": it is the largest interval that still turns
"up to 60 s stale" into "at most 5 s plus a publish", it divides the
60 s tick evenly, and it bounds the worst case in D20 to a number that
can be written down.

### D20 -- A change publishes the ordinary full sweep; do not build a cheaper partial one

This is the decision to argue with, so here is the arithmetic in full.

Baseline is one `_get_stats()` per node per 60 s: fifteen database
round trips, so 0.25/s per node. With a 5 s poll and a domain set that
changes in every single interval -- the worst case, not the expected
one -- it becomes fifteen round trips per 5 s, or 3/s per node. On the
six-node reference cluster that is an added 17/s at full churn against
a whole-cluster target of under 100/s.

The cheaper design is available and the survey costed it: skip the
twelve `get_work_queue_length()` reads on a change-triggered publish
and carry the previous sweep's queue figures forward, refreshing them
only on the 60 s tick. That is a five-fold reduction and it is not
hard to write.

It is rejected anyway, for now, because it buys a factor of three by
introducing an invariant nobody asked for: that a `node_metrics` row
may carry capacity fields from two seconds ago beside queue fields
from fifty-eight seconds ago. `node_queue_waiting` is a typed column
the scheduler reads (`_has_reasonable_queue_state()`,
`scheduler.py:186`, and again as a skip condition in
`summarize_resources()` at `:1040`), so the mixed-age row is not
inert -- it is read by the same decision the fresh half is there to
improve. One publish path that always means "everything in this row
was true at `timestamp`" is worth five times the round trips until
somebody measures that it is not.

The five-fold figure is larger than the three-fold one this decision
was first written against, and it is fair to say that moves the
argument. It does not move it far enough to reverse: the objection was
never the size of the saving, it was that a row carrying two ages is
read by `_has_reasonable_queue_state()` and by `summarize_resources()`'s
own skip condition, and that stays true at any multiple.

What makes this reversible rather than merely opinionated: the
worst-case figure above is the thing to check. If `sf-ctl
database-load` on a busy cluster shows the resources pairs materially
over model after this ships, the queue-read split is the lever, it is
localised to `_get_stats()`, and this decision is the record of why it
was not pulled first.

### D21 -- Any publish resets the 60 s clock

`last_metrics` is set by every publish, whichever gate caused it. The
60 s tick then means "at most 60 s since this node last published",
not "every 60 s regardless", so a change-triggered publish two seconds
before the tick suppresses the tick rather than being followed by a
near-duplicate. This also makes the poll interval a floor on the
publish rate for free, which is what bounds D20's arithmetic.

### D22 -- The poll is one `listDomainsID()`, and `get_all_domains()` is left alone

Add `get_active_domain_ids()` to `LibvirtConnection` beside
`get_all_domains()`: a single `self.conn.listDomainsID()`, returned as
a set, with no per-domain lookup.

Two consequences, both accepted:

- It does not filter to the `sf:` prefix, because filtering requires
  the `lookupByID()`/`name()` pair that makes the call N+1. A non-SF
  domain appearing on a hypervisor would trigger a publish that was
  not needed. A publish is never *wrong*, only occasionally
  unnecessary, and SF hypervisors do not run foreign domains.
- Domain IDs are unique within a libvirtd run but reassigned across
  restarts, so a libvirtd restart produces one spurious publish. Also
  harmless, and a metrics publish after a libvirtd restart is arguably
  the correct behaviour anyway.

`get_all_domains()` itself is not reworked into `listAllDomains`. It
would be a genuine improvement and it has three callers; that is a
tidy-up with its own blast radius and it is not what this phase is
for. The master plan's wording has been corrected instead.

### D23 -- Declare only the pairs the change moves, and add the two that are missing

Do not blanket-mark the resources daemon `activity_coupled`. Of its
five existing budget pairs, `GetInstanceAttributes` and
`GetNodeDaemonState` are not on the `_get_stats()` path at all and
must keep their enforcement.

The gate this decision required has run, and the list below is its
result rather than a guess. Exactly four pairs are on the publish
path:

| Pair | Per publish | In the budget? |
|------|-------------|----------------|
| `GetNodeByFqdn` / `resources` | 1 | yes, to be marked |
| `GetQueueLength` / `resources` | 12, or 17 on the network node | yes, to be marked |
| `GetNodeMetrics` / `resources` | 1 | **no, to be added** |
| `UpsertNodeMetrics` / `resources` | 1 | **no, to be added** -- the operation has no entry for any daemon |

The gate was worth running. This decision's first draft named
`GetObjectState` / `resources` as a fourth existing entry to mark, and
it is not on the publish path at all: it comes from
`node_health.apply_result()` (`shakenfist/node_health.py:152`) by way
of `_run_health_checks()`, a separate thread on its own interval, and
nothing in `_get_stats()` reads `.state`. Marking it would have
switched enforcement off on a pair this phase does not touch. Its
existing note in the budget file says otherwise and is wrong;
correcting that note is a one-line accuracy fix in a file being edited
anyway, so 3d makes it.

`GetNodeDaemonState` / `resources` (the base `Daemon.check_daemon_state()`
poll) and `GetInstanceAttributes` / `resources`
(`identify_libvirt_processes()`, on the billing interval) are likewise
untouched, as is the 300 s-gated attributes block noted in the survey.

Every entry touched carries a note naming this phase and saying why
the rate is now coupled to instance churn. For the two new entries a
rate is not left blank: `BudgetEntry._has_a_term()`
(`shakenfist/schema/database_load_budget.py:88-96`) rejects an entry
with no rate term, on the grounds that "an entry which predicts
nothing cannot be over or under budget", and that is right. The rate
is derived rather than invented -- each runs exactly once per publish
and the floor on publishes is one per node per 60 s, so
`per_node_base_qps: 0.017` -- and the note says it is the
one-per-publish floor and not a fit.

### D25 -- The hand marking cites an issue, because the convention is right

`test_a_hand_marked_pair_says_why_in_its_note`
(`shakenfist/tests/test_derive_database_load_budget.py`) requires any
`activity_coupled` entry whose caller is not one of `api`, `unknown`
or `ctl` to cite an issue matching `#\d{3,}` in its note. The two
existing hand markings cite #4092 and #3999.

The test's own comment says why, and it is the same argument this plan
makes about enforcement: a hand marking "is the only thing standing
between a deliberate exemption and a pair which quietly stopped being
checked", and it is carried forward verbatim by every future
re-derivation. A marking with no issue behind it is an exemption
nobody owns.

So this phase files one rather than weakening the test, and the issue
is the same one the master plan's Future work entry describes: the
four pairs are exempt until the budget is re-derived over a window
that includes the new cadence. Filing it is the one action in this
phase that reaches outside the repository, so it is put to the user
rather than done unilaterally, and the four notes carry the number it
comes back with.

### D26 -- Fix the re-derivation bug this phase exposes

`emit()` in `tools/derive-database-load-budget.py` writes
`entry['measured']['mean_qps']` unconditionally, so a schema-legal
entry carrying no `measured:` block -- which is exactly what D23's two
new pairs are, having never been fitted -- raises `KeyError` the next
time the budget is derived. Eight `EmitRoundTripTestCase` cases catch
it.

This is a real defect in the tool rather than a reason to give the new
entries fabricated measurements, and definition-of-done item 10
already requires the round trip to work. It is in scope for 3d despite
being outside the yaml, because a budget file that cannot be
re-derived is not a budget file.

*Amended after review of the PR, which found this decision had stopped
one symptom short of the disease.* Not raising `KeyError` is not the
same as surviving, and the two new entries would not have survived.
`main()` builds `entries` only from the pairs which clear
`--minimum-mean-qps`, and `emit()` iterates `entries`, consulting the
previous file per key. A pair the new measurement does not see is
therefore never written at all -- and a pair with no measurement is
exactly a pair no measurement saw. The marks, the notes and #4197
would have gone with them, turning `UpsertNodeMetrics` back into
unbudgeted polling, which the coverage text says the CI check treats
as a new poll and fails on.

Three fixes rather than one, then:

1. `emit()` also writes carried pairs which have **no** `measured:`
   block. That absence is the signal, and it needs no new field to
   carry it: `to_entry()` always sets a measurement, so an entry
   without one can only have been written by hand for a call the fit
   could not see. A pair which *was* fitted and has now fallen below
   the cut is still dropped -- it has a measurement saying it used to
   matter and no longer does, and dropping it is what the cut is for.
2. No fallback to the previous file's `measured:` block. Every other
   carried value is judgement, which stays true across a
   re-measurement; a measurement is a fact about one window, and the
   emitted `_doc.window` names a different one. It would be the single
   carried value that is false.
3. `wrapped_block()` breaks on neither hyphens nor long words. A
   folded scalar turns the newline back into a space, so a wrap inside
   `one-per-publish` came back as `one-per- publish` -- text degrading
   a little on every re-derivation. Found because the new round-trip
   test compares notes exactly.

### D27 -- Regenerate the Prometheus rules in the same commit

`examples/prometheus-database-load-rules.yaml` is a rendering of the
budget, and `test_enforced_series_matches_the_budget` asserts the
committed file is byte for byte what
`tools/generate-database-load-rules.py` produces from the committed
budget. The whole point of generating it, as its own header says, is
that an operator's alerts, the CI check and `sf-ctl` cannot hold
different opinions about what normal load is.

So the marks in D23 have a second effect nobody would guess from the
yaml alone: `GetNodeByFqdn`/`resources` and `GetQueueLength`/`resources`
leave the alerting flag, and the two new pairs join the coefficient
series at 0.017 without ever entering that flag. Regenerate rather
than hand-edit, and do it in the same commit as the budget change --
the test exists precisely to catch the two drifting apart.

### D24 -- Extract the cadence decision so it can be tested without the loop

The master plan asks for "a unit test that a domain disappearing
between polls produces a publish before the 60 s tick". Nothing
currently tests `_run_inner()`, and driving that loop in a test means
standing up the nodelock, the health-check thread and the abort path
to observe one boolean.

So the decision is a function: given the last publish time, the last
poll time, the last observed domain-ID set, the current set and the
current time, return whether to publish and whether to poll. Pure,
no libvirt, no database, no clock of its own. `_run_inner()` calls it
and acts on the answer; the tests call it directly. This is the same
shape as `_compute_reservations()` in the same file, which is pure for
the same reason and is tested by seven cases at
`test_daemon_resources.py:87`.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | The libvirt helper. Add `get_active_domain_ids()` to `LibvirtConnection` in `shakenfist/util/libvirt.py` beside `get_all_domains()` (`:192`): return `set(self.conn.listDomainsID())`, typed `set[int]`, with a docstring saying why it does not filter on the `sf:` prefix and why a libvirtd restart may produce one spurious publish (D22). Do **not** change `get_all_domains()` -- it has three callers (`daemons/cleaner/scheduled_tasks.py:222`, `daemons/resources/main.py:401`, `util/libvirt.py:183`) and reworking it is out of scope. Unit test it against a fake `conn` in `shakenfist/tests/`, including the empty case. Single quotes, 120 columns, no trailing whitespace. Commit subject: `libvirt: cheap active domain id listing.` |
| 3b | high | opus | none | The cadence. In `shakenfist/daemons/resources/main.py`, add a module-level pure function implementing D24 -- inputs last publish time, last poll time, last observed domain-ID set, current set (or `None` when the poll has not run this tick), and now; outputs whether to poll and whether to publish. Model it on `_compute_reservations()` (`:102`) for shape and on its tests (`test_daemon_resources.py:87`) for how to test it. Rules: poll when 5 s have elapsed since the last poll (D19); publish when the polled set differs from the last observed set (D18), or when 60 s have elapsed since the last publish; a publish of either kind updates `last_metrics` (D21). Then wire it into `_run_inner()`'s `while` loop (`:698-708`), replacing the bare `if time.time() - last_metrics > 60:` gate, keeping `emit_billing_statistics()`/`identify_libvirt_processes()` on their own untouched gate, and keeping the whole thing inside the existing `try`/`except Exception` so a libvirt failure is swallowed by `util_exceptions.ignore_exception()` exactly as a metrics failure is today. The poll opens its own `util_libvirt.LibvirtConnection()`; it must never hold one across iterations. On a poll that raises, leave the last observed set unchanged and let the 60 s gate carry -- do not treat an error as "the set became empty", which would publish a fabricated zero. Unit tests for the pure function: a first call with no history publishes; an unchanged set inside 60 s does not; a changed set inside 60 s does; a same-size but different set does (the D18 case -- assert it, and confirm that comparing lengths instead of membership makes this test fail); 60 s elapsed with an unchanged set does; a publish resets the 60 s clock. Plus one test driving the wiring with `_get_stats` and the libvirt connection mocked, asserting a vanished domain produces an `upsert_node_metrics` before the tick. Commit subject: `resources: publish when the domain set changes.` |
| 3c | medium | sonnet | none | Functional coverage, which `CLAUDE.md` prefers to unit coverage where only one is possible -- here we can have both. In `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/`, add a test that creates an instance, reads `/admin/resources` via `self.system_client.get_cluster_resources()` and records `per_node[node]['cpu_measured']`, deletes the instance, and asserts `cpu_measured` for that node drops by the instance's vCPUs within 20 s. Follow `test_nodes.py:33-60`, which is the existing precedent for asserting against this endpoint and already documents that `node_metrics` rows are not exposed over REST. Use `self.create_instance()`, never a raw client call -- the phase 2 AST guard (`shakenfist/tests/test_ci_raw_creates.py`) will fail the build otherwise. 20 s, not 5: the assertion is "seconds, not a minute", and a tighter bound would flake on a loaded CI node for no extra signal. Pin the create so the node under assertion is known, and note in a comment that the pin is the assertion's subject rather than a capacity workaround. Commit subject: `ci: assert measurement follows a delete.` |
| 3d | medium | sonnet | none | The load budget, per D23, whose gate has already run -- that decision's table is derived from the code and is the list to use. In `shakenfist/data/database_load_budget.yaml`: mark `GetNodeByFqdn`/`resources` and `GetQueueLength`/`resources` `activity_coupled: true`, and add `GetNodeMetrics`/`resources` and `UpsertNodeMetrics`/`resources`, also `activity_coupled: true`, each with `per_node_base_qps: 0.017` -- one call per publish against a floor of one publish per node per 60 s. `BudgetEntry._has_a_term()` (`shakenfist/schema/database_load_budget.py:88-96`) rejects an entry with no rate term, so the field cannot be omitted; the note must say the figure is the one-per-publish floor rather than a fit. Every note names this phase, says the rate is now coupled to instance churn, and cites the issue from D25. Do **not** mark `GetObjectState`, `GetInstanceAttributes` or `GetNodeDaemonState` for `resources`: none is on the publish path. Correct `GetObjectState`/`resources`'s existing note, which claims "State reads while the resources daemon assembles node metrics" -- the RPC comes from `node_health.apply_result()` (`shakenfist/node_health.py:152`) by way of `_run_health_checks()`, a separate thread on its own interval. Then fix D26: `emit()` in `tools/derive-database-load-budget.py` writes `entry['measured'][...]` unconditionally and raises `KeyError` on an entry with no `measured:` block, which is what the two new pairs are; emit the block only when present, the way `note` and `provisional` are handled just above it. Read the yaml header before editing: it forbids hand-editing **levels** to make a check pass, and marks and notes are not levels. Match the neighbouring entries' YAML shape, including the folded `note: >-` block. Run `shakenfist/tests/test_database_load_budget.py` and the whole of `test_derive_database_load_budget.py`. Commit subject: `budget: metrics publish is activity coupled.` |
| 3e | low | haiku | none | Documentation. Correct every place that states the metrics cadence as a flat 60 s and is still read as current: `docs/operator_guide/` wherever it describes what the resources daemon publishes and how fresh a capacity reading is, and the scheduler's own operator documentation if it explains why a node can refuse work it has room for. Say the new rule in one sentence -- the row is republished within about five seconds of the active-domain set changing, and otherwise at least once a minute. Do **not** edit the sibling plan files that state 60 s (`PLAN-scheduler-reservations.md:52`, `:855`, `PLAN-scheduler-reservations-phase-04a-demand-guard.md:167`, `PLAN-ci-cloud-sizing-phase-03-saturation-coverage.md:261`): a plan records what was true when it was written, and rewriting history in them is worse than the staleness. `AGENTS.md` does not change -- no convention moves. `ARCHITECTURE.md` does not change -- no component boundary moves. Commit subject: `docs: metrics follow the domain set.` |
| 3f | low | haiku | none | Closeout. Set the phase 3 row to `Complete` in the master plan's Execution table and in `docs/plans/index.md`, update the index arithmetic to `3 of 7`, then run `python3 tools/check-plan-status.py`. Only after 3a-3e are reviewed and merged, and a real CI run has been read for the functional assertion in 3c. Record the reading in an Outcome section, as phases 1 and 2 did. Add the budget re-derivation deferred by D23 to the master plan's Future work if it is not already there. |

The survey corrections described at the end of *What the survey found*
were made in the planning commit and are not a step here.

## Risks and mitigations

**A publish storm during CI teardown.** Six instances deleted in
quick succession on one node produce up to one publish per 5 s poll
while the set keeps moving, each costing fifteen database round
trips.
Bounded by D19 and D21 to 12 publishes per node per minute worst case
-- 3/s per node, 17/s on the six-node reference cluster. *Mitigation:*
the worst case is written down here so it can be checked rather than
argued about. Step 3f's Outcome reads `sf-ctl database-load` for the
five pairs in D23 against a run that includes a teardown, and if the
resources pairs are materially over model the queue-read split named
in D20 is the pre-agreed remedy. Checked by whoever writes the
Outcome, not deferred to a future reader.

**A hung libvirtd is now hit twelve times as often.** `libvirt.open()`
against a wedged libvirtd can block, and a block inside `_run_inner()`'s
`try` never reaches `self.idle(1)`, so the systemd watchdog fires and
the daemon is restarted. This is today's behaviour at 60 s intervals;
the phase raises the exposure to 5 s. *Mitigation:* accepted, not
removed -- the failure mode is unchanged and its handling (watchdog
restart) is correct. The alternative, holding one connection across
polls, trades a rarer hang for a stale handle and is worse. Noted here
so that a spike in `sf-resources` watchdog restarts after this ships
is read as "libvirtd is unwell on that node" rather than as a
regression in this change.

**The `activity_coupled` marks quietly disable regression detection on
three pairs that were previously enforced.** *Mitigation:* D23 limits
the marks to pairs actually on the publish path and requires the
implementing step to re-derive that list from the code. The
enforcement loss is real and is the price of the change being
activity-coupled at all; the compensating control is the re-derivation
recorded as Future work in 3f, after which the pairs can carry fitted
rates again.

**The functional test in 3c flakes on a loaded node.** A 20 s bound on
a cluster whose `sf-resources` is competing for CPU could miss.
*Mitigation:* 20 s is four poll intervals plus a publish, chosen with
that margin deliberately; and the assertion is a drop by the
instance's vCPUs, not a drop to zero, so a sibling instance landing on
the same node during the window does not break it.

**Publishing more often makes `instances_active` sampling denser, and
the load budget's own instance count is `sum(instances_active)`.**
*Mitigation:* the value is unchanged, only its sampling rate; the
budget's fit is against the quantity, not against how often it is
observed. Recorded because the header of
`database_load_budget.yaml` warns that every consumer must count
standing instances the same way, and a reader could mistake this for
a change in that.

## Definition of done

Falsifiable, in order:

1. `LibvirtConnection.get_active_domain_ids()` exists, issues exactly
   one call on `self.conn`, and returns a set. A unit test asserts the
   call count is one, and fails if the implementation is changed to
   `lookupByID()` per domain.
2. The cadence function is module-level and pure: a unit test calls it
   with no libvirt connection, no database, and an injected clock.
3. A test asserts that a domain set of the same size but different
   membership publishes, and deleting the membership comparison in
   favour of a length comparison makes that test fail. Confirmed by
   running it both ways, not by reading it.
4. A test asserts that a change-triggered publish resets the 60 s
   clock, so the next tick is 60 s after the change and not 60 s after
   the previous tick.
5. A test asserts that a poll which raises leaves the last observed
   set unchanged and does not publish a zeroed measurement.
6. A test drives `_run_inner()`'s wiring with `_get_stats` and the
   libvirt connection mocked and asserts `upsert_node_metrics` is
   called before 60 s have elapsed when a domain vanishes.
7. `get_all_domains()` is byte-for-byte unchanged, and its three
   callers are unchanged.
8. The functional test in `cluster_ci_tests/` uses
   `self.create_instance()` and `shakenfist/tests/test_ci_raw_creates.py`
   passes.
9. Exactly four `(operation, caller_daemon)` pairs are marked
   `activity_coupled` by this phase, they are the four named in D23,
   each note cites the issue filed under D25, and
   `GetObjectState`/`resources`, `GetInstanceAttributes`/`resources`
   and `GetNodeDaemonState`/`resources` are all still enforced. Check
   by reading `enforced()`'s inputs, not by reading the diff.
10. `tools/derive-database-load-budget.py` round-trips the new marks
    and notes (D26): a re-derivation whose measurement window does not
    contain them neither raises nor drops them, does not stamp them
    with a window they were not measured in, and returns their note
    text byte for byte. A pair which was fitted and has fallen below
    the cut is still dropped. Both halves are tested, and deleting the
    carry-forward makes exactly one test fail. The whole of
    `test_derive_database_load_budget.py` passes.
11. `examples/prometheus-database-load-rules.yaml` is regenerated in
    the same commit as the budget, the two newly marked pairs are gone
    from the alerting flag, and the two new pairs are present in the
    coefficient series and absent from the flag (D27).
12. No document states the metrics cadence as a flat 60 s except plan
    files recording history, and none implies the capacity ledger
    itself became fresher -- only the measurement did.
13. A real CI run shows `cpu_measured` for a node falling within 20 s
    of a delete, read from the run's own artifacts rather than
    asserted. Read from a downloaded bundle, as phases 1 and 2 did.
14. `python3 tools/check-plan-status.py` passes and
    `pre-commit run --all-files` passes.

## What later phases inherit

Phase 4 gains a slightly better `Retry-After` story: with the
measurement following the domain set, a `507` caused by a
just-completed teardown clears in seconds, so a 15 s hint is a
truthful number rather than an optimistic one.

Phase 5 gains a cleaner denominator. The phase 2 wait records measure
how long the suite waits for capacity; before this phase, an unknown
fraction of every wait was the metrics period rather than a genuinely
full cloud. After it, a wait that remains is much more likely to be
real contention, which is the question phase 5 is deciding on.

Neither is a dependency. Both are reasons to land this before phase 5
reads its numbers.

## Back brief

Restate before starting: which of the two ledgers this phase makes
fresher and which it deliberately leaves alone; why the trigger
watches set membership rather than a count or a vCPU total; the
fifteen-round-trip cost of a publish and the worst-case rate D19
and D21
bound it to; and which budget pairs lose enforcement and what is meant
to restore it.

**Gate before 3d.** *Discharged during implementation.* The
derivation was run against the code before the budget file was opened,
and it found three errors in this plan's first draft: the publish
costs fifteen round trips rather than ten, `GetObjectState` is not on
the publish path, and the schema forbids the rate-less entry D23
originally called for. Implementing 3d then found two more things the
plan had not anticipated, now D25 and D26. All five are corrected
above; this note stands as the record that the gate earned its
place.

No other gate. 3a, 3b, 3c and 3e are each small enough to review as a
commit.

## Outcome

Complete. Steps 3a-3e merged as
[#4200](https://github.com/shakenfist/shakenfist/pull/4200), merge
commit `03cd7be3a`; this section is step 3f.

### The measurement (definition of done item 13)

Read from the three cluster bundles of merge-queue run
[34777052827](https://github.com/shakenfist/shakenfist/actions/runs/34777052827),
which carries the merged code. Each bundle's
`traces/test_cluster_resources_measured_drops_after_delete.json` gives
the test's own start and end; the hypervisor's
`_commands/journalctl-sf-units` gives the instance's `poweron` and
`poweroff`; `primary`'s gives the `DELETE api request received`. The
instance uuid is not in the create's log line -- that carries a
request id -- so it is read from the `db record created` event and
then followed across nodes.

| Topology | Node | Domain destroyed | Test ends | Drop seen within |
|----------|------|------------------|-----------|------------------|
| `debian-12-slim-primary` | sf2 | 19:59:27.782 | 19:59:33.086 | 5.3 s |
| `ubuntu-2404-slim-primary` | sf3 | 19:40:20.907 | 19:40:26.483 | 5.6 s |
| `debian-12-slim-tier` | sf2 | 19:53:26.753 | 19:53:32.354 | 5.6 s |

The last column is an upper bound rather than the interval itself: the
test polls every 5 s, so the publish landed somewhere inside it. Every
one is one domain poll plus a publish, against a 20 s deadline and
against the up-to-60 s this phase set out to remove. The same test
also passed on all three topologies in the earlier queue run
[34750982544](https://github.com/shakenfist/shakenfist/actions/runs/34750982544),
so the assertion has six passes and no failures.

The rise half reads the same way on `debian-12-slim-tier`, where the
delete was issued 5.89 s after `poweron` -- again one domain poll plus
a publish, which is the shape this phase predicts.

### A caveat this reading found, in the test rather than the code

On both `slim-primary` topologies the test issued its delete 0.34 s
and 1.05 s after the domain's `poweron`. Its rise predicate
(`measured >= idle_measured + cpus`) was therefore already true on the
first read, before any publish could have carried this instance's own
domain: that needs up to one 5 s poll and then a trip to the API.
Something else raised the node's `cpu_measured` between the test's
opening read and its first poll -- on a suite running five workers at
once, most likely a sibling test's domain starting on the same node.

The consequence is bounded but real. `baseline_measured` can include a
vCPU this test did not create, so the drop assertion
(`measured <= baseline_measured - cpus`) can be satisfied by that
other instance going away rather than by this one. It is a false-pass
risk and not a false-fail one, which is why it is recorded here rather
than fixed inside a closeout step. It does not weaken the table above:
those intervals are measured from this instance's own `poweroff`.

Fixing it means making the rise attributable. No `node_metrics`
timestamp is exposed over REST -- which is why the test reads
`/admin/resources` at all -- so the cheapest honest version is to
ignore any rise observed before a full domain-poll interval has
elapsed since the create, which costs 5 s of wall clock and makes the
baseline necessarily post-date this instance's domain. Recorded under
Future work in the master plan.

### The load-budget reading D20's risk asks for

Not takeable yet, and that is recorded here rather than deferred
silently. `sfcbr` is running `0955242bf`, which predates `03cd7be3a`,
so no cluster is running this change and `sf-ctl database-load` there
would measure the old cadence.

What can be checked now is the arithmetic that reading is meant to
test, and it checks out exactly. The nightly figures for 2026-09-13 --
pre-change, six nodes, 20.95 standing instances on average -- put
`GetQueueLength`/`resources` at 1.28 QPS cluster-wide. The table above
under *What the survey found* derives twelve queue reads per publish
on an ordinary node and seventeen on the elected network node, at one
publish per node per 60 s, which for `sfcbr`'s five-plus-one predicts
(5 x 12 + 17) / 60 = 1.28 QPS. That figure had until now only been
derived from the code, never observed, and the first draft of this
plan had it wrong (seven queue reads, ten round trips), so the
agreement is worth writing down. The other three pairs sit below the
nightly report's top-40 cut.

The post-change reading therefore has something to be compared
against: the same pair should rise with instance churn, bounded by D19
and D21 at 3/s per node, and if it lands materially above that model
the queue-read split in D20 is the pre-agreed remedy.
[#4197](https://github.com/shakenfist/shakenfist/issues/4197) carries
it.

### What CI did

Two things in this phase's CI are worth recording, because neither is
about the change and both cost time.

The merge-queue run on the final head
([34777052827](https://github.com/shakenfist/shakenfist/actions/runs/34777052827))
failed on `Node lifecycle`, one second after the same script printed
`No more running services!` and with every `sf-*.service` shown
`inactive dead`. The surviving process the stop check counted was a
`for i in $(seq 1 600); do case "$(hostname)" in sfcbr-*)` wait loop
left over from the deploy, not a Shaken Fist daemon. The three cluster
jobs in that run, which are the ones carrying this phase's assertion,
all passed.

Earlier, on the pull request itself, `Credential scan` and
`Issue links` both failed with *"The job was not started because it
repeatedly failed to be acquired (5 attempts)"* -- no steps, no runner
name, no log. Both passed on a rerun with no change to the branch.
