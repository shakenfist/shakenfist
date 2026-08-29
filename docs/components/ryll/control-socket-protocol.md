# Control Socket Protocol — Version 1.2

This document specifies the wire protocol spoken between a Ryll session
with no host window — `--headless` or `--web` — and any external driver
that connects to its Unix-domain control socket. It is the load-bearing
contract for the automated SPICE-test-harness work: external drivers
such as latency loadtests and Sextant scenario tests implement against
this document. Read the whole document before writing a client or
implementing a verb.

Protocol version: **1.2**

Version history:

- **1.0** — initial.  `hello`, `status`, `send_key`, `paste`,
  `screenshot`, `subscribe`, `unsubscribe` verbs; `latency`,
  `agent_connected`, `paste_completed`, `paste_failed`,
  `dropped` events.
- **1.1** — added the `surface_drawn` event (one wire event per
  display draw command) so a loadtest can compute
  keypress-to-screen latency rather than the `latency` event's
  PING-interval sample.
  Added the `digest_updated` event behind the `digest-decode`
  Cargo feature.  Backwards-compatible at the major-version
  level: v1.0 clients can still hello and operate; they just
  do not subscribe to v1.1 events.
- **1.2** — the socket became available in `--web` mode as well as
  `--headless`; no wire change, but the flag combination a client
  can expect to find a socket behind is wider.  Corrected
  `send_key`'s encoding of 0xE0-prefixed extended scancodes, which
  every version before this transmitted with the prefix byte second
  and the break bit on the prefix rather than the scancode — see
  the note under [`send_key`](#send_key).  Plain scancodes are
  unaffected, so a client that only sends those sees no difference.

---

## Contents

1. [Scope and non-goals](#scope-and-non-goals)
2. [Transport](#transport)
3. [Framing](#framing)
4. [Message envelopes](#message-envelopes)
5. [Hello handshake](#hello-handshake)
6. [Concurrency](#concurrency)
7. [Verb reference](#verb-reference)
8. [Event reference](#event-reference)
9. [Subscription semantics](#subscription-semantics)
10. [Backpressure](#backpressure)
11. [Error model](#error-model)
12. [Versioning](#versioning)
13. [End-to-end worked example](#end-to-end-worked-example)
14. [Implementation](#implementation)

---

## Scope and non-goals

### What this protocol covers

- Driving a Ryll session that has no host window — `--headless` or
  `--web` — from an external process over a Unix-domain socket. The
  GUI cannot host the socket; see the Transport section.
- Querying session state (SPICE connection status, surfaces, agent
  availability).
- Sending keyboard input as individual scancodes or as paste-as-
  keystrokes text.
- Capturing a screenshot of any live surface.
- Subscribing to an asynchronous stream of events: SPICE latency
  samples, agent connect/disconnect transitions, paste completion
  notifications, per-draw surface notifications, decoded
  visual-digest updates (on `digest-decode` builds, in both
  `--headless` and `--web`), and queue-overflow notifications.
- Negotiating the protocol version at connection time so clients and
  servers can evolve independently within a major version.

### What this protocol does NOT cover (v1 non-goals)

- **Mouse and USB-redirection verbs.** No current test consumer needs
  them. They will be added as new minor-version verbs when a test
  that requires them arrives.
- **Authentication or encryption on the socket.** Unix-socket file
  permissions are the security boundary for v1. Cross-host control is
  not a goal; if it ever becomes one, that is a separate design.
- **Multi-client concurrency.** Version 1 accepts exactly one client at
  a time. A second connection attempt while a client is connected
  receives a `busy` error and is closed immediately.
- **Control socket in GUI mode.** The socket is valid with
  `--headless` or `--web`, both of which run a session with no host
  window. Combining `--control-socket` with the GUI is a CLI error:
  the window owns input and the surface, so a second driver
  injecting events behind its back has no defined meaning.
- **Replacing the `--cadence`, `--paste-text`, or `--latency-file`
  flags.** Those flags keep working unchanged. The control socket is a
  new, orthogonal interface.

---

## Transport

The control socket is a **Unix-domain stream socket** (type
`SOCK_STREAM`). Its path is supplied by the caller via Ryll's
`--control-socket <path>` flag. This flag needs `--headless` or
`--web`; Ryll rejects it in GUI mode at launch.

On startup, Ryll:

1. Unlinks any existing file at the path (so a stale socket from a
   previous run does not block startup).
2. Creates a new socket file and calls `bind()`.
3. Sets the file mode to **0600** (owner read/write only) before
   calling `listen()`. File permissions are the sole access-control
   mechanism; protect the path accordingly.
4. Begins accepting connections.

When the SPICE session ends (normally or on error), Ryll closes the
listening socket and unlinks the socket file.

The socket is a streaming transport, not a datagram transport. Because
NDJSON framing (see below) is self-delimiting, the client and server
never need to know how many bytes to read in advance; they read until
they see a newline.

---

## Framing

All messages are framed as **line-delimited JSON** (NDJSON). Every
message is exactly one JSON value encoded as a single line terminated
by a `\n` (ASCII 0x0A) byte. No length prefix, no binary envelope, no
trailing null byte. Each line is a self-contained, parseable JSON
object.

- Encoding: UTF-8.
- Line terminator: `\n`. The sender appends `\n` after every
  serialised object. The receiver splits the byte stream on `\n`
  boundaries and feeds each non-empty line to a JSON parser.
- A single connection is full-duplex: the client writes request lines;
  the server writes response lines and event lines. Writes from each
  side are independent; neither side buffers until it hears from the
  other.
- The server never sends a partial line. If the server has nothing to
  say, it is silent. A reader that reads a complete `\n`-terminated
  line always has a complete JSON object.
- JSON values that span multiple lines are not supported. Do not pretty-
  print messages on the wire.

---

## Message envelopes

There are three envelope shapes. Every message on the wire is one of
these three.

### Request (client to server)

```json
{"id": 1, "method": "send_key", "params": {"scancode": 28, "state": "press"}}
```

Fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | integer or string | yes | Caller-chosen correlation token. The server echoes it in the matching response. Must be unique among in-flight requests on this connection. |
| `method` | string | yes | The verb name. See the verb reference section. |
| `params` | object | yes | Verb-specific parameters. May be `{}` for verbs that take no arguments. Never omit the key. |

The `id` field may be any JSON integer or JSON string. Integers are
recommended for simplicity. The server treats it as an opaque token;
it never performs arithmetic on it.

### Response (server to client)

Success:

```json
{"id": 1, "ok": true, "result": {}}
```

Failure:

```json
{"id": 1, "ok": false, "error": {"code": "bad_state", "message": "unrecognised state value \"sideways\""}}
```

Fields:

| Field | Type | Present when | Description |
|-------|------|--------------|-------------|
| `id` | integer or string | always | The `id` copied from the matching request. |
| `ok` | boolean | always | `true` on success, `false` on error. |
| `result` | object | `ok` is `true` | Verb-specific result payload. May be `{}`. |
| `error` | object | `ok` is `false` | Error descriptor. Contains `code` (stable string) and `message` (human-readable string). |

### Event (server to client, unsolicited)

```json
{"event": "latency", "data": {"sample_ms": 12.4, "wallclock_us": 1717000000123456}}
```

Fields:

| Field | Type | Description |
|-------|------|-------------|
| `event` | string | The event name. See the event reference section. |
| `data` | object | Event-specific payload. |

Events have no `id` field and never correspond to a request. They are
delivered whenever the subscribed condition occurs. A client that has
not called `subscribe` receives no events; see the subscription
semantics section.

---

## Hello handshake

Every fresh connection must begin with a `hello` request before any
other request. This rule exists so both sides can negotiate the
protocol version before doing anything that depends on a particular
verb signature.

### Ordering rule

The server tracks whether the current client has completed a successful
`hello` exchange. If the server receives any request other than
`hello` before a successful `hello` response has been sent, it
responds immediately with:

```json
{"id": <the_request_id>, "ok": false, "error": {"code": "no_hello_yet", "message": "first request must be hello"}}
```

Importantly, **the connection stays open** after this error. The client
may then send a `hello` request and continue normally. This is
intentional: a buggy client that accidentally sends one request out of
order should be able to recover without reconnecting.

### Hello request

```json
{"id": 1, "method": "hello", "params": {"client_name": "my-loadtest", "protocol_version": "1.0"}}
```

Parameters:

| Field | Type | Description |
|-------|------|-------------|
| `client_name` | string | A human-readable identifier for the client. Used in server logs; not validated. |
| `protocol_version` | string | The major.minor protocol version the client is requesting. Must be a dotted two-part string, e.g. `"1.0"`. |

### Hello response — success

```json
{"id": 1, "ok": true, "result": {"server_name": "ryll", "protocol_version": "1.2", "supported_methods": ["hello", "status", "send_key", "paste", "screenshot", "subscribe", "unsubscribe"], "supported_events": ["latency", "agent_connected", "paste_completed", "paste_failed", "dropped", "surface_drawn"]}}
```

Result fields:

| Field | Type | Description |
|-------|------|-------------|
| `server_name` | string | Always `"ryll"` in this implementation. |
| `protocol_version` | string | The version the server will speak. In v1.x this is `"1.2"`; older servers reported `"1.0"` or `"1.1"`. Clients should not compare for exact equality — see the compat note below. |
| `supported_methods` | array of string | The complete set of verb names the server recognises. Clients should use this list rather than hard-coding expectations, especially when connecting to a newer server. |
| `supported_events` | array of string | The complete set of event names the server can emit. |

### Hello response — version mismatch

If the client's `protocol_version` has a different **major** component
from the server's supported major version, the server responds with an
error and then **closes the connection**:

```json
{"id": 1, "ok": false, "error": {"code": "protocol_version_mismatch", "message": "server speaks major version 1; client requested major version 2"}}
```

The server writes the error line and then closes the socket. The client
will see EOF immediately after reading that line.

**Minor version mismatches are accepted in both directions.** A v1.3
client connecting to a v1.0 server, or vice versa, is fine: the server
responds with whatever minor version *it* speaks (e.g. `"1.0"` or
`"1.1"`), and both sides behave according to the features they each
know about. The client should check `supported_methods` and
`supported_events` at runtime rather than assuming every verb in this
document is available on every server.

---

## Concurrency

Version 1 supports **one client at a time**.

The server maintains a flag that tracks whether a client is currently
connected. A second `accept()` while a client is connected results in
the server writing a single error line on the new connection and then
closing it:

```json
{"ok": false, "error": {"code": "busy", "message": "another client is connected"}}
```

Note that this synthetic response does not have an `id` field, because
no request was received. A client reading this line can detect the
`busy` condition by checking `ok` and `error.code`. After writing
this line the server closes the new connection. The existing client is
unaffected.

Clients that need to retry should implement a simple back-off loop.
A reasonable strategy is to poll every 250 ms with a short timeout.

---

## Verb reference

Every verb subsection describes the params object, the result object on
success, the error codes specific to that verb, and a worked NDJSON
example. The common error codes (`no_hello_yet`, `unknown_method`,
`bad_params`, `internal_error`) are not repeated per-verb; see the
error model section for their definitions.

### `hello`

Documented in full in the hello handshake section. Repeated here for
completeness as a verb-reference entry.

Params:

| Field | Type | Description |
|-------|------|-------------|
| `client_name` | string | Human-readable client identifier. |
| `protocol_version` | string | Dotted `major.minor` string the client is requesting. |

Result on success:

| Field | Type | Description |
|-------|------|-------------|
| `server_name` | string | `"ryll"`. |
| `protocol_version` | string | The version the server will speak. |
| `supported_methods` | array of string | Verb names this server supports. |
| `supported_events` | array of string | Event names this server can emit. |

Verb-specific error codes:

| Code | When |
|------|------|
| `protocol_version_mismatch` | Major version in `protocol_version` does not match the server's major version. Connection is closed after the error. |

Worked example:

```
→ {"id": 1, "method": "hello", "params": {"client_name": "demo", "protocol_version": "1.1"}}
← {"id": 1, "ok": true, "result": {"server_name": "ryll", "protocol_version": "1.2", "supported_methods": ["hello", "status", "send_key", "paste", "screenshot", "subscribe", "unsubscribe"], "supported_events": ["latency", "agent_connected", "paste_completed", "paste_failed", "dropped", "surface_drawn"]}}
```

---

### `status`

Query the current state of the headless SPICE session.

Params: `{}` (none required)

Result on success:

| Field | Type | Description |
|-------|------|-------------|
| `spice_connected` | boolean | Whether the SPICE main channel is currently established. |
| `agent_connected` | boolean | Whether a SPICE vdagent is currently running in the guest. Some operations (paste) require the agent. |
| `surfaces` | array of surface objects | The set of display surfaces currently known to Ryll. May be empty while the session is still initialising. |

Each surface object:

| Field | Type | Description |
|-------|------|-------------|
| `channel_id` | u8 | The display channel number this surface belongs to. |
| `surface_id` | u32 | The surface identifier within that channel. Surface 0 is the primary surface. |
| `width` | u32 | Width in pixels. |
| `height` | u32 | Height in pixels. |

Verb-specific error codes: none beyond the common set.

Worked example:

```
→ {"id": 2, "method": "status", "params": {}}
← {"id": 2, "ok": true, "result": {"spice_connected": true, "agent_connected": true, "surfaces": [{"channel_id": 1, "surface_id": 0, "width": 1024, "height": 768}]}}
```

---

### `send_key`

Send a single keyboard scancode event to the guest.

Params:

| Field | Type | Description |
|-------|------|-------------|
| `scancode` | u16 | The AT-set 1 **make** code to send, in **logical** form — the way scancode tables are conventionally written. Extended scancodes (0xE0 prefix) are supplied as the full 16-bit value with the prefix byte in the **high** byte, e.g. `0xE04B` for left arrow. This is not the byte order SPICE uses on the wire; the server converts. Do not send a pre-swapped wire value. |
| `state` | string | One of `"down"`, `"up"`, or `"press"`. `"press"` sends a down event immediately followed by an up event in a single operation. |

The server owns both halves of the AT-set 1 encoding, and a client
should send the logical make code for every state:

- **Byte order.** SPICE carries the scancode as a little-endian
  32-bit value, and the wire wants the `0xE0` prefix *first*. The
  server swaps the two bytes, so the logical `0xE04B` is transmitted
  as `E0 4B`.
- **Release bit.** For `"up"` (and the up half of `"press"`) the
  server sets the break bit, which belongs on the scancode byte
  rather than on the prefix.

A client that passes a logical code with the release bit already set
for `"up"` — `0x9E` rather than `0x1E`, or `0xE0CB` rather than
`0xE04B` — still produces the identical wire value, because the
server ORs the bit in and OR-ing a set bit is a no-op. That
affordance is guaranteed and covered by a test.

> **Protocol 1.1 and earlier got extended keys wrong.** Servers
> before 1.2 injected the supplied value verbatim, so `0xE04B`
> reached the guest as `4B E0` — prefix second — and `| 0x80` put
> the break bit on the prefix byte. Every 0xE0-prefixed key was
> wrong in both directions, and a client could only reach the guest
> correctly by sending a pre-swapped wire value, which 1.2 rejects
> as the malformed input it always was. Plain (non-extended)
> scancodes are unaffected and behave identically across both
> versions. A client that needs extended keys should require
> `protocol_version` ≥ 1.2 from `hello`.

Result on success: `{}`

Verb-specific error codes:

| Code | When |
|------|------|
| `bad_state` | The `state` field contains a value other than `"down"`, `"up"`, or `"press"`. |

Worked example (Enter key press):

```
→ {"id": 3, "method": "send_key", "params": {"scancode": 28, "state": "press"}}
← {"id": 3, "ok": true, "result": {}}
```

Worked example (extended key, left arrow, explicit down then up):

```
→ {"id": 4, "method": "send_key", "params": {"scancode": 57419, "state": "down"}}
← {"id": 4, "ok": true, "result": {}}
→ {"id": 5, "method": "send_key", "params": {"scancode": 57419, "state": "up"}}
← {"id": 5, "ok": true, "result": {}}
```

Note: 57419 decimal is 0xE04B, the scancode for the left arrow key.

---

### `paste`

Paste a string of text into the guest by translating it into US-QWERTY
key events and queuing them for delivery.

This verb is **asynchronous**. The response is returned as soon as the
paste task has been queued, not when it has finished typing all the
characters. Completion (or failure) is communicated as a
`paste_completed` or `paste_failed` event delivered to any client that
has subscribed to those events. The `request_id` field in those events
matches the `id` of the `paste` request, so the caller can correlate
the outcome.

This design mirrors how `--paste-text` works internally: the paste
produces a series of synthetic `KeyDown`/`KeyUp` events spaced by
`char_delay_ms`, and those events are generated by a background task
that runs concurrently with the SPICE session loop. Blocking the
`paste` response until the last character is typed would stall the
control socket for potentially several seconds, which is worse than
async reporting.

If the client disconnects while a paste is in progress, Ryll cancels
the outstanding paste task and stops generating synthetic key events.
This is tracked per request ID via a cancellation token held in the
session's in-flight action registry.

Params:

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | The text to type. Must be representable in US-QWERTY layout (ASCII printable characters). Characters that cannot be represented will cause a `paste_failed` event. |
| `char_delay_ms` | u32 or null | Milliseconds to wait between each character. Defaults to 10 ms if omitted or null — a control-socket default, independent of the `--paste-char-delay-ms` CLI flag (whose default is 16 ms). Useful for guests with slow keystroke handling. |

Result on success: `{}` (returned immediately on queue, not on completion)

Verb-specific error codes:

| Code | When |
|------|------|
| `agent_not_connected` | The SPICE vdagent is not currently connected. The paste infrastructure depends on the agent for clipboard operations. Wait for an `agent_connected` event with `connected: true` before retrying. |

Worked example:

```
→ {"id": 6, "method": "paste", "params": {"text": "hunter2", "char_delay_ms": 20}}
← {"id": 6, "ok": true, "result": {}}
```

After some time, assuming the client is subscribed to `paste_completed`:

```
← {"event": "paste_completed", "data": {"request_id": 6, "chars_sent": 7}}
```

Or on failure (e.g. an unrepresentable character mid-string):

```
← {"event": "paste_failed", "data": {"request_id": 6, "reason": "character U+2022 is not representable in US-QWERTY"}}
```

---

### `screenshot`

Capture a PNG or raw RGBA image of a display surface and return it
inline in the response, base64-encoded.

This is a synchronous verb: the server locks the surface mirror,
snapshots the pixel buffer, encodes the image, and responds before
accepting another request. The response therefore may be large and slow
to arrive.

**Size and latency warning.** At 1024 x 768:

- A raw RGBA snapshot is 1024 x 768 x 4 = 3,145,728 bytes (~3 MB).
- A PNG encode of typical desktop content takes approximately 5-10 ms
  and produces roughly 300-900 KB.
- Base64 inflates the payload by 33 %. A 600 KB PNG becomes roughly
  800 KB as a JSON string.

Callers that need low-latency screenshots (e.g. a digest assertion
loop) should use `"format": "rgba"` to skip the PNG encode. Callers
that need human-readable images (e.g. test-failure artefacts) should
use `"format": "png"` (the default).

Headless mode does not instantiate a `SurfaceMirror` by default. The
mirror is created on first use of `screenshot` and kept alive until the
session ends. The first screenshot call may therefore take slightly
longer than subsequent ones as the mirror catches up on buffered
surface events.

Params:

| Field | Type | Description |
|-------|------|-------------|
| `surface_id` | u32 or null | The surface to capture. Defaults to surface 0 (the primary surface) if omitted or null. |
| `format` | string or null | `"png"` (default) or `"rgba"`. |

Result on success:

| Field | Type | Description |
|-------|------|-------------|
| `width` | u32 | Width of the captured surface in pixels. |
| `height` | u32 | Height of the captured surface in pixels. |
| `format` | string | The format actually used: `"png"` or `"rgba"`. Echoed back so the client does not need to remember its own request. |
| `data_base64` | string | The image data, base64-encoded (standard alphabet, no line breaks). For `"png"`, this is a complete PNG file. For `"rgba"`, this is a raw byte array in row-major order, 4 bytes per pixel (R, G, B, A), top-left origin. |

Verb-specific error codes:

| Code | When |
|------|------|
| `no_such_surface` | No surface with the requested `surface_id` exists in the current session. Check `status` for the available surface list. |
| `unsupported_format` | The `format` field contains a value other than `"png"` or `"rgba"`. |

Worked example:

```
→ {"id": 7, "method": "screenshot", "params": {"surface_id": 0, "format": "png"}}
← {"id": 7, "ok": true, "result": {"width": 1024, "height": 768, "format": "png", "data_base64": "iVBORw0KGgo..."}}
```

The `data_base64` value above is truncated; a real PNG will be
hundreds of kilobytes when base64-encoded.

---

### `subscribe`

Register interest in one or more named events. After a successful
`subscribe` call, the server will begin delivering matching events to
this client as they occur.

Event delivery is asynchronous. Events may arrive at any time after the
`subscribe` response, interleaved with responses to other requests.
There is no guarantee of ordering between events and responses on the
wire, except that the server writes each line atomically.

**Unknown event names are silently ignored** and will not appear in the
`subscribed` result. This is intentional forward-compatibility
behaviour: a client compiled against a newer version of this document
may ask for `digest_updated` (a v1.1 event, feature-gated) while
talking to a v1.0 server, or to a v1.1 server built without the
`digest-decode` feature, and neither knows the name. Rather than
failing the call, the server silently drops unrecognised names from
the result. The client
can check `subscribed` to discover which names were actually accepted,
and fall back gracefully if a name it needs is not present.

Params:

| Field | Type | Description |
|-------|------|-------------|
| `events` | array of string | The event names to subscribe to. Unknown names are silently ignored. |

Result on success:

| Field | Type | Description |
|-------|------|-------------|
| `subscribed` | array of string | The subset of requested event names that the server actually agreed to deliver. Names in `events` that the server does not recognise will not appear here. |

Verb-specific error codes: none beyond the common set.

Worked example:

```
→ {"id": 8, "method": "subscribe", "params": {"events": ["latency", "digest_updated"]}}
← {"id": 8, "ok": true, "result": {"subscribed": ["latency"]}}
```

In this example `digest_updated` was silently dropped — the server
is either a v1.0 build or a v1.1 build without the `digest-decode`
feature.

---

### `unsubscribe`

Cancel delivery of one or more named events. After a successful
`unsubscribe`, matching events will no longer be delivered to this
client.

Unsubscribing from an event name that the client is not currently
subscribed to is a **no-op** and is not an error. Similarly,
unsubscribing from an unknown event name is a no-op. The `unsubscribed`
result will reflect only the names that were actually removed from the
active subscription set.

Params:

| Field | Type | Description |
|-------|------|-------------|
| `events` | array of string | The event names to unsubscribe from. |

Result on success:

| Field | Type | Description |
|-------|------|-------------|
| `unsubscribed` | array of string | The subset of requested event names that were removed from the active subscription. Names not currently subscribed are absent from this list. |

Verb-specific error codes: none beyond the common set.

Worked example:

```
→ {"id": 9, "method": "unsubscribe", "params": {"events": ["latency"]}}
← {"id": 9, "ok": true, "result": {"unsubscribed": ["latency"]}}
```

---

## Event reference

Events are unsolicited lines written by the server. They have no `id`
and do not correspond to a request. The client receives only events it
has subscribed to. All events share the same envelope shape:
`{"event": "<name>", "data": {...}}`.

### `latency`

Emitted once per server PING on the SPICE main channel.

The sample is **not** a round-trip time. SPICE has no client-originated
probe — `SPICE_MSG_PING` is server→client only — so Ryll cannot measure
network RTT. What it measures instead is the client-observed interval
between two consecutive server PINGs, which includes the server's own
send cadence as well as the network path and Ryll's receive turnaround.
Spikes indicate a network or server stall; the absolute value largely
reflects how often the server chose to ping. See
[diagnostics.md](/components/ryll/diagnostics/) for the same explanation from the UI
side. (The `--latency-file` flag was intended to write this metric to
disk in headless mode, but is currently declared and unused — this
event stream is the working way to collect samples.)

The field names are unchanged, so this is a correction to the
description rather than a change to the wire contract.

High-frequency callers (e.g. a latency loadtest) should subscribe to
this event and accumulate samples client-side rather than polling with
`status` calls.

Data fields:

| Field | Type | Description |
|-------|------|-------------|
| `sample_ms` | f64 | Interval between consecutive server PINGs, in milliseconds. Always non-negative. |
| `wallclock_us` | u64 | Unix timestamp in microseconds at the moment Ryll emitted the sample, which is just *after* it responded to the PING. Useful for aligning samples with external wall-clock logs. |

Worked example:

```
← {"event": "latency", "data": {"sample_ms": 0.83, "wallclock_us": 1717000000456789}}
```

---

### `agent_connected`

Emitted when the SPICE vdagent connection state transitions. This event
fires on **transitions only** — once when the agent connects and once
when it disconnects. It does not fire periodically as a heartbeat. The
initial state after `hello` can be queried via `status`.

Callers that depend on agent-required verbs (`paste`) should subscribe
to this event and watch for `"connected": true` before issuing the
first paste request.

Data fields:

| Field | Type | Description |
|-------|------|-------------|
| `connected` | boolean | `true` when the agent has just connected; `false` when it has just disconnected. |

Worked example (agent connects):

```
← {"event": "agent_connected", "data": {"connected": true}}
```

Worked example (agent disconnects, e.g. guest shutdown):

```
← {"event": "agent_connected", "data": {"connected": false}}
```

---

### `paste_completed`

Emitted when a paste operation finishes successfully. The `request_id`
field matches the `id` of the `paste` request that initiated the
operation, allowing the caller to correlate the completion with its
original request.

Data fields:

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | integer or string | The `id` from the originating `paste` request. Type matches what the client sent. |
| `chars_sent` | u32 | The number of characters successfully typed into the guest. |

Worked example:

```
← {"event": "paste_completed", "data": {"request_id": 6, "chars_sent": 7}}
```

---

### `paste_failed`

Emitted when a paste operation fails before completing. This includes
cases such as an unrepresentable character in the text, an agent
disconnect mid-paste, or a client disconnect that caused the paste to
be cancelled.

Data fields:

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | integer or string | The `id` from the originating `paste` request. |
| `reason` | string | A human-readable description of why the paste failed. Not a stable code; do not parse this string programmatically. |

Worked example:

```
← {"event": "paste_failed", "data": {"request_id": 6, "reason": "agent disconnected during paste"}}
```

---

### `dropped`

Emitted once per drop episode to inform the client that events were
lost due to backpressure. See the backpressure section for the full
semantics. The cumulative count covers all events dropped since the
previous `dropped` event (or since the start of the session if this is
the first one).

Data fields:

| Field | Type | Description |
|-------|------|-------------|
| `count` | u32 | Number of events that were discarded since the last `dropped` event. |

Worked example:

```
← {"event": "dropped", "data": {"count": 14}}
```

---

### `surface_drawn`

Added in v1.1.  Emitted once per draw command that modifies a display
surface.  Fires unconditionally on every `ImageReady`, `ImageReadyChroma`,
`ImageReadyAlpha`, `FillRect`, `CopyBits`, and `Invert` event produced
by the renderer's display channel — `DisplayMark` (frame boundary) does
not fire `surface_drawn`.

The intended use case is computing keypress-to-screen latency: a
subscriber sends a `send_key down` and records its keypress wallclock,
then takes the wallclock of the first `surface_drawn` it receives
afterwards as the time-of-first-visible-pixel.  Subsequent
`surface_drawn` events from the same logical frame are deduplicated by
the consumer, not by the server.

Data fields:

| Field | Type | Description |
|-------|------|-------------|
| `display_channel_id` | u8 | The display channel that produced the draw. |
| `surface_id` | u32 | The SPICE surface id painted (0 is the primary surface). |
| `produced_at_secs` | f64 | Renderer-internal session-relative seconds at the moment the channel handler called `event_tx.send`.  Monotonic. |
| `wallclock_us` | u64 | Server wallclock at translation time, microseconds since the Unix epoch.  Cross-process consumers should compute deltas in this clock rather than mixing it with `produced_at_secs`. |

Worked example:

```
← {"event": "surface_drawn", "data": {"display_channel_id": 0, "surface_id": 0, "produced_at_secs": 1.234, "wallclock_us": 1717000000123456}}
```

Backpressure note: `surface_drawn` shares the same 256-slot per-client
event buffer as every other event (see [Backpressure](#backpressure)).
A guest that paints aggressively can fire hundreds of `surface_drawn`
events per second; slow consumers will see `dropped` events.  The
recommended pattern for the loadtest is to drain the queue as fast as
the consumer's CSV writer can flush, and to treat `dropped` as a soft
quality signal rather than a fatal error.

---

### `digest_updated`

Added in v1.1.  Available only when ryll is built with
`--features digest-decode`; on a default-features build (or any build
without `digest-decode`) the event is not advertised in the hello
response and `subscribe` for it returns an empty `subscribed` list.

Emitted when a new QR-encoded visual digest is detected on the primary
surface and decoded successfully.  The payload carries the parsed
digest record rather than the raw QR bytes; clients do not need to know
the visual-digest wire format.

Data fields:

| Field | Type | Description |
|-------|------|-------------|
| `frame_counter` | u32 | Monotonic frame counter from the digest header.  The polling task deduplicates by this value: an unchanged counter does not fire an event. |
| `framebuffer_hash` | u32 | CRC32C of the framebuffer at the moment the digest was encoded. |
| `events` | array | Decoded raw-record events from the digest.  Each element is the `shakenfist-visual-digest` crate's `Event` enum in its serde shape: an externally-tagged, single-key object whose key is the snake_case variant name and whose value is an object of the variant's fields (see below). |
| `wallclock_us` | u64 | Server wallclock at translation time, microseconds since the Unix epoch. |

The `events` array is a serde pass-through of the digest crate's
`Event` enum, not a normalised `{"kind": ..., "payload": ...}`
envelope.  Variant names and field names are snake_case; nested
enums such as `Phase` and `BootloaderChoice` serialise as
snake_case strings; booleans and numbers are native JSON values.
Observed on the wire:

```
{"event": "digest_updated", "data": {"events": [
  {"keypress": {"scancode": 0, "timestamp_ms": 138550, "unicode": "\r"}},
  {"scene_transition": {"from": "awaiting", "timestamp_ms": 138550, "to": "booting"}},
  {"line_rendered": {"row": 0, "timestamp_ms": 138550}}
], "frame_counter": 281, "framebuffer_hash": 4022250974, "wallclock_us": 1765432100000000}}
```

Clients matching on these records should treat unknown variant
keys as forward-compatible additions and skip them.

Future-work notes:

- The polling task runs on a fixed 100 ms interval.  A rate-limit knob
  (`--digest-min-interval-ms`) may be added if a future consumer
  needs it.
- Because the wire `events` schema tracks
  `shakenfist-visual-digest::Event` directly, a schema-breaking v2 of
  the digest crate changes this event's payload and the protocol will
  need a re-cut.

---

## Subscription semantics

The default state for a new connection is **no subscriptions**. The
server never pushes events to a client that has not called `subscribe`.

Rules:

- A `subscribe` call adds the named events to the client's active
  subscription set. Subscribing to an event that is already subscribed
  is a no-op.
- An `unsubscribe` call removes the named events from the active
  subscription set. Unsubscribing from an event that is not currently
  subscribed is a no-op.
- Unknown event names in either call are silently ignored. This enables
  forward compatibility: a client compiled against a future version of
  this document can ask for events that the current server does not
  know without breaking the call.
- When the client disconnects, its subscription state is discarded.
  There is no persistent subscription across reconnections.

The subscription state is per-client and per-connection. Because v1
supports only one concurrent client, there is only ever one active
subscription set.

---

## Backpressure

The server maintains a **bounded per-client event queue** with a
capacity of 256 items. Events are placed in this queue by the SPICE
event producers (channel handlers running on the tokio runtime) and
drained by the socket writer task.

If the client is reading slowly and the queue fills up, the server
drops the **oldest** queued events to make room for newer ones. Dropped
events are counted. When the queue next drains to empty, or after a
short bounded delay if the queue remains under pressure but the count
is non-zero, the server emits a single `dropped` event with the
cumulative drop count since the last `dropped` event.

The server **never blocks** the SPICE channel producers waiting for the
client to read. The consequence is explicit: a slow client gets dropped
events, not backpressure that degrades the SPICE session. This is the
correct trade-off for a test-harness control socket where the SPICE
session must remain responsive to the guest at all times.

Clients that want to receive all events reliably should drain the socket
promptly. If the client's consumer loop is slower than the event rate, it
should either:

- Unsubscribe from high-rate events (e.g. `latency`) that it does not
  need.
- Increase the rate at which it drains the socket (e.g. use a
  background thread or async task for reading).
- Accept that drops will occur and handle `dropped` events as a
  diagnostic signal rather than an error.

The `dropped` event itself is never itself dropped — if it cannot fit
in the queue, the existing `dropped` count is incremented in-place
rather than adding a second `dropped` entry.

---

## Error model

All error responses share the same structure:

```json
{"id": <id>, "ok": false, "error": {"code": "<stable_code>", "message": "<human_readable>"}}
```

The `code` field is a stable, machine-readable string. Do not parse
`message` programmatically; it may change between minor versions.

The following error codes are defined in v1.x (all were present in
v1.0; v1.1 added no new codes):

| Code | Semantics |
|------|-----------|
| `no_hello_yet` | A request other than `hello` was received before the hello handshake completed. The connection stays open. |
| `protocol_version_mismatch` | The major version in the `hello` params does not match the server's major version. The server closes the connection after writing this error. |
| `busy` | A second client attempted to connect while one is already connected. Written as the first and only line on the new connection, which is then closed. Note: no `id` field on this response. |
| `unknown_method` | The `method` field names a verb the server does not know. |
| `bad_params` | The `params` object is missing a required field, has a field of the wrong type, or is not a JSON object at all. |
| `bad_state` | The `state` field in a `send_key` request is not `"down"`, `"up"`, or `"press"`. |
| `agent_not_connected` | A `paste` was requested but the SPICE vdagent is not currently connected. |
| `no_such_surface` | A `screenshot` was requested for a `surface_id` that does not exist in the current session. |
| `unsupported_format` | A `screenshot` was requested with a `format` value other than `"png"` or `"rgba"`. |
| `not_implemented` | The method is recognised but not yet implemented. Should not appear in a complete implementation; retained in the code so partial builds have a stable code to return during incremental rollout of a new verb. |
| `internal_error` | An unexpected condition occurred server-side. The `message` field will contain details. Report these as bugs. |

Additional error codes may be added in future **minor** version bumps.
Clients should treat any unrecognised `code` value as a generic error
(print the `message` and fail the operation) rather than treating it as
`internal_error`. This ensures forward compatibility when a client
compiled against v1.0 connects to a v1.2 server that has added new
domain-specific codes.

---

## Versioning

The protocol version is a dotted `major.minor` string. The current
version is **1.2**.

Rules:

- A **major** version bump (e.g. 1.x → 2.0) indicates a breaking
  change: a message envelope shape has changed, a verb has been
  removed, or a field has been renamed or re-typed. Clients and servers
  with different major versions are not compatible. The server rejects a
  `hello` with a mismatched major version with `protocol_version_mismatch`
  and closes the connection.
- A **minor** version bump (e.g. 1.0 → 1.1) indicates an additive,
  backward-compatible change: a new verb was added, a new event was
  added, a new optional parameter was added to an existing verb, or a
  new error code was introduced. Clients and servers with the same major
  but different minor versions SHOULD interoperate. The server accepts
  any `hello` whose `protocol_version` has a matching major, regardless
  of the minor component.
- A minor bump is also used for a **correction to behaviour that was
  never usable as specified** — 1.2's extended-scancode fix is the
  only instance so far. It is not a major bump because nothing about
  the envelope, verb set or field types changes, and a client cannot
  have been depending on the broken behaviour while also reaching the
  guest correctly. It gets a version number rather than passing
  silently so that a client needing the fixed behaviour has something
  to test for.

Forward-compatibility obligations:

- **Clients** must gracefully ignore unknown event names they receive
  (they should never have arrived, but defensively discarding them is
  safer than panicking). Clients must also ignore unknown fields in
  response `result` objects; future minor versions may add fields.
- **Servers** must route unknown `method` values to the `unknown_method`
  error rather than panicking. Servers must also silently ignore unknown
  event names in `subscribe`/`unsubscribe` params.

Version 1.0 was the first published version. Version 1.1 added the
`surface_drawn` and `digest_updated` events. Version 1.2 (the
current version) extended the socket to `--web` mode and corrected
`send_key`'s extended-scancode encoding; see the version history at
the top of this document.

---

## End-to-end worked example

The following transcript shows a complete client session: connect,
hello, status, subscribe, a latency event arriving, a key press, a
paste with async completion, and then disconnect. Arrow direction
indicates the sender: `→` is client-to-server, `←` is server-to-client.

```
→ {"id": 1, "method": "hello", "params": {"client_name": "demo-client", "protocol_version": "1.0"}}
← {"id": 1, "ok": true, "result": {"server_name": "ryll", "protocol_version": "1.2", "supported_methods": ["hello", "status", "send_key", "paste", "screenshot", "subscribe", "unsubscribe"], "supported_events": ["latency", "agent_connected", "paste_completed", "paste_failed", "dropped", "surface_drawn"]}}
→ {"id": 2, "method": "status", "params": {}}
← {"id": 2, "ok": true, "result": {"spice_connected": true, "agent_connected": true, "surfaces": [{"channel_id": 1, "surface_id": 0, "width": 1024, "height": 768}]}}
→ {"id": 3, "method": "subscribe", "params": {"events": ["latency", "agent_connected", "paste_completed", "paste_failed"]}}
← {"id": 3, "ok": true, "result": {"subscribed": ["latency", "agent_connected", "paste_completed", "paste_failed"]}}
← {"event": "latency", "data": {"sample_ms": 1.2, "wallclock_us": 1717000000000100}}
→ {"id": 4, "method": "send_key", "params": {"scancode": 28, "state": "press"}}
← {"id": 4, "ok": true, "result": {}}
← {"event": "latency", "data": {"sample_ms": 1.1, "wallclock_us": 1717000002000200}}
→ {"id": 5, "method": "paste", "params": {"text": "hello", "char_delay_ms": 10}}
← {"id": 5, "ok": true, "result": {}}
← {"event": "latency", "data": {"sample_ms": 1.3, "wallclock_us": 1717000002100300}}
← {"event": "paste_completed", "data": {"request_id": 5, "chars_sent": 5}}
```

After the `paste_completed` event the client closes the connection
(TCP FIN). Ryll detects the EOF on the socket, cleans up the per-client
state (cancellation tokens, subscription set, queue), and resumes
listening for the next client.

Key observations from this transcript:

- The `hello` handshake always comes first and its response lists the
  full verb and event catalogue. Note the minor-version interop at
  work: the client requested `"1.0"` and the v1.1 server accepted it,
  responding with the version *it* speaks and advertising
  `surface_drawn`, which this v1.0 client simply never subscribes to.
- `status` gives a snapshot of the session state at that instant. The
  surfaces list shows one surface, which is all that most single-monitor
  guests present.
- After `subscribe`, events arrive interleaved with responses. The two
  `latency` events arrived between request 4 and request 5, and again
  between request 5's response and the `paste_completed` event. The
  client must be prepared to receive events at any time while subscribed.
- The `paste` response (`id: 5`) arrived before the
  `paste_completed` event. This is guaranteed: the response is written
  when the task is queued; the event is written when the task finishes.
  The `request_id` in `paste_completed` ties the outcome back to the
  original `paste` request.
- The connection can be torn down by either side simply closing the
  socket. No explicit disconnect verb is required.

---

## Implementation

Everything above this point is the contract. This section describes
how ryll implements it, and is not binding on other implementations.

The control socket is exposed by headless and web mode via the
`--control-socket <path>` CLI flag; the GUI rejects it at launch.
Both modes spawn the same server through
`shakenfist_spice_renderer::spawn_control_socket`, so the two cannot
drift apart.

Web mode's socket carries disproportionate weight: web mode is the one
mode with its own browser-side scancode table, so the scenario tests
that drive a session through this protocol and assert on the QR visual
digest are the only automated check on that table. The socket was
headless-only for a long time, and four input bugs shipped behind that
gap -- which is why both modes now spawn the same server rather than
growing separate implementations.

**Module layout.** The control surface lives entirely under
`shakenfist-spice-renderer/src/control/`:

| File | Role |
|------|------|
| `mod.rs` | Public re-exports: `Server`, `StatusProvider` |
| `protocol.rs` | Wire-level types: `Request`, `Response`, `Event`, verb params and result structs, serialisation helpers |
| `server.rs` | `Server::run` — the tokio task that binds the socket, accepts one client at a time, and dispatches verbs |

**Runtime shape.** `Server::run` binds a `tokio::net::UnixListener`
at the supplied path with file mode `0600` (owner read/write only).
File permissions are the sole access-control mechanism; no
authentication is performed on the wire. Exactly one client is
accepted at a time; a second connection attempt while a client is
active receives a `{"ok": false, "error": {"code": "busy", ...}}`
line and is immediately closed.

**Architectural integration.**

- `session.rs` fans the existing per-channel event mpsc into a
  `tokio::sync::broadcast::Sender<ChannelEvent>`. Subscribers on
  this broadcast bus include `HeadlessStats`, the headless-mode
  `SurfaceMirror`-apply task, and the control server's per-client
  event-translator task.
- The control server holds `Arc<tokio::sync::Mutex<SurfaceMirror>>`
  for two purposes: answering `screenshot` requests synchronously
  (locks, snapshots pixel buffer, encodes) and enumerating live
  surfaces in the `status` verb response.
- The control server is given an `Arc<dyn StatusProvider>` so
  `status` can query SPICE-connection state and agent presence
  without coupling `server.rs` to `session.rs` internals.
- Per-client outbound events use a two-task pipeline: an
  event-translator task (subscribes to the broadcast bus, filters by
  the client's subscription set, converts `ChannelEvent` to JSON)
  feeds a 256-slot mpsc into a writer task (drains the mpsc, writes
  newline-terminated lines to the socket). When the mpsc is full, the
  translator drops the **oldest** queued events and increments a
  drop counter; when the queue next drains, a single `dropped` event
  is emitted with the cumulative count. The SPICE session is never
  back-pressured by a slow control-socket client. This is the
  mechanism behind the [Backpressure](#backpressure) contract above.

**How the v1.1 events are produced.** Both sit on top of the v1.0
verb/event surface, implemented by extending `translate_event` (the
per-client broadcast filter) — no new `ChannelEvent` variants were
required on the SPICE side, and no new module on the control-server
side:

- `surface_drawn` fires once per display draw command
  (`ImageReady`, `ImageReadyChroma`, `ImageReadyAlpha`,
  `FillRect`, `CopyBits`, `Invert`).  Renderer-internal
  `produced_at_secs` flows through unchanged; the event carries
  a fresh `wallclock_us` captured at translation time so cross-
  process consumers (the kerbside loadtest orchestrator) can
  compute keypress-to-screen latency against wallclock-recorded
  keypress times.
- `digest_updated` lives behind the `digest-decode` Cargo
  feature.  Off in production builds.  When enabled,
  `crate::digest::run_digest_poller` ticks every 100 ms,
  snapshots the primary surface RGBA, runs
  `shakenfist-visual-digest::decode_qr_rgba` followed by
  `decode`, deduplicates by `frame_counter`, and broadcasts a
  `ChannelEvent::DigestUpdated`.  The translator converts that
  to a `digest_updated` wire event.  The hello / subscribe
  paths both gate on `protocol::supported_events()`, which only
  advertises `digest_updated` when the feature is on.

Both events are pushed to subscribers via the same broadcast
bus / per-client mpsc / drop-oldest backpressure path the v1.0
events use.

**Future work.** The following items are explicitly out of
scope for v1 and will be addressed by follow-up plans:

- Mouse-click and pointer-move verbs (requires Sextant pointer
  collector, which is marked deferred in its own `ARCHITECTURE.md`).
- USB-redirection and WebDAV verbs.
- Authentication or encryption on the socket beyond Unix file
  permissions.
- Multi-client concurrency (the v1 single-client model is
  intentional and adequate for the harness use-case).
- Out-of-band (non-JSON) screenshot transport for low-latency
  digest-assertion loops.
- Synchronous paste (the async model was chosen deliberately; a
  synchronous convenience wrapper belongs in the client, not the
  server).
- Rate-limit knobs for `digest_updated` (currently a fixed
  100 ms tick; add a `--digest-min-interval-ms` if the Sextant
  scenarios produce too many events for slow consumers).
- Cross-platform CI matrix for the new feature combinations;
  only Linux is verified today.

The design rationale for the control socket and the v1.1
additions lives in the kerbside test-harness master plan,
`shakenfist/kerbside/docs/plans/PLAN-test-harness.md`.
