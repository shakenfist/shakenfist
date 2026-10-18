# Phase 4 -- A merge-queue lane with the feature on

Part of [PLAN-kerbside-deployer.md](PLAN-kerbside-deployer.md). Phase 3
([PLAN-kerbside-deployer-phase-03-kerbside-role.md](PLAN-kerbside-deployer-phase-03-kerbside-role.md))
landed in #4465 as `b85005f0e`; this phase's first commit closes it out.

**Planning effort:** high, as the master plan asks. The phase crosses
three repositories, changes what the slowest merge-queue row deploys, and
gives the `kerbside` role's `register` entry point its first real run.
**Review effort:** high for steps 1-3 (the actions change, which every
Shaken Fist ecosystem repository consumes unpinned at `@main`) and step
7 (the exchange test, whose assertions are the phase's proof). Medium
elsewhere.

## Scope

**In scope:**

* A **deploy profile** mechanism in `shakenfist/actions`. A caller of
  `smoke-cluster.yml` names a profile file in its own checkout. The
  profile adds inventory groups and group variables, supplies extra
  variables, creates databases on the CI MariaDB, exports variables to
  the test run, and can ask for a second deploy that must restart nothing
  (D1, D2).
* A second-deploy check for `sf-*` and `kerbside-*` units (#4459, D3).
* Kerbside journals in the CI log bundle, and `kerbside-*` units in the
  per-node failure checks (D4).
* A `kerbside` profile in this repository, used by the existing
  `debian-13-slim-tier` merge row (D5, D6).
* An IP SAN on the Kerbside proxy certificate when
  `kerbside_public_fqdn` is an address (D7).
* `test_vdi_tokens.py`'s mint test un-skipped when the capability is
  advertised, and made to fail when the lane expects it but the cluster
  does not advertise it (#4093, D8).
* A new exchange test that fetches a Kerbside-routed `.vv` and verifies
  the proxy's TLS endpoint against it (D9). It also includes the live
  check from #4097's 2026-09-06 comment that the signing key appears in
  no daemon's environment (D10).
* The developer guide's CI page, the collection README's "register has
  never run" caveat, and a release note.

**Out of scope, and why:**

* **Cutting Kerbside 0.7.0.** That is the operator's release to make.
  It is a gate in the back brief, not a step (S5).
* **A separate cluster for the lane.** The master plan's dependency on
  the CI sizing plan says to add a group to an existing topology rather
  than a new VM (D5).
* **Driving a SPICE session through the proxy.** That stays in
  Kerbside's lane, which already has ryll and the drivers.
* **Moving Kerbside's own lane onto the collection.** That is phase 5,
  which this phase's profile mechanism is shaped to serve.
* **Closing kerbside#482.** It is stale (S5) but belongs to the
  Kerbside repository; it is reported to the operator instead.
* **Re-measuring or re-arming the headroom gate.** Kerbside adds no
  instances, so the band is unaffected (S3).

## What the survey found

Three surveys ran before this plan was written:

* `shakenfist/actions` at `ceb4d27`;
* Kerbside at `aef2d2f`;
* this repository at `9da7ec27d`.

Phase 3's definition of done was spot-checked on `develop` as part of
the completion check:

* `tools/ci-test-kerbside-role.sh` passed all nineteen cases.
* The `sf.target` and `groups[`/`hostvars` greps are empty.
* Both units are `WantedBy=multi-user.target`.
* The CI step and the pre-commit hook are wired.

Findings that change the plan come first.

### S1 -- this repository cannot add a group or a variable to the CI deploy

`smoke-cluster.yml`'s inputs (actions `:46-119`) carry no deploy
variables and no hooks. `build-smoke-cluster` passes only topology, base
image and three secrets (`action.yml:21-41`).

`tools/deploy-collection.sh` takes six positional arguments and hands
`site.yml` one fixed `--extra-vars` string (`:30-35, :65-81`). It has
no `-e @file`.

Inventory groups come from three hardcoded role flags:

* the facts template, `ansible/ci-include-common-localhost.yml:44-46`,
  which iterates `allsf` only (`:39`);
* `build_node`, `tools/ci-make-inventory.py:118-120`;
* `group_flags`, `:84-95`.

The absent-node branch hardcodes its flags too (`:49-57`). The master
plan listed "a `kerbside` group in a topology" and "an extra-vars file".
The real change also touches the facts template, the inventory
generator and the reusable workflow's inputs.

What this repository does control is anything the actions side reads
out of the component checkout:

* `site.yml` and the collection (`deploy-collection.sh:37, 46-50, 65`);
* the MariaDB bootstrap files (`build-smoke-cluster:120-132`);
* `tools/ci-jwks-ca.sh`, which `smoke-cluster.yml:306-320` runs after
  the deploy and tolerates being absent. That is the precedent for
  keeping lane content here and the mechanism in actions.

### S2 -- the second deploy has nowhere to go, and must come early

The deploy runs once, inside `build-smoke-cluster` (`:167-209`). No
existing check looks at unit restarts anywhere (`NRestarts`,
`InvocationID` and `ActiveEnterTimestamp` appear nowhere in either
repository).

Two later steps restart `sf-api` on purpose:

* the JWKS CA step (`smoke-cluster.yml:306-320`, which runs this
  repository's `tools/ci-jwks-ca.sh:83-101`, which appends to
  `/etc/sf/config`);
* the drain check (`:574-580`).

A second deploy after either would legitimately restart `sf-api`. The
check therefore belongs inside `build-smoke-cluster`, straight after
the first deploy.

Ansible's changed count is useless as a signal. Many `site.yml` tasks
are `changed_when: true` on every run, such as the `set-config` steps
and log rotation.

### S3 -- the headroom band measures the vCPU ledger, so Kerbside does not change a shape

`test_headroom_gate_workflow_seams.py:44-58` defines a shape as
`(topology, tier, test_kind, stestr_config)`. It says why `base_image`
is not part of a shape: "the band measures the cloud's committed vCPU
against its ledger, not the guest". Kerbside adds processes to a node,
not instances to the ledger. A slim-tier `cluster-ci.conf` run with
Kerbside on is therefore the same measured shape, and its gate can stay
as it is.

The master plan's "`headroom_gate: false` until it is measured
(#4367)" was wrong twice:

* #4367 is about jobs that bypass `smoke-cluster.yml` and so get no
  probe at all.
* A new job would be measured but unarmed, not unmeasured.

Its claim that the load-budget instrument has a call-site seam in the
same test is also wrong. The load budget is the in-suite test
`test_no_unbudgeted_fixed_rate_database_polling`
(`shakenfist_ci/database_tier.py:439, 790-803`), and it has no
declaration.

Reusing the existing row also avoids three declarations a new job
would need:

* a bundle name in `tools/ci_headroom_harvest.py`'s tables
  (`:181-230`), without which the weekly harvest raises
  `UnknownBundleError` (`:555-577`) and no unit test notices;
* a `CLUSTER_TOPOLOGIES` entry in `test_ci_structural_minimum.py`
  (`:555, 604-618`);
* a `can_merge.needs` entry (`functional-tests.yml:920-926`).

### S4 -- with the default `api_url`, the scrape never crosses hosts

`api_url` defaults to `http://localhost:13000` in both roles
(`roles/kerbside/defaults/main.yml:32`, `roles/node/defaults/main.yml:35`),
and every `allsf` node runs `sf-api`. Kerbside co-located on a tier
node therefore scrapes its own node over loopback. Only the CA slurp
and the database would cross hosts.

The master plan's "so the scrape crosses hosts" holds only if the lane
sets `api_url` for the Kerbside host. The node role uses the same
variable for that host's `sfrc` and `shakenfist.json`
(`roles/node/templates/sfrc:7`, `shakenfist.json:4`).

### S5 -- the release gate, and what 0.7.0 carries

PyPI's newest Kerbside is still 0.6.0 (2026-09-07). There are 265
commits on Kerbside's develop since. The source-secret fixes landed in
kerbside#436:

* `3c5a309` and `8294f22`: the `SOURCE_PUBLIC_FIELDS` allowlist,
  `kerbside/db.py:58-62`;
* `738c718`: console tickets get the same treatment;
* `55bb789` and `b8fc670`: SPICE passwords and tickets stop reaching
  logs.

A release is a `v*` tag plus approval of the `release` environment
(`RELEASE-SETUP.md:168-178`). Its stamp script pins `kerbside-proxy`
exactly (`tools/stamp-proxy-version.sh:113-120`). No issue, milestone
or label marks anything as blocking it. A develop install, by
contrast, resolves the rolling `kerbside-proxy>=0.4.0.dev0` and pulls
whatever dev wheel is newest.

kerbside#482 (nightly sf-e2e failing) is open, but the last ten
nightly runs (2026-09-27 to 2026-10-06) passed. Its body asks to be
closed by hand.

### S6 -- what the exchange returns, and what a test can check

`GET /sf-console.vv?token=<jwt>` (Kerbside `api.py:718-846, 943`) is
unauthenticated; the token is the credential. Its behaviour:

* **Verification is offline:** EdDSA against keys cached from SF; the
  audience must equal `SF_CONSOLE_TOKEN_AUDIENCE` exactly
  (`sf_token.py:121-123`).
* **Returns** a `.vv` from `VIRTVIEWER_TEMPLATE` (`api.py:365-380`)
  carrying:
  * `host = PUBLIC_FQDN`, `port = 5901`, `tls-port = 5900`;
  * a 48-character `password` valid for one minute;
  * `ca`, with its newlines escaped as the two characters `\n`
    (`:819-822`);
  * `host-subject`, present when set (`:824-827`).
* **An unscraped console** returns 404 without burning the `jti`
  (`:791-798`). The scrape runs every 60 seconds. Only instances in
  state `created` with SPICE video are scraped
  (`sources/shakenfist.py:154-159`).
* **A replay** returns 401 `token already used` (`:784-788, 807-813`).
* **The 5900 TLS handshake** completes before any SPICE bytes. It uses
  rustls, with a single certificate and no SNI requirement
  (`rust/kerbside-proxy/src/listen.rs:196-228`, `tls.rs:42-51`). A
  Python `ssl` client can therefore handshake, inspect the certificate
  and close.

On the Shaken Fist side:

* The mint endpoint is `external_api/instance.py:2129-2195`, not
  `:2114-2134` as the master plan cited. It returns 404 without
  `KERBSIDE_URL`, 409 for a non-SPICE instance and 500 on a missing
  key.
* `get_vdi_console_proxy` and `get_vdi_token_public_keys` are on
  client-python develop (`apiclient.py:1373-1407`).
  `test_vdi_tokens.py:21-24` still says they are unmerged.
* The client helper `get_vdi_console_proxy_file` does one
  `requests.get` with no retry, so it cannot ride out the first
  scrape.

### S7 -- a CI Kerbside host will be addressed by IP

CI nodes have no DNS, so `kerbside_public_fqdn` will be the Kerbside
host's mesh address. `site.yml` issues the proxy certificate with that
value as CN and as a **DNS** SAN only (`examples/_shared/site.yml:829-850`),
although `internal_ca` supports `cert_san_ip`
(`roles/internal_ca/vars/main.yml:12`). A client that checks the host
name against an address therefore fails. SPICE clients given a
`host-subject` compare the subject instead, which is why nothing has
broken. An operator who addresses Kerbside by IP has the same
certificate.

### S8 -- smaller facts the briefs depend on

* **`slim-tier`** (actions `ansible/ci-topology-slim-tier.yml`) is
  three 6 vCPU / 12 GiB nodes:

  | Node | Mesh address | Roles |
  |---|---|---|
  | `primary` | 10.0.1.10 | database tier (first), network node, hypervisor |
  | `sf1` | 10.0.1.11 | database tier, hypervisor |
  | `sf2` | 10.0.1.12 | hypervisor only |

  The suite runs on `primary` over ssh (`smoke-cluster.yml:369-398`).
* **MariaDB** runs on `primary` only, bound to `0.0.0.0`. The
  `shakenfist` user is granted `shakenfist.*` only
  (`tools/bootstrap-mariadb.sql:35-38`). Admin access is
  `sudo mariadb` over the unix socket, reached with
  `ssh -i /srv/github/id_ci <user>@primary`.
* **The merge matrix** passes
  `headroom_gate: ${{ vars.CI_HEADROOM_GATE != 'false' }}` to every
  row (`functional-tests.yml:557-578`). The slim-tier row is `:547-554`
  and takes 27-42 minutes.
* **`smoke_collection`** (the PR lane) excludes `merge_group` (`:434`)
  and runs the `localhost` topology. It stays feature-off.
* **Reusable-workflow inputs are strictly declared.** Passing
  `deploy_profile` to an actions `main` that lacks the input fails the
  workflow, so the actions change must land first.
* **The node checks** look only at `sf-*.service`
  (`tools/ci_node_checks.sh:45-50, 81-95`). The clingwrap failure
  config collects `sf-*` journals only
  (`ansible/files/shakenfist-ci-failure-loki.cwd:24-25`).
  `kerbside-api.service` has `Restart=always`, so a crash-looping
  Kerbside would pass every existing check.
* **`shakenfist_ci` does not import `shakenfist`.** Unit tests load its
  modules by path (`shakenfist/tests/test_database_tier_harness.py:33`).
* **Kerbside's freeze is part of the restart hash** (phase 3 D7). A
  package installed from a changing path would restart Kerbside on the
  second deploy. Installing from PyPI does not change it; SF's
  local-wheel rebuild does not either, because the node role hashes
  installed files (collection `README.md:24-35`).
* **#4093** was closed 2026-10-03 by #4420's keyword and reopened on
  2026-10-04, not "the same day".
* **#4097** gained an item on 2026-09-06: prove on a live cluster that
  the signing key is in no `sf-api` or `sf-queues`
  `/proc/<pid>/environ`.
* **#4459** carries `automated-fix-attempted` from filing, so no
  autofixer will race this phase for it.

### Corrections made at source

In this phase's planning commit, the master plan's phase 4 section,
its Situation citation for the mint endpoint, its #4093 date and its
Bugs entries for #4097 and #4367 were corrected for S1-S6 and S8. The
`index.md` row was updated. A later step does not need to redo any of
this.

## Decisions

### D1 -- a deploy profile, owned by the caller and applied by actions

Actions gains one optional input, `deploy_profile`, on `smoke-cluster.yml`
and `build-smoke-cluster`. Its default is empty, meaning no change. Its
value is a path relative to `GITHUB_WORKSPACE`, such as
`shakenfist/tools/ci-deploy-profiles/kerbside-slim-tier.yml.j2`, so
Kerbside's own lane can name a profile in either checkout in phase 5.

A profile is a Jinja2 template. Actions renders it against the facts
file, so it can name node addresses without hardcoding them. It renders
to YAML with these keys, all optional:

```yaml
groups:              # extra inventory groups, by topology host name
  kerbside:
    hosts: [sf2]
    vars:
      api_url: http://10.0.1.10:13000
extra_vars: {...}    # passed to site.yml as --extra-vars @file
mariadb_sql: |       # run once on the primary, with sudo mariadb, before the deploy
  CREATE DATABASE IF NOT EXISTS kerbside; ...
redeploy_check:      # run site.yml a second time; these units must not restart
  units: ['sf-*.service', 'kerbside-*.service']
test_env:            # exported into the functional test run on the primary
  SF_CI_EXPECT_VDI_CONSOLE_PROXY: '1'
```

A new actions script, `tools/ci-apply-deploy-profile.py`, does the
work:

* it renders the profile and validates it, refusing unknown keys and
  any host name not in the inventory;
* it merges groups and group vars into `/srv/github/ci-inventory.yaml`;
* it writes the extra-vars file and the test-env file;
* it prints the SQL for the action to pipe over ssh.

`deploy-collection.sh` gains an optional seventh argument: an extra-vars
file, appended as `--extra-vars @file` after the fixed string.

*Rejected: purpose-built inputs* (`kerbside_host`, `kerbside_url`, and
so on). Every Kerbside variable would then live in actions, which only
the operator can push. Phase 5 would need the same inputs again from
another repository. The profile keeps the lane's content beside the code
it tests, and actions knows nothing about Kerbside.

*Rejected: a bespoke job* modelled on `node_lifecycle_collection`. That
job calls `build-smoke-cluster` directly and edits the inventory. It
would get no headroom probe, which is exactly #4367's gap.

### D2 -- the profile renders against the facts, never against ansible

The extra variables are fully rendered strings by the time ansible sees
them. A profile never relies on lazy templating of `hostvars[...]`
inside a role, which would quietly break the rule that only `site.yml`
reads `groups` and `hostvars`. The step 1 brief names the facts file's
real field names; the example above is illustrative.

### D3 -- the redeploy check runs inside `build-smoke-cluster`, on `InvocationID`

When the profile asks for it, `build-smoke-cluster` does three things
straight after the first deploy:

1. records every matching unit's `InvocationID` on every inventory host;
2. runs `deploy-collection.sh` again with identical arguments;
3. compares the two records.

Any changed or vanished `InvocationID` fails the step, and the failure
names the host and the unit. The comparison is a new actions script,
`tools/ci-redeploy-check.sh`, with `snapshot` and `compare` modes.

* **Why `InvocationID`:** it changes on every start, including a start
  that systemd's own `Restart=` made, where `ActiveEnterTimestamp` can
  be ambiguous. #4459 asks for exactly this.
* **Why inside the action:** it must run before the JWKS and drain
  steps, which restart `sf-api` on purpose (S2), and before the tests,
  so that a second deploy which breaks something is caught.
* **Why only this row:** the second deploy costs a full `site.yml` run
  plus Kerbside's readiness wait (up to 180 seconds; phase 3 D8). The
  slim-tier row is the only one that has `kerbside-*` units to check.
  Its three nodes cover all three node roles, so `sf-*` restart-on-change
  on every node shape is covered too. #4459 is closed by this row
  alone.

### D4 -- Kerbside failures must be visible in CI

Actions' `tools/ci_node_checks.sh` gains `kerbside-*` beside `sf-*`, in
three checks:

* a failed unit fails the check;
* a journal error fails the check;
* a non-zero `NRestarts` on any `kerbside-*` unit fails the check.

The `NRestarts` check exists because `Restart=always` hides a crash
loop from the first two, and kerbside#313's exit-0-on-bad-config
defeats `Restart=on-failure` too. The clingwrap failure config collects
`kerbside-*` journals and `/etc/kerbside/kerbside.ini`, redacted as the
`sf` config is.

These are cheap glob changes. Without them, the first real failure of
`register` would leave nothing to debug.

### D5 -- the lane is the existing slim-tier merge row

The `debian-13-slim-tier` row gains `deploy_profile`, and the matrix
job passes `deploy_profile: ${{ matrix.merge.deploy_profile || '' }}`.
No new cluster, job, bundle name or `can_merge` entry is created (S3).

This is the decision a reviewer is most likely to argue with. It
changes what the slowest merge row deploys. That row then runs the
whole cluster suite with Kerbside scraping alongside it, and it pays
for the second deploy. The alternative is a separate job with a narrow
stestr config: an extra three-VM cluster for every merge group, plus
the three declarations S3 lists. The master plan's dependency on the
CI sizing plan says to add a group to an existing topology rather than
a new VM.

The full suite running beside a live Kerbside is also the more honest
test: it is how sfcbr will run. The feature-off path stays covered by
the three slim-primary rows and the PR smoke lane.

The headroom gate is untouched (S3). The seam test's comment on what a
shape is gains one sentence saying why `deploy_profile` is not part of
it, as it already says for `base_image`.

### D6 -- the profile: Kerbside on sf2, scraping the primary

`tools/ci-deploy-profiles/kerbside-slim-tier.yml.j2`:

* **Group:** `kerbside: [sf2]`. sf2 is a hypervisor outside the database
  tier, so Kerbside shares a host with instances but not with the first
  database host.
* **Group vars:** `api_url` set to the primary's mesh address on port
  13000, so the scrape crosses hosts (S4). The same value reaches sf2's
  `sfrc`, which is harmless and is how an operator would configure a
  Kerbside host anyway.
* **Extra vars:**
  * `kerbside_url` and `kerbside_public_fqdn`, both from sf2's mesh
    address: `http://<sf2>:13002` and the bare address;
  * `kerbside_system_key`, `kerbside_auth_secret_seed` and
    `kerbside_sql_url`, with throwaway CI values committed in the file,
    as actions' `ci*` defaults already are. The SQL URL points at the
    primary's mesh address;
  * `kerbside_token_duration` left at its default.
* **`mariadb_sql`:** the database and the `'kerbside'@'%'` user, as
  Kerbside's `deploy-kerbside.sh:93-99` does.
* **`redeploy_check`:** units `sf-*.service` and `kerbside-*.service`.
* **`test_env`:** `SF_CI_EXPECT_VDI_CONSOLE_PROXY: '1'`.

`kerbside_package` is not overridden: the merged profile uses the
role's default `kerbside>=0.7.0` from PyPI, which keeps the freeze
stable across the two deploys (S8). A unit test renders the profile
against a fixture shaped like the facts file. It checks that the result
satisfies the kerbside and node `argument_specs` constraints that can
be checked offline:

* the seed is at least 32 characters and not the sentinel;
* the key is at least 16 characters and differs from the CI
  `system_key`;
* `kerbside_url`'s host equals `kerbside_public_fqdn`;
* `api_url` is not loopback.

### D7 -- an address gets an IP SAN

In `site.yml`'s Kerbside certificate vars:

* `kerbside_public_fqdn` goes into `cert_san_ip` when it parses as an
  IPv4 or IPv6 address, and into `cert_san_dns` otherwise;
* the CN is unchanged;
* the test uses `ansible.utils.ipaddr` if the collection already
  depends on `ansible.utils`, and the `ipaddress`-equivalent regex
  idiom otherwise (the brief checks which).

`tools/ci-test-kerbside-role.sh` gains a case for each branch. This
fixes the certificate for every operator who addresses Kerbside by IP
(S7), and it lets the exchange test check the host name rather than
switching the check off.

### D8 -- the mint test runs when advertised, and fails when expected

In `test_vdi_tokens.py`:

* **Capability first:** the test reads
  `check_capability('vdi-console-proxy')` before minting.
* **Not advertised:** it skips, unless `SF_CI_EXPECT_VDI_CONSOLE_PROXY`
  is set, in which case it fails.
* **Advertised:** a 404 or 500 from the mint fails rather than skips.
* **Client calls:** it uses `get_vdi_console_proxy` and
  `get_vdi_token_public_keys` instead of `_request_url`, and its stale
  docstring is fixed.

The expectation variable turns "the deploy silently stopped rendering
`KERBSIDE_URL`" from a green skip into a red failure. Without it, every
assertion this phase adds could vanish together.

### D9 -- the exchange test

A new `cluster_ci_tests/test_vdi_kerbside_exchange.py` skips under
D8's rule. It creates the same diskless SPICE instance
`test_vdi_tokens.py` uses, then:

1. **Fetches the `.vv` through Kerbside.** It mints with
   `get_vdi_console_proxy` and GETs the URL with `requests`. A 404
   `console not found` is retried with a fresh mint for up to 150
   seconds, which is two and a half scrapes. Any other status fails
   at once.
2. **Checks the `.vv` names Kerbside, not the hypervisor.** It compares
   it with the direct `.vv` from the instance's own `vdiconsolefile`
   endpoint, using `test_vdi_console_file.py`'s parser:
   * the `(host, tls-port)` pair differs;
   * `tls-port` and `port` lie outside SF's 30000-50000 console range;
   * `host` equals the host of the mint URL.
3. **Checks the proxy's TLS endpoint.** It unescapes `ca`, opens a TLS
   connection to `host:tls-port` with that CA as the only trust anchor
   and host-name checking on (D7), and completes the handshake. It then
   renders the peer certificate's subject in `node.py`'s format and
   asserts it equals `host-subject`.
4. **Checks a replay is refused.** It GETs the same URL again and
   expects 401.

The subject renderer is a small function in a new
`shakenfist_ci/spice_subject.py`. `shakenfist/tests/test_kerbside_host_subject.py`,
which already pins the role's script to
`node._spice_host_subject_from_cert`, loads it by path and pins it too.
That makes three renderers, all held to one reference by one test.
Importing `shakenfist.node` is not an option, because `shakenfist_ci`
does not depend on `shakenfist` (S8).

### D10 -- #4097's environment check rides along

The same module gains `test_signing_key_absent_from_daemon_environments`.
The key's private PEM lives in the `cluster_config` row
`KERBSIDE_JWT_SIGNING_KEY` (`shakenfist/util/vdi_tokens.py:55-57`). #4099
keeps it out of daemon environments, and #4097's 2026-09-06 comment
points out that only mocked fixtures check that.

On every node, through `_node_exec`, the test counts matches in each
running `sf-*` unit's main PID's `/proc/<pid>/environ`, with `grep -c`
so nothing matched is ever printed, for two patterns:

* the row name `KERBSIDE_JWT_SIGNING_KEY`, which is the check #4097
  proposes;
* the PEM marker `PRIVATE KEY`, which catches the key exported under any
  other name.

Every count must be zero. The test first asserts that the key exists,
using `get_vdi_token_public_keys`, so that a cluster with no key cannot
pass vacuously.

It is one test, it only means something on a cluster with the feature
on, and this is the only such lane. Leaving it for later would mean
another lane later.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | worktree in actions | Deploy profiles: input plumbing, `ci-apply-deploy-profile.py` and its tests, `deploy-collection.sh`'s extra-vars file, the facts/inventory path for profile groups. See brief 1. Commit (actions): "Let a caller supply a deploy profile." |
| 2 | high | opus | same actions worktree | The redeploy check (`ci-redeploy-check.sh`, wired after the first deploy) and `test_env` export into the test run. See brief 2. Commit (actions): "Deploy twice and require no restarts." |
| 3 | medium | sonnet | same actions worktree | `kerbside-*` in node checks and the failure log bundle; fix the stale "slim-tier is two nodes" comment. See brief 3. Commit (actions): "Check and collect Kerbside units in CI." |
| 4 | medium | sonnet | none | The IP SAN in `site.yml`, plus two role-test cases. See brief 4. Commit: "Give an addressed Kerbside an IP SAN." |
| 5 | high | opus | none | The profile, its unit test, the slim-tier row and the seam-test comment. See brief 5. Commit: "Deploy Kerbside in the slim-tier merge row." |
| 6 | medium | sonnet | none | `test_vdi_tokens.py`: capability-gated, expectation-aware, client methods. See brief 6. Commit: "Run the VDI mint test where Kerbside is on." |
| 7 | high | opus | none | The exchange test, `spice_subject.py`, its pin, and D10's environment test. See brief 7. Commit: "Exchange a console token through Kerbside in CI." |
| 8 | medium | sonnet | none | The developer guide CI page, collection README caveat, release note. See brief 8. Commit: "Document the Kerbside merge lane." |

Steps 1-3 run in a new actions worktree, `../actions-wt-kd-p04`, on
branch `kerbside-deployer-phase-04-deploy-profiles`, created from
`origin/main`. Only the operator pushes there (master plan). Steps 4-8
run in this worktree.

The steps run one sub-agent at a time. The two repositories could run
in parallel, but step 5's profile is written against step 1's schema.
The management session runs each repository's full check, and reviews
each step before committing it.

The **trial** after step 7 is a management-session task, not a step:

1. The operator pushes the actions branch.
2. The management session pushes this branch with one extra commit,
   "TRIAL: do not merge". That commit points the matrix's `uses:` at
   the actions branch and, if 0.7.0 is not yet on PyPI, sets
   `kerbside_package` in the profile to a pinned Kerbside develop
   commit.
3. It dispatches `functional-tests.yml` on the branch.
4. When the trial passes, it drops the commit before the PR is
   opened.

The trial run's wall-clock times for the slim-tier row are recorded in
the PR description: the first deploy, the second deploy and the suite.

### Brief 1 -- deploy profiles in actions

Work in `../actions-wt-kd-p04` (create it from `origin/main`). Read D1
and D2 of this plan first. Files:

* `.github/workflows/smoke-cluster.yml`: add input `deploy_profile`
  (string, default `''`), with a description pointing at the schema
  doc. Pass it to `build-smoke-cluster`.
* `build-smoke-cluster/action.yml`: the same input. After the inventory
  is generated (`ci-make-inventory.py`) and before
  `deploy-collection.sh` (`:167-209`), when the input is non-empty:
  1. run `tools/ci-apply-deploy-profile.py`;
  2. pipe its SQL to `ssh -i /srv/github/id_ci <user>@<primary>
     sudo mariadb`;
  3. pass its extra-vars file as `deploy-collection.sh`'s new seventh
     argument.

  Reuse the primary-address logic at `:194-201`.
* `tools/ci-apply-deploy-profile.py`: render the profile with Jinja2
  (`StrictUndefined`) against the facts file the topology playbook
  writes (find its path and field names, and document them in the
  script's docstring). It must:
  * refuse unknown top-level keys and unknown host names;
  * merge `groups` into the inventory YAML (hosts by name, vars as
    group vars) without disturbing existing groups;
  * write extra vars and test env to files beside the inventory,
    mode 0600, because they hold secrets;
  * print the SQL.

  The script must run in whichever Python the action already uses for
  `ci-make-inventory.py`; check that Jinja2 and PyYAML are available
  there.
* `tools/deploy-collection.sh`: optional `$7`. When it is non-empty,
  append `--extra-vars "@$7"` after the existing string.
* Tests, beside `tests/test_ci_make_inventory.py`: render, merge,
  unknown key, unknown host, StrictUndefined failure, and an empty
  profile.
* A short schema doc in `docs/` (or the README section that documents
  `smoke-cluster.yml`'s inputs, whichever the repository uses).

Constraints:

* With `deploy_profile` empty, no rendered command may differ from
  today's. Diff the rendered `deploy-collection.sh` command line in a
  test if practical.
* Secrets never go on argv or into logs: no `set -x` around the
  extra-vars path, and the SQL is piped on stdin, never passed with
  `-e`.
* Single quotes in Python and 120-character lines; scripts go in
  `tools/`, not inline in workflow YAML.
* Run the repository's own checks (look for `.pre-commit-config.yaml`
  or `tox.ini`).

### Brief 2 -- the redeploy check and test env

Same worktree. Read D3.

* **`tools/ci-redeploy-check.sh snapshot|compare <inventory> <state-file> <unit-glob>...`.**
  `snapshot` sshes to every inventory host and records `<host> <unit>
  <InvocationID>` for each loaded unit matching the globs. Use
  `systemctl list-units --all --plain --no-legend` plus `systemctl show
  -p InvocationID --value`. `compare` re-reads and prints every
  changed, vanished or new unit, then exits non-zero if any changed or
  vanished.

  Use the ssh options and user that other actions tools use; find them
  rather than inventing them.
* **`build-smoke-cluster`.** When the rendered profile has
  `redeploy_check`, it runs, in order: `snapshot`, `deploy-collection.sh`
  again with identical arguments, `compare`. Do this inside the same
  step as the first deploy or straight after it, within the 90-minute
  timeout. Log each deploy's elapsed seconds.
* **`test_env`.** `smoke-cluster.yml`'s functional test step
  (`:369-398`) runs the suite on the primary over ssh. Export the
  profile's `test_env` file's variables into that environment. Copy
  the file to the primary and source it in the remote command, or pass
  `KEY=value` on the remote command line; values are not secrets. Do
  nothing when there is no profile.
* **Tests:** `compare` logic against fixture state files, covering
  changed, vanished, new and identical units.

### Brief 3 -- Kerbside visibility

Same worktree. Read D4.

* **`tools/ci_node_checks.sh`** (`:45-50, 81-95`): extend the
  failed-unit and journal-error checks to `kerbside-*.service`. Add a
  check that any loaded `kerbside-*` unit has `NRestarts=0`, and fail
  with the unit name and count. A host with no Kerbside units must
  pass.
* **`ansible/files/shakenfist-ci-failure-loki.cwd`** (`:24-25`): collect
  `kerbside-*` journals and `/etc/kerbside/kerbside.ini`. Redact the
  latter the way the `sf` config is redacted, or omit it if there is
  no redaction mechanism; say which.
* **`smoke-cluster.yml:148-152`:** the comment says slim-tier is two
  nodes. It is three (`ci-topology-slim-tier.yml`).

### Brief 4 -- the IP SAN

This worktree. Read D7.

* **`examples/_shared/site.yml:829-850`.** The `&kerbside_certificate_vars`
  anchor gains `cert_san_ip`, holding `kerbside_public_fqdn` when it is
  an IPv4 or IPv6 address. `cert_san_dns` holds it otherwise.
  * Check `shakenfist/deploy/collection/galaxy.yml` and
    `requirements.yml` for `ansible.utils`. Use
    `ansible.utils.ipaddr` only if it is already a dependency.
    Otherwise use a regex test, written so that `10.0.1.12`, `::1`
    and `fe80::1` are addresses and `kerbside.example.com` is not.
  * `cert_san_ip` must be `[]` (not undefined) for a name, and so must
    `cert_san_dns` for an address. Read
    `roles/internal_ca/vars/main.yml:12` to see how they are consumed.
* **`tools/ci-test-kerbside-role.sh`:** add two cases that issue the
  certificate through the same vars and inspect the issued certificate
  with `certtool -i` or `openssl x509 -text`. With an address, the IP
  SAN is present and there is no DNS SAN; with a name, the reverse.
  Mutation-test both cases by swapping the condition, and say so.
* **`docs/operator_guide/installation.md`:** one sentence where
  `kerbside_public_fqdn` is documented.

### Brief 5 -- the profile and the row

This worktree. Read D1, D2, D5 and D6, and the schema step 1 produced
(the management session will paste its final docstring into this
brief).

* **`tools/ci-deploy-profiles/kerbside-slim-tier.yml.j2`**, per D6.
  Throwaway secrets must satisfy:
  * `roles/kerbside/meta/argument_specs.yml` (`:350-358`, plus the
    seed's length and sentinel rules in `tasks/validate.yml`);
  * `roles/node/meta/argument_specs.yml:232-245` (`kerbside_system_key`
    at least 16 characters, and not equal to actions' default CI
    `system_key`; find it in `build-smoke-cluster/action.yml:33-41`).

  The comment at the top says the values are CI-only.
* **`shakenfist/tests/test_ci_deploy_profiles.py`.** It renders every
  `tools/ci-deploy-profiles/*.j2` against a fixture facts file shaped
  like actions' real one, and asserts D6's offline checks. Mutate each
  check once, and record the count.
* **`.github/workflows/functional-tests.yml`:**
  * the slim-tier row (`:547-554`) gains
    `deploy_profile: 'shakenfist/tools/ci-deploy-profiles/kerbside-slim-tier.yml.j2'`;
  * the job's `with:` gains
    `deploy_profile: ${{ matrix.merge.deploy_profile || '' }}`;
  * the comment block on the row says what the profile adds and points
    at this plan.
* **`shakenfist/tests/test_headroom_gate_workflow_seams.py:40-53`:**
  one sentence saying `deploy_profile` is deliberately not part of a
  shape (S3). Confirm the seam tests still pass, and that
  `test_ci_structural_minimum.py` is unaffected.
* **`.pre-commit-config.yaml`:** run the new unit test as part of the
  existing stestr hook. Check whether it is picked up automatically
  first.

### Brief 6 -- the mint test

This worktree. Read D8.
`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_vdi_tokens.py`:

* Add a module helper, `kerbside_expected()`, reading
  `SF_CI_EXPECT_VDI_CONSOLE_PROXY` (`'1'` means expected). Put it in
  `shakenfist_ci/base.py` beside the other `SF_CI_*` reads (`:122-134`),
  so step 7 reuses it.
* `test_mint_and_verify_console_token` (`:84-172`):
  * check the capability first;
  * if it is not advertised, skip, or fail when it is expected;
  * if it is advertised, let 404 and 500 fail with a message naming the
    status;
  * switch to `get_vdi_console_proxy` and `get_vdi_token_public_keys`.
    Check that the client version the CI installs has them: the deploy
    builds the client from client-python's develop checkout
    (`client_repo_path`).
* `test_capability_advertisement_matches_configuration` (`:52-82`) also
  fails when expected but not advertised.
* Fix the module docstring (`:21-24`).
* Run flake8 on the file. The test cannot be run locally; say so.

### Brief 7 -- the exchange test

This worktree. Read D9 and D10, and S6 for the exchange's exact
behaviour (with Kerbside citations). New
`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_vdi_kerbside_exchange.py`.

* **Instance:** reuse `test_vdi_tokens.py`'s `_create_spice_instance`,
  moving it to a shared mixin or base helper rather than copying it.
  Wait for state `created`.
* **Retry and replay:** use the helpers in `shakenfist_ci/retries.py`,
  not a hand-rolled sleep loop. A unit test in
  `test_database_tier_harness.py` (`:278-339`) forbids loops in `base.py`
  that both sleep and read; read it and stay on the right side of it.
* **`.vv` parsing:** use `test_vdi_console_file.py`'s parser (`:29-50`),
  moved to a shared helper if it is a method.
* **TLS:**
  * build an `ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)`;
  * load the unescaped CA with `load_verify_locations(cadata=...)`;
  * keep `check_hostname = True`;
  * wrap a socket to `(host, int(tls_port))` with `server_hostname=host`,
    using a 10-second timeout;
  * take `getpeercert(binary_form=True)`, render it and close.
* **`shakenfist_ci/spice_subject.py`:** a function that takes DER bytes
  and returns the subject string in
  `shakenfist/node.py:_spice_host_subject_from_cert`'s format. Use
  `cryptography`; check it is in `shakenfist/deploy/requirements.txt`
  or the CI venv. Extend `shakenfist/tests/test_kerbside_host_subject.py`
  to load it by path and pin it against `node.py` over the same
  subjects the role script is pinned with. Mutate the escaping order
  once and confirm the test fails.
* **D10:**
  * find how `_node_exec` (`base.py:773-806`) runs a command, and how
    tests get node IPs (`base.py:700-738`);
  * enumerate the `sf-*` units with `systemctl show -p MainPID --value`,
    skipping units whose MainPID is 0;
  * read environ as root (`sudo`) and use `grep -c` or `grep -q`, so the
    test log never carries environ content;
  * mutate the test once by having it look for a variable that is
    present, such as `PATH`, and confirm it fails.
* **Registration:** the module is picked up by `cluster-ci.conf`'s test
  path. Confirm that, and that `smoke-ci.conf` does not pick it up
  (it would only skip there).
* flake8 must be clean. The test cannot run locally; the trial run is
  its first run.

### Brief 8 -- documentation

This worktree, sonnet, medium.

* **`docs/developer_guide/ci.md`:** a section on deploy profiles. Say
  what a profile can do, where the Kerbside one lives, that the slim-tier
  merge row uses it, and what the redeploy check asserts. Link actions'
  schema doc.
* **The collection `README.md`'s Kerbside section and
  `docs/release_notes/v07-v08.md`:** replace "its cluster test arrives
  with the merge-queue lane" (and any "register has never run" wording)
  with a statement of what the slim-tier merge row now proves. Phase
  3's wording is the thing to find: grep for "merge-queue" and
  "never run".
* **`docs/operator_guide/vdi_console_tokens.md`:** nothing. Phase 6
  rewrites it.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| An actions change breaks every consumer, since all of them use `@main` unpinned. | The input defaults to off, and brief 1 requires the off path to render identical commands. The operator pushes a branch, and the trial run uses it before anything merges to actions `main`. |
| The second deploy and Kerbside's bootstrap (a `mysqlclient` source build) push the slim-tier row past its timeouts. | The trial records all three timings. If the row nears its 90-minute action or 60-minute test budget, move `redeploy_check` to the profile-free slim-primary row by giving that row a profile with only `redeploy_check`. That loses the `kerbside-*` restart check, so the operator decides. |
| Kerbside's 60-second scrape trips `test_no_unbudgeted_fixed_rate_database_polling` on slim-tier, which already runs near its ceiling (#4450, #4464). | Kerbside's calls arrive as the `api` caller at about one per minute per operation, roughly 0.017/s against a 0.25/s ceiling. The trial's load-budget output is read for any new pair; a real one is budgeted in `database_load_budget.yaml`, not suppressed. |
| `register`'s first real run fails in a way the rootless test could not see. | That is what this phase is for. D4 makes the failure debuggable, and the trial runs before review. Fixes land in the role, in this phase, with a role-test case where one is possible. |
| Kerbside 0.7.0 is not released when the PR is ready. | The PR cannot pass the merge queue without it, by design (phase 3 D4). The trial uses a pinned develop commit, and the back brief gates the enqueue on the release. |
| The mint, exchange and environment tests silently skip on the Kerbside row. | D8's expectation variable fails them instead. The management session confirms in the trial's subunit output that they ran rather than skipped. |
| A changing Kerbside wheel path restarts Kerbside on the second deploy. | The merged profile installs from PyPI (S8). The trial's develop pin uses a fixed commit for both deploys. |

## Definition of done

* With the profile input empty, actions' tests show that
  `deploy-collection.sh`'s rendered command is unchanged.
  `tools/ci-apply-deploy-profile.py`'s tests fail when an unknown key
  or host is accepted (mutated once each).
* `tools/ci-redeploy-check.sh compare` exits non-zero on a changed or
  vanished `InvocationID` fixture and zero on an identical one.
* The trial run's slim-tier log shows each of these:
  * two `site.yml` runs;
  * a redeploy check naming at least six `sf-*` units and two
    `kerbside-*` units, all unchanged;
  * Kerbside's readiness wait succeeding on both deploys.
* In the trial's subunit stream, these tests passed on the slim-tier row
  and were not skipped:
  * `test_mint_and_verify_console_token`;
  * `test_capability_advertisement_matches_configuration`;
  * the three tests in `test_vdi_kerbside_exchange.py`.

  On the slim-primary rows, the mint and exchange tests skipped.
* `python3 -m stestr run test_kerbside_host_subject` passes, and fails
  when `spice_subject.py`'s escaping order is swapped.
* `python3 -m stestr run test_ci_deploy_profiles` passes, and each
  offline check fails when its constraint is broken in the rendered
  profile.
* `tools/ci-test-kerbside-role.sh` passes, including the two SAN cases,
  and each fails when the address test is inverted.
* `grep -n deploy_profile .github/workflows/functional-tests.yml` shows
  only the slim-tier row and the matrix `with:`.
  `can_merge.needs` and `ci_headroom_harvest.py` are unchanged.
* The merge queue passes with the trial commit dropped and Kerbside
  0.7.0 installed from PyPI. The PR carries `Fixes #4093` and
  `Fixes #4459`; #4097 gets a comment closing items 4 and the
  environment check.
* `pre-commit run --all-files` passes here, and actions' own checks
  pass there.
* The master plan's phase 4 row reads `Complete` with its `Merged`
  cell filled. Phase 5's first commit does that, per
  `plan-phase-landing`. The actions change is recorded as
  `actions <sha> (#pr)` in the same cell.

## Back brief

Before step 1, the management session confirms its understanding of the
plan with the operator. Four points need the operator's hand, not just
agreement:

1. **The lane is the existing slim-tier row (D5),** not a new cluster.
2. **The actions branch** is pushed by the operator after steps 1-3 are
   reviewed, and merged to actions `main` before this repository's PR is
   enqueued, because the PR passes an input that `main` must declare
   (S8).
3. **Kerbside 0.7.0** must be on PyPI before the PR is enqueued (S5).
   Cutting it is the operator's tag and environment approval.
4. **kerbside#482** is stale and could be closed by hand (S5). This
   phase does not touch the Kerbside repository.

## Future work

* **Phase 5 can reuse deploy profiles.** Kerbside's lane can call
  `smoke-cluster.yml` with a profile in its own checkout that sets
  `kerbside_package` to the PR's wheel. D1's path rule makes that
  possible. Whether its freeze stays stable across a redeploy, for a
  wheel at a fixed path, is phase 5's question.
* **A redeploy check on every merge row,** if the slim-tier row's cost
  proves small. That is a one-line profile per row.
* **Kerbside on a dedicated host outside `allsf`.** Actions' facts file
  enumerates `allsf` only (S1), so the profile cannot place one. The
  role supports it (phase 3), and CI does not test it.
* **Re-measure the slim-tier headroom band** once a few merge runs carry
  Kerbside, to confirm S3's reasoning in numbers rather than by
  argument.
