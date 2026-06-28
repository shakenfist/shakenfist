# Phase 6 step 6: retire getsf and delete the legacy chain

Parent plan: [PLAN-remove-primary.md](PLAN-remove-primary.md).
Phase plan: [PLAN-remove-primary-phase-06-galaxy-role.md](PLAN-remove-primary-phase-06-galaxy-role.md).
Precursor: [PLAN-remove-primary-phase-06-step5b-ci-design.md](PLAN-remove-primary-phase-06-step5b-ci-design.md).

Recommended planning effort: **high** (deletes the operator-facing
installer and spans three repos).

## Context

Step 5b proved the collection deploy on the **smoke (single-node)**
tier: `smoke_collection` is green beside the getsf `functional_matrix_pr`
job. But the original step-6 brief ("delete the legacy chain") assumed
the collection covered everything getsf does. It does not yet. getsf
still powers, on `shakenfist/shakenfist`:

- `functional-tests.yml` → `functional_matrix_merge` — **merge-queue
  gating**, topologies `slim-primary` (×3 test groups) and `slim-tier`
  (multi-node, separate db tier).
- `functional-tests.yml` → `ansible_modules` — `slim-primary`, runs
  `ansiblemoduletests.sh` (the native-module lifecycle CI).
- `scheduled-tests.yml` → `functional_matrix` — nightly `localhost` +
  `slim-primary` (across base images).

The collection reusable workflow's `full` tier is currently
`NotImplementedError` (`ci-make-inventory.py`). **getsf cannot be
deleted until the collection covers these multi-node + module jobs**,
or merge-queue and module coverage goes dark. So this phase is
sequenced: build full coverage, prove it green beside getsf, *then*
delete. CI is never dark, mirroring the strangler-fig discipline of the
rest of phase 6.

Confirmed during planning:

- The `ci-topology-*-released.yml` / `*-upgrade.yml` files exist but are
  **not referenced** by any active workflow, so upgrade-from-released is
  out of scope — no need to replicate it on the collection path.
- The reusable workflow already **reuses** `ci-topology-localhost.yml` /
  `slim-primary.yml` / `slim-tier.yml` to provision the under-cloud VMs.
  Those files stay; only the now-dead `getsf-wrapper`-writing task inside
  them is trimmed in the delete step.
- `slim-tier` is primary(`etcd_master`,`network_node`) + two
  hypervisors. The db-tier node count per topology must be confirmed
  against the live topology files when implementing the reduction (the
  byo-mariadb LB-fanout test may run a 2-db-node tier).

## Decisions (phase-local)

1. **Full coverage precedes deletion (hard gate).** Steps 1-3 stand the
   collection up across every getsf topology and prove green *beside*
   getsf. Step 4 deletes only after that. No partial deletion is
   possible — `roles/primary` et al. are shared by every topology, so
   getsf is all-or-nothing.

2. **The reusable workflow is parameterised by topology, not just
   tier.** `smoke-cluster.yml` gains a `topology` input
   (`localhost` | `slim-primary` | `slim-tier`) so one workflow drives
   every job; `tier` (smoke|full) still selects the suite depth. The
   Loki URL becomes the primary's mesh IP for multi-node (127.0.0.1 only
   works single-node).

3. **The cross-node reductions get their first real multi-node
   exercise here.** `mariadb_gateway_hosts`, `network_node_ip`,
   `max_hypervisor_mtu`, `all_mesh_hosts` are computed by
   `examples/_shared/site.yml` but have only ever run against a
   one-node inventory. The full tier is where an off-by-one would bite —
   the plan's stated highest-risk code. The example `cluster` inventory
   and the generated CI inventory must agree on group shape.

4. **client-python shim retirement is a coordinated cross-repo step**
   (step 5), gated on the collection being the *sole* consumer of the
   native modules. The operator pushes client-python; sub-agents prepare
   the diff.

5. **Docs flip with the deletion** (step 6), not before — the README /
   installation guide still describe getsf correctly until it is gone
   (review item 10 confirmed this is intentional).

## Steps

One commit per step (steps spanning the actions/client-python repos are
prepared as operator-pushed diffs). Steps 1-3 only add the new path;
step 4 deletes the old; 5-6 clean up.

