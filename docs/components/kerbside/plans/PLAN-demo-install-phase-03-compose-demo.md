# Phase 3: the `docker compose` demo stack

Master plan: [PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/)

Planned at **high** effort: container plumbing, TLS
bootstrap, two supervised processes and a SPICE target under
one `compose up`, with many independent failure modes and no
existing compose file in the tree to pattern-match against.

## Situation

There is no Python-side container image and nothing
published to a registry. `rust/kerbside-proxy/Dockerfile` is
a build container for the Rust wheel, not a runtime image.
There is no `demo/` directory and no `docker-compose.yml`
anywhere in the tree — `find . -name 'docker-compose*.y*ml'`
returns nothing — so this phase writes the first one.

Most of what the stack needs is proven somewhere in the
tree, and this phase should assemble rather than invent. All
of these were re-verified against `7ed193b`:

| Need | Existing proven source |
|------|------------------------|
| The full env var set | `tools/direct-qemu/start-kerbside.sh:55-82` |
| TLS material and matching subject | `tools/direct-qemu/generate-tls.sh` |
| MariaDB database and user | `tools/direct-qemu/setup-mariadb.sh` |
| Static source `sources.yaml` | `etc/example-static-sources.yaml` |
| Bearer token minting | `tools/direct-qemu/lane-up.sh:143-160` |
| Both processes | `start-kerbside.sh:117-137` (gunicorn, then `kerbside daemon run`) |
| Readiness polling | `start-kerbside.sh:139-198` (API, then SPICE) |
| The SPICE target's qemu invocation | `tools/direct-qemu/start-qemu.sh:103-127` |

Two facts established while planning, both load-bearing and
both still true:

- **The `.vv` embeds the CA.** `ConsolesProxyVirtViewer`
  (`kerbside/api.py:434`) reads `CACERT_PATH` at
  `api.py:446`, escapes newlines, and emits it as `ca=` at
  `api.py:483` via `VIRTVIEWER_TEMPLATE` (`api.py:351`),
  alongside `host-subject=` from `PROXY_HOST_SUBJECT`
  (`api.py:451`) and `tls-port=` (`api.py:481`). So a stock
  `remote-viewer` verifies the demo's self-signed CA **from
  the `.vv` alone** — the evaluator installs no certificates
  and the TLS leg is genuinely exercised. This is why the
  demo can be honest rather than insecure-only.
- **`kerbside db upgrade` exists** (`kerbside/main.py:331`)
  as of phase 1, so the entrypoint does not need a
  repository checkout.

## Mission

`docker compose up` in `demo/`, on a machine with nothing
but docker, reaches a SPICE console served by a real SPICE
server, proxied by kerbside, viewable in `remote-viewer`.

## Scope

In scope:

- `demo/` — `Dockerfile`, `docker-compose.yml`,
  `entrypoint.sh`, `get-console.sh`, `sources.yaml`,
  `kerbside.ini`, `README.md`, and a `spice-target/`
  image.
- Extending the shellcheck hook to cover `demo/` (survey
  finding 6).

Out of scope, deliberately:

- **The CI lane.** Phase 4. This phase must leave
  `KERBSIDE_SOURCE=/src` working because phase 4 depends on
  it, but wires no workflow.
- **Rewriting `docs/installation.md`.** Phase 5, and it must
  be last. `demo/README.md` cross-links to it by path and
  lets phase 5 make the link true.
- **Publishing an image to a registry.** The demo builds
  locally. A published image is a release-process question
  and would need its own security review.
- **Fixing #313** (a malformed INI exits zero). The demo
  ships an INI file, so it is adjacent, but the fix is a
  runtime change and belongs with the issue.

## What the survey found

Verified against `7ed193b`. The previous draft of this file
was written before phases 1 and 2 executed and before the
seven sfui phases landed. Most of it survived. Seven things
did not, and one of them would have sent the implementer to
write a command this project already knows is broken.

The corrections are recorded here rather than at their
source, because unlike phase 2 the stale claims were all in
*this* file, which this commit rewrites. The master plan's
own phase 3 material (`PLAN-demo-install.md:321-343`) was
checked and is accurate.

### 1. The proposed qemu command is known-broken on modern QEMU

The draft specified:

