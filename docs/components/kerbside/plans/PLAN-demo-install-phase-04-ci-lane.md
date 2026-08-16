# Phase 4: a CI lane for the compose demo

Master plan: [PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/)

Planned at medium effort: it follows `rust.yml`'s advisory
path-filtered pattern and reuses the direct-qemu lane's
proxy-wheel steps.

## Situation

The demo from phase 3 becomes a documented, user-facing path
in phase 5. `docs/installation.md` will tell readers to run
it. Nothing else in CI exercises a container build, a compose
stack, or `kerbside db upgrade` on the wheel-install path.

The tree's own history is the argument for this phase:
`etc/kerbside.conf.example` was referenced by two documents
for long enough that a second document copied the reference,
and nothing noticed, because nothing executed it.

## Mission

A CI lane brings up the compose stack against the pull
request's code and asserts a SPICE session is proxied, so a
change that breaks the documented demo fails visibly.

## Approach

### Tier and gating

**Advisory, path-filtered — not a required check.**
`docs/testing.md:34` establishes the precedent with
`rust.yml`, and `.claude/CLAUDE.md` is emphatic that the five
required status checks are gate jobs whose names are bound to
the develop ruleset, so adding a required check means a
ruleset change that `tools/check-required-checks.sh`
validates against. That is not worth it for a demo path.

Path filter, following `rust.yml:10-22`'s shape:

```
paths:
  - 'demo/**'
  - 'kerbside/migrations/**'
  - 'pyproject.toml'
  - 'docs/installation.md'
  - '.github/workflows/demo-compose.yml'
```

`docs/installation.md` is in the list deliberately: the
document and the thing it documents should not be able to
drift without the lane running. `kerbside/migrations/**` and
`pyproject.toml` are there because phase 1's packaging is the
part most likely to break silently.

Read `docs/testing.md` before writing the workflow — it is
the authority on tiers and gate jobs — and add the lane to
its table in the same commit. A lane absent from that table
is a lane nobody knows the status of.

### Runner and shape

`runs-on: [self-hosted, vm, debian-12, l]`. Justification for
`l`: a qemu TCG guest, a MariaDB, a container build, and a
release build of the Rust proxy wheel on one runner. Do not
use an unsized label — `rust.yml:29`'s comment records that
an unsized `vm` label defaults to `xs` (1 core / 2 GB) in
private-ci, which will not do.

`no_proxy: 127.0.0.1,localhost` at job level. The runner
image exports `http_proxy`/`https_proxy` at an upstream
squid, and loopback traffic to the published demo ports must
bypass it or squid returns 503 — recorded at
`direct-qemu-functional.yml:96-99` and hit again by the oVirt
lane, so assume it will bite here too.

Concurrency group cancelling in-progress runs, per
`direct-qemu-functional.yml:33-36`.

### Steps

1. Checkout.
2. Build the proxy wheel: copy the four steps verbatim from
   `direct-qemu-functional.yml`'s wheel build (apt
   prerequisites, `dtolnay/rust-toolchain@stable`, a
   maturin+ziglang venv, `tools/build-proxy-wheel.sh` with
   `WHEEL_OUT`). Needed because the `KERBSIDE_PROXY_PIN` in
   `pyproject.toml` is deliberately absent from the committed
   tree, so a `KERBSIDE_SOURCE=/src` build has no proxy to
   install from PyPI.
3. Build the demo image with `KERBSIDE_SOURCE=/src` and the
   built wheel available, so the lane tests the PR rather
   than the last release.
4. `docker compose up -d --wait`, so compose does the
   readiness waiting rather than a hand-rolled sleep.
5. Run `demo/get-console.sh` and assert the `.vv` contains
   `tls-port=`, `host-subject=`, and a `ca=` field with an
   escaped PEM. Cheap, and it catches the exact silent
   failure phase 3 warns about.
6. Drive a real SPICE session with **ryll headless**,
   following `direct-qemu-functional.yml`'s ryll build (a
   `--depth 1` clone of `shakenfist/ryll`, `cargo build
   --release --no-default-features -p ryll`; **without**
   `--features digest-decode`, which the oVirt lane's
   equivalent step also omits, since this lane asserts
   connection rather than pixels). Connect it to the `.vv`
   over the TLS port and assert the SPICE link handshake
   completes and the main channel opens. `tools/direct-qemu/
   smoke-client.py` is the closest existing driver — read it
   and reuse rather than reinventing the assertion.
7. Assert the negative cases, which is what makes the lane
   more than a smoke test:
   - `docker compose stop spice-target`, then confirm a
     fresh console request fails in a bounded time rather
     than hanging. A proxy that hangs when its backend
     disappears is a real defect and this is the only lane
     positioned to catch it cheaply.
   - Append a dummy oVirt entry to `demo/sources.yaml`,
     restart, and confirm `kerbside demo token` **refuses**,
     naming the offending source. Phase 1 unit-tests this
     guard against fixtures; here it is exercised against a
     real deployment, which is where it actually has to
     hold. Restore the file afterwards.
