# Phase 1 — Document operator-provided load balancing

Parent plan: [`PLAN-remove-apache-lb.md`](PLAN-remove-apache-lb.md).

This is the **docs-first** phase. It adds documentation and
copyable example configurations for operator-provided load
balancing *before* phase 2 deletes the deployer's bundled
Apache. Capturing the soon-to-be-deleted vhost as a worked
example here means no knowledge is lost in the gap between
the two phases. This phase changes **no code** and cannot
break CI.

## Recommended planning effort

This phase was planned at **medium effort** — it is
documentation authoring with the source vhost in hand, plus
two example config files whose correctness hinges on a small,
fully-specified set of proxy path mappings (captured below).

## What exists today

The deployer fronts the cluster API with Apache, configured by
`shakenfist/deploy/ansible/files/apache-site-primary.conf`
(jinja-templated). That vhost is the only existing
description of how SF expects to be reverse-proxied, so it is
the source material for the examples. Its behaviour:

- Listens on `*:80` (no TLS).
- Defines `balancer://sfapi` whose members are each
  hypervisor's `node_mesh_ip:13000` (the gunicorn API
  workers, which serve the API at `/`).
- Proxies four external path families to the balancer.

`docs/operator_guide/installation.md:51` tells operators "the
primary node runs an apache load balancer". `ARCHITECTURE.md:661`
shows "Apache (reverse proxy, adds /api/ prefix)" in the API
flow diagram. `CLAUDE.md:330-332` describes the `/api` prefix
as added by "the Apache reverse proxy configuration". All
three describe the deployer-bundled Apache and must be
re-pointed at the operator-provided model.

`examples/` already holds operator-copyable artefacts
(`grafana-dashboard.json`, `mariadb-tuning.cnf`) that are
referenced from docs as `examples/<name>` and carry a
comment-header explaining their purpose (see the top of
`examples/mariadb-tuning.cnf` for the house style). The
operator-guide navigation is hand-maintained inline in
`mkdocs.yml.tmpl` (lines 126-138); `mkdocs.yml` is regenerated
from the template by the docs-sync workflow and must **not**
be hand-edited.

## The proxy contract (front-loaded so the configs are correct)

Both example configs must reproduce exactly this external
surface. Get this wrong and the OpenAPI UI or the API itself
breaks, so it is specified here in full:

| External path | Backend path | Notes |
|---------------|--------------|-------|
| `/api/<anything>` | `/<anything>` | The `/api` prefix is **stripped**. `/api/auth/namespaces` → backend `/auth/namespaces`. This is why `api_url` ends in `/api`. |
| `/apidocs` | `/apidocs` | Passed through **unchanged**. The Swagger UI is served here. |
| `/flasgger_static` | `/flasgger_static` | Passed through unchanged. Swagger UI static assets. |
| `/apispec_1.json` | `/apispec_1.json` | Passed through unchanged. The OpenAPI spec the UI fetches. |

The doc paths cannot live under `/api` because the Swagger UI
fetches `/apispec_1.json` and `/flasgger_static/...` from the
server root; that is why the original vhost lists them
separately from the `/api` rule.

Two additional real-world requirements the original port-80
vhost did **not** address, but a production example must,
because SF streams disk images and blobs through the API:

- **Large bodies.** Blob uploads/downloads can be many GB.
  The example must lift body-size limits
  (`client_max_body_size 0;` in nginx; `LimitRequestBody 0`
  in Apache) so large transfers are not rejected.
- **Streaming, not buffering.** SF's receiving node streams
  blob bytes through without staging them. The example
  should disable request/response buffering on the API
  location (`proxy_request_buffering off; proxy_buffering
  off;` in nginx) and note the equivalent Apache
  consideration, and raise proxy timeouts for long
  transfers.

And the thing the original vhost lacked that every real
operator needs: **TLS termination at the load balancer**, with
an HTTP→HTTPS redirect. The examples terminate TLS with
placeholder cert paths and proxy cleartext to the `:13000`
backends over the trusted cluster mesh (in-cluster TLS is a
separate concern tracked in `PLAN-embrace-tls.md`).

## Deliverables

1. `examples/apache-loadbalancer.conf` — a de-jinja'd,
   operator-editable Apache config implementing the contract
   above, with TLS.
2. `examples/nginx-loadbalancer.conf` — the nginx equivalent,
   matching the shape of the one known production deployment.
3. `docs/operator_guide/load_balancing.md` — a new page
   explaining the model and snippeting the two examples.
