# Phase 7 — regression detection a deployer can run

Master plan: [PLAN-database-load-reduction.md](PLAN-database-load-reduction.md)

**Status: In progress.** Planned 2026-08-19 alongside phase 6, and
re-surveyed 2026-08-25 once phase 6 landed. See "What the survey
found" below for what that changed.

## Why this phase exists

Phase 5 built a ratchet and the ratchet worked: it correctly refused to
call an instance-count increase a regression, and it caught the real
regression that phase 6 exists to chase. But it lives in a private
operations repository, watching one cluster. Everything that makes it
useful — the expected-load model, the committed baseline, the "is this a
new fixed-rate poll" test — is knowledge about *Shaken Fist*, not
knowledge about our operations, and it is currently unavailable to anyone
who deploys Shaken Fist and to every change that lands in this repository
before it reaches production.

The consequences are concrete. The load regression phase 6 chases ran for
roughly eleven days before anyone looked, because the only detector runs
nightly against production. Nothing in CI would have caught a new
fixed-rate poll at review time. And a deployer with a cluster of their own
has no way to answer "is 200 queries per second normal for my cluster?"
other than asking us.

This phase moves the capability into the product: the model, the check and
the operator's view all become things Shaken Fist ships. The private
nightly report then becomes one consumer of a public mechanism rather than
the sole owner of the signal, which is also the only version of this that
survives the report being rewritten or retired.

## The one idea worth porting

The single most valuable thing the ratchet learned is *not* a number. It
is that **an absolute QPS ceiling is the wrong shape for most of this
load**. Hunt 2026-01 established that Shaken Fist's database load is
dominated by polling whose rate is set by how many objects exist rather
than by any work performed, and that it decomposes cleanly:

```
expected_qps ~= per_node_base x nodes
              + per_instance_coefficient x standing_instances
              + an activity-coupled remainder
```

with six `(operation, caller)` pairs fitting the per-instance term at
r-squared 0.90-0.996. A deployer told "expect under 100 QPS" learns
nothing, because their cluster is not our cluster. A deployer given the
decomposition can compute what *their* cluster should draw, and — much
more usefully — can tell the difference between "my load doubled because I
doubled my instances" and "my load doubled because something is broken".
That distinction is the entire value of the ratchet, and it is portable in
a way that our baseline numbers are not.

So the deliverable is the model and the means to evaluate it, with our
measured coefficients as the shipped defaults.

## What already exists to build on

The survey found more foundation here than expected, and one embarrassment:

* **The counter is already public.** `database_requests_total{operation,
  caller_daemon}` is served from every `sf-database` on
  `MARIADB_GATEWAY_METRICS_PORT` (default 13006,
  `shakenfist/config.py:655`). Phase 4 deliberately made `caller_node` a
  metadata field rather than a label, so summing across tier instances is
  the natural read and label cardinality stays sane.
* **A scrape-and-diff harness already exists in the CI suite.**
  `shakenfist/deploy/shakenfist_ci/database_tier.py` has
  `scrape_database_counters()` (`:48`) and
  `scrape_operation_requests(mesh_ip, operation, caller_daemon)` (`:70`),
  plus `_database_nodes()` (`:109`) and `_sum_requests()` (`:135`) on
  `DatabaseTierTestsMixin` in that same module, and
  `test_instance_get_fetches_the_attributes_row_once` (`:199`) — a working
  before/after delta assertion guarding the #3654 fix. The pattern this
  phase needs is already proven in-tree on one operation; it needs
  generalising, not inventing.
* **`examples/` is an established home for drop-in monitoring config.**
  `examples/loki-secret-alert.yaml` set the precedent in the auth
  federation work: a commented, drop-in rule file plus an operator guide
  section explaining where it goes and how to confirm it fires.
* **The public Grafana dashboard is stale.**
  `examples/grafana-dashboard.json` still ships `etcd Traffic` and `etcd DB
  Size` panels — etcd was removed from the product — and has no
  database-tier panels at all. The good dashboard is the private one. That
  is exactly backwards.

## What the survey found

Re-surveyed 2026-08-25, after phase 6 merged. Most of the foundation
above held; three things did not, and the corrections are recorded at
their source in the section above and in the step briefs, so a later step
does not need to rediscover them.

