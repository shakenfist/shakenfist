"""Node-level resource health evaluation.

Maps a node's capability flags to the object types it hosts, collects the
storage-path health checks those types declare
(PLAN-node-resource-health E1/E2), de-duplicates them, runs each, and
composes a result naming the failed checks and the affected object types.
sf-resources consumes this to drive node.state.
"""

import os
from dataclasses import dataclass

from shakenfist_utilities import logs  # noreorder

from shakenfist import blob
from shakenfist import instance
from shakenfist import upload
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.node import Node
from shakenfist.resource_health import HealthCheck, HealthResult, PathCheck
from shakenfist.schema.object_types import ObjectType


LOG, _ = logs.setup(__name__)


@dataclass
class NodeHealthResult:
    healthy: bool
    failed: list[HealthResult]
    affected_types: set[ObjectType]
    reason: str


def node_object_types() -> list[tuple[ObjectType, list[str]]]:
    """The (object type, storage dependencies) pairs this node hosts.

    Every active node is a potential blob replica store (blob placement is
    disk-based, not capability-gated) and any node may run sf-api and
    receive an upload, so Blob and Upload apply everywhere; Instance is
    hypervisor-only.
    """
    deps: list[tuple[ObjectType, list[str]]] = [
        (blob.Blob.object_type, blob.Blob.health_dependencies),
        (upload.Upload.object_type, upload.Upload.health_dependencies),
    ]
    if config.NODE_IS_HYPERVISOR:
        deps.append((instance.Instance.object_type,
                     instance.Instance.health_dependencies))
    return deps


def build_checks(
        dependencies: list[tuple[ObjectType, list[str]]], *,
        storage_path: str, write_interval: float, timeout: float
) -> tuple[list[HealthCheck], dict[str, set[ObjectType]]]:
    """Build the de-duplicated set of checks for `dependencies`.

    Returns the unique checks (one PathCheck per resolved path) and a map
    from check identity to the set of object types that depend on it, so a
    failed check can be attributed back to every affected type.
    """
    checks: dict[str, HealthCheck] = {}
    types_by_identity: dict[str, set[ObjectType]] = {}
    for object_type, subdirs in dependencies:
        for subdir in subdirs:
            identity = os.path.abspath(os.path.join(storage_path, subdir))
            if identity not in checks:
                checks[identity] = PathCheck(
                    identity, write_interval=write_interval, timeout=timeout)
            types_by_identity.setdefault(identity, set()).add(object_type)
    return list(checks.values()), types_by_identity


def evaluate(
        checks: list[HealthCheck],
        types_by_identity: dict[str, set[ObjectType]]) -> NodeHealthResult:
    """Run every check once and compose the node's health result."""
    failed: list[HealthResult] = []
    affected: set[ObjectType] = set()
    for check in checks:
        result = check.check()
        if not result.healthy:
            failed.append(result)
            affected |= types_by_identity.get(check.identity, set())
    return NodeHealthResult(
        healthy=not failed, failed=failed, affected_types=affected,
        reason=_compose_reason(failed, types_by_identity))


def _compose_reason(
        failed: list[HealthResult],
        types_by_identity: dict[str, set[ObjectType]]) -> str:
    if not failed:
        return 'all resource health checks passed'
    parts = []
    for r in failed:
        types = ', '.join(sorted(
            str(t) for t in types_by_identity.get(r.identity, set())))
        detail = f': {r.detail}' if r.detail else ''
        parts.append(f'{types} depend on {r.identity} ({r.status}{detail})')
    return 'resource health check failed: ' + '; '.join(parts)


def build_for_this_node(
) -> tuple[list[HealthCheck], dict[str, set[ObjectType]]]:
    """Build the checks for the node this daemon runs on, from config."""
    return build_checks(
        node_object_types(),
        storage_path=config.STORAGE_PATH,
        write_interval=config.NODE_HEALTH_WRITE_INTERVAL,
        timeout=config.NODE_HEALTH_PROBE_TIMEOUT)


def apply_result(node: Node, result: NodeHealthResult) -> bool:
    """Apply a health result to a node's state.

    On an unhealthy result for a node not already in error, record an audit
    event carrying the diagnosis (reason, affected object types, failed
    checks) and move the node to STATE_ERROR -- which, by existing mechanics,
    stops scheduling onto the node and stops its blob copies counting as
    replicas. Returns True if the node was changed.

    It never clears error (recovery is operator-only, master plan D6) and
    never touches created/degraded, which the daemon-state and heartbeat
    logic own. Already-errored nodes are left untouched so the diagnosis is
    recorded once, not re-emitted every cycle. The audit event is the durable
    record phase 3 consumes for the affected object types (node attributes are
    a fixed typed schema, so a free-form record lives in the event log).
    """
    if result.healthy:
        return False
    if node.state.value == Node.STATE_ERROR:
        return False
    node.add_event(
        EVENT_TYPE_AUDIT, result.reason,
        extra={
            'affected_types': sorted(str(t) for t in result.affected_types),
            'failed': [
                {'path': r.identity, 'status': r.status, 'detail': r.detail}
                for r in result.failed],
        })
    # The base state property's setter is not visible under the silent
    # follow-imports mypy uses for this module; the setter does exist.
    node.state = Node.STATE_ERROR  # type: ignore[misc]
    return True
