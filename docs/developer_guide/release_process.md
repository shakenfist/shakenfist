Shaken Fist's release process
=============================

Shaken Fist uses GitHub Actions for automated releases. The process uses
Sigstore for signing (no GPG keys required) and PyPI Trusted Publishers for
authentication (no API tokens required).

## Prerequisites

Before you can trigger a release, the following one-time setup must be
completed (see `RELEASE-SETUP.md` in the repository root for details):

1. PyPI Trusted Publisher configured for the repository
2. GitHub Environment named `release` with required reviewers
3. (Optional) Protected tag rules for `v*` tags

## Testing

We only release things which have passed CI testing, and preferably have had a
period running as the underlying cloud for the CI cluster as well. The first
half of that is enforced: the release workflow's `ci-gate` job refuses a tag
unless `functional-tests.yml` succeeded on the tagged commit (see
[The CI gate](#the-ci-gate) below). A hotfix goes through the merge queue like
any other change.

## Triggering a release

### Step 1: Determine the version number

Check the existing tags to find the previous version:

```bash
git tag | grep "^v" | sort -V | tail -5
```

### Step 2: Create and push the version tag

Tag a commit that is on `develop` and went through the merge queue, and wait
for its `functional-tests.yml` run in the queue to finish first.

```bash
git checkout develop  # or the appropriate branch
git pull
git tag v0.X.Y -m "Release v0.X.Y"
git push origin v0.X.Y
```

This triggers the release workflow but does **not** publish anything yet.

### Step 3: Approve the release

1. Go to the repository on GitHub
2. Navigate to **Actions** > **Release** (the running workflow)
3. The workflow will pause at the `sign-tag` job waiting for approval
4. Click **Review deployments**
5. Select the `release` environment and click **Approve and deploy**

### Step 4: Monitor the release

The workflow will:

1. Check that CI passed on the tagged commit (`ci-gate`)
2. Build the Python package, run `twine check` and the wheel contents check
   on it, and build the Ansible collection
3. Sign the release tag using Sigstore/gitsign
4. Generate attestations for the built artifacts
5. Publish the package to PyPI and the collection to Ansible Galaxy
6. Create a GitHub Release with the package files attached

You can monitor progress in the Actions tab. If any step fails, the workflow
stops and you can investigate the logs.

## The CI gate

`ci-gate` is the first job in `release.yml`, and both build jobs need it. It
uses
[`require-ci-success`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#require-ci-success)
to check that `functional-tests.yml` succeeded on the tagged commit. The run
that counts is the commit's merge queue run: a run started by hand, as
`pr-retest.yml` does with `workflow_dispatch`, does not. So a change merged
around the queue, by an administrator bypassing it, cannot be released until
a later commit has gone through the queue. How the gate decides is documented
with the action, and it has no override.

A gate failure means nothing was built, signed or published, so every failure
is recoverable. The release automation template's
[recovery table](https://github.com/shakenfist/development/blob/main/templates/release-automation/README.md#recovering-from-a-gate-failure)
says what to do for each cause. Usually that is moving the tag to a commit
that went through the queue, or, for a flaky queue run, using "Re-run jobs"
on that merge queue run itself and then re-running the `ci-gate` job.

The wheel contents check in `build` is
[`check-dist`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#check-dist).
It fails if the wheel holds a test directory or nothing at all. The wheel
ships the `shakenfist` packages and `shakenfist/data/*.yaml`, and nothing
under `shakenfist/tests/` or `shakenfist/deploy/`: the unit tests, the Ansible
collection and the functional CI suites are all used from a checkout.

Running `release.yml` by hand with `workflow_dispatch` skips the gate and the
publishing jobs, and only builds and checks the packages.

## What gets released

- **PyPI**: The `shakenfist` Python package
- **Ansible Galaxy**: The `shakenfist.shakenfist` collection
- **GitHub Releases**: The package files

## Verifying a release

### Verify the signed tag

Anyone can verify the release tag signature using gitsign:

```bash
git fetch --tags
gitsign verify \
    --certificate-identity-regexp='.*' \
    --certificate-oidc-issuer='https://token.actions.githubusercontent.com' \
    v0.X.Y
```

### Verify PyPI package attestation

PyPI shows attestation status on the package page under the "Provenance"
section. You can also verify locally:

```bash
pip download shakenfist==0.X.Y --no-deps
cosign verify-attestation \
    --certificate-identity-regexp='.*' \
    --certificate-oidc-issuer='https://token.actions.githubusercontent.com' \
    shakenfist-0.X.Y.tar.gz
```

## Troubleshooting

### `ci-gate` fails

- Nothing was published. Follow the recovery table linked from
  [The CI gate](#the-ci-gate) above.

### Workflow doesn't trigger

- Ensure the tag matches the pattern `v*` (e.g., `v0.8.0`, not `0.8.0`)
- Check that you pushed to the correct remote

### Approval not requested

- Verify the `release` environment exists in repository settings
- Check that required reviewers are configured

### PyPI publish fails

- Verify the Trusted Publisher is configured correctly in PyPI
- Ensure the workflow filename and environment name match exactly

### Tag signing fails

- This usually indicates an issue with the Sigstore service or OIDC token
- Check the workflow logs for specific error messages
- Retry the workflow if it's a transient issue

## Rolling back a release

If a release needs to be rolled back:

1. **PyPI**: You cannot delete releases from PyPI, but you can yank them:
    - Go to pypi.org > Your project > Releases
    - Click the version and select "Yank"
    - Yanked versions won't be installed by default but remain available

2. **GitHub**: Delete the release and tag if needed:
    ```bash
    git push origin :refs/tags/v0.X.Y
    ```
    Then delete the release from the GitHub Releases page.

## Legacy process

The old `release.sh` script required a private VM with GPG keys and PyPI
credentials. This has been replaced by the GitHub Actions workflow which uses
keyless signing and OIDC authentication.
