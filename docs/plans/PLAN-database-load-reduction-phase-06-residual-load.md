# Phase 6 — the residual load, and the regression

Master plan: [PLAN-database-load-reduction.md](PLAN-database-load-reduction.md)

**Status: Complete.** 6a through 6g are all done. What the work actually found is recorded in "Findings" below, which corrects this plan's own survey in two places, and in "6g -- the re-measurement" at the end, which corrects one of the Findings in turn.

## Why this phase exists

Phases 1-5 took steady-state sf-database load from ~527/s to 89-92/s,
below this plan's "under 100 operations per second" success criterion,
measured 2026-08-05 to 2026-08-07. It has since climbed back to ~142/s
(2026-08-18). That is not object-count scaling: on 2026-08-09 the cluster
ran 98.0/s carrying a 24h-mean 15.24 standing instances, and on 2026-08-18
it ran 142.3/s carrying 12.48. More load, fewer objects.

So this phase has two jobs which are easy to confuse and must not be:
find and fix what *regressed*, and reduce the residual floor that was
always there and that phase 5 deliberately did not chase. The first is a
bug hunt with a known-good reference point eleven days back. The second is
ordinary optimisation.

## What the survey found (2026-08-19)

Numbers are 24h-averaged per `(operation, caller_daemon)` from
`database_requests_total`, the phase 4 counter, as recorded in the nightly
facts for 2026-08-18. `scaled` baselines are per-standing-instance
coefficients established by the hunt described in the phase 5 plan.

| Pair | 24h QPS | Defended ceiling | Verdict |
|------|---------|------------------|---------|
| `GetObjectState` / `cluster` | 18.95 | 10.5 (absolute, on-phase) | regressed |
| `GetObjectState` / `net` | 11.47 | 7.67 (scaled) | healthy |
| `GetReservation` / `net` | 10.09 | 8.4 (absolute) | healthy, but is #3655 |
| `GetReferencesFrom` / `api` | 6.86 | 3.99 (scaled) | regressed |
| `GetNodeDaemonState` / (7 daemons) | ~20 aggregate | 2.1 each | at the designed floor |
| everything below the top 40 | ~22 aggregate | none | unwatched |

Total 142.3/s across **401 distinct pairs**; the top 40 account for 120.2/s,
so ~22/s is spread across ~361 pairs that no baseline watches individually
and that are individually too small to be worth a baseline.

### The `GetObjectState`/`cluster` loop is the cluster maintenance sweep

Hunt 2026-01 characterised this as a "deploy-bracketed on/off loop"
(~10 QPS continuously between certain deploys, ~zero otherwise, ~900k
queries per observed on-phase) but never identified the owning code, so it
was never filed. The survey places it, and the "on/off" framing now looks
like an artefact of measuring a *duty cycle* rather than a rate:

`ClusterDaemon._run_inner()`
(`shakenfist/daemons/cluster/main.py:568`) gates all maintenance behind
`if now - last_loop_run >= 60`, and sets `last_loop_run = now` where `now`
was captured **before** the pass ran (`:630`, `:652`). The pass is
therefore scheduled every 60 s from its own *start*, so its duty cycle is
`min(1, pass_duration / 60)` — and once a pass exceeds 60 s the loop runs
back to back with no idle time at all. `_cluster_wide_cleanup()` (`:76`)
walks, per pass, every active IPAM, every in-use floating address, every
artifact, every blob and every namespace key, reading state for each.

Direct evidence from the sfcbr journals (`RecordedOperation('cluster wide
cleanup', threshold=10)` logs any pass over 10 s):

* Over six hours on 2026-08-19, six passes exceeded 10 s; the rest were
  faster. Durations 11.8, 13.4, 15.5, **33.9**, 14.5, 16.6 seconds.
* The last three of those are consecutive minutes (17:10:29, 17:11:03,
  17:12:10) — the loop briefly running nearly continuously, which is the
  shape the hunt's "on-phase" describes, in miniature.
* Separately, the same loop logs ~30 `deleting this namespace key because
  it expired` audits every 15-16 minutes, sustained. Something is minting
  short-lived namespace keys at roughly 2/minute and this loop is reaping
  them — which is very likely the same phenomenon as the unfiled `POST
  /auth` finding below, seen from the other end.

