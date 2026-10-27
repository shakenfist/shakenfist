# ryll --rdp operator guide

`ryll --rdp` serves a SPICE session to a stock RDP client, so a desktop that
only speaks SPICE can be reached from a machine with an RDP client and no
SPICE one. One client is served at a time.

It is a reachability fallback, not a better path. The session is transcoded
from SPICE to RDP on the machine running ryll, which costs CPU there, and
the RDP client cannot use SPICE features such as USB redirection and folder
sharing. If you can run a SPICE client, run one. If all you have is a
browser, use [`--web`](/components/ryll/web-frontend/).

## Getting a binary

No release includes `--rdp` yet. The mode lives behind the `rdp` cargo
feature, which is off by default because it pulls in IronRDP and its
CredSSP stack, and the release builds leave it out. A binary built without
it refuses the flag:

    Error: this ryll binary was built without the `rdp` feature

and exits with status 1. Today you build it yourself.

With Docker, from a checkout of the repository:

    make release-rdp

This builds inside the project's devcontainer, as `make release` does, and
copies the binary to `target/rdp-smoke/ryll`. Use that copy rather than
`target/release/ryll`: both targets share `target/release`, so a later
`make release` replaces it with a build that has no `--rdp`.

With a host toolchain:

    cargo build --release -p ryll --features rdp

which leaves the binary at `target/release/ryll`. The
[development guide](/components/ryll/development/) lists the build dependencies, and
[installation](/components/ryll/installation/) points to it for building from source.

## Quick start

    ryll --rdp --file connection.vv

or, with no `.vv` file,

    ryll --rdp --direct 192.168.1.100:5900

ryll connects to the SPICE server, starts the RDP listener and prints what
to type into the RDP client:

    RDP: 127.0.0.1:3389
    user: ryll
    password: <24 random letters and digits>
    security: tls
    certificate SHA-256: 3A:7F:...:C2

The fingerprint is 32 colon-separated byte pairs, in the form FreeRDP and
mstsc show when they ask whether to trust a certificate. It is printed only
when ryll generated the certificate; with `--rdp-tls-cert` it is your own.
The password is printed even when you chose it with `--rdp-password`. These
lines go to standard output, as do ryll's log lines.

Optional flags:

- `--rdp-host 127.0.0.1` — bind address. Defaults to loopback; use `0.0.0.0`
  for LAN access. The address printed is the one bound, so with `0.0.0.0` it
  is `0.0.0.0:3389`, not something to connect to: use the host's own name or
  address.
- `--rdp-port` — TCP port. If you give none, ryll tries 3389 and uses an
  ephemeral port if 3389 is taken; the port bound is the one printed. A port
  you name is used or the launch fails, and `0` asks for an ephemeral port.
- `--rdp-user` and `--rdp-password` — what clients log in with. The user
  defaults to `ryll`. The password defaults to 24 random letters and digits,
  generated at each launch.
