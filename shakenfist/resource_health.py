"""Node resource health checks.

The primitive behind PLAN-node-resource-health: a small, self-contained
health-check abstraction plus a deadline-guarded runner. Object types
declare the local resources they depend on as checks; the sf-resources
evaluator (a later phase) collects the checks of the object types a node
hosts, de-duplicates them by identity, runs each, and drives node.state
from the result.

This module is deliberately standalone -- it has no node-state, daemon,
or config dependencies. It is *not* shakenfist/external_api/health.py,
which is the unrelated sf-api daemon-readiness surface.
"""

import abc
import threading
from dataclasses import dataclass
from typing import Any, Callable

from shakenfist_utilities import logs  # noreorder


LOG, _ = logs.setup(__name__)


class HealthStatus:
    """The outcome categories a health check can report.

    Plain string constants rather than an Enum: the values flow straight
    into log lines, event detail, and node error reasons, and the string
    is the thing we care about.
    """

    OK = 'ok'
    MISSING = 'missing'
    READONLY = 'readonly'
    UNWRITABLE = 'unwritable'
    TIMEOUT = 'timeout'


@dataclass(frozen=True)
class HealthResult:
    identity: str
    status: str
    detail: str | None = None

    @property
    def healthy(self) -> bool:
        return self.status == HealthStatus.OK


class HealthCheck(abc.ABC):
    """A single, node-local health check.

    A subclass checks one resource -- a storage path, a daemon socket, a
    network reachability probe -- and reports a HealthResult. New check
    kinds are added as we encounter the need for them; this module ships
    only the path check.
    """

    @property
    @abc.abstractmethod
    def identity(self) -> str:
        """A stable identifier the evaluator de-duplicates on.

        Two checks that probe the same underlying resource (e.g. two
        object types that both depend on the blobs path) must return the
        same identity so the resource is probed only once per cycle.
        """

    @abc.abstractmethod
    def check(self) -> HealthResult:
        """Probe the resource and report its health.

        Must not raise for an unhealthy resource: an unhealthy resource is
        a HealthResult with a non-OK status, not an exception.
        """


class DeadlineProbe:
    """Run a callable under a wall-clock deadline, and never launch a
    second run while a previous one is still outstanding.

    The outstanding case is not an error to paper over: a hard NFS mount
    *hangs* on server death rather than returning EIO (see
    PLAN-node-resource-health decision D5), so a probe that has not
    returned means "still hung", and piling a second blocked thread on
    top would help nobody. A still-running probe therefore reports "not
    completed" and no new work is started until it finishes.

    The worker runs in a *daemon* thread deliberately, not a
    concurrent.futures executor: that module registers an atexit handler
    which joins every worker thread at interpreter shutdown, and a thread
    blocked forever in a hung-mount syscall would then hang process exit.
    A daemon thread is abandoned at exit instead, which is exactly right
    for a syscall that may never return.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None

    def run(self, fn: Callable[[], Any], timeout: float) -> tuple[bool, Any]:
        """Run ``fn`` under ``timeout`` seconds.

        Returns ``(completed, result)``. ``completed`` is False if a prior
        probe is still running, or if this probe did not return within
        ``timeout``; in both cases the worker thread is left running (as a
        daemon) and ``result`` is None. If ``fn`` raises, the exception is
        re-raised from here.
        """
        if self._thread is not None and self._thread.is_alive():
            return (False, None)

        event = threading.Event()
        holder: list[Any] = [None, None]

        def worker() -> None:
            try:
                holder[0] = fn()
            except BaseException as e:  # backstop: fn returns, does not raise
                holder[1] = e
            finally:
                event.set()

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

        if event.wait(timeout):
            if holder[1] is not None:
                raise holder[1]
            return (True, holder[0])
        return (False, None)
