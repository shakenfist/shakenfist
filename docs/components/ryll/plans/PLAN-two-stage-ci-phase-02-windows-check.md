# Two-stage CI phase 2: Windows cross-check

Phase 2 of [PLAN-two-stage-ci.md](/components/ryll/plans/PLAN-two-stage-ci/). It answers
open question 3 of the master plan — whether a cross-target `cargo
check` from the Linux devcontainer is a viable cheap substitute for
the slow GitHub-hosted Windows builds, which phase 1 moved into the
merge tier where a failure ejects the change from the merge queue.

## Spike result

The spike was run on 2026-08-09 in the `ryll-dev` devcontainer.

`x86_64-pc-windows-msvc` — the triple CI actually builds — **is not
viable**. `cargo check` still runs build scripts, and `aws-lc-sys`
(reached transitively through rustls, and present regardless of
`--no-default-features`) compiles its vendored BoringSSL C sources
for the target. With no MSVC cross toolchain present `cc-rs` falls
back to the host's Linux `gcc`, which fails on the Windows headers:

```
error: unknown type name 'pthread_rwlock_t'; did you mean 'pthread_cond_t'?
  552 |   pthread_rwlock_t lock;
error occurred in cc-rs: command did not execute successfully
  (status code exit status: 1): ... "cc" ... x_x509a.c
```

Making that work needs `xwin`-fetched Windows SDK and CRT headers
plus `clang-cl` — materially heavier tooling and a licensing
question, for a check that still cannot validate linking. Rejected.

`x86_64-pc-windows-gnu` **is viable** once mingw-w64 is present.
Without it the failure is immediate and unambiguous (`ring`: failed
to find tool `x86_64-w64-mingw32-gcc`); with
`gcc-mingw-w64-x86-64` and `g++-mingw-w64-x86-64` installed, `cargo
check --target x86_64-pc-windows-gnu --no-default-features -p ryll`
succeeds in about 24 seconds cold and under a second warm. The check
cross-compiles every native-build dependency in the graph: `ring`,
`aws-lc-sys`, `zstd-sys`, `mozjpeg-sys`, and the cmake-driven
vendored libopus build in `audiopus_sys`.

The spike also corrected a premise carried in the master plan's
situation section: `--no-default-features` does **not** drop the
opus/cmake native build. `shakenfist-spice-webrtc` is an
unconditional dependency of `ryll` and depends on `opus` directly,
independent of ryll's own `audio` feature; only `gui` is
feature-gated in `ryll/Cargo.toml`. The slim graph is therefore not
as slim as assumed, which is why the check is worth its ~24 seconds
rather than being trivially redundant.

## What the check does and does not catch

The gnu triple is a proxy, not the thing CI builds. It shares the
`cfg(windows)` and `windows-sys` surface with the msvc triples,
which is the failure mode the master plan cited (windows-sys churn
and the `--no-default-features` graph breaking in ways a Linux build
cannot see). It does not catch breakage gated on `target_env =
"msvc"`, crates that build only under msvc, link-time failures (no
triple is linked by `cargo check`), or anything specific to
`aarch64-pc-windows-msvc`. Those remain the merge tier's job.

The point is to move the common case from "ejected from the merge
queue twenty minutes in" to "smoke tier fails in about two minutes".

## Target design

Three changes, all following patterns already in the tree.

`.devcontainer/Dockerfile` — add `gcc-mingw-w64-x86-64` and
`g++-mingw-w64-x86-64` to the existing `apt-get install` block with
a comment in the style of the ones already there, and add
`x86_64-pc-windows-gnu` to the existing `rustup component add` line
(as a separate `rustup target add`, same `umask 0000` treatment) so
the target ships in the image rather than being downloaded on every
container run. `RUSTUP_HOME` is baked into the image and not
volume-mounted, so an ad-hoc `rustup target add` does not persist.
Cost is roughly 65 MB of downloads at image build time; the posix
variant is selected by default and needs no `update-alternatives`
fixup.

`Makefile` — a `check-windows` target following the existing
devcontainer-wrapped pattern (`ensure-cache` prerequisite,
`$(DOCKER_RUN) $(RYLL_IMAGE) cargo check ...`), and a line in the
`help` target's build section.

`.github/workflows/ci.yml` — a `Cross-check Windows target` step in
the existing `build-linux` smoke job, placed immediately after the
checkout so it fails fast. Reusing that job means the devcontainer
image is built once, so the marginal CI cost is the image-build
delta plus the check itself; a standalone job on an ephemeral runner
would rebuild the whole image for a 25-second check.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Make the three changes in "Target design" above, then rebuild the devcontainer image and run `make check-windows` to prove it passes end to end. Match the surrounding comment style in each file. |

## Commit checkpoints

One commit for the whole phase: the Dockerfile, Makefile and
`ci.yml` changes are a single logical change and none of them is
useful without the others.

## Validation

* `make devcontainer && make check-windows` passes locally.
* `pre-commit run --all-files` passes, and actionlint is clean
  (nothing in the repo invokes actionlint despite
  `.github/actionlint.yaml` existing — run it via
  `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint`).
* On the PR, `Build (Linux x86_64)` shows the new step passing and
  the job's total runtime has not moved materially.

## Risks and notes

* The check adds its cost to the critical path of the longest smoke
  job. Placing it first means a Windows-only breakage fails the job
  in about two minutes instead of nine.
* `PLAN-ci-platform-matrix.md` plans macOS/Windows *runtime* smoke
  coverage. This check is a compile-time proxy only and does not
  reduce the value of that plan.
* If the gnu target ever diverges enough from msvc to produce false
  failures, the step is a single line to delete — the merge tier
  remains the authoritative Windows signal either way.
