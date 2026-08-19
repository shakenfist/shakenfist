# Phase 7 — regression detection a deployer can run

Master plan: [PLAN-database-load-reduction.md](PLAN-database-load-reduction.md)

**Status: Not started.**

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
  `shakenfist/config.py:647`). Phase 4 deliberately made `caller_node` a
  metadata field rather than a label, so summing across tier instances is
  the natural read and label cardinality stays sane.
* **A scrape-and-diff harness already exists in the CI suite.**
  `shakenfist/deploy/shakenfist_ci/database_tier.py` has
  `scrape_database_counters()` (`:48`) and
  `scrape_operation_requests(mesh_ip, operation, caller_daemon)` (`:70`),
  plus `_database_nodes()` and `_sum_requests()` on the test base class,
  and `cluster_ci_tests/test_database_tier.py` already contains
  `test_instance_get_fetches_the_attributes_row_once` — a working
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

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | high | opus | none | **Derive and commit the budget.** Produce `shakenfist/data/database_load_budget.yaml` (or the repo's established data location — check where other shipped data files live before choosing) holding, per `(operation, caller_daemon)`: a `per_node_base_qps`, an optional `per_instance_qps`, and a short `note` naming the loop that produces it. Seed it from the coefficients established by hunt 2026-01 (`GetObjectState`/net 0.31, `GetReferencesFrom`/api 0.32, `GetInstanceAttributes`/net 0.29, `GetInstanceAttributes`/api 0.27, `GetNetworkInterfaceAttributes`/net 0.23, `FindNetworkInterfaces`/net 0.08 QPS per standing instance) and from the per-node pairs (`GetNodeDaemonState` per daemon, `Dequeue`, `GetBlobTransfersForNode`, `GetExistingLocks`) whose slope is near zero. **Use post-phase-6 numbers, not today's** — this file defends a floor, and encoding a regression as the budget is the exact failure the phase 5 plan warned about. Include a `_doc` block stating the window the numbers came from, the cluster shape, and how to re-derive them. Add a schema and a unit test that validates the file parses and every entry has either a base or a coefficient. |
| 7b | high | opus | none | **Generalise the CI harness and add the idle-load check.** `shakenfist/deploy/shakenfist_ci/database_tier.py` already has `scrape_database_counters()` (`:48`) and `scrape_operation_requests()` (`:70`); add a function returning the *full* per-`(operation, caller_daemon)` delta over a window across all tier nodes, and put the new test in `cluster_ci_tests/test_database_tier.py` beside `test_instance_get_fetches_the_attributes_row_once`, which is the working precedent for the delta shape. The test: (1) emits the **positive control** by asserting `GetNodeDaemonState` appears at approximately `daemon_count / DAEMON_STATE_POLL_INTERVAL` — if that pair is absent or far off, fail with a message saying the harness is not measuring, because that is the vacuous case; (2) quiesces, sleeps a measured idle window, and diffs; (3) fails on any pair above its budget by more than the tolerance in 7a's file, and on any *unbudgeted* pair above a fixed-rate threshold, naming the pair and its rate. Per decision 3 the tolerance is generous — this catches a new poll, not a 10% drift. Commit subject: "ci: fail the build on a new fixed-rate database poll." |
| 7c | medium | sonnet | none | **Ship the production detectors.** Generate `examples/prometheus-database-load-rules.yaml` from 7a's budget file — recording rules for per-`(operation, caller_daemon)` rate over a 24h window and for the cluster total, and alerting rules for a pair materially above its modelled ceiling and for an unbudgeted pair polling at a fixed rate. Comment it for an operator who has not written Prometheus rules before: where the file goes, what to scrape to make it work (the `sf-database` metrics port), and how to confirm it evaluates. Follow `examples/loki-secret-alert.yaml` for tone and level of hand-holding. Include the generator as a small tool so the rules cannot drift from the budget, and a test asserting the committed rules match what the generator produces from the committed budget. |
| 7d | medium | sonnet | none | **`sf-ctl database-load`.** A subcommand in `shakenfist/client/ctl.py` (see the `@click.command()` pattern at `:180` onwards) that scrapes every `MARIADB_GATEWAY_HOSTS` entry on `MARIADB_GATEWAY_METRICS_PORT` twice over a `--window` (default 60s), diffs, and prints a table of `(operation, caller_daemon)`, measured QPS, modelled QPS from the budget for this cluster's node and instance counts, and the ratio — sorted by excess, not by absolute rate, because excess is what a deployer needs to see. Support `--json` for scripting. It must degrade honestly: if a tier node is unreachable, say which and report on the rest rather than silently under-reporting. This is the no-monitoring-stack path from decision 5 and the thing to ask for in a bug report. |
| 7e | medium | sonnet | none | **Fix the public dashboard and document the model.** `examples/grafana-dashboard.json` still has `etcd Traffic` and `etcd DB Size` panels for a component that no longer exists — remove them, and add per-caller database load panels driven by `database_requests_total` so the public dashboard is not strictly worse than the private one. Then add a "Understanding database load" section to `docs/operator_guide/database.md` covering: the decomposition (per-node base, per-standing-object coefficient, activity remainder) and why an absolute number is not a useful expectation; how to read `sf-ctl database-load`; where the drop-in Prometheus rules go; and what to do when a pair exceeds its budget, which is to file an issue with the `sf-ctl --json` output attached. Keep it operator-facing — the derivation and the history belong in the plan documents, and this section links to them rather than restating them. |

## Risks and mitigations

* **The budget encodes the regression.** If 7a is derived before phase 6
  lands, the shipped model defends ~142 QPS as normal and every detector
  built on it is worse than useless. *Mitigation:* 7a explicitly requires
  post-phase-6 numbers; this phase does not start until phase 6's
  re-measurement step is recorded.
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

* A committed, schema-validated database load budget expressing expected
  load as a per-node base plus per-standing-object coefficients, derived
  from a post-phase-6 window, with its derivation method documented.
* Functional CI fails when a change introduces a new fixed-rate poll or
  pushes a budgeted pair well outside its budget, and the check carries a
  positive control that fails loudly if the harness stops measuring.
* A drop-in Prometheus rules file, generated from the budget and tested
  against it, plus an alert an operator can confirm fires.
* `sf-ctl database-load` reports measured versus modelled per-caller load
  on any cluster, with `--json`, and degrades honestly when a tier node is
  unreachable.
* `examples/grafana-dashboard.json` no longer references etcd and has
  database-tier panels.
* `docs/operator_guide/database.md` explains the load model in operator
  terms.
* `pre-commit run --all-files` green; functional CI green.

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
