# VDI console access via Kerbside signed tokens

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (the REST
API decorator stack in `shakenfist/external_api/base.py`, JWT
issuance in `shakenfist/util/access_tokens.py`, the
`cluster_config` table and `load_cluster_config()` in
`shakenfist/config.py` and `shakenfist/mariadb.py`, the
eventlog audit pattern, and the deployer PKI roles under
`deploy/ansible/roles/pki_internal_ca/`), and ground your
answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead.

This is a **cross-repository plan**. The affected repositories
and their working branches are:

| Repository | Branch | Role |
|------------|--------|------|
| shakenfist/shakenfist | `vdi-console-tokens` | Token minting, signing key, pubkey publication |
| shakenfist/client-python | `vdi-console-tokens-client` | New API client methods, CLI surface, viewer launch |
| shakenfist/kerbside | `sf-vdi-tokens` | Token exchange endpoint, scrape changes |
| shakenfist/ryll | `vdi-console-tokens` | pip-installable viewer so the CLI can launch a session |

The master plan (this document) lives in shakenfist's
`docs/plans/`. Phase plan files live in the repository whose
code they change, so that each plan travels with the PR that
implements it. Kerbside's `docs/plans/index.md` carries a
cross-reference entry pointing back here.

For the kerbside side, consult kerbside's `.claude/CLAUDE.md`
and `docs/proxy-architecture.md`. Key kerbside references:
`kerbside/api.py` (the `NovaToken` resource is the pattern the
new exchange endpoint mirrors), `kerbside/consoletoken.py`,
`kerbside/sources/shakenfist.py`, and `kerbside/main.py` (the
maintenance-loop reapers).

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named with `-phase-NN-descriptive`
appended, in the repository the phase changes.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Kerbside is a SPICE VDI protocol proxy that provides remote
console access to VMs across Shaken Fist, OpenStack, and oVirt
clouds. Ironically, Shaken Fist itself now has the worst
Kerbside integration of the three:

- **OpenStack** (Nova 2025.1+ with spice-direct consoles) has
  a per-instance authorisation story: when a user requests a
  console, Nova returns a URL pointing at Kerbside with an
  authentication token. Kerbside validates that token against
  Nova's `/os-console-auth-tokens/` API and only then issues
  its own session credential. Authorisation is enforced by the
  cloud that knows who owns the instance.
- **Shaken Fist** is merely *scraped*: kerbside's
  `ShakenFistSource` polls one configured namespace once a
  minute (`kerbside/sources/shakenfist.py`) and lists the
  consoles in kerbside's admin interface. There is no
  per-instance access control — anyone in kerbside's Keystone
  access group can fetch a `.vv` for any scraped console.
- Shaken Fist *does* already have the authorisation primitive:
  `InstanceVDIConsoleHelperEndpoint`
  (`shakenfist/external_api/instance.py:1442`) hands out a
  `.vv` file gated by `@requires_instance_ownership`
  (`external_api/base.py:328`) — but that `.vv` points the
  client **directly at the hypervisor**, bypassing Kerbside
  (and therefore bypassing TLS termination at the proxy, the
  SPICE protocol firewall, session auditing, and session
  termination).

Because we control both codebases, Shaken Fist can do
something Nova cannot: sign the console grant asymmetrically
so that Kerbside can validate it **offline**, with no
callback to the Shaken Fist API on console open. Nova's model
requires Kerbside to consult Nova at `.vv`-fetch time; a
signed-token model has no such availability coupling.

Relevant existing machinery, confirmed by code inspection:

- SF's REST API JWTs are HS256 via `flask_jwt_extended` with a
  single cluster-wide `AUTH_SECRET_SEED`
  (`external_api/app.py:59`, `config.py:148`). The proxy token
  needs a *different*, asymmetric signing path (Ed25519 via
  raw PyJWT) so that kerbside holds only a public key.
- SF already has PyJWT 2.13.0 as a direct dependency
  (`pyproject.toml:116`), but **not** `cryptography`, which
  PyJWT requires for EdDSA. Kerbside already depends on both
  `cryptography` and PyJWT (via `flask-jwt-extended`).
