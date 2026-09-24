# Proxmox console source

## Situation

Kerbside has source drivers for oVirt, Shaken Fist and a
static fleet. Proxmox has been deferred since 2026-08-02 for
want of a driver, leaving a "no source yet" row in
`docs/index.md`'s use-case table and the feasibility research
parked in PLAN-two-tier-ci.md's future-work section — a
footnote it had outgrown. It moved here on 2026-09-20, and
that section now points at this plan.

That research was done from documentation, because there was
nowhere to test it. There is now a way to build one: a
single-node PVE 9.2.20 on `debian:13`, deployed by prototype
Ansible in a private repository (under ten minutes, survives
a reboot, re-runs idempotently) and torn down after use.
Everything below was measured against such a deployment on
2026-09-20 rather than read. There is no standing node. Phase
1b ports that Ansible to `shakenfist/actions` so CI can build
one on demand.

This was written as a standalone plan, and was promoted to a
master plan on 2026-09-24 when phase 2 was planned. The two
questions that could have invalidated the design had by then
been answered by measurement. Promotion brings the mandatory
push-audit phase with it, the same promotion
PLAN-use-case-docs.md went through on 2026-09-18; see
*Execution*.

## Mission

Broker Proxmox VE SPICE consoles with the guarantees kerbside
gives every other source: a protocol-aware middle that
inspects the session, host-subject pinning on the backend
leg, and an audit trail.

One part of this does reach past Proxmox. Phase 3a moves
console-ticket minting into the authorize path, and the
measurements below show that is an improvement the oVirt
source wants on its own terms — so that phase changes
`ovirt.py` too, deliberately, rather than leaving it on a
7200-second default it never chose.

Out of scope: PVE's VNC consoles, PVE clustering (a
single-node source is enough to prove the model), direct
(unbrokered) `.vv` files for Proxmox consoles, for the reason
given under *What the measurements settled*, and the
Proxmox use-case documentation page, which belongs to
PLAN-use-case-docs.md's table and stays deferred until this
plan produces something to document.

## Why this is not another `ovirt.py`

The driver is the small half. Three layers of the existing
design assume a console can be dialled directly, and Proxmox
is the first source where that is false.

**1. The identity model has nowhere to put a tunnel.**
`BaseSource.__call__` yields `hypervisor_ip`, `insecure_port`
and `secure_port`, which `db.py`'s `Console` row stores and
`rpc/servicer.py` hands to the proxy. Proxmox binds qemu's
SPICE listener to loopback on the node with mandatory TLS;
the only way in is an HTTP CONNECT through `spiceproxy` on
port 3128, whose pseudo-hostname carries the ticket, the
vmid, the node and the port. There is no address to store in
those columns.

**2. Both credentials are minted per request, and the row
has one field to hold them.** `rpc/servicer.py` builds the
`Target` from the stored console row, `ticket` included. That
row is not as stale as it looks: it is refreshed at
token-issue time rather than at discovery. For oVirt,
`ConsolesProxyVirtViewer` fetches a fresh ticket, writes it
with `db.store_console_ticket()`, and only then mints the
kerbside token the client will connect with
(`api.py:482-527`). The authorize path never calls a driver,
and on this design it does not need to.

Proxmox cannot reuse that shape, and this is the finding that
shapes the plan. The pattern has an unstated precondition
oVirt's ticket lifetime satisfies: the credential must
outlive the gap between the client fetching its `.vv` and
actually connecting. A PVE ticket does not — it is good for
about thirty seconds, measured, and that gap is user-paced.
Minting has to move into `AuthorizeConnection`, which would
be the first time that path ever called a source driver. See
*What the measurements settled*.

The shape of what gets stored does not fit either: PVE
returns a SPICE password *and* a CONNECT pseudo-hostname
naming the ticket, the vmid, the node and the port, so
`Console.ticket` alone cannot carry a target — and with
minting moved to connection time, the fresh half of it should
not be stored on the row at all.

Here is what the call actually returns, from that deployment:

```json
{
  "type": "spice",
  "proxy": "http://pve1.example:3128",
  "host": "pvespiceproxy:6aaf3e30:100:pve1:61000::0ce019e3c7...",
  "tls-port": 61000,
  "password": "<40 hex characters, redacted>",
  "host-subject": "OU=PVE Cluster Node,O=Proxmox Virtual Environment,CN=pve1.example",
  "ca": "-----BEGIN CERTIFICATE-----\n..."
}
```

