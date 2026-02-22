# Protocol Reference

The agent communicates with the hypervisor over a vsock connection
using Protocol Buffers. This document describes the message
format and command semantics.

## Transport

The agent listens on vsock port 1025. The hypervisor connects
and sends serialized `HypervisorToAgent` messages. The agent
responds with serialized `AgentToHypervisor` messages. Messages
are sent directly on the stream without framing -- the protobuf
parser consumes exactly the bytes it needs.

## Message Envelope

All messages are wrapped in an envelope containing a list of
commands:

```protobuf
message HypervisorToAgent {
    repeated HypervisorToAgentCommand commands = 1;
}

message AgentToHypervisor {
    repeated AgentToHypervisorCommand commands = 1;
}
```

Each command carries a `command_id` string that correlates
requests with responses.

## Commands

### Welcome / Departure

The hypervisor sends `HypervisorWelcome` with its version string
when a connection is established. The agent responds with
`AgentWelcome` containing its version and the system boot time.

When the hypervisor is finished, it sends `HypervisorDeparture`.
The agent does not reply.

### Ping

A simple liveness check. The hypervisor sends `PingRequest` and
the agent responds with `PingReply`.

### Is System Running

Queries `systemctl is-system-running` and returns the result.
The reply includes a boolean `result` (true if the output is
`running`), the raw `message` string, and the system `boot_time`.

### Gather Facts

Collects information about the guest:

- **Distribution facts**: OS name, version, codename, etc.
  (from the `distro` library).
- **Mount points**: Device, mount point, and filesystem type
  for each mounted filesystem (from `/proc/mounts`).
- **SSH host keys**: Contents of RSA, ECDSA, and Ed25519 public
  key files from `/etc/ssh/`.

### Execute

Runs a shell command inside the guest. The request supports:

- `command`: The command string to execute.
- `environment_variables`: Key-value pairs to set in the
  environment.
- `network_namespace`: If set, the command is run inside the
  specified network namespace via `ip netns exec`.
- `io_priority`: `NORMAL`, `LOW`, or `HIGH`. Controls the
  `ionice` class and priority value.
- `working_directory`: The working directory for the command.

The reply contains `stdout`, `stderr`, `exit_code`, and
`execution_seconds`.

### Put File

Uploads a file to the guest. The `PutFileRequest` includes the
destination `path`, file `mode` (numeric permissions), total
`length`, and the `first_chunk` of data.

Subsequent chunks arrive as `FileChunk` messages. Each chunk
contains an `offset`, `encoding` (always BASE64), and `payload`.
An empty payload signals end-of-file, at which point the file
is closed and permissions are applied.

The agent responds with a `FileChunkReply` for each chunk
received.

### Get File

Downloads a file from the guest. The agent first responds with
a `StatResult` containing the file's mode, size, uid, gid, and
timestamps. It then sends a series of `FileChunk` messages
(up to 100KB each, base64-encoded). An empty payload signals
end-of-file.

If the file does not exist, the agent responds with a
`CommandError`.

### Chmod

Changes file permissions. The request contains a `path` and
numeric `mode`. The agent applies the permissions using
`symbolicmode.chmod()` and responds with `ChmodReply`.

## Error Handling

If a command handler raises an exception, the agent catches it
and responds with a `CommandError` containing the error message
and the original envelope for debugging. The connection remains
open for further commands.

An `UnknownCommand` response is sent if the agent receives a
command it does not recognise.
