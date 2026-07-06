# SPICE Link Protocol

This document describes the SPICE link protocol used to establish connections
and authenticate between clients and servers. In Kerbside, this protocol is
implemented in `spiceprotocol/packets/linkmessages.py` and
`spiceprotocol/packets/authentication.py`.

There is now a second, independent implementation of this wire format: the
server/proxy role in the Rust `shakenfist-spice-protocol` crate (part of
[ryll](https://github.com/shakenfist/ryll)), which the Rust rewrite of the
Kerbside proxy consumes. The byte-level details below are therefore a
cross-implementation contract, not merely a description of the Python proxy.
Where the two implementations differ in robustness (see
[Parsing robustness and resource limits](#parsing-robustness-and-resource-limits)),
that is called out explicitly.

## Connection Handshake Overview

The SPICE connection handshake follows this sequence:

```
    Client                                          Server
      |                                               |
      |  1. SpiceLinkMess (client hello)              |
      |---------------------------------------------->|
      |                                               |
      |  2. SpiceLinkReply (server hello + pubkey)    |
      |<----------------------------------------------|
      |                                               |
      |  3. ClientAuthPacket (encrypted password)     |
      |---------------------------------------------->|
      |                                               |
      |  4. ServerAuthPacket (result code)            |
      |<----------------------------------------------|
      |                                               |
      |  5. Bidirectional channel communication       |
      |<--------------------------------------------->|
```

## SpiceLinkMess (Client Hello)

The client initiates the connection by sending a `SpiceLinkMess` packet:

### Binary Format

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     bytes   magic (REDQ = 0x51444552)
4       4     uint32  major_version
8       4     uint32  minor_version
12      4     uint32  size (bytes following this field)
16      4     uint32  connection_id
20      1     uint8   channel_type
21      1     uint8   channel_id
22      4     uint32  num_common_caps
26      4     uint32  num_channel_caps
30      4     uint32  caps_offset
34+     var   uint32  capabilities (variable length)
```

### Field Details

**magic** (4 bytes)
: Protocol magic number. Must be `REDQ` (0x51444552 little-endian).

**major_version** (4 bytes)
: Protocol major version. Must be 2.

**minor_version** (4 bytes)
: Protocol minor version. Must be 2.

**size** (4 bytes)
: Number of bytes following this field to the end of the message.

**connection_id** (4 bytes)
: For main channel connections (channel_type=1), this is set to 0. The server
  will allocate a new session ID. For other channels, this contains the session
  ID returned by the main channel connection.

**channel_type** (1 byte)
: The type of channel being requested:
  - 1 = main
  - 2 = display
  - 3 = inputs
  - 4 = cursor
  - 5 = playback
  - 6 = record
  - 8 = usbredir
  - 9 = port
  - 10 = webdav

**channel_id** (1 byte)
: Channel instance ID. Used when multiple channels of the same type exist.

**num_common_caps** (4 bytes)
: Number of 32-bit words of common capabilities.

**num_channel_caps** (4 bytes)
: Number of 32-bit words of channel-specific capabilities.

**caps_offset** (4 bytes)
: Byte offset from the `connection_id` field to the start of the capabilities
  vector.

**capabilities** (variable)
: Capability bitmaps. Common capabilities followed by channel capabilities.

## SpiceLinkReply (Server Hello)

The server responds with a `SpiceLinkReply` packet:

### Binary Format

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     bytes   magic (REDQ = 0x51444552)
4       4     uint32  major_version
8       4     uint32  minor_version
12      4     uint32  size (bytes following this field)
16      4     uint32  error
20      162   bytes   pubkey (RSA public key in DER format)
182     4     uint32  num_common_caps
186     4     uint32  num_channel_caps
190     4     uint32  caps_offset
194+    var   uint32  capabilities (variable length)
```

### Field Details

**error** (4 bytes)
: Error code. 0 indicates success. See
  [Protocol Overview - Error Codes](/components/kerbside/protocol-overview/#error-codes) for the
  complete list.

**pubkey** (162 bytes)
: RSA public key in DER (SubjectPublicKeyInfo) format. Used by the client to
  encrypt the password. Kerbside generates a unique 1024-bit RSA key pair for
  each connection. The field is always exactly 162 bytes because that is the
  DER SubjectPublicKeyInfo length of a 1024-bit RSA key with the standard
  65537 exponent; this is why the field is fixed-size on the wire. On error
  replies (see below) the field is present but zero-filled.

**num_common_caps / num_channel_caps** (4 bytes each)
: Number of 32-bit words of common and channel capabilities the server
  advertises. On success Kerbside sends one word of each (see
  [Capability Handling](#capability-handling)).

**caps_offset** (4 bytes)
: Byte offset to the start of the capability words, measured from the `error`
  field (the first byte of the message body, at file offset 16 — the same
  reference point `SpiceLinkMess` uses for its `connection_id` field). On a
  success reply this is `178` (= 4 error + 162 pubkey + 4 + 4 + 4), i.e. the
  words immediately follow this field.

### Error replies

A reply carrying a non-zero `error` (for example the `need_secured` redirect,
error 5) has a fixed degenerate shape rather than a truncated one:

- `pubkey` is present but zero-filled (162 zero bytes),
- `num_common_caps` and `num_channel_caps` are both 0,
- `caps_offset` is `0` (not 178), and
- `size` is therefore 178 with no capability words following.

An implementation that always assumes the success layout (non-zero
`caps_offset`, a real key) will misparse these replies.

## Client Authentication Packet

After receiving the server hello, the client sends an authentication packet:

### Binary Format

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  mechanism
4       128   bytes   encrypted_password
```

### Field Details

**mechanism** (4 bytes)
: Authentication mechanism. Must be 1 for SPICE native authentication
  (AuthSpice).

**encrypted_password** (128 bytes)
: Password encrypted with RSA-OAEP using SHA-1 for both the mask generation
  function (MGF1) and the hash algorithm. The plaintext password must be
  null-terminated before encryption. On the server side, after decrypting,
  a single trailing NUL is stripped to recover the password. (The Python
  proxy strips the final byte unconditionally; the Rust implementation strips
  it only if present, which is equivalent for any spec-conformant client since
  the terminator is always appended.)

### Encryption Details

The password encryption uses:
- Algorithm: RSA-OAEP
- Mask Generation Function: MGF1 with SHA-1
- Hash Algorithm: SHA-1
- Key Size: 1024 bits (produces 128-byte ciphertext)

Python example (using cryptography library):

```python
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

encrypted_password = public_key.encrypt(
    password.encode() + b'\x00',  # Null-terminate
    padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA1()),
        algorithm=hashes.SHA1(),
        label=None
    )
)
```

## Server Authentication Response

The server responds with a simple result code:

### Binary Format

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  error
```

### Field Details

**error** (4 bytes)
: Error code. 0 indicates successful authentication. Non-zero indicates failure
  (typically `permission_denied` = 7).

## Kerbside Token Authentication

Kerbside extends the standard SPICE authentication by using time-limited tokens
instead of static VM passwords:

1. **Token Generation**: The API generates a 48-character alphanumeric token
   with an associated 12-character session ID.

2. **Token Embedding**: The token is embedded in the virt-viewer configuration
   file as the "password" field.

3. **Token Validation**: When Kerbside receives the encrypted password, it
   decrypts it and validates it against the token database, checking:
   - Token existence
   - Token expiry time (the lookup filters on `expires > now`)

   Note: a token is **not** invalidated on first use. It remains usable until
   it expires; expired tokens are reaped only once they also have no open
   proxy channels. The default token lifetime is short (one minute), which
   bounds the reuse window, but a token is not strictly single-use despite
   older descriptions to that effect.

4. **Console Lookup**: Valid tokens are mapped to specific VM consoles,
   allowing Kerbside to establish the correct backend connection.

## TLS/SSL Requirements

Kerbside enforces TLS for all client connections:

- **Secure Port (5900)**: TLS-wrapped connections are accepted and processed.
- **Insecure Port (5901)**: Connections receive a `need_secured` error response
  redirecting them to the TLS port.

## Connection State Machine

Kerbside's proxy implements the following state machine for TLS connections:

```
+-------------------+
|  ClientSpiceLinkMess  |  <-- Initial state
+---------+---------+
          |
          | Parse client hello, generate RSA keypair,
          | send server hello with public key
          v
+-------------------+
|   ClientPassword   |
+---------+---------+
          |
          | Decrypt token, validate, lookup console,
          | connect to hypervisor, send auth result
          v
+-------------------+
|    ClientProxy     |  <-- Bidirectional relay mode
|    ServerProxy     |
+-------------------+
```

## Error Handling

### Protocol Errors

| Error | Handling |
|-------|----------|
| BadMagic | Connection terminated immediately |
| BadMajor | Connection terminated immediately |
| BadMinor | Connection terminated immediately |
| HandshakeFailed | Connection terminated |

### Authentication Errors

| Condition | Response |
|-----------|----------|
| Invalid token | Connection declined, logged |
| Expired token | Connection declined, logged |
| Unknown source | Connection declined, logged |
| Unknown console | Connection declined, logged |
| Hypervisor unreachable | Connection refused, logged |

## Implementation Notes

### RSA Key Generation

Kerbside generates a fresh RSA key pair for each connection to ensure forward
secrecy:

```python
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=1024
)

public_key_der = private_key.public_key().public_bytes(
    Encoding.DER,
    PublicFormat.SubjectPublicKeyInfo
)
```

### Capability Handling

Kerbside uses hardcoded default capabilities based on observed behavior:
- Common caps: 11 (AuthSelection, AuthSpice, MiniHeader)
- Channel caps: 9 (SemiSeamlessMigrate, SeamlessMigrate)

These values are sent in the server hello before the actual hypervisor
capabilities are known.

The wire format permits more than one 32-bit word in each capability vector
(`num_common_caps` / `num_channel_caps` may exceed 1). The Python proxy
decodes only the first word of each and warns that it ignores the rest; the
Rust implementation parses and retains every advertised word. New code should
not assume a single word.

## Parsing robustness and resource limits

The link messages are the first bytes a proxy reads from an unauthenticated,
potentially hostile client, so their length, count, and offset fields must be
treated as adversarial. In particular, `num_common_caps` and `num_channel_caps`
are attacker-controlled 32-bit counts: a parser that allocates storage sized
directly from them (for example a capacity hint derived from the count) can be
driven to an out-of-memory abort by a client that declares billions of
capability words without sending them. This is a real defect class, not
hypothetical — it was found by fuzzing the Rust `SpiceLinkReply` parser and
fixed there.

A robust implementation must, before allocating or indexing:

- **Cap the declared `size`.** Reject a link header whose `size` exceeds a
  sane ceiling far above any legitimate link message. The Rust implementation
  uses 4096 bytes.
- **Cap the capability-word counts.** Reject `num_common_caps` or
  `num_channel_caps` beyond a small limit (the Rust implementation uses 16
  words each) *before* allocating, and never size an allocation from the raw
  count.
- **Bounds-check `caps_offset`.** Validate that the capability region lies
  within the declared body before reading it, treating an out-of-range or
  overflowing offset as a protocol error rather than indexing past the buffer.
- **Treat truncation as an error, not a panic.** A message shorter than its
  declared fields imply must fail cleanly.

The Rust implementation centralises these checks in a bounded reader used by
every link parser, and ships fuzz targets that exercise the link-message,
link-reply, and ticket-decryption paths against arbitrary input
(see the ryll repository). The Python proxy predates this hardening; it is
partially protected by Python's own bounds checking (a short buffer raises
rather than over-reads) but does not enforce explicit size or count caps, so
it should not be treated as the reference for robustness.

## Related Documentation

- [Protocol Overview](/components/kerbside/protocol-overview/) - High-level protocol introduction
- [Channel Protocols](/components/kerbside/channel-protocols/) - Per-channel message formats
