# Remove the Apache load balancer from the deployer

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (the
ansible deployer layout, `getsf` topology generation, the
`primary` role, how `api_url` flows from topology JSON into
`sfrc` / `shakenfist.json`, and how the cluster_ci rig talks
to the API), and ground your answers in what the code
actually does today. Do not speculate about the codebase when
you could read it instead. Where a question touches on
external concepts (Apache `mod_proxy_balancer`, nginx
`upstream` / `proxy_pass`, HTTP reverse proxying, gunicorn),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview and daemon structure, `CLAUDE.md` for build commands
and project conventions (note the "REST API URL Structure"
section, which documents the gunicorn-on-`:13000` vs
Apache-adds-`/api` distinction this plan acts on), and the
parent plan [`PLAN-remove-primary.md`](PLAN-remove-primary.md),
of which this work is the realisation of phase 3 ("Remove
Apache reverse proxy from deployer"), broken out into its own
focused master plan. Key references inside the repo include
`shakenfist/deploy/ansible/roles/primary/tasks/apache2.yml`
(the role action to delete),
`shakenfist/deploy/ansible/files/apache-site-primary.conf`
(the vhost template to delete and preserve as documentation),
`shakenfist/deploy/ansible/deploy.yml` (the `primary_node`
play that invokes the apache2 action),
`shakenfist/deploy/getsf` (where the single-node `api_url`
default is set), and `docs/operator_guide/installation.md`
(the operator-facing documentation to rewrite).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via the table in the Execution section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The Shaken Fist deployer assumes the primary node doubles as
the cluster's API load balancer by installing and configuring
Apache. The mechanism is three pieces:

1. `roles/primary/tasks/apache2.yml` installs `apache2`,
   enables `proxy proxy_http lbmethod_byrequests`, writes a
   site config, and restarts the service.
2. `files/apache-site-primary.conf` is the jinja-templated
   vhost. It listens on port 80 and proxies `/api` (plus the
   OpenAPI paths `/apidocs`, `/flasgger_static`,
   `/apispec_1.json`) to a `balancer://sfapi` pool whose
   `BalancerMember`s are every hypervisor's
   `node_mesh_ip:13000` — i.e. it round-robins across the
   gunicorn API workers.
3. `deploy.yml`'s `primary_node` play (around line 255)
   invokes `role: primary` with `role_action: "apache2"`
   before running `postinstall`.

This is the source of the `/api` URL prefix documented in
`CLAUDE.md`: gunicorn serves the API at `/` on port 13000,
and Apache is what maps the external `/api/...` path onto it.
The single-node `api_url` default that `getsf` emits
(`http://127.0.0.1/api`, set in two places around
`getsf:1050` and `getsf:1059`) only resolves because Apache
is listening on port 80 and proxying `/api`.

Two facts make this assumption removable with low risk:

- **The only known production deployment does not use it.**
  Mikal's production cluster fronts the API with nginx, not
  the deployer-installed Apache. The Apache install is dead
  weight there.
- **CI does not depend on it either.** The cluster_ci rig
  deploys a `localhost` (single-node) topology, but the
  functional tests export
  `SHAKENFIST_API_URL=http://localhost:13000`
  (`functional-tests.yml:416`) and talk straight to gunicorn.
  Apache is installed in CI but never exercised by a test, so
  removing it does not change what CI validates.

The Apache balancer config is also strictly less capable than
what a real operator wants: it has no TLS termination (it
listens on port 80 only), no health checking of backends, and
hard-codes the OpenAPI passthrough paths. Every operator who
takes SF to production replaces it. It belongs in
documentation as a worked example, not in the deployer as an
installed default.

The broader direction — SF stops being a platform deployer
and becomes an opinionated application that runs against
operator-provided infrastructure (DB, metrics, logs, load
balancer) — is set out in
[`PLAN-remove-primary.md`](PLAN-remove-primary.md). This plan
delivers the load-balancer slice of that vision.

## Mission and problem statement

Remove every trace of the Apache load balancer from the
deployer, and replace it with documentation that shows
operators how to put their own reverse proxy / load balancer
in front of the cluster. Concretely:

- The deployer no longer installs or configures Apache. The
  `apache2.yml` role action and the
  `apache-site-primary.conf` template are deleted, and the
  `primary_node` play no longer invokes the apache2 action.
  (The `primary` role itself stays — it still owns bootstrap,
  cluster_config, rsyslog, the ad-hoc inventory, and
  postinstall. Removing the primary node wholesale is the
  parent plan's job, not this one.)
- The single-node convenience deployment keeps working with
  **no operator-provided proxy at all**, by pointing
  `api_url` at gunicorn directly (`http://127.0.0.1:13000`,
  no `/api` prefix). This is the documented "localhost:13000
  escape hatch."
- Production operators are given **working example apache2
  and nginx configurations** in the operator guide, derived
  from (and replacing) the deleted vhost. The examples keep
  the `/api` external-path convention so existing
  `api_url: https://lb.example.com/api` values continue to
  work, and additionally show TLS termination as the thing an
  operator actually needs to add.
- The installation docs stop claiming "the primary node runs
  an apache load balancer" and instead state that load
  balancing is operator-provided, linking to the examples.

The principle, inherited from the parent plan: SF deploys
`sf-*` daemons on the hosts you tell it about, against
infrastructure (including the load balancer) whose addresses
you tell it. The load balancer is now firmly on the
operator's side of that line.

## Alternatives considered

### Keep Apache but make it optional (a topology flag)

We could gate the apache2 action behind a topology flag so
operators who want the bundled balancer keep it. We reject
this: it preserves the maintenance burden (the vhost template,
the `BalancerMember` loop, the unused-in-CI install) to serve
a configuration nobody is known to use, and it muddies the
"infrastructure is operator-provided" line the parent plan is
drawing. A documented example config an operator copies once
is strictly less code to own than a conditional role action
plus its template.

### Replace Apache with a bundled nginx

Since the one real deployment uses nginx, we could swap the
deployer to install nginx instead of Apache. We reject this
for the same reason: it keeps the deployer in the
load-balancer business, just with a different daemon. The
operator's nginx is already configured to their needs (TLS,
WAF, cert rotation, their own logging); a second
deployer-managed nginx would fight it. Ship the nginx config
as an example, not as an install.

### Drop the `/api` prefix convention entirely

We could take this opportunity to serve the API at `/` behind
the operator's LB and retire the `/api` prefix. We reject
this *for this plan*: the `/api` prefix is baked into existing
operators' `api_url` values, the OpenAPI doc URLs, and
client expectations. Changing it is a compatibility break
orthogonal to removing Apache, and would be its own plan. The
example LB configs therefore preserve `/api`. Single-node is
the one place there is no `/api` prefix, because there is no
proxy — and that is already the reality, just made explicit.

## Open questions

1. **Where do the example LB configs live?** _Resolved:_ a
   dedicated `docs/operator_guide/load_balancing.md` page,
   linked from `installation.md`.
2. **Should the example configs ship as files too?**
   _Resolved:_ yes — ship `examples/apache-loadbalancer.conf`
   and `examples/nginx-loadbalancer.conf` (mirroring the
   parent plan's `examples/grafana-dashboard.json`
   precedent), and snippet them into the doc page so the
   rendered docs and the copyable files stay in sync.
3. **Does the multi-node example topology in `installation.md`
   need its `api_url` reconsidered?** The sample at
   `installation.md:79` uses
   `https://...your...install...here.com/api`. With Apache
   gone, that URL now points at the operator's own LB rather
   than the deployer's Apache, but the value itself is still
   correct *if* the operator follows the example configs.
   Phase 1 should make that dependency explicit in the prose,
   not leave it implied.
4. **Is the `api_url` topology field still mandatory for the
   primary node?** Today `deploy.yml:42-46` records `api_url`
   from the `primary_node` entry, and `getsf` only emits the
   stanza for primary nodes. After this change, single-node
   gets `:13000` and multi-node operators supply their LB
   URL. Confirm in phase 2 that a missing/!primary `api_url`
   still degrades gracefully (it currently defaults to empty)
   and document the expectation.

## Execution

The work is small and splits cleanly into a docs-first phase
(which captures the soon-to-be-deleted Apache config as a
worked example before anything is removed, so no knowledge is
lost in between) and a deletion phase (which removes the
deployer code and repoints the single-node default). Both
phases leave CI green.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Document operator-provided load balancing (example apache2 + nginx configs, single-node escape hatch) | [PLAN-remove-apache-lb-phase-01-docs.md](PLAN-remove-apache-lb-phase-01-docs.md) | Complete |
| 2. Remove Apache from the deployer; repoint single-node `api_url` to `:13000` | [PLAN-remove-apache-lb-phase-02-remove.md](PLAN-remove-apache-lb-phase-02-remove.md) | Complete |

Phase notes:

- **Phase 1 (medium effort, docs-first).** Add a load
  balancing page to `docs/operator_guide/` (open question 1)
  with two worked examples: an Apache `mod_proxy_balancer`
  config derived from the existing
  `apache-site-primary.conf`, and an equivalent nginx
  `upstream` + `proxy_pass` config that matches the real
  production deployment's shape. Both preserve the `/api`,
  `/apidocs`, `/flasgger_static`, `/apispec_1.json`
  passthroughs and both show where TLS termination plugs in
  (the deployer's Apache had none). Document the single-node
  `http://localhost:13000` escape hatch — when there is no
  proxy, point `api_url` straight at gunicorn. Rewrite the
  "primary node runs an apache load balancer" paragraph in
  `installation.md:51` to say load balancing is
  operator-provided and link the new page; clarify the
  `api_url` expectation in the multi-node example (open
  questions 3, 4). Optionally ship the configs under
  `examples/` (open question 2). This phase adds no code
  changes and cannot break CI; it deliberately lands the
  documentation *before* the source vhost is deleted so the
  example is a faithful copy.
- **Phase 2 (medium effort, deletion).** Delete
  `roles/primary/tasks/apache2.yml` and
  `files/apache-site-primary.conf`. Remove the
  `role: primary` / `role_action: "apache2"` invocation from
  the `primary_node` play in `deploy.yml` (keep the
  `postinstall` invocation that follows it). Change the
  single-node / primary `api_url` default in `getsf` (both
  the `localhost` branch near line 1050 and the
  `GETSF_NODE_PRIMARY` branch near line 1059) from
  `http://127.0.0.1/api` to `http://127.0.0.1:13000`.
  Verify the cluster_ci deploy still goes green — it should,
  because the functional tests already export
  `SHAKENFIST_API_URL=http://localhost:13000` and never used
  the Apache `/api` path. Grep the tree for any remaining
  `apache` / `/api` assumptions in the deployer (e.g.
  `shakenfist.json`, `sfrc`, `getsf` comments) and the
  `installation.md` apache mention to make sure nothing still
  installs or references the bundled balancer. (The
  `apache2` install inside
  `cluster_ci_tests/test_floating_ips.py` is **not** in
  scope — that installs Apache *inside a test VM* as a web
  server for floating-IP testing, unrelated to the
  deployer's load balancer.)

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean and
avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort level
   and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's summary
   describes what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether
   the brief was insufficient (improve it) or the model was
   too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied with
   the result.

This applies to all steps. If a sub-agent can't succeed even
with a detailed brief and the right model, that's a signal
the brief needs improving, not that the management session
should do the implementation itself.

Both phases of this plan are low-risk deletions / docs and
can work directly in the main tree. Reach for
`isolation: "worktree"` only if a phase-2 sub-agent's CI
verification turns out to require experimental iteration.

### Planning effort

The master plan itself was created at **high effort**. Both
phase plans can be planned at **medium effort** — phase 1 is
documentation authoring with the source config in hand, and
phase 2 is a bounded deletion plus a one-line default change
whose blast radius (the `api_url` flow and the CI rig) is
already mapped in this master plan.

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**

- **high** — Requires reading multiple files, making judgment
  calls, understanding non-obvious invariants, or researching
  external references.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief.
- **low** — Purely mechanical changes (delete a file, remove
  a play stanza, change a string default).

**Model choice:**

- **opus** — Deep reasoning, cross-daemon architectural
  understanding, subtle correctness judgment.
- **sonnet** — Good default for well-briefed implementation
  work. Both phases here suit sonnet given the briefs in this
  plan.
- **haiku** — Purely mechanical tasks: file deletion,
  search-and-replace.

**When in doubt, skew to the more capable model.** Saving
money only matters if the outcome is still acceptable.

**Brief for sub-agent:** Write it as if briefing a colleague
who has never seen the codebase. Include what to change,
which files to touch, what patterns to follow, and any
non-obvious constraints. In particular, a phase-2 brief must
spell out that the `/api` prefix is an Apache artifact, that
single-node must therefore move to `:13000`, and that CI
already hits `:13000` directly so a green functional-tests
run is the acceptance signal.

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified (in particular, the
      `test_floating_ips.py` in-VM apache install is
      untouched).
- [ ] The code passes `pre-commit run --all-files` (flake8,
      stestr unit tests, mypy).
- [ ] The cluster_ci deploy still succeeds — this plan is
      operator-facing, and an internally-clean change that
      breaks the CI deploy is a regression.
- [ ] The example LB configs in docs actually proxy the same
      paths the deleted vhost did (`/api`, `/apidocs`,
      `/flasgger_static`, `/apispec_1.json`).
- [ ] Commit message follows project conventions (including
      the Co-Authored-By line with model, context window,
      effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (flake8,
  stestr unit tests, and mypy type checking).
* `roles/primary/tasks/apache2.yml` and
  `files/apache-site-primary.conf` no longer exist, and no
  part of the deployer installs or configures Apache.
* The `primary_node` play in `deploy.yml` no longer invokes
  the apache2 role action; `postinstall` still runs.
* `getsf` emits `http://127.0.0.1:13000` (not
  `http://127.0.0.1/api`) as the single-node / primary
  `api_url` default, and a single-node `getsf` deploy yields
  a working `sf-client` with no proxy installed.
* The cluster_ci functional tests still pass (they already
  target `http://localhost:13000` directly).
* `docs/operator_guide/` contains a load-balancing page with
  working example apache2 **and** nginx configurations that
  preserve the `/api`, `/apidocs`, `/flasgger_static`, and
  `/apispec_1.json` passthroughs and show where TLS
  termination plugs in.
* `installation.md` no longer claims the primary node runs an
  Apache load balancer; it describes load balancing as
  operator-provided and links the new page, and the
  single-node `:13000` escape hatch is documented.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` are updated
  if they reference the primary-node Apache load balancer.

### Future work

- **Drop the `/api` prefix convention.** Serving the API at
  `/` behind the operator's LB and retiring the `/api` prefix
  would simplify the URL story, but it is a compatibility
  break orthogonal to removing Apache and wants its own plan.
- **Health-check-aware load balancing.** The example LB
  configs can only become genuinely production-honest once SF
  exposes `/healthz` / readiness endpoints. Tracked in
  [`PLAN-health-checks.md`](PLAN-health-checks.md); once it
  lands, revisit the example configs to add active backend
  health checks.
- **TLS / mTLS between components.** The example configs
  terminate operator TLS at the LB; in-cluster TLS (gunicorn,
  gRPC) is tracked in `PLAN-embrace-tls.md`.
- **Update the parent plan.** Mark phase 3 of
  [`PLAN-remove-primary.md`](PLAN-remove-primary.md) ("Remove
  Apache reverse proxy from deployer") as realised by this
  plan, so the two do not describe overlapping work as both
  outstanding.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

### Documentation index maintenance

This plan has been registered in `docs/plans/`:

* **`index.md`** — a row added to the *Master plans* table
  linking this plan, its status, phase arithmetic and
  one-line intent.
* **`order.yml`** — an entry added next to
  `PLAN-remove-primary.md` so it appears in the documentation
  navigation. Phase files are *not* added to `order.yml`;
  they are linked from this plan's Execution table and from
  `index.md` only.

The site navigation in `mkdocs.yml` is produced from
`mkdocs.yml.tmpl` by the docs-sync workflow, which consumes
`order.yml`; it does not need hand-editing.

When all phases are complete, update the status column in
`docs/plans/index.md`.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
