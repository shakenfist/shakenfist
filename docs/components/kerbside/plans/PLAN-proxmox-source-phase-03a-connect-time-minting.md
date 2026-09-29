# Proxmox source phase 3a: minting at connect time

This is the detailed plan for phase 3a of
[PLAN-proxmox-source.md](/components/kerbside/plans/PLAN-proxmox-source/). Read the
master plan first; its *What the measurements settled*
section, and within it *What oVirt does, measured the same
way* and *A second mint revokes the first*, are assumed
knowledge here.

## Prompt

All work for this phase lands in `shakenfist/kerbside`. It
changes no Rust, no protobuf and nothing in ryll: the
`Target` message already carries a `ticket`, and this phase
changes only where that value comes from. Consult
`ARCHITECTURE.md` (particularly *Session termination drops
in-flight connections*, which states the distributed
deployment constraint this plan is built around),
`docs/proxy-architecture.md`, `docs/console-sources.md` and
`docs/schema.md`. Run `tox -eflake8` and `tox -epy3`, and
`pre-commit run --all-files`, before every commit.

Planning effort: **high**. The phase moves a credential's
lifecycle, makes the authorize path call a remote API for
the first time, adds a cross-node locking requirement, and
touches a schema. Ticket and token lifecycle is named in
`PLAN-TEMPLATE.md` as one of the invariants that pushes a
step to high effort.

## Scope

In:

- A source-driver hook for minting a console ticket, and a
  registry mapping a source's `type` to its driver class, so
  the three places that dispatch on type today stop
  hand-rolling it.
- Minting in `AuthorizeConnection` for sources that mint:
  one ticket per session, shared by every channel of that
  session on every proxy node, reused while valid and
  replaced when not.
- Where that ticket lives between channels, and how it is
  removed.
- `ovirt.py` requesting an explicit expiry for every ticket
  it mints, on both the proxied and the direct path.
- `ConsolesProxyVirtViewer` no longer minting or storing an
  oVirt ticket, and a migration clearing the oVirt tickets
  already stored on `Console` rows.
- Settling the supersession question the master plan left
  for this phase, by source reading and by a measurement in
  the direct-qemu lane.
- The oVirt merge-tier lane proving a session outlives its
  ticket.
- Documentation of all of the above, including the four places
  that describe oVirt's ticket lifetime incorrectly today.

Out:

- Anything Proxmox-specific. No `proxmox.py`, no proto
  change, no tunnel. The hook is designed so that phase 4's
  driver fits it (see decision 3), but nothing here depends
  on phase 2 or on a Proxmox node.
- The static source. Its ticket is operator configuration,
  not a minted credential, and stays on the `Console` row.
  Shaken Fist and OpenStack pass no ticket at all and are
  unaffected.
- Two sessions against one VM. A second viewer's mint
  revokes the first viewer's password for new channels. That
  is how oVirt behaves today and it is recorded, not
  changed; see decision 8.
- Encrypting `Console.ticket` at rest for static sources.
- Issue #319 (the proxied `.vv` GET mints a token). This
  phase removes the oVirt ticket write from that GET, which
  shrinks the issue, but the token mint remains and the
  issue stays open.

## What the survey found

Surveyed on 2026-09-28 against `develop` at `5475dab`. The
master plan's section for this phase was written on
2026-09-20, before phases 1b and 2 ran.

1. **The master plan's storage decision does not survive
   the architecture.** It holds the session's ticket "in the
   daemon's memory, keyed by session". Kerbside explicitly
   supports a load balancer spreading one session's channels
   across several proxy nodes (`ARCHITECTURE.md:129-140`,
   `docs/proxy-architecture.md:469-477`), and each node's
   daemon has its own memory. A channel landing on a second
   node would find no ticket, mint its own, and revoke the
   first node's: the per-channel race the master plan
   rejected, reintroduced across nodes. The shared database
   is the only bus every node can reach. Corrected at source
   in the master plan's *What the measurements settled* and
   its phase table, and in the `index.md` row; see
   decision 1.

