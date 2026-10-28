# Deployment options phase 3: OpenStack, by Kolla-Ansible and in front of it

Master plan: [PLAN-deployment-options.md](/components/kerbside/plans/PLAN-deployment-options/).

## Planning effort

The master plan asks for high-effort planning, because the
front-door path has never been written down and nothing in CI
exercises it. This plan was written in a low-effort session. The
survey was not cut short: every claim below was checked against
this tree, `kerbside-patches`, the OpenStack Gerrit, or the Nova
and Kolla-Ansible sources on the day. A review at high effort
before step 3a starts is still worth having, and the back brief
asks for one.

Review effort: high. Both pages make claims about other projects'
configuration that no test checks.

## Scope

In scope:

- `docs/deployment/kolla-ansible.md`, the second page in the
  section, and its entry in `docs/deployment/order.yml`.
- The front-door path: Kerbside, deployed by any mechanism, in
  front of an OpenStack cloud. It covers the Nova settings
  `spice-direct` needs, the routable console address, and the
  Keystone account and group an operator creates by hand. It goes
  in `docs/use-cases/openstack.md`'s "How to set it up", not on
  the Kolla-Ansible page (decision 1).
- Moving `openstack.md`'s "Deployment" paragraph, its upstream
  status table and its "Deployment support is not upstream yet"
  limitation to the new page, leaving a link.
- Two stale claims in `openstack.md` that #605 made false, since
  this phase rewrites the sections that carry them (finding 2).
- The `docs/index.md` cells this phase owns: the Kolla-Ansible
  row of Deployment Options becomes a link, and the OpenStack row
  of Use Cases stops naming a deployment mechanism (decision 6).
- One comment on kerbside-patches#1859, recording what the survey
  found about the upstream stack (step 3c).

Out of scope:

- `installation.md`'s "Deploying for real" table, whose OpenStack
  row still points at kerbside-patches. Phase 6 replaces that
  table.
- Any change in `kerbside-patches` or upstream. Restacking 967801
  (finding 4) and correcting that repository's README (finding 3)
  are its own work, tracked by kerbside-patches#1859.
- A least-privilege Keystone role. Nova's policy makes validating
  a console token admin-only by default (finding 7). Building a
  narrower role is engineering, not documentation, and the pages
  keep saying it is untried.
- A CI lane for the front-door path. It is already the master
  plan's Future work, as "one Kerbside in front of two sources",
  and this phase's pages say no lane runs it.

## What the survey found

Checked on 2026-10-27. The master plan's claims that were wrong
are corrected at source in this phase's planning commit, as
noted under each finding. A step does not need to redo them.

1. **`openstack.md` has no "Deployment" section.** The master
   plan's phase 3 paragraph says that section "shrinks to a
   link". The page's deployment material is a bold
   **Deployment.** paragraph inside `### OpenStack side`, under
   `## How to set it up` (`docs/use-cases/openstack.md` around
   lines 237-273). It carries the upstream status table, dated
   2026-09-27, and the advice to take the deployment from
   kerbside-patches. The rest of "How to set it up" (Nova,
   Guests, Account, Network, Kerbside side) is already most of
   the front-door path, but it is written around Kolla-Ansible:
   it gives the base URL "in the Kolla-Ansible deployment", and
   describes the account as one "the Kolla-Ansible role
   registers". The master plan's phase paragraph is corrected.

2. **#605 moved the OpenStack lane to nightly on 2026-10-25,**
   overtaking the master plan's question 2 ("it stays in the merge
   queue"). Commit f303061 gives the reasons: capacity and
   duplicate runs rather than flakiness. `openstack.md` now
   contradicts itself. Its limitations row says nightly, but two
   sentences still say merge queue: "it runs in the merge queue
   rather than on pull requests" in "A worked example" (line 374),
   and "exercised on every merge-queue entry" in "Status and
   limitations" (line 462). The master plan's question 2 and its
   Situation paragraph about a stale rebase breaking Kerbside's
   merges are corrected. The two page sentences are step 3b's.

3. **kerbside-patches' README says the same stale thing.** Its
   Status section says Kerbside's CI exercises the exchange "on
   every merge". That repository is out of scope here. Step 3c
   records it on kerbside-patches#1859.

