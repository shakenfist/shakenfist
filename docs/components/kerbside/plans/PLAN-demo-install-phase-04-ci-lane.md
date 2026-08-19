# Phase 4: a CI lane for the compose demo

Master plan: [PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/)

Planned at medium effort, as the master plan specifies. The
survey moved one thing out of medium territory, though: this
lane would be the **first container build in kerbside CI**, so
the phase leads with a runner probe rather than assuming a
working Docker daemon (finding 5).

## Situation

Phase 3 built `demo/`. Phase 5 will point `docs/installation.md`
at it, which makes it a documented, user-facing path. Nothing
in CI exercises a container build, a compose stack, or
`kerbside db upgrade` on the wheel-install path.

The argument for this phase stopped being hypothetical while
the phase was being planned. Renovate merged #330 on
2026-08-17, bumping `demo/docker-compose.yml` from `mariadb:11`
to `mariadb:12` — a database major version, under the demo,
with nothing to run it. I verified it by hand during the survey
(finding 7) and it is fine, but "a maintainer happened to check"
is exactly the property this phase exists to replace.

## Mission

A CI lane brings up the compose stack against the pull
request's own code and asserts a SPICE session is proxied, so a
change that breaks the documented demo fails visibly.

## Scope

In scope:

- `.github/workflows/demo-compose.yml`, advisory and
  path-filtered.
- `tools/demo/` scripts holding anything longer than a few
  lines, per the operator convention that workflow steps stay
  short.
- A runner probe, `tools/demo/probe-runner.sh`, modelled on
  `tools/direct-qemu/probe-runner.sh`.
- shellcheck enforcement in CI for `tools/` and `demo/`
  (carried over from the phase 3 review).
- Registering the lane in `docs/testing.md` and
  `.claude/CLAUDE.md`.

Out of scope, deliberately:

- **Rewriting `docs/installation.md`.** Phase 5, and it must
  be last.
- **Making the lane a required check.** Decision 1.
- **Testing the PyPI default build.** Decision 4.
- **Fixing the 5900 collision** (finding 10). Recorded, and
  the probe will detect it, but changing the demo's published
  ports is a phase 3 file change with its own documentation
  consequences and does not belong in a CI phase.
- **Publishing a demo image to a registry.** Still a
  release-process question, as phase 3 recorded.

## What the survey found

The draft of this file was written as part of the master plan
commit (`e9d6497`), before phases 1–3 executed, and lightly
touched by the phase 3 review (`bc1fa4a`). It is substantially
stale. The corrections are recorded here and applied at source
in the master plan's Execution table and the `index.md` row as
part of the planning commit, so a later step need not redo it.

**1. Every line citation in the draft is wrong.** Not
approximately — the referenced lines now hold unrelated
content. Corrected:

| Draft said | Actually |
|---|---|
| `docs/testing.md:34` for the `rust.yml` advisory precedent | `docs/testing.md:73` (table row), prose at `:79` |
| `rust.yml:10-22` for the path-filter shape | push filter `rust.yml:12-20`, PR filter `:22-29` |
| `rust.yml:29` for the unsized-`vm`-defaults-to-`xs` note | `rust.yml:36-38`, and it selects `xl`, not the `l` the draft proposes |
| `direct-qemu-functional.yml:33-36` for concurrency | `:30-32` |
| `direct-qemu-functional.yml:96-99` for the `no_proxy` squid note | `:88-92` |

The `rust.yml` filter list also grew two entries in #314
(`tools/stamp-dev-proxy-version.sh`,
`tools/verify-wheel-stamping.sh`), which is why the range
moved.

**2. The ryll feature-flag claim is backwards, and copying the
named file would produce the opposite of what the draft
intends.** The draft says to follow
`direct-qemu-functional.yml`'s ryll build "**without**
`--features digest-decode`, which the oVirt lane's equivalent
step also omits". In fact `direct-qemu-functional.yml:143`
*includes* `--features digest-decode`; the lane that omits it
is the oVirt one at `functional-tests.yml:629`. For a lane
asserting connection rather than pixels,
`functional-tests.yml:629` is the line to copy.

**3. The proxy-wheel build is one step, and not the one
described.** The draft says to "copy the four steps verbatim
from `direct-qemu-functional.yml`'s wheel build (apt
prerequisites, `dtolnay/rust-toolchain@stable`, a
maturin+ziglang venv, `tools/build-proxy-wheel.sh` with
`WHEEL_OUT`)". Reality at
`direct-qemu-functional.yml:157-161`: a single step that makes
a maturin + **setuptools_scm** venv — no ziglang, which is for
cross-compilation and is not needed for a native build — and
calls `tools/direct-qemu/install-proxy-wheel.sh --venv`, which
sets `WHEEL_OUT` itself at `install-proxy-wheel.sh:53`. The
four-step shape the draft describes is `release.yml`'s.

**4. The lane probably does not need to build the Rust proxy
at all, which removes the most expensive thing in the draft.**
The draft's justification for building it: "the
`KERBSIDE_PROXY_PIN` in `pyproject.toml` is deliberately absent
from the committed tree, so a `KERBSIDE_SOURCE=/src` build has
no proxy to install from PyPI." That was true when written and
#314 changed it. `pyproject.toml:34` now carries a
dev-inclusive **floor**, `kerbside-proxy>=0.4.0.dev0`, and the
`.dev0` suffix is what makes pre-releases eligible for that
requirement. Verified rather than reasoned about:

```
$ pip install --dry-run --report - .      # from a clean checkout
kerbside-proxy 0.5.1.dev1
```

