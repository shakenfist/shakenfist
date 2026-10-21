# Cluster op visibility phase 3: classify and mark observational tasks

This is a detailed phase plan under
[PLAN-cluster-op-visibility.md](PLAN-cluster-op-visibility.md).
Phase 2 built the mechanism: the `observational` column on
`cluster_operation_targets`, the `observational_tasks` declaration
and `is_observational()`, and the SQL filters. This phase decides
which tasks use it, and is the first point at which the flag changes
behaviour.

**Planning effort:** medium (per the master plan). **Review
effort:** medium. The code change is two declarations. The risk is
in the classification itself and in what the newly filtered
pointer does to its readers. Both are researched below, so the
review is about checking that research, not redoing it.

## Scope

In scope:

* Declaring `observational_tasks` on the two models that have
  observational tasks, with a justification comment per task.
* A classification comment on every other model's `model_tasks`,
  so the decision trail lives beside the code as well as in the
  audit table below.
* Replacing phase 2's "nothing is observational" test with one that
  pins the full classification, every task of every model, and
  keeps the `ClassVar` check.
* A behaviour test that drives the real models' task lists, the
  ones the sweeps actually enqueue, through the enqueue path and
  the filtered queries.
* The developer-guide account of mutating vs observational ops and
  when to mark a task observational, a release note, and the code
  comments and docs that still say nothing is classified, or call
  the targets table append-only.

Out of scope, deliberately:

* What a *failing* observational task does to its object. A
  billing or health-check task that raises puts a healthy instance
  into `created-error` today. That is a real bug, filed as #4585.
  It predates this plan, and this phase changes which ops count as
  latest or pending, not what a failing task does. See decision 5.
* Every API surface change: `has_pending_operations`, the
  `/operations` endpoints, and the instance delete walk. That is
  phase 4.
* The CI await helpers. That is phase 5, which gains a note from
  this survey (decision 9).

## What the survey found

The survey ran on 2026-10-20 against `develop` at `bea7587999`.
The master plan's phase 3 section is accurate: the three known
candidates are the only observational tasks, and no caller mixes
kinds. The findings below sharpen it, and three of them bear on
the decisions.

1. **There are 33 tasks across 14 operation models**, in
   `shakenfist/schema/operations/*_op.py`. The audit table below
   lists every one. The phase 2 inertness test already derives the
   model set from `OPERATION_NAMES_TO_CLASSES`, so the replacement
   can be exhaustive by construction.
2. **The three candidates do write things.** None is read-only, so
   the rubric (decision 1) has to say what kind of write is
   allowed:
   * `_collect_billing_statistics`
     (`shakenfist/operations/node_inst_op.py:112`) reads libvirt,
     disk sizes, the console log size and `/proc` OOM scores, and
     records them as an `EVENT_TYPE_USAGE` event.
   * `_health_check_kvm_process` (`node_inst_op.py:177`) clears
     `inst.kvm_pid` when that process no longer exists. Its own
     comment says it deliberately does not error or delete the
     instance.
   * `_verify_size_and_checksum`
     (`shakenfist/operations/node_blob_op.py:113`) calls
     `Blob.verify_size()` and `Blob.verify_checksum()`
     (`shakenfist/blob.py:1011`, `:1036`). These record hashes, and
     on a size or checksum mismatch they call
     `_remove_corrupt_blob()`, which drops this node's replica and
     its location.
3. **No caller mixes an observational task with a mutating one.**
   Every enqueue of the candidate tasks:
   * `daemons/cluster/scheduled_tasks.py:526` (per-instance sweep)
     enqueues `[collect_billing_statistics,
     health_check_kvm_process]`.
   * `scheduled_tasks.py:459` (per-blob checksum sweep),
     `blob.py:978` (after a download), `blob.py:1101` (missing
     hashes) and `external_api/blob.py:343` (a user asking for a
     hash nobody has computed) each enqueue
     `[verify_size_and_checksum]` alone.
   * `instance_delete` is enqueued alone (`instance.py:2798`), and
     so is `ensure_local` (`blob.py:956`).
