# Phase 4: Documentation and functional tests

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

Fix CI failures caused by `--verify` being on by default, add
dedicated functional tests for the verification feature, and
update documentation.

## Current state

### CI failures

Three functional tests in `test_check.py` fail because
`--verify` is on by default (added in Phase 1):

- `test_check_after_normalize_timestamps`
- `test_check_after_combined_filters`
- `test_check_after_exclude_filter`

All three process to `tar://` with filters. The `process`
command returns exit code 1. The exact verification error
could not be reproduced locally, but the fix is clear:
these tests are testing the `check` command, not `--verify`.
They should pass `--no-verify` to avoid running verification
during the `process` step.

Additionally, the existing `test_check_tarball_passes` and
`test_check_tarball_fast_passes` tests process to `tar://`
without filters and pass. This suggests the verification
issue is specific to the interaction between filters and
TarWriter verification in the CI environment.

### Documentation gaps

- `docs/command-reference.md` has a one-line entry for
  `--verify` / `--no-verify` and `--verify-full` but no
  detailed explanation of what verification does, what each
  output type checks, or how errors are reported.
- `docs/performance.md` doesn't mention the performance cost
  of verification.
- No functional tests exercise the `--verify` or
  `--no-verify` flags explicitly.

## Implementation steps

### Commit 1: Fix CI failures in test_check.py

Add `--no-verify` to all `process` invocations in
`test_check.py` that are testing the `check` command, not
verification. This isolates the `check` tests from
verification behavior.

Affected tests:
- `test_check_tarball_passes` (line 138)
- `test_check_tarball_fast_passes` (line 176)
- `test_check_after_normalize_timestamps` (line 222)
- `test_check_after_exclude_filter` (line 277)
- `test_check_after_combined_filters` (line 320)
- `test_check_after_registry_roundtrip` (line 380)
- `test_check_verifies_diff_ids_match` (line 417)

All `process` calls in this file should get `--no-verify`
since this test file is about the `check` command.

### Commit 2: Add verify functional tests

Create `deploy/occystrap_ci/tests/test_verify.py` with
functional tests:

1. **test_verify_dir_passes** — Process busybox to
   `dir://`, verify passes (exit code 0), summary includes
   "verified OK".

2. **test_verify_tar_passes** — Process busybox to
   `tar://`, verify passes.

3. **test_verify_no_verify_skips** — Process with
   `--no-verify`, confirm no verification is run (no
   "verified" in summary).

4. **test_verify_full_dir** — Process busybox to `dir://`
   with `--verify-full`, verify passes.

5. **test_verify_detects_corrupt_dir** — Process busybox
   to `dir://`, delete a layer file, re-run `process` of
   a different image to the same dir with `--verify`,
   confirm exit code 1 and "verify errors" in output.
   (This is tricky — we'd need to corrupt the output
   between process and verify, which happens atomically.
   Alternative: process, corrupt, then use a second
   invocation.)

   Actually, this is hard to test since verify runs at the
   end of the same `process` invocation. A more practical
   negative test: process to `dir://`, manually delete a
   layer, then run `occystrap check` (which is separate).
   But that tests `check`, not `--verify`.

   *Skip negative functional tests for now.* The unit tests
   already cover error detection thoroughly. Functional
   tests focus on the happy path (verify passes for all
   output types).

6. **test_verify_with_filters** — Process busybox to
   `dir://` with `normalize-timestamps` filter and
   `--verify`, verify passes. This specifically tests the
   filter+verify interaction that caused CI failures.

7. **test_verify_tar_with_filters** — Same but with
   `tar://` output. This directly exercises the failing
   CI scenario.

### Commit 3: Update command-reference.md

Add a "Post-write verification" section to
`docs/command-reference.md` describing:
- What `--verify` does for each output type
- What `--verify-full` adds
- How errors are reported in the summary
- The `OCCYSTRAP_VERIFY` and `OCCYSTRAP_VERIFY_FULL`
  environment variables
- Examples of output with verification

Add a note to `docs/performance.md` about verification
overhead.

### Commit 4: Update master plan and ARCHITECTURE.md

Mark phase 4 complete. Ensure ARCHITECTURE.md is current
with all verification details.

## Commit plan

1. **Fix CI: add --no-verify to test_check.py process calls.**
2. **Add verify functional tests.**
3. **Update command-reference.md with verification docs.**
4. **Mark phase 4 complete, update ARCHITECTURE.md.**

## Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Filter+tar verify still fails in CI | Medium | Low | Functional test will catch it; debug with CI logs |
| New functional tests are flaky | Low | Low | Tests use stable busybox image from local registry |

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
