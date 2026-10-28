# Proxmox source phase 3b: tunnel transport

This is the detailed plan for phase 3b of
[PLAN-proxmox-source.md](/components/kerbside/plans/PLAN-proxmox-source/). Read the
master plan first. Its *Why this is not another `ovirt.py`*
(layers 1 and 3) and *Three protocol details that cost an
afternoon* are assumed knowledge here. So is the
[phase 3a plan](/components/kerbside/plans/PLAN-proxmox-source-phase-03a-connect-time-minting/),
whose session ticket this phase extends.

## Prompt

All work for this phase lands in `shakenfist/kerbside`. It
changes the protobuf contract, the Rust proxy's backend leg,
the session token schema and the authorize path. It changes
nothing in ryll: the pinned rev already carries phase 2's
CONNECT support. Consult `ARCHITECTURE.md`,
`docs/proxy-architecture.md`, `docs/schema.md` and
`docs/development.md` (the Rust build and the contract hash).
Run `tox -eflake8`, `tox -epy3` and `pre-commit run
--all-files` before every commit. For any commit touching
`rust/`, also run `make -C rust/kerbside-proxy test` and
`make -C rust/kerbside-proxy lint`, which build in Docker.

Planning effort: **high**. The phase changes the RPC contract,
which couples the daemon and proxy releases. It carries a new
secret, the signed CONNECT host, through the schema, the
authorize path and the proxy's logs. And it adds a backend
path that must never fall back to plaintext.

## Scope

In scope:

- A `Tunnel` sub-message on `Target`, and the regenerated
  Python stubs and contract hash.
- The Rust backend leg dialling a tunnelled target: TLS only,
  through the HTTP CONNECT proxy, with no plaintext attempt
  and no `NEED_SECURED` retry.
- The source-driver hook's return value growing a tunnel, the
  session token row storing it, and `AuthorizeConnection`
  putting it in `Target`.
- A direct-qemu lane that proves a tunnelled session through
  a CONNECT proxy, and proves that a tunnel without a pin is
  refused.
- The docs that describe the backend leg and the schema.

Out of scope:

- The Proxmox driver, discovery, CA handling, and refusing
  Proxmox in the direct `.vv` handler. All of that is phase 4.
  In this phase no source produces a tunnel, and only the
  tests and the lane exercise the path.
- A lane against a real Proxmox node. That is phase 5 (see
  decision 6).
- Tunnels on non-minting sources. A static console behind a
  CONNECT proxy is a feature nobody has asked for.
- HTTPS proxies and proxy credentials, which ryll refuses
  (`proxy.rs`, `HttpsUnsupported` and
  `CredentialsUnsupported`).