2. **A channel cannot arrive late.** The master plan
   imagines "a usbredir channel on a device plug" arriving
   after the ticket ages out. `AuthorizeConnection` resolves
   the client's token with `db.get_token_by_token()`, which
   filters `expires > now` (`db.py:498-507`), and
   `CONSOLE_TOKEN_DURATION` defaults to one minute
   (`config.py:234-236`). So no channel of a session is ever
   authorized after its token expires, however long the
   session stays up. (spice-gtk opens its usbredir channels
   at session start and redirects devices over them later;
   it does not open a channel per plug.) The window in which
   a replacement mint could ever be needed is bounded by the
   token, which makes decision 2 possible. Corrected at
   source.

3. **Supersession is settled by source, and it is benign.**
   A new SPICE password is set by QMP `set_password`, whose
   `connected` argument decides what happens to connected
   clients and defaults to `keep`
   (`qemu/qapi/ui.json:25-61`). oVirt's engine builds the
   ticket request without an `existingConnAction`
   (`ovirt-engine/.../SetVmTicketVDSCommand.java:34-47`), so
   vdsm leaves the libvirt `connected` attribute unset
   (`vdsm/lib/vdsm/virt/vm.py:5131-5132`), libvirt passes no
   `connected` to qemu (`libvirt/src/qemu/qemu_hotplug.c:4480-4485`),
   and qemu keeps the clients. Proxmox calls `set_password`
   with no `connected` either
   (`qemu-server/src/PVE/API2/Qemu.pm:3322`). So a second
   mint revokes the first ticket for *new* channels only;
   established channels stay up. The same holds for expiry:
   ryll's Proxmox lane holds a session to mint+120 s against
   a password that qemu expires at +30 s, and passes. Step
   3a.2 measures both on a real qemu anyway, because this is
   the property the design rests on.

4. **oVirt's ticket lifetime is wrong in four places, in
   opposite directions.** The master plan measured 7200 s,
   the engine default `ovirt.py:148` takes by passing no
   expiry. But `docs/use-cases/ovirt.md:90-94` calls oVirt
   tickets "short-lived", its limitations table
   (`:288`) says "roughly two minutes, set by oVirt",
   `ARCHITECTURE.md:269` says the driver "acquires a
   short-lived graphics-console ticket", and
   `tools/ovirt-e2e/drive-console.py:18-19` says they
   "expire in ~120s". After this phase kerbside sets the
   lifetime, so all four are rewritten in step 3a.8 rather
   than patched now.

5. **Source dispatch is hand-rolled three times.**
   `main.py:169-180` builds a driver from `source['type']`,
   and `api.py:420-430` and `api.py:514-526` each construct
   `oVirtSource` directly. The authorize path would be a
   fourth. Decision 3 adds a registry instead.

6. **The oVirt driver can hang.** `oVirtSource.__init__`
   fetches the engine CA with `requests.get` and no timeout
   (`ovirt.py:46-48`). That is tolerable in the scrape loop
   and in a `.vv` request. It is not tolerable in the
   authorize path, where a hang holds a gRPC worker
   (`rpc/server.py:33`, `API_GRPC_WORKERS`) and, under
   decision 1, a row lock that every other channel of the
   session is waiting on.

7. **The tokens table is already per session.**
   `ConsoleToken` (`db.py:442-468`) carries `session_id`,
   `source`, `uuid` and `expires`, one row per `.vv` issued.
   Every channel's authorize reads it. It is the natural
   home for a per-session credential, and its reaper
   (`db.py:533-559`, driven from `main.py:283-287`) already
   exists.

8. **The proxied GET writes a credential.**
   `ConsolesProxyVirtViewer` (`api.py:482-534`) mints an
   oVirt ticket and stores it on the shared `Console` row,
   where the next `.vv` for the same console overwrites it.
   Two concurrent sessions on one console therefore already
   share whichever ticket was minted last. That behaviour
   ends with this phase, because nothing writes the row for
   oVirt any more.

9. **Nothing needs to change in Rust or the proto.**
   `servicer.py:129-141` fills `Target.ticket` from the
   console row. This phase fills it from the session ticket
   instead, for sources that mint. The proxy
   (`rust/kerbside-proxy/src/session.rs:316-328`) calls
   authorize once per channel with no deadline, and waits.

