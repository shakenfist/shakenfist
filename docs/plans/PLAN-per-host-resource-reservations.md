# Per-host resource reservations for Shaken Fist nodes

## Prompt

Before responding to questions or discussion points in this document, explore
the shakenfist codebase thoroughly. Read the relevant source files and ground
every answer in what the code does today rather than this plan's summary of it.
The load-bearing files are:

- `shakenfist/config.py` — the reservation `Field`s and `load_cluster_config()`.
- `shakenfist/daemons/resources/main.py` — per-node reservation computation and
  the metrics a node publishes about itself.
- `shakenfist/scheduler.py` — how admission consumes those metrics.
- `shakenfist/operations/node_blob_op.py`, `shakenfist/blob.py`,
  `shakenfist/node.py`, `shakenfist/daemons/cluster/main.py` — the other
  `MINIMUM_FREE_DISK` consumers.
- `shakenfist/deploy/collection/roles/node/templates/config`,
  `shakenfist/deploy/collection/roles/node/meta/argument_specs.yml`, and
  `examples/_shared/site.yml` — the deploy-side templating.

Consult `ARCHITECTURE.md`, `CLAUDE.md`, and `GOALS.md`. Line numbers in this
plan are approximate (captured against a nearby `develop` SHA) — re-verify by
symbol name before editing. Flag any uncertainty explicitly rather than
guessing.

## Situation

Shaken Fist reserves CPU and RAM on each hypervisor for the OS and host-level
system services, so the scheduler does not pack instances into capacity those
services need. Today the reservation is expressed as four cluster-global config
keys:

- `RAM_SYSTEM_RESERVATION` (default 2.0 GB) and `CPU_SYSTEM_RESERVATION`
  (default 1 core) — applied on every hypervisor.
- `RAM_INFRA_ROLE_RESERVATION` (4.0 GB) and `CPU_INFRA_ROLE_RESERVATION`
  (1 core) — added on nodes carrying a Shaken Fist infra role
  (`NODE_IS_NETWORK_NODE` or `NODE_IS_DATABASE_NODE`).

Disk has no per-node/per-role reservation at all: a single cluster-global
`MINIMUM_FREE_DISK` (default 20 GB) is subtracted from `disk_free_instances`
at schedule time and consulted by the blob-placement code.

How a config value becomes per-node vs cluster-global is determined by where it
is stored. `SFConfig(BaseSettings)` reads every setting from `SHAKENFIST_<KEY>`
environment variables, populated in two layers at daemon start:

1. **`/etc/sf/config`** — systemd `EnvironmentFile`, templated per host by the
   node role. Per-node keys such as `NODE_IS_NETWORK_NODE` live here.
2. **`cluster_config` table** — `load_cluster_config()` (`config.py` ~27-117)
   reads the table at import and writes each row into `os.environ`
   **unconditionally, overwriting Layer 1**.

So a key is per-node only if it lives in Layer 1 and is never written to
`cluster_config`. `sf-ctl set-config` (`client/ctl.py` ~200-237 →
`mariadb.set_cluster_config`) writes a single global row; `site.yml` uses it to
push `RAM_SYSTEM_RESERVATION`, which is why that value is cluster-wide (and why
it collapses to one value sourced from the first database host). There is **no
delete/unset primitive** for `cluster_config` — `mariadb.py` exposes only
`get_cluster_config` and `set_cluster_config`.

The reservation numbers reach the scheduler differently per resource. For CPU
and RAM, each node's resources daemon bakes its own reservation into the
`cpu_schedulable` / `memory_reserved_mb` metrics it publishes, and the
scheduler reads those metrics (falling back to `config` only for stale metric
rows). For disk, the daemon publishes only raw `disk_free_*` figures and the
scheduler subtracts its **own** `config.MINIMUM_FREE_DISK` — the reservation is
not baked into a metric.

## Mission and problem statement

A zeek sensor now runs on `sf-1` and is not keeping up with its packet rate. We
want to reserve more CPU and RAM for that node so the scheduler leaves the
sensor room. We cannot do this today: raising the infra-role reservation also
raises it on `sf-2` (which hosts an `sf-database`) and every other infra-role
node, and Shaken Fist has no concept of a zeek sensor to key off.

**Mission:** make node resource reservations per-host overridable. Each of RAM
(GB), CPU (hardware **threads**), and root/storage-filesystem disk (GB) becomes
a single absolute per-node number that:

- **defaults** to a value computed during Ansible templating that reproduces
  today's effective reservation for that node (system baseline plus the
  infra-role bump), and
- can be **overridden per host** from the inventory, so `sf-1` can carve out
  headroom for a service the collection knows nothing about.

The values are delivered via `/etc/sf/config` (Layer 1) and never written to
`cluster_config` (Layer 2), so nothing overrides them.

### Approach (settled with the operator)

1. **Rename to new `NODE_*` keys** rather than reuse the existing ones. Three
   reasons: it guarantees no stale Layer-2 row can override the per-node value;
   the CPU unit changes from physical cores to threads and a silent
   meaning-change under the same name is a trap; and we are collapsing the
   system+infra split into one number.