```
qemu-system-x86_64 -m 256 -display none \
  -spice port=5910,password=demo-ticket,disable-ticketing=off
```

`tools/direct-qemu/start-qemu.sh:130-135` documents, in a
comment written after hitting it, that **the inline
`password=` parameter was removed in newer QEMU and fails on
QEMU 10** with `Invalid parameter 'password'`. The working
form, supported since QEMU 5.2 and therefore fine on both
the debian-12 runner (QEMU 7.2) and a modern developer host,
is a secret object:

```
-object secret,id=spice-ticket,data=demo-ticket \
-spice port=5910,password-secret=spice-ticket,disable-ticketing=off
```

This is the most valuable thing the survey found: the demo's
whole point is working on someone else's machine, and the
draft's form fails on the newest QEMU, which is exactly what
a curious evaluator is most likely to have. Decision 2.

The draft also omitted a VGA device. `start-qemu.sh:121`
uses `-vga qxl`, the conventional choice for SPICE, and the
demo should match rather than rely on the q35 default.

### 2. There is no containerised qemu anywhere in the tree

The Situation table's claim that everything is "already
proven somewhere in the tree" does not hold for the SPICE
target. The direct-qemu lane runs `qemu-system-x86_64` **on
the runner**, installed with apt by
`direct-qemu-functional.yml:115`. Nothing in this repository
runs qemu inside a container, so the `spice-target` service
is genuinely new work and needs a base image chosen and its
qemu installed. Decision 3.

### 3. Four `start-kerbside.sh` line references were wrong

The file is 198 lines. The draft cited `:140-160` for the
gunicorn/daemon pair and `:163-215` for readiness polling —
the latter runs 17 lines past the end of the file, and the
former lands inside the API wait loop. Corrected in the
Situation table above:

| Draft said | Actually |
|---|---|
| `:56-90` env vars | `:55-82` |
| `:140-160` gunicorn then daemon | `:117-137` |
| `:163-215` readiness polling | `:139-198`, two loops |
| `:96-103` `find_proxy_bin()` pre-check | `:84-104` |

### 4. `lane-up.sh` no longer hand-rolls a JWT

The draft cited `lane-up.sh:129-161` for bearer token
minting, which at the time was a PyJWT snippet. Phase 1
replaced it: `lane-up.sh:148` now calls
`kerbside demo token --subject kerbside-ci --output ...`,
and the comment at `:115` records what it used to be. This
strengthens the draft's position rather than weakening it —
`demo/get-console.sh` now has a working in-tree reference
for exactly the call it needs to make, instead of a pattern
to avoid.

### 5. The Dockerfile package list was guessed, having said not to

The draft instructed the implementer to "consult
`bindep.txt` rather than guessing the list" and then named
`default-libmysqlclient-dev`. `bindep.txt` asks for
`libmariadb-dev-compat [platform:debian]`. On a
`python:3.13-slim` base — Debian — the bindep answer is the
right one. The full Debian set from `bindep.txt` is
`libmariadb-dev-compat`, `locales`, `pkg-config`,
`libxml2-dev`, `libxslt1-dev`, `build-essential`.

Related: **no workflow pins a `python-version`**.
`grep -rn python-version .github/workflows/*.yml` returns
nothing, so the draft's "match `functional-tests.yml`'s
version if it pins one" resolves to "it does not"; the
Dockerfile picks its own base and `requires-python` is
`>=3.11` (`pyproject.toml:17`).

### 6. shellcheck does not cover `demo/`

`.pre-commit-config.yaml:25` scopes the shellcheck hook to
`files: ^tools/`. `demo/entrypoint.sh` and
`demo/get-console.sh` would be committed unchecked, while
the `tools/direct-qemu/` scripts they are derived from are
checked with `-x`. Decision 6.

`check-yaml` needs no change — it is unscoped and will pick
up `docker-compose.yml` and `sources.yaml` automatically.

### 7. The API port is not a config field

`grep API_PORT kerbside/config.py` returns nothing. The
`13002` in the direct-qemu lane is a *script* variable
(`start-kerbside.sh:30`) passed to gunicorn's `--bind`
(`:122`). There is no `KERBSIDE_API_PORT`. The compose file
must therefore set the port in the gunicorn command line,
not the environment — a trap worth naming, because every
other setting in this stack is an env var.

