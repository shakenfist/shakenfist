# Phase 1: Replace requests with httpx

## Prompt

Before responding to questions or discussion points in this
document, explore the occystrap codebase thoroughly. Read relevant
source files, understand existing patterns (pipeline architecture,
input/filter/output interfaces, URI parsing, CLI commands, registry
authentication, error handling), and ground your answers in what
the code actually does today. Do not speculate about the codebase
when you could read it instead. Where a question touches on
external concepts (Docker Registry V2, OCI specs, container image
formats, compression), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

Consult `ARCHITECTURE.md` for the pipeline pattern, element types,
input/filter/output interfaces, and cross-cutting concerns (layer
caching, parallel downloads, compression). Consult `CLAUDE.md` for
build commands and project conventions.

I prefer one commit per logical change, and at minimum one commit
per phase. Do not batch unrelated changes into a single commit.
Each commit should be self-contained: it should build, pass tests,
and have a clear commit message explaining what changed and why.

## Goal

Replace the `requests` library with `httpx` for all registry HTTP
communication, gaining connection pooling, HTTP/2 multiplexing,
and a modern client in one step. Add `--rate-limit` and `--retries`
CLI flags.

## Current state

### HTTP call sites to migrate

There are three distinct HTTP call patterns in the codebase:

**1. `util.request_url()` (util.py:40-108)**

Central HTTP function used by `inputs/registry.py` and
`quay.py`. Calls `requests.request()` directly (no session).
Has its own retry loop for `ChunkedEncodingError` and
`ConnectionError` with hardcoded `MAX_RETRIES = 3`.

Callers:
- `inputs/registry.py:Image.request_url()` — wraps
  `util.request_url()` with bearer token auth and 401 retry
- `quay.py:QuayClient._request()` — wraps
  `util.request_url()` with quay.io API auth headers

**2. `outputs/registry.py:RegistryWriter._request()` (line 115-154)**

Has its own independent HTTP code using `requests.request()`
and `requests.get()` directly — does NOT use
`util.request_url()`. Has its own 401 bearer token
negotiation. No retry logic at all.

**3. Docker daemon code (stays on requests)**

- `inputs/docker.py` — `requests_unixsocket.Session()`
- `inputs/dockerpush.py` — `requests_unixsocket.Session()`
- `outputs/docker.py` — `requests_unixsocket.Session()`

These use Unix domain sockets and are NOT migrated in this
phase.

### Test mock inventory

Two test files contain HTTP mocks that need updating:

- `tests/test_quay.py` — 14 instances of
  `@mock.patch('occystrap.quay.util.request_url')`.
  These mock `util.request_url` so they will naturally
  continue to work if `util.request_url`'s signature stays
  compatible. However, mock return values use
  `requests.Response`-like objects which need to match httpx
  response attributes.

- `tests/test_registry_output.py` — 25 instances of
  `@mock.patch('occystrap.outputs.registry.requests.request')`
  plus 1 `@mock.patch('occystrap.outputs.registry.requests.get')`.
  These patch `requests` directly in the registry output
  module and will need to patch the new httpx client instead.

### CLI options to add

Currently `main.py:cli()` has these relevant options:
- `--parallel` / `-j` (default 4) — stored in
  `ctx.obj['MAX_WORKERS']`
- `--insecure` — stored in `ctx.obj['INSECURE']`

New options to add:
- `--rate-limit` — requests per second
- `--retries` — max retry count (replacing hardcoded
  `MAX_RETRIES = 3`)

## Implementation steps

### Step 1: Add httpx dependency

Update `pyproject.toml` to add `httpx[http2]` to dependencies.
Keep `requests` for Docker daemon code.

```
dependencies = [
    "click>=7.1.1",
    "httpx[http2]",          # apache2 — HTTP/2 + connection pooling
    "requests",              # apache2 — retained for Docker daemon unix socket
    "requests-unixsocket",   # apache2
    ...
]
```

### Step 2: Add CLI flags and context plumbing

