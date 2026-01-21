# SPICE Link Protocol

This document describes the SPICE link protocol used to establish connections
and authenticate between clients and servers. In Kerbside, this protocol is
implemented in `spiceprotocol/packets/linkmessages.py` and
`spiceprotocol/packets/authentication.py`.

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
  [Protocol Overview - Error Codes](/components/kerbside/protocol-overview.md#error-codes) for the
  complete list.

**pubkey** (162 bytes)
: RSA public key in DER (SubjectPublicKeyInfo) format. Used by the client to
  encrypt the password. Kerbside generates a unique 1024-bit RSA key pair for
  each connection.

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
  null-terminated before encryption.

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
   - Token expiry time
   - One-time use (token is invalidated after use)

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

## Related Documentation

- [Protocol Overview](/components/kerbside/protocol-overview.md) - High-level protocol introduction
- [Channel Protocols](/components/kerbside/channel-protocols.md) - Per-channel message formats
