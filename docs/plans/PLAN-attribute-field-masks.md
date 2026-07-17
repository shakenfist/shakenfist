# PLAN: attribute field masks everywhere, and un-pack the node instances list

## Background

Merge CI for PR #3425 (run 29557132804, 2026-07-17) failed on
`cluster_ci_tests.test_scheduler.TestAffinity.test_affinity`. The scheduler
audit events showed the affinity pass scoring every node zero because the
tagged neighbour instance had vanished from its node's
`node_attributes.instances` list, roughly 55 seconds after
`place_instance()` had written it there.

The mechanism is a cross-attribute lost update. `Node._save_attributes()`
writes the entire `node_attributes` row, and several writers do unlocked
read-modify-write cycles against that same row:

- `Node.observe_this_node()` is called every 15 seconds by *both*
  sentinel-first and sentinel-last on every node, holds no lock, and
  saves the full row to update `last_seen` and the role flags.
- The resources daemon's `process_metrics`, `dependency_versions`,
  `qemu_version`, `libvirt_version`, `python_version` and
  `python_implementation` setters save the full row every 300 seconds.
  The `dependency_versions` save reuses a row snapshot loaded before a
  `dpkg-query` subprocess ran, widening the stale window to seconds.
- `add_instance()` / `remove_instance()` (lock: `instances`) and
  `register_daemon()` / `deregister_daemon()` (lock: `daemons`) are
  individually locked, but the locks are per-attribute while the write
  is whole-row, so they can clobber *each other* as well as being
  clobbered by the unlocked writers above.

Any interleaving where a full-row writer loads before, and saves after,
another writer's commit silently reverts that commit. This is the same
bug class as the agent-operations enqueue lost update fixed previously,
which introduced the `fields` parameter (a field mask) on
`update_instance_attributes`, `update_artifact_attributes` and
`update_network_attributes`. Node, namespace and blob never got that
conversion.

## Audit: remaining full-row RMW exposure

| Attributes row | Multi-writer fields | Field mask? | Risk |
|---|---|---|---|
| `node_attributes` | `instances` (list), `daemons` (list), `last_seen`/roles, version + metrics fields | no | **High** — unlocked 15s writers race API-side list updates; source of the test_affinity flake |
| `namespace_attributes` | `keys` (dict, lock `keys`), `trust` (list, lock `trust`) | no | Medium-low — cross-attribute clobber possible between key and trust operations |
| `blob_attributes` | `expires_at`, `info`, `size` full-row writes can revert a concurrent `update_blob_last_used()` column write | no | Low — `last_used` is advisory |
| `instance_attributes` | many | yes | converted (agent-await fix) |
| `artifact_attributes` | few | yes | converted |
| `network_attributes` | few | yes | converted |
| `network_interface_attributes` | single mutable field | n/a | safe |
| `agent_operation_attributes` | single mutable field, locked fresh RMW | n/a | safe |

Lists still packed into attribute rows: `node_attributes.instances`
(relational data, high write rate — this plan un-packs it),
`node_attributes.daemons` (derivable from `node_daemon_states`, see
follow-ups), `namespace_attributes.trust` (tiny, low write rate — field
mask is sufficient).

## Policy

Callers of `update_*_attributes` must pass a field mask naming exactly
the fields they changed. Full-row writes (`fields=None`) are reserved
for row creation and pydantic-upgrade persistence, and must say so at
the call site. To give this teeth, the `fields` argument becomes
**required** (still `Optional[...]`, but with no default) on every
multi-field update function, so a new caller cannot silently take the
full-row path. Single-field rows (`network_interface_attributes`,
`agent_operation_attributes`) are exempt.

This policy gets recorded in CLAUDE.md and AGENTS.md.

## Phase 1: field masks for node, namespace and blob attributes

1. Protos: add `repeated string fields = 2;` to
   `UpdateNodeAttributesRequest`, `UpdateNamespaceAttributesRequest`
   and `UpdateBlobAttributesRequest`, with the same comment style as
   `UpdateInstanceAttributesRequest`. Regenerate with `tox -e genprotos`.
2. `mariadb.py`: thread `fields` through the public functions, the
   `_direct_*` implementations (targeted `UPDATE ... SET` of only the
   named columns) and the `_grpc_*` clients, mirroring the instance
   implementation. Update the database daemon handlers to honour the
   mask.
3. Convert writers:
   - `Node.observe_this_node()` →
     `fields=['last_seen', 'installed_version', 'is_etcd_master',
     'is_hypervisor', 'is_network_node', 'is_eventlog_node',
     'is_database_node']`
   - `Node.add_instance()` / `remove_instance()` → `fields=['instances']`
     (interim; removed entirely by Phase 2)
   - `Node.register_daemon()` / `deregister_daemon()` → `fields=['daemons']`
   - resources daemon setters → each names exactly its own field
   - `Namespace` key writers → `fields=['keys']`; trust writers →
     `fields=['trust']`
   - `Blob` setters → name their fields so they cannot revert
     `update_blob_last_used()`
