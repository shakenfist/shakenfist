# Phase 6: Repackage the deployer as the `shakenfist.shakenfist` galaxy collection

Parent plan: [PLAN-remove-primary.md](PLAN-remove-primary.md).

Recommended planning effort: **high** (this changes the
operator-facing deployment API).

## Prompt

Before responding, read these files so you understand the
current deployer shape, the collection it becomes, and the
legacy chain it deletes:

- `shakenfist/deploy/ansible/deploy.yml` — the monolithic
  play. Note every `- hosts:` stage, the `role_action`
  dispatch pattern, and the cross-node `set_fact` /
  `hostvars[groups[...]]` computations at the play level
  (the MARIADB gateway list around line 207, the lowest-MTU
  hypervisor around line 210, the admin-namespace condition
  around line 241).
- `shakenfist/deploy/ansible/deploy.py` — the topology-JSON
  → ansible-inventory translator. It emits
  `/etc/sf/ansible-hosts` and `/etc/sf/deploy-vars.json`
  and then shells out to `ansible-playbook`. Dies in this
  phase.
- `shakenfist/deploy/getsf` and `shakenfist/deploy/install`
  — the interactive installer (generates `/root/sf-deploy`)
  and its wrapper. Both die in this phase.
- `shakenfist/deploy/ansible/roles/base/` — becomes the
  core `node` role. Read all of `tasks/` (`bootstrap.yml`,
  `config.yml`, `register.yml`, `create_admin_namespace.yml`,
  `main.yml`), `templates/config` (the capability template),
  `templates/etc_hosts.tmpl`, and `defaults/main.yml`.
- `shakenfist/deploy/ansible/roles/base/templates/config`
  in detail — this is the heart of the refactor. It reads
  `groups['hypervisors']`, `groups['network_node']`,
  `groups['etcd_master']` directly to set
  `SHAKENFIST_NODE_IS_HYPERVISOR` /
  `_IS_NETWORK_NODE` / `_IS_DATABASE_NODE`, and does the
  cross-node lookups `SHAKENFIST_NETWORK_NODE_IP` (line ~26)
  and `SHAKENFIST_MARIADB_GATEWAY_HOSTS` (line ~34). The
  direct-MariaDB block (~lines 41-47) is already wrapped in
  `{% if inventory_hostname in groups['etcd_master'] %}`
  (added by byo-mariadb phase 4).
- `shakenfist/deploy/ansible/roles/primary/` — the role
  that dies. Its three task files scatter: `bootstrap.yml`
  (writes `/etc/sf/inventory.yaml`, computes lowest MTU),
  `cluster_config.yml` (the idempotent `sf-ctl set-config`
  calls — the phase-2 remainder), `postinstall.yml`
  (installs the four ansible modules by copying them, plus
  log rotation and sanity checks).
- `shakenfist/deploy/ansible/roles/network/`,
  `roles/hypervisor/`, `roles/pki_internal_ca/`,
  `roles/database/`, `roles/common/` — the component roles
  and the handlers-only `common` role. `database` is a thin
  "register + start sf-database" role that folds into `node`
  as a capability. `pki_internal_ca` becomes `internal_ca`.
- `shakenfist/config.py` — the target config schema:
  `NODE_IS_HYPERVISOR` / `NODE_IS_NETWORK_NODE` (lines
  ~491-501), `NETWORK_NODE_IP` (~210), `MARIADB_GATEWAY_HOSTS`
  (~361-371), `NODE_NAME` / `NODE_EGRESS_*` / `NODE_MESH_*`
  (~510-536). Note: `NODE_IS_DATABASE_NODE` is environment-only
  (rendered in the template, not a config.py field).
- In the **client-python** sibling repo
  (`/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python`):
  `shakenfist_client/ansible/` (the `sf_instance` /
  `sf_network` / `sf_namespace` bash shims + `sf_snapshot.py`),
  `shakenfist_client/commandline/ansible.py` (the real logic
  the shims shell into), and `commandline/admin.py`'s
  `ansible_module_path` command. These become native modules.
- `shakenfist/deploy/ansible_module_ci/` — the module tests
  (e.g. `004.yml`). They must keep passing when the modules
  move into the collection.
