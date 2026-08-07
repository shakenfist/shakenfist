# Two-tier CI phase 1: the oVirt lane deploys and drives kerbside

Phase 1 of [PLAN-two-tier-ci.md](/components/kerbside/plans/PLAN-two-tier-ci/). Read
that master plan first: it holds the prompt, the tier split,
the oVirt front-door architecture decision, and the agent
guidance this phase inherits.

This phase is independent of the master plan's precondition
(sf-e2e PR-readiness) and can start immediately. It does not
move any job between tiers — that is phase 3. It makes the
oVirt lane worth keeping before we decide where to keep it.

## Situation (grounded)

### What the lane does today

`.github/workflows/functional-tests.yml`'s `ovirt_matrix`
job (lines 124-409) builds a complete single-node oVirt 4.5
environment on Rocky 8 — engine plus hypervisor on one SF
instance at `10.0.2.2`, FQDN `ovirt.local` — boots a
SPICE-enabled Debian 12 GNOME guest with the agents
pre-installed, and then runs two checks **on the target**:

- `tools/test-ovirt-console.py` — talks to the engine API,
  asserts the VM has a SPICE display, lists its graphics
  consoles, and opens a raw SPICE link handshake against the
  hypervisor's plaintext console port.
- `tools/dump-ovirt-host-subject.py` — non-gating
  diagnostic, prints each host's
  `certificate.subject` and a spice-common grammar verdict.

Neither deploys kerbside. The PR's code is never installed on
anything; `kerbside/sources/ovirt.py` never runs; the oVirt
ticket branch in `ConsolesProxyVirtViewer`
(`kerbside/api.py:465-473`) has never executed in CI; and no
byte of SPICE has ever traversed the Rust proxy from an oVirt
hypervisor. As a gate this lane validates that oVirt still
installs, not that kerbside still works.

Closing this also closes a recorded item from
`PLAN-host-subject-phase-02-kerbside-adoption.md`'s future
work: *"Wiring the oVirt lane's console test through the proxy
with `secure_port`/`host_subject` (true cross-hypervisor
enforcement proof)"*.

### What the last green run tells us

Run 30692971441, job 91351363033 (2026-08-01, ~42 minutes
wall clock, `m` runner). Facts taken from its log, not
assumed:

- The console the engine reports is
  `protocol=spice, address=10.0.2.2, port=5900,
  tls_port=5901`. **Both** ports are populated, and the
  address is an IP, not a name.
- The plaintext port answers the SPICE link handshake with
  `NEED_SECURED (5)`. That is exactly the condition
  `backend.rs:93` looks for before retrying on
  `target.secure_port`, so the proxy's escalate-to-TLS path
  is the path this lane will exercise.
- The host certificate subject is `O=local,CN=ovirt.local`,
  verdict `PARSES`. (`O=local` comes from
  `OVESETUP_PKI_ORG=str:local` in
  `shakenfist/actions`' `ovirt-45-rocky-8-answers.conf.j2`.)
  No scrape-time normalisation is needed.

So every value `kerbside/sources/ovirt.py:108-117` yields is
present and well-formed in this environment. Nothing about
the environment blocks this phase.

### Why kerbside cannot run on the oVirt node

`pyproject.toml:17` sets `requires-python = ">=3.11"`.
The oVirt target is Rocky 8, whose system Python is 3.6;
getting 3.11, MariaDB, and a `mysqlclient` build environment
onto it would be substantial work whose only purpose is to
avoid a network hop we actually want to exercise.

The runner does not have that problem, is already Debian 12
with the whole direct-qemu toolchain pattern proven on it,
and — per `kerbside-single-node.yml`'s "Additional tasks for
CI" block — is itself attached to the `10.0.2.0/24` test
network, so it routes to `10.0.2.2` directly.

## Mission

Extend the oVirt lane so that, after the environment is
built, it:

1. deploys **the PR's** kerbside (package + Rust proxy
   wheel) on the CI runner;
2. registers a `type: ovirt` source pointed at the engine
   and proves discovery populates a console row with the
   scraped address, ports, and `host_subject`;