## Decisions

1. **The session's ticket lives on its token row, in the
   shared database.** Two new nullable columns on
   `consoletokens`: `backend_ticket` (string) and
   `backend_ticket_expires` (integer, epoch seconds). The
   authorize path reads and writes them under `SELECT ...
   FOR UPDATE` on the token row, so exactly one channel of a
   session mints, on whichever node it lands on, while the
   others wait and then reuse what it wrote.

   This is the decision a reviewer is most likely to
   dispute, because it puts a live console password in the
   database that the master plan said would stay out of it.
   The comparison that matters is not with memory, which
   does not work (survey finding 1), but with today: today
   an oVirt password sits on the `Console` row for two
   hours, shared between sessions, and survives the session.
   Under this decision it sits on one session's row for at
   most the token's lifetime plus a small slack (a minute
   and a half by default), and is cleared by the reaper once
   it is expired. The alternative of requiring sticky load
   balancing moves the constraint onto every operator and
   fails silently when they get it wrong.

   Holding a row lock across a remote API call is
   deliberate. The call is bounded (decision 5), the lock is
   per session so nothing else contends for it, and the
   waiters want exactly the thing the holder is producing.
   An optimistic scheme (claim, mint, publish) would need its
   own timeout and recovery for a claimant that dies, which
   is the lock's job already.

   The ticket is a secret field. `ConsoleToken.export()`
   leaves it out unless asked, mirroring
   `CONSOLE_SECRET_FIELDS` (`db.py:246`), and it is never
   logged.

2. **An oVirt ticket's expiry covers the token, so oVirt
   never re-mints.** When the first channel of a session
   mints, it asks for an expiry of the token's remaining
   lifetime plus `TICKET_SLACK` (30 s). The slack covers a
   channel authorized just before the token expires and then
   authenticating to qemu. After that no channel can be
   authorized (survey finding 2), so the ticket is never
   needed again. A deployment that lengthens
   `CONSOLE_TOKEN_DURATION` lengthens the ticket with it,
   which is what it asked for.

