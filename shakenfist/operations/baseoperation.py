import time
from enum import Enum
from types import ModuleType
from typing import Any, Optional, cast

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.operations.error_report import ErrorReport
from shakenfist.schema.operations.baseclusteroperation import PRIORITY


class BaseOperationException(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


class InvalidPriorityException(BaseOperationException):
    def __init__(self, task: str, priority: str) -> None:
        super().__init__(f'{priority} is not a valid priority')


class CannotDeferUnqueued(Exception):
    ...


class BaseOperation(dbo):
    # docs/developer_guide/state_machine.md has a description of these states.
    STATE_QUEUED = 'queued'
    STATE_PREFLIGHT = 'preflight'
    STATE_EXECUTING = 'executing'
    STATE_COMPLETE = 'complete'
    STATE_ABORT = 'abort'

    ACTIVE_STATES = {dbo.STATE_CREATED, STATE_QUEUED, STATE_EXECUTING,
                     STATE_COMPLETE}


def get_all_node_queues(node_uuid: str) -> list[str]:
    return (get_node_user_facing_node_queues(node_uuid) +
            get_general_user_facing_node_queues() +
            get_node_background_node_queues(node_uuid) +
            get_general_background_node_queues())


def get_all_user_facing_node_queues(node_uuid: str) -> list[str]:
    return (get_node_user_facing_node_queues(node_uuid) +
            get_general_user_facing_node_queues())


def get_all_background_node_queues(node_uuid: str) -> list[str]:
    return (get_node_background_node_queues(node_uuid) +
            get_general_background_node_queues())


def get_node_user_facing_node_queues(node_uuid: str) -> list[str]:
    return [
        f'{node_uuid}-clusteroperation-user_waiting',
        f'{node_uuid}-clusteroperation-user_facing',
        f'{node_uuid}-clusteroperation-user_facing_high_io'
    ]


def get_node_background_node_queues(node_uuid: str) -> list[str]:
    return [
        f'{node_uuid}-clusteroperation-background',
        f'{node_uuid}-background',
        f'{node_uuid}-clusteroperation-background_high_io'
    ]


def get_general_user_facing_node_queues() -> list[str]:
    return [
        'any-clusteroperation-user_waiting',
        'any-clusteroperation-user_facing',
        'any-clusteroperation-user_facing_high_io'
    ]


def get_general_background_node_queues() -> list[str]:
    return [
        'any-clusteroperation-background',
        'any-clusteroperation-background_high_io'
    ]


def get_all_network_queues() -> list[str]:
    return [
        'networknode-clusteroperation-user_waiting',
        'networknode-clusteroperation-user_facing',
        'networknode-clusteroperation-user_facing_high_io',
        'networknode-clusteroperation-background',
        'networknode-clusteroperation-background_high_io'
    ]


def get_node_network_queues(node_uuid: str) -> list[str]:
    return [
        f'{node_uuid}-network-user_waiting',
        f'{node_uuid}-network-user_facing',
        f'{node_uuid}-network-user_facing_high_io',
        f'{node_uuid}-network-background',
        f'{node_uuid}-network-background_high_io',
    ]


class BaseClusterOperation(BaseOperation):
    # Note that cluster operations are created in etcd transactions and don't
    # have .new() methods. They therefore jump straight to queued as an initial
    # state.
    @classmethod
    def _db_get(cls, object_uuid: str) -> Optional[dict[str, Any]]:
        o = mariadb.get_cluster_operation(object_uuid)
        if not o:
            return None

        if o.get('version', 0) != cls.current_version:
            if not cls.upgrade_supported:
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - '
                    f'{cls.object_type}: {o}')
        return o

    state_targets: dict[Optional[str], Optional[tuple[str, ...]]] = {  # type: ignore[assignment]
        None: (BaseOperation.STATE_QUEUED,),
        BaseOperation.STATE_QUEUED: (BaseOperation.STATE_EXECUTING,
                                     dbo.STATE_DELETED, dbo.STATE_ERROR,
                                     BaseOperation.STATE_ABORT),
        BaseOperation.STATE_EXECUTING: (BaseOperation.STATE_COMPLETE,
                                        dbo.STATE_DELETED, dbo.STATE_ERROR,
                                        BaseOperation.STATE_ABORT,
                                        BaseOperation.STATE_QUEUED),
        BaseOperation.STATE_COMPLETE: (dbo.STATE_DELETED,),
        BaseOperation.STATE_ABORT: (dbo.STATE_DELETED,),
        dbo.STATE_ERROR: (dbo.STATE_DELETED,),
        dbo.STATE_DELETED: None,
    }

    def hard_delete(self) -> None:
        # Clean up the cluster_operations row and the
        # cluster_operation_errors row (if any) so neither table grows
        # unbounded as the cleaner sweeps terminal-state operations.
        # ``delete_cluster_operation_error`` is idempotent — ops that
        # never failed have no row to remove.
        uuid_str = str(self.uuid)
        mariadb.delete_cluster_operation_error(uuid_str)
        mariadb.delete_cluster_operation(uuid_str)
        super().hard_delete()

    def __init__(
            self,
            static_values: dict[str, Any],
            schema: ModuleType
    ) -> None:
        super().__init__(static_values['uuid'], static_values.get('version'))
        self.__priority = PRIORITY[static_values['priority']]
        self.__request_id: Optional[str] = static_values.get('request_id')
        self.__depends_on: Optional[list[dict[str, Any]]] = \
            static_values.get('depends_on')
        self.__runs_after: Optional[list[dict[str, Any]]] = \
            static_values.get('runs_after')

        # We only know this if we have been dequeued
        self._queue_name: Optional[str] = None

        # Set by the queue dispatcher from the work_item payload so that
        # defer_with_backoff() knows how many times this operation has
        # already been deferred. Each defer() bumps the counter that gets
        # written into the next work_item.
        self.current_defer_count: int = 0

        # Convert tasks names back into enum entries
        self.__tasks: list[Enum] = []
        for task_name in static_values['tasks']:
            try:
                self.__tasks.append(schema.model_tasks[task_name])
            except KeyError as e:
                self.state = self.STATE_ERROR  # type: ignore[misc]
                self.add_event(
                    EVENT_TYPE_AUDIT, 'unknown task {task_name}: {e}')
                raise e

    @property
    def priority(self) -> PRIORITY:
        return self.__priority

    @property
    def request_id(self) -> Optional[str]:
        return self.__request_id

    @property
    def depends_on(self) -> list[dict[str, Any]]:
        if not self.__depends_on or len(self.__depends_on) == 0:
            return []
        return self.__depends_on

    @property
    def runs_after(self) -> list[dict[str, Any]]:
        if not self.__runs_after or len(self.__runs_after) == 0:
            return []
        return self.__runs_after

    @property
    def queue_name(self) -> Optional[str]:
        return self._queue_name

    @queue_name.setter
    def queue_name(self, name: str) -> None:
        self._queue_name = name

    @property
    def tasks(self) -> list[Enum]:
        return self.__tasks

    # Methods
    def external_view(self) -> dict[str, Any]:
        tasks = []
        for t in self.__tasks:
            tasks.append(t.name)

        return {
            'operation_type': self.object_type,
            'uuid': str(self.uuid),
            'state': self.state.value,
            'tasks': tasks
        }

    def execute(self) -> None:
        self.state = BaseClusterOperation.STATE_EXECUTING  # type: ignore[misc]
        for t in self.tasks:
            self.dispatch_task(t)  # type: ignore[attr-defined]
            if self.state.value in [BaseClusterOperation.STATE_ABORT,  # type: ignore[attr-defined]
                                    BaseClusterOperation.STATE_DELETED,
                                    BaseClusterOperation.STATE_ERROR,
                                    BaseClusterOperation.STATE_QUEUED]:
                return
        self.state = BaseClusterOperation.STATE_COMPLETE  # type: ignore[misc]

    def defer(
            self,
            waiting_on: Optional[list[dbo]] = None,
            delay: int = 15
    ) -> None:
        if not self.queue_name:
            raise CannotDeferUnqueued(
                'You cannot defer a cluster operation which has not been '
                'queued')

        # Re-enqueue this operation for a retry after delay seconds
        wo: Optional[list[str]] = None
        if waiting_on:
            wo = []
            for wobj in waiting_on:
                wo.append(wobj.unique_label())

        next_defer_count = self.current_defer_count + 1
        self.add_event(
            EVENT_TYPE_STATUS, f'Execution deferred for {delay} seconds',
            extra={
                'waiting_on': wo,
                'defer_count': next_defer_count
            })
        work_item = {
            'operation_type': self.object_type,
            'operation_uuid': str(self.uuid),
            'defer_count': next_defer_count
        }
        mariadb.enqueue_work_item(
            self.queue_name, work_item, delay=delay)
        self.state = self.STATE_QUEUED  # type: ignore[misc]

    def defer_with_backoff(
            self,
            delays: tuple[int, ...] = (15, 30, 60),
            reason: Optional[str] = None
    ) -> bool:
        """Schedule a retry of this operation with backoff.

        Uses self.current_defer_count to look up the next delay from
        delays. Returns True if a retry was scheduled (and the op is
        now back in STATE_QUEUED), or False if the retry budget has
        been exhausted (in which case the caller should error the op
        out itself).
        """
        if self.current_defer_count >= len(delays):
            self.add_event(
                EVENT_TYPE_STATUS,
                'retry budget exhausted, will not defer further',
                extra={
                    'defer_count': self.current_defer_count,
                    'max_defers': len(delays),
                    'reason': reason
                })
            return False

        delay = delays[self.current_defer_count]
        self.add_event(
            EVENT_TYPE_STATUS,
            'scheduling retry after transient failure',
            extra={
                'defer_count': self.current_defer_count + 1,
                'max_defers': len(delays),
                'delay': delay,
                'reason': reason
            })
        self.defer(delay=delay)
        return True

    def is_outstanding(self) -> bool:
        if self.state.value in [BaseClusterOperation.STATE_ERROR,
                                BaseClusterOperation.STATE_DELETED,
                                BaseClusterOperation.STATE_ABORT,
                                BaseClusterOperation.STATE_COMPLETE]:
            return False
        return True

    @property
    def error_report(self) -> Optional[ErrorReport]:
        """The structured ErrorReport persisted for this op, if any.

        Reads fresh from MariaDB on every access -- no caching --
        because the worker may persist the report after this object
        was loaded from the database. Returns ``None`` if no report
        has been written (the common case for COMPLETE/ABORT ops).
        """
        return mariadb.get_cluster_operation_error(self.uuid)

    def raise_for_error(self, timeout: Optional[float] = None) -> None:
        """Block until terminal, then raise on ERROR.

        Polls until this operation reaches a terminal state (bounded
        by ``timeout`` -- defaults to ``config.API_ASYNC_WAIT``). On
        ``STATE_ERROR`` raises :class:`NetworkOperationFailed` carrying
        the persisted :class:`ErrorReport`. On any other terminal
        state (``COMPLETE``, ``ABORT``, ``DELETED``) returns silently;
        callers that need to distinguish ``ABORT`` from ``COMPLETE``
        inspect ``op.state.value`` explicitly. Raises
        :class:`OperationTimeout` if the deadline elapses without a
        terminal transition.
        """
        refreshed = poll_until_terminal(self, timeout)
        if refreshed.state.value == dbo.STATE_ERROR:
            report = refreshed.error_report
            if report is None:
                # Fallback: error state with no persisted report. This
                # shouldn't happen in practice -- the dispatcher writes
                # the report before flipping state to ERROR -- but a
                # race or an older worker could leave the row missing.
                # Build a minimal report so callers still get a
                # structured failure to branch on.
                report = ErrorReport(
                    code='internal.unknown',
                    message=(
                        f'operation {refreshed.uuid} ended in error '
                        f'state'),
                    details={},
                    origin_class='',
                    traceback='',
                )
            raise exceptions.NetworkOperationFailed(report)


