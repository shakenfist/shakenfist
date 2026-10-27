# Deployment options phase 2: by hand, with pip, a venv and systemd

Master plan: [PLAN-deployment-options.md](/components/kerbside/plans/PLAN-deployment-options/).

## Planning effort

Planned at medium effort, as the master plan asks. Review effort:
medium. The phase adds one unit test, two unit files and one page.
The page makes many small factual claims about file modes, ports
and process behaviour, and each one can be checked against the
ansible role, which is the exercised version of the same recipe.

## Scope

In scope:

- `docs/deployment/manual.md`, the first page in the section. It
  creates `docs/deployment/` and an `order.yml` holding only this
  page (phase 1 decision 3).
- Example systemd units in `etc/systemd/`, which answers master
  plan question 3, and a unit test that holds them to the role's
  templates (decisions 1 and 2).
- Two corrections the page depends on. The first is
  `etc/kerbside.conf.example`'s ownership advice, which contradicts
  the role (decision 4). The second is `installation.md`'s
  silence on the per-source client libraries (decision 5).
- Making the "By hand" row of the `docs/index.md` Deployment
  Options table a link. The Ansible row's "Any" also gets
  corrected, since the survey found it false for oVirt (step 2d).
- Filing one issue against the role (step 2d).

Out of scope:

- **Fixing the role.** It installs no client library beyond
  `shakenfist-client`, so it cannot deploy a working oVirt or
  OpenStack source or a working Keystone login (survey finding 4),
  and the fix is an engineering change to `ansible/`. Phase 2 files
  it, and phase 5 documents whatever state it is in by then.
- **The role's API listener default.** The role binds gunicorn to
  `0.0.0.0` in plain HTTP. This page's example binds to loopback
  instead (decision 3), so the two differ on purpose. Whether the
  role's default is right depends on what Shaken Fist puts in
  front of a co-located Kerbside, which is phase 5's question.
  Recorded under Risks.
- A reverse proxy configuration. The page says that one is needed,
  and what it must do, but Kerbside does not ship an nginx or
  Apache file. One would be a second untested artefact.
- `installation.md`'s "Deploying for real" table, which phase 6
  owns.
- Distribution packages (deb, rpm). None exist, and the page says
  so.

## What the survey found

