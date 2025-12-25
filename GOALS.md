# Current development goals

## Currently under way

* Convert from etcd to mariadb for persistent storage to take advantage of secondary indices
* Add mypy type hints
* Use shakenfist.schema for all data storage requiring a schema -- etcd while it lasts, generation of mariadb schemas, REST API outputs
* REST API input schema validation

## Partial, but not currently being progressed

* Move privileged operations to privexec. Perhaps move all process executions. Perhaps have multiple privexec daemons with different access levels.
* Opportunistically convert to f-strings
* Remove IPAMReservation.to_legacy_dict() and from_legacy_dict() once etcd migration is complete. Update ipam.py in-memory store to use IPAMReservation objects directly, and event logs to use model_dump().

## Not yet started

* ansible-lint for the deployment Ansible
* mTLS with a private CA for gRPC services
* SO_PASSCRED for UDS services