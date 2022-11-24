Shaken Fist's State Machine
===========================

Shaken Fist implements a state machine for each object type. This page documents the possible states for each object, and which transitions between states are valid.

Instances
---------

* `Initial`: this is the first state for an instance. A UUID has been allocated, a placeholder database entry created, and a request to create the instance has been queued.
* `Preflight`: the instance creation request has been dequeued and is being validated against the current state of the cluster by the scheduler.
* `Creating`: the instance is being created.
* `Created`: the instance is now running.
* `Deleted`: the instance is now deleted.
* `Error`: the instance is unable to be used.

* Instances marked as Deleted are deleted from the DB after sitting deleted for
  some period of time.

* Any instance may enter the Error state, which happens when something bad has happened. That process involves the instance being moved to a transition state named for the instance's previous state, so for example an instance which was Created that went into Error would transition through Created-Error. This is done because the Error transition is a queue job and happens sometime later. Instances in the Error state are not removed like those in the deleted state, as we assume a caller must acknowledge an error occured. To remove them, delete the instance in Error state.

Networks
--------

* `Initial`: first state for a network. A UUID has been allocated, database entry created, and a request to create the network on the `networknode` has been queued.
* `Created`: the network has been created on the `networknode`.
* `Delete_Wait`: the network has been scheduled for deletion. Waiting for
  instances on the network to be deleted.
* `Deleted`: the network is now deleted.
* `Error`: the network has encountered an error and cannot be used.

* Networks marked as Deleted are deleted from the DB after sitting deleted for
  some period of time.

* A network is regarded as `Dead` when it is in state `Deleted`, `Delete_Wait` or `Error`.

Nodes
-----

* `Created`: on first check in, a node is created in the "created" state.
* `Stopping`: the node is gracefully shutting down.
* `Stopped`: the node has gracefully shut down.
* `Deleted`: the node was manually evacuated and removed.
* `Missing`: the node has not checked in within the NODE_CHECKIN_MAXIMUM deadline.
* `Error`: the node has not check in for ten times NODE_CHECKIN_MAXIMUM, and all instances on this node have been declared to be in an error state.