Phase 1 was checked complete first. Its merge, #555 (merge commit
200d6b3), is an ancestor of `develop`. `DOC_PATHS` includes
`docs/deployment/*.md` (`tools/check-backend-tls-claims.py:132`), and
the mutation tool carries the matching mutation
(`tools/mutate-backend-tls-claims.py:32,56`). Both issues exist and
are open: #553, and kerbside-patches#1859. `git ls-files
docs/deployment` prints nothing. No local branch or worktree is left
over. The remote branch `deployment-options-546` still exists on
origin, which is harmless. Phase 1 has one deferred item, recorded
against this phase in plan-blockers note n2: the by-hand and Ansible
rows' "Tested in Kerbside CI" cells were to be rewritten. It has
already been done, by #587 (`0778191`), which rewrote both cells
when `sf-e2e` moved onto the role. The close-out commit records
that.

The master plan's phase 2 section and its question 3 were then
checked against the tree on 2026-10-25. Three claims no longer
hold, and are corrected at source in the master plan in the same
commit as this file:

1. **"Kerbside ships no systemd units" is no longer true.** The
   `kerbside` role moved into this repository with the
   `shakenfist.kerbside` collection (#580). It carries both units as
   Jinja templates,
   `ansible/roles/kerbside/templates/kerbside-api.service` and
   `kerbside-daemon.service`. Question 3 assumed the units would
   come from Shaken Fist's collection, and that the collection would
   switch to Kerbside's copies once they existed. Both parts are now
   moot, because the role is Kerbside's own. What remains is whether
   a by-hand operator gets static files to copy. If they do, those
   files are a second copy of the templates in the same repository,
   and the copies can drift apart. Decisions 1 and 2 deal with
   that.
2. **There are no state or log directories to create.** The
   master plan asked the page to cover "state and log directories".
   Kerbside writes only two things at runtime. One is its API
   socket under `/run/kerbside` (`kerbside/rpc/server.py:46`), which
   the daemon unit's `RuntimeDirectory=kerbside` creates for the
   service user. The other is a CA tempfile written through
   `tempfile.mkstemp()` by the oVirt driver
   (`kerbside/sources/ovirt.py:38-42`). Logs go to syslog unless
   `LOG_OUTPUT_PATH` says otherwise (`kerbside/config.py:183-186`),
   so under systemd they reach the journal. The role creates
   `/srv/kerbside` only to hold the venv, and leaves it owned by root
   (`tasks/bootstrap.yml`, "Create the Kerbside user"). The page
   documents that layout, and invents no `/var/log/kerbside`.
3. **The open question 3 rationale citing #313 is out of date.**
   #313 (a malformed `kerbside.ini` exited zero) is closed, and
   Kerbside now exits with status 1 (`docs/configuration.md:16`).
   `Restart=always` is still right, for the reason the role's
   templates now give: Kerbside never exits on purpose, so any exit
   is one to restart from.

The survey also found two problems the master plan does not
mention:

4. **Nothing installs the client libraries, and the docs never
   say they are needed.** `pyproject.toml:103-107` lists four
   optional libraries in a comment, none of them a dependency:
   `shakenfist-client`, `ovirt-engine-sdk-python`, `keystoneauth1`
   and `openstacksdk`. Each is imported lazily. The Keystone login
   (`kerbside/api.py:232-244`) imports `keystoneauth1` and
   `keystoneclient`, from `python-keystoneclient`, which the comment
   does not list. OpenStack token exchange (`api.py:699-720`) imports
   `openstack` and the same two. Without them, the login and the
   OpenStack exchange each return a 500 and log the import failure.
   Interactive login is Keystone-only (#300), so this hits every
   deployment that lets users log in, not just OpenStack sources.
   Of the deployment paths, only Kolla's image installs any of them:
   it adds `openstacksdk` as a build plugin
   (kerbside-patches `_patches/patch013-kerbside-container-build.patch`)
   on a Kolla base image. (A correction made during
   implementation: the survey first found only the oVirt half
   below, and said OpenStack sources need nothing extra.)
   `ovirt-engine-sdk-python` is deliberately not a
   dependency (`pyproject.toml:105,193`). `kerbside/sources/ovirt.py:30`
   imports it lazily, and the source is ignored without it
   (`:79`). The only place that installs it is the oVirt CI lane
   (`tools/ovirt-e2e/deploy-kerbside.sh:124`), which also installs
   `libcurl4-openssl-dev` and `libssl-dev` for its build. The role's
   install task (`tasks/bootstrap.yml`, "Install Kerbside, its proxy,
   gunicorn and any client its sources need") adds
   `shakenfist-client` for Shaken Fist sources and nothing for oVirt.
   Yet its argument specs accept an `ovirt` source, and the index
   says it configures "Any" source. `installation.md` names neither
   client library. `shakenfist_client` is mentioned only in
   `use-cases/shakenfist.md:371`'s limitations table. So both the
   by-hand path and the role produce an oVirt source that is
   silently ignored.
5. **`etc/kerbside.conf.example` and the role disagree about who
   owns `kerbside.ini`.** The example's header (lines 4-6) says to
   install it "owned by the user Kerbside runs as and mode 0600".
   The role writes it as root, group `kerbside`, mode 0640, inside a
   root-owned 0750 `/etc/kerbside` (`tasks/config.yml`,
   `tasks/config_file.yml:25-27`). In that layout the service can
   read its configuration but cannot rewrite it.

The rest held. `installation.md`'s "What a running Kerbside needs"
already lists the pieces (two processes, a database and
`kerbside db upgrade`, the proxy's TLS material, a source, eight
settings), so this page links there and restates none of it.
`gunicorn` is a pinned dependency (`pyproject.toml:46`), so a venv
install brings it along, and the role's explicit `gunicorn` argument
is redundant. The API builds no URL from the request
(no `ProxyFix`, no `url_for`, no `request.host`), so a reverse proxy
in front of it needs no header plumbing.

## Decisions

1. **Ship static example units in `etc/systemd/`.** This answers
   question 3 with yes, in a different shape from the one the master
   plan expected. The by-hand page's operator does not run Ansible,
   so pointing them at a Jinja template with
   `{{ kerbside_venv }}` in it is pointing them at something they
   have to render in their head. `etc/systemd/kerbside-api.service`
   and `etc/systemd/kerbside-daemon.service` sit beside
   `etc/kerbside.conf.example`, the file this page already sends
   operators to. They are the role's templates rendered with the
   role's defaults, with one difference (decision 3), and with
   comments written for someone copying the files rather than for
   Ansible.
2. **A unit test holds the example units to the role's
   templates.** Two copies of one unit in one repository will drift,
   and only the role's copy is exercised: `sf-e2e` deploys through
   it on every pull request. So the test renders each template with
   `jinja2` (a pinned dependency), using the values in
   `ansible/roles/kerbside/defaults/main.yml`. It then compares every
   directive line, which is everything but comments and blank lines,
   with the matching file in `etc/systemd/`, section by section.
   Deliberate differences are an explicit override dict in the test,
   each with a comment saying why, so a new one cannot be added
   quietly. This is what lets the page say the units are tested,
   honestly: not by running them, but by being held to the ones CI
   does run.
3. **The example API unit binds to `127.0.0.1`, not `0.0.0.0`.**
   **This is the decision most likely to be argued with,** because it
   makes the example differ from the role, and a by-hand operator
   then needs a reverse proxy before anything outside the host can
   reach the API. The case for it is `installation.md` itself. It
   says the demo's `0.0.0.0` gunicorn line "is the demo's shape, not
   a recommendation", because the API carries bearer tokens and
   Keystone credentials over plain HTTP. A file people copy verbatim
   should be safe when copied verbatim. The page names both ways to
   put TLS on the API leg, a TLS-terminating reverse proxy or
   gunicorn's `--certfile` and `--keyfile`, and says that neither is
   exercised in CI. The case against is that every other copy of
   this command line binds to every interface. That is a reason to
   look at those copies (Risks), not to copy the exposure.
4. **Configuration is owned by root and readable by the
   `kerbside` group,** as the role lays it out: `/etc/kerbside` is
   `root:kerbside 0750`, and `kerbside.ini` and `sources.yaml` are
   `root:kerbside 0640`. The service needs to read its configuration
   and never writes it, and the role's layout is the one CI exercises.
   `etc/kerbside.conf.example`'s header is corrected to match. It is
   a comment, so the test that parses the example's `# key = value`
   lines is unaffected. The proxy key follows the role too: 0640,
   readable by the group.
