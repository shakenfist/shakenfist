# Shaken Fist: Opinionated to the point of being impolite
![Python application](https://github.com/shakenfist/shakenfist/workflows/Python%20application/badge.svg)
<a href="https://pypi.org/project/shakenfist" target="_blank">
    <img src="https://img.shields.io/pypi/v/shakenfist?color=%2334D058&label=pypi%20package" alt="Package version">
</a>

**Documentation:** https://shakenfist.com/
**Source Code:** https://github.com/shakenfist/shakenfist

## Claude Code Skills

This repository includes Claude Code skills in `.claude/skills/` to assist with
common development tasks:

- **migrate-etcd-to-mariadb**: Guides the migration of object data from etcd
  to MariaDB, following established patterns from Upload, DnsMasq, IPAM, and
  blob migrations.
- **add-grpc-service**: Guides adding new gRPC service methods to the database
  microservice, including proto definitions, handler implementation, and client
  functions.
- **add-mypy-coverage**: Guides adding mypy type annotations to modules,
  following the incremental rollout approach documented in PLAN-mypy-rollout.md.

These skills are loaded automatically when using Claude Code within this
repository.