4. **The filtered pointer only reaches instances.**
   `node_inst_op` targets only `INSTANCE` and `node_blob_op` only
   `BLOB` (their `target_fields`). Blobs expose no
   `last_cluster_operation` in their external view, no
   `runs_after` chain reads a blob's pointer, and no caller asks
   `has_pending_cluster_operation_target` about a blob; the callers
   in `daemons/network/maintain.py` and `network/network.py:552` all
   ask about networks. Classifying `verify_size_and_checksum`
   therefore changes only the stored flag and what the by-target
   listing reports. The blob sweep's duplicate-op guard
   (`scheduled_tasks.py:434`) reads the unfiltered
   `get_cluster_operation_targets_for_object()`, so it still sees
   pending checksum ops.
5. **Instances lose their sweep pointer, and nothing breaks.** Once
   the per-instance sweep is observational, an instance at rest
   usually reports `last_cluster_operation: null`. Its last mutating
   op was hard-deleted about thirty seconds after going terminal,
   and the sweep ops no longer count. The four instance
   `runs_after=[...last_cluster_operation]` callers
   (`instance.py:2800`, `instance.py:2913`,
   `external_api/instance.py:1285`, `:1449`) pass that through
   `_convert_deps()` (`schema/operations/baseclusteroperation.py:114`),
   which skips `None` entries. A null pointer already happens today,
   between sweeps, so this is not a new input. The swagger example
   at `external_api/instance.py:122` shows a populated pointer and
   stays valid.
6. **A failing measurement task errors the instance.**
   `NodeInstOp.dispatch_task()` (`node_inst_op.py:84`, the handler
   at `:96`) turns any exception from any task into
   `<state>-error` on the instance.
   `_collect_billing_statistics` catches only `libvirtError`, so a
   transient sf-database failure inside its `add_event()` call
   errors a healthy guest. Filed as #4585; see decision 5. The blob
   dispatcher does not do this. It errors only the op.