- Validating the connect targets a management plane supplies
  (issue #138, item 1). The tunnel fields inherit that issue's
  trust model rather than resolving it.

## What the survey found

Surveyed 2026-10-26 against `develop` at `696f5aa` (phase 3a's
merge).

1. **The ryll pin is already bumped.** The master plan's 3b
   row says "needs phase 2 merged and the pin bumped". The pin
   in `rust/kerbside-proxy/Cargo.toml:25` is ryll `4c91b13`,
   and `git merge-base --is-ancestor ea4bf67 4c91b13` holds,
   so it contains phase 2's merge. `build_config` already
   names the field, with `proxy: None`
   (`rust/kerbside-proxy/src/backend.rs:232-234`). No bump is
   needed. The row's `backend.rs:183` reference is stale:
   `build_config` is now at `:202`.

2. **The `Console` row already has a home for the node.** The
   master plan wants a migration giving the row "somewhere to
   record the node and the `spiceproxy` URL". The node is what
   `Console.hypervisor` (`kerbside/db.py:266`) already holds
   for every source: the machine the console runs on. Phase
   4's driver needs it to build
   `/nodes/{node}/qemu/{vmid}/spiceproxy`, and can store it
   there without a new column.

3. **The rest of a tunnelled target is per mint, so it
   belongs on the session's token row.** The `spiceproxy`
   response (the master plan's JSON) returns four values
   together:
   - `password`, the SPICE ticket;
   - `host`, the CONNECT pseudo-hostname, which carries its
     own signed ticket with a 40 s window;
   - `tls-port`, which PVE signs into the pseudo-hostname, so
     it must agree with it;
   - `proxy`, the URL to CONNECT through.

   The API caller can choose `proxy`, so it is not fixed per
   node either. 3a's session ticket stores only `(ticket,
   expires)` (`db.py:487-538`, `MintedTicket` at
   `kerbside/sources/base.py:11`), and later channels reuse
   what the first channel stored (`db.session_ticket`,
   `db.py:637-684`). A later channel therefore needs the whole
   tunnel from the row, not just the password. The migration
   belongs on `consoletokens`, not `consoles`.

4. **The CONNECT host is a secret.** It is a signed
   credential. It must be cleared with the password, kept out
   of `export()`, and kept out of logs and audit events.
   `backend.rs` logs `target.hypervisor` and `hypervisor_ip`
   (`:114`, `:171-172`, `:395`) and records the full connect
   error as an audit event (`connect_failure_message`,
   `:380`). ryll's own errors name the proxy, not the target:
   the dial label is `HTTP proxy {proxy}` (`client.rs:603-607`
   at the pin), and its `Display` redacts the target (ryll
   test `display_target_with_a_proxy_redacts_the_target`). So
   the rule is to keep the connect host out of
   `Target.hypervisor` and out of kerbside's own log fields.
   The lane checks that end to end.

5. **The insecure-first dial is wrong for a tunnel.**
   `backend::run` (`:85-140`) tries `insecure_port` first and
   escalates to `secure_port` only on `NEED_SECURED`. ryll
   refuses a tunnel without a `tls_port` in `SpiceClient::new`,
   and a plaintext open through a proxy in `open_transport`
   (`client.rs:341-370`, `:585-590`). Today's flow would
   therefore fail every tunnelled channel at the first
   attempt, and the retry would never run. That was phase 2's
   finding, and it still holds.

6. **There is a harness to build the lane on.**
   `tools/direct-qemu/mock-grpc-server.py` answers
   `AuthorizeConnection` with a `Target` built from its
   arguments (`:174-184`). `verify-rust-proxy.sh` with
   `BACKEND_TLS=1` gives qemu a pinned TLS listener, and
   `run-host-subject-checks.sh` drives match and mismatch
   modes through it in the direct-qemu workflow (`:276`). A
   tunnel lane is that harness plus a CONNECT proxy and two
   mock arguments. It needs neither a daemon nor a Proxmox
   node.

7. **A proto change couples the daemon and the proxy.**
   `kerbside/rpc/contract.py` holds a sha256 of
   `kerbside.proto`, generated by `tox -egenprotos`.
   `kerbside/proxy_supervisor.py` refuses a binary whose
   embedded hash differs. That is intended: the release note
   says to upgrade the daemon and the proxy wheel together.

The stale facts in the master plan are corrected at source in
this planning commit:

- the 3b row's pin and line reference, and its migration
  target;
- open question 3, which decision 1 settles;
- where kerbside's first Proxmox lane lands, which decision 6
  settles.

## Decisions

1. **`Target` grows a sub-message, not loose fields.** This
   settles the master plan's open question 3. Add
   `message Tunnel { string proxy_uri = 1; string connect_host
   = 2; }` and `Tunnel tunnel = 11;` on `Target`.
   - A message field has presence in proto3, so "is this
     tunnelled" is one check (`target.tunnel.is_some()` in
     prost).
   - The two values mean nothing apart.
   - The TLS port stays in `secure_port`: under a tunnel it is
     still qemu's TLS port, now reached through the proxy, and
     PVE signs it into the connect host.
   - Under a tunnel the proxy dials neither `hypervisor_ip`
     nor `insecure_port`.
   - `hypervisor` keeps naming the node, for logs. It never
     holds the connect host.

2. **A tunnelled target is TLS only and is checked before any
   dial.** `build_config` returns a `Result`. Given a tunnel,
   it:
   - parses `proxy_uri` with ryll's `parse_proxy_uri`;
   - sets `host` to `connect_host` and `tls_port` to
     `Some(secure_port)`;
   - refuses an empty `connect_host` or a zero `secure_port`
     with an error naming the field, not its value.

   `run` then makes one TLS attempt. There is no insecure
   attempt and no `NEED_SECURED` retry. A refusal goes through
   `record_connect_failure`, like any other failed connect.

   A tunnel with no `host_subject` is refused by ryll's
   `SpiceClient::new`, and kerbside does not duplicate that
   check. One enforcement point is better than two that can
   drift. The lane proves the refusal reaches the audit trail.

3. **The minted ticket carries the tunnel.**
   - `MintedTicket` gains a third field, `tunnel`, defaulting
     to `None` (`namedtuple(..., defaults=[None])`), so
     `ovirt.py` and every existing test keep working
     unchanged.
   - `tunnel` is a `Tunnel(proxy_uri, connect_host, port)`
     named tuple in `kerbside/sources/base.py`.
   - `mint_console_ticket`'s docstring says the tunnel and the
     ticket expire together: `expires` covers both credentials.
     A source whose two credentials expire separately (PVE:
     password 30 s, CONNECT ticket 40 s) reports the earlier
     one.

4. **Three nullable columns on `consoletokens`, not a blob.**
   - `backend_proxy_uri` (String);
   - `backend_connect_host` (String);
   - `backend_port` (Integer).

   They are written and cleared together with
   `backend_ticket`, in `session_ticket` and
   `clear_expired_backend_tickets`. `backend_connect_host`
   joins `CONSOLE_TOKEN_SECRET_FIELDS`.

   This is the decision a reviewer would most likely argue
   with. Against it: a JSON column would make the next
   transport a code change rather than a migration. For it:
   named columns are queryable and typed, documented in
   `docs/schema.md` like every other column, and the clear in
   `clear_expired_backend_tickets` stays one `UPDATE` naming
   what it forgets. A secret hidden inside a blob is the
   easier one to leak through an `export()` that forgets to
   look inside it. The migration has no data step, because no
   existing row has a tunnel.

5. **The authorize path copies the tunnel into `Target`.**
   When the session's ticket has a tunnel, `AuthorizeConnection`
   (`kerbside/rpc/servicer.py:142-282`) sets `Target.tunnel`,
   sets `secure_port` from the tunnel's port, and sets
   `insecure_port` to 0. Nothing else changes: the lock, the
   reuse margin, the deadline and the denials are all 3a's.
   The catch-all already logs only the exception's class
   (`_error_summary`), so the connect host cannot reach the
   log through it. A test proves this with a sentinel.

6. **Kerbside's first Proxmox lane lands in phase 5, not
   here.** The master plan left this for 3b's planning.
   - 3b has no driver, so a lane against a real node would
     need a fixture that calls `spiceproxy` and writes the
     database by hand. That would duplicate phase 4 in a test
     tool.
   - What 3b changes is transport-generic. The CONNECT
     exchange itself is ryll's, and ryll's lane already proves
     it against a real `spiceproxy` on every ryll pull request
     that touches the tunnel.
   - So 3b proves kerbside's half against a CONNECT proxy in
     the direct-qemu lane (decision 7), and phase 5 proves the
     whole path against PVE once a driver can mint.

7. **The lane's CONNECT proxy behaves like `spiceproxy` where
   it matters.**
   - It is a small asyncio relay in
     `tools/direct-qemu/connect-proxy.py`.
   - It accepts a CONNECT only when the request line and the
     `Host:` header both name an expected opaque connect host
     and port. That host is a random sentinel per run, so it
     is no more a DNS name than PVE's pseudo-hostname is.
   - It relays to qemu's TLS listener.
   - It answers anything else `401`, as PVE does.
   - It logs only counts: tunnels opened and refusals.

   The mock gives the target an undialable `hypervisor_ip`
   (`192.0.2.1`, TEST-NET-1), so a session that comes up can
   only have come through the proxy. The proxy's tunnel count
   confirms it.

## Step plan

One commit per step. Every commit passes `tox -eflake8`,
`tox -epy3` and `pre-commit run --all-files` on its own, and
every commit touching `rust/` also passes `make -C
rust/kerbside-proxy test lint`. Run the steps in order: 3b.3
needs 3b.1's stubs and 3b.2's columns, and 3b.4 needs all
three.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3b.1 | high | opus | none | **Contract and backend leg.** Read decisions 1 and 2. In `kerbside/rpc/kerbside.proto` add `message Tunnel { string proxy_uri = 1; string connect_host = 2; }` and `Tunnel tunnel = 11;` on `Target` (`:93-104`). Extend the comment above `Target` to say what a tunnel means: TLS-only through an HTTP CONNECT proxy; `secure_port` is the port the CONNECT names; `hypervisor_ip` and `insecure_port` are not dialled; `connect_host` is a credential and must never be logged. Regenerate with `tox -egenprotos` and commit the regenerated `kerbside/rpc/kerbside_pb2*.py*` and `contract.py`. In `rust/kerbside-proxy/src/backend.rs`, change `build_config` (`:202-236`) to return `Result<ConnectionConfig>`. Without a tunnel it behaves exactly as today. With one it parses `proxy_uri` with `shakenfist_spice_protocol`'s `parse_proxy_uri` (read `proxy.rs` at the pinned rev in `~/.cargo/git` or `/srv/kasm_profiles/mikal/vscode/src/shakenfist/ryll` at `4c91b13`), sets `host = connect_host`, `tls_port = Some(secure_port)`, and `proxy = Some(parsed)`. It returns an error for an empty `connect_host` or a zero `secure_port`, naming the field and never quoting `connect_host`. In `run` (`:73-196`), take the tunnel branch before the insecure-first logic: one `connect_once` with that config, failures through `record_connect_failure`, and no `NEED_SECURED` retry. A `build_config` error also goes through `record_connect_failure`. Add `tunnelled = target.tunnel.is_some()` to the success and failure log lines, and check every `info!`/`warn!`/`debug!` in `backend.rs`, `session.rs` and `rpc.rs` that takes a `Target` field: none may print `connect_host` or `ticket`. Unit tests beside the existing `build_config_*` tests (`:484-`): the tunnel field mapping, the empty-host and zero-port refusals (asserting the error text excludes a sentinel connect host), a bad proxy URI, and that a target without a tunnel still maps exactly as before. Also update the `Target` literal at `session.rs:605` and any other construction the compiler finds. Commit: "Carry an HTTP CONNECT tunnel in Target." |
| 3b.2 | high | opus | none | **Schema and minted tunnel.** Read decisions 3 and 4, and use the `add-database-migration` skill in `.claude/skills/`. In `kerbside/sources/base.py` add `Tunnel = namedtuple('Tunnel', ['proxy_uri', 'connect_host', 'port'])`, and make `MintedTicket` `namedtuple('MintedTicket', ['ticket', 'expires', 'tunnel'], defaults=[None])`. Update `mint_console_ticket`'s docstring per decision 3. Add nullable `backend_proxy_uri` (String), `backend_connect_host` (String) and `backend_port` (Integer) to `ConsoleToken` (`kerbside/db.py:487-538`), with a migration whose `down_revision` is the current head (`d019b0e3c87a` at planning; check with `alembic heads`) and a working downgrade. Add `backend_connect_host` to `CONSOLE_TOKEN_SECRET_FIELDS` (`:484`) and update the comment above it. Extend `export()` to include the two non-secret columns. In `session_ticket` (`:637-684`), store all three from `minted.tunnel` (or `None` when it has none) beside the ticket, and return a `MintedTicket` with the stored tunnel on reuse (a tunnel when `backend_connect_host` is set, else `None`). In `clear_expired_backend_tickets` (`:687-706`), null all three with the ticket. Test the migration as 3a's `d019b0e3c87a` test does: offline MySQL SQL asserted in both directions. Run it up, down and up against a MariaDB 11 container and record that in the commit message. Unit tests in `test_db.py`: a tunnel is stored and reused intact; one without a tunnel stores nulls; clearing forgets the tunnel; `export()` omits the connect host unless `include_secrets`. Update `docs/schema.md`'s `consoletokens` section (`:203-248`) and its ERD block. Commit: "Keep a session's tunnel with its ticket." |
| 3b.3 | high | opus | none | **Authorize path and mock.** Read decision 5. In `AuthorizeConnection` (`kerbside/rpc/servicer.py:142-282`), where `backend = db.session_ticket(...)` returns (`:211-251`): when `backend.tunnel` is set, build `Target` with `tunnel=kerbside_pb2.Tunnel(proxy_uri=..., connect_host=...)`, `secure_port=backend.tunnel.port` and `insecure_port=0`. Leave everything else as it is. Tests in `kerbside/tests/unit/test_rpc.py` with a fake minting driver (follow the existing minting-source tests): a minted tunnel reaches `Target`, and a second channel of the session gets the same tunnel from the row. A source without a tunnel gets no `tunnel` field (`HasField('tunnel')` is false) and its console's ports. A test with a sentinel connect host, capturing kerbside's logs through the authorize path including a failing mint, asserts the sentinel never appears. In `tools/direct-qemu/mock-grpc-server.py`, add `--tunnel-proxy-uri` and `--tunnel-connect-host-file`: the connect host is read from a file so it never lands in `ps` or the workflow log. When both are given, the reply carries a `Tunnel`. Update its docstring. Make sure nothing the mock logs includes the connect host; its `RecordAuditEvent` logging quotes audit messages, which the lane will grep. Commit: "Authorize tunnelled consoles." |
| 3b.4 | high | opus | none | **The tunnel lane.** Read decisions 6 and 7, and finding 6. Write `tools/direct-qemu/connect-proxy.py` (asyncio, standard library only): listen on a local port and read the expected connect host from a file and the expected port from an argument. Accept `CONNECT <host>:<port> HTTP/1.x` only when both the request line's authority and the `Host:` header equal `<expected host>:<port>`, as PVE requires (master plan, *Three protocol details*). Then answer `HTTP/1.0 200 Connection established` and relay bytes both ways to qemu's TLS listener. Answer anything else `401` and close. Write counts of tunnels opened and refused to a status file, and log nothing else. Write `tools/direct-qemu/run-tunnel-checks.sh`, modelled on `run-host-subject-checks.sh`, with three modes, each in its own workdir and torn down before the next. In every mode the mock's `--hypervisor-ip` is `192.0.2.1`, and its connect host is a fresh random sentinel written to a 0600 file. Modes: `match` (correct `host_subject`): the smoke client passes, the proxy counted at least one tunnel per channel the session opened and refused none. `no-subject` (empty `host_subject`): the session fails, the mock's audit log has a `Hypervisor connection failed` event naming the missing `host_subject`, and the proxy counted zero tunnels. `mismatch` (wrong subject): the session fails at the TLS handshake through the tunnel. Then, as `verify-ticket-rotation.sh` does with its password, grep every collected artifact for the sentinel with `grep -rlF -f`, removing any hit with an ERROR and failing the run. Add one workflow step to `.github/workflows/direct-qemu-functional.yml` after the host-subject step (`:276`), with its artifact upload modelled on `:278-293`. Keep the step to one line that calls the script. Read `verify-rust-proxy.sh`'s `BACKEND_TLS=1` path (`:130-136`, `:561`, `:605`) and reuse it rather than starting qemu yourself. This workflow has no `workflow_dispatch`, so the first CI run is on the pull request: record its link and result in this plan's *Outcome*. Commit: "Prove a tunnelled session in the direct lane." |
| 3b.5 | medium | sonnet | none | **Docs.** `docs/proxy-architecture.md`: its *TLS Configuration* section (`:51-61`) and the connection walk-through (`:126`, which describes the `need_secured` retry) gain the tunnelled path. It is TLS only, through an HTTP CONNECT proxy, refused without a certificate-subject pin, and dials nothing but the proxy. Say that the connect host is a credential and is never logged. `docs/testing.md`: add the tunnel step to the direct-qemu lane's description. `docs/development.md`: where it describes the contract hash (`:490-500`), note that a proto change means the daemon and proxy are upgraded together, if it does not already say so. Run `tools/check-backend-tls-claims.py`. If a true new sentence trips it, adjust the guard's rule deliberately, with its unit test and a run of `tools/mutate-backend-tls-claims.py`, rather than rewording to dodge it, and say which in the commit message. `ARCHITECTURE.md` changes only if its component or data-flow summary states that the proxy always dials the hypervisor directly; check, and change one sentence at most. Describe the code as it is, with no references to this plan, its phases or its decisions. Commit: "Describe tunnelled backend connections." |

## Risks and mitigations

- **The connect host leaks.** It is a signed credential, and
  it passes through the schema, the authorize path, the
  proxy, ryll and the audit trail. Mitigation:
  - decision 1 keeps it out of the fields kerbside logs;
  - 3b.1 and 3b.3 each test with a sentinel;
  - 3b.4 greps every artifact the lane collects, the
    audit-event log included.

  The management session reruns that grep on the downloaded
  artifacts before ticking the definition of done.
- **A tunnel falls back to plaintext.** ryll refuses it twice
  (`new` and `open_transport`), and decision 2 removes the
  insecure attempt. Mitigation: 3b.1's unit tests. In the
  lane, the CONNECT proxy relays only to qemu's TLS port, and
  the undialable `hypervisor_ip` means a direct dial cannot
  succeed.
- **The daemon and proxy are upgraded apart.** The contract
  hash makes a mismatch refuse to start rather than misroute.
  Mitigation: the release note names the pairing, and 3b.5
  checks that `docs/development.md` says it.
- **The lane's proxy is more forgiving than PVE's.** It does
  not reproduce PVE's ticket ages. Mitigation: that is
  deliberate (decision 6). Ticket ages are phase 4's
  driver's to report and phase 5's lane's to prove. 3b
  proves only that kerbside carries and dials a tunnel.
- **Dead code until phase 4.** No source produces a tunnel in
  this phase. Mitigation: the lane drives the proxy half end
  to end, and 3b.3's fake driver drives the daemon half. The
  first real producer is reviewed in phase 4 against these
  tests.

## Definition of done

- [x] `grep -n "Tunnel tunnel = 11" kerbside/rpc/kerbside.proto`
      matches. `tox -egenprotos` leaves the tree unchanged,
      so the stubs and `CONTRACT_HASH` are current.
- [x] `make -C rust/kerbside-proxy test lint` passes, and the
      tests include a tunnelled `build_config` refusing an
      empty connect host and a zero port without quoting
      the host.
- [x] Removing the tunnel mapping from `build_config` (so a
      tunnelled target is dialled as a direct one) makes the
      lane's `match` mode fail. Record the result in the
      3b.4 commit message.
- [x] The migration's offline MySQL SQL is asserted in both
      directions by a unit test, and it runs up, down and up
      against MariaDB.
- [x] `ConsoleToken.export()` without `include_secrets` has no
      `backend_connect_host` key, by unit test.
- [x] `test_rpc.py` shows a minted tunnel reaching `Target`
      and being reused by a second channel, and no
      `tunnel` field for a source that does not mint one.
- [x] The direct-qemu run on the pull request passes all three
      tunnel modes, with the proxy's counts as decision 7
      states, and its link is in *Outcome*.
- [x] The connect-host sentinel appears in none of that run's
      uploaded artifacts.
- [x] `tools/check-backend-tls-claims.py` passes, and no page
      under `docs/` says the backend leg always dials the
      hypervisor directly or always tries plaintext first.
- [x] The master plan's Execution table records this phase's
      merge commit, set by the next phase's close-out.

## Bugs fixed during this work

None yet.

## Future work

- Issue #138's connect-target validation now covers the
  tunnel fields too: a proxy URI and port from the management
  plane flow into the dial unchecked, just as the address
  fields do.
- The direct path still narrows `insecure_port` and
  `secure_port` with `as u16` in `build_config` and
  `connect_direct`, so 65536 becomes port 0. The tunnel path
  refuses out-of-range ports. Review declined the direct path's
  casts as predating this phase; they belong with #138's
  connect-target validation.

## Execution notes

Implementation began 2026-10-26 (AEDT). At the back brief the
operator confirmed decisions 4 and 6, and gave this session
permission to push the branch and open the pull request once
the definition of done is met locally, since the direct-qemu
lane runs only on a pull request.

The steps run in sequence, in table order: 3b.3 builds on
3b.1's regenerated stubs and 3b.2's columns, and 3b.4 drives
all three.

3b.1 corrected one definition-of-done item at source. It said
removing the tunnel branch from `backend::run` would fail the
lane, but `build_config` already makes a tunnelled config TLS
only, so without that branch the direct path's first attempt is
the same TLS dial through the proxy and the session still comes
up. The branch only prevents a second attempt, which the Rust
test `tunnelled_target_dials_only_its_proxy_once` catches and
the lane cannot see. The item now mutates `build_config`'s
tunnel mapping instead.

Regenerating the stubs in 3b.1 also moved
`GRPC_GENERATED_VERSION` from 1.83.0 to 1.84.0. The committed
stubs had been generated before `pyproject.toml`'s
`grpcio==1.84.0` pin, so this matches the runtime rather than
changing it.

All five steps ran as planned, one commit each, with no change of
order or model. 3b.2 sized the two string columns at 255, like
`backend_ticket`: PVE's `assemble_spice_ticket` (pve-common
`Ticket.pm`) builds the connect host from a timestamp, the vmid,
the node and the port plus a 40-character SHA-1 signature, so it
fits. 3b.4's lane ran locally on alternate proxy ports, because
this host already listens on 5900: four of five full runs passed
every mode, and the fifth failed `match` on the cursor channel
with `BadConnectionId`, which `run-host-subject-checks.sh` also hits
on this host's qemu over the direct path. The `build_config`
mutation failed `match` as the definition of done requires. 3b.5
found that `ARCHITECTURE.md` and `docs/network-ports.md` each
said the backend leg always dials the plaintext port first, and
named the tunnelled exception in both.

## Outcome

All five steps landed on `proxmox-source-phase-03b-tunnel` on
2026-10-26 (AEDT), one commit each. 565 unit tests and 86 Rust
tests pass.

The direct-qemu lane passed on `2025bfc`, the pull request's
head after its review round and a rebase onto develop:
[run 40606519156](https://github.com/shakenfist/kerbside/actions/runs/40606519156).
All three tunnel modes passed, with the CONNECT proxy's counts
as decision 7 states:

| Mode | Tunnels | Refused | Upstream failed | Channels relayed |
|---|---|---|---|---|
| `match` | 5 | 0 | 0 | 5 |
| `no-subject` | 0 | 0 | 0 | 0 |
| `mismatch` | 1 | 0 | 0 | 0 |

The lane's own leak check found no connect host in any collected
artifact, the copy of its console output included. The uploaded
artifacts and the step log were also checked after the run for
the sentinel's shape, which is the `pvespiceproxy:` prefix or
any 40-character hex run, since each sentinel is random and
never uploaded. Neither appears anywhere.

CI found one defect. `tunnelled_target_dials_only_its_proxy_once`
passed only when a `tls.rs` test had first installed the rustls
crypto provider, which ryll needs to build a TLS verifier. The
test harness now installs it as `main.rs` does (`245daa5`).

One automated review round raised 0 fix, 2 document and
6 consider items, and was not re-run. It took both document items
and three consider items (`2025bfc`):
- A minted tunnel missing a field is refused before it is
  stored, rather than reaching protobuf as a gRPC INTERNAL
  error.
- A partly stored tunnel is minted again.
- The lane fails on a missing counter, and its leak check
  covers its own console output.

The demo compose lane failed twice before the rebase, because
the PyPI dev proxy wheel predated this proto change. That is the
residual case the lane's workflow header describes. It passed on
`2025bfc`.

## Back brief

Before executing any step of this plan, back brief the
operator on the intended approach and on any deviation from
this plan found during implementation. In particular:

- confirm decision 4 before 3b.2 writes the migration. It is
  the schema shape phase 4 inherits, and a migration is the
  expensive thing to redo;
- confirm decision 6. It moves kerbside's first Proxmox lane
  to phase 5, so nothing in this phase talks to a real node;
- say whether this session may push the branch and open the
  pull request once the definition of done is met. The
  direct-qemu lane cannot be dispatched, so its first run is
  on the pull request.
