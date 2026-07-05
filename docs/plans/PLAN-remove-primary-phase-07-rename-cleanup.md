# Phase 7: rename etcd_master to database_node, and cleanup

This is a sub-plan of [PLAN-remove-primary.md](PLAN-remove-primary.md).
Phase 6 repackaged the deployer as the `shakenfist.shakenfist` ansible
collection and deleted the getsf installer chain. Phase 7 removes the
last etcd-era naming: the `etcd_master` inventory group (which has
meant "the database tier" since PLAN-byo-mariadb), and the vestigial
etcd-era node role flags.

## Context

The master plan scoped phase 7 as "mostly mechanical: rename
`etcd_master` → `database_node` across the surviving collection roles,
example playbooks, templates, CI, and comments". Investigation while
drafting this plan found the residue is broader than deployer naming,
and includes one silent test-coverage gap:

1. **The inventory group** (`etcd_master`) is authoritative in the
   collection world: `examples/_shared/site.yml` computes
   `node_is_database_node` from membership (`:145`), reduces
   `mariadb_gateway_hosts` from the group (`:156`), and targets four
   plays at it (`:248`, `:263`, `:291`, `:312`). It also appears in the
   example inventories and READMEs, `docs/operator_guide/installation.md`
   (which documents the rename caveat), and the actions repo's
   under-cloud tooling (`ci-topology-*.yml` `add_host` groups,
   `ci-include-common-localhost.yml`'s topology-facts dump, and the
   group emitted by `tools/ci-make-inventory.py`).

2. **The node role flags are vestigial.** `node_attributes` carries
   `is_etcd_master` and `is_eventlog_node` (schema, MariaDB column,
   proto field, API external view), but the only writer is
   `Node.observe_this_node()`, which hardcodes both to `False`
   (`shakenfist/node.py:314`). No config knob exists to set them. They
   are dead fields left behind by PLAN-remove-etcd, faithfully
   marshalled through every layer.

3. **The database tier is not recorded on the node object at all.**
   `node_is_database_node` exists only as a deploy-time ansible
   variable. There is no `NODE_IS_DATABASE_NODE` config setting and no
   node attribute, so nothing at runtime can enumerate the database
   tier.

4. **`test_database_tier.py` has never run its assertions.** It
   discovers the tier via `n.get('is_etcd_master')` — which is always
   `False` — so it always finds zero tier nodes and skips (including on
   the slim-tier CI topology that exists to exercise it). Skips count
   as passes, so this was invisible.

So the phase is: rename the group with a one-release compatibility
window, replace the dead flags with one real `is_database_node` node
attribute driven by config, and key the tier test on the real flag so
its assertions finally run.

## Decisions (phase-local)

- **`database_node` is the group name.** Matches the master plan and
  the role terminology used everywhere since PLAN-byo-mariadb.
- **One-release compatibility for inventories.** `site.yml` plays
  target `database_node:etcd_master` and membership/reduction
  expressions use the union of both groups, with a loud warning task
  when `etcd_master` is non-empty. The fallback is removed next
  release. Operator inventories therefore keep working unchanged for
  one cycle.
- **Replace, don't rename, the dead flags.** A new `is_database_node`
  node attribute is written from a new `NODE_IS_DATABASE_NODE` config
  setting (templated by the collection's node role from the existing
  `node_is_database_node` variable). The dead `is_etcd_master` /
  `is_eventlog_node` fields keep being emitted (always `False`) for one
  release so older clients don't KeyError, then the columns, proto
  fields (numbers reserved), API keys and schema fields are dropped.
  Renaming a field that has only ever been `False` would launder bad
  data into a new name.
- **Proto evolution, not mutation.** `is_database_node` is added as a
  new field number in `NodeAttributesValue`; the old field numbers are
  only reserved when the fields are removed next cycle. Regenerate with
  `tox -e genprotos`.
- **Schema change follows the established protocol.** The migration
  adds the `is_database_node` column (idempotent, default `False`);
  operators run `sf-ctl ensure-mariadb-schema` before rolling daemons,
  as with every schema change since PLAN-byo-mariadb.
- **CI ordering keeps CI green at every step.** The union-targeting in
  `site.yml` means the actions repo's topologies can flip group names
  before or after the collection change lands; we still sequence them
  (server first, actions second, docs last) so any single revert is
  clean.

## Steps