- The post-etcd cluster-wide key/value store is the
  `cluster_config` table (`mariadb.py:1530`), read into
  `SHAKENFIST_*` environment variables at process start by
  `load_cluster_config()` (`config.py:27`). `AUTH_SECRET_SEED`
  is already shared across API nodes exactly this way — the
  signing keypair follows the same pattern.
- The pattern for publishing cluster material to kerbside
  exists: `GET /admin/cacert`
  (`external_api/admin.py:47`, any authenticated namespace)
  returns the SPICE CA cert, advertised via the
  `cluster-cacert` capability string in the root page HTML
  (`external_api/app.py:201`), and consumed by kerbside's
  `ShakenFistSource.__init__` at source-init time.
- `@redirect_instance_request` (`external_api/base.py:273`)
  proxies vdiconsole requests to the instance's hypervisor
  because the direct `.vv` needs node-local state (the SPICE
  CA file and the node's ports). A proxy-token endpoint needs
  none of that, so it can answer from **any** API node and
  must not carry that decorator.
- The Python client (`shakenfist_client`, repo
  shakenfist/client-python) follows HTTP redirects silently
  (`apiclient.py:208-236`), so the new endpoint must return
  the kerbside URL **in a JSON body**, not as a 3xx redirect —
  otherwise clients transparently fetch the `.vv` and the URL
  is never surfaced to tooling that wants it.
- Kerbside's exchange machinery exists for Nova: the
  `NovaToken` resource (`kerbside/api.py:516`, route
  `/nova-console.vv`) validates an external token, calls
  `db.add_console(...)`, mints a kerbside `consoletoken` (a
  48-char random string sized for the SPICE password field,
  `kerbside/consoletoken.py:19`) and returns a `.vv`. The SF
  exchange endpoint mirrors this shape, minus the callback.
- The CLI's viewer launch already exists, but is virt-viewer
  shaped: `sf-client instance vdiconsole` writes the `.vv` to a
  temp file and runs `remote-viewer` via subprocess
  (`shakenfist_client/commandline/instance.py:636-663`). Ryll —
  our own SPICE client, which understands kerbside's `.vv`
  extensions including `host-subject` — accepts either
  `--file <path>` or `--url <URL to fetch a .vv from>`
  (ryll README, "Connect using a .vv configuration file"). The
  `--url` form means the CLI can hand ryll the kerbside
  exchange URL directly: ryll performs the one-shot token
  exchange itself and the JWT never touches disk. Ryll is not
  currently pip-installable; the precedent for shipping a Rust
  binary via pip is kerbside-proxy's maturin bin wheel
  (kerbside `rust/kerbside-proxy/`, built in Docker).
- Hypervisor SPICE TLS certs are provisioned by the deployer:
  one cluster CA, per-node server certs with `cn = hostname`
  (`deploy/ansible/roles/pki_internal_ca/tasks/host_certificate.yml`).
  The cert subject is not currently recorded anywhere the API
  exposes. Kerbside's scrape yields `host_subject: None` for
  SF consoles today, so the proxy's host-subject enforcement
  (which kerbside ships as of v0.4.0) protects only
  static/oVirt paths, not SF.

## Mission and problem statement

Give Shaken Fist the best Kerbside integration of the three
supported clouds:

1. **Per-instance authorisation, enforced by Shaken Fist.** A
   user may open a proxied console for an instance if and only
   if they hold a namespace credential that
   `requires_instance_ownership` accepts for that instance.
2. **No connect-time coupling.** Kerbside must be able to
   authorise a console open without calling the Shaken Fist
   API: token validation is an offline signature check plus a
   local database lookup.
3. **Defense in depth.** Kerbside takes backend connection
   details (hypervisor IP, ports, host subject) only from its
   own scraped database, never from token claims — a forged or
   replayed token cannot point the proxy at an arbitrary
   backend.
4. **Seamless client UX.** `sf-client instance vdiconsole`
   lands the user in a running viewer session with all of this
   happening under the hood: mint the token, hand ryll the
   exchange URL, session opens. The token plumbing — and
   ideally the viewer installation itself, via a
   pip-installable ryll — is invisible.
5. The existing direct-to-hypervisor `.vv` path keeps working
   for clusters with no Kerbside deployed.

Out of scope for this plan (deferred to a later deployment
phase, see Future work): ansible-collection deployer support,
including a dedicated `kerbside` infrastructure group modelled
on the database tier.

### Token flow (the design in one place)

```
sf-client                Shaken Fist API              Kerbside
   |                           |                          |
   |-- GET vdiconsoleproxy --->|                          |
   |   (namespace JWT)         | ownership check          |
   |                           | mint Ed25519 JWT         |
   |<-- {url, expires_at} -----|  (aud=kerbside, sub=     |
   |                           |   instance, jti, exp)    |
   |                           |                          |
   |-- GET /sf-console.vv?token=<jwt> ------------------->|
   |                           |    verify sig (cached    |
   |                           |    pubkey), check aud/   |
   |                           |    exp, jti unused,      |
   |                           |    console in scrape DB  |
   |<-- .vv (password = kerbside consoletoken) -----------|
   |                           |                          |
   |== SPICE connect to kerbside, existing machinery ====>|
```

The JWT is a bearer capability for **one HTTP exchange**, not
a session credential. It never appears in the `.vv` (the SPICE
password field cannot hold a ~250-char JWT; kerbside's
existing 48-char consoletoken fills that role). Kerbside
persists only the token's `jti` for replay rejection until
`exp`; Shaken Fist stores nothing per-token and mints-and-
forgets, recording only an audit event.

Token claims (settled in phase 0, strawman here):

- `iss`: the SF cluster's `config.ZONE` (matches existing JWT
  practice, `util/access_tokens.py`).