- `--rdp-security tls|nla` — how clients authenticate. See
  [Credentials and security modes](#credentials-and-security-modes).
- `--rdp-tls-cert` and `--rdp-tls-key` — your own certificate instead of a
  self-signed one generated at launch. See [Certificates](#certificates).

`--rdp` cannot be combined with `--web` or `--headless`. `--control-socket`
works with it, and is unaffected by SPICE reconnects; see the
[control socket protocol](/components/ryll/control-socket-protocol/).

Every flag is tabulated in [configuration.md](/components/ryll/configuration/#rdp-mode).

## Clients

Only one client has been tested.

| Client | Status |
|--------|--------|
| FreeRDP 3.15.0 (`xfreerdp3`) on Linux | Tested: display, keyboard, mouse, cursor, resize, clipboard, audio, both security modes, takeover and SPICE reconnect |
| mstsc on Windows | Expected to work; not tested |
| Windows App / Microsoft Remote Desktop on macOS, iOS and Android | Expected to work; not tested |
| Remmina and other FreeRDP-based clients | Expected to work; not tested |

"Expected to work" means the client speaks standard RDP, which is all ryll
asks of it. Nobody has checked. Differences between clients show up in
details such as which mouse buttons, audio formats and resize requests they
send, and the sections below say where they matter. A client that insists on
Network Level Authentication needs `--rdp-security nla`.

The FreeRDP command lines that matter:

    xfreerdp3 /v:HOST:PORT /u:ryll /p:PASSWORD /cert:tofu /dynamic-resolution /sound

- `/cert:tofu` remembers the certificate the first time and complains if it
  changes. With a generated certificate that is every launch; compare the
  fingerprint ryll printed with the one FreeRDP shows before accepting it.
- `/dynamic-resolution` lets the desktop follow the window when it is
  resized. Without it the client only sends the size it connected with.
- `/sound` turns audio on. `/sound:format:1` forces uncompressed PCM, the
  fallback ryll offers after Opus.
- `/sec:nla` is for a server started with `--rdp-security nla`.
- `-clipboard` turns the clipboard off.

Other clients have their own settings for the same things; mstsc's are on
its "Local Resources" tab.

## What works

- **Display**: the guest's primary display. Only one monitor is served; if a
  client sends a layout with several, ryll uses the primary one and ignores
  the others. A client that is slow to take frames is sent the pixels as they
  are by the time it asks, never a backlog.
- **Keyboard**: key presses and releases as scancodes, and the client's
  Scroll, Num and Caps Lock state.
- **Mouse**: position, left and right buttons and the vertical wheel, in both
  SPICE mouse modes. When the guest runs vdagent, positions are absolute.
  When it does not, ryll turns the client's positions into relative moves,
  and the guest's own pointer acceleration makes the two pointers drift
  apart; moving the pointer back to the middle of the window and carrying on
  is the workaround.
- **Cursor**: the guest's cursor shape, sent as an RDP pointer (scaled down
  to fit 96x96 when larger), hidden and shown with the guest's. The guest's
  cursor position is not sent: the client draws the pointer where its own
  mouse is.
- **Resolution**: ryll asks the guest to use the RDP client's desktop size,
  both the size the client connects with and the size its window changes to.
  The size asked for is rounded down to a multiple of 8, so a window 1366
  pixels wide gets a 1360 pixel guest. The guest only adopts a size if it
  runs vdagent. A connection whose size differs from the guest's sees a blank
  desktop for up to about 2 seconds at connect, before the client is resized
  to the guest's size.
- **Clipboard**: text, both ways, between the client and the guest (the guest
  needs vdagent). Line endings are converted between the client's CRLF and
  the guest's LF. A client's clipboard reaches the guest when it connects.
  Text the guest copied before the client connected is not offered to it;
  text copied afterwards is. Text the client copies that is longer than
  100 MiB, the most ryll can send a guest's vdagent in one message, is not
  shared, and a warning is logged. Rich formats and files are not shared.
- **Audio**: the guest's audio plays on the client. ryll offers Opus and then
  16-bit 48 kHz stereo PCM, and uses the first one the client accepts. Opus
  is SPICE's own packets passed through, a few tens of kbit/s. PCM is about
  1.5 Mbit/s. ryll has no flag to turn either off; do it in the client. ryll
  never sends a volume change.

## What is lost

- **USB redirection and folder sharing from the client**: the RDP client
  cannot pass its own USB devices or folders to the guest. ryll's own
  `--usb-disk`, `--usb-disk-ro`, `--share-dir` and `--share-dir-ro` are
  passed to the SPICE session as in the other modes, giving the guest a disk
  image or a directory from the machine running ryll; that has not been
  tested through `--rdp`.
- **Unicode key events**: some clients, mobile ones in particular, send a
  character with no scancode. SPICE input is scancodes only, so ryll drops
  them and logs a warning once: `RDP input: the client sent a Unicode key
  event, which SPICE cannot carry; dropping it`. Characters that arrive as
  ordinary key presses are unaffected.
- **Side mouse buttons and horizontal scrolling**: SPICE has neither, so they
  are dropped with a warning logged once.
- **The middle button, with clients that send it on the standard mouse
  message**: the RDP library ryll uses turns it into a plain pointer move, so
  the guest never sees the click. FreeRDP sends it on a channel of its own and
  is unaffected; this is
  [issue 604](https://github.com/shakenfist/ryll/issues/604).
- **Audio the client did not accept**: if the SPICE server sends raw PCM and
  the client chose Opus, ryll does not encode it and plays nothing; a warning
  is logged once.

## Credentials and security modes

The username and password are compared in constant time and are case
sensitive. The domain a client sends is ignored, because clients fill it in
inconsistently. ryll does not count or limit failed attempts, so on a network
you do not trust, leave the generated password in place: it has about 143 bits.

A generated password is printed at each launch, so it changes every time.
A password given with `--rdp-password` is visible to the other users of the
host in the process list.

`--rdp-security` chooses how clients authenticate, and a listener offers one
mode, not both:

- **`tls`** (default): TLS, with the username and password sent inside it and
  checked by ryll. Every client speaks it, including clients that also
  offer NLA.
- **`nla`**: Network Level Authentication (CredSSP). The client proves it
  knows the password before the session starts. A client that cannot do
  NLA is refused.

Both run inside TLS, so both need a certificate.

## Certificates

By default ryll generates a self-signed certificate at launch. It is held in
memory, so every launch has a new fingerprint, and clients that remember
certificates will complain at the next launch. The certificate names the bind
address and `localhost`; with `0.0.0.0` or `::` that is `localhost` alone, so
a client connecting by another name sees a name mismatch.

To present a certificate clients can trust, or one that survives a restart,
give both flags together (clap rejects one without the other):

    ryll --rdp --file connection.vv \
        --rdp-tls-cert /etc/ryll/tls/cert.pem \
        --rdp-tls-key  /etc/ryll/tls/key.pem

The certificate file is PEM and may hold a chain, leaf first. The key file may
hold a PKCS#1, PKCS#8 or SEC1 key if its name ends in `.pem`; with any other
name it must hold a PKCS#8 key. A file that cannot be read, a key that does
not match the certificate, or a missing flag fails the launch, before ryll
connects to SPICE. The certificate is read once at startup: to rotate it,
restart ryll.

The [certificate recipes in the web guide](/components/ryll/web-frontend/#cert-recipes) work
here too. A certificate from a CA the client trusts, for a name the client
connects to, is the way to stop its warning.

## One client at a time, and reconnect

A client that authenticates takes the session over from the one connected,
which is disconnected, and any keys or buttons it held are released in the
guest. A client that fails to authenticate leaves the connected one alone.
Only one client at a time may be between connecting and authenticating;
others wait unaccepted, and a client that has not authenticated within
30 seconds of being accepted is dropped.

If the SPICE connection is lost after a session has been established, ryll
keeps the RDP listener and the connected client up, shows the client the last
frame at half brightness, and retries SPICE after 1, 4 and 16 seconds.
Keyboard and mouse input and resize requests made meanwhile are discarded,
not replayed. When SPICE is back the client is sent the whole desktop at full
brightness. Text on the clipboard is kept and reaches the guest once it is
back.

ryll exits, disconnecting the client, when the first SPICE attempt never
reaches a session, when the three retries are used up, when the `.vv` file's
ticket is single-use or has expired so there can be no retry, and on
Ctrl+C. The exit status:

| Ended by | Status |
|----------|--------|
| Ctrl+C (SIGINT) | 0 |
| The first SPICE attempt never reached a session | 1, with that attempt's error |
| The retries used up | 1 |
| The ticket is single-use or expired when the session is lost | 1 if the lost session ended with an error, otherwise 0 |

[session-lifecycle.md](/components/ryll/session-lifecycle/#rdp-mode) has the details,
including what each of these looks like in the log.

## What it costs

These numbers are from one run, to show the order of magnitude. They were
measured on 2026-10-21 on a release build with the `rdp` feature, using
`tools/rdp-measure.sh` against the ryll process: CPU (100% is one core,
and includes SPICE decoding and RDP encoding) and bytes sent to the client,
over 30 seconds each. The client was FreeRDP 3.15.0, which negotiated
RemoteFX, on the same 16-core host as ryll, showing a 1024x768 desktop
from a 2 vCPU QEMU guest.

| Workload | Average CPU | Peak CPU | Average rate |
|----------|-------------|----------|--------------|
| Idle (20 s) | 0.3% | 1.0% | 0.6 kbit/s |
| Typing in a terminal | 8.5% | 12.8% | 294 kbit/s |
| Scrolling a Wikipedia article | 84.6% | 131.1% | 1.12 Mbit/s |
| Video (480p VP9, 853x480 region) | 248.2% | 298.8% | 109 Mbit/s |

An idle or typing session is cheap. Scrolling costs most of a core. Video is
expensive because the client is sent bitmaps of a region that changes about
28 times a second: expect to need a core or more per client, and a fast link,
for anything like it. The client shared a host with ryll, so the numbers say
nothing about a real network. [development.md](/components/ryll/development/), under
"Verifying `--rdp`", says how to repeat the measurement.

## Service mode

For a long-lived deployment, run ryll under systemd. There is no reference
unit file in the repository; this one is adapted from
[`examples/ryll-web.service`](https://github.com/shakenfist/ryll/blob/develop/examples/ryll-web.service)
and is a starting point, not something the project tests:

```ini
[Unit]
Description=ryll --rdp SPICE-to-RDP server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ryll
Group=ryll
EnvironmentFile=/etc/ryll/rdp.env
ExecStart=/usr/local/bin/ryll --rdp \
    --file ${VV_FILE} \
    --rdp-host ${RDP_HOST} \
    --rdp-port ${RDP_PORT} \
    --rdp-user ${RDP_USER} \
    --rdp-password ${RDP_PASSWORD} \
    --rdp-tls-cert ${RDP_TLS_CERT} \
    --rdp-tls-key ${RDP_TLS_KEY}
Restart=on-failure
RestartSec=5s
KillSignal=SIGINT
TimeoutStopSec=10s
StandardOutput=journal
StandardError=journal

NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadOnlyPaths=/etc/ryll
```

Things a unit has to get right:

- **`KillSignal=SIGINT`.** SIGINT is the clean stop: ryll disconnects the
  client, closes the listener and exits 0. ryll does not handle SIGTERM, so
  systemd's default `KillSignal` ends the process at once, with no goodbye to
  the client.
- **A fixed password.** A generated one changes at every restart, so the
  service wants `--rdp-password` from an `EnvironmentFile`. Create it
  `root:ryll`, mode `0640`, and keep the password out of the unit file. It is
  still on ryll's command line, so other users of the host can read it from
  the process list; there is no password-file flag yet
  ([issue 648](https://github.com/shakenfist/ryll/issues/648)).
- **The journal holds the password.** ryll prints the launch lines, password
  included, to standard output, and its log lines go there too. With
  `StandardOutput=journal`, as above, anyone who can read the unit's journal
  can read the password. There is no flag to keep it out.
- **A fixed port.** Without `--rdp-port`, ryll silently moves to an ephemeral
  port when 3389 is taken. Name the port and a clash fails the start instead.
- **Certificate files** readable by the `ryll` user, and the key not by
  anyone else:

      chown root:ryll /etc/ryll/tls/cert.pem /etc/ryll/tls/key.pem
      chmod 0640 /etc/ryll/tls/cert.pem
      chmod 0640 /etc/ryll/tls/key.pem

  Rotate by replacing the files and restarting the unit.
- **A reusable ticket.** If the `.vv` file's ticket is single-use, ryll cannot
  reconnect to SPICE and exits when the session is lost. If the session ended
  with an error, `Restart=on-failure` then starts it against a ticket that has
  been spent; if it ended cleanly, ryll exits 0 and is not restarted at all.
  Either way the service stops serving. A service wants a `.vv` file that
  stays valid, or `--direct` with `--password-file`.
- **Reaching the client.** `--rdp-host 0.0.0.0` (or the interface address)
  and a firewall rule for the TCP port. RDP is TCP only here.

## Troubleshooting

### `this ryll binary was built without the rdp feature`

The binary has no `--rdp`. See [Getting a binary](#getting-a-binary).

### The launch fails naming the port

    starting the RDP server: binding 127.0.0.1:3389: ...

You named a port with `--rdp-port` and something else is listening on it.
Pick another, or give `0` for any free one. Without `--rdp-port` this cannot
happen on 3389: ryll moves to an ephemeral port and prints it.

### The launch fails naming the certificate

    loading the RDP TLS identity from "cert.pem" and "key.pem": ...

The files could not be read or parsed. Check the permissions of the user ryll
runs as, and that they are PEM. `the RDP TLS certificate and key are unusable
or do not match` means the key is not the certificate's.

### The client connects and is refused

ryll logs a warning, with the username but never the password:

    RDP client presented wrong credentials

Check the username, the password (case sensitive) and, if the client insists
on NLA, whether the server was started with `--rdp-security nla`.

### The client hangs at connect, then drops

Only one client at a time may be between connecting and authenticating. If
another client, or a port scanner, connected first and has not authenticated,
yours waits unaccepted until it does or is dropped after 30 seconds. ryll
logs `the client did not authenticate within 30s` for the one dropped.

### A blank desktop for two seconds at connect

Expected when the client's size differs from the guest's: ryll asks the guest
for the client's size, and if the guest does not adopt it the client is
resized to the guest's after about 2 seconds. A guest with no vdagent never
adopts it.

### The desktop does not follow the window

The guest has to run vdagent, and FreeRDP needs `/dynamic-resolution`.
Sizes asked for are rounded down to a multiple of 8.

### The pointer lands in the wrong place or drifts

The guest has no vdagent, so SPICE is in relative mouse mode; see
[What works](#what-works). Installing `spice-vdagent` in the guest avoids the
whole class of problem.

### The middle button does nothing

See [What is lost](#what-is-lost). Use FreeRDP, or wait for the fix to
[issue 604](https://github.com/shakenfist/ryll/issues/604).

### The client shows a darkened frame

The SPICE connection was lost, and ryll is retrying. The log says so:

    rdp: reconnecting to SPICE in 4s (retry 2 of 3)

If the retries fail, ryll exits and logs why: `rdp: not reconnecting to SPICE:
...`.

### No audio

The client has to ask for it (`/sound` in FreeRDP). If ryll logs `the SPICE
server sends raw PCM but the client chose Opus, which ryll does not encode;
not played`, the SPICE server is sending raw PCM; force the client to PCM
(`/sound:format:1`).

### Pasting long text does nothing

Text over 100 MiB is dropped, with `the client's text is too long for the
guest; dropped` in the log. Both ends need to be running their clipboard
channels: the guest needs vdagent.

### Certificate warning

Expected with the generated certificate; see [Certificates](#certificates).

## Security note

The default bind address is loopback, so a fresh `ryll --rdp` is reachable
only from the machine it runs on. Everything below is about changing that.

Binding `0.0.0.0` or a LAN address exposes a session that can type into the
guest to anything that can reach the port. The password is the only thing
standing in front of it. Use the generated one if you can, or a long random
one; use your own certificate and have clients check it, because a client
that accepts any certificate gives the password to whoever answers; and keep
the port off networks you do not control.

ryll does not count or limit failed logins. It does bound what an
unauthenticated client can do: one at a time is accepted until it
authenticates, and each is dropped after 30 seconds. That means a client that
connects and says nothing can keep every other client waiting for 30 seconds
at a time, repeatedly. The listener has no other defence, so firewall it if
that matters.

Both security modes run inside TLS. A client that does not check the
certificate cannot tell ryll from someone in between, and in `tls` mode that
someone is handed the password. Verify the fingerprint either way.

## CI smoke lane

A nightly lane drives FreeRDP against `ryll --rdp` against a QEMU guest and
checks authentication, the frame, input, a client reconnect, takeover, a
SPICE reconnect and Ctrl+C. It is advisory, not a gate on pull requests; see
[the RDP smoke lane](/components/ryll/ci/#the-rdp-smoke-lane). Run it locally with
`make rdp-smoke`.
