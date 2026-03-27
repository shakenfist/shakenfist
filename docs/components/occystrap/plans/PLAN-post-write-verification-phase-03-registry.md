# Phase 3: RegistryWriter verifier

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

Implement `verify()` for `RegistryWriter` so that `process`
with `registry://` output verifies the pushed image is
reachable in the registry.

## Current state

### RegistryWriter after finalize()

State available:
- `self._config_digest` — e.g., `'sha256:abc123...'`
- `self._config_size` — integer, config blob size in bytes
- `self._layers` — list of dicts, each with `'digest'`,
  `'size'`, `'mediaType'`
- `self.registry`, `self.image`, `self.tag`
- `self._moniker` — `'https'` or `'http'`
- `self.username`, `self.password`, `self.secure`

**Clients are closed after finalize():**
`finalize()` calls `_close_thread_clients()` and closes
`self._client`. This means `_request()` cannot be used
in `verify()` — it relies on `_get_thread_client()` which
returns closed clients from thread-local storage.

The `_blob_exists()` helper also uses `_request()` and
is therefore not usable after finalize.

### Registry V2 API for verification

- `HEAD /v2/{name}/blobs/{digest}` — check blob exists,
  returns 200 with `Content-Length` header or 404.
- `GET /v2/{name}/manifests/{tag}` — fetch pushed manifest,
  returns JSON body. Accept header controls format.

Both endpoints may require authentication (Bearer token).

## Design

### Fresh client for verification

Since finalize() closes all clients, `verify()` must create
a fresh httpx client. This is straightforward via
`util.create_client()`. The client is used for a small
number of HEAD/GET requests (one per blob + one manifest
GET) and closed when verification completes.

Authentication reuse: `self._cached_auth` may still hold a
valid token from the push, but tokens expire. Safer to
let `_request()` handle auth from scratch. However,
`_request()` depends on `_get_thread_client()`. The
simplest approach: create a standalone verification method
that makes requests directly via the fresh client, with
its own auth handling (copy the auth pattern from
`_request()` or use `util.request_url()` which has its
own retry/auth support — but `util.request_url()` doesn't
handle registry Bearer auth, only the `_request()` method
does).

*Decision:* Re-initialize the thread client infrastructure
before verifying by calling `_init_thread_clients()` with
a fresh client. This lets `_request()` and `_blob_exists()`
work normally during verification.

### RegistryWriter.verify()

**Default mode (`full=False`):**
1. Create a fresh httpx client and re-initialize thread
   client infrastructure.
2. HEAD each layer blob digest to confirm it exists.
3. HEAD the config blob.
4. GET the manifest by tag and compare the JSON body
   against what was pushed (byte-for-byte comparison
   of the compact JSON, or parse and compare the config
   digest and layer digests).
5. Close the fresh client.

**Full mode (`full=True`):**
Same as default — there is no additional useful check.
GET-ing each blob and re-hashing would be extremely
expensive (downloading everything we just uploaded) and
provides minimal additional confidence beyond HEAD
existence checks. The registry already verifies digests
on upload.

### Error handling

- Network errors during verification → warning (the push
  succeeded; verification is best-effort).
- 404 on a blob → error (blob should exist after push).
- Manifest body mismatch → error (manifest corruption).
- Auth failure during verification → warning (token may
  have expired between push and verify).

## Implementation steps

### Step 1: Implement RegistryWriter.verify()

Add `verify(full=False)` to `RegistryWriter`:
- Create fresh client via `util.create_client()`.
- Set `self._client` and `self._own_client = True`.
- Call `self._init_thread_clients()`.
- HEAD each layer blob via `self._blob_exists()`.
- HEAD the config blob.
- GET the manifest and compare config/layer digests.
- Close the fresh client in a finally block.
- Wrap the entire method in try/except to catch
  network errors as warnings.

### Step 2: Add unit tests

- Mock `_request` to return 200 for all HEADs and
  manifest GET → verify passes.
- Mock `_request` to return 404 for a layer blob →
  verify reports error.
- Mock `_request` to return a manifest with wrong
  config digest → verify reports error.
- Mock `_request` to raise a connection error →
  verify reports warning.

### Step 3: Update documentation

Update ARCHITECTURE.md and master plan.

## Commit plan

1. **Implement RegistryWriter verification.**
   Add verify() with unit tests.

2. **Update documentation for phase 3.**

## Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Auth token expired between push and verify | Low | Low | Warning, not error; push already succeeded |
| Registry rate-limits verification HEADs | Low | Low | Small number of requests; respects rate limiter |
| Re-initializing clients after close | Low | Medium | Clean re-init via _init_thread_clients() |

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
