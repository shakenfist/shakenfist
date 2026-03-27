# Post-write verification for output integrity

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
| 2. Label filtering | PLAN-thing-phase-02-labels.md | Not started |
| ...   | ...  | ...    |
```

I prefer one commit per logical change, and at minimum one commit
per phase. Do not batch unrelated changes into a single commit.
Each commit should be self-contained: it should build, pass tests,
and have a clear commit message explaining what changed and why.

## Situation

Occystrap recently gained significant parallelism (httpx with
HTTP/2, concurrent multi-image processing, parallel layer
downloads and uploads). This makes bulk operations much faster
but also increases the chance that transient errors (network
glitches, rate-limiting, disk I/O issues) could silently produce
incomplete output.

### What exists today for confidence

**Processing summary (added in the performance overhaul):**
The `process` command now prints a summary line after completion:

```
Summary: 47/47 images, 312 layers, 4.2 GB, 38.1s, 2 retries
No failed images.
```

This shows aggregate stats including retry counts and rate-limit
events, plus an explicit "No failed images" confirmation for bulk
operations. The `RequestStats` class in `util.py` tracks retries
and rate-limit events across all threads. This addresses the
*visibility* problem — users can see at a glance whether anything
went wrong — but does not address the *correctness* problem of
verifying what was actually written to disk or pushed to a
registry.

**`check.py` module:**
- `CheckResults` class: accumulates errors/warnings/info with
  `error()`, `warning()`, `info()` methods and `has_errors`
  property.
- `check_metadata(manifest, config, results)`: fast-mode
  validation of manifest structure, schema version, layer count
  consistency, compression compatibility, media types.
- `check_layers(input_source, manifest, config, results)`:
  full validation that downloads layers and verifies diff_ids,
  tar format, whiteout correctness.

**`check` CLI command:**
- Takes a source URI and runs `check_metadata` (always) plus
  `check_layers` (unless `--fast`).
- Reports results in text or JSON format.
- Only works against *input* sources (registries, tarballs,
  Docker daemon). Does **not** support `dir://` as a check
  source.

**Output writers (`finalize()` state):**
- `DirWriter`: writes layers as files in subdirectories, writes
  `manifest-{name}-{tag}.json` and updates `catalog.json`.
  Uses `os.rename()` for zero-copy layer placement when
  `temp_path` is available.
- `TarWriter`: writes layers and manifest into a tarball,
  closes the tarball.
- `RegistryWriter`: pushes blobs and manifest to a registry,
  reports upload stats. Already checks blob existence before
  upload via HEAD requests.
- `DockerWriter`: builds a tarball and POSTs to Docker API.
- `OCIBundleWriter` / `MountWriter`: extend DirWriter with
  OCI bundle / overlay extraction.

**Base output tracking (`ImageOutput`):**
- `_track_element(type, size)`: counts layers and bytes.
- `_total_bytes`, `_layer_count`: available after processing.
- Stats returned from `_fetch()` as a dict with bytes, layers,
  retries, and rate_limits.

None of the output writers verify their own output after
writing. The pipeline trusts that if no exception was raised,
the output is correct.

## Mission and problem statement

Add a `--verify` / `--no-verify` flag to the `process` command
that runs post-write verification after each image completes.
Verification confirms that the output is complete and correct
by reading back what was written and checking it against what
should have been written.

The goal is confidence, not exhaustive validation. A user
running a bulk mirror of 200 images should be able to look at
the output and know definitively whether every image landed
correctly — without manually running `check` on each one.

The verification should be:
- **On by default** — users shouldn't have to opt in to
  correctness. `--no-verify` disables it for speed.
- **Output-type-specific** — each output format has different
  things to verify.
- **Non-destructive** — verification reads but never modifies
  the output.
- **Efficient** — avoid re-downloading or re-reading more data
  than necessary. Default mode checks existence and sizes.
  Full mode re-reads and hashes.
- **Integrated with the summary** — verification results feed
  into the existing processing summary line.

## Design decisions

1. **`--verify` is on by default** with `--no-verify` to
   disable. The performance cost is small relative to the
   transfer, and the confidence benefit is high.

2. **Two verification levels.** The default `--verify` checks
   file/blob existence and sizes (fast). `--verify=full` also
   re-reads and hashes every layer (thorough but slower). This
   mirrors the `check` command's `--fast` vs full distinction.

3. **Each output writer records its expectations during
   processing.** The writer knows what files/blobs it wrote
   and at what sizes. Verification checks reality against
   those expectations. This sidesteps the filter interaction
   problem entirely — filters transform content before the
   writer sees it, so the writer's expectations already
   reflect the filtered output.

4. **Verification failures cause non-zero exit code.** Exit
   code 0 means all images processed *and* verified. Exit
   code 1 means processing or verification failure.

