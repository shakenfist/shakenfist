# Phase 3: the cluster-daemon cascade for an errored node

Master plan: [PLAN-node-resource-health.md](PLAN-node-resource-health.md).
Depends on phase 2 (sf-resources marks a node `STATE_ERROR` and records
an audit event carrying `affected_types`).

## Context

Phase 2 makes a node with failed storage go `STATE_ERROR`, which on its
own stops scheduling onto it and stops its blob copies counting as
replicas (both for free, via existing state filters). Phase 3 adds the
**cascade**: from a *surviving* node — the cluster daemon — react to an
errored node by

1. **erroring its hosted instances** (move each to `<state>-error`) when
   instance storage was the thing that failed, and
2. **dropping its now-unreadable blob location records + re-replicating**
   when the blob store was the thing that failed.

Both are gated on *which* object type was affected (master plan
blast-radius-as-membership): an `uploads`-only failure marks the node
`error` but must **not** kill its instances or drop its blob replicas.

Why the cluster daemon and not the affected node (master plan D7): the
affected node may be dying, and it should do only the one fast write
(mark itself `error`, phase 2). The heavier cascade — potentially many
blobs and instances — runs on a node that will survive, reusing the
existing dead-node machinery.

## Key references in the existing code

- **`shakenfist/daemons/cluster/main.py`** — the "Node management" loop
  (`for n in Nodes([])`, the `if/elif` chain on `n.state.value` for
  missing / returned / deleted). The **`STATE_DELETED`** branch is the
  pattern to mirror: it iterates `instance.healthy_instances_on_node(n)`
  and, for each `blob_uuid in n.blobs`, calls `b.remove_location(n.fqdn)`
  then `b.request_replication()`, with `self.pet_watchdog()` per item.
  Phase 3 adds a parallel **`STATE_ERROR`** branch. Note the deleted
  path *deletes* instances (`i.delete(global_only=True)`); phase 3
  *errors* them instead (they stay for the operator to snapshot/delete).
- **`shakenfist/node_health.py`** — phase 2's `apply_result` wrote an
  audit event `add_event(EVENT_TYPE_AUDIT, reason, extra={'affected_types':
  [...], 'failed': [...]})`. Phase 3's reader lives here too, so the
  event format is owned by one module.