**The delta test is not where this plan said it was, and it must not go
there.** `test_instance_get_fetches_the_attributes_row_once` lives in
`shakenfist/deploy/shakenfist_ci/database_tier.py:199`, inside
`DatabaseTierTestsMixin`, not in `cluster_ci_tests/test_database_tier.py`.
That is not an accident of tidying. The module docstring (`:1-17`) records
why: stestr discovers tests per directory and the two suites are disjoint,
so **a test defined in `cluster_ci_tests/` runs for the first time in the
merge queue** — which is how #3694 landed two broken things at once and
blocked the queue for four days. The mixin is subclassed from both
`smoke_ci_tests/test_database_tier.py` and
`cluster_ci_tests/test_database_tier.py` so the shared tests run in PR CI,
where a break is cheap to find. The cluster suite's own file holds exactly
one test, `test_grpc_lb_fans_out_across_sf_database_instances`, and holds
it because it genuinely requires N>=2 `sf-database` instances. 7b's brief
originally said to add the new test beside a test that is not there; it now
says to put it in the mixin, and decision 8 records why.

**There is no established location for shipped runtime data.**
`[tool.setuptools.package-data]` in `pyproject.toml` names only
`deploy/**`, which reads as "a new YAML under `shakenfist/` will not be
installed". That reading is wrong, and it was worth checking rather than
guessing, because the failure it would cause is the nastiest shape
available: `sf-ctl database-load` working in a source checkout and in CI,
and raising `FileNotFoundError` on a real deployment, where nodes install a
**wheel** built by `python3 -m build`
(`deploy/collection/roles/node/tasks/bootstrap.yml:186-191`). Building the
wheel and listing it settles it: `setuptools_scm` supplies the file finder
and `include-package-data` defaults on, so every git-tracked file under
`shakenfist/` ships. `shakenfist/kerbside/*.md` and `shakenfist/protos/*.pyi`
are already carried this way despite matching no `package-data` entry. So
the budget file may live in the package, no packaging change is needed --
and 7a owes a test that proves it, because the mechanism is implicit and a
future `package-data` tidy-up would silently break it.

**The positive control's arithmetic changed the day after phase 6
merged.** Phase 6's 6g section found the elected cluster daemon never
called `check_daemon_state()`, so `GetNodeDaemonState` ran at exactly 5/6
of the expected rate on `sfcbr`; that was filed as #3874 and fixed in
`b00f2b6fb`, which added the call to the elected loop
(`daemons/cluster/main.py:642`). The plan's risk bullet still said the
arithmetic "excludes the elected cluster daemon", which is now false. It is
also not simply back to `daemon_count / DAEMON_STATE_POLL_INTERVAL`: the
elected loop sleeps `lock.lost_event.wait(5)` (`:692`) rather than
`idle()`, so its poll is rate-limited by its own 5s loop rather than by the
2s interval. Decision 9 states the corrected expression, and the risk
bullet is fixed.

**One budgeted pair is a known open bug.** #3876 — `GetReferencesFrom`/api
running 11.6x its paired `GetReferencesTo`/api, localised by 6g to
unpaired reads in `Blob.external_view()` and `Artifact.external_view()` --
is one of the six per-instance coefficient pairs 7a is told to seed from
(0.32 QPS per standing instance). Measuring it today and committing the
result is precisely "encode the regression as the budget". Decision 10
handles it without blocking the phase on the fix.

**What held.** The counter is public on `MARIADB_GATEWAY_METRICS_PORT`
(default 13006, `config.py:655` — the plan said `:647`, corrected above);
`examples/loki-secret-alert.yaml` is the drop-in precedent and is
documented from `docs/operator_guide/logging.md:239`;
`examples/grafana-dashboard.json` still carries `etcd Traffic` and `etcd DB
Size` panels and contains no reference to `database_requests_total` at all.
`docs/operator_guide/database.md` already has an "Attributing database load
to callers" section (`:294`) with a working `topk` example, so 7e extends a
section rather than starting one, and no operator-facing page states a load
expectation that phase 6 withdrew.

## Decisions

1. **Ship a model, not a number.** The budget file expresses expected load
   as a per-node base plus a per-standing-object coefficient per
   `(operation, caller_daemon)`, with our measured values as defaults and
   a documented way to re-derive them. Absolute ceilings are used only for
   pairs the hunt showed to have near-zero slope.