3. **The hook is general, and phase 4 fits it without
   change.** `BaseSource` gains a class attribute
   `MINTS_TICKETS = False` and a method
   `mint_console_ticket(uuid, lifetime)` that returns a
   `MintedTicket(ticket, expires)`. A driver honours
   `lifetime` where its API lets it (oVirt) and reports the
   shorter expiry where it cannot (Proxmox's fixed ~30 s).
   The authorize path reuses a stored ticket while
   `expires - now >= REUSE_MARGIN` (10 s) and re-mints
   otherwise. For oVirt that branch never fires within a
   token's life. For Proxmox it fires for a channel arriving
   more than about 20 s after the first mint, which is safe
   by survey finding 3. A new `kerbside/sources/__init__.py`
   maps type to class (`driver_class(source_type)`) and
   replaces the three hand-rolled dispatches.

4. **The direct `.vv` path keeps minting on request, with an
   explicit expiry of `CONSOLE_TOKEN_DURATION`.** A direct
   `.vv` embeds the hypervisor ticket, so its lifetime has to
   cover the same user-paced gap a proxied `.vv`'s token
   does. The operator has already said how long that gap may
   be. Reusing the setting rather than adding an
   `OVIRT_DIRECT_TICKET_LIFETIME` knob keeps one answer to
   one question. It replaces the 7200 s the path gets today
   by passing nothing.

5. **A mint is bounded, and a failed mint denies the
   channel.** The oVirt driver's CA fetch gets a timeout, and
   its SDK connection is built with one (`MINT_TIMEOUT`,
   15 s for both). A mint that raises or times out returns
   `Denied(reason='console ticket unavailable')`, writes an
   audit event, and stores nothing, so the next channel
   tries again. The master plan already accepted that the
   source's API being down now breaks connection setup; this
   makes it break promptly and visibly.

6. **Minting is audited, the ticket is not.** A successful
   mint adds the audit event `Minted backend console ticket`
   with its lifetime in seconds; a failed one adds
   `Backend console ticket mint failed` with the exception's
   class name only. Reuse is not audited, since it happens
   once per channel and says nothing new.

7. **Existing oVirt tickets are cleared by migration, and
   old daemons are not supported alongside new ones.** The
   migration that adds decision 1's columns also sets
   `consoles.ticket` to `''` for every console whose source
   has `type = 'ovirt'`, removing up to two hours of live
   passwords at upgrade. A daemon from before this phase
   reads the (now empty) row ticket for oVirt and its
   channels fail authentication. Every node's daemon has to
   be upgraded together, which the shared-schema migration
   already implies; the release note says so.

8. **Two sessions on one VM stay as they are.** A second
   session's mint revokes the first session's ticket for new
   channels, and survey finding 3 shows its established
   channels stay up. The first session's remaining channels
   have all opened within a second or two of its main
   channel, long before a human opens a second viewer, so in
   practice nothing is lost. This is recorded in
   `docs/console-sources.md` rather than engineered around.

## Step plan

One commit per step. Every commit passes `tox -eflake8`,
`tox -epy3` and `pre-commit run --all-files` on its own.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a.1 | medium | sonnet | none | **Registry.** Create `kerbside/sources/__init__.py` with `driver_class(source_type)` returning `ShakenFistSource`, `oVirtSource` or `StaticSource` for `'shakenfist'`, `'ovirt'`, `'static'`, and raising `KeyError` for anything else (OpenStack has no driver; `main.py:175-178` skips it and must keep doing so). Import the driver modules lazily inside the function or at module level, matching how `main.py:20-22` imports them today, and check for an import cycle with `kerbside/sources/base.py`. Replace the dispatch at `main.py:169-180` with it, keeping its `openstack` skip and its unknown-type error. Do not touch `api.py` yet; 3a.4 and 3a.5 rewrite those call sites. Add `MINTS_TICKETS = False` and a `mint_console_ticket(self, uuid, lifetime)` that raises `NotImplementedError` to `BaseSource` (`kerbside/sources/base.py`), plus a `MintedTicket` named tuple `(ticket, expires)` there, with docstrings saying what the method promises: return a ticket valid until `expires` (epoch seconds), honouring `lifetime` where the source's API allows and reporting the shorter expiry where it does not. Unit tests in a new `kerbside/tests/unit/test_sources_registry.py`. Commit: "Look source drivers up by type." |
| 3a.2 | high | opus | none | **Measure supersession on a real qemu.** The direct-qemu lane's qemu has no monitor; `tools/direct-qemu/start-qemu.sh:121` sets SPICE up with `password-secret`. Add a QMP unix socket to that qemu (under the lane's existing work directory, mode 0600). Write `tools/direct-qemu/verify-ticket-rotation.sh`, modelled on `tools/direct-qemu/verify-terminate-live.sh`: bring up a ryll headless session through the proxy (as that script does), confirm its channels are up over ryll's control socket, then over QMP (a) `set_password` with protocol spice and a new password and no `connected` argument, and (b) `expire_password` with protocol spice and time `now`. After each, wait 10 s and assert over the control socket that the session is still connected and still has its surface. Then assert that a fresh connection presenting the *old* password is refused (the lane's mock or static source can be pointed at the old value; read how `verify-terminate-live.sh` and `mock-grpc-server.py:286` supply the ticket). Wire it into `.github/workflows/direct-qemu-functional.yml` as its own step beside `verify-terminate-live.sh` (`:185`). Keep the workflow step to one line calling the script. Never print a password: pass them through files or environment variables, not arguments that land in `ps` or the log. Record the result in this plan's *Outcome*. Commit: "Prove a new SPICE password keeps connected clients." |
| 3a.3 | high | opus | none | **Schema.** Use the `add-database-migration` skill in `.claude/skills/`. Add nullable `backend_ticket` (String) and `backend_ticket_expires` (Integer) to `ConsoleToken` (`db.py:442-468`) and its migration, with a working downgrade. In the same migration, clear `consoles.ticket` to `''` for every console whose `consoles.source` names a `sources` row with `type = 'ovirt'` (decision 7); downgrade leaves those rows empty, which is safe because a pre-3a daemon re-mints on the next `.vv`. Keep the ticket out of `ConsoleToken.export()` unless `include_secrets=True`, mirroring `CONSOLE_SECRET_FIELDS` (`db.py:246`) and how `get_console` takes `include_secrets`; check every caller of `export()` and of `get_token_by_token`/`get_tokens_by_console`/`get_token_by_session_id` still gets what it needs. Add to `db.py`: (1) `session_ticket(session_id, mint, lifetime, reuse_margin)` which, in one transaction, selects the token row by `session_id` `with_for_update()`, returns the stored `(ticket, expires)` if `expires - now >= reuse_margin`, and otherwise calls `mint(lifetime)` (a callable returning a `MintedTicket`) with the lock held, stores the result and commits; on an exception from `mint` it rolls back and re-raises; (2) `clear_expired_backend_tickets()`, nulling both columns where `backend_ticket_expires < now` or the token itself has expired, returning the count. Call (2) from the maintenance loop beside `_reap_expired_console_tokens` (`main.py:283-287`). Unit tests in `test_db.py` and `test_migrations.py` following their existing SQLite patterns; note in a test docstring that SQLite ignores `FOR UPDATE`, which is why 3a.6 exists. Update `docs/schema.md` (the `consoletokens` table, and the `consoles.ticket` paragraph at `:149-171`, which must now say only static sources store a ticket there). Commit: "Give each session somewhere to keep its ticket." |
| 3a.4 | high | opus | none | **The oVirt driver mints.** In `kerbside/sources/ovirt.py`: set `MINTS_TICKETS = True`; give the CA fetch at `:46-48` a `timeout=MINT_TIMEOUT` (15 s, a module constant) and pass `timeout=MINT_TIMEOUT` to the SDK `Connection` wherever `_ensure_connection` builds one; change `get_console_for_vm(..., acquire_ticket=False)` to take `ticket_lifetime=None` and, when acquiring, call `console_service.ticket(ticket=OVIRT_SDK_TYPES.Ticket(expiry=int(lifetime)))` and return the ticket's value *and* the expiry the engine actually granted (the returned `Ticket` carries `.expiry`; read the ovirtsdk4 source in the lane's venv or `/srv/src-reference/ovirt/ovirt-engine-sdk` rather than guessing its units, and assert them in a test). Implement `mint_console_ticket(uuid, lifetime)` on top of it, returning `MintedTicket(value, now + granted_expiry)`. Update the direct handler (`api.py:380-479`) to use `sources.driver_class`, and to request `config.CONSOLE_TOKEN_DURATION * 60` seconds (decision 4); keep its existing secrets discipline (only the oVirt branch reads the source secrets). Tests in `test_sources_ovirt.py` and `test_api.py` (`:713-741` mock `get_console_for_vm` today; update them to the new signature and assert the lifetime requested). Commit: "Ask oVirt for a ticket that expires." |
| 3a.5 | xhigh | opus | none | **Mint in the authorize path.** In `kerbside/rpc/servicer.py:93-147`: after the token and console checks, look the source's driver up with `sources.driver_class(source['type'])`. If the class has `MINTS_TICKETS`, call `db.session_ticket(token['session_id'], mint, lifetime, REUSE_MARGIN)`, where `lifetime = max(token['expires'] - now, 0) + TICKET_SLACK` (decision 2; `TICKET_SLACK = 30`, `REUSE_MARGIN = 10`, module constants with a comment each saying why), and `mint` constructs the driver from `db.get_source(..., include_secrets=True)` and calls `mint_console_ticket(console['uuid'], lifetime)`, closing the driver in a `finally`. Audit per decision 6 (`db.add_audit_event`, as the function already does for "Channel created"); deny per decision 5 with `Denied(reason='console ticket unavailable')`. Put the minted ticket in `Target.ticket`; sources that do not mint keep `console['ticket']`. Never log the ticket, and check the existing `LOG.error('AuthorizeConnection failed: %s' % e)` at `:144` cannot carry one (an SDK exception can quote its request; log the class name there if in doubt, and say why in a comment). In `api.py:482-534` (`ConsolesProxyVirtViewer`), remove the oVirt mint and both `store_console_ticket` calls, so the proxied GET touches no ticket for any source type, and rewrite the comment block above it; then check whether `db.store_console_ticket` (`db.py:424`) has any caller left and delete it if not. Tests in `test_rpc.py`: a minting source mints once for three channels of one session; a stored ticket within the margin is reused; one past the margin is replaced; a mint failure denies, audits and stores nothing; a non-minting source still gets its row ticket. Commit: "Mint console tickets when a channel connects." |
| 3a.6 | high | opus | none | **Prove the lock on MariaDB.** SQLite ignores `FOR UPDATE`, so the unit tests cannot show that concurrent channels mint once. Add `kerbside/tests/functional/test_session_ticket_lock.py` (create the package if absent), skipped unless `KERBSIDE_TEST_SQL_URL` is set: create the schema on that URL, insert one token, and run eight threads calling `db.session_ticket` for the same session with a `mint` that sleeps 1 s and counts its calls. Assert exactly one mint and eight identical tickets. Add a second case where the stored ticket is inside the reuse margin, and assert one re-mint. The direct-qemu lane already runs MariaDB (`tools/direct-qemu/setup-mariadb.sh`); add one workflow step after it that runs this file against that database, calling a `tools/direct-qemu/run-ticket-lock-test.sh` rather than inlining commands. Check it fails with the `with_for_update()` removed, and record that in the commit message. Commit: "Test that one session mints once." |
| 3a.7 | high | opus | none | **The oVirt lane proves the lifetime.** In `tools/ovirt-e2e/drive-console.py`: correct the docstring's "expire in ~120s" (`:18-19`), and after step 4 hold the session until at least 30 s past the ticket's expiry, which is the token's remaining lifetime plus `TICKET_SLACK` from issue, then re-run the smoke client and assert the session is still up. Assert the console's `consoles.ticket` is empty for the lane's oVirt console, that the session's token row carries a `backend_ticket_expires`, and that exactly one `Minted backend console ticket` audit event exists for the session however many channels it opened. Never print the ticket. This lane runs in the merge tier only (`functional-tests.yml:380`), so run it once from the pull request with the bot's retest mechanism described in `docs/testing.md` before asking for merge, and record the run in *Outcome*. Commit: "Hold the oVirt session past its ticket." |
| 3a.8 | medium | sonnet | none | **Docs.** Rewrite oVirt's ticket lifecycle where survey finding 4 found it wrong: `docs/use-cases/ovirt.md:88-112` (flow step B moves from the `.vv` request to the first channel; the mermaid edge label at `:73` changes with it) and the limitations row at `:288` (kerbside now sets the lifetime: token lifetime plus 30 s proxied, `CONSOLE_TOKEN_DURATION` direct); `ARCHITECTURE.md:269-280` (the driver row, and the paragraph on where tickets are stored); `docs/console-sources.md` (the oVirt section, plus the two-sessions note from decision 8 and the supersession finding); `docs/proxy-architecture.md:95-105,203-235` (authorize now calls a source driver for minting sources, and can deny when the source is unreachable); `docs/configuration.md:18` (`CONSOLE_TOKEN_DURATION` now also bounds oVirt ticket lifetimes). Every claim about ticket lifetime must agree across these pages: grep `docs/ ARCHITECTURE.md tools/ovirt-e2e` for `ticket` near `minute`, `second`, `short-lived` and `expir` and reconcile each hit. Describe the code as it is: no references to this plan, its phases or its decisions. Commit: "Describe where console tickets come from now." |

