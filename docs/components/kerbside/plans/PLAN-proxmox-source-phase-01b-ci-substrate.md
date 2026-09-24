# Proxmox source phase 1b: a Proxmox node in CI

This is the detailed plan for phase 1b of
[PLAN-proxmox-source.md](/components/kerbside/plans/PLAN-proxmox-source/). Read the
master plan first, and read
[the phase 2 plan](/components/kerbside/plans/PLAN-proxmox-source-phase-02-ryll-connect/)
too: this phase exists to replace phase 2's step 2f. It
replaces an operator-assisted run against a private node with
a CI lane that anybody can re-run.

The phase corrects a premise both plans were written on. They
assume a standing, validated PVE 9.2.20 node (master plan
open question 5, phase 2 step 2f and *Validation against a
real node*). There is no such node. The measurements came
from an ephemeral deployment, built by prototype Ansible in
the private `homelab-deployments-lfs` repository and torn
down afterwards. Phase 2 therefore has nothing to validate
against. This phase gives it something, and gives phase 3b's
kerbside lane the same thing later.

## Prompt

Work lands in two repositories other than this one, in this
order:

1. `shakenfist/actions`, checkout at
   `/srv/kasm_profiles/mikal/vscode/src/shakenfist/actions`,
   default branch `main`. **Consumers pin `@main`, so merging
   there is a deploy to the whole fleet** (`AGENTS.md:10-25`
   in that repository). Read its `AGENTS.md`,
   `ARCHITECTURE.md`, `docs/actions.md`, `docs/ansible.md`,
   `docs/ci.md` and `docs/consuming.md` before touching it.
2. `shakenfist/ryll`, on phase 2's branch
   `spice-http-connect`, worktree
   `/srv/kasm_profiles/mikal/vscode/src/shakenfist/ryll-wt-http-connect`.
   Read ryll's `AGENTS.md`, `STYLEGUIDE.md` and `docs/ci.md`,
   particularly *Concurrency* and *Build network isolation*.

The plan lives here in `shakenfist/kerbside/docs/plans/`,
beside its master plan. That is the precedent phase 2 and
PLAN-host-subject-phase-01-ryll-verifier.md set. Nothing in
kerbside's code changes in this phase.

The Ansible being ported is in the private repository
`~/src/private/homelab-deployments-lfs`:
`playbooks/proxmox-9-debian-13.yml`, `roles/proxmox_host/`,
`docs/proxmox.md`, `docs/console-tickets.md` and
`tools/pve-ticket-probe.py`. Sub-agents may read it. They
must not copy anything from it that *What the survey found*
lists as private, and they must never paste a credential
from it into a commit, a plan or a pull request.

Where this plan states Proxmox behaviour, ground it in
Proxmox's own source rather than in this paraphrase. Two
claims below are marked as unverified. Settle them against
`pve-manager` and `pve-common` before building on them.

Planning effort: **high**. The phase publishes a fleet-wide
action that cannot be integration-tested before merge in the
usual way. It handles a credential in public CI logs, and it
is the validation gate for a phase that touches a
connection's identity check.

## Repository and branch logistics

- **actions:** branch `proxmox-substrate` off current
  `main`. One commit per step, 1b.1 to 1b.4. Each commit
  must pass `pre-commit run --all-files` and
  `python3 -m unittest discover -s tests -t .`. Land it by
  pull request. The operator merges, because a merge there
  is a deploy.
- **ryll:** commits on `spice-http-connect`, after phase 2's
  step 2e and before its push audit 2g. They replace 2f. See
  decision 9 for why they go there and not on a branch of
  their own. Each commit must pass `make lint` and
  `make test`.
- **kerbside:** this file, plus the master plan and phase 2
  plan edits that register it. Those are a planning commit
  in this repository, made by the management session.
- **Ordering constraint:** the ryll lane names
  `shakenfist/actions/deploy-proxmox-on-shakenfist@main`, so
  it cannot run until the actions pull request has merged.
  Phase 2's pull request therefore waits on the actions one.
- **Recording:** the master plan's `Merged` cell for 1b
  records `actions <sha> (#pr)`, and notes that the ryll half
  landed in phase 2's merge.
- **Push audit, per repository:**
  - *actions* has no `PUSH-AUDIT.md` (checked against
    `origin/main` at `500b42d`). Per the push-audit shared
    block, step 1b.6 says so and records what was done
    instead.
  - *ryll* has one. Because the lane's commits sit on
    `spice-http-connect`, phase 2's step 2g audits them as
    part of that branch's diff. This phase cites 2g rather
    than running it twice.
  - *kerbside* receives only plan text in this phase. The
    master plan's phase 7 covers it.

## Scope

In:

- A composite action in `shakenfist/actions`,
  `deploy-proxmox-on-shakenfist`. Inside the calling job it
  stands up a single-node PVE 9 on a Debian 13 ShakenFist
  instance, with a SPICE guest, a least-privilege API token
  and a runner that can resolve and reach the node. It is
  ported from the private role, with the private parts
  removed.
- A helper that mints a `.vv` from that node through the API
  token, shipped with the action so that every consumer mints
  the same way.
- The action's own lane in `shakenfist/actions`. It runs on
  pull requests that touch the action, on demand, and weekly
  to catch upstream Proxmox drift.
- Documentation of all of the above in `shakenfist/actions`.
- A ryll lane that builds the branch's ryll and deploys a
  node through the action. It then runs phase 2's positive
  check and its three negative checks against freshly minted
  tickets.
- Recording those results where phase 2's step 2f would have
  put them.

Out:

- The kerbside lane. It consumes the same action, and
  belongs to phase 3b per the operator. See *Future work*,
  and note the discrepancy with the master plan's phase 5
  flagged there.
- PVE clustering, PVE 8, and VNC consoles, as in the master
  plan.
- Making either new lane a required check. Both are
  path-filtered, and a required check that never reports
  blocks a pull request forever. Ryll's `docs/ci.md`
  (*It is deliberately not a gate*, `:468-485`) makes that
  argument for its mermaid lane, and it applies here.
- Fixing the missing fork guard on ryll's existing VM lanes.
  It was found by the survey, and it is future work.
- Proving the least-privileged role set. That is master plan
  open question 4. The substrate makes it measurable, but
  measuring it is not this phase.

## What the survey found

Surveyed 2026-09-24 against actions `origin/main` at
`500b42d`, ryll `origin/develop` at `e11ad22`, kerbside
`develop` at `f63bd40`, and homelab-deployments-lfs at
`0713b0f`. The phase 2 branch was at `9a5d1a9` (step 2a
committed, 2b in progress, not yet pushed).

