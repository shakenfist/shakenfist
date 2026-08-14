# Phase 3: the `docker compose` demo stack

Master plan: [PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/)

Planned at high effort: container plumbing, TLS bootstrap,
two supervised processes and a SPICE target under one
`compose up`, with many independent failure modes.

## Situation

There is no Python-side container image and nothing
published to a registry. `rust/kerbside-proxy/Dockerfile` is
a build container for the Rust wheel, not a runtime image.

Everything the stack needs is nonetheless already proven
somewhere in the tree, and this phase should assemble rather
than invent:

| Need | Existing proven source |
|------|------------------------|
| The full env var set | `tools/direct-qemu/start-kerbside.sh:56-90` |
| TLS material and matching subject | `tools/direct-qemu/generate-tls.sh` |
| MariaDB database and user | `tools/direct-qemu/setup-mariadb.sh` |
| Static source `sources.yaml` | `etc/example-static-sources.yaml` |
| Bearer token minting | `tools/direct-qemu/lane-up.sh:129-161` |
| Both processes | `start-kerbside.sh:140-160` (gunicorn, then `kerbside daemon run`) |
| Readiness polling | `start-kerbside.sh:163-215` |

Two facts established while planning, both load-bearing:

- **The `.vv` embeds the CA.** `kerbside/api.py:445-448`
  reads `CACERT_PATH`, escapes newlines, and emits it as
  `ca=` in the virt-viewer file
  (`VIRTVIEWER_TEMPLATE`, `api.py:351-366`), alongside
  `host-subject=` from `PROXY_HOST_SUBJECT`. So a stock
  `remote-viewer` verifies the demo's self-signed CA **from
  the `.vv` alone** — the evaluator installs no certificates
  and the TLS leg is genuinely exercised. This is why the
  demo can be honest rather than insecure-only.
- **`kerbside db upgrade` exists** as of phase 1, so the
  entrypoint does not need a repository checkout.

## Mission

`docker compose up` in `demo/`, on a machine with nothing
but docker, reaches a SPICE console served by a real SPICE
server, proxied by kerbside, viewable in `remote-viewer`.

## Approach

### Services

Three services, all on the default compose network:

1. **`db`** — upstream `mariadb` image, `MARIADB_DATABASE`/
   `MARIADB_USER`/`MARIADB_PASSWORD` set to demo values, a
   named volume, and a `healthcheck` using
   `mariadb-admin ping`. Do not publish its port.
2. **`spice-target`** — the thing being proxied. See below.
3. **`kerbside`** — built from `demo/Dockerfile`, depends on
   `db` with `condition: service_healthy` and on
   `spice-target`. Publishes API and both SPICE ports.

### The SPICE target

The demo needs a real SPICE server or it proves nothing. Run
`qemu-system-x86_64` with **no disk**:

```
qemu-system-x86_64 -m 256 -display none \
  -spice port=5910,password=demo-ticket,disable-ticketing=off
```

With no bootable device SeaBIOS renders a "No bootable
device" screen — which is a genuine SPICE display surface,
proving the whole path end to end. Deliberately chosen over
the alternatives:

- **No KVM required.** TCG is fine for a BIOS screen, so the
  demo does not need `/dev/kvm` or privileged containers,
  and works on a laptop, in a VM, and in CI. If `/dev/kvm`
  happens to be available it is not used, and the plan does
  not try to detect it — conditional acceleration is
  complexity for no visible gain at a BIOS prompt.
- **No guest image download.** Uncalibrated Sextant is the
  project's real test guest and the right choice for CI
  assertions, but making an evaluator fetch or build a guest
  to see a demo is exactly the friction this phase removes.

Say plainly in `demo/README.md` and the docs that the black
screen with BIOS text is the expected result, and that it is
a SPICE session, not a placeholder. An evaluator who thinks
the demo failed is worse than no demo.

### The kerbside image

`demo/Dockerfile`, single stage on `python:3.13-slim` (the
tree requires `>=3.11`; match `functional-tests.yml`'s
version if it pins one). Needs at build time:
`build-essential`, `pkg-config`, `default-libmysqlclient-dev`
for `mysqlclient`, and `openssl` plus `curl` at runtime.
Consult `bindep.txt` rather than guessing the list, and
`docs/installation.md`'s bindep section stops being
hypothetical if the Dockerfile is the worked example.

Install kerbside via a build argument so the same Dockerfile
serves the evaluator and CI:

```
ARG KERBSIDE_SOURCE=kerbside
RUN pip install --no-cache-dir "${KERBSIDE_SOURCE}"
```

Default installs the released package from PyPI, which is
what the docs describe. CI overrides it with `/src` and a
bind-mounted checkout so the lane tests the PR, not the last
release. **The default must be the released package**: a
demo that silently tests unreleased code is a demo that
works for the maintainer and fails for everyone else.

