# Phase 2 — static object value caching

Master plan: [PLAN-database-load-reduction.md](PLAN-database-load-reduction.md)

**Status: Complete (pending deploy measurement).** Landed in four
commits: 2a feeds the version cache from `get_all_node_metrics` (no
per-node `get_node`); 2b adds the cache core (process-global dict +
lock, `get`/`put`/`evict`, hit/miss/evict counters, two TTL config
options, `str()`-normalised keys); 2c+2d wire all ten public
`get_<type>()` readers and all `update_/delete_/create_namespace`
writers (landed together as the cache is inert until fully wired);
2e is this docs/status pass (ARCHITECTURE.md caching section,
operator guide config table). `pre-commit run --all-files` green
including mypy `--strict`. The before/after `database_get_*` numbers
on sfcbr will be recorded here once phase 1 and phase 2 deploy.

## Goal

Restore the etcd-era principle "objects are cacheable, attributes are
not" in the MariaDB world: add a narrow read-through cache for
*immutable static object values* at the `mariadb.py` client boundary,
and remove the redundant per-node `get_node` in
`_maintain_version_cache`. Cut the remaining steady-state `get_node`
load (dominated post-phase-1 by the version cache, ~20/s) and the
repeated point re-hydration of hot immutable objects (Blob, Node,
Instance) in the scheduler, cluster, and resources loops — and make
bursty API object hydration cheaper as a side effect.

Explicitly **out of scope**: caching any mutable data — object
states, metadata, attributes, daemon states, IPAM, reservations,
queues, locks, references. Those stay strictly read-through.

## Findings (the caching surface)

An investigation of every `get_<type>()` reader, its writers, and its
hotness established the properties this design depends on:

* **Clean static/mutable split.** Every public `get_<type>()` returns
  a `frozen=True` Pydantic model containing *only* static columns;
  every mutable field is fetched by a separate
  `get_<type>_attributes()`. No reader mixes static and mutable
  fields. So caching a whole `get_<type>()` result is safe — there is
  no mutable field hiding inside it, and the frozen model needs no
  defensive copy on read.
