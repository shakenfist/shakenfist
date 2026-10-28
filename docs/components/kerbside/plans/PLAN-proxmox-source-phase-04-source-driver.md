# Proxmox source phase 4: the source driver

This is the detailed plan for phase 4 of
[PLAN-proxmox-source.md](/components/kerbside/plans/PLAN-proxmox-source/). Read the
master plan first. Its *Why this is not another `ovirt.py`*,
*What the measurements settled* and *Three protocol details
that cost an afternoon* are assumed knowledge here. So are the
[phase 3a plan](/components/kerbside/plans/PLAN-proxmox-source-phase-03a-connect-time-minting/),
whose minting hook this driver implements, and the
[phase 3b plan](/components/kerbside/plans/PLAN-proxmox-source-phase-03b-tunnel-transport/),
whose `Tunnel` this driver returns.

## Prompt

All work for this phase lands in `shakenfist/kerbside`. It
adds a source driver, `kerbside/sources/proxmox.py`, and
changes the direct `.vv` handler, the consoles page, the
Ansible role's source checker and the documentation. It
changes nothing in Rust or the protobuf contract. It widens
four schema columns with one migration: `consoles.uuid`,
`consoletokens.uuid` and `auditevents.uuid` were created as
`String(36)`, too short for decision 1's
`<vmid>-<smbios uuid>` (38 to 46 characters), and
`sources.username` as `String(50)`, which a token ID
(`user@realm!tokenid`) can exceed. All four become
`String(255)`.
Consult `ARCHITECTURE.md`, `docs/console-sources.md`,
`docs/proxy-architecture.md` and `kerbside/sources/ovirt.py`,
the other minting driver. Run `tox -eflake8`, `tox -epy3` and
`pre-commit run --all-files` before every commit.

Planning effort: **high**. The driver handles a new
credential, the PVE API token, and returns two more, the
SPICE password and the signed CONNECT host. Its identity
scheme decides what every audit event for a Proxmox console is
filed under. And its mint replaces the VM's SPICE password,
so a mint in the wrong place breaks other sessions.

## Scope

In scope:

- `kerbside/sources/proxmox.py`: discovery, the per-node host
  subject, and `mint_console_ticket` returning a `MintedTicket`
  with a `Tunnel`.
- Registering the `proxmox` source type.
- The direct `.vv` handler refusing a source that offers only
  proxied consoles, before it mints, and the consoles page not
  offering one.
- The Ansible role's source checker and argument spec accepting
  a `proxmox` source.
- The source's configuration reference in
  `docs/console-sources.md`, its row in `ARCHITECTURE.md`, and
  correcting the repository's `add-source-type` skill.

Out of scope:

- A CI lane against a real node. That is phase 5, and it is the
  first time this driver meets PVE. This phase tests against
  fixtures built from the measured responses in the master plan
  and from PVE's source.
- The Proxmox use-case page, which is phase 6.
- PVE clustering, LXC containers and VNC consoles, as the master
  plan rules out. Nothing here assumes a single node, but only a
  single node will be tested.
- A Proxmox API served under a certificate from a CA other than
  the cluster's own, such as an ACME certificate on `pveproxy`.
  See *Future work*.

## What the survey found

Facts about PVE are cited from its source at the current heads
of `qemu-server`, `pve-manager`, `pve-common` and
`pve-access-control` on git.proxmox.com, read on 2026-10-28.

1. **One call lists every VM, with its node.**
   `GET /cluster/resources?type=vm` returns each guest's `vmid`,
   `node`, `type` (`qemu` or `lxc`), `status`, `name` and
   `template` (`pve-manager` `PVE/API2/Cluster.pm:264`). It is
   open to every user, filtered by the caller's `VM.Audit`. The
   master plan's "discovery over `/nodes/{node}/qemu`" works too,
   but needs a call per node, and finds a migrated VM's new node
   only on the next scrape. Corrected at source.

