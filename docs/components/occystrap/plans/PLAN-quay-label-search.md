# Quay.io tag-based bulk image discovery and download

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

When we get to detailed planning, I prefer a separate plan file
per detailed phase. These separate files should be named for the
master plan, in the same directory as the master plan, and simply
have `-phase-NN-descriptive` appended before the `.md` file
extension. Tracking of these sub-phases should be done via a table
like this in this master plan under the Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. Registry listing API | PLAN-thing-phase-01-listing.md | Not started |
| 2. URI parsing | PLAN-thing-phase-02-uri.md | Not started |
| ...   | ...  | ...    |
```

I prefer one commit per logical change, and at minimum one commit
per phase. Do not batch unrelated changes into a single commit.
Each commit should be self-contained: it should build, pass tests,
and have a clear commit message explaining what changed and why.

## Terminology

Docker/OCI registries use specific terms that can be confusing.
Here is how they relate to each other:

```
Organization (namespace)    kolla
  └── Repository            kolla/nova-api
        ├── Tag             kolla/nova-api:latest        → manifest
        ├── Tag             kolla/nova-api:2025.1-debian → manifest
        └── Tag             kolla/nova-api:wallaby       → manifest
  └── Repository            kolla/keystone
        ├── Tag             kolla/keystone:latest        → manifest
        └── ...
```

- **Organization** (or **namespace**): a grouping of related
  repositories on the registry. On quay.io this is the top-level
  entity (e.g., `kolla`, `shakenfist`).
- **Repository**: a named image — the collection of all tagged
  versions of that image. `kolla/nova-api` is a repository. The
  quay.io API calls these "repositories" and lists them under a
  namespace.
- **Tag**: a human-readable pointer to a specific image manifest
  within a repository. `latest` and `2025.1-debian` are tags.
  A single repository can have many tags.
- **Image**: what you actually pull — a specific repository at a
  specific tag, e.g., `quay.io/kolla/nova-api:latest`.
- **Label**: key-value metadata embedded *inside* an image's
  config (set via `LABEL` in a Dockerfile). Labels are unrelated
  to tags. Reading labels requires fetching the image config blob.
  Labels are **not** used in this plan.

So when this plan says "list repositories in the org and check if
the tag exists," it means: enumerate the image names (`nova-api`,
`keystone`, `glance-api`, ...) under the `kolla` namespace, then
for each one check whether it has a tag called `latest`.

## Situation

Occystrap can currently fetch individual container images from any
Docker/OCI-compliant registry (including quay.io) using the
`registry://` URI scheme. However, there is no way to discover
images programmatically — users must already know the exact
image name and tag.

Quay.io provides a proprietary REST API (`/api/v1/`) that can
enumerate repositories within an organization and list their tags.
This API is separate from the standard Docker Registry V2 API,
which does not support organization-level enumeration (the
`/v2/_catalog` endpoint is disabled on quay.io).

The quay.io API v1 works unauthenticated for public repositories
(requires `public=true` query parameter). For private
organizations, a bearer token is required (obtained from quay.io
account settings or a robot account). This is different from the
Docker Registry V2 bearer token exchange — it is a simple
`Authorization: Bearer <token>` header using a quay.io API token.

## Mission and problem statement

Add the ability to discover and download all container images
with a given tag from a quay.io organization, optionally filtered
by a repo name glob pattern.

The core use case is: "download every image tagged `latest` (or
`2025.1-debian`, etc.) from the `kolla` organization on quay.io."

This is a tag-based bulk fetch — no image label inspection is
needed. The discovery step simply lists repos in the org via the
quay.io API v1, filters by repo name glob, and then pulls each
matching `org/repo:tag` combination using the existing registry
pipeline.

This should feel like a natural extension of occystrap's existing
URI-based pipeline. The proposed approach is a new `quay://` URI
scheme that expands to multiple `registry://` operations
internally.

### Proposed URI syntax

```
quay://ORG/GLOB:TAG
```

Components:
- `ORG` — the quay.io organization / namespace
- `GLOB` — a glob pattern matching repo names within the org
  (use `*` for all repos)
- `TAG` — the exact image tag to fetch

Examples:

```
# All repos in "kolla" org with tag "2025.1-debian"
quay://kolla/*:2025.1-debian

# Only repos starting with "centos-"
quay://kolla/centos-*:latest

# All repos with tag "latest"
quay://kolla/*:latest

# Private org (auth via --username/--password or env vars)
quay://myorg/*:latest
```