- `aud`: the kerbside deployment's public base URL (from the
  new `KERBSIDE_URL` cluster config). Kerbside rejects tokens
  whose `aud` is not itself.
- `sub`: the instance UUID.
- `sf:namespace`: the owning namespace (audit/display only —
  kerbside must not make authorisation decisions from it).
- `iat`, `exp`: TTL default of a few minutes
  (`KERBSIDE_TOKEN_DURATION`, phase 0 decides the default).
- `jti`: UUID4, kerbside's replay-rejection handle.
- Header `kid`: which cluster signing key signed this, so
  rotation is possible without a flag day.

## Alternatives considered

### Nova-parity callback validation

Kerbside receives an opaque token and calls back to a new SF
"validate console token" admin API, exactly as it does for
Nova. Rejected: it reintroduces the availability coupling
(SF API down means no new consoles), requires SF to persist
issued tokens, and buys nothing — we control both sides, so
offline signature verification is available to us in a way it
was not for Nova.

### Push model

SF calls a kerbside API to register each issued token, so
kerbside validates locally. Rejected: inverts the dependency
(SF must hold kerbside credentials and reach its API),
requires new kerbside API surface, and fails when kerbside's
control plane is briefly unreachable even though its data
plane is fine. Signed tokens achieve the same local validation
with no push channel.

### SF builds the proxy `.vv` itself

The vdiconsoleproxy endpoint could skip the kerbside HTTP
exchange and emit a `.vv` pointing at kerbside directly, with
the JWT... somewhere. Rejected on two grounds: the SPICE
password field cannot carry a JWT (48-char kerbside tokens
exist precisely because the field is tiny), and SF would need
to know kerbside's public CA cert, ports, and proxy
host-subject to author the `.vv` — smearing kerbside's TLS
identity into SF's config. Kerbside stays the author of its
own `.vv`, as it is for Nova.

### Kerbside-side ACLs on scraped consoles

Keep pure scraping and grow a per-console ACL model inside
kerbside, mapping kerbside (Keystone) users to consoles.
Rejected: duplicates SF's identity model in a second database
that must be kept in sync, and kerbside's identity layer is a
single coarse Keystone group with no relationship to SF
namespaces. Authorisation belongs to the cloud that owns the
instance.

## Open questions

These are settled in phase 0 before implementation starts.
Recommendations are recorded inline.

