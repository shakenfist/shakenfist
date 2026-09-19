# Capacity refusals

When the scheduler cannot place an instance because the cluster is full, the
`POST /instances` request fails with an HTTP `507 Insufficient Storage` status.
This document describes the response format and what it means.

## Which refusals are transient

Not every `507` is worth retrying, and not even every *scheduling* `507` is.
The server says which is which, so a client does not have to guess from the
message text.

**Transient refusals** (`transient: true`) -- a resource the cluster measures
and shares ran out, and an ordinary instance teardown returns it within
seconds:

1. **Resource filter stages** -- `sufficient_idle_cpu`, `sufficient_idle_memory`,
   `sufficient_free_disk`, `sufficient_idle_disk` and `queue_state`. One of the
   scheduler's pre-filter stages eliminated every candidate because the cluster
   does not have enough of that resource *right now*.

2. **The capacity guard** (`capacity_guard`) -- the capacity admission guard
   refused every candidate. The cluster's accounting shows no room for the
   placement, but a concurrent delete or a capacity reconciler pass can change
   that within seconds.

**Non-transient refusals** (`transient: false`, or no `transient` field at
all) -- the cluster would refuse the identical request just as firmly in
fifteen seconds, in an hour, or after every instance on it had been deleted:

- **Structural filter stages** -- `cpu_max_per_instance` means the request asks
  for more vCPUs than any single node in the cluster will host, whatever else
  is running. `is_hypervisor` and `pre_schedule` mean the candidate set was
  empty before any resource was measured: no node in the request's candidate
  list is a hypervisor, or there were no candidates to begin with. These are
  still `507` responses and still carry their `stage`, because the stage is
  what tells you what to fix -- but they carry no `Retry-After`, and a client
  that retried them would replay the same impossible request until its
  deadline.

- **Address-pool exhaustion** (`CongestedNetwork`) -- the virtual network's
  address pool is exhausted. This refusal carries no `stage` or `transient`
  field at all. No amount of waiting on the ten-second horizon will help; the
  pool recovers only after the deletion halo expires, which is much longer and
  differently-shaped than an instance teardown. Solve it by adding address
  space or freeing a network, not by retrying.

- **Hard affinity conflict** (`409`) -- an instance requested a hard placement
  constraint (`require_with_tag` or `require_without_tag`) which no node
  satisfies. The cluster is not full; the constraint simply cannot be met.

- **No suitable node** (`404`) -- not a capacity fact at all; a node named in
  the request is not in the active node list.

## The response body

A scheduling `507` returns a JSON body with four fields:

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

- **A scheduler filter name** -- `sufficient_idle_cpu`, `sufficient_idle_memory`,
  `sufficient_free_disk`, `sufficient_idle_disk`, `queue_state`,
  `cpu_max_per_instance`, `is_hypervisor` or `pre_schedule`. The filter name is
  the official stage name for this refusal, and is the same string the
  instance's `schedule has no candidates at stage ...` audit event records.

- **`capacity_guard`** -- the capacity admission guard refused every candidate.
  This stage name is used when the admission check itself, rather than a
  pre-filter, refused the placement.

- **`unknown`** -- the refusal reached the API carrying no stage. This is a
  defensive value that should never appear in normal operation; if you see it,
  report it. It is never marked transient.

### `transient` field

The `transient` field is a boolean saying whether waiting and trying again can
plausibly succeed. It is `true` only for the stages listed as transient above.
Read this field rather than inferring retry-worthiness from the status code or
the `stage` name: the classification lives in one place in the server
(`TRANSIENT_CAPACITY_STAGES` in `shakenfist/external_api/base.py`), and a
client that reproduces the list will drift from it.

## Retry-After header

A transient `507` includes an HTTP `Retry-After` header:

```
Retry-After: 15
```

The value is **always 15 seconds** for all transient capacity refusals. It is not
computed per-refusal.

### Why 15 seconds

The `15` is the default delay that the server itself uses when it defers work
(`BaseClusterOperation.defer()`'s 15-second default). This makes the promise
honest: the number a client is told to wait and the number the server waits
before re-examining deferred work were chosen to match.

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

!!! warning "Not in a released client yet"

    The `retry_transient_capacity` flag described below exists only on the
    client-python repository's `transient-capacity-refusals-phase-04-client`
    branch. It is **not** in any released `shakenfist-client` -- the latest
    release is v0.8.3 -- so passing it to a `Client` you installed from PyPI
    raises `TypeError`. This section will name a minimum client version once
    that release is cut.

    The server half of the contract described above is live regardless: the
    `Retry-After` header and the `stage` and `transient` body fields are
    readable by any HTTP client today, and implementing the retry yourself
    needs nothing from the library.

The `shakenfist-client` Python library includes an optional retry for
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

Two separate bounds stop that loop, and whichever is reached first re-raises the
server's own refusal, so a caller sees exactly the exception it would have seen
with the flag off:

- **The caller's deadline**, if one was passed to the API call. This bounds the
  sleeping; the request issued after the last sleep is not itself cut short, so
  a call can return a round trip after its budget.
- **An attempt cap** of five, because the deadline alone is a poor bound on what
  replaying costs the *cluster*. Every refused create still costs an instance
  record, IPAM allocations, an event trail and a delete operation, at the moment
  the cluster is busiest. An `ASYNC_BLOCK` caller with no timeout of its own has
  an hour of budget, which at a fifteen second hint is around 240 replays.

Retries are not attempted for `507` responses that are not marked as transient
-- which includes every structural refusal listed above -- or for other HTTP
status codes.

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

- A refused create holds its allocated addresses until its delete is processed,
  so a long wait against a small network can run out of address space before it
  runs out of budget. It does *not* hold capacity: the ledger is charged in the
  same database transaction that writes the placement, and a refusal rolls that
  transaction back.

The client library makes the feature available to every caller; operators and
tools that want it can turn it on. Test suites and other latency-aware workloads
should leave it off.
