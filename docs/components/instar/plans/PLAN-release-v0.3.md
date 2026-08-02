# Release v0.3.0

## Status: Complete (v0.3.0 published 2026-08-02; install validation carried to #474)

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

This is the **release execution plan** for v0.3.0, modelled on
`PLAN-release-v0.2.md` (Complete; tagged 2026-05-09). The release
machinery — `make release`, `make check-version`, `make package`,
`.github/workflows/release.yml`, Sigstore tag signing gated on
the `release` GitHub environment — has run end-to-end exactly
once, for v0.2.0. This plan covers the steps from "develop is
releasable" through "v0.3.0 published on GitHub and validated".

The release execution itself is irreversible (a published GitHub
Release with a Sigstore-signed tag is hard to retract cleanly).
Treat the tagging step as high effort: the management session
should perform it with the operator, not a sub-agent.

## Situation

### What has landed since v0.2.0

v0.2.0 was tagged 2026-05-09. As of drafting (2026-07-28) there
are **794 commits** on `develop` since the tag. Headlines:

* **The qemu-img subcommand parity roster closed at 15/15.**
  Added since v0.2.0: `create`, `resize`, `rebase`, `commit`,
  `map`, `snapshot`, `check --repair`, `amend`, `dd`, `bitmap`,
  `bench` (see the master plans in `docs/plans/index.md`).
* **qcow2 write infrastructure** (`crates/qcow2-write`), with
  copy-on-write for snapshot-shared clusters. Resolved the four
  snapshot-corruption defects #420 / #421 / #423 / #432.
* **Format coverage expansion**: VDI, Parallels, QCOW1, and DMG
  graduated to full read support (convert/compare/dd/bench);
  detection parity closed against qemu-img's 14-format roster;
  QED refusal-as-policy recorded.
* **Data-integrity fixes**: resize dirty-bitmap data loss,
  qcow2 shrink sub-byte refcount corruption, fixed-VHD
  virtual_size overflow, VHDX resize sequence numbers, the
  May and June fuzzer-backlog drains.