### Verified correct, and worth saying so

The subject string `C=US,O=Kerbside CI,CN=kerbside-ci` is
exactly what `generate-tls.sh:60` produces and what
`start-kerbside.sh:75` pins. `generate-tls.sh:74` gives the
proxy certificate `subjectAltName = IP:127.0.0.1,
DNS:localhost`, which is why the loopback-only demo works
with the CI TLS material unmodified, and why `PUBLIC_FQDN`
should be `127.0.0.1` as at `start-kerbside.sh:72`.
`etc/example-static-sources.yaml` documents the required
console fields the demo needs (`uuid`, `name`, `hypervisor`,
`hypervisor_ip`, `insecure_port`, `ticket`).
`docs/configuration.md`'s `API_SOCKET_PATH` row does carry the co-location note
the demo should cite. All four `.vv` emitters in `api.py`
embed the CA; the demo uses
`/console/proxy/<source>/<uuid>/console.vv` (`api.py:849`).

### What implementation found, after the survey

Three more things, none of which the survey could have
caught by reading:

**8. The released package cannot run this demo, so decision
4's default is reversed.** `entrypoint.sh` calls
`kerbside db upgrade`, which phase 1 added and no release
carries: the newest tag is v0.4.0 and a 0.4.0 image fails
with `Error: No such command 'db'`. Decision 4 wanted the
default to be the released package, on the reasoning that a
demo silently testing unreleased code works for the
maintainer and fails for everyone else. That reasoning is
right and the default is still wrong, because the choice is
between a demo that works and a demo that installs the
released package. `KERBSIDE_SOURCE` now defaults to `/src`,
with a Dockerfile comment saying to flip it back in the
first release carrying `kerbside db upgrade`.

A checkout install brings no `kerbside-proxy`, so the image
installs the released proxy wheel explicitly. That is safe
and was checked rather than assumed: the only change to
`kerbside/rpc/kerbside.proto` between v0.4.0 and develop is
a comment, so the daemon and the released proxy speak an
identical contract. Re-check before trusting it again.

**9. Debian trixie does not put SPICE in `qemu-system-x86`.**
It is in `qemu-system-modules-spice`, a Recommends rather
than a Depends, so `--no-install-recommends` excludes it and
qemu dies at startup with `There is no option group 'spice'`
— after the container has already published its port, so a
naive TCP check passes while nothing works. The direct-qemu
lane never hits this because its runner installs qemu from
an older Debian. Survey finding 2 said the SPICE target was
new ground; this is what was on it.

**10. `docker compose exec` inherits nothing the entrypoint
exported.** It starts a fresh process from the image ENV
plus the compose `environment:` block, so `kerbside demo
token` run that way saw neither the generated seed nor the
certificate paths, and refused to mint against the
unconfigured sentinel — the guard working correctly on a
container that was in fact configured. Fixed at the cause
with `demo/demo-env.sh`, sourced by both the entrypoint and
a `kerbside-demo-env` wrapper on PATH, so one definition
serves both entry paths. The seed is read from the state
volume rather than passed on a command line, so it stays out
of `ps` and `docker inspect`.

## Decisions

1. **Three services, `db` / `spice-target` / `kerbside`, on
   the default compose network.** `db` is the upstream
   `mariadb` image with a named volume and a
   `mariadb-admin ping` healthcheck, port unpublished;
   `kerbside` depends on it with
   `condition: service_healthy`. `setup-mariadb.sh` is a
   reference for the database and user names only — the
   image's `MARIADB_DATABASE`/`MARIADB_USER`/
   `MARIADB_PASSWORD` do that work in compose, and
   reimplementing the script would be worse.