5. **Verification results integrate with the existing summary
   line.** After `Summary: 47/47 images, 312 layers, ...` the
   verification adds `47/47 verified` or
   `45/47 verified, 2 FAILED`. The `_print_summary` function
   already accepts these counters.

6. **`verify()` is a concrete method on `ImageOutput`, not
   abstract.** The default implementation returns success
   (no-op). Output writers override it to add type-specific
   checks. This avoids requiring every output writer and
   filter to implement an empty method.

## Open questions

1. **Should `check dir://` be added as part of this work?**

   The existing `check` command only supports input URIs
   (registry, tar, docker). Adding `dir://` as a check source
   would allow `occystrap check dir:///path/to/output` as a
   standalone operation, separate from the `--verify` flag on
   process. This would be useful but is a larger change to the
   input infrastructure.

   *Recommendation:* Defer to future work. The `--verify` flag
   on `process` covers the primary use case. Adding `dir://`
   as a check source is a separate plan.

2. **Should registry verification re-fetch the manifest or
   just HEAD the blobs?**

   *Recommendation:* Default mode: HEAD each blob and GET the
   manifest to compare against what was pushed. Full mode:
   additionally GET each blob and hash it. The manifest GET is
   cheap and catches manifest push failures.

## Execution

### Phase 1: Verification framework and DirWriter verifier

Add `--verify` / `--no-verify` flags to the `process` command.
Add a `verify()` method to `ImageOutput` (default: return
empty `CheckResults`). Each writer records expectations during
`process_image_element()` and checks them in `verify()`.

Implement for `DirWriter` (the most common output for bulk
operations):

- Check manifest file exists and is valid JSON.
- Check config file exists.
- Check each layer directory and `layer.tar` file exists.
- Check each layer file size matches what was recorded during
  write.
- Full mode: re-read and SHA256-hash each layer file.

`OCIBundleWriter` and `MountWriter` inherit from `DirWriter`
and get its verification for free.

Wire `verify()` into `_fetch()` so it runs after `finalize()`.
Add verification counts to the stats dict returned by `_fetch`
and to `_print_summary`.

### Phase 2: TarWriter and DockerWriter verifiers

**TarWriter:**
- Re-open the tarball read-only and list entries.
- Check manifest.json, config file, and all layer tarballs
  are present.
- Check sizes match recorded expectations.
- Full mode: re-read and hash layers within the tarball.

**DockerWriter:**
- Query Docker API (`/images/{id}/json`) to confirm the image
  was loaded.
- Check image ID matches expected config digest.

### Phase 3: RegistryWriter verifier

- HEAD each layer blob to confirm it exists in the registry.
- HEAD the config blob.
- GET the manifest and compare against what was pushed (byte
  comparison of the JSON body).
- Full mode: GET each blob and hash it.

Note: `RegistryWriter` already does a blob-exists HEAD check
*before* upload to skip existing blobs. Verification is the
complementary check *after* the full push completes, confirming
the manifest and all blobs are reachable.

### Phase 4: Documentation and functional tests

- Update `docs/command-reference.md` with `--verify` /
  `--no-verify` / `--verify=full` documentation.
- Update README, ARCHITECTURE.md.
- Add functional tests:
  - `test_verify_dir.py`: process to dir, verify passes.
  - `test_verify_tar.py`: process to tar, verify passes.
  - `test_verify_registry.py`: process to registry, verify
    passes.
  - Negative tests: corrupt output, verify detects failure.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Verification framework and DirWriter | PLAN-post-write-verification-phase-01-framework.md | Complete |
| 2. TarWriter and DockerWriter verifiers | PLAN-post-write-verification-phase-02-tar-docker.md | Complete |
| 3. RegistryWriter verifier | PLAN-post-write-verification-phase-03-registry.md | Complete |
| 4. Documentation and functional tests | PLAN-post-write-verification-phase-04-docs-tests.md | Complete |

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
* Documentation in `docs/` has been updated to describe any new
  commands or features.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` have been
  updated if the change adds or modifies modules or CLI commands.
* `process --verify` is on by default and exits non-zero on
  verification failure.
* Summary line includes verification counts.
* Each output writer has a type-specific verify() implementation.
* Functional tests cover both positive and negative verification
  cases.

### Future work

- Add `dir://` as a source for the `check` command so that
  `occystrap check dir:///path/to/output` works standalone.
- Add a `--verify-only` mode that re-verifies a previously
  written output without reprocessing.
- Verification for the `proxy` command's downstream writes.
- Checksums file (e.g., `SHA256SUMS`) written alongside
  directory output for external verification tools.
- Pre-existing security issues found during the performance
  audit: URL encoding in auth scope parameters, auth token
  redaction in debug logs. These are not related to
  verification but were noted in the audit.

### Bugs fixed during this work

(None yet.)

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
