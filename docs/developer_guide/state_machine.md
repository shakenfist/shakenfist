# Shaken Fist's State Machine

Shaken Fist implements a state machine for each object type. This page documents
the possible states for each object, and which transitions between states are
valid.

Shaken Fist rigidly enforces the state model defined for each object, and will
raise an exception if an unexpected transition occurs. The state model for a
given object is defined in the `state_targets` map in the object class for those
keen on reading code.

Objects marked as `deleted` are removed from etcd after sitting deleted for
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

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> initial
  [*] --> error

  initial --> preflight
  initial --> queued
  initial --> deleted
  initial --> error

  preflight --> queued
  preflight --> deleted
  preflight --> error

  queued --> executing
  queued --> deleted
  queued --> error

  executing --> complete
  executing --> deleted
  executing --> error

  complete --> deleted

  error --> deleted

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

  created --> delete_wait
  created --> deleted
  created --> created_error

  initial_error --> error
  preflight_error --> error
  creating_error --> error
  created_error --> error
  delete_wait_error --> error

  error --> delete_wait
  error --> deleted

  delete_wait --> deleted
  delete_wait --> delete_wait_error

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

* `created`: on first check in, a node is created in the "created" state.
* `stopping`: the node is gracefully shutting down.
* `stopped`: the node has gracefully shut down.
* `deleted`: the node was manually evacuated and removed. Note that the `node`
  object is the only object type to never hard delete, although a `node` cannot
  be undeleted.
* `missing`: the node has not checked in within the NODE_CHECKIN_MAXIMUM deadline.
* `error`: the node has not check in for ten times NODE_CHECKIN_MAXIMUM, and all
  instances on this node have been declared to be in an error state. The `node`
  object is the only object which can return from an `error` state to other states.

The following transitions are possible:

``` mermaid
stateDiagram-v2
  [*] --> created
  [*] --> error
  [*] --> missing

  created --> deleted
  created --> error
  created --> missing
  created --> stopping

  stopping --> stopped
  stopping --> deleted
  stopping --> error
  stopping --> created

  stopped --> created
  stopped --> deleted
  stopped --> error

  error --> created
  error --> deleted

  missing --> created
  missing --> deleted
  missing --> error
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