# PLAN: Queue performance phase 11 -- multi-column coalescing key

Planning effort: high. Review effort: high.

## Why this phase exists

The coalescing fold and the enqueue-side dedup both key on a single
indexed column, which for `NetOp` is the network alone
(`shakenfist/operations/net_op.py:58`). A task that does *node-local*
work therefore cannot be coalesced: two hypervisors' operations are
indistinguishable to the SQL while doing different work on different
hosts. That is why `network_ensure_mesh` was removed from
`COALESCIBLE_TASKS` in phase 8, and why
`NodeNetOp.network_apply_create_hypervisor` has never been coalescible
despite the phase 6 audit identifying it. Filed as #3884.

Phase 9 came first deliberately -- generalising a primitive that had
been silently broken for three months, before anything proved it worked
on a running cluster, would have repeated the mistake. Phase 9 proved
it works and phase 10 characterised what is left of the wait tail, so
the sequencing condition the master plan set is now met.

## Scope

**In scope.**

* Generalising the coalescing key from a single `(column, value)` to a
  list of them, through every layer that carries it: the operation
  class declaration, `mariadb.py`'s direct/gRPC/public trio, the
  protobuf messages, the database daemon handler, and the network
  dispatcher's routing key.
* Recording the target node on `NetOp`, so a mesh operation's row can
  say which host it is for.
* Making the two guards that currently protect the single-column key
  *key-aware* rather than removing them.
* Returning `network_ensure_mesh` to `COALESCIBLE_TASKS` with the key
  `(network_uuid, node_uuid)`.
* Unit and functional CI coverage for a fold on a per-node queue.

**Out of scope.**

* `NodeNetOp.network_apply_create_hypervisor`, the other half of
  #3884. Survey finding 6 shows it is drained by `sf-queues`, whose
  worker pool has no target partitioning at all, so the safety
  argument that makes the per-node fold sound for `sf-net` does not
  hold there. See decision 5; this is the decision most likely to be
  argued with.
* Any change to dispatch order, concurrency, pool sizing or fairness.
  Phase 7 decided against explicit fairness and phase 10 found no
  reason to revisit it.
* Dropping the now-unused `queue_is_cluster_wide` reasoning from the
  networknode path. The cluster-wide queues keep their existing
  behaviour unchanged; this phase only widens what else is allowed.
* Fixing #3864 (an operation's events becoming unreachable thirty
  seconds after it goes terminal). Phase 9 worked around it by
  emitting the fold event against the target as well as the
  operation, and this phase inherits that workaround unchanged.

## What the survey found

Nine findings. Four of them change what this phase should build, and
two are corrections to #3884 and to the master plan, made at source in
this same commit (see "Corrections made at source"), so nothing later
in this phase needs to redo them.

1. **The measurement says the opportunity is real, and it is much
   larger than what coalescing reaches today.** `queue-wait-report.py`
   over a six hour `sfcbr` window (4,653 `execution duration` events)
   reports, for `net_op`: 1,510 samples, of which the fold `ran` 263
   times and folded 4 siblings, while **581 were refused outright by
   the `not_cluster_wide` guard**. Broken down by queue class, the
   per-node `network` family contributes 573 on `user_facing` and 8 on
   `background`. On that same lane 346 further samples took
   `batch_size_one`, so 573 of the 919 per-node network operations
   (62%) were dequeued alongside at least one sibling -- which is the
   ceiling on what a per-node fold could collapse. Read `581` as an
   upper bound rather than a count of foldable work: the outcome chain
   in `shakenfist/operations/baseoperation.py:481-489` tests
   `not_cluster_wide` *before* `no_coalescible_tasks`, so an operation
   with no coalescible task lands in the same bucket. In practice
   `network_ensure_mesh` is the only NetOp task routed to a per-node
   queue (three call sites, finding 3), so nearly all of that 581 is
   the population this phase is about.

2. **There are three guards, not one, and #3884 names only one of
   them.** The issue says "the guard is currently the only thing
   making the queue-blind SQL safe", meaning the enqueue-time
   `InvalidCoalescibleEnqueue` check at
   `shakenfist/schema/operations/net_op.py:157-165`. There is a second,
   independent guard in the fold itself:
   `shakenfist/operations/baseoperation.py:461-464` computes
   `queue_is_cluster_wide` from `queue_name.startswith('networknode-')`
   and skips the fold entirely otherwise
   (`baseoperation.py:485`). The third is simply
   `network_ensure_mesh`'s absence from `COALESCIBLE_TASKS`
   (`schema/operations/net_op.py:82-86`). All three must move together;
   relaxing only the one #3884 names would leave the fold still
   skipping every per-node queue and the phase would measure as a
   no-op.

3. **#3884's call-site line numbers have drifted, and one is on a
   different file line than stated.** Current positions:
   `Network.ensure_mesh` at `shakenfist/network/network.py:983` (issue
   says 980), `shakenfist/daemons/network/maintain.py:678` (673) and
   `:733` (728), the two-task list at
   `shakenfist/network/network.py:319` (316) and
   `shakenfist/external_api/instance.py:1131` (1091). The shape of each
   site is as the issue describes.

4. **`Network.ensure_mesh` fans out one operation per participating
   node in a single call** (`network.py:979-990` loops over
   `node_uuids`, enqueueing with `target=node_uuid,
   family='network'`). So N instance starts on one network produce N
   operations on *each* participating node's queue, which is the same
   duplication pattern `network_apply_update_dnsmasq` has and the
   reason the fold exists. The issue describes this as "a node
   restoring N instances on the same network still enqueues N mesh ops
   where one would do", which is right but understates it: the fan-out
   multiplies by the node count as well.