3. relays a real SPICE session from `ryll --headless`
   through kerbside's proxy to the oVirt hypervisor, over
   the backend's TLS leg with certificate-subject pinning;
4. asserts the session is recorded and that API-driven
   termination removes it;
5. leaves the proxy, gunicorn, and ryll logs in artifacts.

## Architecture decision: kerbside runs on the CI runner

Kerbside is deployed on the runner, off-box from oVirt,
reaching the engine at `https://ovirt.local/ovirt-engine`
and the hypervisor at `10.0.2.2:5900/5901`.

Consequences worth stating plainly:

- **No `shakenfist/actions` change is required.** Unlike
  sf-e2e — which needed the
  `deploy-kerbside-on-shakenfist` composite action because
  its kerbside lives on a remote primary — everything here
  runs locally on the runner. The master plan's note that
  phase 1 "touches shakenfist/actions as well as this repo"
  is superseded: the only case that would drag that repo in
  is target-side firewall prep (see Risks), and only if the
  reachability probe shows it is needed.
- The lane exercises the realistic front-door topology
  (option (a) in the master plan): kerbside on its own host,
  no squid, `SpiceProxyDefault` never set. That makes it a
  worked example phase 4 can distil into docs.
- Ports are confusing in logs and this is not a conflict:
  kerbside's *client-facing* proxy binds `5900`/`5901` on
  the **runner**, while the oVirt hypervisor's SPICE ports
  are `5900`/`5901` on **`10.0.2.2`**. Different hosts.

Rejected alternatives:

- **Co-locate kerbside on the oVirt node** (the sf-e2e
  shape). Blocked by the Python 3.11 floor on Rocky 8.
- **A dedicated Debian kerbside instance on the test
  network.** Closest to production and a genuinely better
  demonstration, but costs another SF instance and another
  provisioning path for no additional coverage of kerbside
  itself. Recorded as future work.

## The path being proven

```
ryll --headless
  -> kerbside proxy        127.0.0.1:5901  (plaintext)
                           127.0.0.1:5900  (TLS, proxy CA)
  -> hypervisor            10.0.2.2:5900   -> NEED_SECURED
  -> hypervisor            10.0.2.2:5901   TLS: verified
                           against the engine CA, subject
                           pinned to O=local,CN=ovirt.local
  -> qemu on the oVirt host, authenticated with a fresh
     engine-issued graphics-console ticket
```

Everything on the right of the first arrow is code that has
never run in CI.

## Configuration details that would otherwise each cost a CI cycle

These are the traps found while reading the source. Put them
in the implementing agent's brief verbatim.

1. **The source `url` has no `/api` suffix.**
   `oVirtSource._ensure_connection` appends `/api` itself
   (`ovirt.py:68`) and the CA check appends
   `/services/pki-resource?...` (`ovirt.py:46-48`). The
   correct value is
   `https://ovirt.local/ovirt-engine`. The existing CI steps
   pass `.../ovirt-engine/api` to the *test scripts*, which
   is right for them and wrong for `sources.yaml`.
2. **`ca_cert` is inline PEM, and it is compared for
   equality.** `__init__` writes it to a temp file, fetches
   the engine's own copy from
   `<url>/services/pki-resource?resource=ca-certificate&format=X509-PEM-CA`,
   and marks the source errored unless the two match after
   `rstrip()` (`ovirt.py:57-63`). Fetch the CA from **that
   exact URL** (`curl -k`) rather than `scp`-ing
   `/etc/pki/ovirt-engine/ca.pem`, so the bytes match by
   construction.
3. **The runner must resolve `ovirt.local`.** The engine's
   HTTPS certificate is `CN=ovirt.local` and `requests`
   verifies it against the CA, so an IP URL fails. Add
   `10.0.2.2 ovirt.local` to the runner's `/etc/hosts`. The
   *backend* leg needs no DNS — the engine reports the
   console address as `10.0.2.2`.
