# Phase 2: TarWriter and DockerWriter verifiers

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

Implement `verify()` for `TarWriter` and `DockerWriter` so
that `process` with `tar://` and `docker://` output destinations
verifies the written output.

## Current state

### TarWriter after finalize()

- `self.image_path` — path to the written tarball file.
- `self.image_tar` — closed `tarfile.TarFile` object (not
  usable for reading).
- `self.tar_manifest[0]['Config']` — config entry name.
- `self.tar_manifest[0]['Layers']` — list of layer entry
  names (e.g., `['def456/layer.tar']`).

The tarball at `self.image_path` is a USTAR-format tar
containing:
- `manifest.json` — JSON array with one manifest dict.
- `{config_hash}.json` — config file.
- `{layer_hash}/layer.tar` — one per layer.

No expectation tracking exists yet (no `_expected_layers`
dict like DirWriter has).

### DockerWriter after finalize()

- The temporary tarball has been **deleted** (line 192-193).
- The image was loaded into the Docker daemon via
  `POST /images/load`.
- `self.image` and `self.tag` identify the loaded image.
- `self.socket_path` provides the Docker socket path.
- `self._tar_manifest[0]` has the same structure as
  TarWriter.

Since the temp tarball is deleted after `docker load`,
TarWriter-style verification (re-reading the tarball) is
not possible. Verification must query the Docker daemon.

### Phase 1 infrastructure

- `ImageOutput.verify(full=False)` — default no-op returning
  empty `CheckResults`.
- `_process_single()` calls `writer.verify()` after finalize
  and write_bundle, adds `verify_errors` to stats dict.
- `_print_summary()` shows "verified OK" or "N verify errors".

## Design

### TarWriter.verify()

Re-open the tarball read-only and check its contents against
what was written.

**Default mode (`full=False`):**
1. Open `self.image_path` with `tarfile.open(mode='r')`.
2. Read and parse `manifest.json`.
3. Check `manifest.json` is a non-empty list with `Config`
   and `Layers` keys.
4. Check the config entry exists in the tarball.
5. Check each layer entry in `Layers` exists in the tarball.
6. Compare entry count: manifest should list the same number
   of layers as `self.tar_manifest[0]['Layers']`.

**Full mode (`full=True`):**
All of the above, plus:
7. Extract and validate each layer entry is a valid tarball
   (open as tar-in-tar and call `getmembers()`).

**Expectation tracking:**
TarWriter doesn't need a separate `_expected_layers` dict
because all expectations are available from
`self.tar_manifest` after finalize. The tarball's internal
manifest should match `self.tar_manifest`.

### DockerWriter.verify()

Query the Docker daemon to confirm the image was loaded.

**Default mode (`full=False`):**
1. Query `GET /images/{image}:{tag}/json` via the Docker
   socket API.
2. Check for HTTP 200 (image exists).
3. Compare the config digest: the response's `Id` field
   should match `sha256:{config_hash}` where config_hash
   is derived from `self._tar_manifest[0]['Config']`
   (strip `.json` suffix).

**Full mode (`full=True`):**
Same as default — there's no additional data to verify from
the Docker API without pulling the image back out, which
would be prohibitively expensive and defeats the purpose.

**Constraint:** Verification requires the Docker socket to be
accessible. If the socket is not available (e.g., in a test
environment), `verify()` should report a warning rather than
an error, since the image load itself would have already
failed.

## Implementation steps

### Step 1: Implement TarWriter.verify()

Add `verify(full=False)` to `TarWriter` in
`outputs/tarfile.py`:

- Re-open `self.image_path` as read-only tar.
- Extract and parse `manifest.json`.
- Check config and all layers exist as tar entries.
- Full mode: validate each layer entry is a valid tar.
- Handle `FileNotFoundError` (tarball missing) and
  `tarfile.TarError` (corrupt tarball) gracefully as
  verification errors.

### Step 2: Implement DockerWriter.verify()

Add `verify(full=False)` to `DockerWriter` in
`outputs/docker.py`:

- Query Docker API for the image.
- Check HTTP 200 response.
- Compare image ID against expected config digest.
- Handle connection errors (socket not available) as
  warnings.

### Step 3: Add unit tests

**TarWriter tests:**
- Correct tarball passes verification.
- Missing tarball detected.
- Tarball with missing layer entry detected.
- Full mode detects corrupt layer entry.

**DockerWriter tests:**
- Mock the Docker API to return success.
- Mock the Docker API to return 404 (image missing).
- Mock the Docker API to be unreachable (warning, not
  error).

### Step 4: Update documentation

Update ARCHITECTURE.md to note TarWriter and DockerWriter
verification. Update the phase 2 status in the master plan.

## Commit plan

1. **Implement TarWriter and DockerWriter verification.**
   Add verify() to both writers with unit tests.

2. **Update documentation for phase 2.**

## Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Tarball re-read is slow for large images | Low | Low | Default mode doesn't read layer data, just lists entries |
| Docker socket not available in tests | Medium | None | Mock the requests_unixsocket session |
| Docker API returns unexpected format | Low | Low | Check key fields defensively |

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