### How kerbside's lanes get a cloud today

1. **Three shapes, one mechanism.** Each lane creates its
   nested cloud as an instance in the runner's own
   ShakenFist namespace, using the `shakenfist.shakenfist`
   collection's `sf_*` modules on the runner. The difference
   is how much of that is wrapped:
   - `ovirt_matrix` inlines it. It checks out actions and
     shakenfist, runs `tools/install-collection.sh`, then
     `ansible/kerbside-single-node.yml`, then drives the
     guest over ssh with scripts copied out of
     `actions/tools/` (`functional-tests.yml:457-537`).
   - `openstack_matrix` uses
     `setup-kerbside-environment@main` followed by
     `deploy-kolla-ansible@main` (`:959-960`, `:1095`).
   - `sf-e2e` uses `setup-test-environment`,
     `build-smoke-cluster` and
     `deploy-kerbside-on-shakenfist`
     (`sf-e2e-functional.yml:99-134`).

   The runner reaches the nested instance because the
   playbook's CI block adds the test network to the runner
   and DHCPs on it (`kerbside-single-node.yml:65-116`). That
   block keys on `identifier` being the runner's own
   hostname, `SHAKENFIST_NAMESPACE=$(hostname)`
   (`functional-tests.yml:457-461`).
2. **The runner label sizes the runner, not the nested
   node.** The nested instance's size is fixed by the
   playbook: 12 vCPU, 16 GB and 400 GB for kolla and oVirt
   (`kerbside-single-node.yml:57-63`,
   `kerbside-create-instance.yml:2-9`).
   - PVE's 4 vCPU, 8 GB and 40+60 GB is likewise an
     `sf_instance` argument
     (`homelab playbooks/proxmox-9-debian-13.yml:94-99,127-136`).
     No runner label asks for it.
   - The runner still needs a size. A `vm` runs-on with no
     size silently becomes `xs`: 1 vCPU and 2048 MB
     (actions `AGENTS.md:90-98`). An ansible deploy driven
     from the runner needs at least `s`.
   - Kerbside's lanes run `sanity_checks` and
     `openstack_matrix` on `m`, and `ovirt_matrix`, `sf-e2e`
     and `direct-qemu` on `l`. The `l` lanes are the ones
     that also build Rust on the runner
     (`functional-tests.yml:195,411-415,941`;
     `sf-e2e-functional.yml:86`;
     `direct-qemu-functional.yml:83`).
3. **Nested KVM works on this fabric already.**
   - The oVirt lane runs VMs on a hypervisor that is itself
     a ShakenFist instance, and checks `/dev/kvm` and
     `vmx`/`svm` there (actions
     `tools/ovirt-prepare-host.sh:106-110`).
   - Kolla's nova defaults to `virt_type: kvm`
     (kerbside-patches `etc/globals-master.yml:631`, left
     commented).
   - The homelab node saw `kvm_amd: Nested Virtualization
     enabled` (`docs/proxmox.md:313-314`). The role falls
     back to TCG when `/dev/kvm` is missing
     (`roles/proxmox_host/tasks/smoke.yml:28-39`).
4. **Kerbside has already solved "the certificate names a
   host the runner cannot resolve".** The oVirt lane appends
   `10.0.2.2 ovirt.local` to the runner's `/etc/hosts`
   (`functional-tests.yml:716-728`). It also puts the name
   and the address in `no_proxy`, because the runner image
   exports `http_proxy` and `https_proxy` for a squid cache
   that cannot route to the test network (`:418-428`). A
   Proxmox lane needs both, for the node's FQDN.
5. **A default-branch checkout would defeat a self-test.**
   `setup-kerbside-environment` checks out
   `shakenfist/actions` at its default branch
   (`action.yml:33-38`) and runs playbooks from there
   (`:114-116`). So even a relative `uses: ./...` self-test
   of that action would exercise `main`'s playbooks, not the
   pull request's. `review-pr-with-claude` avoids this by
   resolving its script through `github.action_path`
   (`review-pr-with-claude/action.yml:39`). That is the
   precedent decision 2 follows.

### How ryll's CI works today

6. **Ryll runs on the same fleet, and already consumes
   shakenfist/actions.**
   - Its Linux jobs use `[self-hosted, vm, debian-13-docker,
     l]` (`ci.yml:83,115,138`).
   - It calls `pr-auto-review.yml@main` (`ci.yml:602`) and
     `pr-bot-trigger@main` (`pr-retest.yml:38`).
   - The fleet runner images carry `ansible`
     (actions `ansible/ci-image.yml:149-160`), which on
     Debian includes `community.general` and `ansible.posix`,
     both used by the role.
   - The `-docker` images are the same playbook with
     `extras: docker` (actions `docs/ansible.md:385-392`).

   Ryll has no ShakenFist-cluster lane and no
   integration lane against any SPICE server.
   `make test-qemu` (`Makefile:440-457`) is a manual target:
   a local qemu with `disable-ticketing=on`, plaintext, and
   nothing in CI starts it. Ryll's workflows read no secret
   beyond `GITHUB_TOKEN`. Its authentication to the fabric
   would come from the runner image and conductor, as
   kerbside's does, with `/srv/github/id_ci` and the
   `sf_*` credentials behind `/home/debian/ansible-hosts`.
   **That ryll's runners carry working ShakenFist
   credentials is a premise nothing in these repositories
   can confirm.** The conductor is in private-ci, which is
   not checked out here. Step 1b.10's first run settles it.
7. **Ryll's VM lanes carry no fork guard.**
   `grep head.repo` over ryll's `ci.yml` finds nothing. By
   contrast, actions tests that every VM lane in its `ci.yml`
   compares `github.event.pull_request.head.repo.full_name`
   against `github.repository`, and says why: these runners
   hold `/srv/github/id_ci`, the key to every node in the CI
   mesh (`tests/test_workflow_references.py:419-436`). A
   lane that creates cloud instances with fleet credentials
   must carry the guard. The existing ryll lanes are out of
   scope here and are recorded under *Future work*.
8. **The `l` pool is the scarcest resource on the fleet.**
   It runs `MAX_WORKERS = 6` across every repository, and
   ryll's `docs/ci.md` requires a job-level concurrency
   block on anything a pull request can trigger
   (`docs/ci.md:694-745`).

### What in the homelab role is private, and what is not