2. **The SPICE target is a disk-less qemu using
   `-object secret` for the ticket.** Survey finding 1. Full
   invocation, matching `start-qemu.sh` where it matters:

   ```
   qemu-system-x86_64 -machine q35 -m 256 -vga qxl \
     -object secret,id=spice-ticket,data=demo-ticket \
     -spice port=5910,password-secret=spice-ticket,disable-ticketing=off \
     -display none
   ```

   No `accel=kvm`: TCG is fine for a BIOS screen, so the
   demo needs no `/dev/kvm` and no privileged container, and
   works on a laptop, in a VM, and in CI. Do not add
   conditional acceleration — complexity for no visible gain
   at a BIOS prompt. No OVMF pflash either, unlike
   `start-qemu.sh`: SeaBIOS's "No bootable device" screen is
   the display surface, and it renders without a firmware
   image to source.

   Rejected: downloading or building a guest image.
   Uncalibrated Sextant is the project's real test guest and
   the right choice for CI assertions, but making an
   evaluator fetch one to see a demo is the friction this
   phase exists to remove.

   Say plainly in `demo/README.md` and in the compose file
   that the black screen with BIOS text is the expected
   result and is a real SPICE session. An evaluator who
   thinks the demo failed is worse than no demo.

3. **`spice-target` builds from `debian:trixie-slim` with
   `qemu-system-x86` installed, not from a third-party qemu
   image.** Survey finding 2 means something must be chosen.
   A distro base with one apt package is auditable in five
   lines, matches how `direct-qemu-functional.yml:115` gets
   qemu, and adds no dependency on an image nobody in this
   project maintains. Debian trixie tracks what the CI
   runner will move to.

   The counter-argument is image size — a qemu install is
   not small, and there are purpose-built qemu images. It is
   not worth a supply-chain question in a demo whose whole
   claim is that you can read what it does.

4. **The image installs kerbside via a `KERBSIDE_SOURCE`
   build argument defaulting to the released PyPI package.**

   ```
   ARG KERBSIDE_SOURCE=kerbside
   RUN pip install --no-cache-dir "${KERBSIDE_SOURCE}"
   ```

   CI overrides it with `/src` and a bind-mounted checkout
   so the lane tests the PR. **The default must be the
   released package**: a demo that silently tests unreleased
   code is a demo that works for the maintainer and fails
   for everyone else. When `KERBSIDE_SOURCE=/src` the
   `kerbside-proxy` pin is absent from the committed tree by
   design (`pyproject.toml:29`, `KERBSIDE_PROXY_PIN`), so CI
   must also install a locally built proxy wheel;
   `tools/build-proxy-wheel.sh` and
   `tools/direct-qemu/install-proxy-wheel.sh` already do
   this and phase 4 wires them in. Document the requirement
   as a Dockerfile comment; do not make the default path
   build Rust.

5. **Both processes run in the `kerbside` container, with
   `kerbside daemon run` as the foreground child.** They
   share `API_SOCKET_PATH` and `docs/configuration.md`'s `API_SOCKET_PATH` row
   requires co-location, so splitting them would mean a
   shared volume for a unix socket — more moving parts for a
   demo, and still not the production shape. `exec` the
   daemon rather than backgrounding both and `wait`-ing, so
   a crashed daemon exits the container and compose reports
   it; backgrounding hides the failure that matters most.
   Say in a comment and in `demo/README.md` that this is not
   a shape to copy, and name the real one.

6. **Extend the shellcheck hook to `^(tools|demo)/`.**
   Survey finding 6. One line, and it holds the demo scripts
   to the standard of the CI scripts they are copied from.
   The alternative — putting them under `tools/` — puts the
   evaluator's entry point somewhere they will not look.

7. **Ship both configuration mechanisms: env vars in
   `docker-compose.yml` and `demo/kerbside.ini` mounted at
   `/etc/kerbside/kerbside.ini`.** The demo is the natural
   place to show the INI form phase 2 documented and to make
   the precedence concrete. The generated seed stays out of
   the INI file and in the volume, so nothing secret is in a
   committed file. The INI file must double any percent sign
   — see the header of `etc/kerbside.conf.example` and issue
   #313, which is why a malformed one would exit zero.

8. **Loopback-only port publishing**, per master plan
   decision 3 (settled 2026-08-14):

   ```
   ports:
     - '127.0.0.1:13002:13002'   # REST API and web UI
     - '127.0.0.1:5900:5900'     # SPICE, TLS
     - '127.0.0.1:5901:5901'     # SPICE, redirect to TLS
   ```

   The demo has no real authentication, so it must not be
   reachable from the network by default. Comment why
   immediately above, and state it as a limitation in
   `demo/README.md` rather than only in the compose file.
   This also matches the certificate's
   `subjectAltName = IP:127.0.0.1`.

