# Phase 6: N>1 sf-database functional CI shape

Parent plan: [PLAN-byo-mariadb.md](PLAN-byo-mariadb.md).

## Prompt

Before responding, read these files so you understand
the current deploy shape and what phases 1-5 have
landed:

- `shakenfist/deploy/ansible/deploy.yml` — the
  playbook. The `[primary_node]` and `[etcd_master]`
  hosts groups, the cluster-config delegation to
  `etcd_master[0]`, and the threading of
  `mariadb_password` / `mariadb_gateway_hosts`.
- `shakenfist/deploy/ansible/roles/base/tasks/register.yml`
  — sf-database startup is currently
  `delegate_to: etcd_master[0]` with
  `run_once: true`, which collapses an N-element
  group to a single instance. Phase 6 unwinds that.
- `shakenfist/deploy/ansible/roles/base/templates/config`
  — `SHAKENFIST_MARIADB_GATEWAY_HOSTS` is rendered
  as a single host (`etcd_master[0]`'s mesh IP).
  Phase 6 renders it as a comma-separated list of
  every `etcd_master` member's mesh IP. The
  `SHAKENFIST_MARIADB_HOST` block already only
  renders on `etcd_master` group members; no change
  needed there.
- `shakenfist/deploy/ansible/roles/base/tasks/config.yml`
  — the `sf-database.service` template is dropped
  with `when: inventory_hostname in
  groups.get('etcd_master', [])`. Multi-instance
  startup is supported by the existing template; the
  only thing standing in the way is `register.yml`'s
  delegation.
- `shakenfist/daemons/database/main.py` — Prometheus
  counters are named `database_{op}_total` (e.g.
  `database_get_state_total`,
  `database_set_state_total`) and live on each
  instance's `MARIADB_GATEWAY_METRICS_PORT` (default
  13006). Per-instance counters are what phase 6's
  functional test asserts non-zero against, to
  prove the LB fanned requests out.
- `shakenfist/util/grpc_channel.py` — the
  `make_database_channel()` factory ships
  `loadBalancingConfig: round_robin` and
  `healthCheckConfig: serviceName: ""` in
  `grpc.service_config`. Round-robin picks a
  subchannel per RPC, so two sf-database instances
  fronted by the same channel will see roughly 50/50
  request distribution.
- `tools/ci-install-mariadb.sh` and
  `tools/bootstrap-mariadb.sql` — phase 5's
  operator-facing helpers. The SQL snippet already
  grants `'shakenfist'@'%'` so any client host can
  connect. The CI install script does **not**
  override Debian's `bind-address = 127.0.0.1`
  default; phase 6 needs MariaDB to listen for
  remote etcd_master nodes too, so step 2 extends
  the script with a bind-all drop-in for multi-node
  deployments.
- `.github/workflows/functional-tests.yml` — phase 5
  left four "Install BYO MariaDB on primary" / "Run
  getsf installer on primary" pair sites for the
  three existing matrix entries (Smoke localhost,
  three `slim-primary` merge entries) and one
  ansible-modules site. Phase 6 adds **one new
  matrix entry** under the merge-queue job that
  consumes a new `slim-tier` topology with at
  least two `etcd_master` members.
- `.github/workflows/scheduled-tests.yml` — the
  scheduled localhost / slim-primary entries.
  Phase 6 does **not** add a scheduled `slim-tier`
  entry; one entry on the merge-queue side is
  enough to keep regression coverage on every PR
  that reaches merge, and the scheduled run already
  has long enough runtime budgets.
- `docs/operator_guide/database.md` — the operator-
  facing description of the BYO MariaDB and tier
  model. Phase 6 adds a single short paragraph
  noting that N>1 sf-database is exercised by CI,
  plus an example `MARIADB_GATEWAY_HOSTS` value
  with a comma-separated list. The bulk of the
  doc rewrite is phase 7's scope.

The point of this phase is to prove the tier works
under load and to lay down regression coverage so a
future "only one sf-database actually serves
requests" bug cannot reach a release silently. The
tier already works in code (phases 1-3 made it work);
phase 6 makes CI watch it work.