4. Make `fields` required (no default) on all six multi-field update
   functions; annotate the remaining intentional `fields=None` call
   sites (creation, upgrade persistence).

Phase 1 alone kills the observed flake: `observe_this_node()` can no
longer touch the `instances` column.

## Phase 2: un-pack `node_attributes.instances` into object_references

The node→instance edge is relational data and belongs in a table with
atomic per-edge rows, not a JSON list subject to read-modify-write.
`object_references` already models exactly this shape and has a
precedent: `BLOB_LOCATION` rows record node→blob placement, queried via
`Node.blobs`.

1. Add `RelationshipType.INSTANCE_LOCATION`
   (`string='instance_location'`, `proto_id=8`). Key the source by node
   **UUID** (as a string), not fqdn — the fqdn keying of `BLOB_LOCATION`
   is a legacy of the pre-UUID era and should not be copied.
2. `Instance.place_instance()`: replace the node-cache bookkeeping with
   reference ops — delete the old node's reference, create the new
   node's — still under the existing `placement` attribute lock so the
   placement value and the edge move together.
3. `Node.instances` property: query
   `get_references_from(ObjectType.NODE, str(self.uuid),
   RelationshipType.INSTANCE_LOCATION)`. `add_instance()` /
   `remove_instance()` and the `instances` attribute lock go away
   (single-row INSERT/DELETE needs no application lock).
4. `Instance.hard_delete()`: remove the instance's INSTANCE_LOCATION
   reference (project rule: objects clean up in `hard_delete()`).
   Verify the cleaner's evacuate/resurrect paths
   (`daemons/cleaner/scheduled_tasks.py` calls `place_instance`) behave.
5. Migration in `sf-ctl ensure-mariadb-schema`: seed
   `object_references` from existing `node_attributes.instances` lists
   (idempotent INSERT IGNORE semantics), consistent with the schema
   version bump.
6. The `instances` column on `node_attributes` is no longer read or
   written but remains for one release cycle as a rollback fallback,
   matching the `daemon_states` precedent.
7. Confirm `object_references` indexes cover the new query pattern
   (`source_object_type, source_uuid` compound index exists; verify a
   relationship-filtered lookup uses it).

## Testing

- Unit: per-object tests that an update with a field mask writes only
  the named columns (mirror the existing instance field-mask tests);
  tests for `Node.instances` backed by references; migration seeding
  test.
- Functional: `test_affinity` already covers the user-visible
  behaviour. The flake is timing-dependent so CI passing once is not
  proof, but Phase 2 removes the racing list entirely.
- Events: preserve the existing audit events (`schedule have highest
  affinity` details were what made this diagnosable — keep
  `instance_count` and `considered` in the affinity detail).

## Code review outcomes

A high-effort review of the implementation raised ten findings; all
were addressed except one accepted limitation:

1. The startup reconcile (formerly a `Node.instances` setter working
   from a snapshot taken before minutes of restore work) now re-checks
   each removal candidate's authoritative `Instance.placement` before
   unrecording it, so a concurrently placed instance is never removed.
2. **Accepted limitation**: during the rolling-upgrade window, a
   not-yet-restarted `sf-database` ignores the new `fields` proto
   field and performs a full-row write. This degrades to exactly the
   previous release's semantics until the daemon rolls, matching how
   the instance/artifact/network masks shipped; no guard is added.
3. The v3 migration guards against a missing `node_attributes` table
   (older databases; table-ensure ordering runs object_references
   first).
4. Instead of freezing the legacy column at migration time, the new
   code dual-writes it (masked, locked) and unions it into reads for
   one transition release, so mid-roll placements from old nodes stay
   visible to upgraded schedulers and a rollback reads fresh data.
5. The v3 version stamp is withheld when the seeding hit retryable
   write errors, so a re-run of ensure-mariadb-schema retries them.
   Corrupt (non-list) values are logged and skipped without blocking.
6. The migration skips nodes in deleted or error states so stale
   lost-update residue does not become permanent phantom references.
7. The public `update_*_attributes` functions validate the field mask
   before dispatch, so a bad field name raises ValueError on the gRPC
   path exactly as it does on the direct path, instead of becoming a
   silently discarded StatusReply failure.
8. `Instance.hard_delete()` removes any INSTANCE_LOCATION references
   targeting the instance as a backstop for delete paths that could
   not resolve the placement node.
9. The mock reference store keys rows on the real table's primary key
   (which excludes relationship_value) and mirrors the real upsert's
   keep-first-value behaviour.
10. The mock's remove_relationship returns True whether or not a row
    existed (False only means database error), matching the real
    contract, and only removes a row whose stored relationship_value
    matches.

## Follow-ups (not this PR)

- Drop `node_attributes.daemons`: registration is derivable from
  `node_daemon_states` rows (register creates a state row, deregister
  deletes it). One release after this PR, remove the column together
  with `instances` and the vestigial `daemon_states`/etcd columns.
- Consider whether `Node.blobs` / `BLOB_LOCATION` should migrate from
  fqdn to UUID keying as part of the wider FQDN→UUID cleanup.
