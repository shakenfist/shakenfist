# Supply-chain and prompt-injection scanning

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
CI workflow files, pre-commit config, `scripts/check-rust.sh`,
and `AGENTS.md`. Understand how CI currently runs, what
pre-commit already covers, and which runner labels are in
use. Do not speculate when you could read the file instead.

When researching external tooling (`cargo audit`,
`cargo deny`, `gitleaks`, unicode bidi detection), ground
recommendations in the upstream documentation rather than
memory, and flag any uncertainty explicitly.

## Situation

While formalising our merge process for external PRs (see
`MERGE-TEMPLATE.md` on branch `pr-31`), we identified a set
of deterministic, non-LLM scanners that would catch
supply-chain risk, credential leaks, known-vulnerable
dependencies, and Trojan Source / prompt-injection patterns.
These scanners have two properties that make them worth
adopting beyond the PR-merge context:

1. They run in milliseconds to seconds, not minutes.
2. They are pattern- or database-driven, not LLM-driven, so
   they cannot be manipulated by adversarial content in the
   code they inspect.

A license audit of the 670 crates in `Cargo.lock` via
`cargo metadata` (run inside the `ryll-dev` Docker image)
returned the following unusual license declarations:

- `epaint_default_fonts 0.29.1` —
  `(MIT OR Apache-2.0) AND OFL-1.1 AND LicenseRef-UFL-1.0`.
  This is the embedded-fonts crate used by `egui`/`epaint`.
  The `AND` clause forces us to accept both OFL-1.1 and
  UFL-1.0. UFL is the Ubuntu Font Licence; it is permissive
  for redistribution and embedding but is non-SPDX-standard,
  so `cargo deny` requires an explicit exception.
- `configparser 3.1.0` — `MIT OR LGPL-3.0-or-later`.
- `htmlescape 0.3.1` — `Apache-2.0 / MIT / MPL-2.0`
  (deprecated `/` separator, semantically OR).
- `r-efi 5.3.0` and `6.0.0` —
  `MIT OR Apache-2.0 OR LGPL-2.1-or-later`.

The last three all offer a permissive option via the `OR`
clauses, so they resolve automatically against a
permissive-only allowlist. Only `epaint_default_fonts`
requires an exception.

A review of `.github/workflows/ci.yml` confirmed that the
existing pre-commit hooks are covered in CI as follows:

- `rust-check` (rustfmt + clippy): covered
  by the `lint` job (`cargo fmt --all --check` and
  `cargo clippy ... -D warnings`).
- `shellcheck`: **not covered in CI**. A contributor who
  skips pre-commit locally can land shell-script changes
  that fail shellcheck.

This plan closes that gap as a side-effect of adding the
new scanners.

The existing workflows use `[self-hosted, static]` for all
non-matrix jobs; only the cross-platform `build` matrix in
`ci.yml` uses GitHub-hosted runners because we do not have
macOS or Windows self-hosted runners. New jobs in this plan
all run on `[self-hosted, static]`.

## Mission and problem statement

Add deterministic security tooling to ryll's CI and
pre-commit pipeline so that:

1. Known-vulnerable dependencies (RustSec advisories) are
   detected automatically on every PR and weekly for drift.
2. Dependency policy (licenses, sources, bans, duplicates)
   is enforced on every PR.
3. Accidental credential commits are caught before push and
   in CI.
4. Trojan Source and other unicode-based attacks on source
   code are detected before push and in CI.
5. Every existing pre-commit hook has a matching CI job so
   that skipping pre-commit does not bypass enforcement.

## Open questions

None open. Decisions made during planning:

- **License allowlist**: permissive only. MIT, Apache-2.0,
  BSD-2-Clause, BSD-3-Clause, ISC, Zlib, Unicode-3.0,
  Unlicense, BSL-1.0, CC0-1.0, MIT-0, 0BSD,
  CDLA-Permissive-2.0, OFL-1.1.
