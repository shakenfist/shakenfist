# Phase 2: Parallel Quay API resolution

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

Replace the sequential tag-checking loop in
`resolve_quay_uri()` with concurrent `has_tag()` calls via
`ThreadPoolExecutor`, reducing the Quay API resolution phase
from O(n) sequential HTTP requests to O(n/j) wall-clock time
where j is the parallelism level.

## Current state

`quay.py:resolve_quay_uri()` (line 212-279) does:

1. Create a `QuayClient` (line 234).
2. List all repos in the namespace — paginated, sequential,
   typically 2-4 pages (line 244).
3. Filter by glob — pure CPU, instant (line 248).
4. **Sequential tag check** — for each matching repo, call
   `client.has_tag()` one at a time (lines 253-273). For
   100 matching repos this is 100 sequential HTTP round-trips
   to quay.io, each taking 100-300ms. **This is the
   bottleneck being addressed.**
5. Close the client (line 275).

After Phase 1, the `QuayClient` uses a shared `httpx.Client`
with connection pooling and HTTP/2. The client is already
thread-safe (`httpx.Client` handles concurrent requests from
multiple threads). The `has_tag()` method is stateless and
read-only — it takes `(namespace, repo, tag)` and returns tag
metadata or None.

### Callers

`resolve_quay_uri()` is called from:

- `main.py:_resolve_quay_images()` (line 156) — has access to
  `ctx.obj['MAX_WORKERS']` (the `-j` flag, default 4).
- Tests mock `QuayClient` at the class level, so the internal
  implementation of `resolve_quay_uri()` can change freely
  without affecting test mocks. Tests verify results, not
  call ordering.

### Thread safety of has_tag()

`has_tag()` calls `self._request()` which calls
`util.request_url()` with `client=self._client`. The httpx
client is thread-safe. The `_headers()` method reads
`self.token` which is immutable after construction. No shared
mutable state is accessed. **Conclusion: `has_tag()` is safe
to call from multiple threads concurrently.**

### Test impact

The `TestResolveQuayUri` tests (test_quay.py:351-472) mock
`QuayClient` at the class level via
`@mock.patch('occystrap.quay.QuayClient')`. They set
`client.has_tag.side_effect` or `.return_value` and then
check results. Key considerations:

- `test_basic_resolution` uses `side_effect` list — with
  concurrent execution, the order in which `has_tag()` is
  called is non-deterministic, so `side_effect` as an
  ordered list may not map correctly to repos. **This test
  needs updating** to use a `side_effect` function keyed
  on the repo argument instead.
- `test_glob_filter` checks `has_tag.call_count` — this
  still works with concurrent execution.
- `test_passes_token` checks `QuayClient` constructor args —
  needs updating for the new `max_workers` parameter.
- `test_since_filters_old_tags` and friends use
  `.return_value` (same value for all calls) — these work
  fine with concurrent execution.

## Implementation steps

### Step 1: Add max_workers parameter to resolve_quay_uri()

Add a `max_workers` parameter to `resolve_quay_uri()`:

```python
def resolve_quay_uri(namespace, repo_glob, tag,
                     token=None, since=None,
                     max_workers=4):
```

### Step 2: Replace sequential loop with ThreadPoolExecutor

Replace the sequential loop (lines 253-273) with:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _check_one_repo(client, namespace, repo, tag,
                    since_ts):
    """Check a single repo for the tag. Returns
    (repo, tag_info) or (repo, None)."""
    tag_info = client.has_tag(namespace, repo, tag)
    if not tag_info:
        return (repo, None)
    if since_ts is not None:
        tag_ts = tag_info.get('start_ts', 0)
        if tag_ts < since_ts:
            return (repo, None)
    return (repo, tag_info)

# In resolve_quay_uri(), replace the loop with:
results = []
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    futures = {
        executor.submit(
            _check_one_repo, client, namespace,
            repo, tag, since_ts): repo
        for repo in matching_repos
    }
    for future in as_completed(futures):
        repo, tag_info = future.result()
        if tag_info is not None:
            results.append(
                ('quay.io',
                 '%s/%s' % (namespace, repo), tag))
```

Note: `as_completed()` yields futures in completion order,
not submission order. The result list will not be in the
same order as the input repos. This is fine — the caller
(`_process_multi`) doesn't depend on ordering of the
resolution results.

### Step 3: Add progress logging

Currently, the sequential loop logs each repo as it's
checked. With concurrent execution, logging every repo
would be noisy and interleaved. Replace with:

- Log the total count before starting:
  `Checking tag for N repositories (j workers)...`
- Log a summary after completion:
  `Found M of N repositories with tag`

Individual repo-level logging can remain at DEBUG level
inside `has_tag()`.

### Step 4: Pass max_workers from CLI context

Update `main.py:_resolve_quay_images()` to pass
`max_workers` through:

```python
ctx_obj = ctx.obj if ctx and ctx.obj else {}
max_workers = ctx_obj.get('MAX_WORKERS', 4)

return quay_module.resolve_quay_uri(
    namespace, repo_glob, tag, token=token,
    since=since, max_workers=max_workers)
```

### Step 5: Update tests

**test_basic_resolution**: Change `side_effect` from an
ordered list to a function that returns based on the `repo`
argument:

```python
def has_tag_side_effect(ns, repo, tag):
    if repo in ('nova-api', 'glance-api'):
        return tag_info
    return None

client.has_tag.side_effect = has_tag_side_effect
```

**test_passes_token**: `QuayClient` constructor is now called
with `token='secret'` only (max_workers doesn't go to the
constructor — it goes to `resolve_quay_uri()`). This test
should still pass as-is.

**test_glob_filter**: Uses `.return_value` (same for all
calls), so call_count check is fine. Results order may differ
with concurrent execution — sort before asserting if needed.

**All result-checking tests**: Results may arrive in any order
due to `as_completed()`. Sort results before asserting, or
use `assertCountEqual` (order-independent).

### Step 6: Update documentation

Update `ARCHITECTURE.md` to note that Quay API tag resolution
is now parallelized. Update the "Quay Resolution" or similar
section.

## Commit plan

1. **Parallelize tag resolution in resolve_quay_uri().**
   Add `max_workers` parameter, replace sequential loop with
   ThreadPoolExecutor, extract `_check_one_repo()` helper,
   update progress logging. Pass `max_workers` from
   `_resolve_quay_images()` in main.py. Update tests for
   concurrent execution (sort results, use side_effect
   functions). Update docs.

This is a small, focused change — a single commit is
appropriate.

## Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Quay.io rate-limits concurrent requests | Medium | Low | --rate-limit flag from Phase 1 throttles |
| Result ordering changes | High | None | Callers don't depend on order |
| Test flakiness from concurrent mock | Low | Low | Use side_effect functions, assertCountEqual |
| Thread overhead for small repo counts | Low | None | ThreadPoolExecutor is cheap for small N |

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
