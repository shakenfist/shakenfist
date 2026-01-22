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

## Traffic Inspection

Being able to inspect traffic being passed by the proxy is useful during both
development and whilst diagnosing issues in production but has obvious privacy
concerns.

The VDI proxy may be configured to log details of traffic for all sessions by
setting the `KERBSIDE_TRAFFIC_INSPECTION` environment variable to "1". This will
write session traffic details to the directory configured by
`KERBSIDE_TRAFFIC_OUTPUT_PATH`, in a sub directory per session identifier.
Additionally, more detailed information can be logged by also setting
`KERBSIDE_TRAFFIC_INSPECTION_INTIMATE` to "1".

Traffic inspection is per proxy not per session and implies a restart of the
proxy before it is enabled. This ensures that users are aware that traffic
inspection has been enabled. If traffic inspection is enabled, audit messages
are recorded per channel logged (as not all channels need to flow through the
same proxy machine). Additionally, the display channel is altered to show a
dashed red and yellow border to provide a visual warning that this inspection
is occurring.

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| TRAFFIC_INSPECTION | Boolean (default False) | Whether to log detailed traffic information. Defaults to false, but can be useful for debugging service issues in production. |
| TRAFFIC_INSPECTION_INTIMATE | Boolean (default False) | If TRAFFIC_INSPECTION is true and this option is also set to true, then log intimate debug details of sessions including keystrokes and all display frames. |
| TRAFFIC_OUTPUT_PATH | String (default empty) | Where to write traffic inspection logs to. This must be set if TRAFFIC_INSPECTION is True. |

## Logging and Monitoring Settings

The VDI proxy provides Prometheus style metrics on a configurable port (default
13003). These metric values are also logged for later processing if desired.

Values tracked include:

- Number of active console sessions
- Number of active console channels
- Bandwidth and latency information for each console channel
- REST API request statuses as a metric with labels for each HTTP status code
- REST API request response latency as a histogram per HTTP status code

| Configuration Option | Type | Description |
|---------------------|------|-------------|
| LOG_OUTPUT_PATH | String (default empty) | Where to write logs to. If "stdout", then stdout is used. If blank, syslog is used. |
| LOG_OUTPUT_JSON | Boolean (default False) | If true, log entries are in JSON format. |
| LOG_VERBOSE | Boolean (default False) | Whether to log verbose debugging information. |
| PROMETHEUS_METRICS_PORT | Integer (default 13003) | The TCP port that the prometheus metrics HTTP server will listen on. |

## Related Documentation

- [Console Sources](/components/kerbside/console-sources/) - Configuring sources.yaml
- [Proxy Architecture](/components/kerbside/proxy-architecture/) - Internal proxy design
