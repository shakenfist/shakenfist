# Proxy dev releases phase 4: docs, downstream cleanup, verification

Master plan: `PLAN-proxy-dev-releases.md`. This phase makes the
documentation tell the truth about the new release semantics
(phases 1-3 changed how unreleased installs get a proxy binary,
and the docs still describe the old world), simplifies the
downstream `kerbside-patches` patch175 that the new semantics
make partly redundant, and defines the upstream verification
that proves the plan's core premise: unmodified upstream kolla
master now builds working kerbside images.

Planning effort: medium (per the master plan — partly
cross-repo, but every judgement call is small).

## Scope

In scope:

* kerbside documentation corrections: `AGENTS.md`,
  `docs/installation.md`, `docs/proxy-architecture.md`, plus a
  sweep of `ARCHITECTURE.md`, `README.md` and
  `RELEASE-SETUP.md` for the same stale claims.
* First deployer-facing documentation of the phase 3 contract
  handshake (refusal behaviour, `--contract-hash`,
  `KERBSIDE_SKIP_CONTRACT_CHECK`) — currently documented
  nowhere outside code and plan files.
* In `shakenfist/kerbside-patches`: simplify
  `_patches/patch175-kolla-master-install-proxy-wheel.patch`
  (post-merge gated; see decision 4) and propose refreshing its
  Gerrit submission (wave-8).
* The upstream verification: a Gerrit `recheck` proving the
  kerbside scenario jobs go green from unmodified kolla master.
* Status flips for phase 3 in the Execution table and
  `index.md` (done in this planning commit, per convention).