`host` is not a hostname. `proxy` is where the CONNECT goes,
`host-subject` and `ca` are the pinning material — the same
shape the oVirt driver already consumes — and `password` is
the SPICE ticket. One call yields everything a driver needs.

**3. The dialer is in another repository.**
`rust/kerbside-proxy/src/backend.rs` builds a
`ConnectionConfig` and hands it to `SpiceClient`, and the
dial itself is ryll's, at
`shakenfist-spice-protocol/src/client.rs:361`: a
`TcpStream::connect`, keepalives, an optional TLS wrap, then
the link handshake and auth. The CONNECT has to happen
between the TCP dial and the TLS wrap, which is inside that
function. This is the PLAN-host-subject shape again — a ryll
change first, a kerbside adoption second — and it is why the
transport work, not the driver, is what gates a Proxmox
source.

## Open questions

These are ordered by how much they can move the design.

The first two questions this plan was written with have
since been answered by measurement; see *What the
measurements settled* below. Questions 1 and 2 below are
settled by
[the phase 2 plan](/components/kerbside/plans/PLAN-proxmox-source-phase-02-ryll-connect/)
(decisions 1 and 4), and are kept here with their answers
because the reasoning is what later phases build on. Question
5 is settled by
[the phase 1b plan](/components/kerbside/plans/PLAN-proxmox-source-phase-01b-ci-substrate/).
The rest are open.

**1. Where does the CONNECT live — ryll or kerbside?** Either
`ConnectionConfig` grows an optional proxy, and
`connect_channel` performs the CONNECT before the TLS wrap;
or the crate grows an entry point that accepts an
already-connected stream and kerbside does the CONNECT
itself. The recommendation is ryll: it keeps every way of
reaching a SPICE server in one place, and ryll is itself a
client that someone will eventually want to point at a
Proxmox console directly. The cost is that a deployment
concern lands in a protocol crate. **Settled: ryll.** The
proxy is a typed `ConnectionConfig` field, parsed where the
string arrives rather than at connect time.

Putting the CONNECT in ryll does not weaken kerbside's
inspection. The CONNECT is a transport step beneath TLS: once
`spiceproxy` answers `200 OK` it relays opaque bytes, the TLS
session runs end to end between kerbside and qemu (pinned by
`host-subject`), and `connect_channel` still returns kerbside
a plaintext `SpiceStream` for the relay and firewall, exactly
as it does today. `spiceproxy` sees only ciphertext. What
would bypass kerbside is a client doing the CONNECT itself,
which is the direct `.vv` path this plan rules out for
Proxmox; a standalone ryll pointed at Proxmox is that case by
choice, not kerbside's brokered path.

**2. What is the TLS `ServerName` inside the tunnel?**
`connect_channel` derives it from `config.host`, which under
a tunnel is the CONNECT pseudo-hostname and not a DNS name at
all. Host-subject pinning already substitutes for hostname
verification, and PVE always supplies a subject, so the pin
is the identity check — but the crate must then *refuse* a
tunnelled connection that has no `host_subject`, rather than
quietly ending up with no identity check on the backend leg.
Both directions want a test, as PLAN-host-subject did.

**Settled**, and more pressing than it read here: the phase
2 survey found that `ServerName::try_from` rejects a
pseudo-hostname outright, because of its colons, so without
an answer every tunnelled handshake fails before it starts.
`SpiceClient::new` refuses a tunnel with no `host_subject` or
no `tls_port`. With a pin, the verifier forgives the name,
so under a tunnel the `ServerName` is the proxy's host and
serves only as SNI.

**3. Does `Target` grow a field or a transport sub-message?**
Either way it is a proto change, and proto changes carry the
contract-hash handshake from PLAN-proxy-dev-releases phase 3,
so the daemon and the proxy binary have to ship together.

**4. What is the least-privileged API account?** The
prototype deployment uses `PVEVMUser` on `/vms` plus
`PVEAuditor` on `/`, with a `privsep=0` API token. That works;
it is not proven minimal, and the same honesty the oVirt
use-case page applies to `SuperUser` applies here.