2. **SPICE is on for `qxl*` and `virtio*` displays.** qemu is
   started with `-spice` when the VM's `vga` type is `qxl`,
   `qxl2`, `qxl3`, `qxl4` or starts with `virtio`
   (`qemu-server` `src/PVE/QemuServer.pm:3500`, and
   `vga_conf_has_spice` at `:2961`). `status/current` reports
   `spice: 1` by the same rule (`src/PVE/API2/Qemu.pm:3524-3528`).
   `GET .../config` returns pending values unless asked for
   `current=1`.

3. **Each mint sets the VM's SPICE password.** `spiceproxy` runs
   `set_password` and then `expire_password +30` on the VM's
   qemu (`Qemu.pm:3416-3417`). So the password is good for
   30 seconds from the mint, and any call to `spiceproxy` changes
   the password that a session's later channels would
   authenticate with. Discovery must never mint.

4. **The proxy address can be chosen.** `spiceproxy` takes an
   optional `proxy` parameter (`Qemu.pm:3391`). Without it PVE
   returns `http://<hostname -f of the VM's node>:3128`
   (`pve-access-control` `src/PVE/AccessControl.pm:637-659`).
   PVE's schema recommends "the same node you use to connect to
   the API" (`pve-common` `src/PVE/JSONSchema.pm:782`). So
   kerbside can be told to CONNECT to the API host it already
   reaches. The master plan says "the node's FQDN is
   load-bearing on our side". That is still true of the host
   subject, whose `CN` is that FQDN. It is not true of the
   dial. Corrected at source.

5. **The host subject can be read without minting.**
   `spiceproxy`'s `host-subject` is the subject of
   `/etc/pve/local/pve-ssl.pem` as `X509_NAME_oneline` prints
   it, with the leading `/` dropped and each `/` before an
   attribute turned into `,` (`AccessControl.pm:608-628`).
   `GET /nodes/{node}/certificates/info` returns each node
   certificate's `filename` and its `subject` in the same oneline
   form (`pve-manager` `PVE/API2/Certificates.pm:51-75`,
   `pve-common` `src/PVE/Certificate.pm:301`). It is open to
   every user.

6. **New VMs get an SMBIOS UUID.** `qm create` writes `smbios1`
   with a generated UUID when none is given
   (`Qemu.pm:1549-1551`). A VM whose config was written by other
   means may still have none.

7. **Two privileges are enough.** `spiceproxy` checks
   `VM.Console` on `/vms/{vmid}` (`Qemu.pm:3383`), and
   `status/current` and `config` check `VM.Audit` (`:3477`,
   `:1829`). `cluster/resources` and `certificates/info` are
   open to all users. So the master plan's open question 4 has
   an answer from the source: a role with `VM.Audit` and
   `VM.Console` on `/vms`. The prototype's `PVEAuditor` on `/`
   is not needed. Phase 5 can prove that against a real node.

8. **The direct handler mints before it builds the file.**
   `ConsolesDirectVirtViewer` mints a fresh ticket for any
   `MINTS_TICKETS` source (`kerbside/api.py:530-557`). It then
   builds a `.vv` pointing at the console's `hypervisor` and
   `insecure_port`, which for Proxmox name nothing dialable. A
   refusal placed after the mint is too late: the mint has
   already replaced the VM's password (finding 3). Sessions
   already connected survive that (phase 3a, survey finding 3).
   A session whose later channels reuse its stored ticket does
   not. The consoles page offers *Direct* to every administrator
   for every console (`kerbside/api/templates/consoles.html:146-150`).

9. **The source schema already fits an API token.** A source has
   `url`, `username`, `password` and `ca_cert`
   (`kerbside/main.py:192-197`), and `password` is a secret
   field. A token ID (`user@realm!tokenid`) and its secret fit
   `username` and `password`, and PVE takes them as one header,
   `Authorization: PVEAPIToken=<id>=<secret>`. The survey
   said no migration was needed, which was wrong: the initial
   schema made `sources.username` a `String(50)`, which a long
   realm or token name can exceed, and made every console
   `uuid` column a `String(36)`, which decision 1's
   `<vmid>-<smbios uuid>` never fits. `db.py` declares them
   unbounded and the unit tests run on SQLite, which ignores
   lengths, so neither showed. A migration widens all four to
   `String(255)`. `requests` is already a runtime dependency
   (`pyproject.toml:74`), so no client library is added.

