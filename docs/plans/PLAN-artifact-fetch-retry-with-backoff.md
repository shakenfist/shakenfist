# PLAN: Retry transient artifact fetches with backoff

## Status

Complete.

Landed via the `stability` branch.

## Problem

When `artifact_fetch_op` hits a transient upstream failure (the
serving host is briefly unreachable during a patch reboot, DNS
hiccups, a 5xx blip), the operation moves straight to
`STATE_ERROR`. If the fetch was kicked off as part of an instance
start, `inst.enqueue_delete_due_error(...)` then tears the instance
down.

Concretely, CI run `25975382274` saw
`test_blob_download.test_download_size_matches_expected` fail when
the underlying `https://sfcbr.shakenfist.com/cgi-bin/uuid.cgi`
fetch hit a transient network problem (probably ansible restarting
the upstream to apply OS patches). The artifact entered `error`
within ~15 seconds, with zero blobs; the very next test using the
same URL succeeded. So the failure mode is "single transient blip"
rather than "upstream actually broken".

This is bad UX for users of the cloud, not just CI: a one-second
network blip during an `instance create` is not something that
should require manual cleanup.

## Approach

Add a generic per-operation retry budget on `BaseClusterOperation`,
backed by the existing `defer()` mechanism:

1. `BaseClusterOperation.current_defer_count: int` is set by the
   queue dispatcher from the work_item payload at dispatch time.
   Default 0.
2. `defer()` writes `defer_count = current_defer_count + 1` into
   the next work_item, so the counter survives across re-enqueues
   without needing a new MariaDB column. (The `cluster_operations`
   table is insert-only, so we can't mutate the op row.)
3. `defer_with_backoff(delays=(15, 30, 60), reason=...)` consults
   `current_defer_count`. If there is budget left, it picks the
   next delay and calls `defer()`. Otherwise it returns `False`,
   leaving the caller to do whatever final-error handling it
   wants.
4. `artifact_fetch_op._image_fetch` calls `defer_with_backoff()`
   on transient (`HTTPError` / `requests.exceptions.RequestException`
   / `requests.exceptions.ConnectionError`) failures *before*
   falling through to `Artifact.STATE_ERROR` and
   `enqueue_delete_due_error`.

Retry schedule is `(15s, 30s, 60s)`, giving a ~1m45s ceiling. This
covers an OS-patch service restart but is short enough that a
genuinely broken URL surfaces an error quickly.

## What changed

- `shakenfist/operations/baseoperation.py`
  - Added `current_defer_count` attribute.
  - `defer()` now threads `defer_count` through the work_item
    payload and into the deferred-execution event.
  - Added `defer_with_backoff()`.
- `shakenfist/daemons/queues/workitem.py` and
  `shakenfist/daemons/network/workitem.py` read `defer_count` from
  the work_item payload and set `op.current_defer_count` before
  executing.
- `shakenfist/operations/artifact_fetch_op.py` calls
  `defer_with_backoff()` on transient fetch exceptions before
  taking the existing error path.
- `shakenfist/tests/operations/test_baseoperation.py` (new)
  exercises both the `defer()` payload and the retry-budget logic.

## Out of scope

- No schema change: the retry counter rides on the work_item
  payload, not on `cluster_operations.metadata_json`.
- No changes to other operation types. Once we have field
  experience, we can opportunistically migrate other ops that have
  similar "transient remote dependency" failure modes (cleaner,
  cluster ops invoking external services, etc.).
- The "use cached version on DNS error" branch is preserved
  exactly as-is.
- We did not touch the CI smoke test
  (`test_blob_download.test_download_size_matches_expected`).
  Server-side retries make it pass naturally: each `defer()` emits
  an event, which keeps `_await_objects_ready()`'s "no progress in
  5 minutes" timer from firing while a retry is pending.

## Validation

Unit: `shakenfist/tests/operations/test_baseoperation.py` covers
defer-count threading, the default `(15, 30, 60)` schedule, custom
schedules, and budget exhaustion.

Functional: the next run of the smoke suite that includes
`TestBlobDownload` will exercise the full flow against a real
cluster. The pre-existing
`test_download_size_matches_expected` is itself a regression test
for this fix.
