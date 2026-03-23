# Phase 4: Functional tests and documentation

## Context

This is phase 4 of the
[quay.io tag-based bulk image discovery plan](/components/occystrap/plans/PLAN-quay-label-search/).
Phases 1-3 implemented the quay.io API client, URI parsing,
multi-image resolution, and command integration. This phase adds
functional tests and updates all documentation.

## Goal

1. Add functional tests that exercise the quay:// pipeline
   end-to-end.
2. Update all documentation to describe the new feature.

## Functional tests

### Test strategy

The existing functional tests in `deploy/occystrap_ci/tests/`
hit a local Docker registry at `localhost:5000`. For quay://
tests, we will hit the real quay.io API.

The quay.io API is public for public organizations and does not
require authentication. We will use a well-known public org
(e.g., `projectquay`) and query for a tag that is expected to
exist on at least one repository. This gives us a true
end-to-end integration test that exercises the full pipeline:
quay.io API discovery, glob filtering, tag existence checks,
and image metadata fetching.

The `info` command is the natural fit for these tests — it
fetches manifests and configs but does not download layer blobs,
keeping the tests fast and light on network traffic. The
`process` command tests will still use the local registry via
mocked resolution, since downloading real images from quay.io
in CI would be slow and bandwidth-heavy.

If quay.io is unreachable in CI, these tests will fail. This is
acceptable — we want to know if the integration is broken. If
flakiness becomes a problem in practice, we can revisit.

### Test file

New file: `deploy/occystrap_ci/tests/test_quay_bulk.py`

Following the existing functional test pattern:
- Uses `testtools.TestCase`
- Uses `CliRunner` from Click

### Test cases

**Real quay.io tests (info command):**

These tests hit the real quay.io API and fetch image metadata
(manifests and configs, no layer blobs) from quay.io. They use
a well-known public org like `projectquay` with a repo glob
that matches at least one repository with a known tag.

1. **`test_info_quay_text`** — Run `info` with a real `quay://`
   URI (e.g., `quay://projectquay/quay*:latest`). Verify output
   contains at least one image and includes expected fields
   (image name, layer count).

2. **`test_info_quay_json`** — Same as above but with `-O json`.
   Verify output is a valid JSON array with at least 1 entry,
   each having `image`, `tag`, `layer_count` fields.

3. **`test_info_quay_no_matches`** — Run `info` with a real
   quay:// URI that will not match anything (e.g., a nonsense
   glob or a tag that does not exist). Verify the command
   exits 0 and prints "No images found".

**Mocked resolution tests (process command):**

These tests mock `_resolve_quay_images` to return tuples
pointing at the local CI registry (`localhost:5000`). This
avoids downloading real layer blobs from quay.io while still
testing the multi-image pipeline end-to-end.

4. **`test_process_quay_to_dir`** — Mock resolution to return
   2 images from `localhost:5000` (e.g., `library/busybox` and
   `library/hello-world`). Run `process` with `quay://` source
   and `dir://...?unique_names=true` destination. Verify the
   output directory contains manifest files for both images.

5. **`test_process_quay_tar_rejected`** — Mock resolution to
   return 1 image. Run `process` with `tar://` destination.
   Verify exit code is non-zero with an error message about
   tar:// not supporting multi-image sources.

## Documentation updates

### `docs/command-reference.md`

1. Add `quay://` to the **Input URI Schemes** section (new
   subsection after `registry://`).
2. Add `quay://` to the input URI list in the `process` command.
3. Add `quay://` to the input URI list in the `info` command.
4. Add examples showing multi-image usage.

### `ARCHITECTURE.md`

1. Add a new subsection describing the quay.io client module
   (`occystrap/quay.py`) and its role.
2. Add `quay.py` and `tests/test_quay.py` to the directory
   listing.
3. Document the multi-image resolution flow (quay:// URI →
   resolver → list of registry:// references → existing
   pipeline per image).

### `README.md`

1. Add `quay://ORG/GLOB:TAG` to the Input URI Schemes list.
2. Add a brief example showing bulk fetch from quay.io.

### `AGENTS.md`

1. Add a bullet point about the `quay://` multi-image input
   pattern — noting that it is not an `ImageInput` subclass
   but a resolver that expands to multiple `registry://`
   operations.

### `docs/plans/index.md`

Already updated in phase 1 commit. No changes needed.

## Commit plan

Two commits:
1. Functional tests (`deploy/occystrap_ci/tests/test_quay_bulk.py`)
2. Documentation updates (all doc files in one commit)