10. **The role refuses unknown source types.** The Ansible
    role's checker has a closed table of types and their required
    fields (`ansible/roles/kerbside/files/kerbside-check-sources.py:57-63`),
    and `meta/argument_specs.yml:74-84` lists the types. A role
    deployment refuses a `proxmox` source until both learn it.
    The checker's tests are in
    `kerbside/tests/unit/test_ansible_role.py`.

11. **The add-source-type skill is stale.** Its step 3 says a
    console dict has the keys `source`, `uuid`, `name`,
    `hypervisor` and `port`. The drivers yield `hypervisor_ip`,
    `insecure_port`, `secure_port` and `host_subject`, and no
    `port` (`kerbside/sources/ovirt.py:137-146`). Its step 6
    assumes a new client library, and it does not mention the
    role's checker. Corrected in step 4.4.

12. **The minting budget is tight.** A mint runs under
    `minting.MINT_DEADLINE`, which is 20 seconds
    (`kerbside/sources/minting.py:24`). The session reuses a
    ticket until `REUSE_MARGIN`, 10 seconds, before it expires
    (`kerbside/rpc/servicer.py:39`). So a 30-second Proxmox
    ticket serves a session's channels for about 20 seconds. The
    authorize path already refuses a minted tunnel missing a
    field (`kerbside/rpc/servicer.py`, `tunnel_complete`), so the
    driver need not duplicate that check.

13. **Documentation was scheduled late.** The master plan puts
    `console-sources.md` in phase 6. This repository documents a
    user-visible change in the change that makes it, and a
    source type is unusable without its configuration reference.
    This phase writes that reference and the `ARCHITECTURE.md`
    row, and phase 6 keeps the use-case page. Corrected at
    source.

The master plan's phase 4 row (findings 1 and 13), its FQDN
constraint (finding 4) and open question 4 (finding 7) are
corrected in the planning commit, and the `index.md` row
describes this phase from the survey.

## Decisions

1. **A Proxmox console's `uuid` is `<vmid>-<smbios uuid>`.**
   This is the decision most likely to be argued with. The vmid
   alone is what an operator sees, but PVE hands out the lowest
   free vmid, so a deleted VM's number comes back on the next VM
   created. Under a bare vmid, two different machines would share
   one console's audit trail, and a token issued just before the
   swap would connect to the new machine. The SMBIOS UUID alone
   is unique, but nothing maps it back to a vmid without reading
   every VM's config, and the mint needs the vmid on a
   30-second clock. Both together give a unique identity that
   the mint can parse. The mint checks the VM's current SMBIOS
   UUID against it before minting. A VM with no SMBIOS UUID
   (finding 6) is skipped at discovery with an info-level log
   naming its vmid, and documented.

2. **Discovery makes one call for the guests, one per node and
   one per candidate VM.**
   - `cluster/resources?type=vm` lists the guests. The driver
     keeps `qemu` guests that are `running` and not templates.
   - For each node with a candidate, `certificates/info` gives
     the host subject, from the entry whose `filename` is
     `pve-ssl.pem`, converted as finding 5 describes.
   - For each candidate, `config?current=1` gives `vga` and
     `smbios1`. The driver keeps VMs whose display runs SPICE by
     finding 2's rule, which it states with its source.

   Each console is yielded with:
   - `hypervisor` set to the node name;
   - `hypervisor_ip`, `insecure_port` and `secure_port` empty,
     since a Proxmox console has no address of its own;
   - `name` set to the VM's name;
   - `host_subject` set to the node's subject.

   A node whose subject cannot be read yields no consoles, with a
   warning, rather than consoles that cannot be pinned.