At 18.95 QPS against a 60 s cycle the sweep is issuing roughly 1,140
`GetObjectState` calls per pass. The question phase 6 must answer is not
"where does this come from" — it is `_cluster_wide_cleanup()` — but
"why has the per-pass object count grown, and which of those state reads
does the sweep actually need".

Also noted while reading: `_cluster_wide_cleanup()` takes a
`last_loop_run` argument (`:76`, passed at `:648`) which its body never
uses. Dead parameter; delete it while in there.

### `GetReservation`/`net` is #3655, filed and unfixed

The one item in this phase that is already fully diagnosed. Issue #3655
(open) records that the floating-IP maintenance path sweeps every in-use
address reservation three times per 30 s cycle. The call sites confirm it:
`shakenfist/daemons/network/maintain.py:533`,
`shakenfist/daemons/network/floating_ip_reaper.py:82` and `:137`, and
`shakenfist/daemons/cluster/main.py:134` — each an
`fn.ipam.get_reservation(addr)` inside a `for addr in ...in_use` loop,
which is one RPC per address per sweep. `mariadb.py` has no bulk form;
`get_reservation()` (`:6418`) is single-address only.

### `GetReferencesFrom`/`api` climbed above its per-instance ceiling

Flagged regressed on 7 of the last 8 nights, peaking at 13.28 on
2026-08-15. The scaled ceiling says the API should issue 0.32 of these per
standing instance; it is issuing about 0.55. `references_from` is populated per
object inside `external_view()`, one RPC each, at
`shakenfist/instance.py:653`, `artifact.py:610`, `blob.py:305` and
`node.py:448` — where the node path issues *two*, one keyed by fqdn and
one by uuid. A list endpoint therefore costs one (or two) of these per
object returned, so either a list endpoint grew the field or something is
polling one harder than it used to. Note #3654 fixed the directly
analogous defect for `GetInstanceAttributes` by memoising within
`Instance.external_view()` (`instance.py:563`) — check first whether
`references_from` in the same function was simply missed.

### The `POST /auth` re-authentication storm was never filed

Hunt 2026-01 measured ~45% of "mutating" API background traffic as bare
`POST /auth` token acquisition — activity-independent, and disguising idle
hours in every API-side measurement. It was characterised at the API log
level but never attributed to a client, so it was never filed upstream and
exists only in the hunt's verdict document. The namespace-key expiry
cadence above is a second, independent sighting of what may be the same
behaviour. Clients are in a separate repository
(`shakenfist_client`, in `client-python`), so a fix may well land there;
the attribution work is here.

### Success criterion 2 is probably wrong

Recorded as open question 5 on the master plan. `get_node_daemon_state` is
now the second operation by rate, at ~20/s aggregate — but that ~20/s is
exactly 48 daemon processes polling at the 0.5 Hz that phase 1 chose, and
it is second only because everything around it got much cheaper. Phase 6
should resolve the criterion rather than chase the number.

## Decisions

1. **Separate the regression from the floor.** The 2026-08-07 nightly
   facts are a known-good reference point with per-pair detail. Any pair
   materially above its 2026-08-07 value is regression work; anything at
   or below it is floor work. Do the regression work first — it is
   bounded, it has a bisection window of about eleven days of `develop`,
   and the floor was already judged acceptable at 89-92/s.