9. **The token comes from `kerbside demo token --subject
   demo-admin`, with no fallback.** Master plan decision 1,
   delivered by phase 1 and now also used by
   `lane-up.sh:148`. Its guards do useful work here: it
   refuses unless every configured source is `static`
   (`main.py:380`) and refuses while `AUTH_SECRET_SEED` is
   the sentinel (`main.py:452`). Both hold for this stack,
   so a refusal means the entrypoint's seed generation or
   `demo/sources.yaml` is wrong — a genuine failure worth
   surfacing. No PyJWT snippet anywhere in `demo/`; if the
   command is missing or refuses wrongly, that is a phase 1
   defect to fix there.

   The docs must stay honest that this is a demonstration
   affordance standing in for real authentication, citing
   issues #300 and #301 so a reader sees it is tracked
   rather than accidental.

**The decision most likely to be argued with is 3.** Picking
`debian:trixie-slim` + `apt install qemu-system-x86` makes
the demo image large and slow to build the first time, and a
reviewer may reasonably prefer a smaller purpose-built SPICE
target — or question whether the demo needs a real SPICE
server at all rather than a canned one. The reasoning is
that a demo proving the SPICE path with a fake SPICE server
proves nothing, and that an unmaintained third-party image
is a worse trade in a directory whose value is being
readable. If a reviewer disagrees, the change is localised
to one Dockerfile.

## The entrypoint

`demo/entrypoint.sh`, `set -euo pipefail`, in order:

1. **Bootstrap TLS if absent.** Call
   `tools/direct-qemu/generate-tls.sh`, copied into the
   image at build time so there is one TLS bootstrap in the
   tree and it is the CI-proven one, writing to a named
   volume so it survives `compose restart` but not
   `compose down -v`. Skip if `proxy-cert.pem` exists.
   `PROXY_HOST_SUBJECT` is then the literal
   `C=US,O=Kerbside CI,CN=kerbside-ci` set in the compose
   file, not derived at runtime: the string must match what
   the *client* checks, and pinning it as a literal is what
   makes a mismatch fail loudly. The `Kerbside CI`
   organisation name is inherited from the CI script;
   renaming it means editing both and is not worth it.
2. **Generate `AUTH_SECRET_SEED`** with
   `openssl rand -hex 32` into the volume if absent, and
   read it thereafter. Never a baked-in constant — issue
   #131 is exactly the failure of shipping a known signing
   key, and phase 2 made the example model the right
   behaviour. Persisting it is what lets a token minted at
   `up` still work after a restart.
3. **Wait for the database**, then `kerbside db upgrade`.
   The compose healthcheck covers most of this; a short
   retry loop covers the rest, because `service_healthy` and
   "accepting the kerbside user's credentials" are not the
   same event.
4. **Start `gunicorn kerbside.api:app`** bound to
   `0.0.0.0:13002` inside the container — the port is a
   gunicorn argument, not an env var (survey finding 7).