3. **The mint makes three calls.** It parses the vmid from the
   uuid. `cluster/resources?type=vm` finds the VM's current
   node, so a migrated VM still mints. `config?current=1`
   confirms the SMBIOS UUID; on a mismatch the driver raises
   `ConsoleTicketUnavailable`, naming the vmid but not the
   UUID. Then a `POST` to `spiceproxy` mints, with the `proxy`
   of decision 4. The driver returns
   `MintedTicket(password, t0 + 30, Tunnel(proxy, host, tls-port))`.
   `t0` is taken before the first request, and 30 is a named
   constant citing `Qemu.pm:3417`. The `lifetime` asked for
   cannot be honoured and is not sent. Each request has a
   five-second timeout, so three fit inside `MINT_DEADLINE`.

4. **The CONNECT goes to the API host.** The mint passes the
   host of the source's `url` as `proxy`, as PVE recommends
   (finding 4). The broker then needs to reach only the host it
   already talks to, on port 3128 as well as the API port, and
   the node's FQDN never has to resolve from the broker. A
   source whose `url` names a host other than a PVE node, such
   as a load balancer, needs 3128 forwarded too. The docs say
   so.

5. **The source's `ca_cert` is the cluster CA, and it does both
   jobs.** It verifies the API through `requests`'s `verify`,
   written to a temporary file as `ovirt.py` does and removed in
   `close()`. The authorize path already sends it to the proxy
   as the backend leg's CA. It is PVE's `pve-root-ca.pem`, which
   signs the node certificate that qemu's SPICE listener uses.
   The `ca` that each mint returns is not used. Trusting it
   would let the API choose the CA the backend leg is checked
   against.

6. **Credentials are an API token, nothing else.** `username`
   is the token ID and `password` its secret, sent only in the
   `Authorization` header. A password login is not supported:
   its ticket expires after two hours, it would need a cookie
   and CSRF handling, and a token can be limited to decision 7's
   privileges with privilege separation. Neither the header, the
   secret nor any minted value is logged. A failure is logged by
   exception class and HTTP status only, as the authorize path
   does.

7. **Least privilege is `VM.Audit` and `VM.Console` on `/vms`**,
   from finding 7. The docs give the `pveum` commands for a role
   with exactly those, and a privilege-separated token holding
   it. Phase 5's lane should create its token that way, so that
   the claim is proven rather than read.

8. **A source declares whether it offers direct consoles.**
   `BaseSource` gains `DIRECT_CONSOLES = True`, and the Proxmox
   driver sets it `False`. `ConsolesDirectVirtViewer` looks the
   driver up and refuses with 409 before it reads the source's
   secrets or mints. It audits the refusal, as it audits a
   non-administrator's. The consoles page omits *Direct* for
   consoles whose source's driver sets it `False`. The handler
   is the guarantee; the page is a courtesy. A flag on the
   driver, rather than a check on the type name, keeps the rule
   with the source that needs it.

9. **This phase's tests are unit tests.** They mock `requests` at
   the driver's boundary, with fixtures shaped like the master
   plan's measured `spiceproxy` response and the PVE source
   above. They assert:
   - the requests made, with their parameters and timeouts;
   - what is yielded and returned;
   - that a sentinel token secret, password and connect host
     reach no log record, on success and failure paths alike.

   The first contact with a real node is phase 5's lane.

## Step plan

