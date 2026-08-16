# Proxy dev releases phase 2: the committed dev specifier

Master plan: `PLAN-proxy-dev-releases.md`. This phase commits the
dev-inclusive `kerbside-proxy` version specifier to
`pyproject.toml` and teaches the release stamp script to replace
it with the exact lockstep pin. This is the change that makes a
plain `pip install` of a git checkout resolve a proxy wheel — the
one that turns the upstream Kolla scenario jobs green once the
plan merges and the bootstrap wheel exists.

Planning effort: medium (per the master plan — phase 1 settled the
version scheme; what remains is mechanical, with care around the
stamp script's replacement logic).

## Scope

In scope:

* `pyproject.toml`: the committed
  `"kerbside-proxy>=0.4.0.dev0"` requirement and a rewrite of the
  now-false "deliberately absent from the committed tree" policy
  comment above the `# KERBSIDE_PROXY_PIN` marker.
* `tools/stamp-proxy-version.sh`: replace-the-specifier logic, the
  matching COMMITTED-PIN POLICY header rewrite, and a small error
  path fix found by the survey (see below).
* Runnable verification that the committed specifier resolves
  today (against the 0.4.0 release, pre-bootstrap) and admits dev
  versions.

Out of scope: the publish workflow (phase 1, implemented), the
contract handshake (phase 3), docs and downstream cleanup
(phase 4), pruning (phase 5). No behaviour change to the dev
stamp script (`tools/stamp-dev-proxy-version.sh`).

## What the survey found

Verified against the tree on 2026-08-14, after rebasing the plan
branch onto current origin/develop (index.md conflicted with the
demo-install plan's row from PR #305; union-resolved, both rows
kept):

* The `# KERBSIDE_PROXY_PIN` marker is at `pyproject.toml:29`,
  inside the `[project] dependencies` list and well outside the
  pin-indirect-dependencies block (`# START_OF_INDIRECT_DEPS` at
  line 78, `# END_OF_INDIRECT_DEPS` at line 122). The
  pin-indirect-dependencies workflow rewrites only between its own
  markers, so the master plan's "indifferent to the new line"
  claim holds. One nuance worth recording: the nightly re-resolve
  pip-installs the direct dependency set, so it will now download
  the ~3.4 MB proxy bin wheel each night — harmless, and the
  proxy wheel has no Python dependencies to pin.
* `tools/stamp-proxy-version.sh:87-94` currently has two live
  branches: replace an existing `"kerbside-proxy==...\"` pin, else
  insert before the marker. Neither matches a `>=` specifier, so
  without this phase the release stamp would insert a second
  kerbside-proxy line alongside the committed one — the script
  MUST learn the new form before any release is cut from a tree
  containing it.
* **Survey bug**: run with no argument on a machine without
  setuptools_scm, `tools/stamp-proxy-version.sh` exits 1 printing
  nothing — `set -e` kills the script inside the
  `version="$(...)"` command substitution before the "ERROR: no
  version supplied" message is reached. Verified on this host.
  Fixed in step 2b since that script is being edited anyway; the
  new dev stamp script shares the pattern and gets the same
  one-line fix.
* **Master plan correction (made at source in this planning
  commit)**: the phase 2 sketch said this phase "should land after
  phase 1 has published at least one dev wheel". That assumed
  per-phase PRs; the operator's actual flow (2026-08-14) is one PR
  when the master plan completes, because CI is expensive. The
  sketch now reflects that: all phases land together; in the
  window between merge and the bootstrap dispatch completing,
  fresh git installs resolve the 0.4.0 *release* wheel, and the
  phase 3 contract handshake is what turns any resulting skew
  into a loud startup refusal rather than a subtle failure.
* Phase 1's row in the Execution table now reads "Implemented (on
  branch; merge deferred to plan completion)" — the accurate
  status under the single-PR flow.

## Decisions

1. **Exact committed line**:
   `"kerbside-proxy>=0.4.0.dev0",            # apache2` — placed
   where the release pin gets inserted today (immediately before
   the `# KERBSIDE_PROXY_PIN` marker), keeping the license-tag
   column convention of the surrounding list. The
   `# KERBSIDE_PROXY_PIN` marker line STAYS: it costs nothing,
   remains the anchor for the stamp script's fallback branch, and
   marks the line for future readers.
2. **Stamp script replacement regex matches any specifier
   operator**, not just `==`: the first branch becomes "a
   `"kerbside-proxy<op>...\"` line exists → rewrite the whole
   quoted requirement to `"kerbside-proxy==${version}"`". This
   collapses committed-`>=` and re-stamped-`==` trees into one
   code path; the insert-at-marker branch remains as fallback for
   hypothetical old trees. Idempotence (re-stamping an
   already-stamped tree) must keep exactly one kerbside-proxy
   line — the reviewer-bait risk here is a regex that matches
   twice or matches inside the indirect-deps block, and the
   defence is the step 2b round-trip test plus the fact that no
   kerbside-proxy line can legally exist elsewhere in the file.
3. **The policy comments in both files are rewritten, not
   patched.** The committed policy is now "a dev-inclusive floor
   is committed so unreleased installs resolve the newest proxy
   wheel from PyPI (see dev-proxy-wheel.yml); the release stamp
   tightens it to the exact lockstep pin". The old rationale
   ("dev installs must not require the sibling package on PyPI")
   is retired by this plan and must not survive anywhere — one
   place saying the old thing and one the new is the
   documentation failure mode this repo's plans keep warning
   about.
4. **The silent-death fix is in scope** (survey bug above): guard
   the command substitution (`|| true`) in both stamp scripts so
   the existing empty-version ERROR path actually reaches its
   message. Two lines total; folded into 2b rather than filed as
   an issue, because the alternative is committing a known-broken
   error path in a file the same commit edits.