One commit per step. Each commit must pass
`pre-commit run --all-files` (which includes
`actionlint` on workflow YAML). Step 4 depends on a
landed change in the external `shakenfist/actions`
repository (a new `ci-topology-slim-tier.yml`
playbook); that coordination is called out in the
step's brief and below in *Risks*.

## Context

After phase 5, CI exercises the BYO contract end to
end against a single `sf-database` instance.
`SHAKENFIST_MARIADB_GATEWAY_HOSTS` is a one-element
list, the `round_robin` LB policy has nothing to
round-robin between, and `register.yml`'s
`delegate_to: etcd_master[0] + run_once: true` makes
"start sf-database on every etcd_master node" act
the same as "start one sf-database on the primary".
That is *correct* for single-tier deployments and
*invisible* for any other shape, because no shape
runs more than one `sf-database` today.

Two specific properties of the tier model are
currently untested:

1. **A request from a daemon's gRPC channel actually
   reaches more than one sf-database instance.** The
   `round_robin` policy's correctness depends on the
   resolver returning multiple addresses and the
   subchannels being healthy. If the resolver fails
   silently, or the health check fails on N-1
   subchannels, the channel falls back to "always
   pick the one healthy subchannel" and the LB
   becomes a no-op. Nothing about that would surface
   in a single-endpoint test.
2. **A second `sf-database` startup against the same
   MariaDB completes cleanly.** Phase 1 fixed the
   schema-versions race defensively and moved
   migrations out of daemon startup, but no CI run
   has ever brought two instances up against the
   same schema concurrently. A latent bug — a stray
   `Table(...)` registration outside
   `TABLE_CREATION_LOCK`, an SQLAlchemy state-cache
   race on shared metadata, a `record_start` clash
   on identical hostnames — would not surface in
   single-instance CI.

Phase 6 closes both gaps by:

1. **Removing the single-instance pinning in
   `register.yml`** so multi-etcd_master topologies
   genuinely run sf-database on every group member.
2. **Rendering `MARIADB_GATEWAY_HOSTS` as the full
   `etcd_master` group list** so daemons construct
   multi-endpoint LB channels in line with the
   actual topology.
3. **Teaching `ci-install-mariadb.sh` to listen on
   all interfaces** so non-primary etcd_master
   nodes can reach MariaDB on the primary.
4. **Adding a `slim-tier` CI topology** (in the
   external `shakenfist/actions` repo) with three
   nodes and `GETSF_NODE_ETCD_MASTER` set to two
   of them, plus a workflow matrix entry that
   consumes it.
5. **Shipping a functional test** that drives sf-api
   for a couple of seconds, scrapes
   `database_*_total` counters from each
   sf-database instance's metrics endpoint, and
   asserts every instance saw a non-trivial share
   of the traffic.
6. **A one-paragraph docs update** to
   `docs/operator_guide/database.md` calling out
   that N>1 sf-database is the tested production
   shape and showing the comma-separated
   `MARIADB_GATEWAY_HOSTS` form.

After this phase lands:

- Every PR's merge-queue run exercises a 2-instance
  sf-database tier against a shared MariaDB.
- The functional test fails if either instance does
  not serve a share of the traffic, which catches:
  (a) the gRPC LB silently falling back to a single
  subchannel, (b) one `sf-database` instance
  crashing on startup, (c) one instance going
  silently unhealthy mid-run.
- A future regression to single-instance behaviour
  (someone re-adds `delegate_to: etcd_master[0]` on
  the assumption that sf-database is "still a
  singleton", or a config-template edit drops the
  list) fails on the next PR rather than at
  operator deploy time.

## Decisions (phase-local)