5. **Exec `kerbside daemon run`.** Decision 5.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | worktree | Create `demo/Dockerfile` and `demo/entrypoint.sh` per "The entrypoint" and decisions 4 and 5. Read `tools/direct-qemu/start-kerbside.sh` first and in full — it is the reference for the env var set (`:55-82`), the gunicorn invocation (`:117-131`) and the readiness polling (`:139-198`), and the sequencing traps in its comments are real. Copy `tools/direct-qemu/generate-tls.sh` into the image rather than reimplementing TLS bootstrap. Base `python:3.13-slim`; build deps are the Debian set from `bindep.txt` — `libmariadb-dev-compat`, `pkg-config`, `build-essential`, `libxml2-dev`, `libxslt1-dev`, `locales` — **not** `default-libmysqlclient-dev` (survey finding 5); plus `openssl` and `curl` at runtime. Build the image and confirm `kerbside`, `kerbside-proxy` and `gunicorn` resolve on `PATH`, and that `python -c 'from kerbside.proxy_supervisor import find_proxy_bin; print(find_proxy_bin())'` succeeds — the same pre-check `start-kerbside.sh:84-104` runs. Do not proceed until that passes. |
| 3b | medium | sonnet | worktree | Create `demo/spice-target/Dockerfile` per decision 3: `debian:trixie-slim`, `apt-get install -y --no-install-recommends qemu-system-x86`, and a CMD running the exact invocation in decision 2. Verify in isolation before any compose work: `docker run --rm -p 127.0.0.1:5910:5910` the image, then confirm from the host that the port accepts a connection and that qemu logged no `Invalid parameter` error. The `-object secret` form is load-bearing — the inline `password=` form fails on QEMU 10 (`start-qemu.sh:130-135`), so if you find yourself reaching for it, re-read survey finding 1. |
| 3c | high | opus | worktree | Create `demo/docker-compose.yml` with the three services (decision 1), the env var set, the loopback-only publishing with its explanatory comment (decision 8), and named volumes for the database and for the generated TLS/seed material. Add `demo/sources.yaml` — derived from `etc/example-static-sources.yaml`, one console, `hypervisor_ip` the `spice-target` service name, `insecure_port: 5910`, `ticket: demo-ticket`; note in it that a real deployment sets `secure_port` and `host_subject` and point at `docs/use-cases/ovirt.md`, which covers backend pinning properly. Backend TLS is deliberately not used: `generate-tls.sh` does emit qemu x509 material, but a second TLS leg adds a failure mode the evaluator cannot see. Add `demo/kerbside.ini` (decision 7). Bring the stack up from `docker compose down -v` and iterate until `docker compose ps` shows all three healthy, the API answers on `127.0.0.1:13002`, and both SPICE ports accept a TCP connection. Report the actual `docker compose logs kerbside` output for a successful start — not a summary of it. |
| 3d | medium | sonnet | worktree | Create `demo/get-console.sh` per decision 9. It mints the token with `docker compose exec kerbside kerbside demo token --subject demo-admin --output ...`, lists consoles via the API, fetches the `.vv` from `/console/proxy/<source>/<uuid>/console.vv`, writes `./demo-console.vv`, and prints the `remote-viewer` command. Copy the shape from `tools/direct-qemu/lane-up.sh:143-160`, which already does the minting half. No PyJWT fallback — if the command refuses, surface its message and stop. Verify the `.vv` contains `tls-port=`, `host-subject=` and a `ca=` holding an escaped PEM (`api.py:481-483`); their absence means the TLS leg will not verify. Also extend `.pre-commit-config.yaml:25` to `^(tools\|demo)/` (decision 6) and make `pre-commit run --all-files` pass with the new scripts in scope. |
| 3e | high | opus | none | End-to-end verification with a real client. From a clean `docker compose down -v`, bring the stack up, run `get-console.sh`, and open the `.vv` with `remote-viewer` (install `virt-viewer` if absent; the host runs a normal X session, so a window appears). Confirm the session establishes over the **TLS** port with CA verification from the `.vv`, and that the SeaBIOS screen renders. Capture a screenshot to the scratchpad and report its path. Then the negative cases: stop `spice-target` and confirm kerbside reports a failure rather than hanging; and add a dummy oVirt entry to `demo/sources.yaml` and confirm `kerbside demo token` refuses and names it, then revert. If the TLS leg does not verify, **do not** fall back to the insecure port and call it done — diagnose it. A demo that silently proves only the plaintext path is the failure mode this phase most needs to avoid. |
| 3f | medium | sonnet | worktree | Write `demo/README.md`: what the stack is, the three commands (`docker compose up -d`, `./get-console.sh`, `remote-viewer`), that the expected result is a BIOS screen and why, that ports are loopback-only and why, that two processes in one container is not a production shape and what the real one is, and `docker compose down -v` to remove everything including the generated CA and seed. Cite #300 and #301 for the token affordance. Keep it short and cross-link `docs/installation.md` by path — phase 5 writes that, so let phase 5 make the link true. Do not duplicate the walkthrough. |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The demo works over the insecure port while the TLS leg is broken — the console still appears, so it looks fine | Step 3e requires evidence of *which* port carried the session, and forbids falling back. The management session checks that evidence before accepting the phase. |
| qemu-in-a-container is new ground (finding 2) and fails in a way that eats the phase | Step 3b verifies the SPICE target standalone, before compose exists, so a qemu problem cannot masquerade as a networking or kerbside problem. |
| The inline `password=` form gets reintroduced from muscle memory or from an LLM's priors | Called out in finding 1, decision 2 and the 3b brief, with the failing QEMU version named. |
| `mysqlclient` fails to build in the image | The Debian package set is taken from `bindep.txt` (finding 5), which exists precisely because `mysqlclient` ships no wheel and needs `pkg-config`. 3a stops on a failed build rather than proceeding. |
| Two processes in one container gets copied into a real deployment | A comment in the entrypoint, a paragraph in `demo/README.md`, and the co-location reference at `docs/configuration.md`'s `API_SOCKET_PATH` row. |
| The demo image drifts from the released package it claims to install | Decision 4 keeps the default `KERBSIDE_SOURCE=kerbside`; phase 4's lane exercises `/src`, so both paths stay live. |

