import fcntl
import hashlib
import json
import os
import sys
import threading
import time
import traceback
from types import TracebackType

from shakenfist_utilities import logs  # noreorder


LOG, _ = logs.setup(__name__)


def ignore_exception(processname: str, e: BaseException) -> None:
    msg = f'[Exception] Ignored error in {processname}: {e}'
    exc_type, exc_value, exc_tb = sys.exc_info()
    fields: dict[str, object] = {}
    if exc_tb:
        msg += '\n'
        msg += '\n'.join(traceback.format_exception(exc_type, exc_value, exc_tb))

        # This ERROR is the only line for this event which reaches
        # centralised logging, so it has to carry the correlation
        # fields record_exception would otherwise have put on its own
        # (now suppressed) WARNING. Without them an operator cannot get
        # from a log line to /srv/shakenfist/exceptions/<hash>.json, or
        # see from the logs that a signature is on its five hundredth
        # occurrence -- and ignore_exception is by far the largest
        # producer of recorded exceptions in the codebase.
        fields = record_exception(
            exc_type, exc_value, exc_tb, already_logged=True) or {}
    LOG.with_fields(fields).error(msg)


def record_exception(
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    exc_tb: TracebackType | None,
    already_logged: bool = False
) -> dict[str, object] | None:
    """Record an exception to /srv/shakenfist/exceptions/<hash>.json.

    Returns the correlation fields for the recorded signature --
    exception_hash, exception_class and count -- or None if the record
    could not be written. A caller which logs the event itself, and
    therefore passes already_logged, attaches these to its own log
    line so the one line which does reach centralised logging still
    points at the on-disk record. They are built here rather than by
    each caller so there is a single definition of what a correlation
    field is called.
    """
    traceback_str = '\n'.join(traceback.format_exception(exc_type, exc_value, exc_tb))

    h = hashlib.sha256(traceback_str.encode()).hexdigest()[-8:]

    fd = None
    data = {}
    try:
        # This function must never raise: it is called from exception
        # handlers (the API server error path, sys.excepthook) where a
        # failure here would replace the exception being recorded and
        # misattribute the original failure (issue 3433). The directory
        # creation and open therefore sit inside the try as well.
        os.makedirs(os.path.join('/srv/shakenfist/exceptions'), exist_ok=True)

        flags = os.O_RDWR | os.O_CREAT
        fd = os.open(f'/srv/shakenfist/exceptions/{h}.json', flags, 0o644)

        # One writer at a time
        fcntl.flock(fd, fcntl.LOCK_EX)

        # Read previous data, if any
        size = os.fstat(fd).st_size
        if size > 0:
            d = os.read(fd, size)
            if d:
                data = json.loads(d.decode())
            os.lseek(fd, 0, os.SEEK_SET)

        # Add new data
        data['traceback'] = traceback_str
        data['count'] = data.get('count', 0) + 1
        if 'events' not in data:
            data['events'] = []
        data['events'].append(time.time())

        # Persist
        os.write(fd, json.dumps(data, indent=4, sort_keys=True).encode())

        # An exception noteworthy enough to record is noteworthy enough
        # to reach centralised logging, which generally ships INFO and
        # above. Log the first occurrence of a given traceback hash at
        # WARNING, with the traceback as a structured field so it stays
        # independently queryable (the same reason suppress_exceptions_
        # to_client attaches one, issue 3433); repeats log a compact
        # line at DEBUG so a hot loop does not flood the aggregator (the
        # on-disk file keeps the authoritative count and event times).
        # Callers which have already emitted a full-detail log line for
        # this exception (ignore_exception's ERROR) pass
        # already_logged=True so we don't emit a second, content-free
        # entry for the same event and double the signature count in
        # downstream log mining (issue 3590). Those callers get the
        # fields back to put on their own line, so nothing shipped is
        # lost by the suppression.
        fields: dict[str, object] = {
            'exception_hash': h,
            'exception_class': exc_type.__name__ if exc_type else None,
            'count': data['count'],
        }
        log_ctx = LOG.with_fields(fields)
        if already_logged:
            log_ctx.debug('Recorded exception (already logged by caller)')
        elif data['count'] == 1:
            log_ctx.with_fields({'traceback': traceback_str}).warning(
                f'Recorded new exception: {exc_value}')
        else:
            log_ctx.debug('Recorded repeat exception')

        return fields

    except Exception as e:
        # Ignore the exception here because we're already on the error path
        LOG.with_fields(data).with_fields({
            'recording_exception': str(e)
        }).error('Failed to record exception')
        return None
    finally:
        if fd is not None:
            os.close(fd)


_original_excepthook = sys.excepthook


def _tracking_excepthook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: TracebackType | None
) -> None:
    record_exception(exc_type, exc_value, exc_tb)
    _original_excepthook(exc_type, exc_value, exc_tb)


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    record_exception(args.exc_type, args.exc_value, args.exc_traceback)


def install_exception_tracking() -> None:
    sys.excepthook = _tracking_excepthook
    threading.excepthook = _thread_excepthook
    LOG.info('Installed exception tracking')