2. **The budget file is data in this repository, and it is the single
   source of truth.** CI reads it, the `sf-ctl` command reads it, the
   shipped Prometheus rules are generated from it, and any external
   consumer reads it too. One file, several readers — not one model per
   consumer, which is how the private and public versions would drift.
3. **CI checks shape, production checks level.** A CI cluster is small,
   short-lived and shares hardware, so asserting a precise QPS there would
   flake. CI's job is to catch the thing that is unambiguous at any scale:
   a new `(operation, caller_daemon)` pair polling at a fixed rate on an
   *idle* cluster, or an existing pair leaving its budget by a wide
   margin. Precise level-tracking stays in production monitoring.
4. **Every detector carries a positive control.** The failure mode this
   phase must not have is a check that passes because it is broken — the
   lesson recorded in the auth federation leak-detection work, and the
   reason its Loki test emits a synthetic token before asserting the
   absence of real ones. Here the natural control is a rate we set by
   configuration: `GetNodeDaemonState` should appear at
   `daemon_count / DAEMON_STATE_POLL_INTERVAL`, so a harness that cannot
   see that is not measuring anything and must fail loudly.
5. **Give deployers a path with no monitoring stack.** Prometheus rules
   serve deployers who run Prometheus. An `sf-ctl` subcommand that scrapes
   the tier twice, diffs, and prints a per-caller table against the budget
   serves everyone else, is the natural thing to ask for in a bug report,
   and is how a deployer answers "is this normal for my cluster?" without
   installing anything.
6. **Do not put load assertions in the health endpoints.** Database load
   is a capacity and regression signal, not a liveness one; a node drawing
   more queries than budgeted is not unhealthy and must not be drained or
   restarted for it. This keeps phase 7 clear of the health-check plan's
   routing rules.
7. **Our private nightly report becomes a consumer.** It reads the same
   committed budget file instead of its own baseline, so the two cannot
   disagree. That change is small, belongs in that repository, and is out
   of scope here beyond noting the contract it depends on.
8. **The CI check goes in `DatabaseTierTestsMixin`, not the cluster
   suite.** A test defined under `cluster_ci_tests/` runs for the first
   time in the merge queue, which is the #3694 failure mode the mixin was
   created to avoid. The idle-load check needs no particular topology --
   it needs a quiet cluster and a known daemon count, both of which the
   smoke topology has — so it belongs where it runs in PR CI. The one
   test that legitimately lives in the cluster suite is there because it
   requires N>=2 `sf-database` instances; this one does not.
9. **The positive control is a stated expression with a named exception,
   not a round number.** On an idle cluster every daemon polls its own
   state row through `idle()`, which calls `check_daemon_state()` on each
   0.2s tick under a `DAEMON_STATE_POLL_INTERVAL` rate limit, giving
   `1/2` per second per daemon. The single **elected** cluster daemon is
   the exception: since #3874 it does poll, but from a loop that sleeps
   `lock.lost_event.wait(5)`, so it contributes `1/5` per second. The
   control is therefore

   ```
   expected_qps(GetNodeDaemonState) = (daemons - 1)/2 + 1/5
   ```

   and it is an **upper bound**, exact only when every daemon is idle: a
   daemon doing work calls `check_daemon_state()` less often than the rate
   limit allows, and `DAEMON_STATE_POLL_MAX_INTERVAL` backs the poll off
   further while the database is unreachable. The check must therefore be
   one-sided — fail on materially *below* expected, since that is the
   "harness cannot see the whole cluster" signal, and tolerate above only
   within noise. Encode the expression, not the number, so that changing
   `DAEMON_STATE_POLL_INTERVAL` or the elected loop's sleep updates the
   control rather than silently invalidating it.