## Definition of done

Each item is checkable by running something. Outcome
recorded after each.

- [x] `docker compose up -d` from a clean checkout, with
      only docker installed, brings all three services to
      healthy. Verified from `docker compose down -v`.
- [x] `remote-viewer` opens the console **over TLS** with CA
      verification from the `.vv`, and a SPICE display
      renders. Screenshot captured: iPXE attempts a network
      boot, then SeaBIOS reports `No bootable device`, in a
      window titled `demo-console via proxy session ID
      mPnihdsfZ112`. Every established socket was on 5900
      and none on 5901, and the relay logged
      `channel_type="display"`, `"cursor"`, `"main"` and
      `"inputs"`, so real SPICE channels crossed the TLS
      leg. Nothing was added to the system trust store.
- [x] The `.vv` contains `tls-port=`, `host-subject=` and a
      `ca=` field holding an escaped PEM. `get-console.sh`
      asserts all three and refuses to write the file
      otherwise.
- [x] `KERBSIDE_SOURCE=/src` builds against a local
      checkout. It is now the default (finding 8), so this
      is what every build exercises.
- [x] `docker compose down -v` leaves nothing behind — zero
      volumes and zero containers matching the project — and
      a subsequent `up` regenerates TLS and the seed and
      still works end to end.
- [x] No `/dev/kvm`, no privileged containers, no host
      package installation. The only match for
      `grep -rn 'privileged\|/dev/kvm' demo/` is the comment
      in spice-target/Dockerfile explaining why neither is
      used.
- [x] The generated seed differs between two clean
      deployments: `59e8ba58187eda20...` then, after
      `down -v` and `up`, `f924d709a7f941f8...`.
- [x] `kerbside demo token` refuses when a non-`static`
      source is added to `demo/sources.yaml`, and the
      refusal names it: `Refusing to mint: source
      "pretend-ovirt" is of type "ovirt", not "static"`.
      Exit status 1. Reverted afterwards, and minting works
      again.
- [x] Stopping `spice-target` produces a reported failure,
      not a hang: `remote-viewer` exited in 9s and the proxy
      logged `hypervisor connection failed
      hypervisor=spice-target error=failed to lookup address
      information`.
- [x] `pre-commit run --all-files` passes with `demo/` in
      shellcheck's scope. Checked properly, and the check
      earned its place: an all-files run passed while
      `demo/` was still untracked, which is the
      "matches nothing also passes" failure the criterion
      warned about. Running shellcheck against the four
      scripts explicitly found a real defect — `demo-env.sh`
      has no shebang because it is sourced (SC2148) — fixed
      with a `shellcheck shell=bash` directive.
- [x] No fact about the demo is stated differently in
      `demo/README.md` and the compose file's comments. The
      ports, the expected BIOS screen and the two-process
      caveat each have one wording, and README.md defers to
      `demo/Dockerfile` on the `KERBSIDE_SOURCE` default
      rather than restating it.

### Added in review

The automated review raised 15 items; all were addressed or
recorded. Four changed behaviour, and one of them exposed a
packaging bug that had nothing to do with the demo.

