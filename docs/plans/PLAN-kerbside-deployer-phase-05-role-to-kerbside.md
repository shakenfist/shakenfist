# Phase 5 -- The `kerbside` role moves into Kerbside

Part of [PLAN-kerbside-deployer.md](PLAN-kerbside-deployer.md). Phase 4
([PLAN-kerbside-deployer-phase-04-ci.md](PLAN-kerbside-deployer-phase-04-ci.md))
landed in #4479 as `0016c5dad`; this phase's first commit closes it out.

**Planning effort:** high, as the master plan asks. The phase crosses
three repositories (Kerbside, actions, Shaken Fist) and a release
boundary. It changes the role's interface, and it moves a CI lane that
is the only cross-repository check of the console path.
**Review effort:** high for steps 1-2, which generalise the role, and
for step 5, which changes what `site.yml` passes it. Medium elsewhere.

## Scope

**In scope:**

* The `kerbside` role, its systemd units and its helper scripts move
  into the Kerbside repository. They become the `shakenfist.kerbside`
  collection, published to Ansible Galaxy from Kerbside's release
  workflow (D1).
* The role loses every Shaken Fist assumption. Its single hard-coded
  source becomes a required `kerbside_sources` list, validated per
  source type. Readiness, port checks and the code hash stop assuming
  Shaken Fist (D3, D4).
* Shaken Fist builds its own source entry, and makes its own checks,
  in a new node role entry point (D3). `site.yml` consumes the external
  role as a soft dependency, pinned by a requirements file it also
  reads (D2).
* A `collections` key in actions' deploy profiles, so a CI deploy can
  install collections beside Shaken Fist's (D5).
* The tests split along the same line (D6).
* Kerbside's sf-e2e lane deploys through the role on a Shaken Fist
  smoke cluster. It stops using `deploy-kerbside-on-shakenfist` and
  its own provisioning scripts (D8).
* Docs only as far as needed to keep every page true: the collection
  READMEs on both sides, and pointers from the pages that describe
  the role today (D9).

**Out of scope, and why:**

* **A non-Shaken Fist source in CI.** The list accepts any type
  Kerbside supports, and the role's unit tests render and validate
  each one. A live second source is Kerbside's deployment options
  plan's business.
* **The full Ansible documentation page.** Kerbside's
  `PLAN-deployment-options.md` phase 5 owns it and is blocked on this
  phase. This phase writes only the collection's README.
* **Publishing `shakenfist.shakenfist` to Galaxy.** It has never been
  published (S3), and that is a release-process fix in this
  repository, not part of the move. It is filed (see Future work).
* **Moving Kerbside's lane onto `smoke-cluster.yml`.** It cannot
  carry Kerbside's drivers (D8). #4367 stays open.
* **Closing kerbside#579**, the nightly proxy-wheel build failure. It
  is Kerbside's, and is confirmed before the lane moves so the move is
  not blamed for it.
* **#4097 item 6**, a shared host-subject fixture read by both
  repositories. Step 1 lays the Kerbside half (D6). The general fix
  stays with that issue.

## What the survey found

Three surveys ran in parallel:

* **Shaken Fist:** the role and every reference to it.
* **Kerbside and actions:** the sf-e2e lane, packaging, the sources
  model and the deployment options plan.
* **ansible-galaxy experiments:** run on ansible-core 2.15.13, 2.18.19
  and 2.21.5, with the same results on all three. The scratch tree is
  under the session scratchpad and is not kept.

Paths are relative to each repository's root. R is
`shakenfist/deploy/collection/roles/kerbside`.

### S1 -- the role is mechanically self-contained, but Shaken Fist-shaped in logic

**Mechanically self-contained.** The role is 1983 lines in 19 files.
* `meta/main.yml:12` declares `dependencies: []`.
* There is no `include_role`, plugin, `module_utils`, `role_path` or
  `playbook_dir` use.
* Every module and filter is `ansible.builtin`.
* There is no `groups[`, `hostvars` or `ansible_facts`. The master
  plan's "reads no inventory group names and no other host's facts" is
  literally true.

**Shaken Fist-shaped in at least seven places beyond the variables:**

1. `templates/sources.yaml:13-20` builds exactly one
   `type: 'shakenfist'` entry from `deploy_name`, `api_url`,
   `kerbside_system_key` and `kerbside_sf_ca_cert`.
2. `files/kerbside-check-sources.py:29,52-60` requires the Shaken
   Fist source's six keys, as non-empty strings, on *every* entry.
   Kerbside itself requires only `source` and `type`
   (`kerbside/main.py:67`).
3. `files/kerbside-ready.py:84-90` and `tasks/register.yml:204-250`
   define readiness as "the source named `deploy_name` has fetched its
   signing keys".
4. `tasks/config.yml:25-33` asserts, unconditionally, that
   `kerbside_sf_ca_cert` is a PEM.
5. `tasks/bootstrap.yml:148` hashes `site-packages/shakenfist_client`
   under errexit, so bootstrap fails if the client is not installed.
6. `tasks/validate.yml` holds three Shaken Fist checks:
   * the port check against Shaken Fist's listeners when
     `kerbside_colocated` is set (`:97-111`, list at
     `vars/main.yml:31-36`);
   * the loopback `api_url` check (`:159-177`);
   * the `kerbside_url == kerbside_url_on_sf_nodes` check (`:185-196`).
7. The flat ceiling of 30000 on every port (`validate.yml:73-89`) is
   Shaken Fist's instance console range.

**Variables.** Seven of the role's options are Shaken Fist inputs:
* `kerbside_system_key`
* `kerbside_sf_ca_cert`
* `kerbside_url_on_sf_nodes`
* `kerbside_colocated`
* `api_url`
* `deploy_name`
* `kerbside_client_package`

**Coupling to `internal_ca`.** The role is implicitly coupled to
Shaken Fist's `internal_ca` role:
* `vars/main.yml:11-19` hard-codes internal_ca's certificate file
  names;
* `site.yml:829-858` issues the Kerbside certificate with internal_ca
  between the role's `bootstrap` and `config` entry points.

**No released consumer.** No Shaken Fist tag contains the role: the
newest tag is `v0.8.0-rc4`, which predates #4465. Its interface can
therefore change in the move without compatibility shims.

### S2 -- `site.yml` already reaches the role only through `include_role`

Every use of the role is an `include_role` with `tasks_from`:
* `validate` in the validate play (`examples/_shared/site.yml:160-173`);
* `bootstrap`, `config` and `register` in "Deploy Kerbside"
  (`:796-868`).

That is the only shape that survives the collection being absent. With
no collection installed and an empty `kerbside` group:
* `include_role` behind a false `when`, or in a play that matches no
  host, runs cleanly, and `--syntax-check` passes;
* `import_role`, a play-level `roles:` list and `import_playbook` all
  fail at parse time, before any play runs, whatever the `when`.

When an `include_role` of a missing role actually executes, it fails
with "The role 'X' was not found". That message gives an operator no
hint about what to install.

### S3 -- Galaxy

* **`shakenfist.shakenfist` is not on Galaxy, in any repository
  state.** `release.yml:202-235` publishes it only on `v*` tags, and no
  tag has been pushed since that job was added (a3357605d1,
  2026-06-26). Every operator installs it from a checkout or from the
  server wheel (`pyproject.toml:210-214` ships `deploy/**`).
* **The `shakenfist` namespace already exists**, owned by
  `mikalstill` and `shakenfist-bot`. It publishes one collection,
  `shakenfist.k3s`, from `client-python-k3s`'s `release.yml` with an
  `ANSIBLE_GALAXY_TOKEN` secret. That job is the pattern to copy.
* **No collection named `kerbside` exists in any namespace.**
* **`shakenfist.kerbside` with a role `kerbside` builds, installs,
  runs, and passes `galaxy-importer` 0.4.44.** The importer insists on
  `requires_ansible` in `meta/runtime.yml`.

### S4 -- how a collection can depend on another

* **Galaxy refuses a git URL in `galaxy.yml` `dependencies`.**
  `ansible-galaxy` builds such a collection and installs its
  dependency happily, but `galaxy-importer` rejects the upload:
  "Invalid dependency format". A Galaxy dependency must be a
  `namespace.name`.
* **`ansible-galaxy collection install -r requirements.yml` accepts
  git (tag, branch or SHA), `dir` and tarball sources.**
  * A git source may omit the subdirectory: ansible-galaxy scans one
    level down for `galaxy.yml`.
  * A `dir` path resolves against the **current directory**, not the
    requirements file.
  * A `dir` source installs the working tree, uncommitted edits
    included.
* **An installed copy satisfies a name dependency without contacting
  Galaxy.** With a git source and a dependent in one command, the git
  source must come first, or resolution fails. That matters only if
  something declares the dependency (D2 makes sure nothing does).
* **`--no-deps` and `--offline` exist from 2.15 on.**
* **Precedence when a collection is installed twice:**
  1. a `collections/` directory beside the playbook;
  2. `ANSIBLE_COLLECTIONS_PATH`, first entry winning;
  3. the default path;
  4. site-packages.

  `ansible-galaxy collection list` does not print them in precedence
  order.

