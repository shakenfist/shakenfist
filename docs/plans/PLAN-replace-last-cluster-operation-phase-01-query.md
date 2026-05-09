# Phase 1: Add `has_pending_cluster_operation` query and tests

This is phase 1 of `PLAN-replace-last-cluster-operation.md`.
Read that document first for the overall mission and the
recorded decisions on the six open questions. This phase is
purely additive — nothing is rewired to use the new query
yet, that is phase 2.

## Goal

Introduce a new "any non-terminal cluster operation targets
this object?" query, exposed both at the `mariadb` module
level and as a method on
`DatabaseBackedObjectWithOperations`. Provide unit tests
that prove the query is history-aware (the latest-only race
described in the master plan's *Situation* section).

After this phase lands, the rest of the codebase still uses
the single-pointer `last_cluster_operation` property — the
new method just exists alongside it, ready to be adopted in
phase 2.

## Why this is a self-contained phase

- The new query has no callers yet, so the only way it
  breaks anything is if it breaks the build, the proto
  compile, or the existing
  `cluster_operation_targets`-related tests.
- The schema migration table (`cluster_operation_targets`)
  already exists; this phase adds no DDL.
- Splitting it from phase 2 means the gating switch in
  phase 2 has zero risk of being entangled with bugs in
  the new query implementation — phase 1 ships with
  comprehensive unit tests, so phase 2 only has to verify
  the *call site* changes.

## Detailed work

### 1. Proto changes (`protos/database.proto`)

Add a new RPC and request/reply pair alongside the existing
`cluster_operation_targets` RPCs (today they are at lines
~262–268 and the messages at ~1696–1751). Model the new
RPC on `GetLatestClusterOperationTarget`:

```proto
// Inside the existing service block, after
// GetLatestClusterOperationTarget:
rpc HasPendingClusterOperationTarget (HasPendingClusterOperationTargetRequest) returns (HasPendingClusterOperationTargetReply) {}

// Alongside the other cluster_operation_targets messages:
message HasPendingClusterOperationTargetRequest {
  ObjectType target_object_type = 1;
  string target_uuid = 2;
}

message HasPendingClusterOperationTargetReply {
  bool pending = 1;
}
```

The reply is a plain `bool pending` rather than reusing
`StatusReply` because the answer "no, none pending" is the
expected, common case and is not an error.

After editing the proto:

```bash
tox -e genprotos
```

Verify that `shakenfist/protos/database_pb2.py` and
`database_pb2_grpc.py` (and the `.pyi` stubs) regenerated
cleanly and were committed.

### 2. `mariadb.py` query implementation

Add the three-layer trio mirroring
`get_latest_cluster_operation_target`
(`mariadb.py:5399`, `:5618`, `:5758`). All three new
functions go in the cluster_operation_targets section of
`mariadb.py`.

Active states are `['queued', 'preflight', 'executing']`,
matching the precedent set by
`_direct_delete_stale_cluster_operation_targets`
(`mariadb.py:5508`). Terminal is the complement, defined
by exclusion. Define the constant once near the top of the
cluster_operation_targets section so both functions reuse
it:

```python
# An operation is "in flight" if and only if its row in
# object_states has one of these state values. Anything
# else (complete, abort, error, deleted, ...) is terminal.
# Matches _direct_delete_stale_cluster_operation_targets.
_ACTIVE_OPERATION_STATES = ('queued', 'preflight', 'executing')
```

#### `_direct_has_pending_cluster_operation_target`

JOIN `cluster_operation_targets` against `object_states` on
`operation_uuid == object_uuid`, filter
`target_object_type` and `target_uuid`, restrict
`state_value` to `_ACTIVE_OPERATION_STATES`, return whether
any row exists.

Use `sa.exists()` so the SQL planner can stop at the first
matching row rather than counting:

```python
def _direct_has_pending_cluster_operation_target(
    target_object_type: ObjectType,
    target_uuid: str
) -> bool:
    """True if any in-flight cluster operation targets this object."""
    engine = _get_engine()
    table = _get_cluster_operation_targets_table()
    states_table = _get_object_states_table()

    try:
        with engine.connect() as conn:
            inner = sa.select(sa.literal(1)).select_from(
                table.join(
                    states_table,
                    table.c.operation_uuid == states_table.c.object_uuid
                )
            ).where(
                sa.and_(
                    table.c.target_object_type == target_object_type,
                    table.c.target_uuid == target_uuid,
                    states_table.c.state_value.in_(
                        _ACTIVE_OPERATION_STATES)
                )
            )
            stmt = sa.select(inner.exists())
            result = conn.execute(stmt).scalar()
            return bool(result)
    except OperationalError as e:
        LOG.warning(
            f'MariaDB read failed for has_pending_cluster_operation '
            f'{target_object_type}/{target_uuid}: {e}')
        # Fail closed: if we cannot prove no op is in flight,
        # treat that as "in flight" so callers defer rather
        # than racing.
        return True
```

The fail-closed default is deliberate — phase 2's gating
callers (`Network.is_okay()` and friends) treat a True
return as "do nothing, an op is handling it", which is the
safe behaviour during a transient DB failure.

#### `_grpc_has_pending_cluster_operation_target`

Standard gRPC client pattern, mirroring
`_grpc_get_latest_cluster_operation_target`
(`mariadb.py:5618`). Same fail-closed default on
`grpc.RpcError`.

```python
def _grpc_has_pending_cluster_operation_target(
    target_object_type: ObjectType,
    target_uuid: str
) -> bool:
    try:
        stub = _get_database_stub()
        request = database_pb2.HasPendingClusterOperationTargetRequest(
            target_object_type=cast(
                shakenfist_enums_pb2.ObjectType.ValueType,
                target_object_type.proto_id),
            target_uuid=target_uuid
        )
        reply = _grpc_call(
            stub.HasPendingClusterOperationTarget, request)
        return bool(reply.pending)
    except grpc.RpcError as e:
        LOG.error(
            f'gRPC HasPendingClusterOperationTarget failed for '
            f'{target_object_type}/{target_uuid}: {e}')
        return True
```

#### Public dispatch `has_pending_cluster_operation_target`

```python
def has_pending_cluster_operation_target(
    target_object_type: ObjectType,
    target_uuid: str
) -> bool:
    """True if any in-flight cluster operation targets this object.

    "In flight" means the operation's row in object_states is
    in {queued, preflight, executing}. Any later operation
    against the same object that has reached a terminal state
    does NOT mask an earlier in-flight operation, fixing the
    latest-only race in the legacy single-pointer
    last_cluster_operation gating.
    """
    if _use_database_service():
        return _grpc_has_pending_cluster_operation_target(
            target_object_type, target_uuid)
    return _direct_has_pending_cluster_operation_target(
        target_object_type, target_uuid)
```

### 3. Database daemon handler (`shakenfist/daemons/database/main.py`)

Add a `HasPendingClusterOperationTarget` method on the
service class, alongside the existing
`GetLatestClusterOperationTarget` (`main.py:4238`). It
should call `_direct_has_pending_cluster_operation_target`
and return the bool. Wrap in the same try/except pattern
the surrounding handlers use; on failure return
`HasPendingClusterOperationTargetReply(pending=True)` to
preserve the fail-closed contract.

### 4. `DatabaseBackedObjectWithOperations` method

In `shakenfist/baseobject.py`, add a new method on the
class that begins at line 671. Place it directly after the
`last_cluster_operation` property (line 673) so the two
related queries are side-by-side:

```python
def has_pending_cluster_operation(self) -> bool:
    """True if any in-flight cluster operation targets this object.

    Replaces the legacy pattern of reading
    ``last_cluster_operation`` and inspecting the embedded
    operation's state. That pattern was racy: a later
    terminal op against the same object would mask an
    earlier in-flight op. This query inspects every target
    row.
    """
    if self.in_memory_only:
        return False
    return mariadb.has_pending_cluster_operation_target(
        self.object_type, str(self.uuid))
```

`in_memory_only` returns False (not True) — an in-memory
object cannot have any operations in flight against it
because it has no DB rows for them to reference. This
matches the convention used by `last_cluster_operation`
above (returns None for in-memory).

### 5. Unit tests (`shakenfist/tests/test_cluster_operation_targets.py`)

Add a new `HasPendingClusterOperationTestCase` to the
existing test module. Mirror the mock style of
`LastClusterOperationWithTargetsTestCase` (lines 19–48).
The unit tests should mock
`mariadb.has_pending_cluster_operation_target` and verify
the property delegates correctly. Required cases on the
baseobject method:

1. `test_no_targets_returns_false` — mock returns False,
   assert method returns False.
2. `test_in_flight_target_returns_true` — mock returns
   True, assert method returns True.
3. `test_in_memory_object_short_circuits` — construct with
   `in_memory_only=True`, mock should not be called, method
   returns False.

In addition, add a separate
`HasPendingClusterOperationQueryTestCase` that exercises
the `mariadb.has_pending_cluster_operation_target`
dispatcher. Mock `_use_database_service` and the underlying
`_direct_*` / `_grpc_*` to verify routing.

For the SQL-level behaviour (the actual race fix), prefer
to add a stestr functional test in
`tests/test_cluster_operation_targets.py` that exercises
the dispatcher against a real (mocked-engine) result set.
If a real-engine test is too heavy for this phase, document
that decision in the phase commit message and rely on the
phase 2 gating tests plus functional CI to catch the race
fix.

The minimum required SQL-level coverage (whether achieved
via real engine or via mocking
`_direct_has_pending_cluster_operation_target`'s SQL
result):

4. **No targets** — query returns False.
5. **One target, in-flight** — query returns True.
6. **One target, terminal** — query returns False.
7. **Mixed: terminal target then in-flight target** —
   query returns True. *This is the latest-only race the
   plan exists to fix.*
8. **Multiple terminal targets, no in-flight** — query
   returns False.

### 6. Lint, type-check, and proto regeneration

```bash
tox -e genprotos      # only if proto changed (it did)
pre-commit run --all-files
tox                   # full unit test run
```

Confirm mypy passes on the new code in `mariadb.py`,
`baseobject.py`, and `daemons/database/main.py`.

## Files expected to change

- `protos/database.proto` — new RPC, request, reply.
- `shakenfist/protos/database_pb2.py` — regenerated.
- `shakenfist/protos/database_pb2.pyi` — regenerated.
- `shakenfist/protos/database_pb2_grpc.py` — regenerated.
- `shakenfist/mariadb.py` — three new functions, one new
  module constant.
- `shakenfist/daemons/database/main.py` — one new RPC
  handler.
- `shakenfist/baseobject.py` — one new method on
  `DatabaseBackedObjectWithOperations`.
- `shakenfist/tests/test_cluster_operation_targets.py` —
  new test cases.

No other files should be modified. In particular,
`Network.is_okay()`, the network maintainer, the cleaner
daemon, and any other gating callers are untouched in this
phase.

## Commit shape

One commit, message along the lines of:

```
Add has_pending_cluster_operation_target query.

Introduces a history-aware query that returns True when any
cluster operation targeting the object is in a non-terminal
state. Adds the proto RPC, the three-layer mariadb dispatch,
the database daemon handler, and a method on
DatabaseBackedObjectWithOperations. No callers are switched
yet — that is phase 2.
```

(Plus the standard `Prompt:`, `Signed-off-by`, and
`Co-Authored-By` lines.)

## Acceptance criteria

- `tox` passes.
- `pre-commit run --all-files` passes.
- `tox -e genprotos` produces no diff when re-run after
  the commit (i.e. the regenerated stubs are committed).
- The new method is callable on any subclass of
  `DatabaseBackedObjectWithOperations` (Network, Instance,
  Artifact, Blob, NetworkInterface, IPAM, AgentOperation
  — verify the class hierarchy in the brief, not by
  changing those classes).
- No existing test in
  `tests/test_cluster_operation_targets.py` regresses.
- The race-fix unit test
  (`test_terminal_then_in_flight_returns_true`) genuinely
  exercises the JOIN logic, not just a mock that
  pre-decides the answer. If a real-engine test is too
  heavy here, the test must at minimum assert the SQL
  query shape includes the `state_value IN (...)` filter.

## Out of scope

- Switching any caller to use the new method.
- Renaming or privatising `set_last_cluster_operation`.
- Touching `object_metadata.last_cluster_operation_json`.
- Updating documentation (deferred to phase 5).
