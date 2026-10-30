# Proxmox source phase 5: the CI lane

This is the detailed plan for phase 5 of
[PLAN-proxmox-source.md](/components/kerbside/plans/PLAN-proxmox-source/). Read the
master plan first. Its *What the measurements settled* and
*Three protocol details that cost an afternoon* are assumed
knowledge here. So is the
[phase 4 plan](/components/kerbside/plans/PLAN-proxmox-source-phase-04-source-driver/),
whose driver this lane is the first to run against a real node.
Its decision 7 asks this lane to prove the least-privilege
account, and its *Future work* lists what the driver knowingly
does not handle.

## Prompt

All work for this phase lands in `shakenfist/kerbside`. It adds
a CI lane that builds a real Proxmox VE node with
`shakenfist/actions`' `deploy-proxmox-on-shakenfist` action. The
lane deploys the pull request's kerbside on the runner, registers
a `type: proxmox` source against the node, and relays a real
SPICE session through kerbside's proxy and PVE's `spiceproxy`. It
adds `.github/workflows/proxmox-functional.yml` and
`tools/proxmox-e2e/`, and documents the lane. It changes the
driver only where the lane proves it wrong. Consult
`ARCHITECTURE.md`, `docs/testing.md` (*CI tiers* and *The oVirt
end-to-end kerbside lane*), `tools/ovirt-e2e/README.md` and
`docs/console-sources.md`'s Proxmox VE section. Run `tox -eflake8`,
`tox -epy3` and `pre-commit run --all-files` before every commit.

Planning effort: **high**. The lane handles live credentials in
public CI logs: two PVE API tokens, a SPICE password and a signed
CONNECT host. Its oracles have to tell a harness timing fault
from a real defect when a ticket lasts 30 seconds. And it is the
first contact between phase 4's driver and PVE, so it may find
defects in that driver.

## Scope

In scope:

- A workflow that deploys a PVE node, deploys kerbside on the
  runner, and drives one proxied console end to end.
- Creating the source's token with the documentation's own
  `pveum` commands, and proving that the two privileges are both
  enough and both needed.
- Fixture VMs on the node that discovery must skip.
- Oracles read from PVE's own access log, for the property that
  matters most here: discovery and the direct refusal never mint,
  and a session mints once.
- Fixing any driver defect the lane proves, each with a unit test
  that reproduces it.
- Documenting the lane, its tier, and its place in the pre-release
  check.

Out of scope:

- The Proxmox use-case page, which is phase 6.
- PVE clustering and live migration. The action builds one node,
  so phase 4's live-migration gap stays in its *Future work*.
- Changing `deploy-proxmox-on-shakenfist` itself. See *Future
  work*.
- Generalising `tools/ovirt-e2e/` into a shared lane library. See
  decision 6.

## What the survey found

1. **The action does most of the node's setup.**
   `deploy-proxmox-on-shakenfist` (`shakenfist/actions` `main`,
   `deploy-proxmox-on-shakenfist/action.yml`) boots PVE 9 in the
   runner's own Shaken Fist namespace, at `10.0.2.2` by default.
   It starts a running SPICE guest (vmid 100, `--vga qxl`, cirros)
   and maps the node's FQDN in the runner's `/etc/hosts`. It adds
   the FQDN and the address to `no_proxy` and `NO_PROXY` through
   `GITHUB_ENV`. Its outputs include `api_url`
   (`https://<fqdn>:8006`), `node_name`, `node_fqdn`, `vmid`,
   `token_id`, `token_file` (0600, masked) and `ca_file`. The
   weekly `proxmox-substrate.yml` run of 2026-10-25 took nine
   minutes for the whole job.

2. **The action's token is not the documented one.** Its
   `ansible/tasks/proxmox/api.yml` and `ansible/vars/proxmox.yml`
   create `kerbside@pve!console` with `--privsep 0`. They grant the
   user `PVEVMUser` on `/vms` and `PVEAuditor` on `/`. Their own
   comment says this least privilege is "asserted, not proven",
   although `action.yml`'s description calls it "a least-privilege
   API token". `docs/console-sources.md` documents a different
   account: a `KerbsideConsole` role holding `VM.Audit` and
   `VM.Console`, and a privilege-separated token. The doc's
   commands also create the user `kerbside@pve`, which the action
   has already created, so they cannot run unchanged on this node.