1. **Multi-instance startup is a `when:` gate, not
   a delegate-and-run-once collapse.** The
   "Start the database daemon on etcd_master nodes"
   task in `register.yml` currently uses
   `delegate_to: etcd_master[0]` with
   `run_once: true`, which makes it a single
   action regardless of group size. Phase 6
   replaces both with
   `when: inventory_hostname in
   groups.get('etcd_master', [])` so every member
   of the group starts its own sf-database. The
   `register-daemon database` task immediately
   above already uses this gate pattern and works
   correctly under N>1; the start task just needs
   to follow the same shape.

2. **`MARIADB_GATEWAY_HOSTS` template renders the
   full list.** Today the template line is
   `SHAKENFIST_MARIADB_GATEWAY_HOSTS="{{
   hostvars[groups['etcd_master'][0]]['node_mesh_ip']
   }}"`. Phase 6 makes it
   `SHAKENFIST_MARIADB_GATEWAY_HOSTS="{{
   groups['etcd_master'] | map('extract', hostvars,
   'node_mesh_ip') | join(',') }}"`. Single-
   etcd_master deployments degenerate to a one-
   element list with no trailing comma, which is
   what `_parse_comma_separated_hosts` in
   `config.py` already handles.

3. **CI install script grows a bind-all drop-in.**
   `ci-install-mariadb.sh` writes a small
   `/etc/mysql/mariadb.conf.d/99-bind-all.cnf`
   containing `bind-address = 0.0.0.0` before
   restarting MariaDB. This is correct for CI
   (ephemeral VMs reachable only inside the test
   namespace), correct for single-box dev installs
   (loopback connects to 0.0.0.0 fine), and only
   problematic for an operator who copies the
   script verbatim onto a production host — which
   the script's leading comment already warns
   against. The drop-in lands as `99-*` so it
   overrides any Debian-shipped `50-server.cnf`
   bind-address default.

4. **New topology lands in the external
   `shakenfist/actions` repo.** Phase 5
   deliberately left the actions repo untouched
   because phase 5's goal was the narrowest
   possible "restore green" fix. Phase 6's goal —
   prove the tier works — requires a new topology
   the actions repo doesn't ship today. The new
   playbook `ansible/ci-topology-slim-tier.yml`
   provisions three VMs (10.0.0.20-22 is fine,
   reusing the existing slim-primary IP block) and
   the matching `getsf-wrapper` sets
   `GETSF_NODE_ETCD_MASTER="<vm-1-name>
   <vm-2-name>"`. Two etcd_master nodes is enough
   to exercise the LB path; three would not
   meaningfully strengthen the test and would
   widen the CI VM footprint. The third VM is a
   hypervisor.

5. **Matrix entry only on the merge-queue side.**
   The new entry goes in the merge-queue job's
   `matrix.merge` list, not in the PR-localhost
   `matrix.pr` list and not in the scheduled
   workflow. Rationale: every PR already runs the
   PR-localhost smoke; adding a `slim-tier` PR
   entry would double infrastructure spend per PR
   for marginal coverage. The merge-queue gate is
   the right place to insist a PR has been
   exercised against the multi-instance tier
   before it ships, and the merge-queue runs less
   often than per-PR CI so the cost is bounded.

6. **Functional test asserts each instance served
   at least 5% of total traffic.** A daemon's gRPC
   channel hits the round-robin policy at
   per-call granularity, so 100 calls across two
   instances should be roughly 50/50 ± normal
   jitter. The 5% floor is loose enough to
   survive transient unhealthiness or one
   subchannel briefly going into reconnect, and
   strict enough to fail loudly when the LB
   silently degenerates to a single subchannel
   (which manifests as 100% / 0%). The test uses
   the `database_*_total` family, scraping each
   instance's Prometheus endpoint at
   `http://<mesh_ip>:13006/metrics`.

7. **The functional test lives in
   `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/`.**
   That directory's tests run against
   multi-node deployments (slim-primary today,
   slim-tier after phase 6). It can also live
   under a new subdirectory if the test fits
   poorly with the existing tests, but the
   simpler choice is the existing dir with a
   `test_database_tier.py` file. Smoke and
   guest CI don't see the test (they're
   single-node) which is fine — the test only
   matters in the tier shape.