8. `if: always()` artifact upload of `docker compose logs`
   for all three services, the `.vv` (**with the `password=`
   line redacted** — it is a live console token, short-lived
   but real, and CI artifacts are downloadable), and the ryll
   output.

Put the multi-step logic in `tools/demo/` scripts called from
the workflow, not inline in YAML: the operator's global
convention is that anything beyond about five lines in a
workflow step goes in `tools/` and is invoked from there.

### Do not test the PyPI path here

The demo's *default* build installs released `kerbside` from
PyPI. A lane that exercised the default would test the last
release, not the pull request, and would fail whenever PyPI
lags a version bump. Test `KERBSIDE_SOURCE=/src` here, and
note in the workflow comment that the released-package path
is covered at release time and by the evaluator. Record it in
the master plan's future work if a periodic check of the
PyPI path seems worth adding later — a nightly would be the
natural home.

### Carried over from the phase 3 review

**shellcheck over `demo/` has no CI enforcement.** Phase 3
extended the pre-commit hook to `^(tools|demo)/`, which is
the right scope, but `grep -rn pre-commit .github/workflows
tox.ini` shows pre-commit is never invoked by any workflow or
tox environment -- the only hits are comments in
`pr-address-comments.yml` explaining why it is deliberately
skipped there. So the demo's four shell scripts are checked
only on machines where a developer installed the hooks.

This phase should close that, and it is the natural owner
because it is already adding the lane that runs the demo.
Either add a shellcheck step to the new lane covering
`tools/` and `demo/`, or add a `tox -e shellcheck`
environment and call it from `sanity_checks`. Prefer the
former: `sanity_checks` is a gate job feeding a required
check, and `docs/testing.md` is the authority on what may
change there.

**A cheap smoke check ahead of the full lane.**
`docker compose config` validates the compose schema and
`docker compose build` catches Dockerfile syntax, both in
seconds and without bringing anything up. Worth running as
the lane's first step so a schema typo fails fast rather
than after a multi-minute image build and stack start.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Write `tools/demo/lane-up.sh` and `tools/demo/lane-assert.sh`, modelled on `tools/direct-qemu/lane-up.sh` in structure and comment style. `lane-up.sh` builds the image with `KERBSIDE_SOURCE=/src` plus the prebuilt proxy wheel, brings the stack up with `--wait`, and fails with the relevant `docker compose logs` on timeout. `lane-assert.sh` runs `demo/get-console.sh`, asserts the three `.vv` fields from step 5 above, drives ryll against the TLS port asserting the main channel opens, then performs both negative cases from step 7 (backend stopped, and `kerbside demo token` refusing a non-static source list), restoring `demo/sources.yaml` on exit via a trap so a failure mid-assertion does not leave the working tree dirty. Read `tools/direct-qemu/smoke-client.py` and reuse its assertion approach rather than writing a new SPICE client driver. |
| 4b | medium | sonnet | none | Write `.github/workflows/demo-compose.yml` per "Runner and shape" and "Steps". Copy the proxy-wheel build steps and the ryll build steps from `direct-qemu-functional.yml` rather than paraphrasing them — they encode runner-specific workarounds. Include the `check_paths` review-only filter job only if the lane is cheap enough to care; since this lane is advisory and path-filtered to a narrow set, the filter is unnecessary and its absence should be noted in a comment. Add the `if: always()` artifact step with the `.vv` password line redacted. |
| 4c | low | haiku | none | Add the lane to `docs/testing.md`'s workflow table (see line 34 for the `rust.yml` advisory row as the model) and to the "Neither tier" list in `.claude/CLAUDE.md`'s CI Workflows section. One row and one bullet; do not restructure either document. |
| 4d | medium | sonnet | none | Prove the lane fails when it should, which is the only evidence that matters for a new lane. On a scratch branch, break the demo three ways in sequence and confirm the lane goes red each time for the right reason: (i) point `demo/sources.yaml` at the wrong `insecure_port`; (ii) set `PROXY_HOST_SUBJECT` to a mismatched string, which must fail the TLS assertion rather than silently falling back; (iii) revert phase 1's packaging so `kerbside db upgrade` cannot find its migrations. Report the three failure messages. Do not merge the scratch branch. |

## Success criteria

* The lane is green on a pull request touching `demo/`.
* The lane is red, with a comprehensible message, for each
  of the three deliberate breakages in 4d.
* The lane does not run on unrelated changes.
* No change to the develop ruleset and no new required
  check.
* `docs/testing.md` and `.claude/CLAUDE.md` list the lane.
* Artifacts are uploaded on failure and contain no live
  console token.
