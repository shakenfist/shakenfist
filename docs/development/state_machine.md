Shaken Fist's State Machine
===========================

Shaken Fist implements a state machine for each object type. This page documents the possible states for each object, and which transitions between states are valid.

Instances
---------

* None -> Initial: this is the first state for an instance. A UUID has been allocated, a placeholder database entry created, and a request to create the instance has been queued.
* Initial -> Preflight: the instance creation request has been dequeued and is being validated against the current state of the cluster by the scheduler.
* Preflight -> Creating: the instance is being created.
* Creating -> Created: the instance is now running.
* Created -> Deleted: the instance is now deleted.
* Deleted -> None: the instance has been hard deleted after sitting deleted for some period of time.

* Any instance may enter the Error state, which happens when something bad has happened. That process involves the instance being moved to a transition state named for the instance's previous state, so for example an instance which was Created that went into Error would transition through Created-Error. This is done because the Error transition is a queue job and happens sometime later. Instances in the Error state are not removed like those in the deleted state, as we assume a caller must acknowledge an error occured. To remove them, delete the instance in Error state.

Nodes
-----

* None -> Created: on first check in, a node is created in the "created" state.
* Created -> Deleted: the node was manually evacuated and removed.
* Created -> Missing: the node has not checked in within the NODE_CHECKIN_MAXIMUM deadline.
* Created -> Error: the node has not check in for ten times NODE_CHECKIN_MAXIMUM, and all instances on this node have been declared to be in an error state.