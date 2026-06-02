# Phase 5 — delete the daemon, the DLQ, and the on-disk chunks

Parent plan:
[PLAN-eventlog-direct-mariadb.md](PLAN-eventlog-direct-mariadb.md).
Predecessors:
[Phase 1](PLAN-eventlog-direct-mariadb-phase-01-schema.md),
[Phase 2](PLAN-eventlog-direct-mariadb-phase-02-write.md),
[Phase 3](PLAN-eventlog-direct-mariadb-phase-03-prune.md),
[Phase 4](PLAN-eventlog-direct-mariadb-phase-04-read.md).

## Scope

Phase 5 is the final code-deletion phase. After this phase
ships:

- `sf-eventlog` is gone. The daemon code, its systemd unit
  templating, the ansible deploy bits, the `event.proto`,
  and the regenerated `event_pb2*` stubs are all deleted.
- The `EventLog` class and its sqlite storage helpers
  (`EventLogChunk`, `upgrade_data_store`, `_shard_db_path`,
  `_timestamp_to_year_month`, the corruption-marker
  handling) are removed from `shakenfist/eventlog.py`.
- The MariaDB `event_dlq` table, the four pairs of
  direct/gRPC accessors, the four public dispatchers, the
  table-getter, the schema-ensure, the DATA_MIGRATIONS
  entry, and the four DLQ RPCs on `protos/database.proto`
  are all deleted. `_migrate_etcd_event_dlq` is also
  deleted (see "DLQ deletion timing" below).
- The `EVENTLOG_SUPPRESS_GRPC`, `set_force_event_dlq`,
  `get_force_event_dlq`, `_mark_eventlog_unavailable`,
  `_is_eventlog_available`, `get_eventlog_client`, and
  `_add_event_multi_inner` machinery in `eventlog.py`
  goes. `add_event_multi` shrinks to spool-only: enqueue
  to the local spool; on failure, `EVENTLOG_SPOOL_DROPPED`
  increments and the event is dropped. The
  `_add_event_dlq_inner` DLQ fallback goes with it — the
  spool is the durability boundary, full stop.
- Config keys `EVENTLOG_NODE_IP`, `EVENTLOG_API_PORT`,
  `EVENTLOG_METRICS_PORT`, `EVENTLOG_SUPPRESS_GRPC`, and
  `NODE_IS_EVENTLOG_NODE` are removed from `config.py`.
- The `redirect_to_eventlog_node` decorator in
  `external_api/base.py` is deleted along with its five
  call sites on the event endpoints
  (`{instance,artifact,network,node,blob}.py`).
- On-disk sqlite chunks under `/srv/shakenfist/events/`
  are documented as operator-cleanup
  (`rm -rf /srv/shakenfist/events`) in phase 6's release
  notes. No automated deletion in phase 5.

Out of scope (deferred):

- Documentation, release notes, ARCHITECTURE / README /
  AGENTS updates (phase 6).
- The `MAX_{TYPE}_EVENT_AGE` configs (phase 3 prune
  consumes them — they stay).
- The drainer (`eventlog_drainer.py`), the spool
  (`eventlog_spool.py`), and the public `add_event*`
  module-level functions in `eventlog.py` — all unchanged.
  Callers' write API is preserved exactly as the master
  plan requires.

## DLQ deletion is total

Master plan decision 2's earlier draft talked about
keeping `_migrate_etcd_event_dlq` for a release. The
operator has clarified: don't bother. The single
operating deployment will upgrade all nodes together in
a coordinated outage, so there is no need to preserve
the etcd→DLQ legacy migration as a hedge. Phase 5
deletes `_migrate_etcd_event_dlq` along with the rest of
the DLQ code. The master plan's decision 2 wording and
Future-work entry are updated alongside this phase plan
to drop the now-irrelevant rollover provision.

## eventlog.py simplification

Today's `add_event_multi` has three terminal paths:

1. Local spool (`eventlog_spool.enqueue`) — happy path.
2. Synchronous gRPC to sf-eventlog
   (`_add_event_multi_inner`) — fallback when spool init
   failed or `EVENTLOG_SUPPRESS_GRPC` / `force_event_dlq`
   is set.
3. MariaDB DLQ (`_add_event_dlq_inner`) — fallback when
   gRPC fails, or when `force_event_dlq` is set during
   daemon startup.

Phase 5 collapses to one path. Pseudocode:

```python
def add_event_multi(event_type, objects, message,
                    duration=None, extra=None,
                    suppress_event_logging=False,
                    log_as_error=False):
    if suppress_event_logging:
        return
    event_uuid = sf_random.random_id()
    try:
        request_id = flask.request.environ.get('FLASK_REQUEST_ID')
    except RuntimeError:
        request_id = None
    payload = {
        'event_uuid': event_uuid,
        'event_type': event_type,
        'fqdn': config.NODE_NAME,
        'duration': duration,
        'message': message,
        'extra': util_json.json_dump(extra or {}),
        'request_id': request_id,
        'timestamp': time.time(),
        'objects': [
            {'object_type': str(ot), 'object_uuid': str(ou)}
            for ot, ou in simpler_objects
        ],
    }
    eventlog_spool.enqueue(payload)
    # On failure, EVENTLOG_SPOOL_DROPPED counter increments
    # inside the spool; no other action needed.
```

The `correlation_id` local that today aliases
`event_uuid` for the legacy gRPC/DLQ paths goes — those
paths are gone, so the alias is dead. `simpler_objects`
normalisation stays unchanged (that's caller-facing
input handling).

`add_event` (singleton form) still forwards to
`add_event_multi`.

## DLQ infrastructure deletion

The full deletion list in `shakenfist/mariadb.py`:

- `_get_event_dlq_table()`
- `_ensure_event_dlq_schema()`
- The four direct functions:
  `_direct_enqueue_event_dlq`,
  `_direct_drain_event_dlq`,
  `_direct_delete_event_dlq`,
  `_direct_get_event_dlq_count`.
- The four gRPC wrappers:
  `_grpc_enqueue_event_dlq`,
  `_grpc_drain_event_dlq`,
  `_grpc_delete_event_dlq`,
  `_grpc_get_event_dlq_count`.
- The four public dispatchers:
  `enqueue_event_dlq`,
  `drain_event_dlq`,
  `delete_event_dlq`,
  `get_event_dlq_count`.
