# Cluster op visibility phase 4: API surface

This is a detailed phase plan under
[PLAN-cluster-op-visibility.md](PLAN-cluster-op-visibility.md).
Phases 2 and 3 made `last_cluster_operation` and the pending query
ignore observational work. This phase exposes the pending answer to
API clients, makes the per-object operation listings report every
operation that targets the object rather than one pointer's
neighbourhood, and rebuilds the instance delete walk on the same
query.

**Planning effort:** high (per the master plan). **Review effort:**
high. The code is small, but it changes what three public responses
say, adds a capability token clients will match on, extends a proto
message, and changes which operations an instance delete cancels.

## Scope

In scope:

* `has_pending_operations` in every external view that already
  carries `last_cluster_operation`, and the capability token that
  advertises it.
* The three per-object listings,
  `GET /instances|networks|artifacts/<ref>/clusteroperations`,
  rebuilt on the targets table, with each operation's stored
  `observational` flag.
* A `pending_only` filter on
  `get_cluster_operation_targets_for_object()`, so that the
  default listing and the delete walk filter on state in SQL.
* The instance delete walk (`Instance.enqueue_delete()`), rebuilt on
  that listing.
* Swagger examples and descriptions for all of the above, the API
  reference pages, the developer-guide note, and the release note.
* A client-python pull request adding the accessors the docs
  already claim exist, and a helper that waits on the new field.

Out of scope, deliberately:

* The CI await helpers in `shakenfist/deploy/shakenfist_ci/base.py`.
  Phase 5 switches them to the new field. Nothing in this phase
  changes what they poll.
* The four remaining `runs_after=[obj.last_cluster_operation]` sites.
  The survey records them (finding 8) for the master plan's Future
  work, but each is a separate ordering question.
* Interfaces. `NetworkInterface` is a plain `DatabaseBackedObject`,
  carries no pointer and gains no field (decision 10).
