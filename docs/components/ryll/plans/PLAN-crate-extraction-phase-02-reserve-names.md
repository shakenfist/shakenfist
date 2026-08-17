# Phase 2: Reserve crate names on crates.io

## Prompt

Before executing any step of this plan, back brief the operator
as to your understanding of the plan and how the work you intend
to do aligns with the master plan
([PLAN-crate-extraction.md](/components/ryll/plans/PLAN-crate-extraction/)) and the
Phase 2 Decision (#7) it records.

This phase has a unique property: it includes a **publish to a
public, immutable registry**. Once a `0.0.0` crate is uploaded
to crates.io, it cannot be replaced or deleted (only yanked,
and the name is permanently bound to the publishing account).
Treat the publish step with the same care as a force-push: do
the dry-run, eyeball the manifest, and only then publish.

The actual `cargo publish` command will be run by the operator,
not by Claude. Claude does not have crates.io credentials and
must not attempt to log in or publish. Claude's job in this
phase is to prepare the placeholder crates, run all local
verification including `cargo publish --dry-run`, and then
hand over to the operator with the exact commands to run.

I prefer one commit per logical change. The "create the three
placeholders" change is symmetric across the three crates and
the artefacts are inert until published, so it is one commit.
The "mark phase complete" status update is a separate commit
made *after* the operator confirms all three publishes
succeeded.

## Situation

After Phase 1 (commits 7191d9e..c3472d6, status 011f78c), the
repository is a Cargo workspace:

```
ryll-repo/
├── Cargo.toml          # workspace manifest
├── ryll/
│   ├── Cargo.toml      # ryll package, inherits workspace fields
│   └── src/
└── ...
```

[Cargo.toml](https://github.com/shakenfist/ryll/blob/develop/Cargo.toml) declares
`members = ["ryll"]` and shares `edition`, `license`,
`authors`, and `repository` via `[workspace.package]`.

The master plan ([PLAN-crate-extraction.md](/components/ryll/plans/PLAN-crate-extraction/))
Decision #7 mandates that the three target crate names be
reserved on crates.io with `0.0.0` placeholder publishes
before any extraction work begins:

- `shakenfist-spice-compression`
- `shakenfist-spice-protocol`
- `shakenfist-spice-usbredir`

Pre-research checked the registry on the day this plan was
written: all three names returned HTTP 404 from
`https://crates.io/api/v1/crates/<name>`, meaning they were
unowned and available. (The bare `spice-*` namespace was also
unowned at the same time, which is a separate consideration —
see Decision #3 in the master plan for why we are not claiming
those.)

This phase has zero impact on ryll's existing build, tests,
release pipeline, or runtime behaviour. The new placeholder
crates are workspace members but contain only a doc-comment in
`lib.rs`; they are pulled into `cargo test --workspace` (and
will trivially pass) and `cargo clippy --workspace
--all-targets` (and will trivially pass), and CI will start
building/checking them automatically once they appear in the
workspace.

## Mission and problem statement

Reserve the three crate names on crates.io by publishing
minimal, well-documented `0.0.0` placeholder crates from the
Cargo workspace, so that no other party can claim them before
the real `0.1.0` releases land in the upcoming extraction
phases.

After this phase completes:

- `shakenfist-spice-compression`,
  `shakenfist-spice-protocol`, and
  `shakenfist-spice-usbredir` exist on crates.io as version
  `0.0.0`, owned by the operator's crates.io account.
- Each placeholder crate's `crates.io` page clearly explains
  that the crate is a name reservation for the upcoming
  pure-Rust SPICE extraction work, with a link back to the
  ryll repository and the master plan.
- The three crates exist as workspace members in the local
  repository, all build cleanly, and CI runs lint/test against
  them automatically.
- The release of an actual `0.1.0` for any of the three crates
  is a future-phase concern and explicitly out of scope here.

There must be no functional changes to ryll, no dependency
changes for ryll, and no build/test regressions.

## Approach

The phase splits into three discrete activities, each with its
own risk profile:

1. **Local preparation** (Claude, repeatable, reversible).
   Create the three new workspace member directories with
   minimal `Cargo.toml`, `lib.rs`, and `README.md` files. Add
   them to `[workspace] members = [...]`. Verify that
   `cargo build --workspace`, `cargo test --workspace`,
   `cargo clippy --workspace --all-targets`, and `cargo fmt
   --all --check` all succeed. Run `cargo publish --dry-run -p
   <name>` for each crate and inspect the staged tarball to
   confirm the manifest, license, README, and `lib.rs` look
   correct after workspace inheritance is resolved. Commit
   this.

2. **Operator publish** (operator, irreversible).
   The operator runs `cargo login` (one-time, if not already
   done) and then `cargo publish -p <name>` for each of the
   three crates from the workspace root. Once each upload
   completes, the operator visits the crates.io page for the
   crate to confirm it is live and the README rendered
   correctly.

3. **Status update** (Claude, after operator confirms).
   Mark Phase 2 as complete in the master plan's execution
   table, and record any execution discoveries in this phase
   plan's "Discoveries during execution" section. Commit.

The local preparation commit must leave CI green even before
the publishes happen. The publishes are entirely outside the
git history — the only artefacts are on crates.io.

## Pre-flight verification

Before starting Step 1, Claude verifies:

1. **Working tree is clean** on the `display-iteration` branch
   and Phase 1 is fully committed (`git log --oneline -10`
   should show 011f78c at top or close to it).
2. **CI is green on the current commit**, via
   `ci-status shakenfist/ryll runs --branch display-iteration
   --limit 3`. If CI is broken, fix that first.
3. **Names are still available on crates.io.** This was true
   when the plan was written, but anyone could have published
   in the interim. For each of the three names:
   ```sh
   curl -sI -o /dev/null -w "%{http_code}\n" \
     "https://crates.io/api/v1/crates/shakenfist-spice-compression"
   ```
   All three must return `404`. If any returns `200`, **stop
   and re-plan immediately** — someone else has claimed the
   name. Possible responses: pick a new prefix (e.g.
   `sf-spice-*`), or contact the new owner if the existing
   crate is empty/abandoned. Do not proceed with the rest of
   Phase 2 until this is resolved.
4. **Operator has a crates.io account and an API token.** This
   does not need to be done by Claude; surface the requirement
   in the back-brief and let the operator confirm before Step 1
   begins. The operator should also confirm whether the
   account they will publish from is the one that should own
   these crates long-term (per Decision #7, the names cannot be
   transferred between accounts without going through the
   crates.io team or a formal consent message).

If any of the checks above fail, stop and re-plan rather than
improvising.

## Crate template

Each of the three placeholder crates has the same structure:

```
<crate-name>/
├── Cargo.toml
├── README.md
└── src/
    └── lib.rs
```

### `Cargo.toml`

Each placeholder crate inherits as much as possible from
`[workspace.package]` to minimise drift:

```toml
[package]
name = "shakenfist-spice-<component>"
version = "0.0.0"
edition.workspace = true
license.workspace = true
authors.workspace = true
repository.workspace = true
description = "Reserved name for an upcoming pure-Rust SPICE <component> implementation extracted from the ryll SPICE client."
readme = "README.md"
```

Notes:

- `version = "0.0.0"` is the conventional placeholder version.
  Cargo's resolver treats `0.0.0` as the lowest version, so
  any later `0.1.x` release will be picked automatically by
  `cargo add` and `cargo update` once it lands.
- `name`, `version`, `description`, and `license` (or
  `license-file`) are the four fields crates.io requires for
  publish to succeed. `repository` is strongly recommended;
  inheriting it from the workspace ensures consistency.
  `authors` is optional and informational but cheap to inherit.
- `readme = "README.md"` is the explicit form. Cargo
  auto-detects `README.md` in the package root if not set, but
  being explicit makes it obvious what is being uploaded and
  rendered on the crates.io page.
- The description is intentionally long — it appears in
  `cargo search`, the crates.io listing, and `cargo metadata`,
  so make every word do work explaining the reservation.
- No `[dependencies]`, no `[features]`, no `[dev-dependencies]`
  blocks. Placeholders must be functionally empty.
- No `[package.metadata.deb]` or `[package.metadata.generate-rpm]`.
  These crates are libraries, not binaries, and are not
  packaged.

### `src/lib.rs`

A single line of `//!` doc-comment is enough to make the crate
build. The doc-comment becomes the rustdoc landing page, so it
should briefly explain what the crate is for and link to the
README:

```rust
//! Reserved crate name for an upcoming pure-Rust SPICE
//! <component> implementation extracted from the
//! [ryll](https://github.com/shakenfist/ryll) SPICE client.
//!
//! This crate is currently a name reservation only and
//! contains no functional code. See the README for details
//! and a link to the extraction plan.
```

No `pub` items. The crate must compile (and `cargo publish`
runs a verification build), but it must not export an API
that someone could accidentally start depending on.

### `README.md`

The README is the most important artefact in this phase: it is
what crates.io renders on the crate's page, and it is the
primary signal to anyone who stumbles across the crate that it
is a legitimate reservation rather than squatting. The Rust
Forge crate ownership policy recommends specific language for
reservation crates; the template below incorporates it.

```markdown
# shakenfist-spice-<component>

**This crate is a functionally empty crate that exists to
reserve the crate name for an upcoming pure-Rust SPICE
<component> implementation extracted from the
[ryll](https://github.com/shakenfist/ryll) SPICE client. It
should not be used.**

## Status

Reserved by the [shakenfist](https://github.com/shakenfist)
project. The real `0.1.0` release will be published when the
extraction work in
[ryll](https://github.com/shakenfist/ryll/blob/develop/docs/plans/PLAN-crate-extraction.md)
lands. Until then, this `0.0.0` release is intentionally
empty.

## What this crate will contain

<one or two sentences specific to each crate — see per-crate
text below>

## Why a placeholder?

crates.io has a flat, immutable, first-come namespace. We are
publishing this empty crate now to prevent the name from being
claimed by an unrelated party (typosquatter, AI-generated junk
crate, well-meaning third party) before the real
implementation is ready. This is consistent with the
[Rust Forge crate ownership policy](https://forge.rust-lang.org/policies/crate-ownership.html)'s
guidance on reservation crates: the README clearly states the
intent, and substantive publishing will follow.

## Project links

- Source repository:
  <https://github.com/shakenfist/ryll>
- Extraction plan:
  <https://github.com/shakenfist/ryll/blob/develop/docs/plans/PLAN-crate-extraction.md>
- Issues / contact: file via the ryll repository
```

### Per-crate "What this crate will contain" text

**`shakenfist-spice-compression`:**

> When released, this crate will provide pure-Rust
> implementations of the SPICE image-stream codecs: QUIC (the
> SPICE wavelet/arithmetic codec, not the QUIC transport
> protocol), GLZ (dictionary-based cross-frame LZ), LZ
> (single-frame LZ), and LZ4. Each algorithm will be gated
> behind a Cargo feature for dependency-minimisation.
>
> The initial `0.1.0` release will contain decompression only,
> matching what the ryll client needs today. The crate name
> deliberately covers both directions so that compression
> implementations of the same codecs can be added in future
> minor releases without a crate rename. SPICE proxies (such
> as the planned Rust rewrite of the kerbside proxy) and
> server-side tooling are likely to want compression as well.

**`shakenfist-spice-protocol`:**

> When released, this crate will provide pure-Rust SPICE
> protocol primitives: protocol constants and opcodes,
> wire-format message structs with serialisation, the SPICE
> link handshake (including TLS and RSA-OAEP authentication),
> and a `SpiceClient` for connecting to a SPICE server.

**`shakenfist-spice-usbredir`:**

> When released, this crate will provide a pure-Rust parser
> and message types for the SPICE USB redirection protocol
> (the SPICE-side of `usbredir`), suitable for clients,
> proxies, and protocol analysis tools.

## Execution

### Step 1: Create the three placeholder workspace members

This is the only Claude-authored commit in Phase 2 (other than
the status update at the end). Operator should be ready to do
the publish step immediately after this lands, to keep the
window between the placeholders existing in git and being
claimed on crates.io as short as possible — although in
practice the placeholders are only on the local branch until
the operator pushes, so the real squatting risk is the time
between push and publish.

Steps within the commit:

1. For each of the three crate names, create the directory
   structure under the workspace root:
   ```
   shakenfist-spice-compression/
   ├── Cargo.toml
   ├── README.md
   └── src/lib.rs
   ```
   ...and the same for `shakenfist-spice-protocol/` and
   `shakenfist-spice-usbredir/`.

2. Each `Cargo.toml` follows the template in the "Crate
   template" section above. Each `lib.rs` is the four-line
   doc-comment. Each `README.md` follows the template, with
   the per-crate "What this crate will contain" paragraph
   substituted in.

3. Update the top-level [Cargo.toml](https://github.com/shakenfist/ryll/blob/develop/Cargo.toml)
   `[workspace] members` list:
   ```toml
   [workspace]
   resolver = "2"
   members = [
       "ryll",
       "shakenfist-spice-compression",
       "shakenfist-spice-protocol",
       "shakenfist-spice-usbredir",
   ]
   ```

4. Verify locally inside the devcontainer:
   - `cargo fmt --all --check` — passes (no Rust source to
     format other than the trivial `lib.rs`).
   - `cargo clippy --workspace --all-targets -- -D warnings`
     — passes for all four members.
   - `cargo build --workspace` — builds all four members.
   - `cargo build --release -p ryll` — ryll builds in
     release.
   - `cargo test --workspace` — all 99 ryll tests pass; the
     three placeholder crates each contribute "0 tests run".
   - `cargo deb --no-build -p ryll` — produces the same
     `target/debian/ryll_0.1.3-1_amd64.deb` as Phase 1.
   - `cargo generate-rpm -p ryll` — produces the same
     `target/generate-rpm/ryll-0.1.3-1.x86_64.rpm` as Phase 1.
   - `pre-commit run --all-files` — passes.

5. **Critical: dry-run each publish.** For each of the three
   crates, from the workspace root:
   ```sh
   cargo publish --dry-run -p shakenfist-spice-compression
   cargo publish --dry-run -p shakenfist-spice-protocol
   cargo publish --dry-run -p shakenfist-spice-usbredir
   ```
   Each must succeed. The dry-run will perform the local
   verification build and packaging steps but will not contact
   the registry, so it will not catch a name collision or auth
   failure — those can only happen at real publish time. What
   it *will* catch is missing required metadata, an invalid
   manifest, license file mismatches, and similar local issues.

6. **Inspect the packaged tarballs.** `cargo publish --dry-run`
   leaves a `.crate` file in `target/package/`. Run:
   ```sh
   ls -la target/package/
   ```
   For each of the three `.crate` files, inspect the embedded
   `Cargo.toml` to verify that workspace inheritance has been
   resolved correctly (the published manifest should contain
   concrete `edition`, `license`, `authors`, `repository`
   values, not `.workspace = true` references):
   ```sh
   tar -xzOf target/package/shakenfist-spice-compression-0.0.0.crate \
       shakenfist-spice-compression-0.0.0/Cargo.toml
   ```
   Confirm the README and `lib.rs` are also present in the
   tarball listing:
   ```sh
   tar -tzf target/package/shakenfist-spice-compression-0.0.0.crate
   ```
   Expected files: `Cargo.toml`, `Cargo.toml.orig`, `README.md`,
   `src/lib.rs`, `.cargo_vcs_info.json`, and possibly
   `Cargo.lock`. No `target/`, no `.git/`, no leftover artefacts.

7. Commit. The commit should NOT include the
   `target/package/` directory; it is in `.gitignore` via the
   existing `target/` rule.

**Commit message (subject ≤ 50 chars):**
> Add placeholder crates for crates.io reservation.

### Step 2: Operator publishes to crates.io

This step is performed by the operator, not Claude. Claude
should hand off with the exact command sequence and a
reminder of the irreversibility.

Operator pre-flight:

1. Confirm `cargo login` has been run on the publishing
   machine, with the correct API token from
   <https://crates.io/me> (or whichever account is intended to
   own these crates long-term).
2. Re-verify the names are still available immediately before
   publishing (the same `curl` check as the Phase 2 pre-flight).
   Belt-and-braces against a same-day claim.
3. Decide whether to push the branch to GitHub before or after
   publishing. Pushing first means the README's repository link
   will work the moment the crate goes live; publishing first
   means the crate exists before the placeholder commit is
   visible to anyone. Recommendation: **push first**, so the
   crates.io page links to a real branch.

Publish sequence (run from the workspace root):

```sh
cargo publish -p shakenfist-spice-compression
cargo publish -p shakenfist-spice-protocol
cargo publish -p shakenfist-spice-usbredir
```

The order does not matter; the crates are independent. After
each one completes, visit:

- <https://crates.io/crates/shakenfist-spice-compression>
- <https://crates.io/crates/shakenfist-spice-protocol>
- <https://crates.io/crates/shakenfist-spice-usbredir>

...and confirm:

- The version is `0.0.0`.
- The README is rendered (markdown headings, links).
- The "Repository" link points to `github.com/shakenfist/ryll`.
- The "License" is `Apache-2.0`.
- The owner is the expected crates.io account.

If any of those look wrong, **do not publish a `0.0.1` to
fix it** — fix the source in a follow-up commit and decide
whether the cosmetic issue is worth burning a version. The
README content is the only thing that genuinely matters; a
typo there is fine to live with on `0.0.0` since the real
release will be `0.1.0` with a fresh README.

### Step 3: Mark Phase 2 complete

After the operator confirms all three publishes succeeded:

1. Update the master plan's execution table:
   ```
   | 2. Reserve crate names on crates.io | PLAN-crate-extraction-phase-02-reserve-names.md | Complete |
   ```
2. Add a "Discoveries during execution" section to this phase
   plan capturing anything surprising (e.g. dry-run caught
   something unexpected, the operator's account had a 2FA
   wrinkle, the crates.io UI rendered the README oddly).
3. If any of the open questions below were answered during
   execution, fold the answers back into the relevant Decision
   in the master plan, or into a new note in this phase plan.
4. Commit.

**Commit message (subject ≤ 50 chars):**
> Mark Phase 2 (crate name reservation) complete.

## Open questions

1. **Which crates.io account should own the placeholders?**
   The operator (mikalstill) is the expected owner. Whether to
   immediately add a second co-owner (e.g. a shakenfist
   organisation account or another shakenfist team member's
   personal account) for bus-factor purposes is a policy
   question for the operator. If yes, the
   `cargo owner --add <user> <crate>` invocation can be done
   right after publish. Defer the decision; do not block this
   phase on it.

2. **Should we also publish to a private registry?** No.
   Decision #1 in the master plan establishes that internal
   consumers (kerbside) will use git dependencies, not the
   registry. The crates.io publishes are purely about name
   reservation. Re-confirming here so it does not get
   re-litigated mid-phase.

3. **Yank `0.0.0` after `0.1.0` publishes?** Decision #7
   notes that yanking is optional after the real release lands.
   The Rust Forge ownership policy actually recommends *not*
   yanking placeholder crates — the README is what explains
   the reservation to a confused user, and yanking hides that
   explanation from `cargo search`. Defer the yank decision to
   the future-work phase that publishes `0.1.0`; do not commit
   to yanking here.

4. **Do we need to push the branch before publishing?**
   Strongly recommended (so the README's GitHub links work
   immediately when the crate goes live), but not strictly
   required by cargo-publish. The publishing operator can
   decide based on their workflow. Note that pushing the
   branch is a separate operator action that the user has
   asked Claude not to do automatically.

## Administration and logistics

### Success criteria

We will know Phase 2 has been successfully implemented when:

* The repository contains three new workspace member
  directories: `shakenfist-spice-compression/`,
  `shakenfist-spice-protocol/`, and
  `shakenfist-spice-usbredir/`, each with `Cargo.toml`,
  `README.md`, and `src/lib.rs`.
* The top-level [Cargo.toml](https://github.com/shakenfist/ryll/blob/develop/Cargo.toml) `members` list
  contains all four crates (ryll plus the three placeholders).
* `cargo build --workspace`,
  `cargo test --workspace`,
  `cargo clippy --workspace --all-targets -- -D warnings`,
  `cargo fmt --all --check`, and
  `pre-commit run --all-files` all succeed locally.
* CI is green on the branch after the placeholder commit.
* `cargo publish --dry-run -p <name>` succeeded for all three
  placeholders, and the embedded `Cargo.toml` in each
  `target/package/<name>-0.0.0.crate` contains concrete
  workspace-inherited values (not `.workspace = true`).
* All three crates exist on crates.io as version `0.0.0`,
  owned by the operator's account.
* Each crates.io page renders the README correctly, with
  working links back to the ryll repository and the master
  plan.
* The master plan's execution table shows Phase 2 as
  Complete.
* No functional changes to ryll: `ryll/Cargo.toml` is
  unmodified, `ryll/src/` is unmodified, all 99 existing tests
  still pass, and the `.deb` and `.rpm` packaging still works
  with `-p ryll`.

### Future work

* **`0.1.0` publishes** happen at the end of Phases 3, 4, and
  5 (compression, protocol, usbredir respectively), once
  each crate's API stabilises. That work is tracked under the
  master plan's "Future work" section, not here.
* **Adding co-owners on crates.io** for bus-factor — see open
  question #1. If/when this happens, document the decision in
  the master plan's Decision #7 as an addendum.
* **Crates.io trusted-publishing via GitHub Actions** is a
  newer feature that lets a repo's CI publish to crates.io
  using OIDC tokens instead of long-lived API tokens. Worth
  evaluating for the eventual `0.1.0` release flow, but out
  of scope for the initial reservation since the operator is
  publishing manually from a trusted local machine.
* **Reserving `shakenfist-spice-client` and other future
  names.** If Phase 6 (the `SpiceClient` extraction) decides
  the eventual home of `SpiceClient` is a standalone
  `shakenfist-spice-client` crate rather than a member of
  `shakenfist-spice-protocol`, that name should also be
  reserved at the time the decision is made. Out of scope
  here.

### Bugs fixed during this work

(None expected. Phase 2 is purely additive — three new
workspace members. If a clippy or test issue surfaces in the
new placeholder crates, fix it inline; if a pre-existing ryll
issue is uncovered, fix it in a separate commit before the
placeholder commit lands.)

### Discoveries during execution

* **Mid-phase rename from `decompression` to `compression`.**
  After Step 1 was already partially in progress (the
  decompression placeholder directory had been created and
  the protocol placeholder was being written), the operator
  asked to rename the first crate to `shakenfist-spice-
  compression` so the eventual crate could cover both
  directions of the SPICE codecs in a single namespace. This
  was handled by:
  1. Renaming the in-flight (untracked) directory.
  2. Re-verifying `shakenfist-spice-compression` was also
     available on crates.io (it was, 404).
  3. Updating Decision #6 in the master plan to capture the
     naming rationale, the master plan's other references
     to the old name, and both phase plan files.
  4. Committing the rename as a standalone documentation-only
     commit (26c38c0) before the placeholder commit (42c8e8a),
     so the two changes stayed logically separate.
  The rename did not require any extra crates.io action — the
  old name was never published, only mentioned in
  yet-to-be-committed plan text.

* **Operator publishes via docker, not native cargo.** The
  operator's machine has docker but no native cargo
  installation. Publishes were performed by passing the
  short-lived crates.io API token into the existing `ryll-dev`
  devcontainer image via `-e CARGO_REGISTRY_TOKEN=...`, with a
  `for` loop wrapping `cargo publish -p <name>`. This worked
  cleanly: docker has network access by default,
  `CARGO_REGISTRY_TOKEN` is read by cargo without needing a
  prior `cargo login`, and the token never touched disk inside
  the container (no `~/.cargo/credentials.toml` write). Worth
  remembering for future publish operations.

* **Token scoping recommendation.** The publish was performed
  with a `publish-new`-only token scoped to the three
  crate-name patterns and an expiration of 1 day. This is the
  correct security posture for a one-shot reservation publish:
  even if the token had leaked, an attacker could not have
  used it to publish updates to existing crates, change
  ownership, yank, or publish new crates outside the
  reservation namespace.

* **All three publishes succeeded within ~7 seconds.** The
  three uploads landed at 2026-04-08T11:31:06, 11:31:09, and
  11:31:13 UTC respectively. crate sizes 1.6-2 KB each. No
  retries needed, no metadata rejections, no rate limiting.
  Cargo's `--dry-run` correctly predicted the package
  contents and the verification build succeeded for all
  three before the real publish.

* **`.cargo_vcs_info.json` was added to the published
  tarballs.** During local dry-run inspection (Step 1), the
  packaged tarballs contained 5 files (Cargo.toml,
  Cargo.toml.orig, Cargo.lock, README.md, src/lib.rs) but
  *not* `.cargo_vcs_info.json`, because the working tree had
  uncommitted changes when the dry-run ran. Once the
  placeholders were committed (42c8e8a) and the operator
  published from the clean tree, the VCS info file was added
  automatically by cargo. This is expected cargo behaviour
  and was correctly anticipated in Step 1's tarball inspection
  notes.

* **No need to push the branch first.** The Phase 2 plan
  flagged this as a recommendation (open question #4), but in
  practice the README links pointing at
  `develop/docs/plans/PLAN-crate-extraction.md` will 404 only
  until `display-iteration` is merged to `develop`, which is
  expected to happen as part of normal merge cadence. Since
  the crates.io pages will be visited primarily by us in the
  short term (and by anyone running `cargo search shakenfist`
  who is curious), the broken link for the first day or two
  is not a meaningful problem.

* **Verification via the crates.io HTTP API.** Rather than
  manually clicking through three crates.io pages in a
  browser, all the success-criteria checks (version, license,
  repository, owner, yanked status) were verified by hitting
  `https://crates.io/api/v1/crates/<name>` and parsing the
  JSON. This is more thorough than visual inspection and is
  recorded here in case future phases want to do the same
  for `0.1.0` publish verification.

### Back brief

Before executing any step of this plan, back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan. Specifically confirm:

1. That Claude will prepare the placeholder crates and run
   all local verification including `cargo publish --dry-run`,
   but will **not** run the real `cargo publish` — that is the
   operator's job.
2. That the three crate names (`shakenfist-spice-*`) are
   verified available on crates.io as part of pre-flight.
3. That the operator has confirmed they have a crates.io
   account with `cargo login` configured, and that the
   account is the one intended to own these crates long-term.
4. That the placeholder commit will leave CI green even
   before the publishes happen, and that the publishes are
   entirely outside the git history.
5. The four-step sequence: (pre-flight) → (Step 1: prepare
   and commit) → (Step 2: operator publishes) → (Step 3:
   mark complete).
