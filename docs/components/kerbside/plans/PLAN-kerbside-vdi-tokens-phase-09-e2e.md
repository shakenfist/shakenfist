# Kerbside VDI tokens phase 9: full cross-repo end-to-end lane

This is phase 9 of a **cross-repository master plan** whose plan of record
lives in the Shaken Fist repository
(`shakenfist/docs/plans/PLAN-kerbside-vdi-tokens.md`). Phases 0-8 are done and
merged across four repos:

| Repo | What merged | Release |
|------|-------------|---------|
| visual-digest-rust | crates.io publishing + tooling | 0.1.1 |
| ryll | visual-digest repin, PyPI publish | 0.1.7 |
| client-python | seamless Kerbside VDI launch (phase 4) | 0.8.3 |
| shakenfist (server) | mint path (phases 1-2, 6-SF, 7-8) | merge only |
| kerbside | exchange endpoint (phases 5, 6-kerbside, 8) | merge only |

Phase 9 is the **post-merge** phase: it runs the full flow against a **real
Shaken Fist** and closes the one gap the earlier phases could not.

## Situation (grounded)

What is already proven, and where the hole is:

- **Offline exchange is exhaustively unit-tested.** `tests/unit/test_sf_token.py`
  (nine adversarial cases: valid, expired, wrong audience, forged signature,
  unknown kid with/without refetch, refetch throttled, missing claim, malformed)
  and `tests/unit/test_api.py::SfTokenApiTestCase` (replay, unscraped console,
  valid exchange, 404-does-not-burn-jti) cover `verify_sf_token` and the
  `/sf-console.vv` endpoint against locally-generated keypairs.
- **The proxy relay of a kerbside-issued `.vv` is proven end to end** by the
  `direct-qemu` lane (`direct-qemu-functional.yml`), which boots a real
  Uncalibrated Sextant guest under qemu, fronts it with a real kerbside +
  Rust proxy, and drives a scenario through `ryll --headless` over its control
  socket.
- **The SF mint path is functionally tested** by
  `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_vdi_tokens.py` (phase
  7a): a namespaced client mints a `/vdiconsoleproxy` token whose signature
  verifies against `/admin/vditokenpubkey`. That test **skips** today because
  no cluster in the SF functional matrix provisions `KERBSIDE_URL`.

The hole phase 9 fills: **nothing yet proves the joined flow** — SF mints,
kerbside verifies *offline against keys SF actually published*, exchanges the
JWT for a consoletoken + `.vv`, and proxies the session — because that needs a
genuine `type: shakenfist` source. Phase 7 established (and grounded in the
code) that this cannot be faked in the cloud-free `direct-qemu` lane:

- `sf_token.verify_sf_token` only trusts `type: shakenfist` sources
  (`_shakenfist_source_names()`); a `type: static` source's token is never
  verifiable.
- The exchange asserts `console['source'] == claims['source']`, so the console
  must live under the verifying shakenfist source's name.
- The daemon maintenance loop (`_parse_sources`, every 60s) reaps any console a
  live scrape did not yield, so a hand-seeded console does not survive.
- `KERBSIDE_URL` is read into SF `config` at **process start**
  (`load_cluster_config()` → `SFConfig()`), so it must be provisioned at deploy
  time, not flipped on at test runtime.

Therefore a **real single-node Shaken Fist** is required — exactly what this
phase stands up.

### CI reality (grounded in `shakenfist/actions`)

Kerbside's existing functional lanes deploy *other* clouds (oVirt, OpenStack via
kolla) on an under-cloud VM and drive them over SSH from the workflow. Two facts
shape phase 9:

1. **Kerbside CI can already create under-cloud SF instances.** The existing
   `ansible/kerbside-single-node.yml` provisions guests with the
   `shakenfist.shakenfist` collection's native `sf_*` modules, so the runner has
   working outer-SF API access.
2. **A real single-node SF at develop HEAD is a solved problem via
   `build-smoke-cluster`.** `shakenfist/actions/setup-test-environment` +
   `build-smoke-cluster` (input `topology: localhost`) provision an under-cloud
   VM and deploy SF onto it *from locally-built develop-HEAD wheels*
   (`sf_build_local_wheels=true`). It outputs `primary` (egress IP),
   `namespace`, and `inventory`, and leaves `/etc/sf/sfrc`,
   `/srv/shakenfist/venv/bin/sf-client`, and the SF API on `http://localhost:13000`
   on the primary, with a **known** `system_key`
   (`ci-system-key-do-not-use-in-production-ci-system-key` by default).