* **Both code paths funnel through the public function.**
  `get_<type>()` branches on `_use_database_service()` (mariadb.py:293)
  into `_grpc_*`/`_direct_*`. A cache placed *above* that branch
  transparently serves gRPC callers (compute nodes) and direct
  callers (the sf-database daemon's own 64 threads) alike. The same
  is true for the `update_*`/`delete_*` invalidation hooks.
* **Static rows are immutable after creation except for two events:**
  1. **Online version upgrade** persists a bumped row. Crucially, it
     goes through the *public* `update_<type>` (verified:
     `Blob._persist_pydantic_upgrade` → `mariadb.update_blob`,
     `Node` → `mariadb.update_node`, `Namespace` →
     `delete_namespace`+`create_namespace`). So an eviction hook on
     the public `update_<type>` makes the cache self-heal after an
     upgrade. Upgrades are rare (once per object per cluster-wide
     version bump, gated on all nodes already being updated).
  2. **`hard_delete`** removes the row via the public `delete_<type>`.
* **Four types have NO post-creation writer at all** — Instance,
  Network, NetworkInterface, AgentOperation. Their rows change only
  on create and delete. (`update_network_interface` exists but has
  zero object-layer callers; the others have no `update_*` at all.)
* **The hot re-hydration is repeated point `from_db()` in loops**, not
  iterators (iterators already do a single bulk `find_*` query). The
  scheduler's nested `Node.from_db`/`Instance.from_db`
  (scheduler.py:422-435), the cluster daemon's `Blob.from_db` scans,
  the blob replication loops, and the resources daemon's per-cycle
  `Node.from_db(NODE_NAME)` re-fetch the same immutable rows
  repeatedly. Blob, Node, Instance are the highest-value targets.
* **`_maintain_version_cache`'s `get_node` (baseobject.py:78) is
  redundant.** It fetches a node solely for `fqdn`, but the loop
  already calls `get_node_metrics` whose dict carries `fqdn`; and
  `get_all_node_metrics()` returns one list of
  `{node_uuid, fqdn, timestamp, metrics}` that replaces the
  per-node loop entirely.

## Design decisions

1. **Location: the public `get_<type>()` boundary in `mariadb.py`.**
   Above `_use_database_service()`, so one cache serves both paths.
2. **Structure: one process-global dict keyed `(object_type, uuid)` →
   frozen model, guarded by a dedicated `threading.Lock`** (separate
   from `TABLE_CREATION_LOCK`). Process-global (not thread-local)
   because the sf-database daemon runs 64 worker threads in one
   process — a thread-local cache would be 64 disjoint copies with
   64× the miss rate. Compute-node daemons are ~single-threaded, so
   process-global there is effectively per-daemon and small.
3. **Every entry is TTL-bounded.** This is a correctness requirement,
   not just hygiene: a cross-process `hard_delete` or upgrade fires no
   local eviction hook, so the only bound on that staleness is the
   TTL. Two tiers:
   * **Immutable types (Instance, Network, NetworkInterface,
     AgentOperation): long TTL, default 300s.** Only cross-process
     deletion can make them stale.
   * **Upgradeable types (Blob, Node, Artifact, Upload, DnsMasq,
     Namespace): short TTL, default 30s.** Bounds cross-process
     upgrade-persist staleness (already tolerated — the object layer
     lazily re-upgrades an old-version model on load) and deletion.
   Both TTLs are config options; **0 disables that tier** (kill
   switch). Local writes still invalidate immediately via the hooks.
4. **Never cache misses.** Only present rows are cached; a `None`
   result is never stored, so a create-after-lookup or a
   delete-then-lookup can never be masked by a negative entry.
5. **Invalidation hooks at the public write layer:** every
   `delete_<type>` evicts `(type, uuid)`; every `update_<type>` for
   the upgradeable tier evicts; `create_namespace`/`delete_namespace`
   evict (namespace upgrades via delete+recreate). Immutable-tier
   types need eviction only on `delete_<type>`.
6. **Do not cache:** `get_cluster_operation` / all `operations/*`
   (high create/hard_delete churn), `get_node_by_fqdn` (fqdn-keyed;
   low volume ~2/s — a follow-up could add an fqdn→uuid index), and
   anything read via `find_*` iterators (already single-query).

## Open question 2 — TTL (resolved)

Resolved to the **two-tier TTL above (300s immutable / 30s
upgradeable), both config-tunable, 0 = disabled**. Rationale: an
unbounded "process-lifetime" cache is unsafe because a cross-process
`hard_delete` would let a dead object resurrect indefinitely (the
scheduler resolving `Instance.from_db` against a deleted instance is
the concrete hazard); a bounded TTL caps that at the tier interval,
and callers already tolerate a `get_<type>()` racing a concurrent
delete at the DB level (the cache only widens that window to the
TTL). The upgradeable tier is short because the DB genuinely changes
there (rarely) cross-process; the immutable tier is long because only
deletion changes it. Own-node identity (the resources daemon's
per-cycle `Node.from_db(NODE_NAME)`) is served by the Node tier — no
special process-lifetime case is needed.

## Architecture

```
get_<type>(uuid):
    hit = _object_cache_get(type, uuid)        # under lock; respects TTL
    if hit is not None:
        counters['object_cache_hit'].inc(); return hit
    counters['object_cache_miss'].inc()
    row = _grpc_get_<type>(...) if _use_database_service() else _direct_get_<type>(...)
    if row is not None:                         # never cache misses
        _object_cache_put(type, uuid, row, ttl=TTL_FOR[type])
    return row

update_<type>(data):    ...write...; _object_cache_evict(type, data.uuid)   # upgradeable tier
delete_<type>(uuid):    ...write...; _object_cache_evict(type, uuid)         # all tiers
```

The self-healing property: because `_persist_pydantic_upgrade` calls
the public `update_<type>`, the very act of committing an online
upgrade evicts the pre-upgrade entry, so the next read re-fetches the
new version. No special upgrade handling is needed beyond the
`update_<type>` hook.

## Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 2a | medium | sonnet | none | **Remove the redundant `get_node` in `_maintain_version_cache`** (baseobject.py:55-127). Source `fqdn` from the metrics dict instead of `mariadb.get_node(uuid)` at line 78 — prefer rewriting the loop over `get_all_node_metrics()` (returns `{node_uuid, fqdn, timestamp, metrics}` per node in one RPC), falling back to the existing per-node `get_node_metrics` shape if simpler. No cache involved. Independent, shippable alone; removes the dominant post-phase-1 `get_node`. Unit test that `get_node` is not called on the version-cache refresh path. |
| 2b | high | opus | none | **Build the cache core in `mariadb.py`** (no wiring yet): a process-global `_OBJECT_CACHE: dict[tuple[str, UUID], tuple[float, Any]]` (expiry-stamp, model), a dedicated `_OBJECT_CACHE_LOCK`, config options `OBJECT_CACHE_TTL_IMMUTABLE` (default 300) and `OBJECT_CACHE_TTL_MUTABLE` (default 30) in `config.py` (0 disables), and helpers `_object_cache_get(otype, uuid)` (returns None on miss/expiry, holds the lock), `_object_cache_put(otype, uuid, model, ttl)` (no-op when ttl==0; never called with None), `_object_cache_evict(otype, uuid)`. Register `object_cache_hit`/`object_cache_miss`/`object_cache_evict` counters in the sf-database monitor op list (`daemons/database/main.py:4985`) and increment them (they surface on the direct path; note client-side gRPC processes won't emit them unless the 13007 node metrics endpoint exports client counters — acceptable, primary signal is the server-side `get_<type>` drop). Full unit coverage of the core: hit/miss, TTL expiry, ttl==0 disable, never-store-None, evict. |
| 2c | medium | sonnet | none | **Wire the immutable tier** (long TTL): add the cache read at the top of `get_instance`, `get_network`, `get_network_interface`, `get_agent_operation`, and `_object_cache_evict` in `delete_instance`, `delete_network`, `delete_network_interface`, `delete_agent_operation`. Unit test: second `get_instance` is served from cache (no `_grpc`/`_direct` call); `delete_instance` evicts; expiry re-fetches. |
| 2d | high | opus | none | **Wire the upgradeable tier** (short TTL): add the cache read at the top of `get_node`, `get_blob`, `get_artifact`, `get_upload`, `get_dnsmasq`, `get_namespace`; add `_object_cache_evict` in `update_node`, `update_blob`, `update_artifact`, `update_upload`, `update_dnsmasq`, and in `create_namespace`+`delete_namespace` and every corresponding `delete_<type>`. **Verify each `_persist_pydantic_upgrade` routes through the hooked public `update_<type>`** (confirmed for Node/Blob/Namespace; check Artifact/Upload/DnsMasq). Unit test the self-heal: a cached old-version row, an `update_<type>`, then a read returns the new row. Test delete-eviction and TTL. |
| 2e | medium | sonnet | none | **Tests, docs, measurement.** Consolidated cache tests (concurrency smoke: interleaved put/evict under the lock; no negative caching; per-tier TTL). Update `ARCHITECTURE.md` with the caching model and the objects-cacheable/attributes-not principle, and `docs/operator_guide/` for the two new config options. Record the before/after `database_get_node_total` / `database_get_blob_total` / `database_get_instance_total` rates on sfcbr here once deployed. |

## Correctness invariants (must hold, and how)

* **No mutable data cached.** Only `get_<type>()` static readers are
  wired; states/attributes/metadata/IPAM/queues/locks are untouched.
* **Upgrades self-heal.** `_persist_pydantic_upgrade` → public
  `update_<type>` → evict. Verified for the wired types in 2d.
* **No resurrection after delete.** Every `delete_<type>` evicts;
  cross-process deletes bounded by TTL; misses never cached.
* **Version gating never reads a cached static row.**
  `_maintain_version_cache` is fixed (2a) to not call `get_node` at
  all, and the upgrade gate reads node *metrics*, not `get_<type>()`.
* **Thread-safe.** All get/put/evict take `_OBJECT_CACHE_LOCK`. The
  read-fetch-vs-concurrent-write race (thread B caches an old row
  just after thread A evicts) is bounded by TTL, as at the DB layer.

## Verification

* `pre-commit run --all-files` green (flake8, unit suite, mypy).
* Unit tests per step above; the full-cluster CI (which exercises
  instance/network/blob create+delete and online object hydration)
  stays green — this is where a bad invalidation hook would surface
  as a stale object.
* On deploy to sfcbr: `database_get_node_total` falls toward ~0 (2a
  removes the version-cache reads; the Node tier absorbs the rest),
  and `database_get_blob_total` / `database_get_instance_total` drop
  by the loop re-hydration factor. Record numbers here.
* A kill-switch check: setting both TTLs to 0 restores exact
  pre-phase-2 behaviour (pure read-through) — a fast rollback path
  that needs no redeploy of code, only config.

## Risks

* **A stale cached object driving a wrong decision** (scheduler,
  cluster reaper). Bounded by TTL and by callers' separate liveness
  (`get_state`) checks; the kill switch is the escape hatch.
* **A missed invalidation hook** on some `update_/delete_` path.
  Mitigated by wiring hooks at the single public layer, the
  `_persist_pydantic_upgrade` verification in 2d, and CI's
  create/delete lifecycle coverage.
* **Memory growth** (blobs number in the thousands). Bounded by TTL
  expiry; if needed, add a size cap in a follow-up (noted, not built).

## Success criteria

* Static `get_<type>()` reads for the wired types are served from
  cache within their TTL; no mutable data is cached.
* `_maintain_version_cache` issues no `get_node`.
* Online upgrades and deletions are reflected correctly (self-heal +
  eviction); CI green.
* `database_get_node_total` and the hot static readers fall on sfcbr;
  both-TTLs-zero cleanly disables the cache.
