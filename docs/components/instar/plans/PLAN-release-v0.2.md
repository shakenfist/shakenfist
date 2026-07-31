# Release v0.2.0

## Status: Complete

v0.2.0 was tagged and published on 2026-05-09 (commit `24dabc7
Release v0.2.0`, tag `v0.2.0`, `CHANGELOG.md` dated 2026-05-09).
The execution table below is preserved for historical reference;
all gates were either passed or carried forward as follow-ups
(`PLAN-distro-matrix-ci.md` for the cross-distro install matrix).

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, LUKS, KVM, virtio,
disk image formats, Sigstore signing, Debian/RPM packaging),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

This is the **release execution plan** for v0.2.0. Most of the
preparation work (Cargo.toml metadata, release workflow, .deb /
.rpm packaging, audit infrastructure, VMM boundary audit) has
already landed; see the "Situation" section for what is in flight
or already complete. This plan covers the steps from "preparation
PR merged" through to "v0.2.0 release published on GitHub".

Consult `ARCHITECTURE.md`, `AGENTS.md`, `docs/`, and
`docs/commentary/` for codebase orientation. Consult
`CHANGELOG.md` for the drafted v0.2.0 release notes and
`docs/plans/PLAN-distro-matrix-ci.md` for follow-up CI work that
is intentionally out of scope here.

The release execution itself is irreversible (a published GitHub
Release with a Sigstore-signed tag is hard to retract cleanly).
Treat the tagging step as high effort: the management session
should perform it, not a sub-agent.

## Situation

### Already complete (merged into `develop` via PR #281, commit 53b11df)

* `src/vmm/Cargo.toml` and the format-crate `Cargo.toml` files
  carry the metadata required for a public release (description,
  license, repository, authors, keywords, categories) and the six
  internal format crates are marked `publish = false`.
* `Makefile` has `make release VERSION=x.y.z`, `make
  check-version`, `make metadata`, `make deb`, `make rpm`, and
  `make package` targets.
* `.github/workflows/release.yml` triggers on `v*` tag pushes,
  builds via `make instar`, packages a tarball, runs
  `make package` for x86_64-linux-gnu, signs the tag with
  Sigstore, and creates a GitHub Release with three artifacts
  (tarball, `.deb`, `.rpm`).
* `src/.devcontainer/Dockerfile` installs `cargo-deb` and
  `cargo-generate-rpm` for the packaging targets.
* `src/vmm/src/main.rs` `get_binary_dir()` resolver supports
  three layouts: `INSTAR_BIN_DIR` env override, executable
  directory (developer mode), and `/usr/lib/instar/` (system
  install via .deb/.rpm).
* PR-level package smoke (`package-smoke` job in
  `.github/workflows/functional-tests.yml`) builds the .deb and
  installs it in a `debian:trixie` container with `/dev/kvm`
  passthrough on every PR.
* `CHANGELOG.md` has a drafted `[0.2.0] - Unreleased` section
  covering the rename, added operations, format support, the
  new resolver, packaging, and CI smoke.
* `README.md` has install instructions for .deb / .rpm /
  tarball, the glibc 2.39 minimum, and the system requirements.
* `SECURITY.md` exists.
* `docs/plans/` is bootstrapped with `index.md`, `order.yml`,
  and `PLAN-distro-matrix-ci.md` (deferred follow-up).
* `tools/audit/wave1.sh` and `tools/audit/wave2-mechanical.sh`
  exist and are wired into `PUSH-AUDIT.md`.
* VMM boundary audit (`PLAN-audit.md` Phase 5) is complete: 8
  bugs fixed including sector bounds checking, BackingStore
  overflow / capacity, IO buffer cap, sandboxed info exit
  handling, DebugBuffer OOM, SerialDecoder cap.
* Coverage-guided fuzzing harness infrastructure (`PLAN-audit.md`
  Phase 6) merged; not a v0.2 blocker per the plan.

### Not yet done

* `Cargo.toml` versions are still `0.1.0`. `make release
  VERSION=0.2.0` will bump them.
* `CHANGELOG.md` `[0.2.0] - Unreleased` heading needs to become
  `[0.2.0] - <date>`.
* Pre-tag audit checklist items have not been re-run on the
  current `release-0.2-prep` head: full `make test`, `cargo
  audit`, an explicit `cargo clippy --all-targets`, secrets
  scan.
* `.deb` install has been smoke-tested on `debian:trixie` (in
  CI and locally). `.rpm` install on a real RPM-based distro
  has *not* been validated against KVM — local Rocky 9 test
  failed because of the glibc 2.39 floor (which is documented
  but not yet exercised against a glibc-2.39+ RPM distro).
