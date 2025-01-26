# Design of the in guest agent

Shaken Fist has an in guest agent, called SF Agent, which is capable of a
variety of operations inside an instance regardless of if user accounts or
functioning networking exist on the instance. This is useful in a variety of
scenarios, but is heavily used in the K3S orchestrator and Shaken Fist's
functional testing.

Shaken Fist communicates with this agent via a qemu virtio-serial channel,
which is effectively a bidirectional virtual serial channel. That is, there is
a single stream of bytes in each direction, and multiplexing of the various
operations must be handled by Shaken Fist itself. This is distinct from the
`privexec` daemon that Shaken Fist uses to execute commands on hypervisors
which uses a Unix Domain Socket, and the Unix Domain Socket handles issues of
multiplexing.

Effort is also put into making the code for the in guest agent itself as small
and flexible as possible. It needs to exist in every guest OS we support, so
it needs to be light on resource requirements and forgiving of the various
different python dependency versions we might see. The Shaken Fist client has
similar constraints.

The following simplifying assumptions are therefore made:

* only one command is sent to the agent to execute at a time. Commands are
  queued by Shaken Fist, and executed in the order they were requested. However,
  a single API request for an AgentOperation can contain more than one command
  to execute in a batch.