The steps run in order, one sub-agent each, in this worktree.
Each one runs `tox`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4.1 | high | opus | none | **The driver.** Read decisions 1-6 and 9, and findings 1-6, 9 and 12. Write `kerbside/sources/proxmox.py` with `ProxmoxSource(base.BaseSource)`, `MINTS_TICKETS = True` and `DIRECT_CONSOLES = False`. Add the attribute to `BaseSource` with a one-line comment, defaulting to `True`. Model the CA tempfile, `errored` handling and `close()` on `kerbside/sources/ovirt.py`. Use `requests` with a five-second timeout on every call and the `Authorization: PVEAPIToken=<username>=<password>` header. Implement discovery (decision 2), the subject conversion (finding 5) as a small pure function, the uuid scheme (decision 1) with a pure parse function, and `mint_console_ticket` (decisions 3 and 4). Each mint makes exactly three requests, in that order. A VM that is not running, has no SPICE display, has moved off every node, or whose SMBIOS UUID differs raises `ConsoleTicketUnavailable` with a message naming the vmid and no secret. Register `proxmox` in `kerbside/sources/__init__.py:driver_class`. Write `kerbside/tests/unit/test_sources_proxmox.py`, modelled on `test_sources_ovirt.py`, covering every path above. It must assert that every `requests` call carries a timeout, that a mint sends `proxy` equal to the source URL's host, and that sentinel secret, password and connect-host values appear in no captured log record, on the error paths too. Extend `test_sources_registry.py`. Mutation-check the uuid check, the timeout, the `proxy` parameter and the `t0` ordering with the `mutation-check` skill, and report the script's path. Commit: "Add a Proxmox VE source driver." |
| 4.2 | medium | opus | none | **No direct consoles.** Read decision 8 and finding 8. In `ConsolesDirectVirtViewer` (`kerbside/api.py:464`), after the administrator check and the console and source lookups, refuse with 409 and the message "this source offers proxied consoles only" when the source type's driver has `DIRECT_CONSOLES` false. Do it before `db.get_source(..., include_secrets=True)` and before any mint. Audit the refusal against the console with `session_username()`. In `Consoles.get`, pass the template the names of the sources whose driver sets it false, using `driver_class` and treating `KeyError` as direct-capable. Omit *Direct* for those consoles in `consoles.html`. Tests go in `kerbside/tests/unit/test_api.py`: a Proxmox console's direct request is refused with 409, the driver's mint is never called (assert on a mock), and the audit event is written. The page has no direct link for that console and keeps one for an oVirt console. Mutation: moving the refusal after the mint must fail a test. Commit: "Refuse direct consoles for proxied-only sources." |
| 4.3 | medium | sonnet | none | **The role.** Read finding 10. Add `'proxmox': ('url', 'username', 'password', 'ca_cert')` to `TYPE_REQUIRED` in `ansible/roles/kerbside/files/kerbside-check-sources.py`, and name the type in `meta/argument_specs.yml:74-84`, adding that its username is an API token ID. Extend `kerbside/tests/unit/test_ansible_role.py`: a complete Proxmox source passes, and one missing `ca_cert` fails with the existing message shape, which never prints a value. Commit: "Let the role configure a Proxmox source." |
| 4.4 | medium | sonnet | none | **Docs.** Read decisions 1, 4, 5, 6 and 7. In `docs/console-sources.md`, add a `## Proxmox VE` section after oVirt, in its style. Cover: the `sources.yaml` fields (url to port 8006, username a token ID, password its secret, ca_cert the cluster's `pve-root-ca.pem`); the `pveum` commands for a role with `VM.Audit` and `VM.Console` and a privilege-separated token holding it on `/vms`; that the broker must reach the API host on 8006 and 3128; how a console's `uuid` is formed and why; that only running VMs with a `qxl` or `virtio` display are listed; that consoles are proxied only, with no direct `.vv`; and that tickets last 30 seconds and are minted when a session's first channel connects. Add the source to the example `sources.yaml` there. Add a Proxmox row to `ARCHITECTURE.md`'s Source Abstraction table (`:272`). Correct `.claude/skills/add-source-type/SKILL.md` per finding 11, and add the role checker as a step. Run `tools/check-backend-tls-claims.py`. Describe the code as it is, with no references to this plan, its phases or its decisions. Commit: "Document the Proxmox VE source." |

## Risks and mitigations

- **The fixtures are wrong about real PVE.** Nothing in this
  phase talks to a node, so a field name, a subject format or a
  `current=1` behaviour could differ from the fixtures, and the
  tests would still pass. Mitigation: every fixture cites the PVE
  source line it comes from, and decision 2's subject conversion
  is tested on the measured subject in the master plan. Phase 5's
  lane is the real check, and is the next phase.
- **A credential reaches a log.** The token secret, the SPICE
  password and the connect host all pass through this driver.
  `requests` exceptions can quote a URL; this driver's URLs carry
  no secret, but the header does. Mitigation: decision 6 logs
  class names and statuses only, and step 4.1's sentinel tests
  capture every record on success and failure paths. The
  management session reviews every log call in the diff.
- **Discovery mints by accident.** A mint during a scrape would
  replace the password of every VM being scraped. Mitigation:
  step 4.1 asserts that discovery makes no `POST`, and review
  checks that `spiceproxy` is reached only from
  `mint_console_ticket`.
- **The direct refusal is bypassed.** Any future code path that
  mints for a `.vv` must also respect `DIRECT_CONSOLES`.
  Mitigation: the flag lives on the driver, step 4.2's mutation
  test pins the order in the one handler that exists, and the
  docs state the rule.
- **The uuid scheme is disliked after consoles exist.** Changing
  it later orphans the audit trail of every existing Proxmox
  console. Mitigation: it is decision 1 and a back-brief gate,
  so it is settled before any code is written.

## Definition of done

- [x] `driver_class('proxmox')` returns `ProxmoxSource`, by unit
      test.
- [x] `test_sources_proxmox.py` passes, and covers discovery
      filtering (stopped, template, LXC, non-SPICE, no SMBIOS),
      the subject conversion on the master plan's measured
      subject, the uuid parse, and a mint's three requests in
      order with `proxy` equal to the source URL's host.
- [x] A mint for a VM whose SMBIOS UUID differs raises
      `ConsoleTicketUnavailable` and never reaches `spiceproxy`,
      by unit test.
- [x] Discovery makes no `POST`, by unit test.
- [x] Every `requests` call the driver makes has a timeout, by
      unit test, and removing it fails that test.
- [x] A sentinel token secret, password and connect host appear
      in no captured log record, on success or failure paths, by
      unit test.
- [x] A direct `.vv` request for a Proxmox console returns 409,
      writes an audit event, and never calls the driver's mint,
      by unit test. Moving the refusal after the mint fails it.
- [x] The consoles page has no *Direct* link for a Proxmox
      console and keeps one for an oVirt console, by unit test.
- [x] The role's checker accepts a complete Proxmox source and
      refuses one without `ca_cert`, by unit test.
- [x] `docs/console-sources.md` has a Proxmox VE section naming
      both privileges, both ports and the uuid form, and
      `tools/check-backend-tls-claims.py` passes.
- [x] `tox -epy3`, `tox -eflake8` and `pre-commit run
      --all-files` pass.
- [ ] The master plan's Execution table records this phase's
      merge commit, set by the next phase's close-out.

## Bugs fixed during this work

None yet.

## Future work

- An API served under a certificate from a CA other than the
  cluster's, such as ACME on `pveproxy`, fails decision 5's
  single `ca_cert`. Supporting it means a second CA field for the
  API, which is a schema change for one deployment shape.
- A cluster whose API host is a load balancer needs port 3128
  forwarded beside the API (decision 4). Choosing a proxy per
  node instead would need the node's FQDN to resolve from the
  broker again.
- A VM that live-migrates mints on its new node, but its console
  keeps the old node's pinned subject until the next scrape, so it
  cannot be reached for up to a minute. A `MintedTicket` could
  carry a subject the driver reads from the new node's
  `certificates/info`, overriding the console row's.
- The mint's cutoff before the `spiceproxy` POST counts from when
  the driver starts, not from when the caller began waiting, so a
  mint queued behind another for the same console can still set
  the password after its caller gave up. Passing the remaining
  deadline into the driver would close that.

## Execution notes

- 2026-10-28 AEDT, back brief. Mikal confirmed decision 1's
  console uuid (`<vmid>-<smbios uuid>`) and decision 8's direct
  refusal (a 409 before any mint, audited, with the Direct link
  hidden). This session may push the branch and open the pull
  request once the definition of done passes locally.
- Steps run in sequence, 4.1 to 4.4, because each runs `tox` and
  `pre-commit` in the shared worktree.
- Step 4.1 made choices the plan leaves open. The console uuid
  lowercases the SMBIOS UUID, and the parser accepts only that
  form. A failure to list the guests raises, so the source is
  marked errored and keeps its consoles. A failure to read one
  VM's configuration skips only that VM. The mint also refuses
  a guest that is not a qemu VM. Nine mutants were run against
  the uuid check, the GET and POST timeouts, the `proxy`
  parameter, the `t0` ordering, template listing, the SPICE
  check at mint, and logging a failure's text. All nine were
  killed, and the review reproduced that.
- Step 4.4's review corrected the token setup in the docs. A
  privilege-separated token holds the intersection of its own
  privileges and its user's (pve-access-control
  `RPCEnvironment.pm`, the permission calculation for tokens), so
  the user needs the role on `/vms` too, not just the token. The
  step also updated source-type lists the row did not name:
  `docs/schema.md`, `docs/installation.md`, `docs/network-ports.md`,
  `docs/configuration.md`, `docs/index.md`, `docs/proxy-architecture.md`,
  the README's two mentions and AGENTS.md's one-line summary.
