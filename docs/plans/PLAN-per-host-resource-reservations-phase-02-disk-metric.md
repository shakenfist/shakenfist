# Phase 2: Disk reservation model (metric + consumers)

Master plan: [PLAN-per-host-resource-reservations.md](PLAN-per-host-resource-reservations.md)

## Goal

Make the disk reservation per-node, replacing the flat cluster-global
`MINIMUM_FREE_DISK` with the per-host `NODE_DISK_RESERVATION_GB` added (unused)
in phase 1. Because the code that judges a node's free disk usually runs on a
*different* node, the owning node publishes its reservation as a metric and
remote evaluators read the candidate node's published value — mirroring how
`cpu_schedulable` / `memory_reserved_mb` already work. Along the way, fix a
pre-existing bytes-vs-GB unit bug in the local blob check.

Behaviour-neutral when every node keeps the default reservation (the new Field
default is `20.0`, equal to the retired `MINIMUM_FREE_DISK`).

All references verified against the `node-reservation-overrides` worktree at
this phase's start; re-confirm by symbol before editing.

## Verified code map

| What | Location |
|------|----------|
| Disk-measurement block (statvfs loop) | `shakenfist/daemons/resources/main.py:230-262` |
| Per-path metrics published (bytes) | `disk_free_sfroot`, `disk_free_blobs`, `disk_free_events`, `disk_free_image_cache`, `disk_free_instances`, `disk_free_uploads` (main.py:254) |
| Aggregates | `disk_total`, `disk_free` (min free across **distinct** filesystems, deduped by `f_fsid`), `disk_used` (main.py:258-262) |
| Seam to publish `disk_reservation_gb` | the `retval.update({...})` at main.py:258-262 |
| Scheduler metric cache | `self.metrics[node]`, populated by `get_active_node_metrics()` (scheduler.py:47-81) via `mariadb.get_node_metrics` per active node |
| Blob-placement shared helper | `nodes_by_free_disk_descending(minimum, maximum, intention)` — `shakenfist/node.py:702-726` |
| `mariadb.get_node_metrics` shape | returns `{'node_uuid','fqdn','timestamp','metrics':{...}}`; callers unwrap `.get('metrics', {})` |

### Consumers and local/remote verdicts (the crux)

| Site | file:line | Metric read today | Node judged | Verdict |
|------|-----------|-------------------|-------------|---------|
| `_has_sufficient_disk` | scheduler.py:221-222 | `disk_free_instances` | candidate hypervisor | **REMOTE** — `self.metrics[node]` |
| `summarize_resources` | scheduler.py:618-620 | `disk_free_instances` | every node in `self.metrics` | **REMOTE** — `self.metrics[n]` |
| `_ensure_local` | operations/node_blob_op.py:114-124 | `disk_free_blobs` | **this** node (`NODE_UUID`) | **LOCAL** — may read `config` |
| `request_replication` | blob.py:891-894 → node.py:717 | `disk_free_blobs` (`intention='blobs'`) | candidate targets | **REMOTE** — via helper |
| blob rebalance | daemons/cluster/main.py:184-190 → node.py:717 | `disk_free_blobs` | all active nodes | **REMOTE** — via helper |

## Resolved design decisions

1. **Publish the reservation as a metric.** Emit
   `retval['disk_reservation_gb'] = config.NODE_DISK_RESERVATION_GB` in the disk
   block (main.py:258-262). Raw `disk_free_*` reporting is unchanged, so free
   space stays visible for debugging.
2. **Metric-absent fallback.** Every remote consumer reads
   `metrics.get('disk_reservation_gb', config.NODE_DISK_RESERVATION_GB)`. Since
   the Field default (20.0) equals the retired `MINIMUM_FREE_DISK`, an old
   metrics row (mid-upgrade) falls back to a numerically identical value —
   behaviour-neutral.