5. **`cluster_operations.node_uuid` already exists, is already indexed,
   and is already populated -- for other operation types.** The column
   is declared at `shakenfist/mariadb.py:2069` with
   `sa.Index('ix_cluster_ops_node', 'node_uuid')` at `:2076`, and
   the enqueue path extracts it from the metadata dict
   unconditionally -- in `_direct_create_and_enqueue_cluster_operation`
   at `:21226`, which is the function `enqueue_cluster_operation`
   actually reaches; `_direct_create_cluster_operation` at `:21010`
   does the same thing but is not on this path. `_COALESCIBLE_TARGET_COLUMNS`
   (`mariadb.py:22648`) already whitelists it. So the claim in #3884
   that the column "is simply always NULL for NetOps today" is correct
   as stated, but the reason is only that NetOp's model has no
   `node_uuid` field -- no schema migration is needed, and the moment
   the field appears on the model the column populates itself.

6. **`NodeNetOp` already carries `node_uuid`, but is drained by a
   dispatcher with no target partitioning.** The model has both
   `node_uuid` and `network_uuid`
   (`shakenfist/schema/operations/node_net_op.py:41-42`), so the half
   of #3884's step 2 that concerns `network_apply_create_hypervisor`
   is already done. What is not done, and what the issue does not
   consider, is the dispatcher. In the same six hour window all 570
   `node_net_op` samples sit on `per-node (cluster op)` queues, drained
   by `sf-queues`. `sf-queues` is a `WorkerPoolDaemon` that fills spare
   slots from a single `dequeue_work_items` call
   (`shakenfist/daemons/daemon.py:677-720`) and starts one worker per
   claimed item; there is no routing key and no per-target affinity.
   `sf-net`, by contrast, hashes every operation to a fixed worker by
   `_routing_key` (`shakenfist/daemons/network/workitem.py:93-105`) and
   documents that as a load-bearing safety invariant at `:48-91`. The
   fold's soundness on a per-node queue rests on that invariant. This
   is why `network_apply_create_hypervisor` is out of scope -- see
   decision 5.

7. **The safety-invariant comment explicitly forbids what this phase
   does, and is right to, on the evidence available when it was
   written.** `shakenfist/daemons/network/workitem.py:74-79` says
   "What actually makes the fold safe is that every coalescible task is
   confined to the cluster-wide networknode queue, enforced at enqueue
   time by the InvalidCoalescibleEnqueue guard [...] Do not weaken that
   guard on the strength of this invariant; it is the guard that holds,
   not this one." That instruction assumes the key cannot distinguish
   nodes. Once it can, a different and complete argument becomes
   available (decision 4), but the comment is then wrong and must be
   rewritten in the same commit that relaxes the guard, not left to
   contradict the code.

8. **`NetOp` is at `current_version = 2` and has no
   `_upgrade_step_1_to_2`, so loading a version-1 row raises
   `AttributeError` rather than the documented `UpgradeException`.**
   `shakenfist/baseobject.py:206-211` builds the step name and calls
   `getattr(self, step)` with no default, so the `if not step_func`
   branch below it is unreachable. Verified directly:

   ```
   AttributeError: 'Fake' object has no attribute '_upgrade_step_1_to_2'
   ```

   `NetOp.__init__` calls `self.upgrade(static_values)`
   (`shakenfist/operations/net_op.py:61`) and `_db_get`
   (`operations/baseoperation.py:213-218`) lets a version mismatch
   through because `upgrade_supported` is True. The window is narrow --
   cluster operations are hard deleted thirty seconds after going
   terminal, so only a rolling upgrade can present a version-1 row --
   but it is real, and this phase must bump `NetOp` to version 3, which
   walks straight through the hole. A sweep of every operation schema
   whose `current_version` exceeds its `initial_version` finds exactly
   one such gap, and it is this one; the script is in the Definition of
   done.

9. **`target_fields` is not a free place to declare the key.**
   `_coalescible_target_reference`
   (`shakenfist/operations/baseoperation.py:174-205`) resolves the
   object type for the fold's audit event through the schema model's
   `target_fields` map, but that same map is what
   `enqueue_cluster_operation`
   (`shakenfist/schema/operations/util.py:66-74`) iterates to write
   `cluster_operation_targets` rows. Adding `node_uuid` to it would
   start writing a NODE target row for every NetOp and NodeNetOp, which
   changes what `has_pending_cluster_operation()` reports for a node
   and grows a table the cleaner has to keep up with. The coalescing
   key therefore needs its own declaration. See decision 3.

### Corrections made at source

Both are in this planning commit, so no later step should redo them:

* The master plan's phase 11 section
  (`docs/plans/PLAN-queue-performance.md`) said the work needs
  `node_uuid` added to the model and the `(column, value)` list, and
  said nothing about the fold-time guard or about the dispatcher
  difference between `sf-net` and `sf-queues`. It now names all three
  guards and records why `network_apply_create_hypervisor` is deferred.
* The `docs/plans/index.md` row is updated for the phase-11 scope cut,
  so the index does not promise both halves of #3884.

#3884 itself is a GitHub issue and cannot be corrected in this commit.
Findings 2, 3 and 6 should be posted to it as a comment when this plan
is approved.

## Decisions

