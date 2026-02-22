# Shaken Fist In-Guest Agent

The Shaken Fist in-guest agent (`sf-agent`) is a Python daemon
that runs inside virtual machines managed by
[Shaken Fist](https://shakenfist.com). It provides a side channel
for the hypervisor to interact with the guest operating system
without requiring network connectivity.

## Features

- **Command execution**: Run arbitrary commands inside the guest
  with configurable I/O priority, environment variables, working
  directory, and network namespace support.
- **File transfer**: Upload and download files between the
  hypervisor and the guest using a chunked, base64-encoded
  protocol.
- **File permissions**: Set file permissions using symbolic mode
  notation (e.g. `ugo+rw`).
- **System facts**: Gather OS distribution information, mounted
  filesystems, and SSH host keys.
- **Health checks**: Query systemd status and agent liveness.

## How It Works

The agent listens on
[vsock](https://man7.org/linux/man-pages/man7/vsock.7.html)
port 1025 for connections from the hypervisor. All communication
uses [Protocol Buffers](https://protobuf.dev/) for serialization.
Each connection is handled in its own thread, allowing multiple
concurrent operations.

The hypervisor side of this protocol is implemented in the main
[Shaken Fist](https://github.com/shakenfist/shakenfist)
repository.

## Installation

The agent is installed automatically by Shaken Fist when
preparing guest images. For manual installation:

```bash
pip install shakenfist-agent
```

The agent is typically started as a systemd service:

```bash
sf-agent daemon run
```

Use `--verbose` for debug logging:

```bash
sf-agent --verbose daemon run
```

## Further Reading

- [Protocol Reference](/components/agent-python/protocol/) -- details of the protobuf
  message format and command semantics.
- [Developer Guide](/components/agent-python/developer-guide/) -- how to build, test,
  and extend the agent.