9. **Private, and not to be ported:**
   - `homelab_ci_ssh_key` and its `~/.ssh/id_general`
     default (`playbook:40,137,158,184,198`).
   - The `shakenfist_instance` preflight role and the
     `shakenfist_actions_dir` / `shakenfist_src_root` checks
     (`playbook:51-76`), which exist only because the
     homelab repository borrows the actions readiness gate
     from outside.
   - A fixed root password, `proxmoxproxmox`
     (`defaults/main.yml:44`, `tasks/install.yml:205-219`).
     It is harmless on a private fabric and wrong in a
     public repository. Nothing in CI logs into the web UI.
   - The report play, which prints the root password and the
     API token secret (`playbook:209-231`). In a public
     repository's CI log that would publish the credential.
   - The resolver check, which copies
     `{{ homelab_files_dir }}/tools/check-guest-resolver.py`
     from the private tree (`tasks/network.yml:199-216`).
   - The example FQDNs and namespace in the docs
     (`pve1.<namespace>.sfcbr`, `docs/proxmox.md:144,148`).

   **Generic, and worth porting as validated:**
   - `install.yml`: hostname pinning, `/etc/hosts`, the
     cloud-init preserve fragment, the kernel then reboot
     then `proxmox-ve` order, postfix preseed, and removal of
     the Debian kernel.
   - `disable-enterprise-repos.yml`, including its
     anchored-regex fix.
   - `storage.yml`, the LVM-thin pool.
   - `api.yml`, the user, ACL and `privsep=0` token.
   - `smoke.yml`, the cirros guest with `--vga qxl` and the
     KVM fallback.
   - The bridge half of `network.yml`, created through
     `pvesh`.

   The fleet cache addresses are not a secret. The public
   actions playbooks already write the squid proxy at
   `192.168.1.15:3128` and the devpi mirror
   (`docs/ansible.md:238-251`). Pointing the node's apt at
   the squid is therefore precedent, not a leak.
10. **The measurements never exercised two things a real
    client does.** `pve-ticket-probe.py` mints by ssh-ing to
    the node and running `sudo pvesh create
    .../spiceproxy` as root (`tools/pve-ticket-probe.py:40-47`),
    not through the API token. It then dials `--host <floating
    IP>` and only *prints* the advertised proxy URL
    (`:90,103,122,125`; usage at
    `docs/console-tickets.md:74-77`). So:
    - the API token and its ACL were created
      (`tasks/api.yml`), but no ticket was ever minted with
      them;
    - no client ever resolved the node's FQDN from outside
      the node.

    A `.vv` client dials the `proxy=` URL by name, and
    kerbside's driver will mint with the token. Both paths
    are first exercised by this phase. That is a reason to
    exercise them, not to route around them (decisions 4 and
    5).

### Shape, ticket and FQDN

11. **Actions precedent is composite actions plus flat
    playbooks, not roles and not reusable workflows, for
    anything that must share a job with the consumer.**
    - Actions has `ansible/*.yml`, `ansible/tasks/` and
      `ansible/vars/`, and no `roles/` directory.
    - `ARCHITECTURE.md:218-222` explains the split. A
      composite action puts a live cluster *inside the
      consumer's job*. A reusable workflow is for consumers
      that only want the standard suite.
    - A Proxmox consumer must mint and connect within about
      30 seconds of each other, so its client and the mint
      must share a job. Only the composite shape allows
      that.
    - Two tests will see the new playbook:
      `test_ansible_readiness.py` requires every play that
      creates an instance to be followed by a readiness play
      targeting the added hosts, and it names the full set
      of creating playbooks (`:439-461`), which must be
      edited. `tools/ansible-syntax-check.sh` finds
      playbooks by their `hosts:` line.
    - `test_vip_reservation.py` reads its playbook set out of
      `setup-kerbside-environment` (`:54-62,140-148`), so a
      playbook not named `kerbside-*` and not run by that
      action is outside it, as it should be.
12. **The FQDN has three sources, and they must agree.**
    - The role takes the domain from the fabric's resolver
      search list (`defaults/main.yml:28-37`) and writes
      `/etc/hosts` from it (`install.yml:52-57`).
    - The homelab's third bug was the node certificate and
      the ticket's `proxy` URL naming different hosts
      (`docs/proxmox.md:228-236,330-333`). The certificate
      came from the resolver's search domain; the proxy URL
      came from `hostname -f`.
    - **Unverified:** the most likely mechanism is
      `PVE::Tools::get_fqdn` reading the `search` line of
      `/etc/resolv.conf`. The consequence does not depend on
      it: if the CI fabric hands the node no search domain,
      the role falls back to the invented `proxmox.lab`, and
      the mismatch returns.
    - Separately, the `spiceproxy` API takes an optional
      `proxy` parameter that overrides the advertised proxy
      host. **Also unverified; check the API schema in
      `PVE/API2/Qemu.pm`.** That would let a client avoid DNS
      altogether. Decision 4 declines to use it.
13. **Timing.**
    - The window is about 30 seconds. The proxy refuses at
      41 seconds and later, and the 35-36 second band already
      fails SPICE auth (master plan, *What the measurements
      settled*).
    - Phase 2 checks an expired ticket at a delay of 45
      seconds, only four seconds clear of the first 401 row.
    - A runner-to-API `curl` returns in well under a second.
      An ssh hop to run `pvesh` costs one to two seconds of
      connection setup.
    - Minting a new ticket resets qemu's password
      (supersession). A check that mints while an earlier
      session is still up confounds both, so the checks must
      run one after another.

## Decisions

1. **A composite action, `deploy-proxmox-on-shakenfist`,
   over one playbook and a set of task files.**
   - The playbook is `ansible/proxmox-single-node.yml`. The
     task files go under `ansible/tasks/proxmox/`, with vars
     in `ansible/vars/proxmox.yml`.
   - It is not a reusable workflow, because the consumer's
     client has to share the job (survey finding 11).
   - It is not a role directory, because actions has no
     roles and its two playbook-parsing checks are written
     around playbooks and task includes.
   - The name mirrors `deploy-kerbside-on-shakenfist`.
   - `deploy-kolla-ansible` is not a model. It assumes a VM
     that `setup-kerbside-environment` already made. This
     action makes its own, because nothing else needs this
     node.

2. **The action finds its own files through
   `github.action_path`, not through a checkout of
   `shakenfist/actions`.**
   - The playbook, task files and helpers are all resolved
     as `${{ github.action_path }}/../...`. A relative
     `uses: ./deploy-proxmox-on-shakenfist` in the actions
     repository's own lane then runs the pull request's
     playbooks, which makes decision 7 a real pre-merge test.
   - A consumer at `@main` gets files from the same ref as
     the `action.yml` it resolved.
   - It still checks out `shakenfist/shakenfist` for
     `install-collection.sh`, as every instance-creating
     path does.