def poll_until_terminal(
        op: 'BaseClusterOperation',
        timeout: Optional[float] = None) -> 'BaseClusterOperation':
    """Block until ``op`` reaches a terminal state, then return it refreshed.

    Polls ``cls.from_db(op.uuid)`` (where ``cls`` is the concrete op
    type) at a 0.1 second cadence so each iteration sees the freshest
    state written by the worker. The terminal state set is
    ``{STATE_COMPLETE, STATE_ABORT, STATE_DELETED, STATE_ERROR}``.

    ``timeout`` defaults to ``config.API_ASYNC_WAIT`` (15 seconds) when
    ``None`` is passed; callers can override for long-running ops.
    Raises :class:`OperationTimeout` if the deadline elapses without
    observing a terminal state.
    """
    if timeout is None:
        timeout = config.API_ASYNC_WAIT
    terminal_states = {
        BaseClusterOperation.STATE_COMPLETE,
        BaseClusterOperation.STATE_ABORT,
        dbo.STATE_DELETED,
        dbo.STATE_ERROR,
    }
    cls = type(op)
    deadline = time.time() + timeout
    while True:
        refreshed = cls.from_db(op.uuid)
        if refreshed is not None and refreshed.state.value in terminal_states:
            return cast('BaseClusterOperation', refreshed)
        if time.time() >= deadline:
            raise exceptions.OperationTimeout(
                f'operation {op.uuid} did not reach terminal state '
                f'within {timeout}s')
        time.sleep(0.1)