2. **One absolute number per resource.** Ansible owns the default calculation
   (including the infra-role bump); the operator overrides the whole number.
   Server code reads one flat reserved value per resource — no infra-role
   branching remains in the daemon or scheduler.
3. **Disk reservation applies to every filesystem the resources daemon
   tracks**, replacing `MINIMUM_FREE_DISK`. Because remote evaluators judge a
   node's free space, the daemon must publish the per-node reservation as a
   metric and consumers must read the candidate node's published value — the
   same shape CPU and RAM already use.
4. **The old `cluster_config` `RAM_SYSTEM_RESERVATION` row is left inert.** Once
   the Field is removed, pydantic ignores the leftover env var, so no delete is
   needed for correctness. Building `sf-ctl unset-config` is deferred to Future
   work.

### New config keys

| New key | Type | Replaces | Field-default fallback |
|---------|------|----------|------------------------|
| `NODE_RAM_RESERVATION_GB` | float | `RAM_SYSTEM_RESERVATION` + `RAM_INFRA_ROLE_RESERVATION` | `2.0` |
| `NODE_CPU_RESERVATION_THREADS` | int | `CPU_SYSTEM_RESERVATION` + `CPU_INFRA_ROLE_RESERVATION` (semantics change: **threads**, not cores) | `2` |
| `NODE_DISK_RESERVATION_GB` | float | `MINIMUM_FREE_DISK` (now per-node, applied to all tracked paths) | `20` |

The Field default is only a last-resort fallback for deploys that bypass this
Ansible; Ansible always templates an explicit per-host value.

### Ansible default formulas (behaviour-neutral)

With `infra_role = inventory_hostname in (network_node ∪ database_node)`:

- **RAM** = `max(2.0, memtotal_gb * 0.1) + (infra_role ? 4.0 : 0)` — the current
  10% rule plus the infra bump.
