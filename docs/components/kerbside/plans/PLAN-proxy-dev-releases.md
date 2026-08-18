# Rolling kerbside-proxy dev releases

## Prompt

Before responding to questions or discussion points in this
document, explore the kerbside codebase thoroughly. Read
relevant source files, understand existing patterns (the
Rust SPICE proxy in `rust/kerbside-proxy/`, the gRPC
control contract in `kerbside/rpc/`, the proxy launch path
in `kerbside/proxy_supervisor.py`, the release machinery in
`.github/workflows/release.yml`,
`tools/stamp-proxy-version.sh`, and
`tools/build-proxy-wheel.sh`). Ground your answers in what
the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question
touches on external concepts (PEP 440 version semantics,
pip pre-release resolution, maturin version handling, PyPI
trusted publishing, Kolla image builds), research as needed
to give a confident answer. Flag any uncertainty explicitly
rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the overall proxy
architecture and `AGENTS.md` for build commands, project
conventions, and code organisation. Key cross-repo
references for this plan:

- `shakenfist/kerbside-patches` — carries
  `_patches/patch175-kolla-master-install-proxy-wheel.patch`,
  the downstream image-build workaround this plan partially
  obsoletes
- `openstack/kolla` — `kolla/common/sources.py` pins
  `kerbside-base` to this repo's develop branch tip and the
  merged kerbside-base Dockerfile does `pip install
  /kerbside`; the whole point of this plan is that upstream
  kolla needs NO further changes
- `openstack/kolla-ansible` — Gerrit changes 988189, 988913
  and 989614 run the (currently non-voting, currently red)
  kerbside scenario jobs that will prove this plan worked

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be
named for the master plan, in the same directory as the
master plan, and simply have `-phase-NN-descriptive`
appended before the `.md` file extension. Tracking of these
sub-phases should be done via a table like this in this
master plan under the Execution section.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The rust-proxy master plan (complete) split the SPICE data
path into a Rust binary (`rust/kerbside-proxy/`) shipped as
a separate maturin bin wheel, `kerbside-proxy`, released in
lockstep with the pure-Python `kerbside` package from the
same `v*` tag. The lockstep pairing is enforced at release
time only: `tools/stamp-proxy-version.sh` inserts an exact
`kerbside-proxy==X.Y.Z` pin into `pyproject.toml`
immediately before the `# KERBSIDE_PROXY_PIN` marker, and
stamps the same version into the crate's Cargo.toml. By
deliberate policy the committed tree carries NO pin at all
(the marker line is a comment), so that dev and CI installs
do not require the sibling package to exist on PyPI; a dev
checkout is expected to resolve the binary from the Rust
build tree or `KERBSIDE_PROXY_BIN` via
`proxy_supervisor.py::find_proxy_bin()`.

This leaves a gap between the two supported install modes.
A consumer that installs kerbside *from git* but does not
build the Rust tree gets a daemon that cannot start:
`find_proxy_bin()` exhausts its search paths and raises
RuntimeError. The most important such consumer is the
upstream Kolla image build: `kolla/common/sources.py` (on
kolla master) fetches this repo at the develop branch tip
and the kerbside-base Dockerfile runs `pip install
/kerbside`. Since "Phase 5: supervise the Rust proxy from
the daemon" merged on 2026-07-07, every kerbside scenario
job on the upstream kolla-ansible Gerrit series (988189,
988913, 989614) has failed the same way: the
`kerbside_proxy` container exits ~2 seconds after start and
`kolla-ansible check` reports the container missing. The
jobs are non-voting, so this went unnoticed until
2026-08-13.

The downstream `shakenfist/kerbside-patches` repo has a
partial workaround
(`patch175-kolla-master-install-proxy-wheel.patch`: install
a local wheel from `/kerbside/proxy-wheels/` if present,
else `pip install kerbside-proxy`), but that patch only
affects downstream image builds — upstream Zuul builds
images from upstream kolla plus the Gerrit Depends-On
chain and never see `_patches/`. Landing an equivalent
change in upstream kolla takes weeks per patch, and its
unpinned PyPI fallback would reintroduce version skew
(latest *released* binary against develop-tip Python).
Also note pip will not select dev releases for a bare
`kerbside-proxy` requirement, so that fallback can never
serve unreleased contract changes.