3. **The runner can reach the node as root.** The action's
   `tools/proxmox-deploy.sh` drives the playbook from the runner
   with the inventory in `/home/debian/ansible-hosts`. So the
   runner can SSH to the node and run `pveum` and `qm` there with
   `sudo`.

4. **The smoke guest has an SMBIOS UUID.** It is made with
   `qm create`, which writes one (phase 4, survey finding 6). So
   its console uuid is `100-<smbios uuid>`. This is the first time
   that is checked on real PVE rather than read from source.

5. **The oVirt lane is the model, and it runs nightly only.**
   `functional-tests.yml`'s `ovirt_matrix` builds the proxy wheel
   and ryll on the runner. It deploys kerbside there as plain
   processes with `tools/ovirt-e2e/deploy-kerbside.sh`, which
   reuses `tools/direct-qemu/start-kerbside.sh`. Then
   `tools/ovirt-e2e/drive-console.py` drives the session with
   ryll and `tools/direct-qemu/smoke-client.py`. That script holds
   the session past the token's expiry, terminates it over REST,
   and scans the artifacts for the engine password. It runs on
   `[self-hosted, vm, debian-13, l]`, nightly and on dispatch
   (`docs/testing.md`, *CI tiers*). `deploy-kerbside.sh` and
   `gen-sources.py` are oVirt-specific throughout.

6. **Ryll's Proxmox lane is advisory and path-filtered.**
   `shakenfist/ryll`'s `.github/workflows/proxmox-functional.yml`
   runs on pull requests touching the tunnel code, and on
   dispatch. It is deliberately not a required check, because a
   path-filtered required check never reports, which blocks a
   pull request forever. It also skips pull requests from forks,
   because its runner holds the CI mesh's SSH key. Upstream drift
   in PVE is left to the actions repository's weekly lane.

7. **Nightly failures already have a home.** The scheduled jobs
   in `direct-qemu-functional.yml` and `sf-e2e-functional.yml`
   call `tools/file-nightly-failure-issue.sh <lane> <run-url>`,
   which files or updates a `Nightly <lane> lane failing` issue.
   `docs/development.md`'s *Releasing* asks the tagger to check
   for open oVirt and OpenStack nightly issues before tagging.

8. **PVE's access log can count mints.** Every API request,
   `POST .../spiceproxy` included, is written to
   `/var/log/pveproxy/access.log` on the node. That is the only
   place a mint can be counted without trusting kerbside's own
   account of it. The log's field layout, and whether it names the
   token, must be checked in `pve-http-server`'s source before an
   oracle depends on it.

9. **PLAN-two-tier-ci.md placed this lane in the merge tier.**
   Its *Future work* says the Proxmox lane lands "directly in the
   merge tier". The cloud lanes have since left the merge queue
   for the nightly tier (`docs/testing.md`, *CI tiers*). That plan
   is complete and is not edited. The master plan's
   *Relationship to other plans* now notes that the tier is
   decided here.

Nothing in the master plan's phase 5 row is false, but it is one
line. It is expanded in the planning commit from decisions 1 and
2, and the `index.md` row describes this phase from the survey.

## Decisions

1. **The lane is nightly, on dispatch, and advisory on pull
   requests that touch the Proxmox path.** This is the decision
   most likely to be argued with. Its own workflow,
   `proxmox-functional.yml`, runs:
   - on `schedule`, filing `Nightly proxmox lane failing` through
     `tools/file-nightly-failure-issue.sh` when it fails;
   - on `workflow_dispatch`;
   - on `pull_request`, path-filtered to:
     - `kerbside/sources/proxmox.py`, `kerbside/sources/base.py`
       and `kerbside/sources/minting.py`;
     - `kerbside/rpc/**`;
     - `rust/kerbside-proxy/src/backend.rs` and the proxy's
       `Cargo.lock`;
     - the workflow and `tools/proxmox-e2e/**`.

   It has no `merge_group` trigger and is not a required check.
   The alternative is the smoke tier, which would book a nested PVE
   node, about nine minutes and an `l` runner, on every push of
   every pull request. PVE's churn, like the cloud lanes', is
   upstream (the no-subscription repository, and the action
   itself), and that churn is what pushed those lanes out of the
   gate. Path filtering gives signal on the pull requests that can
   break this source, as it does in ryll (finding 6). The price is
   that such a pull request can merge red. The nightly issue and
   the release check (step 5.3) are what stop that from reaching
   a release.