- `_migrate_etcd_event_dlq` (per "DLQ deletion is
  total" above).
- The `event_dlq` entry in `DATA_MIGRATIONS`.
- `EVENT_DLQ_VERSION` constant.
- The `_event_dlq_table` global sentinel.
- The `_ensure_event_dlq_schema(engine)` call in
  `ensure_schema()`.

In `protos/database.proto`:

- `EnqueueEventDlqRequest`, `EnqueueEventDlqReply` (if
  present), `DrainEventDlqRequest`, `DrainEventDlqReply`,
  `DeleteEventDlqRequest`, `GetEventDlqCountRequest`,
  `GetEventDlqCountReply` messages.
- `EnqueueEventDlq`, `DrainEventDlq`, `DeleteEventDlq`,
  `GetEventDlqCount` rpc entries.

In `shakenfist/daemons/database/main.py`:

- `EnqueueEventDlq`, `DrainEventDlq`, `DeleteEventDlq`,
  `GetEventDlqCount` handlers.
- The four `'*_event_dlq'` entries in the Monitor
  operations list (auto-registered counters).

In `shakenfist/protos/database_pb2*.py(i)` — regenerate
via `tox -e genprotos` after the proto change.

## Daemon and deploy deletion

`shakenfist/daemons/eventlog/` — delete the entire
directory. Only `main.py` lives there.

`protos/event.proto` — delete. `_make_stubs.sh` globs
`*.proto` so regen continues to work.

`shakenfist/protos/event_pb2.py`, `event_pb2.pyi`,
`event_pb2_grpc.py`, `event_pb2_grpc.pyi` — delete.
These are checked-in regen artifacts.

Ansible (`deploy/ansible/`):

- `roles/base/tasks/config.yml` — delete the
  `sf-eventlog.service` task block (around lines
  126-136).
- `roles/base/templates/config` — delete the
  `NODE_IS_EVENTLOG_NODE` block (lines 25-29) and the
  `EVENTLOG_NODE_IP` setting (line 33).
- `roles/base/defaults/main.yml` — delete
  `eventlog_node_ip: 127.0.0.1` (line 23).
- `deploy.yml` — drop `eventlog_node` from the
  `hosts:` line (line 67) and remove the
  `eventlog_node_ip` derivation (line 267).
- `register.yml` — delete the eventlog-daemon
  registration block (lines 32-37) and the sf-eventlog
  service-start block (lines 79-85).
- `create_admin_namespace.yml` — drop the
  `SHAKENFIST_EVENTLOG_NODE_IP` env-var pass (line 8).

## Config and decorator deletion

In `shakenfist/config.py`:

- `EVENTLOG_METRICS_PORT` (lines 143-146).
- `EVENTLOG_SUPPRESS_GRPC` (lines 333-337).
- `EVENTLOG_NODE_IP` (lines 338-341).
- `EVENTLOG_API_PORT` (lines 342-345).
- `NODE_IS_EVENTLOG_NODE` (lines 445-448).

In `shakenfist/external_api/base.py`:

- `redirect_to_eventlog_node` decorator (lines 474-504).
- Any imports it brings in that become unused.

In the five REST endpoints
(`external_api/{instance,artifact,network,node,blob}.py`):

- Remove the `@api_base.redirect_to_eventlog_node`
  decorator line from each event endpoint's decorator
  stack. The other decorators (`@verify_token`,
  `@arg_is_*_ref`, `@requires_*_ownership`,
  `@caller_is_admin`, `@log_token_use`) stay.

## On-disk sqlite chunks

The `/srv/shakenfist/events/` directory is not deleted
by phase 5 code. Phase 6's operator guide documents
`rm -rf /srv/shakenfist/events/` as the cleanup step
after a successful phase 5 deploy. Reasoning: automating
the deletion inside the daemon would mean the daemon
must still be running to delete itself (chicken-and-
egg); a separate cleanup script is fine but operators
generally prefer to do the rm themselves rather than
discover it happened during an upgrade.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | worktree | Delete the sf-eventlog daemon, its proto, and its ansible/systemd integration. Delete `shakenfist/daemons/eventlog/` (entire directory). Delete `protos/event.proto`. Delete `shakenfist/protos/event_pb2.py`, `event_pb2.pyi`, `event_pb2_grpc.py`, `event_pb2_grpc.pyi`. Run `tox -e genprotos` — confirm it still succeeds against the remaining `*.proto` files. In `deploy/ansible/`: delete the `sf-eventlog.service` task in `roles/base/tasks/config.yml`, the `NODE_IS_EVENTLOG_NODE` block and `EVENTLOG_NODE_IP` setting in `roles/base/templates/config`, the `eventlog_node_ip` default in `roles/base/defaults/main.yml`, the `eventlog_node` references in `deploy.yml` and `register.yml`, and the `SHAKENFIST_EVENTLOG_NODE_IP` env-var pass in `create_admin_namespace.yml`. Run `pre-commit run --all-files`. Any test that imports `shakenfist.daemons.eventlog` or `shakenfist.protos.event_pb2` will break — find them via grep and either delete them (if they test the deleted daemon) or update them. Commit message subject: "eventlog: remove sf-eventlog daemon, proto, and deploy." |
| 5b | high | opus | worktree | Delete the `EventLog` class and sqlite storage helpers in `shakenfist/eventlog.py`. Keep: `add_event`, `add_event_multi`, the spool integration, the `simpler_objects` normalisation. Delete: the `EventLog` class (around lines 453-631), `EventLogChunk` (around line 648+), `upgrade_data_store` (lines 363-438), `_shard_db_path`, `_timestamp_to_year_month`, plus any module-level constants only the deleted code references (e.g. event-chunk path constants). Test cleanup: `shakenfist/tests/test_eventlog*.py` likely has sqlite-storage-specific tests (chunk creation, version migration, prune-from-chunk) — delete those test classes, but keep the write-path tests added in phase 2e (the ones that test `add_event_multi` payload shape). Run `pre-commit run --all-files`. Commit message subject: "eventlog: remove EventLog class and sqlite storage." |
| 5c | high | opus | worktree | Delete the DLQ infrastructure and the legacy SUPPRESS_GRPC / force_event_dlq fallback paths. In `shakenfist/eventlog.py`: delete `set_force_event_dlq`, `get_force_event_dlq`, `_mark_eventlog_unavailable`, `_is_eventlog_available`, `get_eventlog_client`, `_add_event_multi_inner`, `_add_event_dlq_inner`. Simplify `add_event_multi` per the "eventlog.py simplification" section of the phase 5 plan — single spool-only path with the existing event_uuid / request_id promotion. The DLQ fallback is gone; the spool's `EVENTLOG_SPOOL_DROPPED` counter is the only signal on failure. In `shakenfist/mariadb.py`: delete `_get_event_dlq_table`, `_ensure_event_dlq_schema`, `_event_dlq_table` global, `EVENT_DLQ_VERSION`, the four `_direct_*_event_dlq` functions, the four `_grpc_*_event_dlq` functions, the four public dispatchers (`enqueue_event_dlq`, `drain_event_dlq`, `delete_event_dlq`, `get_event_dlq_count`), `_migrate_etcd_event_dlq`, the `event_dlq` entry in `DATA_MIGRATIONS`, and the `_ensure_event_dlq_schema(engine)` call in `ensure_schema()`. In `protos/database.proto`: delete the four DLQ messages and the four DLQ rpc entries; regenerate stubs via `tox -e genprotos`. In `shakenfist/daemons/database/main.py`: delete the four DLQ handlers and the four `*_event_dlq` entries in the Monitor operations list. In `shakenfist/config.py`: delete `EVENTLOG_SUPPRESS_GRPC`. Test cleanup: find tests that reference any of the deleted symbols (`test_event_dlq.py`, plus DLQ-related tests in other files) and delete them. Run `pre-commit run --all-files`. Commit message subject: "events: delete DLQ table, suppress-grpc paths, and legacy fallbacks." |
| 5d | medium | opus | worktree | Delete the remaining config keys and the `redirect_to_eventlog_node` decorator. In `shakenfist/config.py`: delete `EVENTLOG_METRICS_PORT`, `EVENTLOG_NODE_IP`, `EVENTLOG_API_PORT`, `NODE_IS_EVENTLOG_NODE`. In `shakenfist/external_api/base.py`: delete the `redirect_to_eventlog_node` decorator (around lines 474-504). In the five REST endpoints (`external_api/{instance,artifact,network,node,blob}.py`): remove the `@api_base.redirect_to_eventlog_node` decorator line from each event endpoint's stack. Confirm no other decorator changes and that the auth stack is preserved exactly. Find any remaining references to the deleted config keys via grep and clean them up. Run `pre-commit run --all-files`. Commit message subject: "events: remove EVENTLOG_* config and redirect_to_eventlog_node." |

Ordering: 5a → 5b → 5c → 5d. Strict sequential — each
commit depends on the previous and removes some
infrastructure that the next step's deletions interact
with.

`isolation: "worktree"` chosen for all four steps
because phase 5 is the most destructive phase of the
plan; a worktree gives the management session a clean
rollback if a sub-agent goes off the rails. Master plan
guidance:
> **Use `isolation: "worktree"` for sub-agents on phase 5
> (daemon, DLQ, and sqlite-chunk deletion) because the
> on-disk and table-drop steps are irreversible by
> sub-agent and benefit from a discardable worktree if
> the output is unsatisfactory.**

Per the phase 1/2/3/4 lesson: any commit that deletes a
public/internal symbol pulls its test fixtures along
into the same commit. Sub-agents are explicitly briefed
to find and clean up broken tests, not leave them for a
follow-up step.

## Risks and mitigations

- **Risk:** Genprotos regen fails after deleting
  `event.proto` because `_make_stubs.sh` references it
  somewhere we didn't see.
  **Mitigation:** Step 5a confirms regen succeeds before
  committing. If it doesn't, the worktree is discarded
  and we re-survey.

- **Risk:** A test we miss in steps 5a-5d breaks the
  build at a later commit.
  **Mitigation:** Each step's brief explicitly tells the
  sub-agent to grep for references to the deleted
  symbols and either delete the test or update it.
  Pre-commit's unit-test hook runs across the whole
  suite at every commit and catches anything missed.

- **Risk:** Ansible changes break an existing operator's
  upgrade in unexpected ways (e.g. they have an
  `eventlog_node` group with hosts in it).
  **Mitigation:** Phase 6 release notes call out the
  ansible inventory change explicitly ("remove
  eventlog_node group; no replacement"). The single
  operating deployment has signed off.

- **Risk:** The `redirect_to_eventlog_node` deletion
  changes the URL routing on a multi-node sf-api
  deployment — a request that used to be redirected now
  returns the response from whichever sf-api received
  it.
  **Mitigation:** That's the intended behaviour
  post-phase-4. The decorator's whole purpose was to
  funnel reads to the sqlite-holder. With reads coming
  from MariaDB on any node, any sf-api can serve them.

- **Risk:** A historic sqlite chunk on disk causes
  operator confusion ("why is `/srv/shakenfist/events/`
  still there?").
  **Mitigation:** Phase 6's operator guide documents the
  manual cleanup. The directory taking up disk space
  but not being read is the worst that happens; no
  daemon writes to it any more.

## Definition of done

- [ ] `shakenfist/daemons/eventlog/` directory does not
      exist.
- [ ] `protos/event.proto` does not exist; no
      `event_pb2*` files in `shakenfist/protos/`.
- [ ] No code references `EventLog`, `EventLogChunk`,
      `upgrade_data_store`, `set_force_event_dlq`,
      `get_force_event_dlq`,
      `_mark_eventlog_unavailable`,
      `_is_eventlog_available`, `get_eventlog_client`,
      `_add_event_multi_inner`, `_add_event_dlq_inner`,
      `_migrate_etcd_event_dlq`, `_get_event_dlq_table`,
      `_ensure_event_dlq_schema`, any
      `*_event_dlq` function, `enqueue_event_dlq`,
      `drain_event_dlq`, `delete_event_dlq`,
      `get_event_dlq_count`, `EVENT_DLQ_VERSION`,
      `EVENTLOG_NODE_IP`, `EVENTLOG_API_PORT`,
      `EVENTLOG_METRICS_PORT`,
      `EVENTLOG_SUPPRESS_GRPC`,
      `NODE_IS_EVENTLOG_NODE`, or
      `redirect_to_eventlog_node`. Grep proves it.
- [ ] `add_event_multi` is the single-path spool-only
      implementation per the "eventlog.py
      simplification" section.
- [ ] Ansible inventory + deploy YAML no longer
      references `eventlog_node` or
      `SHAKENFIST_EVENTLOG_NODE_IP`.
- [ ] `pre-commit run --all-files` is clean at every
      commit.
- [ ] All existing tests pass; tests for deleted code
      are deleted, not left as skipped.
- [ ] Each commit is self-contained; commit messages
      follow project conventions including the Prompt
      paragraph and Co-Authored-By line with model and
      effort.

## Back brief

Before executing any step of this phase, the
implementing sub-agent should back-brief the management
session on its understanding of the brief and the
surrounding context. The destructive nature of phase 5
makes back-briefing especially important: a sub-agent
that misunderstands which symbols to keep vs delete can
do real damage even with worktree isolation.