**5. Where does a CI lane get a node?** **Settled by phase
1b.** A composite action in `shakenfist/actions`,
`deploy-proxmox-on-shakenfist`, builds a node inside the
calling job. It is ported from the private prototype with its
private parts removed, and has its own weekly lane for
upstream drift. Ryll's lane uses it first and kerbside's
follows.

One constraint is already settled and is not an open
question: **the node's FQDN is load-bearing on our side.**
PVE derives the node certificate from it, and every ticket
carries both a `proxy` URL naming it and a `host-subject` to
pin against. A node whose domain does not resolve for the
broker hands out a proxy address that cannot be dialled and a
subject that will not match. Any lane must give its node a
resolvable domain rather than an invented one — which is a
bug the private deployment hit, and fixed, before it worked.
Phase 1b asserts at deploy time that `hostname -f`, the node
certificate and the ticket agree, and maps the FQDN on the
runner from the ticket itself.

## What the measurements settled

Measured on 2026-09-20 against that deployment, by minting a
ticket and driving the full CONNECT -> TLS -> SPICE
link -> auth path against it. Both numbers are also readable
in PVE's own source, which is quoted here because it explains
them.

**A ticket authorises many channels.** Six simultaneous
tunnels opened on one ticket all reached an authenticated
SPICE link (6/6). A session does not need a ticket per
channel, and `assemble_spice_ticket`'s "this should be used
as one-time password" comment describes intent rather than
enforcement.