The Rust proxy comes in with `kerbside`'s exact pin when
installing from PyPI. When `KERBSIDE_SOURCE=/src`, that pin
is absent from the committed tree by design (see the
`KERBSIDE_PROXY_PIN` comment in `pyproject.toml`), so CI
must also install a locally built proxy wheel —
`tools/build-proxy-wheel.sh` and
`tools/direct-qemu/install-proxy-wheel.sh` already do this
and phase 4 wires them in. Document the requirement in the
Dockerfile as a comment; do not try to make the default path
build Rust.

### The entrypoint

`demo/entrypoint.sh`, `set -euo pipefail`, in order:

1. **Bootstrap TLS if absent.** Call
   `tools/direct-qemu/generate-tls.sh` — copied into the
   image at build time, so there is one TLS bootstrap in the
   tree and it is the CI-proven one — writing to a named
   volume so it survives `compose restart` but not `compose
   down -v`. Skip if `proxy-cert.pem` already exists.
   `PROXY_HOST_SUBJECT` is then the literal subject that
   script produces, `C=US,O=Kerbside CI,CN=kerbside-ci`, set
   in the compose file rather than derived at runtime.
   Deriving it from the certificate is tempting and wrong:
   the string must match what the *client* checks, and
   pinning it as a literal is what makes a mismatch fail
   loudly. Note the `Kerbside CI` organisation name is
   inherited from the CI script; renaming it means editing
   both, and is not worth it.
2. **Generate `AUTH_SECRET_SEED`** with `openssl rand -hex 32`
   into the volume if absent, and read it thereafter. Never
   a baked-in constant: issue #131 is exactly the failure of
   shipping a known signing key, and the demo should model
   the right behaviour. Persisting it is what lets a token
   minted at `up` still work after a restart.
3. **Wait for the database**, then `kerbside db upgrade`.
   Compose healthchecks cover most of this; a short retry
   loop covers the rest, because `service_healthy` and
   "accepting the kerbside user's credentials" are not the
   same event.
4. **Start `gunicorn kerbside.api:app`** bound to `0.0.0.0`
   inside the container on the API port.
5. **Exec `kerbside daemon run`** as PID 1's foreground
   child, so a crashed daemon exits the container and
   compose reports it. Do not background both and `wait` —
   that hides the failure that matters most.

Two processes in one container is not the shape a production
deployment should copy. State that in a comment and in the
docs, and say what the real shape is (separate units or
containers, sharing `API_SOCKET_PATH`, per
`docs/configuration.md`'s note that the proxy must be
co-located with the daemon).

### Configuration

Set env vars in `docker-compose.yml`, taking
`start-kerbside.sh:56-90` as the reference list, and **also**
ship `demo/kerbside.ini` demonstrating the INI form from
phase 2, mounted at `/etc/kerbside/kerbside.ini`. The demo is
the natural place to show both mechanisms and which wins.
Keep secrets (the seed) out of the INI file and in the
generated volume.

`sources.yaml`: `demo/sources.yaml`, derived from
`etc/example-static-sources.yaml`, one console whose
`hypervisor_ip` is the `spice-target` service name,
`insecure_port: 5910`, `ticket: demo-ticket`. Backend TLS is
deliberately not used — `generate-tls.sh` does emit qemu
x509 material, and a second TLS leg would add a failure mode
without adding anything the evaluator can see. Note in the
file that a real deployment sets `secure_port` and
`host_subject`, and point at the oVirt use-case page, which
covers backend pinning properly.

### Ports

Publish to loopback explicitly:

```
ports:
  - '127.0.0.1:13002:13002'   # REST API and web UI
  - '127.0.0.1:5900:5900'     # SPICE, TLS
  - '127.0.0.1:5901:5901'     # SPICE, redirect to TLS
```

Master plan decision 3, settled 2026-08-14, requires this:
the demo has no real authentication, so it must not be
reachable from the network by default. Comment why,
immediately above, so the evaluator who changes it knows
what they are accepting, and state it as a limitation in
`demo/README.md` rather than only in the compose file.

### Getting a token and a `.vv`

`demo/get-console.sh`: mint a bearer token, `GET` the
console list, then fetch the `.vv` and write it to a file,
printing the `remote-viewer` command.

The token comes from **`kerbside demo token --subject
demo-admin`**, run inside the kerbside container — master
plan decision 1, settled 2026-08-14 and delivered by phase 1
step 1f. No PyJWT snippet anywhere in `demo/`; if the
command is missing or refuses, that is a phase 1 defect to
fix there, not to work around here.

The command's own guards do useful work for the demo: it
refuses unless every configured source is `static` and
refuses if `AUTH_SECRET_SEED` is still the sentinel. Both
hold for this stack, so a refusal means the entrypoint's
seed generation or `demo/sources.yaml` is wrong — a genuine
failure worth surfacing rather than routing around. Do not
add a fallback path.