- 2026-10-28 AEDT, review round 1. The automated review found
  that a Proxmox console uuid does not fit the `String(36)`
  uuid columns of `consoles`, `consoletokens` and `auditevents`:
  on MariaDB in strict mode every Proxmox console fails with
  "Data too long", and without it the uuid is cut short. The
  plan's premise that the schema needed no change (finding 9)
  missed it because the models are unbounded and the unit tests
  use SQLite. Migration `dabe2f66e369` widens the three columns
  and `sources.username` to `String(255)`, dropping and
  restoring `fk_consoletokens_console` around the change, since
  MariaDB refuses to alter a column a foreign key names. A
  functional test on MariaDB, run in the direct-qemu lane beside
  the ticket lock test, stores the longest uuid the driver can
  produce and a 100-character username, and fails without the
  migration. The same round bounded a mint's elapsed time before
  the spiceproxy POST, refused node names PVE would not accept
  before putting them in API paths, and made the `test_api`
  Proxmox fixture match what discovery stores.
- Review round 2 (0 fix) found that skipping a node or VM whose
  read failed let the scrape finish normally, so the scrape loop
  removed those consoles and, by cascade, the tokens of sessions
  using them. Any failed request now raises, as a failed guest
  list already did, and the source keeps its consoles for that
  scrape. This reverses step 4.1's choice above. The cutoff now
  uses a monotonic clock. Live migration and the cutoff's
  remaining gap are recorded under Future work.

