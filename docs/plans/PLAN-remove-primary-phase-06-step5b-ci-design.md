# Phase 6 step 5b: reusable smoke-cluster CI workflow — design

Parent plan: [PLAN-remove-primary.md](PLAN-remove-primary.md).
Phase plan: [PLAN-remove-primary-phase-06-galaxy-role.md](PLAN-remove-primary-phase-06-galaxy-role.md).

This is a **design document** for step 5b (and the foundation phase 8
builds on). It is written before any code because the work spans the
`shakenfist/actions` repo (which only the operator pushes) and can only
be proven green by a real CI run. Step 5a (the `release.yml` collection
build/publish jobs) is already done and committed; this covers the
other half of step 5.

## Goal

Replace the `getsf`-based CI deploy with a deploy that drives the new
`shakenfist.shakenfist` collection + `examples/` playbooks, and package
that deploy as **one reusable GitHub Actions workflow** in
`shakenfist/actions` that shakenfist (now) and the downstream repos
(phase 8) call with a few-line `uses:`. No throwaway intermediate: the
workflow authored here is the final reusable one.

## Current deploy flow (what we are replacing)

Traced from `shakenfist/.github/workflows/functional-tests.yml`
(the `functional_matrix_pr` "Smoke tests" job, topology `localhost`)
and the `shakenfist/actions` repo:

1. **`setup-test-environment@main`** (composite action, actions repo) —
   checks out `shakenfist`, `client-python`, `agent-python`, `actions`
   into the under-cloud worker; sets `SHAKENFIST_NAMESPACE=$(hostname)`.
2. **Build infrastructure** — runs
   `ansible/ci-topology-localhost.yml` against the under-cloud SF. This:
   - provisions the under-cloud VM(s) via the `sf_instance` / `sf_network`
     modules (the "Smoke" topology is a single 12-CPU/16G box at
     `10.0.0.10`);
   - `add_host`s them into ansible with group membership
     `allsf,etcd_master,primary_node,network_node,hypervisors`;
   - via `ci-include-common-localhost.yml`: builds the server, client and
     agent **wheels** under-cloud, writes `ci-environment.sh` (per-node
     IP/uuid env), and copies the wheels onto the node;
   - writes **`/tmp/getsf-wrapper`** onto the primary — a generated
     script that exports `GETSF_*` env (nodes, wheels, floating block,
     DNS, extra config) and runs `getsf`.
3. **Install BYO MariaDB / Loki on primary** — scp + run
   `shakenfist/tools/ci-install-mariadb.sh` and `ci-install-loki.sh`.
4. **Run getsf installer on primary** — ssh to the node and run
   `/tmp/getsf-wrapper` with `GETSF_MARIADB_*` / `GETSF_LOKI_BASE_URL`.
   getsf deploys SF onto `GETSF_NODES` (just `localhost` for smoke).
5. **Wait schedulable / import images / run `stestr`** — the
   `smoke-ci.conf` suite, driven on the node.
6. **Checks** — `ci_drain_check.sh`, `ci_log_checks_loki.sh`,
   exception scan, CPU-hog check.
7. **Log gather** — scp **`/etc/sf/inventory.yaml`** off the primary,
   `sed` it (`/root/.ssh`→`/home/debian/.ssh`, `localhost`→`primary`),
   then run `ci-node-checks.yml` and `ci-gather-logs-loki.yml` from the
   worker against that fetched inventory.

The two things this design removes: the **getsf-wrapper** deploy
mechanism (step 4) and the **`/etc/sf/inventory.yaml`** fetch (step 7,
decision 9 — the file no longer exists once the primary role dies).

## New deploy flow (collection + examples)

Steps 1-4 of this phase already built the replacement deploy interface,
and it is built around a **separate ansible controller** — exactly the
"build the wheels once, ship them everywhere" model:

- `examples/_shared/site.yml` **play 0** runs on `hosts: localhost`
  `connection: local` (the controller) and, gated on
  `sf_build_local_wheels: true`, builds the server and client wheels
  from `repo_path` / `client_repo_path` **once**. **Play 1** runs on
  `hosts: allsf`, copies those wheels to every node's `/tmp`, and
  overrides `server_package` / `client_package` to the per-node wheel
  path. The file's own comment says this "is what the step-5 CI path
  uses to deploy." So the build is controller-side and single; nodes
  only receive the artifact.
