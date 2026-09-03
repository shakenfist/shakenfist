# Current development goals

## Currently under way

* Convert from etcd to mariadb for persistent storage to take advantage of secondary indices
* Artifacts have been migrated to mariadb (static values, attributes, and version indexes).
* Blob transfer tracking and incomplete locations are now fully in mariadb (blob_transfers table). The clusteroperations-by-blob etcd index has been replaced with the cluster_operation_targets mariadb table. The per-blob scheduled task queue now enumerates blobs from mariadb instead of etcd.
* Add mypy type hints
* Use shakenfist.schema for all data storage requiring a schema -- etcd while it lasts, generation of mariadb schemas, REST API outputs
* REST API input schema validation
* Use the schedule library, not manual timer loops

## Partial, but not currently being progressed

* Move privileged operations to privexec. Perhaps move all process executions. Perhaps have multiple privexec daemons with different access levels.
* Opportunistically convert to f-strings
* Remove IPAMReservation.to_legacy_dict() and from_legacy_dict() once etcd migration is complete. The in-memory store now uses IPAMReservation objects directly. Event logs still use to_legacy_dict() and should be updated to use model_dump().
* Change ObjectState.object_uuid from str to UUID4 type for type safety and efficient storage. This is blocked on migrating Namespace objects to MariaDB because they still use non-UUID identifiers. Node objects have been migrated and now use real UUID4s. For Namespace objects:
    - Generate proper UUIDs for namespaces
    - Store the namespace name as a separate indexed field
    - Migrate existing namespace references from name to UUID
    - Update all code that looks up namespaces by name to use the name field instead
* Add mypy UUID4 type hint to BaseObject.__uuid. This is blocked on the Namespace object migration -- BaseObject.__uuid is currently typed as str because Namespace objects use the namespace name as their UUID instead of a proper UUID. Node objects have been migrated and now use real UUID4s.
  Once namespaces also use real UUIDs, we can add UUID4 typing to __uuid which would then propagate type safety to unique_label() and from_db() methods. Note: The Namespace class has custom __init__ and uuid property implementations that bypass UUID validation.
* Use enums instead of strings in gRPC protos. Many gRPC message fields currently use strings where enums would provide better type safety and validation. This would require updating the proto definitions and regenerating the Python bindings.

## Not yet started

* ansible-lint for the deployment Ansible
* mTLS with a private CA for gRPC services
* SO_PASSCRED for UDS services
* Progressively convert to more modern python features like f-strings
* Convert the iptables rule generation we use for virtual networks to the more modern nftables. nftables also has a stable JSON API and python bindings (`nftables` on pypi), so this should clean up a fair bit of command line generation code.
* Provide network traffic flow exporters for analysis by operators.
* Drop generic methods on the database service which mimic etcd calls -- `get`, `put`, etc. Instead the database service should own all of the business logic around accessing the database tier, and calls to the database service should in the form of coherent requests -- list all uploads on this node for example.
* Stop converting UUIDs to strings all the time.
* mTLS for inter-cluster traffic.
* Remove the deprecated weighted affinity form. The affinity rules themselves are no longer in doubt: the soft form *ranks* the hypervisors which survived admission and does not admit anything itself, so a placement with a single candidate neither honours nor violates a preference, and `require_with_tag` / `require_without_tag` are how you ask for admission rather than for a ranking. That is documented in `docs/user_guide/affinity.md` and was settled by phase 6 of `docs/plans/PLAN-scheduler-reservations.md`, which also closed issue 3565. What is left is retiring the numeric weights, which needs its own release and deprecation window; `docs/operator_guide/scheduler.md` has the recipe for finding instances still carrying one.
* systemd restarts often timeout / fail.
* I am no longer sure that many binaries / systemd services is a good idea. Perhaps one big binary would be better?

## Wider structural things

* I think having code for a given object type spread out across the various daemons was a mistake. So for example, a Blob should be implemented in blob.py, and as much as is possible all object lifecycle management code should _also_ be in blob.py. If the cleaner daemon on each node needs to do a thing, then it should call into a method in blob.py to do that thing. This way, the implementation for a given object type is all together, and its easier to reason about the behaviour of that object. This is a transition which has not occurred, although some small steps have been made in that direction.
* If all of an object's implementation are going to live together -- then should there be a standard interface for lifecycle tasks for objects? Should the cleaner daemon in the example above just be calling _all_ `local_maintenance()` routines on _all_ object types regularly? Perhaps the interface could let you specify the frequency for the call if you were keen. This would mean adding a new object type to the cluster would be relatively trivial to do. It would also mean we could have "plugin" object types later if we wanted which might be cool.