The fix that requires no new upstream kolla code: publish
rolling PEP 440 dev releases of the `kerbside-proxy` wheel
to PyPI, and commit a dev-inclusive version specifier to
`pyproject.toml` so that any `pip install <checkout>`
transitively resolves the newest proxy wheel. Because a
specifier that itself names a dev version opts that one
requirement into pre-releases, no consumer needs `--pre`
and the already-merged upstream Dockerfile works unchanged.

Two refinements agreed during design discussion:

1. **Publish only when the binary's inputs change.** Most
   develop merges touch only Python; republishing an
   identical binary is waste. The publish workflow is
   path-filtered. Critically, the proto that defines the
   gRPC contract lives in the *Python* tree
   (`kerbside/rpc/kerbside.proto`) and the Rust stubs are
   generated at build time by `build.rs` into `OUT_DIR` —
   nothing committed under `rust/` changes when the
   contract changes — so the filter must include the proto
   explicitly. `.github/workflows/rust.yml` already models
   the correct path set.
2. **A contract handshake as backstop.** With sparse
   publishing, version *inequality* between the Python
   package and the installed binary is the normal,
   correct state, so the guard must check contract
   compatibility, not version equality. Both sides can
   embed a hash of `kerbside.proto` at build/generation
   time; the supervisor compares them before launching the
   binary. This also catches any future input the path
   filter fails to anticipate.

Relevant release machinery facts, verified 2026-08-14:

- `kerbside` versions come from setuptools_scm (git tags,
  `write_to = kerbside/_version.py`). On develop between
  tags it derives `X.Y.Z.devN+g<sha>` where N counts
  commits since the last tag (monotonic per branch).
- The proxy wheel's version comes from Cargo.toml
  `[package] version` via maturin `dynamic = ["version"]`;
  the release stamps it from the tag. The committed value
  is a placeholder (`0.1.0`). Cargo versions must be
  semver, so a PEP 440 dev version cannot be stamped there
  directly; the dev workflow needs either maturin's
  semver→PEP 440 pre-release translation (e.g.
  `X.Y.Z-dev.N`) or a static `version =` in the crate's
  pyproject.toml. Phase 1 must verify which path maturin
  supports cleanly.
- `tools/build-proxy-wheel.sh` builds manylinux_2_28
  wheels for x86_64 and aarch64 (zig cross) and is
  reusable outside the release workflow. Bin wheels are
  `py3`-tagged, so one wheel covers all Python versions.
- Release publishing uses PyPI trusted publishers gated by
  the `release` GitHub environment
  (`.github/workflows/release.yml`, RELEASE-SETUP.md). A
  new workflow file publishing dev wheels needs its own
  trusted-publisher registration on the `kerbside-proxy`
  PyPI project, and must NOT be gated by the
  approval-required `release` environment or the
  automation is defeated.
- `tools/stamp-proxy-version.sh` currently inserts the pin
  when only the marker exists, or replaces an existing
  `kerbside-proxy==` pin. Once a committed `>=` specifier
  exists it must learn to *replace* that line with the
  exact pin at release time. Its "COMMITTED-PIN POLICY"
  comment block (and the matching comment in
  pyproject.toml) documents the old policy and must be
  rewritten.
- The `kerbside` wheel does not currently ship the
  `.proto` file (`[tool.setuptools] packages` only), so
  the handshake should compare hashes embedded at
  generation time (`tools/gen-protos.sh` writing a
  committed constant) rather than hashing the proto file
  at runtime.

## Mission and problem statement

Make `pip install` of an unreleased kerbside checkout
produce a working deployment with zero manual intervention
and zero further upstream kolla changes, by:

1. publishing rolling PEP 440 dev releases of the
   `kerbside-proxy` wheel to PyPI whenever (and only when)
   the binary's inputs change;
2. committing a dev-inclusive `kerbside-proxy` version
   specifier so plain pip installs resolve those wheels,
   while the release process continues to stamp exact
   lockstep pins into released artifacts; and
3. adding a proto-hash contract handshake between the
   daemon and the binary so that any skew the sparse
   publishing scheme lets through is detected loudly at
   startup instead of failing subtly at runtime.

Success is externally observable: the upstream
kolla-ansible kerbside scenario jobs (non-voting, red since
2026-07-07) turn green without any new kolla or
kolla-ansible code beyond what is already in flight.

## Open questions

All resolved by the operator on 2026-08-14:

