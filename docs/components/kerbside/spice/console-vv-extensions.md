# console.vv Extensions and Interpretations

The standard virt-viewer connection file format (the `.vv`
file produced by virt-manager, oVirt, OpenStack, Kerbside,
and other SPICE deployment tools) has no formal extension
registry. Producers add custom keys ad-hoc, and consumers
either honour or ignore them. This document tracks the keys
that **ryll** (the SPICE client at <https://github.com/shakenfist/ryll>)
either:

1. Reads with stronger or more specific semantics than the
   standard implies, or
2. Defines as new extension keys for SPICE deployments that
   want to opt in to ryll-specific behaviour.

Producers (Kerbside, oVirt, OpenStack drivers, custom gateway
implementations) can use this doc to populate their console.vv
output for the best ryll experience. Other SPICE clients
(remote-viewer, GNOME Boxes) ignore the extension keys and
function as before — extensions degrade gracefully.

## Standard format reference

The base console.vv format is documented in spice-gtk's
`virt-viewer-file.c` and (less authoritatively) the
[virt-viewer manual](https://virt-manager.org/). A minimal
file looks like:

```ini
[virt-viewer]
type=spice
host=spice.example.com
port=5900
password=ticket-string-goes-here
delete-this-file=1
```

ryll currently parses a subset: `host`, `port`, `tls-port`,
`password`, `ca`, `host-subject`. (Wider parity with the
standard format — `title`, `fullscreen`, `disable-channels`,
etc. — is tracked as a separate ryll work item; the keys
documented here are independent of that gap.)

## ryll's interpretation of standard keys

### `delete-this-file=1` → single-use ticket

The standard meaning of `delete-this-file=1` is "the consumer
should remove this file after reading it, because the
contents are sensitive". The spec is silent on whether the
*ticket inside* the file is single-use. Empirically, every
producer that emits one-shot tickets (Kerbside, oVirt) sets
`delete-this-file=1`, because a deleted file would be
useless for reuse anyway.

**ryll treats `delete-this-file=1` as a signal that the
ticket is single-use** and therefore:

- Disables automatic reconnect on disconnect.
- On disconnect, shows an explanatory modal ("Session ended —
  cannot reconnect; this connection used a single-use ticket")
  rather than the generic "Connection lost; click Reconnect"
  modal.
- Skips the doomed retry ratchet that would otherwise produce
  a stream of failed link-establishment attempts and
  associated bug-report zips.

If a future producer wants the file deleted but the ticket
*reusable*, this interpretation will surprise it. We have
not encountered that case in the wild and consider it a
deployment contradiction. If it ever arises, an explicit
override key can be added; we are not speculatively defining
one.

### `delete-this-file=0` or absent → reusable / permanent

ryll allows automatic reconnect against the same `password`
ticket. Suitable for development setups, local QEMU
instances, libvirt VMs reached over a stable host:port pair,
and any deployment where re-linking with the same credential
is expected to succeed.

## ryll-defined extensions

### `ticket-valid-until=<unix-ts>`

**Status**: defined by ryll, not in the standard format.

**Format**: integer Unix timestamp (seconds since epoch, UTC).

**Example**:

```ini
[virt-viewer]
host=spice.example.com
port=5900
password=ticket-string
delete-this-file=1
ticket-valid-until=1730500000
```

**Semantics**: the time after which the SPICE server will
reject this ticket regardless of any other consideration
(single-use status, whether it has been used before, etc.).
Producers that issue tickets with explicit lifetimes
(typically all gateway-style deployments) should set this.

**ryll behaviour when set**:

- The bottom status bar shows a countdown ("Session ends in
  04:32") so the user knows when to expect disconnect.
- 30 seconds before expiry, ryll pushes a notification
  ("Session ticket expires in 30 seconds") so the user has
  time to save their work or request a new connection.
- If the ticket is *not* single-use (`delete-this-file` not
  set or `=0`), ryll still respects the expiry: auto-reconnect
  attempts skip if the deadline is in the past.
- On disconnect after expiry, ryll shows a "Session ended —
  ticket expired" modal with the exact expiry time, instead
  of the generic disconnect modal.

**ryll behaviour when absent**: no behaviour change. The
absence is the default and matches today's console.vv
producers.

**Edge case — clock skew**: if the server appears to have
disconnected the session before `ticket-valid-until` (per
the client's local clock), ryll still renders the
expired-ticket modal (the server's view is authoritative)
and logs a warning about possible clock skew.

## How to support these in your producer

### Kerbside

Kerbside issues short-lifetime, single-use tickets. The
recommended console.vv shape:

```ini
[virt-viewer]
type=spice
host={{ gateway_host }}
port={{ gateway_port }}
password={{ ticket }}
delete-this-file=1
ticket-valid-until={{ ticket_issued_unix + ticket_lifetime_secs }}
```

The `ticket_lifetime_secs` value should match the actual
server-side validity window — the SPICE server invalidates
expired tickets at `reds.cpp` ticket-validation, so a
producer-side value that doesn't match the server-side
lifetime will produce surprising behaviour (UI says "valid
until 14:30" but server rejects at 14:25).

### oVirt

oVirt issues per-VM-console tickets via the engine API. The
existing console.vv emitted by oVirt sets `delete-this-file=1`
already, which ryll interprets correctly. Adding
`ticket-valid-until` requires a small enhancement to the
engine's console-file generation:

```python
# pseudocode — oVirt-engine equivalent
ticket = api.vms_service().vm_service(vm_id).ticket(...)
vv_content = render_vv_template(
    ticket_value=ticket.value,
    ticket_expiry_unix=int((ticket.expiry).timestamp()),
)
```

### OpenStack (nova-novncproxy / spice-html5 path)

OpenStack typically uses the noVNC / spice-html5 web
transcoder, which doesn't go through console.vv at all. If
your deployment also offers a native-viewer download (some
do, via novaclient `get-spice-console`), populate the same
two keys.

### Custom / homebrew gateways

The minimum to enable the ryll-specific UX is:

- Set `delete-this-file=1` if the ticket is single-use.
- Set `ticket-valid-until` to the unix timestamp at which the
  server will stop accepting the ticket.

Both keys are independent. A reusable ticket with an expiry
sets only `ticket-valid-until`. A single-use ticket without
a known expiry sets only `delete-this-file=1`.

## Future extensions under consideration

These are not implemented yet — they are placeholders to be
discussed before any client-side work begins. PRs / issues
welcome.

- `ticket-renewable=1` plus a `ticket-renew-url=<https-url>`:
  a client could request a fresh ticket from the named URL
  before expiry, allowing seamless long-running sessions
  without forcing the user back to the issuing portal. The
  obvious downside is the security model — the renew URL has
  to be authenticated by something other than the existing
  ticket, since the existing ticket is single-use.
- `monitors=N`: hint to the client about expected monitor
  count. Today ryll takes this as a CLI flag (`--monitors N`).
  Lifting to console.vv lets the deployment pre-configure
  multi-monitor setups based on what the guest can actually
  drive.
- `bug-report-url=<https-url>`: where ryll should POST
  bug-report zips for support cases, instead of leaving them
  on disk. Useful for managed-service deployments where users
  can't easily extract local files.

If you need a key not on this list, open an issue at
<https://github.com/shakenfist/ryll> referencing this doc so
the discussion lands somewhere lasting.