That is a dev wheel published from develop by
`dev-proxy-wheel.yml`, and resolving it is precisely what phase
2 of `PLAN-proxy-dev-releases` set out to achieve for
downstream git installs. So a `/src` build pairs a checkout
daemon with a develop-tracking binary on its own, with no cargo
in the lane. See decision 3 for the residual case.

**5. No kerbside workflow uses Docker. At all.** `grep -rn
'docker\|podman\|buildkit' .github/workflows/` returns nothing.
This lane would be the repository's first container build, and
three things the draft silently assumes are unverified on the
private-CI runners:

- a usable Docker daemon and permission to reach it;
- Docker Engine 23.0+, which `demo/Dockerfile` requires by
  name for `RUN --mount=type=bind` under the built-in
  BuildKit frontend;
- egress from *inside* a build container to pypi.org. The
  runners sit behind a squid
  (`direct-qemu-functional.yml:88-92`) and
  `functional-tests.yml:106` points pip at a devpi mirror
  (`http://192.168.1.15:3141/root/pypi/+simple/`). A
  `docker build` inherits neither the runner's proxy
  environment nor its `PIP_INDEX_URL`.

This is the single most likely reason a first attempt at the
lane fails, and it is cheap to find out first. Hence step 4a.

**6. The shellcheck-in-CI gap is real and unchanged.** `grep
-rn pre-commit .github/workflows tox.ini` finds only two hits,
both comments in `pr-address-comments.yml` (lines 16 and 154)
explaining why pre-commit is skipped there. `tox.ini` has
`flake8`, `py3`, `cover`, `genprotos` and `bindep`, and no
shellcheck environment. So the demo's four shell scripts are
checked only where a developer installed the hooks. The phase 3
review's carry-over stands.

**7. Renovate is already changing the demo untested, and the
current develop had never been run.** #330 (`433bc88`,
2026-08-17) bumped the demo database from `mariadb:11` to
`mariadb:12`. Phase 3's end-to-end verification was on
`mariadb:11`. Verified during this survey against
`mariadb:12.3.2`: `docker compose up -d --wait` reaches all
three services healthy, all 9 migrations apply, the proxy
starts, and `kerbside demo token` mints. No action needed — but
this is the phase's own justification, arriving unprompted.

**8. The `.vv` really does carry a live credential.** The
draft's artifact-redaction requirement is well founded:
`kerbside/api.py:374` puts `password=%(token)s` in
`VIRTVIEWER_TEMPLATE`.

**9. A `/src` build needs full git history, so the lane needs
`fetch-depth: 0`.** `demo/Dockerfile` records that
setuptools_scm's git file finder is the only thing installing
`kerbside/sources/` and `kerbside/migrations/`, and
`install-proxy-wheel.sh`'s header notes a shallow clone cannot
count commits since the last `v*` tag. `actions/checkout`
defaults to depth 1. The precedent is already in the tree:
`direct-qemu-functional.yml:100` and
`sf-e2e-functional.yml:113` both set `fetch-depth: 0`.

**10. The demo's fixed port 5900 collides with any host
running a VNC server.** Hit twice on the development host
during this survey; the bind fails with `failed to bind host
port 127.0.0.1:5900/tcp: address already in use` from the
Docker daemon, which does not name what holds it. A CI runner
is a plausible place for the same collision. Out of scope to
fix (see Scope), but the probe should detect it and say so.

**11. A gotcha for anyone writing a compose override in this
phase.** `docker compose` merges list-valued keys such as
`ports` by *appending*, so an override file that lists fewer
ports does not reduce them — the original bindings are still
attempted. Replacing the list needs the `!override` tag
(Compose 2.24+; this host runs v5.4.0). Learned the hard way
while working around finding 10.

Nothing else in the draft's reasoning failed. The tier
decision, the artifact redaction, the negative-case assertions
and the "do not test the PyPI path" argument all survive
scrutiny and are carried forward.

## What implementation found, after the survey

**12. A `workflow_dispatch`-only probe cannot be run before it
merges.** GitHub only offers `workflow_dispatch` for workflows
that exist on the *default* branch, so the shape decision 2
specifies — a throwaway dispatch-only workflow — is
untriggerable on a feature branch, which would have made the
gating step impossible to execute before merge. Resolved by
giving `demo-probe.yml` a `pull_request` trigger as well,
path-filtered to itself and the probe script so it costs a
runner only on the pull request that introduces it. The
`workflow_dispatch` trigger is kept for re-running it later.

**13. Asserting the backend-failure case on `get-console.sh`
does not work, and passes when it should fail.** Fetching a
`.vv` never touches the hypervisor: `get-console.sh` mints a
token, reads the console list and verifies the TLS leg to the
*proxy*, all of which keep working with the SPICE target
stopped, because the proxy only dials the backend once a client
opens a session. The first draft of `lane-assert.sh` asserted
on `get-console.sh` here and reported a stopped backend as
success. Phase 3's own evidence says why — it verified this
case with `remote-viewer`, not with `get-console.sh` — so the
assertion now drives ryll and additionally requires the proxy
to have logged `hypervisor connection failed`.

**14. Appending a non-static source to `demo/sources.yaml`
needs zero indentation, or it crashes the daemon instead of
tripping the guard.** The file is a list of *sources*, each
with `source:`, `type:` and `consoles:`. An indented entry
lands inside the demo source's `consoles:` list, which is
malformed enough that the daemon dies at startup; the container
never returns, `docker compose exec` fails, and the refusal
under test never happens. The first draft did exactly that and
reported the crash as a missing refusal. `lane-assert.sh` now
appends a top-level source and separately checks that kerbside
came back healthy, so a crash is reported as a crash.

