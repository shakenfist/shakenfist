# Logging

Shaken Fist daemons emit **structured JSON logs** and can ship
them to an operator-provided [Loki](https://grafana.com/oss/loki/)
log store. Shaken Fist does not run a log store, and it no longer
aggregates logs onto a primary node: each node either ships its
own logs to your Loki, or — if you have not configured one —
writes them to the local systemd journal.

## What changed

Earlier releases forwarded every node's syslog to a primary node
over rsyslog, where logs landed in `/var/log/syslog`. That
central aggregator has been removed. In its place:

- Daemon logs are **structured JSON** (one JSON object per line),
  not plain text. This is the only daemon log format.
- If you tell Shaken Fist where your Loki is, each node ships its
  own logs there directly, buffered through a local on-disk spool.
- If you do not, each node logs to its local journal and ships
  nothing.

Local journald still captures each node's logs regardless, so
on-box debugging with `journalctl` always works.

## The two modes

Shaken Fist's logging has exactly two modes, keyed on whether
`LOKI_BASE_URL` is set. The log **format** (structured JSON) is
identical in both; only the **destination** differs.

### Loki configured (the preferred path)

With a Loki endpoint set, each daemon buffers its JSON log lines
in a local on-disk spool and a background drainer ships them to
Loki, with retry and backpressure. It does **not** also write
them to a local syslog/file sink — there is no deliberate second
pipeline. The spool is the local-durability buffer that covers
transient Loki outages.

### Loki not configured (the fallback)

Leaving `LOKI_BASE_URL` empty means each daemon emits the same
JSON to the node's local journal (journald) and ships nothing. A
node-local agent such as [promtail](https://grafana.com/docs/loki/latest/send-data/promtail/),
[Grafana Alloy](https://grafana.com/docs/alloy/latest/), or
vector can then scrape that journal if you want central logs
collected on your own terms.

In both modes, systemd additionally captures each service's
stdout/stderr into journald (uncaught tracebacks and any output
emitted before logging is configured). That is free, local-only,
and not a deliberate shipping pipeline.

## Configuration

Set these via the deployer (`getsf` prompts / `GETSF_LOKI_*`
environment variables) or directly as `SHAKENFIST_*` config:

| Option | Default | Meaning |
|--------|---------|---------|
| `LOKI_BASE_URL` | `''` | Base URL of your Loki, e.g. `http://loki:3100`. Empty disables the shipper. |
| `LOKI_TENANT` | `''` | Value sent as the `X-Scope-OrgID` header for multi-tenant Loki. Empty omits the header. |
| `LOKI_AUTH_HEADER` | `''` | Opaque `Authorization` header value (e.g. `Bearer <token>`). Empty sends none. Treat as a secret. |
| `LOG_EVENTS_TO_LOKI` | `True` | Whether the per-event `Added event` log line is emitted to the log stream. Never affects the authoritative MariaDB event write (see [Events vs logs](#events-vs-logs)). |

### Tenant

`LOKI_TENANT` is worth setting deliberately when you run Shaken
Fist against a shared Loki. Shaken Fist's logs can be high volume,
and a dedicated tenant keeps them out of your other tenants'
streams — both for query hygiene and so per-tenant volume limits
and retention can be set independently.

### TLS and mutual auth

This release supports a plain-HTTP or operator-terminated-HTTPS
Loki endpoint plus the optional opaque auth header above. mTLS to
the Loki endpoint (operator-provided CA, client certificates) is
tracked separately and is not yet available.

## Labels and the field contract

Shaken Fist tags every Loki stream with a small, bounded set of
labels:

```
{job="shakenfist", daemon="<sf-daemon>", host="<node>"}
```

Everything else — including high-cardinality identifiers such as
`instance_uuid`, `network_uuid`, and `request-id` — lives in the
JSON log line **body**, never in a label. This is deliberate:
promoting high-cardinality values to Loki labels causes a
label-cardinality explosion. Query them with LogQL's JSON parser
instead:

```logql
{job="shakenfist"} | json | instance_uuid="<uuid>"
{job="shakenfist", daemon="sf-net"} | json | level="ERROR"
```

The full set of JSON field names (the base fields plus the
`with_fields` conventions) is documented as a stable contract in
the `shakenfist_utilities` library, in
[`docs/log-record-fields.md`](https://github.com/shakenfist/library-utilities/blob/develop/docs/log-record-fields.md).

## Buffering, backpressure and durability

The shipper is modelled on Shaken Fist's eventlog spool/drainer
(see [Events](events.md)). Each daemon process holds its own
disk-backed sqlite spool at:

```
/srv/shakenfist/spool/logship/<daemon>-<pid>.db
```

- **Durability boundary.** A log call enqueues one cheap sqlite
  insert and returns; the line is on disk and survives a process
  crash. On startup the spool recovers orphan files left by
  previously-dead PIDs.
- **Backpressure.** A background drainer thread batches lines and
  POSTs them to Loki's `/loki/api/v1/push`. On failure the batch
  stays in the spool and is retried with exponential backoff; a
  brief Loki outage loses nothing.
- **Drop, don't block.** If the spool exceeds its high-water mark
  (a sustained outage), new lines are dropped — with a counter
  increment and a warning — rather than blocking the daemon. Only
  the Loki copy is ever lost; the node still has journald.
- **Clean shutdown.** On process exit the drainer flushes the
  spool synchronously, within a bounded timeout.

Each daemon exposes Prometheus metrics for this on its existing
metrics endpoint:

| Metric | Meaning |
|--------|---------|
| `logship_spool_depth` | Lines currently pending in the local spool. |
| `logship_spool_dropped_total` | Lines dropped at the high-water mark. |
| `logship_push_total{result=...}` | Loki push attempts by outcome. |
| `logship_push_seconds` | Loki push request latency. |

## Events vs logs

Shaken Fist has two structured-record streams, and they are not
the same thing:

- **Events** are the authoritative, per-object record (instance,
  network, blob, …), stored in MariaDB and read back through the
  REST API. They back billing, owner/admin audit, and per-object
  progress. See [Events](events.md).
- **Logs** are the operational, interleaved-with-everything view
  for debugging "what was this node doing at 14:03", shipped to
  Loki (or journald).

By convention every event also emits an `Added event` log line,
so events show up in your log stream too — giving a single
time-ordered pane of events and logs together. That echo is
controlled by `LOG_EVENTS_TO_LOKI` (default on); turning it off
keeps events authoritative in MariaDB while reducing Loki volume.
Event **storage** always stays in MariaDB — it is never moved to
Loki.

If you run with `LOG_EVENTS_TO_LOKI` off but still want a single
pane, put a Loki logs panel and a MariaDB events panel on one
time-aligned Grafana dashboard.

## Using your own log agent instead

If you already run a node-local log agent (promtail, Grafana
Alloy, vector, …), you can leave `LOKI_BASE_URL` unset and have
your agent scrape each node's journal. Shaken Fist's logs are
structured JSON there too, so your agent can parse and label them
however your pipeline prefers.