## Risks and mitigations

- **A hung mint stalls every channel of a session.** The row
  lock serialises them behind it. Mitigation: decision 5's
  timeouts. 3a.4 must show the SDK connection timeout
  actually applies to the ticket call, not only to login, by
  reading `ovirtsdk4` and testing it with a stalled fake;
  the management session checks that test exists.
- **gRPC worker exhaustion.** Each waiting channel holds a
  worker from `API_GRPC_WORKERS` for up to `MINT_TIMEOUT`.
  A session opens about six channels, so a few simultaneous
  session starts against a slow engine can fill the pool.
  Mitigation: record the worst case in
  `docs/proxy-architecture.md` (3a.8) and note in *Future
  work* that a per-session wait could move off the pool if
  it bites. Not engineered now, because the engine is fast
  when it is up and a slow engine already fails sessions.
- **A secret leaks through an exception.** The authorize
  path's catch-all logs `%s` of the exception, and the SDK
  and `requests` can both quote a request. Mitigation: 3a.5
  audits that path, and 3a.7's lane greps the daemon log for
  the ticket value, as `drive-console.py` already avoids
  printing it.
- **Rolling upgrades.** A pre-3a daemon cannot authorize an
  oVirt channel once the migration has cleared the row
  (decision 7). Mitigation: the release note says to upgrade
  every node's daemon together; kerbside's migrations are
  already a shared, one-shot step, so this adds no new
  procedure.