- **CPU threads** = `(1 + (infra_role ? 1 : 0)) * 2` → **2** on a plain
  hypervisor, **4** on an infra-role node. We assume 2 threads/core (operator:
  close enough), which also matches the scheduler's existing `reserved_cores *
  2` legacy assumption, so no CPU-topology facts are needed.
- **Disk** = `20`, matching the retired `MINIMUM_FREE_DISK`.

A host_var override (`node_ram_reservation_gb`, `node_cpu_reservation_threads`,
`node_disk_reservation_gb`), when defined, wins over the computed default.

## Open questions

Resolved with the operator:

- **Disk target** — apply the reservation to *all* paths the resources daemon
  tracks (root/`sfroot`, `blobs`, `events`, `image_cache`, `instances`,
  `uploads`), not just the instances subdir. The root fs needs headroom too (so
  logging keeps working).
- **CPU default** — assume 2 threads/core.

Resolved during phase 1 detailed planning:

- **Scheduler legacy metrics fallback** (`scheduler.py` ~139-142): the scheduler
  already reads node-published metrics as its primary path; the config keys
  survive only in the rolling-upgrade fallback. Re-express both the CPU and RAM
  fallbacks against the new keys, drop the infra-role branch and the
  `reserved_cores * 2` (the new key is in threads). See phase 1.
- **`is_network_node` / `is_database_node` metrics** are load-bearing node
  identity (node.py, database tier, API, schema) and keep being published; only
  their use in reservation math is removed. The `infra_role` local is dropped.

Also resolved during phase 2 detailed planning:

- **`MINIMUM_FREE_DISK` consumers, local vs remote:** scheduler
  `_has_sufficient_disk` / `summarize_resources` and the blob-placement helper
  `nodes_by_free_disk_descending` (used by `blob.py` and the cluster rebalance)
  judge remote nodes → read the candidate's published `disk_reservation_gb`;
  `node_blob_op._ensure_local` judges the local node → reads `config`. See
  phase 2.

Remaining:

1. **Stale `cluster_config` cleanup** — leave the `RAM_SYSTEM_RESERVATION` row
   inert, or build `sf-ctl unset-config`? Deferred to Future work unless the
   dead key in `show-config` proves annoying.

## Execution

Implementation is done by sub-agents per the shared execution model below; the
management session plans, reviews the actual files, and commits (one commit per
phase). Each phase's detailed steps, effort, model, and briefs live in its own
plan file.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Config keys + CPU/RAM reservation math | [PLAN-per-host-resource-reservations-phase-01-config-cpu-ram.md](PLAN-per-host-resource-reservations-phase-01-config-cpu-ram.md) | Complete |
| 2. Disk reservation model (metric + consumers) | [PLAN-per-host-resource-reservations-phase-02-disk-metric.md](PLAN-per-host-resource-reservations-phase-02-disk-metric.md) | Complete |
| 3. Ansible templating and default computation | [PLAN-per-host-resource-reservations-phase-03-ansible-templating.md](PLAN-per-host-resource-reservations-phase-03-ansible-templating.md) | Complete |
| 4. Docs, plan index, and cleanup | [PLAN-per-host-resource-reservations-phase-04-docs-cleanup.md](PLAN-per-host-resource-reservations-phase-04-docs-cleanup.md) | Complete |

Phases 1 and 2 are server-side and independently committable (each builds and
passes unit tests). Phase 3 is deploy-side and has no unit-test coupling to 1/2.
Phase 4 is documentation plus optional cleanup. The 33fl-side `sf-1` override
lives in the 33fl repo on its own branch and is out of scope for this plan
(tracked in Future work).

## Agent guidance

### Execution model

All implementation work is done by sub-agents; the management session is
reserved for planning, review, and decision-making. For each step: spawn a
sub-agent with the brief from the phase file at the recommended effort/model,
then **read the actual changed files** (the summary describes intent, not
necessarily what happened), fix or retry if wrong, and commit once satisfied.
Use `isolation: "worktree"` for risky steps.

### Management session review checklist

- [ ] The intended files changed; no unrelated files touched.
- [ ] `pre-commit run --all-files` passes (flake8, stestr unit tests, mypy).
- [ ] The change is semantically right, not just syntactically valid — in
      particular, reservations are **behaviour-neutral** for every node except
      the one deliberately overridden.
- [ ] Commit message follows project conventions, including the `Co-Authored-By`
      line with model, context window, effort level, and any other settings.

## Administration and logistics

### Success criteria

- `pre-commit run --all-files` passes (flake8, stestr unit tests, mypy).
- The four old reservation Fields and `MINIMUM_FREE_DISK` are gone; the three
  new `NODE_*` keys exist, are read as ordinary `config.X` values, and are never
  written to `cluster_config`.
- Each node's deployed reservation reproduces today's **intended** value
  (system baseline + infra-role bump), verified by inspecting `/etc/sf/config`
  and node metrics on a plain hypervisor, an infra-role node, and an overridden
  node. Note this is not a strict numeric no-op (see phase 3's honest
  accounting): RAM becomes each node's own 10% rather than the old cluster-wide
  single value (a correction, differs on heterogeneous-RAM clusters), and CPU
  uses the accepted 2-threads/core approximation (over-reserves ~1 thread on
  non-hyperthreaded nodes). Disk is identical at the default.
- The disk reservation is enforced against all resources-daemon-tracked paths,
  sourced per-node from a published metric.
- Unit tests cover the reservation math (CPU threads, RAM, disk floor) and the
  removal of the infra-role branch; functional coverage where practical
  (`shakenfist/deploy/shakenfist_ci/cluster_ci_tests`).
- Lines wrapped at 120 chars, single quotes for strings, double quotes for
  docstrings. No proto changes are expected; if any are made, regenerate with
  `tox -e genprotos`.
- `docs/` updated (operator docs for the config-key rename and the new disk
  semantics); `ARCHITECTURE.md` / `README.md` / `AGENTS.md` updated if module
  behaviour changes materially.

### Future work

- **33fl `sf-1` override.** Set `node_cpu_reservation_threads` /
  `node_ram_reservation_gb` (and disk if warranted) in `host_vars/sf-1.yml` to
  give the zeek sensor headroom. Separate repo, separate branch. Size the values
  from sf-1's real zeek footprint.
- **`sf-ctl unset-config`.** A genuine gap — there is no way to delete a
  `cluster_config` row. Build the direct + gRPC delete path and the CLI command,
  then retire the inert `RAM_SYSTEM_RESERVATION` row cleanly.
- **Interaction with `PLAN-scheduler-reservations.md`** (atomic scheduling via a
  reservations table) — unrelated mechanism, but both touch scheduler admission;
  whoever lands second should re-read the other's disk/CPU accounting.

### Bugs fixed during this work

- **Local blob-replication disk check reserved bytes, not GB**
  (`operations/node_blob_op.py:123`, found during phase 2 planning). The
  `_ensure_local` guard compared `disk_free_blobs − b.size` (both bytes) against
  `MINIMUM_FREE_DISK` (20, meant GB), so it effectively reserved ~20 *bytes*
  before pulling a local replica — the free-disk floor was almost never
  enforced on that path. Every other consumer divides free space by `GiB`
  first. Phase 2 fixes it by reserving `NODE_DISK_RESERVATION_GB * GiB` bytes.
  This makes the local path meaningfully stricter than before — an intentional
  behaviour change, called out in the phase-2 commit.

_(Scan the shakenfist GitHub issue tracker for other open issues touching
reservations, scheduling headroom, or `MINIMUM_FREE_DISK`.)_

### Documentation index maintenance

On creation of this master plan, `docs/plans/index.md` gains one *Master plans*
row and `docs/plans/order.yml` gains a master-plan entry (phase files are listed
in neither). Update the status column as phases complete.

### Back brief

Before executing any step, the implementing agent must back-brief the operator
on its understanding of the plan and how the intended work aligns with it.