**15. `KERBSIDE_SOURCE=/src` cannot be built from a git
worktree**, which matters because that is where this plan was
written. `.git` is a file in a worktree, pointing outside the
build context, so setuptools_scm fails with `LookupError:
setuptools-scm was unable to detect version for /src`.
`demo/Dockerfile` documents this and issue #326 tracks it; it
is recorded here because every local verification of this
phase had to be done from a throwaway clone instead.

## What the first CI run found

**16. The runner image has no docker at all**, which is a
stronger result than finding 5 anticipated. Finding 5 asked
whether the daemon was usable, new enough, and able to reach an
index; the probe never got as far as any of those. Its entire
output on `[self-hosted, vm, debian-12, l]` was:

```
ERROR: docker is not installed on this runner
=== docker client and daemon ===

=== probe-runner cannot continue without docker ===
```

Debian 12 cannot close the gap either, so this is not a matter
of adding `docker.io` to the existing apt step. Bookworm ships
`docker.io` 20.10.24, below the 23.0 `demo/Dockerfile` needs
from the built-in BuildKit frontend, and bookworm has **no**
`docker-compose-v2` package at all — its only compose is
1.29.2, the end-of-life python implementation, which does not
provide the `docker compose` subcommand the demo documents.
Verified against `sources.debian.org`, not assumed. Resolved by
decision 9.

The second run, with decision 9's install step in front of the
probe, answered all of finding 5 and closed the gate:

```
client 29.7.2 / server 29.7.2
Docker Compose version v5.5.0
server version: 29.7.2
port 13002: free
port 5900: free
port 5901: free
index reachable from inside a build
/dev/vda3       282G  4.1G  266G   2% /
=== probe-runner complete: this runner can build and run the demo ===
```

The index check is the one that mattered, and it did the real
thing: pulled `python:3.13-slim` from Docker Hub, then
downloaded `kerbside_proxy-0.5.0-py3-none-manylinux_2_28_x86_64.whl`
at 29 MB/s from inside the build. The squid also serves
`download.docker.com` — `docker-ce`, `docker-ce-cli`,
`containerd.io`, `docker-buildx-plugin` and
`docker-compose-plugin` all fetched from `bookworm/stable` — and
both proxy paths were configured from the runner's real
environment (`http://cache.home.stillhq.com:3128`) rather than
guessed. All three ports the demo publishes are free on these
runners, so finding 10's collision is a development-host problem
only.

**17. The four SC2015 findings in `lane-assert.sh` were never
linted locally**, despite step 4d's own definition of done
recording a clean `tox -e shellcheck`. That run covered 40
scripts; CI covered 44. The local run predated the last round of
edits to `lane-assert.sh`, so the `A && ok ... || bad ...` lines
it introduced were added after the only check that would have
caught them. Rewritten as explicit `if`/`else`, which is what
SC2015 asks for and is clearer anyway, since `bad` running
because `ok` failed is a genuine (if unlikely) misreport.

A second, more durable version of the same trap: the wrapper
selected files with plain `git ls-files`, so a newly written and
not-yet-added script was silently unchecked —
`install-docker.sh` was invisible to it on first run. Now
`git ls-files --cached --others --exclude-standard`. This cannot
diverge from the pre-commit hook for a file being committed,
because pre-commit sees staged files; it only makes the tox
environment stricter for work in progress. The empty-list guard
in that script already records an earlier member of this same
family of bug.

**18. The redaction step ran before log collection, so it could
not have scrubbed the logs at all** — and a real secret went out
in the first green run's artifact because of it. Finding 8 aimed
the redaction at the `.vv` console token, which it did handle
(`password=REDACTED` in both `.vv` files, confirmed by
downloading the artifact). What it missed is that MariaDB prints
the root password it generates for `MARIADB_RANDOM_ROOT_PASSWORD`
(`demo/docker-compose.yml:17`) straight into its own log:

```
db-1 | [Note] [Entrypoint]: GENERATED ROOT PASSWORD: <32 chars>
```

Severity is low on its own terms — the database is never
published outside the compose network, the password is random per
run, and the container is destroyed with the job — but it was a
credential in an artifact downloadable by anyone who can read the
repository, and the step meant to prevent exactly that was
ordered so that it never could.

Resolved by `tools/demo/redact-artifacts.sh`, called *after* log
collection. It redacts both secrets and then **fails the step if
either pattern stopped matching**, because a redaction that
silently stops working looks identical to one that worked. It
prints offending filenames only, never the matching line, since
the workflow log is as public as the artifact. Verified both ways
against the real leaked artifact from the green run: it scrubs
the live token and the database password, and it exits 1 naming
the file when a token is planted somewhere the patterns do not
reach.

## What review found

The automated reviewer raised twelve items on pull request #336.
Three were real defects in this phase's own work, and two of
them are the same failure mode findings 13 and 14 already record
— an assertion reporting the wrong thing — which is worth saying
plainly rather than filing as three unrelated fixes.

**19. `set -e` made the bounded-failure assertion dead code.**
`lane-assert.sh` ran `timeout ... ryll ...` as a bare command
followed by `RC=$?`. Under `set -euo pipefail` a non-zero exit
terminates the script *before* `RC` is ever assigned. Verified
directly: a script doing `timeout 1 sleep 5; RC=$?` exits 124
without reaching the next line.

The consequences were worse than a missed assertion. Exit 124 is
precisely the hang this group exists to catch, so the `bad "a
session hung..."` branch could never run; and everything after
it — the proxy's own failure log line, and the whole of
assertion 4 — was silently skipped, while the summary still
reported however many assertions had passed. The lane reported 8
passing because ryll happens to exit 0 on this path today, which
is an accident of ryll's behaviour and not a property the script
established. Fixed with `|| RC=$?`.