The gap phase 9 must build: **there is no existing glue that stands up an SF
cluster AND points a kerbside proxy at it.** In this repo the SF-cluster world
(`build-smoke-cluster`) and the kerbside-proxy world (`kerbside-patches` / kolla
containers, driven by `deploy-kolla-ansible`) never meet. Phase 9 writes that
glue.

## Mission

Add a new kerbside CI lane that:

1. deploys a single-node Shaken Fist at develop HEAD (reusing
   `build-smoke-cluster`);
2. provisions `KERBSIDE_URL` + a signing key in the SF cluster and restarts
   `sf-api` so the mint endpoint activates;
3. creates a SPICE-console instance in a test namespace;
4. stands up a kerbside whose `sources.yaml` holds a `type: shakenfist` source
   pointing at that cluster, so it scrapes the console and caches the signing
   keys;
5. drives the real flow: **mint** (via the client) → **offline verify** →
   **exchange** for `.vv` → **proxied SPICE session**, asserting a session audit
   row and clean teardown; and
6. exercises the adversarial matrix against the live app: replayed token,
   expired token, wrong audience, unknown kid, cross-namespace mint attempt.

## Architecture decision (confirmed with maintainer)

Four decisions were taken before implementation:

1. **Assertion depth: full sextant-screen.** The lane boots the Uncalibrated
   Sextant assertion-oracle guest *inside* the nested SF instance (import the
   image, boot it with a SPICE console) and drives its on-screen visual digest
   through `ryll`'s control socket, exactly as the `direct-qemu` lane does. This
   is the strongest assertion — it proves not just the exchange but a fully
   working proxied session end to end.
2. **Kerbside co-located on the SF primary.** Kerbside runs as plain processes on
   the primary node (as `direct-qemu`'s `start-kerbside.sh` does), reaching the SF
   API on `http://localhost:13000` and reading the cluster CA from `/etc/sf/`
   locally, so the shakenfist source's CA-cert equality check and cluster scrape
   work without exposing SF's API externally. The primary already has MariaDB
   (from `build-smoke-cluster`); phase 9 creates a `kerbside` db/user there.
   `ryll` runs on the primary too, driving kerbside's proxy over `localhost`, so
   the whole session is self-contained on the primary.
3. **New `shakenfist/actions` composite action.** The SF-provision +
   kerbside-deploy glue lands as a `deploy-kerbside-on-shakenfist` composite
   action in `shakenfist/actions` (mirroring `deploy-kolla-ansible`), reusable
   beyond this lane. **This makes phase 9 a two-PR change: the actions PR lands
   first, then the kerbside PR references `shakenfist/actions/deploy-kerbside-on-shakenfist@main`.**
4. **Dispatch/scheduled gating first.** The lane runs via `workflow_dispatch`
   (and optionally a nightly `schedule`), not as a `pull_request` check, until it
   is shown stable — the deploy is heavy and we have seen merge-queue
   SSH-timeout flakes elsewhere. It is promoted to a PR gate once green and
   reliable.

### Alternatives considered and rejected

- **Kerbside on the runner, talking to SF over the network** — rejected:
  single-node SF binds its API on `localhost:13000` (not externally exposed, no
  external TLS), and the shakenfist source needs cluster-local API access for
  `get_cluster_cacert` / `get_nodes` / `get_instances`. Exposing that is brittle
  work for no gain.
- **`getsf` a self-contained SF on the runner** — rejected: `getsf` is the legacy
  path in `shakenfist/actions` (it survives only in the "released"/"upgrade"
  topologies); `build-smoke-cluster` is the maintained path and already
  builds+installs SF at develop HEAD.
- **Handshake-only assertion** — rejected in favour of the full sextant-screen
  assertion above (decision 1).
- **Glue as kerbside-repo scripts** — rejected in favour of the reusable
  composite action (decision 3); the session-driving pieces (image import, mint /
  exchange / ryll driving, adversarial checks) still live in `kerbside/tools/`,
  but the deploy glue is in `shakenfist/actions`.

## Execution

Split across two repos:

### `shakenfist/actions` (PR lands first)

A new composite action `deploy-kerbside-on-shakenfist/action.yml` that, given a
running single-node SF cluster (the `build-smoke-cluster` outputs) and a kerbside
checkout + proxy wheel staged on the runner, does the primary-side work over SSH:

1. **Provision the integration in SF**: `sf-ctl set-config KERBSIDE_URL
   https://<kerbside-public-fqdn>`; `sf-ctl set-config KERBSIDE_TOKEN_DURATION
   300`; `sf-ctl ensure-kerbside-signing-key`; restart `sf-api` so the
   process-cached `KERBSIDE_URL` takes effect. `<kerbside-public-fqdn>` is how
   the token `aud` and the returned URL base are formed; it must equal kerbside's
   `SF_CONSOLE_TOKEN_AUDIENCE`. Since kerbside is co-located, this can be a
   localhost-resolvable name / the primary's own address.
2. **Deploy kerbside on the primary**: copy the PR's kerbside checkout + proxy
   wheel to the primary; create a `kerbside` MariaDB db/user in the primary's
   existing MariaDB; install kerbside + the proxy wheel into a venv; `pip install
   ryll==0.1.7` (released bin wheel — this lane tests the SF↔kerbside
   integration, not ryll HEAD); write `sources.yaml` with one `type: shakenfist`
   source (`url: http://localhost:13000`, `username: system`, `password:
   <system_key>`, `ca_cert: <cluster CA from /etc/sf/>`); export
   `KERBSIDE_SF_CONSOLE_TOKEN_AUDIENCE`; start kerbside (adapt `start-kerbside.sh`
   to run on the primary); wait for the first scrape to yield the console.

Inputs: `base_user`, `primary`, `system_key`, `kerbside_public_fqdn`,
`token_duration`. It mirrors `deploy-kolla-ansible`'s shape (SSH into the node,
run a sequence of steps, fail fast).

