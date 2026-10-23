# Truthful cluster operation visibility

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(KVM/libvirt, VXLAN networking, MariaDB/Galera, gRPC/protobuf),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/baseobject.py` (object lifecycle and state
machine), `shakenfist/mariadb.py` (three-layer database
access pattern), `shakenfist/schema/` (Pydantic models), and
`shakenfist/daemons/database/main.py` (gRPC database daemon).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table like this in this master plan under the
Execution section.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

On 2026-07-19 the smoke test
`test_interface_plug_and_exec_reboot` failed on PR #3462 (run
29668325279) with "Interface not found in `sudo lshw -class
network` output:" followed by *empty* output. Forensics from
the CI bundle established the following sequence:

* 01:35:54.9 -- the test POSTed the interface hot-plug. The
  API handler (`external_api/instance.py`,
  `InstanceInterfacesEndpoint.post`) synchronously enqueued
  the net reconcile op and the `hot_plug_instance_interface`
  op, with `cluster_operation_targets` rows correctly written
  for the instance.
* 01:35:55.0 -- the test's
  `_await_instance_operations_complete()` helper
  (`deploy/shakenfist_ci/base.py`) polled the instance's
  `last_cluster_operation`, saw the hot-plug op `queued`, and
  kept waiting. Correct so far.
* Between polls, the cluster daemon's per-instance sweep
  (`daemons/cluster/scheduled_tasks.py`,
  `_process_per_instance_queue`) enqueued a routine
  `node_inst_op` (`collect_billing_statistics`,
  `health_check_kvm_process`) against the same instance. It
  completed within seconds and became the instance's
  `last_cluster_operation`, shadowing the still-queued
  hot-plug op.
* 01:36:05.0 -- the helper's second poll fetched the billing
  op, saw `complete`, and returned. The hot-plug chain had
  not even started executing (it ran at 01:36:10, delayed
  behind its mesh-op dependency).
* 01:36:16 -- the test ran `sudo lshw -class network` in the
  guest. The instance is created with **no** NICs, so empty
  output with rc 0 is exactly correct for a guest whose
  hot-plug has not happened yet.
* 01:36:18 -- one second after the test failed and tore the
  instance down, the attach op finally executed and logged
  `libvirt: Domain Config error : Requested operation is not
  valid: domain is not running`.

The underlying defect is the **single-pointer
`last_cluster_operation` pattern**.
`PLAN-replace-last-cluster-operation.md` (all phases
complete) already moved the *internal gating* callers --
`Network.is_okay()` and siblings -- onto the history-aware
`has_pending_cluster_operation()` query, and its docstring
(`baseobject.py:685`) names this exact race: "a later
terminal op against the same object would mask an earlier
in-flight op". This plan is that plan's successor: several
consumer groups were out of its scope and still use the racy
pointer:

1. **CI await helpers.**
   `_await_instance_operations_complete()`
   (`deploy/shakenfist_ci/base.py:522`) and
   `_await_network_operations_complete()` (`base.py:546`)
   poll `last_cluster_operation` and inspect only that op's
   state.
2. **The "outstanding operations" REST endpoints.**
   `GET /instances/<ref>/clusteroperations`
   *(path corrected in phase 4; the plan first said `/operations`)*
   (`external_api/instance.py`,
   `InstanceOutstandingOperationsEndpoint`, plus network and
   artifact twins) are built on
   `baseobject.get_cluster_operations()` (`baseobject.py:722`),
   which seeds from `last_cluster_operation` and walks only
   that op's `depends_on`/`runs_after` edges. During the
   flake window it would have returned `[]` -- the endpoint
   lies precisely when a background op has shadowed the
   pointer.
3. **The `runs_after` serialisation chains.** Operations on
   an object are *not* implicitly serial; the dispatcher
   (`daemons/queues/workitem.py`) only defers behind ops
   listed in `depends_on`/`runs_after`. Six enqueue sites
   opt in by passing
   `runs_after=[obj.last_cluster_operation]`
   (`network/network.py:321`, `instance.py:1829`,
   `instance.py:1925`, `external_api/artifact.py:375`,
   `external_api/instance.py:863`,
   `external_api/instance.py:1026`). A background op
   permuting the pointer between two user mutations makes the
   second mutation chain behind the (instantly complete)
   measurement op instead of the first mutation -- a genuine
   server-side ordering bug, independent of CI.

One endpoint is already history-aware:
`GET /clusteroperations?target_object_type=&target_uuid=`
(`external_api/clusteroperation.py`,
`ClusterOperationsEndpoint`, added in phase 7 of
`PLAN-network-facade.md`; client wrapper
`list_cluster_operations_for_target()` behind the
`cluster-operations-by-target` capability). It joins
`cluster_operation_targets` against `cluster_operations` and
returns every op that has targeted the object, newest-first,
including state. It is, however, heavyweight for polling: it
hydrates every historical op (one DB round trip per op, with
history growing over the object's lifetime).

Design decisions already made in discussion with the
operator:

* We will **not** serialise background measurement ops behind
  the object's op chain (`runs_after`). Health checks should
  be timely; the sweep would pile deferrals onto exactly the
  instances that are busiest; and enqueue-time chaining is
  not atomic anyway, so it cannot deliver a strict guarantee.
* Instead we adopt a **mutating vs observational** split:
  measurement-only ops (billing statistics, KVM health
  checks, blob checksum verification) still write
  `cluster_operation_targets` rows for audit history, but are
  excluded from `get_latest_cluster_operation_target()` and
  `has_pending_cluster_operation_target()`. Observational
  ops therefore no longer permute `last_cluster_operation`
  nor count as "pending".
* We will expose a cheap `has_pending_operations` boolean in
  `external_view()` for operation-bearing objects, backed by
  the existing EXISTS query.
* We will make the "outstanding operations" endpoints
  truthful by reimplementing `get_cluster_operations()` over
  the targets table.

Useful implementation reference points collected during
forensics. **These line numbers are stale.** Phase 2's survey
(2026-10-18) re-located all of them; use the *What the survey
found* section of
[the phase 2 plan](PLAN-cluster-op-visibility-phase-02-observational-schema.md)
instead. Two of the facts below were also wrong by then, and are
corrected inline.

* Targets table definition:
  `mariadb._get_cluster_operation_targets_table()`
  (`mariadb.py:1085`), currently schema version 2; migration
  in `_ensure_cluster_operation_targets_schema()`
  (`mariadb.py:1136`); version constant
  `CLUSTER_OPERATION_TARGETS_VERSION` registered in
  `EXPECTED_SCHEMA_VERSIONS` (`mariadb.py:253`).
* Three-layer accessors for targets: direct at
  `mariadb.py:3605-3810`, gRPC at `mariadb.py:3942-4059`,
  public at `mariadb.py:4141-4248`. Proto RPCs in
  `protos/database.proto:268-276`, message
  `ClusterOperationTargetProto` at `database.proto:1850`.
  Handlers in `daemons/database/main.py`. Regenerate stubs
  with `tox -e genprotos`.
* *(Corrected in phase 2.)* Target rows are written inside
  the atomic enqueue transaction
  (`_direct_create_and_enqueue_cluster_operation`), from the
  `targets` list that `enqueue_cluster_operation()` derives
  from `model_class.target_fields`. Before phase 2 there were
  two more writers: a redundant post-enqueue write in
  `node_blob_op.create_and_enqueue()`, and the dead
  `baseobject._set_last_cluster_operation()`. Phase 2 deletes
  both.
* *(Added in phase 2.)* Terminal operations are hard-deleted
  (#3864), and their target rows go with them
  (`BaseClusterOperation.hard_delete()`). The table holds
  in-flight ops plus recently terminal ones, not a full
  history. *(Corrected in phase 4.)* "About thirty seconds after
  going terminal" is when an op becomes eligible. The sweep that
  deletes it runs every fifteen minutes, so a finished op
  normally lingers for up to about fifteen minutes. Errored ops
  are never hard deleted (#3922), and their target rows go only
  when the seven-day `CLUSTER_OPERATION_TARGET_RETENTION` prune
  removes them. See survey finding 6 of
  [the phase 4 plan](PLAN-cluster-op-visibility-phase-04-api-surface.md).
* The pending query joins `object_states` and filters on
  `_ACTIVE_OPERATION_STATES` (`mariadb.py:3682`); it fails
  closed (returns True) on DB errors.
* API capabilities are advertised in `external_api/app.py`
  (see the list around line 215) and checked client-side via
  `check_capability()` in
  `client-python/shakenfist_client/apiclient.py` (line 350 as of
  phase 4). Client changes land in the sibling `client-python`
  repository.

## Mission and problem statement

Make cluster operation visibility truthful and cheap for
every consumer, so that:

* "is anything in flight for this object?" has one correct,
  indexed answer (`has_pending_operations`), exposed through
  `external_view()` and usable by tests, clients, and
  internal gating alike;
* measurement-only background operations stop corrupting the
  `last_cluster_operation` pointer, which restores the
  intended semantics of the six `runs_after` serialisation
  chains and stops background sweeps from reordering user
  mutations;
* the `/instances/<ref>/clusteroperations` family of endpoints
  reports every outstanding operation targeting the object,
  not just the ones reachable from the newest target row; and
* the functional CI await helpers stop flaking on background
  op timing (`test_interface_plug_and_exec_reboot` being the
  proven victim).

## Open questions

* **Classification sweep.** Which *tasks* are observational?
  Known candidates: `collect_billing_statistics` and
  `health_check_kvm_process` (the per-instance sweep in
  `daemons/cluster/scheduled_tasks.py`), and
  `verify_size_and_checksum` (the blob checksum sweep, same
  file). Phase 3 must audit every task of every operation
  model and classify each one with a comment. Anything that
  mutates guest-visible or cluster state is mutating.
* **Where does the flag live?** *Resolved in phase 2.* Not
  per model: `node_inst_op` and `node_blob_op` both mix
  observational and mutating tasks. Not per call either, which
  would let a caller mislabel the work. Each model declares
  `observational_tasks` beside `target_fields`, and an op is
  observational only if every one of its tasks is. The result
  is stored per row in a column on `cluster_operation_targets`.
  See decision 1 of the phase 2 plan.
* **Capability naming.** *Resolved in phase 4.*
  `object-pending-operations`, in the `cluster-operations`
  family, advertising both the field and the truthful
  per-object listings. See decision 2 of the phase 4 plan.
* **`all=true` semantics.** *Resolved by the phase 2
  survey, and corrected by the phase 4 survey.* Since #3864,
  terminal ops are hard-deleted, so `/clusteroperations?all=true`
  returns in-flight ops, finished ops from about the last fifteen
  minutes (the deleted-object sweep's cadence, not thirty
  seconds), and errored ops for up to the seven-day target-row
  retention (#3922). It is not a full history and is bounded by
  that retention. No limit parameter is needed, and phase 4
  documents it as what it is.
* **Should `hard_delete` of ops prune target rows?** *Resolved
  by the phase 2 survey.* It already does:
  `BaseClusterOperation.hard_delete()` deletes the op's target
  rows first. The `CLUSTER_OPERATION_TARGET_RETENTION` prune
  is a backstop. Phase 2 corrects the operator docs that still
  call the table an append-only history.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. CI await helper deflake | [PLAN-cluster-op-visibility-phase-01-ci-deflake.md](PLAN-cluster-op-visibility-phase-01-ci-deflake.md) | Complete | `bc7c119bf` (#3465) |
| 1b. Atomic target writes + by-target JOIN fix | (folded into phase 1 PR; see below) | Complete | `bc7c119bf` (#3465) |
| 2. Observational flag: schema and query layer | [PLAN-cluster-op-visibility-phase-02-observational-schema.md](PLAN-cluster-op-visibility-phase-02-observational-schema.md) | Complete | `9b098c6ac` (#4574) |
| 3. Classify and mark observational tasks | [PLAN-cluster-op-visibility-phase-03-mark-observational.md](PLAN-cluster-op-visibility-phase-03-mark-observational.md) | Complete | `27ea18202` (#4588) |
| 4. API surface: has_pending_operations and truthful outstanding ops | [PLAN-cluster-op-visibility-phase-04-api-surface.md](PLAN-cluster-op-visibility-phase-04-api-surface.md) | In progress | — |
| 5. CI helper simplification, functional coverage, documentation | PLAN-cluster-op-visibility-phase-05-coverage-docs.md | Not started | — |
| 6. Push audit | PLAN-cluster-op-visibility-phase-06-push-audit.md | Not started | — |

The `Merged` column records what put each phase on `develop`.
These entries were reconstructed after the fact, because the
plan did not record them as its phases landed; they come from
the repository's merged pull request list cross-checked against
the first-parent history, and not from a path-filtered
`git log`, which cannot say which commits arrived inside a
pull request. Every SHA is the merge commit of the pull request named
beside it, so `<sha>^1..<sha>` is the whole of what that pull
request put on `develop`. A phase which has not landed reads
`—`.

Phase 1b was folded into phase 1's pull request rather than
landing separately, so both rows name the same merge and the
audit reads that one range for both.

### Phase 1b: Atomic cluster_operation_targets writes (done, in the phase 1 PR)

Discovered while verifying phase 1 in CI. Even with the
by-target endpoint fixed, `test_interface_plug_and_exec_dhcp`
still failed intermittently: the CI await helper's first
by-target poll, fired microseconds after the interface
hot-plug POST returned, saw `[]` and concluded "done" while
the hot-plug op was in fact queued. The bundle proved it -- op
"created" at T, POST returned at T+3ms, poll at T+5ms returned
`[]`. The cause: `enqueue_cluster_operation`
(`schema/operations/util.py`) wrote the op's state/work-queue
rows via `create_and_enqueue_cluster_operation` (one
transaction) and *then* wrote the `cluster_operation_targets`
rows in a separate, non-transactional loop. A by-target reader
in that gap sees an enqueued op with no target rows. This same
race undermines `has_pending_cluster_operation()`, the
network-node `is_okay()` gating, and everything phase 2/4 will
build on the by-target query -- so it is a root-cause fix, not
a test workaround.

Fix (shipped in the phase 1 PR): thread a `targets` list of
`(ObjectType, target_uuid)` pairs through
`create_and_enqueue_cluster_operation` (public/gRPC/direct)
and the `CreateAndEnqueueClusterOperationRequest` proto, and
write those rows in the **same** transaction as the op.
`enqueue_cluster_operation` now derives the list from
`model_class.target_fields` and passes it down instead of
looping `create_cluster_operation_target` after the fact.
`create_cluster_operation_target` remains for its other caller
(`node_blob_op.py`) and for `_set_last_cluster_operation`
(dead code, still slated for removal in phase 2). Unit tests:
`_direct_create_and_enqueue_cluster_operation` writes N target
inserts before a single commit; `enqueue_cluster_operation`
passes the derived `targets` and no longer calls the retired
per-row writer.

### Phase 1: CI await helper deflake (plan at medium effort)

Requires a small server-side fix after all (see phase 1b and
the phase 1 plan's scope correction). Rewrite
`_await_instance_operations_complete()` and
`_await_network_operations_complete()` in
`deploy/shakenfist_ci/base.py` to call
`system_client.list_cluster_operations_for_target()` and wait
until no returned op is in a non-terminal state (non-terminal
being anything outside `complete`/`deleted`/`abort`; `error`
should still fail the test as today). Keep the 5-minute
timeout and the existing failure messages. Note the helpers
run against the system namespace client so the by-target
endpoint's namespace gate is not a concern. This phase is
test-only; a stestr run and a smoke CI pass are the
verification.

### Phase 2: Observational flag -- schema and query layer (plan at high effort)

Add the mutating/observational distinction to storage and
queries, default everything to mutating (no behaviour
change until phase 3). The detailed plan's survey changed
the shape of this phase: the flag is derived from each op's
tasks, the writer is the atomic enqueue (the
`create_cluster_operation_target` path is deleted rather
than extended), and the operator schema and upgrade docs
ship here rather than in phase 5. See
[the phase 2 plan](PLAN-cluster-op-visibility-phase-02-observational-schema.md).
The original outline follows.

* `cluster_operation_targets` v3 migration: add
  `observational` boolean (server default false) in
  `_ensure_cluster_operation_targets_schema()`; bump
  `CLUSTER_OPERATION_TARGETS_VERSION`. Consider whether
  `idx_cot_target` needs to become
  `(target_object_type, target_uuid, observational)` -- check
  the query plans for the latest/pending queries.
* Thread the flag through the writer:
  `create_cluster_operation_target()` public/gRPC/direct
  trio, `CreateClusterOperationTargetRequest` and
  `ClusterOperationTargetProto` in `protos/database.proto`,
  the database daemon handler, and the auto-write loop in
  `enqueue_cluster_operation()`. Regenerate with
  `tox -e genprotos`. *(Superseded: the flag rides
  `CreateAndEnqueueClusterOperationRequest`.)*
* Exclude observational rows in
  `get_latest_cluster_operation_target()` and
  `has_pending_cluster_operation_target()` (both direct and
  gRPC paths -- the filter belongs in SQL, not in Python).
  `list_cluster_operations_for_target()` keeps returning
  everything (it is the audit/history view) but should
  surface the flag in its summary dicts.
* Delete the dead `_set_last_cluster_operation()` and its
  now-unused `create_cluster_operation_target` call path if
  nothing else uses it.
* Rolling upgrade note: schema must be applied via `sf-ctl
  ensure-mariadb-schema` before daemons roll, per the
  existing verify-at-startup contract. Old daemons writing
  rows without the column are fine (server default false =
  mutating, the conservative value).

### Phase 3: Classify and mark observational tasks (plan at medium effort)

Audit every task of every operation model in
`shakenfist/schema/operations/*_op.py`, classify each as
mutating or observational with a short justification
comment, and declare the observational ones in that model's
`observational_tasks` (the mechanism phase 2 built). Also
check every `create_and_enqueue()` caller, so that no caller
combines an observational task with a mutating one by
accident. Known observational tasks:
`collect_billing_statistics` and `health_check_kvm_process`
in `node_inst_op`, and `verify_size_and_checksum` in
`node_blob_op`. Everything else is expected to stay
mutating. The deliverable includes the audit table in the
phase plan, so the decision trail survives. Phase 2 already
added the unit test that proves the filter, and a test that
no model declares observational tasks. This phase replaces
that second test with one pinning the classification table.
The developer-guide account of mutating vs observational ops,
and when to mark a task observational, lands here, because
this is when the distinction first means something.
`last_cluster_operation` in the instance external view stops
reporting sweep ops once this phase lands, and the release
notes should say so. Watch one trap, found in phase 2:
`observational_tasks` must be annotated `ClassVar`. Without the
annotation, pydantic turns it into a field, the class attribute
the helper reads disappears, and the classification silently
does nothing. The phase 2 inertness test checks
`model.model_fields` for this, and its replacement must keep an
equivalent check.

### Phase 4: API surface (plan at high effort)

See [the phase 4
plan](PLAN-cluster-op-visibility-phase-04-api-surface.md). Its
survey corrected this section in five places, marked inline: the endpoints are
`/<type>/<ref>/clusteroperations`, not `/operations`; the
current listing is not newest-first; the delete walk also
aborts other objects' ops it reaches through dependencies; the
client has no per-object accessors at all; and finished ops
live for up to about fifteen minutes, not thirty seconds. It
also adds a `pending_only` request field, so the default
listing filters on state in SQL. The original outline follows.

* Add `has_pending_operations` to `external_view()` for
  `DatabaseBackedObjectWithOperations` subclasses that
  already expose `last_cluster_operation` (instance, network,
  artifact at minimum -- audit which views include the
  pointer today). One extra EXISTS query per view render;
  verify the views that render lists of objects (e.g.
  instance list endpoints) do not multiply this into a
  hot-path problem -- if they do, restrict the field to
  single-object GETs. *(Phase 4: exactly those three views,
  plus the artifact view the snapshot listing uses. Lists
  already pay one target query per object for the pointer, so
  the field goes everywhere the pointer does.)*
* Advertise a new capability string (proposed
  `object-pending-operations`) in `external_api/app.py`.
* Reimplement `baseobject.get_cluster_operations()` over
  `mariadb.get_cluster_operation_targets_for_object()` +
  per-op state checks so the
  `/instances|networks|artifacts/<ref>/clusteroperations`
  endpoints report all outstanding ops, not just the latest
  chain. Keep newest-first ordering. *(Phase 4: today's order
  is the pointer, then one level of its `depends_on` and
  `runs_after`, which also pulls in other objects' ops. The
  rebuilt listing orders by target sequence number, newest
  first, and lists only ops that target the object.)*
* *(Added by the phase 2 survey.)* The instance delete path
  (`Instance` around `instance.py:2773`) picks which
  outstanding ops to cancel by walking the dependency tree of
  `self.last_cluster_operation`. That is the same pointer
  race. Rebuild it on the same all-pending-targets walk as
  `get_cluster_operations()`, and decide explicitly whether
  delete cancels in-flight observational ops. Once phase 3
  lands they are no longer the pointer, so the current walk
  skips them. Phase 3 made that safe rather than leaving it
  harmless by assumption: both sweep tasks now return early
  for an instance that is not `created`, and the billing
  task no longer raises when a delete removes a file it is
  measuring. It is still a behaviour change, so state it.
  *(Phase 4: the walk is recursive through `depends_on` and
  `runs_after`, so it also aborts network reconcile ops that
  target only the network, and the delete waits only for the
  pointer, so a snapshot that is not the pointer can be
  overtaken. Both are fixed by the rebuild. Delete cancels
  observational ops too; see decisions 6 and 7.)*
* client-python (sibling repo): add
  `get_instance()`-adjacent accessors as needed, a
  `has_pending_operations`-aware helper, and capability
  checks mirroring `list_cluster_operations_for_target()`.
  Update the OpenAPI examples/swagger helpers touched.
  *(Phase 4: the client has no accessor for the per-object
  listings at all, although the API reference pages show
  examples calling `get_instance_queued_cluster_operations()`
  and its twins, which have never existed.)*

### Phase 5: CI helper simplification, functional coverage, documentation (plan at medium effort)

* Switch the phase-1 CI helpers to poll
  `has_pending_operations` (cheaper, single GET) once the
  deployed server advertises the capability; keep the
  by-target fallback for older servers.
* *(Added by the phase 4 survey.)* The helpers can do this with
  `check_capability('object-pending-operations')`,
  `get_instance()` and `get_network()`, which the client already
  has, so this phase does not wait on phase 4's client-python
  pull request. Once that pull request has merged,
  `TestOutstandingOperationsBodyAll`
  (`cluster_ci_tests/test_api.py`) can use the new
  `get_network_clusteroperations()` wrapper instead of
  `_request_url`, though it must keep one raw call, because the
  raw body path is what it tests.
* *(Added by the phase 3 survey.)* `_await_object_operations_complete()`
  (`deploy/shakenfist_ci/base.py`) fails a test when any op it
  watched go in flight ends in `error`, and its by-target listing
  includes observational ops. A billing op that errored (#4585)
  therefore fails an unrelated test. Ignore ops reporting
  `observational: true`, both when deciding whether anything is
  outstanding and when failing on `error`.
* Functional test: hot-plug an interface and assert the
  await helper genuinely blocks until the attach lands even
  when a billing sweep fires (can be provoked by simply
  waiting through a sweep period during the await).
* Documentation: (the `docs/operator_guide/` schema and
  upgrade notes ship in phase 2, and the developer-guide
  account of mutating vs observational ops in phase 3; check
  that both are still accurate)
  `ARCHITECTURE.md`/`AGENTS.md`/`CLAUDE.md` storage notes
  for the new column and field, `docs/plans/index.md`
  status updates.

### Phase 6: Push audit

Runs `PUSH-AUDIT.md` over the accumulated diff of every
phase in this plan against `develop`, not the last phase's
diff alone. Findings land as their own pull request, and
the plan is not complete until each is resolved or declined
in writing here. If the audit finds nothing, that is
recorded in one sentence.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean and
avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort level
   and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's summary
   describes what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether
   the brief was insufficient (improve it) or the model was
   too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied with
   the result.

This applies to all steps, including high-effort ones. If a
sub-agent can't succeed even with a detailed brief and the
right model, that's a signal the brief needs improving, not
that the management session should do the implementation
itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental. The worktree is discarded if the
output is unsatisfactory. For safe, well-understood changes,
sub-agents can work directly in the main tree.

### Planning effort

The master plan itself was created at **high effort**.

Recommended planning effort per phase is noted in the phase
summaries above: phases 2 and 4 (schema design, cross-daemon
protocol changes, API semantics) at high effort; phases 1, 3
and 5 (well-understood test changes, a classification sweep
with a clear rubric, docs) at medium effort.

### Step-level guidance

Each phase plan should include a step table using this
format:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**
- **high** — Requires reading multiple files, making judgment
  calls, understanding non-obvious invariants, or researching
  external references. The sub-agent needs to think carefully
  about edge cases.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief. May need to read a few
  files but the approach is well-defined.
- **low** — Purely mechanical changes (rename, reformat, add
  a log line, regenerate proto stubs). The brief is a
  complete instruction.

**Model choice:** opus for deep reasoning, subtle correctness
judgment (locking, state machines, migration), or steps that
must hold much of `mariadb.py` plus the database daemon in
context at once (context window: opus has 1M tokens, sonnet
and haiku 200K). sonnet is the default for well-briefed
implementation work. haiku only for purely mechanical tasks
with near-complete instructions. When in doubt, skew to the
more capable model — a failed implementation wastes more time
than the heavier model costs.

**Brief for sub-agent:** Write it as if briefing a colleague
who has never seen the codebase. Include: what to change,
which files to touch, what patterns to follow, and any
non-obvious constraints. Front-load the research the planner
already did — the Situation section of this plan contains
file/line references collected during forensics precisely so
briefs can cite them.

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code passes `pre-commit run --all-files` (flake8,
      stestr unit tests, mypy).
- [ ] If proto files changed, stubs were regenerated with
      `tox -e genprotos` and committed.
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] Commit message follows project conventions (including
      the Co-Authored-By line with model, context window,
      effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (flake8,
  stestr unit tests, and mypy type checking).
* New code follows existing patterns: object lifecycle in
  `baseobject.py`, MariaDB access via the three-layer pattern
  (direct/gRPC/public), Pydantic schemas in
  `shakenfist/schema/`.
* The observational filter is pushed down to the MariaDB SQL
  layer (WHERE clause with index support), not applied in
  Python.
* An observational op enqueued against an object changes
  neither `last_cluster_operation` nor
  `has_pending_operations` for that object, and a unit test
  proves it.
* `GET /instances/<ref>/clusteroperations` returns a queued
  op even when a later terminal op targets the same instance,
  and a test proves it.
* `test_interface_plug_and_exec_reboot` no longer races the
  hot-plug: the await helper blocks until the attach lands.
* gRPC proto changes have been regenerated with
  `tox -e genprotos`.
* `docs/operator_guide/database.md` documents the
  `cluster_operation_targets` v3 schema, and the developer
  guide documents the mutating/observational distinction.
* `ARCHITECTURE.md`, `README.md`, `AGENTS.md` and `CLAUDE.md`
  have been updated where they describe the affected storage
  or API behaviour.
* `docs/plans/index.md` and `docs/plans/order.yml` reference
  this plan and its phases with current status.

### Future work

* Client-side load balancing interactions: none expected, but
  the new gRPC field rides the existing database service
  surface — nothing to do here beyond genprotos.
* Consider whether the scheduler / cleaner should also
  consult `has_pending_operations` instead of any remaining
  last-op reads (audit for other legacy readers of
  `last_cluster_operation` beyond the six `runs_after`
  sites). *(Audited in phase 4: nothing in the scheduler or
  cleaner reads the pointer. Its readers are the three views
  and the six `runs_after` sites, one of which, in
  `Network.new()`, always sees `None`. The four that phase 4
  leaves alone serialise behind the newest mutating op, which
  skips older pending ops that were enqueued without
  `runs_after`: disk fetches, redirected starts and
  `instance_create`. Whether each should wait on every pending
  mutating op is its own question. See survey finding 8 of the
  phase 4 plan.)*
* `/clusteroperations?all=true` pagination if history size
  becomes a practical problem. It is bounded by the seven-day
  target-row retention, mostly by errored ops (#3922).
* If `HasPendingClusterOperationTarget` from sf-api becomes a
  real load problem, one RPC answering both latest and pending
  for an object would keep each view at today's query count.
  Phase 4 measured the field at about 2.5 requests a second on
  sfcbr.
* Interfaces are targets of hot-plug ops but carry neither
  `last_cluster_operation` nor `has_pending_operations`.
* The per-blob sweep's duplicate guard could pass
  `pending_only=True` (added in phase 4) instead of hydrating
  every target row. Its load is negligible today.
* The 15-second enqueue-to-execution latency observed for the
  `user_waiting` hot-plug chain in CI (dependency deferral
  polling cadence) is worth a separate look — it is latency,
  not correctness, but user-visible.
* #4585: a failing billing or KVM health check task moves a
  healthy instance to `created-error`, because
  `NodeInstOp.dispatch_task()` errors the instance on any task
  exception. Found by the phase 3 survey. Its natural fix keys off
  the `observational_tasks` declarations phase 3 adds.
* #4603: pages and code comments outside this plan's API
  reference say terminal ops are removed after about thirty
  seconds, when that is only eligibility and the sweep runs
  every fifteen minutes. Several of those sentences were
  written by phases 2 and 3. Found by the phase 4 survey.
* An agent operation's preflight (`node_aop_op`) targets only the
  agent operation, not its instance, so an instance delete never
  cancels a pending one. Found in phase 4; it predates this plan.
* `Instance.enqueue_delete()` fails open: the target listing it
  walks returns `[]` on a database error, so the delete aborts
  nothing and waits for no snapshot. A variant of the listing which
  raises, so the delete is refused or retried instead, would make
  the snapshot guarantee hold. Found by the automated review of
  phase 4; the pointer walk it replaced failed the same way.
* Issue #3278 (CI and test coverage backlog) may want a note
  about the deflaked helper pattern.

### Bugs fixed during this work

* **`GET /clusteroperations?target_object_type=&target_uuid=`
  was broken on arrival** (fixed in phase 1). The handler
  `ClusterOperationsEndpoint.get(self)` accepted neither
  target parameter as a keyword argument and read them from
  `flask.request.args`, but the SF client sends parameters in
  a JSON body which the `log_request` decorator injects as
  kwargs — so every real client call raised a `TypeError`
  surfaced as HTTP 400. The endpoint's unit tests only drove
  the query-string path via Flask's test client and never
  caught it. Fixed by accepting the injected kwargs (with a
  query-string fallback) and adding a body-path unit test.
* **The by-target listing query never matched** (fixed in
  phase 1b). `_direct_list_cluster_operations_for_target`
  JOINed `cluster_operation_targets.operation_uuid`
  (`String(36)`, dashed form) directly against
  `cluster_operations.uuid` (a `Uuid()` column whose stored
  form has no dashes / is native), so the JOIN never matched
  and the endpoint always returned `[]` even for a queued op.
  `has_pending`/`get_latest` were unaffected because they JOIN
  `operation_uuid` against `object_states.object_uuid` (both
  dashed `String(36)`). It escaped notice because the endpoint
  400'd on arrival (so the query never ran) and its SQLite
  unit fixture used `String(36)` for `cluster_operations.uuid`
  instead of the real `Uuid()`, so String==String matched and
  masked the bug. Fixed with a two-step query (select
  operation_uuids from targets, then `ops.uuid.IN` the UUID
  objects so the `Uuid` bind processor converts to storage
  form), and the fixture was corrected to use `Uuid()` so the
  existing SQL tests now guard it. This is the fix that
  actually made the CI hot-plug await reliable -- the
  atomic-write fix (phase 1b) was necessary but not
  sufficient on its own.
* Candidate already identified: `_set_last_cluster_operation`
  in `baseobject.py` is dead code and will be removed in
  phase 2.

A scan of the GitHub issue tracker on 2026-07-19 found no
open issues describing this race directly; the closest
adjacent reports are #3432 (lock lifetime noise) and #3278
(CI coverage tracking), neither of which this plan resolves.

### Documentation index maintenance

When creating this master plan, update the following files in
`docs/plans/`:

* **`index.md`** — add one row to the *Master plans* table
  with a link to this plan, its initial status, its phase
  arithmetic, and a one-line intent.
* **`order.yml`** — add an entry for this master plan, in
  the intended reading order. Phase files are
  linked from the Execution table only, which makes that
  table the only path to them.

When all phases are complete, update the status column in
`docs/plans/index.md`.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