10. **A budget entry may be marked provisional, and provisional entries
    do not fail CI.** `GetReferencesFrom`/api is a budgeted pair *and* an
    open bug (#3876). Committing today's measurement makes the bug the
    defended floor; blocking the phase on the fix makes a detection phase
    wait on an unrelated repair. So the schema carries `provisional:` with
    an issue reference and a one-line reason, 7b reports provisional pairs
    without failing on them, 7c emits them as recording rules but not
    alerting rules, and 7d prints them flagged. When #3876 lands, the fix
    re-measures the pair and drops the flag — a small, obvious follow-up
    rather than a silent inheritance.
11. **The budget ships inside the package as YAML, and a test proves it
    is installed.** YAML rather than a Python dict because the Prometheus
    generator, the private nightly report and any deployer's own tooling
    are not all Python; inside the package rather than `examples/` because
    `sf-ctl database-load` must read it on a node, where only the wheel
    exists. The wheel does carry it — `setuptools_scm` finds every
    git-tracked file and `include-package-data` defaults on — but nothing
    in `pyproject.toml` says so, so the test asserts the file is readable
    through `importlib.resources` rather than through a path relative to
    `__file__`, which is the difference between noticing a packaging
    regression and shipping one.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | high | opus | none | **Derive and commit the budget.** Produce `shakenfist/data/database_load_budget.yaml` — the survey settled the location, see decision 11 — holding, per `(operation, caller_daemon)`: a `per_node_base_qps`, an optional `per_instance_qps`, an optional `provisional` block (issue reference plus a one-line reason, see decision 10), and a short `note` naming the loop that produces it. Seed it from the coefficients established by hunt 2026-01 (`GetObjectState`/net 0.31, `GetReferencesFrom`/api 0.32, `GetInstanceAttributes`/net 0.29, `GetInstanceAttributes`/api 0.27, `GetNetworkInterfaceAttributes`/net 0.23, `FindNetworkInterfaces`/net 0.08 QPS per standing instance) and from the per-node pairs (`GetNodeDaemonState` per daemon, `Dequeue`, `GetBlobTransfersForNode`, `GetExistingLocks`) whose slope is near zero. **Use post-phase-6 numbers, not today's** — this file defends a floor, and encoding a regression as the budget is the exact failure the phase 5 plan warned about. Those seed coefficients predate #3708 and so were measured across four of six nodes; treat them as the right *shape* and re-derive the levels (phase 6's 6g section has the method and the post-fix model `QPS ~= 32 + 4.9 x nodes + 4.65 x standing_instances`). Include a `_doc` block stating the window the numbers came from, the cluster shape, and how to re-derive them. Add a schema and unit tests that validate: the file parses; every entry has either a base or a coefficient; every `provisional` entry names an open issue; and the file is readable through `importlib.resources` rather than a path relative to `__file__`, because that is what proves the wheel carries it. Mark `GetReferencesFrom`/`api` provisional against #3876 — it is a known open bug and its measured value today is the thing the budget must not canonise. |