5. **Renovate is left alone.** It may eventually propose bumping
   the `>=0.4.0.dev0` floor as releases happen; that is harmless
   (the stamp script rewrites whatever operator/version it finds)
   and arguably useful. If it churns, tightening its config is
   phase 4/future-work material, not this phase.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | In `pyproject.toml`, replace the eight-line comment block above `# KERBSIDE_PROXY_PIN` (lines 20-28: "The Rust SPICE proxy binary is shipped ... rather than from this pin.") with a rewritten block stating the NEW policy (decision 3: committed dev-inclusive floor so git installs resolve the newest wheel — released or dev — from PyPI, published by dev-proxy-wheel.yml; the `.dev0` suffix is what opts pip into pre-releases; tools/stamp-proxy-version.sh tightens this line to the exact `==` lockstep pin at release time; keep the marker-line explanation). Insert `"kerbside-proxy>=0.4.0.dev0",            # apache2` immediately before the `# KERBSIDE_PROXY_PIN` line, aligning the `# apache2` comment column with the neighbouring entries. Do not touch anything between START_OF_INDIRECT_DEPS and END_OF_INDIRECT_DEPS. Run `pre-commit run --files pyproject.toml` and `tox -epy3` (the deps list must still parse and install). |
| 2b | medium | sonnet | none | In `tools/stamp-proxy-version.sh`: (1) rewrite the COMMITTED-PIN POLICY header block (lines ~20-26) per decision 3 — the committed tree now carries `kerbside-proxy>=X.Y.Z.dev0` and this script REPLACES it with the exact pin; (2) generalise the first replacement branch: match any `"kerbside-proxy<specifier>"` requirement (regex on the quoted string, e.g. `"kerbside-proxy[=><~!][^"]*"`) and rewrite it to `"kerbside-proxy==${version}"`, keeping the marker-insert branch as fallback and the final ERROR branch; (3) fix the silent death: change `version="$(cd ... && python3 -m setuptools_scm 2>/dev/null)"` to append `|| true` inside the substitution so the existing "ERROR: no version supplied" path prints (make the same one-line fix in `tools/stamp-dev-proxy-version.sh`, which copied the pattern). Functional round-trip test, then restore with git checkout: on the committed tree run `tools/stamp-proxy-version.sh 9.9.9` and assert `grep -c '"kerbside-proxy' pyproject.toml` is 1 and the line is `==9.9.9`; run it AGAIN and assert still exactly one line, still `==9.9.9`; also assert Cargo.toml got `version = "9.9.9"`. shellcheck via pre-commit must pass. |
| 2c | — | — | — | Verification, run by the management session (like phase 1's step 1d): in a scratch venv, (a) `pip download 'kerbside-proxy>=0.4.0.dev0' --no-deps -d <scratch>` must fetch the 0.4.0 release wheel (the newest on PyPI pre-bootstrap), proving the spec is valid, pre-release-enabled and resolvable before any dev wheel exists; (b) with `packaging`, assert `0.4.1.dev163` is contained in the specifier and `0.3.0` is not. Record both outputs in the PR description alongside the phase 1 evidence. |

## Risks and mitigations

* **A release cut from a tree with the committed `>=` line, using
  an un-updated stamp script, would ship two kerbside-proxy
  requirements** (the inserted `==` plus the committed `>=` —
  pip would intersect them, but the file would be wrong). Cannot
  happen here: 2a and 2b land in the same phase, and the 2b
  round-trip test exercises the committed tree exactly as a
  release would see it. The management session checks the test
  output.
* **Regex over-match** (decision 2): mitigated by the round-trip
  idempotence assertions and by grep-counting exactly one
  kerbside-proxy line after each stamp.
* **The committed floor makes `pip install .` depend on PyPI
  reachability** where the committed tree previously had no such
  dependency. This is the entire point of the plan (the
  offline/dev escape hatches — `KERBSIDE_PROXY_BIN`, the local
  Rust build tree — survive unchanged in `find_proxy_bin()`), but
  it is a real behaviour change for offline `tox -epy3` runs:
  step 2a runs tox to prove the dep resolves in this repo's own
  CI posture before the phase is called done.
* **Window between merge and bootstrap publish** (fresh installs
  resolve 0.4.0 against develop-tip Python): accepted and
  documented in the master plan correction; phase 3's handshake
  is the backstop, and the post-merge sequence (bootstrap
  dispatch immediately after merge) keeps the window to minutes.

## Definition of done

* `pyproject.toml` contains exactly one `kerbside-proxy` line,
  reading `>=0.4.0.dev0`, above the retained
  `# KERBSIDE_PROXY_PIN` marker and outside the indirect-deps
  block; no comment in the file still claims the pin is absent
  from the committed tree.
* The 2b round-trip test output (single line, `==9.9.9`,
  idempotent, Cargo.toml stamped) is recorded and the tree
  restored.
* `tools/stamp-proxy-version.sh` run with no argument and no
  setuptools_scm prints its ERROR message (no silent exit 1);
  same for `tools/stamp-dev-proxy-version.sh`.
* Step 2c's resolution evidence (0.4.0 wheel downloaded;
  specifier-containment asserts) is recorded for the PR
  description.
* `pre-commit run --all-files` and `tox -epy3` pass.
* The master plan's phase 2 sketch no longer claims per-phase PR
  sequencing, and the Execution table / `index.md` rows are
  updated (all done in the planning commit).

## Back brief

Before executing any step of this plan, back brief the operator on
the plan and how the intended work aligns with it. No additional
gates: the phase is small, self-verifying, and lands on the same
branch as phase 1 under the single-PR flow.