- **License exception**: `epaint_default_fonts` allowed
  `LicenseRef-UFL-1.0` via `[[licenses.exceptions]]`.
- **`cargo audit` cadence**: on every PR push plus a weekly
  cron to detect drift from DB updates.
- **`cargo deny check`**: on every PR.
- **Runner choice**: `[self-hosted, static]` everywhere we
  can (all new jobs).
- **`semgrep`**: deferred. The Rust rulesets are thinner
  than other languages and clippy already covers most
  custom-pattern enforcement we care about.
- **`cargo vet` / `cargo crev`**: deferred as
  heavier-than-needed for a solo-maintainer project.
- **`trufflehog`**: rejected in favour of `gitleaks`
  (overlapping coverage, `gitleaks` is leaner).

## Execution

This is a standalone plan, not a phased master plan. The
work is one PR, roughly seven files.

### Files to add or modify

1. **`deny.toml`** (new, repo root)
   - `[graph]` targets Linux/macOS/Windows so we scan the
     full dependency set.
   - `[advisories]` — deny unmaintained + vulnerability
     advisories; `yanked = "deny"`.
   - `[licenses]` allowlist as decided above.
   - `[[licenses.exceptions]]` entry for
     `epaint_default_fonts` allowing `LicenseRef-UFL-1.0`.
   - `[bans]` — `multiple-versions = "warn"`, no specific
     bans yet (start permissive, tighten later if we see
     patterns).
   - `[sources]` — `unknown-registry = "deny"`,
     `unknown-git = "deny"`, only `crates.io` allowed.

2. **`.github/workflows/supply-chain.yml`** (new)
   - `runs-on: [self-hosted, static]`.
   - Triggers: `pull_request` to `develop`, `push` to
     `develop`, `schedule` weekly (Monday 09:00 UTC),
     `workflow_dispatch`.
   - Jobs:
     - `cargo-audit` — `rustsec/audit-check@v2.0.0` or
       equivalent.
     - `cargo-deny` — `EmbarkStudios/cargo-deny-action@v2`.
     - `gitleaks` — `gitleaks/gitleaks-action@v2` with
       `config-path: .gitleaks.toml` if we add a config
       (skip unless we need exceptions).
     - `shellcheck` — fills the CI gap we identified; run
       `shellcheck -x scripts/*.sh tools/*.sh`.
     - `bidi-check` — calls `./tools/check-bidi.sh`
       against the diff.
   - All jobs run in parallel; no dependencies between
     them.

3. **`.pre-commit-config.yaml`** (modify)
   - Add `gitleaks` hook from
     `github.com/gitleaks/gitleaks` (pinned rev).
   - Add local `bidi-check` hook calling
     `tools/check-bidi.sh`.
   - Existing hooks (`rust-check`, `shellcheck`) unchanged.

4. **`tools/check-bidi.sh`** (new)
   - Per `~/.claude/CLAUDE.md` rule: CI logic longer than
     ~5 lines goes into a script in `tools/` so CI YAML
     just invokes it.
   - Behaviour: read each file path from `$@`; grep for
     bidi control codepoints (U+202A–U+202E,
     U+2066–U+2069), zero-width chars (U+200B–U+200F,
     U+FEFF); exit 1 with a clear error if any are found.
   - When invoked with no args, scan the whole tree.

5. **`AGENTS.md`** (modify)
   - Add a short "Security scanners" section documenting:
     - What `deny.toml` controls and how to update the
       allowlist or add exceptions.
     - What `cargo audit` checks and how to respond to
       alerts.
     - What `gitleaks` checks and where to add
       `.gitleaksignore` exceptions.
     - What `tools/check-bidi.sh` checks.
   - Note that all five pre-commit hooks are also enforced
     in CI so skipping pre-commit locally is not a way to
     avoid the checks.

6. **`docs/plans/index.md`** (modify)
   - Add a row under "Standalone plans":
     `| 2026-04-18 | [Supply-chain scanning](/components/ryll/plans/PLAN-supply-chain-scanning/) | Deterministic scanners for dependencies, secrets, and unicode-based attacks |`