**The `.vv` check could not detect the failure it claimed
to.** Grepping the file for `tls-port=`, `host-subject=` and
`ca=` proved almost nothing: kerbside emits the first and
third unconditionally (`api.py:481,483`), so they are
present whether or not the TLS listener works and whatever
port the client uses. The README promised a transport-level
assurance the code did not provide — the exact false
confidence this phase's risk table warns about.
`get-console.sh` now connects to the port the `.vv`
advertises, verifies the presented certificate against the
CA embedded in that same `.vv`, and checks the subject
matches, printing `TLSv1.3, certificate verified against the
CA in the .vv`. The README says what is actually proven.

**The demo raced its own startup.** `docker compose up -d`
returns once containers exist, and the only precondition
check was `docker compose ps --status running`, which passes
immediately. Three misleading first-run failures were
reachable, including "no consoles: is spice-target running?"
blaming an innocent service. The `kerbside` service now has
a healthcheck — which also makes a dead gunicorn visible,
since only the daemon is exec'd — and `get-console.sh` waits
on it, then waits for the console list after minting,
because that call needs a token.

**Item 5 exposed issue #326, which is not the demo's bug.**
Adding a `.dockerignore` that excluded `.git` produced an
install that died at import with `No module named
'kerbside.sources'`. `kerbside/sources/` and
`kerbside/migrations/` have no `__init__.py` and are not in
`pyproject.toml`'s `packages`; they reach an install *only*
through setuptools_scm's git file finder. Worse, the build
had been succeeding by accident — a stray untracked
`kerbside.egg-info/` was supplying the file list, so the
"clean checkout" criterion had been verified on a tree that
was not clean. The image now installs `git` (setuptools_scm
shells out to it, and the earlier build never had it),
`.git` stays in the context, and both facts are documented
where someone will trip over them. Verification moved to a
real clone, since a git worktree cannot build the image at
all.

**Values from the API are no longer spliced into remote
shell strings.** Not exploitable as shipped — the only input
is `demo/sources.yaml` — but this is the file most likely to
be copied as the basis for a real one, and passing
positional arguments is easier to read than the escaping it
replaced.

Smaller: certificates are regenerated when they expire
rather than served past 30 days; the bearer token goes to a
per-run `/tmp` path and is removed on exit instead of living
on the state volume beside the CA key; qemu runs as
`nobody`; the demo UUID is now valid hex; the README states
a minimum Docker version and no longer contradicts its own
code block. Three new unit tests pin the couplings the
review noted were advisory-only — the certificate subject
shared between `generate-tls.sh` and the compose file, the
SPICE ticket shared between the target image and
`sources.yaml`, and the UUID — each demonstrated to fail
before being trusted.

Deferred with a home rather than dropped: CI enforcement of
shellcheck over `demo/` is recorded in the phase 4 plan,
which owns the demo lane, and the missing inbound links from
`README.md` and `docs/index.md` are now explicitly in phase
5's scope rather than implied by "rewrite installation.md".

## Future work

- **A published demo image**, so `compose up` does not build
  from source. A release-process and image-signing
  question, not a demo question.
- **Backend TLS to the SPICE target.** `generate-tls.sh`
  already emits the qemu x509 material and
  `start-qemu.sh:109` shows the `tls-channel=default` form.
  Deliberately omitted here; the oVirt use-case page covers
  backend pinning for readers who need it.
- **#313**, a malformed INI exiting zero, which this phase
  makes marginally more reachable by shipping an INI file
  people will edit.

## Back brief

Before starting, the implementing session should restate:
the three services and why the SPICE target is a real qemu;
why the ticket uses `-object secret`; that `KERBSIDE_SOURCE`
defaults to the released package; and that TLS verification
is the acceptance criterion rather than "a console
appeared".

**Gate on decision 3.** Step 3b picks a base image and
installs qemu into it, and 3c builds the compose file on top
of whatever 3b produced. If the reviewer wants a different
SPICE target, that is cheap to change before 3b and annoying
after 3c. Confirm decision 3 before 3b starts.

No other gate: 3a, 3d and 3f are all localised, and 3e is
verification rather than construction.

## Registration note

The master plan's Execution table and `docs/plans/index.md`
are updated in the same commit as this file. The Execution
table named the phase 3, 4 and 5 plan files as plain text
rather than links although the files exist; that is fixed
here for all three.

This repository has no `docs/plans/order.yml`. The shared
convention says phase files are not registered there anyway,
so nothing is missing — but the file does not exist here at
all, and a future session should not go looking for it.