* GitHub `release` environment with required reviewers has not
  been confirmed. The `sign-tag` job blocks on it.
* Self-hosted runner availability for the `release.yml` job
  labels (`[self-hosted, debian-12, s]`, `[self-hosted, static]`)
  has not been confirmed in the v0.2 timeframe.
* The legacy `v0.1` tag exists from 2026-01-28 as an internal
  pre-release; v0.2.0 is the first public release.

## Mission and problem statement

Cut the **v0.2.0** tag and publish a signed GitHub Release with
three x86_64-linux-gnu artifacts (tarball, `.deb`, `.rpm`).
Verify the published artifacts install cleanly on at least one
RPM-based distro before announcing the release.

The non-trivial parts are:

1. The first-time activation of the `release.yml` workflow.
   It has never run against a real `v*` tag; failures here block
   the release.
2. The Sigstore tag signing (`sign-tag` job) gates on the
   `release` GitHub environment, which has not been confirmed to
   exist with required reviewers.
3. The `.rpm` artifact has not yet been validated end-to-end on
   a real KVM-capable RPM host. Rocky 9 is excluded by the glibc
   floor; Rocky 10 / Fedora 40+ should work but are unproven.

## Open questions

These need answers before execution.

1. **Where is the tag cut?** With PR #281 merged into
   `develop`, two options remain:
   * Tag on `develop` directly. v0.1 was tagged on `develop`
     per the existing pattern.
   * Merge `develop` to `main` first, tag on `main`. Matches
     the `gitStatus` "main branch" annotation in the session
     metadata, but inconsistent with the v0.1 tagging history.

   *Recommendation:* tag on `develop`. The version-bump commit
   (step 9) and the dated CHANGELOG (step 10) should land
   directly on `develop` via a small follow-up branch, with the
   tag pointing at the bump commit. Promoting `develop` to
   `main` can be a separate cadence decision unrelated to v0.2.

2. **`.rpm` install validation — which distro?** Options:
   * Rocky 10 (matches the merge-queue matrix in
     `PLAN-distro-matrix-ci.md`).
   * Fedora 40+ (newer qemu-img, broader package compatibility).
   * Both.

   *Recommendation:* Fedora-latest as the primary check (it has
   the newest qemu-img and exercises the most aggressive
   compatibility profile); Rocky 10 as a stretch if a host is
   available. If neither is available the release can still
   ship — the `.rpm` packaging is structurally identical to the
   `.deb` and the .deb has been validated end-to-end — but the
   v0.2 release notes should call out the `.rpm` as
   "structurally tested but not yet exercised against an RPM
   distro under KVM".

3. **GitHub `release` environment.** Does it exist? If not,
   create it with the operator (mikal) as the required
   reviewer. This is a one-time GitHub setting (Settings →
   Environments) and is independent of the code.

4. **Pre-existing audit findings.** Are there any open
   `cargo audit` advisories against the current dependency
   tree, and any clippy lints surfaced by
   `cargo clippy --all-targets` that are not blocked by the
   pre-commit hook? If yes, they need to be triaged as fix /
   defer / RUSTSEC-allowlist before tagging.

5. **Announcement.** Is there a release announcement target
   (mailing list, blog, social) and if so does this plan need
   to cover it, or is announcement separate from "tagged and
   published"?

   *Recommendation:* defer announcement. v0.2 is a "0.x.y
   signals interface may change" release per
   `PLAN-release.md`'s versioning strategy; a quiet first
   release is fine.

## Execution

The plan is sequential gates with no genuine parallelism. Each
row must complete before the next begins.