**20. The "session crossed the TLS port" assertion was a
tautology.** It grepped the proxy log for `secure SPICE listener
bound`, which `rust/kerbside-proxy/src/listen.rs` emits once at
*bind* time; its `insecure SPICE listener bound` sibling fires
identically for the plaintext port. So the check passed whenever
the proxy had merely started, including runs where the client
never connected — and it sat outside the branch that establishes
a session, so it passed when ryll never came up at all.
Confirmed against the real artifact from the green run: both
lines appear at startup, twice, because the daemon restarted the
proxy.

This mattered more than an ordinary weak assertion, because the
silent-plaintext-fallback failure is the one thing the entire
demo is arranged to make visible, and the `PASS:` line read as
coverage of exactly that.

Replaced with a real per-session oracle. Both ports are
published on loopback, so the honest question is what the host's
socket table says while the session is live: zero established
sockets on the plaintext port is the property under test, a
non-zero count on the TLS port is the corroborating positive,
and both port numbers come from the `.vv` itself rather than
being hardcoded. A separate assertion now checks a genuinely
per-connection log line (`hypervisor connection successful`,
which carries a `session_id`), and the old check is gone rather
than renamed.

**21. `install-docker.sh`'s idempotence short-circuit skipped
the configuration it exists to apply.** The early `exit 0` on an
already-usable docker also skipped the systemd drop-in and
`~/.docker/config.json` — the two things an improved runner
image would *not* bring with it. So the lane would have broken
on the day the runner image grew docker, which is the day this
script was supposed to become harmless. It failed safe rather
than silently, but the diagnosis would have pointed at the
probe. Now only the apt install is gated, and the daemon restart
is guarded on the drop-in changing so the unconditional path is
not gratuitously bouncing the daemon.

The rest were accepted as written, and are worth recording
because two of them are latent versions of bugs this plan
already documents:

- **The redaction covered only `*.vv`.** Widened to every file
  under the artifact directory before the residual check, so a
  token reaching a `.log` is now *fixed* rather than merely
  reported. Retested against the leaked artifact with a token
  planted in `logs/kerbside.log`.
- **The port-collision check could not name a holder.**
  `ss -tlnp` only fills the process column for the caller's own
  sockets, so for the collision the comment names — a system VNC
  service on 5900 — it added nothing over the daemon's error.
  This is not hypothetical: it is exactly what happened on the
  development host, where the 5900 holder could not be
  identified. Now `sudo -n ss` with an unprivileged fallback.
  Worse, `netstat` is absent from a Debian 12 base, so the
  fallback branch reported every port free when neither tool
  existed; that now warns explicitly.
- **The shebang regex anchored on end-of-line**, so
  `#!/bin/sh -e` and `#!/bin/bash -eu` were not selected. No such
  file exists in the tree today, which makes this the third
  member of finding 17's family: a selection bug that reads as
  coverage. Now `\b(sh|bash|dash|ksh)\b`, verified to accept the
  argument-carrying forms and still reject `#!/usr/bin/python3`.
- **Two `docker compose` calls discarded both streams under
  `set -e`**, and nothing asserted `spice-target` came back after
  the restart, so a half-dead backend would have been reported as
  a mint-guard failure — finding 14's wrong-attribution shape
  again. Both routed through `bad`, with a wait-and-assert after
  the restart.
- **The runner-size comment implied a cargo-free lane.** It is
  not: ryll is a cargo release build. The comment now says so and
  carries the measured timings, which also answer the reviewer's
  caching suggestion — at 4.5 minutes end to end there is nothing
  to optimise.
- **The default PyPI build path is tested by nothing.** Already
  decision 4 and already Future work; the gap is now
  cross-referenced from `demo/docker-compose.yml` at the point
  someone would change the glue that breaks it.
- `demo-probe.yml` being a TEMPORARY workflow merging to develop
  was raised, and the reviewer's preferred resolution — delete it
  in this pull request, since `demo-compose.yml` runs the probe as
  its second step — is what had already been done.

### What testing the review fixes then found

Running the rewritten script locally, in both directions, turned
up three more of the same family. That is the argument for
actually running it rather than reasoning about it.

**22. `set -o pipefail` killed the diagnostic block, in exactly
the case it exists for.** `PROXY_REASON="$(docker compose logs
... | grep -E ... | tail -3)"` looks safe because `tail` succeeds,
but under `pipefail` a `grep` that matches nothing makes the
pipeline exit 1, the assignment fails, and `set -e` ends the
script. So when the proxy had **no** matching reason to report,
the block died instead of falling through to print the ryll log —
losing the only evidence available precisely when the proxy had
none to offer. Found because a local run hit it. Fixed with
`|| true`, and the same trap is now called out in a comment on
`count_proxy_log`.