| 7b | high | opus | none | **Generalise the CI harness and add the idle-load check.** `shakenfist/deploy/shakenfist_ci/database_tier.py` already has `scrape_database_counters()` (`:48`) and `scrape_operation_requests()` (`:70`); add a function returning the *full* per-`(operation, caller_daemon)` delta over a window across all tier nodes, and put the new test in `DatabaseTierTestsMixin` in that same module beside `test_instance_get_fetches_the_attributes_row_once` (`:199`), which is the working precedent for the delta shape. **Not** in `cluster_ci_tests/test_database_tier.py` — the module docstring explains why (decision 8, and #3694). Note `METRICS_PORT` is hardcoded at `:29` in this module rather than read from config; leave that alone here, but do not copy the habit into 7d. The test: (1) emits the **positive control** by asserting `GetNodeDaemonState` appears at approximately `(daemons - 1)/DAEMON_STATE_POLL_INTERVAL + 1/5`, the expression derived in decision 9 — compute it from the constants, do not write the number down, and make the check one-sided since the bound is an upper one; if that pair is absent or materially below, fail with a message saying the harness is not measuring, because that is the vacuous case; (2) quiesces, sleeps a measured idle window, and diffs; (3) fails on any pair above its budget by more than the tolerance in 7a's file, and on any *unbudgeted* pair above a fixed-rate threshold, naming the pair and its rate — but reports rather than fails for entries marked `provisional` (decision 10). Per decision 3 the tolerance is generous — this catches a new poll, not a 10% drift. Commit subject: "ci: fail the build on a new fixed-rate database poll." |
| 7c | medium | sonnet | none | **Ship the production detectors.** Generate `examples/prometheus-database-load-rules.yaml` from 7a's budget file — recording rules for per-`(operation, caller_daemon)` rate over a 24h window and for the cluster total, and alerting rules for a pair materially above its modelled ceiling and for an unbudgeted pair polling at a fixed rate. Comment it for an operator who has not written Prometheus rules before: where the file goes, what to scrape to make it work (the `sf-database` metrics port), and how to confirm it evaluates. Follow `examples/loki-secret-alert.yaml` for tone and level of hand-holding. Include the generator as a small tool so the rules cannot drift from the budget, and a test asserting the committed rules match what the generator produces from the committed budget. |
| 7d | medium | sonnet | none | **`sf-ctl database-load`.** A subcommand in `shakenfist/client/ctl.py` (see the `@click.command()` pattern at `:180` onwards) that scrapes every `MARIADB_GATEWAY_HOSTS` entry on `MARIADB_GATEWAY_METRICS_PORT` — read from config, not hardcoded — twice over a `--window` (default 60s), diffs, and prints a table of `(operation, caller_daemon)`, measured QPS, modelled QPS from the budget for this cluster's node and instance counts, and the ratio — sorted by excess, not by absolute rate, because excess is what a deployer needs to see. Support `--json` for scripting. It must degrade honestly: if a tier node is unreachable, say which and report on the rest rather than silently under-reporting. Flag provisional entries in the output so a deployer is not told to file an issue we already have. Register it with `cli.add_command()` alongside `gateway_health` (`ctl.py:518`), which is the closest existing precedent — same shape of command, and it shows `sf-ctl` reaching the tier from a node. This is the no-monitoring-stack path from decision 5 and the thing to ask for in a bug report. |
| 7e | medium | sonnet | none | **Fix the public dashboard and document the model.** `examples/grafana-dashboard.json` still has `etcd Traffic` and `etcd DB Size` panels for a component that no longer exists — remove them, and add per-caller database load panels driven by `database_requests_total` so the public dashboard is not strictly worse than the private one. Then add a "Understanding database load" section to `docs/operator_guide/database.md` covering: the decomposition (per-node base, per-standing-object coefficient, activity remainder) and why an absolute number is not a useful expectation; how to read `sf-ctl database-load`; where the drop-in Prometheus rules go; and what to do when a pair exceeds its budget, which is to file an issue with the `sf-ctl --json` output attached. Keep it operator-facing — the derivation and the history belong in the plan documents, and this section links to them rather than restating them. |

## Risks and mitigations

* **The budget encodes the regression.** If 7a is derived before phase 6
  lands, the shipped model defends ~142 QPS as normal and every detector
  built on it is worse than useless. *Mitigation:* 7a explicitly requires
  post-phase-6 numbers; this phase does not start until phase 6's
  re-measurement step is recorded. Phase 6 is now complete and its 6g
  section carries the numbers to seed from.
* **The budget is derived from a measurement that could not see the whole
  cluster.** Every figure taken before 2026-08-11 undercounts: until #3708,
  `database_requests_total` could not see daemons co-located with MariaDB,
  which on `sfcbr` was two nodes of six — and the whole cluster daemon
  whenever the maintenance lock happened to sit on one of them. This is not
  hypothetical; it is what made phase 6's own regression hunt chase a ghost,
  and it means the hunt 2026-01 coefficients named in 7a's brief are seeds
  for the *shape* only and must be re-derived before they are committed as
  levels. *Mitigation:* 7a states the coverage its numbers were taken under
  in the file's `_doc` block, and 7b's positive control is a coverage check
  as much as a harness check — the `GetNodeDaemonState` expression in
  decision 9 only holds if every node is being counted, so it fails loudly
  if a routing change ever hides one again. That expression is the
  corrected one: this plan previously said the arithmetic excluded the
  elected cluster daemon, which was true when it was written and stopped
  being true when #3874 was fixed in `b00f2b6fb`.
* **The CI check flakes and gets disabled.** A load assertion on shared CI
  hardware is the classic flaky test, and a disabled check is worse than
  no check because it reads as coverage. *Mitigation:* decision 3 — CI
  asserts shape with a generous tolerance, not level; and the positive
  control means a broken harness fails loudly rather than passing.
* **The model does not generalise off sfcbr.** Our coefficients come from
  one cluster with one workload shape. A deployer with 500 instances and
  no CI churn may fit the model badly. *Mitigation:* ship the derivation
  method alongside the numbers, and treat the defaults as defaults; the
  `sf-ctl` output is designed to be readable even when the modelled column
  is wrong, because measured QPS and the pair name are useful on their
  own.
* **Two sources of truth re-emerge.** The private report keeps its own
  baseline "just for now" and the two drift. *Mitigation:* decision 2 and
  decision 7 — the committed file is the only model, and the report's
  switch to it is a stated dependency of this phase's value even though
  the change itself is out of scope.
* **Scope creep into a monitoring product.** *Mitigation:* decision 6.
  This phase ships one data file, one CI test, one rules file, one CLI
  subcommand and one docs section. Anything beyond that is future work.

## Definition of done

Each item names the check that settles it. All of these were run at
planning time and every one currently reports "not done", which is what
makes them worth keeping — a criterion that already passes before the
work starts is not a criterion.

* **The budget exists, validates, and ships.** `shakenfist/data/
  database_load_budget.yaml` parses; every entry carries a base or a
  coefficient; every `provisional` entry names an open issue; and the file
  is reachable through `importlib.resources`. The last of those is the one
  that matters:

  ```bash
  python3 -m build --wheel --outdir /tmp/wheel . && \
      unzip -l /tmp/wheel/*.whl | grep database_load_budget.yaml
  ```

  must print a line. *(Checked 2026-08-25: the file does not exist, and
  the wheel build itself works — 555 entries, including
  `shakenfist/kerbside/*.md`, which is the evidence that a git-tracked
  data file ships without a `package-data` entry.)*
* **The budget's levels are post-phase-6.** Its `_doc` block names the
  measurement window, the cluster shape it was taken on, and the fact that
  figures before 2026-08-11 undercount by two nodes and the cluster
  daemon. No coefficient is copied from hunt 2026-01 without re-derivation.
* **The positive control is an expression, not a number.** `grep -rn
  'DAEMON_STATE_POLL_INTERVAL' shakenfist/deploy/shakenfist_ci/` finds the
  new check computing its expectation from the constant, and the elected
  cluster daemon's `1/5` term is present and commented. A reviewer can
  change `DAEMON_STATE_POLL_INTERVAL` in a scratch branch and the check's
  expectation moves with it.
* **The check runs in PR CI, not only in the merge queue.** The new test
  is defined in `database_tier.py`'s `DatabaseTierTestsMixin`, so
  `grep -c 'def test_' shakenfist/deploy/shakenfist_ci/cluster_ci_tests/
  test_database_tier.py` still reports 1. *(Checked 2026-08-25: reports 1
  today.)*
* **The rules cannot drift from the budget.** A test regenerates
  `examples/prometheus-database-load-rules.yaml` from the committed budget
  and asserts byte equality with the committed rules, and it fails if
  either is edited alone.
* **`sf-ctl database-load` works against a cluster.** It appears in `sf-ctl
  --help`, prints measured versus modelled per-caller load sorted by
  excess, supports `--json`, flags provisional entries, and when one tier
  host is unreachable it names that host and still reports on the rest --
  demonstrated by pointing `MARIADB_GATEWAY_HOSTS` at a live host plus a
  dead one.
* **The public dashboard is no longer worse than useless.** `grep -c etcd
  examples/grafana-dashboard.json` reports 0 and `grep -c
  database_requests_total examples/grafana-dashboard.json` reports more
  than 0. *(Checked 2026-08-25: 6 and 0 respectively.)*
* **The operator can answer "is this normal for my cluster?" from the
  docs.** `docs/operator_guide/database.md` gains a load-model subsection
  after "Attributing database load to callers" (`:294`) covering the
  decomposition, `sf-ctl database-load`, where the rules file goes, and
  what to do when a pair exceeds budget. No page states an absolute
  cluster-wide QPS expectation, since phase 6 withdrew that framing.
* `pre-commit run --all-files` green; functional CI green.

## Back brief

Before executing any step, back brief the operator: which artefact the
step produces, which of decisions 8-11 constrain it, and — for 7a
specifically — the measurement window and cluster shape the coefficients
will be derived from, before any number is written down. 7a is the step
whose output every later step inherits, so a wrong window there is
expensive to unwind and cheap to catch in the brief.

## Future work

* **Query-result churn.** The truest definition of wasteful polling is a
  read whose result has not changed since the last read, and nothing
  measures that today. Hunt 2026-01 recorded it as the main missing
  telemetry; it would need either server-side result hashing or MariaDB
  `performance_schema` digest sampling, and it is the natural input to any
  future decision about the deferred watch/subscribe mechanism.
* **Per-call-site attribution.** `caller_daemon` localises load to a
  daemon but not to a loop, which is why the hunt needed source reading to
  finish several attributions. The phase 4 metadata channel is where a
  call-site label would go, and it composes with the OpenTelemetry thread.