4. **The oVirt SDK is deliberately not a kerbside
   dependency.** `pyproject.toml:70` and `:161` keep
   `ovirt-engine-sdk-python` commented out, and
   `ovirt.py:28-35` imports it lazily and errors the source
   if absent. The venv must install it explicitly. It is a C
   extension: the runner needs `libxml2-dev`,
   `libcurl4-openssl-dev`, and `build-essential`
   (`bindep.txt` already carries the first and the last for
   dpkg platforms).
5. **oVirt tickets are short-lived** (engine default 120
   seconds) and are minted fresh on every `.vv` request
   (`api.py:465-473`, which also writes the ticket to the
   console row via `db.store_console_ticket`). The driver
   must fetch the `.vv` and launch ryll immediately —
   never fetch, then wait, then connect.
6. **`insecure_port` is tried first.** `backend.rs:93`
   only escalates to `secure_port` after a `need_secured`
   error. In this environment `port=5900` is populated and
   does answer `need_secured`, so the path works — see
   Risks for what to do if that ever changes.

## Execution

New tooling lives in `tools/ovirt-e2e/`, mirroring the
layout and conventions of `tools/sf-e2e/`. All of it runs on
the runner.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | worktree | Create `tools/ovirt-e2e/gen-sources.py` and `tools/ovirt-e2e/deploy-kerbside.sh`. `gen-sources.py` mirrors `tools/sf-e2e/gen-sources.py` in shape and security posture (write 0600 via `os.open`, never echo the password, print only non-secret facts to stderr): it takes `--output`, `--engine-url` (e.g. `https://ovirt.local/ovirt-engine`), `--username`, `--password`, `--source-name`, fetches the CA from `<engine-url>/services/pki-resource?resource=ca-certificate&format=X509-PEM-CA` with verification disabled (this is the bootstrap fetch; `oVirtSource.__init__` re-fetches it verified), and writes a single-element YAML list with keys `source`, `type: ovirt`, `url`, `username`, `password`, `ca_cert`. `deploy-kerbside.sh` is modelled on `tools/sf-e2e/deploy-kerbside.sh` but runs locally (no SSH) and against oVirt instead of SF: install apt prerequisites (`mariadb-server`, `build-essential`, `pkg-config`, `libssl-dev`, `default-libmysqlclient-dev`, `libxml2-dev`, `libxslt1-dev`, `libcurl4-openssl-dev`, `python3-venv`, `openssl`, `curl`); reuse `tools/direct-qemu/setup-mariadb.sh` verbatim for the database; create a venv and `pip install <repo> <proxy-wheel-glob> gunicorn ovirt-engine-sdk-python`; generate TLS with `tools/direct-qemu/generate-tls.sh`; run `gen-sources.py`; start kerbside by reusing `tools/direct-qemu/start-kerbside.sh` unchanged (it already hardcodes the MariaDB URL, `PUBLIC_FQDN=127.0.0.1`, the proxy host subject matching `generate-tls.sh`, and the auth-seed file the driver needs); then poll until `db.get_source(name)['errored']` is false, failing after 120s with the daemon log tail — an errored source is the single most likely failure and its cause is always in that log. Finish by writing `/tmp/kerbside-ovirt-ci/kerbside.env` (venv, workdir, api port, seed file, sources path, source name, engine url) for the driver, exactly as sf-e2e's script writes `kerbside.env`. Default `WORKDIR=/tmp/kerbside-ovirt-ci`, overridable by env. Read the traps in "Configuration details" above and encode them; do not re-derive them. |
| 1b | high | opus | worktree | Create `tools/ovirt-e2e/drive-console.py`, modelled closely on `tools/sf-e2e/drive-happy-path.py` (read it first — reuse its env-file loader, its `_log` convention, its DB-polling helper shape, and its rule that no token, ticket, seed, or `.vv` body is ever printed). Steps: (1) poll `db.get_console(source, uuid)` until the scrape has discovered the VM, resolving the VM uuid by listing consoles for the source and matching the `smoke-test-` name prefix the lane's `start-test-target.py` uses; log the discovered `hypervisor_ip`, `insecure_port`, `secure_port`, and `host_subject` — this is the discovery assertion and also the diagnostic if the later legs fail; assert `secure_port` and `host_subject` are both non-empty and fail loudly if not, since the whole point is the TLS leg. (2) Mint a JWT from the auth seed exactly as `tools/direct-qemu/lane-up.sh` lines 138-168 do (same payload shape; kerbside's `verify_token` only checks signature and expiry). (3) `GET /console/proxy/<source>/<uuid>/console.vv`, write it 0600, and launch `ryll --verbose --headless --file <vv> --control-socket <sock>` **immediately** (see trap 5), redirecting stdout/stderr to the workdir; poll for the control socket for 30s with the same on-timeout diagnostic dump lane-up.sh does. (4) Run `tools/direct-qemu/smoke-client.py` against the socket as a subprocess and require exit 0 — it asserts hello, non-empty surfaces, and a valid PNG screenshot, which is exactly the "real SPICE relayed" assertion we want and is guest-agnostic. Do NOT use `wait-for-banner.sh` or anything digest-related: this guest is Debian GNOME, not Sextant, so build ryll without `digest-decode`. (5) Assert a session row exists for the console in kerbside's DB and that an audit event was recorded. (6) Terminate via `GET /console/terminate/<source>/<uuid>` (check the exact route in `kerbside/api.py` before writing it) and assert the session disappears. Exit non-zero with a specific message on every failed assertion. |
| 1c | medium | sonnet | none | Wire the lane in `.github/workflows/functional-tests.yml`'s `ovirt_matrix` job. Change `runs-on` from `m` to `l` (a release build of ryll plus a MariaDB and a proxy build on the same runner). After the existing "Dump oVirt host certificate subjects" step, add, in order: (i) `echo '10.0.2.2 ovirt.local' | sudo tee -a /etc/hosts`; (ii) a reachability probe from the runner to `10.0.2.2:5900` and `:5901` that fails with an explicit "the hypervisor SPICE ports are not reachable from the runner; check firewalld on the oVirt host" message; (iii) the four proxy-wheel-build steps copied verbatim from the `openstack_matrix` job (lines 496-516: apt prerequisites, `dtolnay/rust-toolchain@stable`, maturin+ziglang venv, `tools/build-proxy-wheel.sh` with `WHEEL_OUT`) — the toolchain they install is also what step (iv) needs; (iv) build ryll from source into `/usr/local/bin/ryll` exactly as `direct-qemu-functional.yml` lines 65-72 do but **without** `--features digest-decode`; (v) run `tools/ovirt-e2e/deploy-kerbside.sh`; (vi) run `tools/ovirt-e2e/drive-console.py`. Add a second `actions/upload-artifact` (`if: always()`, `if-no-files-found: warn`) for `/tmp/kerbside-ovirt-ci/` covering the kerbside daemon log, both gunicorn logs, `sources.yaml`, the ryll stdout/stderr, and the smoke-client log — mirror the direct-qemu lane's artifact step. Leave the existing target-side steps and their artifact bundle untouched: they are cheap and they localise "the environment broke" versus "kerbside broke". Note the workflow already sets `no_proxy` nowhere for this job — add `no_proxy: 127.0.0.1,localhost` at job level, copying `direct-qemu-functional.yml` lines 21-22, or the runner's squid will 503 the loopback API calls. |
| 1d | low | sonnet | none | Write `tools/ovirt-e2e/README.md` in the style of `tools/sf-e2e/README.md`: topology (kerbside on the runner, oVirt at 10.0.2.2), the connection path diagram from this plan, the step flow, the env-file contract, and the security note. Add the new directory to `AGENTS.md`'s key-file map alongside the existing `tools/` entries. No `docs/` changes — the operator-facing oVirt documentation is phase 4's deliverable. |
| 1e | high | opus | none | Bring-up. Driven from the management session, not a standing sub-agent: dispatch `functional-tests.yml` with `target=["ovirt-45-rocky-8"]` and a non-zero `retention`, watch with `ci-status`, and hand each failure to a sub-agent with the failing log excerpt and the relevant artifact. Expect two to four iterations; each full run is ~50 minutes, so batch fixes rather than shipping one-line changes per cycle. Do not merge until a dispatch run is green twice in a row. |

