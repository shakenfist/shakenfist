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
| `/run/kerbside/api.sock` (unix) | `API_SOCKET_PATH` | daemon | Rust proxy only | Control-plane gRPC between the daemon and its proxy. It is not a TCP port, but it is listed because it is why the daemon and its proxy must share a host. The API never dials it and may run elsewhere, since it reaches the daemon only through the database, though it reads the same `sources.yaml` and CA certificate from disk. |

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
leg is therefore a hypervisor port, not Kerbside's.

Whether the overlap matters on a host that also runs hypervisor SPICE
consoles depends on the cloud, so it is not a reason to move
Kerbside's ports everywhere:

- **oVirt** puts a console on whatever port the engine reports,
  typically from 5900 upwards ([oVirt](/components/kerbside/use-cases/ovirt/)). A
  Kerbside on one of those hypervisors does contend, and its
  `VDI_*_PORT` values should move out of the range.
- **Shaken Fist** allocates each console a random port in the
  30000-50000 range (`Instance._allocate_console_port` in Shaken
  Fist's `shakenfist/instance.py`), so Kerbside's defaults clear
  them. The role reserves nothing by default: on a Shaken Fist
  hypervisor pass the cluster's daemon ports and a range covering
  the consoles in `kerbside_reserved_ports`. The Ports section of
  `ansible/README.md` shows `[30000, 65535]`, which covers them with
  room to spare; keep ports of your own out of whichever range you
  reserve. That the consoles are nowhere near 5900 is part of why Shaken
  Fist's operator guide recommends putting Kerbside on a hypervisor
  ([Shaken Fist](/components/kerbside/use-cases/shakenfist/)).
- **Proxmox VE** puts each VM's SPICE listener above this range and
  fronts it with `spiceproxy` on port 3128, so a PVE node listens on
  nothing Kerbside's 5900/5901 defaults want. No lane co-locates the
  two -- the Proxmox lane runs Kerbside off-box from PVE -- so this
  one is reasoned from where PVE puts its consoles rather than
  exercised ([Proxmox VE](/components/kerbside/use-cases/proxmox/)).

For any other cloud, check where it puts its consoles before
co-locating.

## Connections Kerbside makes

| Destination | From | Purpose |
|-------------|------|---------|
| Hypervisor SPICE, **plaintext and TLS** ports, per console | Rust proxy | The backend leg. The proxy dials the plaintext port first and escalates to TLS only when the hypervisor answers `NEED_SECURED` and the console has a TLS port (`rust/kerbside-proxy/src/backend.rs`). So both ports must be reachable, and a hypervisor that accepts plaintext is relayed in plaintext. A console that carries a tunnel is the exception: the proxy dials only the HTTP CONNECT proxy it names, sends a plaintext CONNECT to it, and runs a TLS-only SPICE session inside the tunnel. |
| MariaDB/MySQL (`SQL_URL`, usually 3306) | API and daemon | The shared state and the only cross-node bus. The proxy never talks to the database. |
| Cloud APIs named in `sources.yaml` | daemon, and the API | Console discovery (Shaken Fist, oVirt engine HTTPS, the Proxmox VE API on 8006). A Proxmox source also needs the same host on 3128, for the CONNECT proxy. The API also calls out on the exchange paths: Keystone and Nova for OpenStack, and a single key refetch from Shaken Fist on an unknown key id. |

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
  the database, and each configured cloud's API. For a Proxmox VE source
  that is the host in its `url`, on 8006 for the API and 3128 for the
  CONNECT proxy every console is reached through.
- **Users never need a route to a hypervisor.**
