# Deployment options phase 5: the Ansible role

Master plan: [PLAN-deployment-options.md](/components/kerbside/plans/PLAN-deployment-options/).

## Planning effort

The master plan asks for medium effort. This plan was written in a
low-effort session, below that. The survey checked every claim below
against this tree, Shaken Fist's `develop` and the open issues on the
day, so it was not cut short, but the review should be done at medium.
Review effort: medium. Most of what this page could say is already
written elsewhere. The risk is a page that copies it and then drifts
from it.

## Scope

In scope:

- `docs/deployment/ansible.md`, the fourth page in the section, and its
  entry in `docs/deployment/order.yml`.
- Making the Ansible role row of the `docs/index.md` Deployment Options
  table a link.
- Correcting `ansible/README.md`'s claim that the role works with any
  console source type (finding 3).
- Pointing the by-hand page and the Shaken Fist use case at the new
  page, where they now point at the collection's README or nowhere
  (decision 5).
- One new issue, for the settings the role cannot write (finding 4,
  step 5b).

Out of scope:

- Changing the role. #602 (client libraries) and the step 5b issue
  (settings) are engineering, and this plan is documentation. The page
  states both gaps and links them.
- Restating the collection README's variable reference, or Shaken
  Fist's operator guide (decision 2).
- `installation.md`'s "Deploying for real" table. Phase 6 owns it.

## What the survey found

The master plan's phase 5 paragraph and its inventory were written
before the role moved, and before Shaken Fist wrote its operator guide
for Kerbside. Findings 1 and 2 corrected them at source in this
phase's planning commit: the inventory row for the role, and the phase
5 paragraph.

1. **The role is here, and documented here.** It is the `kerbside` role
   of the `shakenfist.kerbside` collection, under `ansible/` (25 files).
   v0.8.0 (2026-10-19) published it to Galaxy, and is still the newest
   release. `ansible/README.md` is a complete operator reference, about
   250 lines. It covers the five entry points (`validate`, `bootstrap`,
   `config`, `register`, `main`), a minimal playbook, every variable in
   tables, source examples for `shakenfist` and `static`, packages and
   local wheels, ports, certificates, readiness, layout, and redeploys.
   `ansible/roles/kerbside/README.md` only points at it. The master
   plan's inventory row still described the role as Shaken Fist's,
   under `shakenfist/deploy/collection/roles/kerbside/`, writing one
   `type: shakenfist` source and being built by shakenfist#4479.

2. **Shaken Fist's operator guide already covers the topologies.**
   shakenfist `docs/operator_guide/kerbside.md` was added on 2026-10-23
   (39ab2f2415) and last changed on 2026-10-24. It covers:
   - four topologies: co-located, a dedicated host, bring your own,
     and one Kerbside for several clusters or platforms through
     `kerbside_extra_sources`;
   - the network routes Kerbside and users need;
   - load balancing, with nginx examples, marked untested;
   - certificates from `internal_ca`, or the operator's own;
   - "What you give up today": #300, #301, #136 and the `system`
     credential.

   It links the collection README for the role's variables. The
   master plan said this phase would cover the bring-your-own path;
   Shaken Fist's guide already does, from the side that owns it, so
   this page links it. Shaken Fist pins the collection at
   `>=0.8.0,<0.9.0` in `examples/_shared/requirements.yml`.

3. **`ansible/README.md` overclaims.** Its second paragraph says "The
   role works with any console source type". It validates all four
   (`meta/argument_specs.yml:75`), but `tasks/bootstrap.yml:109-124`
   installs only `kerbside_client_package`, and only for a `shakenfist`
   source. An `ovirt` source is ignored, and an `openstack` source's
   exchange returns 500, for want of their client libraries. That is
   #602, open with no pull request. The index's sources cell for the
   role already says "Shaken Fist and static, until #602".

4. **The role writes fifteen settings and no others.**
   `templates/kerbside.ini` renders a fixed dict. It covers the
   database, the seed, the sources path, the FQDN, the certificate
   paths and subject, the audience, the four SPICE ports, the metrics
   port and the node name.
   It has no pass-through. Its own comment says it writes no
   `keystone_*` settings. So a role-deployed Kerbside cannot have a
   Keystone login, even once #602 installs `keystoneauth1` and
   `python-keystoneclient`. It also cannot set `firewall_mode`,
   `log_verbose`, the token durations, `prometheus_metrics_address` or
   anything else in `kerbside/config.py`, and the header says "Local
   changes are overwritten on the next deploy".
   - **A workaround exists.** `kerbside/config.py:20-35` copies each
     INI key into a `KERBSIDE_*` environment variable only when that
     variable is not already set, so the environment wins.
   - **A systemd drop-in survives a redeploy.** The role writes
     `kerbside-api.service` and `kerbside-daemon.service` themselves,
     not their `.d/` directories.
   - **The role will not notice a drop-in change.** Its restart
     detection hashes the code, the configuration it writes, the units
     and the certificate (`ansible/README.md` "Redeploys"), so the
     operator restarts by hand.

   No issue covers this. Step 5b files one.