Out of scope: phase 5 (pruning); the bootstrap dispatch itself
and the operator's one-time PyPI/environment setup (master
plan completion checklist); any new upstream kolla code (the
plan's core premise is that none is needed); the future-work
SHA-match assertion for the `/kerbside/proxy-wheels/` override
(stays in the master plan's Future work).

## What the survey found

Verified against both trees on 2026-08-15, after rebasing this
branch onto current origin/develop (31 commits; four
`docs/plans/index.md` conflicts union-resolved; develop's
cf6e3e8 regenerated the gRPC stubs and re-wrapped a proto
comment, and the committed contract constant still matches the
proto — `f06c4ef8…` both sides):

* **`AGENTS.md:119-127` contradicts itself.** The packaging
  paragraph still says "`kerbside` exact-pins `kerbside-proxy`"
  and "The committed tree carries no pin (the sibling is not on
  PyPI in a dev checkout…)" — false since phase 2 committed the
  `>=0.4.0.dev0` floor, and contradicting the dev-release-lane
  paragraph phase 1 added fifteen lines below (AGENTS.md:134+).
  Phase 2's decision 3 ("the old rationale must not survive
  anywhere") swept `pyproject.toml` and the stamp script but
  missed this file.
* **`docs/installation.md:14-19` and
  `docs/proxy-architecture.md:321-340`** carry the same stale
  claims: "exact-pins … at the same version", "the gRPC
  contract matches by construction", and (proxy-architecture)
  "`tools/stamp-proxy-version.sh` stamps that same version
  into … the `kerbside` dependency pin" — the script now
  REPLACES the committed floor. Neither page mentions dev
  wheels. `RELEASE-SETUP.md:66` has a milder form ("stamps the
  `==<version>` pin into the dependency list") worth aligning
  in the same pass. `ARCHITECTURE.md` describes the
  daemon/proxy split but makes no packaging claims — sweep
  only.
* **The contract handshake is undocumented.** `grep -rn
  'SKIP_CONTRACT\|contract-hash' docs/ README.md
  ARCHITECTURE.md AGENTS.md RELEASE-SETUP.md` (excluding
  docs/plans/) finds nothing. A deployer hitting the phase 3
  refusal has no page to land on.
* **patch175 is as the master plan sketched it**: the
  `kerbside-base` Dockerfile conditional installs
  `/kerbside/proxy-wheels/*.whl` if present, else bare
  `pip install kerbside-proxy`. Registered in `kolla/ORDER:3`
  and `kolla-wave-8/ORDER:1` (Gerrit wave, source kolla
  master, destination branch `kerbside-overridable-proxy-wheel`,
  Change-Id Iaa556368a1d995a6764bd8b47833cc9472b1bb5d).
* **The override branch is load-bearing beyond skew-safety**:
  kerbside's own `.github/workflows/functional-tests.yml`
  (~line 825) documents staging a PR-built wheel into the image
  build tree "so the kerbside-base Dockerfile installs it in
  preference to the released one (see the proxy-wheels
  conditional in kerbside-patches)". The override branch must
  survive, confirming the sketch.
* **The fallback branch is worse than redundant**: a bare
  `pip install kerbside-proxy` never opts into pre-releases, so
  it can never resolve a dev wheel. Post-merge it is a no-op
  (the floor already resolved a wheel); in any failure case it
  silently installs the latest *release*, masking staleness the
  phase 3 handshake would otherwise surface at a definite point.
* **Timing constraint the sketch did not state (corrected at
  source in this planning commit)**: simplifying patch175
  before the kerbside PR merges would break downstream master
  image builds — pre-merge develop has no committed floor, so
  with the fallback gone the image gets no proxy at all. The
  patch175 change is gated on the kerbside PR merging and the
  bootstrap publish completing.

## Decisions

1. **patch175 keeps only the local-override branch; the PyPI
   fallback `else` branch is deleted.** After this plan,
   `pip install /kerbside` resolves a proxy wheel by itself
   (the committed floor), so the fallback is a no-op on the
   happy path and a silent staleness-masker on every other
   path — the opposite of the loud-failure posture phase 3
   builds. The patch's commit message (and `.patch-message`)
   is rewritten to describe the override as the local
   development/skew-safety mechanism it actually is; the
   current message's claim that "the kerbside package depends
   on the kerbside-proxy package correctly" predates and is
   superseded by this plan.
2. **The wave-8 Gerrit change is refreshed, not abandoned**
   (recommendation; the operator drives all Gerrit pushes).
   Upstream kolla needs no change for green CI — that is the
   plan's premise — but the override mechanism is still worth
   landing upstream: it is how kerbside's functional CI
   exercises PR-built proxies inside kolla images, and landing
   it eventually lets kerbside-patches drop the local patch.
   The refreshed change carries the simplified, override-only
   diff. The arguable alternative is abandoning it as no
   longer necessary; the counter is that "not necessary for
   green CI" is not "not useful".
3. **Where the handshake gets documented**:
   `docs/proxy-architecture.md`'s "How the binary gets there:
   packaging" section is the authoritative home (it already
   owns the wheel/lockstep story and now gains the floor, the
   dev-wheel lane, and the handshake including
   `KERBSIDE_SKIP_CONTRACT_CHECK`); `docs/installation.md`
   gets a short deployer-facing note that the daemon verifies
   the binary's gRPC contract at startup and refuses on
   mismatch, linking to proxy-architecture rather than
   restating the mechanism. One page owns each fact; the
   others point at it.
4. **The phase splits into an in-branch half and a post-merge
   tail.** Step 4a (kerbside docs) lands on this branch now;
   step 4b (patch175, in a kerbside-patches worktree, its own
   PR) and step 4c (Gerrit recheck) can only run after the
   kerbside PR merges and the bootstrap dispatch completes.
   The phase is recorded as "Implemented (on branch)" when 4a
   is done, with 4b/4c tracked as this phase's post-merge tail
   alongside the master plan's completion checklist. This is
   the decision most likely to be argued with — the
   alternative is shrinking phase 4 to docs-only and moving
   4b/4c into the master plan checklist; keeping them here
   means the cross-repo work has a step table, briefs and a
   definition of done instead of a checklist bullet.

## Decision 1 reversed (2026-08-18)

**patch175 keeps its PyPI fallback branch; step 4b is
withdrawn and the downstream patch is not changed.**

Decision 1 rested on the fallback being a no-op on the happy
path and a "silent staleness-masker" everywhere else. The
second half does not survive measurement:

* Kolla's `install_pip` macro (`kolla/docker/macros.j2:35-43`)
  always emits `pip --no-cache-dir install --upgrade`, and the
  fallback runs *after* the `pip install /kerbside` that
  resolves the committed floor. So it always runs against an
  environment where a proxy wheel is already installed.
* Measured against live PyPI with pip 26.2.1 on 2026-08-18:
  with `kerbside-proxy 0.5.1.dev1` installed, `pip install
  --upgrade kerbside-proxy` reports "Requirement already
  satisfied" and installs nothing. It does not downgrade the
  dev wheel to the newest final release (0.5.0), which was the
  concrete harm the reversal check was looking for.
* The only path on which the fallback installs anything is one
  where the floor failed to resolve a wheel — and that failure
  aborts the image build before the fallback is reached.
* Any skew that does survive is already loud: phase 3's
  contract handshake refuses to launch a binary whose embedded
  proto hash differs from the package's.

Against a benefit that measurement puts at zero, deleting it
costs a downstream patch edit, a wave-8 Gerrit repush and the
rebase churn that follows, and carries a real tail risk: if
any part of the floor assumption is wrong for some build, the
image ends up with no proxy binary at all.

Consequences for the rest of this plan:

* Step 4b is withdrawn, not deferred. The commit the master
  plan recorded as "committed in a kerbside-patches worktree
  awaiting its own PR" no longer exists in any worktree or
  branch of that repo, so nothing is stranded.
* Decision 2 (refresh rather than abandon the wave-8 Gerrit
  change) stands, but the refreshed change carries the current
  diff rather than an override-only one. The patch's message,
  rewritten upstream on 2026-08-13, describes the override
  without mentioning the fallback it still contains; aligning
  the message is a nice-to-have for the next repush.
* Step 4c (the Gerrit recheck) is unaffected and still
  outstanding; the operator drives it.
* The Definition of done drops its patch175 clause
  accordingly.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Kerbside docs truth pass. (1) `AGENTS.md:117-127`: rewrite the packaging paragraph — the committed tree carries a dev-inclusive floor (`kerbside-proxy>=0.4.0.dev0`, see `pyproject.toml:20-35`) so git installs resolve the newest wheel (release or dev) from PyPI; `tools/stamp-proxy-version.sh` REPLACES the floor with the exact `==` pin at release; the daemon verifies the binary's contract hash at launch (`kerbside/proxy_supervisor.py:check_contract`). Keep it consistent with the dev-lane paragraph at AGENTS.md:134+ without duplicating it. (2) `docs/installation.md:14-26`: same correction, plus a deployer note: at startup the daemon runs `kerbside-proxy --contract-hash` and refuses to launch a binary whose gRPC contract differs (remediations in the error message; `KERBSIDE_SKIP_CONTRACT_CHECK=1` is the explicit un-supported escape hatch); link to proxy-architecture.md for detail. (3) `docs/proxy-architecture.md` "How the binary gets there: packaging" (~321-340): rewrite per decision 3 — floor semantics, the dev-proxy-wheel.yml lane (path-filtered pushes to develop), stamp-REPLACES-floor, and a new short subsection on the contract handshake (committed `kerbside/rpc/contract.py` constant generated by `tools/gen-protos.sh`; `build.rs` embeds the same sha256; `--contract-hash`; refusal in `launch_rust_proxy`; the escape hatch). (4) Sweep `ARCHITECTURE.md`, `README.md`, `RELEASE-SETUP.md`, `docs/index.md`, `docs/testing.md`, `docs/direct-qemu-harness.md`, `docs/spice/*.md` for 'exact-pin'/'carries no pin'/'matches by construction' claims and align (RELEASE-SETUP.md:66 says "stamps the pin into" — make it "replaces the committed floor with"). Falsifiable checks before finishing: `grep -rn 'carries no pin' . --exclude-dir=docs/plans --exclude-dir=.git` → zero hits; `grep -rln 'KERBSIDE_SKIP_CONTRACT_CHECK' docs/ | grep -v plans` → at least proxy-architecture.md and installation.md; every remaining 'exact-pin' hit outside docs/plans/ describes release-time behaviour, not the committed tree. `pre-commit run --all-files` passes. Commit subject: "Document the dev floor and contract handshake." |
| 4b | medium | sonnet | kerbside-patches worktree | **WITHDRAWN 2026-08-18 — the fallback this step deletes is inert; see "Decision 1 reversed" above. Retained for the record only; do not execute.** Original brief: **GATED: run only after the kerbside PR merges AND the bootstrap dev wheel is on PyPI (operator confirms both).** In a fresh worktree of shakenfist/kerbside-patches (branch off develop): edit `_patches/patch175-kolla-master-install-proxy-wheel.patch` and its `.patch-message` — delete the `else` / `install_pip(['kerbside-proxy'])` branch so the conditional only installs `/kerbside/proxy-wheels/*.whl` when present (mind the `\`-continuation shell syntax inside the Dockerfile RUN; the surviving branch keeps its current form), and rewrite the message per decision 1 (override = local dev/skew-safety mechanism; the kerbside package now resolves its own proxy wheel via the committed dev-inclusive floor — reference kerbside's dev-proxy-wheel.yml). Do not touch `kolla/ORDER` or `kolla-wave-8/ORDER`. Verify with the repo's documented flow (`_build/test-apply.sh`, per its AGENTS.md — never edit `src/`); the patch must apply cleanly to the kolla checkout and the resulting Dockerfile must contain exactly one `proxy-wheels` conditional and zero bare `kerbside-proxy` pip installs. `pre-commit run --all-files` in that repo. This is its own PR in kerbside-patches (operator creates it); the wave-8 Gerrit refresh (decision 2) is proposed to the operator, not pushed by the agent. |
| 4c | — | — | — | Verification, run by the management session with the operator, post-merge: after the bootstrap `dry_run: false` dispatch publishes the first dev wheel, comment `recheck` on the kolla-ansible Gerrit change (988913 or a sibling in the series). Confirm via `~/bin/zuul-result` that the kerbside scenario jobs pass — a green run from unmodified upstream kolla master IS the "no upstream change needed" confirmation the master plan asks for. Judge only the kerbside scenario jobs: issue #293 (cirros download flake) and the MariaDB IST flake can still fail unrelated voting jobs and just need another recheck. Record the green build UUIDs for the plan closeout. |

## Risks and mitigations

* **Running 4b early breaks downstream master image builds**
  (no floor on pre-merge develop + no fallback = no proxy).
  Mitigated by the explicit gate in the 4b brief and the back
  brief below; the operator confirms merge + bootstrap before
  4b is spawned.
* **Doc drift between the three pages** (the failure mode
  phase 2's decision 3 warned about, and which the survey
  caught in AGENTS.md): mitigated by decision 3's
  one-page-owns-each-fact rule and 4a's grep-based checks,
  which the management session re-runs at review.
* **The 4c recheck fails for unrelated reasons** (cirros
  flake #293, MariaDB IST): the brief scopes the judgement to
  the kerbside scenario jobs specifically; unrelated flakes
  get another recheck, not a plan reopen.
* **The wave-8 refresh may conflict with its wave config**
  (`skip_rebase: true`, pinned `source_sha`): operator-driven;
  the 4b PR only changes `_patches/`, which the wave tooling
  consumes on its next run.

## Definition of done

* No file outside `docs/plans/` claims the committed tree
  exact-pins `kerbside-proxy` or carries no pin (grep evidence
  recorded at review).
* `KERBSIDE_SKIP_CONTRACT_CHECK` and the startup refusal are
  documented in `docs/proxy-architecture.md`, and
  `docs/installation.md` mentions the check and links there.
* `pre-commit run --all-files` passes in kerbside (4a).
* Post-merge tail (tracked, not blocking "implemented on
  branch"): a kerbside scenario job goes green on Gerrit from
  unmodified kolla master, build UUID recorded. The patch175
  clause is dropped — see "Decision 1 reversed" above.
* The master plan's phase 4 sketch carries the timing gate
  (corrected in this planning commit), and the Execution
  table / `index.md` rows are updated.

## Back brief

Before executing any step of this plan, back brief the
operator on the plan and how the intended work aligns with it.
One hard gate: step 4b must not be spawned until the operator
confirms the kerbside PR has merged AND the bootstrap dev
wheel is live on PyPI — an early 4b breaks downstream image
builds (see risks).
