# Agent protocol v2

v2 of the Shaken Fist agent protocol is serialized protobufs sent down
virtio-vsock channels to the in-guest agent. This page documents the expected
flow of messages between the hypervisor and this in-guest agent.

## Initial connection to the agent

When the hypervisor connects to the agent for the first time, it will send a
`HypervisorWelcome` message. This message contains the version of the
hypervisor, and the agent replies with a `AgentWelcome` message containing the
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