5. **CI.** The sanity job (`.github/workflows/functional-tests.yml`)
   runs `tools/ci-test-ansible-role.sh`. That is a rootless test of
   `validate`, of `bootstrap` against a stub virtualenv, and of
   `config`, with sources of every type, including an oVirt and a
   static source and no `kerbside_url`. It also runs `register`,
   without the migration or the units, and with a stub readiness
   check. (Planning read the workflow's comment, which said
   `register` was not run; step 5a's review corrected this finding
   and that comment.)
   The same job runs `tools/ci-check-ansible-collection.sh`:
   ansible-lint, a build, and galaxy-importer. `sf-e2e` deploys
   through the role with one `shakenfist` source, co-located, and
   checks that a second deploy restarts nothing. So the role has
   rendered every source type but has started Kerbside only with a
   Shaken Fist source. No lane runs the role on a dedicated host, with
   a static source, or outside Shaken Fist's `site.yml`. The index's
   CI cell for the role is accurate.

6. **What already links where.**
   - `docs/deployment/manual.md:15-19` sends Ansible users to
     `ansible/README.md`.
   - `docs/use-cases/shakenfist.md` "How to set it up" names the
     collection and links Shaken Fist's guide, but no Kerbside page
     about the role.
   - The index row for the role is not a link.
   - `docs/deployment/order.yml` lists `manual.md`, `kolla-ansible.md`
     and `containers.md`. The index table's order is by hand, Ansible
     role, Kolla-Ansible, containers.

## Decisions

1. **One page with the standard four headings, mostly links.**
   - **Who this is for:** the operator who runs Ansible, whether or not
     they run Shaken Fist, and what to read if they do not.
   - **What it deploys:** a short table of what the role creates and
     what it leaves to the operator: the database, the proxy
     certificate, TLS in front of the API, and any setting outside the
     fifteen.
   - **How to use it:** three routes, each to the document that owns it
     (decision 3).
   - **Status and limitations:** the table, with this phase's two gaps
     at its head.

2. **No variable tables, no topology prose.** The collection README is
   the role's reference. It ships in the collection tarball and Galaxy
   renders it. Shaken Fist's guide owns placement beside a cluster. A
   third copy on this page would be the one that drifts, as the master
   plan's inventory row did. The page links both with anchors, and
   names a variable only where it says what the role does with it.
   This is the decision a reviewer is most likely to argue with: the
   docs site does not publish `ansible/`, so a reader on shakenfist.com
   follows a link to GitHub for the reference. I think that is right.
   The README has to stand alone on Galaxy, so it cannot become a stub
   that points here. Two full references would disagree within a
   release.

3. **Three routes.**
   - **Shaken Fist deploys Kerbside:** add hosts to the `kerbside`
     group, and `site.yml` runs the role. Link Shaken Fist's guide.
   - **Your own playbook:** the README's minimal playbook and its
     `validate`-first rule, by link, for a front-door Kerbside with any
     mix of sources.
   - **A Shaken Fist cluster joining a Kerbside you deploy yourself:**
     Shaken Fist's "Bring your own", by link. Kerbside's side is route
     2 with a `shakenfist` source.

   Route 2 is the front door the master plan recommends. The page says
   which sources the role can deploy today (decision 4).

4. **State the gaps on the day, and correct the README.** The page's
   limitations table leads with two rows:
   - **#602:** the role installs no oVirt or OpenStack client library,
     so those sources do not work.
   - **The step 5b issue:** the role writes no setting outside the
     fifteen, so there is no Keystone login, firewall mode or verbose
     logging.

   `ansible/README.md`'s "works with any console source type" becomes
   a sentence that says it validates and renders all four, and links
   #602 for the two whose clients it does not install. The index's
   sources cell stays as it is, and gains the step 5b issue only if
   the cell already names gaps. It does: "until #602".

