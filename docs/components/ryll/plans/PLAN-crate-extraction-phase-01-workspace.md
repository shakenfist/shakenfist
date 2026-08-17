# Phase 1: Convert ryll to a Cargo workspace

## Prompt

Before executing any step of this plan, back brief the operator
as to your understanding of the plan and how the work you intend
to do aligns with the master plan
([PLAN-crate-extraction.md](/components/ryll/plans/PLAN-crate-extraction/)).

This phase contains no functional code changes. Its sole purpose
is to lay the groundwork for the crate extraction phases that
follow. If you discover that any of the assumptions below are
wrong (e.g. cargo-deb behaves differently from documented, the
release workflow has additional version-checks, the IDE breaks
in a way the plan did not anticipate), stop and re-plan rather
than improvising.

I prefer one commit per logical change. Each commit must leave
the tree in a working state: `cargo build`, `cargo test`,
`pre-commit run --all-files`, and the CI workflow must all pass
after every commit, not just at the end of the phase.

## Situation

Ryll is currently a single-package Cargo project. The repo
layout is:

```
ryll-repo/
├── Cargo.toml          # [package] name = "ryll"
├── Cargo.lock
├── src/                # ryll source
├── target/             # build output
├── .cargo-cache/       # docker-mounted cargo cache
├── .devcontainer/
├── .github/workflows/  # CI, release, codeql, renovate, etc.
├── .pre-commit-config.yaml
├── scripts/
│   └── check-rust.sh   # rustfmt + clippy via docker
├── tools/
├── docs/
├── Makefile
├── README.md
├── AGENTS.md
├── ARCHITECTURE.md
├── STYLEGUIDE.md
├── LICENSE
├── PLAN-TEMPLATE.md
├── PUSH-AUDIT.md
└── renovate.json
```

The package's `Cargo.toml` declares `name = "ryll"`,
`version = "0.1.3"`, edition 2021, a default `capture` feature,
~30 dependencies, and the `[package.metadata.deb]` and
`[package.metadata.generate-rpm]` blocks used by the packaging
workflow.

