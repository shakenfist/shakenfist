# Configuring Kerbside

Kerbside is configured via environment variables (prefixed with `KERBSIDE_`) or
an INI configuration file at `/etc/kerbside/kerbside.ini`. Environment variables
take priority over INI file settings. See `etc/kerbside.conf.example` for a
complete configuration example.

**Note**: This documentation should match `kerbside/config.py`. If you find
discrepancies, the source code in `config.py` is authoritative.

## Basic Settings

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| SOURCES_PATH | String (default ./sources.yaml) | The path the console sources file (sources.yaml) resides at. |
| SQL_URL | String | The SQLAlchemy SQL connection URL for the VDI proxy database. |
| CONSOLE_TOKEN_DURATION | Integer (default 1) | The number of minutes a console access token should be valid for. |
| AUTH_SECRET_SEED | String (no default) | A random string used to hash the signatures on JWT authentication tokens. Knowledge of this string is required to sign a JWT. |
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
| KEYSTONE_AUTH_VERIFY | Boolean or String (default True) | Whether to verify TLS sessions to Keystone. False to disable verification; True to verify using system CA bundle; or a path to a specific CA certificate or bundle. |
| KEYSTONE_SERVICE_AUTH_USER | String (no default) | The service account username. |
| KEYSTONE_SERVICE_AUTH_PASSWORD | String (no default) | The password for the service account. |
| KEYSTONE_SERVICE_AUTH_USER_DOMAIN_ID | String (default "default") | The domain the service account resides in. |
| KEYSTONE_SERVICE_AUTH_PROJECT | String (default "admin") | The project the service account resides in. |
| KEYSTONE_SERVICE_AUTH_PROJECT_DOMAIN_ID | String (default "default") | The project domain the service account resides in. |
| KEYSTONE_ACCESS_GROUP | String (default "kerbside") | The Keystone group that users wishing to access the VDI proxy administrative interface or REST API must be a member of. |

## Network Settings

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| PUBLIC_FQDN | String | The DNS name for the load balancers serving all Kerbside traffic. This needs to be configured to ensure DNS and the SSL certificates for the VDI proxy match or the certificates will be invalid. |
| PUBLIC_SECURE_PORT | Integer (default 5900) | Port secure connections should connect to on the PUBLIC_FQDN. This can be different from VDI_SECURE_PORT if there is a load balancing layer in front of Kerbside. |
| PUBLIC_INSECURE_PORT | Integer (default 5901) | Port insecure connections should connect to on the PUBLIC_FQDN. This can be different from VDI_INSECURE_PORT if there is a load balancing layer in front of Kerbside. |
| NODE_NAME | String (default "kerbside") | A unique name for each machine or container running the VDI proxy. This is used for logging and tracking purposes. |
| VDI_ADDRESS | String (default 0.0.0.0) | The IPv4 address to bind the SPICE proxy to. |
| VDI_SECURE_PORT | Integer (default 5900) | The port the VDI proxy will serve TLS SPICE sessions over. |
| VDI_INSECURE_PORT | Integer (default 5901) | The port the VDI proxy will serve insecure SPICE sessions over. These insecure sessions are only used to redirect the user to the secure port. |

## Control-plane gRPC service

These settings configure the `KerbsideProxy` gRPC service the daemon hosts over a unix domain socket, which the SPICE proxy consults for connection authorization and channel bookkeeping.

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| API_SOCKET_PATH | String (default /run/kerbside/api.sock) | The unix domain socket the KerbsideProxy gRPC service listens on. The containing directory is created with 0700 permissions; access is guarded by filesystem permissions since the peer is a trusted local process. The proxy must be co-located with the daemon to share this socket. Keep the path short: AF_UNIX socket paths are limited to ~108 bytes (SUN_LEN), and an over-long path will fail to bind/connect; a path under /run is safe. |
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

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| LOG_OUTPUT_PATH | String (default empty) | Where to write logs to. If "stdout", then stdout is used. If blank, syslog is used. |
| LOG_OUTPUT_JSON | Boolean (default False) | If true, log entries are in JSON format. |
| LOG_VERBOSE | Boolean (default False) | Whether to log verbose debugging information. |
| PROMETHEUS_METRICS_PORT | Integer (default 13003) | The TCP port that the prometheus metrics HTTP server will listen on. |
| PROMETHEUS_METRICS_ADDRESS | String (default 127.0.0.1) | The address the Rust proxy binds its `/metrics` server to. Defaults to loopback because the endpoint is unauthenticated and must not be exposed on the public VDI interface; set a management address (or 0.0.0.0 behind a firewall) to scrape it from another host. |

## Related Documentation

- [Console Sources](/components/kerbside/console-sources/) - Configuring sources.yaml
- [Proxy Architecture](/components/kerbside/proxy-architecture/) - Internal proxy design