5. **Document the drop-in workaround, marked untested.** For a setting
   outside the fifteen, the page shows a systemd drop-in on each unit
   that reads the setting. It uses `EnvironmentFile=` naming a
   root-owned, mode-0600 file, never `Environment=`, because
   `systemctl show` prints `Environment=` to any local user and a
   Keystone service password would be among them. The page says:
   - why it works (`kerbside/config.py`'s precedence);
   - that a redeploy keeps it;
   - that the role will not restart Kerbside when it changes;
   - that nothing tests it.

   The alternative, "not possible until the issue is fixed", leaves
   OpenStack operators who use the role with no login at all. The
   by-hand page already uses a drop-in, in step 9.

6. **Cross-links and order.** `manual.md`'s "If you can run Ansible"
   paragraph links the new page in place of the README. The page links
   the README in turn. In `use-cases/shakenfist.md` "How to set it up",
   one link to the new page beside Shaken Fist's guide. `order.yml`
   gains `- ansible.md: Ansible` after `manual.md`, matching the index
   table. Nothing else moves.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | opus | none | Write `docs/deployment/ansible.md`. Add `- ansible.md: Ansible` to `docs/deployment/order.yml` after `manual.md`, and make the Ansible role row of `docs/index.md` a link to `deployment/ansible.md`, changing nothing else in that row. Correct `ansible/README.md`'s second paragraph per decision 4. Make decision 6's two cross-links. **Read first:** this plan, all of it. For form and tone, `docs/deployment/manual.md`, `kolla-ansible.md` and `containers.md`: the four `##` headings in order (`## Who this is for`, `## What it deploys`, `## How to use it`, `## Status and limitations`), the last a `Not covered \| Why` table. Then `ansible/README.md`, `ansible/roles/kerbside/` (`meta/argument_specs.yml`, `tasks/*.yml`, `templates/*`), `kerbside/config.py` (lines 1-60 and the settings), and `tools/ci-test-ansible-role.sh`. Read the sanity job's ansible steps in `.github/workflows/functional-tests.yml` and the sf-e2e section of `docs/testing.md`. For Shaken Fist, read `git -C ../shakenfist show origin/develop:docs/operator_guide/kerbside.md` and `...:docs/operator_guide/installation.md` (its Kerbside variables), read only. **What it deploys:** check each row against the tasks, not the README. **How to use it:** decision 3's three routes, linking with anchors: the README on GitHub (`https://github.com/shakenfist/kerbside/blob/develop/ansible/README.md#...`), and Shaken Fist's guide at its published URL (`https://shakenfist.com/operator_guide/kerbside/`, as `docs/use-cases/shakenfist.md` links it). Then a `###` for settings the role does not write, per decision 5: list which settings it does write, from `templates/kerbside.ini`; show the drop-in for both units with `EnvironmentFile=`; say that variable names are `KERBSIDE_` plus the upper-cased INI key; give one Keystone example using the `keystone_*` names in `kerbside/config.py`; and say the four things decision 5 lists. Link the step 5b issue as `#TBD`; the management session fills it in. **Status and limitations:** at least #602; the settings gap; `register` runs in CI only with a Shaken Fist source, co-located (finding 5); no lane runs the role outside Shaken Fist's `site.yml`; no lane runs two Kerbside hosts, linking Shaken Fist's load-balancing section; and the login gap (#300) as the other pages state it. Do not copy the README's variable tables or Shaken Fist's topology prose (decision 2). State backend TLS only conditionally, as `rust/kerbside-proxy/src/backend.rs` behaves. **Checks:** run `python3 tools/check-backend-tls-claims.py`, `timeout 900 python3 tools/mutate-backend-tls-claims.py`, `timeout 900 tox -epy3 -- kerbside.tests.unit.test_docs_links`, and `pre-commit run --all-files` after `git add -N` on the new page. Check every `#anchor` by hand, because the link test strips anchors (#490); for anchors in Shaken Fist's guide, use the headings in its source. Commit subject: "Document deploying Kerbside with Ansible." |
| 5b | low | (management session) | n/a | File one issue: "The ansible role cannot set kerbside.ini settings beyond the fifteen it writes". The issue is published, so confirm its wording with Mikal first. Body: `templates/kerbside.ini` renders a fixed dict of fifteen settings, with no pass-through, and overwrites local changes. So a role-deployed Kerbside cannot configure a Keystone login (`keystone_*`), even once #602 installs its libraries, or `firewall_mode`, `firewall_permitted_channels`, `log_verbose`, `api_token_duration`, `console_token_duration`, `prometheus_metrics_address` and the rest of `kerbside/config.py`. The workaround is a systemd drop-in with `EnvironmentFile=`, because environment variables win over the INI file (`kerbside/config.py:20-35`), but the role's restart detection does not see it. Suggest a `kerbside_extra_settings` dict, validated against `config.py`'s field names, with secrets `no_log`. Link #602 and `docs/deployment/ansible.md`. Then replace `#TBD` in the page with the issue's number, and record the URL in the execution notes. |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The page copies the README's variables or Shaken Fist's topologies, and drifts from them. | Decision 2. The management session reads the diff for any variable table or topology paragraph. The definition of done counts lines that name a `kerbside_` variable. |
| The drop-in example is wrong, and an operator's Kerbside does not start, or a secret leaks. | Decision 5 requires `EnvironmentFile=` and a 0600 file. The management session checks the variable names against `kerbside/config.py` and the precedence against `load_ini_settings`. The page says nothing tests it. |
| The README correction reads as if the role now refuses oVirt and OpenStack sources. | It does not refuse them. The sentence says they validate and render, but their clients are not installed. The management session checks the wording against `bootstrap.yml`. |
| Shaken Fist's guide changes its headings, and the anchors break. | The link test cannot see other repositories' anchors. The page links that guide's page, plus at most two anchors, each checked on the day. |
| #602 is fixed while this phase is open. | Re-check #602's state before committing 5a, and again in the definition of done. If it has closed, the page and the README follow the code on the day, and the index cell changes with them. |

## Definition of done

- [x] `docs/deployment/ansible.md` exists with exactly the four `##`
      headings, in order: `grep '^## ' docs/deployment/ansible.md`.
- [x] `docs/deployment/order.yml` lists `manual.md`, `ansible.md`,
      `kolla-ansible.md` and `containers.md`, in that order, and
      nothing else.
- [x] The Ansible role row of `docs/index.md` links
      `deployment/ansible.md`, and its other cells are unchanged:
      `git diff origin/develop -- docs/index.md` shows that row only.
- [x] The page has no variable table. At most ten lines name a
      `kerbside_` variable:
      `grep -c 'kerbside_[a-z_]*' docs/deployment/ansible.md`.
- [x] `ansible/README.md` no longer says "works with any console
      source type", and links #602.
- [x] The page links #602, the step 5b issue (no `#TBD` remains), the
      collection README and Shaken Fist's Kerbside guide.
- [x] The drop-in example uses `EnvironmentFile=` and no
      `Environment=` line with a value: `grep -n '^Environment='
      docs/deployment/ansible.md` prints nothing. Every variable it
      names is a `KERBSIDE_` field in `kerbside/config.py`.
- [x] The page's list of settings the role writes matches
      `ansible/roles/kerbside/templates/kerbside.ini`, setting for
      setting. Checked by reading.
- [x] `manual.md` links `ansible.md`, and `use-cases/shakenfist.md`
      "How to set it up" links it.
- [x] Every anchor in the changed pages resolves, including those in
      Shaken Fist's guide. Checked by hand, because the link test
      strips anchors (#490).
- [x] `tools/check-backend-tls-claims.py` exits 0, and
      `tools/mutate-backend-tls-claims.py` still reports every mutation
      caught.
- [x] `tox -epy3`, `tox -eflake8` and `pre-commit run --all-files`
      pass.
- [x] The step 5b issue exists, and its URL is in the execution notes.

## Execution notes

- Back brief answered on 2026-10-28 (AEDT). Mikal kept decisions 2
  and 5, and approved step 5b's issue as worded.
- Step 5b ran before step 5a, so that the page could link the issue's
  real number rather than `#TBD`. It was filed on 2026-10-28 as #629:
  https://github.com/shakenfist/kerbside/issues/629
- The review of step 5a found finding 5 wrong: the rootless role test
  runs `register`, without the migration or the units. The sanity
  job's comment in `.github/workflows/functional-tests.yml` said it
  did not, which is where planning got it. Both are corrected, the
  comment in its own commit.
- Decision 4 said the index's sources cell would gain #629. It did not:
  that cell is about which sources the role can deploy, and #629 is
  about settings, so the definition of done's "other cells unchanged"
  stands.
- With every mechanism in the index now linked, its sentence
  "Mechanisms whose name is not a link are planned rather than written"
  has no case left. Phase 6 reconciles `docs/index.md`, so it is left
  for that phase.
- Definition of done checked on 2026-10-28. `tox -epy3` passed 571
  tests, and flake8, pre-commit and the TLS claim guard passed. The
  mutation tool caught all 12 mutations. All 38 local anchors in the
  changed pages resolve, and the README's and Shaken Fist's guide's
  anchors match their headings on the day. One line of the page names
  a `kerbside_` variable. #602 and #629 are both open.
- The first automated review (2026-10-29) found that #618, the Proxmox
  VE source, had landed on `develop` after this branch was cut. The
  role now validates five source types, and a `proxmox` source needs
  no client library. The branch was rebased onto it, and the page, the
  README and the index's sources cell now name Proxmox. That cell
  changed after all, against the definition of done's "other cells
  unchanged", because the role's sources changed under it. The page
  also gained a row saying no lane starts a Proxmox source through the
  role.

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of the plan and how the work you intend to do
aligns with it. Three points need Mikal's answer before 5a runs:

- **Decision 2:** link the collection README and Shaken Fist's guide
  rather than restating either.
- **Decision 5:** document the drop-in workaround, marked untested.
- **Step 5b's issue wording**, which is published.