1. **New RPC names, not new fields on the existing messages.**
   `ClaimCoalescibleSiblingsRequest` and
   `FindExistingCoalescibleOpRequest`
   (`protos/database.proto:503-536`) each carry a scalar
   `target_column`/`target_uuid`. Adding a repeated field alongside
   them is wire-compatible in the protobuf sense and *unsafe in
   practice*: during a rolling upgrade a new client would send the
   extra pair to an old `sf-database`, which would ignore it and fold
   on the network alone -- exactly the cross-node corruption the guards
   exist to prevent, arriving silently. Instead add
   `ClaimCoalescibleSiblingsV2` / `FindExistingCoalescibleOpV2` taking
   `repeated CoalescibleKeyPair keys`. An old server answers
   `UNIMPLEMENTED`, which the client treats as "coalescing unavailable"
   and skips the fold -- a safe, loud, temporary loss of an
   optimisation rather than a quiet correctness failure. The old RPCs
   stay, unmodified, for one release.

2. **`NetOp` gains `node_uuid`, derived from `target` at enqueue, and
   goes to version 3.** `create_and_enqueue` already takes `target`,
   which for the per-node family *is* the node uuid, so the value is
   available without changing any call site. Set
   `node_uuid = target if target != 'networknode' else None` inside
   `create_and_enqueue`, with an explicit comment, rather than adding a
   parameter every caller would have to remember to pass -- a caller
   that forgot would produce an operation whose key silently degrades
   to the network alone. The alternative considered and rejected was
   moving `network_ensure_mesh` onto `NodeNetOp`, which already has
   both columns: it would force `network.py:319` and
   `instance.py:1131` to split their two-task lists into two operations
   joined by `runs_after`, changing ordering semantics on the instance
   start path for no gain this phase needs. The version bump also
   carries the missing `_upgrade_step_1_to_2` from finding 8, because
   the phase cannot add a 2-to-3 step and leave the 1-to-2 hole beneath
   it.

3. **The coalescing key is declared separately from `target_fields`.**
   Replace `coalescible_target_column: Optional[str]` with
   `coalescible_key_columns: tuple[str, ...] = ()`, read by the fold,
   by the enqueue-side dedup and by `_routing_key`.
   `_coalescible_target_reference` keeps resolving its audit-event
   reference through `target_fields` and keeps emitting against the
   network only, for the reason in finding 9: the event has to survive
   the operation, and the network is the object an operator queries.
   Nothing is added to `target_fields`.

4. **Both guards become key-aware; neither is deleted.** The
   enqueue-time guard changes from "a coalescible task may only be
   enqueued to `networknode`" to "a coalescible task may only be
   enqueued to a target its key distinguishes" -- concretely, a
   non-`networknode` target requires `node_uuid` in
   `coalescible_key_columns` *and* a non-`None` value on the
   operation. The fold-time guard changes from
   `queue_name.startswith('networknode-')` to "cluster-wide queue, or a
   per-node queue whose key includes `node_uuid`". The safety argument
   for the second case is complete and worth writing down in full,
   because it is what the rewritten comment at
   `daemons/network/workitem.py:48-91` has to say:
   a `(network_uuid, node_uuid)` key can only match operations carrying
   that node's uuid; every such operation is enqueued to that node's
   own `{node_uuid}-network-*` queue; that queue is drained by exactly
   one dispatcher process, that node's net-worker; and within that
   process every operation for the same network hashes to the same
   worker thread by `_routing_key`.

   Link two is narrower than it first looks, and implementing this
   step made that concrete. `enqueue_cluster_operation` builds the
   queue name as `{target}-{family}-{priority}`, so a node uuid in
   `target` puts the operation on `{node}-network-*` only when the
   caller also passes `family='network'`. The default family is
   `clusteroperation`, whose per-node queues go to `sf-queues` --
   where link four does not exist. A key naming `node_uuid` is
   therefore necessary for the fold to be safe but not sufficient:
   both guards test the family as well, the enqueue-time one directly
   and the fold-time one through the queue name's prefix. Reducing
   either to a test of the key alone reopens decision 5's race. So a fold can never mark complete
   an operation another thread is executing, which is precisely
   property (3) of the existing invariant, now holding for per-node
   queues as well as cluster-wide ones. Record the outcome
   `not_cluster_wide` under a new name (`key_cannot_distinguish_queue`)
   so the report keeps saying which guard fired, and add it to
   `COALESCE_OUTCOMES` in `tools/queue-wait-report.py:497-512`, whose
   completeness is already asserted by
   `test_every_outcome_the_code_records_is_reported`.

5. **`NodeNetOp.network_apply_create_hypervisor` is deferred, and this
   is the decision most likely to be argued with.** It is the cheaper
   half of #3884 -- the model already has both columns and no version
   bump is needed -- so cutting it looks like leaving value on the
   table. The reason is finding 6: decision 4's safety argument has
   four links, and the fourth (one worker thread per target within the
   draining process) does not exist in `sf-queues`. Two workers on one
   node can hold two operations for the same `(network, node)` at once.
   The failure is not merely duplicated work: worker A's fold can flip
   B's operation to `complete` in the window between B's
   `if op.state.value != STATE_QUEUED: return` check
   (`shakenfist/daemons/queues/workitem.py:181-182`) and B's own
   transition to `executing`, and `state_targets` has no
   `complete -> executing` edge. That race has never been exercised,
   because the fold has never run on a queue without partitioning.
   Making it safe means either partitioning the `sf-queues` pool by
   target or making dequeue-to-`executing` a single atomic
   transition -- both real pieces of work, both with a blast radius
   well beyond coalescing. File that as a successor issue and let it
   be scheduled on its own merits rather than smuggled in behind a
   mesh optimisation.