- **The unit tests pass on SQLite and prove nothing about
  locking.** Mitigation: 3a.6, on the real engine, with a
  mutation showing it fails without the lock.

## Definition of done

- [ ] `grep -rn "store_console_ticket\|acquire_ticket=True" kerbside`
      finds no call that stores an oVirt ticket on a
      `Console` row.
- [ ] `grep -rn "oVirtSource(\|ShakenFistSource(\|StaticSource(" kerbside --include=*.py | grep -v tests`
      finds only `kerbside/sources/`.
- [ ] Every `console_service.ticket(` call in
      `kerbside/sources/ovirt.py` passes an explicit expiry.
- [ ] The migration upgrades and downgrades on SQLite in
      `test_migrations.py`, and clears `consoles.ticket` for
      oVirt consoles only; a test shows a static console's
      ticket survives it.
- [ ] `test_rpc.py` shows one mint for three channels of one
      session, reuse within the margin, a re-mint outside
      it, and a denial with an audit event and nothing
      stored when the mint fails.
- [ ] The MariaDB lock test runs in the direct-qemu lane and
      passes, and the commit message records that it failed
      with `with_for_update()` removed.
- [ ] `verify-ticket-rotation.sh` runs in the direct-qemu
      lane and passes: the session survives `set_password`
      and `expire_password`, and the old password is refused.
