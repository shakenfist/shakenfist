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