1. **Signing key custody and bootstrap.** Recommendation:
   store the keypair in `cluster_config` (the same custody as
   `AUTH_SECRET_SEED`, which gates strictly more power), as a
   JSON value holding `{kid, private_pem, public_pem,
   created}`. Generate lazily on first use with an atomic
   `INSERT` on the `key_name` primary key (loser of the race
   re-reads), plus an `sf-ctl` subcommand for explicit
   pre-generation and rotation. Rotation model: publish
   current + previous public keys, sign with current only.
2. **Token TTL default.** Recommendation: 300 seconds. Long
   enough for a human to click through a saved URL, short
   enough that the replay-cache table stays trivial. The
   deeper revocation question is bounded by scrape freshness
   anyway (a deleted instance vanishes from kerbside's console
   table within a minute, and live sessions are governed by
   kerbside's existing termination machinery, not the JWT).
3. **Scrape scope.** Today `ShakenFistSource` scrapes exactly
   one namespace (`sources/shakenfist.py:62`), which would
   make the token flow work only for instances in that
   namespace. Recommendation: when the source's credential is
   the `system` namespace, scrape cluster-wide via
   `get_instances(all=True)` (already supported by client and
   server). Phase 0 should decide whether per-namespace
   scraping survives as a filter option.
4. **How kerbside learns which SF source a token belongs to.**
   Recommendation: mirror `NovaToken` — iterate configured
   `shakenfist` sources and attempt verification against each
   source's cached public keys; `aud` must equal kerbside's
   own `PUBLIC_FQDN`-derived URL. On an unknown `kid`, refetch
   the source's published keys once before rejecting (this is
   also the rotation path).
5. **Fate of the direct-to-hypervisor `.vv` endpoint.** It is
   ownership-gated and remains correct for proxyless clusters.
   Recommendation: keep it unconditionally for now; add a
   cluster config knob to disable it only if operators ask.
   Decide in phase 0 whether the *client* should prefer the
   proxy path automatically when the capability is advertised.
6. **Kerbside admin-UI exposure of SF consoles.** Anyone in
   kerbside's Keystone access group can currently mint a
   session for any scraped console via
   `/console/proxy/<source>/<uuid>/console.vv`. With
   cluster-wide scraping this surface grows to every SF
   instance. Recommendation: keep the admin path (it is the
   operator break-glass and is audited), but record the
   decision explicitly; a follow-on could restrict `.vv`
   issuance for SF-sourced consoles to an admin subset of the
   access group.
7. **host_subject provenance.** The deployer provisions
   per-node SPICE certs with `cn = hostname`, but nothing
   exposes the subject via the API. Options: (a) kerbside
   synthesises `CN=<node hostname>` per a per-source config
   flag; (b) each SF node records its actual
   `server-cert.pem` subject into node attributes at daemon
   startup, and the API exposes it for the scrape.
   Recommendation: (b) — it is accurate for custom PKI too and
   keeps kerbside free of deployment assumptions; (a) is
   acceptable as an interim if (b) grows too large, but note
   host-subject verification is exact-match on the full
   subject, so synthesis is brittle against deployer changes.
8. **Endpoint and capability naming.** Strawman used below:
   route `GET /instances/<ref>/vdiconsoleproxy`, capability
   string `vdi-console-proxy`, pubkey route
   `GET /admin/vditokenpubkey`, kerbside route
   `/sf-console.vv`. The kerbside route name is **decided**
   (operator preference, 2026-07-19): `sf-console` rather
   than the full `shakenfist-console`, accepting the
   asymmetry with the existing `/nova-console.vv` for
   brevity. Phase 0 confirms or amends the rest.
9. **Ryll distribution mechanism.** A maturin bin wheel (the
   kerbside-proxy pattern) is the strawman, published as a
   standalone `shakenfist-ryll` package that client users
   opt into via an extra (`pip install
   shakenfist-client[vdi]`). The risk is manylinux
   compliance: unlike kerbside-proxy (a headless network
   daemon), ryll's GUI build links winit's X11/Wayland/xkb
   surface, cpal's ALSA, and rfd's dialog backend — some of
   which are outside the manylinux allowed-library set.
   Phase 3 must prototype the wheel build early and, if
   auditwheel cannot be satisfied, fall back to an installer
   that fetches a ryll release binary (GitHub releases) into
   a user-local path, keeping the `sf-client` UX identical.
   Decide in phase 0 only the *shape* (standalone package +
   extra, plus a PATH/remote-viewer fallback chain); let the
   phase-3 prototype pick the mechanism.