2. **The source's token is created by running the documentation.**
   A script extracts the `shell` block under *Proxmox VE account*
   in `docs/console-sources.md`. It renames `kerbside@pve` to
   `kerbside-lane@pve`, because the action already owns that user
   (finding 2), and runs the block on the node over SSH. If the
   documented commands are wrong, the lane fails, and a doc edit
   that breaks them is tested by the lane. The script refuses a
   block containing any command other than `pveum role add`,
   `pveum user add`, `pveum acl modify` and `pveum user token add`.
   That matters because CI runs this text as root. The token is
   created with `--output-format json`, and its secret goes over
   SSH straight into a 0600 file on the runner. It is masked with
   `::add-mask::` before anything reads it, and is never echoed.
   Kerbside is given only this token. The action's token is used
   only for the lane's own setup reads.

3. **Both privileges are proven necessary, not only sufficient.**
   The same script also makes two more privilege-separated tokens,
   one with a role holding only `VM.Audit` and one holding only
   `VM.Console`. The lane checks them directly against the API,
   before any session is opened:
   - the token without `VM.Audit` gets 403 on the smoke VM's
     `config`;
   - the token without `VM.Console` gets 403 on `spiceproxy`;
   - the documented token gets 403 on `status/stop`, which needs
     `VM.PowerMgmt`.

   With the session passing on the documented token, that settles
   the master plan's open question 4. Neither of the refused
   `spiceproxy` calls mints, so none of them changes the smoke
   VM's password.

4. **Discovery is tested against VMs it must skip.** Over SSH,
   with `qm`, the lane creates diskless 64 MB guests that never
   boot an OS:
   - 101, running with the default `std` display;
   - 102, running with `qxl` and its `smbios1` deleted;
   - 103, stopped with `qxl`;
   - 104, a `qxl` template, if `qm template` accepts a diskless
     VM (if it does not, the step says so and drops 104).

   The lane asserts that the console list for the source is
   exactly the smoke VM. That console's uuid must be
   `100-<smbios uuid>`, with the UUID read from the VM's config
   through the action's token. Its `hypervisor` must be the node
   name, and its host subject
   `OU=PVE Cluster Node,O=Proxmox Virtual Environment,CN=<fqdn>`.
   The daemon log must name vmid 102 at info level. This is the
   oVirt lane's `no-spice-test` lesson applied to every skip rule
   phase 4 wrote.

5. **Mints are counted by PVE, not by kerbside.** The lane records
   the size of `/var/log/pveproxy/access.log` before it starts
   kerbside. It then counts `spiceproxy` POSTs after that point,
   at each checkpoint below, and expects:

   | Checkpoint | Expected mints |
   |------------|----------------|
   | After the first scrapes | 0 |
   | After a refused direct `.vv` request (409, audited) | 0 |
   | After the first proxied session's channels have all connected | 1 |
   | After holding the first session | 1 |
   | After a second session opens while the first is held | 2 |
   | After the lane's own mint, while the second is held | 3 |
   | At the end, after terminating the second | 3 |

   The second session displaces the first. qemu's SPICE server
   serves one client at a time, and disconnects the client it has
   when a new client's main channel links, as it does for any
   SPICE client. So once the second session's channels have
   connected, the first session's ryll must have exited and the
   proxy must have logged each of its relays ending, while the
   second passes frames. The first still running is a defect, "the
   first session was not displaced". That a password change keeps
   a connected client is shown instead by the lane minting once
   itself, with the documented token and kerbside's own request,
   while the second session is held: the second must still pass
   frames afterwards. That is measured on real PVE rather than in
   the direct-qemu lane. Before relying on the log, step
   5.2 confirms its layout from `pve-http-server`'s source, and
   whether it names the token. If it names the token, the count is
   filtered to `kerbside-lane@pve`.

   The oracle keeps two faults apart. A count that is wrong is a
   defect. A session whose channels did not all connect within
   `REUSE_MARGIN` of the first channel's mint is a harness timing
   fault, because a later channel legitimately mints again. That
   fault is reported as such, from the proxy's log timestamps.
   This is the same split `docs/testing.md`'s *Log-derived
   oracles* asks for.