4. **The enablement change is stacked on the deployment change.**
   Question 1's decision was to keep pushing the OpenStack-side
   enablement changes (967801) and freeze the Kerbside-deployment
   changes (976889 onwards). In Gerrit, 967801's parent is
   976889's commit. So as the stack stands, the routable console
   address cannot merge before the change that deploys Kerbside,
   and the decided line cannot take effect until 967801 is
   restacked onto master. The Keystone account "that does not
   require Kerbside to be Kolla-deployed", which question 1 also
   names, is not a change anywhere. kerbside-patches#1859, which
   was to record the split there, is still open. The master
   plan's question 1 gains a note. The page states the upstream
   position as it is, not as decided (decision 3).

5. **The upstream statuses have not moved since the page's
   snapshot.** Queried on 2026-10-27:

   | Change | Status | Updated |
   |--------|--------|---------|
   | kolla 975495 (image build) | Merged | 2026-06-08 |
   | kolla-ansible 967800 (SPICE options) | Merged | 2026-04-03 |
   | kolla-ansible 967802 (require secure) | Merged | 2026-05-26 |
   | kolla-ansible 976889 (deploy Kerbside) | Open | 2026-10-23 |
   | kolla-ansible 967801 (routable address) | Open | 2026-10-23 |
   | kolla-ansible 988189 (CI scenario jobs) | Open | 2026-10-23 |
   | kolla-ansible 988913 (Nova's Tempest test) | Open | 2026-10-23 |
   | kolla-ansible 989614 (Kerbside's Tempest test) | Open | 2026-10-23 |

   The `spice-direct-consoles` topic has gained one change,
   kolla-ansible 1005458, "Collect etcd logs with Fluentd". It is
   stacked on the Kerbside series to show that the JSON log parser
   generalises, and deploys nothing of Kerbside's.

6. **The live Kolla-Ansible role is `patch147`, not `patch014`.**
   `kerbside-patches/_patches/patch014-deploy-kerbside-container.patch`
   is a 2025 relic which authenticates Kerbside as the cloud's
   admin user. `kolla-ansible-kerbside/ORDER` applies
   `patch147-kolla-ansible-master-kolla-ansible-kerbside-role.patch`,
   and `openstack.md`'s account claims match it. It:
   - registers a `kerbside` user in the `service` project with the
     `admin` role (`kerbside_ks_users`);
   - creates a Keystone group `kerbside`;
   - writes `keystone_service_auth_*` as that user, and
     `keystone_access_group = kerbside`, into `kerbside.ini`;
   - writes one `type: openstack` source using the same account,
     with `verify` set to `openstack_cacert` when there is one;
   - sets `spice_direct_proxy_base_url` in `nova-cell`'s
     `nova.conf` when `enable_kerbside` is set, and serves the API
     under uwsgi behind HAProxy.

   It also carries its own operator guide,
   `doc/source/reference/compute/kerbside-guide.rst`. Read
   `patch147`, never `patch014`, for anything the page says the
   role does.

7. **What Nova and stock Kolla-Ansible provide.**
   - Nova 2025.1's `nova/conf/spice.py` has both options the
     front door needs. `spice_direct_proxy_base_url` defaults to
     `http://127.0.0.1:13002/nova`. `server_proxyclient_address`
     is also there.
   - Nova's `nova/policies/console_auth_tokens.py` makes reading a
     console token `base.ADMIN`, which is why the account needs
     the admin role.
   - Stock Kolla-Ansible master's `nova-cell/templates/nova.conf.j2`
     writes a `[spice]` section when `nova_console` is `spice`
     (967800, merged). It sets `server_proxyclient_address` only
     inside its HTML5 branch, which is the gap 967801 closes. It
     never sets `spice_direct_proxy_base_url`.

   So a Kerbside in front of a stock Kolla-Ansible cloud needs
   both options set through Kolla's configuration override. That
   is the most useful thing the Kolla-Ansible page can tell an
   operator who already runs Kolla (decision 4). Nothing tests it.

8. **What the front door needs from Kerbside's own settings.**
   There are two separate Keystone uses:
   - The `type: openstack` entry in `sources.yaml` holds the
     credentials that validate console tokens (needs admin).
   - `kerbside.ini`'s `keystone_*` settings serve interactive
     login: the service account that checks group membership, and
     `keystone_access_group`, default `kerbside`
     (`kerbside/config.py:84-125`, `docs/configuration.md` "Keystone
     Settings").

   Kolla uses one account for both. `KEYSTONE_SERVICE_AUTH_PROJECT`
   defaults to `admin`, where Kolla sets `service`. The token
   exchange endpoint itself needs no login (`openstack.md`'s
   limitations table).

9. **Inbound links into `openstack.md`** are `#value-proposition`
   (`console-sources.md:250`) and `#network`
   (`network-ports.md:86`). Neither section moves.

10. **Scaffolding is ready.** `docs/deployment/order.yml` exists
    with one entry. `tools/check-backend-tls-claims.py` scans
    `docs/deployment/*.md`, and its mutation tool knows that.
    `docs/testing.md` has the anchors the pages will link,
    `#ci-tiers` and `#tempest-tests-against-a-kolla-ansible-deployment`.

## Decisions

1. **The front-door path goes in `use-cases/openstack.md`, not on
   the Kolla-Ansible page.** The master plan puts both on
   `kolla-ansible.md`. But the front-door configuration (Nova's
   two settings, the routable address, the Keystone account and
   group, the network) is the same however Kerbside is deployed:
   by hand, by the ansible role, or in a container. An operator
   deploying Kerbside by hand in front of OpenStack would not look
   under "Kolla-Ansible" for it. It is a property of the cloud
   Kerbside sits in front of, which is what the use-case page
   describes, and that page's "How to set it up" already holds
   most of it. Putting it on the Kolla page would rebuild exactly
   the conflation of use case and mechanism that the master plan
   exists to undo. This is the decision a reviewer is most likely
   to dispute, because it departs from the master plan's text. The
   master plan's phase paragraph is corrected to match.

2. **`kolla-ansible.md` is a mechanism page with the standard four
   headings,** and it holds:
   - what kerbside-patches gives you (from `patch147`);
   - the upstream status table, moved from `openstack.md` and
     re-checked on the day the step runs;
   - the maintained-by-rebase stance;
   - the Debian-only container constraint from kerbside-patches'
     README.

   It links kerbside-patches and `patch147`'s own guide for the
   steps rather than restating them. Kolla-Ansible's own
   documentation owns the steps.

3. **The upstream position is stated as it stands.** The page
   says plainly that the Kolla-Ansible deployment is maintained by
   rebase in kerbside-patches and is not being actively pushed
   upstream. It records the enablement/deployment split question 1
   decided. It also says that 967801 is currently stacked on
   976889, so the split has not yet taken effect. It does not
   predict upstream outcomes. Change 1005458 is left out of the
   table: it is not Kerbside's deployment.

4. **The Kolla-Ansible page also covers a Kerbside in front of a
   stock Kolla-Ansible cloud,** as a `###` under "How to use it".
   The cloud runs `nova_console: spice`, and both Nova options
   come from Kolla's configuration override. That is what a Kolla
   operator who does not take the kerbside-patches role needs,
   and given the rebase-only stance it is the path most of them
   should take. The page states the Kolla-specific mechanics: the
   override file, and which hosts' `nova.conf` need the options.
   It links `openstack.md` for what the options mean and for the
   Keystone account. The step verifies the override path, and
   which Nova services read each option, against the sources
   rather than from memory. The page says no lane runs it.

5. **The hand-made Keystone account matches the Kolla role's
   shape:**
   - a `kerbside` user in the `service` project with the `admin`
     role;
   - a `kerbside` group for interactive login;
   - one account for both the source and `kerbside.ini`.

   Any difference has a reason given. The commands are
   python-openstackclient's, checked against its documentation.
   The page keeps saying that no least-privilege role has been
   tried, and why: Nova's policy says admin.

6. **`docs/index.md`:** the Kolla-Ansible row of Deployment
   Options links the new page. The OpenStack row of Use Cases,
   "deployed alongside the cluster with Kolla-Ansible via
   kerbside-patches", loses its deployment clause, because a
   use-case row that names one mechanism is the conflation this
   plan undoes and the Deployment Options table now carries that
   fact. Every other row, and `installation.md`, is left to phase
   6.

7. **`openstack.md`'s "A worked example" and its Kolla-Ansible
   scenario-job bullets stay where they are.** They describe what
   CI proves about the OpenStack use case, which is the use-case
   page's business. The Kolla page links them. Step 3b fixes only
   their stale merge-queue sentence.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Write `docs/deployment/kolla-ansible.md`, add `- kolla-ansible.md: Kolla-Ansible` to `docs/deployment/order.yml` after `manual.md`, and move the Kolla-Ansible deployment material out of `docs/use-cases/openstack.md`. Read first: this plan (survey findings 1, 4-7, and decisions 2-4, 6), `docs/deployment/manual.md` (the page format and tone, and its four `##` headings in order: `## Who this is for`, `## What it deploys`, `## How to use it`, `## Status and limitations`, the last with a `Not covered \| Why` table), and all of `docs/use-cases/openstack.md`. In `kerbside-patches` (`../kerbside-patches`, read with `git show origin/develop:<path>` after `git fetch`; never edit or check out in that clone), read `README.md`, `kolla-ansible-kerbside/ORDER` and `_patches/patch147-kolla-ansible-master-kolla-ansible-kerbside-role.patch`, including the `kerbside-guide.rst` it adds. Never use `patch014`, which is a dead relic. **Who this is for:** an operator already running Kolla-Ansible who wants Kerbside as one more control-plane service, and is content to take a patched Kolla-Ansible from kerbside-patches. It is not for an operator who wants Kerbside in front of several clouds: link `../use-cases/multi-cloud.md`, and the `###` in decision 4. **What it deploys:** from `patch147` only. List the containers, the database, the Keystone user, role and group, the certificates, the `nova.conf` setting, HAProxy and the uwsgi API, and fluentd. Say what it leaves to the operator. **How to use it:** link kerbside-patches and the guide for the steps; state the Debian-only container constraint from its README; then a `###` for a Kerbside in front of a stock Kolla-Ansible cloud, per decision 4. Verify in kolla-ansible master (`git clone --depth 1 https://opendev.org/openstack/kolla-ansible` into your scratch directory; read only, run nothing from it): `nova_console: spice`, the `[spice]` section in `ansible/roles/nova-cell/templates/nova.conf.j2`, and the documented `nova.conf` override path and merge order. Verify which Nova services read `spice_direct_proxy_base_url` and `server_proxyclient_address` in Nova stable/2025.1 (`nova/conf/spice.py`, and grep `nova/` for `spice_direct_proxy_base_url`; clone it shallow the same way). Also check what Nova appends to the base URL, against Kerbside's route in `kerbside/api.py`, so that the value you give resolves to `/nova-console.vv`. Then the upstream position, per decision 3: the table moved from `openstack.md`, its statuses re-queried on the day (`curl -s https://review.opendev.org/changes/<n> \| tail -n +2` gives JSON), dated, with the live topic link kept; the maintained-by-rebase stance; the question 1 split; and the 967801-on-976889 stacking, re-checked through `/changes/967801/revisions/current/commit`. **Status and limitations:** at least that Kolla-Ansible cannot deploy Kerbside without kerbside-patches; that the OpenStack lane is nightly-tier and all-in-one (link `../testing.md#ci-tiers` and `../use-cases/openstack.md#a-worked-example`); that the account has `admin`; Debian-only images; and that the stock-Kolla front door is untested. In `openstack.md`, replace the **Deployment.** paragraph and its table with two or three sentences that link `../deployment/kolla-ansible.md`. Move the "Deployment support is not upstream yet" limitations row to the new page, and leave nothing in `openstack.md` that restates it. Do not touch the rest of "How to set it up": step 3b rewrites it. In `docs/index.md`, make the Deployment Options row's "Kolla-Ansible (kerbside-patches)" a link to `deployment/kolla-ansible.md`, and change the Use Cases OpenStack row per decision 6. State backend TLS only conditionally, as `rust/kerbside-proxy/src/backend.rs` behaves. Run `python3 tools/check-backend-tls-claims.py`, `tox -epy3 -- kerbside.tests.unit.test_docs_links` and `pre-commit run --all-files` (after `git add -N` on the new page). Check every `#anchor` you write by hand against the target's headings: the link test strips anchors (#490). Commit subject: "Document deploying Kerbside with Kolla-Ansible." |
| 3b | high | opus | none | Rewrite `docs/use-cases/openstack.md`'s `## How to set it up` so that it is the front-door path, true however Kerbside is deployed (decisions 1 and 5). Read first: this plan (findings 1, 2, 6-9, decisions 1, 5 and 7), the page as step 3a left it, `docs/deployment/kolla-ansible.md` from 3a, `docs/configuration.md` "Keystone Settings", `etc/kerbside.conf.example`'s Keystone section, `docs/console-sources.md#openstack`, and `kerbside/config.py:80-130`. **Nova:** keep the two settings. Replace "in the Kolla-Ansible deployment" with the value for any deployment, in terms of `PUBLIC_FQDN` and the API port. Say which Nova services and hosts need each setting, as verified against Nova stable/2025.1's source (clone it shallow into your scratch directory; read only). Note that `spice_direct_proxy_base_url`'s default points at `127.0.0.1`, so an unset one fails in a recognisable way, and describe how. Also say that SPICE has to be the console type (`[spice] enabled`, `[vnc] enabled = false`), and link the Kolla page for how Kolla sets these. **Account:** replace "The Kolla-Ansible role registers…" with the account an operator creates, as `openstack` CLI commands: the user in the `service` project, the `admin` role on it, the `kerbside` group, and adding the users who may log in to the group. Check every command and flag against python-openstackclient's documentation. Then show where the credentials go: the `sources.yaml` entry, by link to `console-sources.md#openstack`, and `kerbside.ini`'s `keystone_service_auth_*`, `keystone_auth_url` and `keystone_access_group`, by link to `configuration.md`. Say that `KEYSTONE_SERVICE_AUTH_PROJECT` defaults to `admin`, so an account in `service` has to set it. Keep the least-privilege caveat, and give its reason: Nova's `console_auth_tokens` policy is admin-only by default. Say that Kolla-Ansible's role makes the same account, by link. **Network and Kerbside side** need only Kolla-neutral wording where they have any Kolla wording. Do not repeat what `installation.md`, `configuration.md` or `console-sources.md` already say; link them. Fix the two stale merge-queue sentences (finding 2): line ~374 in "A worked example", and line ~462 in "Status and limitations", whose opening paragraph must agree with its own `openstack_matrix` row. Add a limitations row saying no CI lane deploys Kerbside in front of OpenStack other than through Kolla-Ansible, so the front-door path is untested. Do not link plan files from the page. Keep the `#network` and `#value-proposition` anchors (finding 9). State backend TLS only conditionally. Run `python3 tools/check-backend-tls-claims.py`, `tox -epy3 -- kerbside.tests.unit.test_docs_links` and `pre-commit run --all-files`, and check anchors by hand. Commit subject: "Document Kerbside in front of any OpenStack." |
| 3c | low | (management session) | n/a | Post one comment on kerbside-patches#1859. It is outward-facing, so confirm the wording with Mikal first. Draft: the enablement change 967801 is currently stacked on the deployment change 976889 in Gerrit, so the split recorded in Kerbside's `PLAN-deployment-options.md` question 1 cannot take effect until 967801 is restacked onto master. No change exists yet for a Keystone account that does not need Kerbside to be Kolla-deployed. The README's Status section says Kerbside's CI exercises the exchange "on every merge", which has been nightly since shakenfist/kerbside#605. Link the new `docs/deployment/kolla-ansible.md` once it has merged, or the pull request until then. Record the comment's URL in this plan's execution notes. |

Steps 3a and 3b run in sequence. Both edit `openstack.md`, and 3b
links the page 3a writes.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The front-door instructions are wrong in a way nothing catches. No lane runs them. | Every option, command and file path is checked against its source by the step agent, which cites where it checked. The management session re-checks three of them before committing: the Nova base URL suffix, the Kolla override path and one CLI command. Both pages say outright that the path is untested. |
| The upstream status table goes stale again. | It is dated and keeps the live topic link, as it does now. The upstream status lives on one page only after this phase, so there is one place to refresh it. |
| The Kolla page and `openstack.md` drift into saying the same things twice. | Decision 1 sets the boundary: the cloud side is in `openstack.md`, and Kolla mechanics are on the Kolla page. The definition of done checks for the moved table and limitation in their new home only. The management session reads the two pages side by side before committing 3b. |
| A backend-TLS claim creeps into either page unconditionally. | `tools/check-backend-tls-claims.py` scans both pages. It runs in each step and in the definition of done, with its mutation tool. |
| The page takes `patch014` for the role. | Finding 6 and the 3a brief name `patch147` and rule out `patch014`. The management session checks the account claims against `patch147` lines 548-560 and 1262-1300 before committing 3a. |
| Decision 1 is rejected in review. | The back brief asks about it before 3a runs, and 3a's and 3b's boundary is a single paragraph to change if it is. |

## Definition of done

- [x] `docs/deployment/kolla-ansible.md` exists with exactly the
      four `##` headings, in order:
      `grep '^## ' docs/deployment/kolla-ansible.md`.
- [x] `docs/deployment/order.yml` lists `manual.md`, then
      `kolla-ansible.md`, and nothing else.
- [x] The upstream status table appears on the Kolla page and not
      in `openstack.md`:
      `grep -l '| Change | What it does | Status |' docs -r`
      prints only `docs/deployment/kolla-ansible.md`.
- [x] Every status in that table matches a Gerrit query run on the
      day the step ran, and the table carries that date.
- [x] The page states that 967801 is stacked on 976889, re-checked
      on the day.
- [x] Neither page cites `patch014`:
      `grep -rn patch014 docs/deployment docs/use-cases` prints
      nothing.
- [x] `openstack.md` no longer says the OpenStack lane runs in the
      merge queue. `grep -n -i 'merge.queue' docs/use-cases/openstack.md`
      matches only the sentence that says it never runs there.
- [x] In `openstack.md`'s "How to set it up", every mention of
      Kolla-Ansible is in a sentence that links
      `deployment/kolla-ansible.md`. Checked by reading.
- [x] The hand-made account on `openstack.md` names the `service`
      project, the `admin` role and the `kerbside` group, as
      `patch147` does, and says that `KEYSTONE_SERVICE_AUTH_PROJECT`
      has to be set.
- [x] The Nova base URL the pages give ends in a path that
      `kerbside/api.py` routes to the token exchange.
- [x] The Deployment Options row links `deployment/kolla-ansible.md`,
      and the Use Cases OpenStack row does not mention Kolla-Ansible:
      `grep -n '^| \[OpenStack\]' docs/index.md`.
- [x] Every anchor in both pages resolves. Checked by hand, because
      the link test strips anchors (#490).
- [x] `tools/check-backend-tls-claims.py` exits 0, and
      `tools/mutate-backend-tls-claims.py` still reports every
      mutation caught.
- [x] `tox -epy3`, `tox -eflake8` and `pre-commit run --all-files`
      pass.
- [x] The kerbside-patches#1859 comment from step 3c exists, and
      its URL is in the execution notes.

## Execution notes

- Back brief answered on 2026-10-28 (AEDT). Mikal kept decision 1,
  declined a separate high-effort review of this plan, approved
  step 3c's comment as drafted, and gave this session permission to
  push and open the pull request once the definition of done is
  checked.
- Steps 3a and 3b run in sequence, one sub-agent each, because both
  edit `openstack.md` and 3b links the page 3a writes.
- Step 3a found two things the survey did not. Kolla merged 986283,
  which builds `nova-libvirt-spice` on Rocky from a third-party
  rebuild of `qemu-kvm`. kerbside-patches' CI also has weekly Ubuntu
  and Rocky 10 lanes and a two-node lane, so its README's "Debian
  only" is stale too. The page says what Kerbside's own lane runs.
- Step 3a re-queried Gerrit on 2026-10-28: no status had changed
  since the survey, and 967801's parent is still 976889's current
  revision.
- Step 3b found that Kerbside's Keystone login scopes every user's
  token to the `admin` project in the `default` domain, fixed in
  `kerbside/api.py` rather than configurable. A user in the access
  group with no role on that project cannot log in. No page said so.
  `openstack.md` now does, under Account and as a limitation. Whether
  to file it as an issue was put to Mikal at the end of the phase;
  he approved, and it is #612, filed on 2026-10-28 and linked from
  the limitation.
- The definition of done's item on Kolla-Ansible mentions in "How to
  set it up" is read as covering the setup instructions. "A worked
  example" stays as it was, by decision 7, apart from an opening link
  to the Kolla-Ansible page. Its other Kolla-Ansible mentions describe
  CI and are not linked.
- Step 3c posted the approved comment on kerbside-patches#1859 on
  2026-10-28:
  https://github.com/shakenfist/kerbside-patches/issues/1859#issuecomment-6452874522
- Definition of done checked on 2026-10-28. `tox -epy3` passed 556
  tests, and flake8, pre-commit and the TLS claim guard passed. The
  mutation tool caught all 12 mutations, and all 32 local anchors in
  the three changed pages resolve. The table-header grep also matches
  this plan, which quotes the header. Among the pages it matches only
  `kolla-ansible.md`.

## Back brief

Before executing any step of this plan, back brief the operator
on your understanding of the plan and how the work you intend to
do aligns with it. Three points need Mikal's answer before 3a
runs:

- **Decision 1**, which departs from the master plan by putting
  the front-door path in `openstack.md` rather than on the
  Kolla-Ansible page.
- **Whether to review this plan at high effort first,** since it
  was written below the effort the master plan asks for.
- **Step 3c's comment wording**, which is published outside this
  repository.