10. **Viewer selection order in the CLI.** Recommendation:
   packaged ryll if importable/installed, else `ryll` on
   `$PATH`, else `remote-viewer` (which cannot follow the
   exchange URL, so the CLI fetches the `.vv` to a temp file
   for it exactly as today), with a `--viewer` override.
   `remote-viewer` must remain a working path — the `.vv` we
   emit is standard virt-viewer format.

## Execution

| Phase | Repo | Plan | Status |
|-------|------|------|--------|
| 0. Decisions and token format | shakenfist | PLAN-kerbside-vdi-tokens-phase-00-decisions.md | Not started |
| 1. Cluster signing key + pubkey publication | shakenfist | [PLAN-kerbside-vdi-tokens-phase-01-signing-key.md](PLAN-kerbside-vdi-tokens-phase-01-signing-key.md) | Complete |
| 2. vdiconsoleproxy endpoint | shakenfist | [PLAN-kerbside-vdi-tokens-phase-02-proxy-endpoint.md](PLAN-kerbside-vdi-tokens-phase-02-proxy-endpoint.md) | Complete |
| 3. Pip-installable ryll | ryll | PLAN-pip-distribution.md (in ryll's docs/plans/) | Complete |
| 4. Client, CLI, and viewer launch | client-python | PLAN-vdi-console-tokens.md (in that repo, branch `vdi-console-tokens-client`) | Complete |
| 5. Kerbside exchange endpoint | kerbside | PLAN-kerbside-vdi-tokens-phase-05-exchange.md (in kerbside, branch `sf-vdi-tokens`) | Complete |
| 6. Cluster-wide scrape and host_subject | kerbside + shakenfist | PLAN-kerbside-vdi-tokens-phase-06-scrape.md (in kerbside) | Complete |
| 7. Functional test: SF mint path | shakenfist | PLAN-kerbside-vdi-tokens-phase-07-ci.md (in kerbside) | Complete |
| 8. Documentation | all | PLAN-kerbside-vdi-tokens-phase-08-docs.md | Complete |
| 9. Full cross-repo end-to-end + kerbside exchange lane (post-merge, real SF) | all | PLAN-kerbside-vdi-tokens-phase-09-e2e.md (in kerbside) | Complete |

### Phase 0: Decisions and token format

Settle the Open questions above and write the token format
down as a short normative spec (claims, algorithm, key
storage, rotation, error responses) inside the phase plan.
Everything downstream cites that spec. Plan at high effort —
this is where cross-repo consistency is cheapest to buy.

### Phase 1: Cluster signing key and pubkey publication (SF)

- Add `cryptography` as a direct dependency (PyJWT needs it
  for EdDSA; today SF has PyJWT but not cryptography).
- Key generation and storage per phase 0: `cluster_config`
  row(s), atomic lazy creation, `sf-ctl` management
  subcommand.
- `GET /admin/vditokenpubkey`: JSON list of
  `{kid, alg, public_pem}`. Auth mirrors `/admin/cacert`
  (`@verify_token` + `@log_token_use`, any authenticated
  namespace — the material is public-key only).
- Capability string in the root-page HTML so clients and
  kerbside can probe for support.
- Unit tests: generation atomicity (two racing creators
  converge), endpoint shape, no private material in the
  response or in any log/event.

### Phase 2: vdiconsoleproxy endpoint (SF)

- New `KERBSIDE_URL` config field (empty default = feature
  off), populated cluster-wide via `cluster_config` like
  `DNS_SERVER` is today.
- New `InstanceVDIProxyConsoleHelperEndpoint`: decorator stack
  `@verify_token` / `@arg_is_instance_ref` /
  `@requires_instance_ownership` / `@log_token_use` —
  deliberately **without** `@redirect_instance_request`, since
  minting needs no node-local state and must work from any API
  node.
- Guards: 404-equivalent when `KERBSIDE_URL` is unset; the
  same instance-state and `video.vdi` checks the scrape
  applies (`state == created`, vdi type starts with `spice`).
- Mint the JWT per the phase-0 spec; respond
  `{url: '<KERBSIDE_URL>/sf-console.vv?token=<jwt>',
  expires_at: ...}` as JSON (no redirect — the client follows
  redirects silently and the URL must be surfaceable).
- Audit event on the instance (`EVENT_TYPE_AUDIT`) recording
  `jti` and `kid` but never the token itself.
- Unit tests: ownership enforcement (namespace A cannot mint
  for namespace B's instance), disabled-feature path, claim
  correctness, TTL.

### Phase 3: Pip-installable ryll (ryll)

The goal: `pip install shakenfist-ryll` (typically via the
`shakenfist-client[vdi]` extra) puts a working `ryll` binary
on the venv's PATH, the way kerbside's pip install ships
`kerbside-proxy`.

- Prototype a maturin bin wheel early, mirroring
  kerbside-proxy's Docker-wrapped build (ryll builds in
  Docker already; no native toolchain on dev hosts). The
  open risk is manylinux compliance for a GUI binary — see
  open question 9. If auditwheel cannot be satisfied, pivot
  to the fallback there (a thin `shakenfist-ryll` package
  that fetches the matching GitHub release binary at install
  or first run) without changing the client-facing contract:
  either way the package exposes a `ryll` executable.
- CI: build the wheel in ryll's existing rust.yml lane;
  release automation alongside ryll's crate/binary releases.
- This phase deliberately contains all the packaging
  weirdness so that phase 4 sees only "a `ryll` binary is
  installed or it is not".

### Phase 4: Client, CLI, and viewer launch (client-python)

- `get_vdi_console_proxy(instance_ref)` returning the parsed
  JSON, and a convenience that fetches the kerbside URL and
  returns `.vv` text (plain `requests.get` — kerbside's
  endpoint takes no SF auth).
- `get_vdi_token_public_keys()` for kerbside's source driver.
- CLI: `sf-client instance vdiconsole` becomes the seamless
  path — when the `vdi-console-proxy` capability is
  advertised, mint the token and launch the viewer; with
  ryll, pass the exchange URL via `ryll --url` (no temp
  file, token never on disk); with `remote-viewer`, fetch
  the `.vv` to a temp file as today. Viewer selection per
  open question 10 (packaged ryll → PATH ryll →
  remote-viewer, `--viewer` / `--direct` overrides).
  `vdiconsolefile` keeps working for both paths.
- Packaging: `vdi` extra depending on `shakenfist-ryll`
  (from phase 3).
- Release note: kerbside's phase 6 consumes
  `get_vdi_token_public_keys()`, so a client release
  precedes it (kerbside pins `shakenfist-client>=`).

### Phase 5: Kerbside exchange endpoint (kerbside)

- New `SfToken` resource at `/sf-console.vv`, mirroring
  `NovaToken` (`api.py:516`) with validation replaced by:
  signature verification against cached per-source public
  keys (EdDSA; refetch-once on unknown `kid`), `aud`/`exp`
  checks, and single-use `jti` enforcement.
- Alembic migration: a `sf_token_jtis` replay table (`jti`
  primary key, `expiry`); reject exchanges whose `jti` is
  present; reaper in the maintenance loop mirroring
  `_reap_expired_console_tokens` (`main.py:185`).
- Console lookup strictly from the scraped `consoles` table by
  `(source, sub)` — claims are never used for backend
  addressing. 404 when the console is not (yet) scraped.
- Issue the kerbside `consoletoken` and `.vv` exactly as the
  Nova path does; audit events for accepted and rejected
  exchanges.
- Unit tests: replay rejection, expired token, wrong `aud`,
  unknown `kid` (with and without successful refetch), forged
  signature, unscraped console.

### Phase 6: Cluster-wide scrape and host_subject (kerbside + SF)

- Scrape scope per phase 0: `get_instances(all=True)` under a
  system credential; public-key fetch added to
  `ShakenFistSource.__init__` beside `get_cluster_cacert()`,
  with periodic refresh.
- host_subject per phase 0's decision: SF-side node attribute
  publication and/or kerbside-side synthesis, then populate
  `host_subject` in the scrape yield
  (`sources/shakenfist.py:85`) so the proxy's existing
  enforcement covers SF backends.
- The SF half of this phase (node cert-subject publication)
  lands on the SF branch with its own phase plan section.

### Phase 7: Functional test — SF mint path

Narrowed to the SF-side mint test; the kerbside functional lane
moved to phase 9 (see below and the phase-7 plan in kerbside for
the grounding).

- SF: a `cluster_ci_tests` functional test
  (`deploy/shakenfist_ci/cluster_ci_tests/test_vdi_tokens.py`)
  asserting the minting path (ownership gate, JSON shape,
  verifiable signature against the published pubkey) without
  needing a kerbside deployment. Calls the REST endpoints via
  `_request_url` so it is independent of the client PR's merge
  order, and skips cleanly when `KERBSIDE_URL` is unprovisioned
  (it is process-cached at SF start, so it is set for real by
  phase 9's deployment; the test then activates).

### Phase 8: Documentation

Done. The detailed plan of record for this phase is
`PLAN-kerbside-vdi-tokens-phase-08-docs.md` (authored in the kerbside repo
alongside phases 5–7). The four repos' doc PRs are independent.

- SF: user guide (how to open a proxied console), operator
  guide (enabling `KERBSIDE_URL`, key rotation runbook),
  `ARCHITECTURE.md` / `AGENTS.md` touch-ups.
- Kerbside: rewrite the Shaken Fist section of
  `docs/console-sources.md` (it currently documents only the
  scrape model), update `docs/proxy-architecture.md` and the
  plan index.
- client-python: README/CLI help, including the viewer
  selection chain and the `[vdi]` extra.
- ryll: README install section (pip alongside the existing
  build-from-source instructions).

### Phase 9: Full cross-repo end-to-end (post-merge, real SF)

Runs once the four PRs are on `develop`, so kerbside CI can
install SF and the client at HEAD. Absorbs the kerbside
exchange/proxy lane that phase 7 originally intended: a
direct-qemu-style exchange against a static console is
infeasible because kerbside only trusts `type: shakenfist`
sources for token verification and the maintenance loop reaps
any console a live scrape did not yield — so a real SF (not a
static or hand-seeded console) is required.

- Deploy a single-node SF in CI (as realized: the
  `build-smoke-cluster` action, not getsf), provision `KERBSIDE_URL`
  (deploy-time, since it is process-cached) and a signing key,
  point a kerbside at it, and drive the full flow: mint token via
  client, exchange for `.vv`, connect through the proxy, assert
  session audit/teardown.
- Adversarial coverage against the live app: replayed token,
  expired token, wrong audience, unknown kid, cross-namespace
  mint attempt.
- The SF mint path (phase 7's `test_vdi_tokens.py`) activates
  here too, once `KERBSIDE_URL` is provisioned.

## Dependencies on other plans

None hard. This plan is independent of the BYO-MariaDB /
remove-primary thread — it uses `cluster_config` as it exists
today. Sequencing within this plan: phases 1→2 are ordered on
the SF side; phase 3 (ryll packaging) is independent and can
start immediately — it is also the highest-uncertainty item,
so starting its prototype early buys information cheaply;
phase 4 needs phases 2 and 3 (and degrades gracefully if 3
slips, via the remote-viewer fallback); phase 5 needs only
the phase-0 spec plus phase 1 (a pubkey endpoint to fetch in
tests can be faked until then); phase 6 needs a released
client containing phase 4's `get_vdi_token_public_keys()`. A
kerbside release (v0.5.0) ships phases 5–6; the
lockstep-release discipline from the rust-proxy transition
applies if any proxy-contract change sneaks in (none is
expected — this plan is entirely control-plane).

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the phase plan, at the recommended effort level
   and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's summary
   describes what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether
   the brief was insufficient (improve it) or the model was
   too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

Cross-repo caution: a sub-agent works in exactly one
repository per step. Steps that "need" to touch two repos are
a sign the phase plan split a seam wrongly — go back to the
plan.

### Planning effort

The master plan and phase 0 are high-effort work. Phases 1, 2
and 5 involve key custody, authorisation gates, and replay
semantics — plan those at high effort too. Phase 4 (client
methods mirroring existing ones, launcher chain) and phase 8
(docs) can be planned at medium effort. Two items carry the
plan's schedule risk and deserve design-document treatment
rather than checklists: phase 3's manylinux question (spike
it first) and phase 7's SF-lane-in-kerbside-CI step.

### Step-level guidance

Each phase plan should include the step table from
`PLAN-TEMPLATE.md` (`| Step | Effort | Model | Isolation |
Brief for sub-agent |`). Front-load research into briefs: cite
file and line for the pattern each step mirrors (e.g. "mirror
`AdminClusterCaCertificateEndpoint` at
`external_api/admin.py:47`", "mirror the `NovaToken` resource
at `kerbside/api.py:516`"). Security-sensitive steps (key
generation, signature verification, replay cache) should
recommend opus and include the adversarial test list in the
brief.

### Management session review checklist

Per `PLAN-TEMPLATE.md`, plus specific to this plan:

- [ ] No private key material in API responses, logs, or
      events (grep the diff for `private` near logging calls).
- [ ] Kerbside never reads backend addresses from JWT claims.
- [ ] Every new endpoint carries the intended decorator stack
      — in particular `requires_instance_ownership` on the
      minting endpoint and *no* `redirect_instance_request`.
- [ ] `pre-commit run --all-files` passes in whichever repo
      changed (SF's runs flake8, stestr, mypy; kerbside's runs
      its tox envs).

## Administration and logistics

### Success criteria

We will know this plan is complete when:

* A user holding only a namespace key can run
  `sf-client instance vdiconsole <instance>` against a
  Kerbside-enabled SF cluster and land in a running ryll
  session for their own instance with no other manual steps
  (given `pip install shakenfist-client[vdi]`) — and cannot
  get a console for an instance in another namespace.
* The same command still works with only `remote-viewer`
  installed, and still works direct-to-hypervisor on
  clusters with no `KERBSIDE_URL` configured.
* Kerbside opens that console with zero Shaken Fist API calls
  on the exchange path (signature check + local DB only).
* A replayed exchange URL is rejected; an expired one is
  rejected; both rejections are audited on the kerbside side,
  and every mint is audited on the SF side.
* SF-sourced consoles carry a `host_subject` and the proxy
  enforces it, closing the gap where enforcement covered only
  static/oVirt sources.
* CI proves the end-to-end flow (kerbside SF lane) and the
  minting authorisation gate (SF cluster_ci), and the
  pre-push checks pass in all four repos.
* Documentation in all four repos reflects the feature, and
  the plan index status tables are current.

### Future work

* **Deployer support (explicitly deferred phase).** Teach the
  ansible collection to deploy Kerbside alongside SF: a
  dedicated `kerbside` infrastructure group (modelled on the
  database tier from the BYO-MariaDB work, not "every node"),
  rendering `sources.yaml` with a system credential, writing
  `KERBSIDE_URL` into `cluster_config`, and wiring kerbside's
  own TLS material. This lands as its own plan once the code
  support here has proven itself.
* Kerbside admin-auth pluggability: kerbside's own login is
  Keystone-only (`kerbside/api.py:200`), which is an odd
  requirement for an SF-only deployment. Possibly converges
  with SF's OIDC plan (`PLAN-oidc-authentication.md`) and the
  auth-federation branch.
* Restricting kerbside's operator break-glass `.vv` issuance
  for SF-sourced consoles to an admin subset (open question 6
  records the current decision).
* Signing-key rotation automation (scheduled rotation rather
  than operator-initiated `sf-ctl`).

### Bugs fixed during this work

(To be filled in as encountered. A scan of the shakenfist and
kerbside issue trackers for related open bugs should happen
during phase 0 and be recorded here.)

### Documentation index maintenance

On creation of this plan: add a *Master plans* row to
`docs/plans/index.md` and an entry to `docs/plans/order.yml`
in the shakenfist repo, and a cross-reference entry to
kerbside's `docs/plans/index.md`. As phases complete, update
the Execution table above and both indexes.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