- `examples/single-node/inventory.yaml` is the *operator-facing* static
  inventory (one box, `connection: local`, controller == node). CI's
  node IPs are dynamic, so the reusable workflow **generates** a
  topology-matched inventory (real egress/mesh IPs, SSH connection,
  group membership) that imports the same `_shared/site.yml`. The
  committed `examples/single-node` and `examples/cluster` inventories
  remain the documentation and the `--syntax-check` targets; CI's
  generated inventory is their dynamic-IP equivalent.
- Cluster-config seeds (`auth_secret`, `system_key`, `mariadb_*`,
  `dns_server`, `floating_network_ipblock`, …) come from the example
  `group_vars/all.yml`, overridable with `--extra-vars`.

So the new deploy runs **from the controller (the CI runner)** against
the generated inventory:

```
ansible-galaxy collection install dist-collection/*.tar.gz   # the 5a tarball
ansible-playbook -i <generated-ci-inventory> \
    examples/_shared/site.yml \
    --extra-vars "sf_build_local_wheels=true \
                  repo_path=$GITHUB_WORKSPACE/shakenfist \
                  client_repo_path=$GITHUB_WORKSPACE/client-python \
                  mariadb_password=citestpw \
                  auth_secret=<ci-seed> system_key=<ci-key> \
                  mariadb_database=shakenfist dns_server=8.8.8.8 \
                  floating_network_ipblock=192.168.230.0/24"
```

Play 0 builds both wheels once on the runner; play 1 ships them to every
node in the generated inventory. The collection install source is the
**built tarball** from step 5a (`tools/build-collection.py` →
`dist-collection/*.tar.gz`), so CI exercises the real published
artifact, not the loose tree.

### Component-under-test install (generalises to phase 8)

The `server_package` / `client_package` / `pip_extra` override is the
single hook for "install the thing this CI run is testing":

| Caller (component) | server_package | client_package |
|--------------------|----------------|----------------|
| `shakenfist`       | local wheel (this PR) | local wheel or released |
| `client-python`    | released / pinned | local wheel (this PR) |
| `library-python`   | released | released (+ library wheel via `pip_extra`) |
| `kerbside`         | released | released (+ kerbside wheel via `pip_extra`) |

For shakenfist's own CI: `sf_build_local_wheels=true` with both repo
paths (build both from the checked-out PRs). For a downstream repo:
point `server_package` at a released/pinned shakenfist and use
`pip_extra` (or its own repo_path) to inject the component-under-test.

### Log gather without `/etc/sf/inventory.yaml`

Drop the fetch-and-`sed` step. **The generated inventory the deploy ran
from is the topology** — feed that same file to `ci-node-checks.yml` and
`ci-gather-logs-loki.yml`. It already enumerates every node with real
IPs and SSH connection, which is exactly what the scp'd
`/etc/sf/inventory.yaml` used to provide. The Loki dump still targets the
primary's `localhost:3100` (the gather playbook's localhost play), so no
per-node Loki endpoint rewriting is needed.

## The reusable workflow contract

`shakenfist/actions/.github/workflows/smoke-cluster.yml`, `on:
workflow_call`:

```yaml
on:
  workflow_call:
    inputs:
      component:        { type: string, required: true }   # repo under test
      component_ref:    { type: string, required: true }   # its SHA/ref
      tier:             { type: string, default: smoke }    # smoke | full
      base_image:       { type: string, default: 'sf://label/ci-images/debian-12' }
      base_image_user:  { type: string, default: debian }
      stestr_config:    { type: string, default: smoke-ci.conf }
      server_package:   { type: string, default: '' }       # '' => build local
      client_package:   { type: string, default: '' }
    secrets:
      # inherited via `secrets: inherit` from the caller
```

The job body is the current `functional_matrix_pr` job with step 4
(getsf) swapped for the collection deploy above and step 7's inventory
fetch swapped for the generated inventory. Per repo convention, any
deploy glue >5 lines goes in `shakenfist/actions/tools/` and is called
from the step:

- `tools/ci-make-inventory.py` — turn the topology's per-node facts
  (the `*_egress_ip` / `*_mesh_ip` data already in `ci-environment.sh`,
  plus the tier) into an ansible inventory importing `_shared/site.yml`.
- `tools/deploy-collection.sh` — `ansible-galaxy collection install` the
  5a tarball and run `ansible-playbook _shared/site.yml` with the
  `--extra-vars` above.