6. **No new index.** The generalised query filters
   `operation_type`, then `network_uuid`, then `node_uuid`. A composite
   `(network_uuid, node_uuid)` index would serve it slightly better,
   but `cluster_operations` is a hot insert path -- every operation in
   the cluster writes a row -- and `network_uuid` alone is already
   selective enough that the residual scan is a handful of pending rows
   per network. Phase 9 measured the existing query at a 3.7 ms median
   and a 154.8 ms maximum; step 8 re-measures with the wider key and
   the index decision is revisited only if that moves. Recording this
   as a decision rather than an omission, because `CLAUDE.md` asks for
   index review whenever a query changes.

7. **Functional coverage extends `test_coalescing.py` rather than
   adding a file.** The existing test
   (`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_coalescing.py`)
   already knows how to create contending instances on one network and
   how to read both coalescing signals off the event stream. A per-node
   assertion is a second test method in that class, sharing the
   fixture. A separate file would duplicate the setup and, more to the
   point, would not fail if someone deleted the shared helper.

8. **A key column with no value binds `IS NULL`, and that is the
   correct narrower semantics -- not a reason to skip the fold.**
   Recorded after step 11b, because it is the one place the original
   decisions were wrong and it would have turned this phase into a
   regression. `COALESCIBLE_KEY_COLUMNS` is a property of the operation
   class, so widening it to `('network_uuid', 'node_uuid')` in step 11d
   widens it for *every* NetOp -- including
   `network_apply_update_dnsmasq` and
   `network_apply_create_network_node`, the two tasks that actually
   fold today, which live on the cluster-wide queue and therefore carry
   `node_uuid = None`. Step 11a's preflight refuses a `None`-valued key
   column outright, so widening the tuple would have silently disabled
   the only coalescing the cluster does. It would have been logged and
   it would have been measured, but only after the fact.

   The fix is to bind `None` as `IS NULL` rather than refuse it. That
   is exactly right on both sides: a cluster-wide operation folds only
   other cluster-wide operations, because they are the ones whose
   `node_uuid` is also NULL, and a per-node operation folds only
   operations for its own node. Both are strictly narrower than
   today's network-only key, which is the property the whole phase
   rests on. The empty-key-list refusal stays -- with no equality at
   all the statement would fold everything -- and the protection
   against a caller who simply forgot to supply a value stays too, in
   `_coalescible_keys`'s `KeyError`: a missing dict entry is a bug, an
   explicit `None` is a decision.

   Proto3 has no null, so `CoalescibleKeyPair.uuid` being an empty
   string cannot mean "match NULL" unambiguously. Add a
   `bool is_null = 3` to the pair. The V2 messages have not shipped,
   so this is a free change now and would not be later.

9. **The extra NODE reference on the "operation created" audit event
   is accepted, not suppressed.** `enqueue_cluster_operation`
   (`shakenfist/schema/operations/util.py:101-112`) fans that event out
   to every metadata key ending in `_uuid` whose value is not `None`,
   so giving `NetOp` a `node_uuid` means a per-node mesh enqueue now
   also records the event against the node. This is worth a decision
   rather than a shrug, because event volume has bitten this project
   before. Three reasons to keep it. It is one extra `event_objects`
   row per per-node NetOp, which the six hour baseline in finding 1
   puts at roughly 150 an hour on `sfcbr`. `NodeNetOp` already behaves
   exactly this way, so the alternative is an inconsistency rather than
   a saving. And step 11d exists to *reduce* the number of these
   operations, so the net effect on volume is expected to be negative.
   Suppressing it would mean changing that `_uuid` scan, which would
   also change every other operation type -- a much larger blast radius
   than the thing being avoided.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 11a | high | opus | none | Generalise the coalescing key end to end, with **no behaviour change**: every caller passes a one-element key and every existing test still passes. In `protos/database.proto` add `message CoalescibleKeyPair { string column = 1; string uuid = 2; }` and two new RPCs `ClaimCoalescibleSiblingsV2` / `FindExistingCoalescibleOpV2` with request messages carrying `repeated CoalescibleKeyPair keys` in place of the scalar `target_column`/`target_uuid` at `protos/database.proto:503-536`; leave the existing RPCs and messages untouched. Regenerate with `tox -e genprotos` (never `grpc_tools.protoc` directly) and commit the stubs. In `shakenfist/mariadb.py` change `_direct_find_existing_coalescible_op` (`:22735`) and `_direct_claim_coalescible_siblings` (`:22821`) to take `keys: list[tuple[str, str]]`, validating every column against `_COALESCIBLE_TARGET_COLUMNS` (`:22648`, which already lists `node_uuid`) and emitting one `.where()` per pair; `_coalescible_preflight` (`:22651`) must validate and coerce every uuid in the list, keeping its existing log-on-skip behaviour, since #3878 is the reason it logs at all. Update the gRPC and public wrappers at `:23790` and `:23810` to match, routing to the V2 RPC and treating `grpc.StatusCode.UNIMPLEMENTED` as "coalescing unavailable" -- return `None` / `[]` and log a warning, exactly as the existing `OperationalError` paths do. Add the handlers to `shakenfist/daemons/database/main.py` beside `FindExistingCoalescibleOp` (`:248`) and `ClaimCoalescibleSiblings` (`:273`), and register their counters wherever the existing pair is registered. In `shakenfist/operations/baseoperation.py` replace the `coalescible_target_column: Optional[str]` class attribute (`:131`) with `coalescible_key_columns: tuple[str, ...] = ()`, updating its doc comment (`:123-130`), the read in `_coalescible_target_reference` (`:191` -- it keeps using the *first* column only, and keeps resolving through `target_fields`; do not add anything to `target_fields`) and the read in `execute` (`:407`). Set `NetOp.coalescible_key_columns = ('network_uuid',)` (`shakenfist/operations/net_op.py:58`). Update `_routing_key` in `shakenfist/daemons/network/workitem.py:102`, which currently does `type(op).coalescible_target_column or 'network_uuid'` -- it should use the first key column, or `'network_uuid'` when the tuple is empty. |
