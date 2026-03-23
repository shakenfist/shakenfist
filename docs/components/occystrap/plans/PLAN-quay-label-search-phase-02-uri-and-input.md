# Phase 2: quay:// URI parsing and multi-image resolution

## Context

This is phase 2 of the
[quay.io tag-based bulk image discovery plan](/components/occystrap/plans/PLAN-quay-label-search/).
It builds on the `QuayClient` implemented in
[phase 1](/components/occystrap/plans/PLAN-quay-label-search-phase-01-api-client/).

## Goal

Add `quay://` as a recognized URI scheme and implement a resolver
that expands a single `quay://` URI into a list of concrete
`(registry, image, tag)` tuples that the existing `registry.Image`
input class can fetch.

This phase does NOT modify the `info` or `process` commands —
that is phase 3. This phase provides the building blocks they
will use.

## URI parsing

### How `quay://kolla/*:latest` is parsed by `urlparse`

```
quay://kolla/*:latest
  scheme = 'quay'
  netloc = 'kolla'        (the org / namespace)
  path   = '/*:latest'    (glob:tag)
  query  = ''

quay://kolla/centos-*:2025.1-debian
  scheme = 'quay'
  netloc = 'kolla'
  path   = '/centos-*:2025.1-debian'

quay://kolla/*:latest?token=abc
  scheme = 'quay'
  netloc = 'kolla'
  path   = '/*:latest'
  query  = 'token=abc'
```

So `parse_uri()` already handles this correctly — `host` gets
the org name and `path` gets the `/glob:tag` string. We just
need a `parse_quay_uri()` function to split the path into the
glob pattern and tag.

### New named tuple

The existing `parse_registry_uri()` returns a plain tuple. For
quay we should follow the same pattern:

```python
def parse_quay_uri(uri_spec):
    """Parse quay URI into (namespace, repo_glob, tag, options).

    Handles formats like:
        quay://kolla/*:latest
        quay://kolla/centos-*:2025.1-debian
        quay://myorg/*:latest?token=abc

    Returns:
        Tuple of (namespace, repo_glob, tag, options_dict)
    """
```

The tag is split from the path at the last colon (same logic as
`parse_registry_uri`). If no colon is present, default to
`latest`. If no glob is present (just `quay://kolla`), default
to `*`.

### Changes to `uri.py`

1. Add `'quay'` to `INPUT_SCHEMES`.
2. Add `parse_quay_uri()` function.
3. Update the module docstring to document the `quay://` format.

## Multi-image resolver

### New function in `occystrap/quay.py`

Add a `resolve_quay_uri()` function to the existing `quay.py`
module (next to `QuayClient`):

```python
def resolve_quay_uri(namespace, repo_glob, tag, token=None):
    """Resolve a quay:// URI into matching image references.

    Lists all repositories in the namespace, filters by the
    glob pattern, checks tag existence for each match, and
    returns a list of (registry, image, tag) tuples suitable
    for constructing registry.Image inputs.

    Args:
        namespace: Quay.io organization name.
        repo_glob: Glob pattern for repository names
            (e.g., '*', 'nova-*').
        tag: Exact tag name to match.
        token: Optional quay.io API token for private orgs.

    Returns:
        List of ('quay.io', 'namespace/repo', tag) tuples.
    """
```

The function:
1. Creates a `QuayClient(token=token)`.
2. Calls `client.list_repositories(namespace)`.
3. Filters repo names using `fnmatch.fnmatch(name, repo_glob)`.
4. For each matching repo, calls `client.has_tag(namespace,
   repo, tag)`.
5. Returns `[('quay.io', 'namespace/repo', tag)]` for each
   repo that has the tag.
6. Logs progress: "Checking tag for repo N of M: org/name..."

### Authentication

The quay.io API v1 uses a different token from the Docker
Registry V2 bearer tokens. However, both can be the same
credential in practice (e.g., a robot account token works for
both).

For this phase, the `token` parameter flows from the URI query
string (`?token=...`) or from the existing `--password` CLI
option. The resolver accepts the token as a parameter and passes
it to `QuayClient`. How the CLI surfaces this is decided in
phase 3.

We will try the following approach:
- If `?token=...` is in the URI, use that.
- Otherwise, if `--password` is set, use that as the quay.io
  API token (since quay.io robot account tokens work for both
  the API v1 and Docker Registry V2).
- Otherwise, no auth (works for public orgs).

This keeps authentication simple and avoids adding a new CLI
option for now. If it turns out that the API v1 and Registry V2
need different credentials, we can add `--quay-token` later.

### Integration with PipelineBuilder

The `PipelineBuilder.build_input()` method currently returns a
single `ImageInput`. With `quay://`, it would need to return
multiple inputs — which breaks the interface.

Instead of modifying `build_input()`, phase 3 will detect the
`quay` scheme *before* calling `build_pipeline()` and loop over
the resolved images. `build_input()` does not need to handle
`quay://` at all — the resolver produces `registry://`-compatible
tuples that go through the existing registry path.

Therefore, **no changes to `pipeline.py`** in this phase.

## Changes summary

### `occystrap/uri.py`

- Add `'quay'` to `INPUT_SCHEMES`
- Add `parse_quay_uri(uri_spec)` function
- Update module docstring

### `occystrap/quay.py`

- Add `resolve_quay_uri(namespace, repo_glob, tag, token=None)`
- Import `fnmatch`

### `occystrap/tests/test_quay.py`

New tests for URI parsing (may also go in a new
`test_uri_quay.py` or be added to existing `test_quay.py` —
keeping them in `test_quay.py` is simpler):

- `test_parse_quay_uri_basic` — `quay://kolla/*:latest` parses
  to `('kolla', '*', 'latest', {})`.
- `test_parse_quay_uri_with_glob` — `quay://kolla/centos-*:v1`
  parses to `('kolla', 'centos-*', 'v1', {})`.
- `test_parse_quay_uri_no_tag` — `quay://kolla/*` defaults tag
  to `latest`.
- `test_parse_quay_uri_no_glob` — `quay://kolla` defaults glob
  to `*` and tag to `latest`.
- `test_parse_quay_uri_with_token` — `quay://kolla/*:v1?token=x`
  includes `{'token': 'x'}` in options.
- `test_parse_uri_recognizes_quay` — `parse_uri('quay://...')`
  returns scheme `'quay'`.

New tests for the resolver:

- `test_resolve_basic` — mock `list_repositories` returning
  3 repos, `has_tag` returning True for 2, verify result is
  2 tuples.
- `test_resolve_glob_filter` — mock `list_repositories`
  returning `['nova-api', 'keystone', 'nova-scheduler']`,
  glob `nova-*`, verify only nova repos are checked.
- `test_resolve_no_matches` — all repos lack the tag, verify
  empty list.
- `test_resolve_empty_org` — `list_repositories` returns `[]`,
  verify empty list.
- `test_resolve_passes_token` — verify `QuayClient` is
  constructed with the token.

## Commit plan

A single commit containing:
- Changes to `occystrap/uri.py`
- Changes to `occystrap/quay.py`
- New tests in `occystrap/tests/test_quay.py`