### `kerbside` (PR references the merged action)

One new workflow, `.github/workflows/sf-e2e-functional.yml`, triggered by
`workflow_dispatch` (and optionally a nightly `schedule`) — **not**
`pull_request` yet (decision 4). A single job on `[self-hosted, vm, debian-12,
l]`, `timeout-minutes: 120`. Session-driving logic lives in
`kerbside/tools/sf-e2e/*` (no >5-line inline CI scripts, per project
convention):

1. **Stand up SF.** `shakenfist/actions/setup-test-environment` then
   `build-smoke-cluster` with `topology: localhost`. Capture `primary`,
   `namespace`, `system_key`.
2. **Build the PR's proxy wheel** on the runner (Rust toolchain +
   `tools/build-proxy-wheel.sh`, exactly as the openstack lane does) and stage it
   for the composite action.
3. **Deploy kerbside against SF** via
   `shakenfist/actions/deploy-kerbside-on-shakenfist@main`.
4. **Import + boot the assertion-oracle guest** (`import-sextant.sh`): upload
   `tests/fixtures/uncalibrated-sextant.qcow2` to SF as a shared artifact, then
   create a UEFI, SPICE-console instance from it in a fresh test namespace and
   await `created`. Wait for kerbside's scrape to pick the console up.
5. **Happy path** (`drive-happy-path.sh` + Python driver on the primary):
   `client.get_vdi_console_proxy_file(<instance>)` performs mint + exchange in one
   call and returns the `.vv`; hand it to `ryll --headless --file <.vv>
   --control-socket ...` against kerbside's proxy on localhost; reuse
   `wait-for-banner.sh` + `smoke-client.py` / `run-scenario.sh` to assert the
   Sextant boot banner and drive the on-screen visual digest (full direct-qemu
   depth). Assert a session audit row exists and that teardown removes it.
6. **Adversarial matrix** (`drive-adversarial.sh`), each against the live
   kerbside `/sf-console.vv?token=<jwt>`:
   - **replay** — exchange the same freshly-minted token twice; second returns
     401 `token already used`;
   - **expired** — mint with a short duration, exchange after expiry → 401;
   - **wrong audience** — a token whose `aud` is not this kerbside → 401;
   - **unknown kid** — a token signed by an untrusted key / unknown `kid` → 401
     (and confirm the refetch debounce holds under repeat);
   - **cross-namespace mint** — a second namespace's client minting for the first
     namespace's instance → SF returns 404/403 (ownership enforced at the mint,
     not the exchange).
