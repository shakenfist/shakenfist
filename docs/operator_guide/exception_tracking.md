# Exception Tracking

Shaken Fist includes an exception tracking system that records unhandled
exceptions to disk for later analysis. This helps operators identify and
debug recurring issues in production clusters.

## How It Works

When an exception occurs, it is recorded to `/srv/shakenfist/exceptions/` as
a JSON file. Each unique exception traceback is hashed (using SHA-256) and
stored in a file named after the last 8 characters of that hash. This means
identical exceptions are deduplicated automatically.

Each JSON file contains:

* `traceback`: The full exception traceback
* `count`: The number of times this exception has occurred
* `events`: A list of Unix timestamps for each occurrence

For example:

```json
{
    "traceback": "\nTraceback (most recent call last):\n  File ...",
    "count": 3,
    "events": [1703692800.123, 1703693100.456, 1703693400.789]
}
```

## What Gets Tracked

The exception tracking system captures:

* Unhandled exceptions in the main thread via `sys.excepthook`
* Unhandled exceptions in worker threads via `threading.excepthook`
* Exceptions passed to `ignore_exception()`, which are caught but logged

## Exceptions in the log stream

Every recorded exception also produces exactly one log line above DEBUG,
and that line carries the fields needed to find the on-disk record:

* `exception_hash` -- the same 8 characters used in the filename, so the
  record is at `/srv/shakenfist/exceptions/<exception_hash>.json`
* `exception_class` -- the exception type, for example `ValueError`
* `count` -- how many times this signature has been seen, which is the
  value from the JSON file at the moment the line was emitted

Which line carries them depends on how the exception was caught:

| Source | Shipped line | Level |
|--------|--------------|-------|
| `ignore_exception()` (daemons and workers) | `[Exception] Ignored error in <process>: ...` | ERROR |
| An unhandled exception in a REST API handler | `Server error` | ERROR |
| `sys.excepthook` / `threading.excepthook`, first occurrence of a signature | `Recorded new exception: ...` | WARNING |
| `sys.excepthook` / `threading.excepthook`, repeat occurrence | `Recorded repeat exception` | DEBUG |

Only one line per event is emitted above DEBUG. The recorder's own
book-keeping line (`Recorded exception (already logged by caller)`) stays
at DEBUG whenever the caller has already logged the full detail, so a
single failure does not appear twice in centralised logging under two
different message signatures -- which used to double the apparent rate of
every task exception type in log mining (github issue #3590).

Note that repeat occurrences from the excepthooks drop to DEBUG on
purpose, so a hot loop cannot flood the aggregator. The JSON file remains
the authoritative record of how many times a signature has occurred and
when, so a signature which appears once in your logs may still have a
`count` in the thousands.

To go from a log line to the record, take `exception_hash` from the log
event and read the matching file on the node which emitted it:

```bash
cat /srv/shakenfist/exceptions/1a2b3c4d.json | jq .
```

## Viewing Exceptions

To list all recorded exceptions:

```bash
ls -la /srv/shakenfist/exceptions/
```

To view the details of a specific exception:

```bash
cat /srv/shakenfist/exceptions/<hash>.json | jq .
```

To find the most frequently occurring exceptions:

```bash
for f in /srv/shakenfist/exceptions/*.json; do
    echo "$(jq -r .count $f) $f"
done | sort -rn | head -10
```

## Cleanup

Exception files accumulate over time. You may wish to periodically clean up
old exception files, particularly after addressing the underlying issues:

```bash
# Remove all exception files
rm /srv/shakenfist/exceptions/*.json

# Or remove files older than 7 days
find /srv/shakenfist/exceptions/ -name "*.json" -mtime +7 -delete
```
