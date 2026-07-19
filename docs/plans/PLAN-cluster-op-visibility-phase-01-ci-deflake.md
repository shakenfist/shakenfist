# Cluster op visibility phase 1: CI await helper deflake

This is a detailed phase plan under
`PLAN-cluster-op-visibility.md`. Read the master plan's
Situation section first; it contains the forensic evidence
and file/line references this phase relies on.

## Scope

Rewrite the two functional-CI await helpers so they wait on
*every* outstanding cluster operation targeting the object,
instead of following the racy `last_cluster_operation`
single pointer. No server changes. This phase merges alone
and immediately deflakes `test_interface_plug_and_exec_reboot`
(and any other test using these helpers).

Out of scope, deliberately: the observational flag (phase 2/3),
`has_pending_operations` (phase 4), and switching these
helpers to the cheaper pending-field poll (phase 5).

## Current behaviour (what we are replacing)

Both helpers live in `deploy/shakenfist_ci/base.py`:

* `_await_instance_operations_complete(instance_uuid)`
  (line 522): polls `get_instance()`, reads
  `last_cluster_operation`, fetches that one op via
  `get_cluster_operation(op_type, op_uuid)`, returns when its
  state is `complete`/`deleted`/`abort`, fails the test on
  `error`, polls every 10s, times out (test failure) after 5
  minutes.
* `_await_network_operations_complete(network_uuid)`
  (line 546): identical shape for networks, 5s poll, and
  carries an explanatory comment about why network mutations
  need draining (dnsmasq/iptables reload race) that must be
  preserved.

The failure mode: any unrelated op that targets the object
and reaches a terminal state between polls (the per-instance
billing/health sweep being the proven case) becomes the
"last" op, and the helper concludes everything is done while
earlier-enqueued ops are still queued.

There are five call sites across the suite (e.g.
`smoke_ci_tests/test_agentops.py`,
`smoke_ci_tests/test_networking.py:445`). None use a return
value.

## New behaviour

Use the history-aware by-target listing that already exists:

* Server: `GET /clusteroperations?target_object_type=<t>&target_uuid=<u>`
  (`external_api/clusteroperation.py`,
  `ClusterOperationsEndpoint`) — joins
  `cluster_operation_targets` against `cluster_operations`,
  returns one `external_view()` summary dict per op that has
  ever targeted the object, newest-first. Observed dict shape:
  `{'operation_type': 'node_inst_op', 'uuid': ...,
  'state': 'complete', 'tasks': [...], 'node_uuid': ...,
  'instance_uuid': ...}`.
* Client: `list_cluster_operations_for_target(
  target_object_type, target_uuid)`
  (`client-python/shakenfist_client/apiclient.py:1618`),
  gated on the `cluster-operations-by-target` capability
  (advertised by the server in `external_api/app.py`; CI
  always deploys matched server/client builds, so a missing
  capability should surface as a hard failure, not a silent
  fallback).

The await rule:

1. Each poll, list all ops for the target and partition by
   state. Terminal states are `complete`, `deleted`, and
   `abort` (matching today's helper). Everything else
   (`initial`, `queued`, `preflight`, `executing`, and any
   deferred variants) is outstanding.
2. Return once no op is outstanding.
3. `error` handling must not regress into a new flake: an
   *old* error op from earlier in the object's history would
   otherwise fail every subsequent await. Track the uuids
   this helper has itself observed as outstanding during this
   call; fail the test (preserving today's message format)
   only if one of *those* ops transitions to `error`. An op
   that is already `error` on the very first poll is also
   grounds for failure — it may be the op we were asked to
   wait for — but this matches today's semantics only when it
   was the last op; flag any test that trips this in review.
4. An op that disappears from the listing between polls has
   been hard-deleted by the cleaner; treat it as terminal.
5. Keep the existing poll cadences (10s instance, 5s
   network), the 5-minute timeout, and the timeout failure
   messages verbatim.

Both helpers become thin wrappers over one shared private
implementation parameterised by object type string
(`'instance'` / `'network'`), uuid, poll interval, and the
label used in failure messages. Preserve the network
helper's dnsmasq/iptables rationale comment on the wrapper,
and add a short comment on the shared implementation
explaining *why* it enumerates all ops (the
lco-shadowing race, with a pointer to the master plan).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Rewrite `_await_instance_operations_complete` and `_await_network_operations_complete` in `deploy/shakenfist_ci/base.py` (lines 522-574) as wrappers over a new shared `_await_object_operations_complete(object_type, object_uuid, poll_interval, label)` implementing the await rule in the "New behaviour" section of this phase plan (read it in full first). Use `self.system_client.list_cluster_operations_for_target(object_type, object_uuid)`; the client method already exists. Terminal states `complete`/`deleted`/`abort`; track observed-outstanding op uuids and fail via `self.fail()` with the existing message format if one transitions to `error`; treat ops that vanish from the listing as terminal; keep 10s/5s cadence, the 5-minute timeout, and the timeout messages verbatim. Preserve the dnsmasq/iptables comment on the network wrapper; add a comment on the shared helper citing the lco-shadowing race and `docs/plans/PLAN-cluster-op-visibility.md`. Do not change any call sites (there are five; none use return values). Single quotes, 120-char lines. Verify with `tox -eflake8 -- -HEAD`. |
| 1b | n/a | management | n/a | Review the diff against this plan (checklist in the master plan), run `pre-commit run --all-files`, commit. |
| 1c | n/a | operator | n/a | Push the branch and watch the `Smoke tests (collection)` job (via `ci-status shakenfist/shakenfist runs --branch cluster-op-visibility`). The agentops and networking smoke tests exercise both helpers. Re-run at least twice if green to gain flake confidence, since the race is timing dependent (a billing sweep must land inside the await window to have been dangerous). |

## Risks and notes

* The by-target endpoint hydrates every historical op for the
  object (one DB read per op). Test-lifetime objects are
  young, so history is short; this is acceptable for CI
  polling and is replaced by the cheap pending-field poll in
  phase 5.
* The suite deploys a client built from client-python
  develop; `list_cluster_operations_for_target()` and the
  server capability both landed with network-facade phase 7
  and are present on develop. If `IncapableException` fires
  in CI, something is genuinely wrong with the deploy — do
  not swallow it.
* Semantics change for `error`: today an error op only fails
  the await if it happens to be the pointer target; the new
  rule fails on any watched op erroring. This is stricter and
  correct, but if an existing test relied on an unrelated op
  erroring mid-await, it will now fail visibly — treat that
  as a real bug surfaced, not a regression to paper over.

## Success criteria

* `tox -eflake8 -- -HEAD` and `pre-commit run --all-files`
  pass.
* Both helpers share one implementation; behaviour matches
  the await rule above.
* A green `Smoke tests (collection)` run on the branch,
  including `test_interface_plug_and_exec_reboot` and
  `test_provided_extra_dns` (the network-helper user).
* Master plan Execution table and `docs/plans/index.md`
  updated to reflect phase status.
