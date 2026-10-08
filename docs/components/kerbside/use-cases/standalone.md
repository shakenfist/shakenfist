# Kerbside standalone

Let's assume you have SPICE consoles on machines hand built
outside of a cloud, and you want to hand them to users
without handing out routes to the machines that host them.
Kerbside's `static` source
driver reads a fixed list of targets from `sources.yaml` and
fronts them with the same audited, firewalled proxy the cloud
deployments get.

## Value proposition

The [Shaken Fist](/components/kerbside/use-cases/shakenfist/), [OpenStack](/components/kerbside/use-cases/openstack/) and
[oVirt](/components/kerbside/use-cases/ovirt/) console sources put Kerbside in front of a platform that
already knows where its consoles are. Here there is none — a lab
bench, a CI job, a rack of appliance VMs no API will ever
enumerate — and the `static` driver is how you get the proxy's
session model, its audit trail and its SPICE firewall without
standing anything else up first.

- **There is nothing to stand up and nothing to authenticate
  to.** The driver makes no external call at all. There is no
  service account to create, no discovery CA to paste and have
  checked for equality, no discovery interval to tune, and no
  platform outage that can put the source into an errored state.
  (Reaching a target over TLS still needs that target's CA; what
  is absent is the second one, for talking to a platform.)
  Everything Kerbside knows comes from one local file, which is
  also the only thing that can be wrong.
- **The console list reloads every sixty seconds, in both
  directions.** The maintenance loop re-reads `sources.yaml` and
  rebuilds the driver from it, so a target added to the file
  becomes a console in a little over a minute — audit logged as
  `Discovered new console` — and a target removed from the file
  stops being one, audit logged as `Console no longer available`. No
  restart is needed, and neither direction has to be taken on
  trust: both leave a record. Editing an entry that is already
  there is applied the same way, and audit logged as `Console
  configuration changed` with the fields the edit touched.
- **The SPICE firewall is on by default.** Kerbside terminates
  the client's connection, drives the SPICE link handshake
  itself, and classifies every framed message against a
  per-channel allowlist. See
  [proxy-architecture.md](/components/kerbside/proxy-architecture/).
- **Sessions are objects, not TCP flows.** Every proxied console
  is a row you can list over the REST API, with an audit trail,
  and can terminate in flight. That is worth as much in front of
  a bench qemu as in front of a cloud, because qemu itself
  offers none of it.
