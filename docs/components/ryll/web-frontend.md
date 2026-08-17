# ryll --web operator guide

`ryll --web` exposes a SPICE session as an HTTP endpoint
serving a browser shell that talks to the SPICE server via
WebRTC. Single-viewer for MVP; multi-viewer is future work.

## Quick start

    ryll --web session.vv

Optional flags:

- `--web-host 127.0.0.1` — bind address. Defaults to
  loopback; use `0.0.0.0` for LAN access.
- `--web-port 0` — TCP port. Defaults to ephemeral.

The binary prints a URL with a per-launch token:

    ryll: serving web frontend at http://127.0.0.1:34567/?token=abc...

Open the URL in Firefox or Chrome. The browser fetches the
embedded HTML/JS/CSS shell, opens an `RTCPeerConnection`,
exchanges SDP via `POST /offer`, and starts streaming.

## What works

- **Display**: SPICE display channel rendered in the browser
  via H.264 over WebRTC.
- **Inputs**: keyboard and mouse from the browser to SPICE, in
  both mouse modes — absolute positions when the guest runs
  vdagent, relative deltas when it does not.
- **Cursor**: rendered as an `<img>` overlay above the
  `<video>`, positioned from the viewer's own pointer in client
  mouse mode and from the guest's reported position in server
  mode. The host browser cursor is hidden only once the SPICE
  server has actually sent a cursor shape, so a guest that sends
  none leaves you with the ordinary browser pointer rather than
  with no pointer at all.
- **Audio**: Opus passthrough from SPICE (no re-encoding) when
  the server negotiated Opus. PCM-only SPICE servers currently
  produce silent audio (a warning is logged).
- **Resolution**: the SPICE guest resizes to match the browser
  viewport at connect time (via vdagent
  `VDAgentMonitorsConfig`), and the encoder follows the guest
  through the resize. Without vdagent in the guest the resize
  does not happen and the browser scales whatever resolution the
  guest booted at, which looks soft.
- Ctrl-C cleanly stops the binary.

## Reconnect behaviour

`--web` mode is resilient to browser disconnects.

### Browser tab close → reopen

When the browser tab is closed (or the network between the
browser and ryll drops), the server-side bridge reaper
notices the `RTCPeerConnection` reaching a terminal state
(`Failed`, `Disconnected`, or `Closed`) within ~1 second.
The reaper:

1. Takes the bridge out of the active slot and closes it,
   tearing down the DTLS/SRTP state.
2. Calls `EncoderInfra::stop()` so the H.264 encoder task
   exits and CPU usage drops to idle.
3. Clears the audio pump.

The **SPICE session is left untouched**. Reopening the same
URL at any time establishes a fresh `RTCPeerConnection` via a
new `/offer` round-trip; the encoder restarts, requests a
keyframe, and the guest desktop appears within a few frames.

### Browser-side auto-reconnect

On transient ICE or connection-state failures the browser
retries automatically with exponential backoff:

| Attempt | Delay |
|---------|-------|
| 1 | 1 s |
| 2 | 2 s |
| 3 | 4 s |
| 4 | 8 s |
| 5 | 16 s |

After 5 failed attempts the status overlay shows
"Disconnected. Click to reconnect." and a button lets the
operator trigger a manual retry.

Each attempt constructs a brand-new `RTCPeerConnection` (no
stale SDP cache), resets the backoff counter on a successful
`Connected` transition, and retriggers the viewport-resize
message so the guest resolution re-syncs.

### Graceful shutdown

Ctrl-C or SIGTERM drains the axum HTTP server (existing
graceful-shutdown path) then explicitly closes any active
bridge before the process exits, ensuring DTLS/SRTP state
tears down cleanly.

## Limitations (MVP)

- Single viewer at a time. A second offer replaces the
  existing connection.
- No clipboard sync, USB redirection, or folder sharing
  (out of MVP scope).
- No multi-monitor (single video track, single primary
  surface).
