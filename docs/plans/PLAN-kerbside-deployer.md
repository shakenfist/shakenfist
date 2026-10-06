# Deploy Kerbside from the ansible collection

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (the
`shakenfist.shakenfist` collection's role/playbook split, the
restart-on-change contract, the `internal_ca` role, the
cluster-config play, and the VDI console token mint path), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(SPICE TLS, virt-viewer `.vv` files, gunicorn, alembic,
systemd), research as needed to give a confident answer. Flag
any uncertainty explicitly rather than guessing.

This plan spans four repositories, but most of the work lands in
this one:

- **shakenfist** (this repository): the collection under
  `shakenfist/deploy/collection/`, the deploy playbook
  `examples/_shared/site.yml`, the `shakenfist_ci` functional
  suite, and the operator guide.
- **shakenfist/actions**: the CI topologies,
  `tools/ci-make-inventory.py`, `tools/deploy-collection.sh` and
  the reusable `smoke-cluster` workflow. By precedent
  (`PLAN-ci-cloud-sizing.md`, remove-primary phase 7), only the
  operator pushes there. Phases that need an actions change hand
  the operator a reviewed diff.
- **kerbside**: its sf-e2e lane, and its use-case documentation
  for Shaken Fist.
- **client-python**: probably untouched. It is listed because
  `sf-client instance vdiconsolefile` is the user-visible outcome
  this plan is measured by.

This plan is the deferred "Deployer support" item from
[Kerbside VDI console tokens](PLAN-kerbside-vdi-tokens.md) (Future
work). It takes nothing that plan owns. Every code path it relies
on (the mint, the `vdiconsoleproxy` endpoint, the capability gate,
`sf-ctl ensure-kerbside-signing-key`, Kerbside's
`/sf-console.vv` exchange and its SF scrape) landed there and is
treated here as a fixed interface. A defect found in one of those
paths is fixed where that plan would have fixed it, and recorded
in both plans.

Consult `ARCHITECTURE.md` for the system architecture overview
and `AGENTS.md` for project conventions. Key references inside
the repository:

- `shakenfist/deploy/collection/README.md`: the role contract,
  under which only `site.yml` reads `groups[...]`, and the
  restart-on-change rules.
- `examples/_shared/site.yml`: the tier modelling for
  `database_node` at `:234-266` and `:311-320`; the
  `internal_ca` plays at `:388-422`; the cluster-config play at
  `:519-631`; the final sanity checks at `:678-718` (line numbers
  as of phase 3's survey).
- `roles/internal_ca/tasks/`: `host_certificate.yml` and
  `distribute_certificates.yml`.
- `roles/node/templates/config:81-89`: the `kerbside_url`
  rendering from #4024.
- `shakenfist/util/vdi_tokens.py`: the signing key.
- `docs/operator_guide/vdi_console_tokens.md`: the manual
  procedure this plan automates.

In the Kerbside repository, read these:

- `docs/installation.md`
- `docs/use-cases/shakenfist.md`
- `kerbside/config.py`
- `kerbside/sources/shakenfist.py`
- `tools/sf-e2e/`: the only place Kerbside has ever run next to
  a Shaken Fist cluster, and the closest thing to a blueprint.

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

### The feature is built, and no cluster can turn it on

The Kerbside VDI console token plan is `Complete`. A Shaken Fist
cluster with `KERBSIDE_URL` set does three things:

- advertises the `vdi-console-proxy` capability (`external_api/app.py:438-442`);
- mints short-lived Ed25519 tokens at
  `GET /instances/<ref>/vdiconsoleproxy`
  (`external_api/instance.py:2114-2134`);
- causes `sf-client instance vdiconsolefile` to fetch a
  Kerbside-routed `.vv` instead of a direct one
  (client-python, `commandline/instance.py:705, 786`).

A cluster without `KERBSIDE_URL` hands out a `.vv` that names the
hypervisor's IP and SPICE ports directly. That is goal 5 of the
tokens plan, and it is working as designed.

What does not exist is any way to get from the first state to the
second short of doing it by hand. Kerbside deployment was
explicitly deferred. shakenfist#4004 tracked it, and was closed on
2026-09-02 by the automated fix in #4024. That fix delivered only
the issue's "smallest useful increment": a `kerbside_url` variable
that renders `SHAKENFIST_KERBSIDE_URL` into `/etc/sf/config`.

The issue body itself called the rest "a real plan's worth of
work". Closing #4004 left that work with no tracker. Specifically,
nothing deploys or configures Kerbside itself, issues its
certificates, gives it a credential, or creates the signing key.

`roles/node/defaults/main.yml:63-64` says so plainly: "Deploying
Kerbside itself, and provisioning the signing key with sf-ctl
ensure-kerbside-signing-key, remain the operator's job."

The consequence was found on 2026-10-02. sfcbr, the project's own
production cluster, was still handing out direct `.vv` files a
month after the tokens plan closed, because nobody had deployed
Kerbside for it. sfcbr is deployed with this collection, and has
no `kerbside_url` and no Kerbside host.

### Where Kerbside has actually run beside Shaken Fist

Only in Kerbside's own `sf-e2e-functional.yml` lane, through the
`shakenfist/actions/deploy-kerbside-on-shakenfist` composite
action and Kerbside's `tools/sf-e2e/{provision-sf.sh,
deploy-kerbside.sh, gen-sources.py}`. Its own documentation
(`kerbside docs/use-cases/shakenfist.md:304-309`) lists the first
two of the shortcuts that make it unfit to copy; the scripts show
the rest:

- Kerbside is co-located on the SF primary and talks to it over
  loopback, with an `http://127.0.0.1:13002` audience.