### S5 -- CI installs only Shaken Fist's collection

* actions' `tools/deploy-collection.sh:52-64`:
  * builds Shaken Fist's tarball from the `shakenfist` checkout;
  * installs only that tarball;
  * runs `site.yml`, adding the profile's extra vars file as an
    optional seventh argument.

  No input or profile key installs another collection.
* `tools/ci-apply-deploy-profile.py` accepts exactly five top-level
  keys (`TOP_LEVEL_KEYS`, `:108`) and refuses any other:
  * `groups`
  * `extra_vars`
  * `mariadb_sql`
  * `redeploy_check`
  * `test_env`

  The profile template's context includes `workspace`.
* The profile path is resolved against `$GITHUB_WORKSPACE` when
  `build-smoke-cluster` runs (`:298-304`).
* Kerbside's lane checks Kerbside out *after* `build-smoke-cluster`
  (`sf-e2e-functional.yml:101-113`). A profile in the Kerbside
  repository would not exist yet.
* Called from Kerbside, the Shaken Fist checkout is always develop.
  No input chooses another ref.

### S6 -- Kerbside's lane today

`sf-e2e-functional.yml` runs on pull requests, nightly at 02:30 UTC and
on dispatch. In the merge queue it reports, but skips the work.

The `sf-e2e` job:

1. Calls `build-smoke-cluster` with `topology: localhost` and no
   profile.
2. Checks out Kerbside and builds the proxy wheel.
3. Calls `deploy-kerbside-on-shakenfist`, which copies the checkout
   and wheel to the primary and runs:
   * `tools/sf-e2e/provision-sf.sh`: `sf-ctl set-config KERBSIDE_URL`
     and `KERBSIDE_TOKEN_DURATION`, `ensure-kerbside-signing-key`, and
     a restart of sf-api;
   * `tools/sf-e2e/deploy-kerbside.sh`:
     * creates the database;
     * builds ryll from git;
     * makes a venv at `/tmp/sf-e2e/venv` with Kerbside installed from
       source and the client unpinned;
     * generates TLS;
     * runs `gen-sources.py`, which writes one `type: shakenfist`
       source using the *system* key;
     * starts Kerbside as plain processes;
     * writes `/tmp/sf-e2e/kerbside.env`.
4. Runs the drivers (`drive-happy-path.py`, `drive-adversarial.py`)
   with the venv's Python. The drivers import `kerbside.db` directly
   and read paths, the seed, the SQL URL and the sources path from
   `kerbside.env`.

`deploy-kerbside-on-shakenfist` (actions) has no logic of its own.

**Run history.**
* kerbside#482 (the stale nightly failure) was closed on 2026-10-17.
* kerbside#579 is open: the 2026-10-18 nightly failed building the
  proxy wheel.
* Pull request runs on 2026-10-17 passed in about 39 minutes.

### S7 -- Kerbside's packaging

* **Versioning:** setuptools_scm with `v*` tags.
* **Release:** `release.yml` runs on a tag push. It publishes to PyPI
  by trusted publishing and creates a GitHub release.
* **kerbside-proxy** is stamped to an exact lockstep pin at release
  (`tools/stamp-proxy-version.sh`).
* **Ansible content:** none tracked anywhere in the repository; no
  `galaxy.yml`, `roles/` or `.service` file.
* **Python style:** 80-character lines and single quotes
  (`AGENTS.md:217-219`).
* **`shakenfist_client`** is not a runtime dependency. The Shaken Fist
  source driver loads it lazily through `importlib`, so the role keeps
  installing it.