5. **The per-source client libraries go in `installation.md`, not
   in the page.** Phase 1 decision 7 says that the pages link to
   `installation.md` for the pieces, and acquisition is
   `installation.md`'s job. "Installing with pip" gains a short
   statement: a Shaken Fist source needs `shakenfist-client` (0.8.3
   or later, per `use-cases/shakenfist.md:371`), and an oVirt source
   needs `ovirt-engine-sdk-python` together with the OS packages its
   build needs, both installed into Kerbside's venv. It also says
   that without them the source is ignored and the daemon logs why.
   An `openstack` source needs `openstacksdk`, `keystoneauth1` and
   `python-keystoneclient`. Interactive login needs the last two,
   whatever the sources, because it is Keystone-only. Without them
   the API answers 500 and logs the import failure. Static sources
   need nothing extra.
6. **Upgrades are a short section in the page, linked to
   `installation.md`'s "Upgrading".** By hand, an upgrade means
   installing the new tagged release into the venv, running
   `kerbside db upgrade`, then restarting the daemon and the API.
   The page adds only what is specific to running under systemd: the
   daemon restart ends every live SPICE session, because the proxy
   is the daemon's child. The proxy gets 15 seconds to drain
   (`KillMode=mixed`, `TimeoutStopSec=30`), and the page says so
   rather than promising a seamless upgrade. The version-specific
   upgrade notes stay where they are.