Add to `main.py:cli()`:
- `--rate-limit` (default: None/unlimited, type: float,
  envvar: `OCCYSTRAP_RATE_LIMIT`, help: 'Max HTTP requests
  per second')
- `--retries` (default: 3, type: int,
  envvar: `OCCYSTRAP_RETRIES`, help: 'Max retries for failed
  HTTP requests')

Store in `ctx.obj['RATE_LIMIT']` and `ctx.obj['RETRIES']`.

### Step 3: Rewrite util.py HTTP layer

Replace the core of `util.py`:

- Remove `import requests` and
  `from requests.exceptions import ...`
- Add `import httpx` and `import threading`
- Keep `MAX_RETRIES = 3` as the default but make it
  overridable
- Add `create_client()` factory:

```python
def create_client(http2=True, rate_limit=None):
    """Create an httpx.Client with connection pooling
    and optional HTTP/2.

    Args:
        http2: Enable HTTP/2 negotiation (default True).
        rate_limit: Max requests per second (None =
            unlimited).

    Returns:
        An httpx.Client instance. Caller is responsible
        for closing it.
    """
    limits = httpx.Limits(
        max_connections=20,
        max_keepalive_connections=10)
    client = httpx.Client(
        http2=http2,
        limits=limits,
        headers={'User-Agent': get_user_agent()},
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, connect=10.0))
    return client
```

- Add a simple rate limiter class (token bucket) if
  `rate_limit` is specified:

```python
class RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, rate):
        self._rate = rate
        self._lock = threading.Lock()
        self._last = time.monotonic()

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            min_interval = 1.0 / self._rate
            elapsed = now - self._last
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            self._last = time.monotonic()
```

- Rewrite `request_url()` to accept an optional `client`
  parameter (httpx.Client) and `rate_limiter` parameter:

```python
def request_url(method, url, headers=None, data=None,
                stream=False, auth=None,
                retries=MAX_RETRIES, client=None,
                rate_limiter=None):
```

  When `client` is provided, use `client.request()` or
  `client.stream()`. When not provided, create a temporary
  client for backwards compatibility.

  Key API differences to handle:
  - Streaming: `requests` returns response with
    `r.iter_content(8192)`. httpx with `client.stream()`
    returns a context manager; for non-streaming use
    `client.request()`. For the `stream=True` case,
    return a response object that the caller can iterate.
    httpx's `response.iter_bytes(8192)` is the equivalent.
  - Error types: catch `httpx.ConnectError`,
    `httpx.RemoteProtocolError`, `httpx.ReadError` instead
    of `ChunkedEncodingError`, `ConnectionError`.
  - Add 429 retry: if `r.status_code == 429`, read
    `Retry-After` header, sleep that long (or exponential
    backoff if absent), and retry.
  - Add 5xx retry: same backoff logic for status >= 500.
  - Auth: httpx supports `auth=(user, pass)` tuples same
    as requests.

  The debug logging block stays the same — `r.status_code`,
  `r.headers`, `r.text` all work identically in httpx.

### Step 4: Migrate inputs/registry.py

- Remove `from requests.exceptions import ...`
- Add `import httpx`
- In `Image.__init__()`, create an httpx client:
  ```python
  self._client = util.create_client()
  ```
- Pass `client=self._client` to all `util.request_url()`
  calls in `Image.request_url()`.
- In `_download_layer()`:
  - Change `except (ChunkedEncodingError, ConnectionError)`
    to `except (httpx.ConnectError, httpx.RemoteProtocolError,
    httpx.ReadError)`
  - Change `r.iter_content(8192)` to `r.iter_bytes(8192)`.
    Note: for streaming, `util.request_url()` with
    `stream=True` needs to return a response that supports
    `iter_bytes()`. httpx responses support this natively.
  - Remove `MAX_RETRIES` / `RETRY_BACKOFF_BASE` local
    constants (use the ones from util or pass retries through).
- Add cleanup: close client in a `close()` method or
  `__del__`. The `fetch()` method is the main entry point
  and the executor is already shut down there, so add
  `self._client.close()` after the executor work.

### Step 5: Migrate outputs/registry.py

- Remove `import requests`
- Add `import httpx`
- In `RegistryWriter.__init__()`, create an httpx client:
  ```python
  self._client = util.create_client()
  ```
- Rewrite `_request()` to use `self._client.request()`
  instead of `requests.request()`. The auth token
  negotiation logic stays the same but uses
  `self._client.get()` instead of `requests.get()`.
- Close client in `finalize()` after all work is done.

### Step 6: Migrate quay.py (automatic)

`quay.py:QuayClient._request()` calls
`util.request_url()` which will use httpx after Step 3.
However, `QuayClient` currently doesn't pass a client, so
each call creates a temporary client (no pooling).

- Add `self._client = util.create_client()` to
  `QuayClient.__init__()`.
- Pass `client=self._client` in `_request()`.
- Add a `close()` method.
- Update `resolve_quay_uri()` to close the client after use.

### Step 7: Plumb rate_limit and retries through the pipeline

- In `main.py:cli()`, store new options in `ctx.obj`.
- In `PipelineBuilder` or wherever `Image`,
  `RegistryWriter`, and `QuayClient` are constructed, pass
  `rate_limit` and `retries` through.
- `util.create_client()` receives `rate_limit` and creates
  a `RateLimiter` if specified.
- `util.request_url()` receives `retries` to override the
  default.
- The `RateLimiter` instance should be attached to or
  passed alongside the client. Since multiple threads share
  one client, the rate limiter's internal lock handles
  thread safety.

### Step 8: Update tests

**test_quay.py (14 mocks):**
These mock `util.request_url` which keeps the same function
signature. The mock return values need to match httpx
response attributes. Key: `r.json()`, `r.status_code`,
`r.headers`, `r.text`, `r.content` all work the same in
httpx. If tests construct `mock.Mock()` return values with
these attributes, they should work unchanged. Verify and
fix any that break.

**test_registry_output.py (25+1 mocks):**
These patch `requests.request` and `requests.get` directly.
After migration, `RegistryWriter._request()` uses
`self._client.request()` and `self._client.get()`. Update
mocks to patch `self._client` or inject a mock client.

Recommended approach: have `RegistryWriter.__init__()` accept
an optional `client` parameter for dependency injection in
tests. Then tests pass a mock client directly instead of
patching module-level imports.

### Step 9: Verify streaming compatibility

The critical streaming path is `_download_layer()` in
`inputs/registry.py` (line 309):

```python
for chunk in r.iter_content(8192):
    tf.write(d.decompress(chunk))
    h.update(chunk)
    progress.update(len(chunk))
```

With httpx, this becomes:

```python
for chunk in r.iter_bytes(8192):
    tf.write(d.decompress(chunk))
    h.update(chunk)
    progress.update(len(chunk))
```

The `util.request_url()` function currently returns the raw
response object. For `stream=True`, httpx requires using
`client.stream()` as a context manager. Two approaches:

**Option A:** `util.request_url()` handles the context
manager internally and returns a response that has already
entered the streaming context. The caller iterates
`.iter_bytes()` and the response is closed when the caller
is done. This is clean but the caller must be careful about
response lifecycle.

**Option B:** Return the response from `client.request()`
(non-streaming even for large bodies), then use
`response.iter_bytes()` which httpx supports on regular
responses too. This is simpler but may buffer more.

*Recommendation:* Option A. Use `client.stream()` in
`request_url()` when `stream=True`, but return the response
object directly (the context manager stays open). The caller
calls `r.close()` when done. Document this contract.
Actually, httpx also supports calling `client.request()`
and then using `response.stream()` or `response.iter_bytes()`
without the context manager — check which approach works
cleanest.

## Commit plan

This phase should be split into commits roughly as follows:

1. **Add httpx dependency and create_client factory.**
   Add `httpx[http2]` to `pyproject.toml`. Add
   `create_client()` and `RateLimiter` to `util.py`.
   No callers yet — existing code unchanged. Tests pass.

2. **Migrate util.request_url() to httpx.**
   Rewrite `request_url()` to use httpx internally while
   keeping the same external interface. Add 429/5xx retry.
   Add `--retries` and `--rate-limit` CLI flags. Update
   `test_quay.py` mocks if needed.

3. **Migrate inputs/registry.py to use httpx client.**
   Create client in `Image.__init__()`, pass through calls,
   update streaming path, update error handling.

4. **Migrate outputs/registry.py to use httpx client.**
   Create client in `RegistryWriter.__init__()`, rewrite
   `_request()`, update `test_registry_output.py` mocks.

5. **Migrate quay.py to use httpx client.**
   Create client in `QuayClient.__init__()`, pass through
   calls, close on completion.

6. **Documentation updates.**
   Update `ARCHITECTURE.md`, `README.md`, `AGENTS.md`,
   and `docs/` to reflect httpx usage, new CLI flags, and
   HTTP/2 support.

## Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| httpx streaming API differs from requests | High | Medium | Step 9 analysis; test with real registries |
| Test mock updates miss edge cases | Medium | Low | Run full test suite after each commit |
| HTTP/2 not supported by target registry | Low | None | httpx falls back to HTTP/1.1 via ALPN |
| Rate limiter contention under high `-j` | Low | Low | Token bucket with lock is simple and correct |
| httpx version compatibility issues | Low | Low | Pin minimum version in pyproject.toml |

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