3. **Helper contract change.** Refactor `nodes_by_free_disk_descending` to
   filter/sort on **per-node headroom** = `disk_free_<intention>_gb − that
   node's reservation`, reading `metrics.get('disk_reservation_gb', default)`
   inside the existing per-node loop (node.py:709-724). Callers stop folding the
   reservation into `minimum`/`maximum`:
   - `blob.py:893` → `minimum=blob_size_gb` (was `blob_size_gb +
     MINIMUM_FREE_DISK`); the helper now subtracts each node's own reservation.
   - `cluster/main.py:189` "low disk" band → `maximum=2 *
     config.NODE_DISK_RESERVATION_GB` in headroom terms (headroom < 2×res ⟺ raw
     free < 3×res when reservation is default, preserving today's `3 ×
     MINIMUM_FREE_DISK` trigger). This is a rebalancing heuristic, so keying the
     band off the cluster daemon's own default is acceptable; note it in code.
4. **Fix the local-blob unit bug** (`node_blob_op.py:123`). Today it compares
   bytes against `MINIMUM_FREE_DISK` (GB), effectively reserving ~20 bytes.
   Convert properly: reserve `config.NODE_DISK_RESERVATION_GB * GiB` bytes (or
   divide free by `GiB` first). Record in the master plan's "Bugs fixed".
5. **Remove `MINIMUM_FREE_DISK`** once all consumers are converted; grep to
   confirm zero references.

## Decided: enforce at the two allocation points (Option B, operator-confirmed)

The stated intent is to keep the reservation free on *every* filesystem the
resources daemon tracks (including root, so logging keeps working). But only two
tracked filesystems have a placement decision that gates on free space:
`instances` (instance scheduling) and `blobs` (blob placement). Nothing
allocates onto `events`/`uploads`/`image_cache`/`sfroot`, so there is no natural
enforcement point for them today.

- **Option B (recommended, default in the steps below):** enforce the per-node
  reservation at the two existing allocation points on their current path
  metrics (`disk_free_instances` for scheduling, `disk_free_blobs` for blob
  placement). This protects the two filesystems SF actually fills. Filesystems
  with no placement path are not independently gated. Minimal, low-footgun,
  preserves today's path targeting.
- **Option A:** switch every check to the aggregate `disk_free` (the tightest
  filesystem, main.py:260) minus the reservation, guaranteeing the floor holds
  on *every* tracked filesystem including root. Cost: more conservative — an
  instance could be rejected because an unrelated small filesystem (e.g.
  `uploads`) is tight — and it changes blob placement to read the aggregate
  rather than the blobs filesystem.

**The operator chose Option B.** The steps below implement it. (If root/logging
ever needs explicit protection, switching the reads to the aggregate `disk_free`
is a one-line metric-key change per site plus test adjustments — recorded as a
possible future extension.)

## Target implementation (reference)

```python
# resources/main.py, in the disk retval.update
retval.update({
    'disk_total': total,
    'disk_free': minimum,
    'disk_used': used,
    'disk_reservation_gb': config.NODE_DISK_RESERVATION_GB,
})

# scheduler._has_sufficient_disk
reservation = self.metrics[node].get(
    'disk_reservation_gb', config.NODE_DISK_RESERVATION_GB)
disk_free = int(self.metrics[node].get('disk_free_instances', '0')) / GiB
disk_free -= reservation

# node.py nodes_by_free_disk_descending, inside the per-node loop
reservation = metrics.get('disk_reservation_gb', config.NODE_DISK_RESERVATION_GB)
headroom_gb = int(int(metrics.get('disk_free%s' % intention, '0')) / GiB) - reservation
if headroom_gb < minimum:
    continue
if maximum != -1 and headroom_gb > maximum:
    continue
by_disk[headroom_gb].append(n.fqdn)

# node_blob_op._ensure_local (bug fix: convert to bytes)
reservation_bytes = config.NODE_DISK_RESERVATION_GB * GiB
if int(metrics.get('disk_free_blobs', 0)) - int(b.size) < reservation_bytes:
    ...skip...
```

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | low | haiku | none | In `shakenfist/daemons/resources/main.py`, add `'disk_reservation_gb': config.NODE_DISK_RESERVATION_GB` to the disk `retval.update({...})` at ~258-262. Nothing else. |
| 2b | high | opus | none | In `shakenfist/scheduler.py`, convert `_has_sufficient_disk` (221-222) and `summarize_resources` (618-620) to subtract the candidate node's published reservation: `self.metrics[node].get('disk_reservation_gb', config.NODE_DISK_RESERVATION_GB)` instead of `config.MINIMUM_FREE_DISK`. Keep reading `disk_free_instances` (Option B). Update the reason dict at 228 and any comments. |
| 2c | high | opus | none | Refactor `nodes_by_free_disk_descending` in `shakenfist/node.py:702-726` to subtract each node's reservation inside the loop and filter/sort on headroom (see Target implementation). Update the two callers: `blob.py:893-894` → `minimum=blob_size_gb`; `daemons/cluster/main.py:189-190` → `maximum=2 * config.NODE_DISK_RESERVATION_GB`. Update the helper docstring and the callers' comments to describe reservation-aware headroom. Verify no other caller of the helper exists. |
| 2d | medium | opus | none | In `shakenfist/operations/node_blob_op.py:114-124`, replace `config.MINIMUM_FREE_DISK` with the local reservation and **fix the unit bug**: the comparison is in bytes, so reserve `config.NODE_DISK_RESERVATION_GB * GiB` (import/confirm `GiB`). Since this is the local node, reading `config.NODE_DISK_RESERVATION_GB` directly is correct; `metrics` here is the local row. Add a comment noting the historical byte/GB bug it fixes. |
| 2e | low | haiku | none | Remove the `MINIMUM_FREE_DISK` Field from `shakenfist/config.py:257-261`; grep the whole tree (production + tests + deploy) to confirm zero remaining references. |
| 2f | medium | opus | none | Tests in `shakenfist/tests/test_scheduler.py`: adapt `test_not_enough_disk` (140) — it relies on the default reservation arithmetic (`20*GiB` free − 20 = 0), which stays true with `NODE_DISK_RESERVATION_GB=20`; add `disk_reservation_gb` to the fake baselines where needed. Add NEW coverage: (1) a node with a higher `disk_reservation_gb` is admitted with less free disk than a default node (per-node override honoured); (2) `nodes_by_free_disk_descending` respects a per-node reservation (blob placement); (3) a regression test for the `_ensure_local` byte/GB fix. Use `update_node_metrics` to give one node a different `disk_reservation_gb`. Run `tox -e py3` green. |

Steps 2a→2f in order; 2c depends on the helper being the sole blob seam. This is
one logical change → **one commit** for the phase.

## Verification

- `pre-commit run --all-files` green (touches `shakenfist/` Python — flake8/py3
  run for real; mypy still doesn't cover these paths).
- Grep confirms zero `MINIMUM_FREE_DISK` references anywhere.
- With all reservations at the default 20, every disk decision matches today's
  behaviour (the `_ensure_local` site now reserves a real 20 GiB instead of the
  buggy ~20 bytes — an intentional behaviour change; call it out in the commit).
- A node given a larger `node_disk_reservation_gb` (phase 3 / inventory) advertises
  less schedulable and blob-placement headroom than an identical default node,
  verified via metrics and a test placement.