Commits: one per step (1a, 1b, 1c, 1d), then whatever
bring-up fixes 1e produces, each self-contained.

## Risks

- **The hypervisor's SPICE ports may not be reachable from
  the runner.** The existing handshake check runs *on* the
  target, so it proves nothing about the runner's path.
  `engine-setup` runs with `OVESETUP_UPDATE_FIREWALL=yes`
  and host-deploy installs the vdsm firewalld service
  (which opens 5900-6923/tcp), so this is expected to work
  — but the probe in step 1c exists so that if it does not,
  the lane says so in one line instead of failing inside a
  TLS handshake. If prep is genuinely needed, it belongs in
  `shakenfist/actions`' `tools/ovirt-prepare-host.sh`
  (hypervisor role), not in an inline SSH step here.
- **`insecure_port` absent.** If an oVirt configuration ever
  reports `port=None` with only `tls_port` set,
  `insecure_port` lands as 0 and `backend.rs` dials port 0,
  fails with connection-refused rather than `need_secured`,
  and never escalates. That would be a real kerbside defect
  (the backend should go straight to TLS when no plaintext
  port exists), not a lane defect — fix it in the proxy, do
  not work around it in CI. Not expected in this
  environment: the 2026-08-01 run shows both ports set.
- **Subject-pinning mismatch.** Kerbside pins
  `host.certificate.subject` as reported by the engine. If
  vdsm presents a qemu certificate whose subject differs
  from the host certificate's, the TLS leg refuses. That is
  a genuine finding about oVirt integration and should be
  recorded and fixed, not suppressed. The proxy log line
  (`pinned host_subject ... does not match`) plus the audit
  event are the evidence; both are in the artifacts step.
