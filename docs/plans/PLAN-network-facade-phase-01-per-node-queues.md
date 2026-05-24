# Phase 1: per-node sf-net queue family and dispatcher hardening

## Context

This is the first phase of `PLAN-network-facade.md`. It is
foundational: no per-method `Network` migration can land
until the queue infrastructure exists to receive the
enqueued operations and the dispatcher behaves correctly
for chained ops.

Phase 1 ships three things, all to `daemons/network/`,
`operations/baseoperation.py`, and
`schema/operations/util.py`:

1. **A new per-node network queue family** with the same
   five priority lanes the codebase already uses
   (`user_waiting`, `user_facing`, `user_facing_high_io`,
   `background`, `background_high_io`). Sf-net's
   net-worker on each node services this family for that
   node, alongside the existing cluster-wide
   `networknode-*` family.
2. **Exponential back-off for deferred ops inside the
   dispatcher** (in-memory map, 100 ms first defer,
   double each subsequent defer, cap at 15 s, soft size
   cap of 1000 entries with oldest-first eviction).
   The map is per-worker-process and is **only correct
   because each queue is serviced by exactly one
   worker**; this assumption gets a prominent comment
   at the map's declaration so a future maintainer
   considering multi-worker dequeue can find it.
3. **Cancellation check on dequeue.** Before executing
   or computing a defer, the dispatcher inspects the
   op's current state. If it is terminal (`STATE_ABORT`,
   `STATE_DELETED`, `STATE_ERROR`, `STATE_COMPLETE`),
   the dispatcher drops the back-off entry, resolves
   the work item, and proceeds to the next job. This
   fixes a latent bug: today executing a pre-aborted op
   raises `InvalidStateException` because
   `state_targets[STATE_ABORT] = (STATE_DELETED,)` only.

What Phase 1 does **not** do:

* No `Network` API surface changes. `Network.ensure_mesh`,
  `n.add_floating_ip`, etc. still do their work inline
  exactly as today.
* No `BridgedVXLanNetwork` class yet — that lands in
  Phase 2.
* No `ErrorReport` infrastructure yet — also Phase 2.
* No `maintain.py` changes — Phase 6.

After Phase 1, sf-net dequeues from both the per-node and
network-node families, the dispatcher's defer is much
tighter for chains, and the latent
`InvalidStateException` is gone. Nothing else has moved.

## Key references in the existing code

* `shakenfist/operations/baseoperation.py:39-94` — the
  queue-list helpers we are mirroring. The existing
  `get_all_network_queues()` returns the five
  `networknode-*` queue names; we add
  `get_node_network_queues(node_uuid)` to return five
  `{node_uuid}-network-*` queue names.
* `shakenfist/schema/operations/util.py:17-50` —
  `enqueue_cluster_operation` builds the queue name as
  `f'{target}-clusteroperation-{metadata["priority"]}'`.
  We add a `family` parameter (default
  `'clusteroperation'` for back-compat) so per-node
  network ops can build
  `{node_uuid}-network-{priority}` queue names.
* `shakenfist/daemons/network/workitem.py:35-58` — the
  net-worker dequeue loop. Needs to iterate the per-node
  family first (in priority order), then fall back to
  the network-node family.
* `shakenfist/daemons/network/workitem.py:59-143` —
  `_cluster_operation_execute`. The cancellation check
  and the back-off map's defer-replacement live here.
* `shakenfist/operations/baseoperation.py:212-240` —
  the existing `defer(delay=15)` helper. We are **not**
  changing this. Existing callers continue to pass an
  explicit delay (or get the 15 s default). The
  dispatcher's internal calls to `op.defer()` are
  replaced with the new back-off helper.
* `shakenfist/daemons/resources/main.py:286` — also
  calls `get_all_network_queues()` for metrics
  collection. Must be updated to also surface per-node
  queue lengths.
