# Phase 5: Filter by tag age (`since` parameter)

## Context

This is phase 5 of the
[quay.io tag-based bulk image discovery plan](/components/occystrap/plans/PLAN-quay-label-search/).

Some quay.io organizations have thousands of repositories
accumulated over years of naming scheme changes. The user needs
a way to limit discovery results to recently-updated images,
avoiding the cost of checking tags on stale repositories.

## Goal

Add a `since` query parameter to the `quay://` URI that filters
out images whose tag was created or updated before a given date.

### URI syntax

```
quay://ORG/GLOB:TAG?since=YYYY-MM-DD
```

Examples:

```bash
# Only images tagged after 2024-01-01
occystrap info "quay://kolla/*:latest?since=2024-01-01"

# Combine with token
occystrap info "quay://myorg/*:latest?since=2025-06-01&token=abc"
```

## Implementation

### Data available from the quay.io API

The tag listing endpoint already returns timestamp data in each
tag object:

```json
{
  "name": "latest",
  "start_ts": 1774047466,
  "last_modified": "Fri, 20 Mar 2026 22:57:46 -0000"
}
```

`start_ts` is a Unix timestamp representing when the tag was
created or last updated. We already fetch this data in
`has_tag()` — we just discard everything except whether the
tags list is non-empty.

### Change 1: `QuayClient.has_tag()` returns tag info

Change `has_tag()` to return tag metadata instead of a boolean:

```python
def has_tag(self, namespace, repo, tag):
    """Check whether a repository has a specific active tag.

    Returns:
        A dict with tag metadata (name, start_ts,
        manifest_digest, last_modified) if the tag exists
        and is active, or None if it does not exist or the
        repository is not found.
    """
```

Return `None` instead of `False`, and the tag dict instead of
`True`. This is a compatible change for the resolver since
`None` is falsy and a dict is truthy — so existing `if
client.has_tag(...)` checks still work. However, the unit tests
that assert `self.assertTrue(result)` or
`self.assertFalse(result)` should be updated to use
`self.assertIsNotNone(result)` and `self.assertIsNone(result)`
for clarity.

### Change 2: `resolve_quay_uri()` accepts `since`

Add a `since` parameter (a `datetime.date` or `None`):

```python
def resolve_quay_uri(namespace, repo_glob, tag,
                     token=None, since=None):
```

When `since` is set:
1. `has_tag()` returns the tag metadata dict (or `None`).
2. The resolver checks `tag_info['start_ts']` against
   `since` converted to a Unix timestamp.
3. If the tag is older than `since`, the repo is skipped.
4. Log the skip: "Skipping org/repo: tag 'latest' is from
   2021-03-15, before since=2024-01-01"

When `since` is `None`, the behaviour is unchanged.

### Change 3: URI parsing

In `_resolve_quay_images()` in `main.py`, extract the `since`
option from the parsed URI options and convert it to a
`datetime.date`:

```python
since_str = options.get('since')
if since_str:
    since = datetime.date.fromisoformat(since_str)
else:
    since = None
```

Pass `since` through to `resolve_quay_uri()`.

No changes to `uri.py` or `parse_quay_uri()` are needed —
`since` is just a query parameter that `parse_uri()` already
handles via the generic query string parser.

### Change 4: Unit tests in `test_quay.py`

Update existing tests:
- `test_tag_exists` — assert returns a dict with `name` and
  `start_ts` keys (not just truthy).
- `test_tag_missing` — assert returns `None` (not just falsy).
- `test_repo_not_found` — assert returns `None`.

New tests:
- `test_resolve_since_filters_old_tags` — mock `has_tag` to
  return tag info with `start_ts` in 2021. Set `since` to
  2024-01-01. Verify the repo is excluded from results.
- `test_resolve_since_includes_new_tags` — mock `has_tag` to
  return tag info with `start_ts` in 2025. Set `since` to
  2024-01-01. Verify the repo is included.
- `test_resolve_since_none_skips_filter` — verify that
  `since=None` does not filter anything (backwards compatible).
- `test_parse_quay_uri_with_since` — verify
  `quay://kolla/*:latest?since=2024-01-01` includes
  `{'since': '2024-01-01'}` in options.

### Change 5: Functional test in `test_quay_bulk.py`

Add one new test hitting the real quay.io API:
- `test_info_quay_since_filters_old` — Run `info` with a
  `since` date far in the future (e.g., `since=2099-01-01`).
  Verify no images are returned (all tags are older than 2099).
  This tests the full pipeline without depending on specific
  tag timestamps.

### Change 6: Documentation

Update `docs/command-reference.md`:
- Add `since=YYYY-MM-DD` to the quay:// query options table.
- Add an example showing the `since` parameter.

### Iteration: early filtering at repo listing stage

After initial testing against a quay.io organization with 1,876
repositories, we discovered that applying `since` only at the
tag-check stage was too slow — it still required 1,876 individual
`has_tag()` API calls before any filtering happened.

The quay.io repository listing API supports a `last_modified=true`
query parameter that returns a `last_modified` Unix timestamp on
each repository object. This timestamp reflects when *any* tag
in the repository was last updated.

**Change 1b: `list_repositories()` accepts `since_ts`**

When `since_ts` is provided:
1. The `last_modified=true` parameter is added to the API request.
2. During pagination, repos with `last_modified < since_ts` are
   skipped immediately — they never enter the list.
3. The log message reports how many repos were skipped.

This filtering happens at the listing stage (pages of 100), so
the expensive per-repo `has_tag()` calls are only made for repos
that have been recently modified. For the 1,876-repo org, this
reduced the candidate set to a fraction of the total.

The tag-level `since` check in `resolve_quay_uri()` is retained
as a second filter. The repo-level `last_modified` is "any tag
was updated recently", but the specific tag we want might still
be older than `since`. The two-stage filter ensures correctness:
repo-level for speed, tag-level for precision.

**Additional tests:**
- `test_since_ts_filters_old_repos` — mock repos with different
  `last_modified` timestamps, verify old ones are excluded.
- `test_since_ts_none_returns_all` — verify no filtering and no
  `last_modified=true` in the URL when `since_ts` is None.

## Changes summary

| File | Changes |
|------|---------|
| `occystrap/quay.py` | `has_tag()` returns dict or None; `list_repositories()` accepts `since_ts`; `resolve_quay_uri()` accepts `since` and passes `since_ts` to listing |
| `occystrap/main.py` | `_resolve_quay_images()` parses `since` from URI options |
| `occystrap/tests/test_quay.py` | Update 3 existing tests, add 6 new tests |
| `deploy/occystrap_ci/tests/test_quay_bulk.py` | Add 1 new functional test |
| `docs/command-reference.md` | Add `since` to quay:// docs |

## Commit plan

Two commits:
1. Initial `since` implementation (tag-level filtering)
2. Early filtering at repo listing stage (after testing revealed
   the performance issue)