7. **Artifacts + teardown**: gather kerbside logs, gunicorn logs, ryll
   stdout/stderr, the `.vv`, SF `sf-api`/`sf-console` logs, and the sources.yaml;
   upload on `always()`. The under-cloud reaper collects the SF instance.

## First run (2026-07-31, dispatch 30582413364)

The first dispatch (`retention=30`) **failed at the happy path's first gate**,
but every heavy unknown upstream of it passed:

* `build-smoke-cluster`, the co-located kerbside deploy (MariaDB, ryll from
  source, proxy wheel, source key caching), and — notably — the **nested UEFI +
  SPICE boot** of the Uncalibrated Sextant instance all went green. Open
  question 1 is effectively answered: the guest reached `created` on the CI
  hypervisor without a handshake-level fallback.
* `drive-happy-path.py` then timed out after 180s waiting for the console to
  appear in kerbside. The daemon log showed the source repeatedly *finding* the
  instance (correct `vdi: spice`, state `created`) but discarding it:
  `Ignoring instance whose node is not in the node map`.

**Root cause — a real bug in `kerbside/sources/shakenfist.py`, not the lane.**
The source keyed its node map by `node['name']` (the fqdn) but looked it up by
`inst['node']`, which Shaken Fist reports as a node **UUID** (from the
instance's placement). Name never equals UUID, so every SPICE console was
dropped. It stayed latent because the unit-test fixture set `node='n1'` *and*
`name='n1'`, making them accidentally equal. Fixed on branch
`sf-source-node-uuid-fix`: key the map by `node['uuid']`, emit `hypervisor` as
the node fqdn (a connectable host, as the direct-`.vv` `host=` needs), and
synthesize `host_subject` from the fqdn. Test fixtures now model SF's real
contract (distinct `uuid` vs `fqdn`) and fail without the fix. The lane's
retain-on-dispatch step also gained `always()` so a *failed* run still lingers
for debugging (it was skipped on this failure, tearing the env down early).

Re-dispatch once the fix merges; downstream gates (the proxied SPICE session
and on-screen digest assertion) are still unproven past this point.

## Residual open questions

1. **Booting Uncalibrated Sextant inside nested SF.** *(Largely resolved by the
   first run — the guest booted UEFI + SPICE and reached `created`.)* The guest
   is UEFI and its assertion oracle needs a working SPICE console on the SF
   instance. If the on-screen digest step later proves flaky on the CI
   hypervisors, fall back to a handshake-level assertion for that step (still
   proving mint → verify → exchange → proxy) and track the on-screen assertion
   as follow-up.
2. **`kerbside_public_fqdn` / audience value.** With kerbside co-located and
   driven over localhost, confirm the exact string used for both SF's
   `KERBSIDE_URL` and kerbside's `SF_CONSOLE_TOKEN_AUDIENCE` (they must match
   byte-for-byte). A stable localhost-resolvable name is simplest.
3. **Runner budget.** Confirm the `l` class + 120-minute timeout hold once the
   full deploy+session is timed; adjust if the deploy dominates.

## Success criteria

* A single new CI lane deploys a real single-node Shaken Fist at develop HEAD,
  provisions `KERBSIDE_URL` + a signing key, stands up a kerbside with a
  `type: shakenfist` source, and drives an SF-minted token through offline
  verification, exchange, and a proxied SPICE session — green.
* The adversarial cases (replay, expired, wrong audience, unknown kid,
  cross-namespace mint) are each asserted against the live app.
* Phase 7a's `test_vdi_tokens.py` mint-path test *activates* (stops skipping) on
  this provisioned cluster.
* No token string or private key material is logged, in scripts or artifacts.
* `README.md`, `AGENTS.md`, `ARCHITECTURE.md`, and `docs/` note the new lane and
  the SF console-source end-to-end coverage.
* The master plan's phase 9 row and this repo's `docs/plans/index.md` are updated
  to *Done* when the lane is green.

## Future work

* Promote the primary-side kerbside install to a reusable
  `shakenfist/actions/deploy-kerbside-on-shakenfist` composite action if a second
  consumer appears (Alternative C).
* Add the sextant-screen assertion (Open question 1) if the handshake-level
  assertion lands first.
* Fold key rotation (`sf-ctl rotate-kerbside-signing-key`) into the lane to prove
  the two-key window and kerbside's unknown-kid refetch against a real rotation.