| #  | Step                                                          | Effort | Model  | Status      |
|----|---------------------------------------------------------------|--------|--------|-------------|
| 1  | Merge PR #281 (`release-0.2-prep`) into `develop`             | low    | (operator) | Done (commit 53b11df) |
| 2  | Resolve the open questions above (tag location, RPM distro, release environment, audit findings, announcement) | medium | opus | Not started |
| 3  | Run `make test` end-to-end (Rust unit + Python integration); record any flakes | medium | sonnet | Not started |
| 4  | Run `cargo audit` inside the devcontainer; triage findings    | medium | sonnet | Not started |
| 5  | Run `cargo clippy --all-targets --all-features` inside the devcontainer; confirm clean against the lint baseline | medium | sonnet | Not started |
| 6  | Secrets scan: `git grep -i 'password\|secret\|token\|api[_-]key' -- ':!*.lock' ':!docs/**'` and review hits | low | haiku | Not started |
| 7  | Confirm GitHub `release` environment exists with required reviewers; create if missing | low | (operator) | Not started |
| 8  | Confirm self-hosted runners online for the `release.yml` job labels | low | (operator) | Not started |
| 9  | `make release VERSION=0.2.0` on the chosen tag branch; review the bump commit and tag | medium | (operator) | Not started |
| 10 | Update `CHANGELOG.md` heading from `[0.2.0] - Unreleased` to `[0.2.0] - YYYY-MM-DD`; amend or follow-up commit | low | sonnet | Not started |
| 11 | Push the bump commit, then push the tag (`git push origin HEAD && git push origin v0.2.0`) | low | (operator) | Not started |
| 12 | Watch `release.yml` to completion; approve the `release` environment when prompted | medium | (operator) | Not started |
| 13 | Verify the GitHub Release page shows the tarball, `.deb`, and `.rpm` artifacts | low | sonnet | Not started |
| 14 | Verify Sigstore tag signature (`gitsign verify-tag v0.2.0` or equivalent)         | medium | sonnet | Not started |
| 15 | Real-world `.deb` install validation on a clean Debian / Ubuntu VM with `/dev/kvm`: download, install, run `instar info` against a known qcow2 | medium | (operator) | Not started |
| 16 | Real-world `.rpm` install validation on the chosen RPM distro (per question 2) with `/dev/kvm` | medium | (operator) | Not started |
| 17 | Update `docs/plans/index.md` to mark this plan **Complete**   | low    | sonnet | Not started |

Notes on the "(operator)" rows: GitHub environment changes,
pushes that trigger CI billing or an auto-publish, and the actual
release tag are not safe to delegate to a sub-agent. The
management session should drive them, with sub-agents reserved
for the audit / verification rows.

The audit rows (3-6) can in principle run in parallel since they
are read-only checks against the same tree, but in practice each
takes a few minutes and the cost of serialising them is low. Run
them serially if the management session is doing the spawning;
parallelise only if a clean batch dispatch is convenient.

## Agent guidance

### Execution model

The release-execution gates are mostly mechanical
verifications (`make test`, `cargo audit`, `cargo clippy`,
fixture installs). Sub-agents at sonnet medium effort can run
them and report results.

The release-execution gates (steps 7, 9, 11, 12, 15, 16) are
**operator-driven**: GitHub environment configuration, version
bump and tag, the actual `git push origin v0.2.0`, the manual
approval inside the `release` environment, and the post-release
real-world install verification. These should not be delegated
to sub-agents — the actions are visible-to-others, irreversible,
or require human judgement on a "does this artifact actually
work on my Debian VM" call.

### Planning effort

The plan itself was drafted at high effort (this document).
Re-reading and updating it after merging PR #281 is medium
effort: the situation will have shifted (the PR is no longer
"in flight") and the open questions may have collapsed.

