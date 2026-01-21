# Kerbside Documentation

Welcome to the Kerbside documentation.

## Documentation Version

This documentation covers:

- **SPICE Protocol Version**: 2.2 (current as of spice-protocol 0.14)
- **Kerbside Version**: Verified against `kerbside/spiceprotocol/packets/constants.py`
- **OpenStack Integration**: Nova 2025.1 (Epoxy) and later

Protocol documentation is derived from the official SPICE protocol sources at
`gitlab.freedesktop.org/spice/spice-protocol` and verified against Kerbside's
implementation. Message IDs and constants reflect Kerbside's internal mappings.

## Introduction

Kerbside is a [SPICE](https://spice-space.org/) protocol native VDI proxy
responsible for providing rich VDI experiences to users of Shaken Fist, oVirt,
OpenStack, or any other cloud where the hypervisor is capable of providing SPICE
consoles over the network.

The SPICE protocol has existed for a long time, and still represents the
richest and most performant option for remote desktops using Open Source
technologies. Before Kerbside, consoles were generally provided by a HTML5
transcoded interface in a web client. Unfortunately, HTML5 interfaces do not
support many of the more novel features of the SPICE protocol, nor do they
support high resolution desktops. By avoiding transcoding to a HTML5 client,
we avoid these problems.

Novel features of SPICE include high resolution desktops, multi-screen desktops,
USB device passthrough, sound, multiple user connections to a single console,
adaptive compression, and more.

### Kerbside is Experimental

Kerbside is currently considered experimental. While it works, it has not yet
seen large scale deployment and it is likely that it will need modifications
as it is hardened for production use.

### Kerbside is a Proxy, Not a Complete User Interface

Whilst Kerbside presents a simple administrative interface over HTTP and has
REST APIs for orchestrating consoles, it is not intended as a complete SPICE
desktop VDI solution. It is intended that Kerbside itself is orchestrated by
an external system. That is, in order for a desktop to be presented to a user
the following steps need to occur:

1. The user requests a desktop via an external user interface that we call "the
   Broker". In Shaken Fist's case the broker is embedded in Shaken Fist itself
   and is initiated via a Shaken Fist REST API. In the OpenStack case this role
   is likely performed by Horizon or Skyline, although this is not yet
   implemented.

2. The cloud boots the instance that runs the desktop. The Broker waits for the
   instance to be booted.

3. The Broker requests a `.vv` virt-viewer compatible ini file from Kerbside,
   and delivers that to the requesting user. The configuration file describes a
   connection to Kerbside, along with short lived access token.

4. The user opens the `.vv` file with a SPICE client such as `remote-viewer`.
   `remote-viewer` connects to Kerbside.

5. Kerbside uses the access token to determine which instance in the cloud is
   the requested desktop and initiates a proxied connection to the hypervisor.

6. The user then happily uses their SPICE console, largely unaware of these
   various steps.

### Connection Flow Diagram

```
+------------------+              +-------------------+              +------------------+
|                  |              |                   |              |                  |
|  External Broker |  1. Request  |     Kerbside      |  5. Connect  |   Hypervisor     |
|  (SF/Horizon)    +------------->|   SPICE Proxy     +------------->|   (QEMU/KVM)     |
|                  |   .vv file   |                   |   to console |                  |
+--------+---------+              +---------+---------+              +------------------+
         |                                  ^
         | 2. Return                        |
         |    .vv file                      | 4. Connect with
         v                                  |    access token
+--------+---------+                        |
|                  |                        |
|   User's SPICE   +------------------------+
|   Client         |  3. User opens .vv file
|  (remote-viewer) |
|                  |
+------------------+
```

### Implementation in OpenStack

OpenStack Nova now supports native SPICE direct consoles as of the 2025.1 Epoxy
release. The
[Nova specification](https://specs.openstack.org/openstack/nova-specs/specs/2025.1/implemented/libvirt-spice-direct-consoles.html)
adds a new "spice-direct" console type that enables users to access virtual
desktops using native SPICE clients like remote-viewer instead of HTML5 proxies.

When users request a SPICE direct console, Nova returns a URL pointing to
Kerbside with an authentication token. Kerbside validates this token via the
`/os-console-auth-tokens/` API and establishes the proxied connection to the
hypervisor. This provides users with a much richer virtual desktop experience
including support for USB passthrough, audio, and multi-monitor configurations.

Kerbside needs to be deployed as a component of the OpenStack cluster to provide
a safe mechanism for users to interact with their console. OpenStack is (wisely)
unwilling to provide direct network connectivity from a client network to TCP
ports on the hypervisor, and so Kerbside acts as an intermediary to protect
those hypervisors. There is a sample implementation of Kerbside deployment using
Kolla-Ansible in the
[Kerbside Patches repository](https://github.com/shakenfist/kerbside-patches).

### What About Bumblebee?

The folks over at the NECTAR research cloud developed
[Bumblebee VDI](https://github.com/NeCTAR-RC/bumblebee), which is superficially
similar to Kerbside in that it provides a mechanism to make it easier to obtain
a virtual desktop as a user. The Kerbside description above would classify
Bumblebee as a Broker to our model -- it orchestrates the creation and then
access to virtual desktops for users. However, Bumblebee exclusively orchestrates
HTML5 consoles using Apache Guacamole as its HTML5 proxy at the moment, so misses
out on some of the richer features of SPICE and has the performance implications
of a HTML5 desktop environment.

## Documentation Index

### Operator Documentation

Guides for deploying and configuring Kerbside:

- [Configuration](/components/kerbside/configuration.md) - Configuration reference for all Kerbside
  settings including TLS, Keystone, API, and monitoring options

- [Console Sources](/components/kerbside/console-sources.md) - Configuring console sources
  (sources.yaml) for Shaken Fist, oVirt, and OpenStack

- [Database Schema](/components/kerbside/schema.md) - Database tables, columns, and relationships

### Architecture Documentation

Internal design of the Kerbside proxy:

- [Proxy Architecture](/components/kerbside/proxy-architecture.md) - Internal architecture including
  process model, connection state machine, traffic inspection, and worker
  management

- [ARCHITECTURE.md](/components/kerbside/../ARCHITECTURE.md) - High-level system architecture
  overview (in project root)

### SPICE Protocol Documentation

Core protocol specifications covering connection handshake, authentication, and
channel message formats:

- [Protocol Overview](/components/kerbside/protocol-overview.md) - Introduction to SPICE protocol
  fundamentals including channel types, message structure, capabilities, and
  security model

- [Link Protocol](/components/kerbside/spice-link-protocol.md) - Connection handshake details
  including SpiceLinkMess/SpiceLinkReply formats, RSA key exchange, and
  authentication flow

- [Channel Protocols](/components/kerbside/channel-protocols.md) - Detailed message formats for each
  SPICE channel:
  - Main channel - Session control, channel negotiation
  - Display channel - Screen updates, drawing operations, video streaming
  - Inputs channel - Keyboard and mouse input
  - Cursor channel - Cursor shape and position
  - Playback/Record channels - Audio streaming
  - Smartcard channel - Smart card redirection

- [Keyboard Scancodes](/components/kerbside/scancodes.md) - Complete reference for IBM PC XT
  scancodes used by the inputs channel, including standard keys, extended keys
  (E0 prefix), and media/browser keys

- [Compression Protocols](/components/kerbside/compression-protocols.md) - LZ and GLZ image
  compression formats used by the display channel, including header formats,
  command encoding, and dictionary-based decompression

- [Capabilities](/components/kerbside/capabilities.md) - Channel capability negotiation including
  common, display, audio, and input capabilities with recommended settings

### Device Redirection Protocols

Protocols for redirecting client devices to the virtual machine:

- [USB Redirection](/components/kerbside/usb-redirection.md) - USB device redirection protocol
  (usbredir) including all control and data message formats, capability
  negotiation, and device filter rules

### Guest Agent Protocol

Protocol for advanced guest integration features:

- [VD Agent Protocol](/components/kerbside/vd-agent-protocol.md) - Guest agent protocol for
  clipboard sharing, file transfer, display configuration, and volume sync

## Protocol Implementation Status

| Feature | Status | Notes |
|---------|--------|-------|
| SPICE Link Handshake | Full | Token-based auth extension |
| Main Channel | Full | Session control, agent passthrough |
| Display Channel | Full | All drawing ops, video streaming |
| Inputs Channel | Full | Keyboard, mouse, scancodes |
| Cursor Channel | Full | Shape, position, trail |
| Playback Channel | Proxy | Audio passthrough |
| Record Channel | Proxy | Audio passthrough |
| Smartcard Channel | Proxy | Passthrough |
| USB Redirection | Proxy | Passthrough with hello decoding |
| Port Channel (VMC) | Full | USB redir encapsulation |
| WebDAV Channel | Proxy | Passthrough |
| VD Agent | Proxy | Clipboard/file transfer passthrough |

**Full**: Protocol decoded and logged during inspection
**Proxy**: Traffic forwarded transparently without decoding

## Quick Reference

### Channel Types

| ID | Channel   | Purpose |
|---:|-----------|---------|
|  1 | main      | Session control |
|  2 | display   | Screen updates |
|  3 | inputs    | Keyboard/mouse |
|  4 | cursor    | Cursor rendering |
|  5 | playback  | Audio output |
|  6 | record    | Audio input |
|  7 | tunnel    | Obsolete |
|  8 | smartcard | Smart card |
|  9 | usbredir  | USB devices |
| 10 | port      | VMC protocol |
| 11 | webdav    | File sharing |

### Error Codes

See [Protocol Overview - Error Codes](/components/kerbside/protocol-overview.md#error-codes) for the
complete list of SPICE link error codes.

### Common Capabilities

| Bit | Capability | Description |
|----:|------------|-------------|
| 0 | AuthSelection | Auth mechanism selection |
| 1 | AuthSpice | SPICE native auth |
| 2 | AuthSASL | SASL auth |
| 3 | MiniHeader | Compact message headers |

## External References

Official SPICE protocol resources:

- [SPICE Protocol Documentation](https://www.spice-space.org/spice-protocol.html)
- [SPICE Protocol Source](https://gitlab.freedesktop.org/spice/spice-protocol)
- [SPICE Server Source](https://gitlab.freedesktop.org/spice/spice)
- [USB Redirection Protocol](https://www.spice-space.org/usbredir.html)
- [usbredir Source](https://gitlab.freedesktop.org/spice/usbredir)
- [usbredir Protocol Spec](https://gitlab.freedesktop.org/spice/usbredir/-/blob/main/docs/usb-redirection-protocol.md)

## Contributing

When adding new protocol support or modifying existing implementations:

1. Update the relevant documentation in this directory
2. Include binary format descriptions with field offsets
3. Document any deviations from the official SPICE protocol
4. Add traffic inspection support in `spiceprotocol/packets/`
5. Update constants in `spiceprotocol/packets/constants.py`

## Document Conventions

Throughout this documentation:

- **Binary formats** use little-endian byte order unless noted otherwise
- **Offset** values are in bytes from the start of the structure
- **Size** values are in bytes
- **uint32**, **uint16**, **uint8** refer to unsigned integers
- **int32**, **int16**, **int8** refer to signed integers
- **bytes** refers to raw binary data
- **string** refers to null-terminated ASCII/UTF-8 text
- **S->C** means server to client direction
- **C->S** means client to server direction
