# Phase 3 -- A `kerbside` role and the `kerbside` group in `site.yml`

Part of [PLAN-kerbside-deployer.md](PLAN-kerbside-deployer.md). Phase 2
([PLAN-kerbside-deployer-phase-02-sf-side.md](PLAN-kerbside-deployer-phase-02-sf-side.md))
landed in #4442 as `80292df08`; this phase's first commit closes it out.

**Planning effort:** high, as the master plan asks. This is the bulk of
the plan: a new role for a service with no systemd units of its own,
whose malformed configuration exits 0 (kerbside#313), deployed to hosts
that may not be Shaken Fist nodes at all.
**Review effort:** high for steps 3 and 4, which decide when Kerbside
restarts and how `site.yml` targets hosts outside `allsf`. Medium
elsewhere.

## Scope

**In scope:**

* A new `shakenfist.shakenfist.kerbside` role with `validate`,
  `bootstrap`, `config` and `register` entry points (D1).
* A `kerbside` inventory group in `examples/_shared/site.yml`: mapped to
  plain role variables, included in the reachability preflight,
  validated before any host changes, and deployed after the Shaken Fist
  sanity checks (D2).
* The proxy certificate from `internal_ca`, called with phase 1's
  parameters, or operator-supplied paths (D6).
* Restart-on-change for the two Kerbside units, written so that a
  certificate renewal counts as a change (D7).
* A readiness wait that proves Kerbside fetched the signing key with
  the credential phase 2 minted (D8).
* A rootless scripted test and a unit test, run in the sanity-checks
  job and as pre-commit hooks (D10).
* The collection README, the operator guide's variable list and a
  release note.

**Out of scope, and why:**

* **Running the role against a real cluster.** No CI lane can: the
  smoke cluster's inventory groups are hardcoded in `shakenfist/actions`
  (S9). Phase 4 adds the lane. Until then the register entry point is
  proved only as far as a rootless test can prove it, and the
  documentation says so (D10).
* **A second-deploy "nothing restarted" check.** It needs a real cluster
  and the same actions change. Phase 4 takes it, for `sf-*` and
  `kerbside-*` units alike (#4459).
* **Creating Kerbside's database.** Bring-your-own, as open question 3
  decided.
* **Keystone, HTTPS for Kerbside's API, a least-privilege scrape
  credential, more than one tested Kerbside host.** Out of scope for the
  whole plan.
* **Kerbside's own documentation.** Phase 6.

## What the survey found

Two surveys ran before this plan was written: one over the collection
and `site.yml` at `076c5975a`, one over Kerbside at `d0963ff`. Findings
that change the plan come first.

### S1 -- a host outside `allsf` is invisible to `site.yml` today

The reachability probe targets `allsf` only (`site.yml:47`), and the
`add_host` loop that builds `reachable` iterates `groups['allsf']`
(`:82`). A `kerbside:&reachable` play would therefore match nothing on
a dedicated Kerbside host. Both lines must widen to the union.

Every Shaken Fist play targets `allsf:&reachable` or a subset of it
(plays 4-17), including the final sanity checks (`:678-718`). A Kerbside
host outside `allsf` is already excluded from the node role, the SPICE
certificate and the 401 checks by construction. The master plan's worry
there is answered by the existing patterns rather than by new guards.

An absent group is harmless: a play on `kerbside:&reachable` with no
`kerbside` group warns "Could not match supplied host pattern" and
skips (checked empirically). `groups.get('kerbside', [])` is the idiom
already used for optional groups (`:76, 99, 106, 239, 264-266`). No
group yet drives plays while being optional: `storage` is mentioned
only in the header comment (`:17`).

### S2 -- restart-on-change is not tested anywhere

The master plan's invariant says restart-on-change "is tested, not
assumed". For the node role it is not tested at all. No script, unit
test or CI step deploys twice and checks for restarts, and CI runs
`site.yml` once (`shakenfist/actions` `tools/deploy-collection.sh:65`).
Filed as #4459. Phase 4 takes the real-cluster check (scope above).
This phase tests the Kerbside role's change detection rootlessly (D10).

### S3 -- a renewed certificate signals nothing

`distribute_certificates.yml` copies the CA, key and certificate
without `register` or `notify` (`:156-193`). A renewal, which phase 1
made work, therefore changes the files on disk and sets no fact. The
node role gets away with it because SPICE reads its certificate at
instance start. Kerbside's proxy does not re-read its certificate, so
the Kerbside role must detect the change itself (D7).

### S4 -- "healthy" and "signing key fetched" are different things

The master plan says `register` waits "until the SF source reports
healthy, meaning the signing key has been fetched". They are not the
same:

* A new source row is created with `errored=False` before any scrape
  (Kerbside `main.py:106-114`), so `errored` is false on a source that
  has never worked.
* A failed key fetch deliberately does not error the source
  (`sources/shakenfist.py:85-106`).
* The `sf_token_keys` row is the real signal. It is written only after
  the CA comparison passed (`shakenfist.py:72-79`) and the system key
  authenticated (`:111-117`).
* But those rows are never deleted (`db.py:958-981` has only upsert and
  get), so "a row exists" is true forever after the first success. The
  freshness signal is `fetched_at` (a float, `db.py:943`), refreshed on
  every successful scrape pass.

The sf-e2e lane's poll is not HTTP and needs no Keystone: a Python
heredoc calling `kerbside.db.get_sf_token_keys()`
(`tools/sf-e2e/deploy-kerbside.sh:193-224`). The master plan cited
`:297-321`; the file is 226 lines long and never exceeded 227. There
is no source-health metric on 13003 (`metrics.rs:40-116`) and the REST
`/source` endpoint needs a JWT (`api.py:892-894`). So the database,
read through Kerbside's own module, is the only signal available
without Keystone (D8).

Importing `kerbside.db` loads the INI, and a malformed INI exits 0
(`config.py:37-39`, kerbside#313). A readiness check must parse what it
prints, never trust its exit status.

### S5 -- the latest Kerbside release returns the source password

PyPI's newest `kerbside` is 0.6.0 (2026-09-07). In it, `GET
/source/<name>` returns the source verbatim, password included
(`git show v0.6.0:kerbside/api.py:864-876`). Under this plan, that
password is the cluster's `kerbside` system-namespace key. Kerbside
fixed it after the release, in `3c5a309` ("Never return source secrets
from the API.") and `8294f22`. A role defaulting to `kerbside` from PyPI
would install the leaking version (D4).

The practical exposure is small without Keystone, since a JWT needs
Keystone or the seed. It is still a credential with the run of the
cluster, and the default should not ship it.

### S6 -- what Kerbside needs from its host

Kerbside ships no systemd units (grep, and `PLAN-demo-install.md:455-457`)
and names no user. Kolla runs it as `kerbside`; the demo runs it as
root.

* **Writable:** only the `API_SOCKET_PATH` directory, default
  `/run/kerbside`. The daemon creates it 0700 and chmods it
  unconditionally (`rpc/server.py:44-53`), so it needs root to create:
  `RuntimeDirectory=kerbside`. Logs go to syslog, and therefore to
  journald, unless `LOG_OUTPUT_PATH` is set (`util.py:11-18`).
* **Readable by the service user:** the INI, `sources.yaml` (read by
  the daemon *and* the API, `sf_token.py:79-88, 186-191`), `CACERT_PATH`
  (the API), and the certificate and key (the proxy child, same uid).
* **The proxy binary** is found by `KERBSIDE_PROXY_BIN` (authoritative,
  fails if not executable), then `PATH`, then an in-repo build path
  (`proxy_supervisor.py:46-83`). A unit must set one of the first two.
* **Packages:** `kerbside`, `kerbside-proxy` (exactly pinned by a
  release, `tools/stamp-proxy-version.sh:18-30`), `gunicorn`, and
  `shakenfist_client`, which is not a dependency of `kerbside`
  (`pyproject.toml:104, 166-169`). `mysqlclient==2.3.0` builds from
  source. Apt build dependencies: `build-essential pkg-config
  python3-dev libmariadb-dev-compat libxml2-dev libxslt1-dev`
  (`installation.md:18-21`). A Kerbside host outside `allsf` gets none
  of the node role's packages.
* **Ports:** the API port is only gunicorn's `--bind`. `VDI_SECURE_PORT`,
  `VDI_INSECURE_PORT`, `PUBLIC_*_PORT` and `PROMETHEUS_METRICS_PORT` are
  Kerbside settings (`config.py:106-183`). None collides with Shaken
  Fist's 13000, 13001 or 13005-13007, or with its 30000-50000 VDI range
  (`instance.py:1756`).
* **`SOURCES_PATH` defaults to `./sources.yaml`**, relative to the
  working directory (`config.py:216`). It must be set explicitly.

### S7 -- `PROXY_HOST_SUBJECT` is embedded, not compared, by Kerbside

The proxy logs `--host-subject` and `--cacert` and uses neither
(`rust/kerbside-proxy/src/main.rs:195-196`). The API embeds
`CACERT_PATH` and `PROXY_HOST_SUBJECT` into the `.vv`
(`api.py:497-502, 692-697, 819-838`), and the SPICE client compares the
subject against the certificate. Master plan open question 4 said the
proxy compares it. The conclusion stands (render it by
`_spice_host_subject_from_cert()`'s rules), because the client is the
consumer. A wrong subject surfaces as a client refusing to connect, not
as a deploy failure, so the rendering needs a test that pins it to
`node.py` (D5).

`node.py:119-192` renders RDNs in certificate (DER) order, as `SHORT=value`
joined by `,` with no space, escaping `\` and `,`. `internal_ca` issues
`O` then `CN`. `openssl x509 -subject -nameopt RFC2253` prints them
reversed, which is a trap for anyone tempted to shell out.

### S8 -- `sf.target` isolation leaves Kerbside units alone

`register.yml` isolates `sf.target` when it is not active
(`:167-175`). `sf.target` has `Requires=multi-user.target`
(`templates/sf.target`), so isolating it keeps every enabled unit that
is `WantedBy=multi-user.target`. Kerbside units must be enabled and
wanted by `multi-user.target`, never by `sf.target`. On a co-located
host that is what keeps a Shaken Fist register from stopping Kerbside.

### S9 -- a `kerbside` group cannot reach CI without an actions change

`ci-make-inventory.py:84-95` hardcodes the groups from per-node flags,
and `deploy-collection.sh:65-81` passes a fixed `--extra-vars` string.
`site.yml` itself comes from the shakenfist checkout
(`deploy-collection.sh:37, 65`), so this phase's `site.yml` changes are
exercised by the PR's smoke cluster, on the feature-off path only.

### S10 -- `api_url` exists, and its default only works co-located

`api_url` defaults to `http://localhost:13000`
(`roles/node/defaults/main.yml:35`). `sf-api` binds `0.0.0.0:13000` on
every `allsf` node (`sf-api.service:33`), so the default works for a
Kerbside host that is also a Shaken Fist node, and never for one that is
not. Separately, `examples/cluster/group_vars/all.yml:7-8` pairs a
load-balancer comment with a prefix-less URL that `load_balancing.md`
reserves for the single-node escape hatch: filed as #4460.

### S11 -- smaller facts the briefs depend on

* `internal_ca`'s parameters exist under the names phase 1 gave them:
  `cert_name`, `cert_cn`, `cert_san_dns`, `cert_san_ip`, `cert_dest_dir`,
  `cert_owner`, `cert_group`, `cert_mode`, `cert_key_mode`,
  `cert_expiration_days` (`roles/internal_ca/defaults/main.yml:21-47`).
  The CN is not added as a SAN automatically (`vars/main.yml:202-214`).
  `distribute_certificates.yml:143-148` always creates `/etc/pki/CA`,
  a harmless side effect on a Kerbside-only host.
  `tools/ci-test-internal-ca.sh:110-123` already issues a
  Kerbside-shaped certificate.
* `/admin/cacert` serves the answering node's
  `/etc/pki/libvirt-spice/ca-cert.pem` (`external_api/admin.py:52-61`),
  and Kerbside compares `ca_cert` with it after `rstrip()`
  (`shakenfist.py:73`).
* `configparser` uses basic interpolation (`config.py:18`), so every
  `%` in a rendered value must be doubled. Alembic doubles the URL again
  internally (`migrations/env.py:24-25`); the INI writer doubles once.
* `kerbside db upgrade` is alembic `upgrade head`, idempotent, with no
  lock (`main.py:425-437`). Kerbside has no validate command at all
  (`main.py:357, 425, 443, 530`), and a malformed `sources.yaml`
  crash-loops the daemon (kerbside#465).
* The daemon forwards SIGTERM to the proxy with a 15-second deadline
  (`main.py:333-339`).
* The node role's patterns to mirror: venv with `creates:`, `uv pip
  install --reinstall` with `changed_when: true`, and a hash of the
  installed files as the real "code changed" signal
  (`roles/node/tasks/bootstrap.yml:188-254`). The apt idiom with the
  dpkg-lock retry is at `:128-162`.
* There is no generic "every role default is documented" test.
  `shakenfist/tests/test_node_config_template.py:93-100` covers the
  node role only.
* The master plan's `site.yml` line citations had all drifted (phase 2
  grew the cluster-config play), and so had its `deploy-kerbside.sh`
  citations. Kerbside's use-case page lists two of the sf-e2e lane's
  five shortcuts, not all of them.

### S12 -- what the master plan got right

The packaging, process, configuration, sentinel (`~~unconfigured~~`,
kerbside#131), audience derivation, `sources.yaml` shape, MySQL-only
database and port claims all held. So did the co-location defect: the
API and the daemon share only the database (`api.py` never touches
`API_SOCKET_PATH`), and only the daemon and proxy must share a host.

### Corrections made at source

In this phase's planning commit, the master plan's key references, its
phase 3 and phase 4 sections, its open question 4 and its invariants
box were corrected for S2, S4, S5, S7 and the stale citations. Phase 4
gained the second-deploy check (#4459) and the Kerbside release
prerequisite (D4). The `index.md` row was updated. A later step does
not need to redo any of this.

## Decisions

### D1 -- the role has four entry points, and site.yml stays thin

`roles/kerbside/` with:

* `validate`: every assertion that can fail a deploy, run once, with a
  Kerbside host's variables, before any host changes. It is the phase 2
  pattern of keeping logic in role entry points, where argument_specs
  and a rootless test reach it, with `site.yml` only wiring.
* `bootstrap`: user, packages, venv, code-change detection.
* `config`: INI, `sources.yaml`, units, and the desired-state hash.
* `register`: migrate, start or restart, wait for readiness, record
  the deployed state.
* `main`: bootstrap, config, register, as the node role does. `validate`
  is not part of `main`, because `site.yml` runs it once, in a play of
  its own, before any host changes.

The role reads plain variables only. `site.yml` maps
`groups.get('kerbside', [])` to `kerbside_hosts`, and computes
`kerbside_colocated` (`inventory_hostname in groups['allsf']`) per host.
`meta/main.yml` has no `common` dependency: its handlers belong to the
node's journald and logind, which a dedicated Kerbside host does not
need. The role carries its own daemon-reload instead.

### D2 -- where Kerbside sits in `site.yml`

* **Reachability (plays 1-2):** probe and `add_host` over
  `allsf:kerbside`.
* **Validation (a play of its own, straight after play 2):** on
  `kerbside:&reachable`, `run_once`, include `kerbside` `validate` with
  `kerbside_hosts`, `kerbside_noncolocated_hosts` and
  `kerbside_url_on_sf_nodes`. Not on `localhost`, as first planned:
  operators keep the Kerbside variables in `group_vars/kerbside`, which
  `localhost` cannot see, so it would refuse a correct inventory. That
  is #4441's family, a play reading variables other than the ones the
  hosts will use. With no `kerbside` group the play matches no host and
  nothing runs, which is the feature-off path. `any_errors_fatal` makes
  a refusal end the run before any later play.
* **Deployment: new plays after the final sanity checks.** One play on
  `kerbside:&reachable` runs bootstrap, then the certificate (D6), then
  config, then register. They come last so that `sf-api` is proven up,
  and the signing key and the `kerbside` credential minted (play 6a),
  before Kerbside's first scrape. A Kerbside failure is then never
  confused with a Shaken Fist one.

The master plan said "after the Shaken Fist register play". After the
sanity checks is the same ordering made stricter.

### D3 -- the cross-cutting assertions

`validate` fails the deploy, before any host changes, when the group is
non-empty and any of these hold:

* `kerbside_url`, `kerbside_system_key`, `kerbside_public_fqdn`,
  `kerbside_sql_url` or `kerbside_auth_secret_seed` is empty. This is
  where phase 2's deferred "`kerbside_system_key` is required when the
  group is non-empty" lands.
* `kerbside_auth_secret_seed` is the sentinel `~~unconfigured~~`
  (kerbside#131) or shorter than 32 characters.
* Any configured Kerbside port (API, VDI secure and insecure, metrics)
  is 30000 or above (open question 1), or two of them are equal.
* The three certificate override paths are neither all set nor all
  empty (D6).
* `api_url` is a loopback URL and some Kerbside host is not co-located
  (S10). The message names the host and says what `api_url` needs to
  be.
* `kerbside_url_on_sf_nodes` is supplied and is not `kerbside_url` byte
  for byte. `site.yml` supplies it as `kerbside_url` as the first `allsf`
  host sees it, empty when it sees none. This enforces the invariant
  that `kerbside_url` is the single source of truth for Shaken Fist's
  `KERBSIDE_URL` and Kerbside's audience: set only in
  `group_vars/kerbside`, it would configure Kerbside and leave Shaken
  Fist without it. The message says to set it where both groups read it,
  such as `group_vars/all`. Not supplied (`null`, the default) skips the
  check, for a playbook other than `site.yml`.

`kerbside_url` set with an empty group stays legal: that is the
bring-your-own-Kerbside path phase 2 serves.

### D4 -- the default package requires a release that redacts source secrets

`kerbside_package` defaults to a floor on the first Kerbside release
containing `3c5a309` and `8294f22`: `kerbside>=0.7.0` (S5; confirmed by
the operator, see the back brief).

That release does not exist yet. Cutting it is an operator action, and a
prerequisite of phase 4 rather than of this phase, because nothing runs
the role before phase 4. Until it exists, `kerbside_package` must be
overridden: to a wheel, as `server_package` can be, or to a version.
The alternative, defaulting to `kerbside` and documenting the leak,
ships the cluster credential readable to any Kerbside API token holder
by default. That is the decision most likely to be argued with: it means
the role cannot install from PyPI with defaults on the day it merges.

`kerbside_proxy_package` defaults to empty, meaning the version the
release pins. `kerbside_client_package` defaults to `shakenfist-client`.
A local `kerbside` wheel must be built from a git checkout, because
`kerbside.sources` and `kerbside.migrations` ship only through
setuptools_scm's file finder (kerbside#326). The variable's
argument_specs description says so.

### D5 -- `PROXY_HOST_SUBJECT` is computed by a script pinned to `node.py`

The role ships `files/kerbside-host-subject.py`, run with the Kerbside
venv's Python on the Kerbside host against the installed certificate. It
uses `cryptography`, a Kerbside dependency, and implements
`_spice_host_subject_from_cert()`'s rules (S7). It exits non-zero when
the subject cannot be rendered, where `node.py` returns `None`.

A unit test in `shakenfist/tests/` loads the script by path and compares
its output with `node._spice_host_subject_from_cert()` over generated
certificates. The certificates cover: the `internal_ca` shape (`O`,
`CN`); a value with a comma; a value with a backslash; a long multi-RDN
subject; and an unknown OID, where both refuse. The two implementations
cannot drift without a failing test.

The rendered subject is `%`-doubled like every other INI value.

### D6 -- the certificate

When the three override paths (`kerbside_proxy_cert_path`,
`kerbside_proxy_key_path`, `kerbside_cacert_path`) are empty, the
deployment play calls `internal_ca` `host_certificate` and
`distribute_certificates` with:

* `hostname: inventory_hostname`
* `cert_name: <inventory_hostname>-kerbside`, so it cannot collide
  with a co-located node's SPICE certificate;
* `cert_cn` and `cert_san_dns: [kerbside_public_fqdn]`, which satisfy
  both Kerbside's documentation and modern clients (S7);
* `cert_dest_dir: /etc/kerbside/pki`;
* `cert_owner: root`, `cert_group: kerbside`, `cert_key_mode: "0640"`.

This runs after `bootstrap`, which creates the `kerbside` group.

When they are set, the certificate play is skipped, and the role uses
the paths as they are on the Kerbside host.

`sources.yaml`'s `ca_cert` is a different CA: the one Shaken Fist's
`/admin/cacert` serves, against which the backend leg verifies. In both
modes, `site.yml` slurps `/etc/pki/libvirt-spice/ca-cert.pem` from the
first reachable database-tier host and passes its content to the role as
`kerbside_sf_ca_cert`. It is the file `/admin/cacert` serves, so the
`rstrip()` comparison passes by construction, whatever produced the
SPICE certificates. The master plan said "read from the control node's
CA file". That is the same bytes today, but it would stop being true
the day an operator supplies their own SPICE certificates (Embrace TLS).

### D7 -- restart-on-change through a desired-state hash written after success

`bootstrap` hashes the installed `kerbside`, `kerbside_proxy` and
`shakenfist_client` files, plus the `kerbside-proxy` binary, as the node
role does (S11). `config` hashes the rendered INI, `sources.yaml`, both
units, and the certificate, key and CA files, however they were
produced. The two hashes combine into one desired-state value.

`register` compares that value with `/srv/kerbside/.deployed-state.sha256`,
and restarts both units when it differs or the file is missing. It
writes the file **only after the readiness wait succeeds**.

This differs from the node role, which decides from facts set when
templates are written. Writing the marker after success means a deploy
that fails between rendering and readiness restarts again next time,
rather than leaving a half-applied change marked as deployed. Including
the certificate files is what makes a renewal a change (S3). Both units
restart together, matching the node role's node-wide granularity
(`README.md:49-52`).

Units:

* `kerbside-api.service`: `User=kerbside`; gunicorn
  `--bind {{ kerbside_api_address }}:{{ kerbside_api_port }}`
  `--workers {{ kerbside_api_workers }} --timeout 300`. The timeout
  matches `sf-api.service`, because the unknown-kid refresh calls Shaken
  Fist inside a request.
* `kerbside-daemon.service`: `User=kerbside`, `RuntimeDirectory=kerbside`,
  `Environment=KERBSIDE_PROXY_BIN=/srv/kerbside/venv/bin/kerbside-proxy`,
  `TimeoutStopSec=30`.
* Both: `Restart=always`, `RestartSec=5`, `WantedBy=multi-user.target`,
  enabled (S8). `always` rather than `on-failure`, because kerbside#313's
  exit 0 on a malformed INI defeats `on-failure`, and a daemon exiting
  0 is never intended.
* The INI and `sources.yaml` are `root:kerbside 0640`, so the service
  can read but not rewrite them. The master plan said `sources.yaml`
  would be 0600. That leaves either the API or the daemon unable to read
  it, unless both run as the owner.
* Both files are checked before they replace the old ones, with
  `ansible.builtin.template`'s `validate:`. The INI is parsed by
  `configparser` with interpolation, every value read, and the
  `[kerbside]` section required. `sources.yaml` must be a list of
  mappings with the keys the Shaken Fist driver reads. A bad render
  fails the deploy and leaves the running Kerbside on its old,
  working files (kerbside#313, #465).

### D8 -- readiness means a fresh signing-key fetch

`register` records the host's time before the restart decision. After
the restarts, it waits for the API (`GET /` with `Accept:
application/json`, which needs no auth). It then runs
`files/kerbside-ready.py` with the venv's Python, `run_once` on
`kerbside_hosts[0]`. The script prints one JSON object: the source's
`errored` flag and its `sf_token_keys.fetched_at`. The task retries
until `fetched_at` is later than the recorded time, for up to 180
seconds, parsing stdout and ignoring the exit status (S4). On timeout,
the failure message includes the last JSON and points at
`journalctl -u kerbside-daemon`.

It waits on every deploy, including one that restarted nothing. That
costs up to one scrape interval, about 60 seconds. It buys proof, each
time, that Kerbside authenticates with the credential phase 2 just
re-minted, against a CA that still matches. Phase 2 rotates that
credential's nonce on every deploy, so this is the deploy-time check
that the 401-and-re-authenticate path works. Skipping the wait on a
no-op deploy is the obvious saving, and the one to take if the cost
proves annoying; it is recorded as Future work.

### D9 -- migrations once, before any restart

`kerbside db upgrade` runs `run_once`, delegated to `kerbside_hosts[0]`,
after config and before the restart. New code may need the new schema.
Alembic has no lock, so once is the only safe count. A failed migration
fails the deploy with the old units still running.

### D10 -- what is tested now, and what waits for phase 4

`tools/ci-test-kerbside-role.sh` is rootless, in the shape of
`tools/ci-test-kerbside-cluster-config.sh`. The config directory, unit
directory, state directory and owners become role variables with the
production defaults, so the test can point them at a temporary
directory. It covers:

* every `validate` failure in D3, and the feature-off pass with an
  empty group;
* the rendered INI: it parses with interpolation, a `%` in
  `kerbside_sql_url` round-trips, and `sf_console_token_audience`
  equals `kerbside_url` byte for byte;
* the rendered `sources.yaml`: `ca_cert` equals `kerbside_sf_ca_cert`
  byte for byte, `username` is `system`, and the file's mode is right;
* the `validate:` refusal of a malformed INI, leaving the old file in
  place;
* the desired-state hash: unchanged on a second config run; changed by
  a certificate-file change and by a variable change;
* `site.yml` host targeting, using `ansible-playbook --list-hosts` with a
  test inventory holding a Kerbside host outside `allsf`. That host must
  appear in the reachability and Kerbside plays only. A feature-off
  inventory must leave the Kerbside plays empty.

`register` is not run by the test: it needs systemd, MariaDB and a
Shaken Fist API. Its first real run is phase 4's lane. The release note
and the collection README say the role is new, and that its cluster
test arrives with the merge-queue lane.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | none | The role's skeleton, `validate` and `bootstrap`. See brief 1. Commit: "Add a kerbside role: validate and bootstrap." |
| 2 | high | opus | none | `config`: the INI, `sources.yaml`, the units, the host-subject script and its unit test, the desired-state hash. See brief 2. Commit: "Render Kerbside's configuration and units." |
| 3 | high | opus | none | `register`: migrate, restart on change, readiness, the state marker. See brief 3. Commit: "Start Kerbside and wait for its first scrape." |
| 4 | high | opus | none | `site.yml` wiring: reachability, validation, certificate, deployment plays. See brief 4. Commit: "Deploy Kerbside from the kerbside group." |
| 5 | high | opus | none | `tools/ci-test-kerbside-role.sh`, its CI step and pre-commit hook. See brief 5. Commit: "Test the kerbside role without root." |
| 6 | medium | sonnet | none | The collection README, the operator guide variables, a release note. See brief 6. Commit: "Document the kerbside role." |

The steps run in order, in this worktree, one sub-agent at a time
(concurrent agents in one tree revert each other through pre-commit's
stash). Every brief carries the master plan's invariants box. The
management session runs `pre-commit run --all-files` and reviews each
step before committing it.

### Brief 1 -- skeleton, `validate`, `bootstrap`

Create `shakenfist/deploy/collection/roles/kerbside/` with `defaults/main.yml`,
`meta/main.yml` (`galaxy_info` as in `roles/internal_ca/meta/main.yml`,
`dependencies: []`), `meta/argument_specs.yml`, `handlers/main.yml` (one
"Reload systemd" handler), and `tasks/{main,validate,bootstrap}.yml`.
`galaxy.yml` needs no change.

Variables, all documented in `argument_specs` with an anchor shared by
the entry points as `roles/node/meta/argument_specs.yml` does with
`&node_options`:

* Required when deploying: `kerbside_url`, `kerbside_public_fqdn`,
  `kerbside_system_key` (`no_log`), `kerbside_auth_secret_seed`
  (`no_log`), `kerbside_sql_url` (`no_log`), `kerbside_sf_ca_cert`.
* `kerbside_hosts` (list, default `[]`), `kerbside_colocated` (bool),
  `api_url`, `deploy_name`.
* Package variables: `kerbside_package` (D4: `"kerbside>=0.7.0"`), `kerbside_proxy_package`
  (`""`), `kerbside_client_package` (`"shakenfist-client"`) and
  `kerbside_pip_extra` (`""`).
* Port and listener variables: `kerbside_api_address` (`0.0.0.0`),
  `kerbside_api_port` (13002), `kerbside_api_workers` (2),
  `kerbside_vdi_secure_port` (5900), `kerbside_vdi_insecure_port`
  (5901) and `kerbside_metrics_port` (13003).
* Certificate overrides: `kerbside_proxy_cert_path`,
  `kerbside_proxy_key_path` and `kerbside_cacert_path`, each defaulting
  to `""`.
* Path and ownership variables, for the rootless test (D10):
  `kerbside_config_dir` (`/etc/kerbside`), `kerbside_venv`
  (`/srv/kerbside/venv`), `kerbside_state_dir` (`/srv/kerbside`),
  `kerbside_unit_dir` (`/etc/systemd/system`), `kerbside_user` and
  `kerbside_group` (`kerbside`).

`validate.yml` implements D3 exactly. The whole file is a no-op, with a
debug line saying so, when `kerbside_hosts` is empty. Use
`ansible.builtin.assert` with `quiet: true` and fixed `fail_msg`s that
never interpolate a secret. Mirror the message style of
`roles/node/tasks/kerbside_credentials.yml`. The loopback check parses
`api_url`'s host (`localhost`, `127.0.0.0/8`, `::1`). It needs
per-host co-location, so take `kerbside_noncolocated_hosts` (a list
`site.yml` computes) rather than reading `hostvars`.

`bootstrap.yml` mirrors `roles/node/tasks/bootstrap.yml`:

* the apt idiom at `:128-162`, with the dpkg-lock retry, for
  `build-essential pkg-config python3-dev python3-venv
  libmariadb-dev-compat libxml2-dev libxslt1-dev`;
* a system user and group, `kerbside`, with no login shell and home
  `kerbside_state_dir`;
* the venv with `creates:`, created without `--system-site-packages`;
* the `uv` install of `kerbside_package`, `kerbside_proxy_package`,
  `gunicorn` and `kerbside_client_package`;
* the code hash at `:228-254`, over the installed `kerbside`,
  `kerbside_proxy` and `shakenfist_client` site-packages and
  `bin/kerbside-proxy`. Set it as the fact `kerbside_code_hash`, not as
  a changed flag (D7).

Do not touch `site.yml` (brief 4). Run `pre-commit run --all-files`;
ansible-lint must pass.

### Brief 2 -- `config`

In `roles/kerbside/`, add `tasks/config.yml`, `templates/kerbside.ini`,
`templates/sources.yaml`, `templates/kerbside-api.service`,
`templates/kerbside-daemon.service` and `files/kerbside-host-subject.py`.
Add a `config` entry point.

* Certificate paths: the overrides when set; otherwise
  `/etc/kerbside/pki/{server-cert,server-key,ca-cert}.pem`. Those are the
  `internal_ca` destination names under `cert_dest_dir`, from
  `roles/internal_ca/defaults/main.yml:35-39`.
* `PROXY_HOST_SUBJECT`: run the script with `{{ kerbside_venv
  }}/bin/python` against the certificate (`changed_when: false`). The
  script implements `shakenfist/node.py:119-192`'s rules exactly: DER
  order, the short names at `:44-53`, `\` then `,` escaping, `,` join,
  and a non-zero exit with a message where `node.py` returns `None`.
* The INI: `[kerbside]` with `sql_url`, `auth_secret_seed`,
  `sources_path` (absolute), `public_fqdn`, `cacert_path`,
  `proxy_host_cert_path`, `proxy_host_cert_key_path`,
  `proxy_host_subject`, `sf_console_token_audience` (always
  `kerbside_url`; never derived), `public_secure_port`,
  `public_insecure_port`, `vdi_secure_port`, `vdi_insecure_port`,
  `prometheus_metrics_port` and `node_name` (`inventory_hostname`).
  * Every value passes through `replace('%', '%%')`.
  * No `keystone_*` keys.
  * `root:{{ kerbside_group }}` 0640, `no_log: true`.
  * `validate:` uses a Python one-liner, with the venv's Python, that
    reads every value of `[kerbside]` through `ConfigParser` and exits
    non-zero on any error or a missing section. Put it in
    `files/kerbside-check-ini.py` if it exceeds a line.
* `sources.yaml`: one source with `source: {{ deploy_name }}`,
  `type: shakenfist`, `url: {{ api_url }}`, `username: system`,
  `password: {{ kerbside_system_key }}`, and `ca_cert` as a literal
  block scalar of `kerbside_sf_ca_cert`. Render it through `to_nice_yaml`
  from a dict rather than by string templating, so quoting is never
  wrong. `root:{{ kerbside_group }}` 0640, `no_log: true`. `validate:`
  loads it with `yaml.safe_load`, and requires a list of mappings
  carrying `source`, `type`, `url`, `username`, `password` and
  `ca_cert`.
* The units, per D7, into `kerbside_unit_dir`, notifying "Reload
  systemd". Read `roles/node/templates/sf-api.service` for the shape,
  but none of `sf.target`, `sf-ctl` or `Type=notify` (S8).
* The desired-state hash: `stat` with `get_checksum` on the INI,
  `sources.yaml`, both units, the certificate, the key and the CA. Join
  their checksums with `kerbside_code_hash` and hash the result. Set the
  fact `kerbside_desired_state`. Do not write the marker (brief 3).

Add `shakenfist/tests/test_kerbside_host_subject.py`. It loads
`files/kerbside-host-subject.py` with `importlib.util` and compares it
with `shakenfist.node._spice_host_subject_from_cert` over certificates
generated with `cryptography` in the test, covering the cases in D5.
Follow `shakenfist/tests/test_node_config_template.py`'s style. Add a
`test_kerbside_defaults_are_documented` beside it: every key in the
role's `defaults/main.yml` appears in the `main` entry point's options,
mirroring `:93-100` of that file.

### Brief 3 -- `register`

Add `tasks/register.yml` and `files/kerbside-ready.py`, the `register`
entry point, and `main.yml` (bootstrap, config, register).

1. `flush_handlers`, so units are reloaded.
2. Record `date +%s.%N` on `kerbside_hosts[0]` as
   `kerbside_ready_after` (`run_once`, `delegate_to`).
3. Slurp `kerbside_state_dir/.deployed-state.sha256` if it exists. Set
   `kerbside_restart_needed` to "missing, or different from
   `kerbside_desired_state`", and log it as the node role logs
   `restart_needed=` (`roles/node/tasks/register.yml:40-46`).
4. `kerbside db upgrade` with the venv's binary, `run_once`, delegated to
   `kerbside_hosts[0]`, as `kerbside_user`. Then check that its output
   does not show the INI silently failing (kerbside#313: it would exit
   0 having done nothing). Read `kerbside/main.py:403-437` in
   `../kerbside` to see what success prints, and assert on that.
5. Both units `enabled: true`, `state: restarted` when
   `kerbside_restart_needed`, else `started`, `daemon_reload: false`.
6. Wait for `GET http://127.0.0.1:{{ kerbside_api_port }}/` with
   `Accept: application/json` to return 200 (`ansible.builtin.uri`,
   retries).
7. Run `kerbside-ready.py` (`run_once`, `delegate_to: kerbside_hosts[0]`,
   `no_log: false`; it prints no secret). The script imports
   `kerbside.db`, and prints `{"errored": ..., "fetched_at": ...}` for
   source `deploy_name`, with `null` for whatever is absent. It
   tolerates a missing source or key row, and never prints the key. Use
   `failed_when: false` and `changed_when: false`, and `until:` the
   parsed `fetched_at` exceeds `kerbside_ready_after`, for 60 retries of
   3 seconds. Parse stdout defensively: non-JSON output counts as not
   ready (S4). A final assert fails with the last output and
   `journalctl -u kerbside-daemon -u kerbside-api` in the message.
8. Write `kerbside_desired_state` to the marker, 0644.

Read Kerbside's `db.py:198-226` and `:940-981` in `../kerbside`, at
`d0963ff`, for the function names. Run `pre-commit run --all-files`.

### Brief 4 -- `site.yml`

In `examples/_shared/site.yml`:

* Plays 1-2: the reachability probe and the `add_host` loop cover
  `allsf` plus `groups.get('kerbside', [])` (`:47`, `:82`). The absent
  host warning must still name Kerbside hosts.
* A new play straight after play 2, on `kerbside:&reachable`, with
  `gather_facts: false` and `any_errors_fatal: true`, including
  `kerbside` `validate` `run_once` with:
  * `kerbside_hosts: "{{ groups.get('kerbside', []) }}"`;
  * `kerbside_noncolocated_hosts` set to the Kerbside hosts not in
    `groups['allsf']`;
  * `kerbside_url_on_sf_nodes` set to the first `allsf` host's
    `kerbside_url`, defaulting to empty (D3).

  Not play 2 itself: on `localhost`, `validate` would not see
  `group_vars/kerbside` (D2).
* A new play after the final sanity checks, `hosts: kerbside:&reachable`.
  * Play-level vars: `kerbside_hosts` as above, and `kerbside_colocated`.
  * A delegated `slurp` of `/etc/pki/libvirt-spice/ca-cert.pem` from
    `database_tier_hosts[0]`, `run_once`, decoded into
    `kerbside_sf_ca_cert`. Compute `database_tier_hosts` the way play 6
    does at `:236-240`, since play 6's facts are on `allsf` hosts only.
  * `include_role` `kerbside` `tasks_from: bootstrap`.
  * When the override paths are empty, `internal_ca`
    `host_certificate` and `distribute_certificates` with D6's
    parameters, as the existing calls at `:405-422` do.
  * `tasks_from: config`, then `tasks_from: register`.
* The header comment: add `kerbside` to the group list at `:11-18` and
  the new variables to `:22-28`.

The feature-off path must be untouched apart from the widened
reachability loop, and the validation and deployment plays matching no
host. Run `pre-commit run --all-files`.

### Brief 5 -- the rootless test

Write `tools/ci-test-kerbside-role.sh` in the shape of
`tools/ci-test-kerbside-cluster-config.sh`:

* a temporary `ANSIBLE_COLLECTIONS_PATH` symlink;
* `-i localhost, -c local -e ansible_become=false`;
* secrets via `-e @file`;
* numbered cases with a summary line.

Cover every bullet in D10.

* For config, stub `{{ kerbside_venv }}/bin/python` with a symlink to
  the test's `python3`, which has `cryptography`. Generate a certificate
  with `openssl` for the override paths. Point `kerbside_config_dir`,
  `kerbside_unit_dir` and `kerbside_state_dir` at the temporary tree,
  and set the owner and group to the invoking user.
* Skip the apt and user tasks by tag, as `tools/ci-test-internal-ca.sh`
  skips root-only tasks.
* For `site.yml`, build a two-file test inventory under the temporary
  directory: hosts in `allsf`/`hypervisors`/`database_node`, plus a
  `kerbside` group with one co-located host and one dedicated host. Run
  `ansible-playbook --list-hosts examples/_shared/site.yml`. Assert from
  the per-play host lists that the dedicated host appears only in the
  reachability and Kerbside plays. Repeat with no `kerbside` group.

Wire it like the existing scripts: a `sanity_checks` step in
`.github/workflows/functional-tests.yml` after
`ci-test-kerbside-cluster-config.sh` (`:235-239`), and a
`test-kerbside-role` pre-commit hook whose `files:` regex covers
`roles/kerbside/`, `examples/_shared/site.yml` and the script. Add a
bullet to `docs/developer_guide/standards.md` beside the existing two.

Then mutation-test it from a copy, never with `git checkout`. Break
each assertion in `validate.yml`, the `%` doubling, the audience,
`ca_cert`, the `validate:` on the INI, a file in the desired-state hash,
and the widened reachability loop. Report which case caught each
mutation. Every mutation must fail a case.

### Brief 6 -- documentation

* `shakenfist/deploy/collection/README.md`: a `kerbside` row in the
  roles table (`:16-21`). Extend the `## Kerbside` section (`:193-213`)
  with the group, the required variables, the certificate overrides and
  the package floor (D4). Say that the role's cluster test arrives with
  the merge-queue lane.
* `docs/operator_guide/installation.md`: the Kerbside variables beside
  phase 2's (`:211-224`), and the database the operator creates (name,
  user, grant). Keep it short; phase 6 writes `kerbside.md`.
* `docs/operator_guide/vdi_console_tokens.md`: one paragraph pointing
  at the group as the deployer path. Its "manual step" section stays
  until phase 6.
* The release note beside phase 2's in `docs/release_notes/v07-v08.md`.

No fact about the group or its variables may be stated differently in
the README, `installation.md` and `argument_specs`. Do not edit
`docs/components/kerbside/` (synced from Kerbside).

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| `register` has never run anywhere when this merges. | D10: the release note and README say so. Phase 4's lane is its first run, and phase 4 is planned at high effort. The management session reads brief 3's output against Kerbside's source, line by line, rather than trusting the summary. |
| The widened reachability loop changes the feature-off path for every cluster. | It is the only feature-off change. The PR's smoke cluster runs it, and the management session checks that cluster's log for the probe play's host list. Brief 5's feature-off `--list-hosts` case pins it. |
| `PROXY_HOST_SUBJECT` drifts from what clients compute, so consoles fail only at connect time. | D5's unit test compares the script with `node.py` itself. |
| A package floor on an unreleased version makes the default uninstallable. | Intended (D4), and confirmed by the operator. Phase 4 lists the release as its prerequisite. |
| The readiness wait adds up to a minute to every deploy with Kerbside. | Accepted (D8), with the cheaper variant in Future work. |
| A malformed render takes down a running Kerbside. | `validate:` on both files means the old file stays in place (D7), and brief 5 tests that for the INI. |
| `fetched_at` is written by any Kerbside host's daemon; with N hosts, clock skew could make the comparison pass early. | Only N=1 is tested (master plan open question 8). The check is `run_once` on the host whose clock set the threshold, and the risk is recorded in Future work. |

## Definition of done

* `tools/ci-test-kerbside-role.sh` exits 0, the sanity-checks job runs
  it, and every mutation in brief 5 fails a named case.
* `python3 -m stestr run test_kerbside_host_subject` passes, and fails
  when the script's escaping order is swapped.
* `grep -rn "sf.target" shakenfist/deploy/collection/roles/kerbside/templates/`
  prints nothing, and `grep -n WantedBy` on both unit templates prints
  `multi-user.target`. (The role's tasks mention `sf.target` once, in a
  comment explaining why the units avoid it.)
* `grep -rn "kerbside" examples/_shared/site.yml` shows the group read
  only through `groups.get('kerbside', [])`, and
  `grep -rn "groups\[\|hostvars" shakenfist/deploy/collection/roles/kerbside/`
  prints nothing.
* Every default in `roles/kerbside/defaults/main.yml` is in its
  `argument_specs` (the unit test), and every secret variable there
  has `no_log: true`.
* The PR's smoke cluster passes with `site.yml` from this branch, and
  its log shows the Kerbside plays matching no hosts.
* No fact about the `kerbside` group, its variables or its package
  floor differs between the collection README, `installation.md` and
  `argument_specs`.
* `pre-commit run --all-files` passes, including the new hook.
* The master plan's phase 3 row reads `Complete` with its `Merged` cell
  filled. Phase 4's first commit does that, per `plan-phase-landing`.

## Back brief

Before step 1, the management session confirms its understanding of the
plan with the operator. Two decisions were put to the operator while
planning, and both were answered on 2026-10-05:

1. **The package floor (D4):** floor on the next Kerbside release,
   `kerbside>=0.7.0`. Cutting that release is a prerequisite of phase 4.
2. **The readiness wait (D8):** wait for a fresh signing-key fetch on
   every deploy, including one that restarted nothing.

## Future work

* **Skip the readiness wait when nothing restarted** (D8), if a minute
  per deploy proves too much. It would accept a `fetched_at` younger
  than two scrape intervals.
* **A source-status command in Kerbside**, such as `kerbside source
  status --json`, reading the same rows. It would replace
  `kerbside-ready.py`'s import of Kerbside internals with a supported
  interface, and its exit status could be trusted once kerbside#313 is
  fixed.
* **Readiness across N Kerbside hosts**, if more than one is ever
  tested: per-host freshness rather than a shared row.
* **Kerbside's documentation overstates the proxy's use of
  `CACERT_PATH`** (`docs/proxy-architecture.md:52-53`; only the API
  reads it). That joins phase 6's Kerbside documentation fixes.
* **A local `kerbside` wheel in `site.yml`'s local-wheel mode.** Play 4
  builds Shaken Fist wheels for `allsf` only. Phase 5 decides whether
  Kerbside's lane passes its wheel through `kerbside_package` or needs
  more.