- **Ticket timing.** A slow `.vv`-to-connect gap expires the
  engine ticket and the hypervisor refuses the link. Trap 5
  addresses it; if it still bites, the fix is in the driver,
  not a longer engine ticket lifetime — production clients
  have the same constraint.
- **Runtime.** The lane is ~42 minutes today; ryll's release
  build and the kerbside deploy should add roughly 10-12,
  comfortably inside the existing 120-minute timeout.

## Success criteria

* The oVirt lane installs the PR's kerbside and its proxy
  wheel, and the `type: ovirt` source reaches non-errored
  state — proving `ovirt.py`'s CA equality check and engine
  authentication against a live 4.5 engine.
* Discovery populates a console row whose `hypervisor_ip`,
  `insecure_port`, `secure_port`, and `host_subject` are
  logged, with `secure_port` and `host_subject` asserted
  non-empty.
* `ryll --headless` completes hello, reports non-empty
  surfaces, and returns a valid PNG screenshot through
  kerbside's proxy — i.e. real SPICE traffic from an oVirt
  hypervisor crossed the Rust proxy, over TLS, with the
  certificate subject pinned.
* A session and its audit events exist in kerbside's DB, and
  API-driven termination removes the session.
* The kerbside daemon, gunicorn, ryll, and smoke-client logs
  are uploaded as a distinct artifact bundle.
* `pre-commit run --all-files` passes; `tox -eflake8` and
  `tox -epy3` pass.
* A dispatch run of the lane is green twice consecutively.

## Future work (recorded, not in this phase)

* A dedicated Debian kerbside instance on the oVirt test
  network, instead of co-locating kerbside on the CI runner
  — closer to the documented production topology.
* A prebuilt ryll binary artifact. Three lanes
  (direct-qemu, sf-e2e, and now oVirt) each build ryll from
  source on every run; publishing a binary would cut
  several minutes from each.
* Driving the *direct* (non-proxied) `.vv` endpoint for
  oVirt as well, which exercises
  `ConsolesDirectVirtViewer`'s oVirt ticket branch
  (`api.py:391-397`). Low value while the proxy path is the
  supported one.
* Asserting guest-agent presence: this guest ships
  `spice-vdagent`, so `agent_connected` may become a
  meaningful assertion here in a way it never could in the
  Sextant lanes.

