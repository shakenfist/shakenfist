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
| 4. Docs, downstream cleanup and verification | [PLAN-proxy-dev-releases-phase-04-docs-and-downstream.md](/components/kerbside/plans/PLAN-proxy-dev-releases-phase-04-docs-and-downstream/) | Complete (4a docs in PR #314; 4b withdrawn 2026-08-18 after measurement, not delivered; 4c Gerrit recheck green 2026-08-29) |
| 5. Automated dev release pruning | [PLAN-proxy-dev-releases-phase-05-pypi-prune.md](/components/kerbside/plans/PLAN-proxy-dev-releases-phase-05-pypi-prune/) | Complete (merged in PR #328, 2026-08-18) — storage monitor, lockfile-only merges no longer publish, pruning runbook |
| 6. Push audit | [PLAN-proxy-dev-releases-phase-06-push-audit.md](/components/kerbside/plans/PLAN-proxy-dev-releases-phase-06-push-audit/) | Complete |

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

(Decision reversed, 2026-08-18: patch175 keeps its PyPI
fallback branch and the downstream patch is not changed. The
premise for deleting it — that the fallback silently masks
staleness — does not survive measurement. Kolla's
`install_pip` macro (`kolla/docker/macros.j2:35-43`) always
passes `--upgrade`, and the fallback runs *after* the
`pip install /kerbside` that resolves the committed floor, so
by the time it runs a newer dev wheel is already installed.
Measured against live PyPI with pip 26.2.1: with
`kerbside-proxy 0.5.1.dev1` installed, `pip install --upgrade
kerbside-proxy` reports "Requirement already satisfied" and
installs nothing — it neither downgrades to the newest final
release nor masks anything. The fallback can only execute
meaningfully on a path where the floor install already failed,
which fails the image build first, and phase 3's contract
handshake turns any surviving skew into a loud startup
refusal. Against that, deleting it costs a downstream patch
edit plus a wave-8 Gerrit repush and rebase churn, and risks
leaving master images with no proxy at all if any part of the
floor assumption is wrong. The 4b commit this plan previously
recorded as "awaiting its own PR" no longer exists in any
kerbside-patches worktree or branch, so nothing is stranded by
withdrawing the step.)

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

**Phase 6 — push audit.** Work through `PUSH-AUDIT.md`
against the accumulated diff of every phase in this plan —
phases 1 to 5 together, not phase 5 alone, because what
the phases did to each other is only visible once they are
all in the same diff.

Every judgment brief in `PUSH-AUDIT.md` says to read
`git diff develop...HEAD`. That is wrong here and is the
executor's first correction: phases 1 to 5 are already
merged into `develop` (PR #314 as `14b54f3`, PR #328 as
`2e1fd43`), so on a phase 6 branch that range holds this
phase's own paperwork and none of the work being audited.
Unrelated work merged between the two, so the range has to
be scoped to the paths the phases touched:

```
paths=$( { git diff --name-only 14b54f3^1..14b54f3
           git diff --name-only 2e1fd43^1..2e1fd43
         } | sort -u )
git diff 14b54f3^1..2e1fd43 -- $paths
```

That is 40 files and roughly 4,000 added lines as of
2026-08-24. Derive the path set rather than transcribing
it, so a rebase or a later amendment cannot silently
narrow the audit. Substitute this range wherever
`PUSH-AUDIT.md` says `git diff develop...HEAD`, in all
five judgment briefs. Wave 1's lint and test gates run
against the worktree and are unaffected, but every style
and report grep in *both* audit scripts is diff-based
against a hard-coded `DIFF_BASE=develop` --
`tools/audit/wave1.sh:37` and, as the phase 6 survey
found, `tools/audit/wave2-mechanical.sh:18` as well, where
all eight reports are built from it. Run unmodified on a
phase 6 branch, wave 2's script prints "(none)" eight
times and exits 0, which reads as a clean bill of health
and is an empty diff. This sketch previously named only
`wave1.sh` and suggested editing `DIFF_BASE` locally
without committing it; the phase plan rejects that (it
cannot express the path scoping) and teaches both scripts
to read an `AUDIT_RANGE` / `AUDIT_PATHS` pair from the
environment instead, derived by a new
`tools/audit/plan-range.sh`.

The Rust half of the range is not mechanically checked at
all: both audit scripts are Python-only, and the range
touches `rust/kerbside-proxy/build.rs` and
`src/main.rs`. The phase runs
`make -C rust/kerbside-proxy lint test` alongside wave 1.

Scope note: 4b is withdrawn and 4c ships no diff in this
repository, so the audit covers phases 1 to 3, 4a and 5.
(4c completed on 2026-08-29, after the audit ran; it
changed nothing here.)

Findings land as their own pull request; this plan is not
complete until each one is fixed or declined in writing
here, with the reason. If the audit finds nothing, record
that in a sentence — it is a result worth having.

**Phase 6 result.** The audit ran on 2026-08-29 over
`14b54f3^1..2e1fd43`, scoped to the 40 paths phases 1 to
3, 4a and 5 touched: wave 1 mechanical and style, the Rust
lint and test gates, wave 2 mechanical, and the four
judgment agents (2a code quality, 2b tests, 2c
documentation, 2d security). It found **nothing critical,
high or blocking.** Everything it did find was a cheap
correctness or honesty improvement; all of it is fixed in
this phase's pull request, and everything not fixed is
declined below with its reason.

Fixed, each in its own commit:
* `get_binary_contract_hash()` decoded the binary's stdout
  with the strict UTF-8 handler while catching only
  `TimeoutExpired` and `OSError`, so a binary printing
  non-UTF-8 raised `UnicodeDecodeError` out of a function
  documented to return a failure reason for *every*
  failure — the operator would have seen a traceback at
  daemon startup instead of the actionable `RuntimeError`
  `check_contract()` builds. Now decoded with
  `errors='replace'`, so the mangled text falls into the
  digest check that already exists, with a regression test
  that runs a real executable printing real undecodable
  bytes. *Survive a proxy binary that prints non-UTF-8.*
* Three scripts decide whether the crate's
  `pyproject.toml` is still unstamped by testing the same
  literal `dynamic = ["version"]` line in the same file,
  and `tools/build-proxy-wheel.sh` tested it with no end
  anchor while both stampers anchored it at both ends. A
  trailing comment on that line would have made the three
  disagree about the state of the tree. End-anchored, with
  a comment saying the anchoring is deliberate and naming
  its two siblings. *Make the three stamped-tree checks
  agree.*
* `tools/file-pypi-storage-issue.sh` interpolated the
  issue title into the jq program that dedupes on it. Both
  call sites pass literals so nothing was exploitable, but
  it is a trap for the next caller with a computed title;
  the comparison is now made in bash and jq only names
  fields. *Pass the issue title to jq as data.*
* `docs/development.md` said the daemon "verifies" the
  binary's contract hash — the only security-flavoured
  word about the handshake anywhere in the shipped
  documentation. It now compares, and
  `docs/proxy-architecture.md` says explicitly that the
  handshake detects accidental build skew and detects
  nothing whatever about a substituted or tampered binary.
  *Say what the contract handshake does not do.*
* `docs/installation.md` never said that the dev wheel a
  git install resolves is published unattended with no
  per-commit human review behind it, nor that production
  should install a tagged `kerbside==X.Y.Z` and get the
  exact pin; `RELEASE-SETUP.md` described build provenance
  attestations as something consumers of dev wheels rely
  on when in fact no install path in this repository, in
  `docs/installation.md` or in the Kolla patch verifies
  them. Both now say so, and `RELEASE-SETUP.md` also
  records that the response to a suspected compromise is
  the same manual PyPI yank as pruning, since there is no
  delete or yank API. The same commit rewords that file's
  "the phase 5 decisions in ..." pointer, which read as
  though the runbook itself were phased. *Be plain about
  what installs an unreviewed wheel.*

Declined, with the reason:
* A non-docstring string using double quotes at
  `kerbside/proxy_supervisor.py:212`. It contains `'1',
  'true', 'yes', 'on'`, so double-quoting avoids escaping
  four apostrophes. Defensible as written.
* `kerbside/tests/unit/test_proxy_floor.py` subclasses
  `unittest.TestCase` where its three sibling new modules
  use `testtools.TestCase`. Cosmetic: it runs correctly
  under stestr, and two pre-existing test modules do the
  same.
* `KERBSIDE_SKIP_CONTRACT_CHECK` is read through
  `os.environ` rather than `kerbside/config.py`. It
  exactly mirrors the same file's existing precedent,
  `KERBSIDE_PROXY_BIN` at `proxy_supervisor.py:61`: both
  are supervisor bootstrap escape hatches, not runtime
  application settings, and neither belongs in the
  deployment's configuration surface.
* Four advisory test gaps from the 2b review — no
  coverage of `tools/file-pypi-storage-issue.sh`, no
  end-to-end test that the real built binary's
  `--contract-hash` stdout is what the supervisor parses,
  no direct test of `format_bytes`, and no test pinning
  the hash regex against a valid hash followed by trailing
  output. None describes a defect that exists today; all
  four are recorded as future work.
* The duplicated `setuptools_scm` version derivation
  between the two stamp scripts, and the wider
  shared-helper extraction across the four version
  scripts. The duplication is real, but extracting it is a
  refactor of the very code under audit and a larger
  change than an audit should land. Recorded as future
  work; the minimal correctness half of it is the
  end-anchor fix above.
* L1, third-party actions pinned to mutable refs
  (`dtolnay/rust-toolchain@stable`,
  `pypa/gh-action-pypi-publish@release/v1`). This matches
  the repository-wide pre-existing pattern in
  `release.yml` and `rust.yml`; fixing it properly means
  digest-pinning every third-party action across the
  repository with Renovate keeping the digests current,
  which is a repository-wide change rather than a finding
  against this range.
* L3, escaping the markdown fence in the issue body built
  by `tools/file-pypi-storage-issue.sh`. The shell quoting
  is correct and PyPI normalises version strings to PEP
  440, so a backtick cannot reach the fenced block; the
  payoff would be a malformed GitHub issue.
* M1, the unpinned pre-release floor. This is the single
  deliberate hole in an otherwise exactly pinned tree, and
  no fix preserves the feature: a floor cannot be
  hash-pinned, which is the whole point of having one. The
  risk was analysed and accepted in writing when phase 2
  committed it. The documentation half of the finding is
  closed by the `docs/installation.md` and
  `RELEASE-SETUP.md` edits above.
* M2, the unattended OIDC publish job sharing a persistent
  self-hosted runner pool with jobs that execute
  pull-request code. Filed as kerbside#374 rather than
  fixed. It is pre-existing — `release.yml` already
  publishes with `id-token: write` on the same pool — but
  the dev lane widens the exposure window substantially,
  because it fires unattended on every qualifying merge
  instead of a handful of human-initiated tags a year. The
  fix is ephemeral runners for every job holding
  `id-token: write`, which is runner-fleet infrastructure
  and not a change to this repository.

Verified rather than found:
* M3, the `dev-release` environment's deployment-branch
  policy, could not be checked from inside the repository.
  The management session checked it with `gh api`: the
  environment carries a custom branch policy containing
  exactly `develop`. The control is in place.
* The contract handshake cannot go stale on either side.
  `kerbside/tests/unit/test_contract.py:19-29` re-reads
  `kerbside.proto`, recomputes its sha256 and compares it
  to the committed constant, and it runs under
  `tox -epy3`, a required check;
  `rust/kerbside-proxy/build.rs:53-60` re-derives the same
  hash from the proto at build time, so the Rust side
  cannot drift either. The handshake's one plausible
  failure mode is closed.
* The 2c plan-accuracy pass found no drift between the
  planning record and the code: the publish path filter,
  the version floor and the monitor's three thresholds all
  agree across the workflow, `pyproject.toml`,
  `tools/check-pypi-storage.py`, `RELEASE-SETUP.md`,
  `docs/proxy-architecture.md` and the phase plans.
* The contract-check escape hatch fails closed on every
  path, including unrecognised values, and logs loudly
  when it is set.
* No PyPI credential exists anywhere in the repository;
  `issues: write` is scoped to only the jobs that file
  issues; and both new workflows set `permissions: {}` at
  the top level, with neither carrying a `pull_request`
  trigger.

`PUSH-AUDIT.md`'s management session checklist, worked
through: wave 1 passed, including the Rust lint and test
gates step 6b ran alongside it; wave 2's findings are
reviewed and dispositioned above; there were no blocking
2a, 2b or 2c findings needing a re-verified fix; 2d raised
nothing critical or high; the branch carries one planning
commit, two audit-tooling commits and six audit-outcome
commits, with no fixups to squash, no stray files and no
build artefacts; and it is level with `develop` at
`fe4bebe`, so no rebase is needed before it is pushed.

One correction the audit made to its own planning: the
phase 6 plan's brief for step 6g asserted that phases 3
and 4 describe the contract hash both ways, as a security
control and as a compatibility check, and told the
security reviewer to treat that inconsistency as a
finding. The review checked and could not substantiate it
— both phase plans, this plan and the shipped
documentation are consistently about skew, and the only
security-flavoured word anywhere was the single "verifies"
that the documentation fix above removes. The claim was
overstated, and the phase plan now says so.

**This plan is complete.** Phase 4c — the upstream Gerrit
recheck of the kolla-ansible kerbside scenario jobs — was
the last thing outstanding, and the operator confirmed on
2026-08-29 that every Gerrit review is passing Zuul CI.
That was the plan's whole point: the scenario jobs that
were red because an unreleased kerbside install could not
resolve a proxy binary are now green, with no change
required in upstream kolla. With the push audit and its
fixes also done, nothing remains.

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
* patch175's upstream commit message (rewritten 2026-08-13)
  now frames the patch as an optional local override and no
  longer mentions the PyPI fallback branch the diff still
  carries. Worth aligning the message on the next wave-8
  repush; not worth a change of its own.
* Revisit deleting patch175's PyPI fallback if pip's
  `--upgrade` semantics for an already-installed pre-release
  ever change, or if kerbside-patches stops installing the
  kerbside source before the fallback runs — both are the
  measurements that make the fallback inert today.
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
* Extract the shared version derivation the four version
  scripts duplicate — `tools/stamp-proxy-version.sh`,
  `tools/stamp-dev-proxy-version.sh`,
  `tools/verify-wheel-stamping.sh` and
  `tools/build-proxy-wheel.sh` — into one helper they all
  call, in particular the `setuptools_scm` derivation the
  two stampers each carry and the "is this tree stamped"
  test three of them make separately. Raised by the phase
  6 audit, which landed only the minimal correctness half
  (end-anchoring the third test so all three agree)
  because a refactor of the code under audit is not an
  audit's job.
* Four advisory test gaps the phase 6 audit recorded: no
  coverage of `tools/file-pypi-storage-issue.sh` at all,
  no end-to-end test that the real built binary's
  `--contract-hash` stdout is what
  `get_binary_contract_hash()` parses, no direct test of
  `format_bytes` in `tools/check-pypi-storage.py`, and no
  test pinning the contract-hash regex against a valid
  hash followed by trailing output. None of the four
  describes a defect that exists today.
* Run every job holding `id-token: write` on an ephemeral
  runner rather than the shared persistent self-hosted
  pool, so a publish never shares a machine with
  previously executed pull-request code. Tracked as
  kerbside#374; raised by the phase 6 audit, which found
  it pre-existing (`release.yml` publishes the same way)
  but substantially widened by a dev lane that fires
  unattended on every qualifying merge.
* Digest-pin the third-party actions this repository uses
  from mutable refs (`dtolnay/rust-toolchain@stable`,
  `pypa/gh-action-pypi-publish@release/v1`), with Renovate
  keeping the digests current. Raised by the phase 6
  audit and declined there as a repository-wide change
  rather than a finding against one range.
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