- Browser audio autoplay policy: click the volume button on
  the page to enable sound after the page loads.

## Native TLS

ryll supports HTTPS natively via two flags:

    ryll --web session.vv \
        --web-tls-cert /path/to/cert.pem \
        --web-tls-key  /path/to/key.pem

Both flags must be supplied together; clap rejects one
without the other at parse time. Omitting both keeps the
default plain-HTTP behaviour.

**Accepted formats**: PEM-encoded certificate chain
(`cert.pem`) and PEM-encoded private key (`key.pem`).
A chain file should contain the leaf certificate first,
followed by any intermediate CA certificates.

When TLS is active the startup URL line prints `https://`:

    ryll: serving web frontend at https://0.0.0.0:8443/?token=...

**Cert rotation (MVP)**: ryll does not support inline cert
reload. To rotate a certificate, replace the files on disk
then restart the process (or `systemctl restart ryll-web`).
The URL token changes on each restart.

**Security layers**: WebRTC's media path is always
encrypted by DTLS-SRTP at the protocol level, regardless
of whether the signalling page is over HTTPS. Native TLS
protects the URL token and the signalling page (`GET /`,
`POST /offer`). DTLS-SRTP protects the audio/video media
stream. The two layers are complementary: use native TLS
for the signalling path whenever the traffic crosses any
untrusted network.

## Cert recipes

### mkcert (LAN dev)