* `shakenfist/config.py` — `NODE_UUID` is the obvious
  source of the local node's UUID for the per-node
  queue names. Verify it is what gets used at sites
  that pass `target=metadata['node_uuid']` today.

## Success criteria

Phase 1 is complete when:

* `get_node_network_queues(node_uuid)` returns the five
  per-node queue names in priority order.
* `enqueue_cluster_operation(..., family='network')`
  produces `{target}-network-{priority}` queue names;
  the default `family='clusteroperation'` preserves
  current behaviour for every existing caller.
* `daemons/network/workitem.py`'s dequeue loop iterates
  per-node queues first (priority order), then
  network-node queues. A new per-node network op is
  picked up by the local node's net-worker; an existing
  network-node op is still picked up by the elected
  network node only.
* The dispatcher carries an in-memory back-off map. The
  initial defer for an op uses 100 ms; subsequent defers
  double up to a 15 s cap. The entry is removed when
  the op transitions to executing (deps met) or to a
  terminal state. A new entry beyond the cap evicts the
  oldest.
* A prominent comment at the map's declaration warns
  that correctness depends on single-worker dequeue per
  queue, and that any move toward multi-worker dequeue
  requires either a shared / locked map or a return to
  DB-backed state.
* The dispatcher checks op state on dequeue and skips
  ops already in `STATE_ABORT`, `STATE_DELETED`,
  `STATE_ERROR`, or `STATE_COMPLETE`, dropping any
  back-off entry. A unit test exercises this path and
  confirms no `InvalidStateException` is raised.
* Unit tests cover the back-off math, the reset on
  dep-completion, the eviction policy, and the
  cancellation check.
* `pre-commit run --all-files` passes.
* The cluster_ci functional suite passes on the phase 1
  PR.
* `ARCHITECTURE.md` and `AGENTS.md` describe the new
  per-node network queue family.

## Step-level guidance