The docs must still be honest that this is a demonstration
affordance standing in for real authentication, not the
intended user journey, and should cite issues #300 and #301
so a reader can see it is known and tracked rather than
accidental.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | worktree | Create `demo/Dockerfile` and `demo/entrypoint.sh` per the "The kerbside image" and "The entrypoint" sections above. Read `tools/direct-qemu/start-kerbside.sh` first and in full — it is the reference for the env var set, the readiness polling, and the gunicorn invocation, and the sequencing traps it documents in comments are real. Copy `tools/direct-qemu/generate-tls.sh` into the image rather than reimplementing TLS bootstrap. `KERBSIDE_SOURCE` build arg defaulting to `kerbside`. Build the image and confirm `kerbside`, `kerbside-proxy`, and `gunicorn` all resolve on `PATH` inside it, and that `find_proxy_bin()` succeeds — `python -c 'from kerbside.proxy_supervisor import find_proxy_bin; print(find_proxy_bin())'`, the same pre-check `start-kerbside.sh:96-103` runs. Do not proceed to 3b until that passes. |
| 3b | high | opus | worktree | Create `demo/docker-compose.yml` with the three services from "Services", the env var set, the loopback-only port publishing with its explanatory comment, and named volumes for the database and for the generated TLS/seed material. Add `demo/sources.yaml` and `demo/kerbside.ini`. Bring the stack up from a clean state (`docker compose down -v` first) and iterate until `docker compose ps` shows all three healthy, the API answers on `127.0.0.1:13002`, and both SPICE ports accept a TCP connection. Report the actual `docker compose logs kerbside` output for a successful start in your result — not a summary of it. |
| 3c | medium | sonnet | worktree | Create `demo/get-console.sh` per "Getting a token and a `.vv`". It mints the token with `docker compose exec kerbside kerbside demo token --subject demo-admin`, lists consoles via the API, fetches the `.vv` for the single demo console, writes it to `./demo-console.vv`, and prints the `remote-viewer ./demo-console.vv` command. No PyJWT fallback — if the command refuses, surface its message and stop. Verify the `.vv` contains `tls-port=`, `host-subject=`, and a `ca=` field holding an escaped PEM — those come from `kerbside/api.py:445-451` and their absence means the TLS leg will not verify. |
| 3d | high | opus | none | End-to-end verification with a real client. From a clean `docker compose down -v`, bring the stack up, run `get-console.sh`, and open the `.vv` with `remote-viewer` (install `virt-viewer` if absent; the operator's host runs a normal X session, so a window will appear). Confirm a SPICE session establishes over the **TLS** port with CA verification from the `.vv`, and that the SeaBIOS screen renders. Capture a screenshot into the scratchpad and report its path. Then confirm the negative case: stop `spice-target` and check kerbside reports a failure rather than hanging. If the TLS leg does not verify, do not fall back to the insecure port and call it done — diagnose it, because a demo that silently proves only the plaintext path is the failure mode this phase most needs to avoid. |
| 3e | medium | sonnet | worktree | Write `demo/README.md`: what the stack is, the three commands (`docker compose up -d`, `./get-console.sh`, `remote-viewer`), that the expected result is a BIOS screen and why, that ports are loopback-only and why, that two processes in one container is not a production shape, and `docker compose down -v` to remove everything including the generated CA and seed. Keep it short and point at `docs/installation.md` as the narrative version — phase 5 writes that, so cross-link by path and let phase 5 make it true. Do not duplicate the walkthrough here. |

## Success criteria

* `docker compose up -d` from a clean checkout, with only
  docker installed, brings up all three services healthy.
* `remote-viewer` opens the console over TLS with CA
  verification from the `.vv`, and a SPICE display renders.
  Demonstrated with a screenshot, not asserted.
* `KERBSIDE_SOURCE=/src` builds against a local checkout
  (phase 4 depends on this).
* `docker compose down -v` leaves nothing behind; a
  subsequent `up` regenerates TLS and the seed and still
  works.
* No `/dev/kvm`, no privileged containers, no host package
  installation.
* The generated seed is random per deployment, not a
  constant.
* `kerbside demo token` works in the demo stack, and
  demonstrably refuses in the real stack when a non-`static`
  source is added to `demo/sources.yaml` — verify by adding
  a dummy oVirt entry, confirming the refusal names it, then
  reverting. Phase 1 unit-tests the guard; this confirms it
  fires against a real deployment rather than a fixture.
* `tox -eflake8` passes; any shell script added is
  consistent with the style of `tools/direct-qemu/`.

## Notes for review

The likely quiet failure is the demo working over the
insecure port while the TLS leg is broken — the console
still appears, so it looks fine. Step 3d's TLS requirement is
the guard. Check the reviewer-visible evidence for *which*
port the session used before accepting this phase.