| Step | Repos | Brief |
|------|-------|-------|
| 1 | actions + this | **Full/cluster tier of the reusable workflow.** Implement the `full` tier in `tools/ci-make-inventory.py`: take multiple node specs and place each into its capability groups from the topology `add_host` facts (`slim-primary`: primary in db+net+hv, sfN in hv; `slim-tier`: primary in db+net, sfN in hv), emitting the per-host `node_*` vars and SSH connection. Add a `topology` input to `smoke-cluster.yml` and pass the primary's mesh IP as `loki_base_url` for multi-node. Drive the inventory generation from the topology's `ci-environment.sh` (it already exports every node's name→IP). Validate `examples/cluster` deploys via `--syntax-check` and a dry multi-node inventory eyeball of the reductions. |
| 2 | this | **Cut the multi-node CI jobs over.** Add `*_collection` jobs that `uses:` the reusable workflow with `tier: full` + the right `topology` for `functional_matrix_merge` (slim-primary, slim-tier) and scheduled `functional_matrix`, **beside** the getsf jobs (not yet in the merge-queue required checks). Prove each green. This is where the cross-node reductions get their first multi-node proof. |
| 3 | this + actions | **Cut the module CI over.** Point `ansible_modules` at the collection: deploy via the reusable workflow (slim-primary) then run `ansiblemoduletests.sh` (already FQCN-referencing `shakenfist.shakenfist.sf_*`). Confirm the namespace/network/instance/snapshot lifecycle passes against the installed collection. |
| 4 | this + actions | **Delete the legacy chain.** Once 1-3 are green and the operator has flipped the merge-queue required checks to the `*_collection` jobs: `git rm` `shakenfist/deploy/getsf`, `install`, `ansible/deploy.py`, `ansible/deploy.yml`, `ansible/roles/{base,primary,network,hypervisor,pki_internal_ca,database,common}`, `ansible/files/`, `ansible/tasks/`. Remove the getsf `functional_matrix_*` / `ansible_modules` / scheduled `functional_matrix` jobs (leaving only the `*_collection` jobs). In the actions repo, trim the dead `getsf-wrapper`-writing task from `ci-topology-localhost/slim-primary/slim-tier.yml` (provisioning stays). Drop the pre-commit ansible-lint hook's `shakenfist/deploy/ansible/` path (now gone) or repoint it at the collection. |
| 5 | client-python | **Retire the client-python shims.** Remove the `sf-client ansible` subcommand (`commandline/ansible.py`), the four bash shims under `shakenfist_client/ansible/`, and `admin.py`'s `ansible_module_path`. The collection's native modules fully replace them. Prepared as a diff; the operator pushes client-python. Coordinate so client-python's own CI (which builds an SF cluster) has already moved to the reusable workflow (phase 8) or does not depend on the shims. |
| 6 | this | **Documentation.** Rewrite `docs/operator_guide/installation.md` for the collection (`ansible-galaxy collection install shakenfist.shakenfist`, write a playbook, point at `examples/`); add a "deploying against your own infrastructure" section; update `ARCHITECTURE.md` (drop primary-node + deploy.py/getsf), `README.md`, `AGENTS.md`; point the quickstart at `examples/single-node/`. Grep the repo for `getsf`, `deploy.py`, `deploy.yml`, `roles/primary`, `topology.json`, `/root/sf-deploy`, `sf-primary` and confirm only intentional historical (plan-doc) hits remain. Flip the phase-6 `index.md` row to complete. |

## Risks

- **Multi-node cross-node reductions (steps 1-2).** The full tier is the
  first real exercise of `mariadb_gateway_hosts` / `network_node_ip` /
  `max_hypervisor_mtu` / `all_mesh_hosts`. A wrong reduction silently
  misconfigures the cluster (e.g. an incomplete gateway list → clients
  can't reach the db tier). Mitigation: eyeball the computed values for a
  3-node inventory before relying on CI; the slim-tier LB-fanout test is
  a good cross-check that every db node is reached.
- **Merge-queue cutover ordering (step 4).** If the getsf merge jobs are
  removed before the operator flips the required status checks to the
  `*_collection` jobs, the merge queue loses its gate. Mitigation: step 4
  is gated on the operator confirming the new required checks are in
  place (a repo-settings change, tracked like the actions push).
- **Cross-repo deletion (step 5).** Removing the client-python shims
  while any consumer still calls `sf-client ansible` breaks that
  consumer. Mitigation: retire only after the collection is the sole
  consumer; coordinate with phase 8 (downstream repos onto the reusable
  workflow).
- **Stale references after deletion (step 6).** Tooling or docs may
  still reference the deleted paths. Mitigation: the grep gate in step 6.

## Validation

- After steps 1-3: each `*_collection` job (smoke, slim-primary,
  slim-tier, modules) green beside its getsf twin; the dry multi-node
  inventory reductions eyeballed.
- After step 4: `grep -rn 'getsf\|deploy\.py\|deploy\.yml\|roles/primary'
  shakenfist/ .github/` returns only intentional historical hits; the
  getsf jobs are gone; the merge queue gates on the `*_collection` jobs.
- After step 5: client-python has no `sf-client ansible` subcommand and
  its CI is green on the collection path.
- After step 6: an operator can deploy from `installation.md` alone via
  the collection; the grep gate is clean.

## Out of scope

- `etcd_master` → `database_node` ansible group rename — **phase 7**.
- Rolling the reusable workflow out to the downstream repos
  (client-python / library-python / kerbside) — **phase 8** (depends on
  step 5's shim retirement landing).
- Upgrade-from-released CI — the released/upgrade topologies are dormant;
  not replicated here.

## Cross-repo / operator actions

- Push the actions-repo changes (full-tier workflow + topology trims) —
  operator.
- Flip the merge-queue required status checks from the getsf jobs to the
  `*_collection` jobs before step 4 deletes the getsf jobs — operator.
- Push the client-python shim retirement (step 5) — operator.
