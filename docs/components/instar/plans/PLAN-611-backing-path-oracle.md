# PLAN-611: Backing-path resolution must not answer questions about the host

## Status: Complete

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Flag any uncertainty
explicitly rather than guessing.

Consult `ARCHITECTURE.md` for the overall system structure,
`AGENTS.md` for build commands and conventions, and
`docs/chain-discovery.md` for the backing-chain security model this
plan changes.

The request was "/pick-issue", which recommended issue #611, and
then "start on #611 with a short plan first".

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

This is a single-change plan in the shape of `PLAN-375-guest-idt.md`:
one pull request, no phase files. Its steps are the Execution table
below, and the push audit is its last step.

## Situation

Every backing reference instar follows -- a qcow2 backing file, a
qcow2 external data file, a VMDK flat extent, a VHD or VHDX parent
locator -- is resolved on the host by `validate_backing_path` in
`src/vmm/src/chain.rs`, which calls `resolve_backing_path` and only
then consults the allowlist. All of these are image-controlled
strings.

`resolve_backing_path` touches the filesystem before the allowlist is
consulted, in three places:

1. **Absolute references.** `backing.exists()` on the
   attacker-chosen path. If it exists, the path is used and the
   allowlist refuses it ("outside allowed paths"). If it does not,
   the basename is tried beside the image, and failing that the
   answer is "not found".
2. **Relative references.** `parent_dir.join(backing).canonicalize()`.
   For `../../../etc/shadow`, `canonicalize` fails with `NotFound`
   when the target does not exist and succeeds when it does, after
   which the allowlist refuses it. Issue #611 describes only the
   absolute case. This one is the same oracle, and the test suite
   already records it as a fact:
   `ADVERSARIAL_LOCATOR_REASONS['vhd-diff-locator-dotdot']` in
   `tests/test_differencing.py` is a tuple of both answers, because
   which one you get "is a fact about the host rather than about
   instar".
3. **Symlink targets.** `BackingFileNotAllowed` carries the
   *canonical* path, and its `Display` prints it. A reference that
   passes through a symlink inside the allowlist therefore prints
   where that symlink points.

In each case the reason is distinguishable, so an image can ask
"does this host path exist?" and read the answer from stderr. Phase
14 of `PLAN-differencing.md` made `info --chain` walk differencing
parents, and it **exits 0** while printing the reason. A service that
runs it over uploaded images and returns stderr hands the uploader
that answer even when the command succeeds.
`docs/chain-discovery.md` currently documents this as a caveat and
tells operators to withhold that line.

Only existence leaks, and only paths, never content. Nothing outside
the allowlist is ever opened. The cost is real all the same: probing
an attacker-chosen absolute path can also trigger an automount, or
block on a dead NFS mount, before any refusal is reached.

A latent trap matters for the fix. `is_path_allowed` canonicalises
with `unwrap_or_else(|_| path.to_path_buf())`, so a path that does
not exist is compared raw, and `Path::starts_with` compares
components without resolving `..`. `/imgs/../etc/x` therefore
"starts with" `/imgs`. This is harmless today, because the function
only ever sees canonical paths. A lexical pre-check built by calling
it on an un-normalised path would be an allowlist bypass.

## Mission and problem statement

Establish and test one invariant:

> **The outcome of resolving a backing reference -- which path it
> resolves to, or which error it fails with and what that error
> prints -- depends only on filesystem state inside the allowlist.**

Corollary: a reference that is lexically outside the allowlist causes
no filesystem access at all.

The fix lands once, in `src/vmm/src/chain.rs`. It applies to every
format and every operation that resolves a backing reference, because
they all go through `validate_backing_path`.

## Decisions

