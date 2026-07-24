# Phase 4: operator docs, recovery command, and observability

Master plan: [PLAN-node-resource-health.md](PLAN-node-resource-health.md).
Final phase. Depends on phases 1–3 (the primitive, the sf-resources
evaluator that marks a node `STATE_ERROR`, and the cluster-daemon
cascade), all landed on the `node-resource-health` branch.

## Context

Phases 1–3 built the machinery: a dead or hung storage path now takes a
node to `STATE_ERROR`, which stops scheduling onto it, discounts its blob
replicas, and cascades to error its instances and re-replicate its blobs.
What is missing is everything an operator needs to **live with** that
machinery:

1. **A way back.** Master-plan D6 says node error never auto-recovers —
   recovery is operator-only — but there is **no command today** to clear
   it. `sf-ctl` sets node state directly for other transitions
   (`stop` sets `STATE_DEGRADED`, `client/ctl.py:353`), and
   `error → created` is a valid transition (`node.py:82`), yet nothing
   exposes it. A documented recovery procedure needs a real command to
   document, not "edit the database".
2. **A scrapeable signal.** Phase 2 records the diagnosis as an audit
   event (master-plan Q2) — good for a human reading a node's history,
   but not something Grafana can alert on. The sf-6 incident's whole
   lesson was invisibility; a single node-health gauge on the metrics
   port the resources daemon already runs is the minimal seam for future
   alerting.
