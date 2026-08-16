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

With a Loki endpoint set, each daemon buffers its `INFO`-and-above
JSON log lines in a local on-disk spool and a background drainer
ships them to Loki, with retry and backpressure. The spool is the
local-durability buffer that covers transient Loki outages. All
levels (including `DEBUG`) continue to be written to the node's
local journal as well, so on-box `journalctl` debugging is
unaffected.

### Log levels: what ships to Loki

**Only `INFO` and above is shipped to Loki. `DEBUG` stays local
(journald only).** This matches the previous rsyslog deployment,
whose forwarder shipped `*.*;*.!=debug` — DEBUG was never
centrally aggregated, only kept on each node. It also keeps the
highest-volume log level (DEBUG; e.g. every privileged command is
logged at DEBUG) off the spool/push path, which matters for
performance on busy nodes. When you need DEBUG to diagnose a
problem, read it on the node with `journalctl`.

A future iteration may revisit shipping deeper detail centrally
once Shaken Fist has OpenTelemetry-based tracing (see the
development plans).

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

Set these via the deployer (the `loki_base_url`, `loki_tenant` and
`loki_auth_header` variables of the `shakenfist.shakenfist`
collection) or directly as `SHAKENFIST_*` config:

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

## API request validation findings

After upgrading you may see `API request validation finding` lines
from `sf-api` in your log stream. These are **informational**: they
record a request which did not match its endpoint's published
parameter declarations, and while `API_VALIDATION_MODE` is `warn`
(the default) they change no response — the request was answered
exactly as it always was. Do not set `enforce` yet; it only makes
sense once the warn log for your own callers has been read and
understood. See
[PLAN-api-input-validation](../plans/PLAN-api-input-validation.md). If
the lines themselves become a problem — a chatty caller sending an
undeclared key on every request is an extra line per request —
`API_VALIDATION_MODE` can be set to `off`, which disables the layer
entirely.

Each line carries:

| Field | Meaning |
|---|---|
| `validation-reason` | Which rule the request missed: `unknown-parameter` (a body key the endpoint does not declare), `type-mismatch` (a declared parameter with a value of the wrong type), `missing-required` (a declared-required parameter that was not supplied), or `body-path-collision` (a body key with the same name as a URL path parameter). |
| `validation-parameter` | The parameter concerned, truncated to 64 characters. |
| `validation-detail` | The specific rule that was missed — for a `type-mismatch`, the validation message (for example `Not a valid integer.`). |
| `validation-value-type` | The Python type of the offending value. The value itself is never logged. |
| `route` | The route template (for example `/instances/<instance_ref>`), so findings aggregate by endpoint; `path` carries the concrete request path. |
| `validation-response-status` | The status the request went on to return anyway, which is what separates a rejection enforcement would introduce from a status code it would merely change. |
| `validation-mode` | The active `API_VALIDATION_MODE`. |

The reason codes and the measurement they feed are described in
`docs/plans/PLAN-api-input-validation-phase-03-compile-and-warn.md`.

## Secrets in the log stream

Shaken Fist treats a credential reaching the log stream as a bug.
The configuration options which carry secrets are typed so that
rendering one into a log line produces asterisks rather than the
value, and the two places which dump every configuration option
additionally redact by option name — so an option added later is
covered before anyone remembers to think about it.

That was not always true. Releases before v0.8.0 wrote
`AUTH_SECRET_SEED`, `MARIADB_PASSWORD` and `LOKI_AUTH_HEADER` into
the log stream in plaintext on every `sf-queues` start. If you are
upgrading an existing cluster, see
[Credential rotation](credential_rotation.md#credentials-disclosed-before-v080)
for how to confirm the exposure and what to do about it.

The standing consequence for how you run your log store: **treat
it as sensitive**. Even with no credentials in it, it carries
namespace names, instance metadata and request paths, and its
access controls should reflect that.

### Detecting a leaked credential

Prevention is not detection, and the interesting leak is the one
nobody predicted. Key secrets the cluster mints for itself are
therefore shaped to be found: they begin with `sfk_` and end with
a checksum, which is what makes them greppable in a way a random
string is not. The format is described in the
[user guide](../user_guide/authentication.md).

To check a cluster once, ask Loki for anything of that shape:

```logql
{job="shakenfist"} |~ "sfk_[A-Za-z0-9]{38}"
```

Match on the shape rather than on `sfk_` alone. Log lines
legitimately mention the prefix — key creation refuses an
operator-supplied secret that carries it, and says so in the
error — and a check which fires on its own documentation is one
you learn to ignore.

To check continuously, install
[`examples/loki-secret-alert.yaml`](https://github.com/shakenfist/shakenfist/blob/develop/examples/loki-secret-alert.yaml)
into your Loki ruler. The file carries its own installation
instructions, including the tenant trap: a rule filed under a
tenant other than your `loki_tenant` loads without error and
never fires, which is indistinguishable from having nothing to
report.

On a cluster which is not shipping to Loki, the same check
against the local journal on each node is:

```bash
journalctl -u 'sf-*' --no-pager | grep -E 'sfk_[A-Za-z0-9]{38}'
```

That is per-node and only covers the journal's retention, which
is why it is the fallback rather than the recommendation.

If any of these finds something, the credential is disclosed to
everyone with read access to your log store — treat it that way
even if the match is old, and do not copy the matched line into a
ticket or a chat channel, which only spreads it further. Rotation
procedures and their blast radius are in
[Credential rotation](credential_rotation.md).

Shaken Fist's own functional CI runs this same query against a
live cluster on every run, and fails the build if it matches. It
is worth knowing how that test is built, because the shape
generalises: it emits a token of the credential shape first and
requires the query to find *that* before it will assert that
nothing else matched. A detector which has never been observed
firing is not evidence of anything.

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

## Log shipping internals

Daemons log structured JSON via `shakenfist_utilities.logs` (one JSON object
per line; this is the only daemon log format). Shaken Fist does not aggregate
logs onto a primary node. Instead, when a Loki endpoint is configured
(`LOKI_BASE_URL`), each daemon ships its own logs to that operator-provided
Loki through an in-process, on-disk-spooled, batched HTTP push modelled
directly on the eventlog spool/drainer:

- `shakenfist/logship_spool.py` — a per-daemon disk-backed sqlite spool under
  `/srv/shakenfist/spool/logship/<daemon>-<pid>.db` (the durability boundary;
  drop-and-count over a high-water mark; orphan recovery).
- `shakenfist/logship_drainer.py` — a background thread that batches spooled
  lines and POSTs them to Loki's `/loki/api/v1/push` with exponential backoff,
  retaining failed batches for retry.
- `shakenfist/logship.py` — a `logging.Handler` that JSON-formats each record
  into the spool, plus `start()`, which (in Loki mode) attaches the handler to
  the root logger and removes the library's per-module syslog handlers so logs
  go to Loki only.

When no Loki endpoint is configured the daemons log to the local systemd
journal instead. Loki stream labels are bounded to `{job, daemon, host}`; all
identifiers stay in the JSON body — see
[Labels and the field contract](#labels-and-the-field-contract) for the
full contract.