### Step-level guidance

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 3    | medium | sonnet | none      | Run `make test` from the worktree root and report the result. If any test fails, capture the failing test name, the relevant log lines, and decide blocking vs flake (blocking unless reproducibly the same flake as a previously documented one). Do not modify the tree. |
| 4    | medium | sonnet | none      | Run `cargo audit` inside the `instar-build` devcontainer (`docker run --rm -v $(pwd):/workspace -w /workspace/src instar-build cargo audit`, installing cargo-audit if needed). Report each advisory: ID, crate, version, severity, and whether the affected code path is reachable from instar. |
| 5    | medium | sonnet | none      | Run `cargo clippy --release --all-targets --all-features` inside the devcontainer; report any new lint findings not already silenced by the workspace's `clippy.toml` / `Cargo.toml` lint config. |
| 6    | low    | haiku  | none      | Run the `git grep` command in step 6, exclude `.lock` and `docs/`, review hits and report any that look like a real credential or secret. False positives ("password" appearing in a doc string about LUKS passphrases) are expected; flag them as such. |
| 10   | low    | sonnet | none      | After `make release VERSION=0.2.0` has produced the bump commit, edit `CHANGELOG.md` to change `## [0.2.0] - Unreleased` to `## [0.2.0] - YYYY-MM-DD` (today's date). Either amend the bump commit or create a follow-up commit on the same branch — operator decides. |
| 13   | low    | sonnet | none      | Fetch the GitHub Release v0.2.0 metadata via `gh release view v0.2.0 --repo shakenfist/instar --json assets,tagName,isDraft`; verify three assets are present (tarball, .deb, .rpm), the names match the `instar-*-x86_64-unknown-linux-gnu.tar.gz` / `instar_0.2.0-1_amd64.deb` / `instar-0.2.0-1.x86_64.rpm` patterns, and `isDraft` is false. |
| 14   | medium | sonnet | none      | Verify the Sigstore tag signature: `git verify-tag v0.2.0` after configuring gitsign as the verifier; or download the tag's `.sig` from the Rekor transparency log and verify against the OIDC identity. Report success / failure and the verifying identity. |
| 17   | low    | sonnet | none      | Update the row for this plan in `docs/plans/index.md` to mark Status as `Complete`. Commit on a follow-up branch / PR; do not push to `develop` directly. |

### Management session review checklist

After each sub-agent completes, the management session should
verify:

- [ ] The check ran against the expected tree
      (`release-0.2-prep` rebased onto current `develop`, or
      whichever branch holds the bump commit by step 9).
- [ ] No unrelated files were modified — these gates are
      read-only verifications.
- [ ] Reported findings are real (skim the raw output, do not
      trust the summary).
- [ ] For audit findings (steps 4-6), each finding has a
      decision recorded: fix-now, defer-with-issue,
      false-positive.

## Administration and logistics

### Success criteria

We will know v0.2.0 has been successfully released because the
following statements are true:

* The `v0.2.0` tag exists on `develop` (or wherever question 1
  resolves to), is Sigstore-signed, and is verifiable.
* `Cargo.toml` versions across the workspace all read `0.2.0`
  on the tagged commit.
* `CHANGELOG.md` `[0.2.0]` heading carries a date, not
  `Unreleased`.
* The GitHub Release page for v0.2.0 lists three assets:
  `instar-v0.2.0-x86_64-unknown-linux-gnu.tar.gz`,
  `instar_0.2.0-1_amd64.deb`,
  `instar-0.2.0-1.x86_64.rpm`.
* A clean Debian or Ubuntu VM (glibc ≥ 2.39) with `/dev/kvm`
  can `apt install ./instar_0.2.0-1_amd64.deb` and run
  `instar info` against a sample qcow2 successfully.
* A clean RPM-based VM (Fedora-latest or Rocky 10) with
  `/dev/kvm` can `dnf install ./instar-0.2.0-1.x86_64.rpm` and
  run `instar info` successfully — *or* the limitation is
  recorded explicitly in the release notes per open question 2.
* `docs/plans/index.md` lists this plan as Complete.

### Future work

These are explicitly **not** in scope for v0.2 and should remain
so. They appear in `PLAN-release.md` (the original prep plan),
in `docs/plans/PLAN-distro-matrix-ci.md`, or below.

* **Lower glibc baseline** to widen distro compatibility (Rocky
  9, Debian 12, Ubuntu 22.04). Tracked as design block 1 in
  `PLAN-distro-matrix-ci.md`.
* **Multi-distro install + qemu-img differential CI** in the
  merge queue. See `PLAN-distro-matrix-ci.md`.
* **aarch64 / arm64 packaging.** Deferred until test hardware
  exists.
* **musl static builds** for minimal/container environments.
* **crates.io publishing** for any of the format crates. The
  six `publish = false` crates expose a bare-metal CallTable
  ABI, not a general parser API; would require a wrapper layer
  before being useful standalone.
* **Homebrew tap.** Not applicable — instar requires
  `/dev/kvm` and cannot run on macOS.
* **Additional qemu-img subcommands** (create, resize, snapshot,
  rebase, commit, map, measure).
* **cargo-dist / release-plz** automation. Out of scope while
  the build path is unusual (Docker + nightly + bare-metal
  cross-compile).
* **Pre-existing per-crate `[profile]` warnings** from
  `cargo metadata` — six warnings, cosmetic, predate this
  release. Move profiles to the workspace root in a follow-up.
* **`PLAN-release.md` (legacy local-only)** — most of its
  content is now superseded by this plan. It can be deleted
  from the operator's local tree once v0.2.0 ships.

### Bugs fixed during this work

(To be filled in if anything surfaces during the audit gates.)

### Documentation index maintenance

When this plan is committed:

* **`docs/plans/index.md`** — add a new row to the *Master plans*
  table for `PLAN-release-v0.2.md`, dated today, intent "Cut
  the v0.2.0 tag and publish signed GitHub Release artifacts
  for x86_64 Linux", initial status "Drafted, not started",
  phases column "(no phase files; sequential gates)".
* **`docs/plans/order.yml`** — add `- PLAN-release-v0.2.md:
  Release v0.2.0` after the existing `PLAN-distro-matrix-ci.md`
  entry.

When v0.2.0 ships, update the status column in `index.md` to
`Complete`.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