## Status

**Complete.** Steps 1a-1d implemented 2026-08-02 on branch
`ovirt-lane-kerbside`; step 1e (bring-up) finished
2026-08-03 with runs [30776147437][r3] and
[30785102365][r4] green back to back, which is the success
criterion this plan set.

The defect phase 1 targeted is fixed. The oVirt lane no
longer builds an oVirt environment and then tests only that
environment: it deploys kerbside from the PR's own source
and proxy wheel, and relays a real SPICE session from the
oVirt hypervisor through it, over TLS, with the hypervisor
certificate subject pinned.

### Bring-up iterations

**Run 1 — [30763222820][r1], dispatch, failed at "Build ryll
from source" (step 29 of 35).**

Everything up to and including the new pre-flight steps
passed, which settles three of the plan's assumptions
against a live environment rather than against last week's
log: the host certificate subject is still
`O=local,CN=ovirt.local` and still parses, the
`10.0.2.2 ovirt.local` line lands in `/etc/hosts`, and both
`10.0.2.2:5900` and `:5901` accept a connection from the
runner. The proxy wheel also built.

ryll then failed to compile: its `audiopus_sys` dependency
survives `--no-default-features`, `pkg-config` finds no
system Opus on the runner, so the build script falls back
to compiling Opus itself and panics with ``is `cmake` not
installed?``. The prerequisite step had been copied from
`openstack_matrix`, which builds only the wheel and so
needs nothing beyond `build-essential` and `pkg-config`.

Fix: install the same prerequisite set
`direct-qemu-functional.yml` uses, since that workflow has
been building ryll on this runner image successfully.
Adopting the whole list rather than adding `cmake` alone is
deliberate — a full cycle is ~50 minutes, so discovering
the next missing header one run at a time is the expensive
way to do this. Batched with it: the venv `pip install` in
`deploy-kerbside.sh` now retries up to three times, because
`ovirt-engine-sdk-python` is first-touch for this project's
CI and a caching index mirror can report a spurious "no
matching distribution" on a first fetch.

Nothing downstream of the ryll build has executed yet, so
the deploy and drive scripts remain entirely unproven.

**Run 2 — [30765419311][r2], failed at "Deploy kerbside on
the runner" (step 30 of 35).**

The prerequisite fix worked: ryll 0.1.7 compiled in 2m10s
and installed, so the direct-qemu package list is the right
one for this lane.

`deploy-kerbside.sh` then got as far as the venv install
before failing. The workflow passed `--proxy-wheel` in
single quotes, which suppresses `${GITHUB_WORKSPACE}` as
well as the glob, so pip received a literal dollar sign and
reported `Invalid wheel filename (wrong number of parts):
'*'`. The wheel itself was built correctly and was sitting
where it was meant to be. Double quotes are what this call
wants: the glob has to survive the call so the script
expands it at the point of use, but the variable has to
expand at the call site.

Two things this exposed, both fixed alongside it. The
script now resolves the glob during argument validation and
fails in one line if it does not match exactly one existing
file, rather than passing an unexpanded pattern down to pip
and surfacing as that much more confusing message. And the
new pip retry, which exists for transient index misses,
had dutifully retried a completely deterministic failure
three times; resolving the wheel up front means a bad path
fails immediately and only genuinely transient failures
reach the loop.

Verified before re-dispatching, since a wrong guess costs a
full cycle: `start-kerbside.sh` accepts exactly the five
arguments `deploy-kerbside.sh` passes, derives its seed
file to the same path the driver reads
(`$(dirname PID_FILE)/kerbside-auth-seed.txt`), and finds
`alembic.ini` by walking up from the checkout. The glob
validation was exercised locally against both a matching
and an unexpanded pattern.

Still unproven: everything from `generate-tls.sh` onward —
source generation, the source health poll, and the whole of
`drive-console.py`.

**Run 3 — [30776147437][r3], green.** The first time
kerbside has ever been deployed and exercised in this lane.