6. **The session checks follow the oVirt lane.** The first session
   is driven by ryll and `smoke-client.py`. The proxy log must
   show the backend leg as tunnelled, over TLS, with a non-empty
   subject pin. The lane then:
   - holds the session past the ticket's 30 seconds and past
     `CONSOLE_TOKEN_DURATION`, and checks that it still passes
     frames;
   - opens the second session from decision 5, which displaces
     the first, and mints once itself while the second is held;
   - terminates the second session over REST, and checks that the
     proxy dropped it.

   New scripts go in `tools/proxmox-e2e/`, built on
   `tools/direct-qemu/start-kerbside.sh`, `setup-mariadb.sh`,
   `generate-tls.sh` and `smoke-client.py`. The oVirt scripts are
   copied where it helps, not refactored. Refactoring them would
   change a lane that runs only nightly, so a mistake there gets
   no signal on the pull request that makes it. A shared library
   is *Future work*.

7. **No live credential reaches an artifact or the log.** Every
   token secret is masked as soon as the runner holds it. The lane
   mints once itself (decision 5), and never prints, logs or writes
   that response: its SPICE password and connect host are held in
   memory for the scan and the redactor, and every other SPICE
   password and connect host exists only inside kerbside. Before
   upload, the lane scans every
   artifact for each token secret and the literal
   `pvespiceproxy:`, and fails if it finds any of them. It never
   uploads:
   - `sources.yaml`;
   - any `.vv` file;
   - a token file;
   - ryll's unredacted `/tmp/ryll.log`, which ryll's lane already
     excludes.

   The scan's matcher is a pure function with a unit test, so
   that it can be mutation-checked.

8. **The lane builds before it books a node.** The proxy wheel
   and ryll are built before the deploy step, as ryll's lane does,
   so a compile error never spends a PVE node. The runner is
   `[self-hosted, vm, debian-13, l]`, like the oVirt lane's. The
   workflow carries ryll's fork guard, for the same reason
   (finding 6). It has a concurrency group that cancels a
   superseded run on the same ref.

9. **A defect the lane finds in the driver is fixed here.** Each
   fix gets a unit test that fails without it, and an entry in
   *Bugs fixed during this work*. A defect that needs a design
   change, rather than a fix, is recorded and filed as an issue,
   and the lane's expectation is narrowed to match, with the issue
   cited.

## Step plan