- `.github/workflows/functional-tests.yml` and
  `scheduled-tests.yml` — the CI workflows that deploy via a
  `getsf-wrapper`. The wrapper and the `ci-topology-*.yml`
  files they reference live in the **`shakenfist/actions`
  repo**, not here.
- `.github/workflows/release.yml` — the existing PyPI
  release job. Phase 6 adds a collection build-and-publish
  job alongside it.
- External: Red Hat "Good Practices for Ansible"
  (`redhat-cop.github.io/automation-good-practices`) on
  collection structure and not embedding group names in
  roles; the ansible-galaxy collection layout
  (`galaxy.yml`, `roles/`, `plugins/modules/`, `playbooks/`,
  `meta/runtime.yml`).

One commit per step. Each commit must pass
`pre-commit run --all-files` (which now includes ansible-lint
on the new collection content and actionlint on the workflow
edits).

## Context

After phases 1 and 3 (monitoring + Apache removed) and the
byo-mariadb plan (MariaDB BYO, sf-database as a stateless
tier), and with phase 2 dissolved (config stays in the
idempotent role, `bootstrap-system-key` keeps the system
namespace), the deployer is structurally ready to be
repackaged. What remains is the shape change itself: from a
constellation of classic roles driven by `getsf` →
`deploy.py` → `deploy.yml`, to a versioned
`shakenfist.shakenfist` ansible-galaxy collection that
operators consume from their own playbook.

The principle (from the master plan): SF deploys `sf-*`
daemons on hosts you've told it about, against
infrastructure you've told it the addresses of. The roles
express *what a node runs* as variables; the operator's
playbook maps *their* inventory groups to those variables.
No role reads a group name.

**Strangler-fig sequencing.** The new collection and example
playbooks are built **alongside** the untouched legacy tree.
The legacy `shakenfist/deploy/ansible/` + `getsf` +
`deploy.py` path keeps working — and CI keeps using it —
through steps 1-4, so those steps leave CI green trivially
(they only add new files). Step 5 stands the new path up as
an *additional* CI job and proves it green before anything is
deleted. Step 6 deletes the legacy chain only once the new
path is proven. CI is never dark.

**The hard part** is decoupling the `node` role from
inventory group names. The legacy config template derives
capability flags and several genuinely cross-node values
from `groups[...]`:

- `MARIADB_GATEWAY_HOSTS` = mesh IPs of *all* database nodes,
- `NETWORK_NODE_IP` = the network node's mesh IP,
- `max_hypervisor_mtu` = min `node_mtu` across *all*
  hypervisors,
- `/etc/hosts` and the legacy `/etc/sf/inventory.yaml` loop
  over every group.

In the collection, the role reads these only as variables.
The **example playbook** (which is allowed to read `groups`)
performs the group→variable mapping and the cross-node
reductions, then invokes the roles per host. This is the
same division Kubespray and ceph-ansible use.

## Decisions (phase-local)

The three master-plan open questions for this phase are now
resolved:

1. **Publishing channel: ansible-galaxy proper.** The
   collection is built and published to ansible-galaxy via a
   new job in `release.yml` (decision: open question 1).
   Operators consume it with
   `ansible-galaxy collection install shakenfist.shakenfist`.
   Consequence: the `shakenfist` galaxy namespace must be
   claimed and an `ANSIBLE_GALAXY_TOKEN` secret configured —
   **operator-handled prerequisites**, flagged in step 5's
   brief, not something a sub-agent can do.

2. **Dev/test convenience: a runnable `examples/single-node/`
   playbook** (decision: open question 2), with
   `examples/cluster/` alongside it for the multi-node shape.
   The operator-guide quickstart points at
   `examples/single-node/`. These double as the new CI deploy
   targets.

3. **CI cutover is planned here; the actions-repo edits are
   the operator's manual step** (decision: open question 4).
   Step 5 specifies exactly what the `shakenfist/actions`
   repo needs (replace the `getsf-wrapper` invocation with an
   `ansible-galaxy collection install` + `ansible-playbook
   examples/...` invocation, and replace the `ci-topology-*`
   topology JSON with a plain ansible inventory), but flags
   those edits as the operator's job since sub-agents in this
   repo cannot touch the actions repo.