4. A nav entry for the new page in `mkdocs.yml.tmpl`.
5. Updated cross-references in `installation.md`,
   `ARCHITECTURE.md`, and `CLAUDE.md`.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | **Create the two example LB config files.** Both go in `examples/` and both carry a comment header in the house style of `examples/mariadb-tuning.cnf` (read it first): one or two sentences saying this is an *optional, operator-owned* example reverse-proxy / load-balancer config for the Shaken Fist API, that operators copy and edit it (replace backend IPs, cert paths, server name), and that it is a starting point, not a prescription. **`examples/apache-loadbalancer.conf`:** derive from `shakenfist/deploy/ansible/files/apache-site-primary.conf` (read it) but remove all jinja — write a concrete config an operator edits by hand. Provide a `<VirtualHost *:80>` that redirects all traffic to https (`Redirect permanent / https://sf.example.com/`), and a `<VirtualHost *:443>` with `SSLEngine on` and placeholder `SSLCertificateFile`/`SSLCertificateKeyFile` paths. Inside the 443 vhost reproduce the proxy contract from this plan's "proxy contract" table exactly: a `<Proxy balancer://sfapi>` block with two illustrative `BalancerMember "http://10.0.0.1:13000"` / `10.0.0.2:13000` lines (comment that operators list every hypervisor here), then `ProxyPass "/api" "balancer://sfapi"` + `ProxyPassReverse` (prefix-stripping), and separate unchanged-passthrough `ProxyPass`/`ProxyPassReverse` rules for `/apidocs`, `/flasgger_static`, `/apispec_1.json` (each → `balancer://sfapi/<same path>`). Add `LimitRequestBody 0` and a comment about large blob transfers and raising `ProxyTimeout`. Add a top comment listing the required modules: `a2enmod proxy proxy_http proxy_balancer lbmethod_byrequests ssl headers`. **`examples/nginx-loadbalancer.conf`:** an `upstream sfapi { server 10.0.0.1:13000; server 10.0.0.2:13000; }` block (comment: one `server` line per hypervisor); a `server { listen 80; ... return 301 https://$host$request_uri; }` redirect block; and a `server { listen 443 ssl; ... }` block with placeholder `ssl_certificate`/`ssl_certificate_key`, `server_name sf.example.com;`, `client_max_body_size 0;`, and these locations honouring the contract: `location /api/ { proxy_pass http://sfapi/; ... }` (the **trailing slash on `proxy_pass` is load-bearing** — it strips the `/api/` prefix; add a comment saying so), plus `location = /api { return 301 /api/; }` so a bare `/api` still works, plus `location /apidocs { proxy_pass http://sfapi; }`, `location /flasgger_static { proxy_pass http://sfapi; }`, `location = /apispec_1.json { proxy_pass http://sfapi; }` (no trailing slash → path preserved). In the `/api/` location set `proxy_set_header Host $host;`, `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`, `proxy_set_header X-Forwarded-Proto $scheme;`, `proxy_request_buffering off;`, `proxy_buffering off;`, and generous `proxy_read_timeout`/`proxy_send_timeout` (e.g. 3600s) with a comment that this is for large blob streaming. Do not invent SF features; only reproduce the four path families from the existing vhost plus TLS/size/buffering. `pre-commit run --files examples/apache-loadbalancer.conf examples/nginx-loadbalancer.conf`. One commit. |
