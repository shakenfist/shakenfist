# Shaken Fist's State Machine

Shaken Fist implements a state machine for each object type. This page documents
the possible states for each object, and which transitions between states are
valid.

Shaken Fist rigidly enforces the state model defined for each object, and will
raise an exception if an unexpected transition occurs. The state model for a
given object is defined in the `state_targets` map in the object class for those
keen on reading code.

Objects marked as `deleted` are removed from MariaDB after sitting deleted for
some period of time. This is called a "hard deletion" and the period of time is
configured with the CLEANER_DELAY configuration option. Once an object is hard
deleted it will no longer appear in any API request, as it no longer exists in
the database. The exception is that it will still appear in relevant events that
have not yet aged out.

## Agent Operations

* `initial`: the first state for an agent operation. A UUID has been allocated,
  and a placeholder database entry created.
* `preflight`: some agent operations require additional queued steps such as
  fetching `blob`s to the correct `node`. These operations will be in `preflight`
  during this background work.
* `queued`: awaiting execution on the `instance`.
* `executing`: means the Agent Operation is now executing on the `instance`.
* `complete`: the Agent Operation has finished executing on the `instance`.
  Specifically, this means we have received a result from the agent for each
  command. If the agent crashes or a command never returns, this means the Agent
  Operation will never be marked as complete.
* `deleted`: the Agent Operation has been deleted.
* `error`: an error occurred while processing the Agent Operation.
* `expired`: a timing budget the caller set was exhausted -- either the
  wall-clock deadline (`deadline_seconds`), or the progress timeout
  (`progress_timeout_seconds`) while a command which reports progress was
  in flight. This is deliberately distinct from `error`, which means the
  Agent Operation itself failed. The reason is recorded as the state's
  message and as an audit event on the operation. Note that the two
  states are terminal in the same way, but only `expired` is currently
  swept for hard deletion.

The `executing --> queued` edge is a retry of a stalled attempt, not a
failure: it fires when the executor made no progress rather than when it
reported an error, is bounded both by `AGENT_OPERATION_MAX_ATTEMPTS` and by
the operation's own deadline, and is never taken by `execute` operations.

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> initial
  [*] --> error

  initial --> preflight
  initial --> queued
  initial --> deleted
  initial --> error
  initial --> expired

  preflight --> queued
  preflight --> deleted
  preflight --> error
  preflight --> expired

  queued --> executing
  queued --> deleted
  queued --> error
  queued --> expired

  executing --> complete
  executing --> queued
  executing --> deleted
  executing --> error
  executing --> expired

  complete --> deleted

  error --> deleted

  expired --> deleted

  deleted --> [*]
```

## Artifacts

* `initial`: the first state for an artifact. A UUID has been allocated,
  and a placeholder database entry created.
* `created`: the artifact has at least one version.
* `deleted`: the artifact has been deleted.
* `error`: the artifact is in an error state.

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> initial

  initial --> created
  initial --> deleted
  initial --> error

  created --> deleted
  created --> error

  error --> deleted

  deleted --> [*]
```

## Blobs

* `initial`: the first state for an blob. A UUID has been allocated,
  and a placeholder database entry created.
* `created`: the blob has data associated with it.
* `deleted`: the blob has been deleted.
* `error`: the blob is in an error state.

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> initial

  initial --> created
  initial --> deleted
  initial --> error

  created --> deleted
  created --> error

  error --> deleted

  deleted --> [*]