**23. Two assertions counted the proxy log's whole history, so
they could pass on an earlier session's lines.** `docker compose
logs` returns everything the container has ever printed. A
re-run against a stack that had already served a session
reported `PASS: the proxy relayed 8 channel(s)` while that run
established no session at all. A single-run CI lane against a
fresh stack hides this completely, which is what makes it worth
recording: the assertion was sound only by accident of the
harness. Both are now before/after deltas, and the healthy run
correctly reports 4 channels — main, display, cursor, inputs —
rather than a cumulative 8.

**24. A long `WORKDIR` makes ryll's control socket unbindable,
and the script blamed the session for it.** Unix socket paths cap
at about 108 bytes. A scratch-directory override pushed
`ryll-demo.sock` to 127, the bind failed, and `lane-assert.sh`
reported `ryll never created its control socket` for a session
whose log showed a complete SPICE handshake with display updates
and ping/pong traffic. CI's default path is short so this cannot
bite there, but it cost a debugging cycle here and would cost the
next person one too. There is now an explicit length check that
fails early and names the real cause.

Evidence from the local runs, which is what these fixes are worth
rather than the reasoning behind them:

- healthy: 11 assertions pass, and the port oracle reports
  `established sockets: 5900=12, 5901=0` — the same 12-on-TLS,
  0-on-plaintext split phase 3 measured by hand with
  `remote-viewer`.
- the oracle is not blind: holding one plaintext connection open
  on 5901 takes the count to 3, so the assertion fails.
- the previously-dead hang branch fires:
  `FAIL: a session hung for 1s with the backend stopped` — and
  the script now **runs to completion and prints a verdict**
  instead of dying at exit 124 with no output.
- the delta assertions fail honestly on a run with no session,
  where the absolute counts had passed.
- the socket-length guard fails early with the real cause, and a
  short `WORKDIR` still passes.
- teardown leaves ports 13002/5900/5901 free and `sources.yaml`
  restored by the EXIT trap.

## Decisions

**1. Advisory, path-filtered — not a required check.** Carried
forward from the draft, and confirmed: `docs/testing.md:73`
establishes the precedent with `rust.yml`, and the five
required checks are gate jobs whose names are bound to the
develop ruleset that `tools/check-required-checks.sh` validates
against. Renaming or adding one blocks every merge in the
repository. Not worth it for a demo path.

Path filter:

```
paths:
  - 'demo/**'
  - 'kerbside/migrations/**'
  - 'pyproject.toml'
  - 'docs/installation.md'
  - 'tools/demo/**'
  - '.github/workflows/demo-compose.yml'
