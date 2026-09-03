---
title: Load Balancing
---

# Load balancing the Shaken Fist API

## What Shaken Fist provides

Every hypervisor runs an `sf-api` worker, a gunicorn process listening on
port `13000` over plain HTTP and serving the API at `/`. Any node can answer
any request, so the API is horizontally scalable out of the box.

Shaken Fist does **not** ship a load balancer or reverse proxy. Putting one in
front of the API is operator-provided infrastructure: you choose the software,
own its configuration, and run it where it suits your network.

## Why you need one

A reverse proxy or load balancer in front of the `sf-api` workers lets you:

- spread API load across all of your hypervisors,
- present a single, stable external endpoint regardless of which nodes are up,
- terminate TLS in one place, and
- enforce your own perimeter policy (firewalling, a WAF, rate limiting, and
  request logging).

## The `/api` path convention

External clients talk to the load balancer at `<lb>/api/...`. The proxy strips
the `/api` prefix and forwards the request to a backend `sf-api` worker on
`:13000`, which serves the API at `/`. The OpenAPI documentation paths are an
exception -- the Swagger UI fetches them from the server root, so they are
passed through unchanged rather than living under `/api`.

This is why the `api_url` you give the deployer ends in `/api`: that prefix is
what the proxy expects and strips.

| External path | Backend path | Notes |
|---------------|--------------|-------|
| `/api/<anything>` | `/<anything>` | The `/api` prefix is stripped. |
| `/apidocs` | `/apidocs` | Passed through unchanged (Swagger UI). |
| `/flasgger_static` | `/flasgger_static` | Passed through unchanged (Swagger UI assets). |
| `/apispec_1.json` | `/apispec_1.json` | Passed through unchanged (OpenAPI spec). |

## Example configurations

Shaken Fist ships two example proxy configurations, one for Apache and one for
nginx. Both implement the proxy contract above, terminate TLS, and balance
across the per-hypervisor backends. They are starting points, not
prescriptions: you own the certificates, cipher policy, WAF, and logging.

The proxy terminates TLS and then connects to the `:13000` backends over plain
HTTP. Run that proxy-to-backend hop over your trusted cluster network (the same
network the node mesh uses), not a public segment — Shaken Fist assumes the
backend network is trusted, and securing the in-cluster hop is tracked
separately under embracing TLS across the cluster.

### Apache

Copy `examples/apache-loadbalancer.conf`, edit it for your environment, and
enable it:

```bash
sudo cp examples/apache-loadbalancer.conf \
    /etc/apache2/sites-available/shakenfist.conf
sudo a2ensite shakenfist
sudo systemctl reload apache2
```

The salient parts are the balancer pool listing every hypervisor and the
`ProxyPass` rules. The doc paths are listed **before** the catch-all `/api`
rule because Apache matches `ProxyPass` rules in order and the first match
wins:

```apache
<Proxy "balancer://sfapi">
    # List every hypervisor here, one BalancerMember per node.
    BalancerMember "http://10.0.0.1:13000"
    BalancerMember "http://10.0.0.2:13000"
</Proxy>

# Doc paths begin with the string "/api", so they must be listed before
# the catch-all "/api" rule, and are passed through unchanged.
ProxyPass        "/apidocs"         "balancer://sfapi/apidocs"
ProxyPass        "/flasgger_static" "balancer://sfapi/flasgger_static"
ProxyPass        "/apispec_1.json"  "balancer://sfapi/apispec_1.json"

# Strip the /api prefix: /api/auth/namespaces -> backend /auth/namespaces.
ProxyPass        "/api" "balancer://sfapi"
ProxyPassReverse "/api" "balancer://sfapi"
```

