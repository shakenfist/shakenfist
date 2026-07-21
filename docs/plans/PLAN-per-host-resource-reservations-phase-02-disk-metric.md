# Phase 2: Disk reservation model (metric + consumers)

Master plan: [PLAN-per-host-resource-reservations.md](PLAN-per-host-resource-reservations.md)

## Goal

Make disk reservation per-node and applied to every filesystem the resources
daemon tracks, replacing the flat cluster-global `MINIMUM_FREE_DISK`. Because
the code that judges a node's free disk usually runs on a *different* node
(scheduler on the network node; blob placement deciding where to put a blob),
the reservation must be **published as a metric** by the owning node and read
from that metric by remote evaluators — mirroring how `cpu_schedulable` /
`memory_reserved_mb` already work.

## Background (verify against the code)

- The resources daemon (`shakenfist/daemons/resources/main.py` ~239-271)
  `os.statvfs()`es `config.STORAGE_PATH` and the subdirs `blobs`, `events`,
  `image_cache`, `instances`, `uploads`, publishing `disk_free_<path>` (root as
  `disk_free_sfroot`) plus aggregate `disk_total` / `disk_free` / `disk_used`.
  No reservation is subtracted at report time.
- `MINIMUM_FREE_DISK` consumers to convert: `scheduler.py` (`_has_sufficient_disk`
  ~216-237, `summarize_resources` ~622-625), `operations/node_blob_op.py`
  ~123-124, `blob.py` ~894, `node.py` ~716-724, `daemons/cluster/main.py` ~190.
  Line numbers approximate — locate by symbol.

## Design decisions to make in this phase

- **Local vs remote at each call site.** For each `MINIMUM_FREE_DISK` consumer,
  determine whether it evaluates the **local** node (then reading
  `config.NODE_DISK_RESERVATION_GB` directly is correct) or a **remote/candidate**
  node (then it must read that node's published `disk_reservation_gb` metric).
  Document the disposition per site in the commit message.
- **Metric shape.** Publish a single `disk_reservation_gb` (the node's
  `NODE_DISK_RESERVATION_GB`) alongside the raw `disk_free_*` figures, and have
  consumers apply the floor per tracked path. Prefer this over pre-subtracting
  in the daemon, so raw free space stays visible in metrics for debugging.
- **Metric-absent fallback.** If a candidate node's `disk_reservation_gb` metric
  is missing (old row / node mid-upgrade), fall back to
  `config.NODE_DISK_RESERVATION_GB` on the evaluator and log at debug. Match
  whatever staleness convention the CPU/RAM metric paths already use.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | In `shakenfist/daemons/resources/main.py`, publish `disk_reservation_gb = config.NODE_DISK_RESERVATION_GB` in the metrics dict next to the `disk_free_*` figures. Do not change the raw free-space reporting. |
| 2b | high | opus | none | Convert `shakenfist/scheduler.py`: `_has_sufficient_disk` (~216-237) and `summarize_resources` (~622-625) must subtract the **candidate node's** published `disk_reservation_gb` instead of `config.MINIMUM_FREE_DISK`, applying the floor to each tracked path's free figure (at minimum the paths those functions already consider — extend to all tracked paths where the intent is "leave N GB free everywhere"). Implement the metric-absent fallback. Read the surrounding metric-access helpers to match conventions. |
| 2c | high | opus | none | Convert the blob-placement consumers — `operations/node_blob_op.py` (~123-124), `blob.py` (~894), `node.py` (~716-724), `daemons/cluster/main.py` (~190). For each, decide local vs remote (see design decisions) and switch to the per-node value accordingly. These decide *where* a blob lands, so a wrong local/remote choice silently mis-places blobs — reason carefully and note each decision. |
| 2d | low | haiku | none | Remove the now-unused `MINIMUM_FREE_DISK` `Field` from `shakenfist/config.py` and grep the tree to confirm zero remaining references. |
| 2e | medium | sonnet | none | Unit tests: a node publishes `disk_reservation_gb`; the scheduler's disk admission subtracts the candidate node's value and honours the metric-absent fallback; blob placement respects the per-node floor. Follow existing scheduler/blob test modules. `stestr` green. |

## Verification

- `pre-commit run --all-files` green.
- With `NODE_DISK_RESERVATION_GB = 20` everywhere, behaviour matches today's
  `MINIMUM_FREE_DISK = 20` for the instances path, and now additionally enforces
  the floor on the other tracked filesystems.
- Setting a higher value on one node reduces only that node's schedulable/
  placement headroom, verified via node metrics and a test placement.