8. **No bind-address change to the operator-
   facing tuning .cnf.** `examples/mariadb-
   tuning.cnf` is operator infrastructure;
   operators choose their own bind-address based
   on their network topology. The CI script's
   drop-in is the right home for the
   bind-all override because the script is
   already CI/dev-flavoured and warns it is
   not for production.

## Steps

Six steps. Step 3 (the external repo work) is
coordinated outside this repo; steps 4 and 5
must not merge until step 3 has landed and is
discoverable from the CI runner, or the new
matrix entry's `Build infrastructure` step will
fail looking for a topology playbook that
doesn't exist.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | opus | worktree | Multi-instance sf-database startup. Edit `shakenfist/deploy/ansible/roles/base/tasks/register.yml`. Find the task `Start the database daemon on etcd_master nodes` (currently around line 50, with `delegate_to: "{{ groups['etcd_master'][0] }}"` and `run_once: true`). Replace `delegate_to` and `run_once` with `when: inventory_hostname in groups.get('etcd_master', [])`, matching the gate pattern the immediately-preceding `Register the database daemon` task already uses. Verify by reading the task block: after the edit, both the register and start tasks should look structurally identical except for the action (one calls `sf-ctl register-daemon database --node-name`, the other restarts the systemd unit). Update the leading comment block in `register.yml` (lines 1-15) — the comment currently says "Step 3: Start the database daemon on etcd_master nodes" which is correct, but it also says "All sf-ctl commands are delegated to etcd_master[0] (the database-tier node)" in the singular — change "node" to "nodes" and "the database-tier node" to "the first database-tier node". One commit. `pre-commit run --all-files`. |
| 2 | low | sonnet | none | `MARIADB_GATEWAY_HOSTS` list + bind-all drop-in. Two file edits in one commit. (a) `shakenfist/deploy/ansible/roles/base/templates/config` line 34 — change the single-host extraction `SHAKENFIST_MARIADB_GATEWAY_HOSTS="{{hostvars[groups['etcd_master'][0]]['node_mesh_ip']}}"` to a list join: `SHAKENFIST_MARIADB_GATEWAY_HOSTS="{{ groups['etcd_master'] \| map('extract', hostvars, 'node_mesh_ip') \| join(',') }}"`. Verify the single-element case still renders without a trailing comma (jinja's `join` does the right thing). (b) `tools/ci-install-mariadb.sh` — after the `Applying SF bootstrap snippet` block and before the tuning install, add a new section that writes `/etc/mysql/mariadb.conf.d/99-bind-all.cnf` containing `[mysqld]\nbind-address = 0.0.0.0\n`. Make it unconditional (no flag); the script's leading comment already warns it is for CI / dev, not production. The existing `systemctl restart mariadb` block at the tuning install can be reused — if the tuning install is skipped (empty `TUNING_PATH`), the script must still restart MariaDB after writing the bind-all drop-in. Simplest shape: write the bind-all drop-in BEFORE the tuning install, hoist the restart-and-wait block out so it runs once after both edits. `pre-commit run --all-files`. `bash -n tools/ci-install-mariadb.sh`. One commit. |
| 3 | — | — | — | **External coordination, not implemented by a sub-agent in this repo.** Create a PR against `shakenfist/actions` adding `ansible/ci-topology-slim-tier.yml`. The playbook should provision three VMs (reuse the slim-primary IP block 10.0.0.20-22 if practical) and a matching getsf-wrapper that sets `GETSF_NODE_PRIMARY` to vm-1, `GETSF_NODE_ETCD_MASTER` to "vm-1 vm-2" (a two-element space-separated list, matching getsf's `read GETSF_NODE_ETCD_MASTER` shape), `GETSF_NODE_NETWORK` to vm-1, `GETSF_NODE_HYPERVISOR` to "vm-1 vm-2 vm-3" (so the third VM is a hypervisor), and `GETSF_NODE_STORAGE` empty. Pattern-match on the existing `ci-topology-slim-primary.yml` for the rest of the wrapper structure. This step is **not** a sub-agent task; the management session coordinates the actions-repo PR with the operator. Step 4 must not merge until this PR has landed on `main` and the `setup-test-environment` action's pinned ref (`@main` per the workflow YAML) picks it up. Steps 1, 2, 5 and 6 are independent of this and can land in any order before step 4. |
| 4 | medium | opus | worktree | Add the `slim-tier` matrix entry. Edit `.github/workflows/functional-tests.yml`. In the merge-queue job's `matrix.merge` array (around line 803) add a new entry after the last existing `slim-primary` entry: `{ name: 'Debian 12 tier', job_name: 'debian-12-slim-tier', base_image: 'sf://label/ci-images/debian-12', base_image_user: 'debian', topology: 'slim-tier', concurrency: 5, stestr_config: 'cluster-ci.conf' }`. The existing steps under the merge-queue job already consume `matrix.merge.topology` to pick the topology playbook, so no other workflow edit is needed. **Important**: the existing `Install BYO MariaDB on primary` and `Run getsf installer on primary` step pair (around line 1445/1463 of the slim-primary merge sites) ssh-invokes the install on `${primary}` only — but `${primary}` is the primary VM, which is also where MariaDB lives, so this is still correct for slim-tier (the additional sf-database instance on vm-2 connects to MariaDB on vm-1 over the mesh). However the `GETSF_MARIADB_HOST=127.0.0.1` literal in the installer step is *wrong* for slim-tier because the non-primary etcd_master node cannot use 127.0.0.1 to reach MariaDB. Change the value at the slim-tier site only to `${primary_mesh_ip}` — or simpler, leave 127.0.0.1 at the slim-primary sites and inject a separate `if [ matrix.merge.topology == 'slim-tier' ]` shell branch in the new entry's getsf invocation that uses the primary's mesh IP. The cleanest approach is a per-site SSH command rather than a conditional, so duplicate the entire `Install BYO MariaDB on primary` + `Run getsf installer on primary` pair for slim-tier with the corrected `GETSF_MARIADB_HOST` value. Yes that's another five-or-so lines of YAML duplication — the phase-5 decision (workflow YAML duplication accepted) applies the same way here. `actionlint` via pre-commit catches yaml-syntax mistakes. One commit. The commit message must call out the dependency on `shakenfist/actions` step 3 having landed first. |
| 5 | high | opus | none | Functional test for the LB path. Create `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_database_tier.py` with one test class `TestDatabaseTier(base.BaseTestCase)` and one test method `test_grpc_lb_fans_out_across_sf_database_instances`. The test does the following: (a) Discover sf-database instances by reading the cluster's node list (`self.system_client.get_nodes()`) and filtering to nodes whose `is_etcd_master` attribute is True — those are the etcd_master members and therefore the sf-database hosts. **Note**: phase 6 of PLAN-byo-mariadb does not rename `is_etcd_master`; that rename lives with PLAN-remove-primary phase 7. The attribute is still spelled `is_etcd_master` in the API response at the time this test ships. (b) Skip the test if fewer than 2 sf-database instances are present, with a clear `self.skipTest('test_grpc_lb_fans_out_across_sf_database_instances requires N>=2 sf-database instances; saw N=...')` message — that keeps the test harmless on the slim-primary / localhost shapes which only have one etcd_master. (c) Scrape `http://<node_mesh_ip>:13006/metrics` for each sf-database node before driving any traffic, parse out each `database_*_total` counter into a dict keyed by counter name. Use the existing `requests` package (already a test dep) and a small parser; do not pull in a prometheus client library for this. (d) Drive ~100 cheap sf-api calls — `self.system_client.get_namespaces()` is fine — each of which triggers at least one sf-database RPC on the api server's side. Do it serially; concurrency adds noise without strengthening the assertion. (e) Re-scrape `/metrics` on each sf-database node, compute deltas, sum across counter names per node. (f) Assert that each node saw at least `0.05 * total_delta_summed_across_nodes` requests. The 5% floor accommodates jitter while failing loudly on a 100/0 split. Include `self.addDetail` calls dumping the before / after counter dicts so a failure produces a readable forensic trail. Keep the test focused on this one assertion — do not also assert per-RPC-name counters, that's brittle to RPC-call-count changes in unrelated phases. The test ships as part of `cluster-ci.conf` automatically (everything under `cluster_ci_tests/` is discovered by stestr). `pre-commit run --all-files`. One commit. |
| 6 | low | sonnet | none | One-paragraph docs update. Edit `docs/operator_guide/database.md`. Find the existing section that describes `MARIADB_GATEWAY_HOSTS` (added during phase 2). Add a new sub-paragraph immediately after it titled "**Multi-instance deployments**" or similar, two-to-three sentences: (a) "More than one `sf-database` instance can run against the same MariaDB. List every instance's mesh IP in `MARIADB_GATEWAY_HOSTS`, comma-separated." (b) Show an example: `MARIADB_GATEWAY_HOSTS="10.0.0.20,10.0.0.21"`. (c) "Every sf-database instance must be able to reach the MariaDB server; in BYO deployments this typically means the operator's MariaDB is bound to a routable interface, not localhost." Single commit. `pre-commit run --all-files`. The bigger docs sweep (lead-with-BYO restructure, README, ARCHITECTURE.md) is phase 7's scope, not this step. |

## Validation

- `pre-commit run --all-files` passes after each step
  (flake8 / mypy / stestr / actionlint).
- After step 1 + step 2: a manual ansible-lint dry
  run (`ansible-lint shakenfist/deploy/ansible/`)
  shows no new warnings.
- After step 4 (with step 3 landed externally): the
  next merge-queue run shows the new
  `debian-12-slim-tier` job appearing in the
  matrix and reaching green. The build-
  infrastructure step provisions three VMs, the
  BYO MariaDB install step succeeds on vm-1, the
  `Run getsf installer on primary` step completes,
  and `tools/run_remote vm-2 systemctl is-active
  sf-database` returns `active`.
- After step 5: the `test_grpc_lb_fans_out`
  test passes on a merge-queue run with the
  `debian-12-slim-tier` matrix entry, and is
  reported as `skipped` on every other matrix
  entry. The skipped reason in the stestr output
  is readable and mentions the N>=2 requirement.
- After step 6: `mkdocs build` succeeds and the
  new paragraph appears in the rendered
  operator-guide page.

## Risks

- **External repo coordination (step 3 landing
  late).** If the workflow change (step 4) merges
  before the actions-repo PR lands, every PR's
  merge-queue run fails at `Build
  infrastructure` looking for
  `ansible/ci-topology-slim-tier.yml`. The
  brief explicitly tells the management session
  to land step 3 first and confirm the
  `setup-test-environment` action picks up the
  new playbook. The reverse — landing step 3
  before step 4 — is harmless; the topology
  just sits unused until step 4 merges.
- **Round-robin jitter under low call volume.**
  100 calls split across 2 instances is mean-50,
  variance-25. The probability of any single
  instance getting fewer than 5 calls (i.e.
  failing the 5% floor) is vanishingly small for
  a healthy round-robin policy, but a long GC
  pause or a transient unhealthy subchannel
  could push it. If the test flakes, the right
  fix is increasing the call count (to 200 or
  500) rather than loosening the floor — the
  floor exists to catch silent LB regression.
- **Non-primary etcd_master cannot reach
  MariaDB on the primary.** Step 2 fixes the
  bind-address default. If the workflow still
  fails on vm-2's sf-database startup after step
  4 lands, the most likely cause is either: (a)
  the bind-all drop-in wasn't applied (check
  `/etc/mysql/mariadb.conf.d/99-bind-all.cnf`
  on the primary), or (b) iptables / nftables /
  the CI VM's network is blocking 3306 between
  VMs. Mitigation: a one-line systemd unit
  check in the workflow's logs section will
  catch (a); for (b), the CI VM image is
  controlled by the actions repo and is the
  same image used by slim-primary, which is
  already known to permit intra-cluster
  traffic, so this is unlikely.
- **`is_etcd_master` attribute name churn.**
  PLAN-remove-primary phase 7 will rename
  `is_etcd_master` (Python attribute, ansible
  group, API field). If phase 7 lands between
  this plan's step 1 and step 5, step 5's test
  needs the new attribute name. Mitigation:
  the test's discovery logic is a single line
  (`if node['is_etcd_master']`); a future
  rename catches it via the test failing,
  which is fine. Phase 7's PR will be where
  the rename is threaded through.
- **The phase-1 schema-version verification
  fires on the second sf-database start.**
  Phase 1 made sf-database verify the schema
  version on startup and refuse to start on
  mismatch. Two sf-database instances bringing
  themselves up against the same `schema_versions`
  table in close succession is the first time
  this code path runs concurrently in CI. The
  read is a simple `SELECT version FROM
  schema_versions`; there is no race here. If
  one instance fails to start because the
  table read raced with the other instance's
  read, that is a phase-1 bug to fix, not a
  phase-6 redesign. Mitigation: the
  workflow's logs already capture sf-database
  systemd output, so a failed-to-start on
  vm-2 surfaces directly.
- **gRPC subchannel health-check timing.** The
  round-robin policy excludes subchannels with
  failing health checks. If vm-2's
  sf-database is slow to come up, the channel
  on the client side may briefly see only
  vm-1 as healthy and route 100% of early
  traffic there. The test scrapes after the
  call loop completes, so a transient
  unhealthy state during the loop's first few
  calls is fine; only a *persistent* skew to
  one instance fails the test. If the test
  flakes here, the fix is a short
  `time.sleep(2)` before the call loop to
  let the channel settle, or hitting the
  health endpoint of each sf-database
  directly before the loop to confirm both
  are SERVING.

## Out of scope

- The `is_etcd_master` rename — PLAN-remove-
  primary phase 7.
- A bigger docs rewrite (lead-with-BYO,
  `ARCHITECTURE.md`, `README.md`, release notes)
  — phase 7 of this plan.
- A scheduled-workflow `slim-tier` entry — every
  merge gate already covers it; the scheduled
  workflow stays focused on long-running shapes.
- Cross-instance sf-database state sharing
  testing (caches, hostname collisions in
  `record_start`) — these don't exist by design
  (sf-database is stateless), but if a future
  bug surfaces one, the regression test for it
  is its own commit, not part of this phase.
- A `slim-tier` PR-localhost matrix entry —
  doubles per-PR cost for marginal benefit.
- A LB regression test against `localhost`
  topology — the test self-skips when N<2,
  which is the intended shape.
- L4 LB / VIP regression coverage — there is
  no L4 LB in the architecture (decision 6 of
  the master plan).
- Per-caller authz testing — gated on the
  mTLS work in `PLAN-embrace-tls.md`.

## Back brief

Before executing this phase, please back brief
the operator on:

- The six steps and the file boundaries for each.
- Step 3 is **not** a sub-agent task in this
  repo; it is a coordinated PR against the
  external `shakenfist/actions` repo. Step 4
  cannot land until step 3 has merged and is
  picked up by the `setup-test-environment`
  action's `@main` ref. Confirm the
  management session will own that
  coordination rather than spawn an agent to
  do it.
- The decision to add the matrix entry on the
  merge-queue side only (not PR-localhost,
  not scheduled). Confirm.
- The decision to keep `examples/mariadb-
  tuning.cnf` operator-pure and put the
  bind-all drop-in in `ci-install-mariadb.sh`
  instead. Confirm.
- The functional test's 5% per-instance floor
  and the rationale (catches silent LB
  degeneracy without flaking on healthy
  jitter). Confirm.
- The decision to skip the test on N<2
  topologies rather than fail it. Confirm.
- The dependency on phase 1's schema-version
  verify behaving cleanly under concurrent
  startup — phase 6 stresses it for the first
  time, and any latent bug there is a phase-1
  fix, not a phase-6 redesign.