| 11b | high | opus | none | Give `NetOp` a `node_uuid` and bump it to version 3. In `shakenfist/schema/operations/net_op.py` add `node_uuid: Optional[UUID4] = None` to `model` (`:93`) and set `current_version = 3` (`:29`), leaving `initial_version = 1`. In `create_and_enqueue` (`:119`) derive the value as `node_uuid = None if target == 'networknode' else target`, with a comment saying that `target` *is* the node uuid for the per-node `network` family and that deriving it here rather than taking a parameter stops a caller silently degrading the key. Do **not** add `node_uuid` to `target_fields` (`:95`) -- see decision 3 and survey finding 9; it would start writing NODE rows into `cluster_operation_targets` via `schema/operations/util.py:66-74`. In `shakenfist/operations/net_op.py` add a `node_uuid` property alongside `network_uuid` (mirror the pair on `NodeNetOp` at `shakenfist/operations/node_net_op.py:56-64`), add `_upgrade_step_2_to_3` setting `node_uuid` to `None`, and add the **missing** `_upgrade_step_1_to_2` (a no-op body with a comment: the 1-to-2 bump only added optional fields, but `shakenfist/baseobject.py:206-211` calls `getattr(self, step)` with no default so its absence raises `AttributeError`, not the documented `UpgradeException` -- survey finding 8). Both steps are `@classmethod`s taking `static_values`, matching `shakenfist/operations/agentoperation.py:89`. No MariaDB migration is needed: `cluster_operations.node_uuid` already exists and is indexed (`shakenfist/mariadb.py:2069,2076`) and `_direct_create_cluster_operation` (`:21010`) extracts it from the metadata dict on its own. |
| 11c | high | opus | none | Make both guards key-aware without deleting either. Behaviour must still be unchanged at the end of this step, because no task is coalescible on a per-node queue yet. Enqueue-time guard, `shakenfist/schema/operations/net_op.py:157-165`: replace "a coalescible task may only go to `networknode`" with "a coalescible task may only go to a target its key distinguishes" -- for `target != 'networknode'`, raise `InvalidCoalescibleEnqueue` unless `'node_uuid'` is in the operation class's `coalescible_key_columns` *and* the derived `node_uuid` is not `None`. Keep the message pointing at #3884's successor rather than at #3884. Fold-time guard, `shakenfist/operations/baseoperation.py:461-464`: replace `queue_is_cluster_wide` with a predicate that also admits a per-node queue when `'node_uuid'` is in the key columns and the operation's `node_uuid` is set. Rename the recorded outcome `not_cluster_wide` to `key_cannot_distinguish_queue` at `:486` and in `COALESCE_OUTCOMES` in `tools/queue-wait-report.py:497-512`; `test_every_outcome_the_code_records_is_reported` already asserts that list is complete, and the tool must keep parsing the old name out of retained history, so map it rather than dropping it. Then rewrite the safety-invariant comment at `shakenfist/daemons/network/workitem.py:48-91`, which currently says in terms "Do not weaken that guard on the strength of this invariant" -- state decision 4's four-link argument in full (key distinguishes node -> operation is on that node's own queue -> that queue is drained by exactly one process -> within it, one worker thread per network), and say explicitly that the argument does **not** extend to `sf-queues`, which has no routing key (survey finding 6). |
| 11d | medium | opus | none | Flip it on: add `model_tasks.network_ensure_mesh` back to `COALESCIBLE_TASKS` (`shakenfist/schema/operations/net_op.py:82-86`) and set `NetOp.coalescible_key_columns = ('network_uuid', 'node_uuid')` (`shakenfist/operations/net_op.py:58`). Rewrite the long comment block at `schema/operations/net_op.py:63-81` -- it currently explains at length why the task is *not* in the set -- to say what the key is now and what it guarantees. Also rewrite the comment at `:135-155` in `create_and_enqueue`, which asserts "cluster_operations has no queue column to filter on. That is only sound while every coalescible task lives on the single cluster-wide network-node queue". Check `shakenfist/tests/schema/test_net_op_coalescing.py`, which contains a static walk of every `net_create_and_enqueue` call site and will have opinions about this. |
| 11e | medium | sonnet | none | Unit coverage for the three new behaviours. In `shakenfist/tests/test_mariadb_coalescing.py`, cover a two-pair key: a sibling matching on network but not node is *not* folded, one matching both *is*, and a malformed uuid in either position skips the query with a log rather than raising. In `shakenfist/tests/operations/test_baseoperation.py`, cover the new fold-time predicate: per-node queue plus a node-aware key runs the fold, per-node queue plus a network-only key records `key_cannot_distinguish_queue`, and cluster-wide is unchanged. In `shakenfist/tests/schema/operations/test_net_op.py`, cover the derived `node_uuid` (set for a node target, `None` for `networknode`), the version-3 range acceptance, and both upgrade steps. Add the sweep from the Definition of done as a test so no operation schema can bump `current_version` again without its step. Every assertion that claims to prove a fix must be mutation-tested: revert the fix, confirm the test fails, restore. Report which ones you mutation-tested. |
| 11f | high | opus | none | Functional CI coverage. Extend `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_coalescing.py` with a second test method asserting a fold happened on a *per-node* queue, reusing the existing fixture and the two event constants at `:12-20`. The shape to aim for: create enough contending instances on one network that `Network.ensure_mesh`'s per-node fan-out (`shakenfist/network/network.py:979-990`) puts more than one `network_ensure_mesh` operation on one node's queue at once, then assert a `coalesced sibling ops` event whose `extra` names that task. Note the existing test's own comment about sequential creates leaving nothing to coalesce (`:68`) -- that trap applies here too. Read the phase 9 plan's decision 2 for why the event is emitted against the network as well as the operation, and assert against the network: an operation is hard deleted thirty seconds after going terminal and takes its `event_objects` rows with it (#3864). |
| 11g | medium | sonnet | none | Documentation and close-out. Update `docs/developer_guide/database_internals.md`'s coalescing section for the multi-column key and the renamed outcome. Update `CLAUDE.md`'s Common Pitfalls only if a *convention* changed. Fill in this plan's Results section, set the master plan's Execution row and the `docs/plans/index.md` row to Complete, and add a Future work entry for the deferred `sf-queues` half of #3884 naming the successor issue. Do not write measured numbers yet -- step 11h supplies them. |
| 11h | medium | sonnet | none | **Deferred until `sfcbr` has run the merged build for at least 24 hours.** Re-run `tools/queue-wait-report.py` over a window of at least 24 hours and record, in this plan's Results and in the master plan's "What step 11 measured": the `net_op` fold outcome counts before and after, how many siblings the per-node fold actually collapses, and whether the fold's duration distribution moved with the wider key (decision 6's index question turns on this). Also read `SHOW ENGINE INNODB STATUS` / `information_schema` for lock waits and deadlocks on `cluster_operations`: cross-node fold contention is a failure mode the pre-change baseline could not have contained, and duration alone cannot close decision 6 -- see the note in Results. Compare against the six hour pre-change baseline in survey finding 1. Two traps this plan has already paid for: `sfcbr` stamps local time with a `Z` suffix so a window read off the log records is ten hours out from the window Loki was asked for (phase 10 withdrew a whole finding to this), and every window must be fetch-verified against `count_over_time` with no chunk sitting at Loki's 5000 line ceiling. |