The `quay://` scheme would work as an input source with the
existing `info` and `process` commands:

```bash
# List matching images (info mode — shows metadata for each)
occystrap info quay://kolla/*:2025.1-debian

# Download matching images to a shared directory
occystrap process quay://kolla/*:2025.1-debian \
    dir://./kolla-images?unique_names=true

# Download with filters
occystrap process quay://kolla/*:latest \
    dir://./output -f normalize-timestamps
```

### How quay:// differs from registry://

The `quay://` scheme is fundamentally different from `registry://`
because it is a *multi-image* input source. A single `quay://` URI
can resolve to zero or many images. This means:

1. The `info` command needs to iterate over multiple images and
   display metadata for each one.
2. The `process` command needs to iterate over multiple images and
   run the pipeline for each one.
3. The output destination needs to handle multiple images (e.g.,
   `dir://` with `unique_names=true`, or one tarball per image).

This multi-image behavior is new — all existing input sources
produce exactly one image.

### Discovery is cheap

Because we are filtering by tag (not by image labels), discovery
does not require fetching any image configs or layer data. The
quay.io API v1 returns repo names directly, and we simply
construct `quay.io/ORG/REPO:TAG` references for each match. The
tag may or may not exist for a given repo — we can either:

- Check via the quay.io tag listing API (one extra API call per
  repo, but avoids failed pull attempts), or
- Attempt the pull and handle 404s gracefully.

The tag listing API is preferred because it avoids noisy errors
and lets us report the accurate match count upfront.

## Open questions

1. **Output handling for multi-image process**: When processing
   multiple images, how should the output destination work?
   Options:
   - `dir://` with `unique_names=true` (already supported for
     multi-image directories)
   - Auto-generate one tarball per image (e.g., `tar://` with a
     pattern like `{image}-{tag}.tar`)
   - `registry://` output (push each matching image to another
     registry)
   - Require the user to specify a directory output and
     automatically enable `unique_names`?

2. **Rate limiting**: The quay.io API does not document rate
   limits, but listing tags for hundreds of repos will generate
   traffic. Should we add:
   - A progress bar showing discovery progress?
   - A `--dry-run` equivalent that lists matches without
     downloading? (Or is `info` sufficient for this?)

3. **Authentication model**: The quay.io API v1 uses a different
   auth mechanism than the Docker Registry V2 token exchange.
   Should we:
   - Reuse `--username`/`--password` and map them to quay.io API
     tokens?
   - Add a `--quay-token` option (or `QUAY_API_TOKEN` env var)?
   - Put the token in the URI query string (`?token=...`)?
   - Note: for actually *pulling* images after discovery, we still
     need the standard Docker Registry V2 auth (which may use the
     same credentials or may be different).

4. **quay.io only, or generalizable?** The repo listing API is
   quay.io-specific. Docker Hub has a different API for listing
   repos. GitHub Container Registry (ghcr.io) has the GitHub
   Packages API. Should this be:
   - `quay://` only (pragmatic, ships fast)?
   - A more general scheme with registry-specific backends?
   - Start with `quay://` and generalize later if needed?

5. **Missing tags**: When a repo exists but does not have the
   requested tag, should we:
   - Silently skip it?
   - Log a debug/warning message?
   - Include it in `info` output with a "tag not found" note?

## Execution

This is the high-level plan. Detailed phase plans will be created
as sub-documents.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Quay.io API client | PLAN-quay-label-search-phase-01-api-client.md | Complete |
| 2. quay:// URI parsing and multi-image resolution | PLAN-quay-label-search-phase-02-uri-and-input.md | Complete |
| 3. info and process multi-image support | PLAN-quay-label-search-phase-03-commands.md | Complete |
| 4. Functional tests and documentation | PLAN-quay-label-search-phase-04-tests-docs.md | Complete |
| 5. Filter by tag age (`since` parameter) | PLAN-quay-label-search-phase-05-since-filter.md | Complete |

### Phase 1: Quay.io API client

A new module `occystrap/quay.py` that wraps the quay.io REST
API v1:

- `list_repositories(org, public=True)` — paginated listing of
  all repos in an org via
  `GET /api/v1/repository?namespace=ORG&public=true`
- `list_tags(org, repo)` — list tags for a repo via
  `GET /api/v1/repository/ORG/REPO/tag/`
- Authentication via bearer token header (optional)
- Returns simple lists of repo names and tag names

### Phase 2: quay:// URI parsing and multi-image resolution

