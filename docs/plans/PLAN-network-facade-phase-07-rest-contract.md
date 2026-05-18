# Phase 7: REST contract — remove redirect_to_network_node, flip endpoints to 202+poll, add cluster-operation discovery endpoints

## Context

After Phase 6's maintain rewrite, the network facade
work is functionally complete on the dispatcher side.
What remains is the user-facing surface: the REST API
still carries the `redirect_to_network_node` decorator
on four endpoints, and there is no way for a REST
client to discover the chain of cluster operations
spawned by a given request. Phase 7 finishes both:

1. **Remove `redirect_to_network_node`** from three of
   its four call sites. The decorator proxies HTTP
   requests from the receiving API server to the
   network node's gunicorn on port 13000 — an early
   workaround for handlers that needed to run on the
   network node. After phases 2–5, the underlying work
   is queue-based; the API server can enqueue from
   anywhere and the net-worker on the right node
   picks it up.

2. **Flip the affected delete endpoints to 202+poll**
   contract. Today `DELETE /networks/<uuid>` and
   `DELETE /networks` return 200 with synchronous-
   looking response bodies even though the deletion
   work happens asynchronously via the queue. Phase 7
   makes the contract honest: 202 (Accepted) plus the
   tracking op uuid in the response body, matching
   the master plan's open question 10 resolution.

3. **Add two new cluster-operation REST endpoints**
   for chain discoverability, resolving open
   question 12:
   * `GET /cluster_operations/<uuid>/chain` returns
     the transitive `depends_on` closure starting at
     `<uuid>`, scoped to the caller's namespace.
   * `GET /cluster_operations?target_object_type=&target_uuid=`
     returns ops targeting a given object, scoped to
     the caller's namespace.
   
   Both let a 202+poll client trace where in a chain
   a failure happened.

4. **Update `shakenfist/client-python`** (sibling
   repo) so the CLI / library handles the new 202
   response shape transparently. Mikal is the sole
   consumer of both client and server, so the
   contract flip is fine; the client is updated in
   the same change set.

5. **Deferred for Phase 7**: the fourth
   `redirect_to_network_node` site is
   `NetworkPingEndpoint.get` at
   `shakenfist/external_api/network.py:546`. The
   ping handler executes
   `ip netns exec <network_uuid> ping -c 10 <addr>`
   directly and returns its stdout/stderr
   synchronously. The network namespace exists only
   on the elected network node, so this handler
   genuinely needs to run there. Migrating it to be
   queue-based requires new infrastructure to store
   and surface op output (today the queue carries
   only error reports, not command output). Phase 7
   keeps the redirect on the ping endpoint as a
   tactical exception and documents the migration
   as future work. The decorator definition in
   `shakenfist/external_api/base.py:336` stays for
   this one use; future work can either migrate
   ping (introducing op-output infrastructure) or
   inline the redirect.

## What Phase 7 ships

1. **Two new REST endpoints** under
   `/cluster_operations/`:
   * `GET /cluster_operations/<op_uuid>/chain` —
     walks the `depends_on` graph from `<op_uuid>`,
     returning the full transitive ancestor closure
     as a list of op summaries. Namespace-scoped at
     the SQL layer (admin sees everything; non-admin
     sees only ops whose targets are in their
     namespaces). The implementation may use a
     SQL recursive CTE (MariaDB 10.2+) or a
     Python-side BFS over `get_cluster_operation`
     calls — the implementing agent picks whichever
     is cleaner.
   * `GET /cluster_operations?target_object_type=&target_uuid=`
     — lists ops targeting the given object, scoped
     by namespace. The backing query is on the
     `cluster_operation_targets` table; the
     namespace filter happens at the SQL layer (no
     full-table scan with Python filtering).

2. **Removed redirect_to_network_node decorator** from
   three of its four sites in `shakenfist/external_api/`:
   * `interface.py:62` — `InterfaceEndpoint.get`. Read
     (no async work); just remove the decorator.
   * `network.py:143` — `NetworkEndpoint.delete`
     (single-network). Remove decorator AND flip to
     202+poll response.
   * `network.py:265` — `NetworksEndpoint.delete`
     (bulk in namespace). Remove decorator AND flip
     to 202+poll response.

3. **Kept** redirect on the fourth site:
   * `network.py:546` — `NetworkPingEndpoint.get`.
     Decorator stays. Future-work pointer added.

4. **202+poll response shape** for the two delete
   endpoints. Format: HTTP 202, body
   `{'op_type': 'net_op', 'op_uuid': '<uuid>'}` for
   the single-network case;
   `[{'network_uuid': '<u1>', 'op_type': 'net_op',
   'op_uuid': '<o1>'}, ...]` for the bulk case. The
   bulk case may also benefit from a top-level
   wrapper if there's an existing convention; check
   the codebase.

