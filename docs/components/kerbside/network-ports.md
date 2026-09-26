# Network Ports

Every TCP port and socket a Kerbside node uses, what it is for, and
who needs to reach it. The settings named here are documented in
[configuration.md](/components/kerbside/configuration/); this page explains why each one
exists.

## Ports Kerbside listens on

| Port (default) | Setting | Bound by | Who connects | Purpose |
|----------------|---------|----------|--------------|---------|
| 5900/tcp | `VDI_SECURE_PORT` | Rust proxy | SPICE clients | TLS SPICE. All proxied SPICE traffic flows over this port. |
| 5901/tcp | `VDI_INSECURE_PORT` | Rust proxy | SPICE clients | Plaintext SPICE, used only to redirect. It reads the client's link message, answers `NEED_SECURED` and closes the connection. No authentication happens here and no SPICE traffic is relayed. |
| 13002/tcp | gunicorn `--bind` | gunicorn (REST API) | Brokers, users, admins | REST API, web UI, and the `.vv` exchange endpoints (`/sf-console.vv`, `/nova-console.vv`). |
| 13003/tcp, loopback | `PROMETHEUS_METRICS_PORT`, `PROMETHEUS_METRICS_ADDRESS` | Rust proxy | Prometheus | Unauthenticated `/metrics`. Bound to `127.0.0.1` by default. Never expose it on the VDI interface. |
| `/run/kerbside/api.sock` (unix) | `API_SOCKET_PATH` | daemon | Rust proxy only | Control-plane gRPC between the daemon and its proxy. It is not a TCP port, but it is listed because it is why the API, daemon and proxy must be co-located. |

The API port is not a Kerbside setting: it is whatever address you
give gunicorn. 13002 is the value used by the demo, the CI lanes and
the examples in these docs. Put TLS in front of it in production (see
[installation.md](/components/kerbside/installation/#what-a-running-kerbside-needs)).

### Why the insecure port cannot be skipped

It is reasonable to ask whether a TLS-only deployment can drop 5901.
Today it cannot, because every proxied `.vv` file Kerbside generates
names both ports:

```ini
port=<PUBLIC_INSECURE_PORT>
tls-port=<PUBLIC_SECURE_PORT>
```

`remote-viewer` and other spice-gtk clients open each channel on
`port` when the file names one. They move to `tls-port` only after the
server answers `NEED_SECURED`, which is spice-gtk's TLS upgrade path
(see [spice-link-protocol.md](/components/kerbside/spice/spice-link-protocol/)). A
firewalled 5901 therefore breaks those clients before TLS is ever
attempted. [ryll](https://github.com/shakenfist/ryll) goes straight to
`tls-port` when one is present, so it does not need 5901. You cannot
count on every user running ryll, though.

The redirect adds nothing to the attack surface beyond one listening
socket. The listener never authenticates, never talks to the control
plane, never dials a hypervisor and never relays traffic. Its whole
behaviour is the one-message exchange in
`rust/kerbside-proxy/src/listen.rs`. Once the client has upgraded,
none of a session's established connections are on 5901 (the demo
walkthrough in [installation.md](/components/kerbside/installation/) checks this with
`ss`).

Dropping the port would need a code change: `.vv` generation in
`kerbside/api.py` would have to omit `port=`, and the proxy would have
to stop binding it. Kerbside has no setting for this today.

### Public ports and bind ports

`VDI_SECURE_PORT` and `VDI_INSECURE_PORT` are what the proxy binds.
`PUBLIC_SECURE_PORT` and `PUBLIC_INSECURE_PORT` are what the `.vv` file
tells clients to dial on `PUBLIC_FQDN`. The two pairs differ only when
a load balancer or NAT sits in front of Kerbside. Whatever sits there
must forward **both** public ports, for the reason above.

### Kerbside's defaults versus a hypervisor's

Kerbside's defaults put TLS on 5900 and the plaintext redirect on
5901. Hypervisor SPICE ports use the same 5900-upwards range, but
their numbering follows the cloud's choices, and the plaintext port is
often the lower one. A `5900` in a use-case diagram about the backend
leg is therefore a hypervisor port, not Kerbside's. The overlap also
matters if Kerbside runs on a host that also runs hypervisor SPICE
consoles, because the two will contend for the same ports. Move
Kerbside's `VDI_*_PORT` values out of the range in that case.

## Connections Kerbside makes

| Destination | From | Purpose |
|-------------|------|---------|
| Hypervisor SPICE, **plaintext and TLS** ports, per console | Rust proxy | The backend leg. The proxy dials the plaintext port first and escalates to TLS only when the hypervisor answers `NEED_SECURED` and the console has a TLS port (`rust/kerbside-proxy/src/backend.rs`). So both ports must be reachable, and a hypervisor that accepts plaintext is relayed in plaintext. |
| MariaDB/MySQL (`SQL_URL`, usually 3306) | API and daemon | The shared state and the only cross-node bus. The proxy never talks to the database. |
| Cloud APIs named in `sources.yaml` | daemon, and the API | Console discovery (Shaken Fist, oVirt engine HTTPS). The API also calls out on the exchange paths: Keystone and Nova for OpenStack, and a single key refetch from Shaken Fist on an unknown key id. |

The per-cloud pages in [use-cases/](/components/kerbside/index/#use-cases) say which
hypervisor ports each cloud reports and the reachability traps
specific to it. See for example the "Network" sections of
[openstack.md](/components/kerbside/use-cases/openstack/#network) and
[ovirt.md](/components/kerbside/use-cases/ovirt/#network).

## Summary for a firewall review

- **From users:** 5900 and 5901 on `PUBLIC_FQDN`, plus the API port if
  users fetch `.vv` files or use the web UI themselves.
- **From brokers:** the API port.
- **From monitoring:** 13003, and only after you have moved
  `PROMETHEUS_METRICS_ADDRESS` off loopback onto a management address.
- **From Kerbside:** every hypervisor's plaintext and TLS SPICE ports,
  the database, and each configured cloud's API.
- **Users never need a route to a hypervisor.**