2. **Do not add a cache for anything mutable.** Decision 2 of the master
   plan still holds and this phase does not reopen it. `GetObjectState`,
   `GetReferencesFrom` and `GetReservation` are all mutable reads. The
   levers available are: do not ask, ask once per pass instead of once per
   object (the #3654 and #3502 pattern), or push the filter into SQL so
   one RPC answers what N did.
3. **Prefer pushing work into SQL over batching in Python.** Per the
   project's standing preference for filter pushdown, a sweep that needs
   "every X whose state is deleted and older than N" should ask MariaDB
   that question once, not hydrate every X and test in Python. This is
   also what makes the fix survive object-count growth rather than merely
   moving the constant.
4. **The long tail gets a budget, not 361 baselines.** ~22/s across ~361
   pairs is real but individually unattributable, and per-pair ceilings
   there would be pure noise. It is bounded in aggregate by phase 7's
   model instead. This phase's only obligation to the tail is to check
   that no single pair in it is a new fixed-rate poll in disguise.
5. **File before fixing, for anything not already filed.** The
   `GetObjectState`/`cluster` loop and the `POST /auth` storm have both
   now survived one full investigation without being filed, which is how
   they came to be rediscovered here. They get issues as the first act of
   this phase, whether or not they are fixed within it.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | high | opus | none | **Bisect the regression.** Do not write code. The cluster ran 89-92/s on 2026-08-05..07 and ~142/s on 2026-08-18 at a *lower* standing instance count, so something merged in between. Pull the per-pair nightly facts for 2026-08-05 through 2026-08-18 and diff them pair by pair, normalising per-instance pairs by that night's `instances_day_mean` (the coefficients are in the phase 5 plan and the hunt verdict). Produce a ranked list of pairs by QPS *added* since 08-07, which will not be the same ranking as by absolute QPS. Then `git log --oneline --since=2026-08-05 --until=2026-08-19 origin/develop` and correlate: for each pair that grew, name the candidate commits that touch its call path. Deliverable is a written attribution, not a fix — and an honest statement of which growth is unattributed. Note that the 08-08 spike (140.5/s at 35.79 instances) is object-count scaling and is *not* evidence of the regression; the regression is that 08-18 sits at 142/s with a third of those instances. |
| 6b | medium | sonnet | none | **File the two unfiled findings.** Two GitHub issues, each carrying the measured numbers, the repro PromQL, and the call sites from this plan's survey — so neither can be lost a third time. Issue one: the cluster maintenance sweep's `GetObjectState` cost (~19 QPS, `daemons/cluster/main.py:76` and the 60 s duty-cycle gate at `:630`/`:652`), including the pass-duration evidence and the observation that the loop runs back to back once a pass exceeds 60 s. Issue two: the bare `POST /auth` re-authentication volume (~45% of mutating API background per hunt 2026-01), stating explicitly that the client attribution is *not* done and is the first task on it. Apply the `automated-fix-attempted` label to both at filing time if this phase intends to fix them in-flight, so the issue autofixer does not race a branch. |
| 6c | high | opus | worktree | **Fix #3655: bulk-read floating IP reservations.** Four call sites each issue one `get_reservation()` RPC per in-use address inside a sweep: `daemons/network/maintain.py:533`, `daemons/network/floating_ip_reaper.py:82` and `:137`, `daemons/cluster/main.py:134`. Add a bulk accessor to `mariadb.py` beside `get_reservation()` (`:6418`) that returns every reservation for an IPAM in one call — follow the three-layer pattern exactly (`_direct_*`, `_grpc_*`, public, a proto message, `tox -e genprotos`, and a `database_*_total` counter like its neighbours) and read `PLAN-grpc-bounded-replies.md` first, because an all-reservations reply is exactly the unbounded shape that plan exists to stop: bound it, and say in the plan how. Convert all four sweeps to one bulk read per pass. Expected effect ~8-9 QPS. Add a functional test in `cluster_ci_tests/test_database_tier.py` asserting the count, modelled on `test_instance_get_fetches_the_attributes_row_once` which already does exactly this shape for #3654. Commit subject: "net: read floating IP reservations in one pass." Fixes #3655. |
| 6d | high | opus | worktree | **Reduce the cluster maintenance sweep's state reads.** Depends on 6b's issue. `_cluster_wide_cleanup()` (`daemons/cluster/main.py:76`) reads object state per swept object across five populations. Per decision 3, convert the state-filtered walks into SQL questions: the IPAM walk (`:114`) discards everything whose state was updated within 300 s *after* hydrating it; the floating-address walk (`:132`) reads `obj.state` per reservation to find deleted-and-aged users; the artifact walk (`:168`) hydrates a namespace per artifact. `mariadb.get_objects_by_state()` and the orphan-reconciliation queries in `mariadb.py` are the existing precedent for asking MariaDB instead. Measure before and after with the CI harness, not by inspection. Also delete the unused `last_loop_run` parameter (`:76`, `:648`). Do **not** change the 60 s gate or the duty-cycle behaviour in this step — that is a separate judgement about maintenance latency and belongs in its own commit if it is wanted at all. |
| 6e | medium | opus | none | **Diagnose `GetReferencesFrom`/`api`.** It is running at roughly 0.55 per standing instance against a 0.32 ceiling and has been flagged 7 nights of 8. First check the cheap hypothesis: #3654 memoised `GetInstanceAttributes` within `Instance.external_view()` (`shakenfist/instance.py:563`), and `references_from` (`:653`) may be the same defect left unfixed in the same function. If so, fix it the same way and add the same style of CI assertion. If not, find which endpoint grew the read; the call sites are `instance.py:653`, `artifact.py:610`, `blob.py:305` and `node.py:448` (the last issuing two RPCs per node, keyed by fqdn and by uuid — worth collapsing regardless of what this step concludes). Report before fixing if the cause turns out to be a caller polling harder rather than an endpoint doing more work — that is a different fix in a different repository. |
| 6f | low | sonnet | none | **Resolve success criterion 2.** Master plan open question 5. Replace "`get_node` and `get_node_daemon_state` no longer appear in the top five operations by rate" with a criterion that survives the thing phase 1 actually did: `get_node` gone entirely, and `get_node_daemon_state` at or under the arithmetic floor implied by `DAEMON_STATE_POLL_INTERVAL` for the cluster's daemon-process count. Edit the master plan's success criteria and close the open question with the resolution recorded inline, in the style of the other resolved questions there. |
| 6g | high | opus | none | **Re-measure and record.** After 6c-6e deploy to sfcbr, wait for a full 24h window and record the new per-pair numbers in this plan the way phase 5's outcome section does — including any target that did *not* move, which per the master plan's measurement discipline is a finding rather than a detail. Then update the committed load baseline so the improved floor is the defended one, and state plainly whether the plan's under-100/s criterion is met again. If it is not, say by how much and what is left. |

## Risks and mitigations

* **Chasing the floor instead of the regression.** The absolute ranking
  puts `GetObjectState`/`cluster` first, but the *growth* ranking may put
  something else first, and the growth is what broke the criterion.
  *Mitigation:* 6a is a pure attribution step, deliberately produces no
  code, and normalises for instance count before ranking.
* **A bulk reservations reply is unbounded.** 6c replaces N small RPCs
  with one large one, which is exactly the failure mode
  `PLAN-grpc-bounded-replies.md` exists to prevent, and sfcbr has already
  crossed the gRPC message limit twice (#3638). *Mitigation:* the step
  requires reading that plan and bounding the reply as part of the design,
  not as a follow-up.
* **Pushing filters into SQL without the index.** Decision 3's SQL
  pushdown makes things worse, not better, if the predicate has no index
  to use — moving load from the gRPC tier onto MariaDB is not a win.
  *Mitigation:* every new predicate in 6d states which index serves it,
  and adds one if none does; this is a standing project rule.
* **The sweep changes alter cleanup semantics.** 6d touches code that
  deletes IPAMs, releases addresses and removes artifacts. A wrong
  predicate deletes live objects. *Mitigation:* worktree isolation, and
  the sweep's behaviour is covered by functional CI (which exercises full
  object lifecycles) rather than by unit tests alone.
* **The regression is not ours.** It may be a change in what runs against
  sfcbr — CI shape, conductor behaviour, a client — rather than a change
  in Shaken Fist. *Mitigation:* 6a is required to say so explicitly if the
  growth does not correlate with any `develop` commit, and that answer
  ends the phase's regression thread rather than prolonging it.

## Definition of done

* The regression is attributed: every pair that grew materially since
  2026-08-07 is either explained by a named change, explained by standing
  object count, or explicitly recorded as unattributed.
* #3655 is fixed and closed, with a functional-CI assertion that the
  reservation sweep issues one bulk read per pass rather than one per
  address.
* The cluster maintenance sweep's `GetObjectState` cost is reduced, with
  before-and-after numbers from a 24h window recorded in this file.
* The `GetObjectState`/`cluster` loop and the `POST /auth` volume are
  filed as issues with their measured numbers, whether or not they are
  fixed here.
* `GetReferencesFrom`/`api` is either back under its per-instance ceiling,
  or its cause is documented as living outside this repository, or it is
  split out as its own issue with its measured numbers. *(The third:
  #3876. It is neither under its ceiling nor external -- 6g localised it
  to two unpaired read sites in this repository, which is a fix rather
  than a measurement and does not belong in a phase whose remaining work
  was re-measurement.)*
* Master plan success criterion 2 is restated and open question 5 closed.
* `pre-commit run --all-files` green; functional CI green.
* The 24h cluster total is recorded honestly against the under-100/s
  criterion, met or not.

## Back brief

Before executing any step, back brief the operator: which of the two jobs
(regression versus floor) the step belongs to, what the step will measure
before it changes anything, and — for 6c and 6d — what the reply-size and
index consequences of the chosen approach are.

## Findings (2026-08-19)

### 6a — the regression is two things, and only one of them is a defect

Diffing the per-pair nightly facts for 2026-08-07 (92.4/s, the last night
under target) against 2026-08-18 (142.3/s) gives a total delta of +49.9/s.
It decomposes:

**~+19/s is a real defect** — `GetObjectState`/`cluster` went from *below
the top-40 cutoff* to 18.95/s. It is 38% of the whole regression in one
pair. Diagnosis below.

**~+12/s is a node count, not a regression.** The per-node fixed-rate
pairs all grew by almost exactly 50% on 2026-08-12: `Dequeue`/net 2.03 to
3.04, `GetBlobTransfersForNode`/transfers 2.03 to 3.04, and every one of
the seven `GetNodeDaemonState` pairs 1.99 to 2.98. `GetNodeDaemonState` is
one read per `DAEMON_STATE_POLL_INTERVAL` per daemon per node, so its rate
divided by the poll interval *is* a node count: it reads 3.9 nodes before
2026-08-12 and 5.8 after.

*Superseded by 6g.* The arithmetic is right and the conclusion drawn from
it -- "the cluster gained two nodes" -- is wrong. The count that rose is
the number of nodes the **counter can see**, not the number of nodes that
exist. See "6g -- the re-measurement" below.

That is the most important finding for phase 7, and it is a gap in the
model rather than a bug in the code: **the ratchet's baselines scale with
standing instance count but not with node count**, so growing the cluster
reads as a regression on every per-node pair simultaneously. Phase 7's
budget must carry a per-node term. It already plans to; this is the
evidence for why that is not optional.

The remainder is instance-count movement and the long tail (330 to 401
distinct pairs).

### The `GetObjectState`/`cluster` diagnosis — and a correction

This plan's survey attributed it to `_cluster_wide_cleanup()` and its 60
second duty-cycle gate. **That was wrong**, and the correction is worth
recording because the reasoning looked sound.

The raw counters settle it. `database_requests_total` for this pair steps
in a burst every ~16 minutes on both database nodes — +7,614 and +7,604 at
18:38, +7,610 and +7,615 at 18:54 — against a continuous baseline of
~1.3/s. That is ~15,200 calls in a single burst every 15 minutes.
15,200/900 = 16.9/s, exactly the observed 24h average. The 60 second sweep
is the 1.3/s baseline, not the 17.

The 15-minute cadence identifies it as a `schedule.every(15).minutes` job,
and it is `reap_expired_namespace_keys()`
(`shakenfist/daemons/cluster/scheduled_tasks.py`). It walked every expired
key and read `key.state.value` per key — an uncached round trip each, since
`.state` is a property with no memoisation (`baseobject.py:536`) — purely
to discard the ones it could not act on. `keys_with_attributes()`
deliberately does not filter on object state (its docstring says so, and
adds that "any future soft delete path must revisit that"; this sweep *is*
one), so every key the sweep had already soft deleted stayed in its input
until hard deletion caught up, and every stateless zombie key stayed there
forever.

That also explains the shape the earlier investigation could not: the
nightly numbers ramp monotonically (0, 2.82, 4.60, 10.45, 14.45, 16.21,
18.95) rather than switching on and off. It was a backlog growing, not a
loop toggling. The "deploy-bracketed on/off loop" characterisation was an
artefact of sampling.

**Fix:** one `get_objects_by_state()` query per pass for the keys in an
actionable state, and a set membership test in the loop. The zombie
counting this sweep used to do is dropped: `reconcile_orphaned_objects`
owns stateless rows and already counts them hourly.

### 6c — the bulk accessor already existed

The step brief anticipated adding a bulk reservation RPC through all three
layers, with a proto change and a reply-size design. None of that was
needed: `mariadb.get_reservations_for_ipam()` already exists and is already
used by `IPAM.get_haloed_addresses()`. The change is an
`IPAM.get_all_reservations()` helper over it and four call-site
conversions. Reply size is unchanged from a call already made on this path.

The reaper conversion also removed a second read per address —
`get_allocation_age()` was itself a `get_reservation()` — and fixed a
latent crash: for an in-use address with no reservation row,
`get_allocation_age()` returns `None` and the old code evaluated
`now - None`. That address is exactly the leak the sweep exists to find,
so it now falls through to the leak path rather than raising.

### 6e — `GetReferencesFrom`/api is not an endpoint defect

The cheap hypothesis was wrong: `Instance.external_view()` already issues
exactly one `get_references_from` per view, inside the `attribute_memo()`
that #3654 added. There is no per-view duplication to remove.

Per this step's brief, reporting rather than fixing: the growth is request
volume, and it correlates with the same 2026-08-12 step. That step turned
out to be a coverage change rather than two new nodes (see 6g), so the
mechanism below is wrong in its cause while remaining right in its shape:
two more *visible* nodes means two nodes' worth of API polling entering
the counter for the first time, and the
`/instances` list endpoint costs one reference read per instance returned —
so its cost scales with instances *times* poll rate while the ratchet's
coefficient captures only instances. Same model gap as above, seen through
a different pair. No code change here.

One real but small duplication was confirmed and left alone: the node
external view issues two `get_references_from` calls, keyed by fqdn and by
uuid (`shakenfist/node.py:448`), because `BLOB_LOCATION` rows key nodes by
fqdn and `INSTANCE_LOCATION` rows by uuid. Collapsing it needs an `IN`
variant at the SQL layer and is worth ~4 calls per sweep. Not worth the
change on these numbers.

### Deviation from the step plan

Step 6c's brief called for a functional-CI assertion modelled on
`test_instance_get_fetches_the_attributes_row_once`. The sweeps this phase
fixes are fixed-rate timers, not API-triggered, so a functional assertion
would have to sleep through two sampling windows either side of floating
several addresses — around three minutes of wall clock, for a measurement
that would still be noisy on shared CI hardware. Unit assertions on the
sweep functions are exact, instant, and can assert the thing that actually
matters (that the read count does not grow with address count). Both new
guards were mutation tested: re-introducing the per-address read fails
them, and the first version of the namespace-key guard did *not* fail and
was rewritten until it did.

## 6g — the re-measurement (2026-08-24)

The changes reached `sfcbr` at 22:30-22:40Z on 2026-08-20. The CI
conductor was then broken from 09:46Z to 20:30Z on 2026-08-21, so this
step waited for three clean days rather than the one the step brief
asked for. That turned out to matter for a reason unrelated to the
outage, described below.

### Both fixes landed and are holding

24h means from `database_requests_total`, against a 12h window ending
2026-08-20 20:00Z (the last pre-deploy period):

| Pair | Pre-deploy | 2026-08-23 | Delta |
|------|-----------|------------|-------|
| `GetObjectState` / `cluster` (#3814) | 14.76 | 2.22 | -12.54 |
| `GetReservation` / `net` (#3655) | 11.93 | 0.00 | -11.93 |
| `GetReservationsForIPAM` / `net` (its bulk replacement) | 0.20 | 0.60 | +0.40 |

`GetReservation`/`net` reading exactly zero is the sweep conversion, not
a renamed metric: the bulk accessor it now calls is the third row, and
it is present at the rate one call per pass predicts.

Cluster-wide the effect has to be read against standing instance count,
which swings between 12.4 and 31.4 day to day — more than the effect
being measured. Fitting the nine regression-era days (2026-08-12 to
2026-08-20) gives

```
QPS = 82.50 + 4.648 x standing_instances     r2 = 0.974, n = 9
```

and every post-fix day sits below that line by the same amount:

| Day | Standing instances | Measured | Fit predicts | Residual |
|-----|-------------------|----------|--------------|----------|
| 2026-08-21 | 13.12 | 122.79 | 143.48 | -20.69 |
| 2026-08-22 | 21.93 | 163.10 | 184.43 | -21.33 |
| 2026-08-23 | 16.65 | 138.98 | 159.89 | -20.91 |

Three independent days agreeing within 0.6/s, against the ~24/s the two
per-pair deltas above predict less the ~2.3/s `GetReferencesFrom`/api
grew over the same window. The reductions are real and they are the size
they were designed to be.

### The 2026-08-12 step is a measurement change, not two new nodes

6a attributed ~+12/s of the climb to the cluster growing from four nodes
to six. It did not. `count(instances_active)` returns 6.00 on 2026-08-05
and 6.00 on 2026-08-23, and the series is `sf-1` through `sf-6`
throughout. What changed is what the counter could see.

`database_requests_total` is incremented by a server interceptor in
`shakenfist/daemons/database/main.py`, so it counts **gRPC** calls only.
Until `5a53ab353` ("Route non-database daemons via the gRPC tier",
#3708), `_use_database_service()` treated `MARIADB_HOST` as a per-process
signal, and `MARIADB_HOST` is rendered into the shared systemd
`EnvironmentFile` — so on a database-tier node *every* daemon bypassed
the tier and was invisible to the counter. `sfcbr` has two such nodes,
`sf-1` (roles DHN) and `sf-2` (roles DH). Four of six nodes were being
counted. The commit message says as much: it hides those daemons' load
"from the tier's per-caller request metrics and connection accounting".

Bisecting by hour puts the step at 2026-08-11 20:00-21:00Z, `sfcbr`'s
deploy of that commit. Across a tight 4h window either side, the total
goes 68.05/s to 145.74/s, and the shape is unmistakable:

```
GetObjectState      / cluster    0.00 -> 16.69
CountReferencesTo   / cluster    0.00 ->  1.73
GetBlobAttributes   / cluster    0.00 ->  1.50
GetBlob             / cluster    0.00 ->  1.41
GetNodeDaemonState  / net        1.99 ->  2.98   (x1.50 exactly)
GetNodeDaemonState  / transfers  1.99 ->  2.99   (x1.50)
Dequeue             / queues     2.08 ->  3.15   (x1.51)
```

Every per-node poll multiplies by exactly 1.5 — four visible nodes
becoming six — and the entire cluster daemon appears from literal zero.

### Which means the target was never actually met

The cluster daemon is a single elected singleton, so whether its ~21/s
was counted depended on which node happened to hold the maintenance
lock. Tracking `caller_daemon="cluster"` back through the hunt shows it
flapping:

```
07-25  27.85   07-31   2.76   08-05   2.79   08-08  23.08   08-11   8.61
07-28   2.78   08-02  24.48   08-06   2.78   08-09   2.84
                08-04  26.29   08-07   2.79   08-10  19.53
```

The ~2.8/s floor is the five non-elected candidates; the ~20-27/s peaks
are windows where the leader sat on a node the counter could see.

**2026-08-05, 2026-08-06 and 2026-08-07 are exactly the days it could
not.** Those are the three days recorded in the master plan as
"89-92/s, target met". The load was there; the metric was not looking at
it. Adding the cluster daemon's contribution back puts those days at
roughly 110/s, and they were also missing `sf-1` and `sf-2`'s other
daemons. **The under-100/s criterion was never met, and the "regression"
this phase was created to chase was in substantial part the leader
moving back onto a visible node and then #3708 making it permanent.**

That does not make the phase wasted. #3814 and #3655 were real defects
costing a real ~24/s of real database work, and they had been costing it
for as long as they had existed — invisibly, some of the time. Finding
them by chasing a measurement artefact is luck, but the fixes are not.

### The verdict against the criterion

Measured the way the criterion actually specifies — a quiet window, not
a 24h mean that folds in CI workload — on a 30 minute window at
2026-08-23 02:10Z with `sfcbr` at its floor of 8 standing instances:

**102.38 operations per second. Not met, by 2.4%.**

The same measurement pre-fix (2026-08-17 02:00Z, 8.71 instances) reads
133.97/s. The criterion has been restated in the master plan rather than
declared met; the number stays at 100 because moving a target after
missing it by 2.4% is not a re-derivation, it is an excuse.

### The model, which is the durable output

Splitting the load three ways survives contact with the data. The
fixed-rate per-node polls (`GetNodeDaemonState`, `Dequeue`,
`GetBlobTransfersForNode`) are flat against workload and flat across the
fix:

| Day | Poll subtotal | Per node | Standing instances |
|-----|--------------|----------|-------------------|
| 2026-08-16 | 28.60 | 4.77 | 13.77 |
| 2026-08-18 | 27.32 | 4.55 | 12.38 |
| 2026-08-22 | 29.90 | 4.98 | 21.93 |
| 2026-08-23 | 29.64 | 4.94 | 16.57 |

which gives

```
QPS ~= 32 + 4.9 x nodes + 4.65 x standing_instances
```

At six nodes this predicts 163.4/s and 138.5/s for 2026-08-22 and
2026-08-23 against 163.10 and 138.87 measured. It is the form phase 7
needs and the evidence that a per-node term is not optional: on `sfcbr`
the per-node term is 29.6/s, 29% of the quiet floor, and it would be
59/s on a twelve node cluster with no change in behaviour whatsoever.

`GetNodeDaemonState` alone is 20.29/s of that, and it is exactly at its
arithmetic floor: seven polling daemon types across six nodes is 42
processes, less the one elected cluster daemon (below), at one read per
2s = 20.5/s. Closing the remaining 2.4/s to the criterion most plausibly
comes from here — `DAEMON_STATE_POLL_INTERVAL` at 4s rather than 2s
halves it — but that trades against how quickly an externally written
stop request is noticed, so it is a phase 7 decision with a stated cost,
not a free win.

### The elected cluster daemon does not poll its own daemon state

`GetNodeDaemonState`/`cluster` reads 2.48/s where every other daemon
reads ~2.97/s — precisely five sixths. There is no missing node.
`check_daemon_state()` is called from `idle()`, and the elected cluster
daemon does not idle: `_await_election()` calls `self.idle(5)` then
`self.check_daemon_state()` on each pass, but once elected the inner
loop in `_run_inner()` sleeps on `self.lock.lost_event.wait(5)` and
never calls it (`shakenfist/daemons/cluster/main.py`). Five candidates
poll; the leader does not.

The consequence is small but real: `sf-ctl stop cluster`
(`shakenfist/client/ctl.py`) works by writing `DAEMON_STATE_STOPPING`
for the daemon to notice, and on the elected node nothing is reading it,
so the request is ignored until the node loses election. Local SIGTERM
is unaffected — that goes through `exit_gracefully()` and the abort path,
which the elected loop does check. Filed as #3874; out of scope here.

### What 6g leaves unattributed

`GetReferencesFrom`/`api` is the one target this phase does not close.
6e reported it as request volume driven by "two more nodes means more
concurrent CI", which inherits the mistake above — there were no two
more nodes. Re-reading it as a per-standing-instance coefficient, against
the 0.32 ceiling the survey used:

| Day | QPS | Standing instances | Per instance | Coverage |
|-----|-----|-------------------|--------------|----------|
| 2026-08-09 | 5.00 | 17.81 | 0.281 | 4 of 6 nodes |
| 2026-08-18 | 6.58 | 12.38 | 0.531 | 6 of 6 |
| 2026-08-22 | 13.04 | 21.93 | 0.595 | 6 of 6 |
| 2026-08-23 | 9.75 | 16.57 | 0.588 | 6 of 6 |

Scaling the pre-#3708 figure by the 1.5 the fixed-rate polls show gives
0.42 per instance, so roughly half the apparent rise is the API on `sf-1`
and `sf-2` entering the counter. The remaining ~40% is real, and 6g got far enough to say where it is not
and where it probably is.

It is not the instance view: 6e established that
`Instance.external_view()` already issues exactly one
`get_references_from` per view. What gives it away is the sibling
counter. `GetReferencesFrom`/api runs at **11.6x** `GetReferencesTo`/api
-- 9.75/s against 0.84/s -- and every paired external view reads both,
so no paired workload can produce that ratio. Two unpaired sites can:
`Blob.external_view()` issues three `get_references_from` where one
would do, because `depends_on` and `transcoded` each fetch a filtered
subset of the unfiltered list the same method already reads a few lines
later; and `Artifact.external_view()` reads `b.depends_on` once per blob
version inside its `get_all_indexes()` loop, so an artifact with N
versions costs N+1 reads against one `get_references_to`.

Both are real and which dominates is not established. Split out as
**#3876** rather than fixed here: it is a code change, and what remained
of this phase was re-measurement.

Phase 7 should not carry 0.32 forward. Either number it re-derives will
be defensible; this one is not, because it was measured across
two thirds of the cluster.