3. **Port the role as validated, minus the private parts.**
   - What moves across: every generic file listed in survey
     finding 9. The node stays at 4 vCPU, 8 GB, 40 GB root
     and a blank 60 GB, on `debian:13`, pinned to PVE 9 on
     `trixie`.
   - The SSH key becomes `/srv/github/id_ci`, the
     kerbside playbooks' `ssh_private_key`.
   - The root password task is dropped, not replaced.
   - The token-creating task and everything that registers
     its output get `no_log: true`. The secret reaches the
     runner only as a 0600 file, never as a printed fact.
   - The node's apt goes through the fleet squid, as
     `ci-topology-slim-primary-released.yml:240-246` does.
   - Network: the node sits at `10.0.2.2` on a
     `10.0.2.0/24` network named `proxmox`. The runner joins
     it through the same add-interface block as
     `kerbside-single-node.yml:65-116`.
   - The instance is named `pve1-{{ identifier }}`, as the
     homelab does. Its inventory hostname and PVE node name
     are `pve1`.
   - On the guest network, the bridge is ported. NAT and
     dnsmasq are ported behind toggles that default off,
     because nothing on a console path needs guest egress.
     The resolver assertion is not ported, because it
     depends on a private helper.
   - The cirros guest (vmid 100) is fetched through the
     squid with its checksum enforced.

4. **Keep the FQDN real, assert it, and resolve it on the
   runner from the ticket itself.**
   - The domain comes from the fabric's search domain, as
     validated. The play fails if that is empty rather than
     falling back to an invented domain.
   - After install, it asserts that three things name the
     same FQDN: `hostname -f`; the CN of
     `/etc/pve/local/pve-ssl.pem`; and the host of the
     smoke ticket's `proxy` URL, together with the CN in its
     `host-subject`.
   - The action writes `<node address> <fqdn>` into the
     runner's `/etc/hosts`, taking the FQDN from a minted
     ticket rather than predicting it. It appends both to
     `no_proxy` and `NO_PROXY` through `GITHUB_ENV`
     (survey finding 4). Changing the consumer's environment
     is a side effect, so `docs/actions.md` states it.
   - Tickets are minted **without** the `proxy` override
     from survey finding 12, so the lane dials what a real
     Proxmox `.vv` carries.
   - If the fabric turns out to hand out no search domain,
     the fallback is an explicit domain input that also sets
     the node's resolver search domain through a networkd
     drop-in, so all three sources still agree. It is not
     built unless 1b.5 shows it is needed.

5. **Tickets are minted through the API token, over
   HTTPS, from the runner.**
   - This is the path kerbside's driver will use, and the
     measurements never took it (survey finding 10). It also
     proves the ACL from master plan open question 4 is at
     least sufficient.
   - The helper is `tools/proxmox-mint-vv.sh` in actions.
     Its interface is:
     `--api-url --node --vmid --token-id --token-file
     --ca-file --out`.
   - It writes a 0600 `.vv`: the `[virt-viewer]` line, then
     one `key=value` line per field of the response, the
     same shape as phase 2's script. It prints only the mint
     time as epoch seconds.
   - It sends the token header from a 0600 temporary file
     with `curl -H @file`, so the secret never appears in
     argv or in `set -x` output.
   - The action exposes the helper's absolute path as an
     output, `mint_script`.
   - Rejected: minting over ssh with `pvesh`. It skips the
     token and costs a second or two of connection setup
     inside the window.

6. **The action checks itself before it hands anything
   back.**
   - As its last step it mints a ticket with the helper and
     runs `tools/proxmox-connect-probe.py` from the runner.
     The probe resolves the advertised proxy *by name* and
     sends the CONNECT with the `Host:` header. It requires
     a `200`, then closes.
   - A failure there is a substrate failure, reported as
     one, and never reaches a client test. This follows
     kerbside's runner-side port probe
     (`functional-tests.yml:730-743`), which exists to move
     that failure out of an opaque TLS error.
   - The probe goes no further than the CONNECT. TLS and
     SPICE belong to the client under test.

7. **Actions gets a lane of its own for this action,
   `.github/workflows/proxmox-substrate.yml`.**
   - It calls `./deploy-proxmox-on-shakenfist` by relative
     path. Its triggers are `pull_request`, path-filtered to
     the action, the playbook, its task and vars files and
     the two helpers; `workflow_dispatch`; and a weekly
     `schedule`.
   - It runs on `[self-hosted, vm, debian-13, m]` with the
     fork guard, and with the event-branched concurrency
     key.
   - A scheduled failure files or updates a
     `proxmox-substrate` issue. The body comes from a
     script under `tools/`, following `canary.yml`'s
     one-issue-per-outage pattern.
   - It is advisory, not required.
   - The canary does not exercise this action, so without
     this lane the first test of it would be ryll's CI.
   - The weekly run is where upstream drift surfaces: a
     rotated keyring checksum, or a moved
     `pve-no-subscription`. That is the substrate owner's
     problem, and it should not first appear as a red lane
     in a client repository.

8. **The ryll lane is a separate, advisory workflow,
   `.github/workflows/proxmox-functional.yml`.**
   - Triggers: `pull_request` to `develop`, path-filtered to
     `shakenfist-spice-protocol/src/**`, `ryll/src/config.rs`,
     `ryll/src/main.rs`, the workflow and the lane's driver;
     and `workflow_dispatch`.
   - There is no cron. Drift detection is decision 7's job.
   - Runner: `[self-hosted, vm, debian-13-docker, l]`,
     because `make release` builds inside the devcontainer
     and is `l`-sized, as ryll's own `build-linux` is.
   - It carries the fork guard (survey finding 7) and the
     concurrency block ryll's `docs/ci.md` prescribes.
   - It builds ryll first, then deploys the node. A compile
     error then fails before a 4 vCPU nested node is booked,
     and build time has no bearing on the ticket window.
   - It is not a job in `ci.yml`. That would put it in the
     gates' `needs` lists and make a path-filtered lane
     effectively required.

9. **The lane's ryll commits land on `spice-http-connect`,
   inside phase 2's pull request, replacing step 2f.** This
   is the decision most likely to be argued with.
   - The case for it:
     - The positive check cannot pass without phase 2's
       code, and `pull_request` runs the pull request's own
       copy of a workflow, so phase 2's pull request
       validates itself.
     - `workflow_dispatch` cannot help before merge, because
       it only offers workflows that exist on the default
       branch.
     - A follow-on branch would mean merging phase 2 with
       nothing run against a real node, and preventing
       exactly that is what 2f was for.
     - A pull request stacked on `spice-http-connect` would
       not trigger a workflow filtered to `develop`.
     - Landing the lane on `develop` first would give a lane
       that is either red or tests nothing.
   - The cost:
     - Phase 2's pull request grows.
     - Its merge waits on the actions pull request.
     - The lane's fate is tied to phase 2's. If phase 2
       stalls, the lane stalls with it.
     - 1b's ryll half is audited by 2g, not by a step of its
       own.

   The operator may reasonably prefer two pull requests. If
   so, the least-bad alternative is to merge phase 2 first,
   having run the lane from a throwaway copy of the branch
   and recorded the run URL. Then land the lane as its own
   pull request.

