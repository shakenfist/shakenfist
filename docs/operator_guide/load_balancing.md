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

This is why the `api_url` you give the installer ends in `/api`: that prefix is
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
HTTPS redirect, TLS configuration, and the blob-transfer directives) is in
`examples/apache-loadbalancer.conf`.

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
headers, upstream keepalive, a block that drops PHP vulnerability scanners, a
root catch-all so clients that omit the `/api` prefix still work, the bare
`/api` redirect, the HTTP-to-HTTPS redirect, TLS termination, and the
blob-transfer directives described below.

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
`http://localhost:13000`, with **no** `/api` prefix. `sf-api` serves the API at
`/` directly, so the prefix is neither added nor stripped in this mode.

## Health checks

Active backend health checking -- so the proxy stops sending traffic to a node
whose `sf-api` is down -- pairs naturally with the readiness endpoints
described in the [health checks plan](../plans/PLAN-health-checks.md). Once
those endpoints land, point your proxy's health probes at them rather than at a
generic API path.