7. **`docs/plans/order.yml`** (modify)
   - Add this plan to the navigation.

### Out of scope

- `semgrep`, `cargo vet`, `cargo crev`, `trufflehog` —
  deferred as noted in Open questions.
- Tightening `multiple-versions` from warn to deny. Defer
  until we see the baseline noise.
- Enabling `cargo deny` to block on unmaintained crates. We
  have at least one (`audiopus`) and the migration is
  tracked separately in `PLAN-opus-decoder.md`.
- Extending self-hosted coverage to the macOS/Windows
  build matrix. Requires hardware we don't have.

## Agent guidance

### Execution model

This plan is small enough that the management session can
drive execution directly, but each sub-step is well suited
to a sub-agent with a concrete brief. Recommended:

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 1    | medium | sonnet | none      | Write `deny.toml` with the allowlist, UFL exception for `epaint_default_fonts`, advisories/bans/sources per plan. Verify by running `cargo deny check` in the `ryll-dev` container and iterating until clean. |
| 2    | medium | sonnet | none      | Write `.github/workflows/supply-chain.yml` with the five jobs on `[self-hosted, static]`. Triggers per plan. Use pinned action versions. |
| 3    | low    | haiku  | none      | Add `gitleaks` and `bidi-check` hooks to `.pre-commit-config.yaml`. Pin revs. |
| 4    | low    | sonnet | none      | Write `tools/check-bidi.sh`. POSIX-ish bash, shellcheck-clean, handles no-arg (full tree) and file-list invocation. |
| 5    | low    | sonnet | none      | Update `AGENTS.md` with a "Security scanners" section per the bullets in the plan. |
| 6    | low    | haiku  | none      | Update `docs/plans/index.md` and `order.yml` with the new entry. |

Each step should be a separate commit. The management
session reviews each sub-agent's output (reading the
actual file) before moving on.

### Verification before proposing the commit

Run all of these in order. They must all pass:

- `cargo audit` — clean or findings triaged.
- `cargo deny check` — clean against the new `deny.toml`.
- `pre-commit run --all-files` — clean with new hooks.
- `./tools/check-bidi.sh` — clean against the current
  tree.
- Re-run `pre-commit run --all-files` one more time
  immediately before proposing the commit (per
  `~/.claude/CLAUDE.md`).

If any fail that we can't quickly fix, stop and escalate to
the operator.

## Administration and logistics

### Success criteria

We will know this plan has been successfully implemented
when:

* `cargo audit` runs on every PR and weekly on a cron
  against `develop`.
* `cargo deny check` runs on every PR and enforces the
  allowlist, source policy, and advisories.
* `gitleaks` runs on every PR and catches credential-like
  patterns.
* `tools/check-bidi.sh` runs on every PR and catches bidi
  / zero-width unicode in the diff.
* `shellcheck` runs on every PR (closing the existing
  gap).
* The matching pre-commit hooks all still work locally for
  developers who have run `pre-commit install`.
* `AGENTS.md` documents the scanners and where to update
  policy.
* `docs/plans/index.md` and `order.yml` reference this
  plan.
* `pre-commit run --all-files` passes cleanly on the
  proposed branch.

### Future work

- Revisit `semgrep` if we start seeing recurring patterns
  that clippy can't catch.
- Tighten `multiple-versions` from `warn` to `deny` once
  we've cleaned up the baseline.
- Consider `cargo vet` if ryll attracts enough maintainers
  to justify the trust-graph setup.
- If we start getting gitleaks false positives, introduce
  a `.gitleaksignore` with clear comments for each
  exception.

### Bugs fixed during this work

(To be filled in as we go.)

### Documentation index maintenance

This plan is a standalone plan (no phases) so it only
requires:

* A row in `docs/plans/index.md` under "Standalone plans".
* An entry in `docs/plans/order.yml` for navigation.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