Each step below is a self-contained brief for a sub-agent.
Earlier steps' deliverables are prerequisites for later
steps. Steps land as a single commit per logical change.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a. Queue family helpers | low | sonnet | none | Add `get_node_network_queues(node_uuid: str) -> list[str]` to `shakenfist/operations/baseoperation.py` after `get_all_network_queues()` at line 87. It returns the five queue names in this exact order, mirroring the priority taxonomy in `schema/operations/baseclusteroperation.py:13-17`: `f'{node_uuid}-network-user_waiting'`, `f'{node_uuid}-network-user_facing'`, `f'{node_uuid}-network-user_facing_high_io'`, `f'{node_uuid}-network-background'`, `f'{node_uuid}-network-background_high_io'`. Also add unit tests in `shakenfist/tests/operations/test_baseoperation.py` (create if absent) confirming the function returns exactly those five strings in that order for a given uuid. Do not change `get_all_network_queues()` itself in this step. Commit message subject: `operations: add get_node_network_queues helper.` |
| 1b. Family parameter on enqueue helper | low | sonnet | none | In `shakenfist/schema/operations/util.py:17`, add a `family: str = 'clusteroperation'` parameter to `enqueue_cluster_operation`. Update line 42 to use it: `queue_name = f'{target}-{family}-{metadata["priority"]}'`. Verify with `grep` that no existing caller passes `family=` — they all use the default. Add a unit test covering both `family='clusteroperation'` (existing behaviour) and `family='network'` (new behaviour) queue name generation. Do not change any callers of `enqueue_cluster_operation` in this step. Commit message subject: `schema: add family parameter to enqueue_cluster_operation.` |
| 1c. Net-worker dequeue loop | medium | sonnet | none | Modify `shakenfist/daemons/network/workitem.py:36` so the dequeue loop iterates per-node queues first (priority order) and then the network-node queues. Import `get_node_network_queues` and `config`. The new loop body looks like: `for queue_name in (get_node_network_queues(config.NODE_UUID) + get_all_network_queues()):`. Confirm via test or careful read that `config.NODE_UUID` is set at sf-net startup and is the right uuid (it is — sf-net uses it elsewhere). Also update `shakenfist/daemons/resources/main.py:286` so the metrics collection iterates the same combined list (per-node queues for this node, plus the cluster-wide network-node queues) and emits separate metric labels for each family so operators can tell them apart. Add a unit test that mocks `mariadb.dequeue_work_item` and asserts the call order includes the per-node queues first. Commit message subject: `sf-net: dequeue from per-node and network-node queues.` |
| 1d. Cancellation check on dequeue | medium | sonnet | none | In `shakenfist/daemons/network/workitem.py:59` (`_cluster_operation_execute`), immediately after loading the op via `from_db` and verifying it is not None (line 64), add a terminal-state check. If `op.state.value in (BaseClusterOperation.STATE_ABORT, dbo.STATE_DELETED, dbo.STATE_ERROR, BaseClusterOperation.STATE_COMPLETE)`, log an audit event ("skipping op already in terminal state X"), call `mariadb.resolve_work_item(queue_name, workitem.get('operation_uuid'))` (or whichever identifier matches the dispatcher's existing resolve call at line 57), and `return`. Imports: `BaseClusterOperation` is already imported on line 11; `from shakenfist.baseobject import DatabaseBackedObject as dbo` may need to be added. The back-off map drop in this step is best-effort: the map is introduced in step 1e and the cancellation check should be wired to it then. Note for the implementer: write the check so step 1e only needs to insert the map-drop call, not restructure the branch. Add a unit test that constructs an op already in `STATE_ABORT` and confirms the dispatcher resolves the work item without raising. Commit message subject: `sf-net: drop terminal-state ops on dequeue.` |
| 1e. Exponential back-off map | high | opus | none | This is the most subtle step. In `shakenfist/daemons/network/workitem.py`, add an in-memory back-off map as an instance attribute on `Job` (so each worker process owns its own). Constants: `INITIAL_DEFER_DELAY = 0.1`, `MAX_DEFER_DELAY = 15.0`, `DEFER_DELAY_MULTIPLIER = 2.0`, `BACKOFF_MAP_CAP = 1000`. The map is `self._defer_delays: dict[str, float] = {}`. **At the declaration site, write the big scary comment.** It must be the first thing a future maintainer sees, must use language like "WARNING" or "DO NOT", and must say: this map's correctness depends on each queue being serviced by exactly one worker; per-node `{node}-network-*` queues are serviced only by that node's sf-net net-worker; the cluster-wide `networknode-*` queues are serviced only by the elected network node's sf-net net-worker; any change toward multi-worker dequeue (worker pool in one process, multiple nodes voting on the same queue) requires either a shared / locked map (in-process pool) or a return to DB-backed state (cross-node), otherwise two workers can independently defer the same op and the schedule breaks. Keep the comment concise but unambiguous. Then add the helper methods: `_apply_defer(op, waiting_on)` looks up `op.uuid` in the map (default `INITIAL_DEFER_DELAY`), calls `op.defer(waiting_on=waiting_on, delay=current_delay)`, then sets the map entry to `min(current_delay * DEFER_DELAY_MULTIPLIER, MAX_DEFER_DELAY)`. If after the insert `len(self._defer_delays) > BACKOFF_MAP_CAP`, pop the oldest entry (`next(iter(self._defer_delays))` and `del` — Python dicts preserve insertion order). `_drop_defer_entry(op_uuid)` does `self._defer_delays.pop(op_uuid, None)`. Wire these into the dispatcher: replace the two existing `op.defer(waiting_on=[dep_op])` calls (around `workitem.py:108` and `:133`) with `self._apply_defer(op, waiting_on=[dep_op])`. Call `self._drop_defer_entry(str(op.uuid))` immediately before `op.execute()` (step 1d's cancellation check, when it short-circuits, also drops the entry). Update the step 1d cancellation check to call `self._drop_defer_entry(str(op.uuid))` before resolving the work item. Add unit tests covering: (a) first defer uses 100 ms, second 200 ms, third 400 ms, eighth/ninth cap at 15 s; (b) advancing to executing drops the entry, so a subsequent defer on a different dep starts fresh at 100 ms; (c) inserting beyond `BACKOFF_MAP_CAP` evicts the oldest entry; (d) the cancellation path drops the entry. Commit message subject: `sf-net: exponential back-off for deferred ops.` |
| 1f. Documentation | medium | sonnet | none | Update `ARCHITECTURE.md` to describe the new per-node `{node_uuid}-network-*` queue family alongside the existing `networknode-*` family, with a one-paragraph note that the per-node family is for per-hypervisor operations (`create_on_hypervisor`, `ensure_mesh`) while the network-node family is for network-node-only operations (`create_on_network_node`, `add_floating_ip`, etc.). Update `AGENTS.md` if it lists queue families or daemon behaviour. Add a paragraph to the developer guide (`docs/developer_guide/`) about the in-memory exponential back-off and the single-worker safety property, in case operators ever ask why their backoff isn't behaving the way the configuration suggests it should. Do not modify the `CLAUDE.md` project conventions file unless something there is wrong about queue naming. Commit message subject: `docs: describe per-node network queue family.` |