**D1. Lexical gate first.** Build the candidate path without touching
the filesystem. For an absolute reference that is the reference
itself; for a relative one it is the parent image's directory joined
with the reference. Normalise it lexically: drop `.`, resolve `..`
against the preceding component, and clamp `..` at the root as the
kernel does. Make it absolute with `std::path::absolute`, which reads
only the cwd. Compare it against the allowlist. A candidate outside
the allowlist is refused, or sent to the fallback (D2), before any
`exists`, `metadata` or `canonicalize` call. The normaliser and the
check are pure functions and are unit-tested as such.
`is_path_allowed` is not used for this check (see the trap above).

**D2. The basename fallback for absolute references.** Today an
absolute reference that does not exist falls back to its basename
beside the parent image, so that images built on another host still
resolve. That choice is keyed on the existence of an attacker-chosen
path, which is the oracle itself. It has to be keyed on something
lexical instead. Two options were considered, and **(a) was chosen**
by the operator on 2026-10-08:

* **(a) Chosen.** An absolute reference that is lexically
  outside the allowlist goes straight to the basename fallback. It is
  never probed. A fallback that resolves is used, and a line on
  stderr names the substitution:
  `using '<basename>' beside the image in place of '<reference>'`.
  The portability behaviour survives intact. The behaviour change is
  that an absolute reference outside the allowlist that *does* exist,
  with a same-named file beside the image, now resolves to the local
  file where it used to be refused. That is the hazard the fallback
  already carries when the absolute path is missing; it now applies
  whether or not the outside path exists, which is the point. The
  stderr line keeps it visible.
* **(b)** An absolute reference that is lexically outside the
  allowlist is refused outright, and the fallback applies only to an
  absolute reference that is inside the allowlist but missing. This
  is stricter and simpler, but it breaks the portability case the
  fallback exists for: a path from another machine is almost always
  outside this machine's allowlist.

An absolute reference that is inside the allowlist is resolved
normally. If it is missing, it falls back to the basename exactly as
today.

**D3. Allowlist entries are compared in both spellings.** Each entry
is matched in its lexical-absolute form *and* its canonical form.
Entries are operator configuration, not image data, so probing them
is fine. This keeps `$IMAGE_DIR` working where the image directory is
reached through a symlink.

**D4. Symlink escape inside the allowlist.** A candidate that passes
D1 is canonicalised. If `canonicalize` succeeds, the canonical path
is checked against the allowlist as it is today. If it fails with
`NotFound`, walk up the lexical path to the deepest ancestor that
does canonicalise. If that ancestor's canonical form is outside the
allowlist, the answer is "outside allowlist"; otherwise it is "not
found". Take `link -> /etc` inside the image directory:
`link/shadow` and `link/nosuch` then both answer "outside allowlist",
whether or not the target exists. Other errors from `canonicalize`
(permission denied, for example) are dealt with the same way, so a
directory outside the allowlist that cannot be read gives no third
answer.

As built, D4 is a component-by-component walk rather than
`canonicalize` plus an ancestor walk. The ancestor walk still leaked:
a link inside the image directory to a *missing* directory outside
answers "not found" until that directory appears. The walk checks
each symlink target before following it, so it never probes an
image-chosen name outside the allowlist. The lexically normalised
candidate drives only the pre-gate, the fallback decision and the
error text; the walk follows the reference as written, so `..`
after a symlink lands where the kernel and qemu-img put it.

**D5. Errors print the reference, not the resolution.**
`BackingFileNotAllowed` and `BackingFileNotFound` carry the
lexically normalised candidate, never the canonical path, so a
symlink's target is never printed. The message text keeps its
current wording, which `tests/test_check_chain.py` and the docs
quote.

**D6. Narrowing accepted.** A reference whose lexical spelling is
outside the allowlist, but whose canonical form would be inside
(`/elsewhere/link-into-images/base.qcow2`), is now refused, or sent to
the fallback, without being probed. Proving that it lands inside
would mean probing outside the allowlist, which is the thing being
removed. Under (a) the fallback recovers the common case.