* **Hardening**: guest IDT (#375 mitigation), LUKS2 Argon2
  kdf clamps, guest-op sector_size re-checks, unwrap lint,
  devcontainer nightly pin + weekly bump workflow.

`CHANGELOG.md`'s `[Unreleased]` section has been maintained
continuously through all of this (~1690 lines at drafting) —
the CHANGELOG task for this release is *verification and
retitling*, not writing.

### Aborted first attempt (2026-07-28)

A `make release VERSION=0.3.0` was run on `develop` before this
plan existed. It created the version-bump commit and the
`v0.3.0` tag **locally only**; nothing was pushed, so
`release.yml` never fired and the `release` environment was
never engaged. The tag was deleted and `develop` hard-reset to
`origin/develop`. There is no remote residue; the version bump
will be re-done at the tagging gate below.

### Release blockers found at drafting time

* **The packaging manifests are stale.** `src/vmm/Cargo.toml`'s
  `[package.metadata.deb]` and `[package.metadata.generate-rpm]`
  asset lists still ship only the v0.2-era six binaries
  (`core.bin`, `info.bin`, `copy.bin`, `check.bin`,
  `compare.bin`, `convert.bin`). `src/operations/` now builds
  **15** operation binaries plus `core.bin`. A v0.3.0 package
  built today would install an `instar` whose `create`,
  `resize`, `rebase`, `commit`, `map`, `measure`, `snapshot`,
  `amend`, `bitmap`, and `bench` subcommands fail at runtime
  with a missing `/usr/lib/instar/<op>.bin`. The
  `extended-description` text ("info, copy, check, compare,
  convert") is similarly stale.
* **The package smoke test cannot see the gap.**
  `tools/test-package-install.sh` verifies the six v0.2-era
  files by name and exercises only `--help` and `info`. It
  must grow the full binary roster and at least one
  post-v0.2 subcommand, or this class of regression stays
  invisible on every future release.

### Carried forward from v0.2.0

* Real-world `.rpm` install validation on a KVM-capable RPM
  distro never happened for v0.2.0 (carried to
  `PLAN-distro-matrix-ci.md`, which remains Drafted, not
  started). The v0.2 fallback stands: the release may ship
  with the `.rpm` marked "structurally tested but not yet
  exercised against an RPM distro under KVM" in the notes.
* The `release` GitHub environment (required reviewer: mikal)
  and the self-hosted runner labels for `release.yml`
  (`[self-hosted, debian-12, s]`, `[self-hosted, static]`)
  exist and worked for v0.2.0; they need re-confirmation, not
  creation.

## Mission and problem statement

Cut the **v0.3.0** tag and publish a signed GitHub Release with
three x86_64-linux-gnu artifacts (tarball, `.deb`, `.rpm`) whose
packages contain the **complete current binary set** (VMM +
core + 15 operations). Verify the `.deb` installs and runs both
a v0.2-era and a post-v0.2 subcommand on a clean KVM-capable
host before announcing anything.

The non-trivial parts:

1. The packaging-manifest fix and smoke-test extension must land
   *before* the tag, and the smoke test must prove the fix (the
   test should fail against the old manifest).
2. `release.yml` has run exactly once; treat its second run as
   unproven, watch it end-to-end.
3. The CHANGELOG retitle must land **before** the tag so the
   tagged tree carries the dated heading (v0.2 did this after
   tagging as a follow-up; do better this time).

## Open questions

1. **Version number.** 0.3.0 is assumed (large additive surface,
   0.x series, no compatibility promise broken). Confirm.
2. **Announcement.** `docs/openstack-announcement-email.md`
   exists as a draft. Is v0.3.0 the release that gets announced,
   or does announcement remain decoupled?
   *Recommendation:* decouple. Publish quietly, then decide;
   announcement is not a gate.
3. **`.rpm` validation distro.** Same question as v0.2 (Fedora
   latest vs Rocky 10). *Recommendation:* Fedora-latest if a
   KVM-capable host is convenient; otherwise ship with the v0.2
   caveat wording and leave the matrix to
   `PLAN-distro-matrix-ci.md`.
4. **cargo audit findings.** Unknown until the gate runs; each
   finding gets a fix / defer-with-issue / allowlist decision
   before tagging.

Settled by v0.2 precedent and not reopened: the tag is cut on
`develop`, pointing at the version-bump commit; the release is
quiet (no announcement gate); the `release` environment approval
is the operator's manual step.

## Execution

Sequential gates; each row completes before the next begins.
"(operator)" rows are performed by the operator in the
management session, not delegated.

| #  | Step | Effort | Model | Status |
|----|------|--------|-------|--------|
| 1  | Land this plan: `docs/plans/PLAN-release-v0.3.md` + `index.md` row + `order.yml` entry, PR into `develop` | low | (management) | Complete (PR #465, merged 2026-07-30) |
| 2  | Packaging fix on a prep branch: add the ten missing operation binaries to both asset lists in `src/vmm/Cargo.toml`, refresh `extended-description`; prove with `make package` + `dpkg-deb -c` / `rpm -qlp` diffed against `ls src/operations/` | medium | sonnet | Complete (this branch; both package listings verified complete) |
| 3  | Extend `tools/test-package-install.sh`: verify all 16 `.bin` files by roster, exercise one post-v0.2 subcommand end-to-end (e.g. `instar create` + `instar map` on the fixture); confirm the extended test fails against the pre-fix manifest | medium | sonnet | Complete (this branch; roster now derived from `src/operations/`; extended test failed on all ten missing binaries pre-fix, passes post-fix incl. create+map under KVM) |
| 4  | CHANGELOG coverage review: spot-check `[Unreleased]` against `git log v0.2.0..develop` and the master-plan index for missing headline items | medium | sonnet | Complete (this branch): all 16 master-plan programmes were covered; added missing entries (guest IDT #375, security hardening #446-#449, the step-2/3 packaging fix) and rewrote entries superseded in-window (interim #420/#421/#423 gates → COW resolution, retired bench steer-around #397-#401, map phase-diary contradictions). Two `### Fixed` headings remain to merge at the step-11 retitle. |
| 5  | Run `make test` end-to-end (Rust unit + Python integration); record any flakes | medium | sonnet | Complete: Rust unit suite passed; integration suite recorded 7 failures, all heavyweight `test_convert` manifest-image re-encodes, and all 7 passed on isolated quiet-host re-runs — classified as contention flakes (the suite shared the host with the step-2/3 package builds and the lint/audit gates). A clean-host full-suite confirmation run was interrupted; the PR CI run is the full-suite confirmation. |
| 6  | Run `make audit` (cargo audit in the devcontainer); triage findings per open question 4 | medium | sonnet | Complete: 0 vulnerabilities. 2 warnings triaged: `aes` 0.9.0 yanked — stale local `Cargo.lock` only (lockfiles are gitignored; fresh resolution as used by `release.yml` cannot select a yanked version; local lock moved to 0.9.1) — and RUSTSEC-2026-0190 (`anyhow` unsound `downcast_mut`) deferred as unreachable (instar never calls `downcast_mut`). |
| 7  | Run `make lint` (clippy all-targets in the lint container); confirm clean | low | sonnet | Complete: all checks passed. 3 pre-existing warnings in *generated* code (`guest-protocol` build-script output `guest.rs`: an unused label, unused `mut`s) — code-generator/toolchain noise, follow-up candidate, not a release blocker. |
| 8  | Secrets scan: `git grep -i 'password\|secret\|token\|api[_-]key' -- ':!*.lock' ':!docs/**'`; review hits | low | haiku | Complete: 104 hits, all false positives (CI secret-name references, dummy test passphrases, LUKS passphrase-handling code, VMDK 'token' field terminology, docs). No real secrets. |
| 9  | Confirm the `release` environment (required reviewer) and the `release.yml` runner labels are live | low | (operator) | Complete: `release` environment exists (API-confirmed 2026-07-30); runners confirmed online by the operator (2026-08-02) |
| 10 | Merge the prep PR(s) from steps 1-4 into `develop` | low | (operator) | Complete (PR #465 merged; all 12 CI checks green, including the extended package-smoke) |
| 11 | Retitle `CHANGELOG.md` `## [Unreleased]` to `## [0.3.0] - YYYY-MM-DD` (tag day) and add a fresh empty `[Unreleased]` heading; land on `develop` immediately before the bump | low | sonnet | Complete (2026-08-02, committed on `develop` beneath the bump; the duplicate `### Fixed` headings merged into one) |
| 12 | `make release VERSION=0.3.0` on `develop`; review the bump commit and tag | medium | (operator) | Complete (2026-08-02; bump commit `d6bd755` atop the retitle commit) |
| 13 | `git push origin HEAD && git push origin v0.3.0`; watch `release.yml` to completion; approve the `release` environment when prompted | medium | (operator) | Complete (run 30719418010: check-version, build/package, sign-tag after environment approval, release — all green on the workflow's second-ever run) |
| 14 | Verify the Release page: `gh release view v0.3.0 --json assets,tagName,isDraft` shows the tarball, `instar_0.3.0-1_amd64.deb`, `instar-0.3.0-1.x86_64.rpm`; not draft | low | sonnet | Complete (all three assets present with 0.3.0 names; not draft, not prerelease) |
| 15 | Verify the Sigstore tag signature (`git verify-tag v0.3.0` via gitsign, or Rekor lookup); record the verifying identity | medium | sonnet | Complete: good signature, identity `https://github.com/shakenfist/instar/.github/workflows/release.yml@refs/tags/v0.3.0`, Rekor entry validated (tlog 2318166334). Note: the sign-tag job replaces the pushed tag with the signed object, so local clones need `git fetch --tags --force` afterwards. |
| 16 | Real-world `.deb` validation: clean Debian/Ubuntu VM with `/dev/kvm`, install the *published* artifact, run `instar info` **and** one post-v0.2 subcommand against a sample qcow2 | medium | (operator) | Carried forward to issue #474 (v0.2 precedent; CI package-smoke covers the same flow in a container) |
| 17 | Real-world `.rpm` validation per open question 3, or record the caveat in the release notes | medium | (operator) | Carried forward to issue #474, per open question 3's fallback |
| 18 | Mark this plan Complete in `docs/plans/index.md` | low | sonnet | Complete (this PR) |

## Agent guidance

### Execution model

The audit and verification gates (2-8, 11, 14, 15, 18) are
mechanical enough for sub-agents at the listed model/effort; the
management session reviews raw output before advancing (see
checklist below). Steps 9, 10, 12, 13, 16, 17 are
**operator-driven**: GitHub environment configuration, merges to
`develop`, the version bump and tag, the pushes that trigger the
release workflow, the environment approval, and the
install-validation judgement calls are visible-to-others or
irreversible and are not delegated.

Steps 2 and 3 are the only code changes in this plan and should
land as one prep PR (one logical change each, so two commits):
the manifest fix and the smoke-test extension that proves it.
Step 3's brief must include running the extended smoke test
against a package built from the *unfixed* manifest and
confirming it fails — a smoke test that cannot catch the bug it
was extended for is decoration.

### Management session review checklist

After each sub-agent gate:

- [ ] The check ran against the expected tree (current
      `develop`, or the prep branch for steps 2-3).
- [ ] No unrelated files were modified by read-only gates.
- [ ] Reported findings are real (skim the raw output, do not
      trust the summary).
- [ ] Audit findings (steps 6-8) each carry a recorded decision:
      fix-now, defer-with-issue, false-positive.

## Administration and logistics

### Success criteria

* The `v0.3.0` tag exists on `develop`, is Sigstore-signed, and
  verifiable.
* All workspace `Cargo.toml` versions read `0.3.0` on the tagged
  commit, and `make check-version TAG=v0.3.0` passes.
* `CHANGELOG.md` has a dated `[0.3.0]` heading **on the tagged
  commit**, plus a fresh `[Unreleased]` heading.
* The Release page lists the three assets with 0.3.0 names.
* `dpkg-deb -c` on the published `.deb` (and `rpm -qlp` on the
  `.rpm`) lists `/usr/bin/instar` plus all 16 binaries under
  `/usr/lib/instar/`.
* A clean Debian/Ubuntu VM with `/dev/kvm` installs the
  published `.deb` and successfully runs `instar info` and at
  least one post-v0.2 subcommand.
* `.rpm` validated per open question 3, or the caveat is in the
  release notes.
* `docs/plans/index.md` lists this plan as Complete.

### Future work

Carried forward, unchanged in scope from v0.2's list except
where noted:

* Multi-distro install + qemu-img differential CI in the merge
  queue (`PLAN-distro-matrix-ci.md`, still Drafted).
* Lower glibc baseline (design block 1 of the distro-matrix
  plan).
* aarch64 packaging; musl static builds; crates.io publishing;
  cargo-dist / release-plz automation.
* Release announcement (open question 2, if decoupled).
* ~~Additional qemu-img subcommands~~ — complete; the roster
  closed with `bench`.

### Bugs fixed during this work

* Stale `.deb` / `.rpm` asset manifests (found at drafting;
  fixed by step 2) and the package-smoke blind spot that hid
  them (step 3).
* `functional-tests.yml` did not trigger on `tools/` changes,
  leaving the (now load-bearing) package smoke script outside
  CI's paths filter (fixed in PR #465).

### Follow-up issues filed at close-out

* #471 — clippy warnings in generated guest-protocol code
  (from the step-7 lint gate).
* #472 / #473 — tracking issues for the two upstream qemu
  crash reports recorded in PLAN-format-coverage's future
  work (DMG zero-chunk NULL-deref; parallels_check_duplicate
  assertion).
* #474 — post-release install validation of the published
  .deb / .rpm artifacts (steps 16-17 carried forward).

### Documentation index maintenance

When this plan is committed:

* **`docs/plans/index.md`** — add a Master-plans row dated
  2026-07-28, intent "Cut the v0.3.0 tag and publish signed
  GitHub Release artifacts (tarball, .deb, .rpm) for x86_64
  Linux, with the packaging manifests brought up to the 15/15
  subcommand roster", status "Drafted, not started", phases
  "(no phase files; sequential gates)".
* **`docs/plans/order.yml`** — add `- PLAN-release-v0.3.md:
  Release v0.3.0` after the `PLAN-release-v0.2.md` entry.

When v0.3.0 ships, update the status in both this file and
`index.md` to Complete.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