## Step ordering and dependencies

* 1a, 1b are independent of each other and of every later
  step. They can land in either order.
* 1c depends on 1a (uses the helper) and on 1b only if
  the metrics collector starts emitting per-family
  labels that distinguish `network` from
  `clusteroperation` — implementers should verify.
* 1d depends on no prior step in this phase but lands
  before 1e because 1e wires the back-off drop into the
  cancellation branch.
* 1e depends on 1d.
* 1f depends on 1a–1e being complete so the prose
  describes the as-shipped state.

A reasonable single-PR landing order: 1a → 1b → 1c → 1d
→ 1e → 1f, each as its own commit on the phase branch,
then one PR for the whole sequence.

## Back brief

Before executing any step, the implementing sub-agent
must back brief the management session as to its
understanding of the step and how its planned changes
align with this phase plan and the master plan
`PLAN-network-facade.md`. The back brief should explicitly
confirm:

* The big scary comment at the back-off map's
  declaration will be unambiguous about the
  single-worker safety property and what changes to
  worker topology would invalidate it.
* The cancellation check covers all four terminal
  states, not just `STATE_ABORT`.
* No existing caller of `enqueue_cluster_operation` is
  modified in step 1b (only the helper signature
  changes; defaults preserve behaviour).
* The metrics collector in `resources/main.py` emits
  separate labels for the `network` and
  `clusteroperation` families so operators can
  distinguish them.

## Review checklist for the management session

After each step's sub-agent reports completion:

- [ ] The named files were modified; no unrelated files
      changed.
- [ ] `pre-commit run --files <changed files>` passes.
- [ ] `tox -eflake8 -- -HEAD` passes (project convention
      for changed-files style check).
- [ ] New unit tests pass via `stestr run`.
- [ ] The big scary comment in step 1e is actually
      present at the map's declaration and uses
      sufficiently emphatic language.
- [ ] Commit message subject ends in a period, is no
      longer than 50 characters, and the body wraps at
      75 characters per `CLAUDE.md` conventions.
- [ ] The commit body includes the `Prompt:` paragraph
      and the `Co-Authored-By: Claude Opus 4.7 (1M
      context, high effort) <noreply@anthropic.com>` and
      `Signed-off-by` lines per project conventions.

After all steps complete:

- [ ] The cluster_ci functional smoke suite passes on
      the phase 1 PR.
- [ ] No new `ERROR` / `Traceback` lines appear in the
      cluster_ci stable-log gate.
- [ ] Master plan execution table for Phase 1 is
      updated from `Planning` to `Complete`.