* Errored operations never being hard deleted (#3922), and a failing
  sweep task erroring its instance (#4585). Both bear on what the
  listings show, and both stay with their issues.

## What the survey found

The survey ran on 2026-10-22 against `develop` at `3aa55390fc`. The
master plan's premises hold. Several of its facts do not, and two
findings change the shape of the phase: the endpoint paths are
wrong, and the "about thirty seconds" lifetime of terminal ops that
the plan leans on for `all=true` is not what the code does.

1. **Three views carry the pointer, and only three.**
   `DatabaseBackedObjectWithOperations` (`shakenfist/baseobject.py:766`)
   has exactly three subclasses: `Instance`, `Network` and
   `Artifact`. Their views put the pointer at
   `shakenfist/instance.py:747`, `shakenfist/network/network.py:349`
   and `shakenfist/artifact.py:597`. The artifact one is in
   `external_view_without_index()` (`artifact.py:588`), which
   `external_view()` calls and which the snapshot listing calls
   directly (`external_api/snapshot.py:125`). `Blob` is a plain
   `DatabaseBackedObject` (`blob.py:94`), so the blob checksum
   endpoint that phase 3 decision 3 worries about never gains the
   field. The interface swagger example
   (`external_api/interface.py:39`) shows a `last_cluster_operation`
   that `NetworkInterface.external_view()`
   (`network/interface.py:202`) has never rendered.
2. **The list endpoints already pay for the pointer per object.**
   `InstancesEndpoint.get` (`external_api/instance.py:571`, loop at
   594), `NetworksEndpoint.get` (`external_api/network.py:209`,
   loop at 230) and `ArtifactsEndpoint.get`
   (`external_api/artifact.py:401`, loop at 421) render one full
   view per object. Each view already issues one
   `GetLatestClusterOperationTarget` among several other reads.
   `has_pending_operations` adds one `HasPendingClusterOperationTarget`
   per view: the same count, against the same index. Measured on
   sfcbr with `sf-db-load series` (2026-10-19 to 21, daily means):
   `GetLatestClusterOperationTarget::api` 2.4 to 2.9 requests a
   second, against 14 for `GetObjectState::api`. The field therefore
   adds about 2.5 requests a second at today's traffic, and
   `HasPendingClusterOperationTarget::api` does not appear in the
   series at all today, so the infra report's load ratchet will see
   it as a new, unbaselined pair after deploy.
3. **The endpoints are `/clusteroperations`, not `/operations`.** The
   routes are `/artifacts/<artifact_ref>/clusteroperations`,
   `/instances/<instance_ref>/clusteroperations` and
   `/networks/<network_ref>/clusteroperations`
   (`external_api/app.py:563`, `:659`, `:697`). The handlers are
   `InstanceOutstandingOperationsEndpoint`
   (`external_api/instance.py:2517`),
   `NetworkOutstandingOperationsEndpoint` (`network.py:1057`) and
   `ArtifactOutstandingOperationsEndpoint` (`artifact.py:1049`), each
   a five-line loop over `get_cluster_operations()` rendering
   `op.external_view()`. Their swagger examples
   (`instance.py:2479`, `network.py:1042`, `artifact.py:1032`) are
   lists nested inside a list, which the handlers never return.
4. **`get_cluster_operations()` is not newest-first today.**
   `baseobject.py:793` hydrates the pointer's op, then one level of
   its `depends_on`, then one level of its `runs_after`. So the
   order is "the pointer, then what it waits on", and the walk
   includes ops that target other objects (a network reconcile op an
   instance start depends on, for example). Its only callers are the
   three handlers. Two unit tests patch it by name with the keyword
   `outstanding_only`: `test_boolean_sweep.py:130-155` (a spy) and
   `test_outstanding_operations_all.py:80-86` (which returns a
   `MagicMock` op and asserts the response is exactly
   `[{'uuid': 'fake-op'}]`).
5. **`get_cluster_operation_targets_for_object()` returns every row,
   oldest first.** Direct at `mariadb.py:5061`, gRPC at `:5398`,
   public at `:5569`, handler at `daemons/database/main.py:5603`,
   request message at `protos/database.proto:2469` (two fields). It
   is unfiltered, carries each row's `observational` flag and
   `operation_type`, and its one production caller is the per-blob
   sweep's duplicate guard (`daemons/cluster/scheduled_tasks.py:434`),
   which hydrates every row and keeps the ones that are
   `is_outstanding()`. Its gRPC wrapper returns `[]` on an RPC error
   (`mariadb.py:5413`). `has_pending` fails closed instead
   (`mariadb.py:5464`).
6. **Terminal ops outlive "about thirty seconds".**
   `_deleted_object_delay()` (`scheduled_tasks.py:844`) makes an op
   *eligible* for hard delete thirty seconds after it reaches a final
   state, but the sweep that deletes it,
   `per_deleted_object_checks()`, runs every fifteen minutes
   (`daemons/cluster/main.py:1070`) with a ten-second budget. A
   finished op therefore normally lingers for up to about fifteen
   minutes. `error` is not in `FINAL_OBJECT_STATES`
   (`constants.py:204`, #3922), so an errored op is never hard
   deleted. Its target rows go only when the
   `CLUSTER_OPERATION_TARGET_RETENTION` prune (seven days) removes
   them. Two consequences:
   * `all=true` returns in-flight ops, finished ops from about the
     last fifteen minutes, and errored ops from the last seven days.
     It is bounded, so the master plan's "no limit parameter" stands,
     but it is not "thirty seconds of terminal ops".
   * Hydrating every target row to find the outstanding ones costs
     one op read and one state read per row, and an object with a
     run of errored ops carries a week of them. That is why the
     default listing and the delete walk filter on state in SQL
     (decision 4) rather than in Python.
7. **The delete walk reaches past the instance.**
   `Instance.enqueue_delete()` (`instance.py:2753`) hydrates the
   pointer's op and walks `traverse_cluster_operations_tree()`
   (`instance.py:186`, no other callers) recursively through
   `depends_on` and `runs_after`, stopping at ops that are not
   `queued` or `executing`. It aborts everything it reaches except
   ops with an `instance_snapshot` task, then enqueues
   `instance_delete` with `runs_after=[self.last_cluster_operation]`
   (`instance.py:2800`). Three things follow:
   * Because the walk follows dependencies, it aborts network
     reconcile ops (`network_ensure_mesh`, the dnsmasq refresh, the
     hot-plug's reconcile op) that an instance start or hot-plug
     depends on. Those target only the network
     (`schema/operations/net_op.py:200`).
   * Every op an instance's own work creates does target the
     instance: the disk fetches (`artifact_fetch_op` declares
     `instance_uuid`, `schema/operations/artifact_fetch_op.py:33`, and
     `enqueue_disk_fetches()` passes it at `instance.py:1507`), the
     start, create, hot-plug, snapshot and delete ops.
   * The delete waits only for the pointer. A snapshot that is not
     the newest mutating op (a hot-plug enqueued after it, say) is
     protected from abort but not waited for, so the delete can run
     under it.

   An abort does not interrupt a running task: `execute()` checks the
   op's state after each task and returns if it was aborted
   (`operations/baseoperation.py:664`). No unit test covers the
   walk.
8. **Six `runs_after` sites, re-located.** The master plan's line
   numbers are stale:

   | Site | Enqueues | Pointer at enqueue time |
   |------|----------|-------------------------|
   | `network/network.py:330` | create on network node and ensure mesh, in `Network.new()` | Always `None`: the network was minted lines earlier. Inert. |
   | `instance.py:2800` | `instance_delete` | Rebuilt in this phase (decision 7). |
   | `instance.py:2913` | `instance_snapshot` | Newest mutating instance op. |
   | `external_api/instance.py:1285` | instance start | The newest of the disk fetches just enqueued. |
   | `external_api/instance.py:1449` | interface hot-plug | Newest mutating instance op. |
   | `external_api/artifact.py:509` | artifact fetch | Newest fetch for an existing artifact. |

   Each of the four that stay serialises behind the newest mutating
   op, which is right only when that op was itself chained behind
   the older ones. Disk fetches, redirect starts and `instance_create`
   are enqueued without `runs_after`, so they are not. That is
   recorded for Future work. Beyond these sites, the only readers of
   the pointer are the three views, `get_cluster_operations()` and
   the delete walk. Nothing in the scheduler or cleaner reads it,
   which answers the audit the master plan's Future work asks for.
9. **The client has no per-object accessors at all.** client-python
   (`develop` at `d789e0b`) has `check_capability()`
   (`shakenfist_client/apiclient.py:350`, a substring test on the root
   page), `get_cluster_operation()` (`:2060`),
   `get_cluster_operation_chain()` and
   `list_cluster_operations_for_target()` (`:2135`), and nothing for
   the three per-object listings. The functional test
   `TestOutstandingOperationsBodyAll`
   (`deploy/shakenfist_ci/cluster_ci_tests/test_api.py:15`) calls
   them with `_request_url` for that reason. Yet the API reference
   pages give Python examples calling
   `get_instance_queued_cluster_operations()` and its network and
   artifact twins (`docs/developer_guide/api_reference/instances.md:1199`,
   `networks.md:458`, `artifacts.md:526`), which do not exist and
   never have (`git log -S` finds nothing in client-python). The
   same three sections say finished ops are removed after
   `CLEANER_DELAY` seconds, one hour by default, which finding 6
   contradicts. The nearest client pattern is
   `get_instance_agentoperations(instance_ref, all=False)`
   (`apiclient.py:873`).
10. **Capabilities are a pinned dict.** `API_CAPABILITIES` in
    `external_api/app.py:390` maps families to tokens, and
    `tests/external_api/test_root.py` pins it exhaustively. The
    `cluster-operations` family holds `get-cluster-operations`,
    `cluster-operation-chain` and `cluster-operations-by-target`.
    `object-pending-operations` is kebab-case, is not a substring of
    any existing token, and no existing token is a substring of it,
    which matters because clients match by substring.
11. **The test doubles need two additions.** `MockMariaDB`
    (`tests/mock_mariadb.py`) mocks
    `get_latest_cluster_operation_target` and
    `get_cluster_operation_targets_for_object` (around lines 984-996,
    implementations at 3467-3495) but not
    `has_pending_cluster_operation_target`, so every view rendered
    under it would otherwise reach the real function. Its target
    store is keyed by operation UUID, so it holds one target per op
    and cannot represent a multi-target op. The sqlite behaviour
    fixture in `tests/test_observational_current_state.py` runs the
    real direct layer over the cluster_operations, object_states,
    work_queue and targets tables, and is the right home for the
    tests that claim to prove the listing.
12. **No new RPC is needed.** `HasPendingClusterOperationTarget` and
    `GetClusterOperationTargetsForObject` exist with gRPC paths. The
    only proto change is one optional request field (decision 4),
    which an older sf-database ignores.
13. **Docs and checks.** The per-object API reference sections are
    `instances.md:1180`, `networks.md:439` and `artifacts.md:507`.
    The by-target listing and the flag are described in
    `api_reference/clusteroperations.md:22-50`, and the two read
    shapes in `developer_guide/database_internals.md:342-355`. The
    release note is `docs/release_notes/v07-v08.md`, whose phase 3
    section (line 1380) says "A delete no longer cancels a queued
    billing and health sweep" (line 1403), which decision 6 makes
    false again. No request parameter is added, so
    `check-api-parameter-locations` is unaffected, but the pre-commit
    hooks `check-plan-phase-references` (no plan phase numbers in
    `docs/` outside `docs/plans/`) and `check-doc-anchors` apply to
    the docs step.
14. **What phase 5 needs.** The capability token, and the field on
    `GET /instances/<ref>` and `GET /networks/<ref>`. Its helpers can
    read both through `check_capability()`, `get_instance()` and
    `get_network()`, which the client already has, so phase 5 does
    not wait on the client pull request.

Corrections at source: the master plan's phase 4 section, Situation,
mission, open questions, success criteria and Future work have been
corrected for findings 3, 4, 6, 7, 8 and 9 in this planning commit, so a
later step need not redo them. Its phase 5 section gains finding 14.
The many code comments and docs outside this phase's pages that say
"about thirty seconds" (finding 6) are left alone and filed as #4603.
The artifact listing endpoint's missing `@api_base.log_token_use`,
which its instance and network twins have, is filed as #4604.

## Decisions

1. **The field goes wherever the pointer goes, lists included.**
   `has_pending_operations` is rendered beside
   `last_cluster_operation` in the instance, network and artifact
   views, including `Artifact.external_view_without_index()`. The
   master plan's fallback, restricting it to single-object GETs, is
   not needed. The list endpoints already pay one indexed target
   query per object for the pointer, and the new field adds one more
   of the same shape (finding 2), at about 2.5 requests a second on
   sfcbr today. The artifact listing's swagger promises "the same
   output as a GET for a single artifact would show", and a field
   that appears on one and not the other is a trap for clients.
   A batched query for N objects is not warranted: it needs the
   prefetch plumbing that `api-query-batching-roadmap.md` proposes and
   has never landed, for a saving of the same size as the pointer's
   own cost, which nobody has asked to batch. If the load ratchet
   later flags `HasPendingClusterOperationTarget::api` as a real
   problem, the cheaper fix is a single RPC that answers latest and
   pending together, holding the per-view count where it is today.
   That is recorded as Future work. The field reads
   `has_pending_cluster_operation()`, which returns `False` for an
   in-memory object and fails closed (`True`) when the database cannot
   answer.

2. **One token, `object-pending-operations`, in the
   `cluster-operations` family.** It advertises two things that ship
   together: the field, and that the per-object `/clusteroperations`
   listings are built on the targets table and carry `observational`.
   One token, not one per object family, because all three views gain
   the field in one change and a client never needs to tell them
   apart. The family is `cluster-operations` rather than `instances`,
   because the token describes cluster operations across object types.
   The name keeps the master plan's proposal.

3. **A listing reports the operations that target the object, newest
   first, and nothing else.** `get_cluster_operations()` is rebuilt
   on `get_cluster_operation_targets_for_object()`. It hydrates one op
   per target row, ordered by `sequence_number` descending, which is
   the order `last_cluster_operation` already uses, so the newest
   mutating op in an `all=true` listing is the pointer. It no longer walks
   `depends_on` or `runs_after`, so an instance's listing stops
   showing the network ops its start waits on. Those target the
   network and appear in the network's listing, and
   `GET /clusteroperations/<uuid>/chain` exists to trace what an op
   waits on. An op that has been hard deleted since its row was read,
   or whose type no longer has a class, is skipped silently, as
   `_hydrate_op()` does (`external_api/clusteroperation.py:86`).
   `outstanding_only` keeps its meaning (`is_outstanding()`) and its
   keyword name, which two tests patch by.

   It returns a list of `TargetedOperation(op, observational)` named
   tuples, defined in `baseobject.py`, carrying the stored flag from
   the target row, as phase 2 decision 6 asked of every reader. A new
   `cluster_operation_views(outstanding_only)` method renders them as
   `op.external_view()` plus `observational`, and the three handlers
   become one call each.

4. **The default listing filters on state in SQL.**
   `get_cluster_operation_targets_for_object()` gains
   `pending_only: bool = False` at all three layers. When true, the
   direct query joins `object_states` on `operation_uuid` and keeps
   `_ACTIVE_OPERATION_STATES`, exactly as
   `_direct_has_pending_cluster_operation_target()` does, so the
   listing and the field agree on what "pending" means.
   `GetClusterOperationTargetsForObjectRequest` gains
   `bool pending_only = 3`. An older sf-database ignores the field
   and returns every row. That is safe, because
   `get_cluster_operations()` still checks `is_outstanding()` on each
   hydrated op. The check is needed anyway, for an op that finishes
   between the query and the read. Only the cost differs during a
   roll. `get_cluster_operations(outstanding_only=True)` passes
   `pending_only=True`, and `all=true` passes `False`. The blob sweep
   keeps its unfiltered call, because changing it is not this phase's
   business and its load is negligible (0.3 requests a second on
   sfcbr). This is the one place the phase touches a proto, and it is
   the reason: without it, the default listing and every delete would
   hydrate a week of errored ops to find the in-flight ones (finding
   6), and the plan's success criteria ask for the filter in SQL.

5. **Observational ops are listed, flagged, not hidden.** The
   per-object listing answers "what is queued against this object",
   and a queued billing sweep is queued against it. Hiding it would
   make this listing disagree with the by-target listing for no
   gain. The flag lets a client draw the line itself, and it makes
   one invariant testable:
   `has_pending_operations` is true exactly when the default listing
   contains an op with `observational: false`, apart from races
   between the two reads. A unit test pins that invariant.

6. **Delete cancels every outstanding op that targets the instance,
   observational ones included, except snapshots.** The rebuilt walk
   is `get_cluster_operations(outstanding_only=True)`. That is the
   same query the listing uses, so there is one definition of "what is
   outstanding for this instance" and no Python-side flag filter. It
   aborts each op except those with an `instance_snapshot` task.

   Either answer about observational ops is safe, because phase 3 made
   both sweep tasks return early for an instance that is not
   `created`. Cancelling them is chosen because the sweep then does
   not run at all against an instance being deleted. A queued sweep
   never dispatches, and an executing one stops at its next task
   boundary (finding 7). That leaves phase 3's guard as a backstop
   rather than the only defence, and the abort is visible in the
   instance's events, which says why the sweep did not run. The cost
   is one state write and one event per sweep op, of which an instance
   has at most one or two.

   The walk no longer aborts ops that target only other objects,
   which is the larger behaviour change and the decision a reviewer
   is most likely to dispute. The old walk reached the network
   reconcile ops an instance's start or hot-plug depended on, and
   aborted them. Those ops reconcile the whole network for every
   instance on it, and net ops coalesce, so aborting one because one
   instance is going away is wrong, not just wasteful. Everything an
   instance's own work enqueues does target it (finding 7), including
   its disk fetches, so nothing the delete should cancel is lost. A
   reviewer who wants the old reach back would have to argue for
   cancelling shared network work, which is the case against it.

7. **The delete waits for what it chose not to cancel.** The
   `instance_delete` op's `runs_after` becomes every protected
   snapshot op the walk left running, plus the pointer re-read at
   enqueue time, as today. The pointer stays for the race it already
   covered: a mutating op enqueued between the walk and the delete's
   own enqueue is the newest mutating op, so the delete still waits
   for it. A pointer that the walk just aborted is terminal and costs
   nothing. This fixes finding 7's third point, where a snapshot that
   was not the pointer could be deleted out from under. An op whose
   abort raises `InvalidStateException` had already reached a
   terminal state, so it is not waited for. `traverse_cluster_operations_tree()`
   then has no callers and is deleted.

8. **`all=true` is documented as what it is.** The handlers' swagger
   description and the API reference say it adds finished operations
   that have not yet been removed: finished ops normally go within
   about fifteen minutes, and errored ones stay for up to
   `CLUSTER_OPERATION_TARGET_RETENTION`, seven days by default. It is
   not a history and the docs do not call it one. No limit parameter
   is added, because the result is bounded by that retention.

9. **The client pull request follows the server one, and nothing
   waits on it.** It adds `get_instance_clusteroperations()`,
   `get_network_clusteroperations()` and
   `get_artifact_clusteroperations()`, each `(ref, all=False,
   namespace=None)`, mirroring `get_instance_agentoperations()` and
   gated on the existing `instance-clusteroperations`,
   `network-clusteroperations` and `artifact-clusteroperations` tokens,
   so they work against today's servers. It also adds
   `await_no_pending_operations(object_type, object_ref,
   namespace=None, timeout=..., interval=1)`, which polls the object's
   GET until `has_pending_operations` is false and raises
   `IncapableException` when `object-pending-operations` is not
   advertised. The server merges first, because it defines the token,
   the field and the response shape that review may still change.
   Phase 5 reads the field through methods the client already has
   (finding 14), so it does not wait for this pull request either. The
   server docs name the three new methods. The DoD checks that the
   client pull request merged with exactly those names.

10. **Interfaces keep no pointer.** The interface swagger example
    loses the `last_cluster_operation` its view has never had.
    Interfaces are targets of hot-plug ops, so a pending field on them
    could be useful one day. Nothing consumes it today, and adding the
    pointer and the field to `NetworkInterface` is a change of its own
    (Future work).

11. **Documentation names behaviour, not history.** The release note
    is a new `##` section in `v07-v08.md`. It names the field, the
    token, the listing's new contents and order, the `observational`
    key, and the delete changes of decisions 6 and 7. It also rewrites
    the phase 3 section's delete bullet, which this phase makes false.
    The API reference gains a short "Waiting for an object to settle"
    section in `clusteroperations.md`, which says that observational
    work, a checksum verification for example, never makes an object
    pending, so a caller waiting for a requested blob hash polls the
    checksum endpoint instead. That honours phase 3 decision 3. The
    per-object sections drop the one-hour claim and name the real
    client methods. `ARCHITECTURE.md`, `AGENTS.md` and `README.md` do
    not change. Phase 5 owns the storage notes.

## Step plan

Run the steps in order, one at a time, in this worktree. Pre-commit's
stash is repo-wide, so concurrent agents in one tree can revert each
other. Each server step is one commit. Step 4f is a separate pull
request in client-python.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | **The field and the token.** Read decisions 1, 2 and 10 and survey findings 1, 2, 10 and 11 of `docs/plans/PLAN-cluster-op-visibility-phase-04-api-surface.md` first. (1) Add `'has_pending_operations': self.has_pending_cluster_operation()` directly after the `'last_cluster_operation'` entry in `Instance._build_external_view()` (`shakenfist/instance.py:747`), `Network.external_view()` (`shakenfist/network/network.py:349`) and `Artifact.external_view_without_index()` (`shakenfist/artifact.py:597`). Do not add it to `baseobject._external_view()`, which every object type uses. (2) In `shakenfist/external_api/app.py`, append `'object-pending-operations'` to the `cluster-operations` family of `API_CAPABILITIES` (line 430), with a two-line comment saying it advertises both the field and that the per-object `/clusteroperations` listings report every op targeting the object with an `observational` key (step 4c makes that true; the token ships in this commit but the PR ships both). Update `EXPECTED_CAPABILITIES` in `shakenfist/tests/external_api/test_root.py` to match. (3) Add `"has_pending_operations": false,` beside `"last_cluster_operation"` in the swagger examples at `external_api/instance.py:122`, `external_api/network.py:104` and `external_api/artifact.py:256`, keeping each example's key order alphabetical. Delete the `last_cluster_operation` block from the interface example at `external_api/interface.py:39`, which `NetworkInterface.external_view()` has never rendered. (4) `shakenfist/tests/mock_mariadb.py` mocks `get_latest_cluster_operation_target` (around line 991, implementation around 3483) but not `has_pending_cluster_operation_target`. Add a mock beside it, with the same start/addCleanup pattern, whose implementation returns True iff some row from `_mariadb_get_cluster_operation_targets_for_object()` is not observational and its op's state in `self.mariadb_states` (key `f'{op_type}/{op_uuid}'`; check how ops' states are keyed there, and what the mock's object type string for an op is) is one of `queued`, `preflight` or `executing`. (5) Unit tests: each of the three views has the key, and its value is what `has_pending_cluster_operation()` returns (patch it True and False); an in-memory artifact reports False without a database call; the token is advertised on the root page. Run every test module that renders one of these views under `MockMariaDB` and fix any that compare whole view dicts: `grep -rln "external_view()" shakenfist/tests` is the starting list. Run `tox -e py3`, then `pre-commit run --all-files`. Commit subject: "Expose has_pending_operations on object views." |
| 4b | medium | sonnet | none | **Filter targets to pending ones in SQL.** Read decision 4 and survey findings 5, 6 and 12 first; the `add-grpc-service` skill describes this repo's three-layer pattern. Add `pending_only: bool = False` to `_direct_get_cluster_operation_targets_for_object` (`shakenfist/mariadb.py:5061`), `_grpc_get_cluster_operation_targets_for_object` (`:5398`) and the public `get_cluster_operation_targets_for_object` (`:5569`). When true, the direct query joins `_get_object_states_table()` on `targets.operation_uuid == states.object_uuid` and adds `states.state_value.in_(_ACTIVE_OPERATION_STATES)`, copying the join in `_direct_has_pending_cluster_operation_target` (`:5154`); it does **not** filter on `observational`. Keep the ascending `sequence_number` order. Update the docstrings, including the existing comment about the blob sweep, which still passes nothing. Add `bool pending_only = 3;` to `GetClusterOperationTargetsForObjectRequest` (`protos/database.proto:2469`) with a comment: an older sf-database ignores it and returns every row, so callers must still check each op's state. The gRPC wrapper sets it; the handler `GetClusterOperationTargetsForObject` (`shakenfist/daemons/database/main.py:5603`) passes `request.pending_only` through. Regenerate with `tox -e genprotos`; never run protoc directly. Extend the `MockMariaDB` implementation `_mariadb_get_cluster_operation_targets_for_object` (`shakenfist/tests/mock_mariadb.py`, around 3467) to accept `pending_only=False` and apply the same state filter, reusing what step 4a's has_pending mock does; its callers pass the kwarg through `side_effect`, so a signature without it raises `TypeError`. Tests, in `shakenfist/tests/test_observational_current_state.py` (real direct layer on sqlite): queued, executing, complete and error ops against one instance, with `pending_only=True` returning only the first two and the default returning all four; an observational queued op is returned by `pending_only=True` with its flag set. In the existing gRPC round-trip tests nearby, assert the wrapper sends the field and the handler forwards it. Mutation-test: drop the state predicate and confirm the sqlite test fails; restore from a scratchpad copy, never `git checkout`. If `shakenfist/tests/test_mariadb_cluster_operation_targets_live.py`'s `EXPLAIN` harness extends naturally, add the `pending_only` query to it and run it as its docstring says against `mariadb:10.11`; report whether `idx_cot_target` is used. `pre-commit run --all-files`. Commit subject: "Filter op targets to pending ones in SQL." |
| 4c | high | opus | none | **Make the per-object listings truthful.** Read decisions 3, 5 and 8 and survey findings 3, 4 and 6 first. In `shakenfist/baseobject.py`: define `class TargetedOperation(NamedTuple)` with fields `op` and `observational: bool`, and rewrite `DatabaseBackedObjectWithOperations.get_cluster_operations(self, outstanding_only=True)` (line 793) to: return `[]` for an in-memory object; call `mariadb.get_cluster_operation_targets_for_object(self.object_type, str(self.uuid), pending_only=outstanding_only)`; walk the rows in **descending** `sequence_number`; for each, `get_object_class(row.operation_type)` (skip on `NoSuchObject`), `.from_db(row.operation_uuid, suppress_failure_audit=True)` (skip `None`), skip it if `outstanding_only and not op.is_outstanding()`, and append `TargetedOperation(op, row.observational)`. Keep the explanatory comment about best-effort hydration. Do not walk `depends_on` or `runs_after`. Add `cluster_operation_views(self, outstanding_only=True)` returning `[{**t.op.external_view(), 'observational': t.observational} for t in self.get_cluster_operations(outstanding_only=outstanding_only)]`; it must call `get_cluster_operations` with the keyword `outstanding_only`, which `tests/external_api/test_boolean_sweep.py:130-155` spies on. Make the three handlers (`external_api/instance.py:2545`, `external_api/network.py:1085`, `external_api/artifact.py:1076`) return `<obj>.cluster_operation_views(outstanding_only=(not all))`. Swagger, in all three: make each example (`instance.py:2479`, `network.py:1042`, `artifact.py:1032`) a flat list rather than a list inside a list, add `"observational": false` to each op, and in the instance example add a queued `node_inst_op` with tasks `collect_billing_statistics` and `health_check_kvm_process` and `"observational": true`; reword the `all` parameter to say it adds finished operations not yet removed (normally within about fifteen minutes, errored ones for up to seven days) and is not a history; reword the 200 description to "operations targeting this ..., newest first". Update `tests/external_api/test_outstanding_operations_all.py:80-86` so its patched `get_cluster_operations` returns `[TargetedOperation(fake_op, False)]` and the expected body includes `'observational': False`. New tests in a new module `shakenfist/tests/test_object_cluster_operations.py`, built on the sqlite fixture of `tests/test_observational_current_state.py` (factor its `setUp` and helpers into a shared mixin there if that is cleaner than copying, but keep that module's tests passing unchanged); use a minimal `DatabaseBackedObjectWithOperations` stand-in with `object_type = ObjectType.INSTANCE` and a fixed uuid rather than a full `Instance`. Ops are enqueued through the real `node_inst_op` and `node_inst_net_iface_op` schema models so that `from_db` can hydrate them; if hydration needs metadata the fixture's `_enqueue` does not set (for example `version`), build the metadata from the real schema model instead of mocking `from_db`. Cases: (a) the master plan's success criterion: a queued mutating op, then a later mutating op set `complete`; the default listing returns the queued one, and `all=True` returns both, newest first. (b) A queued observational sweep is listed with `observational: True`. (c) The invariant of decision 5, over a few mixes: `has_pending_cluster_operation()` equals `any(not v['observational'] for v in cluster_operation_views())`. (d) An op whose `cluster_operations` row is deleted after its target row is read is skipped without an audit event. (e) A target row whose `operation_type` has no class is skipped. (f) A network-only op the instance op depends on does not appear in the instance's listing. Mutation-test and report: revert the walk to the old pointer-based body and confirm (a) and (f) fail; drop `pending_only=` and confirm the tests still pass (the Python check is the backstop) and that a test counting `from_db` calls for a terminal row fails; reverse the order and confirm (a) fails. Restore from a scratchpad copy. `pre-commit run --all-files`. Commit subject: "List every outstanding op targeting an object." |
| 4d | high | opus | none | **Rebuild the instance delete walk.** Read decisions 6 and 7 and survey findings 7 and 8 first. Rewrite the cancellation part of `Instance.enqueue_delete()` (`shakenfist/instance.py:2753`): `targeted = self.get_cluster_operations(outstanding_only=True)`; for each `t.op`, if any task is `niso_tasks.instance_snapshot` add it to a `survivors` list, otherwise emit the existing `add_event_multi(..., 'task aborted due to enqueued delete request')` and set `op.state = bco.STATE_ABORT`, keeping the existing `InvalidStateException` handler (an op that raises there has already gone terminal, so it is not a survivor). Then enqueue `instance_delete` with `runs_after=[{'op_type': op.object_type, 'op_uuid': str(op.uuid)} for op in survivors] + [self.last_cluster_operation]`. These are plain dicts in the pointer's own shape, not `dependency` objects: `runs_after` goes through `_convert_deps()` (`schema/operations/baseclusteroperation.py:114`), which reads `op['op_type']` and `op['op_uuid']` and skips `None` entries (so a null pointer is fine). Check that `op.object_type` is the string `ObjectType(...)` accepts. Replace the "I don't love this approach" comment with one saying what is cancelled, what is protected and waited for, and that ops on other objects (network reconcile work) are left alone because other instances share them; do not cite plan phases or decisions in code. Delete `traverse_cluster_operations_tree()` (`instance.py:186`), which then has no callers (`grep -rn traverse_cluster_operations_tree shakenfist` must print nothing). Unit tests in a new `shakenfist/tests/test_instance_enqueue_delete.py`, patching `Instance.get_cluster_operations` to return `TargetedOperation`s over `MagicMock` ops with real task enums, and patching `nio_create_and_enqueue` and `add_event_multi` where `instance.py` imports them: (a) a queued hot-plug op and a queued sweep op (observational) are both aborted; (b) a snapshot op is not aborted and appears in `runs_after`, alongside the pointer; (c) with a snapshot op and a newer aborted hot-plug op, `runs_after` still names the snapshot (the case the old code got wrong); (d) an op whose abort raises `InvalidStateException` is not in `runs_after`; (e) nothing outstanding gives `runs_after` of just the pointer; (f) `get_cluster_operations` is called with `outstanding_only=True`. Also one test on the sqlite fixture from step 4c, if it can build enough of an `Instance` cheaply; otherwise say so in the report rather than mocking half of `Instance`. Mutation-test and report: drop the survivors from `runs_after` and confirm (b) and (c) fail; skip observational ops and confirm (a) fails; abort snapshot ops too and confirm (b) fails. Restore from a scratchpad copy. `pre-commit run --all-files`. Commit subject: "Rebuild the instance delete cancellation walk." |
| 4e | medium | sonnet | none | **Document it.** Read decisions 1-3, 5-9 and 11 and survey findings 6, 9 and 13 first. No plan phase numbers in anything under `docs/` outside `docs/plans/` (the `check-plan-phase-references` hook enforces it). (1) `docs/developer_guide/api_reference/clusteroperations.md`: in "Looking up cluster operations" (line 22) say the per-object sub-resources are `/clusteroperations` on artifacts, instances and networks, list the operations targeting the object newest first, carry `observational`, and with `all` add finished ops not yet removed (finding 6's numbers: normally within about fifteen minutes, errored ops for up to `CLUSTER_OPERATION_TARGET_RETENTION`, seven days by default; not a history). Add a section "Waiting for an object to settle" describing `has_pending_operations` on instance, network and artifact views: true while any non-observational op targeting the object is queued or executing, true when the server cannot tell, gated on the `object-pending-operations` capability; that observational work such as a checksum verification never makes an object pending, so a caller waiting for a requested blob hash polls `GET /blobs/<uuid>/checksums` (check the real path in `docs/developer_guide/api_reference/blob_checksums.md`) instead; and a short Python example using `check_capability('object-pending-operations')` and `get_instance()`. (2) `instances.md:1180`, `networks.md:439`, `artifacts.md:507`: replace the `CLEANER_DELAY` sentence with a link to the section above, and rename the examples' `get_*_queued_cluster_operations` to `get_instance_clusteroperations`, `get_network_clusteroperations` and `get_artifact_clusteroperations` with sensible variable names (they currently all say `img_uuid`). (3) `docs/developer_guide/database_internals.md:342-355`: add that `external_view()` exposes `has_pending_cluster_operation()` as `has_pending_operations`, and a third bullet for `get_cluster_operations()`: target rows for the object, newest first, filtered to pending ops in SQL by default, each with its stored flag, and the delete walk built on it. (4) `docs/release_notes/v07-v08.md`: a new `##` section in the file's style covering decision 11's list, including that an instance's listing no longer shows other objects' ops it depends on, and that the infra report will see a new `HasPendingClusterOperationTarget` caller from the API. Rewrite the bullet at line 1403 ("A delete no longer cancels a queued billing and health sweep"), which is now false, so that it describes what a delete does with sweeps now. Run `python3 tools/check-doc-anchors.py`, `tox -e docs` if it builds locally, then `pre-commit run --all-files`. Commit subject: "Document pending operations on objects." |
| 4f | medium | sonnet | worktree | **client-python accessors (separate repository and pull request).** Start only after the server pull request has merged (decision 9), in a new worktree of `/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python` on a branch named `cluster-op-visibility-accessors` from `develop`. Read decision 9 and survey finding 9 first. In `shakenfist_client/apiclient.py`, beside `get_instance_agentoperations()` (line 873) and its network and artifact neighbours, add `get_instance_clusteroperations(self, instance_ref, all=False, namespace=None)`, `get_network_clusteroperations(self, network_ref, all=False, namespace=None)` and `get_artifact_clusteroperations(self, artifact_ref, all=False, namespace=None)`. Each checks its existing token (`instance-clusteroperations`, `network-clusteroperations`, `artifact-clusteroperations`) and raises `IncapableException` otherwise, then sends `GET /<type>s/<ref>/clusteroperations` with `data={'all': all}` plus `namespace` when given, matching how `get_instance(instance_ref, namespace=None)` sends it. Add `await_no_pending_operations(self, object_type, object_ref, namespace=None, timeout=None, interval=1)`, beside the other `await_*` helpers (line 1797), for `object_type` in `instance`, `network` and `artifact`: raise `IncapableException` unless `check_capability('object-pending-operations')`; poll the matching `get_*` until `has_pending_operations` is false and return the view; on timeout raise the client's existing timeout exception type for awaits (read `await_instance_create()` and follow it), defaulting the timeout the way `_poll_cluster_operation()` does. Unit tests in `shakenfist_client/tests/test_client_apiclient.py`, following the `list_cluster_operations_for_target` tests near line 692: each wrapper's URL, body and capability refusal; the helper's poll-until-false, refusal and timeout. Add the four methods to the client's docs or changelog if the repo keeps one. Run the client repo's own pre-commit and tests. Then confirm the three method names equal those in the server's `docs/developer_guide/api_reference/` pages. Commit subject: "Add per-object cluster operation accessors." |
| 4g | n/a | management | n/a | After each step: read the changed files rather than the summary, re-run the step's mutations rather than trusting the report, run `pre-commit run --all-files`, check the commit against the master plan's review checklist, and commit. After 4e, push and watch the PR's CI, including the live-MariaDB job and the smoke suites (`ci-status shakenfist/shakenfist runs --branch cluster-op-visibility-phase-04-api-surface`). Offer the PR; do not open it unasked. Step 4f's PR is likewise offered, not opened. |

## Risks and mitigations

* **A delete stops cancelling something it should.** Decision 6
  narrows the walk to ops that target the instance. *Mitigation:*
  survey finding 7 traced every op an instance's work enqueues, and
  each targets the instance. Step 4d's tests pin the cases, and the
  management session reads `instance.py`'s enqueue sites again in 4g
  against `target_fields` before committing. The smoke suites' delete
  tests (instances deleted mid-create, mid-hot-plug and mid-snapshot)
  run on the PR.
* **A delete now waits longer.** Waiting for every protected snapshot
  rather than only the pointer can hold a delete behind a slow
  snapshot it used to overtake. That is the point, since overtaking
  deletes the disks being snapshotted. *Mitigation:* `runs_after`
  already gives up after `DEPENDENCY_DEFER_BUDGET` deferrals
  (`daemons/queues/workitem.py:181`), so a wedged snapshot cannot hold
  a delete forever. The release note says so.
* **Mixed-version roll.** A new sf-api against an older sf-database
  sends `pending_only`, which is ignored. *Mitigation:*
  `get_cluster_operations()` keeps its `is_outstanding()` check, so
  the result is the same and only the cost differs. Step 4c's
  mutation run proves the backstop. No RPC is added or removed, so
  there is no `UNIMPLEMENTED` path.
* **The load ratchet flags a new pair.**
  `HasPendingClusterOperationTarget::api` appears at about 2.5
  requests a second (finding 2). *Mitigation:* expected, and named in
  the PR description and the release note, so the infra report's
  `unbaselined_high_rate` entry is triaged as known rather than
  chased. Decision 1 records the combined-RPC fix if it is ever
  needed.
* **The listing fails open.** On a database error,
  `get_cluster_operation_targets_for_object()` returns `[]`, so a
  listing can say "nothing outstanding" when it does not know. That
  is unchanged from today's pointer path, which returns `None` on
  error. *Mitigation:* the docs point waiting clients at
  `has_pending_operations`, which fails closed. The listing is a
  view, not a gate.
* **A whole-view test breaks on the new key.** *Mitigation:* step 4a
  greps for tests that render these views and runs the full suite
  before committing.
* **The client pull request drifts from the server docs.**
  *Mitigation:* decision 9 orders the PRs, and the DoD checks the
  names on both sides.
* **A functional test relied on the old walk.**
  `TestOutstandingOperationsBodyAll` asserts that `all=True` reveals
  a completed network op right after the network settles. The new
  listing returns more finished ops than the old walk did, not fewer,
  and finished ops live for at least thirty seconds. *Mitigation:*
  the smoke suites run it on the PR.

## Definition of done

Each item is checkable from the tree or from a command. Every grep
below was run at planning time, and gave the "now" result noted.

- [ ] Every view that renders the pointer renders the field. This
      prints nothing (now it names `instance.py`, `network.py` and
      `artifact.py`):

      ```
      for f in $(grep -rl "'last_cluster_operation': self.last_cluster_operation" shakenfist --include=*.py); do
          grep -q "'has_pending_operations'" $f || echo "missing: $f"
      done
      ```
- [ ] `grep -n "'object-pending-operations'" shakenfist/external_api/app.py shakenfist/tests/external_api/test_root.py`
      prints one line from each file (now nothing).
- [ ] `grep -n "traverse_cluster_operations_tree\|last_op = self.last_cluster_operation\|lco = self.last_cluster_operation" shakenfist/instance.py shakenfist/baseobject.py`
      prints nothing (now six lines).
- [ ] `grep -n "pending_only" protos/database.proto` prints the new
      field (now nothing), and `tox -e genprotos` leaves no diff.
- [ ] `grep -c "last_cluster_operation" shakenfist/external_api/interface.py`
      prints `0` (now `1`).
- [ ] `grep -A1 'outstanding_operations_example = """\[' shakenfist/external_api/*.py | grep -c -- '-    \[$'`
      prints `0`, so no swagger example nests a list in a list (now
      `3`).
- [ ] `grep -rn "CLEANER_DELAY seconds" docs/developer_guide/api_reference/ | wc -l`
      prints `0` (now `3`).
- [ ] `grep -rn "queued_cluster_operations" docs shakenfist | wc -l`
      prints `0` (now `6`).
- [ ] `grep -n "no longer cancels a queued billing" docs/release_notes/v07-v08.md`
      prints nothing (now line 1403), and the file has a section
      naming `has_pending_operations` and `object-pending-operations`.
- [ ] `tox -e py3 -- shakenfist.tests.test_object_cluster_operations shakenfist.tests.test_instance_enqueue_delete shakenfist.tests.test_observational_current_state shakenfist.tests.external_api`
      passes.
- [ ] The mutations listed in steps 4b, 4c and 4d each fail the tests
      they name. This was demonstrated in 4g, not assumed.
- [ ] `pre-commit run --all-files` passes.
- [ ] The smoke suites and the live-MariaDB job are green on the PR.
- [ ] In client-python, after step 4f merges,
      `grep -c "def get_instance_clusteroperations\|def get_network_clusteroperations\|def get_artifact_clusteroperations\|object-pending-operations" shakenfist_client/apiclient.py`
      prints at least `4` (now `0`), and every name printed by
      `grep -rhoE "sf_client\.get_[a-z_]*cluster_?operations" docs`
      in this repository is one of the three methods (now it prints
      the three nonexistent `get_*_queued_cluster_operations`).
- [ ] The master plan's Execution table reads `In progress` for phase
      4 with this file linked, and its phase 4 section, Situation,
      open questions, success criteria and Future work carry the
      corrections listed under the survey. That is done in this
      planning commit.

## Back brief

Before step 4a, back brief the operator on how you understand this
phase and the order of the steps, as the master plan requires. Two
gates follow, each for something cheap to propose and expensive to
undo:

* **Before step 4a, confirm decisions 2 and 6.** The token name is a
  contract that released clients will match on, and the token list is
  append-only in practice (`external_api/app.py:346`). Decision 6
  changes which work a delete cancels, in both directions. Proposing
  either costs nothing. Changing one after the PR merges means a
  second token, or a second behaviour change in the release notes.
* **Before step 4f, confirm the client method names and the helper's
  signature.** They become client API the moment they are released,
  and the server docs name them.

Step 4b's proto field is not gated: it is optional, additive, and
ignored by older services, so getting it wrong costs a follow-up
commit, not a migration.