```

## Instances

* `initial`: this is the first state for an instance. A UUID has been allocated,
  a placeholder database entry created, and a request to create the instance has
  been queued.
* `preflight`: the instance creation request has been dequeued and is being
  validated against the current state of the cluster by the scheduler. At this
  point any required resources (transfers of blobs inside the cluster and
  fetching of images from outside the cluster) also occurs.
* `creating`: the instance is being created on the `node`.
* `created`: the instance is now running.
* `deleted`: the instance is now deleted.
* `error`: the instance is unable to be used.

Any instance may enter the `error` state, which happens when something bad has
happened. That process involves the instance being moved to a transition state
named for the instance's previous state, so for example an instance which was
`created` that went into Error would transition through `created-error`. This is
done because the `error` transition is a queue job and happens sometime later.
Instances in the `error` state are not removed like those in the `deleted` state,
as we assume a caller must acknowledge an error occurred. To remove them, delete
the instance in `error` state.

The state model also permits a `creating` or `created` instance to move directly
to `error`, and any of the `*-error` transition states to move on to `delete-wait`
or `deleted` as well as to `error`. An instance in `error` (or one of the
transition states) may likewise be sent to `delete-wait` or `deleted` when the
caller deletes it.

The following transitions are possible (note that hyphens have been replaced with
underscores in some state names due to limitations in the diagram renderer):

``` mermaid
stateDiagram-v2
  [*] --> initial
  [*] --> error

  initial --> preflight
  initial --> delete_wait
  initial --> deleted
  initial --> initial_error

  preflight --> creating
  preflight --> delete_wait
  preflight --> deleted
  preflight --> preflight_error

  creating --> created
  creating --> delete_wait
  creating --> deleted
  creating --> creating_error
  creating --> error

  created --> delete_wait
  created --> deleted
  created --> created_error
  created --> error

  initial_error --> error
  initial_error --> delete_wait
  initial_error --> deleted
  preflight_error --> error
  preflight_error --> delete_wait
  preflight_error --> deleted
  creating_error --> error
  creating_error --> delete_wait
  creating_error --> deleted
  created_error --> error
  created_error --> delete_wait
  created_error --> deleted
  delete_wait_error --> error

  error --> delete_wait
  error --> deleted

  delete_wait --> deleted
  delete_wait --> delete_wait_error

  deleted --> [*]
```

## Mapping Rules

A mapping rule binds claims from a trusted issuer to a set of scopes in one
namespace. Writing a rule is atomic, so there is no error state.

* `initial`: the first state for a mapping rule. A UUID has been allocated and
  a database entry exists, but the rule's attributes have not been written yet.
* `created`: the rule is complete and may be used to exchange identity tokens.
* `deleted`: the rule has been deleted. It matches nothing from this point on,
  but keys it has already minted keep working until they expire.

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> initial
  initial --> created
  initial --> deleted
  created --> deleted
  deleted --> [*]
```

## Namespace Claims

A namespace claim reserves aggregate cluster capacity for one namespace. Every
mutation is a single guarded database transaction, so there is no error state.

* `initial`: the first state for a claim. The guarded transaction has granted
  the claim and written its row, but the object's creation is not complete.
* `created`: the claim exists.
* `deleted`: the claim has been deleted. Claims are never *soft* deleted --
  the transaction which removes the claim's row is the one which returns its
  capacity to the cluster, because a claim sitting in `deleted` while its row
  still held capacity would promise that capacity to a namespace which no
  longer wanted it. This state is still reachable, because the orphan
  reconciliation sweep writes it to repair a claim row whose state row was
  lost, after which the usual reaper collects it.

