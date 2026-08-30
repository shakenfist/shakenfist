# Shaken Fist: Opinionated to the point of being impolite
![Python application](https://github.com/shakenfist/shakenfist/workflows/Python%20application/badge.svg)
<a href="https://pypi.org/project/shakenfist" target="_blank">
    <img src="https://img.shields.io/pypi/v/shakenfist?color=%2334D058&label=pypi%20package" alt="Package version">
</a>

**Documentation:** https://shakenfist.com/
**Source Code:** https://github.com/shakenfist/shakenfist

## Deployment

Shaken Fist is deployed with the `shakenfist.shakenfist` Ansible collection
(which lives in [`shakenfist/deploy/collection/`](https://github.com/shakenfist/shakenfist/tree/develop/shakenfist/deploy/collection)
in this repository and is published to Ansible Galaxy). You write an inventory
describing your machines, set a handful of variables, and run a playbook.
Ready-to-use examples ship in [`examples/`](https://github.com/shakenfist/shakenfist/tree/develop/examples) —
`examples/single-node/` is the recommended quickstart. See
[`docs/operator_guide/installation.md`](https://github.com/shakenfist/shakenfist/blob/develop/docs/operator_guide/installation.md)
for the full walkthrough.

## Prerequisites

Shaken Fist requires an operator-provided MariaDB 10.11.0+ server (the
`INET4` column type it uses arrived in 10.10, and 10.11 is the oldest
in-support LTS above that, so this is a hard requirement rather than a
preference). Before deploying, provision a MariaDB instance and apply
the bootstrap snippet (`tools/bootstrap-mariadb.sql`). See
[`docs/operator_guide/database.md`](https://github.com/shakenfist/shakenfist/blob/develop/docs/operator_guide/database.md) for the
complete setup workflow.

Shaken Fist emits structured JSON logs and can ship them to an
operator-provided [Loki](https://grafana.com/oss/loki/), or log locally to the
systemd journal if you prefer your own log agent. See
[`docs/operator_guide/logging.md`](https://github.com/shakenfist/shakenfist/blob/develop/docs/operator_guide/logging.md).

Shaken Fist monitors the storage each node depends on and takes a node with
failed storage (a dead disk or a hung NFS mount) out of scheduling
automatically. See
[`docs/operator_guide/node_health.md`](https://github.com/shakenfist/shakenfist/blob/develop/docs/operator_guide/node_health.md).

## Claude Code Skills

This repository includes Claude Code skills in `.claude/skills/` to assist with
common development tasks:

- **add-grpc-service**: Guides adding new gRPC service methods to the database
  microservice, including proto definitions, handler implementation, and client
  functions.
- **add-mypy-coverage**: Guides adding mypy type annotations to modules,
  following the incremental rollout approach documented in
  [`docs/developer_guide/mypy.md`](https://github.com/shakenfist/shakenfist/blob/develop/docs/developer_guide/mypy.md).

These skills are loaded automatically when using Claude Code within this
repository.