CI ([.github/workflows/ci.yml](https://github.com/shakenfist/ryll/blob/develop/.github/workflows/ci.yml))
runs `cargo fmt --check`, `cargo clippy -- -D warnings`,
`cargo build --release`, `cargo test`, `cargo deb --no-build`,
and `cargo generate-rpm` from the repo root, with no `-p` or
`--workspace` flags. The release workflow
([.github/workflows/release.yml](https://github.com/shakenfist/ryll/blob/develop/.github/workflows/release.yml))
does the same and additionally `grep`s `^version = ` from the
top-level `Cargo.toml` to verify the git tag matches.

The pre-commit script
([scripts/check-rust.sh](https://github.com/shakenfist/ryll/blob/develop/scripts/check-rust.sh)) runs
`cargo fmt --check` and `cargo clippy -- -D warnings` inside the
ryll-dev docker image, with the repo mounted at `/workspace`.

The Makefile has `build`, `release`, `test`, `lint`, `lint-fix`
targets that all run cargo inside docker with the repo mounted
at `/workspace`.

`renovate.json` has no path-specific configuration; modern
Renovate auto-discovers Cargo workspace members, so no change
is needed there.

[AGENTS.md](https://github.com/shakenfist/ryll/blob/develop/AGENTS.md) and
[ARCHITECTURE.md](https://github.com/shakenfist/ryll/blob/develop/ARCHITECTURE.md) have ~16 references to
`src/...` paths between them; [README.md](https://github.com/shakenfist/ryll/blob/develop/README.md) has
one. Historical phase plans in `docs/plans/` reference `src/`
~84 times across 20 files; those are historical records of
completed work and should NOT be rewritten.

## Mission and problem statement

Convert the ryll repository into a Cargo workspace with `ryll`
as the sole initial member, so that subsequent extraction phases
can add `shakenfist-spice-compression`,
`shakenfist-spice-protocol`, and `shakenfist-spice-usbredir` as
sibling workspace members.

This phase is purely structural. After it completes:

- `cargo build`, `cargo test`, `cargo clippy`, `cargo fmt`,
  `cargo deb --no-build`, and `cargo generate-rpm` all behave
  identically from the user's perspective (same outputs, same
  binary in `target/release/ryll`, same `.deb` and `.rpm`
  artifacts).
- The release workflow continues to verify the version against
  the git tag.
- `pre-commit run --all-files` continues to pass.
- The IDE / rust-analyzer continues to work for ryll source
  files without manual reconfiguration.

There must be no Rust source changes, no dependency changes, no
behavioural changes, and no version bump.

## Layout decision

Two layouts were considered:

**Option A — keep ryll's package files at the workspace root:**
the top-level `Cargo.toml` would carry both a `[workspace]` and
a `[package]` section, with `members = [".", ...]`. This is the
minimum-disruption option (no file moves) but produces a
confusing tree once the extracted crates land:

```
ryll-repo/
├── Cargo.toml                       # workspace + ryll package
├── src/                             # ryll source (named "src/" not "ryll/")
├── shakenfist-spice-compression/
├── shakenfist-spice-protocol/
└── shakenfist-spice-usbredir/
```

A new contributor reading this tree has no way to tell that
`src/` is ryll while the named directories are sibling crates.
The dual-purpose `Cargo.toml` is also less idiomatic.

**Option B — move ryll's package files into a `ryll/`
subdirectory** (chosen): the top-level `Cargo.toml` becomes
workspace-only, and ryll lives in `ryll/` alongside the other
extracted crates:

```
ryll-repo/
├── Cargo.toml                       # workspace only
├── ryll/
│   ├── Cargo.toml                   # [package] name = "ryll"
│   └── src/
├── shakenfist-spice-compression/
├── shakenfist-spice-protocol/
└── shakenfist-spice-usbredir/
```

This is the conventional Rust workspace layout used by tokio,
serde, hyper, and most other multi-crate projects. It is a
larger one-time move but produces a cleaner ongoing structure.

The chosen approach is **Option B**. The cost is a single
mechanical commit; the benefit accrues forever.

## Approach

The phase is sequenced so that each commit leaves CI green:

1. **Pre-flight commits** update CI, scripts, the Makefile, and
   the docker entrypoints to use workspace-aware cargo flags
   (`--workspace`, `--all`, `-p ryll`). These flags are valid on
   a single-package project too — `--workspace` on a degenerate
   single-package "workspace" is a no-op — so they can be
   introduced *before* the workspace exists, and they continue
   to work afterwards. This makes the actual structural commit
   smaller and lower-risk.

2. **The structural commit** creates the workspace manifest and
   moves ryll's package files into `ryll/`. By this point all
   tooling is already workspace-aware, so the move itself is
   just `git mv` plus writing the new top-level `Cargo.toml`.

3. **A documentation commit** updates the live docs (README,
   AGENTS, ARCHITECTURE) to refer to `ryll/src/...` instead of
   `src/...`. Historical plan files in `docs/plans/` are
   intentionally left alone — they record past work and the
   `src/` paths were correct at that time.

## Pre-flight verification

Before starting, verify:

1. **Working tree is clean**: `git status` shows no
   uncommitted changes (or only changes that should be
   stashed/committed first).
2. **CI is green on the current commit**: check the most recent
   CI run on the branch. If CI is already broken, fix that
   first; do not stack the workspace conversion on top of an
   existing failure.
3. **`cargo deb --no-build -p ryll` works on the current
   single-package layout** (sanity check that `-p ryll` is
   accepted before adding it to CI). Run locally inside the
   devcontainer:
   ```sh
   make release
   docker run --rm -v "$PWD":/workspace -w /workspace ryll-dev \
     sh -c "cargo install cargo-deb --locked && cargo deb --no-build -p ryll"
   ```
   If this fails, stop and investigate. The likely failure mode
   is that the cargo-deb in the devcontainer is older than
   expected; we may need to either pin a version or omit `-p`.
4. **`cargo generate-rpm -p ryll` works**: same sanity check.
5. **`cargo clippy --workspace --all-targets -- -D warnings`
   works on the current layout** (sanity check that the
   `--workspace` and `--all-targets` flags don't surface new
   clippy lints that pass under the bare `cargo clippy`
   invocation). If new lints surface, fix them in a separate
   commit *before* proceeding so they are not entangled with
   the workspace conversion.

If any of these pre-flight checks fail, stop and re-plan.

## Execution

Each step below is one commit unless explicitly noted.

### Step 1: Pre-flight CI updates

Update [.github/workflows/ci.yml](https://github.com/shakenfist/ryll/blob/develop/.github/workflows/ci.yml)
to use workspace-aware cargo flags throughout:

- `cargo fmt --check` → `cargo fmt --all --check` (the `--all`
  flag tells rustfmt to format every package in the workspace;
  on a single-package project this is a no-op).
- `cargo clippy -- -D warnings` → `cargo clippy --workspace
  --all-targets -- -D warnings`. The `--all-targets` flag
  ensures clippy checks tests, examples, and benches in
  addition to lib/bin targets, which is the correct hygiene
  for a workspace.
- `cargo build --release ${{ matrix.features }}` →
  `cargo build --release -p ryll ${{ matrix.features }}`. The
  `-p ryll` is a no-op today and an explicit selector after the
  conversion.
- `cargo test ${{ matrix.features }}` →
  `cargo test --workspace ${{ matrix.features }}`.
- `cargo deb --no-build` → `cargo deb --no-build -p ryll`.
- `cargo generate-rpm` → `cargo generate-rpm -p ryll`.

Apply the same updates to
[.github/workflows/release.yml](https://github.com/shakenfist/ryll/blob/develop/.github/workflows/release.yml).
Additionally, the release workflow's version-check step uses
`grep '^version = ' Cargo.toml`. After the structural commit,
the top-level `Cargo.toml` will be a workspace manifest with no
`version = ` field, and `Cargo.lock` resolution would pick up
the workspace member's version from `ryll/Cargo.toml`. To make
this commit a true no-op pre-flight (changes only flags, not
behaviour), do NOT change the grep target in this commit. The
grep target moves in Step 3 (the structural commit), where the
Cargo.toml itself moves.

Verify locally that `cargo build`, `cargo test`,
`cargo clippy`, and `cargo deb --no-build -p ryll` all still
work with the new flags on the unchanged single-package layout.

Push and confirm CI passes before proceeding.

**Commit message (subject ≤ 50 chars):**
> Use workspace-aware cargo flags in CI.

### Step 2: Pre-flight script and Makefile updates

Update [scripts/check-rust.sh](https://github.com/shakenfist/ryll/blob/develop/scripts/check-rust.sh):

- `cargo fmt --check` → `cargo fmt --all --check`
- `cargo fmt` → `cargo fmt --all`
- `cargo clippy -- -D warnings` →
  `cargo clippy --workspace --all-targets -- -D warnings`
- `cargo clippy --fix --allow-dirty -- -D warnings` →
  `cargo clippy --fix --allow-dirty --workspace --all-targets
  -- -D warnings`

Update [Makefile](https://github.com/shakenfist/ryll/blob/develop/Makefile) targets `build`, `release`,
`test`, `lint`, `lint-fix` to use the same workspace-aware
flags:

- `cargo build` → `cargo build -p ryll` (build target stays
  ryll-only; once we have multiple members, a separate
  `make build-all` could be added if useful).
- `cargo build --release` → `cargo build --release -p ryll`.
- `cargo test` → `cargo test --workspace`.
- The `lint` and `lint-fix` shell strings need the same updates
  as `check-rust.sh`.

Verify locally that `make lint`, `make build`, `make test`, and
`pre-commit run --all-files` all still work. CI should remain
green; this commit only touches local-dev tooling.

**Commit message (subject ≤ 50 chars):**
> Use workspace-aware cargo flags in tooling.

### Step 3: Structural commit — convert to workspace

This is the actual structural change. It must be one commit
because the move and the new manifest are co-dependent — the
intermediate state where `Cargo.toml` exists but `src/` has
moved is not buildable.

Steps within the commit:

1. `git mv Cargo.toml ryll/Cargo.toml`
2. `git mv src ryll/src`
3. Edit `ryll/Cargo.toml`:
   - Replace the literal `edition`, `license`, `authors`,
     `repository` fields with workspace-inheritance forms:
     ```toml
     [package]
     name = "ryll"
     version = "0.1.3"
     edition.workspace = true
     license.workspace = true
     authors.workspace = true
     repository.workspace = true
     description = "A Rust SPICE VDI test client"
     ```
   - The `[features]`, `[dependencies]`, `[dev-dependencies]`,
     `[package.metadata.deb]`, and `[package.metadata.generate-rpm]`
     sections move with the file unchanged.
   - **Important**: `version` does NOT move into
     `[workspace.package]` — each future workspace member
     (compression, protocol, usbredir) will have its own
     independent version. ryll's version stays per-package.
4. Create the new top-level `Cargo.toml`:
   ```toml
   [workspace]
   resolver = "2"
   members = ["ryll"]

   [workspace.package]
   edition = "2021"
   license = "Apache-2.0"
   authors = ["Michael Still <mikal@stillhq.com>"]
   repository = "https://github.com/shakenfist/ryll"
   ```
5. Update the version-check step in
   [.github/workflows/release.yml](https://github.com/shakenfist/ryll/blob/develop/.github/workflows/release.yml):
   `grep '^version = ' Cargo.toml` →
   `grep '^version = ' ryll/Cargo.toml`. This must happen in
   the same commit as the file move so release tagging works
   atomically.
6. Verify locally:
   - `cargo build` (debug, fast sanity check)
   - `cargo build --release -p ryll`
   - `cargo test --workspace`
   - `cargo clippy --workspace --all-targets -- -D warnings`
   - `cargo fmt --all --check`
   - `cargo deb --no-build -p ryll`
   - `cargo generate-rpm -p ryll`
   - `pre-commit run --all-files`
   - The output `target/release/ryll` binary still exists at
     the same path (target dir is at the workspace root, which
     is the same as the old package root).
   - Open the project in VS Code / rust-analyzer and confirm
     it picks up the new layout without manual `Cargo.toml`
     selection. (If it does not, the `.vscode/settings.json`
     may need a `"rust-analyzer.linkedProjects":
     ["./Cargo.toml"]` entry; add it in this commit if so.)
7. Push and confirm CI passes.

**Commit message (subject ≤ 50 chars):**
> Convert ryll to a Cargo workspace.

### Step 4: Documentation updates

Update the live docs that reference `src/` paths to use
`ryll/src/`:

- [README.md](https://github.com/shakenfist/ryll/blob/develop/README.md) — 1 occurrence.
- [AGENTS.md](https://github.com/shakenfist/ryll/blob/develop/AGENTS.md) — ~8 occurrences. Many of these
  are in build/development sections that may also benefit from
  noting the new workspace layout briefly.
- [ARCHITECTURE.md](https://github.com/shakenfist/ryll/blob/develop/ARCHITECTURE.md) — ~8 occurrences.
  The architecture description should mention that ryll is now
  a workspace member at `ryll/`, with a brief note that the
  workspace exists in preparation for extracted crates per
  [PLAN-crate-extraction.md](/components/ryll/plans/PLAN-crate-extraction/).

Do **not** rewrite historical plan files in `docs/plans/`. They
document past work where the `src/` paths were accurate at the
time. Their function is historical, not navigational.

[STYLEGUIDE.md](https://github.com/shakenfist/ryll/blob/develop/STYLEGUIDE.md) had no `src/` references in
the audit and does not need updating.

Verify `pre-commit run --all-files` still passes (it has no
docs hooks, but run it anyway to be safe).

**Commit message (subject ≤ 50 chars):**
> Update docs for the workspace layout.

## Open questions

1. **VS Code rust-analyzer auto-detection.** The plan assumes
   rust-analyzer correctly picks up the new workspace layout
   without configuration. This is the documented behaviour, but
   if it does not work in the actual devcontainer, Step 3 needs
   to also write `.vscode/settings.json` with
   `"rust-analyzer.linkedProjects": ["./Cargo.toml"]`. Verify
   during Step 3 execution and adjust if needed.

2. **`cargo deb` workspace flag handling.** `cargo deb` has
   evolved its workspace support over the years. The pre-flight
   verification confirms `-p ryll` works on the *current*
   layout; it should also work on the post-conversion layout
   since `-p` is the standard cargo selector. If it does not,
   alternative is `cargo deb --no-build --manifest-path
   ryll/Cargo.toml`. Do not assume; verify.

3. **`cargo-deb` reading `[package.metadata.deb]` from
   subdirectory.** When ryll moves to `ryll/Cargo.toml`,
   cargo-deb should read the metadata block from there
   automatically when given `-p ryll`. The output `.deb` should
   land in `target/debian/` (the workspace target dir), not
   `ryll/target/debian/`. Verify in Step 3.

4. **`renovate.json` and Cargo workspace discovery.** Renovate
   should auto-discover the workspace via the new top-level
   `[workspace] members = ["ryll"]`. No `renovate.json` change
   should be needed, but watch for the next renovate run after
   the conversion lands to confirm it still produces dependency
   update PRs against `ryll/Cargo.toml`.

5. **CodeQL workflow.** Not audited in detail. If
   [.github/workflows/codeql-analysis.yml](https://github.com/shakenfist/ryll/blob/develop/.github/workflows/codeql-analysis.yml)
   has hardcoded paths or build commands, they may also need
   the workspace-aware-flag treatment. Audit this file as part
   of Step 1; if it needs changes, include them in Step 1's
   commit.

## Administration and logistics

### Success criteria

We will know Phase 1 has been successfully implemented when:

* The repo has a top-level workspace `Cargo.toml` listing
  `ryll` as a workspace member.
* `ryll/Cargo.toml` is the package manifest, inheriting
  `edition`, `license`, `authors`, and `repository` from the
  workspace.
* `ryll/src/` contains the same files (by content) as the old
  `src/` did before the conversion.
* `cargo build`, `cargo test --workspace`, and
  `cargo clippy --workspace --all-targets -- -D warnings` all
  succeed from the workspace root.
* `pre-commit run --all-files` succeeds.
* CI is green on the branch after the final commit of this
  phase.
* `cargo deb --no-build -p ryll` and
  `cargo generate-rpm -p ryll` produce artifacts in
  `target/debian/` and `target/generate-rpm/` respectively
  (i.e. the existing release pipeline still works).
* The release workflow's version-check still finds the version
  string (now in `ryll/Cargo.toml` instead of `Cargo.toml`).
* No Rust source files have been modified (only moved).
* No `Cargo.lock` entries have changed except for path-related
  metadata (cargo will rewrite the lockfile on first build
  after the move; the dependency graph itself must not change).
* `target/release/ryll` exists at the same workspace-relative
  path it did before.
* README.md, AGENTS.md, and ARCHITECTURE.md refer to
  `ryll/src/...` paths.
* Historical plan files in `docs/plans/` are unchanged.

### Future work

* **Hoist more shared metadata.** Once the extracted crates
  exist, fields like `rust-version` (if we adopt one),
  `homepage`, and `keywords` should also be hoisted into
  `[workspace.package]`. Defer until at least one extracted
  crate exists, so we can see what is actually shared.
* **Workspace-wide lint config.** Once multiple members exist,
  consider hoisting clippy lint configuration into
  `[workspace.lints.clippy]` so all members share the same
  lint rules without per-package duplication. Defer to Phase 3
  or later.
* **`make build-all` target.** Once multiple workspace members
  exist, the Makefile may want a `build-all` target alongside
  the existing ryll-specific `build`. Defer until needed.

### Bugs fixed during this work

* **Five clippy lints in test code** surfaced by the
  `cargo clippy --workspace --all-targets` pre-flight check.
  CI was previously running plain `cargo clippy` without
  `--all-targets`, so test-target lints were not being
  enforced. Fixed in commit 7191d9e (separate from the
  workspace conversion itself):
  - `bugreport.rs` (4 occurrences): test snapshot constructors
    used `let mut x = T::default(); x.field = ...;` patterns
    that triggered `field_reassign_with_default`. Converted to
    struct literal syntax with `..Default::default()`.
  - `webdav/server.rs` (1 occurrence): test helper
    `header_value` had a needless explicit lifetime. Elided.

### Discoveries during execution

* **`cargo generate-rpm -p` is not standard cargo `-p`
  semantics.** Unlike `cargo deb -p ryll`, which works on both
  the pre- and post-conversion layouts, `cargo generate-rpm -p
  ryll` interprets its argument as a `<name>/Cargo.toml` path
  lookup. On the pre-conversion single-package layout there is
  no `ryll/Cargo.toml`, so the command fails with "No such
  file or directory". As a result, the `-p ryll` flag for
  `cargo generate-rpm` had to move from Step 1 (CI flag
  no-ops) into Step 3 (the structural commit), so that the
  flag and the path it references land atomically.
* **Git rename detection got confused** by the new top-level
  `Cargo.toml` having the same path as the original. Step 3
  shows up as `modified: Cargo.toml` + `new file: ryll/Cargo.toml`
  rather than a clean rename. The end state is correct and
  `git log --follow ryll/Cargo.toml` should still find the
  history via similarity scoring at log time, but the cosmetic
  diff is less clean than ideal. The 35 source files in
  `src/` -> `ryll/src/` did all register as 100% renames.

### Back brief

Before executing any step of this plan, back brief the operator
as to your understanding of the plan and how the work you intend
to do aligns with that plan. Specifically confirm:

1. The chosen layout (Option B: ryll moves into `ryll/`).
2. The four-commit sequence: CI flags → script/Makefile flags
   → structural conversion → docs.
3. That you will run the pre-flight verification commands
   *before* starting Step 1, and stop if any of them fail.
4. That you understand what changes go in which commit, and
   that no commit may leave CI broken.
