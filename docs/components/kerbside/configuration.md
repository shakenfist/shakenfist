# Configuring Kerbside

Kerbside is configured via environment variables (prefixed with `KERBSIDE_`) or
an INI configuration file at `/etc/kerbside/kerbside.ini`. Environment variables
take priority over INI file settings. That path is hardcoded as `INI_PATH` in
`kerbside/config.py`; there is no setting which relocates it. See
`etc/kerbside.conf.example` for a complete configuration example.

INI values are read literally, with no `configparser` interpolation, so a value
is written the same way in the INI file as in the environment. A
percent-encoded database password such as `p%40ss` is written as-is; `%%` is
read as two percent signs. Releases before this change had interpolation
enabled and required `%%`, so an INI file written for them must have its
doubled percent signs collapsed when upgrading. If the file fails to parse,
every Kerbside process that loads configuration prints the parse error to
stderr and exits with status 1, so a supervisor such as systemd records a
failed start.

**Note**: This documentation should match `kerbside/config.py`. If you find
discrepancies, the source code in `config.py` is authoritative.

## Basic Settings

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| SOURCES_PATH | String (default ./sources.yaml) | The path the console sources file (sources.yaml) resides at. |
| SQL_URL | String | The SQLAlchemy SQL connection URL for the VDI proxy database. |
| CONSOLE_TOKEN_DURATION | Integer (default 1) | The number of minutes a console access token should be valid for. It also bounds the lifetime of the hypervisor tickets Kerbside mints for oVirt (a Proxmox VE ticket always lasts 30 seconds): a proxied session's ticket is asked to live for the token's remaining life plus 30 seconds, and a direct console's for this many minutes. |
| AUTH_SECRET_SEED | String (must be set) | A random string used as the signing key for JWT authentication tokens; anyone who knows it can forge a token for any user. Generate one with `openssl rand -hex 32`. The built-in default is the sentinel `~~unconfigured~~`, and the API refuses to start while the value is that sentinel or blank. |
| API_TOKEN_DURATION | Integer (default 60) | The number of minutes that a JWT is valid for after being issued. Importantly, Keystone credentials are only validated on JWT creation, so it is possible for a JWT to outlive the Keystone user access token it encapsulates. |

## TLS Settings

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| CACERT_PATH | String (default /etc/pki/CA/ca-cert.pem) | The path to the TLS CA certificate. |
| PROXY_HOST_SUBJECT | String | The TLS Host-Subject that matches the one set for VDI proxy certificates. |
| PROXY_HOST_CERT_PATH | String (default /etc/pki/CA/certs/proxy.pem) | The path to the TLS certificate for the proxy. |
| PROXY_HOST_CERT_KEY_PATH | String (default /etc/pki/CA/certs/proxy-key.pem) | The path to the file containing the key for the proxy TLS certificate. |

## Keystone Settings

These settings configure Keystone integration for OpenStack deployments.

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| KEYSTONE_AUTH_URL | String (no default) | The URL including scheme and port that is to be used to authenticate the Keystone service account for the proxy, and subsequently all proxy user authentications. |
| KEYSTONE_AUTH_VERIFY | Boolean or String (default True) | Whether to verify TLS sessions to Keystone, covering both the user's login and the service account's group check. False to disable verification; True to verify using system CA bundle; or a path to a specific CA certificate or bundle. True and false are case-insensitive; an empty value is a configuration error rather than silently disabling verification. |
| KEYSTONE_SERVICE_AUTH_USER | String (no default) | The service account username. |
| KEYSTONE_SERVICE_AUTH_PASSWORD | String (no default) | The password for the service account. |
| KEYSTONE_SERVICE_AUTH_USER_DOMAIN_ID | String (default "default") | The domain the service account resides in. |
| KEYSTONE_SERVICE_AUTH_PROJECT | String (default "admin") | The project the service account resides in. |
| KEYSTONE_SERVICE_AUTH_PROJECT_DOMAIN_ID | String (default "default") | The project domain the service account resides in. |
| KEYSTONE_ACCESS_GROUP | String (default "kerbside") | The Keystone group that users wishing to access the VDI proxy administrative interface or REST API must be a member of. |
| KEYSTONE_ADMIN_GROUP | String (default empty) | The Keystone group whose members are Kerbside administrators. Only administrators may fetch a direct console file from `/console/direct/<source>/<uuid>/console.vv`, which points the client at the hypervisor with the hypervisor's own SPICE ticket and so bypasses the proxy's firewall, session tracking and termination; everyone else receives a 403, and is not shown the Direct button. A Proxmox VE console has no direct file, and even an administrator's request for one receives a 409. Empty means nobody is an administrator. Before this setting existed every logged in user could fetch a direct file, so a deployment that relies on direct access must set it when upgrading. Membership is checked at login and carried in the session, so removing a user from the group takes effect only when their current session expires (`API_TOKEN_DURATION`) and they log in again; until then they can still fetch direct files. Issued and refused direct requests are recorded in the console's audit events. A configured group that does not exist in Keystone is logged as an error and makes nobody an administrator. |

