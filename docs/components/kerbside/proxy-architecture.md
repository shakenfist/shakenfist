# Kerbside Proxy Architecture

This document provides a detailed technical description of Kerbside's proxy
architecture, including the connection state machine, process model, and traffic
handling.

> **Note:** this describes the Python proxy (`kerbside/proxy.py`), which is the
> active proxy today. A Rust reimplementation (`rust/kerbside-proxy/`) is in
> progress and reproduces this behaviour over async tokio tasks, consulting the
> control-plane gRPC service for authorization; see `ARCHITECTURE.md` and
> `docs/plans/PLAN-rust-proxy.md`.

## Process Architecture

Kerbside uses a multiprocess architecture for scalability and isolation:

```
+---------------------------+
|    kerbside-daemon        |  Main daemon process
|    (main.py)              |  - Spawns proxy subprocess
|                           |  - Runs maintenance loop
+-------------+-------------+
              |
              | Subprocess
              v
+---------------------------+
|    kerbside-proxy         |  Proxy manager process
|    (proxy.py:run)         |  - Accepts connections
|                           |  - Spawns workers
+-------------+-------------+
              |
              | multiprocessing.Process
              v
+---------------------------+
|    kerbside-secure-*      |  Worker processes (one per connection)
|    (SpiceTLSSession)      |  - Handles single SPICE channel
|                           |  - Bidirectional proxy
+---------------------------+
```

### Benefits of Multiprocess Architecture

1. **Isolation**: Each connection runs in its own process, preventing one
   misbehaving connection from affecting others.

2. **Resource Management**: Workers can be killed individually if they become
   unresponsive or consume excessive resources.

3. **Simplicity**: No complex threading or async I/O required; each worker uses
   simple blocking I/O with `select()`.

4. **Observability**: Process names reflect connection state, making monitoring
   easier (e.g., `kerbside-secure-abc123-display-0`).

## Connection Listener

The `SpiceListener` class manages incoming connections:

```python
class SpiceListener:
    def __init__(self, address, port, tls_port):
        # Insecure port (5901) - redirects to TLS
        self.unsecured = socket.socket(...)
        self.unsecured.bind((address, port))

        # Secure port (5900) - TLS-wrapped connections
        self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ssl_context.load_cert_chain(cert_path, key_path)
        self.secured = socket.socket(...)
        self.secured.bind((address, tls_port))
```

### TLS Configuration

- Server certificate and key loaded from configured paths
- CA certificate loaded for client verification (optional)
- Default verify paths configured for system CAs

## Connection State Machine

### Insecure Connection Flow (SpiceSession)

Connections to the insecure port (5901) follow a simple flow:

```
     Accept Connection
            |
            v
    +---------------+
    | Receive Data  |
    +-------+-------+
            |
            v
    +---------------+
    | Parse Client  |
    | SpiceLinkMess |
    +-------+-------+
            |
            v
    +---------------+
    | Send Reply    |
    | need_secured  |
    +-------+-------+
            |
            v
    +---------------+
    | Close         |
    +---------------+
```

This redirects clients to use TLS on port 5900.

### Secure Connection Flow (SpiceTLSSession)

TLS connections follow a more complex state machine:

```
     Accept TLS Connection
            |
            v
    +-------------------+
    | ClientSpiceLinkMess|  Parse client hello, send server hello
    +--------+----------+
             |
             v
    +-------------------+
    | ClientPassword    |  Decrypt token, validate, connect to hypervisor
    +--------+----------+
             |
             v
    +-------------------+
    | ClientProxy       |  Bidirectional traffic relay
    | ServerProxy       |
    +-------------------+
```

### State Handlers

Each state is implemented as a method that returns the number of bytes consumed:

```python
class SpiceTLSSession:
    def __init__(self, ...):
        self.client_next_packet = self.ClientSpiceLinkMess  # Initial state
        self.server_next_packet = None

    def ClientSpiceLinkMess(self, buffered):
        # Parse client hello
        # Generate RSA keypair
        # Send server hello with public key
        self.client_next_packet = self.ClientPassword
        return bytes_consumed

    def ClientPassword(self, buffered):
        # Decrypt token
        # Validate against database
        # Connect to hypervisor
        self.client_next_packet = self.ClientProxy
        self.server_next_packet = self.ServerProxy
        return bytes_consumed

    def ClientProxy(self, buffered):
        # Parse and forward client traffic to server
        return bytes_consumed

    def ServerProxy(self, buffered):
        # Parse and forward server traffic to client
        return bytes_consumed
```

## Main Processing Loop

The main processing loop uses non-blocking I/O with `select()`:

```python
def run(self):
    client_buffered = bytearray()
    server_buffered = bytearray()

    while True:
        sockets = [self.client_conn]
        if self.server_conn:
            sockets.append(self.server_conn)

        # Wait for data (0.2 second timeout)
        readable, _, errors = select.select(sockets, [], sockets, 0.2)

        # Read available data
        for r in readable:
            if r == self.client_conn:
                client_buffered += self.client_conn.recv(1024000)
            elif r == self.server_conn:
                server_buffered += self.server_conn.recv(1024000)

        # Process buffered data
        if client_buffered:
            consumed = self.client_next_packet(client_buffered)
            while consumed > 0:
                client_buffered = client_buffered[consumed:]
                consumed = self.client_next_packet(client_buffered)

        if self.server_next_packet and server_buffered:
            consumed = self.server_next_packet(server_buffered)
            while consumed > 0:
                server_buffered = server_buffered[consumed:]
                consumed = self.server_next_packet(server_buffered)
```

### Why 0.2 Second Timeout?

The short timeout ensures responsive handling even when:
- State transitions occur that enable new packet processing
- One socket becomes readable while processing the other
- Clean shutdown needs to occur