10. **The lane's checks are sequential, freshly minted, and
    fail with distinct messages.** The driver is
    `tools/proxmox-smoke.py` in ryll. Each check mints its
    own ticket through `mint_script` and starts ryll
    immediately. It measures mint-to-launch on the runner,
    and fails with a message naming the *harness* if that
    exceeds 10 seconds, so a slow runner is never reported
    as a ryll defect.
    - **Positive.** All channels authenticate, and the
      session survives 120 seconds after the mint.
    - **Expired ticket.** Wait **50 seconds**, not phase 2's
      45 (survey finding 13), then expect decision 3's
      `401` and its ticket hint.
    - **Wrong pin.** Change the `CN` in `host-subject`, then
      expect a TLS failure naming both subjects.
    - **Missing pin.** Remove `host-subject`, then expect
      decision 4's refusal before any dial.

    The minted `.vv` is never uploaded as an artifact: it
    carries a live SPICE password, and phase 2's redaction
    covers ryll's output, not the file.

    Oracles, positive case:
    - The control socket's `status` reports
      `spice_connected` with a display surface.
    - `screenshot` returns a PNG.
    - `send_key` is accepted, which exercises inputs.
    - For the cursor channel, which the control socket
      cannot report, the check reads ryll's `--verbose`
      per-channel connection lines. The script names the
      exact lines it greps.

11. **Kerbside changes nothing in 1b.** Its lane consumes
    the same action in a later phase. The action's outputs
    are chosen with that consumer in mind: the API URL,
    token id and file, CA file, node, FQDN and vmid are
    exactly what a `sources.yaml` entry for a Proxmox source
    will need.

## Key facts front-loaded for the sub-agents

- Instance-creation precedent to mirror, including the
  runner add-interface block:
  - actions `ansible/kerbside-single-node.yml:1-153`, in
    full: namespace, network, instance include, CI block,
    banner wait, readiness play.
  - `sf_instance` with two disks:
    homelab `playbooks/proxmox-9-debian-13.yml:127-149`.
  - `add_host` fields: actions
    `ansible/kerbside-create-instance.yml:49-59`.
- The readiness gate is `ansible/tasks/wait-for-cloud-init.yml`,
  imported by a play whose `hosts:` names the added group.
  `tests/test_ansible_readiness.py:398-461` enforces both
  that and the explicit set of creating playbooks. Add
  `proxmox-single-node.yml` to that set.
- Collections the ported tasks need on the runner:
  - `ansible.posix.sysctl`, which is only used when NAT is
    on;
  - `community.general.lvg` and `community.general.lvol`;
  - `shakenfist.shakenfist.*`, via
    `tools/install-collection.sh <shakenfist checkout>`.

  The Debian `ansible` package carries the first two.
- Squid apt proxy stanza: actions
  `ansible/ci-topology-slim-primary-released.yml:240-246`.
- Runner facts:
  - SSH key `/srv/github/id_ci`;
  - inventory `/home/debian/ansible-hosts`;
  - namespace `$(hostname)`;
  - the runner exports `http_proxy` (kerbside
    `functional-tests.yml:418-428`);
  - `jq` and `python3` are on the image (actions
    `ansible/ci-image.yml:149-160`).
- Composite-action rules (actions `AGENTS.md`):
  - no `timeout-minutes` inside the action (`:81-83`);
  - every runner label listed in `.github/actionlint.yaml`
    (`:85-88`);
  - a `vm` runs-on names a size (`:90-98`);
  - no more than about five lines of shell inline; the rest
    goes in `tools/` (`:144-147`);
  - actionlint does not lint `action.yml`, so nothing
    checks it before merge (`docs/ci.md`, *What is still not
    covered*).
- Masking: emit `echo "::add-mask::${secret}"` in the step
  that first reads the secret file, before anything else can
  print it.
- PVE facts:
  - ticket JSON keys, and the escaped newlines in `ca`: see
    the master plan, *Three protocol details*;
  - the CONNECT target and `Host:` header must both be
    `<host>:<tls-port>`
    (homelab `tools/pve-ticket-probe.py:53-66,83-90`);
  - spiceproxy answers `HTTP/1.0 200`;
  - the node's root CA is `/etc/pve/pve-root-ca.pem`. It is
    the same CA the ticket's `ca` field carries, so fetch it
    to the runner for `curl --cacert`.
- Ryll:
  - the `.vv` loader is `ryll/src/config.rs:523-574`;
  - the control socket verbs are in
    `docs/control-socket-protocol.md`: `status` at `:358`,
    `send_key` at `:392`, `screenshot` at `:521`;
  - `examples/control-socket-demo.py` is a Python client to
    reuse;
  - `make release` builds `target/release/ryll` in the
    devcontainer with `--network none`, and `make web-smoke`
    is precedent for running that binary on the runner
    afterwards;
  - the build step is preceded by `./.github/actions/cargo-cache`
    (`ci.yml:147-157`).
- Ryll concurrency key for a pull-request lane: `docs/ci.md`,
  *Concurrency*.

## Step plan