**Out of scope.** Escaping image-derived strings on the terminal
(#609) and the JSON escape helpers (#612) are separate; #609 is the
natural follow-on. The `allowed: Vec<PathBuf>` list printed in
`BackingFileNotAllowed` is operator configuration, not a host probe,
and is left as is. Windows absolute references are already classified
before resolution (`is_windows_absolute_reference`) and are not
touched. The chain device cap (#601) is unrelated.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | none | Rewrite backing resolution in `src/vmm/src/chain.rs` to D1-D5. Add pure helpers `lexical_backing_candidate(parent_dir, reference) -> PathBuf` and `lexically_allowed(path, allowlist) -> bool`, the latter matching each entry in both its lexical-absolute and canonical form (D3) and never calling `is_path_allowed` on a non-canonical path. Restructure `validate_backing_path` (callers: `resolve_reported_parent`, the qcow2 backing/data-file and VMDK descriptor sites in `src/vmm/src/main.rs`, `resolve_vmdk_flat_descriptor` in `chain.rs`) so that the lexical gate runs before any `exists`/`canonicalize`; then the D2(a) fallback, emitting its substitution line on stderr from the host the same way other chain-walk notices are emitted; then canonicalise with the D4 ancestor walk; then the canonical allowlist check. `resolve_backing_path` is public but only `validate_backing_path` and the unit tests call it, so fold it in or keep it private. Errors carry the lexical candidate (D5). Check how `parent_image` arrives (it may be relative for the top image) and make it absolute without canonicalising it. Unit tests, in the existing `mod tests` in `chain.rs`, built with `tempfile` dirs: (i) the invariant -- for each of an absolute reference outside, a relative `../` escape, a symlink-in-allowlist escape and an absolute reference with a basename fallback, resolve once with the outside target present and once with it absent, and assert equal `Result`s and equal `Display` strings; (ii) normaliser cases (`..` past root, `.`, trailing slash, `a/b/../../..`); (iii) `/imgs/../etc/x` is not lexically allowed for allowlist `/imgs`; (iv) a symlinked `$IMAGE_DIR` still resolves its own children; (v) no `Display` contains a symlink's target. Update the existing tests at the end of `mod tests` that pin the old absolute-path behaviour. Run `make test-rust` (see the `build-and-test` skill; note the target-dir ownership trap if pre-commit has run as root). |
| 2 | medium | sonnet | none | Integration tests. In `tests/test_differencing.py`, collapse `ADVERSARIAL_LOCATOR_REASONS['vhd-diff-locator-dotdot']` to the single "outside the backing file allowlist" reason, rewrite the comment above it (the "fact about the host" paragraph is no longer true), and fix the parallel composing-walk table below it the same way. Add a CLI test, in whichever of `tests/test_security.py` or `tests/test_info_safe.py` already holds chain-walk security tests, that builds two qcow2 children at runtime with `qemu-img create -f qcow2 -u -F raw -b <ref>`: one naming an absolute path outside the allowlist that exists (a temp file outside the image dir) and one naming a sibling path that does not exist. Run `instar info --chain` on both and assert identical rc and an identical stderr reason once the reference string is masked. Repeat for a relative `../` reference. Add a test that the fallback's substitution line appears. Run the touched test modules with `make test-integration` filtered to them, then the full chain-related suites. |
| 3 | medium | sonnet | none | Documentation. In `docs/chain-discovery.md`, replace the "One caveat about the reasons themselves" paragraph, which tells operators to withhold stderr, with a statement of the invariant and what it costs (D6, and D2's substitution line). Check the "Path Validation" example message still matches. Add a `CHANGELOG.md` `[Unreleased]` `### Security` entry (create the subsection if absent): resolution no longer distinguishes missing from disallowed paths outside the allowlist, for absolute and relative references and through symlinks; errors no longer print a symlink's target. Name the behaviour change from D2/D6 plainly. Grep `docs/` for other text describing the resolution order (`docs/security-audits.md`, `ADVERSARIAL.md`) and correct it. |
| 4 | high | opus | none | Push audit: run `PUSH-AUDIT.md` over `git diff origin/develop...HEAD`. Findings are fixed in this branch before the PR, or declined in writing here. |

Commits: the plan; step 1 (code and unit tests); step 2; step 3; any
fixes from the audit. Every commit passes `pre-commit run
--all-files`.

### Review checklist additions

- [ ] Grep the step 1 diff for every `exists(`, `metadata(`,
      `canonicalize(` and `read_link(` it introduces or keeps, and
      confirm each is reached only by a candidate that has passed the
      lexical gate, or by an allowlist entry.
- [ ] `tests/test_differencing.py` no longer has a reason tuple with
      two answers for one fixture.

## Administration and logistics

### Success criteria

* The invariant holds and is tested at both the unit and CLI levels.
* `make instar` builds and `make lint` is clean; `make test-rust`
  and the chain-related integration suites pass.
* `pre-commit run --all-files` passes.
* `docs/chain-discovery.md` no longer carries a caveat telling
  operators to withhold stderr, and `CHANGELOG.md` records the change.
* Issue #611 is closed from the pull request body (`Fixes #611`),
  which is the only placement that closes it when the PR lands
  through a merge commit.

### Future work

* `rebase` builds its chain with `SecurityConfig::default()` rather
  than the loaded configuration, so an operator's
  `backing-path-allowlist` is ignored there. It is stricter, not
  looser, so it is a consistency fix, not a security one.
* The fallback's stderr line prints the image-chosen reference
  unescaped, the same class as #609. When that is escaped, consider
  returning the substitution to the caller instead of printing it
  inside the resolver, so a walk that resolves a reference twice
  cannot announce it twice.
* The resolver returns a symlink-free path that is opened later by
  path, so someone able to write inside the allowlist could swap a
  component for a symlink between the two. The canonicalise-then-check
  code it replaced had the same gap. Opening with `O_NOFOLLOW`, or
  `openat2` with `RESOLVE_BENEATH`, would close it.

* #609: image-derived path strings reach the terminal unescaped in
  `info` human output. It is the same audience (a service returning
  stderr) and the natural next fix.

### Bugs fixed during this work

* #611, broadened to relative references and symlink targets as
  described in Situation.

### Push audit

Run on 2026-10-08 over `f8d71948..cc66c364`. Wave 1 was clean, and
the five CLI tests fail on the develop binary. Findings and what was
done with them:

| Finding | Disposition |
|---------|-------------|
| `commit` without `-b` opens the overlay's recorded backing for writing without consulting the allowlist (pre-existing) | Fixed in this branch, at the operator's direction |
| `..` after a symlink in the image path or an allowlist entry made a lexical-only entry the walk honoured | Fixed: the image directory is canonicalised, and entries spelled with `..` keep only their canonical form |
| `sub/link/../x` resolved differently from qemu-img and develop | Fixed: the walk follows the reference as written |
| Comments overstated "never looks outside the allowlist" | Fixed: it is never an image-chosen path |
| Fallback test assumed a symlink-free `TMPDIR` | Fixed |
| No CLI test for a symlink escape | Added |
| Fallback line prints the reference unescaped | Declined here: the #609 class, recorded under Future work |
| `chain.rs` is past 1,500 lines | Declined: splitting the resolver out is a refactor for its own PR |

The PR review (shakenfist/instar#648) found that commit, now resolving
the recorded backing through `validate_backing_path`, also took the
same-name file beside the overlay in place of a recorded backing it
could not use, so an overlay could still pick a file to be written:
any namesake of its reference. Commit's write target now goes through
`validate_backing_write_target`, which refuses instead and asks for
`-b`. The same review pointed out that the default allowlist refused
`-b` naming a parent in another directory; a recorded reference
spelled exactly as `-b`, with no `..`, now matches without being
looked at.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