- Its TLS comes from Kerbside's `generate-tls.sh`, not from the
  cluster CA.
- It runs unsupervised: daemonised gunicorn plus a background
  shell job.
- It authenticates with the deploy system key.
- It configures SF with `sf-ctl set-config` rather than with the
  collection variable.

Shaken Fist's own CI has never enabled the feature.
`shakenfist_ci/cluster_ci_tests/test_vdi_tokens.py:84-100` skips
its mint test, and the capability test only asserts the "off"
branch.

#4097 records, as item 4, that "the ansible `kerbside_url`
variable has never been read by a running sf-api". #4093 asks for
the mint test to run somewhere. #4367 notes that Kerbside's deploy
lane has no headroom instrumentation. #4097 and #4367 are open.
#4093 was closed on 2026-10-03 by the merge of this plan's own pull
request, #4420, whose phase table said phase 4 "closes" it. GitHub
read that as a closing keyword. It was reopened the same day, since
nothing makes the mint test run yet (phase 2's survey, S7).

### What deploying Kerbside involves

All references below are to the Kerbside repository unless marked
otherwise.

**Packages.** Kerbside is two PyPI packages:

- `kerbside`: pure Python, providing the API, the daemon and the
  alembic migrations.
- `kerbside-proxy`: a maturin `bindings="bin"` wheel carrying the
  Rust proxy, manylinux x86_64 and aarch64 only, with no sdist.

A release pins the proxy exactly (`installation.md:46-66`).
`mysqlclient` builds from source, so the host needs
`build-essential pkg-config python3-dev libmariadb-dev-compat
libxml2-dev libxslt1-dev`.

`shakenfist_client` (≥0.8.3) is not a dependency of `kerbside`,
but the SF source cannot fetch signing keys without it
(`sources/shakenfist.py:37-40`).

**Processes.** Two must be supervised; the third is supervised
for you:

- `gunicorn kerbside.api:app` serves the REST API, the admin UI
  and the `/sf-console.vv` exchange. Its port is gunicorn's
  `--bind`, and 13002 is the convention.
- `kerbside daemon run` runs the 60-second scrape and maintenance
  loop. It serves gRPC on the `API_SOCKET_PATH` unix socket and
  supervises the Rust proxy as a child process
  (`main.py:318-377`).
- The Rust proxy listens on 5900 (TLS) and 5901 (plaintext, which
  redirects to TLS), and serves Prometheus metrics on loopback
  13003.

No systemd units exist anywhere; Kerbside's own
`PLAN-demo-install.md:455-457` lists them as future work.

**Configuration.** `/etc/kerbside/kerbside.ini` holds a
`[kerbside]` section in which every key becomes `KERBSIDE_<KEY>`,
and environment variables win (`config.py:10-39`). A literal `%`
must be doubled, and a malformed file exits 0 (kerbside#313).
Eight settings are the minimum (`installation.md:150-159`):

- `sql_url`
- `auth_secret_seed`: its default sentinel is forgeable
  (kerbside#131).
- `sources_path`
- `public_fqdn`
- `cacert_path`
- `proxy_host_cert_path`
- `proxy_host_cert_key_path`
- `proxy_host_subject`

`sf_console_token_audience` must equal Shaken Fist's
`KERBSIDE_URL` byte for byte. When it is empty, Kerbside derives
`https://<PUBLIC_FQDN>`, which is wrong whenever the API is not on
port 443.

**The SF source.** It is an entry in `sources.yaml`:

```yaml
- source: <name>
  type: shakenfist
  url: <SF API base URL>
  username: system          # non-system namespaces do not work (kerbside#444)
  password: <a system-namespace key>
  ca_cert: |                # inline PEM, not a path
    ...
```

Kerbside compares `ca_cert` after `rstrip()` with SF's
`GET /admin/cacert`, which serves the node-local
`/etc/pki/libvirt-spice/ca-cert.pem` (`external_api/admin.py:48-63`
here). A mismatch errors the source.

The client Kerbside builds for the SF API is given no CA bundle
(`sources/shakenfist.py:19-23`, kerbside#136). An SF API behind a
private CA must therefore be in the system trust store.

**Database.** Kerbside needs MySQL or MariaDB; sqlite is not
supported. It has its own schema, migrated with
`kerbside db upgrade` (idempotent). The sf-e2e lane created a
`kerbside` database and user on the SF cluster's MariaDB, which
works, but the collection treats MariaDB as externally supplied
and holds no admin credentials for it.

**TLS.** Three separate concerns:

- The **client-to-proxy leg**: Kerbside's own certificate and key,
  plus `CACERT_PATH`. That CA is embedded in every `.vv`, and
  `PROXY_HOST_SUBJECT` must equal the certificate's subject.
  rustls rejects v1 certificates.
- The **API's HTTPS**: Kerbside has no setting for it, so it needs
  a reverse proxy or gunicorn's `--certfile`.
- The **backend leg to the hypervisors**: verified against the
  source's `ca_cert`, and pinned to each node's published
  `spice_server_cert_subject`.

**Authentication.** Admin login is Keystone-only
(`api.py:179-256`, kerbside#300). That blocks the admin UI and
REST API, including session termination, in a deployment with
only Shaken Fist. It does not block the user path:
`/sf-console.vv` takes the SF token as its credential, and the
proxy takes the console token in the `.vv`.

### What the collection offers to build on

**Tiers.** Inventory groups are mapped onto plain role variables
in `site.yml` only. `database_node` is the template:

- `groups.get('database_node', [])` becomes `database_tier_hosts`;
- a per-host `node_is_database_node` flag is set;
- a cluster-wide reduction is computed;
- `argument_specs` validates the result.

The `storage` group is the one precedent for an *optional* group.
No group yet models a host that is not in `allsf`.

**The internal CA.** `internal_ca` creates a CA on the control
node at `/srv/shakenfist/pki/CA/<deploy_name>-ca-cert.pem`. It
issues each `allsf` host a SPICE certificate with
`cn = {{ hostname }}`, `tls_www_server` and no SAN. It installs
those certificates to the **hard-coded** directory
`/etc/pki/libvirt-spice/` (`distribute_certificates.yml:12-55`).

**The cluster-config play.** It runs once, delegated to the first
database-tier host, before the register play. It runs these
`sf-ctl` commands:

- `set-config`, for the seed, the MTU, DNS, the proxy and
  `extra_config`;
- `bootstrap-system-key --key-from-stdin deploy`.

`sf-ctl bootstrap-system-key` mints an unscoped (wildcard) key in
the `system` namespace (`client/ctl.py:173-192`). No `sf-ctl`
path mints a scoped key.

**Restart-on-change.** A change to `/etc/sf/config`, to code or to
a unit restarts that node's daemons. A change made with
`set-config` does not.

**Where cluster_config wins.** A `cluster_config` row overrides
`/etc/sf/config` (`config.py:178-195`). An operator who followed
`vdi_console_tokens.md` and ran `sf-ctl set-config KERBSIDE_URL`
therefore silently shadows whatever the collection later renders.

### Defects found while writing this plan

- **SPICE certificates never renew, and expire after a year.**
  `host_certificate.yml:6-14` stats and expiry-checks
  `/etc/pki/libvirt-spice/server_cert.pem` (underscore), but
  `distribute_certificates.yml:52` writes `server-cert.pem`
  (hyphen). The renewal branch therefore never fires. Phase 1's
  survey found three more faults behind that one:
  - the `when:` reads `.meta.stdout`, which does not exist, so it
    would crash;
  - its condition is inverted;
  - it moves the certificate aside under the key's name.

  Fixing only the filename would have turned a silent no-op into a
  failure on every deploy. The host template also sets no
  lifetime, so certtool's 365-day default applies. sfcbr's sf-1
  certificate expires on 2027-07-11. Every collection-deployed
  cluster's hypervisor SPICE TLS stops verifying a year after its
  first deploy, and redeploying does not help. The private key is
  installed `0444`. Phase 1 fixes the renewal and the lifetime,
  and records the key mode as future work; see its "What the
  survey found" section.
- **`vdi_console_tokens.md:172` says** `/admin/vditokenpubkey` is
  served "to admin callers". It carries only `@log_token_use`
  (`admin.py:78-101`), so any authenticated caller can read it.
  The key is public, so this is a documentation error rather than
  a disclosure. Phase 6 fixes the doc.
- **Kerbside's `installation.md:96-100`** says the API and the
  daemon must be co-located because they share a unix socket. In
  the code only the daemon and the Rust proxy use
  `API_SOCKET_PATH`; the API reaches everything else through the
  database. Phase 6 raises it in the Kerbside repository. It
  matters here only because it would otherwise argue against the
  split units in phase 3.

## Mission and problem statement

An operator who adds hosts to a `kerbside` inventory group and
sets a handful of variables gets a cluster on which
`sf-client instance vdiconsolefile` returns a Kerbside-routed
`.vv`. One run of `site.yml` must produce all of the following:

- Kerbside installed and running under systemd on those hosts;
- its schema migrated;
- its proxy certificate issued from the cluster CA, or supplied
  by the operator;
- its SF source scraping the cluster with a dedicated credential;
- the signing key minted;
- `KERBSIDE_URL` rendered and picked up by a restarted `sf-api`.

Re-running `site.yml` must leave all of that undisturbed when
nothing changed. Shaken Fist's merge queue must prove it, with
Kerbside on a host other than the one serving the API it scrapes.

The `kerbside_url`-only path from #4024 stays supported, for an
operator who runs Kerbside some other way. It also gains the
signing-key step, which today is manual even there.

Out of scope:

- **Keystone and the Kerbside admin UI** (kerbside#300). The
  deployer leaves the `KEYSTONE_*` settings unset and documents
  what that costs.
- **Installing MariaDB.** As with Shaken Fist, the database is
  bring-your-own.
- **Terminating HTTPS for Kerbside's API.** As with `sf-api`, that
  belongs to the operator's load balancer.
- **Automated signing-key rotation.**
- **A least-privilege scoped credential for the scrape.** It needs
  a new `sf-ctl` option and kerbside#444.
- **Testing more than one Kerbside host.** The structure allows
  it.
- **The Kolla-Ansible deployment path** (kerbside-patches), which
  serves OpenStack.

## Open questions

Each has the decision this plan takes if nobody answers.

### 1. Does a Kerbside host have to be a Shaken Fist node?

No. The `kerbside` group is independent of `allsf`. It may
overlap it (co-location) or not (a dedicated host).

The role must therefore not depend on anything the `node` role
sets up. It installs its own packages and venv. Its certificate
is issued by its own `internal_ca` play rather than arriving as a
side effect of `allsf` membership.

Co-location with a hypervisor is safe on ports. SF draws VDI
ports from 30000-50000 (`instance.py:1756`), well clear of the
proxy's 5900/5901 and the API's 13002. A preflight assert still
checks that the configured Kerbside ports are below 30000.

**Default:** independent group, overlap allowed. CI tests the
co-located-but-not-API-host case (phase 4).

### 2. Who owns the systemd units?

**Default:** the collection templates `kerbside-api.service`
(gunicorn) and `kerbside-daemon.service` now, following the shape
of the `sf-*` units and their restart-on-change registration.

The longer-term home is Kerbside, whose demo-install plan lists
units as future work. When Kerbside ships units, the collection
should switch to them; that is recorded under Future work. Two
units rather than one, because the API and the daemon are
independent processes (see the third defect above). Splitting
them lets a later topology put the API behind a different load
balancer from the proxy.

### 3. Does the collection create Kerbside's database?

**Default:** no. The collection takes `kerbside_sql_url` as a
secret variable, and runs `kerbside db upgrade` once per deploy
on the first Kerbside host. Creating the database and user is
documented as an operator step, like Shaken Fist's own.

Phase 4's CI topology creates the database the same way the
sf-e2e lane does today.

*Rejected:* reusing the `mariadb_*` credentials to create the
database. They are an application user's credentials, not an
admin's, and widening them would undo what BYO-MariaDB settled.

### 4. Where does the proxy's certificate come from?

**Default:** `internal_ca` issues it, with the proxy's public
name as CN and as a SAN. Operators may override it with
`kerbside_proxy_cert_path`, `kerbside_proxy_key_path` and
`kerbside_cacert_path`. Embrace TLS plans to make the internal CA
a dev/test convenience and the production path "operator brings
cert paths"; the override is that path, available from the
start.

`PROXY_HOST_SUBJECT` is read back from the issued or supplied
certificate on the Kerbside host, never composed from variables.
Composing it is how the string drifts from the certificate.

The format question this section originally left open is
answered by code already in the tree; phase 1's survey (S5) has
the detail. `shakenfist/node.py` `_spice_host_subject_from_cert()`
renders a subject in DER order as comma-joined `SHORT=value`
pairs, with `\` and `,` escaped. That is what Shaken Fist's own
direct `.vv` files carry as `host-subject`, for the same clients.
Kerbside's proxy does not compare it: it only logs the value, and
Kerbside's API embeds it in the `.vv`, where the SPICE client
compares it (phase 3's survey, S7). Phase 3 renders
`PROXY_HOST_SUBJECT` by the same rules, and pins its renderer to
`node.py` with a unit test. No client test is needed.

### 5. What does Kerbside authenticate to Shaken Fist with?

**Default:** a dedicated `kerbside` key in the `system` namespace,
from a required secret variable `kerbside_system_key`. It is
minted in the cluster-config play with
`sf-ctl bootstrap-system-key --key-from-stdin kerbside`.

It is not the `deploy` key, which every node's `sfrc` holds. A
dedicated key can be rotated or revoked without touching the
cluster's own credential, and shows up separately in
`log_token_use` audit events.

*Answered in phase 2's survey (S2).* `bootstrap-system-key` calls
`Namespace.add_key`, which has always overwritten a key of the same
name -- a rotation, with a new hash and a new nonce. Rotation is
changing the variable and redeploying; nothing needs adding. Because
the play mints on every deploy, as it already does the `deploy` key,
the nonce also changes on every deploy, which `shakenfist_client`
absorbs by re-authenticating once on the resulting 401.

### 6. What should happen when a `cluster_config` row shadows the variable?

**Default:** fail fast. When `kerbside_url` is set and a
`KERBSIDE_URL` row exists in `cluster_config` with a different
value, the cluster-config play stops. Its message names the row
and the `sf-ctl unset-config KERBSIDE_URL` command that fixes it.
An equal row is left alone, with a warning.

The play already unsets one obsolete row
(`RAM_SYSTEM_RESERVATION`), but unsetting an operator's row
silently is a policy choice. A redeploy should not make it.

### 7. Should the signing key be minted whenever `kerbside_url` is set?

**Default:** yes, even with an empty `kerbside` group. The BYO
case needs the key just as much. `ensure_signing_key()` is
idempotent, but last-writer-wins on a race, so it runs in the
existing `run_once` cluster-config play. That play runs before
the register play, which restarts `sf-api`. The ordering means a
restarted `sf-api` never advertises the capability with no key
behind it (`vdi_console_tokens.md:139-167`).

### 8. How many Kerbside hosts?

**Default:** structurally N. The database is Kerbside's only
cross-node bus, so N hosts work if `kerbside_public_fqdn` names a
load balancer that passes 5900/5901 through as TCP. Only N=1 is
tested, and the documentation says so. Migrations run once, on
the first host.

## Execution

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

!!! note "In this project"

    The same term is written twice: once in the phase table
    below, and once in the row this plan carries in
    `docs/plans/index.md`. Keep them in step -- the index row is
    the whole-plan status, so it only reaches `Complete` once
    every phase has been completed, abandoned or superseded.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. `internal_ca` issues certificates to any host at caller-chosen paths, and renews them | [PLAN-kerbside-deployer-phase-01-internal-ca.md](PLAN-kerbside-deployer-phase-01-internal-ca.md) | Complete | `12441652b` (#4430) |
| 2. Shaken Fist side: signing key, Kerbside credential, token duration, shadow guard | [PLAN-kerbside-deployer-phase-02-sf-side.md](PLAN-kerbside-deployer-phase-02-sf-side.md) | Complete | `80292df08` (#4442) |
| 3. A `kerbside` role and the `kerbside` group in `site.yml` | [PLAN-kerbside-deployer-phase-03-kerbside-role.md](PLAN-kerbside-deployer-phase-03-kerbside-role.md) | Complete | `b85005f0e` (#4465) |
| 4. A merge-queue lane with the feature on | PLAN-kerbside-deployer-phase-04-ci.md | Proposed | — |
| 5. Kerbside's sf-e2e lane deploys through the collection | PLAN-kerbside-deployer-phase-05-kerbside-lane.md | Proposed | — |
| 6. Documentation and close-out | PLAN-kerbside-deployer-phase-06-docs.md | Proposed | — |
| 7. Push audit | PLAN-kerbside-deployer-phase-07-push-audit.md | Proposed | — |

The `Merged` column records what put each phase on `develop`: the
merge commit of its pull request, or an explicit `first..last`
range where the phase landed directly. A phase that lands in
another repository records `<repo> <sha> (#pr)`. `—` means the
phase has not landed.

Ordering:

- Phases 1 and 2 are independent and can run in parallel.
- Phase 3 needs phase 1 for the certificate, and phase 2 for the
  credential it writes into `sources.yaml`.
- Phase 4 needs phase 3, and an operator push to
  `shakenfist/actions`.
- Phase 5 needs phase 4. It moves Kerbside's lane onto a deploy
  path SF's merge queue already proves, rather than onto one only
  Kerbside exercises.
- Phase 6 is the last implementation phase, and phase 7 audits
  them all.

### Phase 1 -- `internal_ca` issues certificates to any host

Planned in
[PLAN-kerbside-deployer-phase-01-internal-ca.md](PLAN-kerbside-deployer-phase-01-internal-ca.md).

`host_certificate.yml` and `distribute_certificates.yml` gain
parameters for:

- the file stem on the control node;
- the CN;
- DNS and IP SANs;
- the lifetime and the renewal window;
- the destination directory, file names, owner, group and modes.

They default to today's `/etc/pki/libvirt-spice/` layout, so the
existing `allsf` plays do not change.

Renewal is rewritten to work: it checks expiry on the control
node's copy, branches on the return code, and also reissues when
the template changes. Lifetime becomes an explicit setting. A
rootless scripted test, `tools/ci-test-internal-ca.sh`, runs in
the sanity-checks job and covers issue, idempotence, SANs and
both kinds of renewal.

The `site.yml` play that issues the Kerbside certificate moved to
phase 3, which defines the `kerbside` group and variables it
needs. The subject-format question needed no test (open
question 4).

Planned at high effort: renewal decides when every node's
certificate on every cluster is reissued.

### Phase 2 -- Shaken Fist side

All of this lands in the cluster-config play and the node role:

- `kerbside_token_duration`, rendered as
  `SHAKENFIST_KERBSIDE_TOKEN_DURATION` beside `kerbside_url`. It
  replaces the `extra_config` workaround that `installation.md`
  documents today.
- `sf-ctl ensure-kerbside-signing-key`, run when `kerbside_url`
  is set (open question 7).
- `sf-ctl bootstrap-system-key --key-from-stdin kerbside`, run
  when the `kerbside_system_key` variable is set (open question
  5). Not when the `kerbside` group is non-empty: that group does
  not exist until phase 3, and an operator who runs Kerbside some
  other way needs the credential without one. Phase 3 makes the
  variable required when the group is non-empty. Rotation already
  works (open question 5).
- The shadow guard from open question 6, covering
  `KERBSIDE_TOKEN_DURATION` as well as `KERBSIDE_URL`, and the
  play's own `extra_config` as well as existing rows: the
  documentation told operators to set the duration both ways.
- `sf-ctl show-config` stops redacting numeric values, as
  `redacted_config_items()` already does, so the guard can read
  `KERBSIDE_TOKEN_DURATION` without `--show-secrets`.

The cluster-config play is in `examples/_shared/site.yml`, not a
role, so it has no argument_specs. The Kerbside steps therefore
become two node role entry points that the play includes, which
gives the new variables argument_specs and makes them testable
without a cluster. The `kerbside` key must differ from
`system_key`; the entry point asserts it.

This phase alone retires every manual step in
`vdi_console_tokens.md` but the rotation runbook for an operator
who runs Kerbside some other way. It is useful even if phase 3
never lands.

Planned at high effort: it changes what the cluster-config play
does on every deploy of every cluster, including those that never
turn the feature on, so the "feature off" path needs the explicit
test the tokens plan never had (#4003, #4009, kerbside#412).

### Phase 3 -- The `kerbside` role and group

Planned in
[PLAN-kerbside-deployer-phase-03-kerbside-role.md](PLAN-kerbside-deployer-phase-03-kerbside-role.md).
Its survey corrected several claims this section used to make; the
summary below reflects them.

A new `shakenfist.shakenfist.kerbside` role with `validate`,
`bootstrap`, `config` and `register` entry points.

`validate` runs on `localhost` before any host changes. When the
`kerbside` group is non-empty, it requires:

- `kerbside_url`, `kerbside_system_key`, `kerbside_public_fqdn`,
  `kerbside_sql_url`, and a `kerbside_auth_secret_seed` that is not
  the sentinel;
- every Kerbside port below 30000;
- a non-loopback `api_url` whenever a Kerbside host is not a Shaken
  Fist node.

This is where phase 2's deferred "`kerbside_system_key` is
required when the group is non-empty" lands.

`bootstrap` creates a `kerbside` user, the apt build dependencies
and a `/srv/kerbside/venv`, and installs `kerbside_package`,
`kerbside_proxy_package` (default empty, meaning the version the
release pins), `gunicorn` and `shakenfist_client`.

`kerbside_package` defaults to a floor on the first Kerbside
release that stops returning source passwords from its API.
Kerbside 0.6.0, the newest release, returns them, and the password
is the cluster's `kerbside` system key. That release has to be cut
before phase 4.

`config` renders the following:

- `/etc/kerbside/kerbside.ini`, with `%` escaped and
  `sf_console_token_audience` always set to `kerbside_url`;
- `/etc/kerbside/sources.yaml`, with `ca_cert` slurped from the
  file `/admin/cacert` actually serves;
- both files `root:kerbside 0640`, and checked with `validate:`
  before they replace a working copy (kerbside#313, #465);
- two units wanted by `multi-user.target`, never `sf.target`;
- `PROXY_HOST_SUBJECT`, computed from the installed certificate by
  a script that a unit test pins to `node.py`'s
  `_spice_host_subject_from_cert()`.

The proxy certificate comes from `internal_ca` (`cert_name:
<host>-kerbside`, CN and DNS SAN `kerbside_public_fqdn`,
`/etc/kerbside/pki`, key `0640 root:kerbside`), unless the operator
supplies all three override paths.

`register` runs `kerbside db upgrade` once, then restarts both
units when a desired-state hash differs from the one recorded on
the host. That hash covers code, configuration, units and the
certificate files, so a renewal counts as a change. The hash is
written only after readiness succeeds.

Readiness is a fresh `sf_token_keys.fetched_at` for the source, read
through Kerbside's own database module. It is not "the source is
healthy": a source that has never scraped is not errored, and a
key-fetch failure does not error it. Kerbside's sf-e2e lane polls
the same rows (`deploy-kerbside.sh:193-224`).

`site.yml` does the following:

- widens the reachability preflight to include `kerbside` hosts, which
  today never enter `reachable`;
- runs `validate` beside the tier asserts;
- deploys Kerbside in a play after the final sanity checks, so `sf-api`
  is proven up and the key and credential exist before the first
  scrape.

A Kerbside host outside `allsf` is already excluded from every Shaken
Fist play, including the sanity checks (`site.yml:678-718`), because
they all target `allsf:&reachable`. The phase's rootless test checks
that with `--list-hosts` against a test inventory.

`register` cannot run without a cluster. Its first real run is
phase 4's lane, and the phase's documentation says so.

Planned at high effort: this is the bulk of the plan, and
restart-on-change has to be right for a service whose failure
mode (kerbside#313, a malformed INI exits 0) defeats systemd's
`Restart=on-failure`.

### Phase 4 -- A merge-queue lane with the feature on

The changes to `shakenfist/actions`, handed to the operator as a
reviewed diff:

- a `kerbside` group in a topology. The default is `slim-tier`,
  with Kerbside on a tier node that is not the first database
  host, so the scrape crosses hosts;
- an extra-vars file in `deploy-collection.sh`, replacing the
  growing list of positional arguments;
- the database and user creation, mirroring
  `deploy-kerbside.sh:93-99`;
- a second `site.yml` run, after which no `sf-*` or `kerbside-*`
  unit may have restarted (#4459: restart-on-change has never been
  tested).

Before it can run, a Kerbside release that stops returning source
passwords must exist, because phase 3's `kerbside_package` default
requires it.

In this repository:

- a merge-queue job that turns the feature on, with
  `headroom_gate: false` until it is measured (#4367);
- `test_vdi_tokens.py`'s mint test un-skipped when the capability
  is advertised (#4093);
- a new exchange test that does all of the following:
  - fetches a `.vv` through `vdiconsoleproxy` and Kerbside's
    exchange;
  - asserts it names Kerbside, not the hypervisor;
  - asserts its `host-subject` equals the proxy certificate's;
  - completes a TLS handshake to the proxy port against the
    embedded CA.

That closes #4097 item 4, because the variable is now read by a
running `sf-api` in CI. Driving a SPICE session through it stays
in Kerbside's lane, which already has ryll and the drivers.

Planned at high effort: it crosses repositories, and the headroom
and load-budget instruments have seams that a new armed or
unarmed call site must declare (`test_headroom_gate_workflow_seams.py`).

### Phase 5 -- Kerbside's lane deploys through the collection

Kerbside's `sf-e2e-functional.yml` stops running
`deploy-kerbside-on-shakenfist`. Instead it deploys the smoke
cluster with a `kerbside` group and the PR's proxy wheel as
`kerbside_proxy_package`, then runs its existing drivers
unchanged. The composite action and Kerbside's
`tools/sf-e2e/{provision-sf.sh,deploy-kerbside.sh,gen-sources.py}`
are then retired, or reduced to whatever the drivers still need.

The lane stops testing a deploy shape no real operator would use.
It also becomes an early warning for collection changes that
break Kerbside.

Planned at medium-high effort. The drivers are untouched, but the
lane is currently failing nightly (kerbside#482), and that must be
understood before it is moved. Otherwise the move will be blamed
for it.

### Phase 6 -- Documentation and close-out

- `docs/operator_guide/vdi_console_tokens.md` loses its "manual
  step" section and gains the variables, the group, and the
  bring-your-own-Kerbside path. The "admin callers" error is
  fixed.
- `docs/operator_guide/installation.md` documents the group and
  variables.
- A new `docs/operator_guide/kerbside.md` covers:
  - the topology choices;
  - the database to create;
  - the load-balancer shape for the API (with an nginx example,
    following `examples/nginx-loadbalancer.conf`) and for the
    TCP passthrough of 5900/5901;
  - certificate overrides;
  - what being Keystone-less costs (kerbside#300);
  - the private-CA SF API caveat (kerbside#136).
- Release notes.
- In the Kerbside repository, `docs/use-cases/shakenfist.md`
  points at the collection as the supported path, and the
  co-location claim in `installation.md` is corrected (or filed,
  if the operator prefers). Both are edited there, never in
  `docs/components/kerbside/` here (`AGENTS.md`).

Planned at medium effort.

### Phase 7 -- Push audit

Runs `PUSH-AUDIT.md` over the accumulated diff of every phase in
this plan, not over the last phase alone. The phases here touch
the same files from different directions:

- phases 1 and 3 both edit `site.yml`'s certificate plays;
- phases 2 and 3 both decide what happens when `kerbside_url` is
  set and the group is empty.

That interaction is exactly where the tokens plan's three
"feature-off" defects lived.

By the time this phase runs, its predecessors will have merged,
and a diff against `develop` would be empty and read as clean.
The baseline is therefore the `Merged` column above. Phases that
landed in `shakenfist/actions` or `kerbside` are audited against
those repositories' default branches as part of the pull requests
that land them, and this phase cites those audits. Findings land
as their own pull request, and the plan is not complete until
each is resolved or declined in writing here. If the audit finds
nothing, that is recorded in one sentence.

## Dependencies on other plans

- [Kerbside VDI console tokens](PLAN-kerbside-vdi-tokens.md)
  (`Complete`) supplies every interface this plan deploys
  against. This plan is its deferred deployer phase.
- [Embrace TLS](PLAN-embrace-tls.md) (`Not started`) intends to
  demote `pki_internal_ca`, now `internal_ca`, to a dev/test
  convenience in favour of certificate paths supplied by the
  operator. Phase 1 here adds parameters to `internal_ca` rather
  than restructuring it. Phase 3's certificate overrides are the
  operator-paths shape that Embrace TLS will want, so neither
  plan gates the other. If Embrace TLS starts first, phase 1 is
  re-planned against whatever it makes of `internal_ca`.
- [Right-size the CI test clouds](PLAN-ci-cloud-sizing.md) owns
  topology shape and the headroom gate. Phase 4 adds a group to
  an existing topology rather than a new VM. If that proves
  impossible within the measured headroom, phase 4 stops and
  asks rather than growing the cloud.

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

!!! note "In this project"

    Phases 1, 2, 3 and 4 are planned at high effort, for the
    reasons given in each phase's section. Phase 5 is planned at
    medium-high effort, because the lane's existing nightly
    failure has to be understood first. Phase 6 is planned at
    medium effort.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

!!! note "In this project"

    The invariants a brief in this plan must carry, because none
    of them is inferable from the file being edited:

    - Roles read plain variables only. Only `site.yml` reads
      `groups[...]` or `hostvars[...]` (`collection/README.md:7-12`),
      and unprefixed variables are the cross-role contract.
    - Every template or unit change is registered for
      restart-on-change, and a redeploy with nothing changed
      restarts nothing. Phase 3's survey found that the node role's
      version of this had never been tested (#4459). Phase 3 tests
      the Kerbside role's change detection without root, and phase
      4 tests both on a real cluster.
    - `kerbside_url` is the single source of truth for both SF's
      `KERBSIDE_URL` and Kerbside's `sf_console_token_audience`.
      Neither is ever derived.
    - The "feature off" configuration (no `kerbside_url`, empty
      group) is tested in every phase that touches the
      cluster-config play or `site.yml`.
    - Secrets (`kerbside_system_key`, `kerbside_auth_secret_seed`,
      `kerbside_sql_url`) are marked `no_log` wherever they are
      rendered or passed on a command line, and go through
      `--key-from-stdin` or the like, never through argv.

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] The code passes `pre-commit run --all-files` (flake8,
          stestr unit tests, mypy, ansible-lint).
    - [ ] `python3 tools/check-plan-status.py` passes after any
          edit to a plan or to `docs/plans/index.md`.
    - [ ] A deploy with nothing changed restarts no `sf-*` or
          `kerbside-*` unit.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* An operator who adds a host to a `kerbside` group and sets
  `kerbside_url`, `kerbside_public_fqdn`, `kerbside_system_key`,
  `kerbside_auth_secret_seed` and `kerbside_sql_url` gets a
  Kerbside-routed `.vv` from `sf-client instance vdiconsolefile`
  after one run of `site.yml`, with no `sf-ctl` command run by
  hand.
* An operator who sets only `kerbside_url` (bring-your-own
  Kerbside) gets the signing key minted and the capability
  advertised, with no `sf-ctl` command run by hand.
* A second `site.yml` run with nothing changed restarts nothing.
* SF's merge queue runs a job in which the capability is
  advertised, a token is minted, Kerbside exchanges it, and the
  proxy's TLS endpoint verifies against the CA in the `.vv`.
* Kerbside's sf-e2e lane deploys through the collection, and
  `deploy-kerbside-on-shakenfist` is retired.
* SPICE certificates renew before they expire.
* #4093 and #4097 item 4 are closed by this work.
* The code passes `pre-commit run --all-files` (flake8, stestr
  unit tests, mypy, ansible-lint).
* Lines are wrapped at 120 characters, with single quotes for
  strings and double quotes for docstrings.
* Documentation in `docs/` has been updated. `ARCHITECTURE.md`
  gains Kerbside as an optional deployed component, because the
  shape of a deployment changes. `AGENTS.md` changes only if a
  convention does.

### Documentation index maintenance

This plan is registered in `docs/plans/index.md` (one row, in the
*Master plans* table) and in `docs/plans/order.yml`. Phase files
are linked from the Execution table above and appear in neither,
which is what `tools/check-plan-status.py` enforces.

When this plan is committed, Kerbside's `docs/plans/index.md`
gains a cross-reference row, as it did for the tokens plan. That
lands in the Kerbside repository with phase 5, or earlier if the
operator prefers.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

- **Kerbside admin login without Keystone** (kerbside#300, with
  #301 on the seed doubling as a minting capability). Until
  this lands, a deployment with only Shaken Fist has no Kerbside
  admin UI and cannot terminate a session except by minting a
  JWT from the seed, as the sf-e2e driver does. The tokens
  plan's Future work also points at convergence with SF's
  OIDC and auth-federation work.
- **A least-privilege credential for the scrape.** It needs an
  `sf-ctl` option to mint a scoped `system` key, kerbside#444,
  and a verified scope set. The set is probably `cluster-admin`,
  `node.read`, `instance.read` and `admin.read`, but that is
  unverified.
- **Kerbside's SF API client pins no CA** (kerbside#136). An SF
  API behind a private CA needs that CA in the Kerbside host's
  system trust store. The deployer could install it, but the
  right fix is in Kerbside.
- **Ship the systemd units from Kerbside**, and switch the
  collection to them (open question 2).
- **Automated signing-key rotation**, carried over from the
  tokens plan.
- **More than one Kerbside host, tested**, behind a TCP
  passthrough load balancer.
- **Homelab adoption.** sfcbr, the report that prompted this
  plan, picks the feature up in the operator's own
  infrastructure repository once phase 3 is released. That
  repository, not this plan, tracks the work.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

- **#4415** (open): SPICE certificate renewal never fires, and
  certificates expire after 365 days (found while planning; see
  Situation). Phase 1 fixes it and closes the issue.
- **#4416** (open): the SPICE private key is installed
  world-readable. Phase 1 adds the parameters a fix needs, but
  leaves the default unchanged; see its Future work.
- **#4004** (closed by #4024) delivered the first increment
  only. This plan is the remainder; phase 2 comments on #4004
  with a link here.
- **#4093** (open): the mint test never runs in SF CI. Phase 4
  closes it.
- **#4459** (open): restart-on-change in the node role has never
  been tested (found while planning phase 3). Phase 4 adds the
  second-deploy check.
- **#4460** (open): `examples/cluster` pairs a load-balancer
  comment with a prefix-less `api_url` (found while planning phase
  3). Unrelated to Kerbside beyond the `api_url` it writes into
  `sources.yaml`.
- **#4097** (open): uncovered cross-repository seams. Phase 4
  closes item 4. Items 1-3 and 5-6 are mostly on the Kerbside
  side and stay with that issue.
- **#4367** (open): no headroom instrumentation on Kerbside's
  deploy lane. Phase 4's job is unarmed until it is measured,
  and phase 5 moves the lane onto instrumented infrastructure.
- **kerbside#482** (open): the nightly sf-e2e lane is failing.
  It must be understood before phase 5 moves the lane.
- **kerbside#131, #313, #465** (open): a forgeable default seed,
  a malformed INI that exits 0, and a malformed `sources.yaml`
  that crash-loops the daemon. Phase 3 guards each at deploy
  time: assert the seed, validate the rendered INI and YAML
  before restarting.
- **The `vdi_console_tokens.md` "admin callers" error, and
  Kerbside's co-location claim** (found while planning; see
  Situation). Phase 6.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