The gate did not pass vacuously; the evidence for each
stage, from the job log and the `kerbside-ovirt-*`
artifact:

| Stage | Evidence |
|-------|----------|
| oVirt scrape | discovered `smoke-test-5863`, `insecure_port=5900 secure_port=5901 host_subject=O=local,CN=ovirt.local` |
| Proxy launch | `kerbside-proxy` from the PR's own wheel, subject-pinned |
| TLS escalation | all four channels (main, display, cursor, inputs) logged `hypervisor requires TLS; retrying backend connection over the secure port` |
| Real session | `agent_connected=True`, surface 1024x768, screenshot 36446 bytes with PNG magic |
| Audit and teardown | 10 audit rows, session active, terminated via the REST API, termination event recorded |

Subject pinning really was exercised, which is worth
stating because it fails silently in the passing direction.
The scrape supplied a non-empty `host_subject`, which
`build_config` maps to `Some(...)` rather than the `None`
that would disable verification (`backend.rs:200-206`), and
the only certificate log line concerns the *hostname* check
being bypassed. A pinning failure logs `TLS: rejecting
certificate: pinned host_subject ...` instead, per the test
at `backend.rs:306`.

One piece of grit, not a failure: every channel ends with
`relay ended with error ... peer closed connection without
sending TLS close_notify`. That is ryll disconnecting
abruptly rather than a relay fault, but it is logged at
WARN, so a genuine relay error during teardown would blend
straight into it. Worth quietening later; not phase 1's
problem.

This does not finish step 1e on its own. The success
criterion is two consecutive green dispatch runs, and one
green could still be luck given the roughly 120 second
oVirt ticket window `drive-console.py` races against.

Alongside this, both `Retain the environment if requested`
steps changed from `github.event_name == 'workflow_dispatch'`
to `!cancelled() && github.event_name == 'workflow_dispatch'`.
Without it a failing step skipped the retention step and the
environment was torn down regardless, so the `retention`
input only ever held open environments belonging to runs
that had already passed — the runs nobody needs to log into.
`!cancelled()` rather than `always()` so that a run someone
deliberately cancelled still releases its environment.
`openstack_matrix` had the byte-identical defect and got the
same fix.

**Run 4 — [30785102365][r4], green. Step 1e complete.**

The second consecutive green, on a freshly built
environment and a different console
(`smoke-test-3881`, uuid `f6c265fe...`), with the same
evidence at every stage as run 3. Two independently built
environments producing the same result is what makes this a
lane rather than a lucky run.

Dispatched with `retention=60` to exercise the
`!cancelled()` fix in anger. It behaved: the job ran 1h43m
against roughly 43 minutes of actual work, the balance
being the retention sleep, and the step reports success
where the old condition would have skipped it on any
failure.

### Follow-up, deliberately not done here

Every channel teardown logs `relay ended with error ...
peer closed connection without sending TLS close_notify` at
WARN. It is ryll disconnecting abruptly rather than a relay
fault, but at WARN a genuine relay error during teardown
would be camouflaged by it. That is a proxy logging
question rather than a CI one, so it belongs in its own
change against `rust/kerbside-proxy/src/relay.rs`.

[r1]: https://github.com/shakenfist/kerbside/actions/runs/30763222820
[r2]: https://github.com/shakenfist/kerbside/actions/runs/30765419311
[r3]: https://github.com/shakenfist/kerbside/actions/runs/30776147437
[r4]: https://github.com/shakenfist/kerbside/actions/runs/30785102365

Deviations from the plan as written, decided during
implementation review:

- `no_proxy` for the job is
  `127.0.0.1,localhost,ovirt.local,10.0.2.2`, not just the
  loopback pair the plan implied by pointing at
  direct-qemu's value. The oVirt source reaches the engine
  with python `requests`, which honours `http_proxy` — the
  existing ssh/scp steps do not, which is why the current
  lane has never needed this. Without the engine in
  `no_proxy` the runner's squid would 503 every engine call
  and the source would error.