1. **Refuse or warn on contract-hash mismatch?**
   Decision: **refuse to launch**, with a helpful debug
   message — name both hashes, the binary path and
   version, and the remediation options (upgrade the
   wheel; rebuild the local Rust tree; set
   `KERBSIDE_PROXY_BIN`; or set the
   `KERBSIDE_SKIP_CONTRACT_CHECK` escape hatch for
   debugging). Fail-fast matches how a missing binary
   fails today, and a clear refusal message helps
   deployers generally, not just this CI path.
2. **Dev version scheme.** Decision: as recommended —
   derive from setuptools_scm and strip the local segment
   (`0.4.1.dev62+g<sha>` → `0.4.1.dev62`), so dev versions
   are monotonic within a release cycle and sort below the
   next release. Confirm in phase 1 that maturin emits
   exactly this version for the wheel (via Cargo semver
   pre-release translation or a static pyproject version —
   whichever proves clean).
3. **Committed specifier form.** Decision:
   `kerbside-proxy>=0.4.0.dev0` — floored at the current
   lockstep release (v0.4.0 at planning time) rather than
   the fully-open `>=0.0.0.dev0`. Note the `.dev0`
   component is functionally required, not decorative: pip
   only considers pre-releases for a requirement whose
   specifier itself names one, so a plain `>=0.4.0` would
   silently ignore every dev wheel. `X.Y.Z.dev0` sorts
   immediately below `X.Y.Z`, so the floor still admits
   the 0.4.0 release itself. The floor may be bumped
   opportunistically at later releases but requires no
   maintenance to keep working (the stamp script replaces
   the whole line at release time anyway).
4. **Unattended publishing acceptable?** Decision: yes,
   with trust boundaries made explicit. Dev wheels publish
   through a separate GitHub environment (`dev-release`)
   with no required reviewers, scoping its own PyPI
   trusted-publisher registration — the approval-gated
   `release` environment remains exclusive to real
   releases. Dev wheels still get build provenance
   attestations (`actions/attest-build-provenance` is
   automatic and needs no approval), so consumers can
   verify origin; what they do not get is the Sigstore
   tag-signing step, which is meaningless here since no
   tag exists. Their lower trust level is carried by the
   version scheme itself (PEP 440 dev releases are
   explicitly pre-release artifacts).
5. **Both architectures?** Decision: yes — Kolla CI needs
   both x86_64 and aarch64, so both matrix legs are
   required, not optional.
