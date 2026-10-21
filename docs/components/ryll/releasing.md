# Releasing

Cutting a release is a two-phase operation so the version bump
goes through review and the merge queue rather than landing
directly on `develop`:

1. **Stage 1 — propose:**
   [tools/propose-release.sh](https://github.com/shakenfist/ryll/blob/develop/tools/propose-release.sh)
   (wrapped by `make propose-release X.Y.Z`) creates a
   `release-X.Y.Z` branch from `develop`, bumps the workspace
   version, runs pre-commit and the tests, pushes the branch and
   opens a pull request. You get it reviewed and merge it through
   the merge queue like any other change.
2. **Stage 2 — tag:** after the PR has merged,
   [tools/tag-release.sh](https://github.com/shakenfist/ryll/blob/develop/tools/tag-release.sh) (wrapped by
   `make tag-release X.Y.Z`) finds the commit that merged the
   release, verifies it carries the expected workspace version,
   creates an annotated `vX.Y.Z` tag at that commit, and pushes
   the tag.

The tag push triggers
[.github/workflows/release.yml](https://github.com/shakenfist/ryll/blob/develop/.github/workflows/release.yml),
which:

- Checks that `ci.yml` passed on the tagged commit, and stops
  before building anything if it did not (see
  [The release gate](#the-release-gate)).
- Verifies the tag matches the workspace version, and that
  `Cargo.lock` agrees with it.
- Builds release binaries on Linux (`x86_64` and `aarch64`),
  macOS (Apple Silicon), and Windows (`x86_64` and `aarch64`),
  running the test suite on each.
- Produces `.deb` and `.rpm` (both Linux arches), macOS
  `.tar.gz`, and Windows `.zip` (both Windows arches) artifacts.
- Builds the per-arch `ryll` PyPI wheel natively for `x86_64` and
  `aarch64` (no QEMU), and checks each with
  [`check-dist`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#check-dist),
  which fails a wheel carrying a test directory or nothing outside
  its metadata.
- Packages every workspace crate and builds each from its tarball
  (`make package-check`, in the Linux x86_64 build job), so a crate
  that `cargo publish` would reject fails the run before anything
  is signed or published.
- Sigstore-signs the release tag (keyless, OIDC), gated on the
  `release` environment's manual approval.
- Publishes every workspace crate to crates.io in dependency
  order (see
  [tools/publish-crates.sh](https://github.com/shakenfist/ryll/blob/develop/tools/publish-crates.sh)):
  `shakenfist-spice-protocol` → `shakenfist-spice-compression` →
  `shakenfist-spice-usbredir` → `shakenfist-spice-renderer` →
  `shakenfist-spice-webrtc` → `ryll`.
- Creates a GitHub Release with auto-generated notes and all
  artifacts attached.
- Publishes the `ryll` wheels straight to PyPI via an OIDC
  Trusted Publisher. There is no TestPyPI stage; the comment on
  the `publish-ryll-pypi` job explains why.
- Updates `shakenfist/homebrew-tap` with the new version and the
  SHA256 of the macOS tarball.

All workspace crates share a single version, bumped together.
There is no way to release one crate at a new version without
releasing the others at the same version — this is intentional.

## Prerequisites

### Tag protection

A tag protection rule should be configured so only authorised
maintainers can push `v*` tags. Set this up in GitHub under
Settings → Rules → Rulesets (or the older Settings → Tags):

- **Pattern:** `v*`
- **Restrict who can create matching tags:** maintainers only

This prevents accidental or unauthorised releases.

### Repository secrets

Two secrets must be configured in the ryll repo settings:

- **`CARGO_REGISTRY_TOKEN`** — a crates.io API token with
  `publish-new` and `publish-update` scopes, generated at
  <https://crates.io/settings/tokens>. Without it, the
  `publish-crates` job fails and the crates do not reach
  crates.io. The GitHub Release and binary artifacts are still
  created.
- **`HOMEBREW_TAP_TOKEN`** — a GitHub personal access token
  (classic with `repo` scope, or fine-grained with write access
  to `shakenfist/homebrew-tap`). Without it, the Homebrew tap
  update step fails; the GitHub Release and crates.io publishes
  still succeed.

No secret is needed for the `ryll` PyPI wheel: it publishes via
OIDC Trusted Publishers instead (see below).

### PyPI Trusted Publishers and the `release` environment

The `ryll` wheel publishes to PyPI using an OIDC Trusted
Publisher — no API tokens are stored in the repo. This is
one-time setup, mirroring how kerbside's `kerbside-proxy` wheel
is published (see that repo's `RELEASE-SETUP.md`).

1. **PyPI trusted publisher.** Log in to
   [pypi.org](https://pypi.org), go to the `ryll` project's
   **Settings → Publishing**, and add a publisher with:
   - **Owner:** `shakenfist`
   - **Repository name:** `ryll`
   - **Workflow name:** `release.yml`
   - **Environment name:** `release`
2. **`release` GitHub environment.** Under repo **Settings →
   Environments**, create an environment named `release` with
   **Required reviewers** set to the maintainers who may approve
   releases. This environment gates the `sign-tag` job, and
   `sign-tag` in turn gates *every* publishing job —
   `publish-crates`, `github-release`, `update-homebrew`, and
   `publish-ryll-pypi` — so nothing publishes anywhere, and the
   release tag is not Sigstore-signed, until a required reviewer
   approves the run in GitHub's UI. `publish-ryll-pypi` also
   names the `release` environment itself (PyPI's trusted
   publisher matches on it), and it becomes ready later than
   `sign-tag`, so a release asks for approval **twice**.

Without the `release` environment configured, the workflow run
stalls waiting for an environment that can never be approved.
Without the trusted-publisher registration, `publish-ryll-pypi`
fails with a "publisher not found" error; the
`.deb`/`.rpm`/tarball/zip artifacts, GitHub Release, and
crates.io publishes are unaffected.

### Host tools

Both scripts orchestrate from the operator's host. The
workspace test compile in stage 1 is delegated to `make test`,
which runs inside the devcontainer, so the host's rustc does
not need to satisfy the dependency tree's MSRV. Everything
else runs on the host and requires:

- `cargo-release` — `cargo install --locked cargo-release`.
  Used for the workspace-wide version bump in stage 1. The
  bump only edits `Cargo.toml` files and does not need to
  match the workspace's MSRV. If your host toolchain is older
  than 1.91 (for example Debian's packaged cargo 1.85), pin
  the last version that supports it:
  `cargo install --locked cargo-release@0.25.18`. Stage 1 also
  runs `cargo metadata` to list the workspace crates.
- `docker` — used by `make test` (and by `pre-commit` via
  `scripts/check-rust.sh`) to run the Rust toolchain in the
  devcontainer.
- `gh` — the GitHub CLI, signed in (`gh auth login`). Used in
  stage 1 to open the pull request, and in stage 2 to watch the
  release workflow and open the release page on completion.
- `jq`, `curl`, `git`, `pre-commit`, `make` — standard dev
  tooling.

## Release process

### Choosing the version

The crates are at 0.x, where Cargo treats the minor version as the
breaking-change boundary: a `^0.1.7` dependency accepts 0.1.8 but
not 0.2.0. So if any merged PR since the last tag changed a
published crate's public API incompatibly (a signature, a removed
item, or a public constant's value), propose the next minor version
(0.1.x to 0.2.0), not a patch. Such PRs list their breaking changes
under a "Breaking changes" heading in the PR description. The
GitHub Release notes are generated from PR titles alone, so copy
those lists into the release notes by hand.

### Stage 1: propose the release

From a clean checkout of `develop`, up to date with origin:

```bash
make propose-release 0.2.0
```

The script will:

1. Validate the version as `X.Y.Z`.
2. Confirm the working tree is clean, the branch is `develop`
   and in sync with `origin/develop`, and neither `v0.2.0` nor
   `release-0.2.0` exist locally or on origin.
3. Check the new version is newer than the current one.
4. Query crates.io for `0.2.0` on every workspace crate (read from
   `cargo metadata`), and check that `tools/publish-crates.sh` publishes
   each of them; bail if any version already exists or any crate is missing.
5. Create and switch to `release-0.2.0`.
6. Bump `[workspace.package].version` and the matching
   `version =` qualifiers on ryll's path dependencies via
   `cargo release version 0.2.0 --workspace --execute
   --no-confirm`, then refresh `Cargo.lock` with `make lock`.
   It refuses to go on if the bump left an untracked file.
7. Run `pre-commit run --all-files` over the bumped tree, and
   the release workflow's own version check
   (`tools/release-version.sh check refs/tags/v0.2.0`).
8. Run `make test` (which runs `cargo test --workspace`
   inside the devcontainer).
9. Show `git diff --stat` and prompt
   `Commit, push release-0.2.0 and open a pull request? [y/N]`.
10. On confirmation: commit `Release 0.2.0.`, push
    `release-0.2.0` to origin, and open a pull request into
    `develop`.

Nothing goes to origin before the `y/N` prompt. If you answer
`N`, or anything fails before the push, the script switches back
to `develop` and deletes the release branch; nothing is
committed.

Get the pull request reviewed and merge it through the merge
queue like any other change. No release artefacts are produced
yet — this PR is the audit trail for the version bump itself. The
queue's CI run on the merged commit is the result the release
gate later requires, so do not merge it around the queue.

### Stage 2: tag the merged commit

After the PR has merged:

```bash
make tag-release 0.2.0
```

The script will:

1. Fetch `origin/develop` and origin's tags, overwriting any
   local tag of the same name. `sign-tag` (below) replaces each
   release tag on origin with a signed one, so the clone that
   tagged the previous release still holds the unsigned original,
   and a plain `git fetch --tags` would refuse to update it.
2. Verify no `v0.2.0` tag exists locally or on origin.
3. Find the commit that merged `Release 0.2.0.` into `develop`
   (the queue's merge commit, not `develop`'s tip, which may
   have moved on). If there is none, the PR has not merged yet
   and the script bails.
4. Verify that commit is on `develop`'s first-parent history,
   and that its `[workspace.package].version` and `Cargo.lock`
   both say `0.2.0`.
5. Show the target SHA and subject line, list anything merged
   after it (which is not in the release), spell out that
   publishing cannot be undone, and prompt
   `Create and push tag v0.2.0? [y/N]`.
6. On confirmation: create the annotated tag at that commit and
   push it. If the push fails, the local tag is removed so the
   script can be run again.
7. Watch the triggered release workflow via `gh run watch` and
   open the GitHub Release page when it completes.

`make tag-release` takes only the version. To tag a later commit
instead, for recovery, run `tools/tag-release.sh 0.2.0 <commit>`.

The pushed tag runs `ci-gate` and `check-version`, then builds the `.deb`/
`.rpm`/tarball/zip artifacts and the per-arch `ryll` PyPI wheels
(`build-ryll-wheels`, natively on `x86_64` and `aarch64`, no
QEMU) in parallel. No pull request CI builds the wheels, so if
the wheel build has changed since the last release, run
`manual-build.yml` on `develop` with the `wheel-x86_64` and
`wheel-aarch64` platforms, or a [dry run](#a-dry-run) of the
release workflow, before tagging. Once all builds
succeed, the workflow pauses
at `sign-tag` for approval on the `release` environment — this is
where `gh run watch` will sit until a required reviewer approves
the run. On approval, `sign-tag` Sigstore-signs and force-pushes
the tag, then `publish-crates` and `github-release` run. Once
`github-release` finishes, `update-homebrew` runs and
`publish-ryll-pypi` pauses for a **second** approval on the
`release` environment. It passes `skip-existing`, so a re-run
after a partial failure does not hard-error on a file already
published.

## The release gate

The first job, `ci-gate`, uses
[`require-ci-success`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#require-ci-success)
to check that `ci.yml` concluded `success` on the tagged commit.
If it did not, nothing is built, signed or published. That run is
the merge queue's run on the commit the queue merged, which is
why a release must go through the queue: a commit merged around
it, by an administrator bypassing the queue, has no such run and
cannot be released. A commit that only adds review marks, from
`prune-reviews`, is checked through the commit below it, but a
merge commit is never crossed. The gate has no override.

### A dry run

Running the workflow by hand (**Actions → Release → Run
workflow**) on any branch is a dry run. It runs `check-version`,
which on a branch checks only that `Cargo.toml` and `Cargo.lock`
agree, and every build job, including the wheels and their
`check-dist`. `ci-gate`, `sign-tag`, `github-release` and every
publishing job show as skipped. The build jobs run at all only
because each tests `check-version`'s result itself: a job with no
`if:` of its own is skipped when any job upstream of it was, and
`ci-gate` was. This is the way to exercise a
change to the release build before tagging; `manual-build.yml`
remains for building a single platform's artefacts from a branch.

### Recovering from a gate failure

A gate failure means nothing was built or published. The
recovery depends on why it failed:

| Why | What to do |
|-----|------------|
| The tagged commit was merged around the queue, or sits above such a merge, so it has no queue run | Move the tag to a later commit that went through the queue |
| The tag is not on `develop`'s first-parent history | Move the tag to the commit the queue landed for that change, or a later one |
| The queue's CI run on that commit failed for a reason unrelated to the change, such as a flaky job | Re-run it from **Actions → CI** until it succeeds, then use "Re-run jobs" on the release's `ci-gate` job |
| The CI run is still going | Wait for it, then re-run the `ci-gate` job |

`ci.yml` has no `paths-ignore`: a documentation-only change still
gets a queue run, in which `check_paths` skips the builds and the
run succeeds, so such a commit can be tagged.

To move the tag:

```bash
git push origin :refs/tags/v0.2.0   # needs the tag ruleset's bypass
git tag -d v0.2.0
tools/tag-release.sh 0.2.0 <commit that went through the queue>
```

The
[template README](https://github.com/shakenfist/development/blob/main/templates/release-automation/README.md#recovering-from-a-gate-failure)
has the full table.

## Artifacts produced

| Platform | Artifact | Contents |
|----------|----------|----------|
| Debian/Ubuntu | `ryll_{version}-1_amd64.deb`, `ryll_{version}-1_arm64.deb` | Binary + auto-detected deps |
| Fedora/RHEL | `ryll-{version}-1.x86_64.rpm`, `ryll-{version}-1.aarch64.rpm` | Binary + auto-detected deps |
| macOS (Apple Silicon) | `ryll-{version}-aarch64-apple-darwin.tar.gz` | Binary |
| Windows | `ryll-{version}-x86_64-pc-windows-msvc.zip`, `ryll-{version}-aarch64-pc-windows-msvc.zip` | GUI binary (no `--capture`) |
| crates.io | `shakenfist-spice-protocol`, `shakenfist-spice-compression`, `shakenfist-spice-usbredir`, `shakenfist-spice-renderer`, `shakenfist-spice-webrtc`, `ryll` | Source crates |
| PyPI | `ryll-{version}-py3-none-manylinux_2_28_x86_64.whl`, `ryll-{version}-py3-none-manylinux_2_28_aarch64.whl` | Embedded GUI binary (maturin `bindings = "bin"`); Linux `x86_64`/`aarch64`, glibc >= 2.28 |

## The PyPI wheel's platform tag

The wheels are tagged `manylinux_2_28`, but only part of that tag
is earned, and the rest is asserted. `tools/build-ryll-wheel.sh`
builds in the `manylinux_2_28` image, so the glibc floor is real.
The GUI libraries (GL/EGL, wayland, xcb, xkbcommon, X11) and opus
are loaded with `dlopen` at runtime, so they are not in the
binary's `DT_NEEDED`. But `libasound.so.2` is: cpal links ALSA
directly through `alsa-sys`. ALSA is not on the manylinux
allowlist, so by auditwheel's own definition the wheel is plain
`linux_<arch>`, and the build passes `--skip-auditwheel` to assert
the tag anyway.

The alternatives are worse for a release:

- **Tag it `linux_<arch>`.** PyPI refuses uploads with that tag.
- **Vendor `libasound.so.2`** (let auditwheel repair the wheel).
  That would ship AlmaLinux 8's ALSA library to hosts whose ALSA
  configuration and plugins (PipeWire or PulseAudio on most
  desktops) were built for their own copy, so audio would likely
  break where it works today.
- **Load ALSA with `dlopen`** like the GUI libraries. This is the
  real fix, but `alsa-sys` has no such mode, so it means changing
  or replacing the audio backend.

The cost is that pip installs the wheel on a host with no ALSA
runtime (a slim container or a minimal server), and ryll then
fails at start with `error while loading shared libraries:
libasound.so.2`. The [pip install instructions](/components/ryll/installation/#pip-python-linux-only)
list `libasound2` as a runtime requirement for that reason.

To keep the exception to that one library,
`tools/build-ryll-wheel-in-container.sh` checks the built binary's
`DT_NEEDED` after every build. It fails the release build if
anything outside the `manylinux_2_28` allowlist other than
`libasound.so.2` shows up there, for example after a dependency
bump starts hard-linking xcb or wayland. The allowlist is read from
the policy of the auditwheel the build image ships, so it follows
the base image rather than a copy that could drift from it.

## Troubleshooting

### Version mismatch

The `check-version` job runs `tools/release-version.sh check`,
which reads `[workspace.package].version` from the root
`Cargo.toml`, checks that `ryll`'s entry in `Cargo.lock` agrees,
and compares it to the tag. A mismatch fails the workflow before
anything is built. This should not happen when you release via
the `propose-release` → `tag-release` flow, since `tag-release`
runs the same checks against the commit before tagging it. If it
does (for example after a manual tag push), nothing has been
signed or published:

```bash
git push origin :refs/tags/v0.2.0   # needs the tag ruleset's bypass
git tag -d v0.2.0
# fix the workspace version on develop via a new release PR,
# then re-run make tag-release X.Y.Z.
```

### crates.io publish fails

A `publish-crates` job failure does not roll back the earlier
publishes. If the workflow publishes protocol and compression
then fails on usbredir, the first two are live on crates.io and
cannot be replaced — only yanked. Fix the underlying issue and
cut the next patch version through the normal flow (`make
propose-release 0.2.1`); do not try to re-publish `0.2.0`.

v0.1.6 failed this way: protocol, compression and usbredir
published, then the renderer was rejected for a git dependency
with no `version`, so the renderer, webrtc and ryll never reached
crates.io at 0.1.6. `make package-check` now runs every crate
through the same manifest checks and a build from its tarball in
pull request CI (the `Lint` job) and again in the release's Linux
x86_64 build job, before `sign-tag`, so this class of failure
stops the release before the first upload. Run it locally to
reproduce one. What it cannot catch is anything about crates.io
itself: the token, the network, or a version that already exists.

Common causes:

- `CARGO_REGISTRY_TOKEN` missing, expired, or scoped too
  narrowly. Regenerate with `publish-new` + `publish-update`.
- A dependency inside ryll that still uses a `path = ...` only
  reference without a `version = ...` qualifier. `cargo publish`
  rejects this. The dual-spec qualifiers are wired up and
  maintained by `cargo release`; if you have added new
  workspace-member dependencies, make sure they follow the same
  pattern.

### Release stuck waiting for approval

`sign-tag` (and everything downstream of it) will not run until
a required reviewer approves the workflow run on the `release`
environment. If `gh run watch` appears to hang after the build
jobs finish, or again after `github-release` finishes (the
`publish-ryll-pypi` approval), check the environment approval,
not the workflow. If
the `release` environment does not exist yet, the run stalls
indefinitely — see "PyPI Trusted Publishers and the `release`
environment" above.

### PyPI wheel publish fails

`publish-ryll-pypi` uses an OIDC Trusted Publisher, not a stored
token, so failures are almost always configuration, not
credentials:

- "Publisher not found" — the trusted-publisher entry on
  pypi.org does not match the workflow's Owner / Repository /
  Workflow name / Environment name exactly (case sensitive).
  Re-check the entry against the current `release.yml`.
- A partial failure (e.g. the `x86_64` wheel uploads but
  `aarch64` fails) is safe to re-run: the job passes
  `skip-existing: true`, so a re-run only uploads what is
  missing.
- A PyPI failure does not affect `publish-crates`,
  `github-release`, or `update-homebrew` — none of them depend on
  the PyPI publish succeeding.

### Homebrew tap update fails

If `HOMEBREW_TAP_TOKEN` is missing or expired the tap update
job fails but the release and the crates.io publishes still
succeed. Update the formula manually:

```bash
cd homebrew-tap
# update version and sha256 in Formula/ryll.rb
git commit -am "Update ryll to 0.2.0"
git push
```
