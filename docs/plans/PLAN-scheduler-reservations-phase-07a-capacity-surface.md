# Scheduler reservations phase 7a: an operator-readable capacity surface

## Prompt

This is a phase plan under
[PLAN-scheduler-reservations.md](PLAN-scheduler-reservations.md).
The master plan's Prompt section applies unchanged. D-numbers are
the phase 0 decisions in
[PLAN-scheduler-reservations-phase-00-decisions.md](PLAN-scheduler-reservations-phase-00-decisions.md),
P-numbers are phase 3's in
[PLAN-scheduler-reservations-phase-03-primitive.md](PLAN-scheduler-reservations-phase-03-primitive.md),
and G-numbers are phase 7's in
[PLAN-scheduler-reservations-phase-07-diagnostics.md](PLAN-scheduler-reservations-phase-07-diagnostics.md).
Phase 7b's plan lives in shakenfist/client-python as
`docs/plans/PLAN-scheduler-reservations-phase-07b-client-views.md`
and numbers its decisions D1..D8, which collide with phase 0's;
where this plan cites one it says "7b's D4". This phase's own
decisions are numbered H1..H9.

Planning effort: **high**. The code is small, but it adds a gRPC
RPC, changes a request message, touches the scheduler's metrics
loader that `find_candidates()` also reads, and lands across two
repositories in an order that matters. Review effort: **medium**.
Every step is independently falsifiable, and only step 3 can
change behaviour that is not new.

## Situation

The master plan's survey table (under *Phase scope stubs*, "Phases
7a and 7b were added on 2026-10-20") lists what this plan publishes
and where an operator can read it. Apart from the per-node ledger,
which has a client verb and 7b is giving a command, the answers are
"nowhere" or "`curl` only". 7a is the server half: it makes the
cluster claims ledger, every claim in the cluster, and the reasons
a hypervisor is missing from `/admin/resources` readable over REST,
and then gives each of them an `sf-client` reader.

## Mission and problem statement

An operator should be able to answer three questions without
`curl` or a database shell:

1. How much of the cluster is promised to claims, how much is used
   by unclaimed namespaces, and how much is left for either? This
   is the singleton both capacity guards refuse against.
2. Which claims exist across the whole cluster, and which of them
   still cover placements?
3. Why is this hypervisor missing from the capacity view?

Each answer has to come from the counters the guards actually use,
not from a second ledger derived beside them.

## Scope

In scope:

- A read of the `cluster_capacity` singleton, published in
  `/admin/resources`.
- An admin-only, cross-namespace claim listing, with its
  `coverage_state` filter applied in SQL.
- The reason each active node is missing from `per_node`,
  published beside it.
- Capability tokens for all three, and a real
  `admin_resources_get_example`.