7. **The page is single-host.** Two Kerbsides, or an API on one
   host and a daemon on another, belong to `use-cases/placement.md`,
   and no lane runs either. The page links there and repeats
   `installation.md`'s one constraint: the API host needs the same
   `sources.yaml` and CA as the daemon's.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Ship example systemd units and a drift test. Create `etc/systemd/kerbside-api.service` and `etc/systemd/kerbside-daemon.service` by rendering `ansible/roles/kerbside/templates/kerbside-api.service` and `kerbside-daemon.service` with the values in `ansible/roles/kerbside/defaults/main.yml` (`kerbside_user` and `kerbside_group` `kerbside`, `kerbside_state_dir` `/srv/kerbside`, `kerbside_venv` `/srv/kerbside/venv`, port 13002, 2 workers). There is one deliberate difference: the API unit binds `127.0.0.1` rather than the role's `0.0.0.0` (decision 3 of `docs/plans/PLAN-deployment-options-phase-02-manual.md`). Keep every directive and its order. Rewrite the comments for someone copying the file by hand. Keep the role's reasons (why `Restart=always`, why `KillMode=mixed`, why `RuntimeDirectory`, why the 300 second timeout), and replace "written by the role" with a pointer to `docs/deployment/manual.md`. Keep the reason for `WantedBy=multi-user.target`, but say it plainly: a co-located Shaken Fist node isolates its own target. Say in the API unit that the loopback bind expects TLS in front, and name the two options. Then add `kerbside/tests/unit/test_systemd_units.py`. It loads `defaults/main.yml` with `yaml.safe_load`, renders each template with `jinja2.Environment(keep_trailing_newline=True)` from a `FileSystemLoader` on the templates directory, and passes those defaults plus an override dict holding only `kerbside_api_address: '127.0.0.1'`, with a comment citing decision 3. It parses both the rendering and the `etc/systemd/` file into a list of `(section, directive line)` pairs, ignoring comment and blank lines, and asserts the lists are equal, with a message naming the first difference. Also add one test that changes a directive in a rendered copy, and asserts the comparison then fails, so the test is shown to bite. Find the repository root the way other tests do (look at `kerbside/tests/unit/test_check_backend_tls_claims.py`'s `repository_root` use, or `test_docs_links.py`), and add no new helper if one exists. Run `tox -epy3 -- kerbside.tests.unit.test_systemd_units` and `tox -eflake8`. Then edit one directive in `etc/systemd/kerbside-daemon.service` by hand, confirm the test fails, and restore it with `git checkout`. Commit subject: "Ship example systemd units." |