7. **Stale "nothing is classified" text exists in five places,**
   all of which this phase makes false:
   * `schema/operations/node_inst_op.py:36-42` (the model comment);
   * `tests/test_observational_current_state.py:62` (docstring);
   * `tests/schema/operations/test_observational_tasks.py`
     (replaced outright);
   * `docs/developer_guide/database_internals.md:285` ("No model
     declares any yet");
   * the master plan's success criteria, which need no edit.

   Two more comments still call the targets table append-only,
   which phase 2 corrected in the operator docs but not in code:
   `schema/cluster_operation_target.py:8` and
   `daemons/cluster/main.py:176-177`.
8. **The phase 1 CI helper can now fail on an observational op.**
   `_await_object_operations_complete()`
   (`deploy/shakenfist_ci/base.py:1382`) fails the test when any op
   it watched go in flight ends in `error`. The by-target listing
   it polls includes observational ops, so a billing op that
   errored (finding 6) fails an unrelated test. That is true today
   too, and the flag now lets the helper tell the two apart. It is
   phase 5 work (decision 9).
9. **Release notes are a single file,**
   `docs/release_notes/v07-v08.md`, with one `##` section per
   change.
10. **The developer-guide home already exists.**
    `docs/developer_guide/database_internals.md:280-320` documents
    the flag's mechanics. Nothing yet says when a task should be
    marked, which belongs in the same section.

Corrections at source: the master plan's phase 3 section had no
false claims. Its phase 5 section gains finding 8 in this planning
commit, and its Future work gains #4585.

## Audit table

Rubric: decision 1. One row per task, every model.

| Model | Task | Class | Why |
|-------|------|-------|-----|
| `artifact_fetch_op` | `image_fetch` | mutating | Downloads and creates blobs and artifact versions. |
| `imgcache_op` | `archive_transcode` | mutating | Writes a transcoded image to the cache. |
| `net_iface_op` | `interface_float` | mutating | Allocates and routes a floating address. |
| `net_iface_ip_op` | `interface_defloat` | mutating | Removes a floating address. |
| `net_ip_op` | `route_address` | mutating | Adds host routing for an address. |
| `net_ip_op` | `unroute_address` | mutating | Removes host routing. |
| `net_macaddr_ip_op` | `remove_dhcp_lease` | mutating | Changes dnsmasq lease state. |
| `net_op` | all twelve (`network_deploy` ... `network_apply_delete_network_node`) | mutating | Each creates, changes or removes network host state: bridges, dnsmasq, NAT, mesh, floating IPs. `network_ensure_mesh` reconciles rather than creates, but reconciling changes the host, and network gating (`Network.is_okay()`) must wait for it. |
| `node_aop_op` | `preflight` | mutating | Promotes, expires or fails an agent operation. |
| `node_blob_op` | `verify_size_and_checksum` | **observational** | Measures a replica and records hashes. Its one other write, dropping a replica proven corrupt, corrects a location record that was already false. Decision 3. |
| `node_blob_op` | `ensure_local` | mutating | Replicates a blob onto this node. |
| `node_inst_net_iface_op` | `hot_plug_instance_interface` | mutating | Changes the guest's devices. |
| `node_inst_net_iface_op` | `attach_instance_interface` | mutating | Changes the guest's devices. |
| `node_inst_netdesc_op` | `instance_preflight` | mutating | Part of the create chain. It advances instance state. |
| `node_inst_netdesc_op` | `instance_start` | mutating | Reconciles networks and starts the guest. |
| `node_inst_netdesc_op` | `instance_create` | mutating | Creates the guest. |
| `node_inst_op` | `collect_billing_statistics` | **observational** | Reads and records a usage event. |
| `node_inst_op` | `health_check_kvm_process` | **observational** | Clears a `kvm_pid` cache entry whose process is gone. Decision 2. |
| `node_inst_op` | `instance_delete` | mutating | Deletes the guest. |
| `node_inst_snap_op` | `instance_snapshot` | mutating | Creates blobs and artifacts from the guest's disks. |
| `node_net_op` | `network_destroy` | mutating | Removes a network from a hypervisor. |
| `node_net_op` | `network_apply_create_hypervisor` | mutating | Creates a network on a hypervisor. |

## Decisions

1. **The rubric.** A task is observational if its purpose is to
   measure or verify its target, and its only writes either record
   what it observed (events, usage, hashes) or correct a cached
   observation that reality has already falsified. Anything that
   changes desired state, guest-visible state, or what a user
   action depends on is mutating. When in doubt, mutating. Marking
   a mutating task observational hides it from the gates that
   exist to wait for it, which is the bug this plan is fixing.
   Marking an observational task mutating only costs a stale
   pointer.

2. **`health_check_kvm_process` is observational**, even though it
   clears `kvm_pid`. The pid is a cache of an observed fact, and
   the task clears it only when the process is already gone. Nothing
   a user or another op does waits for that write. Callers that need
   to know whether the guest is running ask libvirt, or read the
   power state the cleaner records.

3. **`verify_size_and_checksum` is observational**, even though a
   mismatch drops the replica. This is the one most open to
   argument, because a location row is cluster state. Three reasons:
   * The write corrects a record that was already false. The blob
     was never really on that node, so the op reveals a change; it
     does not make one.
   * The verify is not a step in any chain: no caller waits on it
     to sequence other work.
   * Per survey finding 4, nothing reads a blob's filtered pointer
     or pending query today. The classification only changes what
     the flag says, so a reviewer who prefers mutating loses nothing
     by accepting this either.

   One caller does have a user waiting on it, which the survey
   missed and review found: `BlobChecksumEndpoint`
   (`external_api/blob.py:343-345`) enqueues the verify at
   `user_waiting` priority when a requested hash is not yet known.
   That caller polls the endpoint for the hash, not the operation,
   so it is consistent with the rubric as corrected in
   `database_internals.md`. Phase 4 must not offer
   `has_pending_operations` as the way to wait for that hash.

   If a blob gate is ever built on `has_pending_cluster_operation`,
   revisit this. The declaration's comment will say so.

4. **The declarations sit beside `target_fields`, with a per-task
   comment.** Each observational member gets one line naming what
   it writes. Every other model's `model_tasks` enum gets a single
   comment, "All tasks mutate: <reason>", rather than one comment
   per obvious task. Thirty lines of "this deletes the network"
   would bury the three that matter. The per-task justification for
   all 33 lives in the audit table above, and the pin test (decision
   6) makes the classification itself unforgettable.

5. **The failure-path bug (#4585) does not block this phase**, and
   is not fixed in it. Hiding a billing op from the pointer and the
   pending query is still right while that bug stands. Waiting for
   a measurement could never prevent its failure from erroring the
   instance. All it did was make tests wait on noise. #4585's
   natural fix keys off the declarations this phase adds, so it
   sequences after this phase. It is recorded in the master plan's
   Future work.

6. **The pin test is exhaustive, not a list of exceptions.** It
   holds a table `{module: {task name: 'observational' |
   'mutating'}}` covering every task of every model. It asserts:
   * the table's models equal the scanned models (which phase 2
     already ties to `OPERATION_NAMES_TO_CLASSES`);
   * each model's task names equal its `model_tasks` members;
   * each model's declared set equals the table's observational
     rows;
   * `observational_tasks` is never in `model.model_fields`.

   A new task, or a new model, then fails the test until someone
   classifies it, and the failure message points at the rubric in
   the developer guide. That makes the answer derivable instead of
   remembered, which is what the master plan's "replace that second
   test with one pinning the classification table" asks for.

7. **No backfill or migration.** The flag is computed at enqueue
   and stored per row. Rows written before this deploys stay
   mutating until their op is hard-deleted, about thirty seconds
   after it goes terminal. In a rolling upgrade, an old enqueuer or
   an sf-database gateway older than phase 2 writes `False`, which
   is the safe direction. The release note says this in one
   sentence.

8. **The release note says what an operator or client author will
   see,** in `docs/release_notes/v07-v08.md`:
   * an instance's `last_cluster_operation` no longer names the
     billing and health sweep, so it is usually `null` at rest;
   * `runs_after` chains on instances now serialise behind the last
     user mutation, not the last sweep;
   * the by-target listing still reports every op, with
     `observational: true` on these.

   It does not mention blobs (finding 4).

9. **The CI helper's error check is phase 5's.** Finding 8 is
   recorded in the master plan's phase 5 section: the helper should
   ignore observational ops, both when deciding whether anything is
   outstanding and when failing on `error`, once the server reports
   the flag. Changing it here would mix a CI behaviour change into a
   classification PR, and the merge-queue smoke suites are exactly
   where that would be hard to diagnose.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | **Classify, and pin the classification.** Read decisions 1-4 and 6 and the audit table in `docs/plans/PLAN-cluster-op-visibility-phase-03-mark-observational.md` first. (1) In `shakenfist/schema/operations/node_inst_op.py`, replace the comment at lines 36-42 with a declaration beside `target_fields`: `observational_tasks: ClassVar[frozenset[model_tasks]] = frozenset({model_tasks.collect_billing_statistics, model_tasks.health_check_kvm_process})`, with one comment line per task saying what it writes (a usage event; clears a stale `kvm_pid`) and a pointer to the developer guide section step 3c writes (`docs/developer_guide/database_internals.md`). It **must** be annotated `ClassVar`. Without it pydantic makes it a field and `is_observational()` silently sees nothing. (2) Same in `node_blob_op.py` for `verify_size_and_checksum`, with a comment that it drops a replica proven corrupt, and that a blob gate built on `has_pending_cluster_operation` would mean revisiting this (decision 3). (3) In each of the other twelve `*_op.py` modules, add one comment above `class model_tasks` reading `# All tasks mutate: <reason from the audit table>.` Do not comment each member. (4) Replace `shakenfist/tests/schema/operations/test_observational_tasks.py` with the exhaustive pin test of decision 6. Keep `_operation_models()` and `test_operation_models_are_found`. The table is a literal dict in the test, every task of every model. Assertion messages should name the module and task, and say to classify it per the rubric in `docs/developer_guide/database_internals.md`. Keep the `model_fields` check per model. (5) Fix the docstring at `shakenfist/tests/test_observational_current_state.py:62` so it no longer says no production model declares the set. Then mutate and confirm the pin test fails, naming the right thing, for each of: drop `ClassVar` on one declaration; add `instance_delete` to the set; remove `health_check_kvm_process`; add a dummy member to a `model_tasks` enum. Restore each from a scratchpad copy, never `git checkout`. Report the four results. Run `tox -e py3 -- shakenfist.tests.schema.operations` and then `pre-commit run --all-files`. Commit subject: "Mark the sweep tasks observational." |
| 3b | medium | sonnet | none | **Prove the real classification end to end.** Read decision 6 and survey findings 3-5 first. In `shakenfist/tests/test_observational_current_state.py`, which already runs the real mariadb direct layer on sqlite with an upsert shim, add tests that use the **real** `shakenfist.schema.operations.node_inst_op.model` and `node_blob_op.model`, not the test's `_ObservingModel`. Look at how `_enqueue()` passes `model_class` and extend it rather than duplicating it. Cases: (a) an instance with a queued `instance_delete` op, then a sweep op with exactly the per-instance sweep's task list (`[collect_billing_statistics, health_check_kvm_process]`, copied from `daemons/cluster/scheduled_tasks.py:526`). The pointer stays on the delete op; pending stays True until the delete op completes, then False while the sweep op is still queued. (b) Only a sweep op: the pointer is `None` and nothing is pending. (c) A blob with a `[verify_size_and_checksum]` op: its stored target row has `observational=True`, and `get_cluster_operation_targets_for_object()` (the blob sweep's duplicate guard) still returns it. (d) `[ensure_local]` on a blob is stored as mutating. To tie the tests to production rather than to a copied list, also add one test to `shakenfist/tests/test_enqueue_cluster_operation.py`, following its existing pattern of patching the mariadb enqueue call. It drives `scheduled_tasks._process_per_instance_queue` with one created, placed instance (mock what it needs) and asserts the enqueue was called with `observational=True`. Mutate `observational_tasks` on `node_inst_op` to empty, and confirm (a), (b) and the sweep test fail. Restore from a copy. Run the touched test modules, then `pre-commit run --all-files`. Commit subject: "Test the real observational classification." |
| 3c | medium | sonnet | none | **Document it.** Read decisions 1-3, 5, 7 and 8 and survey findings 7-10 first. (1) `docs/developer_guide/database_internals.md`: replace "No model declares any yet." (line 285) with the current state (three tasks, two models), and add a short subsection, "When is a task observational?", giving the rubric of decision 1 in prose: the two allowed kinds of write, the when-in-doubt rule and why the asymmetry favours mutating, the `ClassVar` trap, and that the pin test in `shakenfist/tests/schema/operations/test_observational_tasks.py` must be updated with every new task. Link this plan's audit table rather than copying it. (2) `docs/release_notes/v07-v08.md`: a new `##` section in the file's style saying what decision 8 lists, plus decision 7's rolling-upgrade sentence. Mention that failing measurement tasks still error instances, linking #4585, only if it reads naturally; do not make it the headline. (3) Correct the append-only comments at `shakenfist/schema/cluster_operation_target.py:8` and `shakenfist/daemons/cluster/main.py:176-177`. Rows are removed when their op is hard-deleted, about thirty seconds after it goes terminal (`BaseClusterOperation.hard_delete()`), and the retention prune is a backstop. Match the wording phase 2 used in `docs/operator_guide/database.md`. (4) Check that `docs/developer_guide/network_dispatcher.md:420` and `docs/developer_guide/api_reference/clusteroperations.md` are still accurate, and change them only if not. Run `pre-commit run --all-files`. Commit subject: "Document which tasks are observational." |
| 3d | n/a | management | n/a | After each step: read the changed files rather than the summary, re-run the step's mutations rather than trusting the report, run `pre-commit run --all-files`, check the commit against the master plan's review checklist, and commit. |

## Risks and mitigations

* **A mutating task gets marked observational,** and a gate that
  should have waited does not. Mitigation: only three tasks are
  marked, each justified in the audit table. Decision 1's
  when-in-doubt rule biases the other way. The management session
  checks in 3d that the declared sets match the table exactly, and
  the pin test enforces that from then on.
* **The declaration silently does nothing** (the `ClassVar` trap).
  Mitigation: the pin test checks `model_fields`. Step 3a's mutation
  proves that check fails, and step 3b's real-model tests fail
  if the declared set is empty.
* **A reader depended on the sweep moving the pointer.** Survey
  finding 5 checked the four instance `runs_after` sites and the
  `None` handling. The instance delete walk (`instance.py:2773`)
  now skips sweep ops when choosing what to cancel. Phase 4 already
  owns that walk, and the master plan records the behaviour change
  there. This bullet originally said that a sweep surviving a delete
  does no harm, which was true of the health check but not of the
  billing task: it checked each file existed and then stat()ed it,
  so a delete removing the file in between raised, and
  `dispatch_task` errored the deleting instance (#4585's path).
  Review caught it. Both tasks now return early for an instance
  that is not `created`, and the billing task treats a file
  removed mid-measurement as absent.
* **The merge-queue smoke suites change timing,** because instance
  `runs_after` chains stop waiting behind sweep ops. That is the
  intended fix, but a flake that coincides with this PR will look
  caused by it. Mitigation: the PR description names the change. A
  merge-queue failure is triaged against the known-flake list (for
  example #4407) before anyone reverts.
* **A CI test fails on an errored billing op** (survey finding 8).
  This is unchanged by this phase and recorded for phase 5.

## Definition of done

Each item is checkable from the tree or from a command:

- [ ] `grep -rn "observational_tasks: ClassVar" shakenfist/schema/operations/*_op.py`
      prints exactly two lines, `node_inst_op.py` and
      `node_blob_op.py`.
- [ ] `tox -e py3 -- shakenfist.tests.schema.operations.test_observational_tasks`
      passes, and its table marks exactly
      `collect_billing_statistics`, `health_check_kvm_process` and
      `verify_size_and_checksum` observational.
- [ ] Each of the twelve other `*_op.py` modules has a
      `# All tasks mutate:` comment:
      `grep -L "All tasks mutate:" shakenfist/schema/operations/*_op.py`
      prints only `node_inst_op.py` and `node_blob_op.py`.
- [ ] The four mutations of step 3a each fail the pin test, and the
      empty-set mutation of step 3b fails the real-model tests. This
      was demonstrated in 3d, not assumed.
- [ ] `grep -rn -i "declares.* yet" shakenfist docs/developer_guide`
      prints nothing. At planning time it found the four stale
      statements of survey finding 7.
- [ ] `grep -rn "append-only" shakenfist/schema/cluster_operation_target.py shakenfist/daemons/cluster/main.py`
      prints nothing.
- [ ] `docs/release_notes/v07-v08.md` has a section naming
      `last_cluster_operation` and `observational`.
- [ ] `pre-commit run --all-files` passes.
- [ ] The smoke suites are green on the PR.
- [ ] The master plan's Execution table reads `Complete` with
      merge `9b098c6ac` for phase 2, and `In progress` for phase 3
      with this file linked. The phase 5 section names survey
      finding 8, and Future work names #4585. All of this is done in
      the planning commits.

## Back brief

Before step 3a, back brief the operator on how you understand
this phase and the order of the steps, as the master plan
requires. No step needs a separate gate. Decision 3 is the only
classification a reviewer is likely to dispute, and reversing it
is a one-line change to a declaration and a table row.

## Execution notes

* 2026-10-20 (UTC): steps 3a-3c ran in sequence as planned and
  were opened as #4588. The PR's own CI passed.
* The automated review (0 fix, 0 document, 5 consider) found that
  a queued billing sweep now survives an instance delete and could
  error it (see Risks). At Michael's direction this phase took the
  fix rather than leaving it to #4585: a `created` guard on both
  sweep tasks and a stat race fix in the billing task, with tests
  in `test_node_inst_op_collect_billing_statistics.py` and
  `test_node_inst_op_health_check_kvm_process.py`, each
  mutation-checked. Review items 2 (the user-requested hash, see
  decision 3) and 5 (a release note sentence for clients) were
  taken as documentation. Items 3 and 4, test hygiene in
  correct tests, were declined.
* The first merge queue run,
  https://github.com/shakenfist/shakenfist/actions/runs/39473878717,
  failed on two known flakes unrelated to the diff:
  `test_lifecycle_power_cycle` lost ping after a power cycle with
  `ssh.service` failing in the guest (#4407's signature), and the
  node lifecycle job got a 500 from the under-cloud powering off
  sf2. The review fix was pushed while the PR was out of the
  queue.
