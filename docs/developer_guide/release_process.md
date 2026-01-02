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
period running as the underlying cloud for the CI cluster as well. Sometimes in
an emergency we will bend the rules for a hotfix, but we should try and avoid
doing that.

## Triggering a release

### Step 1: Determine the version number

Check the existing tags to find the previous version:

```bash
git tag | grep "^v" | sort -V | tail -5
```

### Step 2: Create and push the version tag

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

1. Build the Python package and archives (deploy.tgz, docs.tgz)
2. Sign the release tag using Sigstore/gitsign
3. Generate attestations for the built artifacts
4. Publish to PyPI
5. Create a GitHub Release with all artifacts attached

You can monitor progress in the Actions tab. If any step fails, the workflow
stops and you can investigate the logs.

## What gets released

- **PyPI**: The `shakenfist` Python package
- **GitHub Releases**: The package files plus:
    - `deploy.tgz` - Deployment tooling archive
    - `docs.tgz` - Documentation archive

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