Note that a claim carries a *second* state which is not this one. This state
machine describes the claim's existence; whether the claim still covers
placements is published separately as `coverage_state` (`active` or `expired`)
and is owned by the capacity reconciler's expiry sweep. An expired claim is
still a `created` object. See
[the scheduler operator guide](../operator_guide/scheduler.md#namespace-capacity-claims).

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> initial
  initial --> created
  initial --> deleted
  created --> deleted
  deleted --> [*]
```

## Namespaces

* `created`: the namespace exists.
* `deleted`: the namespace has been deleted.

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> created
  created --> deleted
  deleted --> [*]
```

## Networks

* `initial`: first state for a network. A UUID has been allocated, database entry
  created, and a request to create the network on the `networknode` has been queued.
* `created`: the network has been created on the `networknode`.
* `delete-wait`: the network has been scheduled for deletion. Waiting for
  instances on the network to be deleted.
* `deleted`: the network is now deleted.
* `error`: the network has encountered an error and cannot be used.

A network is regarded as "dead" when it is in state `deleted`, `delete-wait` or
`error`.

The following transitions are possible (note that hyphens have been replaced with
underscores in some state names due to limitations in the diagram renderer):

``` mermaid
stateDiagram-v2
  [*] --> initial

  initial --> created
  initial --> deleted
  initial --> error

  created --> deleted
  created --> delete_wait
  created --> error

  delete_wait --> deleted
  delete_wait --> error

  error --> deleted

  deleted --> [*]
```

## Network Interfaces

* `initial`: the first state for a network interface. A UUID has been allocated,
  and a placeholder database entry created.
* `created`: the network interface has data associated with it.
* `deleted`: the network interface has been deleted.
* `error`: the network interface is in an error state.

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> initial

  initial --> created
  initial --> deleted
  initial --> error

  created --> deleted
  created --> error

  error --> deleted

  deleted --> [*]
```

## Nodes

* `initial`: the first state for a node. A UUID has been allocated and a
  placeholder database entry created, but the node has not yet completed its
  first check in. A node is promoted to `created` once its daemons report in.
* `created`: the node has checked in and is available for scheduling.
* `stopping`: the node is gracefully shutting down.
* `stopped`: the node has gracefully shut down.
* `deleted`: the node was manually evacuated and removed. The `node` object is
  the only object type that is never hard deleted. Unlike every other object, a
  `node` can also be returned from `deleted` to `created`: you delete a node to
  force the instances hosted on it to be marked as gone, then, once the hardware
  is repaired, return the node to service.
* `missing`: the node has not checked in within the NODE_CHECKIN_MAXIMUM deadline.
* `degraded`: one of the node's daemons is self-reporting as not running. A
  degraded node is still a scheduling candidate.
* `error`: a resource the node depends on has failed -- for example its blob or
  instance storage is unreadable, remounted read-only, or a hung NFS mount (see
  [Node resource health](../operator_guide/node_health.md)). An errored node is
  not a scheduling candidate, and its blob replicas stop counting toward
  replication targets. Resource-health errors never clear automatically; an
  operator runs `sf-ctl clear-node-error` once the underlying problem is fixed.
  The `node` object is the only object which can return from an `error` state to
  other states.

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> initial

  initial --> created
  initial --> error
  initial --> missing
  initial --> degraded

  created --> deleted
  created --> error
  created --> missing
  created --> stopping
  created --> degraded

  stopping --> stopped
  stopping --> deleted
  stopping --> error
  stopping --> created
  stopping --> degraded

  stopped --> created
  stopped --> deleted
  stopped --> error
  stopped --> degraded

  degraded --> created
  degraded --> deleted
  degraded --> error
  degraded --> missing
  degraded --> stopping

  error --> created
  error --> deleted
  error --> degraded

  missing --> created
  missing --> deleted
  missing --> error
  missing --> degraded

  deleted --> created
```

## Trusted Issuers

A trusted issuer is an external identity provider whose tokens the cluster is
willing to validate. Configuring one is atomic, so there is no error state.

* `initial`: the first state for a trusted issuer. A UUID has been allocated
  and a database entry exists, but the issuer's attributes have not been
  written yet.
* `created`: the issuer is complete, and mapping rules may name it.
* `deleted`: the issuer has been deleted. Tokens from it are refused
  immediately, which is the fastest way to disown a compromised provider.

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> initial
  initial --> created
  initial --> deleted
  created --> deleted
  deleted --> [*]
```

## Upload

* `created`: the upload has data associated with it.
* `deleted`: the network interface has been deleted.

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> created
  created --> deleted
  deleted --> [*]
```