* **systemd units:** the deployment options plan's open question 3
  recommends that Kerbside ship units in `etc/systemd/` in its phase 2,
  starting from this role's units. The role's units are already
  `WantedBy=multi-user.target` (R/templates/*.service), so nothing
  about them is Shaken Fist-specific except comments.

### S8 -- Kerbside's sources model

* **Loading** (`kerbside/main.py:72-151`):
  * the only required keys are `source` and `type`;
  * seven optional keys are normalised to strings: `url`, `username`,
    `password`, `project_name`, `user_domain_id`, `project_domain_id`
    and `ca_cert`;
  * any other key reaches the driver.
* **Types** (`:255-268`): `shakenfist`, `ovirt` and `static` are
  scraped. `openstack` is accepted but not scraped. Any other type
  errors that source.
* **Per-type fields:** `docs/console-sources.md` lists them.
  * A Shaken Fist source needs `url`, `username` (which must be
    `system`), `password` and `ca_cert`.
  * A static source needs a `consoles` list.
* **Several sources per type are supported.**
* **One token audience per Kerbside.** `expected_audience()`
  (`sf_token.py:67-75`) returns a single value. Every Shaken Fist
  cluster behind one Kerbside must set the same `KERBSIDE_URL`.

### S9 -- the Shaken Fist tests that reach into the role

* **`shakenfist/tests/test_kerbside_host_subject.py`** pins the role's
  `files/kerbside-host-subject.py`, and `shakenfist_ci/spice_subject.py`,
  to `node.py`'s short names, renderings and refusals (`:147-178`). It
  also requires every role default to be declared in
  `argument_specs['main']` (`:217-227`).
* **`shakenfist/tests/test_ci_deploy_profiles.py` reads no role
  file.** It hard-codes copies of the role's constraints (`:86-99`)
  and a copy of `validate.yml`'s loopback regexes (`:136-149`). The
  master plan's claim that it checks profiles against the role's
  argument specs is false.
* **`tools/ci-test-kerbside-role.sh`** has 1328 lines and 21 cases,
  in two kinds:
  * **role-only:** `validate`, `config` and `bootstrap` through a
    symlinked collection, the sources rendering (case 10) and secrets;
  * **Shaken Fist wiring:**
    * `site.yml`'s play order and include lists (case 18);
    * internal_ca's file names compared with the role's vars;
    * no task setting an undeclared role argument;
    * internal_ca issuance with the Deploy-play vars (cases 19-20).
* **Hooks and CI:** the pre-commit hook `test-kerbside-role`
  (`.pre-commit-config.yaml:71-77`), ansible-lint over the collection
  (`:44-50`), and the sanity job (`functional-tests.yml:241-280`) all
  run these.

### S10 -- smaller facts the briefs depend on

* **ansible-core is pinned nowhere.** CI and release install the
  latest release (2.21.x, which needs Python 3.12 or newer). The docs
  give a floor of 2.15, and `meta/runtime.yml` says `>=2.15.0`.
* **Shaken Fist's `renovate.json` exists.** Renovate's ansible-galaxy
  manager matches `requirements.yml` by default, so a pinned
  requirements file gets bump proposals for free.
* **"Until 0.7.0 is released" is stale.** Kerbside 0.7.0 is on PyPI,
  released 2026-10-18, but the text survives in four places:
  * R/meta/argument_specs.yml:151-152
  * `docs/operator_guide/installation.md:217`
  * `docs/release_notes/v07-v08.md:95-97`
  * the collection README at `:261`
* **Pages describing the role:**
  * the collection README (`:22`, `:194-282`);
  * `docs/operator_guide/installation.md:211-258`;
  * `docs/operator_guide/vdi_console_tokens.md:55-62,78-81,211`;
  * `docs/developer_guide/ci.md:252-275`;
  * `docs/developer_guide/standards.md:296-303`;
  * the release note at `:85-105`.

  `docs/components/kerbside/` is synced from Kerbside and is never
  edited here (`AGENTS.md`).
* **Kerbside's `docs/plans/index.md` has no cross-reference row** for
  this plan, although the master plan's Documentation index
  maintenance section says it gains one "with phase 5".

### Corrections made at source

The planning commit corrects the master plan's phase 5 section and
related bullets:

* the false `test_ci_deploy_profiles.py` claim (S9);
* "Shaken Fist's `site.yml` computes its own entry", which becomes D3's
  node role entry point;
* the claim that phase 4's row tests "a released version of the role",
  which becomes D5;
* the #4367 bullet, since the lane stays on `build-smoke-cluster`
  (D8);
* the kerbside#482 bullet, since it is closed and kerbside#579 is open;
* the ordering bullet, since 0.7.0 exists and the new gate is a
  Kerbside release carrying the collection.

The `index.md` row gains this phase's findings. Later steps should not
redo any of these.

## Decisions

### D1 -- `shakenfist.kerbside`, at `ansible/` in Kerbside, released with Kerbside

**Naming and layout:**
* The collection is `shakenfist.kerbside` and the role `kerbside`, so
  the FQCN is `shakenfist.kerbside.kerbside`.
* It lives at `ansible/` in the Kerbside repository: `galaxy.yml`,
  `meta/runtime.yml` with `requires_ansible: '>=2.15.0'`, `README.md`,
  `roles/kerbside/`.
* The `shakenfist` namespace already exists and publishes from another
  repository with a working token (S3). A `kerbside` namespace would
  need creating, and buys nothing.

**Release:**
* Kerbside's `release.yml` builds the collection, versions it from the
  tag, and publishes it beside the PyPI packages. The job mirrors
  `client-python-k3s`'s publish job, including passing the token
  through `env:`.
* The release build stamps `kerbside_package: "kerbside==<version>"`
  into the role's defaults, the way `stamp-proxy-version.sh` stamps the
  proxy pin. It also stamps `kerbside_collection_version` into
  `vars/main.yml` (D2 reads it).
* So collection 0.8.0 installs Kerbside 0.8.0 by default. That pairing
  is the main thing the move buys: the role and the software it deploys
  are reviewed, tested and released together.
* The committed files are never stamped. They keep a floor
  (`kerbside>=0.7.0`) and `kerbside_collection_version: unreleased`.
  An install from a checkout, such as Kerbside's own lane (D8), never
  runs the release build, so it must carry a marker that says so
  rather than a version that looks real.

**The operator gate.** Publishing needs an `ANSIBLE_GALAXY_TOKEN`
secret in the Kerbside repository. That is the operator's to add.

### D2 -- a soft dependency, pinned by a requirements file that `site.yml` also reads

**No `galaxy.yml` dependency.** Shaken Fist's `galaxy.yml` keeps
`dependencies: {}`. A hard dependency would buy nothing and cost three
things:
* Galaxy refuses a git URL (S4), and Shaken Fist is not on Galaxy
  anyway (S3);
* Kerbside is optional, and most clusters do not deploy it;
* every CI install of Shaken Fist's freshly built tarball would try to
  resolve `shakenfist.kerbside` against Galaxy.

**A requirements file.** Shaken Fist ships
`examples/_shared/requirements.yml` beside `site.yml`. It pins
`shakenfist.kerbside` to the compatible minor series, starting at
`>=0.8.0,<0.9.0`. Operators who deploy Kerbside run
`ansible-galaxy collection install -r examples/_shared/requirements.yml`.
Renovate proposes the bumps (S10).

**`site.yml` keeps the soft-dependency shape and checks it early:**

* **Invariant: only `include_role`.** `site.yml` reaches the role only
  through `include_role` (S2), never `import_role` or `roles:`. The
  header comment states it, and the wiring test asserts it.
* **The validate play wraps its `include_role` in a block.** The
  rescue turns "role not found" into a message naming the install
  command.
* **The version is checked before any host changes.**
  * After `validate`, the play asserts that the installed role's
    `kerbside_collection_version` satisfies the range in
    `requirements.yml`.
  * The range is read from the file itself
    (`lookup('ansible.builtin.file', playbook_dir ~ '/requirements.yml')
    | from_yaml`), so the pin has one source.
  * An install from a checkout reports `unreleased` (D1). The check
    accepts it with a warning naming the installed path, so Kerbside's
    lane, which deploys its own pull request's role, is not refused.
    A real version outside the range is refused.

This is the decision a reviewer is most likely to push back on. The
alternative is a hard dependency by name, which "just works" for
someone who installs Shaken Fist from Galaxy. Nobody can do that today,
and the hard dependency would make every Shaken Fist install, with or
without Kerbside, depend on a second collection's release.

The soft shape costs one documented install command, for the operators
who want Kerbside. If Shaken Fist is ever published to Galaxy, a name
dependency can be added then without changing `site.yml`.

### D3 -- the seam: `kerbside_sources`, and Shaken Fist's entry built by the node role

**The role takes a required, non-empty `kerbside_sources` list.**
* `templates/sources.yaml` dumps it as data. The existing
  `to_nice_yaml` rendering and its quoting guarantees are kept.
* `kerbside_sources` is a secret-bearing value, so every task that
  renders or validates it keeps `no_log`.

**Shaken Fist's entry comes from a new node role entry point,
`kerbside_source`** (`roles/node/tasks/kerbside_source.yml`). It runs on
Kerbside hosts, and its inputs are the variables that leave the role:
`kerbside_system_key`, `kerbside_sf_ca_cert`, `api_url`,
`deploy_name`, `kerbside_url_on_sf_nodes` and `kerbside_colocated`. It:

* builds `{source: deploy_name, type: shakenfist, url: api_url,
  username: system, password: kerbside_system_key,
  ca_cert: kerbside_sf_ca_cert}` as the fact `kerbside_sf_source`;
* runs the Shaken Fist checks that leave the role (S1, items 4 and 6):
  * the CA is a PEM;
  * `api_url` is not loopback unless co-located;
  * `kerbside_url` agrees with what the Shaken Fist nodes render;
* sets `kerbside_reserved_ports` (D4):
  * Shaken Fist's listener ports when co-located;
  * and always, the instance console range from 30000.

**`site.yml` passes
`kerbside_sources: [kerbside_sf_source] + kerbside_extra_sources`.**
* `kerbside_extra_sources` defaults to `[]` and is the operator's hook
  for oVirt, OpenStack or static sources.
* The validate play runs `kerbside_source` before the role's
  `validate`, so a bad Shaken Fist entry fails before any host changes.

**Why the node role, rather than tasks in `site.yml`:** `site.yml`'s
header makes it the only place that reads inventory group names, and
keeps logic in roles. The node role already owns Shaken Fist's other
Kerbside entry points (`kerbside_preflight`, `kerbside_credentials`)
and their rootless test.

The CA slurp from the database node stays in `site.yml`, because it
reads group names.

### D4 -- the role is generalised in the move, not after

The role arrives in Kerbside already free of Shaken Fist. A copy that
still hard-codes one Shaken Fist source would immediately be wrong for
Kerbside's framing (a front door to several clouds). Generalising it
afterwards would be a second, breaking interface change. S1 says
there is no released consumer to protect.

* **Sources validation.** `kerbside-check-sources.py` validates each
  entry by Kerbside's own rules (S8):
  * the required keys;
  * the type is one of the four;
  * each type's required fields;
  * a Shaken Fist source's `username` is `system`;
  * source names are unique.

  It no longer demands Shaken Fist's six keys of every entry.
* **Readiness.** Readiness waits for the signing keys of every
  `type: shakenfist` source, by name. With none, it waits for the API
  alone. `kerbside-ready.py` takes the list.
* **Ports.**
  * `kerbside_reserved_ports` replaces `kerbside_colocated`,
    `kerbside_sf_listener_ports` and the flat 30000 ceiling.
  * It is a list of ports or `[low, high]` ranges that no listener may
    use, and defaults to `[]`.
  * Shaken Fist passes its own (D3), so a Shaken Fist deployment
    behaves exactly as today.
* **Code hash.** The hash covers the `kerbside` package, the proxy
  binary and `uv pip freeze`. It no longer requires
  `site-packages/shakenfist_client`.
* **The Shaken Fist client.** `kerbside_client_package` stays, and is
  installed when any source is `type: shakenfist`.
* **Local wheels.** A new `kerbside_local_wheels` option takes wheels
  on the controller. It copies them to the host and installs them in
  place of `kerbside_package` and `kerbside_proxy_package`. This is
  how a CI lane installs a pull request's Kerbside (D8).
  * The same wheel bytes give the same code hash, so the redeploy
    check still sees no restart.
* **Removed options.** These leave the role, and the argument specs
  shrink to match:
  * `kerbside_system_key`
  * `kerbside_sf_ca_cert`
  * `kerbside_url_on_sf_nodes`
  * `kerbside_colocated`
  * `api_url`
  * `deploy_name`
* **Wording.**
  * The descriptions and comments stop naming "the node role" and "the
    example playbook".
  * The internal_ca file names in `vars/main.yml` stay, as
    `kerbside_proxy_*` defaults with internal_ca's names as their
    values.
  * The units and their `multi-user.target` rationale move unchanged,
    apart from naming the collection.

### D5 -- CI installs the collection through a `collections` profile key

**actions' deploy profiles gain a sixth top-level key, `collections`.**
* It is a list of ansible-galaxy requirement entries.
* `deploy-collection.sh` writes them to a requirements file and
  installs them after Shaken Fist's tarball, before the first
  `site.yml`.
* The install happens once, not before the second deploy.
* Entries are validated like every other key: unknown fields are
  refused.
* A `type: dir` entry's path must be absolute, because ansible-galaxy
  resolves relative paths against the current directory (S4). The
  template's `workspace` variable builds one.

**Shaken Fist's slim-tier profile lists `shakenfist.kerbside` with
the version range from `examples/_shared/requirements.yml`.**
* The merge queue therefore deploys the same Galaxy release an operator
  gets.
* That is the honest form of the master plan's "against a released
  version of the role".
* `test_ci_deploy_profiles.py` checks that the profile's range equals
  the requirements file's.

**Shaken Fist's sanity job installs the collection from the same file,
for the wiring test (D6).**

### D6 -- the tests split along the seam

**Kerbside (step 1):**

* **The role-only test script.** The role-only cases of
  `ci-test-kerbside-role.sh` move as `tools/ci-test-ansible-role.sh`:
  validate, config, bootstrap, sources rendering and secrets. New cases
  cover:
  * several sources;
  * each type's validation;
  * reserved ports;
  * readiness with no Shaken Fist source;
  * local wheels.
* **Running it.**
  * A pre-commit hook and a step in `sanity_checks` run the script.
  * ansible-lint runs over `ansible/`.
  * `galaxy-importer` runs over the built tarball in `sanity_checks`.
    It is the check that caught the `requires_ansible` requirement in
    S3, and it is what Galaxy will run on release.
* **The host-subject script.** It moves with a unit test against a
  JSON fixture of expected renderings and refusals. The fixture is
  generated from Shaken Fist's `node.py` table and names it as its
  source. This is the Kerbside half of #4097 item 6.
* **Defaults and argument specs.** The test that every default is
  declared in the argument specs moves with the role.

**Shaken Fist (step 6):**

* **The wiring test.** `ci-test-kerbside-role.sh` becomes
  `tools/ci-test-kerbside-wiring.sh`. It installs `shakenfist.kerbside`
  from `requirements.yml` into a scratch collections path. It keeps:
  * cases 15-21, comparing internal_ca's file names with the installed
    role's defaults;
  * the "no undeclared role argument" check, against the installed
    argument specs.

  It adds:
  * the only-`include_role` invariant;
  * the missing-collection and out-of-range version messages;
  * cases for the `kerbside_source` entry point.

  The pre-commit hook and sanity step follow the rename. The hook's
  `files` pattern drops the role path, and adds `requirements.yml` and
  the node role's `kerbside_source.yml`.
* **`test_kerbside_host_subject.py`** keeps `node.py` against
  `spice_subject.py`, and drops the role script and the argument specs.
* **No unit-level cross-repository pin.** The seam is held end to end:
  phase 4's exchange test asserts that the proxy certificate's subject
  equals the `.vv`'s `host-subject`, on the slim-tier row. A cross-repo
  unit pin would either read an installed collection, and skip
  silently when it is absent, or vendor a copy that drifts. The
  end-to-end assertion does neither.
* **`test_ci_deploy_profiles.py`** keeps its constants. Their comments
  name the new home, and the requirements-range check from D5 is added.

### D7 -- five pull requests in three repositories, in a fixed order

| PR | Repository | Contents | Needs |
|----|------------|----------|-------|
| A | Kerbside | The collection, generalised (D1, D4), its tests (D6), the release job, the plans-index row | -- |
| B | actions | The `collections` profile key (D5) | -- |
| -- | Kerbside | **Release 0.8.0** to PyPI and Galaxy | A, the operator's token |
| C | Shaken Fist | The role deleted; `kerbside_source`; `site.yml`; `requirements.yml`; tests; docs; the profile | B, the release |
| D | Kerbside | The lane through the role (D8) | C |
| E | actions | `deploy-kerbside-on-shakenfist` removed | D |

**A and B can run in parallel.**

**The release is the operator's gate, as 0.7.0 was in phase 4.** C's
trial runs before it, installing the collection from A's merge commit
by git SHA (a `type: git` profile entry). C's merge queue then installs
from Galaxy.

**The role is frozen while it exists in two places.** From A's first
commit until C merges, the role exists in two repositories. In that
window, Shaken Fist's copy may change only for a fix that is ported to
A in the same session. The step 1 brief and the management session
enforce that.

### D8 -- Kerbside's lane deploys through the role, and stays on `build-smoke-cluster`

**The workflow changes:**
* Kerbside is checked out before `build-smoke-cluster`.
* The Kerbside and proxy wheels are built before it.
* The profile `tools/sf-e2e/deploy-profile.yml.j2` is passed in.

**The profile:**
* puts the `kerbside` group on the localhost topology's single node,
  co-located;
* sets `kerbside_url`, `kerbside_system_key`, the seed,
  `kerbside_sql_url` and `kerbside_local_wheels` (the two wheels);
* creates the database through `mariadb_sql`;
* asks for a redeploy check over `sf-*` and `kerbside-*` units;
* lists the role as a `type: dir` collection pointing at the pull
  request's `ansible/`.

So a Kerbside pull request tests its own role and its own code against
Shaken Fist develop.

**What goes, and what stays:**
* **Deleted:** `provision-sf.sh` and `gen-sources.py`. The node role
  now does their work from `kerbside_url` and `kerbside_system_key`:
  configuration, signing key and a dedicated credential. That also
  closes the Kerbside side of #4097 item 4, the `set-config` line.
* **Reduced:** `deploy-kerbside.sh` becomes building ryll and writing
  `kerbside.env` from the role's paths.
* **Unchanged:** the drivers, apart from the values in that file. They
  read the role-written configuration without loosening its file
  modes.
* **Added:** `gather-artifacts.sh` collects the `kerbside-*` units'
  journals.

**The lane stays on `build-smoke-cluster`.** It does not move to
`smoke-cluster.yml`, and #4367 stays open. `smoke-cluster.yml` ends by
running an stestr suite, and a reusable workflow cannot hand its
cluster to steps in the calling job, so Kerbside's drivers cannot run
there.

**After D merges, E deletes `deploy-kerbside-on-shakenfist`** and its
`docs/actions.md` entry.

### D9 -- docs only to keep pages true

**Kerbside:**
* `ansible/README.md`, which is also the Galaxy README, carries the
  variable reference moved from Shaken Fist's collection README, in
  generic form.
* The Ansible row of `docs/index.md`'s Deployment Options table now
  says the role is Kerbside's.
* `docs/plans/index.md` gains the cross-reference row (S10).

The full Ansible page is Kerbside's deployment options plan's phase 5.

**Shaken Fist:**
* The collection README's Kerbside section shrinks to the Shaken Fist
  side: the group, `kerbside_url`, the credential, `api_url`,
  `kerbside_extra_sources`, the requirements file, and a link to
  `shakenfist.kerbside`.
* `installation.md`, `vdi_console_tokens.md`, `ci.md` and
  `standards.md` follow suit.
* The release note's Kerbside bullet says where the role now lives.
* The four "Until 0.7.0" passages are fixed (S10).

Phase 6 still writes `docs/operator_guide/kerbside.md`.

### D10 -- a copy with provenance, not a history graft

A's first commit copies the role verbatim from Shaken Fist `0016c5dad`
and says so. The generalisation is a separate commit on top, so review
sees the diff from the known-good copy.

Grafting history across repositories would bring Shaken Fist's
unrelated history, or a filtered rewrite, into Kerbside. Neither helps
a reader, and the provenance line points at the real history.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | Kerbside worktree `../kerbside-wt-kd-p05` from `origin/develop` | Copy the role into `ansible/` as `shakenfist.kerbside`, with collection scaffolding, tests, hooks and CI (D1, D6, D10). See brief 1. Commits (Kerbside): "Copy the kerbside role from Shaken Fist.", then "Test the ansible collection in CI." |
| 2 | xhigh | opus | same Kerbside worktree | Generalise the role (D4). See brief 2. Commit (Kerbside): "Take any console source in the ansible role." |
| 3 | medium | sonnet | same Kerbside worktree | Release job, stamping and docs (D1, D9). See brief 3. Commit (Kerbside): "Publish the ansible collection with each release." |
| 4 | high | opus | actions worktree `../actions-wt-kd-p05` from `origin/main` | The `collections` profile key (D5). See brief 4. Commit (actions): "Let a deploy profile install collections." |
| 5 | xhigh | opus | this worktree | Shaken Fist consumes the external role (D2, D3, D5). See brief 5. Commit: "Deploy Kerbside from shakenfist.kerbside." |
| 6 | high | opus | this worktree | Split the tests (D6). See brief 6. Commit: "Test the Kerbside wiring against the collection." |
| 7 | medium | sonnet | this worktree | Docs (D9). See brief 7. Commit: "Point the Kerbside docs at shakenfist.kerbside." |
| 8 | high | opus | Kerbside worktree `../kerbside-wt-kd-p05-lane` from `origin/develop` after C merges | The lane through the role (D8). See brief 8. Commit (Kerbside): "Deploy the sf-e2e lane through the ansible role." |
| 9 | low | sonnet | actions worktree from `origin/main` after D merges | Remove `deploy-kerbside-on-shakenfist` and its docs entry (D8). Commit (actions): "Remove the deploy-kerbside-on-shakenfist action." |

The management session runs each repository's full check and reviews
each step before committing it. Pull request A is steps 1-3; B is step
4; C is steps 5-7; D is step 8; E is step 9.

**Trials, before each pull request is opened:**

* **A:** Kerbside's own CI, including `galaxy-importer`.
* **B:** a `workflow_dispatch` of actions' canary with a profile that
  uses `collections`. Also, Shaken Fist's merge matrix against B's
  branch with no profile change, which must render commands identical
  to `main`'s.
* **C:** a `TRIAL: do not merge` commit, as in phase 4.
  * It points the matrix at B's branch and installs the collection
    from A's merge commit by git SHA.
  * It dispatches `functional-tests.yml`.
  * The slim-tier row must pass with the redeploy check clean and
    phase 4's five Kerbside tests passing, not skipped.
* **D:** the lane's pull request run, and one `workflow_dispatch` that
  matches the nightly trigger.

### Brief 1 -- the collection, copied, scaffolded and tested

Work in `../kerbside-wt-kd-p05`, a new worktree of
`shakenfist/kerbside` from `origin/develop`. Read D1, D6 and D10, and
S1, S3 and S9. Kerbside's Python style is 80 columns with single
quotes (`AGENTS.md:217-219`).

**Commit 1 is the verbatim copy.**
* Copy Shaken Fist's
  `shakenfist/deploy/collection/roles/kerbside/` at `0016c5dad`
  byte for byte to `ansible/roles/kerbside/`. Skip `__pycache__`.
* Add `ansible/galaxy.yml`:
  * namespace `shakenfist`, name `kerbside`, version `0.0.0`
    (stamped at release by step 3);
  * readme, license Apache-2.0, repository and issue URLs;
  * `dependencies: {}`;
  * a `build_ignore` for tests and caches.
* Add `ansible/meta/runtime.yml` with `requires_ansible: '>=2.15.0'`.
* Add a stub `ansible/README.md`.
* The commit message names the source commit.
* Change nothing else in this commit, not even comments, so the
  review diff is empty against Shaken Fist's copy.

**Commit 2 is the tests and CI.** Port the *role-only* cases of
Shaken Fist's `tools/ci-test-kerbside-role.sh` to
`tools/ci-test-ansible-role.sh`:
* validate, config, bootstrap, sources rendering, secrets;
* the bootstrap stub-venv technique at Shaken Fist
  `tools/ci-test-kerbside-role.sh:775-800`.

Leave behind cases 15-21. They test Shaken Fist's `site.yml` and
internal_ca, and step 6 keeps them.

Symlink `ansible/` as `ansible_collections/shakenfist/kerbside` in a
scratch directory, as the Shaken Fist script does at `:51-53,83-84`.

At this commit the cases still exercise the Shaken Fist-shaped
interface. Step 2 changes both together.

Also add:
* **Tests:**
  * a unit test that every role default is declared in
    `argument_specs['main']`, ported from Shaken Fist
    `shakenfist/tests/test_kerbside_host_subject.py:217-227`;
  * a unit test of `files/kerbside-host-subject.py` against
    `tests/fixtures/host-subject-renderings.json`. Generate that fixture
    once from Shaken Fist's `node.py` (`_SPICE_SUBJECT_SHORT_NAMES` and
    the renderings and refusals pinned at
    `test_kerbside_host_subject.py:147-178`), and name the generating
    commit inside it.
* **Hooks and CI:**
  * a pre-commit hook running the script on changes under `ansible/`;
  * a step in `functional-tests.yml`'s `sanity_checks` running it;
  * ansible-lint over `ansible/`, with a config modelled on Shaken
    Fist's `shakenfist/deploy/collection/.ansible-lint`;
  * `ansible-galaxy collection build ansible/` followed by
    `galaxy-importer` (PyPI `galaxy-importer`) on the tarball, failing
    the job on an import error.

Put the CI logic in a script under `tools/`, not inline in the workflow
step.

**Constraints:**
* **The Kerbside wheel must not pick up `ansible/`.** Check
  `sanity_checks`' wheel-contents test still passes.
* **Branch scope.** Do not edit `docs/components` or anything outside
  `ansible/`, `tools/`, `tests/`, the hooks and the workflow.
* **Done when** all ported cases pass and the importer accepts the
  tarball. Mutate twice and confirm a failure each time:
  * remove `requires_ansible`, and the importer step must fail;
  * add an undeclared default, and the defaults test must fail.

### Brief 2 -- the role takes any console source

Same worktree. Read D4 and D3, S1 and S8, and Kerbside's
`docs/console-sources.md` and `kerbside/main.py:67-151,255-268`. This
step changes the role's interface; the test script changes with it.

1. **`kerbside_sources`.** It is required and must be a non-empty
   list, declared in `meta/argument_specs.yml` with `no_log`.
   * `templates/sources.yaml` dumps it with the existing
     `to_nice_yaml(indent=2, width=100000)`. Keep the template's
     comment on why it is data, not strings.
   * Delete the hard-coded entry.
2. **`files/kerbside-check-sources.py`** validates per Kerbside's
   rules:
   * `source` and `type` required;
   * the type is one of `shakenfist`, `ovirt`, `openstack`, `static`;
   * per-type required fields as in `docs/console-sources.md`;
   * a Shaken Fist source's `username` equals `system`;
   * `static` requires a `consoles` list whose entries carry the
     required console keys (`kerbside/sources/static.py:12-31`);
   * names are unique.

   The checker never prints a value, only key names and positions:
   these are secrets.
3. **Readiness** waits on every `type: shakenfist` source's keys.
   * `kerbside-ready.py` takes source names.
   * `register.yml` computes them from `kerbside_sources`.
   * With none, readiness is the API probe alone.
   * Keep `kerbside_ready_retries`.
4. **Ports.** `kerbside_reserved_ports` is a list of ints or
   two-element `[low, high]` ranges, default `[]`.
   * `validate.yml` refuses any listener in it.
   * Delete `kerbside_colocated`, `kerbside_sf_listener_ports` and the
     flat 30000 ceiling.
   * Keep the "no two ports equal" check.
5. **Delete from the role** the loopback `api_url` check, the
   `kerbside_url_on_sf_nodes` check and `config.yml:25-33`'s CA
   assertion. Step 5 re-homes them.
   * Remove `kerbside_system_key`, `kerbside_sf_ca_cert`,
     `kerbside_url_on_sf_nodes`, `kerbside_colocated`, `api_url` and
     `deploy_name` from the defaults and the argument specs.
6. **Bootstrap.**
   * Install `kerbside_client_package` only when some source is
     `type: shakenfist`.
   * The code hash covers the `kerbside` package directory, the proxy
     binary and `uv pip freeze`, and must not fail when
     `shakenfist_client` is absent.
7. **`kerbside_local_wheels`**, a list of controller-side paths,
   default `[]`.
   * When set, copy them to `{{ kerbside_state_dir }}/wheels/` and
     install them in place of the corresponding package spec. A
     `kerbside-*.whl` replaces `kerbside_package`, and a
     `kerbside_proxy-*.whl` replaces `kerbside_proxy_package`.
   * Identical bytes must give an identical code hash.
8. **Wording.** Rewrite descriptions and comments that name "the node
   role", "the example playbook" or "Shaken Fist's rules" so they
   describe the role on its own terms.
   * Shaken Fist appears only as one source type among several.
   * `meta/main.yml`'s description too.

**Tests.** Update the role test script for the new interface, and add
cases for:
* two sources of different types;
* each type's missing-field refusal;
* a duplicate name;
* the `system` username rule;
* reserved ports, as a single port and as a range;
* readiness arguments with and without a Shaken Fist source;
* local wheels: copied, installed and hash-stable.

Keep the secrets case. It must cover every type's password field.

**Done when** the script passes. Mutate each of the following, and
confirm that each fails the case named for it:
* the type allow-list;
* the `system` rule;
* the range check;
* the readiness name list;
* the hash.

### Brief 3 -- release, stamping and docs

Same worktree. Read D1 and D9.

**The release job.** In `.github/workflows/release.yml`, add
`build-collection` and `publish-collection` jobs.

* **Model** them on `client-python-k3s`'s `release.yml` publish job:
  * download the artifact into `runner.temp`;
  * a venv with ansible-core;
  * `ANSIBLE_GALAXY_TOKEN` passed through `env:`, never interpolated
    into the script;
  * publish on tag pushes only, after `sign-tag`.
* **The build** runs a new `tools/build-collection.sh`, which:
  * copies `ansible/` to a temporary directory;
  * stamps `version:` in the staged `galaxy.yml` from the tag, using
    the same version string as the Python packages;
  * stamps `kerbside_package: "kerbside==<version>"` into the staged
    `roles/kerbside/defaults/main.yml`;
  * stamps `kerbside_collection_version: "<version>"` into the staged
    `vars/main.yml`, replacing the committed `unreleased`;
  * runs `ansible-galaxy collection build`.
* **The checkout is never modified.** Shaken Fist's phase 4 found
  that rewriting a tracked file in place dirties every later version;
  see Shaken Fist `tools/build-collection.py`'s header.
* **The committed files** keep the floor `kerbside>=0.7.0` and
  `kerbside_collection_version: unreleased` (D1). Brief 1's
  verbatim-copy commit cannot add the variable, so this step adds it
  to `vars/main.yml`.
* **Attach** the tarball to the GitHub release too.
* **`RELEASE-SETUP.md`** documents the `ANSIBLE_GALAXY_TOKEN` secret.

**Docs:**
* `ansible/README.md` is the Galaxy README. It moves the variable
  reference from Shaken Fist's
  `shakenfist/deploy/collection/README.md:194-282` and makes it
  generic: sources, reserved ports and local wheels. It links to
  Shaken Fist's operator guide for the Shaken Fist side.
* The Ansible row of `docs/index.md`'s Deployment Options table
  (`:145`) says the role is Kerbside's.
* `docs/plans/index.md` gains a cross-reference row for Shaken Fist's
  `PLAN-kerbside-deployer.md`, in the shape the tokens plan's row used.

**Done when:**
* a dry run of `tools/build-collection.sh` with a fake tag leaves
  `git status` clean;
* it produces a tarball the importer accepts;
* the tarball's defaults pin the fake version.

### Brief 4 -- deploy profiles install collections

Work in `../actions-wt-kd-p05`, from `origin/main`. Read D5 and S4-S5.

* **The key.** `tools/ci-apply-deploy-profile.py` accepts a sixth
  top-level key, `collections`, as a list of mappings.
  * Each mapping takes `name` (required), `type` (`galaxy`, `git`,
    `dir` or `file`; default `galaxy`) and `version` (optional).
    Refuse unknown fields.
  * A `dir` or `file` entry's path must be absolute after rendering.
    Refuse relative ones with a message saying why: ansible-galaxy
    resolves them against the current directory.
  * The script writes the entries to a requirements file beside the
    extra vars file, and reports its path the way it reports the
    others.
* **The install.** `tools/deploy-collection.sh` runs
  `ansible-galaxy collection install -r <file>` after Shaken Fist's
  tarball install and before `site.yml`.
  * The collections land in the same collections path that
    `ansible-playbook` uses.
  * `tools/ci-deploy-cluster.sh` passes the file through, and installs
    once, before the first deploy.
* **The off path.** A profile without the key must render byte-for-byte
  the same commands as today. Phase 4's brief 1 set the same rule;
  extend its test.
* **Tests and docs.**
  * Add tests to `ci-apply-deploy-profile.py`'s suite for the new
    key's refusals. Mutate once per refusal.
  * Document the key in `docs/consuming.md`'s profile section, with a
    Galaxy and a `dir` example.

### Brief 5 -- Shaken Fist deploys Kerbside from `shakenfist.kerbside`

This worktree. Read D2, D3 and D5, and S1-S2. Only start this step
after A has merged. A is pull request A of D7: briefs 1-3.

1. **The node role entry point `kerbside_source`.**
   * Add `roles/node/tasks/kerbside_source.yml`, with an entry in
     `roles/node/meta/argument_specs.yml`.
   * Its inputs are `kerbside_system_key`, `kerbside_sf_ca_cert`,
     `api_url`, `deploy_name`, `kerbside_url`,
     `kerbside_url_on_sf_nodes` and `kerbside_colocated`.
   * It sets the fact `kerbside_sf_source` and `kerbside_reserved_ports`.
     Use the listener list from the old role's `vars/main.yml:24-36`
     with its source citations, and the range `[30000, 65535]`.
   * It asserts the three Shaken Fist checks the old role made:
     * the CA is a PEM (old `config.yml:25-33`);
     * the loopback `api_url` (old `validate.yml:159-177`);
     * the `kerbside_url` agreement (old `validate.yml:185-196`).

     Move them with their messages.
   * Every fact holding the key is `no_log`.
2. **The requirements file.** Add `examples/_shared/requirements.yml`:
   ```yaml
   collections:
     - name: shakenfist.kerbside
       version: '>=0.8.0,<0.9.0'
   ```
3. **`site.yml`.**
   * Replace `shakenfist.shakenfist.kerbside` with
     `shakenfist.kerbside.kerbside` in every `include_role`.
   * In the validate play:
     * run `node`'s `kerbside_source` first;
     * then run the role's `validate` with
       `kerbside_sources: "{{ [kerbside_sf_source] + kerbside_extra_sources | default([]) }}"`
       and `kerbside_reserved_ports`, inside a block whose rescue fails
       with "shakenfist.kerbside is not installed; run ansible-galaxy
       collection install -r examples/_shared/requirements.yml" when
       the failure is a missing role;
     * then assert that `kerbside_collection_version` satisfies the
       range read from `requirements.yml`.
       * Use `lookup('ansible.builtin.file', playbook_dir ~
         '/requirements.yml') | from_yaml`, so there is one source for
         the range.
       * Use `version(..., 'ge')` on the floor and `'lt'` on the
         ceiling.
       * Accept `unreleased` with a warning, since that is an install
         from a checkout (D1).
   * In "Deploy Kerbside", pass the same `kerbside_sources` and
     `kerbside_reserved_ports` to `bootstrap`, `config` and `register`.
     Run `kerbside_source` in that play too, after the CA slurp.
   * Keep the internal_ca certificate tasks where they are. Their
     `cert_dest_dir`, owner and group derivations must still match the
     external role's defaults; step 6 tests that.
   * Add `kerbside_extra_sources` to the header's variable notes.
   * Add the only-`include_role` invariant to the header (D2).
4. **Delete the old role.** Delete
   `shakenfist/deploy/collection/roles/kerbside/`. Remove its row from
   the collection README's roles table; step 7 rewrites the section.
5. **The slim-tier profile.** In
   `tools/ci-deploy-profiles/kerbside-slim-tier.yml.j2`, add
   `collections: [{name: shakenfist.kerbside, version: <the range>}]`.
   Drop the comment about `kerbside_package` staying at the role
   default, since the role now pins its own release.

**Constraints:**
* **Do not use `import_role` or `roles:`.** S2 explains why.
* **Do not add a `galaxy.yml` dependency.** D2 explains why.
* **Done when:**
  * the trial (D7) passes on the slim-tier row;
  * with the collection uninstalled and an empty `kerbside` group,
    `site.yml` deploys a cluster with no Kerbside;
  * with the collection uninstalled and a non-empty group, it fails in
    the validate play with the install message, before any host
    changes.

### Brief 6 -- the wiring test

This worktree. Read D6 and S9.

1. **Rename and slim the script.** `git mv tools/ci-test-kerbside-role.sh
   tools/ci-test-kerbside-wiring.sh`. Delete the role-only cases, which
   now live in Kerbside (brief 1).
2. **Install the collection** from
   `examples/_shared/requirements.yml` into a scratch collections path
   set by `ANSIBLE_COLLECTIONS_PATH`. Put Shaken Fist's in-tree
   collection first through the existing symlink, so the PR's own roles
   win.
3. **Keep and adapt cases 15-21:**
   * the play order;
   * the include lists, with the new FQCN and the `kerbside_source`
     includes;
   * internal_ca's file names compared with the *installed* role's
     `defaults/main.yml`;
   * "no task sets an undeclared role argument", against the installed
     argument specs plus the two new arguments;
   * the internal_ca issuance cases.
4. **Add cases:**
   * no `import_role` and no `roles:` names `shakenfist.kerbside`
     anywhere in `site.yml`;
   * the missing-collection rescue message, with the collection path
     emptied;
   * the out-of-range version refusal, with a stub collection whose
     `vars/main.yml` declares `0.7.0`;
   * `unreleased` is accepted with the warning, and `0.9.0` is
     refused;
   * `kerbside_source`'s three refusals and its output entry, rendered
     and compared field by field;
   * `kerbside_extra_sources` is appended after Shaken Fist's entry.
5. **Hooks and CI.**
   * The `.pre-commit-config.yaml` hook becomes `test-kerbside-wiring`,
     with `files` covering `examples/_shared/(site|requirements)\.yml`,
     `roles/node/tasks/kerbside_source.yml`, internal_ca's
     defaults/tasks/vars and the script.
   * `functional-tests.yml`'s sanity step follows the rename.
6. **`test_kerbside_host_subject.py`.** Drop the role script and the
   argument-spec test. Keep `node.py` against `spice_subject.py`, and
   rename the test case accordingly.
7. **`test_ci_deploy_profiles.py`.**
   * Update the constants' comments to name `shakenfist.kerbside`.
   * Add a check that the slim-tier profile's collection range equals
     `requirements.yml`'s.
   * Mutate once to show the check fails.

**Done when** `pre-commit run --all-files` passes, and each new case
fails under a mutation that breaks its property. Record the mutation
count in the commit message.

### Brief 7 -- documentation

This worktree. Read D9 and S10.

* **The collection README.** Rewrite the Kerbside section of
  `shakenfist/deploy/collection/README.md` (`:194-282`) to the Shaken
  Fist side only:
  * the `kerbside` group;
  * `kerbside_url`, `kerbside_system_key` and `api_url`;
  * the CA;
  * `kerbside_extra_sources` with a non-Shaken Fist example;
  * the requirements file and its install command;
  * a link to `shakenfist.kerbside` for every role variable.
* **The other pages.** Update to match:
  * `docs/operator_guide/installation.md:211-258`, whose variables
    table loses the role's own variables and links instead;
  * `docs/operator_guide/vdi_console_tokens.md:55-62,78-81,211`;
  * `docs/developer_guide/ci.md:252-275`, covering the `collections`
    profile key and where the role comes from;
  * `docs/developer_guide/standards.md:296-303`, for the renamed hook.
* **Release note.** In `docs/release_notes/v07-v08.md`, the Kerbside
  bullet (`:85-105`) says the role ships as `shakenfist.kerbside` from
  the Kerbside repository, and gives the install command.
* **Stale text.** Remove every remaining "Until 0.7.0 is released"
  passage (S10).
* **Never edit `docs/components/kerbside/`.**

**Done when:**
* `grep -rn "shakenfist.shakenfist.kerbside\|roles/kerbside" docs
  shakenfist/deploy/collection examples tools` finds nothing outside
  plan files;
* `grep -rn "Until 0.7.0" docs shakenfist` finds nothing;
* the docs link check passes.

### Brief 8 -- Kerbside's lane through the role

Work in a new Kerbside worktree from `origin/develop`, after C has
merged. C is pull request C of D7: briefs 5-7. Read D8, S5 and S6.

1. **Reorder the workflow.** In `.github/workflows/sf-e2e-functional.yml`:
   * check out Kerbside, and build the Kerbside wheel
     (`python3 -m build --wheel`) and the proxy wheel, *before*
     `build-smoke-cluster`;
   * pass `deploy_profile:
     kerbside/tools/sf-e2e/deploy-profile.yml.j2`;
   * delete the `deploy-kerbside-on-shakenfist` step.

   First confirm kerbside#579's state. If the proxy wheel build is
   still failing, say so; it is not this step's to fix.
2. **The profile** `tools/sf-e2e/deploy-profile.yml.j2`. Use actions'
   `docs/consuming.md` profile section as the reference.
   * `groups.kerbside.hosts` is the localhost topology's node.
   * `extra_vars`:
     * `kerbside_url`, the existing `http://127.0.0.1:13002`;
     * `kerbside_public_fqdn`;
     * a throwaway `kerbside_system_key` that is different from the
       CI system key;
     * a throwaway `kerbside_auth_secret_seed` of 32 or more
       characters;
     * `kerbside_sql_url`;
     * `kerbside_local_wheels`, absolute runner paths built from
       `workspace`.
   * `mariadb_sql` creates the database and user.
   * `redeploy_check.units`: `sf-*.service` and `kerbside-*.service`.
   * `collections`: `[{name: '{{ workspace }}/kerbside/ansible',
     type: dir}]`.
3. **Delete** `tools/sf-e2e/provision-sf.sh` and
   `tools/sf-e2e/gen-sources.py`.
4. **Reduce `deploy-kerbside.sh`.** Rename it if that reads better.
   It becomes:
   * building ryll as today;
   * writing `/tmp/sf-e2e/kerbside.env` with the role's paths: the
     venv, `/etc/kerbside/sources.yaml`, the SQL URL, the seed and the
     API port.

   The drivers keep their env contract. They must read the
   role-written files without changing those files' modes or owners;
   run them with `sudo` where they need to. Do not loosen
   `sources.yaml` or `kerbside.ini`.
5. **`gather-artifacts.sh`** collects `journalctl -u 'kerbside-*'`
   from the node, through the existing redaction pass.
6. **Docs.** Update `tools/sf-e2e/README.md`, and the "worked example"
   section of `docs/use-cases/shakenfist.md` (`:283-311`): the lane now
   deploys through the role, and Kerbside runs as systemd units.

**Done when:**
* the pull request run passes;
* a `workflow_dispatch` run passes;
* the gathered artifacts include the Kerbside journals with no
  secret in them.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The role drifts between the two repositories while both copies exist. | D7's freeze. The management session refuses any Shaken Fist change under the old role path between A's first commit and C's merge, unless it is ported to A in the same session. Brief 1's verbatim commit makes any drift visible as a diff. |
| A Kerbside minor release breaks Shaken Fist's `site.yml`. | D2 pins the minor series and checks the range before any host changes. Renovate proposes each bump as a Shaken Fist PR, whose merge queue deploys it on the slim-tier row. |
| The Galaxy publish fails at the first release, and C's gate never opens. | Brief 1 runs `galaxy-importer` in Kerbside's CI on every pull request, so a metadata error is found before release. If publishing still fails, C's merge can install from the release tag by git (`type: git`) until it is fixed. That costs only the requirements file's source, not `site.yml`. |
| Generalising the role changes Shaken Fist's behaviour unnoticed. | D3 makes Shaken Fist pass exactly today's reserved ports and source. C's trial runs phase 4's slim-tier row: both deploys, the redeploy check, and the five Kerbside tests passing rather than skipping. |
| The missing-collection path is only checked by `--syntax-check`, which cannot see it (S2). | Brief 6 tests it by running `site.yml` with an empty collection path, not by a syntax check. |
| Kerbside's lane breaks on the move and loses the only SPICE-through-proxy check. | D is a separate PR. The old lane keeps running until D merges, and E only removes the action after that. Brief 8 confirms kerbside#579 first, so an existing failure is not attributed to the move. |
| `kerbside_local_wheels` restarts Kerbside on every redeploy. | Brief 2 requires identical bytes to give an identical hash, with a test. Brief 8's profile runs the redeploy check over `kerbside-*`. |

## Definition of done

* **Kerbside A:**
  * `ansible/` holds `shakenfist.kerbside`.
  * Its first commit is byte-identical to Shaken Fist `0016c5dad`'s
    role. Check with
    `diff -r <shakenfist>/shakenfist/deploy/collection/roles/kerbside
    ansible/roles/kerbside` at that commit.
  * `tools/ci-test-ansible-role.sh` passes in `sanity_checks`, and so
    does `galaxy-importer` on the built tarball.
  * `grep -rn "deploy_name\|api_url\|kerbside_colocated\|kerbside_url_on_sf_nodes\|kerbside_sf_ca_cert" ansible/roles/kerbside`
    finds nothing.
* **Release:** Kerbside 0.8.0 is on PyPI, and `shakenfist.kerbside`
  0.8.0 is on Galaxy. Its defaults pin `kerbside==0.8.0`.
* **actions B:** a profile with `collections` installs them before the
  first deploy. A profile without the key renders the same commands as
  before.
* **Shaken Fist C:**
  * `shakenfist/deploy/collection/roles/kerbside` does not exist.
  * `grep -rn "import_role\|roles:" examples/_shared/site.yml | grep
    kerbside` finds nothing.
  * The merge queue's slim-tier row passes, installing the collection
    from Galaxy, with "0 restarted", and with the five Kerbside tests
    passed rather than skipped.
* **Kerbside D:**
  * `sf-e2e-functional.yml` names neither `deploy-kerbside-on-shakenfist`
    nor `provision-sf.sh`.
  * The lane passes on a pull request and on dispatch, and its
    artifacts include the `kerbside-*` journals.
* **actions E:** `deploy-kerbside-on-shakenfist/` does not exist.
* **Issues:**
  * #4097 gets a comment closing item 4's Kerbside side.
  * #4367 gets a comment saying why the lane stays on
    `build-smoke-cluster`.
* **Plans:** Kerbside's `docs/plans/index.md` carries the
  cross-reference row. Kerbside's deployment options plan's phase 5
  dependency is told which commit landed the move.

## Back brief

Before step 1, the implementing session confirms its understanding of
D1-D10 in a short message to the operator. It flags anything in this
plan the code contradicts.

There are three gates:

1. **D2's dependency shape, before step 5 starts.** The choice is
   between a soft dependency, a pinned requirements file, and the
   version check reading that file. It is cheap to propose and
   expensive to redo, since it decides how every operator installs the
   collection. The operator confirms it, or picks the hard name
   dependency instead.
2. **The Kerbside 0.8.0 release and the Galaxy token**, before C is
   enqueued. These are the operator's actions. Until then C's trial
   uses a git SHA, and C is not enqueued.
3. **D before E.** E is not opened until D has merged and one nightly
   run of the new lane has passed.

## Execution notes

**Back brief (2026-10-19, Australia/Sydney).** The operator:
* confirmed D2's soft dependency;
* will add `ANSIBLE_GALAXY_TOKEN` to the Kerbside repository and cut
  0.8.0 once pull request A merges;
* allowed this session to push and open pull requests A and B.

**Steps 1-3: pull request A, shakenfist/kerbside#580.**

* Step 1 committed as two commits: `2cb4592` (the verbatim copy;
  `diff -r` against `0016c5dad` is empty) and `c79df10` (tests and CI).
  * The role README went into the second commit, because
    galaxy-importer refuses a role without one, and adding it to the
    first would have broken the byte-identical copy.
  * The `check-yaml` exclusion for the Jinja templates was staged with
    the copy, so that commit is green on its own.
* Step 2 is `6484565`. It departs from brief 2 in three ways:
  * `kerbside_sources` defaults to `[]`, and validate refuses an empty
    list. The argument specs share one anchor, so `required: true` would
    break validate's empty-hosts path.
  * `kerbside_url` is required only when a source is
    `type: shakenfist`.
  * It adds `kerbside_proxy_cert_name`, `kerbside_proxy_key_name` and
    `kerbside_proxy_cacert_name`, which D4 implies.
* Step 3 is `17c3cdf`. `publish-collection` waits on `publish-pypi`, so
  the pinned Kerbside exists on PyPI before the collection naming it is
  public.
* CI's credential scan flagged the role test's invented oVirt password.
  `38d032e` allowlists it by text, following `.gitleaks.toml`'s own
  convention.
* Three automated review rounds followed: `8372bc6`, `1d539e1` and
  `664068f`.

  | Round | fix | document | consider |
  |-------|-----|----------|----------|
  | 1 | 3 | 2 | 5 |
  | 2 | 3 | 1 | 5 |
  | 3 | 0 | 2 | 6 |

  Every round targeted the role as copied, not code added in earlier
  rounds. Round 2's critical item is a real bug the copy brought over:
  * The role doubled every `%` in `kerbside.ini`, but Kerbside has read
    that file with interpolation off since 0.7.0 (Kerbside `00dbc36`,
    issue #551).
  * So a percent-encoded `kerbside_sql_url` password reached Kerbside
    doubled. Shaken Fist develop's copy of the role has the same bug
    until step 5 deletes it.
  * Phase 4's CI password has no `%`, so nothing caught it.
* Round 3 also made validate check `kerbside_sources` as typed JSON. An
  operator's unquoted numeric credential, already a number by the time
  Ansible hands it over, is now refused rather than silently changed.
  The collection check now builds through the release script, so every
  pull request exercises the stamps.

**Step 4: pull request B, shakenfist/actions#181.** Committed as
`a61b3ff`. It departs from brief 4 in three ways:
* the install uses `--force`, since a static runner can keep an earlier
  job's copy, which would otherwise win over a `dir` source;
* it refuses two inputs beyond the brief, a non-string version and a
  bare-string entry;
* it runs 14 mutations.

**Step 4's trial departed from D7.** actions' canary takes no deploy
profile. Instead:
* Shaken Fist's merge matrix ran against a trial branch of B. Like phase
  4's, it carried one "TRIAL: do not merge" commit in each repository,
  pointing `smoke-cluster.yml`'s composite actions and checkout at the
  branch.
* The slim-tier profile listed `shakenfist.k3s`, a real Galaxy
  collection.
* [Run 39158491769](https://github.com/shakenfist/shakenfist/actions/runs/39158491769)
  passed:
  * the collection installed once, before the first deploy;
  * the deploys took 330 s and 181 s, with 43 units unchanged and none
    restarted;
  * 147 tests passed and none failed;
  * the rows without a profile passed unchanged.
* The trial branches and worktree are deleted.

**The release (2026-10-20, Australia/Sydney).** A merged as Kerbside
`2093f2ed`. The operator added `ANSIBLE_GALAXY_TOKEN` as a repository
secret and tagged `v0.8.0`.
[Release run 39309817927](https://github.com/shakenfist/kerbside/actions/runs/39309817927)
passed all nine jobs. Kerbside 0.8.0 is on PyPI, and
`shakenfist.kerbside` 0.8.0 is on Galaxy. Installed from Galaxy, its
`kerbside_collection_version` is `0.8.0` and `kerbside_package` defaults
to `kerbside==0.8.0`. Gate 2 is met.

**Steps 5 and 6: one commit, `83d15cc23a`.** The old
`tools/ci-test-kerbside-role.sh` also tested `site.yml`'s include lists,
so a step 5 commit would have failed its own pre-commit hooks:
`test-kerbside-role`, and `py3`, since `test_kerbside_host_subject.py`
loaded a file from the deleted role. Step 5 departs from brief 5 in
these ways:
* **The CA.** The validation play passes `kerbside_sf_ca_cert: null`,
  because the CA does not exist before Shaken Fist's first deploy. The
  source carries a placeholder `ca_cert` and the PEM check is skipped.
  "Deploy Kerbside" runs `kerbside_source` again with the CA it reads.
* **The rescue always fails.** It names the install command only when
  `kerbside_collection_version` is undefined, that is when the role
  could not be loaded. `any_errors_fatal` also brings hosts which
  passed into the rescue, so keying on the failed task would have told
  them, wrongly, that the collection was missing.
* **The version check.** It handles every operator a range can use,
  compared as semver as ansible-galaxy does, not just a `ge` floor and
  an `lt` ceiling.
* **`kerbside_reserved_ports`** reaches the role as the fact
  `kerbside_source` sets, not as an `include_role` variable. Passing it
  under its own name would be a recursive template.
* **Additions beyond brief 5:**
  * `kerbside_source` refuses an empty `kerbside_system_key`, as the
    old role did.
  * "Deploy Kerbside" passes the role's three certificate file names
    to internal_ca.
  * `TOP_LEVEL_KEYS` in `test_ci_deploy_profiles.py` gains `collections`.

Step 6 renamed the test to `test_spice_subject.py` and replaced brief
6's internal_ca file-name match with a stronger check: for five
override sets, the paths, owner and group that `site.yml` gives
internal_ca equal what the installed role reads. The wiring script has
14 cases and kills all 51 mutations. It installs the pinned release
from Galaxy, so the hook and the CI sanity step now need network access
to Galaxy, which the old script did not.

**Step 7.** Brief 7's first check, the `grep` for
`roles/kerbside`, still finds two kinds of match, both correct:
* `docs/components/kerbside/`, which is synced from Kerbside and must
  not be edited;
* the installed collection's path
  `ansible_collections/shakenfist/kerbside/roles/kerbside` in
  `tools/ci-test-kerbside-wiring.sh`.

The check was written too broadly. Its other two checks pass. The
management session restored the installation guide's note that
Kerbside's admin UI is unavailable without Keystone. The Kerbside
collection documents nothing about Keystone, so linking to it would
have lost the note.

**C's trial needed no TRIAL commit.** B had merged and 0.8.0 was on
Galaxy, so the operator approved pushing the branch and dispatching
`functional-tests.yml` on it as it stood.
[Run 39346524038](https://github.com/shakenfist/shakenfist/actions/runs/39346524038)
passed every job:
* the sanity step's wiring test passed 14 of 14 cases;
* the slim-tier row installed `shakenfist.kerbside:0.8.0` from Galaxy
  before the first deploy;
* its redeploy check reported 43 units unchanged and 0 restarted,
  `kerbside-*` included;
* 147 tests passed and 0 failed, the Kerbside exchange and console
  tests among them. None of the 4 skipped tests is a Kerbside test.

**C merged** as `48849831c` (#4591).

**Step 8: pull request D, shakenfist/kerbside#587.** The operator
approved pushing and opening D, with its own run as the first trial.
Committed as `0778191`.
[Pull request run 39680016813](https://github.com/shakenfist/kerbside/actions/runs/39680016813)
passed. The redeploy check covered the `sf-*` and `kerbside-*` units.
The collection deployed from the checkout reports version `0.0.0`, and
`site.yml` accepts it with its warning about an unreleased collection.

One automated review round followed, at 1 fix and 7 consider. The
next round found 0 fix and 5 consider. `0d75081` took the fix and five
of the consider items:
* **The fix.** `drive-adversarial.py` called the source password the
  CI system key. It is the dedicated `kerbside` key the deploy mints.
* **A real leak, raised as a consider item.** The artifact redaction
  caught `KEY = value` secrets, but not a password in a URL's
  userinfo, such as `kerbside_sql_url`'s in a traceback. So the old
  pipeline uploaded it, as mutation M4 of four shows.
  `gather-artifacts.sh` now redacts `scheme://user:password@`, and its
  leak check looks for that shape.
* The journal names `kerbside-api` and `kerbside-daemon` beside the
  glob, so a unit that failed to load is still collected.
* The checkout is staged with `tar`, excluding `.git`, `target` and
  `kerbside/dist`.
* The deploy profile pins `kerbside_venv` and `kerbside_config_dir`,
  which the drivers read. The drivers' comments say they run as root.

The artifact bundle gains only the redacted `kerbside-*` journals.
Configuration and credential files are never gathered.
[Dispatch run 39687744662](https://github.com/shakenfist/kerbside/actions/runs/39687744662),
which matches the nightly trigger, passed on `0d75081`. Its artifacts
were clean: the only `REDACTED` was Shaken Fist's own
`MARIADB_PASSWORD`. D merged as Kerbside `89f199456`. The first nightly
to include it,
[run 39743016396](https://github.com/shakenfist/kerbside/actions/runs/39743016396)
(2026-10-22 07:47 UTC), passed, which met gate 3.

**Step 9: pull request E, shakenfist/actions#195.** Committed as
`0ae201c`. It deletes `deploy-kerbside-on-shakenfist/` and removes the
action from `README.md`, `ARCHITECTURE.md`, `docs/actions.md` and
`docs/ci.md`. Its review's one fix item asked for proof that no
caller remained. No code changed, and the reply showed no caller
remained:
* Kerbside's only branch, `develop`, stopped using the action at
  `89f199456`;
* none of kerbside-patches' six branches uses it;
* code search across the organisation finds only plan files.

The `REVIEWS.md` and `.vscode` review marks that still name the file
are generated, so they were left to their tooling. E merged as actions
`31139125c`.

**Issues and plans.**
* #4097 gained
  [a comment](https://github.com/shakenfist/shakenfist/issues/4097#issuecomment-6349025513)
  closing item 4's Kerbside side.
* #4367 gained
  [a comment](https://github.com/shakenfist/shakenfist/issues/4367#issuecomment-6349026043)
  saying why the lane stays on `build-smoke-cluster`.
* shakenfist/kerbside#589, merged as `829c636b2`, tells Kerbside's
  deployment options plan which commits landed the move, which
  unblocks its phase 5.

Every pull request in this phase has merged, and its worktrees and
branches are deleted.

## Future work

* **Publish `shakenfist.shakenfist` to Galaxy.** `release.yml`'s
  publish job has never run (S3). Once it does, D2 can add a name
  dependency if that ever becomes wanted. Filed as #4613.
* **A second live source in CI**, for example a static source beside
  Shaken Fist. This belongs to Kerbside's deployment options plan,
  whose gaps already note it.
* **#4097 item 6's Shaken Fist half.** Read brief 1's renderings
  fixture from `node.py`'s own unit tests, so both repositories check
  one table.