| 2 | medium | sonnet | none | **Write `docs/operator_guide/load_balancing.md` and add it to the nav.** New page, front-matter `--- title: Load Balancing ---` then `# Load balancing the Shaken Fist API`. Cover, in order: (a) **What SF provides** — each hypervisor runs `sf-api` (gunicorn) listening on `:13000`, plain HTTP, serving the API at `/`; SF does **not** ship a load balancer (as of this change) — that is operator-provided infrastructure. (b) **Why you need one** — spread API load across hypervisors, present one stable endpoint, terminate TLS, enforce your perimeter/WAF. (c) **The `/api` path convention** — external clients call `<lb>/api/...`; the LB strips `/api` and forwards to a backend's `:13000` at `/`; the OpenAPI doc paths `/apidocs`, `/flasgger_static`, `/apispec_1.json` are passed through unchanged; this is why the `api_url` you give the installer ends in `/api`. Reproduce the proxy-contract table from this phase plan. (d) **Example configurations** — introduce both example files, referenced as `examples/apache-loadbalancer.conf` and `examples/nginx-loadbalancer.conf` (link them the way `docs/operator_guide/database.md` references `examples/mariadb-tuning.cnf`), and snippet the salient lines of each in fenced ```apache / ```nginx blocks. State they are starting points; operators own certs, cipher policy, WAF, and logging. (e) **Blob transfers** — SF streams large disk images and blobs through the API, so configure the LB to allow large, unbuffered bodies and long timeouts; the example configs show the directives. (f) **Single-node escape hatch** — if you run everything on one machine and don't want a proxy, point `api_url` (and `SHAKENFIST_API_URL`) straight at `http://localhost:13000` with no `/api` prefix; sf-api serves the API there directly. (g) **Health checks** — a short forward-looking note that active backend health checking pairs with the readiness endpoints planned in `PLAN-health-checks.md` (link it relatively as `../plans/PLAN-health-checks.md`). Then add the nav entry to `mkdocs.yml.tmpl`: insert `       - "Load Balancing": operator_guide/load_balancing.md` between the "Exception Tracking" line and the "Locks" line (lines ~132-133), matching the 7-space indentation of its neighbours. Do **not** edit `mkdocs.yml` by hand — it is regenerated from the template by the docs-sync workflow (see CLAUDE.md). `pre-commit run --files docs/operator_guide/load_balancing.md mkdocs.yml.tmpl`. One commit. |
| 3 | medium | sonnet | none | **Re-point the existing cross-references at the operator-provided model.** Three files. (a) `docs/operator_guide/installation.md:51`: replace the bullet "The primary node runs an apache load balancer across the API servers in the cluster, and therefore needs to be accessable to your users on HTTP and HTTPS." with text stating that Shaken Fist does not install a load balancer — the operator places their own reverse proxy / load balancer in front of the cluster's `sf-api` daemons (which listen on `:13000`), and linking the new page (`load_balancing.md`). Also add a sentence near the multi-node topology example (around line 79, where `"api_url": "https://...your...install...here.com/api"` appears) clarifying that this `api_url` is the address of the operator's own load balancer and must proxy to the cluster as described on the Load Balancing page. Do not change the `api_url` *value* in the example. (b) `ARCHITECTURE.md:661`: change the API-flow diagram line `Apache (reverse proxy, adds /api/ prefix)` to `Operator-provided load balancer / reverse proxy (adds /api/ prefix)`. (c) `CLAUDE.md:330-332`: change "**When talking through Apache** (standard external access): The `/api/` prefix is added by the Apache reverse proxy configuration" to refer to the operator-provided reverse proxy rather than a bundled Apache (keep the `/api/` example and the gunicorn-direct contrast intact). Keep all edits minimal and factual; do not restructure the surrounding docs. `pre-commit run --files docs/operator_guide/installation.md ARCHITECTURE.md CLAUDE.md`. One commit. |

## Ordering note

Step 1 must land before step 2, because the page references
the example files; a doc page pointing at files that do not
exist yet would not be self-contained. Step 3 is independent
of 1 and 2 but is cheapest to review last, once the new page
exists to link to.

## Management session review checklist (phase-specific)

- [ ] The example configs reproduce the proxy contract table
      exactly — `/api` prefix stripped, the three doc paths
      passed through unchanged. Mentally trace
      `/api/auth/namespaces` → `/auth/namespaces` and
      `/apispec_1.json` → `/apispec_1.json` through each
      config.
- [ ] The nginx `/api/` location's `proxy_pass` has its
      trailing slash (prefix-stripping); the doc-path
      locations do not.
- [ ] Both configs lift body-size limits and disable
      buffering on the API path, and terminate TLS with an
      HTTP→HTTPS redirect.
- [ ] `mkdocs.yml.tmpl` has the new nav entry at the right
      indentation and alphabetical position;
      `mkdocs.yml` was **not** hand-edited.
- [ ] No code under `shakenfist/` changed — this is a
      docs-only phase. In particular the deployer's
      `apache2.yml` and `apache-site-primary.conf` are still
      present (their deletion is phase 2).
- [ ] `pre-commit run --all-files` is clean.
- [ ] Each of the three commits is self-contained and
      follows the project commit-message conventions
      (Signed-off-by, Prompt paragraph, Co-Authored-By with
      model / context / effort).

## Success criteria for this phase

* `examples/apache-loadbalancer.conf` and
  `examples/nginx-loadbalancer.conf` exist, are valid config
  for their respective servers, and implement the proxy
  contract with TLS, large-body, and streaming support.
* `docs/operator_guide/load_balancing.md` exists, is in the
  operator-guide nav, and documents the model, the examples,
  the blob-streaming requirement, and the single-node
  `:13000` escape hatch.
* `installation.md`, `ARCHITECTURE.md`, and `CLAUDE.md` no
  longer describe the load balancer as a deployer-installed
  Apache; they describe it as operator-provided and link the
  new page where appropriate.
* No code changed; the bundled Apache is still installed by
  the deployer (phase 2 removes it).
* `pre-commit run --all-files` passes.

## Hand-off to phase 2

Once this phase lands, phase 2 can delete
`apache2.yml` and `apache-site-primary.conf`, drop the
`role_action: "apache2"` invocation from `deploy.yml`, and
change the `getsf` single-node `api_url` default to
`http://127.0.0.1:13000` — with the operator-facing
documentation already in place to catch anyone who needs a
load balancer.