The full file (including the matching `ProxyPassReverse` rules, the HTTP-to-
HTTPS redirect, TLS configuration, `/readyz` health checking as described
under [Health checks](#health-checks), and the blob-transfer directives) is
in `examples/apache-loadbalancer.conf`.

### nginx

Copy `examples/nginx-loadbalancer.conf`, edit it for your environment, and
enable it:

```bash
sudo cp examples/nginx-loadbalancer.conf \
    /etc/nginx/sites-available/shakenfist.conf
sudo ln -s /etc/nginx/sites-available/shakenfist.conf \
    /etc/nginx/sites-enabled/
sudo systemctl reload nginx
```

The salient parts are the `upstream` block listing every hypervisor and the
`location` rules. The trailing slash on `proxy_pass` in the `/api/` location is
what strips the `/api/` prefix; the doc-path locations have no trailing slash,
so the request path is preserved unchanged:

```nginx
upstream sfapi {
    # List every hypervisor here, one server line per node.
    server 10.0.0.1:13000;
    server 10.0.0.2:13000;
}

location /api/ {
    # The trailing slash strips the /api/ prefix:
    # /api/auth/namespaces -> backend /auth/namespaces.
    proxy_pass http://sfapi/;
}

# Doc paths are served from the server root. No trailing slash on
# proxy_pass, so the request path is preserved unchanged.
location /apidocs {
    proxy_pass http://sfapi;
}
location /flasgger_static {
    proxy_pass http://sfapi;
}
location = /apispec_1.json {
    proxy_pass http://sfapi;
}
```

The full file in `examples/nginx-loadbalancer.conf` adds the rest of a
production-ready configuration: the `Host`, `X-Real-IP` and `X-Forwarded-*`
headers, upstream keepalive, passive health checking as described under
[Health checks](#health-checks), a block that drops PHP vulnerability
scanners, a root catch-all so clients that omit the `/api` prefix still work,
the bare `/api` redirect, the HTTP-to-HTTPS redirect, TLS termination, and
the blob-transfer directives described below.

## Blob transfers

Shaken Fist streams large disk images and blobs -- often many gigabytes --
through the API. Configure your proxy to allow large, unbuffered request
bodies with long timeouts, or transfers will be truncated or time out.

The example configurations show the relevant directives. nginx uses
`client_max_body_size 0` to lift the body-size limit, `proxy_request_buffering
off` and `proxy_buffering off` to stream rather than buffer, and
`proxy_read_timeout`/`proxy_send_timeout` of `3600s`. Apache uses
`LimitRequestBody 0` and a `ProxyTimeout` of `3600`.

## Single-node escape hatch

If you run everything on a single machine and do not want to operate a proxy at
all, you can skip the load balancer entirely. Point `api_url` (and the
`SHAKENFIST_API_URL` environment variable) straight at
`http://127.0.0.1:13000`, with **no** `/api` prefix. `sf-api` serves the API at
`/` directly, so the prefix is neither added nor stripped in this mode.

## Health checks

Your load balancer should actively health-check each `sf-api` backend so that
it stops routing traffic to a worker that is draining or that has lost its
connection to `sf-database`. `sf-api` exposes a dedicated readiness endpoint
for exactly this purpose.

### The probe endpoints

`sf-api` exposes three **unauthenticated** HTTP endpoints on port `13000`:

| Endpoint | Meaning | Who probes it |
|----------|---------|---------------|
| `GET /livez` | Liveness. Always returns `200 ok` while the worker process is serving. | An orchestrator or `systemctl` -- **not** the load balancer. |
| `GET /readyz` | Readiness. Returns `200 ready` when the worker should receive traffic, or `503 not ready` when the worker is draining or when `sf-database` is unreachable. | **The load balancer.** |
| `GET /healthz` | Alias of `/readyz`. | The load balancer (use either). |

Point your load balancer's health probe at **`/readyz`** (or, equivalently,
`/healthz`). Do not health-check `/livez` from the load balancer: a draining
worker still answers `/livez` with `200`, so a `/livez`-based pool would keep
sending it traffic right up until the process exits.

The probe is cheap. Each worker evaluates readiness in a background checker
thread and serves `/readyz` from a cached flag, so a probe never hits the
database directly. Frequent probing is therefore fine -- and recommended,
because of the drain timing described next.

### Tune the probe to beat the drain grace period

On `SIGTERM` (a normal stop or restart) the worker flips `/readyz` to `503`
**first**, then keeps serving live requests for `API_DRAIN_GRACE` seconds
(default `25`) before it actually shuts down. This ordering exists so the load
balancer has a window in which to notice the `503` and drain the node before
any request is dropped.

For that to work, your probe must detect the `503` **comfortably inside** that
~25 second window. The relevant figure is `interval x unhealthy-threshold`: if
it approaches or exceeds 25 s, the worker will exit before the load balancer
marks it unhealthy, and in-flight requests will fail.

A safe, conservative starting point is a **5 second interval with a 2-failure
threshold**: the load balancer sees the `503` within ~10 s, well inside the
25 s grace. The cheap, cached probe makes this frequency painless.

### Route only to sf-api, and firewall the port

The load balancer pool contains **only `sf-api` backends** on `:13000`.
`sf-database` is a separate, internal service; it is health-checked separately
by your monitoring with `grpc-health-probe`, not by this load balancer -- see
[Monitoring sf-database with grpc-health-probe](database.md#monitoring-sf-database-with-grpc-health-probe).
Do not put `sf-database` in the API pool.

The health endpoints are unauthenticated, like the rest of the `:13000`
surface. Firewall port `13000` so it is reachable only from the load balancer
subnet -- this is the same perimeter posture the API already requires.

### TLS and the probe leg

The load balancer terminates the edge (public) certificate. The leg from the
load balancer back to `sf-api:13000` is plain HTTP, or you may re-encrypt to a
backend CA -- that is your choice (see the proxy-to-backend note under
[Example configurations](#example-configurations)). The health probe rides
that same backend leg, so configure the probe with whichever scheme (HTTP or
HTTPS) you use for the backend traffic itself.

### Example: Apache

Apache health-checks balancer members with `mod_proxy_hcheck` (which needs
`mod_watchdog`):

```bash
sudo a2enmod proxy_hcheck watchdog
```

Then add the health-check parameters to each `BalancerMember`:

```apache
<Proxy "balancer://sfapi">
    # hcmethod=GET hcuri=/readyz: probe /readyz. hcinterval=5 hcfails=2:
    # mark the worker down after 2 failed probes (~10s, well inside the
    # 25s drain grace). hcpasses=2: restore after 2 successes.
    BalancerMember "http://10.0.0.1:13000" hcmethod=GET hcuri=/readyz hcinterval=5 hcpasses=2 hcfails=2
    BalancerMember "http://10.0.0.2:13000" hcmethod=GET hcuri=/readyz hcinterval=5 hcpasses=2 hcfails=2
</Proxy>
```

`mod_proxy_hcheck` treats any status of `400` or higher as a failed probe,
so a `503 not ready` from a draining or database-isolated worker takes the
member out of rotation after two probes. This is what
`examples/apache-loadbalancer.conf` ships.

### Example: HAProxy

HAProxy has first-class active HTTP health checks. Use `option httpchk` to
define the probe and `http-check expect status 200` to require a `200`, then
enable per-server checking with `check inter 5s fall 2 rise 2`:

```haproxy
backend sfapi
    option httpchk GET /readyz
    http-check expect status 200
    # check inter 5s fall 2 rise 2: probe every 5s, mark down after 2
    # consecutive failures (~10s, well inside the 25s drain grace),
    # restore after 2 successes.
    server sfapi1 10.0.0.1:13000 check inter 5s fall 2 rise 2
    server sfapi2 10.0.0.2:13000 check inter 5s fall 2 rise 2
```

A `503 not ready` from a draining or database-isolated worker fails the
`expect status 200` check, and HAProxy drains it after two probes.

### Example: nginx (open source)

Open-source (FOSS) nginx does **not** have active health checks. The
`health_check` directive exists only in NGINX Plus, the commercial product.
FOSS nginx can only do **passive** health checking: it marks a backend down
after real client requests to it fail, and it cannot actively poll `/readyz`
on its own.

The passive approach uses `max_fails` and `fail_timeout` on the `upstream`
servers, plus `proxy_next_upstream` so a `503` is retried against another
backend rather than returned to the client:

```nginx
upstream sfapi {
    # Passive health checking: after 2 failed requests, take the backend
    # out of rotation for 10s, then probe it again with live traffic.
    server 10.0.0.1:13000 max_fails=2 fail_timeout=10s;
    server 10.0.0.2:13000 max_fails=2 fail_timeout=10s;
}

location /api/ {
    proxy_pass http://sfapi/;
    # Treat a 503 (and connection errors/timeouts) as "try the next
    # backend" instead of returning it to the client.
    proxy_next_upstream error timeout http_503;
}
```

This is what `examples/nginx-loadbalancer.conf` ships (with
`proxy_next_upstream` at the `server` level so it covers every location).

Be aware of the limitation: because this is passive, FOSS nginx only learns a
worker is draining when a real request happens to hit it and fail. It will not
pull a node out of rotation purely because `/readyz` returns `503`. And the
passive approach has a floor: `proxy_next_upstream` can only re-route a failed
request while some *other* backend in the pool is healthy. A deploy that
restarts every worker at once leaves nothing to fail over to, and in that case
a client-side retry is the only remaining defence.

If you need true active `/readyz` polling with nginx, you must either run
**NGINX Plus** (which has the `health_check` directive), or run an **external
prober** -- a small sidecar that polls each worker's `/readyz` and pulls a node
out (for example by editing the upstream config and reloading, or via your
deployment's API) when it sees a `503`.

### Example: AWS Application Load Balancer (ALB)

A cloud load balancer makes this straightforward, because active HTTP health
checks are built in. Create a target group for the `sf-api` workers and
configure its health check as:

| Setting | Value |
|---------|-------|
| Protocol | `HTTP` |
| Path | `/readyz` |
| Success codes | `200` |
| Healthy threshold | `2` |
| Unhealthy threshold | `2` |
| Interval | `5` seconds |
| Timeout | `2` seconds |

The HTTPS listener terminates TLS with an ACM certificate and forwards to the
targets on port `13000`. The unhealthy threshold of `2` at a `5` second
interval means the ALB drains a worker ~10 s after it starts returning `503`,
well inside the 25 s drain grace.

In Terraform:

```hcl
resource "aws_lb_target_group" "sfapi" {
  name     = "sfapi"
  port     = 13000
  protocol = "HTTP"
  vpc_id   = var.vpc_id

  health_check {
    protocol            = "HTTP"
    path                = "/readyz"
    matcher             = "200"
    interval            = 5
    timeout             = 2
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}
```