## Packet Parsing Pattern

All packet parsers follow a consistent pattern:

```python
def parse_packet(buffered):
    # Check minimum header size
    if len(buffered) < HEADER_SIZE:
        return 0  # Need more data

    # Parse header
    message_type, message_size = struct.unpack_from('<HI', buffered)

    # Check if complete message is available
    if len(buffered) < HEADER_SIZE + message_size:
        return 0  # Need more data

    # Process message
    # ...

    # Return bytes consumed
    return HEADER_SIZE + message_size
```

This pattern enables:
- Partial packet handling (wait for more data)
- Multiple packets in one read (process in loop)
- Clean separation of concerns

## Traffic Inspection

Kerbside can optionally inspect and log all traffic:

### Inspector Architecture

```python
class InspectableTraffic:
    def configure_inspection(self, source, uuid, session_id, channel):
        if config.TRAFFIC_INSPECTION:
            self.logfile = open(...)

    def emit_entry(self, entry):
        if config.TRAFFIC_INSPECTION:
            self.logfile.write(f'{timestamp} {entry}\n')
```

### Channel-Specific Inspectors

Each channel type has dedicated inspectors:

| Channel | Client Inspector | Server Inspector |
|---------|------------------|------------------|
| main    | ClientMainPacket | ServerMainPacket |
| display | ClientDisplayPacket | ServerDisplayPacket |
| inputs  | ClientInputsPacket | ServerInputsPacket |
| cursor  | ClientCursorPacket | ServerCursorPacket |
| port    | ClientPortPacket | ServerPortPacket |

### Intimate Logging

With `TRAFFIC_INSPECTION_INTIMATE` enabled, detailed data is logged:
- Keystrokes and scancodes
- Mouse coordinates and button states
- Image frame data

This creates audit trails but should be used carefully due to privacy
implications.

## ACK Handling for Inserted Packets

When traffic inspection modifies packets (e.g., adding border frames),
the proxy must handle acknowledgements correctly:

```python
def ClientProxy(self, buffered):
    pt = self.client_parser(buffered)

    if pt.inserted_packets > 0:
        # We inserted packets that server will ACK
        self.server_ignore_acks += pt.inserted_packets

    if pt.packet_is_ack and self.client_ignore_acks > 0:
        # This ACK is for a packet we inserted, don't forward
        self.client_ignore_acks -= 1
    else:
        self.server_conn.sendall(pt.data_to_send)
```

## Worker Management

The proxy manager monitors and manages worker processes:

### Worker Tracking

```python
workers = []

for conn, client_host, client_port, secured in listen.accept():
    session = SpiceTLSSession(conn, client_host, client_port)
    p = multiprocessing.Process(target=session.run, ...)
    p.start()
    workers.append(p)
```

### Worker Cleanup

Every second, the proxy manager:

1. **Identifies stray processes**: Workers older than 5 seconds without
   database channel records
2. **Terminates strays**: Sends SIGKILL to unregistered workers
3. **Reaps terminated workers**: Joins completed processes and removes
   database records

```python
# Find strays
for child in psutil.Process(os.getpid()).children():
    if child.pid not in channel_pids:
        if time.time() - child.create_time() > 5:
            os.kill(child.pid, signal.SIGKILL)

# Reap terminated
for p in workers:
    if not p.is_alive():
        p.join(1)
        db.remove_proxy_channel(config.NODE_NAME, p.pid)
```

## Database State

Worker processes register their state in the database:

### Channel Info Recording

```python
db.record_channel_info(
    node_name,
    pid,
    client_ip=...,
    client_port=...,
    connection_id=...,
    channel_type=...,
    channel_id=...,
    session_id=...
)
```

This enables:
- Cross-process monitoring
- Admin UI session display
- Cleanup of orphaned records

## Prometheus Metrics

Workers report metrics via a shared queue:

```python
# In worker
self.prometheus_updates.put(('bytes_proxied', labels, byte_count))

# In manager
while True:
    name, labels, value = prometheus_updates.get(block=False)
    if name == 'bytes_proxied':
        bytes_proxied.labels(**labels).inc(value)
```

### Exported Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| workers | Gauge | - | Number of active workers |
| bytes_proxied | Counter | type, session_id | Bytes transferred |
| proxy_time | Counter | type, session_id | Processing time |

## Error Handling

### Connection Errors

| Error | Handling |
|-------|----------|
| BadMagic/BadMajor/BadMinor | Terminate connection |
| HandshakeFailed | Terminate connection |
| ConnectionRefused | Log, terminate |
| BrokenPipeError | Log, cleanup sockets |
| ConnectionResetError | Log, cleanup sockets |

### SSL Errors

SSL read errors are handled gracefully:

```python
try:
    d = self.client_conn.recv(1024000)
except ssl.SSLWantReadError:
    # SSL layer has no data, continue loop
    pass
```

## Security Considerations

### Token Validation

All connections must present valid tokens:
- Tokens are time-limited (configurable expiry)
- Tokens are single-use (prevent replay attacks)
- Invalid tokens result in immediate disconnection

### Audit Logging

All significant events are logged:
- Channel creation
- Hypervisor connection success/failure
- Token validation results
- Traffic inspection events (if enabled)

### Process Isolation

Each connection runs in its own process:
- Memory isolation between connections
- Independent crash handling
- Resource limits can be applied per-process

## Related Documentation

- [Protocol Overview](/components/kerbside/protocol-overview/) - SPICE protocol introduction
- [Link Protocol](/components/kerbside/spice-link-protocol/) - Connection handshake details
- [Channel Protocols](/components/kerbside/channel-protocols/) - Per-channel message formats