- `sources.yaml` is deliberately NOT uploaded as a CI
  artifact. It holds the engine admin password, which is
  why `gen-sources.py` writes it 0600; uploading it would
  contradict that. The daemon log already says which source
  it loaded and why it errored, which is what anyone
  debugging actually needs.
- `gen-sources.py` rejects an `--engine-url` ending in
  `/api` outright rather than only documenting the trap.
- The health poll in `deploy-kerbside.sh` waits on
  `errored` being false, which is only meaningful because
  `daemon_run()` (`main.py:261-263`) calls
  `_parse_sources()` synchronously before launching the
  Rust proxy, and `start-kerbside.sh` has already blocked
  on the proxy's listener. Verified against the source; the
  script carries a comment saying so, because reordering
  daemon startup would silently invalidate it. A transient
  first-round failure self-heals: `_parse_sources()` clears
  the error state on a later successful scrape
  (`main.py:166`) and the maintenance loop runs every 60
  seconds, so the 180-second deadline allows three
  attempts.

### Review follow-ups (PR #223)

The automated PR review confirmed the lane closes the
coverage hole, and surfaced two assertion gaps the bring-up
notes had already half-admitted, both fixed in-branch:

- `drive-console.py` now asserts from the proxy log that
  the backend leg escalated to TLS and that subject pinning
  did not reject, instead of only asserting the console row
  carried `secure_port`/`host_subject`. The bring-up notes
  had read this evidence out of the run-3 log by hand; the
  driver now enforces it, which is what makes it a
  regression gate rather than an observation.
- The REST terminate now happens while ryll is still
  connected, and the driver waits for the proxy's
  `session terminated by control plane` line (the
  `verify-terminate-live.sh` oracle). The previous
  post-teardown `_session_present()` check was tautological:
  terminate deletes the `ConsoleToken` row that
  `get_sessions()` keys on, so it could not fail once the
  API returned 200.

Smaller review items also addressed: `kerbside.env` is now
actually uploaded as an artifact (it is secret-free by
design, and two comments claimed it was uploaded already);
the `gen-sources.py` bootstrap-fetch docstring now calls
itself trust-on-first-use instead of overclaiming MITM
protection; the lane's artifact step moved to
`upload-artifact@v7` to match the rest of the repo; the
workdir is defined once as job-level `WORKDIR`; the
no-teardown / plain `/etc/hosts` append is now documented
as safe because the vm-labelled runners are single-use;
and `tools/ovirt-e2e/` was added to ARCHITECTURE.md and
`docs/testing.md`. Passing the engine password on argv was
left as-is deliberately — it matches the sf-e2e convention
and the credential is a public CI throwaway; tightening it
should be done uniformly across both lanes or not at all.

### Review follow-ups (PR #250)

The review of the follow-up branch itself found that the
new pinning check was the same class of never-can-fail
oracle the branch had just removed from the terminate path:
an empty `host_subject` maps to `None` in `build_config`,
silently disabling verification while both the escalation
line and the absent-rejection check still pass, and a real
rejection would fail the smoke client before the log check
ran. Fixed with a positive oracle: the escalation `info!` in
`backend.rs` now logs the `host_subject` it is about to
apply, and `drive-console.py` requires a non-empty pin on
every escalation line. Both load-bearing `info!` sites
(`backend.rs` escalation, `relay.rs` terminate) gained
`CI-ORACLE` markers naming their consumers.

Also from that review: the rejection needle was shortened to
`pinned host_subject` (the message is produced by the ryll
crate, not this repo, so the long form could drift silently;
`verify-rust-proxy.sh` already matched the short fragment);
the job-level workdir was renamed to `OVIRT_LANE_WORKDIR`
and mapped to `WORKDIR` only on the deploy and drive steps,
because a job-level `WORKDIR` leaks into steps running
shakenfist/actions scripts; "secret-free by construction"
wording was replaced with the precise invariant (nothing
beyond the well-known loopback MariaDB credential); and the
driver now fails distinctly when the daemon log is missing
instead of reporting the oracle as absent.

## Back brief

Before executing any step, back brief the operator on the
intended approach and on any deviation from this plan
discovered while implementing.
