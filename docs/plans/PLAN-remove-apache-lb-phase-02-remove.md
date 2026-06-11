# Phase 2 — Remove Apache from the deployer

Parent plan: [`PLAN-remove-apache-lb.md`](PLAN-remove-apache-lb.md).

This is the **deletion** phase. With operator-provided load
balancing now documented (phase 1), the deployer's bundled
Apache install is removed and the single-node `api_url`
default is repointed at gunicorn directly. Both commits leave
CI green.

## Recommended planning effort

Planned at **medium effort**. The deletions themselves are
mechanical, but the `api_url` change has a non-obvious blast
radius (it flows from `getsf` topology JSON through
`deploy.yml` into every node's `sfrc` / `shakenfist.json`),
and the commit ordering matters for keeping CI green. That
analysis is front-loaded below so the implementing steps are
straightforward.

## What exists today

The deployer installs Apache as the cluster's API load
balancer in three places:

1. `shakenfist/deploy/ansible/roles/primary/tasks/apache2.yml`
   — installs `apache2`, enables `proxy proxy_http
   lbmethod_byrequests`, writes the site, restarts the
   service.
2. `shakenfist/deploy/ansible/files/apache-site-primary.conf`
   — the jinja vhost that balances `/api` (and the OpenAPI
   doc paths) across each hypervisor's `node_mesh_ip:13000`.
3. `shakenfist/deploy/ansible/deploy.yml` — the `primary_node`
   play (lines ~255-265) runs `role: primary` with
   `role_action: "apache2"` and then `role_action:
   "postinstall"`.

### How `api_url` flows (the part that needs care)

- `getsf` generates the topology JSON and **hardcodes**
  `api_url` for primary nodes only, in two places:
  - the `localhost` (single-node) branch, line ~1050:
    `api_stanza='"api_url": "http://127.0.0.1/api"'`
  - the multi-node `GETSF_NODE_PRIMARY` branch, line ~1059:
    the same string.
  There is **no operator prompt** for `api_url`; multi-node
  operators set it by editing the generated topology (the
  `installation.md` example shows `api_url` as an
  operator-set field pointing at their own load balancer).
- `deploy.yml` (lines ~42-46) reads `api_url` from the entry
  with `primary_node: true` into `hostvars['localhost']`.
- `roles/base/tasks/config.yml` writes that value into
  `/etc/sf/sfrc` (`SHAKENFIST_API_URL`) and
  `/etc/sf/shakenfist.json` (`apiurl`) on **every** SF node
  (`base/config` runs on `allsf`).

The `/api` prefix only resolves because Apache listens on
port 80 and proxies `/api` → `:13000`. Remove Apache and
`http://127.0.0.1/api` serves nothing. The single-node fix is
to point `api_url` at gunicorn directly:
`http://127.0.0.1:13000` (no `/api`, because there is no
proxy to strip it). The primary node runs `sf-api` locally —
`postinstall` verifies `sf-api` is active and curls
`http://localhost:13000/auth/namespaces` expecting a 401 —
so `127.0.0.1:13000` is valid on the primary, and on every
node that runs `sf-api` locally.

`getsf` also carries operator-facing prompt text (lines
~336-345) that still claims "the primary node is where we
will configure the load balancer for API traffic. Therefore,
its public address needs to be the one which is in the API
URL." That is now false and must be reworded.

### Why CI stays green

- `postinstall`'s API health check hits `:13000` directly,
  not the Apache `/api` path.
- The cluster_ci functional tests export
  `SHAKENFIST_API_URL=http://localhost:13000`
  (`functional-tests.yml:416`) and never used Apache.
- CI deploys a `localhost` topology, so after step 1 its
  generated `sfrc` carries `http://127.0.0.1:13000` — which
  is what the tests already use.

### Commit ordering (load-bearing)

The `getsf` `api_url` change must land **before** the Apache
removal. If Apache were removed first while `getsf` still
emitted `http://127.0.0.1/api`, a fresh single-node deploy
between the two commits would have a broken `api_url` (nothing
serves `/api`) — violating "every commit builds and passes".
Doing the `getsf` change first means single-node immediately
uses `:13000` (gunicorn is already running), with Apache still
installed but unused-by-default; CI stays green. Then removing
Apache changes nothing that the default relies on.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | **Repoint the single-node `api_url` default and fix the prompt text in `getsf`.** Edit `shakenfist/deploy/getsf` only. (a) Change the two hardcoded `api_url` defaults from `http://127.0.0.1/api` to `http://127.0.0.1:13000`: line ~1050 (the `if [ ${node} == "localhost" ]` branch) and line ~1059 (the `GETSF_NODE_PRIMARY` branch). Both currently read `api_stanza='"api_url": "http://127.0.0.1/api"'`; both become `api_stanza='"api_url": "http://127.0.0.1:13000"'`. Note the `/api` prefix is an Apache artifact being removed — single-node talks to gunicorn directly, which serves the API at `/` on port 13000 with no prefix. (b) Reword the primary-node prompt text (lines ~336-345). It currently says the primary node "is where we will configure the load balancer for API traffic. Therefore, its public address needs to be the one which is in the API URL." Replace that claim: the primary node is the operations console that deploys the other nodes and receives their logs; Shaken Fist no longer installs a load balancer, so the operator puts their own reverse proxy / load balancer in front of the API (for a single-node install the API is reachable locally at `http://127.0.0.1:13000`). Keep it concise, keep the surrounding `echo` style, and do not change the question/`read GETSF_NODE_PRIMARY` logic below it. Do NOT touch any other file in this step. Run `pre-commit run --files shakenfist/deploy/getsf`. Do not commit. |
| 2 | medium | sonnet | none | **Delete the Apache install from the deployer.** (a) `git rm shakenfist/deploy/ansible/roles/primary/tasks/apache2.yml` and `git rm shakenfist/deploy/ansible/files/apache-site-primary.conf`. (b) In `shakenfist/deploy/ansible/deploy.yml`, the `primary_node` play near line 255 currently lists two role invocations: `- role: primary` with `vars: role_action: "apache2"`, then `- role: primary` with `role_action: "postinstall"`. Remove the entire `apache2` role entry (the `- role: primary` / `vars:` / `role_action: "apache2"` block) and keep the `postinstall` invocation exactly as is. Read the play first to get the YAML indentation right; the result should be a `primary_node` play that runs only `postinstall`. (c) Grep the deployer for any remaining Apache or bundled-`/api` assumptions to confirm nothing else references the removed install: `grep -rin "apache" shakenfist/deploy/` (the only remaining hit should be `cluster_ci_tests/test_floating_ips.py`, which installs apache2 *inside a test VM* as a web server for floating-IP testing — this is UNRELATED and must NOT be touched) and `grep -rn "127.0.0.1/api\|localhost/api" shakenfist/deploy/` (should be empty after step 1). The `primary` role's `main.yml` does a generic `include_tasks "{{ role_action }}.yml"`, so deleting `apache2.yml` needs no change there as long as nothing calls `role_action=apache2` (which the deploy.yml edit removes). Run `pre-commit run --all-files`. Do not commit. |

## Ordering note

Step 1 (getsf default) **must** be committed before step 2
(Apache removal) — see "Commit ordering" above. Do step 1,
review, commit; then step 2, review, commit.

## Open question resolved

Master-plan open question 4 ("is `api_url` still mandatory for
the primary node?"): **yes, and that is unchanged by this
phase.** `deploy.yml` only sets `api_url` from the entry with
`primary_node: true`; if that entry omits `api_url`, the
`sfrc` / `shakenfist.json` templating has no value to
interpolate. `getsf` always emits `api_url` for the primary
(now `:13000`), and multi-node operators set it to their load
balancer URL by hand. No graceful-default logic is added —
the requirement is documented (the Load Balancing page from
phase 1 covers the single-node `:13000` value and the
operator-provided LB for multi-node).

## Verification (management session / operator)

This phase is operator-facing, so an internally-clean change
that breaks the CI deploy is a regression. After both commits:

- `pre-commit run --all-files` is clean (flake8, unit tests,
  mypy, ansible-lint, actionlint).
- Push the branch and confirm the `functional-tests` workflow
  deploys and passes (the localhost topology will now generate
  `api_url: http://127.0.0.1:13000`; the tests already target
  `:13000`). This push is an operator action — Claude does not
  open PRs or push without being asked.

## Management session review checklist (phase-specific)

- [ ] Both `getsf` `api_url` defaults now read
      `http://127.0.0.1:13000`; no `127.0.0.1/api` remains in
      the tree (`grep -rn "127.0.0.1/api" shakenfist/`).
- [ ] The `getsf` primary-node prompt no longer claims the
      deployer configures a load balancer.
- [ ] `apache2.yml` and `apache-site-primary.conf` are gone;
      `git status` shows them deleted (not just emptied).
- [ ] The `primary_node` play in `deploy.yml` runs only
      `postinstall`, with valid YAML and correct indentation.
- [ ] `grep -rin apache shakenfist/deploy/` returns only the
      unrelated `test_floating_ips.py` in-VM web server.
- [ ] No unrelated files changed.
- [ ] `pre-commit run --all-files` passes.
- [ ] Each commit is self-contained and follows the project
      commit-message conventions (Signed-off-by, Prompt
      paragraph, Co-Authored-By with model / context /
      effort). Step 1 is committed before step 2.

## Success criteria for this phase

* `roles/primary/tasks/apache2.yml` and
  `files/apache-site-primary.conf` no longer exist, and no
  part of the deployer installs or configures Apache.
* The `primary_node` play in `deploy.yml` invokes only
  `postinstall`.
* `getsf` emits `http://127.0.0.1:13000` as the single-node /
  primary `api_url` default, and its primary-node prompt text
  no longer describes a bundled load balancer.
* A single-node `getsf` deploy yields a working `sf-client`
  with no proxy installed (API reached at `:13000`).
* The cluster_ci functional tests still pass.
* `pre-commit run --all-files` passes.

## Hand-off

Phase 2 completes the technical scope of
`PLAN-remove-apache-lb`. After it lands, update the parent
plan's status in `docs/plans/index.md` to mark both phases
complete, and (per the parent plan's Future-work note) mark
phase 3 of [`PLAN-remove-primary.md`](PLAN-remove-primary.md)
as realised by this plan so the two do not describe the same
work as both outstanding.
