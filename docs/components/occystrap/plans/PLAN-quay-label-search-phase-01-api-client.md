# Phase 1: Quay.io API client

## Context

This is phase 1 of the
[quay.io tag-based bulk image discovery plan](/components/occystrap/plans/PLAN-quay-label-search/).

## Goal

Create a new module `occystrap/quay.py` that wraps the quay.io
REST API v1, providing two core operations:

1. List all repositories in an organization.
2. Check whether a specific tag exists for a given repository.

This module is a pure API client with no awareness of the
pipeline, URIs, or CLI. It will be consumed by phase 2.

## Quay.io API details

### Repository listing

**Endpoint:** `GET https://quay.io/api/v1/repository`

Key query parameters:
- `namespace` — the organization name
- `public=true` — required to see public repos when
  unauthenticated

**Response:**
```json
{
  "repositories": [
    {
      "namespace": "kolla",
      "name": "nova-api",
      "description": "...",
      "is_public": true,
      "kind": "image",
      "state": "NORMAL"
    }
  ],
  "next_page": "gAAAAABp...opaque_token..."
}
```

**Pagination:**
- Fixed page size of 100 repositories (not configurable).
- Uses opaque cursor tokens: the `next_page` key is present in
  the response when more pages exist, and absent entirely (not
  null) when there are no more results.
- To paginate: pass the `next_page` value as a query parameter
  in the next request.

### Tag existence check

**Endpoint:**
`GET https://quay.io/api/v1/repository/{namespace}/{repo}/tag/`

Note the trailing slash — it is required.

Key query parameters:
- `specificTag` — filter to a specific tag name (avoids fetching
  all tags)
- `onlyActiveTags=true` — exclude expired tags
- `limit=1` — minimize response size since we only need to know
  if the tag exists

**Response:**
```json
{
  "tags": [
    {
      "name": "latest",
      "manifest_digest": "sha256:...",
      "start_ts": 1774047466,
      "last_modified": "Fri, 20 Mar 2026 22:57:46 -0000",
      "is_manifest_list": true
    }
  ],
  "page": 1,
  "has_additional": false
}
```

An empty `"tags": []` array means the tag does not exist.

**Pagination:**
- Uses numeric page-based pagination (`page` param, 1-indexed).
- Has a `limit` param (default 50, max 100).
- Response includes `has_additional` boolean.
- With `specificTag` and `limit=1`, pagination is unnecessary —
  the response will contain zero or one tag.

### Authentication

Both endpoints accept an optional `Authorization: Bearer <token>`
header. The token is a quay.io API application token or robot
account token — not a Docker Registry V2 bearer token.

For public organizations, no authentication is needed. For private
organizations, a 401 or empty results are returned without auth.

## Implementation

### New file: `occystrap/quay.py`

```python
class QuayClient:
    def __init__(self, token=None):
        ...

    def list_repositories(self, namespace):
        """List all repositories in a namespace.

        Handles pagination automatically. Returns a list
        of repository name strings (not the full
        namespace/name, just the name within the org).
        """
        ...

    def has_tag(self, namespace, repo, tag):
        """Check whether a repository has a specific tag.

        Returns True if the tag exists and is active,
        False otherwise.
        """
        ...
```

### Design decisions

1. **Use `util.request_url` for HTTP.** This reuses the existing
   retry logic, user-agent header, and debug logging. However,
   `util.request_url` raises `APIException` for any non-200
   status, and `UnauthorizedException` for 401. The quay.io API
   may return 404 for nonexistent repos (in `has_tag`), so we
   need to handle that — either catch `APIException` and check
   the status code, or add 404 handling.

   Decision: Catch `APIException` in `has_tag` and return `False`
   for 404 responses. This is the simplest approach — the
   `APIException` already carries the status code as its 4th arg.

2. **Authentication.** The client accepts an optional `token`
   parameter. When provided, it is sent as
   `Authorization: Bearer <token>` on every request. The token
   is a quay.io API token, not Docker registry credentials.

   The CLI integration (phase 2) will decide how this token is
   provided — via a new `--quay-token` option, an env var, or
   the URI query string. The client itself just takes a string.

3. **Logging.** Use the existing `shakenfist_utilities.logs`
   pattern (`LOG = logs.setup_console(__name__)`). Log discovery
   progress at info level: number of repos found, pagination
   progress, tag existence results. Log HTTP details at debug
   level (already handled by `util.request_url`).

4. **No glob filtering in the client.** The client returns all
   repos in the namespace. Glob filtering on repo names is a
   concern of the caller (phase 2). This keeps the client a
   clean API wrapper.

5. **Error handling.** The client should raise clear exceptions
   for:
   - Authentication failures (401) — re-raise as a descriptive
     error mentioning quay.io API tokens.
   - Network errors — let `util.request_url`'s retry logic
     handle these.
   - Unexpected API responses — raise with context.

### New file: `occystrap/tests/test_quay.py`

Unit tests using `unittest.mock.patch` to mock
`util.request_url`, following the existing test patterns in the
project:

- `test_list_repositories_single_page` — mock a response with
  no `next_page` key, verify returns correct repo names.
- `test_list_repositories_pagination` — mock two pages (first
  has `next_page`, second does not), verify all repos returned.
- `test_list_repositories_empty` — mock an empty `repositories`
  list, verify returns empty list.
- `test_list_repositories_auth` — verify that when a token is
  provided, the `Authorization` header is sent.
- `test_has_tag_exists` — mock a response with one tag, verify
  returns `True`.
- `test_has_tag_missing` — mock a response with empty `tags`
  list, verify returns `False`.
- `test_has_tag_repo_not_found` — mock a 404 `APIException`,
  verify returns `False`.
- `test_has_tag_unauthorized` — mock a 401
  `UnauthorizedException`, verify it raises with a helpful
  message.

## Commit plan

A single commit containing:
- `occystrap/quay.py` — the API client
- `occystrap/tests/test_quay.py` — unit tests
- No changes to existing files (the client is not wired into
  the CLI or pipeline yet — that is phase 2)
