# Current development goals

## Currently under way

* Convert from etcd to mariadb for persistent storage to take advantage of secondary indices
* Add mypy type hints
* Use shakenfist.schema for all data storage requiring a schema -- etcd while it lasts, generation of mariadb schemas, REST API outputs
* REST API input schema validation

## Partial, but not currently being progressed

* Move privileged operations to privexec. Perhaps move all process executions. Perhaps have multiple privexec daemons with different access levels.
* Opportunistically convert to f-strings

## Not yet started

* ansible-lint for the deployment Ansible