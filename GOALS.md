# Current development goals

## Currently under way

* Convert from etcd to mariadb for persistent storage to take advantage of secondary indices
* Add mypy type hints
* Use shakenfist.schema for all data storage requiring a schema -- etcd while it lasts, generation of mariadb schemas, REST API outputs
* REST API input schema validation

## Partial, but not currently being progressed

* Move privileged operations to privexec. Perhaps move all process executions. Perhaps have multiple privexec daemons with different access levels.
* Opportunistically convert to f-strings
* Remove IPAMReservation.to_legacy_dict() and from_legacy_dict() once etcd migration is complete. The in-memory store now uses IPAMReservation objects directly. Event logs still use to_legacy_dict() and should be updated to use model_dump().
* Change ObjectState.object_uuid from str to UUID4 type for type safety and efficient storage. This is blocked on migrating the Node object to MariaDB because nodes historically use their hostname (FQDN) as their UUID instead of a proper UUID. When moving nodes to MariaDB, we should:
  - Generate proper UUIDs for nodes
  - Store the FQDN as a separate indexed field
  - Migrate existing node references from hostname to UUID
  - Update all code that looks up nodes by hostname to use the FQDN field instead
* Add mypy UUID4 type hint to BaseObject.__uuid. This is blocked on the same Node object migration above -- BaseObject.__uuid is currently typed as str because Node objects use their hostname (FQDN) as their UUID instead of a proper UUID. Once nodes use real UUIDs, we can add UUID4 typing to __uuid which would then propagate type safety to unique_label() and from_db() methods.
* Use enums instead of strings in gRPC protos. Many gRPC message fields currently use strings where enums would provide better type safety and validation. This would require updating the proto definitions and regenerating the Python bindings.

## Not yet started

* ansible-lint for the deployment Ansible
* mTLS with a private CA for gRPC services
* SO_PASSCRED for UDS services