3. **The written model.** There is no operator page describing node
   resource health, and `docs/developer_guide/state_machine.md`'s
   `## Nodes` section describes the `error` state **incorrectly** for the
   post-phase-2 world (it says error means only "has not checked in for
   ten times `NODE_CHECKIN_MAXIMUM`", and omits `degraded` entirely).

Phase 4 closes all three: a recovery command, a minimal metric, and the
docs — with the docs written last so they describe code that exists.

## Scope note (please confirm in the back brief)

The master plan framed phase 4 as "operator docs + observability". Two
of the steps below add **code**, not just prose:

- **4a** adds an `sf-ctl` recovery command. Without it the recovery
  procedure D6 requires documenting has no first-class entry point. The
  fallback (document a raw DB/state poke) is worse; recommend keeping 4a.
- **4b** adds one Prometheus gauge. The user has noted no Grafana
  alerting is configured yet and that the sf-6 failure may have been a
  one-off, so this is the **one step to cut** if a docs-only phase 4 is
  preferred — the audit event already gives a human the diagnosis. If
  cut, 4c simply omits the metrics section.

Both are small and independently droppable. Everything else is docs.

## Key references in the existing code and docs

- **`shakenfist/client/ctl.py`** — flat click commands registered via
  `cli.add_command(...)` (`:432+`); `initialise_node`/`register_daemon`
  take an optional `--node-name`, `Node.from_db(name)`, then set state
  directly (e.g. `stop` at `:341`, which does
  `n.state = Node.STATE_DEGRADED  # type: ignore[misc]`). The new
  recovery command mirrors this exactly.
- **`shakenfist/node.py:82`** — `state_targets[STATE_ERROR] =
  (STATE_CREATED, STATE_DELETED, STATE_DEGRADED)`, so `error → created`
  is legal (the comment: "A node can return from the dead..."). `:45`
  `ACTIVE_STATES` excludes `error`; `:46` `INACTIVE_STATES` includes it.
- **`shakenfist/daemons/resources/main.py`** — already runs a Prometheus
  server on `RESOURCES_METRICS_PORT` (`:138`) with a `gauges` dict
  (`:566`). The node-health thread `_run_health_checks` (`:~545`) is
  where a health gauge is set each cycle; it calls
  `node_health.build_for_this_node()` / `evaluate` / `apply_result`.
- **`shakenfist/node_health.py`** — `evaluate()` returns
  `NodeHealthResult(healthy, failed, affected_types, reason)`; the gauge
  reads `result.healthy`.
- **`shakenfist/config.py`** — `NODE_HEALTH_CHECK_INTERVAL=60`,
  `NODE_HEALTH_WRITE_INTERVAL=300`, `NODE_HEALTH_PROBE_TIMEOUT=30` (the
  knobs the operator page documents).
- **`docs/developer_guide/state_machine.md:259` `## Nodes`** — the state
  list + mermaid diagram to correct (error cause, missing `degraded`).
- **`docs/operator_guide/scheduler.md`** — the placement pipeline; a node
  in `error`/`missing` is not an `active` candidate. Add a one-line
  cross-reference, do not restate the model.
- **`docs/operator_guide/`** — sibling operator pages set the house style
  (`power_states.md`, `database.md`, `load_balancing.md`). The new page
  lives here.
- **`mkdocs.yml` and `mkdocs.yml.tmpl`** — the nav exists in **both**
  files (no generator found in `tools/`/`deploy/`; they are kept in sync
  by hand). A new page needs an "Operator Guide" nav entry in each, in
  the same alphabetical slot.
- **`docs/plans/index.md`** (rows at `:14`, `:57–58`), **`order.yml`**,
  and the master plan's Execution table — the status surfaces to flip to
  Complete.
- **Sibling precedent:**
  [`PLAN-health-checks-phase-04-operator-docs.md`](PLAN-health-checks-phase-04-operator-docs.md)
  — the completed sibling's docs phase; matches this one's shape (extend
  where a home exists, mark the plan complete last).

## Inherited decisions (master plan)

D1 (error stops scheduling + discounts replicas — the *why* the page
explains), D4 (two-tier probe: cheap `statvfs` every cycle + `_heartbeat`
write every `NODE_HEALTH_WRITE_INTERVAL`), D5 (hard-NFS mounts **hang**,
so the probe is deadline-bounded and a timeout **is** the unhealthy
signal — the page's key operator-facing subtlety), D6 (recovery is
operator-only, never auto-cleared — what 4a implements and 4c documents),
D7 (fast local mark vs surviving-node cascade). Q2 (observability: the
audit event is the durable record; 4b adds the optional scrapeable
gauge).

## Design

### G1 — the recovery command (`sf-ctl`)

A flat command mirroring `stop`/`register_daemon`, e.g.
`clear_node_error` → `sf-ctl clear-node-error`:

```python
@click.command(name='clear-node-error')
@click.option('--node-name', default=None,
              help='Node to clear (defaults to NODE_NAME from config)')
def clear_node_error(node_name):
    """Return a node from the error state to created.

    Node resource-health errors never clear automatically (a marginal
    disk must not flap the node in and out of service); an operator runs
    this once the underlying storage is confirmed healthy. If the failure
    persists, sf-resources re-errors the node within one check interval.
    """
    node_name = node_name or config.NODE_NAME
    n = Node.from_db(node_name)
    if not n:
        raise click.ClickException(f'Node "{node_name}" not found.')
    if n.state.value != Node.STATE_ERROR:
        raise click.ClickException(
            f'Node "{node_name}" is in state {n.state.value}, not error; '
            'nothing to clear.')
    n.add_event(EVENT_TYPE_AUDIT, 'operator cleared node error state')
    n.state = Node.STATE_CREATED  # type: ignore[misc]
    click.echo(f'Node "{node_name}" is now in state {n.state.value}.')
```

Guard on the current state being `error` so the command is a no-op-safe
diagnostic when run against a healthy node. It does **not** clear the
instance/blob cascade — errored instances stay terminal (the operator
snapshots/deletes them); this only returns the *node* to scheduling.
Register with `cli.add_command(clear_node_error)`.

### G2 — the node-health gauge (`sf-resources`)

In `_run_health_checks`, after `evaluate()` returns each cycle, set a
`node_resource_health` gauge to `1.0` when healthy, `0.0` when not —
using the same `prometheus_client.Gauge` already imported. Create the
gauge once (module or thread scope, not per cycle — re-registering a
Gauge name raises). This exposes on the existing `RESOURCES_METRICS_PORT`
with no new server. Keep it to the single 1/0 node-level gauge; per-path
gauges are not worth the label churn now (the audit event already carries
per-path detail). Document the metric name in 4c.

### G3 — the operator page (`docs/operator_guide/node_health.md`)

A new page, registered in both mkdocs nav files. Sections:

1. **What it is** — node health beyond heartbeat: the resources a node's
   hosted object types depend on (storage paths today). Contrast with
   `STATE_MISSING` (heartbeat) and `STATE_DEGRADED` (a self-reported dead
   daemon, still schedulable). A storage failure is `STATE_ERROR`, which
   is **not** schedulable (D1).
2. **What is checked** — per node role: a hypervisor probes
   `instances`, `image_cache`, `blobs`; every node probes `blobs`
   (replica store) and `uploads`. The two-tier probe (D4): cheap
   `statvfs` each cycle catches a gone or read-only path; a `_heartbeat`
   write every `NODE_HEALTH_WRITE_INTERVAL` catches write-only failure
   and doubles as a "last seen live" timestamp on disk.
3. **The NFS subtlety (D5)** — a `hard` NFS mount hangs rather than
   erroring; the probe is deadline-bounded (`NODE_HEALTH_PROBE_TIMEOUT`)
   and a **timeout is itself the unhealthy signal**. For NFS-backed
   storage this node probe, not the instance-level qemu pause, is the
   primary detector.
4. **What happens on failure** — node → `error`: no new instances, blob
   replicas rebuilt elsewhere, and (cascade) hosted instances moved to
   `<state>-error` and blob locations dropped, gated on which type was
   affected (an `uploads`-only failure errors nothing but still parks the
   node). Point at the audit event on the node for the diagnosis, and the
   `node_resource_health` gauge (if 4b landed).
5. **Recovery (D6)** — errors never auto-clear. Fix the storage, confirm
   healthy, then `sf-ctl clear-node-error --node-name <n>`; if the
   failure persists the node re-errors within one interval. Errored
   *instances* are terminal — snapshot or delete them; clearing the node
   does not revive them.
6. **Configuration** — the three `NODE_HEALTH_*` knobs with defaults and
   the write-count-vs-detection tradeoff (why the write probe is 300 s,
   not every cycle).

### G4 — correcting existing docs + marking complete

- **`state_machine.md` `## Nodes`**: rewrite the `error` bullet to name
  both causes (prolonged missing heartbeat **and** a failed resource
  dependency), add the missing `degraded` bullet, and (if the diagram
  omits `degraded`) reconcile it. Link the new operator page. Verify the
  bullets match `node.py` `state_targets`/`ACTIVE_STATES` exactly.
- **`scheduler.md`**: one line in the hypervisor/candidate filter noting
  nodes in `error`/`missing` are excluded (`prefilter='active'`),
  cross-linking node health.
- **`ARCHITECTURE.md`**: extend the health section (the sf-api/watchdog
  discussion around `:84–175`) with a short "Node resource health"
  paragraph and note in the `sf-resources` row that it drives
  `node.state` from storage health. **`README.md`/`AGENTS.md`**: a
  one-line pointer to the operator page (README) and to
  `node_health.py`/`resource_health.py` (AGENTS), if not already present.
- **Mark complete**: flip phases 1–4 to Complete in the master plan
  Execution table and the two `docs/plans/index.md` rows; confirm
  `order.yml` needs no change; re-read the master plan **Success
  criteria** and record each as met or note a residual.

## Step-level guidance

Sequential where dependent; isolation `none`; one commit each.

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 4a — recovery command | medium | opus | none | Add `clear_node_error` to `shakenfist/client/ctl.py` per G1 (`sf-ctl clear-node-error --node-name`, `error → created`, guard non-error state, audit event, mirror the `stop`/`initialise_node` idiom incl. the `# type: ignore[misc]` on the state set); register via `cli.add_command`. If ctl.py is in the mypy rollout (it is — tox.ini), keep it clean. Tests (mock `Node.from_db` via the click `CliRunner` pattern used by existing ctl tests, or the project's ctl test harness): an error node → created + one audit event + exit 0; a created node → ClickException/non-zero and state untouched; a missing node name → clean error. Commit subject: `sf-ctl: add clear-node-error to recover an errored node.` |
| 4b — node-health gauge (droppable) | low | sonnet | none | In `shakenfist/daemons/resources/main.py` `_run_health_checks`, create a `node_resource_health` Gauge once and `.set(1.0/0.0)` from `result.healthy` each cycle (G2). Do not re-create the Gauge per cycle. Add/extend a resources-daemon unit test asserting the gauge reflects a healthy vs unhealthy `evaluate()` result. Commit subject: `sf-resources: expose a node_resource_health metric.` |
| 4c — operator guide page | high | opus | none | Write `docs/operator_guide/node_health.md` per G3 (six sections); document the real command from 4a and, if it landed, the metric from 4b. Register the page in the "Operator Guide" nav of **both** `mkdocs.yml` and `mkdocs.yml.tmpl` (alphabetical slot). Keep to the sibling pages' house style; every config default and path must match the code. Commit subject: `docs: operator guide for node resource health.` |
| 4d — correct existing docs + mark complete | medium | opus | none | Per G4: fix `state_machine.md` `## Nodes` (error causes + missing `degraded` + diagram) against `node.py`; add the one-line `scheduler.md` cross-ref; extend `ARCHITECTURE.md` health section + sf-resources row; add the README/AGENTS pointers; flip phases 1–4 to Complete in the master plan Execution table and `docs/plans/index.md` rows; re-verify the master plan Success criteria and record each. Commit subject: `docs: node resource health model, and mark the plan complete.` |

## Step ordering and dependencies

- **4a** and **4b** are independent code steps (different files); either
  order. **4b is optional** (see scope note).
- **4c** comes after 4a (and 4b if kept) so the page documents a command
  and metric that exist, with exact names.
- **4d** is last: it corrects the older docs and closes the plan, and its
  success-criteria sweep should run once everything else is on the
  branch.
- `pre-commit run --all-files` after each code step (the mypy hook runs
  the whole rollout — a few minutes); `mkdocs build` (the `docs` tox env)
  after 4c/4d to catch a broken nav entry or link.

## Success criteria

- `sf-ctl clear-node-error` returns an errored node to `created` (with an
  audit event), refuses on a non-error node, and does **not** revive the
  errored instances; a node whose storage is still bad re-errors within
  one check interval.
- If 4b is kept, `node_resource_health` is exposed on the resources
  metrics port and tracks `evaluate().healthy`.
- `docs/operator_guide/node_health.md` exists, is in both mkdocs navs,
  and correctly describes the checks per role, the two-tier probe, the
  hard-NFS hang/timeout subtlety (D5), the failure cascade, the recovery
  procedure (D6, using 4a's command), and the `NODE_HEALTH_*` knobs.
- `state_machine.md` `## Nodes` no longer misdescribes the `error` state
  and includes `degraded`; `scheduler.md`, `ARCHITECTURE.md`, `README.md`
  and `AGENTS.md` point at the model.
- The master plan and `index.md` show phases 1–4 Complete, and each
  master-plan Success criterion is recorded as met or has a noted
  residual.
- `pre-commit run --all-files` passes and `mkdocs build` succeeds.

## Back brief

Confirm: (1) the two code additions — the `sf-ctl clear-node-error`
recovery command (4a, recommended: the recovery procedure needs a real
entry point) and the single `node_resource_health` gauge (4b, the one
droppable step if docs-only is preferred); (2) that a new
`docs/operator_guide/node_health.md` page is the right home (vs extending
an existing page — there is no node-state operator page today); (3) that
`state_machine.md`'s current `## Nodes` `error` description is stale and
should be corrected here; and (4) that clearing a node's error returns
only the **node** to service, leaving its errored instances terminal for
the operator to snapshot/delete.

## Review checklist for the management session

- [ ] `clear-node-error` guards on the node actually being in `error`,
      writes an audit event, and mirrors the existing ctl state-set idiom
      (`# type: ignore[misc]`); it does not touch instances/blobs.
- [ ] The gauge (if kept) is created once, not per cycle, and reads
      `result.healthy`.
- [ ] The operator page's config defaults, paths, and command/metric
      names all match the code.
- [ ] The NFS-hang / timeout-is-the-signal point (D5) is stated plainly.
- [ ] The new page is in **both** `mkdocs.yml` and `mkdocs.yml.tmpl`;
      `mkdocs build` passes.
- [ ] `state_machine.md` `## Nodes` matches `node.py` (error causes,
      `degraded` present, diagram reconciled).
- [ ] Master plan + `index.md` show phases 1–4 Complete; Success criteria
      revisited and each recorded.
- [ ] `pre-commit run --all-files` passes.

## Documentation index maintenance

Update `docs/plans/index.md` (the intro paragraph at `:14` and the two
Node-resource-health rows at `:57–58`) and confirm `docs/plans/order.yml`
still lists the master plan. Flip the master plan Execution table.
`mkdocs.yml`/`mkdocs.yml.tmpl` gain the new operator page. This phase
completes the plan, so no further phase docs follow.