- [ ] An `ovirt_matrix` run on this phase's pull request is
      green with 3a.7's assertions, and is linked in
      *Outcome*.
- [ ] No page under `docs/`, nor `ARCHITECTURE.md`, nor
      `tools/ovirt-e2e/`, states oVirt's ticket lifetime as
      anything but what kerbside now requests.
- [ ] The master plan's Execution table records this phase's
      merge commit, set by the next phase's close-out.

## Bugs fixed during this work

None yet. Three are expected: the 7200 s oVirt ticket
(the master plan's finding), the oVirt ticket shared between
concurrent sessions through the `Console` row (survey
finding 8), and the untimed CA fetch (survey finding 6).

## Future work

- Move waiting channels off the gRPC worker pool if a slow
  source ever exhausts it (see *Risks*).
- Issue #319: the proxied `.vv` GET still mints a token.
- Issue #134: `/console/direct` hands a raw hypervisor
  ticket to any authenticated user. Decision 4 bounds how
  long that ticket lives, which narrows the issue without
  closing it.

## Outcome

Not started.

## Back brief

Before executing any step of this plan, back brief the
operator on the intended approach and on any deviation from
this plan found during implementation. In particular:

- confirm decision 1 before 3a.3 writes the migration. It
  reverses the master plan's in-memory design, and a schema
  change is the expensive thing to redo;
- report 3a.2's measurement before 3a.5 relies on it. If a
  connected client does not survive a new password, stop:
  decision 3's re-mint path is unsafe, and Proxmox (phase 4)
  inherits the problem;
- if 3a.4 finds the SDK cannot bound the ticket call with a
  timeout, say so before 3a.5 puts it under a row lock.