6. **Pruning old dev releases.** Decision: automate it as
   a phase of this plan (phase 5) rather than deferring —
   an unautomated pruning chore would be forgotten until
   something breaks.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Dev wheel publish workflow | [PLAN-proxy-dev-releases-phase-01-publish-workflow.md](/components/kerbside/plans/PLAN-proxy-dev-releases-phase-01-publish-workflow/) | Complete (merged in PR #314, 2026-08-16) |
| 2. Committed dev specifier and release stamping | [PLAN-proxy-dev-releases-phase-02-dev-specifier.md](/components/kerbside/plans/PLAN-proxy-dev-releases-phase-02-dev-specifier/) | Complete (merged in PR #314, 2026-08-16) |
| 3. Contract handshake | [PLAN-proxy-dev-releases-phase-03-contract-handshake.md](/components/kerbside/plans/PLAN-proxy-dev-releases-phase-03-contract-handshake/) | Complete (merged in PR #314, 2026-08-16) |
| 4. Docs, downstream cleanup and verification | [PLAN-proxy-dev-releases-phase-04-docs-and-downstream.md](/components/kerbside/plans/PLAN-proxy-dev-releases-phase-04-docs-and-downstream/) | Docs (4a) complete in PR #314. Post-merge tail outstanding: patch175 simplification (4b) is committed in a kerbside-patches worktree awaiting its own PR; the Gerrit recheck (4c) has not run |
| 5. Automated dev release pruning | [PLAN-proxy-dev-releases-phase-05-pypi-prune.md](/components/kerbside/plans/PLAN-proxy-dev-releases-phase-05-pypi-prune/) | Implemented (on branch) — storage monitor, lockfile-only merges no longer publish, pruning runbook |

Phase sketches (to be expanded into per-phase plans):

**Phase 1 — dev wheel publish workflow.** New
`.github/workflows/dev-proxy-wheel.yml`: on push to
develop, path-filtered to `rust/**`,
`kerbside/rpc/kerbside.proto`,
`tools/build-proxy-wheel.sh` and the workflow file itself,
plus `workflow_dispatch` as a force-publish escape hatch
(which also serves the bootstrap publish). Stamp a dev
version (open question 2), build both arches with
`tools/build-proxy-wheel.sh`, publish with
`pypa/gh-action-pypi-publish` and `skip-existing: true`.
One-time setup documented in RELEASE-SETUP.md: create the
`dev-release` GitHub environment (no required reviewers)
and register the workflow + environment as a trusted
publisher on the `kerbside-proxy` PyPI project. Dev wheels
get build provenance attestations
(`actions/attest-build-provenance`, automatic); the
Sigstore tag-signing step is NOT replicated (no tag
exists for a dev build) — note both decisions in the
workflow header comment.

**Phase 2 — committed dev specifier and release
stamping.** Replace the comment-only `# KERBSIDE_PROXY_PIN`
policy: commit `"kerbside-proxy>=0.4.0.dev0",` (open
question 3) above the marker in `pyproject.toml`, rewrite
the policy comment blocks in `pyproject.toml` and
`tools/stamp-proxy-version.sh`, and teach the stamp script
to replace the `>=` specifier with the exact `==X.Y.Z` pin
at release time (it already handles the replace-existing-
pin case; the insert-at-marker branch becomes a fallback).
Verify the pin-indirect-dependencies workflow is
indifferent to the new line. Once this phase merges, a
plain `pip install` of a checkout resolves the newest
proxy wheel from PyPI — this is the phase that turns the
upstream scenario jobs green. (Sequencing note, corrected
2026-08-14 during phase 2 planning: the phases all land in
ONE PR when the master plan completes, per the operator's
CI-cost policy — not one PR per phase as originally
sketched. The bootstrap dispatch runs immediately after
that merge; in the window before it completes, fresh git
installs resolve the 0.4.0 release wheel, and the phase 3
contract handshake is what makes any resulting skew a loud
startup refusal rather than a subtle failure.)

**Phase 3 — contract handshake.** `tools/gen-protos.sh`
additionally writes the sha256 of `kerbside.proto` to a
committed constant (e.g. `kerbside/rpc/contract.py`);
`build.rs` embeds the same hash into the binary
(`env!`-style); the binary gains a `--contract-hash`
print-and-exit flag; `proxy_supervisor.launch_rust_proxy()`
invokes it before launch and refuses/warns (open question
1) on mismatch. A CI or gen-protos check asserts the
committed constant matches the proto so the hash cannot go
stale. Unit tests on both sides.

**Phase 4 — docs, downstream cleanup and verification.**
Update AGENTS.md / ARCHITECTURE.md / docs for the new
release semantics ("how do unreleased installs get a
proxy binary"). In `shakenfist/kerbside-patches`, simplify
patch175: the PyPI fallback branch becomes redundant once
phase 2 MERGES and the bootstrap wheel is published (the
plain install resolves the dev wheel) — simplifying earlier
would leave master images with no proxy at all, so that
step is gated on merge + bootstrap (timing correction,
2026-08-15 during phase 4 planning) — while the
`/kerbside/proxy-wheels/` local-override branch remains
useful and keeps its skew-safety role. Confirm upstream
kolla needs no change.
Verification: after the next develop merge that triggers a
publish, recheck one of the Gerrit changes and confirm the
kerbside scenario jobs go green (the ubuntu-noble-upgrade
cirros flake, issue #293, is unrelated and may still need
a recheck).

**Phase 5 — automated dev release pruning.** A scheduled
workflow that keeps the dev release set bounded: retain
the newest K dev releases (and never touch final
releases), remove the rest. Honest caveat for the phase
plan to resolve: PyPI's Warehouse has NO official API for
deleting or yanking releases — upload tokens cannot do it.
Known options, in rough order of preference: (a) the
`pypi-cleanup` tool, which automates a form login with a
bot/machine account and TOTP secret stored as repository
secrets; (b) if automation proves too fragile or
policy-risky, degrade gracefully to a monitoring workflow
that checks the project's remaining PyPI quota / dev
release count and files a GitHub issue when a threshold is
crossed, making the manual chore impossible to forget.
The phase plan should research (a) properly — including
whether a second PyPI account with maintainer rights on
kerbside-proxy is acceptable — and pick.

(Volume correction, 2026-08-17 during phase 5 planning:
this sketch assumed publishing would be sparse. Measured
against the merged workflow's actual path filter, 42 of
the 217 first-parent develop merges in the 42 days since
the Rust tree was created would have triggered a publish —
about 30/month, or 365/year at 5.80 MB per publish, which
is ~2.1 GB/year against PyPI's 10 GB default project
limit: roughly 4.7 years of headroom, rather than the
open-ended "years" assumed here. 32 of those 42 touch only
`rust/kerbside-proxy/Cargo.lock` and/or `Cargo.toml` —
Renovate dependency bumps, none of which can change the
gRPC contract hash. Reducing that inflow is therefore a
lever the phase plan must weigh alongside deletion, and
it interacts with the success criterion below that every
`rust/**` merge publishes.)

(Sequencing note: phases 1-4a landed together in PR #314
under the operator's CI-cost policy. Phase 5 lands as its
own, separate PR — the single-PR batching applied to the
phases that had to merge together to turn the scenario
jobs green, and phase 5 depends on none of that.)

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean and
avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort
   level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it) or the
   model was too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied
   with the result.

This applies to all steps, including high-effort ones. If a
sub-agent can't succeed even with a detailed brief and the
right model, that's a signal the brief needs improving, not
that the management session should do the implementation
itself.

Use `isolation: "worktree"` for sub-agents when the change
is risky or experimental. The worktree is discarded if the
output is unsatisfactory. For safe, well-understood
changes, sub-agents can work directly in the main tree.

### Planning effort

The master plan itself was created at high effort. Phase 1
should be planned at high effort (PyPI trusted publishing,
maturin version semantics, and workflow security posture
all involve judgment calls and external research). Phase 2
is mechanical once phase 1 settles the version scheme and
can be planned at medium effort. Phase 3 should be planned
at high effort (it touches the gRPC contract convention,
build.rs, clap args, and the supervisor launch path across
two languages). Phase 4 is medium effort and partly
cross-repo. Phase 5 should be planned at medium effort but
with its research step (PyPI deletion automation options
and their account/security implications) done carefully
before any implementation.

### Step-level guidance

Each phase plan should include the step table described in
PLAN-TEMPLATE.md (step, effort, model, isolation, brief),
with briefs written so a colleague who has never seen the
codebase could execute them. Front-load the research from
this master plan into the briefs — e.g. phase 1's brief
should state outright that Cargo versions are semver-only
and that the maturin translation must be verified, rather
than leaving the implementing agent to rediscover it.

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code passes `tox -eflake8` and `tox -epy3`; for
      Rust changes, `cargo fmt --check`, `cargo clippy`
      and `cargo test` in `rust/kerbside-proxy/`.
- [ ] Workflow changes pass actionlint.
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] Commit message follows project conventions
      (including the `Co-Authored-By` line with model,
      context window, effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be true:

* A merge to develop that touches `rust/**` or
  `kerbside/rpc/kerbside.proto` publishes a
  `kerbside-proxy` dev wheel to PyPI within one workflow
  run, and a Python-only merge publishes nothing.
  Phase 5 deliberately narrowed this: a merge touching
  only `rust/kerbside-proxy/Cargo.lock` also publishes
  nothing, since a lockfile-only bump cannot change the
  proto, the contract hash, or the binary's interface.
* `pip install .` from a clean develop checkout on a
  machine with no Rust toolchain yields a `kerbside daemon
  run` that launches the proxy binary successfully.
* `tools/stamp-proxy-version.sh X.Y.Z` still produces a
  pyproject.toml whose only kerbside-proxy requirement is
  `kerbside-proxy==X.Y.Z`, and the release workflow is
  otherwise unchanged.
* The daemon refuses to launch the proxy when the
  binary's embedded contract hash does not match the
  Python side's committed constant, with a debug message
  naming both hashes, the binary path and version, and
  the remediation options (including
  `KERBSIDE_SKIP_CONTRACT_CHECK`).
* The upstream kolla-ansible kerbside scenario jobs pass
  on a recheck without new kolla/kolla-ansible code.
* The code passes `tox -eflake8` and `tox -epy3`; the
  crate passes fmt/clippy/test; lines wrap at 120
  characters; Python strings use single quotes except
  docstrings.
* `README.md`, `ARCHITECTURE.md`, `AGENTS.md` and `docs/`
  reflect the new dev-release semantics, and
  RELEASE-SETUP.md documents the one-time trusted
  publisher registration.
* Dev releases on PyPI are automatically bounded (or, if
  automation proves unsafe, a monitoring workflow files an
  issue before quota becomes a problem) — no unautomated
  recurring chore remains.
* `docs/plans/index.md` has a row for this plan, updated
  as phases complete.

### Future work

* Consider whether the downstream kerbside-patches
  `/kerbside/proxy-wheels/` override should grow a
  SHA-match assertion against the source tree it is
  installed beside (the "fail loudly on stale wheel"
  refinement from the design discussion).
* The tree-SHA publish guard (`git rev-parse
  HEAD:rust/kerbside-proxy` compared against the last
  published wheel) as a more robust alternative to path
  filters, if the path list ever bites us.
* A `sys_platform == 'linux'` environment marker on the
  committed kerbside-proxy floor would restore `pip install
  kerbside` on macOS/musl hosts (no proxy wheels exist
  there), at the cost of teaching both stamp scripts to
  preserve the marker when rewriting the line. Raised by
  review on PR #314; declined there because phase 2
  accepted the platform trade deliberately and no
  contributor currently needs it.
* Revisit automated dev-release pruning if PyPI ever ships a
  management API. Warehouse issue #12810 ("Warehouse API to
  delete old .dev wheels (nightly builds)") is precisely
  this use case and was open and labelled "Blocked" when
  phase 5 was planned on 2026-08-17; phase 5 declined to
  automate deletion around that gap because the only
  available mechanism drives PyPI's web login form with an
  account password and TOTP seed. If #12810 lands, the
  monitor phase 5 builds becomes the trigger for a real
  pruning job.
* Upstream kolla: consider eventually switching
  `kerbside-base` from git-develop to released tarballs,
  which would make image builds reproducible and reduce
  the dev-wheel dependency to CI-of-kerbside only.

### Bugs fixed during this work

* Dev wheels built outside the release lane carried the
  placeholder version 0.1.0, which could not satisfy the
  committed floor; a joint `pip install` failed with
  ResolutionImpossible. `tools/build-proxy-wheel.sh` now
  dev-stamps an unstamped tree itself. Found by the sf-e2e
  lane on PR #314.
* That auto-stamp then fired on release-stamped trees too,
  because `tools/stamp-proxy-version.sh` never removed the
  crate pyproject's `dynamic = ["version"]`. Left alone it
  would have failed the release lane or, worse, published a
  release wheel carrying a dev version. The release stamper
  now writes a static version, so tree state distinguishes
  the two modes, and `tools/verify-wheel-stamping.sh` is a
  CI guard so the class is caught on PRs rather than at tag
  time. Found by review on PR #314.
* `tools/verify-wheel-stamping.sh` armed its restore trap
  before its clean-tree check, so a dirty tree would have
  had uncommitted work discarded by the trap's `git
  checkout`. It also omitted `Cargo.lock` from the files it
  restored, which a container run caught.
* `grep -c` under `set -e` exits non-zero on no matches and
  silently killed the enclosing script; fixed in the stamp
  scripts and then again in the new guard.
* `tools/check-pypi-storage.py` (phase 5) first used exit 1
  for both "threshold crossed" and "the check could not
  run", so a transient network failure would have filed a
  threshold issue carrying an empty report. The status is
  now three-way.
* The phase 5 survey mis-measured how often the publish
  workflow fires, by passing both `-m` and `--first-parent`
  to `git diff-tree`, which counts each merge's diff against
  its second parent too. Corrected to 42 triggering merges
  in 42 days rather than 77, and the corrected method was
  checked against the workflow's observed runs.

Known related issues at planning time:

* kerbside#293 — merge CI tempest cirros download flake;
  unrelated to this plan but co-occurs on the same Gerrit
  changes and confuses verification (a red
  ubuntu-noble-upgrade job is NOT a kerbside failure).
* No existing kerbside issue tracks the missing-binary
  image-build failure itself; this plan is its resolution.
  Consider filing one for cross-reference when the first
  phase PR opens.

### Documentation index maintenance

When creating a new master plan from this template, update
`docs/plans/index.md` — done for this plan (see the Master
plans table). When all phases of a plan are complete,
update the status column in `index.md` to *Complete*.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
