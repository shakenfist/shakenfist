# Capacity refusals

When the scheduler cannot place an instance because the cluster is full, the
`POST /instances` request fails with an HTTP `507 Insufficient Storage` status.
This document describes the response format and what it means.

## Which refusals are transient

Two of the four `507` responses from instance creation are marked as transient
and worth retrying. The other two are not.

**Transient refusals** (marked with `transient: true`):

1. **Filter-stage refusal** -- a scheduler filter stage (such as `sufficient_idle_cpu`,
   `sufficient_idle_memory`, or `sufficient_free_disk`) eliminated every candidate.
   The cluster does not have enough of a resource type right now, but an instance
   being deleted will free it within seconds.

2. **Guard-stage refusal** -- the capacity admission guard refused every candidate.
   The cluster's accounting shows no room for the placement, but a concurrent delete
   or a capacity reconciler pass can change that within seconds.

**Non-transient refusals** (no `transient` field, or `transient: false`):

- **Address-pool exhaustion** (`CongestedNetwork`) -- the virtual network's address
  pool is exhausted. No amount of waiting on the ten-second horizon will help; the
  pool recovers only after the deletion halo expires, which is much longer and
  differently-shaped than an instance teardown. This is a problem to solve by
  adding more address space or freeing a network, not by retrying.

- **Hard affinity conflict** (`409`) -- an instance requested a hard placement
  constraint (`require_with_tag` or `require_without_tag`) which no node satisfies.
  The cluster is not full; the constraint simply cannot be met. No amount of waiting
  will help.

- **No suitable node** (`404`) -- not a capacity fact at all; the cluster has no
  hypervisor nodes at all.

## The response body

A transient `507` returns a JSON body with four fields:

```json
{
  "error": "message describing what was refused",
  "status": 507,
  "stage": "scheduler stage that refused the placement",
  "transient": true
}
```

### `stage` field

The `stage` field names where the refusal happened:

- **Scheduler filter name** (e.g., `sufficient_idle_cpu`, `sufficient_idle_memory`,
  `sufficient_free_disk`) -- a pre-filter stage eliminated every candidate.
  The filter name is the official stage name for this refusal.

- **`capacity_guard`** -- the capacity admission guard refused every candidate. This
  stage name is used when the admission check itself (not a pre-filter) refused the
  placement.

- **`unknown`** -- the stage could not be determined. This is a defensive value that
  should never appear in normal operation; if you see it, report it.

### `transient` field

The `transient` field is a boolean that indicates whether the refusal is transient
and worth retrying. It is `true` for the two scheduling refusals above and absent
(or `false`) for the other four response types.

## Retry-After header

A transient `507` includes an HTTP `Retry-After` header:

```
Retry-After: 15
```

The value is **always 15 seconds** for all transient capacity refusals. It is not
computed per-refusal.

### Why 15 seconds

The `15` is the default delay that the server itself uses when it defers work
(`BaseOperation.defer()`'s 15-second default). This makes the promise honest:
the number a client is told to wait and the number the server waits before
re-examining deferred work are the same number, defined once in the code.

The server has no way to know which instances are being deleted at the moment a
refusal happens, so it cannot compute how long until freed capacity reappears.
A computed number would imply knowledge the server lacks. `15` is instead chosen
to clear the metrics path with good margin: phase 3 of the sizing plan measured
that a node's `cpu_measured` drops 5.3-5.6 seconds after an instance is destroyed,
so 15 seconds clears that lag by roughly three times.

The `Retry-After` header is **not** configurable per-cluster or per-request. If
your workload or network requires a different retry delay, it belongs in a client
library or operator tooling that wraps the API, not in the server.

## Client-side retry

The `shakenfist-client` Python library (as of the client-python repository's
transient-capacity-refusals phase 4 branch) includes an optional retry for
transient refusals. It is **off by default**.

### Enabling the retry

To enable automatic retry on transient refusals, pass the `retry_transient_capacity=True`
flag when constructing a `Client`:

```python
from shakenfist_client import Client

client = Client(..., retry_transient_capacity=True)
```

### Retry behavior

When enabled, the client will:

1. Catch a `507` response marked `transient: true`.
2. Sleep for the duration specified in the `Retry-After` header (with bounds
   `[1, 60]` seconds to protect against hostile or malformed headers).
3. Retry the **same request** (same instance name, same parameters).

The retry is bounded by the caller's deadline (if one was passed to the API call)
so the total time spent waiting and retrying cannot exceed the budget. Retries
are not attempted for `507` responses that are not marked as transient, or for
other HTTP status codes.

### Why the retry is off by default

The retry is useful for operators, command-line tools, and workloads that can
tolerate variable latency. It is off by default because:

- Blind retries hide latency from callers and monitoring systems that want to
  understand how long an operation took. Some callers (like test suites) have
  their own higher-level retry logic that tracks this visibility and would be
  undermined by an inner retry loop.

- The retry replays the identical request body. If an instance was deleted due
  to the refusal (which can happen during cleanup of failed creates), the same
  name is still in use and the retry will fail again with the same error.

The client library makes the feature available to every caller; operators and
tools that want it can turn it on. Test suites and other latency-aware workloads
should leave it off.