- **`shakenfist/mariadb.py:4883` `get_object_events(object_type,
  object_uuid, limit=100, event_type=None)`** → `list[EventReadRow]`
  ordered timestamp-descending. `EventReadRow`
  (`shakenfist/schema/event.py:55`) has `message`, `extra` (the
  free-form payload), `timestamp`, `event_type`. This is how phase 3
  reads `affected_types` back (nodes have no free-form attribute store —
  phase 2's reason for using the event log).
- **`shakenfist/instance.py:2174` `healthy_instances_on_node(n)`** —
  `Instances(prefilter='healthy')` for the node; `HEALTHY_STATES` is
  `{initial, preflight, creating, created}` (`:194`). Once an instance
  is moved to `<state>-error` it drops out of this set, which is what
  makes the cascade naturally idempotent.
- **`shakenfist/baseobject.py:605`** — the `error` setter requires the
  object already be in an error state, so set `i.state = <state>-error`
  **before** `i.error = reason` (the instance-disk-errors ordering).
- **`shakenfist/instance.py` `state_targets`** — every `HEALTHY_STATES`
  value has a valid `<state>-error` target, so the transition never
  raises.

## Inherited decisions (master plan)

D1 (error already stops scheduling + discounts replicas), D6 (node error
is operator-cleared only — phase 3 never clears it), D7 (cascade on a
surviving node), D8 (no transitive graph). Blast-radius-as-membership:
gate each cascade on the affected object type.

## Design

### F1 — reading the blast radius (`node_health`)

```python
def errored_node_affected_types(node) -> set[ObjectType] | None:
    """The object types a node's resource-health failure affected, from
    the most recent resource-health audit event, or None if none is
    found (blast radius unknown -> caller does nothing)."""
    for row in mariadb.get_object_events(
            ObjectType.NODE, node.uuid, event_type=EVENT_TYPE_AUDIT):
        affected = (row.extra or {}).get('affected_types')
        if affected is not None:
            return {ObjectType(t) for t in affected}
    return None
```

`get_object_events` returns newest-first, so the first row carrying
`affected_types` is the current diagnosis (phase 2 writes one event on
each transition into `error`). Robust against event pruning: phase 3
cascades on the first cluster pass after the node goes `error` (see F3
idempotency), and the cluster daemon *is* the pruner, so the event
cannot be pruned before phase 3 has run.

### F2 — the cascade (`cluster` daemon)

A new `elif n.state.value == Node.STATE_ERROR:` branch calls
`self._cascade_errored_node(n)`:

```python
def _cascade_errored_node(self, n):
    if str(n.uuid) in self._cascaded_error_nodes:
        return                                   # already handled (F3)
    instances = list(instance.healthy_instances_on_node(n))
    blob_uuids = list(n.blobs)
    affected = node_health.errored_node_affected_types(n)
    if affected is None:
        return                                   # unknown radius; retry next pass

    if ObjectType.INSTANCE in affected:
        for i in instances:
            self.pet_watchdog()
            reason = f'hosting node {n.fqdn} storage is unhealthy'
            i.add_event(EVENT_TYPE_AUDIT, reason)
            i.state = i.state.value + '-error'   # state before error (setter guard)
            i.error = reason

    if ObjectType.BLOB in affected:
        for blob_uuid in blob_uuids:
            self.pet_watchdog()
            b = Blob.from_db(blob_uuid)
            if not b:
                continue
            eventlog.add_event_multi(
                EVENT_TYPE_AUDIT, [n, b],
                'dropping blob location: hosting node storage is unhealthy')
            b.remove_location(n.fqdn)
            b.request_replication()

    self._cascaded_error_nodes.add(str(n.uuid))
```

Instances are **errored, not deleted** (unlike the deleted-node path):
the master plan and the instance-disk-errors work agree an errored
instance is terminal-but-snapshottable, left for the operator. No
libvirt teardown here — the instance's domain lives on the (broken)
errored node; the operator's later delete handles it.

`b.remove_location` + `b.request_replication` is exactly the deleted-node
blob cleanup; because the node is already `error`,
`request_replication` was *already* discounting these copies (D1), so
this is the cleanup that also lets them be reaped.

### F3 — idempotency and not re-reading every pass

The cluster loop runs each pass while the node stays `error` (phase 3
never clears it — D6). Two mechanisms keep the cascade a one-shot:

- Erroring instances drains `healthy_instances_on_node`; dropping blob
  locations drains `n.blobs`. So the *actions* are naturally idempotent
  (the deleted-node path relies on the same).
- But a node whose failure did **not** affect instances/blobs (e.g.
  `uploads`-only) has nothing to drain, so without a guard it would
  re-read its event every pass. An in-memory `self._cascaded_error_nodes`
  set (a `Monitor` instance attribute) records nodes already cascaded;
  the branch skips them. It is discarded when the node is next seen in a
  non-error state (so a recover-then-refail re-cascades). On a cluster
  daemon restart the set is empty and the (idempotent) cascade runs once
  more — harmless.

Add near the top of the per-node loop body:
```python
if n.state.value != Node.STATE_ERROR:
    self._cascaded_error_nodes.discard(str(n.uuid))
```

## Step-level guidance

Sequential; review and commit each. Isolation `none`.

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 3a — the blast-radius reader | medium | opus | none | Add `errored_node_affected_types(node) -> set[ObjectType] \| None` to `shakenfist/node_health.py` per F1: iterate `mariadb.get_object_events(ObjectType.NODE, node.uuid, event_type=EVENT_TYPE_AUDIT)`, return the `affected_types` of the newest row that has one (mapped back to `ObjectType`), else None. Keep it in the mypy rollout (typed). Unit tests (mock `mariadb.get_object_events`): a node whose newest resource-health event has `affected_types=['instance','blob']` → `{INSTANCE, BLOB}`; newest-wins when several events exist; no such event → None; an event with no `extra` is skipped. Commit subject: `node_health: read a node's resource-health blast radius.` |
| 3b — the cluster cascade | high | opus | none | In `shakenfist/daemons/cluster/main.py`: initialise `self._cascaded_error_nodes = set()` in `Monitor.__init__`; in the node-management loop add the non-error discard (F3) and an `elif n.state.value == Node.STATE_ERROR: self._cascade_errored_node(n)` branch; implement `_cascade_errored_node` per F2 (guard-set check, read affected types, error instances when INSTANCE affected — `state` before `error`, one event each — drop blob locations + `request_replication` when BLOB affected, `pet_watchdog` per item, then record in the guard set). Import `node_health`, `ObjectType`. Unit tests (mock `healthy_instances_on_node`, `n.blobs`, `Blob.from_db`, and `errored_node_affected_types`): INSTANCE+BLOB affected → instances moved to `<state>-error` with an event and blob locations removed + re-replicated; INSTANCE-only → instances errored, **no** `remove_location`; `uploads`-only (neither) → nothing errored or dropped; already-in-guard-set → no-op; affected None → no-op and node not added to the set (retries); a non-error node discards its guard entry. Commit subject: `cluster: cascade an errored node to its instances and blobs.` |

## Step ordering and dependencies

- **3a first** (the reader `_cascade_errored_node` calls). **3b** wires
  the cascade. No deploy, proto, or config changes.
- `pre-commit run --all-files` after each (the mypy hook runs the whole
  rollout list — a few minutes).

## Success criteria

- When a hypervisor's blob store fails (phase 2 marks it `error`,
  `affected_types` ⊇ {INSTANCE, BLOB}), the next cluster pass moves its
  healthy instances to `<state>-error` (terminal, not deleted; one audit
  event each) and removes its blob location records with a
  re-replication requested for each — restoring `BLOB_REPLICATION_FACTOR`
  elsewhere.
- An `instances`-only failure (`affected_types` = {INSTANCE}) errors the
  instances but does **not** drop blob locations (the blob store is
  fine); an `uploads`-only failure (neither INSTANCE nor BLOB) does
  neither, though the node stays `error` and out of scheduling.
- The cascade is one-shot per error episode: a persistently-error node
  is not re-processed each pass (guard set), and a cluster-daemon
  restart re-runs it at most once, idempotently.
- Phase 3 never clears node `error` (D6); recovery stays operator-driven.
- `pre-commit run --all-files` passes; new code is typed and in the mypy
  rollout.

## Back brief

Confirm the understanding that phase 3 *errors* (does not delete) the
hosted instances, that both cascades are gated on the affected object
type read back from phase 2's audit event, and that the in-memory guard
set is what stops a persistently-error node being re-processed every
cluster pass.

## Review checklist for the management session

- [ ] The `STATE_ERROR` branch mirrors the deleted-node blob cleanup but
      *errors* instances rather than deleting them.
- [ ] Instance `state` is set to `<state>-error` **before** `error`.
- [ ] Blob-location drop only when BLOB is affected; instance-error only
      when INSTANCE is affected.
- [ ] `pet_watchdog` is called per instance and per blob.
- [ ] The guard set prevents per-pass re-reads; it is discarded when the
      node leaves `error`; `affected is None` does **not** add to the set.
- [ ] Node `error` is never cleared here.
- [ ] `pre-commit run --all-files` passes; mypy rollout updated.