One commit per step, in order. Steps 1b.1 to 1b.6 are in
`shakenfist/actions`. Steps 1b.7 to 1b.10 are in
`shakenfist/ryll` on `spice-http-connect`, between phase 2's
2e and 2g.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1b.1 | high | opus | none | In the actions checkout on branch `proxmox-substrate`, port the homelab `proxmox_host` role and playbook per decisions 1 and 3. Create `ansible/proxmox-single-node.yml` with these plays: (1) on localhost, ensure the namespace, create network `proxmox` (`10.0.2.0/24`, `dns: true`), create instance `pve1-{{ identifier }}` (4 vCPU, 8192 MB, disks `40@debian:13` and a blank `60`, one networkspec at `10.0.2.2` with `float=true`, `side_channels: [sf-agent2]`, no `user_data`, ssh key `/srv/github/id_ci.pub`), `add_host` it as `pve1` in group `allsf` with `proxmox_mgmt_address`, then the CI add-interface block copied from `kerbside-single-node.yml:65-116` (without the `ci-environment.sh` write) and the banner wait; (2) the readiness play importing `tasks/wait-for-cloud-init.yml` against `allsf`; (3) on `pve1` with `become`, the squid apt proxy, then the task files. Port the homelab tasks into `ansible/tasks/proxmox/{install,disable-enterprise-repos,network,storage,api,smoke}.yml`, and the defaults into `ansible/vars/proxmox.yml`, keeping their explanatory comments. The comments are the validation record. Strip everything survey finding 9 lists as private: no root password task, no report play, no resolver check, `proxmox_enable_nat` and `proxmox_enable_dhcp` default false, and the dnsmasq handler moved into the play. Mark the token-add task and any task registering its output `no_log: true`. Write the token secret to a runner-side 0600 file named by a `proxmox_token_file` var, via `delegate_to: localhost` and `no_log`. Also fetch `/etc/pve/pve-root-ca.pem` to a `proxmox_ca_file` var. Add decision 4's assertion after install: fail if the search domain is empty, and fail unless `hostname -f`, the CN of `/etc/pve/local/pve-ssl.pem` (via `openssl x509 -noout -subject`), the smoke ticket's `proxy` host and its `host-subject` CN all name one FQDN, printing all four. Write the FQDN, node address, PVE version, vmid and whether `/dev/kvm` existed to a runner-side facts JSON at a `proxmox_facts_file` var. Add `proxmox-single-node.yml` to the expected set in `tests/test_ansible_readiness.py:442-455`. Run `pre-commit run --all-files`, the unit tests, and `tools/ansible-syntax-check.sh ansible/proxmox-single-node.yml` if the collection is installable locally; say whether it was. Finally grep the new files for `homelab`, `id_general`, `proxmoxproxmox` and `sfcbr`, and report the result. |
| 1b.2 | high | opus | none | Create `deploy-proxmox-on-shakenfist/action.yml`, a composite action, per decisions 2, 4, 5 and 6. Inputs: `base_user` (default `debian`), `node_address` (default `10.0.2.2`), `smoke_vmid` (default `100`) and `workdir` (default `${{ runner.temp }}/proxmox`). Steps: check out `shakenfist/shakenfist` to a path under `workdir` and run `${{ github.action_path }}/../tools/install-collection.sh` on it; then run `ansible-playbook -i /home/debian/ansible-hosts ${{ github.action_path }}/../ansible/proxmox-single-node.yml` with `identifier=$(hostname)` and the file vars from 1b.1, passing inputs through `env:` rather than interpolating them, as `setup-kerbside-environment/action.yml:90-98` explains; then read the token file and `::add-mask::` it; then write `/etc/hosts` and the `no_proxy`/`NO_PROXY` additions per decision 4; then run the self-check (decision 6). Outputs: `node_name`, `node_address`, `node_fqdn`, `api_url` (`https://<fqdn>:8006`), `token_id`, `token_file`, `ca_file`, `vmid`, `kvm`, `pve_version` and `mint_script`. Any step longer than about five lines of shell becomes a script under `tools/`. Write `tools/proxmox-mint-vv.sh` to decision 5's interface. It must not use `set -x`, must write the header file and the `.vv` 0600, and must fail naming the HTTP status if the API refuses. Write `tools/proxmox-connect-probe.py <vv-file>`, which reads `proxy`, `host` and `tls-port`, resolves the proxy host by name, sends `CONNECT <host>:<tls-port> HTTP/1.1` with a `Host:` header of the same value, and exits 0 only on a 200 status line. It prints the status line and the proxy name, never the pseudo-hostname. Both scripts must be executable and pass shellcheck or flake8. Run pre-commit and the unit tests. |
| 1b.3 | medium | sonnet | none | Add `.github/workflows/proxmox-substrate.yml` per decision 7. It has one job, `substrate`, on `[self-hosted, vm, debian-13, m]`, with `timeout-minutes: 90` on the job and on the `uses: ./deploy-proxmox-on-shakenfist` step. The job `if:` carries the fork guard in the form `tests/test_workflow_references.py:419-436` asserts, allowing `schedule` and `workflow_dispatch` through. Use the event-branched concurrency key from ryll's `docs/ci.md` *Concurrency* section, with `cancel-in-progress` true on `pull_request` only. After the action, mint one more `.vv` via `steps.<id>.outputs.mint_script` and run the connect probe again, which proves the outputs are usable by a consumer. The job-level `permissions` is `contents: read`. Add a `report-failure` job for `schedule` failures, modelled on `canary.yml`'s report job but calling a new `tools/report-proxmox-substrate-failure.sh` rather than inline shell, with label `proxmox-substrate`. Path filter: `deploy-proxmox-on-shakenfist/**`, `ansible/proxmox-single-node.yml`, `ansible/tasks/proxmox/**`, `ansible/vars/proxmox.yml`, `tools/proxmox-*`, and the workflow itself. Weekly cron at a quiet hour, stated in UTC in a comment. Confirm actionlint accepts the labels, and run the unit tests; `test_every_referenced_tools_script_exists_and_is_executable` covers the new script. |
| 1b.4 | medium | sonnet | none | Document the action in actions. In `docs/actions.md`, add a `deploy-proxmox-on-shakenfist` section in the style of `deploy-kerbside-on-shakenfist`: a usage block with a caller-side `timeout-minutes`; tables of inputs and outputs; the two environment side effects (`/etc/hosts` and `no_proxy`); the minimum runner size (`s`, and why); that `token_file` holds a live credential; that a consumer must mint immediately before connecting, because the window is about 30 seconds; and a short example of `mint_script` use. In `docs/ansible.md`, add a *Proxmox node* section giving the portless bridge rationale in one paragraph, the three-way FQDN agreement and why it is asserted, and what was deliberately not ported and why (survey finding 9, without naming the private repository's paths or secrets). In `docs/ci.md`, add the `proxmox-substrate.yml` lane and a sentence under *What is still not covered* saying that this action alone is exercised pre-merge, because it resolves its files through `github.action_path`. In `ARCHITECTURE.md`, add the action to the composite actions table and the lane to the workflows table; this is a change to the component inventory. In `README.md`, add the action name to the published list only. Do not touch `AGENTS.md` unless you conclude the `action_path` pattern is a new convention; if so, propose the line rather than adding it. All links follow the two rules `tests/test_documentation_links.py` enforces. |
| 1b.5 | xhigh | opus | none | Push `proxmox-substrate` and open the actions pull request, which triggers `proxmox-substrate.yml`. Drive it green. For each failure, diagnose from the run log (`ci-status`), fix on the branch as a new commit, and re-run. Expect first-run surprises: the fabric search domain, squid reachability from the test network, nested KVM, and the runner's SF credentials. If the search domain is empty, implement decision 4's fallback and say so. Record in this plan's *Outcome*: the green run URL; the deploy's wall time from the action step's timing; the PVE version; whether KVM was nested or TCG; and the FQDN's shape, as `pve1.<redacted>`, never the namespace. Then confirm from the run log that no token secret leaked: `gh run view <id> --log` searched for `"value"` and for `PVEAPIToken=` must find nothing but masked `***`. Record that command's output. |
| 1b.6 | medium | sonnet | none | Push-audit substitute for actions, which has no `PUSH-AUDIT.md`. Run `pre-commit run --all-files`, the unit tests and `tools/gitleaks-scan.sh`. Then walk the branch diff against every trap in actions `AGENTS.md:48-120` and record per trap: applies/does not apply, and how it is handled. Wait for the automated reviewer, triage its items fix/document/consider/none, and ask for a re-review after fixes, since reviews run only once unless requested (`AGENTS.md:57-61`). Record the result here in one paragraph, including "no findings" if that is the result. The operator merges; record `actions <sha> (#pr)` in the master plan's Execution table. |
| 1b.7 | high | opus | none | In the ryll worktree on `spice-http-connect`, after 2e, write `tools/proxmox-smoke.py`, the lane driver, per decision 10. It is invoked with `--ryll`, `--mint-script`, `--api-url`, `--node`, `--vmid`, `--token-id`, `--token-file`, `--ca-file` and `--workdir`. For each check, in order positive, expired, wrong pin, missing pin: mint to a fresh `.vv` and record the mint epoch; edit the `.vv` as the check requires (`host-subject` CN altered, or the key removed); for the expired check, sleep 50 s; launch `ryll --headless --verbose --file <vv> --control-socket <sock>`; fail with a harness message if more than 10 s passed between mint and launch. Positive case: wait for the socket; then assert `status` reports `spice_connected` and at least one surface; `screenshot` returns a PNG; `send_key` succeeds; and ryll's log has a connection line for each of the main, display, inputs and cursor channels. Find the exact log lines in the 2b/2c code rather than guessing, and name them in a constant with a comment. Hold the session until mint+120 s, re-assert `status` and take a second screenshot, then stop ryll and confirm it exits. Negative cases: assert ryll exits non-zero within a bound, and that its output contains the specific error. For expired, that is the 401 with decision 3's ticket hint, and no TLS or link error. For wrong pin, a TLS failure naming both subjects. For missing pin, decision 4's error naming `host_subject`, with no CONNECT sent; assert the latter by checking that ryll's log has no proxy dial line. First check how headless ryll exits when a connect fails and a `.vv` sets `delete-this-file=1`, and report it. Print one summary line per check with elapsed times. Never print the `.vv`, the password or the pseudo-hostname. Reuse `examples/control-socket-demo.py`'s framing. Follow ryll's Python conventions, and run flake8 if ryll's pre-commit covers `tools/*.py`. |
| 1b.8 | medium | sonnet | none | Add `.github/workflows/proxmox-functional.yml` to ryll per decision 8. It has one job on `[self-hosted, vm, debian-13-docker, l]`, `timeout-minutes: 120`, with the fork guard `github.event_name != 'pull_request' \|\| github.event.pull_request.head.repo.full_name == github.repository`, and ryll's documented pull-request concurrency block. Steps: checkout; `./.github/actions/cargo-cache` directly after checkout (see `ci.yml:147-157`); `make release`; `uses: shakenfist/actions/deploy-proxmox-on-shakenfist@main` with its own `timeout-minutes: 60`; then `python3 tools/proxmox-smoke.py` with the action's outputs; and on `always()`, upload the smoke driver's log and ryll's logs, **excluding every `.vv`**. The job's `permissions` is `contents: read`. Use `pull_request` filtered to `develop`, with the paths in decision 8, plus `workflow_dispatch`. No schedule. Add the `vm`/size labels to any actionlint config ryll keeps, if it has one. |
| 1b.9 | low | sonnet | none | Ryll docs. In `docs/ci.md`, add a *Proxmox lane* section: what it proves; that it deploys a real PVE 9 through `shakenfist/actions`; that it is advisory, for the path-filter reason `:468-485` gives; that it carries the fork guard and why; and that the substrate's own weekly lane lives in actions. Add a row to the *Workflow inventory* table. In `docs/configuration.md`'s Proxmox example added by 2e, add one sentence pointing at the lane as the proof that the example works. |
| 1b.10 | xhigh | opus | none | Push `spice-http-connect` (with 2a–2e and 1b.7–1b.9) and open, or update, phase 2's pull request so `proxmox-functional.yml` runs. Drive it green. A failure in the harness is fixed in 1b.7's script; a failure in ryll is a phase 2 defect, fixed in the relevant 2x step's code as a new commit and reported to the management session. Then run **one deliberate-failure run**: a throwaway commit that sets the expired check's delay to 0 must turn the lane red at that check and nowhere else. Drop the commit afterwards. Record under phase 2's *Outcome*, and here: the green run URL; each check's summary line; the mint-to-launch times; the red run URL; and whether the runners' ShakenFist credentials worked on the first attempt (survey finding 6). |

After 1b.1 the management session diffs the ported task
files against the homelab originals. Every difference must
be one decision 3 names. After 1b.2 it reads the action's
steps against `docs/actions.md`'s claims, rather than
trusting either. After 1b.7 it checks that each negative
check asserts a specific message and not merely a non-zero
exit, because an exit code alone passes for the wrong
reason.

## What the lane runs, end to end

```
ryll PR (spice-http-connect)
  └── proxmox-functional.yml              l, debian-13-docker
        ├── make release                   devcontainer, --network none
        ├── deploy-proxmox-on-shakenfist@main
        │     ├── install-collection.sh    via github.action_path
        │     ├── proxmox-single-node.yml  instance, add-interface,
        │     │                             readiness, PVE, token, guest
        │     ├── FQDN three-way assertion
        │     ├── runner /etc/hosts, no_proxy
        │     └── mint + CONNECT probe      substrate self-check
        └── tools/proxmox-smoke.py
              ├── positive     mint → ryll ≤10s → hold 120s
              ├── expired      mint → 50s → ryll → 401 + hint
              ├── wrong pin    mint → edit CN → ryll → TLS error
              └── missing pin  mint → drop key → refused pre-dial
```

## Risks and mitigations

- **Ryll's runners cannot create instances.** The conductor
  hands out credentials, and that cannot be seen from here
  (survey finding 6). Mitigation: 1b.10's first run shows it
  immediately, at the namespace task. If it fails, the phase
  is `Blocked` on private-ci, and the plan says so. The
  actions lane (1b.5) proves only that actions' runners
  have credentials.
- **The token secret lands in a public log.** Ryll's CI logs
  are public. Mitigation: `no_log` on every task that sees
  it, the `::add-mask::`, a header file instead of argv, and
  1b.5's explicit log search.
- **The fabric hands out no search domain.** Then the FQDN
  assertion fails the deploy with all four names printed.
  That is the point of having it. Decision 4's fallback is
  then built in 1b.5 rather than speculatively.
- **Upstream Proxmox drifts.** The keyring checksum, the
  `trixie` suite and package sets all move without notice.
  Mitigation: the weekly actions lane files an issue, so
  drift is seen by the owner before a ryll pull request
  trips on it.
- **Deploy time.** The homelab deploy took under ten
  minutes. CI adds the squid, a kernel reboot on a loaded
  fabric, and the readiness gate's worst case, which is
  about twenty minutes per host (actions `docs/ansible.md:103-110`).
  Mitigation: 1b.5 measures it, and the caller-side timeout
  is set from that measurement rather than from this
  guess.
- **The `l` pool.** Every ryll run of this lane holds an `l`
  slot for the whole deploy. Mitigation: the path filter,
  cancel-in-progress, and building before deploying so a
  broken build releases the slot early. If contention bites,
  split the build into its own job that uploads the binary.
- **Merge ordering.** The actions pull request must merge
  before phase 2's can go green. Mitigation: 1b.1–1b.6 run
  in parallel with phase 2's 2b–2e, which do not need it.
- **Supersession confounds the checks.** Mitigation:
  decision 10 makes the checks sequential, and the positive
  session is stopped before the next mint.
- **A merge to actions `main` is a deploy.** Nothing
  consumes this action until the ryll lane does, so a broken
  first version breaks nobody. That changes once kerbside
  consumes it. The actions lane is what stands between a
  later edit and the fleet.

## Definition of done

- [ ] `deploy-proxmox-on-shakenfist/action.yml` exists, and
      every input and output it declares is documented in
      actions `docs/actions.md`. A reviewer diffing the two
      lists finds no difference.
- [ ] `tests/test_ansible_readiness.py` names
      `proxmox-single-node.yml` among the creating
      playbooks, and `python3 -m unittest discover -s tests
      -t .` passes in actions.
- [ ] `grep -rn -E 'homelab|id_general|proxmoxproxmox|sfcbr'
      deploy-proxmox-on-shakenfist ansible/proxmox-single-node.yml
      ansible/tasks/proxmox ansible/vars/proxmox.yml
      tools/proxmox-*` in actions finds nothing.
- [ ] Every task in `ansible/tasks/proxmox/api.yml` that
      creates the token or registers its output carries
      `no_log: true`.
- [ ] A green `proxmox-substrate.yml` run on the actions
      pull request is linked under *Outcome*, with the
      deploy's wall time. In its log, the FQDN assertion
      prints four matching names.
- [ ] `gh run view <that run> --log | grep -E
      'PVEAPIToken=|"value"'` returns only masked output.
      The command and its result are recorded under
      *Outcome*.
- [ ] A green `proxmox-functional.yml` run on phase 2's pull
      request is linked in phase 2's *Outcome*. Its log
      shows four summary lines: positive (four channels,
      alive at mint+120 s), expired (401 with the hint),
      wrong pin (TLS, both subjects), missing pin (refused,
      no dial).
- [ ] Every positive-case mint-to-launch time in that run is
      under 10 seconds, and all are recorded.
- [ ] A deliberate-failure run turned the lane red at the
      expired check alone, and its URL is recorded.
- [ ] No `.vv` file appears in either lane's uploaded
      artifacts.
- [ ] The actions push-audit substitute (1b.6) is recorded
      under *Outcome*, and 2g's audit covered the lane's
      ryll commits.
- [ ] The master plan's Execution table carries a 1b row
      with `Merged` set to `actions <sha> (#pr)`, and its
      open question 5 no longer mentions a validated node.
      Phase 2's step 2f and *Validation against a real node*
      point at the lane.

## Bugs fixed during this work

None yet. The survey found ryll's VM lanes running without a
fork guard (finding 7). That is recorded under *Future
work* and deliberately not fixed here.

## Future work

- **The kerbside lane.** It consumes this action, points a
  Proxmox source at the node through the action's outputs,
  and drives a proxied session through the tunnel. The
  operator places it in phase 3b. The master plan's
  decomposition currently puts the end-to-end lane in
  phase 5 and gives 3b the tunnel transport alone, so the
  two need reconciling when 3b is planned. A 3b lane can
  prove the tunnelled backend dial before the phase 4 driver
  exists, perhaps with a static source row; phase 5 then
  becomes the driver lane and its promotion into kerbside's
  merge tier.
- Promote the ryll lane from advisory once it has a record,
  or move it into ryll's merge tier. `paths` does not work
  on `merge_group`, so that is a choice between every merge
  booking a nested PVE and staying advisory.
- Add fork guards to ryll's existing VM lanes (survey
  finding 7), and a test like actions'
  `test_every_vm_lane_carries_the_fork_guard`.