5. **Updated `shakenfist/client-python`** (sibling
   repo at
   `/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python`):
   * `apiclient.py:delete_network` (line 866) and
     `delete_all_networks` (around line 876) handle
     the new 202 response. By default, they
     transparently poll the returned op until it
     reaches terminal state, raising on error —
     preserving today's caller experience. An opt-in
     `wait=False` kwarg lets advanced callers get
     the op handle back without polling.
   * Add `apiclient.get_cluster_operation_chain(op_uuid)`
     and
     `apiclient.list_cluster_operations(target_object_type, target_uuid)`
     methods for the two new endpoints.
   * Update affected tests in
     `client-python/shakenfist_client/tests/test_client_apiclient.py`.

6. **Documentation update** covering: the contract
   change for the two delete endpoints; the two
   new cluster-operation discovery endpoints; the
   ping endpoint's deferred status; the
   client-python changes.

## What Phase 7 does **not** do

* Does not remove the `redirect_to_network_node`
  decorator definition. The fourth site (ping)
  still uses it.
* Does not migrate `NetworkPingEndpoint.get` to be
  queue-based. That requires new op-output
  infrastructure and is deferred to future work.
* Does not touch the other three redirect
  decorators (`redirect_instance_request`,
  `redirect_to_eventlog_node`,
  `redirect_upload_request`). Master plan's Q10
  scope explicitly limits Phase 7 to
  `redirect_to_network_node`.
* Does not remove the `get_lock` wrappers — Phase 8.

## Key references in the existing code

* `shakenfist/external_api/base.py:336` — the
  decorator definition. Stays for the fourth site.
* `shakenfist/external_api/interface.py:62` —
  `InterfaceEndpoint.get` (sync read; remove
  decorator only).
* `shakenfist/external_api/network.py:143` —
  `NetworkEndpoint.delete` (single).
* `shakenfist/external_api/network.py:265` —
  `NetworksEndpoint.delete` (bulk).
* `shakenfist/external_api/network.py:546` —
  `NetworkPingEndpoint.get` (decorator stays).
* `shakenfist/external_api/network.py:64-72` —
  `_delete_network` helper, which already enqueues
  `network_apply_delete_network_node` after Phase
  6c. The 202+poll shape just needs to capture the
  op uuid from the enqueue call.
* `shakenfist/external_api/clusteroperations.py`
  (existence to confirm via `ls`) — likely where
  the existing `/cluster_operations` endpoints
  live. The two new endpoints go alongside.
* `shakenfist/external_api/app.py` — Flask app
  setup. Register the two new endpoints.
* `shakenfist/mariadb.py` — existing
  `cluster_operation_targets` helpers
  (`has_pending_cluster_operation_target`,
  `get_recent_terminal_op_states_for_target`) are
  precedents for the new helpers. The new
  `list_cluster_operations_for_target` (or
  similar) should follow the three-layer pattern
  + namespace-scoped SQL filter.
* `/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python/shakenfist_client/apiclient.py`
  — the client library. `delete_network` at line
  866, `delete_all_networks` around line 876.
* `/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python/shakenfist_client/tests/test_client_apiclient.py`
  — client tests to update.

## Success criteria

Phase 7 is complete when:

* `grep -n "@api_base.redirect_to_network_node"
  shakenfist/external_api/network.py
  shakenfist/external_api/interface.py` returns
  exactly **one** hit (on `NetworkPingEndpoint.get`).
  The other three sites have been removed.

* `DELETE /networks/<uuid>` returns HTTP 202 with
  `{'op_type': 'net_op', 'op_uuid': '...'}` in the
  body. `DELETE /networks` returns 202 with the
  bulk equivalent.

* `GET /cluster_operations/<uuid>/chain` returns the
  full ancestor closure (a list) for a chain of
  ops, scoped to the caller's namespace.
* `GET /cluster_operations?target_object_type=NETWORK
  &target_uuid=<uuid>` returns the list of ops
  targeting that network, scoped by namespace.

* `shakenfist/client-python/apiclient.py:delete_network`
  and `delete_all_networks` handle the 202 response,
  defaulting to transparent polling for completion.
  An opt-in `wait=False` returns the op handle
  unmangled.

* `shakenfist/client-python/apiclient.py` has
  `get_cluster_operation_chain` and
  `list_cluster_operations` methods (or
  similar-named) for the two new endpoints.

* `pre-commit run --all-files` passes in both the
  server and client repos.

* `tox -e py3` shows no regressions in either repo.

* cluster_ci functional suite passes on the
  phase 7 server PR.