Further phase-local decisions:

4. **Collection lives at `shakenfist/deploy/collection/`**
   (root holding `galaxy.yml`, `meta/runtime.yml`, `roles/`,
   `plugins/modules/`, `README.md`). It is deliberately
   separate from the dying `shakenfist/deploy/ansible/` tree,
   so the legacy path stays untouched and green until step 6.
   Examples live at repo-root `examples/single-node/` and
   `examples/cluster/` (the `examples/` dir already exists).

5. **Collection name `shakenfist.shakenfist`; version synced
   to the SF version.** The `galaxy.yml` `version` is injected
   at build time from the setuptools_scm SF version (same
   source as `util_general.get_version()`), so the collection
   and server never drift. The collection declares a
   compatible `shakenfist_client` version range as a Python
   requirement (the modules' SDK).

6. **`database` is a capability flag, not a role.** The
   `node` role registers + starts `sf-database` when
   `node_is_database_node` is true. The `database` role and
   its `bootstrap.yml` are deleted (folded into `node`).

7. **`common` handlers fold into the collection.** The three
   handlers (reload systemd, restart journald/logind) become
   handlers within the `node` role (and any component role
   that needs them), or a tiny shared `_common` role the
   others depend on via `meta/main.yml`. The sub-agent picks
   whichever is cleaner; no separately-published role.

8. **Native ansible modules call `shakenfist_client` as an
   SDK.** The four modules become native `AnsibleModule`s
   under `plugins/modules/`, importing the client library
   rather than shelling to `sf-client`. The arg→API mapping
   logic in client-python's `commandline/ansible.py` is the
   reference; the modules either import a reusable helper from
   `shakenfist_client` or reimplement the thin mapping
   against the client's API class. The `sf-client ansible`
   subcommand, the bash shims, and `postinstall.yml`'s
   copy-into-`/usr/share/ansible` dance are retired. The
   control node needs only `pip install shakenfist_client`
   plus the collection — never the server package.

9. **`/etc/sf/inventory.yaml` is dropped (resolved).** The
   step-2 investigation found one consumer: not any shakenfist
   or client-python *code*, but the downstream CI workflows,
   which scp the file off the (now-removed) primary node and
   use it as the inventory for a post-test log-gather step.
   That is a drifted copy-paste-CI convenience, not a real
   dependency, so step 3 does **not** write it. Log-gathering
   should use the same inventory the deploy ran from (the
   example inventory already enumerates every host); folding
   that into a reusable CI workflow is **phase 8** of the
   master plan. The `node` role therefore owns no
   cluster-topology file.

10. **Greenfields only.** No `topology.json` → inventory
    shim (master-plan open question 5 / phase 7). The
    examples document the inventory shape; operators with old
    `topology.json` files rewrite by hand. The `etcd_master`
    → `database_node` ansible group rename is **phase 7**;
    this phase keeps the legacy `etcd_master` group name
    wherever the example playbook still references a group,
    but the *roles* use `node_is_database_node` and never a
    group name.

## Canonical role variables

Every role reads only these (set by the example playbook).
Per-node identity: `node_name`, `node_egress_ip`,
`node_egress_nic`, `node_mesh_ip`, `node_mesh_nic`.
Capability flags: `node_is_hypervisor`,
`node_is_network_node`, `node_is_database_node`.
Cluster-wide (same value on every host):
`network_node_ip`, `mariadb_gateway_hosts` (list),
`max_hypervisor_mtu`, `all_mesh_hosts` (name→mesh-IP map for
`/etc/hosts`). Direct-MariaDB (rendered only when
`node_is_database_node`): `mariadb_host`, `mariadb_port`,
`mariadb_user`, `mariadb_password`, `mariadb_database`.
Cluster-config seeds applied by the playbook's idempotent
config play: `auth_secret_seed`, `ram_system_reservation`,
`dns_server`, `http_proxy`, `extra_config`,
`floating_ip_block`, `system_key` (+ any others the legacy
`cluster_config.yml` set).

## Steps

Six sequential steps, one commit each. Steps 1-4 only add
files and leave the legacy path (and CI) untouched. Step 5
adds the new CI path beside the old. Step 6 deletes the old.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | worktree | **Collection skeleton + component roles.** Create the collection at `shakenfist/deploy/collection/`: `galaxy.yml` (`namespace: shakenfist`, `name: shakenfist`, a placeholder `version: 0.0.0` with a comment that the build job injects the real version, `dependencies: {}`, authors/license/tags), `meta/runtime.yml` (`requires_ansible: '>=2.15'`), and a short `README.md` describing the collection and its roles. Port three component roles into `collection/roles/`: `hypervisor` (from `roles/hypervisor/` — nested KVM, KSM, vhost_vsock, SPICE TLS, libvirt apparmor/config; gated by `node_is_hypervisor` at the playbook level so the role body needs no group reads), `network` (from `roles/network/` — dnsmasq removal, dhcp/dns templates, IP forwarding, and `validate_mtus` which computes `node_mtu` locally via `ip link` — keep that, it's group-free), and `internal_ca` (from `roles/pki_internal_ca/` — CA generation, per-host cert, cert distribution). Fold the `common` handlers in per decision 7. Do NOT touch `shakenfist/deploy/ansible/`. Grep each ported role for `groups[` / `hostvars[` and confirm zero remain (these three are expected to be clean; if any survive, convert to a documented role variable and note it). Add a `collection/roles/<role>/meta/argument_specs.yml` documenting each role's variables. `pre-commit run --all-files` (ansible-lint will check the new roles). Worktree: this lays the structural foundation everything else builds on. |
| 2 | high | opus | worktree | **The core `node` role.** Port `roles/base/` → `collection/roles/node/`, folding in the `database` role, and strip every group-name read. Tasks: `bootstrap.yml` (packages, venv, systemd enable — group-free, port near-verbatim), `config.yml` (writes `/etc/sf/config`, `sfrc`, `shakenfist.json`, systemd units incl. `sf-database.service` gated on `node_is_database_node` instead of `inventory_hostname in groups['etcd_master']`), `register.yml` (`initialise-node`, `register-daemon`, daemon start ordering — replace the `delegate_to: groups['etcd_master'][0]` with the playbook delegating; the role itself runs the local `sf-ctl` registration), `create_admin_namespace.yml` (the idempotent `bootstrap-system-key system {{ system_key }}` — keep as-is). Rewrite `templates/config`: replace the three capability conditionals with `{% if node_is_hypervisor %}` / `{% if node_is_network_node %}` / `{% if node_is_database_node %}`; replace `SHAKENFIST_NETWORK_NODE_IP="{{ hostvars[groups['network_node'][0]]['node_mesh_ip'] }}"` with `="{{ network_node_ip }}"`; replace the `MARIADB_GATEWAY_HOSTS` `groups['etcd_master'] | map('extract', ...)` expression with `="{{ mariadb_gateway_hosts | join(',') }}"`; wrap the direct-MariaDB block in `{% if node_is_database_node %}`. Rewrite `templates/etc_hosts.tmpl` to loop over `all_mesh_hosts` (a name→mesh-IP map) instead of `groups.allsf`, and drop or repoint the `sf-primary` alias (the primary node is gone — decide with a comment). Investigate decision 9: grep the whole repo (server + client-python) for `/etc/sf/inventory.yaml` to determine whether anything reads it at runtime; if nothing does, it dies with the `primary` role (note this in the commit message); if something does, add an `inventory.yaml` template to the `node` role fed by `all_mesh_hosts`. Add `meta/argument_specs.yml` for all `node` variables. The systemd unit files (`sf.target`, `sf.service`, `sf-api.service`, `sfrc`, `shakenfist.json` from `roles/base/templates/` and `deploy/ansible/files/`) come along into the role. Do NOT touch `shakenfist/deploy/ansible/`. Grep the finished role for `groups[`/`hostvars[` → must be zero. `pre-commit run --all-files`. Worktree: one wrong Jinja conditional malforms `/etc/sf/config` on every node; validate the template by rendering it through Jinja2 with a fake var set for hypervisor / network / database / plain nodes before declaring done. |
| 3 | high | opus | worktree | **Example consumer playbooks.** Create `examples/single-node/` and `examples/cluster/`, each with an `inventory.yaml` (or `.ini`) and a `site.yml`. The playbooks are where `groups` is read. `site.yml` structure: (a) a `localhost`/play-level fact stage that, from inventory group membership, computes per-host capability flags (`node_is_hypervisor` = host in `hypervisors`, etc.) and the cluster-wide reductions — `network_node_ip` = mesh IP of the `network_node` member, `mariadb_gateway_hosts` = list of mesh IPs of all `database`-group members, `max_hypervisor_mtu` = `min` of `node_mtu` across hypervisors (run the `network` role's `validate_mtus` first to populate `node_mtu`), `all_mesh_hosts` = map of every host's name→`node_mesh_ip`, and a `ram_system_reservation` default computed per host via `set_fact` from `ansible_memtotal_mb` (e.g. `max(2048, memtotal*0.1)`); (b) per-host plays invoking `shakenfist.shakenfist.node` (+ `.hypervisor` / `.network` / `.internal_ca` guarded by the flags) with those variables; (c) an idempotent **cluster-config play** (the phase-2 remainder) delegated to a database-tier host that runs `sf-ctl set-config` for `AUTH_SECRET_SEED` (caller-supplied), `RAM_SYSTEM_RESERVATION`, `MAX_HYPERVISOR_MTU`, `DNS_SERVER`, `HTTP_PROXY`, `extra_config`, and `sf-ctl bootstrap-system-key system {{ system_key }}` — replacing the dead `roles/primary/cluster_config.yml` and `create_admin_namespace`; (d) the sanity checks from `roles/primary/postinstall.yml` (sf-api/sf-queues active, API 401) as a final verify play. The `single-node` example assigns `localhost` every capability (all flags true, `mariadb_gateway_hosts: [<localhost mesh ip>]`) and documents the byo-mariadb single-box prerequisite (`apt install mariadb-server` + `tools/bootstrap-mariadb.sql` + `sf-ctl ensure-mariadb-schema`). Add a `README.md` in each example dir. Do NOT touch `shakenfist/deploy/ansible/`. `pre-commit run --all-files`. Worktree: the cross-node reductions are the subtlest code in the phase; an off-by-one in the gateway list or MTU min silently misconfigures the cluster. |
| 4 | high | opus | worktree | **Native ansible modules into the collection** (touches the client-python repo — flag for the operator). Create `collection/plugins/modules/sf_namespace.py`, `sf_network.py`, `sf_instance.py`, `sf_snapshot.py` as native `AnsibleModule`s. Use client-python's `shakenfist_client/commandline/ansible.py` as the behaviour reference (it already maps ansible JSON params → API calls for each resource). The modules import the `shakenfist_client` API client (the same class the CLI uses) and perform the create/delete/query with proper `changed`/`check_mode`/`exit_json`/`fail_json` semantics and a documented `argument_spec` + `DOCUMENTATION`/`EXAMPLES`/`RETURN`. If the cleanest factoring is a shared helper, add it to `shakenfist_client` (a client-python change) and import it; otherwise reimplement the thin mapping in the module. Update `shakenfist/deploy/ansible_module_ci/` playbooks (e.g. `004.yml`) to reference the modules by FQCN `shakenfist.shakenfist.sf_*` and to install the collection first; confirm the module CI still exercises namespace/network/instance lifecycle. Add the `shakenfist_client` version requirement to the collection (a `requirements.txt` or documented constraint). Because this spans two repos, the sub-agent must clearly separate its client-python edits (the operator commits those in the client-python repo) from its in-collection edits, and the brief-runner must coordinate the client-python release. Do NOT delete the old shims / `sf-client ansible` subcommand yet (that retirement lands with the client-python change + step 6's docs). `pre-commit run --all-files`. Worktree. |
| 5 | high | opus | worktree | **Cut CI over to the reusable smoke-cluster workflow + add the release job.** Per the decision to write *no throwaway CI*, the new deploy path is the final reusable workflow from phase 8, pulled forward here — not a getsf-shaped intermediate. (a) Author `smoke-cluster.yml` (`on: workflow_call`) in the **`shakenfist/actions`** repo, parameterised by component, ref/wheel, and tier (smoke vs full): it installs the collection (`ansible-galaxy collection install`), deploys via `examples/single-node` (smoke) or `examples/cluster` (full), installs the component-under-test's wheel through the collection's `server_package` / `client_package` / `pip_extra` overrides, runs the `shakenfist_ci` suite at the requested tier, and gathers logs using the **example inventory** the deploy ran from (no `/etc/sf/inventory.yaml`). (b) Wire shakenfist's own `functional-tests.yml` / `scheduled-tests.yml` to **call** that reusable workflow as the new deploy path, kept beside the existing getsf job only until it is green — a cutover, not a parallel throwaway. (c) Add a `build-collection` job to `.github/workflows/release.yml` alongside the PyPI job: inject the SF version into `galaxy.yml` (`sed`/yq from the setuptools_scm version), `ansible-galaxy collection build shakenfist/deploy/collection`, `ansible-galaxy collection publish` with `${{ secrets.ANSIBLE_GALAXY_TOKEN }}`. **Cross-repo / operator-pushed:** the `shakenfist/actions` workflow and this repo's CI-wiring changes are prepared as diffs the operator reviews and **pushes** (sub-agents cannot push the actions repo); the galaxy `shakenfist` namespace and the `ANSIBLE_GALAXY_TOKEN` secret are operator prerequisites (namespace requested 2026-06-25 via the Ansible forum, pending provisioning — the publish step cannot be validated until it lands). Keep workflow scripts >5 lines in `tools/` per repo convention. `pre-commit run --all-files` (actionlint). Worktree. |
| 6 | high | opus | none | **Delete the legacy chain + documentation.** Once step 5's new CI job is confirmed green (operator verifies), `git rm` the legacy tree: `shakenfist/deploy/getsf`, `shakenfist/deploy/install`, `shakenfist/deploy/ansible/deploy.py`, `shakenfist/deploy/ansible/deploy.yml`, and the entire `shakenfist/deploy/ansible/roles/` (base, primary, network, hypervisor, pki_internal_ca, database, common) and `shakenfist/deploy/ansible/files/` + `tasks/` now superseded by the collection. Remove the now-dead `getsf` job from both workflows (leaving only the collection job). Retire the `sf-client ansible` subcommand and bash shims in client-python (coordinate the client-python commit). Documentation: rewrite `docs/operator_guide/installation.md` for the collection workflow (`ansible-galaxy collection install shakenfist.shakenfist`, write a playbook, point at `examples/`); add a `docs/operator_guide/` "deploying SF against your own infrastructure" section; update `ARCHITECTURE.md` (drop primary-node + deploy.py/getsf references), `README.md`, and `AGENTS.md` to the new deployment shape; ensure the quickstart points at `examples/single-node/`. Grep the whole repo for `getsf`, `deploy.py`, `deploy.yml`, `roles/primary`, `topology.json`, `/root/sf-deploy`, `sf-primary` and confirm only intentional historical references (e.g. plan docs) remain. `pre-commit run --all-files`. The plan's documentation index (`index.md`) status rows for phase 6 flip to complete. |

## Validation

- `pre-commit run --all-files` passes after every step
  (ansible-lint on the collection, actionlint on workflows).
- After step 1: `ansible-galaxy collection build
  shakenfist/deploy/collection` succeeds; the three
  component roles contain no `groups[` / `hostvars[`.
- After step 2: rendering `collection/roles/node/templates/
  config` through Jinja2 with fake var sets for a hypervisor
  node, a network node, a database node, and a plain node
  each produces a well-formed `/etc/sf/config`; the role has
  zero `groups[` / `hostvars[`; the `/etc/sf/inventory.yaml`
  investigation is recorded in the commit message.
- After step 3: `ansible-playbook --syntax-check` passes on
  both example `site.yml`s; a dry inventory exercises the
  cross-node reductions correctly (manually eyeball the
  computed `mariadb_gateway_hosts`, `network_node_ip`,
  `max_hypervisor_mtu` for a 3-node inventory).
- After step 4: `ansible_module_ci` passes using the FQCN
  modules; no module shells out to `sf-client`.
- After step 5: the **new** collection-based CI job is green
  (the old `getsf` job is still present and green too); the
  `release.yml` collection job builds the tarball (publish is
  gated on the token/namespace prerequisites). The operator
  has the explicit actions-repo + secret to-do list.
- After [step 6](PLAN-remove-primary-phase-06-step6-legacy-removal.md): `grep -rn 'getsf\|deploy\.py\|deploy\.yml\|
  roles/primary' shakenfist/ .github/` returns only
  intentional historical hits; the legacy CI job is gone; an
  operator reading `docs/operator_guide/installation.md`
  alone can deploy via the collection.

## Risks

- **Cross-node reductions in the example playbook (step 3).**
  `mariadb_gateway_hosts`, `network_node_ip`, and
  `max_hypervisor_mtu` are computed from group membership at
  the playbook layer. A wrong reduction silently
  misconfigures every node (e.g. an incomplete gateway list
  → clients can't reach the DB tier). Mitigation: the brief
  requires eyeballing the computed values for a multi-node
  inventory before relying on CI.
- **`/etc/sf/inventory.yaml` consumer (decision 9 — resolved).**
  The step-2 grep found only a downstream-CI log-gather
  consumer, not a code dependency, so the file is dropped
  (decision 9) and the log-gather inventory becomes a phase-8
  concern. No remaining risk for this phase.
- **The `node` role config template (step 2).** A single
  bad Jinja conditional malforms `/etc/sf/config` and every
  daemon fails to start. Mitigation: render the template
  through Jinja2 with all four node-shape fixtures before
  declaring done.
- **Cross-repo coordination.** Steps 4 (client-python) and 5
  (actions repo) span repos a sub-agent can't fully touch.
  Mitigation: the briefs force the sub-agent to emit an
  explicit, separated to-do list for the other repos; the
  management session tracks those as operator actions and
  does not mark the phase complete until they land.
- **CI cutover ordering.** If step 6 deletes the legacy path
  before the actions-repo new-path plumbing exists, CI goes
  dark. Mitigation: step 5 stands the new job up *beside*
  the old and step 6 is gated on the operator confirming the
  new job (and the actions-repo edits) are green.
- **Galaxy publish prerequisites.** The release job fails if
  the `shakenfist` namespace or `ANSIBLE_GALAXY_TOKEN`
  secret is missing. Mitigation: step 5 lists these as
  operator prerequisites; the publish step is the last to be
  exercised and does not block the build.

## Out of scope

- `etcd_master` → `database_node` ansible group rename —
  **phase 7**. This phase keeps the legacy group name where
  the example playbook references a group, but no role reads
  a group name.
- `topology.json` → inventory migration shim — greenfields
  only (master-plan open question 5 / phase 7).
- The broader BYO-MariaDB deploy surface (the `mariadb`
  role, `tools/bootstrap-mariadb.sql`) — owned by
  PLAN-byo-mariadb; this phase consumes it in the
  single-node example.
- Removing `shakenfist/etcd.py` drain code — PLAN-remove-etcd.

## Back brief

Before executing this phase, please back brief the operator
on:

- The six steps and the strangler-fig ordering (1-4 add the
  collection + examples + modules without touching the
  legacy path; 5 stands the new CI job up beside the old; 6
  deletes the legacy chain only once the new path is proven).
- The two cross-repo dependencies: the client-python module
  refactor/retirement (steps 4 and 6) and the
  `shakenfist/actions` CI cutover + galaxy namespace/token
  prerequisites (step 5) — confirm the operator will execute
  those out-of-repo actions at the right point.
- The three resolved open questions: galaxy-proper
  publishing (new `release.yml` job), the runnable
  `examples/single-node/` playbook, and the planned CI
  cutover with operator-executed actions-repo edits.
- The decision to fold `database` into `node` as a
  capability flag and to keep the `etcd_master` group name
  (for the example playbook only) until phase 7.
- The `/etc/sf/inventory.yaml` investigation (step 2) and
  what happens to it.