- Unit, live-MariaDB and functional coverage.
- The `apiclient` verb and `sf-client` readers in client-python
  (7b's D7).
- API reference and operator guide documentation.

Out of scope, each for a stated reason:

- **Rewriting `/admin/resources` as a read over the typed capacity
  columns.** That is phase 5's D11 third call site. This phase
  adds keys to `summarize_resources()` and leaves its
  `Scheduler()` construction alone (H7).
- **Any change to admission or ranking.** No guard, filter stage or
  ranking term moves. Step 3 touches the metrics loader that
  `find_candidates()` reads, and the Definition of done pins that
  its candidate behaviour is unchanged.
- **The N+1 in the per-namespace claims listing, beyond the shared
  render path.** H5 fixes it for both listings, because they share
  one render function. A general audit of `external_view()`
  re-reads is not this phase's.
- **Teaching CI's sizing tooling to read the exclusion reasons.**
  `shakenfist_ci/sizing.py` and `retries.py` would benefit. The
  benefit is real but optional, and it is recorded under Future
  work.
- **A release.** Phase 8 cuts one covering phases 4b to 7b (4b's
  D7).

## What the survey found

Surveyed against shakenfist `9efbff1481` and client-python
`fdcfe6e` (both `develop`, 2026-10-25). The 7a stub was written on
2026-10-20 from shakenfist `ebc0f9772b`. The corrections below
have been applied at source, in the master plan's 7a and phase 5
stubs and in phase 7's Future work, as part of this planning
commit. A later step does not need to redo them.

1. **There is no "read the claim guard makes".** The stub says the
   singleton should be "published from the same read the claim
   guard makes". Both guards on the singleton are guarded
   `UPDATE`s. Neither reads it:
   - the unclaimed-placement guard at `mariadb.py:27577-27586` is
     `unclaimed_used + x <= total - claimed`;
   - the claim guard, `_claim_capacity_guard()` at
     `mariadb.py:28440`, is `claimed + limit + GREATEST(0,
     unclaimed_used - migrated) <= total`.

   The only reads of the row are post-rollback diagnostics: the
   cluster stage of `_admission_denial_dimensions()` at
   `mariadb.py:27287-27300`, and `_cluster_capacity_dimensions()`
   at `:28633`. The other reader is the reconciler's own reply,
   whose `ClusterCapacity` message is at `database.proto:2775`
   and is decoded at `mariadb.py:26300-26311`. No RPC reads the
   singleton on demand, so this phase adds one.

   What the stub meant is still achievable, and it is simpler than
   it looked. **Both guards test against the same quantity**,
   `total - claimed - unclaimed_used`. The claim guard's migration
   term only ever adds to the headroom of the one namespace being
   migrated. So there is exactly one per-dimension headroom figure
   to publish, and it can be pinned against both guards (H2).
2. **A hypervisor vanishes from `per_node` in five ways, not one.**
   The stub names only the queue-length exclusion
   (`scheduler.py:1088-1090`). The full set:
   - **The node is not `active`.** `get_active_node_metrics()`
     iterates `Nodes([], prefilter='active')` (`scheduler.py:51`).
   - **Its metrics row is stale** (over 120s, `:58`).
   - **It has no metrics row** (`:66-71`).
   - **Its metrics read raised** `ReadException` (`:80-84`). The
     node is then left out of the metrics dict entirely.
   - **Its queue is too long** (`:1088`).

   The stale and empty cases are worse than "dropped without a
   trace". Both store `metrics[node] = {}`. `summarize_resources()`
   reads `is_hypervisor` *from the metrics dict* (`:1085`), so it
   skips such a hypervisor as a non-hypervisor. **The server
   itself cannot tell a stale hypervisor from a network node
   today.** Each of the three metrics cases already writes an
   audit event naming its cause, so the information exists at the
   point the decision is made. It is just not kept.
3. **`get_namespace_claims('')` lists every claim, and the object
   layer already uses it as the admin view.**
   `NamespaceClaims(namespace=None)` calls it with no restriction
   (`namespace_claim.py:450-451`). No REST endpoint reaches that
   path, and it has no `coverage_state` parameter:
   `GetNamespaceClaimsRequest` carries only `namespace`
   (`database.proto:3107`). The filter needs a proto field. In the
   table the column is `state`; `coverage_state` is the name it is
   published under (`namespace_claim.py:375`).
4. **The claims listing makes N+1 reads.** `NamespaceClaims`
   iterates rows it has already read. `NamespaceClaim.external_view()`
   then calls `self._row()`, which is `mariadb.get_namespace_claim()`
   (`namespace_claim.py:251-252`), once per claim. A cluster-wide
   listing is where that is worst.
5. **The API reference documents none of `/admin/resources`.**
   `docs/developer_guide/api_reference/admin.md` has sections for
   locks, VDI token public keys and the CA certificate, and nothing
   for resources. Meanwhile `external_api/admin.py:1-6` claims
   "API reference docs exist: yes ... include examples: yes", and
   the example body at `:103-105` is `{ ... }`. The operator guide
   covers the fields (`docs/operator_guide/scheduler.md:398`,
   `:893-904`).
6. **Phase 5's D11 rewrites the same endpoint.** "`admin.py`'s
   capacity view becomes a read over the typed capacity columns"
   (phase 0 decisions, D11). Whatever this phase adds to
   `summarize_resources()`, phase 5 has to carry through that
   rewrite. The phase 5 stub now says so, and the functional tests
   added here pin the keys.
7. **7b is planned, but not pushed.** Its plan is on
   client-python branch `scheduler-reservations-phase-07b-client-views`
   at `3db3dab`, in worktree `client-python-wt-sr-p07b`, with no
   pull request. Step 8 here adds to 7b's `node capacity` command,
   so it cannot start until 7b's step 4 has merged. Since that
   survey, client-python `develop` has moved from `d789e0b` to
   `fdcfe6e`, so 7b's line references need rechecking when it
   executes. That is 7b's concern, and it is noted here only
   because this phase depends on 7b.
8. **Two stub line references had drifted.** `mariadb.py:28175`
   (the guard) and `:29023` (the cluster-wide listing comment) are
   now `:27577` / `:28440` and `:29296`. `scheduler.py:1053` and
   `:1089` and `admin.py:103` are still accurate.
9. **Phase 7's Future work records an undone item that has been
   done.** It says the census collector's LogQL filter in
   `shakenfist/actions` stops above the guard, and that "no issue
   has been filed yet". That filter was widened on 2026-09-05 by
   `shakenfist/actions` `0c87e8d48` ("Collect the capacity guard's
   refusals too"), and `tools/ci_headroom_collect.sh:187-190` now
   matches all three guard messages. The phase 7 plan now carries
   a dated correction.

Phase 7's falsifiable Definition of done items still hold. There
are seven events routes in `app.py`; `ci_headroom_report.py`
imports only the standard library (`os` was added since, by
#4455); and `shortfall` is `optional` on the wire
(`database.proto:2863`).

## Decisions

**H1. The singleton goes into `/admin/resources` as a top-level
`cluster_capacity` key, not onto an endpoint of its own.** The
stub left this open. The reader that matters is 7b's
`node capacity`, which already fetches `/admin/resources`, and
one fetch means the per-node and cluster ledgers come from the
same moment. That is not a transaction, but it is close enough
that a reader is not comparing numbers taken minutes apart. The
key is named for the table, so a reader can grep from the response
to the schema. It is deliberately not folded into the existing
`total` key. `total.cpu_available` is a sum of per-node headroom,
bounded by measurement. `cluster_capacity.total_cpus` is a sum of
node *limits*. Putting the two side by side under one key is
exactly the confusion D2 of 7b refuses to create in a table.

**H2. Publish the singleton's own nine counters, plus one derived
headroom per dimension, `unallocated_<dim> = total - claimed -
unclaimed_used`.** The stub forbids re-deriving the counters by
summing claim rows, and nothing here does: the counters are the
row. The headroom is derived, because every reader wants it. If
each client computes it, the arithmetic exists in several places,
and that is 7b's own first risk (the binding marker drifting from
the server). It is computed in one Python function, beside the
read, whose docstring names both guard expressions it mirrors.
A live-MariaDB test pins it against both guards (DoD).
`unallocated_*` can be negative. Admission cannot drive it below
zero, but the reconciler rebuilds `claimed_*` from the sum of
every active claim's limits, whether or not they fit. A negative
figure is published as it is, the same as a negative
`cpu_available`.

**H3. Absent and unreadable are two flags, and neither is a
zero.** `cluster_capacity` always carries `row_present` and
`degraded`. If the row is absent (the reconciler has never run,
the P7 normal state) or the read failed, all twelve figures are
`null`. That follows `cpu_committed_row_present` and
`capacity_degraded`, and it is what 7b's D5 (`-` for a server
`None`) already renders. `updated_at` is published too. It is the
row's last write by admission or by the reconciler, not the last
reconcile, and the docs say so.

**H4. The read is bounded, and it swallows failures to `degraded`,
like the per-node read in the same response.** It is
`GetClusterCapacity`, with a `{}` request and a reply carrying
`ClusterCapacity cluster`, `bool present`, `bool degraded` and
`double updated_at`, reusing the reconciler's message. The admin
endpoint is not a hot path, so it could take the default budget.
It does not, for two reasons. The CI headroom probe and the
capacity wait poll this endpoint throughout every cluster job.
And a response in which one read gives up after 10 seconds while
another hangs for the full retry budget is harder to reason about
than one in which both degrade the same way.

**H5. The cross-namespace listing is `GET /admin/claims`, with an
optional `coverage_state` of `active` or `expired`, and it renders
from the rows it already read.** Three alternatives were
considered:

- `/auth/namespaces/claims` would shadow a namespace named
  `claims`.
- `/auth/claims` sits in a family whose federation surface already
  says "claims" for JWT `bound_claims`. The OIDC plan will make
  that collision worse.
- `/admin` is where cluster-wide, admin-only capacity views
  already live (`/admin/resources`), and the operator who reads
  one reads the other.

Any other value of `coverage_state` gets a 400. Neither filter
touches object state: a `deleted`-state claim still holding
capacity is listed, for the reason `NamespaceClaims.__iter__`
gives (`namespace_claim.py:459-480`).

`NamespaceClaim.external_view()` gains an optional `row` argument.
Both this listing and the per-namespace one pass the row the
iterator already holds, so listing N claims costs one claims read
rather than N+1. The per-namespace endpoint is included because
the two share the render path, and fixing one without the other
would leave two shapes of the same function.

**H6. Exclusions are published as `excluded: {node_uuid: {reason,
...}}`, covering active nodes only.** The reasons are `queue too
long` (with `node_queue_waiting` and the threshold, reusing
`_has_reasonable_queue_state()`'s dict at `scheduler.py:208-219`),
`stale metrics` (with the row's age), `no metrics` and
`metrics unreadable`.

Non-active nodes are left out because a client already has their
state from `get_nodes()`, and 7b's view joins against it anyway.
Reporting them would cost a second, unfiltered `Nodes` walk on an
endpoint CI polls continuously, to publish something already
published.

The three metrics reasons are reported for *any* active node,
whatever its role. The server cannot know the role of a node
whose metrics it could not read, and that is finding 2's point.
The client filters on `is_hypervisor` from `get_nodes()`. A
non-hypervisor with *fresh* metrics is not a candidate and is not
"excluded", so it is not listed.

The reasons are captured where the decision is already made:
`get_active_node_metrics()` takes an optional dict to record
them, and `Scheduler.refresh_metrics()` keeps it as
`self.metrics_unavailable`. Its return value and the
`self.metrics` shape do not change, so `find_candidates()`'s
input is byte-for-byte what it was.

**H7. This phase extends `summarize_resources()` and leaves the
endpoint's `Scheduler()` construction to phase 5.** Migrating the
admin view onto typed-column reads is D11's, and doing half of it
here would leave phase 5 with a view half-migrated by someone
else. The cost is that phase 5 inherits two more keys to carry.
The phase 5 stub now says so, and step 6's functional tests fail
if a rewrite drops them.

**H8. One capability token per new surface, in the `admin`
family: `cluster-capacity`, `cluster-resources-excluded` and
`cluster-claims`.** Splitting them costs nothing, and it follows
the comment on `namespace-events` / `namespace-claim-events`
(`app.py:418-425`): separate reads are advertised separately. A
client meeting a server without a token renders 7b's D5 `n/a`
rather than guessing, which is what issue 4003 says happens
otherwise. `test_root.py`'s `EXPECTED_CAPABILITIES` snapshot is
updated in the same commit.

**H9. The client lands as two pull requests, on either side of the
server's.** The verb `get_cluster_claims()` is tiny, and it can
merge to client-python `develop` before the server does. It is
gated on its token, so it simply raises `IncapableException`
against an old server. It has to merge first. Cluster CI builds
the client from `develop` (4b's survey correction), and
`test_namespace_claims.py` routes every request through a client
verb on purpose, so step 6's functional test cannot pass until the
verb exists.

The CLI readers merge last. They extend 7b's `node capacity`,
which does not exist yet. They are also the best test of the
response shapes, so writing them against a shape that might still
change in review is the double-work 7b's D7 was written to avoid.

So there are three pull requests: client-python (step 5), then
shakenfist (steps 1 to 4, 6, 7 and 9), then client-python
(step 8). Step 9's close-out goes in the shakenfist pull request,
because the master plan lives there. Step 8 records its own
outcome in its client pull request, and the 7a row's `Merged`
cell names all three at the next phase's close-out.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | opus | none | **Read the `cluster_capacity` singleton (H2-H4).** Use the `add-grpc-service` skill. In `protos/database.proto`, add `rpc GetClusterCapacity (GetClusterCapacityRequest) returns (GetClusterCapacityReply) {}` beside `GetSchedulerNodeCapacity` (`:413`). Add `message GetClusterCapacityRequest {}` and `message GetClusterCapacityReply { ClusterCapacity cluster = 1; bool present = 2; bool degraded = 3; double updated_at = 4; }`, reusing `ClusterCapacity` (`:2775`). Update that message's comment, which says it is only "the singleton after the pass". Run `tox -e genprotos` and commit the stubs. In `shakenfist/mariadb.py`, add a `ClusterCapacityRead(NamedTuple)` (`row: Optional[ClusterCapacityDict]`, `degraded: bool`), mirroring `SchedulerNodeCapacityRead` (`:26570`), plus the `_direct_`, `_grpc_` and public `get_cluster_capacity()` trio, mirroring `get_scheduler_node_capacity` (`:27925`, `:28078`, `:28237`). The direct read is `sa.select(cluster).where(cluster.c.id == 1)` via `_get_cluster_capacity_table()` (`:3850`). Return `row=None` for an absent row, and `degraded=True` with `row=None` on `OperationalError`. The gRPC wrapper uses `timeout=BOUNDED_QUERY_TIMEOUT, max_slow_failures=1` exactly as `_grpc_get_scheduler_node_capacity` does, and swallows to `degraded=True`. The dict carries the nine counters, `updated_at` as epoch seconds (convert the DB `DateTime` the way the claims row's `updated_at` is converted), and `unallocated_cpus` / `unallocated_memory_mb` / `unallocated_disk_gb`, computed in **one** helper `_cluster_unallocated(row)`. Its docstring quotes both guard expressions it mirrors (the unclaimed guard at `:27577-27586` and `_claim_capacity_guard()` at `:28440`) and says why a negative result is published unclamped (H2). The servicer in `shakenfist/daemons/database/main.py` mirrors `GetSchedulerNodeCapacity` (`:2892-2922`), including the `degraded` forward and the exception-to-degraded path. Register a `get_cluster_capacity` counter wherever `get_scheduler_node_capacity`'s counter is registered. Tests: unit tests for the three layers and the servicer, mirroring the scheduler node capacity ones (absent row, present row, `OperationalError`, gRPC error, daemon-side degraded forwarded). Also add a live test in `shakenfist/tests/test_mariadb_capacity_claims_live.py`. Set a singleton, read `unallocated_cpus = U`, and then assert: a claim of exactly `U` cpus is created and one of `U + 1` is refused; and, with no claim, an unclaimed placement of `U` cpus is admitted and one of `U + 1` is refused at the `cluster` stage. Follow that file's existing fixtures. This is the test that makes H2's "mirrors both guards" a fact. Commit subject: "Read the cluster capacity singleton." |
| 2 | medium | sonnet | none | **Filter claims by coverage state in SQL (H5).** Run this after step 1, because both regenerate protos. Add `string coverage_state = 2;` to `GetNamespaceClaimsRequest` (`database.proto:3107`), with a comment: empty means no filter, and otherwise `active` or `expired`, matched against the table's `state` column. Regenerate the stubs. In `shakenfist/mariadb.py`, give `get_namespace_claims(namespace='', coverage_state='')` (`:29584`), `_direct_get_namespace_claims` (`:29288`) and `_grpc_get_namespace_claims` (`:29473`) the parameter. The direct path adds `.where(claims.c.state == coverage_state)` when it is non-empty, beside the existing namespace clause. Raise `ValueError` for any value other than `''`, `active` or `expired` at the public layer, before any I/O. The servicer `GetNamespaceClaims` (`daemons/database/main.py:3001`) passes it through. Do not touch `claims_in_namespace()` or `Namespace.hard_delete()`'s use: they must keep seeing every claim. Tests: a compiled-statement test asserting the `state` predicate appears only when the filter is given, the gRPC pass-through, and the `ValueError`. Add a live test in `test_mariadb_capacity_claims_live.py`: one active claim and one expired claim (expire it the way that file's existing expiry test does), and each filter returns exactly one. Commit subject: "Filter namespace claims by coverage state." |
| 3 | high | opus | none | **Publish the ledger and the exclusions in `summarize_resources()` (H1, H3, H6).** In `shakenfist/scheduler.py`: (a) give `get_active_node_metrics(unavailable=None)` (`:48-86`) an optional dict. When given, record `{'reason': 'stale metrics', 'metrics_age': <seconds>}`, `{'reason': 'no metrics'}` or `{'reason': 'metrics unreadable'}` against `str(n.uuid)` in the three branches that already event (`:66-84`). Its return value is unchanged. (b) `refresh_metrics()` (`:186`) passes a fresh dict and keeps it as `self.metrics_unavailable`, initialised to `{}` in `__init__` beside `self.metrics`. (c) In `summarize_resources()` (`:1047`), add a top-level `excluded` dict. It is every entry of `self.metrics_unavailable`, plus each hypervisor skipped by the queue check at `:1088-1090`, recorded with the reason dict `_has_reasonable_queue_state()` builds (`:208-219`). Factor that dict's construction so both use one builder, and do not call the method itself, since it logs per call. (d) Add a top-level `cluster_capacity` from `mariadb.get_cluster_capacity()` (step 1), read once, beside `_capacity_by_node()`. Its shape is `row_present`, `degraded`, `updated_at`, the nine counters and the three `unallocated_*`. Every figure is `None` when the row is absent or the read degraded (H3). Extend the block comment at `:1052-1070` to say why this is the singleton's own row and not a sum of claims. **Constraint:** `find_candidates()` must see exactly what it saw before. Do not change `self.metrics`'s shape, and do not add a read to any path `find_candidates()` takes. The existing `test_scheduler.py` suite must pass unmodified, and saying so is part of the step's report. Tests in `test_scheduler.py`, beside `test_summarize_resources_matches_admission` (`:771`): each of the four exclusion reasons appears for the right node; a non-hypervisor with fresh metrics is in neither `per_node` nor `excluded`; the ledger is present, absent and degraded, with `None` figures in the last two; and `unallocated_*` is passed through, not recomputed. Commit subject: "Publish the claims ledger and node exclusions." |
| 4 | medium | opus | none | **`/admin/claims`, the tokens and a real example (H5, H8).** **Back brief gate: before writing code, return the exact JSON response bodies of `/admin/resources` (new keys only) and `/admin/claims` to the management session. These are the shapes client scripts and phase 8's guide will quote.** In `shakenfist/namespace_claim.py`, give `NamespaceClaim.external_view(self, row=None)` an optional row. When it is given, use it instead of `self._row()` (`:251`). Make `NamespaceClaims` able to yield the row with each object; one way is a `with_rows()` generator beside `__iter__` that keeps `apply_filters`. Make `AuthNamespaceClaimsEndpoint.get` (`external_api/auth.py:1556-1562`) use it. Add `AdminClaimsEndpoint` in `shakenfist/external_api/admin.py`: `caller_is_admin`, an optional `coverage_state` query parameter declared through `swagger_helper` (read CLAUDE.md "Parameter declarations are enforced" first; a malformed declaration stops sf-api starting, and `test_parameter_declarations.py` is the check), a 400 for any value other than `active` or `expired`, and a list of `external_view(row=row)` over `get_namespace_claims('', coverage_state)`. It must not iterate in Python to filter. Register `/admin/claims` in `app.py` beside `/admin/resources` (`:548`). Add `cluster-capacity`, `cluster-resources-excluded` and `cluster-claims` to `API_CAPABILITIES['admin']` (`app.py:391`), with a short comment, and update `EXPECTED_CAPABILITIES` in `shakenfist/tests/external_api/test_root.py`. Replace `admin_resources_get_example`'s `{ ... }` (`admin.py:103-105`) with a real two-node body: one node in `per_node`, one in `excluded`, a populated `cluster_capacity` and `total` with `capacity_degraded`. Use every key `summarize_resources()` emits after step 3. Write a claims example for the new endpoint, reusing `claim_example` from `auth.py:1341`. Correct `admin.py`'s header block if anything in it is now false. Tests in `shakenfist/tests/external_api/test_admin.py` and `test_claims.py`: the 400 on a bad `coverage_state`; the filter reaching `get_namespace_claims`; non-admin refusal; and a listing of three claims calling `get_namespace_claims` once and `get_namespace_claim` zero times. Apply that last assertion to both endpoints. `test_openapi_spec.py` must pass. Commit subject: "Add a cluster-wide claims listing." |
| 5 | low | sonnet | worktree | **client-python: the claims listing verb (H9).** In a client-python worktree off `develop` (branch `scheduler-reservations-phase-07a-client-verb`), add `get_cluster_claims(self, coverage_state=None)` to `shakenfist_client/apiclient.py`, beside `get_namespace_claims` (`:1710`). Check the `cluster-claims` capability first and raise `IncapableException` with the house wording (`get_cluster_resources` at `:1853` is the model). Then `GET /admin/claims`, adding `?coverage_state=` only when given. Tests in `tests/test_client_apiclient.py`, following the `get_namespace_claims` tests: the URL with and without the filter, and the capability refusal. No CLI here; that is step 8. This is its own client pull request, and it should merge **before** the shakenfist pull request, whose step 6 calls it. Run client-python's `pre-commit run --all-files`. Commit subject: "Add get_cluster_claims to the API client." |
| 6 | medium | sonnet | none | **Functional coverage, on every cluster topology.** In `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_nodes.py`, beside `test_cluster_resources_reservations` (`:73`), add a test that `/admin/resources` carries `cluster_capacity` with `row_present` true once the reconciler has run. The nine counters and three `unallocated_*` must be ints, `updated_at` a number, and `unallocated_<dim>` must equal `total - claimed - unclaimed_used` from the same response. Use the existing reconciler wait if the file or `retries.py` has one, and say which you used. In the same test, assert that every node from `get_nodes()` whose `state` is in `NODE_ACTIVE_STATES` (`shakenfist/constants.py:47`: `initial`, `created`, `degraded`, which is what `prefilter='active'` resolves to) and whose `is_hypervisor` is true is in exactly one of `per_node` or `excluded`. That is the set equality 7b's DoD asserts client-side, now asserted against the server. In `test_namespace_claims.py`, using the client verbs only (the file's own rule), create a claim in the test's namespace. It must appear in `get_cluster_claims()` and in `get_cluster_claims('active')`, and not in `get_cluster_claims('expired')`. This needs step 5 merged to client-python `develop`, so check that before relying on a CI run. Commit subject: "Cover the capacity surface functionally." |
| 7 | medium | sonnet | none | **Documentation.** In `docs/developer_guide/api_reference/admin.md`, add `## Resources` and `## Claims` sections in the existing sections' form: an overview, the REST call link, and a Python client example (`get_cluster_resources()` and `get_cluster_claims()`). Link the operator guide for meanings rather than restating them. In `docs/operator_guide/scheduler.md`, near `:893`, document `cluster_capacity`: what the counters are, that `unallocated_*` is what both the unclaimed-placement guard and claim creation test against, why it can be negative, that `updated_at` is the last write rather than the last reconcile, and what `row_present` and `degraded` distinguish. Document `excluded` and its four reasons, including that a stale hypervisor used to be indistinguishable from a network node. In the claims section of the same guide, document `/admin/claims` and that it lists claims whose object state is `deleted` on purpose. Do not touch `docs/operator_guide/database.md` unless it lists RPCs; there is no schema change. Commit subject: "Document the capacity surface." |
| 8 | high | opus | worktree | **client-python: the readers (H9, 7b's D7).** **Starts only after 7b's step 4 (`sf-client node capacity`) and this phase's shakenfist pull request have both merged.** **Back brief gate: return a mocked rendering of all three output modes before writing code, as 7b's step 4 did.** In a client-python worktree off `develop`: (a) add a cluster claims ledger table under `node capacity`'s pretty output (columns: dimension, total, claimed, unclaimed used, unallocated), plus the ledger's keys in simple mode, with `row_present` false rendering 7b's D5 `-` and `degraded` true adding a banner line. Gate on `cluster-capacity`; when the token is absent, print nothing for the ledger and say so in `--help`. (b) Where the server sends `excluded` (gate on `cluster-resources-excluded`), replace 7b's D4 sentence with each node's reason, keeping D4's wording as the fallback for an older server. (c) `namespace claim list` gains `--all` (NAMESPACE becomes optional, and exactly one of the two must be given) and `--coverage active\|expired`. The `--all` view adds a `namespace` column ahead of `CLAIM_COLUMNS` and keeps `state` and `coverage` as separate columns (the rule recorded above `CLAIM_COLUMNS`). Catch `IncapableException` per 7b's D8. Tests: `CliRunner` tests for each, including old-server payloads without the new keys or tokens. Update client-python `docs/cluster-capacity.md` (7b's page) and `docs/namespace-claims.md`. Apply the `plan-push-audit-phase` floor checklist to this pull request, and record its verdicts in that pull request's description, since client-python has no `PUSH-AUDIT.md`. Commit subject: "Show the claims ledger and exclusions." |
| 9 | medium | opus | none | **Close-out in the shakenfist pull request.** Write this plan's *Outcome*: what each step did, the step 4 body agreed at the gate, whether step 3's report confirmed `test_scheduler.py` unmodified, and anything deferred and where it went. Add a *Phase status notes* entry for 7a to the master plan. Run `pre-commit run --all-files`. Step 8's outcome is recorded in its own client pull request, and the 7a row's `Merged` cell is filled at the next phase's close-out with all three landings (`client-python <sha> (#pr)` twice, and the shakenfist merge). Commit subject: "Close out scheduler reservations phase 7a." |

### Step notes

- Steps 1 and 2 both regenerate protobuf stubs. Run them in
  sequence.
- Step 3 needs step 1. Step 4 needs steps 2 and 3. Step 6 needs
  steps 1 to 5. Step 7 needs steps 3 and 4.
- Step 5 is independent and can run first, in parallel with
  step 1. Its pull request should be open early so that it can
  merge before the shakenfist pull request's merge-queue run.
- Step 8 is gated on work outside this repository, in 7b. It may
  sit for some time after the shakenfist pull request merges.
  Record that with `plan-blockers` when the shakenfist pull
  request is opened.

## Risks and mitigations

- **Step 3 changes what `find_candidates()` reads.** The metrics
  loader is shared, so a careless change moves placement.
  Mitigation: H6 confines the change to an optional out-parameter,
  and the step's constraint is that `test_scheduler.py` passes
  unmodified. The management session reads the diff of
  `get_active_node_metrics()` and `refresh_metrics()` and states
  in step 9's Outcome that neither the return value nor
  `self.metrics` changed.
- **`unallocated_*` drifts from the guards.** It is a Python copy
  of SQL arithmetic. Mitigation: one helper, whose docstring names
  both expressions, and step 1's live test, which fails if either
  guard and the helper disagree by one cpu.
- **`/admin/resources` gets slower, or fails more often.** It gains
  one primary-key read. Mitigation: H4 bounds it and degrades it
  instead of raising. Step 6's functional test runs under the same
  polling as every other cluster test, so a regression shows up
  as CI timing.
- **The client pull requests land out of order.** Mitigation: H9
  fixes the order. Step 6's brief says to confirm step 5 is on
  client-python `develop` first. If it is not, the functional
  test fails with an `AttributeError` on the client, which is loud
  rather than silent.
- **Phase 5 drops the new keys when it rewrites the endpoint.**
  Mitigation: the phase 5 stub now names them, and step 6's
  functional tests fail without them.
- **Step 8 waits indefinitely on 7b.** Mitigation: the server
  surface is useful without it (`curl`, `--output json`, the verb).
  The blocker is recorded with `plan-blockers`, so the wait is
  visible rather than forgotten.

## Definition of done

The "today" figures were measured against `9efbff1481` while this
plan was written.

- [ ] `grep -c "rpc GetClusterCapacity" protos/database.proto`
      returns 1. It returns 0 today.
- [ ] Step 1's live test passes against MariaDB in CI. It shows
      that a claim and an unclaimed placement of exactly
      `unallocated_cpus` are admitted, and `+1` is refused, for
      each. Changing `_cluster_unallocated()` by one breaks it,
      which the management session checks with a local mutation
      (the `mutation-check` skill).
- [ ] On every cluster topology, `/admin/resources` carries
      `cluster_capacity` with `row_present` true, and
      `unallocated_<dim> == total - claimed - unclaimed_used` in
      the same response. This is asserted by step 6, not by
      reading the code.
- [ ] On every cluster topology, every active hypervisor is in
      exactly one of `per_node` or `excluded`, asserted by step 6.
- [ ] A hypervisor with a stale metrics row appears in `excluded`
      with reason `stale metrics`, asserted by step 3's unit test.
      Today it appears nowhere.
- [ ] `GET /admin/claims?coverage_state=bogus` returns 400, and
      listing N claims through either claims endpoint calls
      `get_namespace_claim` zero times. Both are asserted by
      step 4's tests.
- [ ] `EXPECTED_CAPABILITIES['admin']` in `test_root.py` contains
      `cluster-capacity`, `cluster-resources-excluded` and
      `cluster-claims`.
- [ ] `admin_resources_get_example` contains no `...`, and every
      top-level and per-node key `summarize_resources()` emits
      appears in it. The management session checks this by
      diffing the example's keys against a unit test's output,
      not by eye.
- [ ] `docs/developer_guide/api_reference/admin.md` has `## Resources`
      and `## Claims` headings. Today it has neither.
- [ ] No guard outcome changed. The shakenfist diff touches no
      comparison in `_direct_admit_instance_placement()` or in
      `_claim_capacity_guard()`, does not touch
      `CLAIM_ENFORCEMENT_HARD`, and adds no line inside
      `find_candidates()`. The management session verifies this
      by reading the diff and states it in the Outcome.
- [ ] Client-python: `get_cluster_claims` exists on `develop`
      (step 5), and `sf-client node capacity` against an `excluded`
      fixture prints each node's reason instead of 7b's D4 sentence
      (step 8).
- [ ] `pre-commit run --all-files` passes in both repositories,
      including `check-plan-status.py` and `check-doc-anchors.py`
      here.

## Future work

- **CI sizing could read `excluded`.** `shakenfist_ci/sizing.py`
  (`:197`) and `retries.py` infer a missing node's cause from its
  absence. With reasons published, a structural-minimum failure
  could say "stale metrics on sf-3" rather than "too few
  hypervisors".
- **Should a namespace owner read their own namespace's claim
  ledger share?** `/admin/claims` and `cluster_capacity` are
  admin-only, like everything else in `/admin`. This joins phase
  7's open question about namespace owners reading their own
  events.
- **`external_view()` re-reads elsewhere.** H5 fixes the claims
  listings only. Other listings whose objects re-read a row the
  iterator already held have not been surveyed.

## Execution notes

Implementation started on 2026-10-25 (AEDT).

**Back brief answers (Michael, 2026-10-25).**

- Run steps 1 to 7 and 9 now. Step 8 waits on 7b's step 4 and is
  recorded as a `plan-blockers` note on this plan.
- Step 5 may be pushed and opened as its own client-python pull
  request as soon as it is committed. It is client-python#438.
- Keep the step 4 gate.
- The live-MariaDB tests ran locally against a throwaway
  `mariadb:10.11` container, which is removed at the end of the run.

**Order.** Step 5 ran in parallel with step 1. The two touch
different repositories. Every shakenfist step ran in sequence in
the one worktree.

**Step 4 gate (Michael, 2026-10-25).** The response bodies were
approved as drafted, including H1's key, H5's URL and H8's tokens,
with two refinements:

- `excluded`'s stale-metrics entry carries an absolute
  `metrics_timestamp` (the row's own epoch timestamp) rather than
  a relative `metrics_age`. The age was computed when the metrics
  were loaded, so a cached summary would serve a wrong figure.
  This changed step 3's code, and it is committed on its own,
  ahead of step 4.
- The four exclusion reasons keep their spaced phrases. The scheduler
  already publishes `queue too long` in that spelling in its stage
  events. They are a closed set, `scheduler.EXCLUSION_REASONS`,
  which a test pins and step 7 documents.

**Client verb review.** client-python#438's first automated
review had no `fix` items. One `consider` item was taken:
`coverage_state` is forwarded whenever it is supplied, so the
server stays the only validator.

## Outcome

Not started.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.

Two gates, both cheap now and expensive later:

1. **Before step 4: the response bodies.** H1's placement of the
   singleton, H5's URL, and the exact JSON of `cluster_capacity`,
   `excluded` and `/admin/claims` are what client scripts, 7b's
   view and phase 8's guide will all quote. The step 4 sub-agent
   returns the bodies first, and the management session takes
   them to Michael. If he prefers `/admin/capacity` over a key in
   `/admin/resources` (H1), or another URL for the listing (H5),
   steps 3 and 4 change shape. Nothing before them does.
2. **Before step 8: the mocked rendering**, as for 7b's step 4.