Steps 5.1 to 5.3 run in order, one sub-agent each, in this
worktree, because each runs `tox` and `pre-commit`. Step 5.4 is
the lane's first runs, which only CI can do. The management
session runs it and sends each fix back to a sub-agent.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5.1 | high | opus | none | **Node preparation.** Read decisions 2, 3 and 4, and findings 1 to 4. Read `shakenfist/actions`' `deploy-proxmox-on-shakenfist/action.yml`, `tools/proxmox-deploy.sh`, `tools/proxmox-publish-node.sh` and `ansible/tasks/proxmox/{api,smoke}.yml` on `main` (a clone is at `../actions`; read with `git show origin/main:<path>`). From them, work out how the runner reaches the node over SSH. Write `tools/proxmox-e2e/account_commands.py`, a pure function plus CLI: it extracts the `shell` block under `### Proxmox VE account` in `docs/console-sources.md`, renames the user, refuses any command outside decision 2's allowlist, and refuses a missing or empty block. Write `tools/proxmox-e2e/prepare-node.sh`. It runs those commands on the node, creating the documented token and decision 3's two single-privilege tokens, each secret going straight from SSH JSON output to a 0600 file under the work directory and masked first. It then creates decision 4's VMs with `qm`. Nothing it prints may contain a secret; print token IDs and file paths only. Write `tools/proxmox-e2e/check-privileges.py`, which makes decision 3's three refused calls with `requests` and a five-second timeout, and makes no other call. Unit tests in `kerbside/tests/unit/test_proxmox_e2e_tools.py` cover: the extractor against the real doc, a renamed user, each refused command shape, a missing block, and `check-privileges.py` against a mocked `requests` (403 passes, 200 fails, no other request is made). Mutation-check the allowlist and the rename with the `mutation-check` skill, and report the script's path. Commit: "Prepare a Proxmox node the way the docs say." |
| 5.2 | high | opus | none | **Deploy and drive.** Read decisions 4 to 7 and 9, and finding 5. Before writing the mint-count oracle, read `pve-http-server`'s request logging (git.proxmox.com, `src/PVE/APIServer/AnyEvent.pm`, the access log writer) and record the line format, with its source line, in the script's header. Write `tools/proxmox-e2e/deploy-kerbside.sh`, modelled on `tools/ovirt-e2e/deploy-kerbside.sh` but without the oVirt SDK. It writes one `type: proxmox` source (the URL from the action's `api_url`, the documented token, `ca_cert` inline from `ca_file`) to a 0600 `sources.yaml`, and waits for the source to leave the errored state. Write `tools/proxmox-e2e/drive-console.py`, modelled on `tools/ovirt-e2e/drive-console.py`, with decision 4's discovery assertions, decision 5's checkpoints and oracle, decision 6's session checks, and decision 7's artifact scan. Keep the parsing and scanning pure (access-log line to mint, proxy log to backend-leg facts, files to secrets found), and unit-test them in `kerbside/tests/unit/test_proxmox_e2e_tools.py`. That includes the timing-fault versus count-defect split. Mutation-check the mint counter, the timing split and the scan. Write `tools/proxmox-e2e/README.md` in the style of `tools/ovirt-e2e/README.md`, with its topology and a mermaid diagram of the path proven. Nothing can run against a node locally, so say plainly what was not exercised. Commit: "Drive a proxied Proxmox console end to end." |
| 5.3 | medium | sonnet | none | **Workflow and docs.** Read decisions 1, 7 and 8, and findings 1, 6 and 7. Write `.github/workflows/proxmox-functional.yml`: decision 1's triggers and path list, decision 8's runner, fork guard and concurrency group, and `timeout-minutes` on every step (60 on the deploy step, since composite actions cannot carry their own). Order the steps: checkout kerbside, install the Rust prerequisites, build the proxy wheel and ryll (copy those steps from `functional-tests.yml`'s `ovirt_matrix`, keeping its comments' reasons), `uses: shakenfist/actions/deploy-proxmox-on-shakenfist@main`, `prepare-node.sh`, `check-privileges.py`, `deploy-kerbside.sh`, `drive-console.py`, then the artifact uploads with `if: always()` and `continue-on-error: true`. Add a scheduled-failure job calling `tools/file-nightly-failure-issue.sh proxmox`, copied from `sf-e2e-functional.yml`. Pass action outputs through `env:`, never interpolated into `run:`. Keep every `run:` to five lines or fewer, with longer logic in `tools/proxmox-e2e/`. Docs: add a `## The Proxmox end-to-end lane` section to `docs/testing.md` after the oVirt one, and a row to its CI tiers table. In `docs/development.md`'s *Releasing*, add `Nightly proxmox lane failing` to the issues to check before tagging, and the Proxmox target to what to dispatch. Run `actionlint` if available, `tools/check-required-checks.sh`, and `tools/check-backend-tls-claims.py`. Describe the lane as it is, with no references to this plan or its decisions. Commit: "Run a Proxmox lane nightly and on driver changes." |
| 5.4 | high | opus | none | **First runs.** The management session pushes the branch and opens the pull request, so the lane runs through its own path filter. It reads each failure with `ci-status`, decides whether it is the harness, the action, PVE or the driver, and briefs a sub-agent with the run's evidence. Each fix is its own commit, named for what it fixes, and a driver fix follows decision 9. Once a run on the head is green, the management session settles open question 4 in the master plan, citing the run. It also checks that `docs/console-sources.md`'s Proxmox section states nothing the run contradicted, and records the run in *Outcome*. |

## Risks and mitigations

- **A live credential reaches the public log.** The lane holds
  three API token secrets, and kerbside holds minted SPICE
  passwords and connect hosts. Mitigation: decision 2's
  file-only path for secrets, `::add-mask::` before first use,
  and decision 7's pre-upload scan, which fails the lane. The
  management session reads every `echo`, `print` and `run:` line
  in the diff for a value that could carry a secret, and searches
  the first green run's step log for `pvespiceproxy:` before
  recording it.
- **CI runs repository text as root.** Decision 2 executes a
  documentation block on the node. Mitigation: the allowlist,
  unit-tested and mutation-checked, and a node that is discarded
  with the runner's namespace.
- **The timing oracle flakes.** Under TCG emulation, or on a busy
  sfcbr, a session's later channels can connect after the reuse
  window. Then a second mint is correct behaviour, not a
  regression. Mitigation: decision 5's split reports that case as
  a timing fault, distinct from a wrong count, and the README says
  what each means.
- **First contact finds a lot.** The driver was written from PVE's
  source, and the lane may find several defects in it. Mitigation:
  decision 9. Fixes stay in this phase only when they are fixes,
  each with a reproducing unit test. Anything larger is filed.
- **The lane goes red for reasons that are not kerbside's.** A PVE
  release, the no-subscription repository or the action can break
  it. Mitigation: the actions repository's weekly substrate lane
  breaks on the same causes and tells them apart. The nightly
  issue names the run, and decision 1 keeps such breakage out of
  the merge queue.
- **An advisory lane lets a regression merge.** Mitigation: the
  nightly issue the next morning, and step 5.3's pre-release
  check, which makes an open Proxmox nightly issue a release
  blocker, as the oVirt and OpenStack ones are.

## Definition of done

- [x] `proxmox-functional.yml` triggers on `schedule`,
      `workflow_dispatch` and a path-filtered `pull_request`,
      has no `merge_group` trigger, and is not a required check;
      `tools/check-required-checks.sh` passes.
- [x] A lane run on the pull request's final head is green, and
      is linked in *Outcome*.
- [x] In that run, the console list for the source is exactly the
      smoke VM, with uuid `100-<smbios uuid>`, the node as
      `hypervisor`, and the subject `CN=` the node FQDN. VMs 101
      to 103, and 104 if created, are absent, and the daemon log
      names 102.
- [x] In that run, PVE's access log shows 0 mints after the scrapes
      and after the refused direct `.vv`, 1 after the first
      session connects and after its hold, 2 after the second, and
      3 after the lane's own mint and at the end. The second
      session displaces the first, and still passes frames after
      the lane's own mint.
- [x] In that run, the first session survives past 30 seconds and
      past `CONSOLE_TOKEN_DURATION`, and the proxy drops the second
      on a REST terminate.
- [x] In that run, the token without `VM.Audit` and the token
      without `VM.Console` are refused with 403, and the
      documented token is refused `status/stop`.
- [x] The documented token was created by the commands in
      `docs/console-sources.md`, extracted by
      `account_commands.py`, which a unit test runs against that
      file.
- [x] No artifact of that run contains a token secret or
      `pvespiceproxy:`. The lane enforces this. The management
      session also searched that run's step log for
      `pvespiceproxy:` and found nothing.
- [x] Every pure oracle in `tools/proxmox-e2e/` has a unit test,
      and the mutation runs of steps 5.1 and 5.2 were all killed,
      with their scripts' paths recorded in *Execution notes*.
- [x] The master plan's open question 4 is marked settled, citing
      the run.
- [x] `docs/testing.md` has the lane's section and CI tiers row.
      `docs/development.md`'s *Releasing* names the Proxmox
      nightly issue.
- [x] `tox -epy3`, `tox -eflake8`, `pre-commit run --all-files`
      and `tools/check-backend-tls-claims.py` pass.
- [x] The master plan's Execution table records this phase's
      merge commit, set by the next phase's close-out.

## Bugs fixed during this work

None yet.

## Future work

- `deploy-proxmox-on-shakenfist` creates a `privsep=0` token with
  `PVEVMUser` and `PVEAuditor`, and describes it as
  least-privilege. Once this lane has proven the two-privilege
  role, the action could create the documented account instead.
  Every consumer would then get the proven token. That is a change
  to `shakenfist/actions`, to be filed there when this phase lands.
- `tools/ovirt-e2e/` and `tools/proxmox-e2e/` overlap. A shared
  library for deploying kerbside on a runner, launching ryll and
  scanning artifacts would remove the duplication. It should land
  in a pull request that dispatches both lanes.
- Live migration and the subject re-read, from phase 4's *Future
  work*, need a two-node cluster, which the action does not build.

## Execution notes

- 2026-10-29 AEDT, back brief. Mikal confirmed decision 1's tier
  (nightly, on dispatch, and advisory on Proxmox-path pull
  requests), decision 2's run of the documentation block behind
  the allowlist, and that this session may push the branch and
  open the pull request after step 5.3 so the lane can iterate.
- Steps run in sequence, 5.1 to 5.3, because each runs `tox` and
  `pre-commit` in the shared worktree.
- Step 5.1 added a check the plan did not ask for.
  `prepare-node.sh` reads each token's effective privileges with
  `pveum user token permissions` and requires exactly the expected
  set. Without it, a token whose ACL never applied would be
  refused everything and pass the 403 checks while proving
  nothing. The script also waits for `/cluster/resources` to
  report each fixture guest in its intended state before kerbside
  scrapes. Its SSH user and key, `debian` and `/srv/github/id_ci`,
  come from the action's playbook, since the action exposes
  neither as an output. Twenty-five mutants were killed
  (`step-5.1/mutants.yaml` in the session scratchpad). Four PVE
  behaviours are written from source or memory and are first
  checked by the lane: the JSON shape of
  `pveum user token permissions`, whether `qm template` accepts a
  diskless guest, that `qm start` leaves a deleted `smbios1`
  deleted, and the 403 for each refused call. A template can never
  be running, so guest 104 is skipped as a stopped guest too, and
  does not isolate the template rule.
- Step 5.2 found that `pve-http-server`'s `log_request`
  (`src/PVE/APIServer/AnyEvent.pm`) writes
  `$realip - $userid [$timestr] "$requestline" $code $length`, and
  that `$userid` is the whole token ID for a token request. Mints
  are therefore counted for the documented token alone. The log is
  filtered on the node before it is read, because spiceproxy's
  CONNECT lines in the same file carry connect hosts. The proxy
  logs neither TLS nor the pin for a tunnelled leg at info, so
  rather than change Rust the lane sets
  `RUST_LOG=info,shakenfist_spice_protocol::client=debug`. Of the
  ryll crate's lines that this enables, the dial line prints its
  target redacted, and the pin line prints only the subject.
  Decision 5's "within `REUSE_MARGIN`" is implemented as the code's
  rule: a channel is late at 15 seconds after its session's first,
  which is the 30-second ticket less `REUSE_MARGIN` less five
  seconds of slack. The step added checkpoints after the hold and
  at the end. It cross-checks kerbside's mint audit events against
  PVE's count, and scans for the action's token, the auth seed and
  each session's backend ticket as well. It terminates the first
  session and asserts that the second survives. Comment-only
  changes to `backend.rs` and `relay.rs` name the new consumer of
  their log lines. Thirty-five mutants were killed
  (`step-5.2/mutants.yaml`).
- Step 5.3 added `docs/console-sources.md` to decision 1's path
  filter. The lane runs that page's account block, so without it
  an edit to the block would not run the lane, and decision 2's
  claim would be false. `check-privileges.py` runs with the venv
  that `deploy-kerbside.sh` makes, which has `requests`, so it runs
  after kerbside has started; its refused calls mint nothing. The
  nightly runs at 05:30 UTC, after the other nightlies.
- 2026-10-29 AEDT, lane run 40796050420 on a real PVE node. Every
  check up to the second session passed: discovery listed exactly
  the smoke guest with its uuid, hypervisor and subject, and the
  daemon log named guest 102; the direct `.vv` was refused 409 and
  audited; the first session minted once, its four backend legs
  were tunnelled, over TLS, and pinned to the node's subject; it
  passed frames after a hold past its ticket's and its token's
  expiry; and PVE's access log counted 0, 0, 1, 1 and then 2 mints
  once the second session had connected. The second session's mint
  (`set_password` and `expire_password`) landed at 01:15:50.70 UTC
  and the first session survived it. At 01:15:50.812, while the
  second session's main channel linked to qemu, between its
  `main: authenticating...` at .770 and `main: connected
  successfully` at .818, qemu closed all four of the first
  session's backend connections (`relay ended with error ... peer
  closed connection without sending TLS close_notify`), and the
  first ryll exited. That is spice-server's default single-client
  behaviour, which disconnects the existing client when a new
  client's main channel links. It is not a kerbside defect;
  decision 5's expectation that the first session survives a
  second was wrong. Mikal decided that the lane expects the
  displacement, and keeps a real-PVE proof that a password change
  alone keeps a connected client. So the lane now asserts that the
  second session displaces the first, then mints once itself with
  the documented token while the second is held, and asserts that
  the second still passes frames; it terminates the second rather
  than the first. Decisions 5 to 7 and the Definition of done were
  corrected to match.

- 2026-10-29 AEDT, the lane went green on `5f8f92e`
  ([run 40805221046](https://github.com/shakenfist/kerbside/actions/runs/40805221046)). The four PVE behaviours step
  5.1 wrote from source or memory all held: the JSON of
  `pveum user token permissions` with and without `--path`, a
  diskless guest accepted by `qm template`, a deleted `smbios1`
  staying deleted across `qm start`, and a 403 for each refused
  call. The run's step log was searched for `pvespiceproxy:` and
  holds none. Mutation sets, in the session scratchpad:
  `step-5.1/mutants.yaml` (25), `step-5.2/mutants.yaml` (35),
  `codeql-fix/mutants.yaml` (40, superseding step 5.2's scan
  mutants), `review-r1/mutants.yaml` (9), `review-r2/mutants.yaml`
  (31), `review-r3/mutants.yaml` (24) and
  `displace/mutants.yaml` (35), all killed.

## Outcome

The lane went green against a real PVE node on 2026-10-29 (AEDT),
on `5f8f92e`
([run 40805221046](https://github.com/shakenfist/kerbside/actions/runs/40805221046), 15m47s).
In that run:

- the documented `pveum` block created the source's token, which
  held exactly `VM.Audit` and `VM.Console` on `/vms/100` and
  nothing outside `/vms`. Tokens lacking either privilege were
  refused with 403, and the documented token was refused
  `status/stop`. That settles the master plan's open question 4;
- discovery listed exactly `100-<smbios uuid>` on `pve1`, pinned to
  the node's subject. Guests 101 to 104 were skipped, 104 as a
  template, and the daemon log named 102;
- PVE's access log counted 0, 0, 1, 1, 2, 3 and 3 mints at the
  checkpoints. Every backend leg was tunnelled, TLS, and pinned;
- the first session survived past its ticket and its token, the
  second displaced it, the second survived the lane's own mint,
  and a REST terminate dropped it;
- no uploaded artifact held any of the 14 values scanned for, and
  the step log contains no `pvespiceproxy:`.

The lane's first contact with PVE found no defect in the phase 4
driver. It found one wrong expectation in this plan, recorded under
*Execution notes*: a second session displaces the first. Four
automated review rounds ran 2, 1, 1 and 0 fix items in code, with
CodeQL's six false-positive alerts cleared by restructuring rather
than dismissal. The mutation sets total 164 mutants across the
steps and rounds, all killed.

## Back brief

Before executing any step of this plan, back brief the operator
on the intended approach and on any deviation from it. Three
points need an answer before step 5.1 starts:

1. **Decision 1, the tier.** Advisory on pull requests that touch
   the Proxmox path, nightly, and on dispatch, rather than a
   required smoke-tier lane. This is a spend of sfcbr capacity and
   a choice about what may merge red, so it is Mikal's.
2. **Decision 2, running the documentation as root.** The
   alternative is a hand-written copy of the commands with a unit
   test that compares it to the doc.
3. **Step 5.4, pushing early.** The lane can only run in CI, so
   this session needs to push the branch and open the pull request
   after step 5.3, and then push fix commits while the lane
   iterates. Each run books a PVE node on sfcbr.
