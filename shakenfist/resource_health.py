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
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from shakenfist_utilities import logs  # noreorder


LOG, _ = logs.setup(__name__)

T = TypeVar('T')


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

    def ensure_present(self) -> None:
        """Provision anything this check needs to exist, once, before probing.

        A no-op by default. Kept off the probe path (check()) so that check()
        only ever detects, never provisions -- otherwise a probe that
        recreated a missing resource could mask its disappearance. A subclass
        whose resource can be legitimately absent-but-benign (e.g. a storage
        subdir that is created lazily) creates it here at daemon startup, so a
        later absence is unambiguously a fault.
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

    def run(self, fn: Callable[[], T],
            timeout: float) -> tuple[bool, T | None]:
        """Run ``fn`` under ``timeout`` seconds.

        Returns ``(completed, result)``. ``completed`` is False if a prior
        probe is still running, or if this probe did not return within
        ``timeout``; in both cases the worker thread is left running (as a
        daemon) and ``result`` is None. When ``completed`` is True the
        result is ``fn``'s return value. If ``fn`` raises, the exception is
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


# Defaults are constructor parameters, not config reads: this module has no
# config dependency. DEFAULT_WRITE_INTERVAL is decision D4's value;
# DEFAULT_TIMEOUT is provisional (D5/Q4) and gets its real value when a later
# phase wires it to a config knob.
DEFAULT_WRITE_INTERVAL = 300.0
DEFAULT_TIMEOUT = 30.0

HEARTBEAT_FILENAME = '_heartbeat'


class PathCheck(HealthCheck):
    """Health of a single local storage path.

    Two tiers, both under one deadline (see PLAN-node-resource-health D4/D5):

    - A cheap ``statvfs`` on every call. Its raising OSError means the store
      is gone (the sf-6 case: a shut-down filesystem EIOs even on statvfs);
      the ST_RDONLY mount flag means it was remounted read-only. A subdir
      that is simply not provisioned yet would also raise ENOENT here, so
      ``ensure_present`` creates it once before probing begins -- the probe
      itself only detects, it never provisions.
    - An authoritative write of a ``_heartbeat`` timestamp (with fsync) no
      more often than ``write_interval`` seconds. This is the real
      writability test -- os.makedirs(exist_ok=True) on an existing
      directory proves nothing -- and doubles as a forensic "last seen
      live" marker.

    The whole probe runs through the check's own DeadlineProbe, so a hung
    mount reports TIMEOUT instead of blocking the caller, and this check is
    the primary detector for NFS-backed storage (the instance-level
    error_policy pause never fires on a hang).
    """

    def __init__(self, path: str, *,
                 write_interval: float = DEFAULT_WRITE_INTERVAL,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self._path = os.path.abspath(path)
        self._write_interval = write_interval
        self._timeout = timeout
        self._probe = DeadlineProbe()
        self._last_write = 0.0

    @property
    def identity(self) -> str:
        # The absolute path: two checks on the same path de-duplicate.
        return self._path

    def ensure_present(self) -> None:
        # Create the probed directory if absent. Some probed subdirs are made
        # lazily (uploads on a node that has never received an upload; the
        # instances dir before the first instance boots), so without this a
        # not-yet-provisioned path would be misread as a MISSING fault. Runs
        # once at daemon startup, not on the probe path -- so once probing
        # begins an ENOENT means the store genuinely vanished. A dead store
        # fails loudly here at startup rather than silently later.
        os.makedirs(self._path, exist_ok=True)

    def check(self) -> HealthResult:
        now = time.time()
        do_write = (now - self._last_write) >= self._write_interval
        completed, result = self._probe.run(
            lambda: self._probe_once(do_write), self._timeout)
        if not completed:
            return HealthResult(
                self._path, HealthStatus.TIMEOUT,
                f'probe did not return within {self._timeout}s '
                '(store hung, or a previous probe is still outstanding)')
        assert result is not None  # completed=True => _probe_once returned one
        # Only advance the write clock on a successful write, so a store that
        # fails the write keeps being write-probed rather than waiting out
        # the interval.
        if do_write and result.status == HealthStatus.OK:
            self._last_write = now
        return result

    def _probe_once(self, do_write: bool) -> HealthResult:
        # Runs in the DeadlineProbe daemon thread; must not raise. The probe
        # only detects, never provisions: absent directories are created once
        # by ensure_present() before probing starts, so an ENOENT here means
        # the store genuinely went away, not that a subdir was never made.
        try:
            st = os.statvfs(self._path)
        except OSError as e:
            return HealthResult(self._path, HealthStatus.MISSING, str(e))

        if st.f_flag & os.ST_RDONLY:
            return HealthResult(
                self._path, HealthStatus.READONLY,
                'filesystem is mounted read-only')

        if not do_write:
            return HealthResult(self._path, HealthStatus.OK)

        heartbeat = os.path.join(self._path, HEARTBEAT_FILENAME)
        try:
            fd = os.open(
                heartbeat, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, f'{time.time()}\n'.encode('utf-8'))
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as e:
            return HealthResult(self._path, HealthStatus.UNWRITABLE, str(e))

        return HealthResult(self._path, HealthStatus.OK)