| # | Repo | Description |
|---|------|-------------|
| 1 | this | **Plan.** This document. |
| 2 | this | **Real database-tier flag.** Add `NODE_IS_DATABASE_NODE` (pydantic config, default `False`); add `is_database_node` to `schema/node_attributes.py`, the `node_attributes` migration in `mariadb.py`, `protos/database.proto` (new field number; `tox -e genprotos`), and the marshalling in `daemons/database/main.py`. `Node.observe_this_node()` writes it from config; `external_view()` emits it alongside the (still always-`False`) legacy flags. Unit tests for the attribute round-trip and the external view. |
| 3 | this | **Collection consumes the flag.** The node role templates `NODE_IS_DATABASE_NODE` into `/etc/sf/config` from `node_is_database_node`. `site.yml`: plays target `database_node:etcd_master`; `node_is_database_node` and `mariadb_gateway_hosts` computed from the union of both groups; a pre-flight warning task fires when `groups['etcd_master']` is non-empty ("renamed to database_node, support for the old name is removed in the next release"). Comments updated. |
| 4 | actions | **CI tooling rename.** `ci-topology-*.yml` `add_host` groups say `database_node` (drop `etcd_master`); `ci-include-common-localhost.yml` computes `is_database_node` from the union of both groups (it feeds facts for released-version topologies too); `tools/ci-make-inventory.py` emits `database_node`. Coordinated actions/main push; shakenfist CI validates via workflow_dispatch before anything else changes. |
| 5 | this | **Tier test keys on the real flag.** `test_database_tier.py` discovers the tier via `is_database_node`. On slim-tier this un-skips the assertions for the first time — expect to fix whatever they find, since they have never run. |
| 6 | this | **Docs and examples rename.** Example inventories and READMEs use `database_node`; `installation.md` documents the new name and the one-release fallback (removing the phase-7 caveat); `ctl.py` docstrings say "database node"; release notes gain the rename + deprecation entry; comment sweep for remaining `etcd_master` mentions outside docs/plans/. |
| 7 | client-python | **Client display.** `commandline/node.py` shows a "database node" role sourced from `is_database_node` (via `.get()` so older servers don't break it), keeps tolerating the legacy keys, and drops the "etcd master" wording. Prepared as a branch; the operator pushes and PRs. |
| 8 | this | **Bookkeeping and review.** Flip the phase 6 and 7 rows in `PLAN-remove-primary.md`'s execution table; final grep gate (below); code review of the whole phase and address findings. |

## Risks

- **Mixed-version upgrade window.** Old daemons neither read nor write
  `is_database_node`; the migration defaults it `False`, and the new
  code only trusts it on nodes whose config was written by the updated
  collection. Nothing gates on the flag at runtime yet (only the tier
  test and operator visibility), so a mixed cluster degrades to
  "the flag is False on stale nodes", which is the pre-phase status quo.
- **Operator inventories using `etcd_master`.** Covered by the union
  targeting and warning for one release; the removal next cycle is a
  documented breaking change in the release notes.
- **The tier test has never run.** Un-skipping assertions that have
  never executed may surface real sf-database-tier bugs on slim-tier.
  That is the point of doing it; budget an iteration loop like phase
  6's CI hardening rather than assuming a single green run.
- **Forgotten `etcd_master` consumers outside these repos.** The grep
  gate covers shakenfist, actions, client-python and kerbside; private
  operator playbooks are covered by the compat window.

## Validation

- `git grep -iE "etcd_master|is_etcd_master" -- ':!docs/plans'` in this
  repo returns only: the one-release compat/deprecation sites (site.yml
  union targeting and warning), the legacy dual-emission in
  `node.py` / schema / proto / mariadb marshalling, and release-note
  history. The same grep in the actions repo returns only the
  include-common union.
- Full functional CI (workflow_dispatch) green, with
  `test_database_tier` REPORTED AS RUN (not skipped) on the slim-tier
  job — check the stestr output explicitly, not just the job colour.
- `sf-client node show <db-node>` displays the database-node role
  against a collection-deployed cluster.
- mkdocs build passes; pre-commit green throughout.

## Deferred to the next release cycle

Recorded here so it is not lost: drop the `is_etcd_master` and
`is_eventlog_node` columns (migration), remove the schema fields, API
keys and client display fallbacks, reserve proto field numbers 5 and 7
(verify numbers at removal time), and remove `site.yml`'s
`etcd_master` union targeting and warning. One small PR, gated on one
released version carrying the deprecations.

## Out of scope

- Phase 8 (reusable smoke-cluster rollout to downstream repos),
  including client-python's broken getsf-era `functional_matrix`.
- Publishing the collection to ansible-galaxy (operator: needs
  `ANSIBLE_GALAXY_TOKEN` and a release tag; until then CI installs from
  checkouts).
- The reusable workflow's colliding `bundle-functional-cluster-smoke`
  artifact name (phase-8-adjacent CI hygiene).
- The node-lifecycle in-guest-kill flake (separate follow-up: kill the
  under-cloud instance from the runner instead).

## Cross-repo / operator actions

- Push actions/main for step 4 (I cannot push that repo).
- Push and PR the client-python branch from step 7.
- After the release containing this phase ships, schedule the deferred
  removal PR above.