[mkcert](https://github.com/FiloSottile/mkcert) installs a
local CA into your machine's trust store so browsers on
that machine accept the generated cert without a warning:

    mkcert -install
    mkcert ryll.lan 192.168.1.10

Pass the resulting `.pem` files directly to
`--web-tls-cert` and `--web-tls-key`.

### certbot (public DNS)

For a host with a public DNS A record and ports 80/443
reachable:

    certbot certonly --standalone -d ryll.example.com

Certs land at
`/etc/letsencrypt/live/ryll.example.com/fullchain.pem`
and `.../privkey.pem`. Auto-renewal:

    # /etc/cron.d/certbot-renew (or use certbot's timer)
    0 3 * * * root certbot renew --quiet \
        --deploy-hook "systemctl restart ryll-web"

### openssl one-off (self-signed)

For a one-afternoon diagnostic session where a browser
warning is acceptable:

    openssl req -x509 -newkey rsa:2048 \
        -keyout key.pem -out cert.pem \
        -days 30 -nodes -subj "/CN=ryll.lan"

The browser will show an untrusted-cert warning. Proceed
by adding a permanent exception, or use mkcert instead.

### Internal CA

For org-managed PKI, request a cert from your internal CA
and follow your org's cert-issuance documentation. The
output should be a PEM chain file and a PEM key file,
which pass directly to `--web-tls-cert`/`--web-tls-key`.

## Reverse-proxy fallback

If you already terminate TLS at a reverse proxy for
unrelated reasons, you can pass the plain-HTTP URL
through to ryll and let the proxy handle HTTPS. Native TLS
is recommended for new deployments — the reverse-proxy
path is documented here as a fallback only.

**Caddy** (autocert handles the cert lifecycle):

```caddy
ryll.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

That is the entire config for a publicly-reachable
deployment with an A record pointing at the host. Caddy
talks to Let's Encrypt automatically.

**nginx** (operator manages the cert separately):

```
proxy_pass http://127.0.0.1:8080;
```

A full nginx server block follows the standard
`proxy_pass` + `ssl_certificate` / `ssl_certificate_key`
pattern.

!!! warning "WebRTC media is not proxied"

    ICE candidates emitted by ryll point at ryll's host and
    port directly. The browser opens UDP flows to that
    endpoint — they never go through the reverse proxy.
    The proxy carries only the HTTP signalling page and the
    `POST /offer` request.

    Consequences:

    - **ryll's UDP ports must be reachable from the browser.**
      At startup ryll enumerates the host's non-loopback
      network interface addresses and binds one ephemeral UDP
      socket per address; the OS assigns the actual port per
      socket (typically from 32768–60999 on Linux). Open the
      relevant firewall ports on ryll's host. Pinning a specific
      port is not configurable yet, so a static firewall rule
      currently has to open the OS's whole ephemeral range
      rather than a single port.
    - **ryll advertises a candidate for every non-loopback
      interface address on the host, independent of
      `--web-host`.** `--web-host` controls only the HTTP/HTTPS
      signalling listener (`GET /`, `POST /offer`); it has no
      effect on which addresses WebRTC binds or advertises. If
      the host has more than one non-loopback interface (for
      example a public IP and a private LAN IP), ryll binds and
      advertises candidates for all of them — there is currently
      no way to select or restrict which addresses are used.

## Troubleshooting

### Page loads, video stays black for >10 seconds

**Likely causes:**

- **ICE failure** — UDP between the browser and ryll is
  blocked. Open the browser DevTools console and check the
  `RTCPeerConnection` connection state. If it is stuck on
  `connecting`, ICE negotiation has not completed.
  Fix: ensure ryll's UDP port range is reachable from the
  browser host (firewall / security-group rules). If ryll
  is behind a reverse proxy, see the callout in the
  Reverse-proxy fallback section above.

- **Encoder didn't start** — `RTCPeerConnection` reached
  `connected` but no frames arrived. Check ryll's stderr
  for encoder errors. The encoder requests a keyframe on
  the `Connected` transition; the first frame may take up
  to ~1 second. If no frame arrives after 10 seconds,
  the encoder task is wedged — restart ryll and file a
  bug.

### Video is soft or blurry

Most often the guest never resized, so the browser is scaling a
smaller desktop up to fill the window. Check what the encoder is
actually running at:

    web: encoder restarted at 1024x768@30fps

and compare it to the browser window. In the browser console,
`[ryll] viewport sent: W x H` says the viewport message went out;
`[ryll] viewport deferred` means the control channel was not open
yet and it will be re-sent when it opens. Server-side, run with
`RUST_LOG=info,ryll::web=debug` and look for:

    web inputs: viewport WxH

If that never appears, the guest is not being told to resize — the
usual cause is no `spice-vdagent` in the guest, since the resize is
delivered as a `VDAgentMonitorsConfig` message.

Two known limits, neither of which is a fault to chase:

- **The viewport is sent once per connection.** Resizing or
  maximising the browser window mid-session leaves the guest at the
  resolution it was given when the datachannel opened, and the
  browser goes back to upscaling. Reload the page to resize the
  guest.
- **Sizes are in CSS pixels.** On a HiDPI display the guest is
  asked for fewer pixels than the panel physically has, so the
  image is scaled up by the device pixel ratio no matter what the
  guest does.

Odd sizes are rounded down by one pixel before the guest is asked,
because H.264 cannot encode an odd dimension. A one-pixel border is
not what "blurry" looks like.

### The guest pointer does not move, or lands in the wrong place

Check the negotiated mouse mode in ryll's log:

    main: mouse mode=2 (client (absolute)), supported_modes=3

`supported_modes=1` means the guest is not running
`spice-vdagent`, so the SPICE server cannot offer client mode and
the session stays relative. ryll handles both, but a guest with no
agent also has no absolute pointing device, so the pointer is
driven by deltas and cannot be warped to a position.

If the pointer stops responding entirely part-way through a
session, look for:

    inputs: N consecutive pointer moves dropped ...

That is the ack window wedged — the server acknowledges only the
pointer messages it consumes, so a client sending the form the
server did not negotiate fills the window once and drops
everything after it.

In server mode specifically, expect the guest pointer and your own
to drift apart over a session. The browser reports absolute
positions and ryll converts consecutive ones into deltas, but the
guest then applies its own pointer acceleration to those deltas, so
the two diverge — and there is no warp in server mode to
re-synchronise them. The same conversion means that once your
pointer reaches the edge of the video element, no further movement
is reported in that direction and the guest pointer stops even
though the guest desktop has room. Both go away with Pointer Lock,
which the web frontend does not implement yet; installing
`spice-vdagent` in the guest avoids the whole class of problem by
getting you client mode. Moving your pointer back to the middle of
the window and continuing is the workaround.

### No audio, video works

**Likely causes:**

- **Browser autoplay policy** — the `<video>` element is
  muted by default to satisfy autoplay rules. Click the
  volume button on the page to enable audio.

- **PCM-only SPICE server** — ryll does Opus passthrough
  only in MVP. If the SPICE server negotiated PCM playback
  (no Opus), ryll logs a warning and audio will be silent
  until a future PCM→Opus encoder lands.

### "Click to reconnect" loop

The browser retries automatically five times with
exponential backoff, then shows a manual button.
If the button appears every time you reconnect, check
ryll's logs for:

    bridge reaper: bridge died, reaping

If this line is absent, the reaper task may not be running
or the bridge is not reaching a terminal state. Restart
ryll and file a bug with the full log.

### High CPU when no browser is connected

This should not happen. The bridge reaper
drops the H.264 encoder when the browser disconnects, so
CPU usage returns to near-idle. If you observe sustained
high CPU with no active browser session, check that the
reaper task is reaching the dead-bridge signal in the logs.
If it is absent, file a bug with ryll version and log.

### Every `/offer` returns 500 on a host with no network

`--web` needs at least one non-loopback interface address, even
when you are browsing from the same machine. WebRTC binds its own
UDP sockets and advertises their addresses as ICE candidates;
loopback is excluded because a candidate a remote browser cannot
reach is worse than a clear failure. On a host with networking
down, or a network-isolated container, `WebrtcBridge::new` has
nothing to bind and fails the request rather than handing the
browser an answer it can never connect to.

The error mentions ICE candidates and interface enumeration,
which does not obviously translate to "bring up a network
interface" — that is what it means. Connecting any interface,
including a bridge or a VPN tunnel, is enough. There is no
opt-in for loopback-only operation today; whether to add one
remains an open question.

### Cert load errors at startup

ryll prints a clear error chain on cert-load failure, for
example:

    Error: loading --web TLS cert/key from /etc/ryll/tls/cert.pem /
    /etc/ryll/tls/key.pem: ...

Common causes and fixes:

- **File permissions** — the ryll process must be able to
  read both files. Fix:

      chown ryll:ryll cert.pem key.pem
      chmod 0600 key.pem

- **Malformed PEM** — the file is not valid PEM. Re-export
  the cert/key from your CA or regenerate with openssl.

- **Mismatched cert/key** — the public key in the cert
  does not match the private key. Verify they were
  generated together.

### Browser shows cert warning

The certificate is self-signed or the browser does not
trust the issuing CA. Options:

- Use mkcert (see Cert recipes), which installs its CA
  into the system trust store automatically.
- Install your internal CA's root cert into the browser's
  trust store.
- Accept the browser warning for one-off / diagnostic
  access (the media path is still DTLS-SRTP encrypted).

### Ctrl-C ignored (historic)

Old ryll builds had a race where Ctrl-C was delivered before
the axum server was ready to drain. The
`with_graceful_shutdown` / `Handle::graceful_shutdown` path
fixed it. If you see this on a current ryll build, file a
bug; otherwise, update.

## Security note

ryll supports native HTTPS via `--web-tls-cert` /
`--web-tls-key` — this is the recommended deployment for
any traffic that crosses a network you do not fully
control. Plain HTTP is acceptable only for loopback-only
(`--web-host 127.0.0.1`, the default) or fully-trusted-LAN
deployments where the URL token is the only sensitive
material on the wire and you control all endpoints.

In all cases, the WebRTC media path (audio and video) is
encrypted at the protocol level by DTLS-SRTP, independent
of whether the signalling page is served over HTTPS.

## Service mode

For long-lived deployments, run ryll under systemd so it restarts
automatically on failure and logs go to the journal.

A reference unit file is at [`examples/ryll-web.service`](https://github.com/shakenfist/ryll/blob/develop/examples/ryll-web.service).
Copy it to `/etc/systemd/system/ryll-web.service`, then:

    systemctl daemon-reload
    systemctl enable --now ryll-web

### User and group

Create a dedicated unprivileged account:

    useradd -r -s /usr/sbin/nologin ryll

The unit runs as `User=ryll Group=ryll`.

### EnvironmentFile

The unit reads `/etc/ryll/web.env` so you can tune all parameters
without touching the unit file. Create it with owner `root:ryll`,
mode `0640`:

    install -d -o root -g ryll -m 750 /etc/ryll
    install -o root -g ryll -m 640 /dev/null /etc/ryll/web.env

Example `/etc/ryll/web.env`:

    VV_FILE=/etc/ryll/session.vv
    WEB_HOST=0.0.0.0
    WEB_PORT=8443
    WEB_TLS_CERT=/etc/ryll/tls/cert.pem
    WEB_TLS_KEY=/etc/ryll/tls/key.pem

The `.vv` file should be readable only by the ryll user:

    install -o ryll -g ryll -m 600 /dev/null /etc/ryll/session.vv

### Cert file permissions

The TLS key must be readable by the `ryll` user:

    chown ryll:ryll /etc/ryll/tls/cert.pem /etc/ryll/tls/key.pem
    chmod 0600 /etc/ryll/tls/key.pem

### Extracting the per-launch URL

ryll prints its URL with the per-launch token directly to stdout
(not via the tracing pipeline, so the token never reaches journald
or log aggregators). Under systemd, stdout is captured in the
journal only if the unit uses `StandardOutput=journal`. With the
default `StandardOutput=inherit` the URL goes to the terminal where
you launched the service. To read it from the journal when it is
captured there:

    journalctl -u ryll-web -n 50 --no-pager \
        | grep -oE 'https?://[^ ]+token=[^ ]+' | tail -1

The URL includes the token and is valid until the service restarts.

### Graceful shutdown

`KillSignal=SIGTERM` causes `systemctl stop ryll-web` to send SIGTERM.
This engages the graceful-shutdown path (`with_graceful_shutdown`
/ `Handle::graceful_shutdown`), which drains in-flight HTTP requests
and tears down any active WebRTC bridge cleanly. `TimeoutStopSec=10s`
is a generous ceiling; normal shutdown completes within ~5 seconds.

### Cert rotation (MVP)

ryll does not support inline cert reload. To rotate a certificate:

    # Install new cert/key into /etc/ryll/tls/, then:
    systemctl restart ryll-web

The URL token changes on each restart. Extract the new URL from the
journal using the recipe above.

### Hardening note

`ProtectSystem=strict` + `ReadOnlyPaths=/etc/ryll` prevents writes
outside the declared paths. If you use `--log-file` to write logs to
disk, add `ReadWritePaths=/var/log/ryll` (or your chosen path) to the
unit's `[Service]` section to relax the restriction.

## CI smoke test

`tools/web-smoke.sh` runs on every Linux PR in CI. It
launches `ryll --web` with a stub `.vv` file (pointing at a
non-existent SPICE server on a local nc listener), waits 3
seconds to verify the process has not exited prematurely,
sends SIGTERM, and asserts that ryll exits cleanly within
5 seconds. This catches regressions in HTTP-server
startup, rustls provider install, and SIGTERM handling
without requiring a real SPICE session.

macOS and Windows CI builds verify the `--web` dependencies
link correctly but do not run the smoke test (runtime smoke
is Linux-only for the MVP; see [portability](/components/ryll/portability/)).

## Project status

The `--web` mode ships end-to-end: display, audio, inputs,
cursor, reconnect, CI packaging, native TLS, and operator
documentation. See [the web frontend plan](/components/ryll/plans/PLAN-web-frontend/)
for the development history.