Caller side (shakenfist's `functional-tests.yml`), once green:

```yaml
  smoke:
    uses: shakenfist/actions/.github/workflows/smoke-cluster.yml@main
    secrets: inherit
    with:
      component: shakenfist
      component_ref: ${{ github.sha }}
      tier: smoke
```

## What moves where

| Concern | Today | After 5b |
|---------|-------|----------|
| Provision under-cloud VM(s) | `ci-topology-localhost.yml` (actions) | **unchanged** — still needed to get a VM |
| Build wheels | under-cloud, in `ci-include-common-localhost.yml` | **once on the controller (runner)** via `_shared` play 0; play 1 ships them to all nodes |
| Deploy SF | `getsf-wrapper` + getsf + legacy ansible | `ansible-galaxy collection install` + `ansible-playbook examples/_shared/site.yml` from the runner against the generated inventory |
| `getsf-wrapper` | written by topology | **deleted** |
| Cluster topology for log-gather | scp `/etc/sf/inventory.yaml` off primary | the **generated CI inventory** the deploy ran from |
| Test + check steps | functional-tests.yml | **moved into** smoke-cluster.yml (parameterised) |

The under-cloud topology playbook keeps its non-getsf work (apt proxy,
pip mirror, mount `/srv/ci`, disable logrotate/unattended-upgrades). A
follow-up can slim it to stop writing `getsf-wrapper` and stop building
wheels under-cloud once the on-node build is proven.

## Tiers

- **smoke (single-node)** — the MVP and what shakenfist PR CI + phase 8
  downstream need. The generated inventory has the one provisioned VM in
  every group (`allsf`, `hypervisors`, `network_node`, `etcd_master`)
  with its real egress IP and SSH connection. Ship this first.
- **full (cluster)** — several nodes with distinct capability-group
  membership, generated from the topology's `add_host` facts (which
  already carry `mesh_ip` / `egress_ip`). Same generator, more hosts;
  the merge-queue `slim-primary` / `slim-tier` replacement. Sketch only
  here; land after smoke is green.

Both tiers use one inventory generator (topology facts → an inventory
importing `_shared/site.yml`); the tier just determines how many hosts
and which groups each lands in.

## Rollout sequence

1. **5b.1** Author `smoke-cluster.yml` (smoke tier) + any
   `tools/deploy-collection.sh` in `shakenfist/actions`. *(operator
   pushes)*
2. **5b.2** Add a `smoke` job to shakenfist's `functional-tests.yml`
   that `uses:` it, **beside** the existing getsf smoke job (cutover,
   not throwaway — both green briefly). *(this repo)*
3. **5b.3** Prove the new job green on a real PR run. *(operator)*
4. **5b.4** Remove the old getsf smoke job. *(this repo; can fold into
   step 6's legacy deletion)*
5. **Phase 8** Point `client-python` / `library-python` / `kerbside` at
   the same reusable workflow. *(operator pushes each)*

The full/cluster tier and the merge-queue jobs migrate after smoke is
proven, before step 6 deletes getsf entirely.

## Risks

- **End-to-end deploy only provable in CI.** The collection install +
  on-node wheel build + `examples/single-node` deploy chain cannot be
  fully validated locally (needs a provisioned VM + BYO MariaDB + Loki).
  Mitigation: ship smoke-cluster.yml beside the getsf job and gate
  removal on a green run; `ansible-playbook --syntax-check` and a local
  Jinja render are the most we can pre-verify.
- **Cross-repo push latency.** smoke-cluster.yml lands in actions before
  the caller can `uses:` it, and `@main` means the caller picks up
  actions changes immediately. Sequence: push actions first, then the
  caller wiring. Mitigation: keep the old getsf job until the new one is
  green so CI is never dark.
- **Controller-side wheel build.** Wheels build once on the runner
  (`_shared` play 0) and ship to every node (play 1), matching the
  "build once, ship everywhere" model. The runner must therefore have
  the component repos checked out (it already does, via
  `setup-test-environment`) and a working `python3 -m build`
  toolchain.
- **Secret propagation.** The reusable workflow needs the same proxy /
  devpi / SSH-key environment the inline job has. `secrets: inherit`
  plus the workflow's own `env:` block must reproduce the
  `http_proxy` / `PIP_INDEX_URL` / `/srv/github/id_ci` setup.

## Validation

- `actionlint` on `smoke-cluster.yml` and the caller edit.
- `ansible-playbook --syntax-check` on `examples/single-node/site.yml`
  with the collection installed (already part of step 3 validation).
- A real PR CI run shows the new `smoke` job green with the getsf job
  still present and green.

## Operator (push) actions

- Push `smoke-cluster.yml` (+ `tools/deploy-collection.sh`) to
  `shakenfist/actions` `main`.
- Set the `ANSIBLE_GALAXY_TOKEN` repo secret (for step 5a's publish).
- Confirm the new `smoke` job green before the getsf job is removed.