- Use the substrate to answer master plan open question 4:
  mint with a token holding narrower roles until it fails.
- Serve cirros from the dependencies disk rather than
  through the squid. Actions caches cirros 0.5.2 there
  today (`ansible/ci-dependencies.yml:166`); the role pins
  0.6.3.
- Run `remote-viewer` as a second client in the lane, as a
  reference-behaviour check against spice-gtk.
- A PVE 8 / `bookworm` matrix entry, which the homelab doc
  says is the same playbook with two variables changed
  and which it never tried.
- Port the guest resolver check, once guest networking
  matters to something.

## Outcome

In progress. Actions branch `proxmox-substrate`: 1b.1
`5d7aaca`, 1b.2 `8f7cec6`, 1b.3 `72454bf`, 1b.4 `9beb04b`;
1b.5 (drive the lane green) is under way. Ryll branch
`spice-http-connect`: 1b.7 `dd83856`, 1b.8 `5487b04`, 1b.9
`edd43e8`. 1b.7's local runs, against qemu behind a fake
spiceproxy, found that headless ryll exited 0 on a failed
connect; fixed in ryll `189bff3` (see phase 2's *Outcome*).

## Back brief

Before executing any step of this plan, back brief the
operator on the intended approach and on any deviation from
this plan found during implementation. In particular:

- confirm decision 9 (the lane inside phase 2's pull
  request) before 1b.7 starts, since the alternative changes
  where three commits land;
- report the result of the first actions lane run (1b.5)
  before the ryll steps build on the action's outputs; and
- if 1b.10's first run shows ryll's runners cannot create
  instances, stop and report rather than working around it.