**Two credentials expire separately, and fast.** The SPICE
password is qemu's, expired by `mon_cmd($vmid,
"expire_password", protocol => 'spice', time => "+30")`
(`PVE/QemuServer.pm:5934`, also `PVE/API2/Qemu.pm:3373`). The
proxy ticket is checked by `verify_spice_connect_url`, which
refuses it outside `-20 < age < 40` seconds
(`PVE/Ticket.pm:166`, whose own comment reads "use very
limited lifetime - is this enough?"). Neither is
configurable without patching PVE.

Probing one ticket every five seconds, and again with a
single clean probe per fresh ticket to be sure the probing
itself was not keeping anything alive:

| Age | CONNECT | SPICE auth |
|-----|---------|------------|
| 0-31s | 200 OK | Ok |
| 35-36s | 200 OK | PermissionDenied |
| 41-45s | 401 invalid ticket | — |

**So the usable window is about 30 seconds, and the
mint-at-token-issue pattern does not survive it.** The gap
between `ConsolesProxyVirtViewer` handing over a `.vv` and
`remote-viewer` actually connecting is user-paced — a browser
download prompt, an "open with" dialog, an application
launch — and 30 seconds is not a safe budget for that. The
same arithmetic rules out the direct `.vv` path
(`ConsolesDirectVirtViewer`) for Proxmox: the ticket is
embedded in the file, so the same user-paced gap applies, and
there is no broker in the path to mint a fresh one. A
Proxmox source therefore offers proxied consoles only, and
the direct handler refuses it with an explicit error rather
than handing out a file that works only if opened quickly.

Minting therefore has to happen at connection time, which
means `AuthorizeConnection` calling the source driver — the
first time that path would do so.

The obvious shape, a fresh ticket per channel, does not
work, and the reason is in the supersession finding below:
each mint sets a new qemu password, and a SPICE client opens
its secondary channels in parallel once the main channel
reports them. Channel B's mint lands between channel A's mint
and channel A's auth, and A fails. Per-channel minting is a
race the client wins only by luck.

So the default is **one ticket per session**: the first
channel's authorize call mints, and later channels in the
same session reuse that ticket for as long as it is valid.
A channel that arrives after the ticket has aged out (a
usbredir channel on a device plug, say) mints a replacement.
That is safe only if qemu does not re-check an established
channel's password when it changes, which is what the
supersession section says has not been proven. The ticket is
held in the daemon's memory, keyed by session, and never
written to the `Console` row. The failure mode this adds is
unavoidable whichever shape is chosen: PVE being unreachable
now breaks connection setup, not just discovery.

### Three protocol details that cost an afternoon

None of these are documented where a driver author would
look, and each presents as something it is not:

- **The connect string travels in the `Host:` header**, not
  the CONNECT request line. `PVE/APIServer/AnyEvent.pm:1559`
  reads `$request->header('Host')`. A CONNECT without it is
  answered `401 invalid ticket`, which is indistinguishable
  from an expired ticket.
- **The CONNECT target is `<pseudo-hostname>:<tls-port>`**,
  and the port is load-bearing: PVE signs it into the
  pseudo-hostname and `Ticket.pm:168` refuses the tunnel
  unless the two agree.
- **The `ca` field has its newlines escaped**, because it is
  written into a `.vv` file where a PEM has to survive as one
  INI value. It is not a usable certificate until they are
  put back.

Host-subject pinning was exercised in passing and behaves as
the oVirt driver would expect: the node certificate verifies
against the returned `ca`, and its subject matches the
returned `host-subject`.

### What oVirt does, measured the same way

Phase 3a moves minting into the authorize path because
Proxmox leaves no alternative. The obvious reading of that
is that it is a Proxmox workaround bolted onto a path the
other sources are happy with. Measuring oVirt the same way,
on 2026-09-20 against a freshly deployed 4.5 engine on Rocky
9, says otherwise.

| | Proxmox VE | oVirt |
|---|---|---|
| Default lifetime | ~30s, not configurable | **7200s** |
| Caller may request an expiry | no | yes, honoured |
| Ceiling on a requested expiry | — | none found |
| Channels served by one ticket | 6/6 | 6/6 |
| A second mint revokes the first | — | **yes** |

The engine grants 7200 seconds when the caller asks for
nothing, which is exactly what `ovirt.py` does:
`console_service.ticket()` with no `expiry`, taking
`BackendGraphicsConsoleHelper.DEFAULT_TICKET_EXPIRY`. The
expiry is honoured all the way down to qemu when it is asked
for (valid at 30s, `PermissionDenied` at 40s), and no
ceiling was found — a request for 99999 seconds was granted.

So oVirt is not at risk of expiry; it has the inverse
weakness. kerbside takes the most permissive expiry the API
offers by not asking, discards the `.expiry` the call
returns, and stores that eight-character hypervisor console
password on the `Console` row, where `CONSOLE_PUBLIC_FIELDS`
keeps it out of the API but nothing encrypts it at rest. A
ten-second session leaves a live console password in the
database for the remaining two hours.

**That makes phase 3a a cross-source improvement that
Proxmox happens to make unavoidable.** Once minting can
happen at connect time, `ovirt.py` can pass a short explicit
expiry and stop persisting a long-lived credential at all.
Proxmox forces the pattern; oVirt would choose it. Phase 3a
should therefore be scoped and reviewed as a change to the
minting path in general, not as a Proxmox prerequisite, and
whoever schedules it should expect to touch `ovirt.py`.

### A second mint revokes the first

This was not what the oVirt measurement went looking for,
and it is the part that matters beyond this plan.

Minting a second ticket for a VM invalidates the first
immediately: the engine sets the SPICE password on the
running qemu, a password has one value, and the first ticket
answers `PermissionDenied` the instant the second is issued.
Two brokered sessions against one VM cannot overlap — a
second viewer connecting does not merely get its own
credential, it revokes the first viewer's.

The same is structurally true of Proxmox, which also sets a
single qemu password, but its 30-second window makes it
nearly unobservable there.

This is a live property of the oVirt path today, not a
design input for a new driver. It is why per-channel minting
was rejected above, and it still constrains the
session-scoped design that replaced it: a replacement ticket
minted for a late channel revokes the credential every
earlier channel authenticated with. Whether that matters
depends on whether qemu re-checks an established channel's
password (it does not appear to — the six concurrent
channels stayed up) but "appears not to" is not a foundation
to build on. **Phase 3a cannot be designed without settling
this**, so it belongs in that phase's scope rather than as an
open question here.

## Proposed phases

The decomposition, with each phase's intent. Status and
phase plan links are tracked under *Execution*.

| Phase | Intent |
|-------|--------|
| ~~1. Ticket semantics~~ | Done 2026-09-20, before the plan was scheduled, because it gated the design. See *What the measurements settled* |
| 1b. CI substrate | A Proxmox deployment action in shakenfist/actions with its own lane, and a ryll lane on it that replaces phase 2's operator-assisted step 2f. Kerbside's lane follows on the same action |
| 2. Tunnelled transport in ryll | CONNECT support on the backend dial, with the `ServerName` and no-`host_subject` refusal decided and tested both ways |
| 3a. Minting at connect time | Minting moved into the authorize path through a source-driver hook, with one ticket per session held in daemon memory, and `ovirt.py` converted to request a short explicit expiry and stop persisting the ticket on the `Console` row. Settles the supersession behaviour above first. Not Proxmox-only, and needs nothing from ryll |
| 3b. Tunnel transport | `Target`/proto change (open question 3), an Alembic migration giving the `Console` row somewhere to record the node and the `spiceproxy` URL (the columns it has today assume a direct address), and `backend.rs` passing the tunnel through. `build_config` (`backend.rs:183`) builds `ConnectionConfig` by struct literal with `tls_port: None` and escalates to TLS only on `NEED_SECURED`, but phase 2 refuses a tunnel without a TLS port, so a tunnelled target must set `tls_port` from the start. Needs phase 2 merged and the pin bumped |
| 4. The source driver | `kerbside/sources/proxmox.py`: discovery over `/nodes/{node}/qemu`, console details over `spiceproxy`, CA and subject handling, and the direct `.vv` handler refusing Proxmox sources |
| 5. CI lane | A lane that proves an end-to-end proxied session, per open question 5 |
| 6. Docs | The use-case page PLAN-use-case-docs.md has been holding a row for, plus `console-sources.md` |

Phases 2 and 3a are independent and can run in parallel.
Phase 3a is the natural first kerbside phase: it has value on
its own, since it removes a two-hour console password from
the oVirt path, and it tests the session-scoped ticket design
against a source that already exists before Proxmox depends
on it.

Phase 1b lands in `shakenfist/actions` and ryll, and its ryll
half lands inside phase 2's pull request. Whether kerbside's
first Proxmox lane lands in phase 3b (a tunnel-dial lane) or
phase 5 (the end-to-end lane) is settled when 3b is planned.

Phase 1 was carried out against a deployment rather than in
this repository. Phase 2 lands in `shakenfist/ryll`, and a
phase that lands in another repository is audited there, as
the push-audit block requires.

## Execution

Promoted from a standalone plan on 2026-09-24, when phase 2
was planned. Promotion brings the `plan-push-audit-phase`
obligation from `PLAN-TEMPLATE.md` with it, which is phase 7
and is not optional. Each row records what landed it as it
lands. A phase that lands in ryll records
`ryll <sha> (#pr)` and is audited in ryll, in the pull
request that lands it.

Phase 1 has no plan file and no `Merged` cell. It was a
measurement against a deployment, it changed no code, and
its results are the *What the measurements settled* section
above. The push audit has nothing of it to read.

Phase 1b's `Merged` cell holds `actions <sha> (#pr)`; its
ryll half lands inside phase 2's merge and carries no
separate `Merged` entry of its own.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Ticket semantics | | Complete | |
| 1b. CI substrate | [PLAN-proxmox-source-phase-01b-ci-substrate.md](/components/kerbside/plans/PLAN-proxmox-source-phase-01b-ci-substrate/) | In progress | |
| 2. Tunnelled transport in ryll | [PLAN-proxmox-source-phase-02-ryll-connect.md](/components/kerbside/plans/PLAN-proxmox-source-phase-02-ryll-connect/) | In progress | |
| 3a. Minting at connect time | | Not started | |
| 3b. Tunnel transport | | Not started | |
| 4. The source driver | | Not started | |
| 5. CI lane | | Not started | |
| 6. Docs | | Not started | |
| 7. Push audit | | Not started | |

## Relationship to other plans

- **PLAN-two-tier-ci.md** — where the feasibility research
  was done and still lives, now pointing here. Its future
  work also proposes the Proxmox CI lane that phase 5 would
  build.
- **PLAN-use-case-docs.md** — holds the deferred Proxmox
  page; phase 6 is what unblocks that row.
- **PLAN-host-subject.md** — the precedent for a ryll change
  adopted by kerbside, and the origin of the pinning this
  source depends on more heavily than any other.
- **PLAN-proxy-dev-releases.md** — its phase 3 contract hash
  is why a `Target` change couples the daemon and the binary.

## Status

In progress. Phase 1 is complete, and phases 1b and 2 are
planned. Phase 2 is in progress in ryll (steps 2a and 2b
committed on `spice-http-connect`); its validation waits on
phase 1b.