- **It composes with the clouds.** A static entry sits in the
  same file as oVirt, Shaken Fist and OpenStack entries, which
  is how a target no platform knows about — an appliance, a bare
  qemu, something mid-migration — joins the same console list as
  everything else. One thing comes with that; see
  [User interaction model](#user-interaction-model).

In return, users get the SPICE features a serial console or an HTML5
wrapper cannot offer: high-resolution and multi-monitor
desktops, USB passthrough, audio, and adaptive compression.

## How it works

Nothing is discovered, but something is still enumerated.
Kerbside runs its ordinary maintenance pass over a static
source; the pass simply reads the file instead of calling a
platform API, and `sources.yaml` is the source of truth for what
exists rather than a cache of what a cloud said.

```mermaid
flowchart TD
    file["sources.yaml<br/>(the source of truth,<br/>not a cloud API)"]
    broker["Broker<br/>(your portal, or<br/>Kerbside's own web UI)"]
    client["SPICE client<br/>(remote-viewer, ryll)"]
    kerbside["Kerbside"]
    target["qemu SPICE server"]

    file -- "A. re-read every 60 seconds" --> kerbside
    broker -- "1. request" --> kerbside
    kerbside -- "2. .vv file" --> broker
    broker -- "3. deliver" --> client
    client -- "4. connect, token as password" --> kerbside
    kerbside -- "5. SPICE port → NEED_SECURED → TLS port,<br/>where the entry declares one" --> target
```

**The reload (A).** `_parse_sources()` re-opens the sources file
on every call, and the maintenance loop calls it every sixty
seconds. A target that has appeared in the file
is added and audit logged as `Discovered new console`. A target that has
been removed from the file is deleted and audit logged as
`Console no longer available` — the retention rule that keeps an OpenStack
cloud's rows indefinitely covers only sources the pass did not
enumerate, and this source is enumerated, so it does not apply.
Both directions therefore land in a little over a minute — the
loop sleeps a second at a time and fires once more than sixty
have passed — and both leave an audit event you can check rather
than a claim you have to believe.

**Edits to an existing entry.** A console which already exists
has its host, address, ports, name, `host_subject` and SPICE
password reassigned on every pass, so an edit lands in the same
little over a minute as an addition, in place: the console keeps
its row and its discovery timestamp. An edit is audit logged as
`Console configuration changed` with the names of the fields that
changed — `insecure_port`, say, or `ticket` for a rotated password,
whose value never appears — so an edit leaves the same kind of
record as adding or removing a target. A pass which finds an entry
unchanged records nothing. The proxy reads the password
from the database each time it authorises a connection, so the
change of password on qemu and the edit to the file should land
close together: in between, one of the two disagrees.

**What a bad edit does depends on how it is bad.** A console
entry which is malformed — not a dict, or missing a required
field — marks the whole source errored for that pass, and so
does deleting or misspelling the `consoles` key that holds the
entries. Either way the source is never enumerated, falls under
the same retention rule as an unreachable cloud, and what was
already published stays published rather than being deleted by
a typo. Only an explicit `consoles: []` says the source has no
consoles, and that does delete what it had. A mistake in the
file as a whole — a YAML syntax error, an emptied file, an entry
with no `source` name — is refused whole: the pass logs where the
problem is and changes nothing, so every source keeps what it had
published until the file is repaired. Either way a mistake costs
at most freshness, never the published inventory or the daemon.

**Nothing is fetched per request.** oVirt acquires a short-lived
credential from the engine for every `.vv` file, and OpenStack
calls Nova to validate a token on every exchange. Here the SPICE
password Kerbside presents to the target is written in the file
and stored with the console when the pass records it, so `.vv`
generation makes no call to anything. There is no external
dependency that can be down, which is the other half of "no
control plane".

**The client leg (4).** The `.vv` file points at `PUBLIC_FQDN`
and Kerbside's own ports, carries Kerbside's CA and, when
`PROXY_HOST_SUBJECT` is set, Kerbside's own certificate subject
for the client to check. It carries a short-lived Kerbside
console token as the SPICE password. The client authenticates to
*Kerbside*; the password you wrote in the file never leaves the
server side.

**The backend leg (5).** Kerbside dials the address the entry
gives, on the plaintext SPICE port it gives. Where the entry
also declares a TLS port, a `NEED_SECURED` answer from qemu
escalates the connection to it, and where the entry carries
`host_subject`, the proxy refuses a backend whose certificate
subject does not match — exactly, down to attribute count, order
and type. Both are optional and both default to absent, so the
default shape of this deployment is a plaintext backend leg:
acceptable on a loopback bench, not acceptable across a network.
See the limitations table.

## How to set it up

### The SPICE server side

The target needs a SPICE server listening on a port Kerbside can
reach, with a password set — Kerbside always presents one, so an
open SPICE server is not what this path expects.

Encrypting the backend leg takes three separate things, and two
of them are easy to mistake for the whole job. qemu needs its
TLS channel configured. Kerbside needs the CA that signed the
target's certificate, as `ca_cert` on the *source* rather than
on the target's entry: the daemon reads it for every source type
and forwards it to the proxy as the backend CA
(`AuthorizeConnection` in `kerbside/rpc/servicer.py`). And
`host_subject` on the entry pins which certificate is
acceptable. The CA is not optional decoration on top of the
other two — without it the protocol crate verifies the target
against the public web trust store, which an internal
certificate will not satisfy, so the escalation fails the
handshake rather than proceeding unverified.

Guests want `qemu-guest-agent` and `spice-vdagent` installed, as
they would for any SPICE console — they are what give you
clipboard sharing, display resizing, and clean resolution
changes. Kerbside relays the agent channel; it does not decode
it.

### Network

Kerbside needs direct L3 reachability to every target's SPICE
port, and to its TLS port where one is declared. Clients need to
reach only Kerbside. Nothing else needs a route anywhere,
because there is no API for Kerbside to call: the reachability
surface of this deployment is the targets and nothing more.

The failure mode the sibling pages warn about is sharper here.
Discovery is what usually notices that a platform has become
unreachable; there is no discovery on this path, so nothing at
all is contacted while the console list is being built. An entry
becomes a console whether or not anything is listening, and the
first thing that touches the target is the user's SPICE client.
See the limitations table.

### Kerbside side

Add a static entry to `sources.yaml`, with one block per target
saying where it is, which port to reach it on, and which SPICE
password to present — plus, optionally, a TLS port and the
`host_subject` to pin the backend leg against, and `ca_cert` on
the source itself if any target is to be reached over TLS. The
option and
field reference, a worked example entry, and the ryll
control-socket pairing for driving such a session headlessly are
all in
[console-sources.md](/components/kerbside/console-sources/#static-source).

General settings, including `PUBLIC_FQDN`, Kerbside's own ports
and CA, and `PROXY_HOST_SUBJECT`, are in
[configuration.md](/components/kerbside/configuration/).

### A worked example

There are two, and neither needs repeating here.

The compose demo in
[installation.md](/components/kerbside/installation/#try-it-the-demo-stack) is
the one to run: three containers, a disk-less qemu with a SPICE
server, a single static entry, and a real proxied SPICE session
at the end of it. It is this deployment in miniature, and
`demo/sources.yaml` is a commented example of the entry
described above, including why it deliberately leaves the
backend leg plaintext. What the stack is *not* is tabulated
under
[What the demo is not](/components/kerbside/installation/#what-the-demo-is-not),
and [demo/README.md](https://github.com/shakenfist/kerbside/blob/develop/demo/README.md)
is the reference for the stack itself.

The `direct-qemu` lane is the same shape under CI: the full
daemon, API and MariaDB against a local qemu SPICE server
declared through a static source, on every pull request and
nightly.
[testing.md](/components/kerbside/testing/#the-direct-qemu-lane) covers the
lane, and
[direct-qemu-harness.md](/components/kerbside/direct-qemu-harness/) covers the
daemon-less mock harness beside it, which drives the proxy with
no database and no daemon at all.

## User interaction model

Kerbside is a proxy, not a portal. Something has to ask it for a
console on the user's behalf and deliver the resulting `.vv`
file — the "broker" role described in the
[documentation index](/components/kerbside/index/). This is the deployment with
the least help available: there is no platform portal to embed
in and no platform token to exchange, so the options are
Kerbside's own web UI and REST API, or a portal of your own
written against that API.

That is sharpened by interactive login being Keystone-only today
([#300](https://github.com/shakenfist/kerbside/issues/300)). A
standalone deployment usually has no Keystone anywhere near it,
which leaves a human no way to log into the web UI, so in
practice such a deployment is driven by an API client holding a
token that something else minted.

The exception is the demonstration affordance the compose stack
uses. `kerbside demo token` mints a bearer token straight from
the signing seed, and it refuses to do so unless *every* source
configured in the file is `type: static`
(`_demo_sources_or_fail()` in `kerbside/main.py`), naming the
offending source and its type in the refusal and adding "See
issue #300 for the underlying gap". The reasoning is in the
code: a session token is not scoped to a source, so it
authorises every console of every configured source and there is
no coherent per-source version of the guard. That makes the
composability described above conditional — add a real cloud
beside your static entry and the command stops minting, by
design, because it stands in for authentication in a
demonstration rather than being one. Past that point, tokens
come from the API the way a broker would get them.

## Status and limitations

Kerbside is experimental overall. The standalone path is
exercised on every pull request rather than only in the merge
queue: the `direct-qemu` lane runs the full daemon, API and
database against a real qemu declared through a static source,
and drives a SPICE session through the proxy, including live
termination.

Not covered, and worth knowing before you deploy:

| Limitation | Detail |
|------------|--------|
| Nothing checks that the target is alive | There is no liveness check of any kind. An entry in the file is a console whether or not anything is listening on the port, so Kerbside will happily mint a `.vv` for a qemu that exited an hour ago and the user discovers it by the SPICE client failing to connect. Nothing in the console list, the web UI or the API distinguishes a live target from a dead one. This, rather than anything about the file format, is the honest reason the static source is not intended for production use. |
| The inventory is only as good as your editing | There is no discovery, so nothing ever corrects the file. A target rebuilt on a different port, or with a different SPICE password, is simply wrong until somebody edits it, and the wrongness shows up as a failed connection rather than as an errored source. The sixty-second reload makes the fix fast; it does not make it automatic. |
| Backend TLS needs three things, and is untested through this source | A static entry is plaintext to the target unless you declare a TLS port, unverified unless the source carries a `ca_cert`, and unpinned unless you write a `host_subject`. The CA is the one most easily missed: without it the target is checked against the public web trust store, which an internal certificate will not satisfy, so the escalation fails the handshake. The proxy's enforcement of a pin is exercised both ways in CI — a matching pin accepted, a mismatched one refused — but by `tools/direct-qemu/run-host-subject-checks.sh`, which drives the proxy from a mock control plane rather than from a source; the `direct-qemu` lane's own static entry is plaintext, and the compose demo deliberately leaves all three out. So the enforcement is proven and the path that reaches it *from this source* is not. |
| Nobody can log in | Interactive login is Keystone-only ([#300](https://github.com/shakenfist/kerbside/issues/300)), which a deployment with no OpenStack in it has nothing to point at, and the session JWT scheme has no revocation or issuance audit ([#301](https://github.com/shakenfist/kerbside/issues/301)). A standalone deployment therefore needs something else to hold credentials and call the API. |
| Duplicate identifiers are tolerated | Two entries in one source sharing an identifier produce a warning and the last definition wins. Nothing errors and nothing is marked unhealthy, so a copy-paste mistake silently publishes one target and hides another. |
| A bad edit freezes, rather than empties, the inventory | Validation is per source rather than per entry, so one entry missing a required field, or a missing or misspelled `consoles` key, marks the whole source errored and its published list is retained rather than refreshed — a stale list beside an errored source, not an empty one. A file which cannot be used at all, such as one with a YAML syntax error, freezes every source the same way until it is repaired. The first shows as an errored source in the administrative interface; the second marks nothing errored and is visible only in the daemon log. |
| The SPICE passwords are in the file | Each target's SPICE password is written in `sources.yaml` in the clear, as the cloud sources' credentials are — but here there is one per target rather than one per platform, so the file grows in sensitivity with the fleet. File permissions are the whole of the protection. |
| Scale is untested | The demo and the CI lane each declare a single target. Every pass re-parses the file and re-records every entry, and nothing bounds how that behaves at hundreds of them. No lane covers it. |

## See also

- [Console Sources](/components/kerbside/console-sources/#static-source) — the
  option reference for `type: static`, the example entry, and
  the ryll control-socket pairing
- [Installation](/components/kerbside/installation/#try-it-the-demo-stack) — the
  compose demo, which is the worked example for this page
- [Configuration](/components/kerbside/configuration/) — proxy settings,
  including `PUBLIC_FQDN`, ports, the proxy CA, and
  `PROXY_HOST_SUBJECT`
- [Proxy Architecture](/components/kerbside/proxy-architecture/) — the SPICE
  firewall, the connection state machine, and the relay
- [Testing](/components/kerbside/testing/#the-direct-qemu-lane) — the CI lanes,
  including the direct-qemu lane described above
- [The direct-qemu harness](/components/kerbside/direct-qemu-harness/) — the
  daemon-less local harness for driving the proxy against qemu
- [Kerbside for oVirt](/components/kerbside/use-cases/ovirt/),
  [Kerbside for Shaken Fist](/components/kerbside/use-cases/shakenfist/) and
  [Kerbside for OpenStack](/components/kerbside/use-cases/openstack/) — the sibling
  deployment guides
- [Multi-cloud aggregation](/components/kerbside/use-cases/multi-cloud/) and
  [Placement topologies](/components/kerbside/use-cases/placement/) — the two topology
  pages: several sources behind one Kerbside, and several
  Kerbsides in front of one cloud