## Risks and mitigations

* **The fold marks complete an operation another worker is about to
  execute.** This is the failure mode decision 5 cuts `sf-queues` out
  to avoid, and the whole safety argument for `sf-net` rests on the
  four links in decision 4. *Mitigation:* the argument is written into
  the code at `daemons/network/workitem.py:48-91` in step 11c, so the
  next person to change routing or queue draining meets it. The
  management session verifies in review that all four links are stated
  and that each is true of the code as it stands, not as the comment
  wishes it were. Step 11f's functional test is the empirical half.

* **A rolling upgrade folds across nodes.** A new client talking to an
  old `sf-database` is the one path where the wider key could be
  silently dropped. *Mitigation:* decision 1's separate RPC names turn
  that into `UNIMPLEMENTED`. The management session checks in review
  that the client's `UNIMPLEMENTED` path skips the fold rather than
  falling back to the V1 RPC -- a fallback would reintroduce exactly
  the failure the new name exists to prevent, and is the obvious wrong
  thing for an implementer to write.

* **The measured win is small.** Phase 9 found the existing fold nearly
  inert (7 matches in 1,335 attempts) and this phase could land the
  same way. Survey finding 1 says otherwise -- 573 of 919 per-node
  operations arrive in a multi-operation batch -- but that is an upper
  bound, not a count. *Mitigation:* step 11h measures and reports
  honestly either way. The phase is not justified on throughput alone:
  it removes the reason two of the three guards exist, and #3878 is the
  standing evidence that a silently-inert special case here survives
  months.

* **`network_ensure_mesh` is not as idempotent as everyone believes.**
  The fold is only sound if running it once covers every folded
  sibling. `_apply_ensure_mesh` diffs this host's FDB against the
  current set of participating hypervisors, so a later snapshot
  subsumes an earlier one -- but that is the claim, and it has never
  been tested under folding. *Mitigation:* step 11f asserts the mesh is
  correct *after* a fold, not merely that a fold happened.

* **`test_net_op_coalescing.py`'s static call-site walk fights the
  change.** It was written in phase 8 to enforce the convention this
  phase relaxes. *Mitigation:* step 11d names it explicitly. It should
  be updated to enforce the *new* rule (a coalescible task on a
  per-node target must have a node-aware key), not deleted.

## Definition of done

* `network_ensure_mesh` is in `COALESCIBLE_TASKS` and
  `NetOp.coalescible_key_columns == ('network_uuid', 'node_uuid')`.