* `ARCHITECTURE.md`, `AGENTS.md`, and the developer
  guide describe the new REST contract and the
  discovery endpoints.

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a. Cluster-operation discovery endpoints | high | opus | none | Add two new REST endpoints in `shakenfist/external_api/clusteroperations.py` (or whatever file the existing `/cluster_operations` endpoints live in — `ls shakenfist/external_api/` to check; create a new file if absent following the pattern of other endpoints). **Endpoint 1**: `GET /cluster_operations/<op_uuid>/chain`. The handler walks the `depends_on` graph from `<op_uuid>` and returns the transitive ancestor closure as a list of op-summary dicts. Use the existing `mariadb.get_cluster_operation(op_uuid)` plus the `depends_on` field on each op to traverse — or add a new MariaDB helper if you prefer SQL recursive CTE. Namespace scoping: each op in the chain has targets in `cluster_operation_targets`; resolve target → namespace → check against caller's namespace. Admin sees everything. Return 404 if the starting op uuid does not exist; return 403 if any chain member is in a foreign namespace AND the caller is not admin. **Endpoint 2**: `GET /cluster_operations?target_object_type=<type>&target_uuid=<uuid>`. Returns the list of ops targeting that object. Adds a new MariaDB helper `list_cluster_operations_for_target(target_object_type, target_uuid, namespace=None)` (three-layer + gRPC + proto regen) that joins `cluster_operation_targets` against object-namespace storage and filters by caller namespace at the SQL layer. The handler validates `target_object_type` against the `ObjectType` enum. Both endpoints require auth via the existing `verify_token` decorator. Register the endpoints in `shakenfist/external_api/app.py`. Add Swagger annotations matching the style of existing endpoints (use `swag_from(api_base.swagger_helper(...))`). Add unit tests in `shakenfist/tests/external_api/test_clusteroperations.py` (existing or new) covering: happy path returns the expected ops; non-admin caller in foreign namespace gets 403 (or filtered list); empty result for an unknown uuid; 404 on missing op for the chain endpoint. Commit message subject: `external_api: add cluster-operation discovery endpoints.` |
| 7b. Remove redirect + flip delete endpoints to 202+poll | high | opus | none | Three changes in `shakenfist/external_api/`. (1) `interface.py:62`: remove `@api_base.redirect_to_network_node` from `InterfaceEndpoint.get`. The handler body (`api_util.safe_get_network_interface(...); return ni.external_view()`) is purely a database read; it can run on any node. No 202+poll change needed — it's a synchronous read. (2) `network.py:143`: remove the decorator from `NetworkEndpoint.delete`. Change the handler to: enqueue the delete (already done via `_delete_network` after Phase 6c), capture the op handle, return HTTP 202 with `{'op_type': op.object_type, 'op_uuid': str(op.uuid)}` in the body. Read `_delete_network` in this same file to confirm what it returns — it may need to be updated to return the op handle so the endpoint can pass it through. (3) `network.py:265`: remove the decorator from `NetworksEndpoint.delete`. Change to return HTTP 202 with a list of `{'network_uuid': '...', 'op_type': 'net_op', 'op_uuid': '...'}` entries. The current body returns a list of UUIDs; the new shape carries the op uuid too. **Important**: the `flask.Response` / `flask.make_response` machinery is used elsewhere in the codebase to set the HTTP status code on a JSON return; grep for examples in `shakenfist/external_api/` to find the pattern. Use the same pattern for setting status=202. **Keep** the decorator on `network.py:546` (`NetworkPingEndpoint.get`); add a code comment near it explaining the deferred status: the ping handler executes `ip netns exec` directly, so it needs to run on the network node; migrating to queue-based ping requires op-output infrastructure not yet built (see `PLAN-network-facade.md` future work). Confirm via grep: `grep -n "@api_base.redirect_to_network_node" shakenfist/external_api/network.py shakenfist/external_api/interface.py` returns exactly one hit (the ping site). Update affected tests in `shakenfist/tests/external_api/test_network.py` and `test_interface.py` (existing or new) to assert the new 202 response shape. Commit message subject: `external_api: redirect_to_network_node removal and 202+poll.` |
| 7c. Update client-python | high | opus | none | Switch to the client-python repo at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python`. **Important: this is a sibling git repo.** All changes happen there, on a feature branch named `network-facade-phase-07`. Push the branch when done; do NOT create a PR (per `CLAUDE.md` "Never create github pull requests"). The changes: (1) `shakenfist_client/apiclient.py:866` (`delete_network`) — handle the new 202 response. Default behaviour: detect 202, extract op_uuid, poll the new `GET /cluster_operations/<op_uuid>` endpoint at a small interval (1 second is fine) until the op reaches a terminal state. If state is ERROR, raise an exception carrying the error report. Return the original response body for compatibility with existing callers expecting "info about the network". Add a `wait=True` kwarg (default True) so advanced callers can opt out with `wait=False` and get the (op_type, op_uuid) handle back. (2) `shakenfist_client/apiclient.py:delete_all_networks` (line 876ish) — same treatment for the bulk endpoint; the response is a list, so the wait logic polls each op in parallel or sequentially. (3) Add two new methods: `get_cluster_operation_chain(op_uuid)` and `list_cluster_operations_for_target(target_object_type, target_uuid)` that call the new endpoints from step 7a. (4) Update tests in `shakenfist_client/tests/test_client_apiclient.py` for the changes above; the existing `test_delete_network` may need to mock the new 202 + polling path. (5) Update any CLI commands in `shakenfist_client/commandline/network.py` that invoke `delete_network` / `delete_all_networks` to pass `wait=True` (the default) — they should keep their current behaviour. After all changes pass `pre-commit run --all-files` and `tox -e py3` (or whatever the client repo's test runner is — check first), commit on the feature branch and push. Report back the new commit SHA. Commit message subject (in client-python): `apiclient: handle 202+poll delete and cluster-op discovery.` |
| 7d. Documentation | medium | sonnet | none | (Server-side, in this repo.) Update three docs. (1) `ARCHITECTURE.md`: amend the "REST API URL Structure" section if it exists, or add a paragraph in the existing API-related section, describing: the new 202+poll contract for `DELETE /networks/<uuid>` and `DELETE /networks`; the new `GET /cluster_operations/<uuid>/chain` and `GET /cluster_operations?target_*=` endpoints; that three of four `redirect_to_network_node` sites have been removed and the fourth (ping) is deferred future work. Note that the migration of the other redirect decorators (`redirect_instance_request`, etc.) remains future work. (2) `AGENTS.md`: rename the "Network facade (Phases 2-6)" subsection to "(Phases 2-7)", append a one-paragraph note on the REST contract changes. (3) `docs/developer_guide/network_dispatcher.md`: add a "Phase 7: REST contract" section with the endpoint details and a note that the client library transparently polls by default. Commit message subject: `docs: phase 7 REST contract changes.` |

## Step ordering and dependencies

* 7a (discovery endpoints) is independent and lands first. The client-side changes in 7c will reference these endpoints.
* 7b (redirect removal + 202+poll flip) is independent of 7a but should land before 7c so that the client has a real 202 to test against.
* 7c (client-python) depends on 7a and 7b — it consumes both the new endpoints and the new response shape.
* 7d (docs) lands last.

Recommended order: 7a → 7b → 7c → 7d.

## Back brief

Before executing any step, the implementing sub-agent must back brief the management session. Each agent should explicitly confirm:

* The ping endpoint exception. `NetworkPingEndpoint.get` keeps `@redirect_to_network_node` because it executes `ip netns exec` directly. The decorator definition stays in `base.py`. The plan does not migrate ping in this phase.

* The 202+poll shape applies only to the two delete endpoints (`NetworkEndpoint.delete` and `NetworksEndpoint.delete`). `InterfaceEndpoint.get` is a synchronous read that doesn't need 202+poll — just lose the decorator.

* Namespace scoping for the discovery endpoints. The chain endpoint returns 403 if any chain member is in a foreign namespace and the caller is not admin (alternatively: filter the chain to the visible subset; pick whichever is more useful and document it). The target query endpoint filters at the SQL layer to avoid Python-side filtering of large result sets.

* Client-python is a **separate git repo**. Switch directory to it for step 7c. Commit and push there; do not create a PR.

* The wait semantics for client-side polling. `delete_network(wait=True)` (default) blocks until terminal state and raises on error — preserves today's caller experience. `delete_network(wait=False)` returns the op handle without polling for advanced callers.

## Review checklist for the management session

After each step's sub-agent reports completion:

- [ ] Named files were modified; no unrelated files changed.
- [ ] `pre-commit run --files <changed files>` passes (in the relevant repo).
- [ ] New unit tests pass.
- [ ] Commit message subject ≤ 50 chars, period-terminated, body wraps at 75 per `CLAUDE.md`.
- [ ] Commit body includes the `Prompt:` paragraph plus `Co-Authored-By` / `Signed-off-by` lines.
- [ ] For step 7a: if proto files changed (new gRPC RPC), stubs were regenerated via `tox -e genprotos`.
- [ ] For step 7b: the grep for `@api_base.redirect_to_network_node` returns exactly one hit (ping site).
- [ ] For step 7c: the work is in the client-python repo on a feature branch; the management session has the new commit SHA.

After all steps complete:

- [ ] cluster_ci functional smoke suite passes on the phase 7 server PR.
- [ ] No new `ERROR` / `Traceback` lines in the cluster_ci stable-log gate.
- [ ] The 202 response from `DELETE /networks/<uuid>` is verifiable via curl in a running cluster (or via cluster_ci output).
- [ ] Master plan execution table for Phase 7 is updated from `Planning` to `Complete`.