## Outcome

All four steps landed on `proxmox-source-phase-04-source-driver`
on 2026-10-28 (AEDT), one commit each, followed by two review
rounds. 632 unit tests pass; the new MariaDB test,
`test_column_widths`, passed on MariaDB 11.8 locally and runs in
the direct-qemu lane.

Review round 1 found the one defect the plan missed: console
uuid columns 36 characters wide, too narrow for the uuid scheme
of decision 1. Migration `dabe2f66e369` widens them. Round 2
raised no fix items; it made discovery keep a source's consoles
when any read fails. Twenty-nine mutants were run across the
steps and both rounds, and each was killed by its intended test.

The driver has not yet run against a real PVE node. Proving the
`pveum` commands, the mint and the tunnel end to end is the
next phase's lane.

## Back brief

Before executing any step of this plan, back brief the
operator on the intended approach and on any deviation from
it. Two points need an answer before step 4.1 starts:

1. **Decision 1, the uuid scheme.** It names every Proxmox
   console's audit trail, and changing it later orphans them.
2. **Decision 8, a 409 for a direct `.vv`.** The alternative is
   to keep offering direct consoles and accept a broken file.
   The master plan already rules that out; this confirms the
   status code and the hidden link.

Also confirm whether this session may push the branch and open
the pull request once the definition of done is met locally.
