# Agent protocol v2

v2 of the Shaken Fist agent protocol is serialized protobufs sent down
virtio-vsock channels to the in-guest agent. This page documents the expected
flow of messages between the hypervisor and this in-guest agent.

## Initial connection to the agent

The sidechannel daemon process on the hypervisor wants to maintain an open
connection to each instance with an in-guest agent for the lifetime of that
instance. This is implemented in
`shakenfist.daemons.sidechannel.main.SideChannelMonitorJob`. No AgentOperations
use this initial connection, it is entirely occupied with health checking the
instance.

When the hypervisor connects to the agent for the first time, it will send a
`HypervisorWelcome` message. This message contains the version of the
hypervisor, and the agent replies with an `AgentWelcome` message containing the
version of the agent and the boot time of the instance in return.

This initial connection is then held open for the life of the hypervisor's
sidechannel process, with `PingRequest` messages being sent to periodically.
These are responded to by `PingReply` messages, and indicate that the
connection is still alive.

The hypervisor also initially wants to determine the state of the instance. It
does this by issuing `IsSystemRunningRequest` messages until the agent
indicates via a `IsSystemRunningReply` that instance startup has hit a stable
state. When the hypervisor receives a response to a `IsSystemRunningRequest`
that indicates a change of state for the instance, it will attempt to gather
facts about the instance by sending a `GatherFactsRequest` which should receive
a `GatherFactsResponse`.

When then sidechannel daemon process is instructed to terminate by systemd, it
will silently terminate the connection to the in-guest agent and sends no
further packets.

## AgentOperation execution

AgentOperations are executed with a separate connection to the in-guest agent.
For any instance which has a healthy initial connection and no AgentOperations
already executing, the sidechannel daemon checks if there are any queued
AgentOperations for the instance. If there are, the first one is dequeued and
send to the in-guest agent for processing.  This is implemented in
`shakenfist.daemons.sidechannel.main.SideChannelExecutorJob`. Given that Shaken
Fist guarantees that only one AgentOperation will be executing on an instance
at a given time (AgentOperations for a single instance are a strict linear FIFO
queue), there will only ever be one `SideChannelExecutorJob` thread for a given
instance at any one time.

Similar to the initial connection, the sidechannel daemon sends a
`HypervisorWelcome` message on connection. The agent replies with an
`AgentWelcome` message as before. If the channel between the sidechannel daemon
and the in-guest agent has been idle for more than two seconds, then a
`PingRequest` is send and a `PingReply` received. This can happen if a
particular AgentOperation takes a while to execute, for example a slow command.

### Command execution

Commands are executed using `ExecuteRequest`, which receives a single
`ExecuteReply`. For large stdout and stderr responses, they are converted to
blobs before a result is recorded in the database. Smaller responses are stored
inline in the database.

### Upload a file to an instance

File uploads into an instance are implemented with the `PutBlob` message. This
message includes the destination filename, optionally the required file mode,
and so forth. It also includes the first `FileChunk` message. A series of other
`FileChunk` messages are then send to the in-guest agent, until the entire file
has been sent. A final `FileChunk` with no data is then sent to indicate the end
of the transfer.

The sidechannel daemon will send a batch of `FileChunk` messages initial, and
then send single messages as outstanding messages are acknowledged by the
in-guest agent. At the time of writing, not more than five `FileChunk` messages
should be in flight at any one time.

### chmod

Changes of file mode which are not embedded in a file upload are implemented
using the `ChmodRequest` message, which receives a `ChmodReply` message.

## Handling agent errors

For either type of connection between Shaken Fist and the in-guest agent, if
the agent experiences an error while processing a command message it will
return a `CommandError` message.