| 2b | medium | sonnet | none | Two corrections the page depends on. (1) `etc/kerbside.conf.example` lines 4-6 say to install the file "owned by the user Kerbside runs as and mode 0600". Change that to the layout the ansible role uses (`ansible/roles/kerbside/tasks/config.yml`, "Create Kerbside's configuration directory", and `tasks/config_file.yml:25-27`): owned by root, group `kerbside`, mode 0640, in a `/etc/kerbside` that is `root:kerbside 0750`, so the service can read its configuration but not rewrite it. Keep the sentence about the file holding the JWT signing key and a cleartext service password. Do not touch any `# key = value` line: `kerbside/tests/unit/` parses them. (2) In `docs/installation.md` "Installing with pip" (lines ~40-78), add a short paragraph or three-row table after the `kerbside-proxy` paragraphs. It says that two source drivers import a client library which is not a Kerbside dependency, and which has to be installed into the same venv. A `shakenfist` source needs `shakenfist-client`, 0.8.3 or later (see `docs/use-cases/shakenfist.md:371`). An `ovirt` source needs `ovirt-engine-sdk-python`, which builds a C extension and needs `libcurl4-openssl-dev` and `libssl-dev` on Debian (check `tools/ovirt-e2e/deploy-kerbside.sh:105-116` for the list the CI lane uses, and give the RHEL names if they are clear, `libcurl-devel` and `openssl-devel`). An `openstack` source needs `openstacksdk`, `keystoneauth1` and `python-keystoneclient` (`kerbside/api.py:699-720`), and interactive login needs `keystoneauth1` and `python-keystoneclient` whatever the sources (`api.py:232-244`), because login is Keystone-only (#300); without them the API answers 500 and logs the import failure. `static` sources need nothing extra. Read those imports yourself before writing this, and give no version floor for these three: none is tested. Without its library, a source is ignored and the daemon logs why (`kerbside/sources/ovirt.py:79`; find the equivalent message in `kerbside/sources/shakenfist.py` around line 64 and quote neither message, describe them). Add the oVirt packages to the "Before you install: OS packages" section only as a conditional sentence, not to the main command lines. Run `tox -epy3 -- kerbside.tests.unit.test_docs_links` and `pre-commit run --all-files`. Commit subject: "Document the source drivers' client libraries." |
| 2c | high | opus | none | Write `docs/deployment/manual.md` and create `docs/deployment/order.yml`. Read first: `docs/plans/PLAN-deployment-options-phase-02-manual.md` (decisions and survey), `docs/installation.md` (all of it: this page must not restate "What a running Kerbside needs" or "Upgrading", only link them), `docs/use-cases/standalone.md` (for tone and its Status and limitations table), the role's `ansible/roles/kerbside/tasks/bootstrap.yml`, `config.yml`, `config_file.yml` and `register.yml` (the role is the tested form of this recipe, so every mode, owner and order on the page must match it or say why not), and the units from step 2a. Use exactly four `##` sections, in this order: `## Who this is for`, `## What it deploys`, `## How to use it`, `## Status and limitations` (phase 1 decision 7). The last holds a table headed `Not covered \| Why`. **Who this is for:** an operator who will not run Ansible, or who uses another configuration manager and wants to know what to make it do. It says the role (`ansible/README.md`) is the tested way to get the same result. **What it deploys:** the two units, the `kerbside` system user with no shell and a root-owned home at `/srv/kerbside`, the venv at `/srv/kerbside/venv`, `/etc/kerbside` laid out as in decision 4, `/run/kerbside` via `RuntimeDirectory`, logs to the journal through syslog (`LOG_OUTPUT_PATH`), and no state directory, because Kerbside keeps its state in the database. It also says what it leaves to the operator: the database server, the certificates, TLS for the API leg, and the firewall (link `docs/network-ports.md`). **How to use it:** numbered steps. (1) OS packages, by link to installation.md. (2) The user and group, with `useradd --system --no-create-home --home-dir /srv/kerbside --shell /usr/sbin/nologin`. (3) The venv, then `pip install kerbside==X.Y.Z`, a tagged release, per installation.md, plus the source client libraries, by link to step 2b's text. (4) `/etc/kerbside`, `kerbside.ini` from `etc/kerbside.conf.example` (link it by absolute GitHub URL, as installation.md does), and `sources.yaml`, with the modes. (5) TLS material for the proxy leg, by link, with the key 0640 `root:kerbside`. (6) The database and its user, then `runuser -u kerbside -- /srv/kerbside/venv/bin/kerbside db upgrade`, which is the role's own invocation. (7) Copy the units to `/etc/systemd/system/`, then `systemctl daemon-reload` and `systemctl enable --now kerbside-daemon kerbside-api`. (8) Check it works: `systemctl status`, `journalctl -u`, and a request to the API on the loopback port. Read `ansible/roles/kerbside/tasks/register.yml` "Wait for Kerbside's API to answer" and use the same request. (9) TLS in front of the API: a reverse proxy, or gunicorn's `--certfile`/`--keyfile` added to the unit's `ExecStart` together with a non-loopback bind. Neither is tested in CI. Then an `### Upgrading` subsection per decision 6, and one paragraph per decision 7 that links `../use-cases/placement.md`. **Status and limitations** rows, at least: no CI lane runs these units, though they are held to the role's templates by `kerbside/tests/unit/test_systemd_units.py` and the role is exercised by `sf-e2e`; TLS on the API leg is untested either way; no distribution packages; an upgrade ends live sessions; single host only; and nobody can log in without Keystone (#300). Link `../use-cases/*.md` for *why* someone deploys Kerbside, and do not explain it. **Backend TLS:** say nothing about the proxy-to-hypervisor leg except, if you need to, that it follows `rust/kerbside-proxy/src/backend.rs` and is described in the use-case pages. `tools/check-backend-tls-claims.py` scans this page, so run it. Create `docs/deployment/order.yml` in the format of `docs/use-cases/order.yml` (copy its header comment) with one entry, `- manual.md: By hand`. In `docs/index.md`'s Deployment Options table, make "By hand: pip, venv and systemd" a link to `deployment/manual.md`, and leave the other rows alone. In `docs/development.md` (~line 127), change the "`docs/deployment/`, once it exists, carries" sentence to the present tense. Run `tools/check-backend-tls-claims.py`, `tox -epy3 -- kerbside.tests.unit.test_docs_links`, and `pre-commit run --all-files`. Commit subject: "Document deploying Kerbside by hand." |
| 2d | low | (management session) | n/a | File one issue against kerbside. It is outward-facing, so confirm the wording with Mikal first. Title: "The ansible role installs no client library but shakenfist-client". Body: the role's argument specs accept `ovirt` and `openstack` sources, but `tasks/bootstrap.yml`'s install task adds only `shakenfist-client`, and only when a source is of type `shakenfist`. The other client libraries are deliberately not Kerbside dependencies (`pyproject.toml:103-107`) and are imported lazily: an oVirt source deployed by the role is ignored for want of `ovirt-engine-sdk-python` (`kerbside/sources/ovirt.py:79`), whose build also needs `libcurl4-openssl-dev` and `libssl-dev`; OpenStack token exchange returns 500 for want of `openstacksdk`, `keystoneauth1` and `python-keystoneclient` (`kerbside/api.py:699-720`); and Keystone login, the only interactive login (#300), returns 500 for want of the last two (`api.py:232-244`). `keystoneclient` is imported but missing from `pyproject.toml`'s list of optional libraries. Nothing in CI deploys an oVirt or OpenStack source through the role: the oVirt lane uses `tools/ovirt-e2e/deploy-kerbside.sh`, and the OpenStack lane uses Kolla's image. Link `PLAN-deployment-options.md`. Then, in `docs/index.md`'s Deployment Options table, change the Ansible row's "Any" to "Shaken Fist and static, until #NNN". Put the issue number in the master plan's phase 5 paragraph too, so phase 5 picks it up. Commit subject: "Record the role's missing client libraries." |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The drift test compares only what it parses, so a template change that adds a directive in a section the parser mishandles passes. | Step 2a's own test edits a directive and asserts failure. The management session also adds a directive to a scratch copy of a template and runs the test, before committing 2a. |
| The loopback bind in the example is read as Kerbside's recommendation, while the role binds `0.0.0.0`, and the two documents disagree with no reason given. | The override dict in the drift test carries the reason, and the page says outright that the role differs. Phase 5 asks whether the role's default is right for a dedicated Kerbside host, and records the answer there. |
| The page restates `installation.md` after all, which is the failure phase 1 decision 7 exists to prevent. | The management session diffs the page's claims against "What a running Kerbside needs" before committing 2c. The definition of done names the claims that must appear only by link. |
| The client-library paragraph in 2b gives a version floor or package name that is wrong on the day. | The floor comes from `use-cases/shakenfist.md:371`, which the use-case plan's review checked. The oVirt package names come from the lane that installs them. Neither is guessed. |
| The role's client-library gap goes unfixed, and the index's narrowed sources cell hardens into a permanent caveat. | The issue is filed, and phase 5 cannot complete its page without stating the role's current state. |

## Definition of done

- [x] `etc/systemd/kerbside-api.service` and
      `etc/systemd/kerbside-daemon.service` exist, and
      `tox -epy3 -- kerbside.tests.unit.test_systemd_units` passes.
- [x] Changing any one directive in either `etc/systemd/` file
      makes that test fail. Checked by hand in the management
      session, and the file is restored afterwards.
- [x] `grep -n 'bind 127.0.0.1' etc/systemd/kerbside-api.service`
      matches, and the drift test's override dict has exactly one
      key.
- [x] `head -12 etc/kerbside.conf.example` no longer says 0600 or
      "owned by the user Kerbside runs as".
- [x] `docs/installation.md` names all five client libraries:
      `for p in shakenfist-client ovirt-engine-sdk-python openstacksdk
      keystoneauth1 python-keystoneclient; do grep -q "$p"
      docs/installation.md || echo "missing $p"; done` prints
      nothing.
- [x] `docs/deployment/manual.md` exists with exactly the four `##`
      headings of phase 1 decision 7, in order:
      `grep '^## ' docs/deployment/manual.md`.
- [x] `manual.md` does not list the eight minimum settings, and
      does not include the `gunicorn --bind` or `kerbside daemon
      run` command lines from `installation.md`. It links "What a
      running Kerbside needs" instead:
      `grep -c 'what-a-running-kerbside-needs' docs/deployment/manual.md`
      is at least 1.
- [x] Every mode and owner `manual.md` states matches the role's
      tasks, or the page says why it does not.
- [x] `docs/deployment/order.yml` lists `manual.md` and nothing
      else.
- [x] The By hand row of the `docs/index.md` Deployment Options
      table links `deployment/manual.md`, and the Ansible row's
      sources cell names the client-library issue.
- [x] The client-library issue from step 2d exists, and its number
      is in the master plan's phase 5 paragraph.
- [x] `tools/check-backend-tls-claims.py` exits 0, and
      `tools/mutate-backend-tls-claims.py` still reports every
      mutation caught.
- [x] `tox -epy3`, `tox -eflake8` and `pre-commit run --all-files`
      pass.

## Execution notes

- 2026-10-25 (AEDT): before any step ran, the management session
  found survey finding 4 too narrow. The Keystone login and the
  OpenStack token exchange also import client libraries that are
  not dependencies. Finding 4, decision 5, steps 2b and 2d, the
  risks and the definition of done were corrected to cover all
  five libraries, and the index row with them.
- Back brief answered on 2026-10-25. Mikal approved step 2d's issue
  wording as shown (filed after 2c), and gave this session
  permission to push and open the pull request once the definition
  of done is checked.
- Steps 2a, 2b and 2c run in sequence, one sub-agent each. They
  share the worktree and its tox environment, and 2c's page links
  2a's units and 2b's installation text.
- The step 2b agent kept waking on background work of its own after
  reporting, so the management session stopped it before 2c started
  in the same worktree.
- Step 2c found that `installation.md`'s Debian package list lacked
  `python3-venv`, which the role installs. The fix went into
  `installation.md` rather than the page, which links it.
- Step 2d filed #602 on 2026-10-25 with the approved wording.

## Back brief

Before executing any step of this plan, back brief the operator
on your understanding of the plan and how the work you intend to
do aligns with it. Step 2d's issue wording is the one gate: it is
published outside the repository, so it is shown to Mikal before
it is filed.