```

`docs/installation.md` is deliberately included: the document
and the thing it documents should not be able to drift without
the lane running. `kerbside/migrations/**` and `pyproject.toml`
are there because phase 1's packaging is the part most likely
to break silently — and finding 4 adds a second reason, since
`pyproject.toml` is where the proxy floor lives.

**2. Probe the runner before writing the lane.** Step 4a is a
throwaway workflow that answers finding 5's three questions and
nothing else. It is the cheapest possible way to learn whether
this phase's shape is even viable, and the alternative is
discovering it from a red lane whose failure could be any of a
dozen things. If the probe says the runners cannot build
containers or cannot reach an index from inside a build, **stop
and re-plan** — do not work around it in the lane.

**3. Do not build the Rust proxy in the lane.** Per finding 4,
`pip` resolves a develop-tracking dev wheel on its own. This
drops a cold `cargo build --release` from every run.

The residual case is a pull request that changes
`kerbside/rpc/kerbside.proto` *and* one of this lane's filtered
paths, before `dev-proxy-wheel.yml` has published a wheel for
the new proto. Then the checkout daemon and the PyPI dev wheel
disagree, and `proxy_supervisor.check_contract()` refuses to
launch — `get_binary_contract_hash()` treats a mismatch and an
unanswerable `--contract-hash` alike, so this fails at startup
rather than subtly. The lane will go red for a reason that is
not the demo's fault.

That is an acceptable trade and should be *documented in the
workflow comment* rather than engineered around: the failure is
loud, correctly diagnosed by the daemon's own error, and the
fix is to let the dev wheel publish. Building the proxy in the
lane to avoid it would cost several minutes on every unrelated
run. If it turns out to bite in practice, the fallback is
already written and tested — one step calling
`tools/direct-qemu/install-proxy-wheel.sh --venv`, exactly as
`direct-qemu-functional.yml:157-161` does.

**4. Test `KERBSIDE_SOURCE=/src`, not the PyPI default.**
Carried forward. A lane exercising the default would test the
last release rather than the pull request. Worth restating that
the default genuinely is PyPI again as of `72c5aca` — phase 3
had temporarily reversed it — so this is now a real divergence
between what the lane builds and what a user builds, and the
workflow comment should say so and say why.

**5. shellcheck goes in `tox -e shellcheck`, called from
`sanity_checks` — not in this lane.** This reverses the
draft's stated preference, and it is the decision most likely
to be argued with.

The draft preferred adding a shellcheck step to the new lane on
the grounds that `sanity_checks` is a gate job feeding a
required check. But this lane is path-filtered to `demo/**` and
friends, so a change to `tools/` — 40-odd scripts, the majority
of the shell in the repository — would not trigger it. That
gives the *appearance* of CI shellcheck coverage while leaving
most of the actual shell unchecked, which is worse than the
status quo because it is misleading.

`sanity_checks` is the right home, and the caution about it
does not apply: `.claude/CLAUDE.md` warns against **renaming**
gate jobs, because the ruleset binds their display names.
Adding a step inside `sanity_checks` renames nothing. And the
path filter reaches: `check_paths`' `code` filter
(`functional-tests.yml:81-88`) is `'**'` minus review marks and
`docs/**`, so `demo/**` and `tools/**` both count as code and
`sanity_checks` runs for them.

Match the pre-commit hook's scope exactly (`^(tools|demo)/`,
`-x`, `types_or: [sh, bash, shell]`) so the two cannot disagree
about what passes.

**6. Copy `functional-tests.yml:629` for ryll, not
`direct-qemu-functional.yml:143`.** Per finding 2. This lane
asserts that a SPICE session is established, not what it
renders, so `--no-default-features -p ryll` without
`digest-decode` is correct and cheaper.

**7. Runner size `l`, and never an unsized `vm` label.** A qemu
TCG guest, a MariaDB, a container build and a ryll build on one
runner. `rust.yml:36-38` records that an unsized `vm` label
defaults to `xs` (1 core / 2 GB) in private-ci, which will not
do. `l` rather than `rust.yml`'s `xl` because decision 3
removes the cargo release build that justifies `xl`; the probe
in step 4a should report timings so 4b can revisit this with
evidence rather than guesswork.

**8. `no_proxy: 127.0.0.1,localhost` at job level, and a
concurrency group.** Per `direct-qemu-functional.yml:88-92`
and `:30-32`. Loopback traffic to the published demo ports must
bypass the squid or it returns 503 `ERR_CONNECT_FAIL`. This has
bitten two lanes already; assume it bites here.

**9. Install docker in the lane, from Docker's own apt
repository, rather than waiting on the runner image.** Forced by
finding 16, and chosen over the two alternatives:

- *baking it into the private-ci debian-12 image* would keep the
  lane shorter and would benefit any future container lane, but
  it is outside this repository, so the phase would block on an
  image change, and the lane's dependency on it would be
  invisible to anyone reading the workflow;
- *pointing at some other runner label* was not available:
  nothing in `.github/workflows/` uses docker, so no label is
  known to have it, and `gh api .../actions/runners` returns
  nothing at this permission level.

Installing in the lane keeps the phase self-contained and
reviewable in one pull request, and it exercises the same
install path `demo/README.md` gives a human — which it did not
when this was written; the README stated the requirement and no
install path, so it now names Docker's repository and the
bookworm trap explicitly.

Measured cost is **12 seconds**, not the "about a minute" first
claimed here, out of a 4.5 minute lane.
`tools/demo/install-docker.sh` skips the apt install when the
server is already 23.0+ with the compose plugin, so if the runner
image ever grows one that cost disappears with nobody having to
remember to remove the step. Only the *install* is skipped: the
proxy configuration and the socket permission run every time,
because they are the parts a better image would not bring with
it, and the daemon restart is guarded on the drop-in's content
actually changing.

The script also configures two proxy paths that the runner's own
environment does not cover, because neither is inherited: a
systemd drop-in, without which the daemon cannot pull a base
image through the squid, and `~/.docker/config.json` `proxies`,
without which a build cannot reach PyPI. Both are conditional on
`http_proxy` being set, both are printed, and `probe-runner.sh`
still tests reachability independently rather than assuming the
configuration worked — so if a runner turns out to have direct
egress, the evidence to delete that block will be in the probe
output.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | **Probe first; this step decides whether the rest of the phase is viable.** Write `tools/demo/probe-runner.sh`, modelled closely on `tools/direct-qemu/probe-runner.sh` (read it — same output style, same "print diagnostics, exit non-zero only on a hard blocker" contract). It must report: `docker version` client and server, whether the daemon is reachable as the runner user, whether the server is 23.0+ (`demo/Dockerfile` requires it by name for `RUN --mount=type=bind`), `docker compose version`, whether TCP 5900/5901/13002 are already bound on 127.0.0.1 (finding 10 — the Docker daemon's own bind error does not name the holder, so this must), and whether a throwaway `docker build` that runs `pip download --no-deps kerbside-proxy` can reach an index from *inside* the build (finding 5 — the build inherits neither the runner's `http_proxy` nor `PIP_INDEX_URL`). Exit non-zero for a missing or too-old daemon, or no index reachability; print-and-continue for a bound port. Then add a temporary `.github/workflows/demo-probe.yml`, `workflow_dispatch` only, `runs-on: [self-hosted, vm, debian-12, l]`, that checks out and runs it. Run it, report the output verbatim, and **stop for review before step 4b** — the back brief gates here. Delete the temporary workflow in 4b, keeping the script. |
| 4b | high | sonnet | none | Write `.github/workflows/demo-compose.yml` per the Decisions above. High effort rather than medium because the runner facts from 4a have to be read and applied rather than followed from a recipe. Advisory, path-filtered per decision 1; `fetch-depth: 0` (finding 9); `no_proxy` and concurrency per decision 8; `runs-on` per decision 7. **No cargo step** (decision 3) — and put decision 3's residual-case reasoning in a comment, so the next person to see a contract-hash failure knows it is expected and why. Steps: probe, then `docker compose config` and `docker compose build` as a fast schema and Dockerfile check ahead of anything slow, then `tools/demo/lane-up.sh`, then `tools/demo/lane-assert.sh`. Finish with an `if: always()` artifact upload of `docker compose logs` for all three services, the ryll output, and the `.vv` **with the `password=` line redacted** (finding 8: `kerbside/api.py:374` puts a live console token there, and CI artifacts are downloadable). Delete `.github/workflows/demo-probe.yml`. Keep workflow steps short — anything beyond a few lines belongs in `tools/demo/`. |
| 4c | high | sonnet | none | Write `tools/demo/lane-up.sh` and `tools/demo/lane-assert.sh`, modelled on `tools/direct-qemu/lane-up.sh` in structure and comment style. `lane-up.sh`: build with `KERBSIDE_SOURCE=/src`, `docker compose up -d --wait`, and on failure print `docker compose logs` for the service that is not healthy rather than a bare timeout. `lane-assert.sh`: run `demo/get-console.sh`; assert the `.vv` carries `tls-port=`, `host-subject=` and a `ca=` field holding an escaped PEM; drive ryll headless against the TLS port and assert the link handshake completes and the main channel opens (read `tools/direct-qemu/smoke-client.py` and reuse its assertion approach — do not write a new SPICE client driver); then both negative cases — `docker compose stop spice-target` must make a fresh console request fail in bounded time rather than hang, and appending a dummy `type: ovirt` entry to `demo/sources.yaml` must make `kerbside demo token` refuse and name the offending source. Restore `demo/sources.yaml` from an EXIT trap so a mid-assertion failure does not leave the tree dirty. Note that `demo/get-console.sh` already proves the TLS leg internally — it verifies the presented certificate against the CA embedded in the `.vv` — so do not reimplement that; assert the fields and let ryll cover the SPICE handshake. |
| 4d | medium | sonnet | none | Add shellcheck to CI per decision 5. New `[testenv:shellcheck]` in `tox.ini` mirroring the pre-commit hook's scope exactly — `^(tools|demo)/`, `-x`, shell files only — so the two cannot disagree; read `.pre-commit-config.yaml:21-27` for the authoritative settings. Call it from `sanity_checks` in `functional-tests.yml` (the job starts at line 94) as a step alongside the existing flake8 invocation. Do not rename any job. Demonstrate it fails: introduce a deliberate shellcheck violation in a `demo/` script, show the failure, revert it. |
| 4e | low | haiku | none | Register the lane. One row in `docs/testing.md`'s workflow table (model it on the `rust.yml` advisory row at line 73) and one bullet in the "Neither tier" list in `.claude/CLAUDE.md`'s CI Workflows section. Do not restructure either document. |
| 4f | high | sonnet | worktree | Prove the lane fails when it should — the only evidence that matters for a new lane. On a scratch branch, break the demo four ways in sequence and confirm the lane goes red each time *for the right reason*, capturing the message: (i) point `demo/sources.yaml` at a wrong `insecure_port`; (ii) set `KERBSIDE_PROXY_HOST_SUBJECT` to a mismatched string, which must fail the TLS assertion rather than silently falling back to plaintext; (iii) revert phase 1's packaging so `kerbside db upgrade` cannot find its migrations; (iv) break the compose schema, which must fail at `docker compose config` in seconds rather than after a full image build. Worktree isolation because this step deliberately breaks the tree. Report the four messages. Do not merge the scratch branch. |

## Risks and mitigations

**The runners cannot build containers, or a build cannot reach
a package index.** The likeliest failure, and the whole reason
for step 4a. Mitigation: the probe answers it before any lane
is written, and decision 2 says to stop and re-plan rather than
work around it. Checked by the management session reading 4a's
verbatim output, not a summary of it.

**The lane goes red for a proto/dev-wheel skew rather than a
demo fault.** Accepted per decision 3. Mitigation: the workflow
comment must explain the failure mode, and the daemon's own
refusal message already names the cause. If it recurs, the
fallback step is specified in decision 3 and needs no new
design.

**Something on a runner holds 5900.** Finding 10. Mitigation:
the probe reports bound ports by name so the diagnosis is not
the Docker daemon's uninformative bind error. Fixing the port
choice is explicitly out of scope; if the probe shows a
collision, that becomes a phase 3 follow-up with its own
documentation consequences.

**The lane is slow enough that people stop reading it.** A
container build plus a stack start plus ryll. Mitigation:
decision 3 removes the cargo build; 4b puts `docker compose
config` first so schema errors fail in seconds; the path filter
keeps it off unrelated pull requests. 4a reports timings so
decision 7's runner size can be revisited with evidence.

**shellcheck in `sanity_checks` slows a gate job.** It is a
lint pass over some fifty small files; seconds. Mitigation: if
4d finds otherwise, move it to its own job rather than
weakening the scope, and say so.

## Definition of done

Falsifiable, each checkable by running something. Outcome
recorded after each.

Everything that can be verified without a CI runner was
verified against a real stack, from a throwaway clone (a
`git worktree` cannot build `KERBSIDE_SOURCE=/src` — `.git` is
a file there, and setuptools_scm fails with
`LookupError: setuptools-scm was unable to detect version for
/src`, exactly as `demo/Dockerfile` warns). ryll was built in
Docker, `--no-default-features` and no `digest-decode`, to keep
the host's Rust toolchains untouched.

- [x] `tools/demo/lane-up.sh` brings the stack to healthy
      against the checkout, with **no cargo step**. Verified,
      and it confirms decision 3 directly: the image installs
      `kerbside 0.5.1.dev4+g7e1f2fc` from the checkout and
      `kerbside-proxy 0.5.1.dev1` from PyPI, so the daemon and
      the binary both track develop with nothing built locally.
- [x] `tools/demo/lane-assert.sh` passes all 11 assertions: the
      three `.vv` fields; a ryll SPICE session, zero established
      sockets on the plaintext port, at least one on the TLS
      port, and a per-connection relay log line; a bounded
      backend failure (8s, matching phase 3's ~9s by hand) and
      the proxy logging that failure; `spice-target` returning
      after the restart; and the mint guard refusing and naming
      the offending source. Was 8 before review — findings 19,
      20 and the item-9 fixes replaced a tautology with three
      real checks and added the post-restart one.
- [x] `smoke-client.py` works against a ryll built without
      `digest-decode` — it uses only `hello`, `status` and
      `screenshot`, no digest verbs. Confirmed by reading it
      and then by running it.
- [x] The lane is red, with a message naming the actual cause,
      for each of 4f's four deliberate breakages:
      - wrong `insecure_port`: `ryll never created its control
        socket`, plus the proxy's own reason —
        `hypervisor connection failed ... error=Connection
        refused (os error 111)`.
      - mismatched host subject: `the certificate subject is
        {'C': 'US', 'O': 'Kerbside CI', 'CN': 'kerbside-ci'},
        but the .vv says to expect 'C=US,O=Wrong
        Org,CN=wrong-cn'` — and it did not fall back to
        plaintext.
      - migrations hidden from the build: `Database upgrade
        failed: Path doesn't exist:
        /usr/local/lib/python3.13/site-packages/kerbside/migrations`.
      - broken compose schema: `ERROR:
        demo/docker-compose.yml is not valid`, in seconds,
        before any image build.
- [x] `tox -e shellcheck` passes on a clean tree — 45 scripts
      in 1.3s — and was demonstrated to fail (`FAIL code 1`) on
      a real violation in `demo/demo-env.sh`. Worth recording
      how the first attempt at that demonstration went: a
      deliberate `echo $foo` after `foo="bar"` did **not**
      fail, because shellcheck suppresses SC2086 for provably
      safe literals. The tool was right and the test was
      wrong; the violation used is an unquoted command
      substitution.
- [x] `tox -e shellcheck`'s scope matches the
      `.pre-commit-config.yaml` hook: `tools/` and `demo/`,
      shell only, `-x`. Selection is by extension **or**
      shebang, because `demo/kerbside-demo-env` and
      `tools/run-tempest-tests` are shell without a `.sh`
      suffix and an extension-only match would skip them
      silently. Selection also includes untracked-but-not-ignored
      files, per finding 17, so a script written and not yet
      added is still checked.
- [x] The lane installs its own docker (decision 9), the script
      is idempotent against an already-usable daemon, and the
      `~/.docker/config.json` merge was tested to preserve a
      pre-existing `auths` block rather than overwrite it.
- [x] No change to any gate job name; only a step added inside
      `sanity_checks`.
- [x] `docs/testing.md` and `.claude/CLAUDE.md` both list the
      lane.
- [x] No cargo or Rust step in the workflow, and the residual
      contract-skew case is explained in a comment.

These needed a push and a pull request, and are now settled by
runs on pull request #336:

- [x] `tools/demo/probe-runner.sh` runs on a private-CI runner
      and its output is recorded here, including the Docker
      server version and whether a build reached an index.
      Locally it also correctly reported the real 5900 collision
      on the development host. Both runs are recorded as finding
      16: the first stopped at a missing daemon, the second
      answered everything.
- [x] `tools/demo/install-docker.sh` succeeds on a private-CI
      runner — which is also the only test that the squid in
      front of these runners permits `download.docker.com`. It
      does. Locally only the idempotence path is exercisable,
      and it is: the script exits 0 without touching a host that
      already has docker.
- [x] `.github/workflows/demo-probe.yml` deleted, its output
      recorded in finding 16 first.
- [x] The lane is green on a pull request touching `demo/`. All
      13 steps ran — none skipped — in 4.5 minutes end to end.
      The first such run is *not* what this tick rests on: per
      findings 19 and 20, one of its eight assertions was a
      tautology and two more were reached only because ryll
      happened to exit 0. It rests on the run after the review
      fixes, where all 11 rewritten assertions passed.
- [x] The `/src` build pairs a checkout daemon with a PyPI dev
      proxy wheel, with no cargo build of the proxy, exactly as
      decision 3 argued: `kerbside 0.5.1.dev3+gdfd1719.d20260817`
      with `kerbside-proxy 0.5.1.dev1`. (ryll is still built from
      source; the lane is not cargo-free.)
- [x] A real artifact inspected for a live console token:
      `password=REDACTED` in both `.vv` files. That inspection
      is what turned up finding 18 — the database password the
      same artifact *did* leak — so this item is ticked on the
      strength of having actually downloaded and grepped the
      artifact rather than trusting the step.
- [x] `tools/check-required-checks.sh` passes against the live
      ruleset — it runs as the "Verify required-check names
      against the exported ruleset" step inside `sanity_checks`,
      which is green.

- [x] The redaction fix in finding 18 confirmed on a real run:
      `redacted a generated database password in
      /tmp/kerbside-demo-lane/logs/db.log`, then `no secrets
      remain`. Confirmed by **downloading that run's artifact and
      grepping it**, rather than by trusting the step's own
      output: no unredacted `^password=` line and no unredacted
      `GENERATED ROOT PASSWORD` anywhere in it.
- [x] A green run of the **rewritten** assertions (findings 19,
      20, 22, 23 and the review's item 9). All 11 pass on a
      runner, and the flakiness risk in the port oracle did not
      materialise: CI reports `established sockets: 5900=12,
      5901=0`, byte-identical to the local run and to the split
      phase 3 measured by hand with `remote-viewer`. The relay
      delta reports 4 channels rather than a cumulative count,
      confirming finding 23's fix against a fresh stack.

Still outstanding, and deliberately left for phase 5:

- [ ] The lane does not run on a pull request touching only
      `docs/` outside `installation.md` — demonstrated, not
      assumed. This needs a docs-only pull request, which this
      phase has no reason to raise on its own. Phase 5 edits
      `docs/installation.md`, which is *in* the filter, so it is
      the natural place to demonstrate both directions: that lane
      fires for that page, and does not fire for a `docs/` change
      outside it.

## Registration

Recorded in the master plan's Execution table and in
`docs/plans/index.md`. `docs/plans/order.yml` is for master
plans only and is not touched — and this repository has no
`order.yml` at all, which the phase 3 plan also recorded.

## Future work

- **A periodic check of the PyPI default build.** Decision 4
  keeps it out of the per-PR lane. A nightly is the natural
  home, and it would have caught nothing so far, but it is the
  only thing that would notice the released package breaking
  the demo.
- **Move the demo off port 5900** (finding 10), or make the
  ports configurable. Needs a phase 3 file change and a
  documentation pass.
- **Publish a demo image**, so `compose up` does not build.
  Carried over from phase 3; still a release-process and
  image-signing question.

## Back brief

**Gate at the end of step 4a.** The probe output decides
whether this phase's shape survives. Do not start 4b until the
management session has read 4a's verbatim output and confirmed
the runners can build containers and reach a package index from
inside a build. This is cheap to check and expensive to
discover later.

Before implementation begins, the implementing session should
be able to state:

1. Why there is no cargo step, and what happens if a pull
   request changes the proto and `demo/` together.
2. Why shellcheck goes in `sanity_checks` rather than in this
   lane, and why that does not touch a gate job's name.
3. Which file to copy the ryll build from, and why it is not
   the direct-qemu lane.
4. Why the lane needs `fetch-depth: 0`.