* No operation schema has a `current_version` above its
  `initial_version` without every intervening upgrade step. Falsifiable
  as written, and currently returns exactly one offender
  (`net_op.py`, `_upgrade_step_1_to_2`):

  ```
  python3 -c "
  import glob, os, re, sys
  missing = []
  for p in sorted(glob.glob('shakenfist/schema/operations/*.py')):
      src = open(p).read()
      lo = re.search(r'^initial_version = (\d+)', src, re.M)
      hi = re.search(r'^current_version = (\d+)', src, re.M)
      if not (lo and hi) or lo.group(1) == hi.group(1):
          continue
      op = os.path.join('shakenfist/operations', os.path.basename(p))
      osrc = open(op).read() if os.path.exists(op) else ''
      for v in range(int(lo.group(1)), int(hi.group(1))):
          step = '_upgrade_step_%d_to_%d' % (v, v + 1)
          if step not in osrc and step not in src:
              missing.append((os.path.basename(p), step))
  print(missing)
  sys.exit(1 if missing else 0)"
  ```

* `grep -rn "coalescible_target_column" shakenfist/ --include=*.py`
  returns nothing -- the single-column attribute is gone from the
  operation layer, not merely shadowed.
* `grep -rn "node_uuid" shakenfist/schema/operations/net_op.py` shows
  it on `model` and **not** inside `target_fields`.
* Every outcome `BaseClusterOperation.execute` can record appears in
  `tools/queue-wait-report.py`'s `COALESCE_OUTCOMES`, asserted by the
  existing `test_every_outcome_the_code_records_is_reported`, and the
  tool still renders the retired `not_cluster_wide` name out of
  retained history.
* A functional CI run shows a `coalesced sibling ops` event naming
  `network_ensure_mesh`, recorded against the network, and the network
  is verified correct afterwards.
* The safety-invariant comment at
  `shakenfist/daemons/network/workitem.py` states the four-link
  argument and says explicitly that it does not extend to `sf-queues`.
  No statement about what makes the fold safe is written differently in
  that comment, in `schema/operations/net_op.py`, and in
  `_direct_claim_coalescible_siblings`'s docstring.
* A successor issue exists for the `sf-queues` half of #3884, naming
  the two-worker race in decision 5 concretely enough to be actioned
  without re-deriving it, and #3884 carries a comment recording survey
  findings 2, 3 and 6. Both done: the successor is #4017, and the
  comment is
  https://github.com/shakenfist/shakenfist/issues/3884#issuecomment-5499964712.
* `pre-commit run --all-files` is clean, and proto stubs were
  regenerated with `tox -e genprotos` and committed.
* Step 11h has recorded measured numbers, or this plan says explicitly
  that it is outstanding and why.

## Back brief

Before executing any step of this plan, please back brief the operator
as to your understanding of the plan and how the work you intend to do
aligns with that plan.

Two gates beyond the usual back brief, both cheap to agree and
expensive to redo:

* **Before step 11a**, agree decision 1. New RPC names mean proto
  churn and a deprecation to remember; the alternative is one new
  repeated field and a rolling-upgrade hazard. If the operator prefers
  the field, the phase still works but 11a and its risk row change
  shape, and the change must not be started twice.
* **Before step 11c**, agree the four-link safety argument in decision
  4 as written. It is the load-bearing claim of the whole phase, it
  contradicts a comment that currently tells the reader not to do
  this, and discovering a hole in it after 11c and 11d have landed
  means unwinding both.

## Results

Steps 11a-11g are done. Step 11h (the `sfcbr` re-measurement) is
outstanding -- see below.

**What was built.** The coalescing key generalised from a single
`(column, value)` pair to a tuple of them
(`coalescible_key_columns`), read by both the enqueue-side dedup and
the worker-side fold, and by `_routing_key`. `NetOp` gained
`node_uuid` (derived from `target` inside `create_and_enqueue`, never
taken as a parameter) and moved to version 3, which also added the
`_upgrade_step_1_to_2` that finding 8 showed was missing since the
version 2 bump. New V2 gRPC RPCs (`ClaimCoalescibleSiblingsV2` /
`FindExistingCoalescibleOpV2`) carry a repeated `CoalescibleKeyPair`
in place of the V1 RPCs' scalar pair, so a rolling upgrade against an
old `sf-database` answers `UNIMPLEMENTED` and skips the fold rather
than silently folding on the network alone. Both the enqueue-time and
fold-time guards became key-aware and family-aware rather than being
deleted, `network_ensure_mesh` is back in `COALESCIBLE_TASKS` with the
key `(network_uuid, node_uuid)`, and the `not_cluster_wide` outcome
was renamed `key_cannot_distinguish_queue` (`tools/queue-wait-report.py`
still maps the old name out of retained log history). Verified end to
end against a real database, not mocks: a node A survivor folds only
node A's sibling and leaves node B's queued, and narrowing the key
back to the network alone reproduces the phase 8 bug exactly. Unit
coverage is 26 tests (`8418ad3d6`), functional CI asserts a per-node
fold and verifies the mesh is correct afterwards (`7bbbb57d6`).

**Two mid-phase corrections, both recorded honestly rather than
folded silently into the step that found them.**

* **Decision 8: a `None` key value must bind `IS NULL`, not be
  refused.** `coalescible_key_columns` is a property of the operation
  *class*, so widening it to `('network_uuid', 'node_uuid')` in step
  11d widened it for every `NetOp` -- including the two cluster-wide
  tasks that already fold today, which carry `node_uuid = None`.
  Step 11a's original preflight refused a `None`-valued key column
  outright. Left as originally decided, step 11d would have silently
  disabled the only coalescing the cluster currently does: it would
  have been logged and it would have been measured, but only after
  the fact, by someone looking at a graph that had quietly gone flat.
  The fix, made in `41a3cf670`/`075e17627` and recorded as decision 8
  after being found in review of step 11b, binds `None` as `IS NULL`
  instead -- exactly the narrower semantics the whole phase needs: a
  cluster-wide operation folds only other cluster-wide operations,
  and a per-node operation folds only its own node's. Proto3 has no
  null, so `CoalescibleKeyPair` carries an explicit `is_null` bool
  rather than overloading an empty string.