- Add `quay` to the URI scheme registry in `uri.py`
- Add `parse_quay_uri()` returning (org, repo_glob, tag, options)
- A resolver function (in `quay.py` or a new module) that:
  1. Lists repos in the org via phase 1 API client
  2. Filters repo names by the glob pattern using `fnmatch`
  3. For each matching repo, checks if the requested tag exists
     via the tag listing API
  4. Returns a list of `(registry, image, tag)` tuples ready
     for the existing `registry.Image` input

This is *not* a new `ImageInput` subclass — it is a resolver
that produces a list of standard `registry://` references. The
actual image fetching uses the existing `registry.Image` class.

### Phase 3: info and process multi-image support

- When `info` receives a `quay://` URI, resolve it to matching
  images, then iterate and display metadata for each:
  - Text output: one section per image, separated by blank lines
  - JSON output: array of info objects
- When `process` receives a `quay://` URI, resolve it to
  matching images, then run the pipeline for each:
  - `dir://` with `unique_names=true` is the natural fit
  - `registry://` pushes each image (preserving original name)
  - `tar://` could generate per-image filenames or error
- Progress reporting: "Processing image N of M: org/repo:tag"

### Phase 4: Functional tests and documentation

- Functional tests in `deploy/occystrap_ci/tests/` following the
  existing testtools+stestr pattern, exercising the full quay://
  pipeline against a real or mocked quay.io API
- Update `docs/command-reference.md` with quay:// examples
- Update `ARCHITECTURE.md` with the new quay.io client and
  multi-image resolution flow
- Update `README.md` with feature description
- Update `AGENTS.md`

Note: unit tests for the API client, URI parsing, resolver, and
command integration were delivered in phases 1-3 (32 tests total).

### Phase 5: Filter by tag age (`since` parameter)

- Add `?since=YYYY-MM-DD` query parameter to the `quay://` URI
  to filter out images whose tag is older than the given date
- Change `has_tag()` to return tag metadata (including `start_ts`)
  instead of a boolean, so the caller can filter by age
- Add `since` parameter to `resolve_quay_uri()`
- Update unit tests, functional tests, and documentation

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `flake8 --max-line-length=120` and
  `pre-commit run --all-files`.
* New code follows the existing pipeline pattern (input/filter/
  output interfaces) where applicable.
* There are unit tests for core logic and integration tests for
  new CLI commands.
* Lines are wrapped at 120 characters, single quotes for strings,
  double quotes for docstrings.
* `occystrap info quay://ORG/*:TAG` lists all images in the org
  that have the specified tag, with metadata for each.
* `occystrap process quay://ORG/*:TAG dir://out?unique_names=true`
  downloads all matching images.
* The quay.io API client handles pagination, authentication
  (optional), and error cases gracefully.
* Repos that exist but lack the requested tag are skipped
  gracefully.
* Documentation in `docs/` has been updated to describe the new
  `quay://` URI scheme and its usage.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` have been
  updated.

### Future work

* Support for other registry listing APIs (Docker Hub, ghcr.io)
  via additional URI schemes or a pluggable backend.
* Tag glob patterns (e.g., `2025.1-*`) for matching multiple
  tags per repo.
* Image label filtering — fetch configs during discovery and
  filter by label key/value (the original "label" idea).
* Parallel discovery (concurrent tag-existence checks across
  repos) for large organizations.
* `--dry-run` flag that lists matching images without pulling.
* Cache discovery results to avoid re-querying the quay.io API
  on repeated invocations.

### Pre-existing issues noted during security review

These are not introduced by this plan but were noted during the
push review:

* `outputs/mounts.py` line 204: `util.execute()` uses
  `shell=True` with string interpolation. Layer paths are
  validated via `safe_path_join()` but shell metacharacters in
  digest strings are not escaped. Should use `subprocess.run()`
  with a list of arguments or `shlex.quote()`.
* `inputs/registry.py` line 95: The `realm` URL from the
  `WWW-Authenticate` header is used as a request target without
  validation. A malicious registry could point it to an internal
  service (SSRF). Credentials (if set) would be sent via Basic
  Auth to the attacker-controlled URL.
* `util.py` line 60: Bearer tokens are logged in cleartext at
  DEBUG level. Should redact `Authorization` headers.

### Bugs fixed during this work

* `quay.py`: `TypeError` crash when quay.io API returns
  `last_modified: null` for a repository (fixed by using
  `or 0` instead of dict default).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