## Network Settings

What each port is for, and who needs to reach it, is in
[network-ports.md](/components/kerbside/network-ports/).

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| PUBLIC_FQDN | String | The DNS name for the load balancers serving all Kerbside traffic. This needs to be configured to ensure DNS and the SSL certificates for the VDI proxy match or the certificates will be invalid. |
| PUBLIC_SECURE_PORT | Integer (default 5900) | Port secure connections should connect to on the PUBLIC_FQDN. This can be different from VDI_SECURE_PORT if there is a load balancing layer in front of Kerbside. |
| PUBLIC_INSECURE_PORT | Integer (default 5901) | Port insecure connections should connect to on the PUBLIC_FQDN. This can be different from VDI_INSECURE_PORT if there is a load balancing layer in front of Kerbside. |
| NODE_NAME | String (default "kerbside") | A unique name for each machine or container running the VDI proxy. This is used for logging and tracking purposes. |
| VDI_ADDRESS | String (default 0.0.0.0) | The IPv4 address to bind the SPICE proxy to. |
| VDI_SECURE_PORT | Integer (default 5900) | The port the VDI proxy will serve TLS SPICE sessions over. |
| VDI_INSECURE_PORT | Integer (default 5901) | The port the VDI proxy will serve insecure SPICE sessions over. These insecure sessions are only used to redirect the user to the secure port, but clients must still be able to reach it: see [network-ports.md](/components/kerbside/network-ports/#why-the-insecure-port-cannot-be-skipped). |
| PROXY_CLIENT_NOTSENT_LOWAT_BYTES | Integer (default unset) | `TCP_NOTSENT_LOWAT` on each accepted TLS client-leg SPICE socket (the insecure port only redirects, so it is left alone), in bytes: the most unsent data the kernel queues before the proxy stops relaying from the hypervisor. On a slow client link this moves the display backlog out of the proxy's send buffer and into spice-server's socket, which cuts the kernel memory the proxy holds per session, but it does not reduce keypress-to-draw latency: spice-server's per-channel ACK window bounds the backlog either way. See [the shaped-link measurement](/components/kerbside/performance/proxy-backpressure/). Unset uses the proxy's default (0, off); 0 disables the option. At most 4294967295. Setting it needs a `kerbside-proxy` that accepts `--client-notsent-lowat-bytes` (check `kerbside-proxy --help`): an older binary rejects the flag at startup, and the daemon exits with it. |
| PROXY_BACKEND_RCVBUF_BYTES | Integer (default unset) | `SO_RCVBUF` on each hypervisor-leg SPICE socket, in bytes, capping the backlog the proxy accepts from spice-server while a client is slow. Linux clamps the value to `net.core.rmem_max` (commonly 212992) and doubles it, and setting it turns off receive autotuning for that socket. The same trade-off as PROXY_CLIENT_NOTSENT_LOWAT_BYTES applies. Unset uses the proxy's default (0, off); 0 disables the option. At most 4294967295. Setting it needs a `kerbside-proxy` that accepts `--backend-rcvbuf-bytes` (check `kerbside-proxy --help`): an older binary rejects the flag at startup, and the daemon exits with it. |

## Shaken Fist console tokens

These settings govern the Shaken Fist console token exchange served at
`/sf-console.vv`, where Kerbside verifies a short-lived Ed25519-signed JWT
minted by Shaken Fist entirely offline. See
[Console Sources](/components/kerbside/console-sources/) for the full flow.

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| SF_CONSOLE_TOKEN_AUDIENCE | String (default empty) | The `aud` claim Kerbside requires on a Shaken Fist console token. Empty derives it as `https://<PUBLIC_FQDN>` (see PUBLIC_FQDN above). This value must equal Shaken Fist's own `KERBSIDE_URL` exactly — it is simultaneously the token audience and the base of the exchange URL — so set it explicitly whenever the public URL differs in scheme, port, or path. Note that `KERBSIDE_URL` is Shaken Fist cluster config, distinct from this Kerbside proxy setting. |

## Control-plane gRPC service

These settings configure the `KerbsideProxy` gRPC service the daemon hosts over a unix domain socket, which the SPICE proxy consults for connection authorization and channel bookkeeping.

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| API_SOCKET_PATH | String (default /run/kerbside/api.sock) | The unix domain socket the KerbsideProxy gRPC service listens on. The containing directory is created with 0700 permissions; access is guarded by filesystem permissions since the peer is a trusted local process. The proxy must be co-located with the daemon to share this socket. The REST API never connects to it; it reaches the daemon only through the database and may run on another host, given the same SOURCES_PATH and CACERT_PATH files. Keep the path short: AF_UNIX socket paths are limited to ~108 bytes (SUN_LEN), and an over-long path will fail to bind/connect; a path under /run is safe. |
| API_GRPC_WORKERS | Integer (default 8) | The size of the thread pool serving KerbsideProxy gRPC requests. |

## SPICE firewall

The proxy enforces an application-level SPICE firewall on every relayed
message: L0 framing/size limits and an L1 per-channel, per-direction message
-type allowlist derived from the SPICE protocol. Python owns the tunable policy
and delivers it to the proxy in the `AuthorizeConnection` reply (Python decides
policy; the Rust proxy enforces it). These settings are deployment-wide.

Firewall verdicts are exported as the Prometheus metric
`kerbside_proxy_firewall_verdicts_total{channel,direction,rule,action}` (where
`action` is `enforced` or `observed`) and coalesced into one audit event per
connection.

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| FIREWALL_MODE | String (default "enforce") | Enforcement mode delivered to the proxy. "enforce" applies blocking verdicts (a disallowed message type or an over-cap message terminates the session). "warn" downgrades every blocking verdict to forward-and-log (metric `action=observed`) so an operator can observe what enforcement *would* trip against real traffic before enabling it. Case-insensitive; an unrecognised value falls back to "enforce". |
| FIREWALL_PERMITTED_CHANNELS | String (default empty) | Comma-separated SPICE channel names the proxy may relay (main, display, inputs, cursor, playback, record, tunnel, smartcard, usbredir, port, webdav). Empty means permit all channels; a channel not listed is denied before relay (the client receives a protocol-correct permission-denied). |

The per-channel message-size caps and the (default-off) rate ceiling currently
use the proxy's compiled-in defaults and are not yet configurable; they are a
planned extension of the delivered policy.

## Logging and Monitoring Settings

The Rust proxy provides Prometheus style metrics on a configurable port
(default 13003), bound to a management address (see
`PROMETHEUS_METRICS_ADDRESS`).

Values tracked include:

- Total and currently-active connections
  (`kerbside_proxy_connections_total`, `kerbside_proxy_active_connections`)
- Authorized and denied connection counts
  (`kerbside_proxy_authorized_total`, `kerbside_proxy_denied_total`)
- Bytes relayed per direction
  (`kerbside_proxy_bytes_relayed_total{direction}`)
- Firewall verdicts (`kerbside_proxy_firewall_verdicts_total{...}`)
- spice-server's ACK window on display and cursor channels, followed from
  the relayed `SET_ACK`, `ACK_SYNC` and `ACK` messages without changing them:
  `SET_ACK`s seen by window size
  (`kerbside_proxy_ack_window_set_total{channel,window}`, where `window` is
  `20`, `40` or `other`; 40 means spice-server's start-up bandwidth test
  judged the client's link slow), server messages counted against the window
  (`kerbside_proxy_ack_window_messages_total{channel}`), client ACKs credited
  (`kerbside_proxy_ack_window_acks_total{channel}`), and how often and for how
  long a channel sat at the threshold where spice-server stops sending until
  an ACK arrives (`kerbside_proxy_ack_window_stalls_total{channel}`,
  `kerbside_proxy_ack_window_stalled_seconds_total{channel}`). The proxy
  counts a message once it has read it, so the stall figures are a lower
  bound when spice-server's own buffers hold a backlog. See
  [the ACK window measurement](/components/kerbside/performance/ack-window/).
- Backend display messages an experimental transcoded display channel could
  not draw (`kerbside_proxy_transcode_unsupported_total{kind}`, where `kind`
  is `draw_rop3`, `draw_stroke`, `draw_text`, `draw_composite`,
  `offscreen_surface`, `image_type` or `stream`). Each leaves a stale region
  on that session's client, because a transcoded channel cannot fall back to
  relaying once it has started. The series exists only in a `kerbside-proxy`
  built with the `transcode` feature and run with `--transcode-display`,
  which release wheels are not; see
  [Transcoding the display channel](/components/kerbside/proxy-architecture/#transcoding-the-display-channel-experimental).

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| LOG_OUTPUT_PATH | String (default empty) | Where to write logs to. If "stdout", then stdout is used. If blank, syslog is used. Daemon logs are always structured JSON, one object per line. |
| LOG_VERBOSE | Boolean (default False) | Whether to log verbose debugging information. |
| PROMETHEUS_METRICS_PORT | Integer (default 13003) | The TCP port that the prometheus metrics HTTP server will listen on. |
| PROMETHEUS_METRICS_ADDRESS | String (default 127.0.0.1) | The address the Rust proxy binds its `/metrics` server to. Defaults to loopback because the endpoint is unauthenticated and must not be exposed on the public VDI interface; set a management address (or 0.0.0.0 behind a firewall) to scrape it from another host. |

## Related Documentation

- [Console Sources](/components/kerbside/console-sources/) - Configuring sources.yaml
- [Proxy Architecture](/components/kerbside/proxy-architecture/) - Internal proxy design