* **A key naming `node_uuid` is necessary but not sufficient -- the
  queue family decides which dispatcher drains the work.** Also found
  in review, before step 11c was committed rather than after: the
  first draft of both guards tested only whether the key could
  distinguish nodes, not which dispatcher would actually claim the
  operation. `enqueue_cluster_operation` builds the queue name as
  `{target}-{family}-{priority}`, so a node uuid in `target` reaches
  `sf-net`'s per-node `{node}-network-*` queue only when the caller
  also passes `family='network'`; the default `clusteroperation`
  family routes the same target to `sf-queues`, which has no
  per-worker routing key at all. A key-only guard would have let a
  hypothetical future per-node `clusteroperation`-family enqueue pass
  both checks while being unsafe to fold. Both guards were tightened
  to test the family as well as the key before `075e17627` was
  committed -- the enqueue guard directly, the fold guard through the
  queue name's prefix -- and the `PARTITIONED-WORKER SAFETY INVARIANT`
  comment in `shakenfist/daemons/network/workitem.py` states the
  four-link argument this depends on in full, including why it stops
  at `sf-net` and does not reach `sf-queues`.

**A third defect found on the way, in test infrastructure rather than
production code.** `shakenfist/tests/mock_mariadb.py`'s coalescing
preflight still refused a `None` key value after `41a3cf670` reversed
that behaviour in the real `mariadb.py`. With the widened key, every
cluster-wide `NetOp` carries a `None` `node_uuid`, so the mock would
have silently reported "no coalescing" for every one of them --
mock-based assertions in step 11e would have measured nothing rather
than failing loudly, which is the worse of the two failure modes for
a test double. Found and fixed in `a597dc127`, in the same commit
that turned mesh folding on, because that is the step whose
verification depended on the mock behaving correctly.

**Step 11h is outstanding.** It requires `sfcbr` to have run the
merged build for at least 24 hours, which had not yet happened when
this close-out step ran. The only real numbers available are still
the pre-change baseline from survey finding 1 (a six hour `sfcbr`
window, before any of this phase's code existed): 1,510 `net_op`
samples, the fold ran 263 times and folded 4 siblings, 581 were
refused by the per-node-queue guard, and on the per-node `network`
family lane 573 of 919 operations (62%) dequeued alongside at least
one sibling -- the ceiling on what a per-node fold could collapse.
Whether the wider key actually reaches that ceiling, and whether the
fold's duration distribution moved enough to revisit decision 6's
no-new-index call, is unmeasured. Nothing in this Results section
should be read as reporting a post-change number; there isn't one
yet.

**What 11h must additionally look for, from the PR #4007 review.**
Decision 6 declined a composite `(network_uuid, node_uuid)` index on
the evidence of the pre-change fold durations -- but that baseline
*cannot* contain the new failure mode, because before this phase only
the single elected network node ever ran a fold for a given network.
Now every participating hypervisor does.
`_direct_claim_coalescible_siblings` issues `SELECT ... FOR UPDATE`,
and under REPEATABLE READ a range scan of `ix_cluster_ops_network` for
one network takes next-key locks on every index record it examines,
not only the ones whose `node_uuid` also matches. So per-node folds
for the same network on different hosts can now serialise against each
other, and can deadlock with the `UPDATE` which follows. Separately,
the cluster-wide case binds `node_uuid IS NULL`, which the optimiser
could choose to serve from `ix_cluster_ops_node` -- scanning every
NULL-node row in the table rather than the handful for one network.

So 11h measures lock waits and deadlocks on `cluster_operations`, not
only the fold's own duration. If either shows up, the composite index
makes the `FOR UPDATE` lock only the rows which actually match, and
pins the plan. Do not close decision 6 on duration alone.

## Future work

* **The `sf-queues` half of #3884 remains deferred**, per decision 5.
  `NodeNetOp.network_apply_create_hypervisor`'s model already carries
  both `network_uuid` and `node_uuid`, so no schema change would be
  needed to give it the same two-column key this phase gave `NetOp`
  -- but `sf-queues` is a `WorkerPoolDaemon` with no per-target
  routing key, so the fourth link of the safety argument (one worker
  thread per target within the draining process) does not hold there.
  Two of its workers can hold two operations for the same `(network,
  node)` at once, and one's fold can flip the other's operation to
  `complete` in the window between that worker's own
  `if op.state.value != STATE_QUEUED: return` check
  (`shakenfist/daemons/queues/workitem.py:181-182`) and its transition
  to `executing` -- `state_targets` has no `complete -> executing`
  edge. Making it safe means either partitioning the `sf-queues`
  worker pool by target, mirroring `sf-net`'s `_routing_key`, or
  making dequeue-to-`executing` a single atomic transition; both are
  real pieces of work with a blast radius beyond coalescing, and
  should be scoped and decided on their own merits.

  This is filed as **#4017**, which states the race above concretely
  and names both ways out. The `InvalidCoalescibleEnqueue` message in
  `shakenfist/schema/operations/net_op.py` cites that number rather
  than promising an issue in the abstract. #3884 also carries the
  comment recording survey findings 2, 3 and 6 that the plan's
  "Corrections made at source" section called for.

* **Step 11h**, the `sfcbr` re-measurement described above, once the
  merged build has run for at least 24 hours.
